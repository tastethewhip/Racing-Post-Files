#!/usr/bin/env python3
"""
racecards.py — Racecard scraper for Racing Post (Next.js version)
==================================================================

Drop-in replacement for the original racecards.py. Uses __NEXT_DATA__ JSON
extraction instead of HTML/XPath selectors that broke when RP migrated
/racecards/ to Next.js (2026-05-19).

RESILIENT FEATURES (built in):
  - Per-race try/except: one broken race never crashes the whole run
  - Exponential backoff on 406/network errors
  - Track filtering (--priority-only, --tracks)
  - Skip summary at the end
  - Debug mode (--debug) for full stack traces

OUTPUT:
  Writes {date}.json to ../racecards/ — identical structure to old version.
  Consumed by racecard_importer.py without changes.

USAGE:
  python racecards.py --day 1          # Today
  python racecards.py --day 2          # Tomorrow
  python racecards.py --days 2         # Both days
  python racecards.py --day 1 --priority-only
  python racecards.py --day 1 --tracks "kempton,happy valley"
  python racecards.py --day 1 --debug
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
import traceback

from collections import defaultdict
from typing import Any

from tqdm import tqdm

try:
    from curl_cffi import requests
except ImportError:
    print("ERROR: curl_cffi not installed. Run: pip install curl-cffi")
    sys.exit(1)

try:
    from orjson import dumps
except ImportError:
    def dumps(obj):
        return json.dumps(obj, ensure_ascii=False).encode('utf-8')


# ─── Try importing rpscrape utils (graceful fallback if unavailable) ─────

try:
    from utils.cleaning import normalize_name
except ImportError:
    def normalize_name(name: str) -> str:
        if not name:
            return ''
        return name.strip()

try:
    from utils.course import valid_meeting
except ImportError:
    def valid_meeting(course_name: str) -> bool:
        return True

try:
    from utils.region import get_region
except ImportError:
    get_region = None

try:
    from utils.going import get_surface
except ImportError:
    def get_surface(going: str) -> str:
        going_lower = (going or '').lower()
        if any(x in going_lower for x in ['standard', 'slow', 'fast',
                                           'polytrack', 'tapeta', 'fibresand']):
            return 'AW'
        if 'dirt' in going_lower:
            return 'Dirt'
        return 'Turf'


# ─── Constants ───────────────────────────────────────────────────────────

MAX_DAYS = 2

RACE_TYPE_MAP = {
    'X': 'Flat', 'F': 'Flat', 'C': 'Chase', 'H': 'Hurdle',
    'B': 'NH Flat', 'U': 'Flat', 'A': 'Flat', 'N': 'NH Flat',
    'S': 'Flat', 'P': 'Flat',
}
DEFAULT_RACE_TYPE = 'Flat'

PRIORITY_TRACKS = {
    'meydan', 'sha tin', 'happy valley',
}

REQUEST_DELAY = 0.3   # seconds between per-race requests
MAX_RETRIES = 3

type Racecards = defaultdict[str, defaultdict[str, defaultdict[str, dict[str, Any]]]]


# ─── Network ────────────────────────────────────────────────────────────

def fetch_page(url: str, retries: int = MAX_RETRIES) -> str | None:
    """Fetch page with Chrome impersonation and retry on transient errors."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, impersonate='chrome', timeout=30)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code in (406, 429, 503):
                wait = 2 ** attempt
                print(f'  ⚠️  HTTP {resp.status_code} attempt {attempt+1}/{retries}, '
                      f'waiting {wait}s…')
                time.sleep(wait)
                continue
            print(f'  ⚠️  HTTP {resp.status_code}: {url}')
            return None
        except Exception as e:
            wait = 2 ** attempt
            print(f'  ⚠️  Request error attempt {attempt+1}/{retries}: {e}')
            time.sleep(wait)
    print(f'  ❌  All {retries} attempts failed: {url}')
    return None


