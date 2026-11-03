# ui/analytics.py
# Upgraded Analytics module — corrected recorder/DB handling and aggregation bugs.
import threading
import time
from datetime import datetime, timedelta, date
import sqlite3
import csv
import os
import math
import logging

import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

from core.db import POOL
from core.models import fetch_machines, fetch_latest_counter

# try theme
try:
    from ui.theme import apply_theme, CURRENT_THEME
    THEMES_AVAILABLE = True
except Exception:
    THEMES_AVAILABLE = False

TABLE = "analytics_30min"
BASELINE_TABLE = "analytics_baseline"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analytics")

# -------------------------
# DB helpers
# -------------------------
def init_analytics_db():
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id INTEGER,
                ts TEXT,
                clicks INTEGER,
                UNIQUE(machine_id, ts)
            )
        """)
        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {BASELINE_TABLE} (
                machine_id INTEGER PRIMARY KEY,
                last_total INTEGER,
                last_ts TEXT
            )
        """)
        conn.commit()
    finally:
        POOL.put(conn)


def round_to_30(dt: datetime) -> datetime:
    """Round down to the 30-min slot (seconds/microseconds zeroed)."""
    minute = 0 if dt.minute < 30 else 30
    return dt.replace(minute=minute, second=0, microsecond=0)


def fetch_intervals(machine_id=None, days=30):
    """
    Returns list of (datetime, clicks).
    If machine_id is None -> combined (grouped by ts).
    """
    # include last `days` days (inclusive). start is the earliest datetime to include.
    start_dt = datetime.now() - timedelta(days=days)
    start = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    conn = POOL.get()
    try:
        c = conn.cursor()
        if machine_id is None:
            c.execute(f"""
                SELECT ts, SUM(clicks) FROM {TABLE}
                WHERE ts >= ?
                GROUP BY ts
                ORDER BY ts ASC
            """, (start,))
            rows = c.fetchall()
            out = [(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), int(r[1] or 0)) for r in rows]
        else:
            c.execute(f"""
                SELECT ts, clicks FROM {TABLE}
                WHERE machine_id=? AND ts >= ?
                ORDER BY ts ASC
            """, (machine_id, start))
            rows = c.fetchall()
            out = [(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), int(r[1] or 0)) for r in rows]
        return out
    finally:
        POOL.put(conn)


def fetch_intervals_matrix(days=30, machine_id=None, window_start=None, window_end=None):
    """
    Returns matrix (7 x 48), slot labels, day labels and full list of (dt, clicks)
    Optionally filter by window_start/window_end (datetime objects).
    """
    rows = fetch_intervals(machine_id=machine_id, days=days)
    if window_start or window_end:
        filtered = []
        for dt, clicks in rows:
            if window_start and dt < window_start:
                continue
            if window_end and dt > window_end:
                continue
            filtered.append((dt, clicks))
        rows = filtered

    slots = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 30)]
    day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    matrix = np.zeros((7, 48), dtype=int)
    for dt, clicks in rows:
        wk = dt.weekday()
        idx = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
        if 0 <= wk < 7 and 0 <= idx < 48:
            matrix[wk, idx] += clicks
    return matrix, slots, day_labels, rows


