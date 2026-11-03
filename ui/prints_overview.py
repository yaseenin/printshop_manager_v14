# ui/prints_overview.py
# Prints Overview Tab — insight-first dashboard with calendar date ranges

import csv
import threading
from datetime import date, datetime, timedelta
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from ui.analytics import fetch_intervals_for_date, fetch_machine_totals_by_date
from core.models import fetch_machines
from ui.theme import apply_theme, CURRENT_THEME

try:
    from tkcalendar import DateEntry
    TKCAL_AVAILABLE = True
except Exception:
    TKCAL_AVAILABLE = False

try:
    import mplcursors
    MPLCURSORS_AVAILABLE = True
except Exception:
    MPLCURSORS_AVAILABLE = False


class PrintsOverviewTab:
    def __init__(self, parent, root=None, style=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()

        self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        self._BG_FRAME = self.colors.get("BG_FRAME", "#111827")
        self._BG_CARD = self.colors.get("BG_CARD", "#1e2933")
        self._FG_TEXT = self.colors.get("FG_TEXT", "#e5e7eb")
        self._ACCENT = self.colors.get("ACCENT_BLUE", "#38bdf8")
        self._SUCCESS = self.colors.get("ONLINE_GREEN", "#22c55e")

        self.frame = ttk.Frame(parent)
        self._loading = False

        self._build_ui()
        self._load_machines()
        self._set_range(7)
        self.frame.after(400, self.refresh)

    def get_frame(self):
        return self.frame

    def _build_ui(self):
        header = ttk.Frame(self.frame)
        header.pack(fill="x", padx=12, pady=(10, 8))

        ttk.Label(header, text="Prints Overview", font=("Arial", 18, "bold")).pack(side="left")

        range_row = ttk.Frame(header)
        range_row.pack(side="right")

        for days, label in ((7, "7D"), (30, "30D"), (90, "90D")):
            ttk.Button(range_row, text=label, width=5, command=lambda d=days: self._set_range(d)).pack(side="left", padx=3)

        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill="x", padx=12, pady=(0, 8))

        if TKCAL_AVAILABLE:
            self.start_date = DateEntry(ctrl, width=12, background="#1e2933", foreground="#e5e7eb", borderwidth=1)
            self.end_date = DateEntry(ctrl, width=12, background="#1e2933", foreground="#e5e7eb", borderwidth=1)
            ttk.Label(ctrl, text="From").pack(side="left", padx=(0, 4))
            self.start_date.pack(side="left")
            ttk.Label(ctrl, text="To").pack(side="left", padx=(8, 4))
            self.end_date.pack(side="left")
        else:
            self.start_date = ttk.Entry(ctrl, width=12)
            self.end_date = ttk.Entry(ctrl, width=12)
            ttk.Label(ctrl, text="From").pack(side="left", padx=(0, 4))
            self.start_date.pack(side="left")
            ttk.Label(ctrl, text="To").pack(side="left", padx=(8, 4))
            self.end_date.pack(side="left")

        ttk.Label(ctrl, text="Machine").pack(side="left", padx=(12, 4))
        self.machine_var = tk.StringVar(value="All")
        self.machine_combo = ttk.Combobox(ctrl, width=24, state="readonly")
        self.machine_combo.pack(side="left")

        ttk.Button(ctrl, text="Apply", command=self.refresh, width=8).pack(side="left", padx=(10, 4))
        ttk.Button(ctrl, text="Table", command=self._open_hourly_table, width=8).pack(side="left", padx=(6, 4))
        ttk.Button(ctrl, text="Export CSV", command=self._export_csv, width=10).pack(side="left")

        self.summary_frame = ttk.Frame(self.frame)
        self.summary_frame.pack(fill="x", padx=12, pady=(0, 8))

        self.metric_cards = []
        for title, key in (
            ("Total Prints", "total"),
            ("Average / Day", "avg_day"),
            ("Peak Day", "peak_day"),
            ("Peak Hour", "peak_hour"),
        ):
            card = ttk.Frame(self.summary_frame, padding=10)
            card.pack(side="left", fill="y", expand=True, padx=(0, 8))
            ttk.Label(card, text=title, font=("Arial", 9, "bold")).pack(anchor="w")
            val = ttk.Label(card, text="—", font=("Arial", 15, "bold"), foreground=self._ACCENT)
            val.pack(anchor="w", pady=(6, 0))
            setattr(self, f"_{key}_label", val)
            self.metric_cards.append(card)

        self.chart_frame = ttk.Frame(self.frame)
        self.chart_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self.fig = Figure(figsize=(10, 6), dpi=100)
        self.ax_hourly = self.fig.add_subplot(2, 2, 1)
        self.ax_heat = self.fig.add_subplot(2, 2, 2)
        self.ax_pie = self.fig.add_subplot(2, 2, 3)
        self.ax_top = self.fig.add_subplot(2, 2, 4)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        # store last dataset for table view
        self._last_matrix = None
        self._last_dates = None

    def _set_range(self, days):
        end_dt = date.today()
        start_dt = end_dt - timedelta(days=max(days - 1, 0))
        self._set_date_values(start_dt, end_dt)
        self.refresh()

    def _set_date_values(self, start_dt, end_dt):
        if TKCAL_AVAILABLE:
            self.start_date.set_date(start_dt)
            self.end_date.set_date(end_dt)
        else:
            self.start_date.delete(0, tk.END)
            self.end_date.delete(0, tk.END)
            self.start_date.insert(0, start_dt.strftime("%Y-%m-%d"))
            self.end_date.insert(0, end_dt.strftime("%Y-%m-%d"))

    def _parse_date_value(self, value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d").date()
        except Exception:
            return datetime.strptime(str(value), "%m/%d/%y").date() if "/" in str(value) else None

    def _get_range(self):
        start_value = getattr(self.start_date, "get_date", lambda: self.start_date.get)()
        end_value = getattr(self.end_date, "get_date", lambda: self.end_date.get)()
        start_dt = self._parse_date_value(start_value()) if callable(start_value) else self._parse_date_value(start_value)
        end_dt = self._parse_date_value(end_value()) if callable(end_value) else self._parse_date_value(end_value)

        if start_dt is None or end_dt is None:
            end_dt = date.today()
            start_dt = end_dt - timedelta(days=6)
        if end_dt < start_dt:
            start_dt, end_dt = end_dt, start_dt
        return start_dt, end_dt

    def _load_machines(self):
        try:
            machines = fetch_machines(active_only=False)
            items = ["All"] + [f"{m['id']} - {m['name']}" for m in machines]
            self.machine_combo["values"] = items
            self.machine_combo.set(items[0] if items else "All")
        except Exception:
            self.machine_combo["values"] = ["All"]
            self.machine_combo.set("All")

    def refresh(self):
        if self._loading:
            return
        self._loading = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            start_dt, end_dt = self._get_range()
            days = (end_dt - start_dt).days + 1
            sel = self.machine_combo.get() or "All"
            machine_id = None
            if sel != "All" and " - " in sel:
                try:
                    machine_id = int(sel.split(" - ")[0])
                except Exception:
                    machine_id = None

            dates = [start_dt + timedelta(days=i) for i in range(days)]
            matrix_hours = [[0 for _ in range(days)] for _ in range(24)]
            hourly = [0 for _ in range(24)]
            daily_totals = {}
            totals_by_machine = {}

            for col, d in enumerate(dates):
                try:
                    rows = fetch_intervals_for_date(d, machine_id=machine_id)
                except Exception:
                    rows = []

                day_total = 0
                for dt, clicks in rows:
                    h = dt.hour
                    val = int(clicks or 0)
                    matrix_hours[h][col] += val
                    hourly[h] += val
                    day_total += val
                daily_totals[d.strftime("%Y-%m-%d")] = day_total

                if machine_id is None:
                    try:
                        for mid, total in fetch_machine_totals_by_date(d):
                            totals_by_machine[mid] = totals_by_machine.get(mid, 0) + total
                    except Exception:
                        continue

            if machine_id is not None:
                totals_by_machine[machine_id] = sum(hourly)

            items = sorted(totals_by_machine.items(), key=lambda x: x[1], reverse=True)
            pie_labels = []
            pie_sizes = []
            other_sum = 0
            for idx, (mid, val) in enumerate(items):
                if idx < 6:
                    pie_labels.append(str(mid))
                    pie_sizes.append(val)
                else:
                    other_sum += val
            if other_sum > 0:
                pie_labels.append("Other")
                pie_sizes.append(other_sum)

            total_prints = sum(hourly)
            avg_day = total_prints / len(dates) if dates else 0
            peak_day = max(daily_totals.items(), key=lambda item: item[1]) if daily_totals else ("—", 0)
            peak_hour = max(range(24), key=lambda h: hourly[h]) if sum(hourly) > 0 else None
            busiest_machine = max(items, key=lambda x: x[1]) if items else (None, 0)

            self.frame.after(0, lambda: self._update_dashboard(
                hourly=hourly,
                pie_labels=pie_labels,
                pie_sizes=pie_sizes,
                dates=dates,
                matrix_hours=matrix_hours,
                items=items,
                total_prints=total_prints,
                avg_day=avg_day,
                peak_day=peak_day,
                peak_hour=peak_hour,
                busiest_machine=busiest_machine,
                machine_name=(self.machine_combo.get() or "All"),
            ))
        finally:
            self._loading = False

    def _update_dashboard(self, hourly, pie_labels, pie_sizes, dates, matrix_hours, items, total_prints, avg_day,
                          peak_day, peak_hour, busiest_machine, machine_name):
        try:
            self._total_label.configure(text=f"{total_prints:,}")
            self._avg_day_label.configure(text=f"{avg_day:,.0f}")
            peak_label = peak_day[0] if isinstance(peak_day, tuple) else "—"
            peak_value = peak_day[1] if isinstance(peak_day, tuple) else 0
            self._peak_day_label.configure(text=f"{peak_label} ({peak_value:,})")
            if peak_hour is None:
                self._peak_hour_label.configure(text="—")
            else:
                self._peak_hour_label.configure(text=f"{peak_hour:02d}:00")

            # Build hourly chart
            self.ax_hourly.clear()
            if sum(hourly) > 0:
                self.ax_hourly.bar(range(24), hourly, color=self._ACCENT, alpha=0.9)
                self.ax_hourly.set_title("Hourly print volume")
                self.ax_hourly.set_xlabel("Hour")
                self.ax_hourly.set_ylabel("Prints")
                self.ax_hourly.set_xticks(range(0, 24, 2))
            else:
                self.ax_hourly.text(0.5, 0.5, "No data", ha="center", va="center")
                self.ax_hourly.set_axis_off()

            # Heatmap
            self.ax_heat.clear()
            if matrix_hours and dates and any(sum(row) for row in matrix_hours):
                import numpy as np
                arr = np.array(matrix_hours)
                im = self.ax_heat.imshow(arr, aspect="auto", cmap="viridis", origin="lower")
                # annotate cells with values
                for (i, j), val in np.ndenumerate(arr):
                    if val != 0:
                        self.ax_heat.text(j, i, f"{int(val)}", ha="center", va="center", color="white", fontsize=8)
                self.ax_heat.set_title("Daily heatmap")
                self.ax_heat.set_xlabel("Day")
                self.ax_heat.set_ylabel("Hour")
                self.ax_heat.set_xticks(range(0, len(dates), max(1, len(dates) // 6)))
                self.ax_heat.set_xticklabels([d.strftime("%m-%d") for i, d in enumerate(dates) if i % max(1, len(dates) // 6) == 0], rotation=30, ha="right")
                self.fig.colorbar(im, ax=self.ax_heat, fraction=0.045, pad=0.04)
                # optionally enable mplcursors tooltips for the heatmap
                if MPLCURSORS_AVAILABLE:
                    try:
                        mplcursors.cursor(im, hover=True).connect(
                            "add",
                            lambda sel: sel.annotation.set_text(str(int(arr[int(round(sel.target[1]))][int(round(sel.target[0]))])))
                        )
                    except Exception:
                        pass
            else:
                self.ax_heat.text(0.5, 0.5, "No data", ha="center", va="center")
                self.ax_heat.set_axis_off()

            # Machine pie
            self.ax_pie.clear()
            if pie_sizes and sum(pie_sizes) > 0:
                labels_nice = []
                try:
                    mid_map = {m["id"]: m.get("name", str(m["id"])) for m in fetch_machines(active_only=False)}
                    for lbl in pie_labels:
                        if lbl == "Other":
                            labels_nice.append("Other")
                        else:
                            try:
                                mid = int(lbl)
                                labels_nice.append(mid_map.get(mid, str(mid)))
                            except Exception:
                                labels_nice.append(lbl)
                except Exception:
                    labels_nice = pie_labels

                self.ax_pie.pie(pie_sizes, labels=labels_nice, autopct="%1.1f%%", startangle=140)
                self.ax_pie.set_title("Machine split")
            else:
                self.ax_pie.text(0.5, 0.5, "No data", ha="center", va="center")
                self.ax_pie.set_axis_off()

            # Top machines list
            self.ax_top.clear()
            if items:
                top_items = sorted(items, key=lambda x: x[1], reverse=True)[:8]
                names = []
                values = []
                try:
                    mid_map = {m["id"]: m.get("name", str(m["id"])) for m in fetch_machines(active_only=False)}
                    for mid, val in top_items:
                        names.append(mid_map.get(mid, str(mid)))
                        values.append(val)
                except Exception:
                    names = [str(mid) for mid, _ in top_items]
                    values = [val for _, val in top_items]
                self.ax_top.barh(names[::-1], values[::-1], color="#22c55e")
                self.ax_top.set_title("Top machines")
                self.ax_top.set_xlabel("Prints")
            else:
                self.ax_top.text(0.5, 0.5, "No data", ha="center", va="center")
                self.ax_top.set_axis_off()

            self.fig.tight_layout()
            self.canvas.draw_idle()
            # save for table view
            self._last_matrix = matrix_hours
            self._last_dates = dates
            # enable mplcursors for other charts if available
            if MPLCURSORS_AVAILABLE:
                try:
                    # hourly bars
                    if sum(hourly) > 0:
                        bars = self.ax_hourly.patches
                        mplcursors.cursor(bars, hover=True).connect(
                            "add",
                            lambda sel: sel.annotation.set_text(f"{int(sel.artist.get_height()):,} prints at {int(sel.artist.get_x()):02d}:00")
                        )
                    # pie slices
                    for w in getattr(self.ax_pie, 'containers', []):
                        try:
                            mplcursors.cursor(w, hover=True)
                        except Exception:
                            continue
                except Exception:
                    pass
        except Exception:
            pass

    def _export_csv(self):
        try:
            start_dt, end_dt = self._get_range()
            target = filedialog.asksaveasfilename(
                defaultextension=".csv",
                initialfile=f"prints_{start_dt:%Y-%m-%d}_to_{end_dt:%Y-%m-%d}.csv",
                title="Export print overview"
            )
            if not target:
                return

            dates = [start_dt + timedelta(days=i) for i in range((end_dt - start_dt).days + 1)]
            with open(target, "w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["date", "prints"])
                for single_date in dates:
                    rows = fetch_intervals_for_date(single_date, machine_id=None)
                    daily_total = sum(int(clicks or 0) for _, clicks in rows)
                    writer.writerow([single_date.strftime("%Y-%m-%d"), daily_total])
            messagebox.showinfo("Export complete", f"CSV saved to:\n{target}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))


    def _open_hourly_table(self):
        # Open a dialog that shows days as rows and hours as columns
        if not self._last_matrix or not self._last_dates:
            messagebox.showinfo("No data", "No dataset available for table view. Apply a date range first.")
            return

        top = tk.Toplevel(self.frame)
        top.title("Hourly prints table")
        top.geometry("1000x400")

        cols = ["Date"] + [f"{h:02d}:00" for h in range(24)]
        tree = ttk.Treeview(top, columns=cols, show="headings")
        tree.pack(fill="both", expand=True)

        # configure headings
        for c in cols:
            tree.heading(c, text=c)
            tree.column(c, width=80 if c == "Date" else 60, anchor='center')

        # insert rows: dates as rows, matrix is hours x days
        for col_idx, d in enumerate(self._last_dates):
            row = []
            for h in range(24):
                try:
                    val = int(self._last_matrix[h][col_idx])
                except Exception:
                    val = 0
                row.append(str(val))
            tree.insert('', 'end', values=[d.strftime('%Y-%m-%d')] + row)

        # add a vertical scrollbar
        vsb = ttk.Scrollbar(top, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")


def get_frame():
    return PrintsOverviewTab
