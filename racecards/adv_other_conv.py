import tkinter as tk
from tkinter import filedialog, messagebox
import json
import csv
import os

class JSONToCSVConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("Advanced JSON to CSV Converter")
        self.root.geometry("850x600")
        self.root.configure(bg="#1e1e1e")

        self.files = []

        title = tk.Label(
            root,
            text="Advanced JSON → CSV Converter",
            font=("Arial", 22, "bold"),
            bg="#1e1e1e",
            fg="white"
        )
        title.pack(pady=15)

        button_frame = tk.Frame(root, bg="#1e1e1e")
        button_frame.pack(pady=10)

        tk.Button(
            button_frame,
            text="Add JSON Files",
            command=self.add_files,
            bg="#4CAF50",
            fg="white",
            width=22,
            height=2
        ).grid(row=0, column=0, padx=10)

        tk.Button(
            button_frame,
            text="Clear List",
            command=self.clear_files,
            bg="#f44336",
            fg="white",
            width=22,
            height=2
        ).grid(row=0, column=1, padx=10)

        tk.Button(
            root,
            text="Convert ALL Fields to CSV",
            command=self.convert_files,
            bg="#2196F3",
            fg="white",
            width=40,
            height=2
        ).pack(pady=15)

        self.listbox = tk.Listbox(
            root,
            width=120,
            height=22,
            bg="#2d2d2d",
            fg="white",
            selectbackground="#555555"
        )
        self.listbox.pack(padx=20, pady=10)

        status = tk.Label(
            root,
            text="Every nested field will be extracted into CSV columns",
            bg="#1e1e1e",
            fg="#bbbbbb"
        )
        status.pack()

    def add_files(self):
        files = filedialog.askopenfilenames(
            title="Select JSON Files",
            filetypes=[("JSON Files", "*.json")]
        )

        for file in files:
            if file not in self.files:
                self.files.append(file)
                self.listbox.insert(tk.END, file)

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, tk.END)

    def flatten_json(self, obj, parent_key=""):
        """
        Fully flatten ANY JSON structure.
        Handles:
        - dictionaries
        - nested dictionaries
        - lists
        - lists of dictionaries
        - deeply nested objects
        """

        items = {}

        if isinstance(obj, dict):
            for key, value in obj.items():
                new_key = f"{parent_key}.{key}" if parent_key else key
                items.update(self.flatten_json(value, new_key))

        elif isinstance(obj, list):

            if len(obj) == 0:
                items[parent_key] = ""

            else:
                for index, item in enumerate(obj):
                    new_key = f"{parent_key}[{index}]"
                    items.update(self.flatten_json(item, new_key))

        else:
            items[parent_key] = obj

        return items

    def extract_rows(self, data):
        """
        Creates one CSV row per runner if runners exist.
        Otherwise flattens whole document into one row.
        """

        rows = []

        try:
            # RacingPost-style structure
            for region_key, region in data.items():

                if not isinstance(region, dict):
                    continue

                for course_key, course in region.items():

                    if not isinstance(course, dict):
                        continue

                    for race_key, race in course.items():

                        if not isinstance(race, dict):
                            continue

                        race_flat = self.flatten_json(race)

                        runners = race.get("runners", [])

                        if runners and isinstance(runners, list):

                            for i, runner in enumerate(runners):

                                row = dict(race_flat)

                                # Flatten runner separately
                                runner_flat = self.flatten_json(
                                    runner,
                                    f"runner"
                                )

                                row.update(runner_flat)

                                rows.append(row)

                        else:
                            rows.append(race_flat)

        except Exception:
            # Generic JSON fallback
            rows.append(self.flatten_json(data))

        return rows

    def convert_files(self):

        if not self.files:
            messagebox.showwarning(
                "No Files",
                "Please add JSON files first."
            )
            return

        converted = 0

        for file_path in self.files:

            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                rows = self.extract_rows(data)

                if not rows:
                    rows = [self.flatten_json(data)]

                # Collect ALL possible fields
                headers = set()

                for row in rows:
                    headers.update(row.keys())

                headers = sorted(headers)

                output_file = os.path.splitext(file_path)[0] + ".csv"

                with open(
                    output_file,
                    "w",
                    newline="",
                    encoding="utf-8"
                ) as csvfile:

                    writer = csv.DictWriter(
                        csvfile,
                        fieldnames=headers,
                        extrasaction="ignore"
                    )

                    writer.writeheader()

                    for row in rows:
                        writer.writerow(row)

                converted += 1

            except Exception as e:

                messagebox.showerror(
                    "Error",
                    f"Failed to convert:\n\n{file_path}\n\n{str(e)}"
                )

        messagebox.showinfo(
            "Finished",
            f"Successfully converted {converted} file(s)."
        )

if __name__ == "__main__":
    root = tk.Tk()
    app = JSONToCSVConverter(root)
    root.mainloop()