def extract_next_data(page_html: str) -> dict | None:
    """Extract __NEXT_DATA__ JSON blob from a Next.js page."""
    match = re.search(
        r'<script\s+id="__NEXT_DATA__"\s+type="application/json">(.*?)</script>',
        page_html,
        re.DOTALL,
    )
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError as e:
            print(f'  ⚠️  __NEXT_DATA__ JSON parse error: {e}')
    return None


# ─── Helpers ─────────────────────────────────────────────────────────────

def get_pattern(race_name: str) -> str:
    """Extract Group/Grade/Listed pattern from race name."""
    if not race_name:
        return ''
    regex = r'(\(|\s)((G|g)rade|(G|g)roup) (\d|[A-Ca-c]|I*)(\)|\s)'
    match = re.search(regex, race_name)
    if match:
        return f'{match.groups()[1]} {match.groups()[4]}'.title()
    if any(x in race_name.lower() for x in {'listed race', '(listed'}):
        return 'Listed'
    return ''


def parse_id_from_url(url: str | None) -> int | None:
    """Extract numeric ID from '/profile/horse/1004665/mehmas' style URL."""
    if not url:
        return None
    m = re.search(r'/profile/\w+/(\d+)/', url)
    return int(m.group(1)) if m else None


def parse_color_sex(color_sex: str | None) -> tuple[str, str, str]:
    """Parse 'b c' → ('b', 'Colt', 'c'), 'gr g' → ('gr', 'Gelding', 'g')."""
    if not color_sex:
        return ('', '', '')
    parts = color_sex.strip().split()
    if len(parts) < 2:
        return (color_sex.strip(), '', '')
    colour = ' '.join(parts[:-1])
    code = parts[-1]
    names = {'c': 'Colt', 'f': 'Filly', 'g': 'Gelding',
             'm': 'Mare', 'h': 'Horse', 'r': 'Ridgling'}
    return (colour, names.get(code.lower(), code), code)


def parse_form(form_data: list[dict] | None) -> str:
    """Convert formFiguresData list to form string."""
    if not form_data:
        return ''
    return ''.join(f.get('figure', '') for f in form_data)


def num(value) -> int | None:
    """Convert RP rating that can be '-' / int / None → int | None."""
    if value is None or value == '-' or value == '':
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_race_type(code: str) -> str:
    """Race type with fallback for unknown codes."""
    if code in RACE_TYPE_MAP:
        return RACE_TYPE_MAP[code]
    print(f"  ⚠️  Unknown race type code '{code}' → defaulting to '{DEFAULT_RACE_TYPE}'")
    return DEFAULT_RACE_TYPE


# ─── Index page ──────────────────────────────────────────────────────────

def get_meetings(target_date: str) -> list[dict] | None:
    """Fetch racecards index page and return meetings list."""
    url = f'https://www.racingpost.com/racecards/{target_date}'
    html = fetch_page(url)
    if not html:
        return None
    if '__NEXT_DATA__' not in html:
        print('  ❌  Index page has no __NEXT_DATA__ — RP may have changed format again')
        return None
    data = extract_next_data(html)
    if not data:
        return None
    try:
        return (data.get('props', {}).get('pageProps', {})
                .get('initialState', {}).get('raceCards', {})
                .get('meetings', []))
    except Exception as e:
        print(f'  ❌  Failed to navigate index JSON: {e}')
        return None


def extract_race_urls(
    meetings: list[dict],
    track_filter: set[str] | None = None,
) -> list[tuple[int, str, str, str]]:
    """Return list of (raceId, fullUrl, courseName, countryCode) from meetings.

    Applies valid_meeting + optional track_filter.
    """
    out: list[tuple[int, str, str, str]] = []

    for meeting in meetings:
        course = meeting.get('courseName', '') or meeting.get('courseStyleName', '')
        if not course:
            continue
        if meeting.get('isAbandoned', False):
            continue
        if not valid_meeting(course.lower()):
            continue
        if track_filter and not any(t in course.lower() for t in track_filter):
            continue

        country = meeting.get('country', '')

        for race in meeting.get('races', []):
            rid = race.get('raceId')
            href = race.get('raceUrl', '')
            if not rid or not href or race.get('isAbandoned', False):
                continue
            full = f'https://www.racingpost.com{href}' if href.startswith('/') else href
            out.append((rid, full, course, country))

    return out


