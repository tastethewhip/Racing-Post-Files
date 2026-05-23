#!/usr/bin/env python

import argparse
import datetime
import json
import os
import re
import sys
import tomli

from collections import defaultdict
from dotenv import load_dotenv
from lxml import html
from pathlib import Path
from orjson import dumps
from tqdm import tqdm
from typing import Any

from utils.cleaning import clean_string
from utils.course import valid_meeting
from utils.going import get_surface
from utils.network import NetworkClient
from utils.profiles import get_profiles
from utils.region import get_region, valid_region

from models.racecard import Racecard, Runner

_ = load_dotenv()

MAX_DAYS = 2

RACE_TYPE = {
    'F': 'Flat',
    'X': 'Flat',
    'C': 'Chase',
    'H': 'Hurdle',
    'B': 'NH Flat',
    'W': 'NH Flat',
    'U': 'Unknown',  # Unknown/unspecified race type
}

type Racecards = defaultdict[str, defaultdict[str, defaultdict[str, dict[str, Any]]]]


def load_field_config() -> dict[str, Any]:
    """Load field configuration from settings/user_racecard_settings.toml or default_racecard_settings.toml"""
    user_config_path = Path('../settings/user_racecard_settings.toml')
    default_config_path = Path('../settings/default_racecard_settings.toml')

    config_path = user_config_path if user_config_path.exists() else default_config_path

    if not config_path.exists():
        return {
            'data_collection': {
                'fetch_profiles': False,
                'fetch_stats': False,
            },
            'field_groups': {},
        }

    with open(config_path, 'rb') as f:
        config = tomli.load(f)

    return config


def validate_days_range(value: str) -> int:
    try:
        days = int(value)
        if 1 <= days <= MAX_DAYS:
            return days
        raise argparse.ArgumentTypeError(
            f'Value must be an integer between 1 and {MAX_DAYS}. Got: {days}'
        )
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid value: '{value}'. Expected an integer.")


def _extract_next_data(content: bytes) -> dict[str, Any]:
    """Parse __NEXT_DATA__ JSON blob from a Next.js page and return initialState."""
    doc = html.fromstring(content)
    scripts = doc.xpath('//script[@id="__NEXT_DATA__"]/text()')
    if not scripts:
        raise ValueError('__NEXT_DATA__ script tag not found')
    data = json.loads(scripts[0])
    return data['props']['pageProps']['initialState']


def _url_to_id(url: str | None) -> int | None:
    """Extract numeric ID from RP profile URLs like /profile/horse/1004665/mehmas."""
    if not url:
        return None
    try:
        return int(url.split('/')[3])
    except (IndexError, ValueError):
        return None


def _to_int(val: Any) -> int | None:
    """Convert RP rating values (int, None, or '-' string) to int or None."""
    if val is None or val == '-' or val == '':
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _format_distance(total_yards: int) -> tuple[str, str]:
    """Return (distance_round, distance) display strings from total yards."""
    miles = total_yards // 1760
    remaining_y = total_yards % 1760
    furlongs = remaining_y // 220
    yards = remaining_y % 220

    parts: list[str] = []
    if miles:
        parts.append(f'{miles}m')
    if furlongs:
        parts.append(f'{furlongs}f')
    if not parts:
        # sub-furlong or exact furlong count with no miles
        parts.append(f'{total_yards // 220}f')

    distance_round = ''.join(parts)
    distance = distance_round + (f'{yards}y' if yards else '')
    return distance_round, distance


def get_pattern(race_name: str):
    regex_group = r'(\(|\s)((G|g)rade|(G|g)roup) (\d|[A-Ca-c]|I*)(\)|\s)'
    match = re.search(regex_group, race_name)

    if match:
        pattern = f'{match.groups()[1]} {match.groups()[4]}'.title()
        return pattern.title()

    if any(x in race_name.lower() for x in {'listed race', '(listed'}):
        return 'Listed'

    return ''


