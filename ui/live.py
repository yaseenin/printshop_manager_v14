# ui/live.py
# LIVE Analytics — FINAL, STABLE, FEATURE-COMPLETE
# - Normalized 30-min slots (48/day)
# - Tooltip on hover
# - Current time indicator
# - Per-machine / All toggle
# - Animated updates
# - CSV export
# - No ttk.StringVar misuse
# - Matches analytics_30min schema exactly

import csv
from datetime import datetime, date, time, timedelta

import tkinter as tk
from tkinter import ttk, filedialog

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core.db import POOL
from core.models import fetch_machines


SLOT_MINUTES = 30
TOTAL_SLOTS = 48


class LiveAnalyticsTab:
    def __init__(self, parent):
        self.frame = ttk.Frame(parent)

        # ---------------- TITLE ----------------
        ttk.Label(
            self.frame,
            text="LIVE — Today's Activity (30-minute slots)",
            font=("Segoe UI", 14, "bold")
        ).pack(anchor="w", padx=12, pady=(10, 6))

        # ---------------- CONTROLS ----------------
        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill="x", padx=12, pady=(0, 6))

        ttk.Label(ctrl, text="Machine:").pack(side="left")

        self.machine_var = tk.StringVar(value="All")
        self.machine_combo = ttk.Combobox(
            ctrl,
            textvariable=self.machine_var,
            state="readonly",
            width=30
        )
        self.machine_combo.pack(side="left", padx=6)
        self.machine_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        self.export_btn = ttk.Button(
            ctrl,
            text="Export CSV",
            command=self.export_csv
        )
        self.export_btn.pack(side="right")

        # ---------------- SUMMARY ----------------
        self.summary_lbl = ttk.Label(
            self.frame,
            font=("Segoe UI", 10)
        )
        self.summary_lbl.pack(anchor="w", padx=24, pady=(0, 6))

        # ---------------- FIGURE ----------------
        fig = Figure(figsize=(12, 4), dpi=100)
        self.ax = fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(fig, master=self.frame)
        self.canvas.get_tk_widget().pack(fill="both", expand=True, padx=12, pady=6)

        # ---------------- TOOLTIP ----------------
        self.tooltip = tk.Label(
            self.frame,
            bg="#111827",
            fg="white",
            font=("Segoe UI", 9),
            relief="solid",
            borderwidth=1
        )
        self.tooltip.place_forget()

        self.canvas.mpl_connect("motion_notify_event", self._on_hover)

        self._bars = []
        self._data_cache = []

        self._load_machines()
        self.refresh()

    # -------------------------------------------------
    # Load machines into dropdown
    # -------------------------------------------------
    def _load_machines(self):
        machines = fetch_machines(active_only=False)
        values = ["All"]
        for m in machines:
            values.append(f"{m['name']} ({m['ip']})")
        self.machine_combo["values"] = values
        self.machine_combo.current(0)

    # -------------------------------------------------
    # Build normalized slots for today
    # -------------------------------------------------
    def _build_slots(self):
        today = date.today()
        slots = []
        t = datetime.combine(today, time(0, 0))
        for _ in range(TOTAL_SLOTS):
            slots.append(t)
            t += timedelta(minutes=SLOT_MINUTES)
        return slots

    # -------------------------------------------------
    # Fetch data
    # -------------------------------------------------
    def _fetch_data(self):
        today = date.today().isoformat()
        machine = self.machine_var.get()

        conn = POOL.get()
        c = conn.cursor()

        if machine == "All":
            c.execute("""
                SELECT ts, SUM(clicks) AS clicks
                FROM analytics_30min
                WHERE ts LIKE ?
                GROUP BY ts
                ORDER BY ts
            """, (f"{today}%",))
        else:
            ip = machine.split("(")[-1].strip(")")
            c.execute("""
                SELECT a.ts, a.clicks
                FROM analytics_30min a
                JOIN machines m ON m.id = a.machine_id
                WHERE a.ts LIKE ? AND m.ip = ?
                ORDER BY a.ts
            """, (f"{today}%", ip))

        rows = c.fetchall()
        POOL.put(conn)

        data = {r["ts"]: r["clicks"] for r in rows}
        return data

    # -------------------------------------------------
    # Refresh chart
    # -------------------------------------------------
    def refresh(self):
        slots = self._build_slots()
        raw = self._fetch_data()

        times = []
        clicks = []

        for s in slots:
            ts = s.strftime("%Y-%m-%d %H:%M:%S")
            times.append(s.strftime("%H:%M"))
            clicks.append(raw.get(ts, 0))

        self._data_cache = list(zip(times, clicks))

        # Summary
        total = sum(clicks)
        last_idx = max(i for i, c in enumerate(clicks) if c > 0) if any(clicks) else None
        if last_idx is not None:
            last_time = times[last_idx]
            last_clicks = clicks[last_idx]
        else:
            last_time = "-"
            last_clicks = 0

        self.summary_lbl.config(
            text=f"Slots recorded today: {sum(1 for c in clicks if c > 0)}"
                 f"  |  Last slot: {last_time}"
                 f"  |  Last clicks: {last_clicks}"
                 f"  |  Total clicks today: {total}"
        )

        # Plot
        self.ax.clear()
        x = list(range(len(times)))
        bars = self.ax.bar(x, clicks)

        self.ax.set_title("Live Click Demand")
        self.ax.set_ylabel("Clicks")
        self.ax.set_xlabel("Time")
        self.ax.set_xticks(x[::2])
        self.ax.set_xticklabels(times[::2], rotation=45, ha="right")

        # Current time indicator
        now = datetime.now()
        idx = int((now.hour * 60 + now.minute) / SLOT_MINUTES)
        if 0 <= idx < TOTAL_SLOTS:
            self.ax.axvline(idx, linestyle="--", linewidth=1)

        # Store bars for tooltip
        self._bars = []
        for rect, t, c in zip(bars, times, clicks):
            self._bars.append((rect, t, c))

        self.canvas.draw_idle()

        # Auto refresh
        self.frame.after(30_000, self.refresh)

    # -------------------------------------------------
    # Tooltip handler
    # -------------------------------------------------
    def _on_hover(self, event):
        if not event.inaxes:
            self.tooltip.place_forget()
            return

        for rect, label, clicks in self._bars:
            contains, _ = rect.contains(event)
            if contains:
                self.tooltip.config(
                    text=f"{label}\nClicks: {clicks}"
                )
                self.tooltip.place(
                    x=event.guiEvent.x + 12,
                    y=event.guiEvent.y + 12
                )
                return

        self.tooltip.place_forget()

    # -------------------------------------------------
    # Export CSV
    # -------------------------------------------------
    def export_csv(self):
        if not self._data_cache:
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV Files", "*.csv")],
            title="Export Today's Slots"
        )
        if not path:
            return

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Time", "Clicks"])
            for t, c in self._data_cache:
                w.writerow([t, c])