# ─── Per-race page ───────────────────────────────────────────────────────

def scrape_race_page(url: str) -> tuple[dict | None, list[dict] | None]:
    """Fetch one race page and return (race_dict, runners_list)."""
    html = fetch_page(url)
    if not html or '__NEXT_DATA__' not in html:
        return None, None
    data = extract_next_data(html)
    if not data:
        return None, None
    try:
        container = (data.get('props', {}).get('pageProps', {})
                     .get('initialState', {}).get('racePage', {})
                     .get('data', {}))
        race = container.get('race', {})
        runners = container.get('runners', [])
        return (race, runners) if race else (None, None)
    except Exception as e:
        print(f'  ⚠️  Race page parse error: {e}')
        return None, None


# ─── Field mapping ───────────────────────────────────────────────────────

def map_race(race: dict, target_date: str, url: str, meeting_country: str) -> dict:
    """Map __NEXT_DATA__ race → old racecards.py output dict."""

    course_id = race.get('courseId')
    try:
        course_id = int(course_id) if course_id else None
    except (ValueError, TypeError):
        course_id = None

    course_name = race.get('courseStyleName', '') or race.get('meetingName', '')
    country_code = race.get('countryCode', meeting_country or '')

    # Region: prefer get_region(courseId) for compatibility, fall back to countryCode
    if get_region and course_id:
        try:
            region = get_region(str(course_id))
        except Exception:
            region = country_code
    else:
        region = country_code

    race_name = race.get('raceTitle', '')
    race_type_code = race.get('raceType', 'F')
    race_type = race.get('raceTypeDesc', '') or get_race_type(race_type_code)

    going = race.get('going', '')
    surface = race.get('surfaceType', '') or get_surface(going)

    rc = race.get('raceClass', '')
    race_class = int(rc) if rc and str(rc).isdigit() else None
    pattern = get_pattern(race_name.lower() if race_name else '')
    if not race_class and pattern:
        race_class = 1

    age_band = race.get('agesAllowed', '')
    rating_band = race.get('officialRatingBandDesc') or None

    handicap = (
        rating_band is not None
        or (race_name and 'handicap' in race_name.lower())
        or bool(race.get('raceHandicapDesc'))
    )

    prize = race.get('formattedTotalPrizeMoney') or None
    stalls = race.get('stalls', '') or race.get('straightRoundJubileeCode', '')

    if course_name == 'Belmont At The Big A':
        course_id = 255
        course_name = 'Aqueduct'

    return {
        'href': url,
        'race_id': race.get('raceId'),
        'date': target_date,
        'off_time': race.get('startTime', ''),
        'course_id': course_id,
        'course': course_name,
        'course_detail': stalls,
        'region': region,
        'race_name': race_name,
        'race_type': race_type,
        'distance_f': race.get('distanceFurlongs'),
        'distance_y': race.get('distanceYards'),
        'distance_round': None,
        'distance': None,
        'pattern': pattern,
        'race_class': race_class,
        'age_band': age_band,
        'rating_band': rating_band,
        'prize': prize,
        'field_size': race.get('numberOfRunners') or race.get('declaredRunners'),
        'handicap': handicap,
        'going': going,
        'surface': surface,
        'runners': [],
    }