def get_race_urls(
    client: NetworkClient, dates: list[str], region: str | None = None
) -> dict[str, list[tuple[str, str]]]:
    race_urls: defaultdict[str, list[tuple[str, str]]] = defaultdict(list)

    for date in dates:
        url = f'https://www.racingpost.com/racecards/{date}'
        status, response = client.get(url)

        if status != 200 or not response.content:
            print(f'Failed to get racecards for {date} (status: {status})')
            continue

        try:
            state = _extract_next_data(response.content)
        except (ValueError, KeyError, IndexError) as e:
            print(f'Failed to parse __NEXT_DATA__ for {date}: {e}')
            continue

        for meeting in state.get('raceCards', {}).get('meetings', []):
            course_name = meeting.get('courseName', '')
            if not valid_meeting(course_name.lower()):
                continue
            for race in meeting.get('races', []):
                race_id = str(race['raceId'])
                href = race['raceUrl']

                if region:
                    course_id = href.split('/')[2]
                    if get_region(course_id) != region.upper():
                        continue

                race_urls[date].append((race_id, href))

    return dict(race_urls)


def parse_runners(
    runners_json: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> list[Runner]:
    runners: list[Runner] = []
    field_groups = config.get('field_groups', {})
    data_opts = config.get('data_collection', {})

    include_all_groups = not field_groups

    def should_include_group(group: str) -> bool:
        if include_all_groups:
            return True
        if group not in field_groups:
            raise KeyError(f'Unknown field group: {group}')
        return field_groups[group] is True

    for runner_json in runners_json:
        profile = None
        if data_opts.get('fetch_profiles', True):
            try:
                profile = profiles[runner_json['horseId']]
            except KeyError:
                print(
                    f'[WARN] Missing profile for: {runner_json["horseId"]} - {runner_json["horseName"]} '
                    f'(continuing without profile data)'
                )

        runner = Runner()
        runners.append(runner)

        if should_include_group('core'):
            runner.name = clean_string(runner_json['horseName'])
            runner.horse_id = runner_json['horseId']
            runner.number = runner_json['startNumber']
            runner.draw = runner_json['draw'] if runner_json['draw'] else None

        if should_include_group('basic_info'):
            runner.age = runner_json.get('age')
            color_sex = runner_json.get('colorSex', '')
            parts = color_sex.rsplit(' ', 1)
            runner.colour = parts[0] if len(parts) > 1 else (color_sex or None)
            runner.sex_code = parts[1] if len(parts) > 1 else None
            runner.region = runner_json.get('countryOrigin')
            runner.dob = None  # not in racecard data; available via profile fetch
            if profile:
                runner.sex = profile.get('horseSex')

        if should_include_group('performance'):
            runner.form = (
                ''.join(f['figure'] for f in runner_json['formFiguresData'])[::-1]
                if runner_json.get('formFiguresData')
                else ''
            )
            runner.rpr = _to_int(runner_json.get('rpPostmark'))
            runner.ts = _to_int(runner_json.get('rpTopspeed'))
            runner.ofr = _to_int(runner_json.get('officialRatingToday'))
            runner.last_run = runner_json.get('daysSinceLastRun')

        if should_include_group('jockey'):
            runner.jockey = clean_string(runner_json['jockeyName'])
            runner.jockey_id = runner_json['jockeyId']
            runner.jockey_allowance = runner_json.get('weightAllowanceLbs')
            runner.claim = runner_json.get('weightAllowanceLbs')

        if should_include_group('trainer'):
            runner.trainer = clean_string(runner_json['trainerName'])
            runner.trainer_id = runner_json['trainerId']
            runner.trainer_rtf = runner_json.get('trainerRtf')
            if profile:
                runner.trainer_location = profile.get('trainerLocation')
                runner.trainer_14_days = profile.get('trainerLast14Days')

        if should_include_group('weight'):
            runner.lbs = runner_json.get('weightCarried')

        if should_include_group('equipment'):
            runner.headgear = runner_json.get('horseHeadGear')
            runner.headgear_first = bool(runner_json.get('horseHeadGearFirstTime'))
            runner.gelding_first_time = bool(runner_json.get('geldingFirstTime'))
            ws = runner_json.get('windSurgery')
            if ws is None or ws is False:
                runner.wind_surgery_first = False
                runner.wind_surgery_second = False
            elif isinstance(ws, bool):
                runner.wind_surgery_first = ws
                runner.wind_surgery_second = False
            elif isinstance(ws, dict):
                runner.wind_surgery_first = bool(ws.get('firstTime', ws.get('first', False)))
                runner.wind_surgery_second = bool(ws.get('secondTime', ws.get('second', False)))
            else:
                runner.wind_surgery_first = bool(ws)
                runner.wind_surgery_second = False

        if should_include_group('breeding'):
            runner.sire = clean_string(runner_json.get('sireName', ''))
            runner.sire_id = _url_to_id(runner_json.get('sireUrl'))
            runner.sire_region = runner_json.get('sireCountry')
            runner.dam = clean_string(runner_json.get('damName', ''))
            runner.dam_id = _url_to_id(runner_json.get('damUrl'))
            runner.dam_region = runner_json.get('damCountry')
            runner.damsire = clean_string(runner_json.get('damsireName', ''))
            runner.damsire_id = _url_to_id(runner_json.get('damsireUrl'))
            runner.damsire_region = runner_json.get('damsireCountry')
            if profile:
                runner.breeder = clean_string(profile.get('breederName', ''))
                runner.breeder_id = profile.get('breederUid')

        if should_include_group('ownership'):
            runner.owner = clean_string(runner_json['ownerName'])
            runner.owner_id = runner_json['ownerId']

        if should_include_group('comments'):
            runner.comment = runner_json.get('diomed')
            runner.spotlight = runner_json.get('spotlight')

        if should_include_group('status'):
            runner.non_runner = bool(runner_json.get('nonRunner'))
            runner.reserve = bool(runner_json.get('irishReserve'))

        if should_include_group('silk'):
            silk = runner_json.get('silkImage', '')
            runner.silk_url = silk or None
            runner.silk_path = (
                silk.removeprefix('https://www.rp-assets.com/svg/').removesuffix('.svg')
                if silk
                else None
            )

        if should_include_group('profile') and profile:
            runner.profile = profile.get('profile')

        if should_include_group('history') and profile:
            if profile.get('previousTrainers'):
                runner.prev_trainers = [
                    {
                        'trainer': clean_string(trainer['trainerStyleName']),
                        'trainer_id': trainer['trainerUid'],
                        'change_date': trainer['trainerChangeDate'].split('T')[0],
                    }
                    for trainer in profile['previousTrainers']
                ]
            if profile.get('previousOwners'):
                runner.prev_owners = [
                    {
                        'owner': clean_string(owner['ownerStyleName']),
                        'owner_id': owner['ownerUid'],
                        'change_date': owner['ownerChangeDate'].split('T')[0],
                    }
                    for owner in profile['previousOwners']
                ]

        if should_include_group('medical') and profile and profile.get('medical'):
            runner.medical = [
                {'date': med['medicalDate'].split('T')[0], 'type': med['medicalType']}
                for med in profile['medical']
            ]

        if should_include_group('quotes') and profile:
            if profile.get('quotes'):
                runner.quotes = [
                    {
                        'date': q['raceDate'].split('T')[0],
                        'horse': clean_string(q['horseStyleName']),
                        'horse_id': q['horseUid'],
                        'race': q['raceTitle'],
                        'race_id': q['raceId'],
                        'course': q['courseStyleName'],
                        'course_id': q['courseUid'],
                        'distance_f': q['distanceFurlong'],
                        'distance_y': q['distanceYard'],
                        'quote': q['notes'],
                    }
                    for q in profile['quotes']
                ]
            if profile.get('stable_quotes'):
                runner.stable_tour = [
                    {
                        'horse': clean_string(q['horseName']),
                        'horse_id': q['horseUid'],
                        'quote': q['notes'],
                    }
                    for q in profile['stable_quotes']
                ]

    return runners


def scrape_racecards(
    race_urls: dict[str, list[tuple[str, str]]],
    date: str,
    config: dict[str, Any],
    client: NetworkClient,
) -> Racecards:
    races: Racecards = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))

    data_opts = config.get('data_collection', {})
    fetch_profiles = data_opts.get('fetch_profiles', False)

    for race_id, href in tqdm(
        race_urls[date],
        desc=date,
        bar_format='{desc}: {percentage:3.0f}% |{bar:49}| {n}/{total} ETA {remaining}',
        ncols=91,
    ):
        url_base = 'https://www.racingpost.com'
        url_racecard = f'{url_base}{href}'

        status_racecard, resp_racecard = client.get(url_racecard)

        if status_racecard != 200:
            print(f'Failed to get racecard (status: {status_racecard}): {url_racecard}')
            continue

        try:
            state = _extract_next_data(resp_racecard.content)
        except (ValueError, KeyError, IndexError) as e:
            print(f'Failed to parse __NEXT_DATA__: {e} url={url_racecard}')
            continue

        rp_data = state.get('racePage', {}).get('data', {})
        race_data = rp_data.get('race', {})
        runners_json = rp_data.get('runners', [])

        if not race_data or not runners_json:
            print(f'Missing race or runner data: {url_racecard}')
            continue

        profiles: dict[str, dict[str, Any]] = {}
        if fetch_profiles:
            profile_urls = [
                url_base + r['horseUrl'].split('#')[0] + '/form'
                for r in runners_json
                if r.get('horseUrl')
            ]
            profiles = get_profiles(client, profile_urls)

        race: Racecard = Racecard()

        race.href = url_racecard
        race.race_id = int(race_id)
        race.date = date

        race.off_time = race_data.get('startTime', '')

        race.course_id = race_data.get('courseId')
        race.course = race_data.get('courseStyleName', '')
        course_detail_code = race_data.get('straightRoundJubileeCode')
        race.course_detail = course_detail_code.strip('()') if course_detail_code else ''

        if race.course == 'Belmont At The Big A':
            race.course_id = 255
            race.course = 'Aqueduct'

        race.region = get_region(str(race.course_id))
        race.race_name = race_data.get('raceTitle', '')
        race.race_type = RACE_TYPE.get(race_data.get('raceType', 'U'), 'Unknown')

        race.distance_f = race_data.get('distanceFurlongs')
        race.distance_y = race_data.get('distanceYards')
        if race.distance_y:
            race.distance_round, race.distance = _format_distance(race.distance_y)
        else:
            race.distance_round = race.distance = ''

        race.pattern = get_pattern(race.race_name.lower())
        rc = race_data.get('raceClass')
        race.race_class = int(rc) if rc is not None and str(rc).isdigit() else rc
        race.race_class = 1 if not race.race_class and race.pattern else race.race_class

        race.age_band = race_data.get('agesAllowed')
        race.rating_band = race_data.get('officialRatingBandDesc') or None
        race.prize = race_data.get('formattedTotalPrizeMoney')
        race.field_size = race_data.get('declaredRunners')

        race.handicap = race.rating_band is not None or 'handicap' in race.race_name.lower()
        race.going = race_data.get('going', '')
        race.surface = get_surface(race.going)

        race.runners = parse_runners(runners_json, profiles, config)

        races[race.region][race.course][race.off_time] = race.to_dict()

    return races


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Scrape racecards for a single day or a range of days.',
        formatter_class=argparse.RawTextHelpFormatter,
    )

    flag_group = parser.add_mutually_exclusive_group()

    _ = flag_group.add_argument(
        '--day',
        type=validate_days_range,
        help="Scrape a single specific day (N). E.g., '--day 2' scrapes the 2nd day.",
        metavar='N',
    )
    _ = flag_group.add_argument(
        '--days',
        type=validate_days_range,
        help="Scrape a range of days (N total). E.g., '--days 2' scrapes 2 days.",
        metavar='N',
    )

    _ = parser.add_argument(
        '--region',
        type=str,
        help="Region code to filter by (e.g., 'gb', 'ire').",
        metavar='CODE',
    )

    args = parser.parse_args()

    dates: list[str] = [
        (datetime.date.today() + datetime.timedelta(days=i)).isoformat() for i in range(MAX_DAYS)
    ]

    if args.day:
        dates = [dates[args.day - 1]]
    elif args.days:
        dates = dates[: args.days]
    else:
        parser.print_usage(sys.stderr)
        print(f'\nError: Must specify a day (--day) or days (--days) (1-{MAX_DAYS})')
        sys.exit(1)

    if not os.path.exists('../racecards'):
        os.makedirs('../racecards')

    region = args.region.lower() if args.region else None

    if region and not valid_region(region):
        print(f'Invalid region: {args.region}')
        sys.exit(1)

    email = os.getenv('EMAIL')
    auth_state = os.getenv('AUTH_STATE')
    access_token = os.getenv('ACCESS_TOKEN')

    client = NetworkClient(email=email, auth_state=auth_state, access_token=access_token)

    race_urls = get_race_urls(client, dates, region)

    if not race_urls:
        print(f'No races found for: {", ".join(dates)}')
        sys.exit(1)

    config = load_field_config()

    for date in race_urls:
        racecards = scrape_racecards(race_urls, date, config, client)

        with open(f'../racecards/{date}.json', 'w', encoding='utf-8') as f:
            _ = f.write(dumps(racecards).decode('utf-8'))


if __name__ == '__main__':
    main()
