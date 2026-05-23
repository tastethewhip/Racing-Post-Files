import pandas as pd
import json
from collections import defaultdict

class HorseRacingPredictor:
    def __init__(self):
        self.grouped_results = defaultdict(list)
        
        # Default variable values
        self.stable_var = 0
        self.trainer_var = 10
        self.form_var = 60
        self.recent_var = 40
        self.speed_var = 20
        self.course_var = 10
        self.going_var = 30
        self.distance_var = 20
        
        # Exponent (slider equivalent, 0-100)
        self.slider_value = 50
        
        # Odds lookup table
        self.ODDS_TABLE = [
            (1.96078431372549, "50/1"),
            (2.94117647058824, "33/1"),
            (3.84615384615385, "25/1"),
            (4.76190476190476, "20/1"),
            (5.88235294117647, "16/1"),
            (7.69230769230769, "12/1"),
            (8.33333333333333, "11/1"),
            (9.09090909090909, "10/1"),
            (10, "9/1"),
            (10.5263157894737, "17/2"),
            (11.1111111111111, "8/1"),
            (11.7647058823529, "15/2"),
            (12.5, "7/1"),
            (13.3333333333333, "13/2"),
            (14.2857142857143, "6/1"),
            (15.3846153846154, "11/2"),
            (16.6666666666667, "5/1"),
            (18.1818181818182, "9/2"),
            (20, "4/1"),
            (22.2222222222222, "7/2"),
            (23.0769230769231, "10/3"),
            (25, "3/1"),
            (26.6666666666667, "11/4"),
            (28.5714285714286, "5/2"),
            (30.7692307692308, "9/4"),
            (33.3333333333333, "2/1"),
            (34.7826086956522, "15/8"),
            (36.3636363636364, "7/4"),
            (38.0952380952381, "13/8"),
            (40, "6/4"),
            (42.1052631578947, "11/8"),
            (44.4444444444444, "5/4"),
            (47.6190476190476, "11/10"),
            (48.7804878048781, "21/20"),
            (50, "1/1"),
        ]
    
    def slider_to_exp(self, val):
        """Convert slider value (0-100) to exponent (0.5-20)"""
        min_exp = 0.5
        max_exp = 20
        t = val / 100
        return min_exp * ((max_exp / min_exp) ** t)
    
    def get_price_from_percentage(self, pct):
        """Get betting odds from win percentage"""
        for max_pct, price in self.ODDS_TABLE:
            if pct <= max_pct:
                return price
        return "4/5"
    
    def parse_stats(self, stat_str):
        """Parse JSON-like stats string"""
        if not stat_str or (isinstance(stat_str, float) and pd.isna(stat_str)):
            return 0
        try:
            obj = json.loads(str(stat_str).replace("'", '"'))
            return int(obj.get("wins", 0))
        except:
            return 0
    
    def safe_float(self, val, default=0):
        """Safely convert to float"""
        if pd.isna(val) or val == "" or val == "-":
            return default
        try:
            return float(str(val).replace("%", ""))
        except:
            return default
    
    def process_data(self, df):
        """Process CSV data and calculate scores"""
        self.grouped_results = defaultdict(list)
        exp = self.slider_to_exp(self.slider_value)
        
        for _, row in df.iterrows():
            # Skip rows with no last_run data
            if pd.isna(row.get('last_run')) or str(row.get('last_run', "")).strip() == "":
                continue
            
            # Calculate total score
            trainer_wins_pct = self.safe_float(row.get('stats_trainer_ovr_wins_pct', 0))
            trainer_rtf = self.safe_float(row.get('trainer_rtf', 0))
            trainer_14_days = self.safe_float(row.get('trainer_14_days_wins', 0))
            
            rpr = self.safe_float(row.get('rpr', 0))
            lbs = self.safe_float(row.get('lbs', 0))
            last_run = self.safe_float(row.get('last_run', 1), 1)
            
            ts = self.safe_float(row.get('ts', 0))
            ofr = self.safe_float(row.get('ofr', 0))
            
            total = (
                self.stable_var +
                (trainer_wins_pct * self.trainer_var) +
                trainer_rtf +
                trainer_14_days +
                max(((rpr - lbs) + rpr) * self.form_var, 1) +
                max(self.recent_var / last_run, 1) +
                max(((ts - ofr) + ts) * self.speed_var, 1) +
                (self.parse_stats(row.get('stats_horse_course')) * self.course_var) +
                (self.parse_stats(row.get('stats_horse_going')) * self.going_var) +
                (self.parse_stats(row.get('stats_horse_distance')) * self.distance_var)
            ) / 100
            
            score = total ** exp
            
            # Store results
            race_id = row.get('race_id')
            row_data = row.to_dict()
            row_data['Total'] = round(total * 1000) / 25
            row_data['Score'] = round(score * 10) / 10
            
            self.grouped_results[race_id].append(row_data)
    
    def display_results(self):
        """Display formatted results"""
        for race_id, race in self.grouped_results.items():
            sum_score = sum(h['Score'] for h in race)
            race.sort(key=lambda x: x['Score'], reverse=True)
            
            print(f"\n{'='*120}")
            print(f"{race[0].get('course', 'N/A')} - {race[0].get('off_time', 'N/A')} - {race[0].get('race_name', 'N/A')} - {race[0].get('distance_round', 'N/A')}")
            print(f"{'='*120}")
            
            print(f"{'Name':<20} {'Trainer':<15} {'Jockey':<15} {'Score':<10} {'Rank':<6} {'%':<8} {'Price':<10} {'Probability':<20}")
            print(f"{'-'*120}")
            
            for i, horse in enumerate(race):
                pct = round((horse['Score'] / sum_score) * 1000) / 10 if sum_score > 0 else 0
                price = self.get_price_from_percentage(pct)
                bar = '█' * int(pct / 5) + '░' * (20 - int(pct / 5))
                
                print(f"{str(horse.get('name', 'N/A')):<20} {str(horse.get('trainer', 'N/A')):<15} {str(horse.get('jockey', 'N/A')):<15} {horse['Total']:<10.2f} {i+1:<6} {pct:<8.1f}% {price:<10} [{bar}]")
    
    def export_csv(self, output_file='predictions.csv'):
        """Export results to CSV"""
        rows = []
        
        for race_id, race in self.grouped_results.items():
            sum_score = sum(h['Score'] for h in race)
            race.sort(key=lambda x: x['Score'], reverse=True)
            
            for i, horse in enumerate(race):
                pct = round((horse['Score'] / sum_score) * 1000) / 10 if sum_score > 0 else 0
                price = self.get_price_from_percentage(pct)
                
                rows.append({
                    'course': horse.get('course'),
                    'off_time': horse.get('off_time'),
                    'name': horse.get('name'),
                    'Score': horse['Total'],
                    'Rank': i + 1,
                    '%': pct,
                    'Price': price,
                    'Comment': horse.get('comment', ''),
                    'Spotlight': horse.get('spotlight', ''),
                    'Quotes': horse.get('quotes', '')
                })
        
        df_output = pd.DataFrame(rows)
        df_output.to_csv(output_file, index=False)
        print(f"\nResults exported to {output_file}")
    
    def run(self, csv_file):
        """Main execution"""
        try:
            df = pd.read_csv(csv_file)
            print(f"Loaded {len(df)} rows from {csv_file}")
            self.process_data(df)
            self.display_results()
            self.export_csv()
        except FileNotFoundError:
            print(f"Error: {csv_file} not found")
        except Exception as e:
            print(f"Error: {e}")


if __name__ == "__main__":
    predictor = HorseRacingPredictor()
    predictor.run('2026-05-05.csv')  # Hardcoded filename
