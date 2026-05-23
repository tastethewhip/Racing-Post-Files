#!/usr/bin/env python3

import gzip
import os
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
from datetime import date, datetime
from pathlib import Path

from typing import Optional



from dotenv import load_dotenv
from lxml import html
from orjson import loads

from utils.argparser import ArgParser
from utils.network import NetworkClient
from utils.paths import Paths, build_paths
from utils.settings import Settings
from utils.update import Update

_ = load_dotenv()

settings = Settings()

RACE_TYPES: dict[str, set[str] | None] = {
    'flat': {'Flat'},
    'jumps': {'Chase', 'Hurdle', 'NH Flat'},
    'both': None,
}


def get_race_urls(
    years: list[str], tracks: list[tuple[str, str]], race_type: str, client: NetworkClient
) -> list[str]:
    """Get race URLs by years."""
    from rpscrape import get_race_urls as original_get_race_urls
    return original_get_race_urls(years, tracks, race_type, client)


def get_race_urls_date(
    dates: list[date], tracks: list[tuple[str, str]], client: NetworkClient
) -> list[str]:
    """Get race URLs by dates."""
    from rpscrape import get_race_urls_date as original_get_race_urls_date
    return original_get_race_urls_date(dates, tracks, client)


def load_or_save_urls(
    path: Path,
    builder,
) -> list[str]:
    """Load URLs from file or build and save them."""
    from rpscrape import load_or_save_urls as original_load_or_save_urls
    return original_load_or_save_urls(path, builder)


def scrape_races(
    race_urls: list[str],
    paths: Paths,
    race_type: str,
    client: NetworkClient,
    file_writer,
    progress_callback=None,
    cancel_flag=None,
):
    """Scrape races with progress callback."""
    from utils.race import Race, VoidRaceError
    from rpscrape import prepare_betfair

    betfair = prepare_betfair(
        race_urls=race_urls,
        paths=paths,
    )

    last_url = paths.progress.read_text().strip() if paths.progress.exists() else None

    if last_url:
        try:
            race_urls = race_urls[race_urls.index(last_url) + 1 :]
        except ValueError:
            pass

    append = last_url is not None and paths.output.exists()

    with file_writer(str(paths.output), append=append) as f:
        if not append:
            _ = f.write(settings.csv_header + '\n')

        total_races = len(race_urls)
        for idx, url in enumerate(race_urls):
            # Check for cancellation
            if cancel_flag and not cancel_flag[0]:
                raise Exception("Scraping cancelled by user")
            
            # Update progress
            if progress_callback:
                progress = (idx / total_races) * 100
                progress_callback(progress, f"Processing {idx + 1}/{total_races}: {url}")

            _, response = client.get(url)
            doc = html.fromstring(response.content)

            try:
                race = (
                    Race(client, url, doc, settings.fields, betfair.data)
                    if betfair
                    else Race(client, url, doc, settings.fields)
                )
            except VoidRaceError:
                continue

            allowed = RACE_TYPES.get(race_type)
            if allowed is not None and race.race_info.race_type not in allowed:
                continue

            for row in race.csv_data:
                _ = f.write(row + '\n')

            _ = paths.progress.write_text(url)

    if progress_callback:
        progress_callback(100, "Finished scraping.")


def writer_csv(file_path: str, append: bool = False):
    return open(file_path, 'a' if append else 'w', encoding='utf-8')


def writer_gzip(file_path: str, append: bool = False):
    mode = 'at' if append else 'wt'
    return gzip.open(file_path, mode, encoding='utf-8')


class RPScrapeGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("RPScrape - Racing Post Scraper")
        self.root.geometry("900x700")
        
        self.settings = settings
        self.client: Optional[NetworkClient] = None
        self.scraping = False
        self.cancel_flag = [True]  # [True] = scraping, [False] = cancel
        
        self.setup_ui()
        self.load_settings()

    def setup_ui(self) -> None:
        """Set up the user interface."""
        # Create notebook for tabs
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Create tabs
        self.setup_scrape_tab()
        self.setup_settings_tab()
        self.setup_log_tab()

    def setup_scrape_tab(self) -> None:
        """Set up the main scraping tab."""
        scrape_frame = ttk.Frame(self.notebook)
        self.notebook.add(scrape_frame, text="Scrape")
        
        # Input selection frame
        input_frame = ttk.LabelFrame(scrape_frame, text="Input Options", padding="10")
        input_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Mode selection
        ttk.Label(input_frame, text="Mode:").grid(row=0, column=0, sticky=tk.W)
        self.mode_var = tk.StringVar(value="years")
        mode_frame = ttk.Frame(input_frame)
        mode_frame.grid(row=0, column=1, sticky=tk.W)
        ttk.Radiobutton(mode_frame, text="By Years", variable=self.mode_var, 
                       value="years", command=self.update_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_frame, text="By Dates", variable=self.mode_var, 
                       value="dates", command=self.update_mode).pack(side=tk.LEFT)
        
        # Years selection
        self.years_label = ttk.Label(input_frame, text="Years:")
        self.years_label.grid(row=1, column=0, sticky=tk.W)
        self.years_var = tk.StringVar()
        self.years_entry = ttk.Entry(input_frame, textvariable=self.years_var, width=40)
        self.years_entry.grid(row=1, column=1, sticky=tk.EW)
        ttk.Label(input_frame, text="(comma-separated, e.g., 2024,2025)", 
                 font=("", 8)).grid(row=2, column=1, sticky=tk.W)
        
        # Dates selection
        self.dates_label = ttk.Label(input_frame, text="Dates:")
        self.dates_label.grid(row=3, column=0, sticky=tk.W)
        self.dates_var = tk.StringVar()
        self.dates_entry = ttk.Entry(input_frame, textvariable=self.dates_var, width=40)
        self.dates_entry.grid(row=3, column=1, sticky=tk.EW)
        ttk.Label(input_frame, text="(comma-separated, format: YYYY-MM-DD)", 
                 font=("", 8)).grid(row=4, column=1, sticky=tk.W)
        
        # Tracks selection
        ttk.Label(input_frame, text="Tracks:").grid(row=5, column=0, sticky=tk.W)
        self.tracks_var = tk.StringVar()
        ttk.Entry(input_frame, textvariable=self.tracks_var, width=40).grid(
            row=5, column=1, sticky=tk.EW)
        ttk.Label(input_frame, text="(comma-separated track names, or leave blank / enter 'all' for all tracks)", 
                 font=("", 8)).grid(row=6, column=1, sticky=tk.W)
        
        # Race type selection
        ttk.Label(input_frame, text="Race Type:").grid(row=7, column=0, sticky=tk.W)
        self.race_type_var = tk.StringVar(value="both")
        race_type_frame = ttk.Frame(input_frame)
        race_type_frame.grid(row=7, column=1, sticky=tk.W)
        ttk.Radiobutton(race_type_frame, text="Flat", variable=self.race_type_var, 
                       value="flat").pack(side=tk.LEFT)
        ttk.Radiobutton(race_type_frame, text="Jumps", variable=self.race_type_var, 
                       value="jumps").pack(side=tk.LEFT)
        ttk.Radiobutton(race_type_frame, text="Both", variable=self.race_type_var, 
                       value="both").pack(side=tk.LEFT)
        
        # Options frame
        options_frame = ttk.LabelFrame(scrape_frame, text="Options", padding="10")
        options_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.gzip_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Use Gzip compression for output", 
                       variable=self.gzip_var).pack(anchor=tk.W)
        
        self.clean_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(options_frame, text="Clear previous request data", 
                       variable=self.clean_var).pack(anchor=tk.W)
        
        # Output directory
        ttk.Label(options_frame, text="Output Directory:").pack(anchor=tk.W)
        output_frame = ttk.Frame(options_frame)
        output_frame.pack(fill=tk.X, pady=5)
        self.output_var = tk.StringVar(value=str(Path.home() / "rpscrape_output"))
        ttk.Entry(output_frame, textvariable=self.output_var, width=40).pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(output_frame, text="Browse", 
                  command=self.browse_output_dir).pack(side=tk.LEFT, padx=5)
        
        # Control buttons
        button_frame = ttk.Frame(scrape_frame)
        button_frame.pack(fill=tk.X, padx=5, pady=10)
        
        self.start_button = ttk.Button(button_frame, text="Start Scraping", 
                                      command=self.start_scraping)
        self.start_button.pack(side=tk.LEFT, padx=5)
        
        self.stop_button = ttk.Button(button_frame, text="Stop", 
                                     command=self.stop_scraping, state=tk.DISABLED)
        self.stop_button.pack(side=tk.LEFT, padx=5)
        
        # Progress frame
        progress_frame = ttk.LabelFrame(scrape_frame, text="Progress", padding="10")
        progress_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, 
                                           maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=5)
        
        self.status_label = ttk.Label(progress_frame, text="Ready", foreground="blue")
        self.status_label.pack(anchor=tk.W)

    def setup_settings_tab(self) -> None:
        """Set up the settings tab."""
        settings_frame = ttk.Frame(self.notebook)
        self.notebook.add(settings_frame, text="Settings")
        
        # Environment variables
        env_frame = ttk.LabelFrame(settings_frame, text="Betfair Credentials", padding="10")
        env_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(env_frame, text="Email:").grid(row=0, column=0, sticky=tk.W)
        self.email_var = tk.StringVar(value=os.getenv('EMAIL', ''))
        ttk.Entry(env_frame, textvariable=self.email_var, width=40).grid(
            row=0, column=1, sticky=tk.EW)
        
        ttk.Label(env_frame, text="Auth State:").grid(row=1, column=0, sticky=tk.W)
        self.auth_state_var = tk.StringVar(value=os.getenv('AUTH_STATE', ''))
        ttk.Entry(env_frame, textvariable=self.auth_state_var, width=40, 
                 show="*").grid(row=1, column=1, sticky=tk.EW)
        
        ttk.Label(env_frame, text="Access Token:").grid(row=2, column=0, sticky=tk.W)
        self.access_token_var = tk.StringVar(value=os.getenv('ACCESS_TOKEN', ''))
        ttk.Entry(env_frame, textvariable=self.access_token_var, width=40, 
                 show="*").grid(row=2, column=1, sticky=tk.EW)
        
        # Save settings button
        ttk.Button(env_frame, text="Save Credentials", 
                  command=self.save_credentials).grid(row=3, column=0, columnspan=2, pady=10)
        
        # Auto-update option
        options_frame = ttk.LabelFrame(settings_frame, text="Application", padding="10")
        options_frame.pack(fill=tk.X, padx=5, pady=5)
        
        self.auto_update_var = tk.BooleanVar(value=self.settings.toml.get('auto_update', False) 
                                             if self.settings.toml else False)
        ttk.Checkbutton(options_frame, text="Check for updates on startup", 
                       variable=self.auto_update_var).pack(anchor=tk.W)

    def setup_log_tab(self) -> None:
        """Set up the log tab."""
        log_frame = ttk.Frame(self.notebook)
        self.notebook.add(log_frame, text="Log")
        
        self.log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, 
                                                  state=tk.DISABLED, height=20)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # Clear log button
        ttk.Button(log_frame, text="Clear Log", 
                  command=self.clear_log).pack(pady=5)

    def update_mode(self) -> None:
        """Update input fields based on selected mode."""
        mode = self.mode_var.get()
        if mode == "years":
            self.years_entry.config(state=tk.NORMAL)
            self.dates_entry.config(state=tk.DISABLED)
        else:
            self.years_entry.config(state=tk.DISABLED)
            self.dates_entry.config(state=tk.NORMAL)

    def browse_output_dir(self) -> None:
        """Browse for output directory."""
        directory = filedialog.askdirectory(title="Select Output Directory")
        if directory:
            self.output_var.set(directory)

    def log(self, message: str) -> None:
        """Add message to log."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + '\n')
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)
        self.root.update()

    def clear_log(self) -> None:
        """Clear the log."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

    def load_settings(self) -> None:
        """Load settings from configuration."""
        if self.settings.toml:
            self.gzip_var.set(self.settings.toml.get('gzip_output', False))

    def save_credentials(self) -> None:
        """Save credentials to environment."""
        os.environ['EMAIL'] = self.email_var.get()
        os.environ['AUTH_STATE'] = self.auth_state_var.get()
        os.environ['ACCESS_TOKEN'] = self.access_token_var.get()
        messagebox.showinfo("Success", "Credentials saved successfully!")

    def parse_input(self) -> Optional[dict]:
        """Parse and validate user input."""
        try:
            mode = self.mode_var.get()
            race_type = self.race_type_var.get()
            
            if mode == "years":
                years_str = self.years_var.get().strip()
                if not years_str:
                    messagebox.showerror("Error", "Please enter at least one year")
                    return None
                years = [y.strip() for y in years_str.split(',')]
                dates = None
            else:
                dates_str = self.dates_var.get().strip()
                if not dates_str:
                    messagebox.showerror("Error", "Please enter at least one date")
                    return None
                try:
                    dates = [datetime.strptime(d.strip(), '%Y-%m-%d').date() 
                            for d in dates_str.split(',')]
                except ValueError:
                    messagebox.showerror("Error", "Invalid date format. Use YYYY-MM-DD")
                    return None
                years = None
            
            tracks_str = self.tracks_var.get().strip()

            # Allow blank or "all" to mean all tracks
            if not tracks_str or tracks_str.lower() == "all":
                tracks = []
            else:
                tracks = [t.strip() for t in tracks_str.split(',') if t.strip()]
            
            output_dir = Path(self.output_var.get())
            output_dir.mkdir(parents=True, exist_ok=True)
            
            return {
                'mode': mode,
                'years': years,
                'dates': dates,
                'tracks': tracks,
                'race_type': race_type,
                'gzip': self.gzip_var.get(),
                'clean': self.clean_var.get(),
                'output_dir': output_dir,
            }
        except Exception as e:
            messagebox.showerror("Error", f"Input parsing error: {str(e)}")
            return None

    def update_progress(self, progress: float, message: str) -> None:
        """Update progress bar and status."""
        self.progress_var.set(progress)
        self.status_label.config(text=message, foreground="blue")
        self.root.update()

    def start_scraping(self) -> None:
        """Start the scraping process in a separate thread."""
        if not self.settings.toml:
            messagebox.showerror("Error", "Settings configuration not found")
            return
        
        config = self.parse_input()
        if not config:
            return
        
        self.scraping = True
        self.cancel_flag = [True]
        self.start_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progress_var.set(0)
        self.status_label.config(text="Initializing...", foreground="blue")
        
        thread = threading.Thread(target=self.scrape_thread, args=(config,), daemon=True)
        thread.start()

    def scrape_thread(self, config: dict) -> None:
        """Run scraping in background thread."""
        try:
            self.log("Initializing scraper...")
            self.update_progress(0, "Connecting to Racing Post...")
            
            # Initialize network client
            self.client = NetworkClient(
                email=os.getenv('EMAIL'),
                auth_state=os.getenv('AUTH_STATE'),
                access_token=os.getenv('ACCESS_TOKEN'),
            )
            
            self.log("Connected successfully")
            self.log(f"Mode: {config['mode']}, Race type: {config['race_type']}")
            self.log(f"Output directory: {config['output_dir']}")
            
            # Build paths
            from rpscrape import clear_request
            
            paths = build_paths(config, config['gzip'])
            
            if config['clean']:
                self.log("Clearing previous request data...")
                clear_request(paths)
            
            # Get race URLs
            self.update_progress(5, "Fetching race URLs...")
            self.log("Fetching race URLs...")
            
            if config['mode'] == 'years':
                race_urls = load_or_save_urls(
                    paths.urls,
                    lambda: get_race_urls(config['years'], config['tracks'], 
                                         config['race_type'], self.client),
                )
            else:
                race_urls = load_or_save_urls(
                    paths.urls,
                    lambda: get_race_urls_date(config['dates'], config['tracks'], self.client),
                )
            
            self.log(f"Found {len(race_urls)} races to scrape")
            
            if not race_urls:
                self.log("No races found!")
                messagebox.showwarning("No Races", "No races found matching your criteria")
                self.update_progress(100, "Completed - no races found")
                return
            
            # Scrape races
            self.update_progress(10, "Starting scrape...")
            file_writer = writer_gzip if config['gzip'] else writer_csv
            
            scrape_races(
                race_urls,
                paths,
                config['race_type'],
                self.client,
                file_writer,
                progress_callback=self.update_progress,
                cancel_flag=self.cancel_flag,
            )
            
            self.log(f"Scraping completed successfully!")
            self.log(f"Output saved to: {paths.output.resolve()}")
            self.update_progress(100, "Completed", )
            self.status_label.config(foreground="green")
            messagebox.showinfo("Success", "Scraping completed successfully!")
            
        except Exception as e:
            self.log(f"Error: {str(e)}")
            self.update_progress(0, "Error occurred", )
            self.status_label.config(foreground="red")
            messagebox.showerror("Error", f"Scraping failed: {str(e)}")
        finally:
            self.scraping = False
            self.start_button.config(state=tk.NORMAL)
            self.stop_button.config(state=tk.DISABLED)

    def stop_scraping(self) -> None:
        """Stop the scraping process."""
        self.cancel_flag[0] = False
        self.log("Stopping scraper...")
        self.status_label.config(text="Stopped", foreground="orange")
        self.start_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)


def main():
    if settings.toml is None:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "Error",
            "Settings configuration not found. Please check your settings file."
        )
        root.destroy()
        return

    root = tk.Tk()
    app = RPScrapeGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()