def fetch_intervals_for_date(target_date: date, machine_id=None):
    """
    Return list of (datetime, clicks) for the given date (00:00:00 -> 23:59:59).
    If machine_id is None, return combined (grouped by ts across machines).
    """
    start = datetime.combine(target_date, datetime.min.time()).strftime("%Y-%m-%d 00:00:00")
    end = datetime.combine(target_date, datetime.max.time()).strftime("%Y-%m-%d 23:59:59")
    conn = POOL.get()
    try:
        c = conn.cursor()
        if machine_id is None:
            c.execute(f"""
                SELECT ts, SUM(clicks) FROM {TABLE}
                WHERE ts BETWEEN ? AND ?
                GROUP BY ts
                ORDER BY ts ASC
            """, (start, end))
            rows = c.fetchall()
            out = [(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), int(r[1] or 0)) for r in rows]
        else:
            c.execute(f"""
                SELECT ts, clicks FROM {TABLE}
                WHERE machine_id=? AND ts BETWEEN ? AND ?
                ORDER BY ts ASC
            """, (machine_id, start, end))
            rows = c.fetchall()
            out = [(datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S"), int(r[1] or 0)) for r in rows]
        return out
    finally:
        POOL.put(conn)


def fetch_machine_totals_by_date(target_date: date):
    """
    Return list of (machine_id, total_clicks) for the given date, ordered desc.
    """
    start = datetime.combine(target_date, datetime.min.time()).strftime("%Y-%m-%d 00:00:00")
    end = datetime.combine(target_date, datetime.max.time()).strftime("%Y-%m-%d 23:59:59")
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute(f"""
            SELECT machine_id, SUM(clicks) FROM {TABLE}
            WHERE ts BETWEEN ? AND ?
            GROUP BY machine_id
            ORDER BY SUM(clicks) DESC
        """, (start, end))
        rows = c.fetchall()
        return [(int(r[0]), int(r[1] or 0)) for r in rows]
    finally:
        POOL.put(conn)


# -------------------------
# UI: AnalyticsTab (unchanged UI; small logic fixes in refresh)
# -------------------------
class AnalyticsTab:
    def __init__(self, parent, root=None, style=None, rebuild_callback=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()
        self.rebuild_callback = rebuild_callback

        if THEMES_AVAILABLE:
            self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        else:
            self.colors = {
                "BG_FRAME": "#0b1220",
                "BG_CARD": "#16202b",
                "FG_TEXT": "#e5e7eb",
                "ACCENT": "#3b82f6",
            }

        self._BG_FRAME = self.colors.get("BG_FRAME")
        self._BG_CARD = self.colors.get("BG_CARD")
        self._FG_TEXT = self.colors.get("FG_TEXT")
        self._ACCENT = self.colors.get("ACCENT")

        self.frame = ttk.Frame(parent)
        self._page_idx = 0   # pagination index (0-based)
        self._page_idx = 9999   # pagination index (0-based), default to latest
        self._page_size = 7  # days per page (may change to 30 for large ranges)
        self._machine_map = {}
        self._build_ui()

        init_analytics_db()
        # initial refresh
        self.frame.after(200, self.refresh)
        self._schedule_auto_refresh()

    def get_frame(self):
        return self.frame

    def _build_ui(self):
        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=(6, 4))

        title = ttk.Label(top, text="Analytics", font=("Arial", 16, "bold"), foreground=self._FG_TEXT)
        title.pack(side="left")

        btn_export = ttk.Button(top, text="Export CSV", command=self._export_csv)
        btn_export.pack(side="right", padx=(6,0))
        btn_refresh = ttk.Button(top, text="Refresh", command=self.refresh)
        btn_refresh.pack(side="right", padx=(6,0))

        # controls
        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill="x", padx=10, pady=(6, 6))

        ttk.Label(ctrl, text="Range (days):", foreground=self._FG_TEXT).pack(side="left")
        self.range_var = tk.IntVar(value=30)
        self.range_combo = ttk.Combobox(ctrl, textvariable=self.range_var, values=[7,14,30,90,180], width=6, state="readonly")
        self.range_combo.pack(side="left", padx=(4,10))
        self.range_combo.bind("<<ComboboxSelected>>", lambda e: self._on_range_change())

        ttk.Label(ctrl, text="Machine:", foreground=self._FG_TEXT).pack(side="left", padx=(8,4))
        self.machine_var = tk.StringVar(value="All Machines")
        self.machine_combo = ttk.Combobox(ctrl, textvariable=self.machine_var, state="readonly", width=36)
        self.machine_combo.pack(side="left")
        self.machine_combo.bind("<<ComboboxSelected>>", lambda e: self._on_machine_change())

        # date-from/to (for daily detail overrides)
        date_ctrl = ttk.Frame(self.frame)
        date_ctrl.pack(fill="x", padx=10, pady=(2,6))
        ttk.Label(date_ctrl, text="Date From (YYYY-MM-DD):", foreground=self._FG_TEXT).pack(side="left")
        self.date_from_var = tk.StringVar(value="")
        self.date_from_entry = ttk.Entry(date_ctrl, textvariable=self.date_from_var, width=12)
        self.date_from_entry.pack(side="left", padx=(4,8))
        ttk.Label(date_ctrl, text="Date To (YYYY-MM-DD):", foreground=self._FG_TEXT).pack(side="left")
        self.date_to_var = tk.StringVar(value="")
        self.date_to_entry = ttk.Entry(date_ctrl, textvariable=self.date_to_var, width=12)
        self.date_to_entry.pack(side="left", padx=(4,8))
        ttk.Button(date_ctrl, text="Show Daily Detail", command=self._on_show_daily_detail).pack(side="left", padx=(8,0))

        # pagination controls
        page_ctrl = ttk.Frame(self.frame)
        page_ctrl.pack(fill="x", padx=10, pady=(0,6))
        self.prev_btn = ttk.Button(page_ctrl, text="<< Prev", command=self._prev_page)
        self.prev_btn.pack(side="left")
        self.page_lbl = ttk.Label(page_ctrl, text="Page 1", foreground=self._FG_TEXT)
        self.page_lbl.pack(side="left", padx=8)
        self.next_btn = ttk.Button(page_ctrl, text="Next >>", command=self._next_page)
        self.next_btn.pack(side="left")

        # summary / combined average
        self.summary_lbl = ttk.Label(self.frame, text="", foreground=self._FG_TEXT, font=("Arial", 10))
        self.summary_lbl.pack(fill="x", padx=12, pady=(0, 6))

        # scrollable area for charts & machine list
        canvas = tk.Canvas(self.frame, bg=self._BG_FRAME, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True, padx=(6,0), pady=(0,6))
        scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y", pady=(0,6))
        canvas.configure(yscrollcommand=scrollbar.set)

        self.inner = ttk.Frame(canvas)
        self.inner_id = canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(self.inner_id, width=e.width))

        # Chart cards
        self.top_card = ttk.Frame(self.inner)
        self.top_card.pack(fill="x", padx=14, pady=(8,12))

        self.dow_card = ttk.Frame(self.inner)
        self.dow_card.pack(fill="x", padx=14, pady=(0,12))

        self.heatmap_card = ttk.Frame(self.inner)
        self.heatmap_card.pack(fill="x", padx=14, pady=(0,12))

        # machine-by-machine container
        self.machine_list_card = ttk.Frame(self.inner)
        self.machine_list_card.pack(fill="both", expand=True, padx=14, pady=(0,12))

        # daily detail container (OPTION A: stacked daily cards)
        self.daily_detail_card = ttk.Frame(self.inner)
        self.daily_detail_card.pack(fill="both", expand=True, padx=14, pady=(6,12))

        # populate machine selector
        self._build_machine_list()

    def _build_machine_list(self):
        try:
            machines = fetch_machines(active_only=False)
        except Exception:
            machines = []
        items = ["All Machines"]
        self._machine_map = {"All Machines": None}
        for m in machines:
            name = m.get("name") or f"Machine {m['id']}"
            label = f"{name} — {m.get('ip','-')}"
            items.append(label)
            self._machine_map[label] = m["id"]
        self.machine_combo.configure(values=items)
        self.machine_combo.set("All Machines")

    def _on_range_change(self):
        days = int(self.range_var.get())
        # choose page size: weekly for small ranges, monthly for big
        if days <= 30:
            self._page_size = 7
        else:
            self._page_size = 30
        self._page_idx = 0
        self.refresh()

    def _on_machine_change(self):
        self._page_idx = 0
        self.refresh()

    def _prev_page(self):
        if self._page_idx > 0:
            self._page_idx -= 1
            self.refresh()

    def _next_page(self):
        self._page_idx += 1
        self.refresh()

    def _schedule_auto_refresh(self):
        # Auto-refresh every 60 seconds to show incoming live data
        if self.frame.winfo_exists():
            self.refresh()
            self.frame.after(60000, self._schedule_auto_refresh)

    # -------------------------
    # Refresh + page window calculation
    # -------------------------
    def refresh(self):
        days = int(self.range_var.get())
        mid = self._machine_map.get(self.machine_var.get(), None)

        # compute total pages
        total_pages = math.ceil(days / self._page_size) if self._page_size else 1
        if self._page_idx < 0:
            self._page_idx = 0
        if self._page_idx >= total_pages:
            self._page_idx = total_pages - 1 if total_pages > 0 else 0

        # compute page window (inclusive)
        full_end = round_to_30(datetime.now())
        full_start = full_end - timedelta(days=days) + timedelta(seconds=1)
        page_start = full_start + timedelta(days=self._page_idx * self._page_size)
        page_end = page_start + timedelta(days=self._page_size) - timedelta(seconds=1)
        if page_end > full_end:
            page_end = full_end

        # draw page label
        pg_label = "Page {}/{} — {} → {}".format(self._page_idx+1, total_pages, page_start.date().isoformat(), page_end.date().isoformat())
        self.page_lbl.config(text=pg_label)

        # also prefill date-from / date-to with page window if empty
        if not self.date_from_var.get():
            self.date_from_var.set(page_start.date().isoformat())
        if not self.date_to_var.get():
            self.date_to_var.set(page_end.date().isoformat())

        # fetch aggregated data limited to 'days' but we'll filter to the page window
        try:
            matrix, slots, day_labels, raw_rows = fetch_intervals_matrix(days=days, machine_id=mid, window_start=page_start, window_end=page_end)
        except Exception:
            logger.exception("Failed to fetch intervals matrix")
            matrix = np.zeros((7,48), dtype=int)
            slots = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0,30)]
            day_labels = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            raw_rows = []

        # combined_by_slot across the page window
        combined_by_slot = matrix.sum(axis=0)  # 48 slots

        # averages and utilization
        # page_days inclusive
        page_days = (page_end.date() - page_start.date()).days + 1
        total_clicks = int(combined_by_slot.sum())
        avg_per_day = total_clicks / max(1, page_days)
        avg_per_hour = total_clicks / max(1, page_days * 24)
        avg_per_slot = total_clicks / max(1, page_days * 48)

        # compute weekday counts in page window (how many times each weekday occurs)
        weekday_counts = np.zeros(7, dtype=int)
        if raw_rows:
            # count distinct dates per weekday present in the page window
            dates_seen = set()
            for dt, _ in raw_rows:
                dates_seen.add(dt.date())
            for d in dates_seen:
                weekday_counts[d.weekday()] += 1
        else:
            # fallback: evenly distribute days across weekdays for short-sighted view
            for i in range(page_days):
                weekday_counts[(page_start.date() + timedelta(days=i)).weekday()] += 1
        # avoid division by zero
        weekday_counts = np.maximum(weekday_counts, 1)
        weekday_avgs = matrix.sum(axis=1) / weekday_counts

        # Utilization %: percent of non-zero slots during business hours (7am-19pm)
        bh_start = 7 * 2
        bh_end = 19 * 2  # exclusive
        slots_in_bh = np.sum(matrix[:, bh_start:bh_end] > 0)
        total_slots_bh = matrix.shape[0] * (bh_end - bh_start)
        util_pct = (slots_in_bh / total_slots_bh) * 100 if total_slots_bh > 0 else 0.0

        # busiest stats
        busiest_day_idx = int(np.argmax(matrix.sum(axis=1)))
        busiest_day_name = day_labels[busiest_day_idx]
        busiest_day_val = int(matrix.sum(axis=1)[busiest_day_idx])
        hour_totals = np.array([combined_by_slot[i*2] + combined_by_slot[i*2+1] for i in range(24)])
        busiest_hour_idx = int(np.argmax(hour_totals)) if hour_totals.size else 0
        busiest_hour_val = int(hour_totals[busiest_hour_idx]) if hour_totals.size else 0
        busiest_30_idx = int(np.argmax(combined_by_slot)) if combined_by_slot.size else 0
        busiest_30_val = int(combined_by_slot[busiest_30_idx]) if combined_by_slot.size else 0
        busiest_30_label = slots[busiest_30_idx] if slots else "00:00"

        summary = ("Range: last {} days (page {} of {}) • Total prints: {:,} • "
                   "Avg/day: {:.1f} • Avg/hour: {:.2f} • Avg/30min: {:.2f} • "
                   "Util% (7-19): {:.1f}% • Busiest Day: {} ({:,}) • "
                   "Busiest Hour: {:02d}:00–{:02d}:00 ({:,}) • "
                   "Busiest 30-min: {} ({:,})").format(
            days, self._page_idx+1, total_pages, total_clicks,
            avg_per_day, avg_per_hour, avg_per_slot,
            util_pct, busiest_day_name, busiest_day_val,
            busiest_hour_idx, busiest_hour_idx+1, busiest_hour_val,
            busiest_30_label, busiest_30_val
        )
        self.summary_lbl.config(text=summary)

        # clear and draw
        for w in self.top_card.winfo_children(): w.destroy()
        for w in self.dow_card.winfo_children(): w.destroy()
        for w in self.heatmap_card.winfo_children(): w.destroy()
        for w in self.machine_list_card.winfo_children(): w.destroy()
        for w in self.daily_detail_card.winfo_children(): w.destroy()

        self._draw_popular_times(self.top_card, slots, combined_by_slot, page_title="Popular Times (page window)")
        self._draw_dow(self.dow_card, day_labels, matrix.sum(axis=1))
        self._draw_heatmap(self.heatmap_card, matrix, slots, day_labels)

        # per-machine breakdown: show a compact card per machine with top slots + mini-chart
        try:
            machines = fetch_machines(active_only=False)
        except Exception:
            machines = []
        if machines:
            for m in machines:
                self._draw_machine_row(self.machine_list_card, m, days, page_start, page_end)
        else:
            tk.Label(self.machine_list_card, text="No machines configured.", fg=self._FG_TEXT, bg=self._BG_FRAME).pack(padx=10, pady=6)

        # By default show daily detail for the current page window
        self._draw_daily_cards(self.daily_detail_card, page_start, page_end, mid)

    # -------------------------
    # Drawing helpers (unchanged)
    # -------------------------
    def _draw_popular_times(self, parent, labels, values, page_title="Popular Times"):
        lbl = tk.Label(parent, text=page_title, bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 11, "bold"))
        lbl.pack(anchor="w", pady=(4,6))
        fig = Figure(figsize=(10,2.6), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(values))
        bars = ax.bar(x, values, align="center", linewidth=0)

        # ticks every 4 slots (~2 hours)
        step = 4
        ax.set_xticks(np.arange(0, len(labels), step))
        ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)], rotation=45)
        ax.set_ylabel("Prints")
        ax.set_title(f"{page_title}")
        ax.grid(axis="y", linestyle=":", linewidth=0.5)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

        # hover tooltip (best-effort)
        annot = ax.annotate("", xy=(0,0), xytext=(15,15), textcoords="offset points",
                            bbox=dict(boxstyle="round", fc="w"),
                            arrowprops=dict(arrowstyle="->"))
        annot.set_visible(False)

        def update_annot(bar, val):
            x = bar.get_x() + bar.get_width()/2
            y = bar.get_height()
            annot.xy = (x, y)
            annot.set_text(f"{int(val):,} prints")
            annot.get_bbox_patch().set_facecolor("white")
            annot.get_bbox_patch().set_alpha(0.95)

        def hover(event):
            vis = annot.get_visible()
            if event.inaxes == ax:
                for i, bar in enumerate(bars):
                    cont, _ = bar.contains(event)
                    if cont:
                        update_annot(bar, values[i])
                        annot.set_visible(True)
                        canvas.draw_idle()
                        return
            if vis:
                annot.set_visible(False)
                canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", hover)

    def _draw_dow(self, parent, day_labels, day_totals):
        lbl = tk.Label(parent, text="Prints by Day of Week", bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 11, "bold"))
        lbl.pack(anchor="w", pady=(4,6))
        fig = Figure(figsize=(8,2.2), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(day_labels))
        bars = ax.bar(x, day_totals, color=self._ACCENT)
        ax.set_xticks(x)
        ax.set_xticklabels(day_labels)
        ax.set_ylabel("Prints")
        ax.grid(axis="y", linestyle=":", linewidth=0.5)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

        annot = ax.annotate("", xy=(0,0), xytext=(15,15), textcoords="offset points",
                            bbox=dict(boxstyle="round", fc="w"))
        annot.set_visible(False)

        def update_annot(i):
            x = bars[i].get_x() + bars[i].get_width()/2
            y = bars[i].get_height()
            annot.xy = (x, y)
            annot.set_text(f"{int(day_totals[i]):,} prints")
            annot.get_bbox_patch().set_facecolor("white")
            annot.get_bbox_patch().set_alpha(0.95)

        def hover(evt):
            vis = annot.get_visible()
            if evt.inaxes == ax:
                for i, bar in enumerate(bars):
                    cont, _ = bar.contains(evt)
                    if cont:
                        update_annot(i)
                        annot.set_visible(True)
                        canvas.draw_idle()
                        return
            if vis:
                annot.set_visible(False)
                canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", hover)

    def _draw_heatmap(self, parent, matrix, slot_labels, day_labels):
        lbl = tk.Label(parent, text="Heatmap — Day vs 30-min slots (darker = more prints)", bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 11, "bold"))
        lbl.pack(anchor="w", pady=(4,6))

        ticks = [i*2 for i in range(24)]
        ticklabels = [slot_labels[i] for i in ticks]

        fig = Figure(figsize=(12,3.6), dpi=100)
        ax = fig.add_subplot(111)
        im = ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap="YlGnBu")
        ax.set_yticks(np.arange(len(day_labels)))
        ax.set_yticklabels(day_labels)
        ax.set_xticks(ticks)
        ax.set_xticklabels(ticklabels, rotation=45)
        ax.set_xlabel("Time of day")
        ax.set_title("Heatmap (aggregated over page window)")
        fig.colorbar(im, ax=ax, orientation="vertical", fraction=0.05)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    def _draw_machine_row(self, parent, machine, days, page_start, page_end):
        # compact card: left = summary, right = mini chart + top slots
        name = machine.get("name") or f"Machine {machine['id']}"
        mid = machine["id"]

        # fetch matrix & rows for this machine limited to page window
        matrix, slots, day_labels, rows = fetch_intervals_matrix(days=days, machine_id=mid, window_start=page_start, window_end=page_end)
        combined_by_slot = matrix.sum(axis=0)
        total_clicks = int(combined_by_slot.sum())
        page_days = (page_end.date() - page_start.date()).days + 1
        avg_per_day = total_clicks / max(1, page_days)

        # top 3 busiest slots
        top_idx = np.argsort(combined_by_slot)[-3:][::-1] if combined_by_slot.size else []
        top_str = ", ".join([f"{slots[i]} ({int(combined_by_slot[i])})" for i in top_idx]) if top_idx.size else "—"

        outer = tk.Frame(parent, bg=self._BG_CARD)
        outer.pack(fill="x", pady=(6,6))

        left = tk.Frame(outer, bg=self._BG_CARD)
        left.pack(side="left", fill="y", padx=(6,12))

        tk.Label(left, text=name, bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 10, "bold")).pack(anchor="w")
        tk.Label(left, text=f"Total (page): {total_clicks:,}", bg=self._BG_CARD, fg="#9ca3af", font=("Arial", 9)).pack(anchor="w")
        tk.Label(left, text=f"Avg/day: {avg_per_day:.1f}", bg=self._BG_CARD, fg="#9ca3af", font=("Arial", 9)).pack(anchor="w")
        tk.Label(left, text=f"Top slots: {top_str}", bg=self._BG_CARD, fg="#9ca3af", font=("Arial", 9)).pack(anchor="w")

        right = tk.Frame(outer, bg=self._BG_CARD)
        right.pack(side="left", fill="both", expand=True)

        # mini chart
        fig = Figure(figsize=(6,1.2), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(combined_by_slot))
        ax.bar(x, combined_by_slot, linewidth=0)
        ax.set_xticks(np.arange(0, len(slots), 8))
        ax.set_xticklabels([slots[i] for i in range(0, len(slots), 8)], rotation=45, fontsize=7)
        ax.set_title("", fontsize=8)
        ax.set_yticks([])
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=right)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    def _draw_daily_cards(self, parent, page_start, page_end, mid):
        for w in parent.winfo_children(): w.destroy()

        try:
            start_date = page_start.date()
            end_date = page_end.date()
        except Exception:
            start_date = datetime.now().date()
            end_date = start_date

        delta_days = (end_date - start_date).days
        if delta_days < 0:
            return

        for i in range(delta_days + 1):
            d = start_date + timedelta(days=i)
            self._draw_single_day_card(parent, d, mid)

    def _draw_single_day_card(self, parent, day_date, mid):
        outer = tk.Frame(parent, bg=self._BG_CARD, bd=1, relief="solid")
        outer.pack(fill="x", pady=(8,8))

        header = tk.Frame(outer, bg=self._BG_CARD)
        header.pack(fill="x", padx=8, pady=(6,4))
        tk.Label(header, text=f"Day: {day_date.isoformat()}", bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 11, "bold")).pack(side="left")
        rows = fetch_intervals_for_date(day_date, machine_id=mid)
        slots = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0,30)]
        slot_counts = np.zeros(48, dtype=int)
        for dt, clicks in rows:
            idx = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
            if 0 <= idx < 48:
                slot_counts[idx] += clicks
        total = int(slot_counts.sum())

        hour_totals = np.array([slot_counts[i*2] + slot_counts[i*2+1] for i in range(24)])
        if hour_totals.size:
            busiest_hour_idx = int(np.argmax(hour_totals))
            busiest_hour_val = int(hour_totals[busiest_hour_idx])
        else:
            busiest_hour_idx = 0
            busiest_hour_val = 0
        if slot_counts.size:
            busiest_30_idx = int(np.argmax(slot_counts))
            busiest_30_val = int(slot_counts[busiest_30_idx])
            busiest_30_label = slots[busiest_30_idx]
        else:
            busiest_30_idx = 0
            busiest_30_val = 0
            busiest_30_label = "00:00"

        details = f"Total prints: {total:,} • Busiest Hour: {busiest_hour_idx:02d}:00–{busiest_hour_idx+1:02d}:00 ({busiest_hour_val:,}) • Busiest 30-min: {busiest_30_label} ({busiest_30_val:,})"
        tk.Label(outer, text=details, bg=self._BG_CARD, fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", padx=8, pady=(0,4))

        body = tk.Frame(outer, bg=self._BG_CARD)
        body.pack(fill="both", expand=True, padx=8, pady=(4,8))

        left = tk.Frame(body, bg=self._BG_CARD)
        left.pack(side="left", fill="both", expand=True)

        right = tk.Frame(body, bg=self._BG_CARD)
        right.pack(side="left", fill="y", padx=(8,0))

        fig = Figure(figsize=(8,2.0), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(slot_counts))
        ax.bar(x, slot_counts, linewidth=0)
        step = 4
        ax.set_xticks(np.arange(0, len(slots), step))
        ax.set_xticklabels([slots[i] for i in range(0, len(slots), step)], rotation=45, fontsize=8)
        ax.set_ylabel("Prints")
        ax.set_title(f"Prints — {day_date.isoformat()}")
        ax.grid(axis="y", linestyle=":", linewidth=0.5)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=left)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

        tk.Label(right, text="Per-machine totals", bg=self._BG_CARD, fg=self._FG_TEXT, font=("Arial", 10, "bold")).pack(anchor="w")
        machine_totals = fetch_machine_totals_by_date(day_date)
        if not machine_totals:
            tk.Label(right, text="No prints", bg=self._BG_CARD, fg="#9ca3af").pack(anchor="w", pady=(6,0))
        else:
            for mid, tot in machine_totals:
                name = f"Machine {mid}"
                try:
                    machines = fetch_machines(active_only=False)
                    for mm in machines:
                        if mm["id"] == mid:
                            name = mm.get("name") or name
                            break
                except Exception:
                    pass
                tk.Label(right, text=f"{name}: {tot:,}", bg=self._BG_CARD, fg="#9ca3af").pack(anchor="w", pady=(2,0))

    def _mat_style(self, fig, ax):
        try:
            fig.patch.set_facecolor(self._BG_CARD)
            ax.set_facecolor(self._BG_CARD)
            ax.tick_params(colors=self._FG_TEXT, which="both")
            for label in ax.get_xticklabels() + ax.get_yticklabels():
                label.set_color(self._FG_TEXT)
            ax.title.set_color(self._FG_TEXT)
            ax.yaxis.label.set_color(self._FG_TEXT)
            ax.xaxis.label.set_color(self._FG_TEXT)
            for spine in ax.spines.values():
                spine.set_color("#263242")
        except Exception:
            pass

    # -------------------------
    # Export (CSV) for current page and machine
    # -------------------------
    def _export_csv(self):
        days = int(self.range_var.get())
        mid_label = self.machine_var.get()
        mid = self._machine_map.get(mid_label)
        total_pages = math.ceil(days / self._page_size) if self._page_size else 1
        full_end = round_to_30(datetime.now())
        full_start = full_end - timedelta(days=days) + timedelta(seconds=1)
        page_start = full_start + timedelta(days=self._page_idx * self._page_size)
        page_end = page_start + timedelta(days=self._page_size) - timedelta(seconds=1)
        if page_end > full_end:
            page_end = full_end

        rows = fetch_intervals(machine_id=mid, days=days)
        rows = [(dt, clicks) for dt, clicks in rows if dt >= page_start and dt <= page_end]
        if not rows:
            messagebox.showinfo("Export", "No analytics data for selected page/machine.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = (mid_label or "combined").replace(" ", "_").replace("/", "_")
        path = os.path.join(os.path.abspath(os.getcwd()), f"analytics_{safe_name}_page{self._page_idx+1}_{timestamp}.csv")
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["ts", "clicks"])
                for dt, clicks in rows:
                    w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), clicks])
            messagebox.showinfo("Export", f"Exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))

    # -------------------------
    # Daily detail show handler
    # -------------------------
    def _on_show_daily_detail(self):
        df = self.date_from_var.get().strip()
        dt = self.date_to_var.get().strip()
        if not df or not dt:
            self.refresh()
            return
        try:
            d_from = datetime.strptime(df, "%Y-%m-%d").date()
            d_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except Exception:
            messagebox.showerror("Date error", "Enter dates in YYYY-MM-DD format.")
            return
        if d_to < d_from:
            messagebox.showerror("Date error", "Date To must be same or greater than Date From.")
            return

        page_start = datetime.combine(d_from, datetime.min.time())
        page_end = datetime.combine(d_to, datetime.max.time())
        days = (d_to - d_from).days + 1
        summary_text = f"Custom Date Range: {d_from.isoformat()} → {d_to.isoformat()} (days: {days})"
        self.summary_lbl.config(text=summary_text)

        for w in self.daily_detail_card.winfo_children(): w.destroy()
        mid = self._machine_map.get(self.machine_var.get(), None)
        current = d_from
        while current <= d_to:
            self._draw_single_day_card(self.daily_detail_card, current, mid)
            current = current + timedelta(days=1)

# -------------------------
# module-level helpers
# -------------------------
def attach(notebook, root=None, style=None):
    inst = AnalyticsTab(notebook, root=root, style=style)
    frame = inst.get_frame()
    notebook.add(frame, text="Analytics")
    return inst

if __name__ == "__main__":
    init_analytics_db()
