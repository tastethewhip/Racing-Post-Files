import tkinter as tk
from tkinter import filedialog, messagebox
import json
import csv
import os

class JSONToCSVConverter:
    def __init__(self, root):
        self.root = root
        self.root.title("JSON to CSV Converter")
        self.root.geometry("700x500")
        self.root.configure(bg="#1e1e1e")

        self.files = []

        title = tk.Label(
            root,
            text="JSON to CSV Converter",
            font=("Arial", 20, "bold"),
            bg="#1e1e1e",
            fg="white"
        )
        title.pack(pady=15)

        btn_frame = tk.Frame(root, bg="#1e1e1e")
        btn_frame.pack(pady=10)

        self.add_btn = tk.Button(
            btn_frame,
            text="Add JSON Files",
            command=self.add_files,
            bg="#4CAF50",
            fg="white",
            width=20,
            height=2
        )
        self.add_btn.grid(row=0, column=0, padx=10)

        self.clear_btn = tk.Button(
            btn_frame,
            text="Clear List",
            command=self.clear_files,
            bg="#f44336",
            fg="white",
            width=20,
            height=2
        )
        self.clear_btn.grid(row=0, column=1, padx=10)

        self.convert_btn = tk.Button(
            root,
            text="Convert Selected Files to CSV",
            command=self.convert_files,
            bg="#2196F3",
            fg="white",
            width=35,
            height=2
        )
        self.convert_btn.pack(pady=15)

        self.listbox = tk.Listbox(
            root,
            width=90,
            height=18,
            bg="#2d2d2d",
            fg="white",
            selectbackground="#555555"
        )
        self.listbox.pack(padx=20, pady=10)

        info = tk.Label(
            root,
            text="Supports multiple JSON files",
            bg="#1e1e1e",
            fg="#bbbbbb"
        )
        info.pack(pady=5)

    def add_files(self):
        file_paths = filedialog.askopenfilenames(
            title="Select JSON Files",
            filetypes=[("JSON Files", "*.json")]
        )

        for file in file_paths:
            if file not in self.files:
                self.files.append(file)
                self.listbox.insert(tk.END, file)

    def clear_files(self):
        self.files.clear()
        self.listbox.delete(0, tk.END)

    def flatten_json(self, y, parent_key='', sep='_'):
        items = []

        if isinstance(y, dict):
            for k, v in y.items():
                new_key = f"{parent_key}{sep}{k}" if parent_key else k
                items.extend(self.flatten_json(v, new_key, sep=sep).items())

        elif isinstance(y, list):
            if len(y) > 0:
                if isinstance(y[0], dict):
                    for i, item in enumerate(y):
                        items.extend(
                            self.flatten_json(
                                item,
                                f"{parent_key}{sep}{i}",
                                sep=sep
                            ).items()
                        )
                else:
                    items.append((parent_key, ", ".join(map(str, y))))
            else:
                items.append((parent_key, ""))

        else:
            items.append((parent_key, y))

        return dict(items)

    def extract_runners(self, data):
        rows = []

        try:
            for region_name, region_data in data.items():
                for course_name, course_data in region_data.items():
                    for race_time, race in course_data.items():

                        base_race = {
                            "region": race.get("region"),
                            "course": race.get("course"),
                            "date": race.get("date"),
                            "off_time": race.get("off_time"),
                            "race_name": race.get("race_name"),
                            "distance": race.get("distance"),
                            "going": race.get("going"),
                            "race_class": race.get("race_class"),
                            "field_size": race.get("field_size"),
                        }

                        runners = race.get("runners", [])

                        for runner in runners:
                            row = base_race.copy()

                            runner_data = {
                                "horse_name": runner.get("name"),
                                "age": runner.get("age"),
                                "sex": runner.get("sex"),
                                "trainer": runner.get("trainer"),
                                "trainer_rtf": runner.get("trainer_rtf"),
                                "trainer_14_days": runner.get("trainer_14_days"),
                                "stable_tour": runner.get("stable_tour"),
                                "stats": runner.get("stats"),
                                "jockey": runner.get("jockey"),
                                "owner": runner.get("owner"),
                                "draw": runner.get("draw"),
                                "lbs": runner.get("lbs"),
                                "form": runner.get("form"),
                                "rpr": runner.get("rpr"),
                                "ts": runner.get("ts"),
                                "medical": runner.get("medical"),
                                "spotlight": runner.get("spotlight"),
                                "comment": runner.get("comment"),
                                "sire": runner.get("sire"),
                                "dam": runner.get("dam"),
                            }

                            row.update(runner_data)
                            rows.append(row)

        except Exception as e:
            print("Error reading racing structure:", e)

        return rows

    def convert_files(self):
        if not self.files:
            messagebox.showwarning("No Files", "Please add JSON files first.")
            return

        success = 0

        for file_path in self.files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                rows = self.extract_runners(data)

                if not rows:
                    # Generic flatten fallback
                    rows = [self.flatten_json(data)]

                output_file = os.path.splitext(file_path)[0] + ".csv"

                headers = set()
                for row in rows:
                    headers.update(row.keys())

                headers = sorted(headers)

                with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                    writer = csv.DictWriter(csvfile, fieldnames=headers)
                    writer.writeheader()

                    for row in rows:
                        writer.writerow(row)

                success += 1

            except Exception as e:
                messagebox.showerror(
                    "Conversion Error",
                    f"Failed to convert:\n{file_path}\n\n{e}"
                )

        messagebox.showinfo(
            "Finished",
            f"Successfully converted {success} file(s) to CSV."
        )

if __name__ == "__main__":
    root = tk.Tk()
    app = JSONToCSVConverter(root)
    root.mainloop()