def map_runner(r: dict) -> dict:
    """Map __NEXT_DATA__ runner → old racecards.py output dict."""

    colour, sex, sex_code = parse_color_sex(r.get('colorSex'))
    form = parse_form(r.get('formFiguresData'))

    silk_url = r.get('silkImage', '')
    silk_match = re.search(r'/svg/(.+?)\.svg', silk_url)
    silk_path = silk_match.group(1) if silk_match else ''

    return {
        'age': r.get('age'),
        'breeder': None,
        'breeder_id': None,
        'claim': r.get('weightAllowanceLbs'),
        'colour': colour,
        'comment': r.get('diomed'),
        'dam': normalize_name(r.get('damName', '')),
        'dam_id': parse_id_from_url(r.get('damUrl')),
        'dam_region': r.get('damCountry'),
        'damsire': normalize_name(r.get('damsireName', '')),
        'damsire_id': parse_id_from_url(r.get('damsireUrl')),
        'damsire_region': r.get('damsireCountry'),
        'dob': None,
        'draw': r.get('draw') if r.get('draw') else None,
        'form': form,
        'gelding_first_time': r.get('geldingFirstTime', False),
        'headgear': r.get('horseHeadGear'),
        'headgear_first': r.get('horseHeadGearFirstTime', False),
        'horse_id': r.get('horseId'),
        'jockey': normalize_name(r.get('jockeyName', '')),
        'jockey_allowance': r.get('weightAllowanceLbs'),
        'jockey_id': r.get('jockeyId'),
        'last_run': r.get('daysSinceLastRun'),
        'lbs': r.get('weightCarried'),
        'name': normalize_name(r.get('horseName', '')),
        'non_runner': r.get('nonRunner', False),
        'number': r.get('startNumber'),
        'ofr': num(r.get('officialRatingToday')),
        'owner': normalize_name(r.get('ownerName', '')),
        'owner_id': r.get('ownerId'),
        'profile': None,
        'region': r.get('countryOrigin', ''),
        'reserve': r.get('irishReserve', False),
        'rpr': num(r.get('rpPostmark')),
        'sex': sex,
        'sex_code': sex_code,
        'silk_path': silk_path,
        'silk_url': silk_url,
        'sire': normalize_name(r.get('sireName', '')),
        'sire_id': parse_id_from_url(r.get('sireUrl')),
        'sire_region': r.get('sireCountry'),
        'spotlight': r.get('spotlight'),
        'trainer': normalize_name(r.get('trainerName', '')),
        'trainer_14_days': None,
        'trainer_id': r.get('trainerId'),
        'trainer_location': None,
        'trainer_rtf': r.get('trainerRtf'),
        'ts': num(r.get('rpTopspeed')),
        'wind_surgery_first': r.get('windSurgery'),
        'wind_surgery_second': None,
        'stats': {},
        'prev_trainers': [],
        'prev_owners': [],
        'medical': [],
        'quotes': [],
        'stable_tour': [],
    }


# ─── Main scrape loop ───────────────────────────────────────────────────

def scrape_racecards(
    target_date: str,
    track_filter: set[str] | None = None,
) -> tuple[Racecards, list[dict]]:
    """Scrape all racecards for a date. Returns (racecards, skipped)."""

    races: Racecards = defaultdict(lambda: defaultdict(lambda: defaultdict(dict)))
    skipped: list[dict] = []

    # Step 1: Index page
    print(f'\nFetching index for {target_date}…')
    meetings = get_meetings(target_date)
    if not meetings:
        print('  ❌  No meetings found')
        return races, skipped

    # Apply filter info
    filter_msg = ''
    if track_filter:
        filter_msg = f' (filter: {", ".join(sorted(track_filter))})'
    print(f'  Found {len(meetings)} meetings{filter_msg}')

    # Step 2: Race URLs
    race_info = extract_race_urls(meetings, track_filter)
    if not race_info:
        print('  ❌  No race URLs extracted')
        return races, skipped
    print(f'  {len(race_info)} races to scrape')

    # Step 3: Scrape each race
    for race_id, url, course_name, country in tqdm(
        race_info,
        desc=target_date,
        bar_format='{desc}: {percentage:3.0f}% |{bar:49}| {n}/{total} ETA {remaining}',
        ncols=91,
    ):
        try:
            race_data, runners_data = scrape_race_page(url)

            if not race_data:
                skipped.append({
                    'race_id': race_id, 'course': course_name,
                    'reason': 'No race data in __NEXT_DATA__',
                })
                continue

            mapped = map_race(race_data, target_date, url, country)
            if runners_data:
                mapped['runners'] = [map_runner(r) for r in runners_data]

            races[mapped['region']][mapped['course']][mapped['off_time']] = mapped

        except Exception as e:
            print(f'\n  ⚠️  Error {course_name} race {race_id}: {e}')
            if '--debug' in sys.argv:
                traceback.print_exc()
            skipped.append({
                'race_id': race_id, 'course': course_name,
                'reason': str(e),
            })
            continue

        time.sleep(REQUEST_DELAY)

    return races, skipped


# ─── CLI ─────────────────────────────────────────────────────────────────

def validate_days_range(value: str) -> int:
    try:
        days = int(value)
        if 1 <= days <= MAX_DAYS:
            return days
        raise argparse.ArgumentTypeError(
            f'Must be 1–{MAX_DAYS}. Got: {days}')
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid: '{value}'")


def main() -> None:
    parser = argparse.ArgumentParser(
        description='Scrape Racing Post racecards (__NEXT_DATA__ / Next.js).',
        formatter_class=argparse.RawTextHelpFormatter,
    )

    flag_group = parser.add_mutually_exclusive_group()
    _ = flag_group.add_argument(
        '--day', type=validate_days_range, metavar='N',
        help="Single day. '--day 1' = today, '--day 2' = tomorrow.",
    )
    _ = flag_group.add_argument(
        '--days', type=validate_days_range, metavar='N',
        help='Scrape N days starting today.',
    )
    _ = parser.add_argument(
        '--priority-only', action='store_true',
        help=f'Only priority tracks: {", ".join(sorted(PRIORITY_TRACKS))}',
    )
    _ = parser.add_argument(
        '--tracks', type=str,
        help="Comma-separated track filter (e.g. 'meydan,sha tin')",
    )
    _ = parser.add_argument(
        '--debug', action='store_true',
        help='Full stack traces on errors',
    )

    args = parser.parse_args()

    dates: list[str] = [
        (datetime.date.today() + datetime.timedelta(days=i)).isoformat()
        for i in range(MAX_DAYS)
    ]

    if args.day:
        dates = [dates[args.day - 1]]
    elif args.days:
        dates = dates[:args.days]
    else:
        parser.print_usage(sys.stderr)
        print(f'\nError: Must specify --day or --days (1-{MAX_DAYS})')
        sys.exit(1)

    # Track filter
    track_filter = None
    if args.priority_only:
        track_filter = PRIORITY_TRACKS
        print(f'🎯 Filtering to priority tracks: {", ".join(sorted(PRIORITY_TRACKS))}')
    elif args.tracks:
        track_filter = {t.strip().lower() for t in args.tracks.split(',')}
        print(f'🎯 Filtering to tracks: {", ".join(sorted(track_filter))}')

    # Output directory
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           '..', 'racecards')
    os.makedirs(out_dir, exist_ok=True)

    total_skipped: list[dict] = []

    for target_date in dates:
        racecards, skipped = scrape_racecards(target_date, track_filter)
        total_skipped.extend(skipped)

        out_path = os.path.join(out_dir, f'{target_date}.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            _ = f.write(dumps(racecards).decode('utf-8'))

        n = sum(len(ts) for rr in racecards.values() for ts in rr.values())
        print(f'  ✅ Wrote {n} races → {out_path}')

    # Summary
    print(f'\n{"="*60}')
    if total_skipped:
        print(f'⚠️  SKIPPED {len(total_skipped)} race(s):')
        for s in total_skipped:
            print(f'   - {s["course"]} (race {s["race_id"]}): {s["reason"][:60]}')
    else:
        print('✅ All races processed successfully!')
    print(f'{"="*60}')


if __name__ == '__main__':
    main()
