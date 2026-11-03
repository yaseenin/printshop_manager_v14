# ui/analytics.py
# Improved Analytics module — user-friendly UI with better organization and clarity
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


def fetch_intervals_matrix(days=30, machine_id=None, window_start=None, window_end=None, aggregate_weekday=True):
    """
    Returns a matrix of activity aggregated into 30-min slots.

    By default (aggregate_weekday=True) returns a (7 x 48) matrix aggregated by weekday
    (Mon..Sun), a list of slot labels, weekday labels and the raw rows.

    If aggregate_weekday=False the function returns a (D x 48) matrix where D is the
    number of days in the requested window (each row is a single date), slot labels,
    date labels (YYYY-MM-DD) and the raw rows.

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

    if aggregate_weekday:
        day_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        matrix = np.zeros((7, 48), dtype=int)
        for dt, clicks in rows:
            wk = dt.weekday()
            idx = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
            if 0 <= wk < 7 and 0 <= idx < 48:
                matrix[wk, idx] += clicks
        return matrix, slots, day_labels, rows
    else:
        # build a day-by-day matrix (rows = number of days in window)
        if not rows:
            return np.zeros((0, 48), dtype=int), slots, [], rows
        # determine date range from rows
        dates = sorted({dt.date() for dt, _ in rows})
        # ensure contiguous dates covering requested window if possible
        min_date = min(dates)
        max_date = max(dates)
        ndays = (max_date - min_date).days + 1
        date_list = [min_date + timedelta(days=i) for i in range(ndays)]
        matrix = np.zeros((ndays, 48), dtype=int)
        date_labels = [d.strftime("%Y-%m-%d") for d in date_list]
        for dt, clicks in rows:
            day_idx = (dt.date() - min_date).days
            slot_idx = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
            if 0 <= day_idx < ndays and 0 <= slot_idx < 48:
                matrix[day_idx, slot_idx] += clicks
        return matrix, slots, date_labels, rows


def _fallback_intervals_for_date(target_date: date, machine_id=None):
    """Fallback for empty analytics_30min tables: derive hourly clicks from machine counters."""
    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date, datetime.max.time())
    date_str = target_date.strftime("%Y-%m-%d")

    conn = POOL.get()
    try:
        c = conn.cursor()
        if machine_id is None:
            # combine all machines, bucket by hour using the delta between consecutive snapshots
            c.execute(
                "SELECT machine_id, timestamp, total_impressions FROM machine_counters WHERE date(timestamp)=? ORDER BY machine_id, timestamp ASC",
                (date_str,),
            )
            rows = c.fetchall()
            hour_totals = {}
            prev = {}
            for mid, ts, total in rows:
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
                if dt < start or dt > end:
                    continue
                prev_total = prev.get(mid, 0)
                delta = max(0, int(total or 0) - prev_total)
                if delta > 0:
                    bucket = dt.replace(minute=0, second=0, microsecond=0)
                    hour_totals[bucket] = hour_totals.get(bucket, 0) + delta
                prev[mid] = int(total or 0)
            return [(dt, clicks) for dt, clicks in sorted(hour_totals.items())]

        c.execute(
            "SELECT timestamp, total_impressions FROM machine_counters WHERE machine_id=? AND date(timestamp)=? ORDER BY timestamp ASC",
            (machine_id, date_str),
        )
        rows = c.fetchall()
        hour_totals = {}
        prev_total = None
        for ts, total in rows:
            dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            if dt < start or dt > end:
                continue
            total = int(total or 0)
            if prev_total is not None:
                delta = max(0, total - prev_total)
                if delta > 0:
                    bucket = dt.replace(minute=0, second=0, microsecond=0)
                    hour_totals[bucket] = hour_totals.get(bucket, 0) + delta
            prev_total = total
        return [(dt, clicks) for dt, clicks in sorted(hour_totals.items())]
    finally:
        POOL.put(conn)


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
        if not out:
            out = _fallback_intervals_for_date(target_date, machine_id=machine_id)
        return out
    finally:
        POOL.put(conn)


def _fallback_machine_totals_by_date(target_date: date):
    """Fallback totals from machine_counters for the selected date."""
    date_str = target_date.strftime("%Y-%m-%d")
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT machine_id, MAX(total_impressions) FROM machine_counters WHERE date(timestamp)=? GROUP BY machine_id ORDER BY machine_id ASC",
            (date_str,),
        )
        rows = c.fetchall()
        if not rows:
            return []

        totals = []
        for mid, total in rows:
            totals.append((int(mid), int(total or 0)))
        return totals
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
        if not rows:
            return _fallback_machine_totals_by_date(target_date)
        return [(int(r[0]), int(r[1] or 0)) for r in rows]
    finally:
        POOL.put(conn)


# -------------------------
# UI: AnalyticsTab (Improved)
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
        self._machine_map = {}
        self._last_canvas_width = 0
        
        # View state
        self._show_patterns = tk.BooleanVar(value=False)
        self._show_machines = tk.BooleanVar(value=False)
        self._show_daily = tk.BooleanVar(value=False)
        
        self._build_ui()

        init_analytics_db()
        # initial refresh after 2 seconds
        self.frame.after(2000, self.refresh)
        # auto-refresh every 60 seconds
        self.frame.after(60000, self._schedule_auto_refresh)

    def get_frame(self):
        return self.frame

    def _build_ui(self):
        # Header
        header = ttk.Frame(self.frame)
        header.pack(fill="x", padx=16, pady=(12, 8))

        title = ttk.Label(header, text="📊 Analytics", font=("Arial", 18, "bold"), foreground=self._FG_TEXT)
        title.pack(side="left")

        # Action buttons
        btn_frame = ttk.Frame(header)
        btn_frame.pack(side="right")
        
        ttk.Button(btn_frame, text="📥 Export CSV", command=self._export_csv).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="🔄 Refresh", command=self.refresh).pack(side="left", padx=4)

        # Quick Filters Section
        filters = ttk.Frame(self.frame)
        filters.pack(fill="x", padx=16, pady=(0, 12))

        # Time range presets
        preset_frame = tk.Frame(filters, bg=self._BG_FRAME)
        preset_frame.pack(side="left")
        
        tk.Label(preset_frame, text="Quick View:", bg=self._BG_FRAME, fg=self._FG_TEXT, font=("Arial", 10, "bold")).pack(side="left", padx=(0,8))
        
        self.range_var = tk.IntVar(value=7)
        presets = [
            ("Today", 1),
            ("This Week", 7),
            ("This Month", 30),
            ("3 Months", 90)
        ]
        
        for label, days in presets:
            btn = tk.Button(preset_frame, text=label, 
                          command=lambda d=days: self._set_preset(d),
                          bg=self._BG_CARD, fg=self._FG_TEXT,
                          relief="raised", bd=1, padx=12, pady=6,
                          font=("Arial", 9))
            btn.pack(side="left", padx=2)

        # Machine selector
        machine_frame = tk.Frame(filters, bg=self._BG_FRAME)
        machine_frame.pack(side="right")
        
        tk.Label(machine_frame, text="Machine:", bg=self._BG_FRAME, fg=self._FG_TEXT, font=("Arial", 10)).pack(side="left", padx=(0,4))
        self.machine_var = tk.StringVar(value="All Machines")
        self.machine_combo = ttk.Combobox(machine_frame, textvariable=self.machine_var, state="readonly", width=30)
        self.machine_combo.pack(side="left")
        self.machine_combo.bind("<<ComboboxSelected>>", lambda e: self.refresh())

        # Custom date range (collapsible)
        self.custom_range_frame = tk.Frame(self.frame, bg=self._BG_FRAME)
        self.custom_range_frame.pack(fill="x", padx=16, pady=(0, 8))
        
        custom_toggle = tk.Button(self.custom_range_frame, text="▶ Custom Date Range", 
                                 command=self._toggle_custom_range,
                                 bg=self._BG_CARD, fg=self._FG_TEXT,
                                 relief="flat", anchor="w", font=("Arial", 9))
        custom_toggle.pack(fill="x", pady=2)
        
        self.custom_inputs_frame = tk.Frame(self.custom_range_frame, bg=self._BG_FRAME)
        # Don't pack initially - will be shown on toggle
        
        tk.Label(self.custom_inputs_frame, text="From:", bg=self._BG_FRAME, fg=self._FG_TEXT).pack(side="left", padx=(20,4))
        self.date_from_var = tk.StringVar(value="")
        self.date_from_entry = ttk.Entry(self.custom_inputs_frame, textvariable=self.date_from_var, width=12)
        self.date_from_entry.pack(side="left", padx=4)
        
        tk.Label(self.custom_inputs_frame, text="To:", bg=self._BG_FRAME, fg=self._FG_TEXT).pack(side="left", padx=(12,4))
        self.date_to_var = tk.StringVar(value="")
        self.date_to_entry = ttk.Entry(self.custom_inputs_frame, textvariable=self.date_to_var, width=12)
        self.date_to_entry.pack(side="left", padx=4)
        
        ttk.Button(self.custom_inputs_frame, text="Apply", command=self._apply_custom_range).pack(side="left", padx=8)
        
        self._custom_range_visible = False

        # Scrollable content area
        canvas = tk.Canvas(self.frame, bg=self._BG_FRAME, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True, padx=(8,0), pady=(0,8))
        scrollbar = ttk.Scrollbar(self.frame, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y", pady=(0,8))
        canvas.configure(yscrollcommand=scrollbar.set)

        self.inner = ttk.Frame(canvas)
        self.inner_id = canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        
        def _on_canvas_resize(e):
            if e.width != self._last_canvas_width:
                self._last_canvas_width = e.width
                canvas.itemconfig(self.inner_id, width=e.width)
        canvas.bind("<Configure>", _on_canvas_resize)
        
        # Mouse wheel scrolling
        canvas.bind("<Enter>", lambda _: self._bind_mousewheel(canvas))
        canvas.bind("<Leave>", lambda _: self._unbind_mousewheel(canvas))
        
        self._main_canvas = canvas

        # Content sections
        # 1. Summary cards
        self.summary_card = tk.Frame(self.inner, bg=self._BG_FRAME)
        self.summary_card.pack(fill="x", padx=12, pady=(0,12))

        # 2. Insights section
        self.insights_card = tk.Frame(self.inner, bg=self._BG_CARD, relief="solid", bd=1)
        self.insights_card.pack(fill="x", padx=12, pady=(0,12))

        # 3. Main chart (always visible)
        self.main_chart_card = tk.Frame(self.inner, bg=self._BG_CARD, relief="solid", bd=1)
        self.main_chart_card.pack(fill="x", padx=12, pady=(0,12))

        # 4. Patterns section (collapsible)
        self.patterns_section = tk.Frame(self.inner, bg=self._BG_FRAME)
        self.patterns_section.pack(fill="x", padx=12, pady=(0,12))
        
        patterns_toggle = tk.Button(self.patterns_section, text="▶ Usage Patterns", 
                                   command=self._toggle_patterns,
                                   bg=self._BG_CARD, fg=self._FG_TEXT,
                                   relief="raised", anchor="w", font=("Arial", 11, "bold"), bd=1)
        patterns_toggle.pack(fill="x", pady=2)
        
        self.patterns_content = tk.Frame(self.patterns_section, bg=self._BG_FRAME)
        # Don't pack initially

        # 5. Machine breakdown (collapsible)
        self.machines_section = tk.Frame(self.inner, bg=self._BG_FRAME)
        self.machines_section.pack(fill="x", padx=12, pady=(0,12))
        
        machines_toggle = tk.Button(self.machines_section, text="▶ Machine Breakdown", 
                                   command=self._toggle_machines,
                                   bg=self._BG_CARD, fg=self._FG_TEXT,
                                   relief="raised", anchor="w", font=("Arial", 11, "bold"), bd=1)
        machines_toggle.pack(fill="x", pady=2)
        
        self.machines_content = tk.Frame(self.machines_section, bg=self._BG_FRAME)
        # Don't pack initially

        # 6. Daily detail (collapsible)
        self.daily_section = tk.Frame(self.inner, bg=self._BG_FRAME)
        self.daily_section.pack(fill="x", padx=12, pady=(0,12))
        
        daily_toggle = tk.Button(self.daily_section, text="▶ Daily Breakdown", 
                               command=self._toggle_daily,
                               bg=self._BG_CARD, fg=self._FG_TEXT,
                               relief="raised", anchor="w", font=("Arial", 11, "bold"), bd=1)
        daily_toggle.pack(fill="x", pady=2)
        
        self.daily_content = tk.Frame(self.daily_section, bg=self._BG_FRAME)
        # Don't pack initially

        # populate machine selector
        self._build_machine_list()

    def _bind_mousewheel(self, canvas):
        canvas.bind_all("<MouseWheel>", lambda e: self._on_mousewheel(canvas, e))
        canvas.bind_all("<Button-4>", lambda e: self._on_mousewheel(canvas, e))
        canvas.bind_all("<Button-5>", lambda e: self._on_mousewheel(canvas, e))

    def _unbind_mousewheel(self, canvas):
        canvas.unbind_all("<MouseWheel>")
        canvas.unbind_all("<Button-4>")
        canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, canvas, event):
        if event.num == 5 or event.delta < 0:
            canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            canvas.yview_scroll(-3, "units")

    def _toggle_custom_range(self):
        self._custom_range_visible = not self._custom_range_visible
        if self._custom_range_visible:
            self.custom_inputs_frame.pack(fill="x", pady=(4,8))
            # Update button text
            for widget in self.custom_range_frame.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▼ Custom Date Range")
                    break
        else:
            self.custom_inputs_frame.pack_forget()
            for widget in self.custom_range_frame.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▶ Custom Date Range")
                    break

    def _toggle_patterns(self):
        self._show_patterns.set(not self._show_patterns.get())
        if self._show_patterns.get():
            self.patterns_content.pack(fill="x", pady=(8,0))
            for widget in self.patterns_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▼ Usage Patterns")
                    break
            self._refresh_patterns()
        else:
            self.patterns_content.pack_forget()
            for widget in self.patterns_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▶ Usage Patterns")
                    break

    def _toggle_machines(self):
        self._show_machines.set(not self._show_machines.get())
        if self._show_machines.get():
            self.machines_content.pack(fill="x", pady=(8,0))
            for widget in self.machines_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▼ Machine Breakdown")
                    break
            self._refresh_machines()
        else:
            self.machines_content.pack_forget()
            for widget in self.machines_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▶ Machine Breakdown")
                    break

    def _toggle_daily(self):
        self._show_daily.set(not self._show_daily.get())
        if self._show_daily.get():
            self.daily_content.pack(fill="x", pady=(8,0))
            for widget in self.daily_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▼ Daily Breakdown")
                    break
            self._refresh_daily()
        else:
            self.daily_content.pack_forget()
            for widget in self.daily_section.winfo_children():
                if isinstance(widget, tk.Button):
                    widget.config(text="▶ Daily Breakdown")
                    break

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

    def _set_preset(self, days):
        self.range_var.set(days)
        self.date_from_var.set("")
        self.date_to_var.set("")
        self.refresh()

    def _apply_custom_range(self):
        df = self.date_from_var.get().strip()
        dt = self.date_to_var.get().strip()
        if not df or not dt:
            messagebox.showerror("Date Error", "Please enter both From and To dates in YYYY-MM-DD format.")
            return
        try:
            d_from = datetime.strptime(df, "%Y-%m-%d").date()
            d_to = datetime.strptime(dt, "%Y-%m-%d").date()
        except Exception:
            messagebox.showerror("Date Error", "Enter dates in YYYY-MM-DD format.")
            return
        if d_to < d_from:
            messagebox.showerror("Date Error", "To date must be same or after From date.")
            return
        
        days = (d_to - d_from).days + 1
        self.range_var.set(days)
        self.refresh()

    def _schedule_auto_refresh(self):
        if self.frame.winfo_exists():
            self.refresh()
            self.frame.after(60000, self._schedule_auto_refresh)

    def refresh(self):
        """Main refresh - always shows summary + main chart, refresh sections if visible"""
        days = int(self.range_var.get())
        mid = self._machine_map.get(self.machine_var.get(), None)

        # Calculate date window
        end_dt = round_to_30(datetime.now())
        start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
        
        # Override with custom dates if specified
        if self.date_from_var.get() and self.date_to_var.get():
            try:
                start_dt = datetime.combine(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").date(), datetime.min.time())
                end_dt = datetime.combine(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").date(), datetime.max.time())
                days = (end_dt.date() - start_dt.date()).days + 1
            except:
                pass

        # Fetch data
        try:
            aggregate_weekday = True if days <= 31 else False
            matrix, slots, day_labels, raw_rows = fetch_intervals_matrix(days=days, machine_id=mid, window_start=start_dt, window_end=end_dt, aggregate_weekday=aggregate_weekday)
        except Exception:
            logger.exception("Failed to fetch intervals matrix")
            matrix = np.zeros((7,48), dtype=int)
            slots = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0,30)]
            day_labels = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"]
            raw_rows = []

        # Calculate statistics
        combined_by_slot = matrix.sum(axis=0)
        total_clicks = int(combined_by_slot.sum())
        avg_per_day = total_clicks / max(1, days)
        
        # Busiest stats
        hour_totals = np.array([combined_by_slot[i*2] + combined_by_slot[i*2+1] for i in range(24)])
        busiest_hour_idx = int(np.argmax(hour_totals)) if hour_totals.size else 0
        busiest_hour_val = int(hour_totals[busiest_hour_idx]) if hour_totals.size else 0
        
        busiest_day_idx = int(np.argmax(matrix.sum(axis=1)))
        busiest_day_name = day_labels[busiest_day_idx]
        busiest_day_val = int(matrix.sum(axis=1)[busiest_day_idx])
        
        # Business hours utilization
        bh_start = 7 * 2
        bh_end = 19 * 2
        slots_in_bh = np.sum(matrix[:, bh_start:bh_end] > 0)
        total_slots_bh = matrix.shape[0] * (bh_end - bh_start)
        util_pct = (slots_in_bh / total_slots_bh) * 100 if total_slots_bh > 0 else 0.0

        # Compare with previous period
        try:
            prev_start = start_dt - timedelta(days=days)
            prev_end = start_dt - timedelta(seconds=1)
            prev_matrix, _, _, _ = fetch_intervals_matrix(days=days, machine_id=mid, window_start=prev_start, window_end=prev_end, aggregate_weekday=aggregate_weekday)
            prev_total = int(prev_matrix.sum())
            if prev_total > 0:
                change_pct = ((total_clicks - prev_total) / prev_total) * 100
            else:
                change_pct = 0
        except:
            change_pct = 0

        stats = {
            'total': total_clicks,
            'avg_day': avg_per_day,
            'busiest_hour': busiest_hour_idx,
            'busiest_hour_val': busiest_hour_val,
            'busiest_day': busiest_day_name,
            'busiest_day_val': busiest_day_val,
            'util_pct': util_pct,
            'change_pct': change_pct,
            'days': days,
            'start_date': start_dt.date(),
            'end_date': end_dt.date()
        }

        # Clear and redraw always-visible sections
        for w in self.summary_card.winfo_children(): w.destroy()
        for w in self.insights_card.winfo_children(): w.destroy()
        for w in self.main_chart_card.winfo_children(): w.destroy()

        self._draw_summary_cards(self.summary_card, stats)
        self._draw_insights(self.insights_card, stats, matrix, day_labels)
        self._draw_main_chart(self.main_chart_card, slots, combined_by_slot, stats)

        # Refresh visible sections
        if self._show_patterns.get():
            self._refresh_patterns()
        if self._show_machines.get():
            self._refresh_machines()
        if self._show_daily.get():
            self._refresh_daily()

    def _draw_summary_cards(self, parent, stats):
        """Draw metric cards with key statistics"""
        cards_container = tk.Frame(parent, bg=self._BG_FRAME)
        cards_container.pack(fill="x", pady=8)

        # Card 1: Total prints
        change_symbol = "↑" if stats['change_pct'] > 0 else "↓" if stats['change_pct'] < 0 else "→"
        change_color = "#10b981" if stats['change_pct'] > 0 else "#ef4444" if stats['change_pct'] < 0 else "#6b7280"
        self._create_metric_card(cards_container, "Total Prints", 
                                f"{stats['total']:,}", 
                                f"{change_symbol} {abs(stats['change_pct']):.1f}% vs previous period",
                                change_color)

        # Card 2: Daily average
        self._create_metric_card(cards_container, "Daily Average", 
                                f"{stats['avg_day']:.0f}", 
                                "prints per day")

        # Card 3: Peak hour
        hour_label = f"{stats['busiest_hour']:02d}:00"
        if stats['busiest_hour'] < 12:
            hour_label += " AM"
        elif stats['busiest_hour'] == 12:
            hour_label += " PM"
        else:
            hour_label = f"{stats['busiest_hour']-12:02d}:00 PM"
        
        self._create_metric_card(cards_container, "Peak Hour", 
                                hour_label, 
                                f"{stats['busiest_hour_val']:,} prints")

        # Card 4: Business hours usage
        self._create_metric_card(cards_container, "Business Hours Activity", 
                                f"{stats['util_pct']:.0f}%", 
                                "7 AM - 7 PM utilization")

    def _create_metric_card(self, parent, title, value, subtitle, subtitle_color=None):
        """Create a single metric card"""
        card = tk.Frame(parent, bg=self._BG_CARD, relief="solid", bd=1)
        card.pack(side="left", padx=6, pady=4, fill="both", expand=True)

        tk.Label(card, text=title, bg=self._BG_CARD, 
                fg="#9ca3af", font=("Arial", 9)).pack(pady=(12,4))
        
        tk.Label(card, text=value, bg=self._BG_CARD, 
                fg=self._FG_TEXT, font=("Arial", 22, "bold")).pack(pady=2)
        
        sub_color = subtitle_color or "#6b7280"
        tk.Label(card, text=subtitle, bg=self._BG_CARD, 
                fg=sub_color, font=("Arial", 8)).pack(pady=(4,12))

    def _draw_insights(self, parent, stats, matrix, day_labels):
        """Draw insights section with automatic pattern detection"""
        tk.Label(parent, text="💡 Insights", bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(8,4))

        insights = []

        # Busiest day insight
        if stats['busiest_day_val'] > 0:
            avg_day_val = matrix.sum(axis=1).mean()
            if stats['busiest_day_val'] > avg_day_val * 1.3:
                pct_more = ((stats['busiest_day_val'] / avg_day_val) - 1) * 100
                insights.append(f"• {stats['busiest_day']}s are {pct_more:.0f}% busier than average")

        # Utilization insight
        if stats['util_pct'] < 30:
            insights.append(f"• Low usage detected ({stats['util_pct']:.0f}% utilization) - consider machine consolidation")
        elif stats['util_pct'] > 80:
            insights.append(f"• High utilization ({stats['util_pct']:.0f}%) - may need additional capacity")

        # Trend insight
        if abs(stats['change_pct']) > 20:
            direction = "increased" if stats['change_pct'] > 0 else "decreased"
            insights.append(f"• Print volume has {direction} significantly ({abs(stats['change_pct']):.0f}%) compared to previous period")

        # Peak time insight
        if 9 <= stats['busiest_hour'] <= 11:
            insights.append(f"• Peak usage in mid-morning ({stats['busiest_hour']:02d}:00) - typical pattern")
        elif 14 <= stats['busiest_hour'] <= 16:
            insights.append(f"• Peak usage in mid-afternoon ({stats['busiest_hour']:02d}:00)")
        elif stats['busiest_hour'] < 7 or stats['busiest_hour'] > 19:
            insights.append(f"• Unusual peak time detected ({stats['busiest_hour']:02d}:00) - outside business hours")

        if not insights:
            insights.append("• No significant patterns detected in current period")

        for insight in insights:
            tk.Label(parent, text=insight, bg=self._BG_CARD, fg=self._FG_TEXT, 
                    font=("Arial", 9), anchor="w", justify="left").pack(anchor="w", padx=24, pady=2)

        tk.Label(parent, text="", bg=self._BG_CARD).pack(pady=4)  # Spacer

    def _draw_main_chart(self, parent, labels, values, stats):
        """Draw main activity chart"""
        period_text = f"{stats['start_date']} to {stats['end_date']}" if stats['days'] > 1 else str(stats['start_date'])
        
        tk.Label(parent, text=f"Print Activity — {period_text}", bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(8,6))

        fig = Figure(figsize=(11,3.2), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(values))
        bars = ax.bar(x, values, align="center", linewidth=0, color=self._ACCENT)

        # Show ticks every 4 slots (2 hours)
        step = 4
        ax.set_xticks(np.arange(0, len(labels), step))
        ax.set_xticklabels([labels[i] for i in range(0, len(labels), step)], rotation=45, fontsize=9)
        ax.set_ylabel("Prints", fontsize=10)
        ax.grid(axis="y", linestyle=":", linewidth=0.5, alpha=0.3)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0,8))
        canvas.draw()

        # Tooltip
        annot = ax.annotate("", xy=(0,0), xytext=(15,15), textcoords="offset points",
                           bbox=dict(boxstyle="round", fc="white", alpha=0.95),
                           arrowprops=dict(arrowstyle="->"))
        annot.set_visible(False)

        def update_annot(bar, val, idx):
            x = bar.get_x() + bar.get_width()/2
            y = bar.get_height()
            annot.xy = (x, y)
            annot.set_text(f"{labels[idx]}\n{int(val):,} prints")

        def hover(event):
            vis = annot.get_visible()
            if event.inaxes == ax:
                for i, bar in enumerate(bars):
                    cont, _ = bar.contains(event)
                    if cont:
                        update_annot(bar, values[i], i)
                        annot.set_visible(True)
                        canvas.draw_idle()
                        return
            if vis:
                annot.set_visible(False)
                canvas.draw_idle()

        canvas.mpl_connect("motion_notify_event", hover)

    def _refresh_patterns(self):
        """Refresh patterns section content"""
        for w in self.patterns_content.winfo_children(): w.destroy()

        days = int(self.range_var.get())
        mid = self._machine_map.get(self.machine_var.get(), None)

        end_dt = round_to_30(datetime.now())
        start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
        
        if self.date_from_var.get() and self.date_to_var.get():
            try:
                start_dt = datetime.combine(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").date(), datetime.min.time())
                end_dt = datetime.combine(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").date(), datetime.max.time())
            except:
                pass

        try:
            matrix, slots, day_labels, _ = fetch_intervals_matrix(days=days, machine_id=mid, window_start=start_dt, window_end=end_dt)
        except Exception:
            return

        # Day of week chart
        dow_card = tk.Frame(self.patterns_content, bg=self._BG_CARD, relief="solid", bd=1)
        dow_card.pack(fill="x", pady=(0,12))
        self._draw_dow(dow_card, day_labels, matrix.sum(axis=1))

        # Heatmap
        heatmap_card = tk.Frame(self.patterns_content, bg=self._BG_CARD, relief="solid", bd=1)
        heatmap_card.pack(fill="x", pady=(0,12))
        self._draw_heatmap(heatmap_card, matrix, slots, day_labels)

    def _refresh_machines(self):
        """Refresh machine breakdown section"""
        for w in self.machines_content.winfo_children(): w.destroy()

        days = int(self.range_var.get())
        end_dt = round_to_30(datetime.now())
        start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
        
        if self.date_from_var.get() and self.date_to_var.get():
            try:
                start_dt = datetime.combine(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").date(), datetime.min.time())
                end_dt = datetime.combine(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").date(), datetime.max.time())
            except:
                pass

        try:
            machines = fetch_machines(active_only=False)
        except Exception:
            machines = []

        if machines:
            for m in machines:
                self._draw_machine_row(self.machines_content, m, days, start_dt, end_dt)
        else:
            tk.Label(self.machines_content, text="No machines configured.", 
                    fg=self._FG_TEXT, bg=self._BG_FRAME, font=("Arial", 10)).pack(padx=10, pady=20)

    def _refresh_daily(self):
        """Refresh daily breakdown section"""
        for w in self.daily_content.winfo_children(): w.destroy()

        days = int(self.range_var.get())
        mid = self._machine_map.get(self.machine_var.get(), None)
        
        end_dt = round_to_30(datetime.now())
        start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
        
        if self.date_from_var.get() and self.date_to_var.get():
            try:
                start_dt = datetime.combine(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").date(), datetime.min.time())
                end_dt = datetime.combine(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").date(), datetime.max.time())
            except:
                pass

        start_date = start_dt.date()
        end_date = end_dt.date()
        delta_days = (end_date - start_date).days

        if delta_days > 31:
            tk.Label(self.daily_content, text="⚠️ Daily breakdown limited to 31 days. Please use a shorter date range.", 
                    fg="#f59e0b", bg=self._BG_FRAME, font=("Arial", 10)).pack(padx=10, pady=20)
            return

        for i in range(min(delta_days + 1, 31)):
            d = start_date + timedelta(days=i)
            self._draw_single_day_card(self.daily_content, d, mid)

    def _draw_dow(self, parent, day_labels, day_totals):
        """Draw day of week chart"""
        tk.Label(parent, text="Prints by Day of Week", bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(8,6))

        fig = Figure(figsize=(9,2.4), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(day_labels))
        bars = ax.bar(x, day_totals, color=self._ACCENT)
        ax.set_xticks(x)
        ax.set_xticklabels(day_labels, fontsize=10)
        ax.set_ylabel("Prints", fontsize=10)
        ax.grid(axis="y", linestyle=":", linewidth=0.5, alpha=0.3)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0,8))
        canvas.draw()

        # Tooltip
        annot = ax.annotate("", xy=(0,0), xytext=(15,15), textcoords="offset points",
                           bbox=dict(boxstyle="round", fc="white", alpha=0.95))
        annot.set_visible(False)

        def update_annot(i):
            x = bars[i].get_x() + bars[i].get_width()/2
            y = bars[i].get_height()
            annot.xy = (x, y)
            annot.set_text(f"{day_labels[i]}\n{int(day_totals[i]):,} prints")

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
        """Draw heatmap"""
        tk.Label(parent, text="Activity Heatmap — Day vs Time", bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 11, "bold")).pack(anchor="w", padx=12, pady=(8,6))

        ticks = [i*2 for i in range(24)]
        ticklabels = [slot_labels[i] for i in ticks]

        fig = Figure(figsize=(12,3.8), dpi=100)
        ax = fig.add_subplot(111)
        im = ax.imshow(matrix, aspect="auto", interpolation="nearest", cmap="YlGnBu")
        ax.set_yticks(np.arange(len(day_labels)))
        ax.set_yticklabels(day_labels, fontsize=10)
        ax.set_xticks(ticks)
        ax.set_xticklabels(ticklabels, rotation=45, fontsize=9)
        ax.set_xlabel("Time of Day", fontsize=10)
        fig.colorbar(im, ax=ax, orientation="vertical", fraction=0.05)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=parent)
        canvas.get_tk_widget().pack(fill="both", expand=True, padx=8, pady=(0,8))
        canvas.draw()

    def _draw_machine_row(self, parent, machine, days, page_start, page_end):
        """Draw compact machine card"""
        name = machine.get("name") or f"Machine {machine['id']}"
        mid = machine["id"]

        try:
            matrix, slots, day_labels, rows = fetch_intervals_matrix(days=days, machine_id=mid, window_start=page_start, window_end=page_end)
        except:
            return

        combined_by_slot = matrix.sum(axis=0)
        total_clicks = int(combined_by_slot.sum())
        page_days = (page_end.date() - page_start.date()).days + 1
        avg_per_day = total_clicks / max(1, page_days)

        # Top 3 busiest slots
        top_idx = np.argsort(combined_by_slot)[-3:][::-1] if combined_by_slot.size else []
        top_str = ", ".join([f"{slots[i]} ({int(combined_by_slot[i])})" for i in top_idx]) if top_idx.size else "—"

        outer = tk.Frame(parent, bg=self._BG_CARD, relief="solid", bd=1)
        outer.pack(fill="x", pady=6)

        left = tk.Frame(outer, bg=self._BG_CARD)
        left.pack(side="left", fill="y", padx=12, pady=10)

        tk.Label(left, text=name, bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 11, "bold")).pack(anchor="w")
        tk.Label(left, text=f"Total: {total_clicks:,} prints", bg=self._BG_CARD, 
                fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", pady=(4,0))
        tk.Label(left, text=f"Daily average: {avg_per_day:.1f}", bg=self._BG_CARD, 
                fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", pady=(2,0))
        tk.Label(left, text=f"Peak times: {top_str}", bg=self._BG_CARD, 
                fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", pady=(2,0))

        right = tk.Frame(outer, bg=self._BG_CARD)
        right.pack(side="left", fill="both", expand=True, pady=8, padx=(0,8))

        # Mini chart
        fig = Figure(figsize=(6.5,1.4), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(combined_by_slot))
        ax.bar(x, combined_by_slot, linewidth=0, color=self._ACCENT)
        ax.set_xticks(np.arange(0, len(slots), 8))
        ax.set_xticklabels([slots[i] for i in range(0, len(slots), 8)], rotation=45, fontsize=7)
        ax.set_yticks([])
        ax.spines['top'].set_visible(False)
        ax.spines['right'].set_visible(False)
        ax.spines['left'].set_visible(False)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=right)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    def _draw_single_day_card(self, parent, day_date, mid):
        """Draw single day detail card"""
        outer = tk.Frame(parent, bg=self._BG_CARD, relief="solid", bd=1)
        outer.pack(fill="x", pady=6)

        header = tk.Frame(outer, bg=self._BG_CARD)
        header.pack(fill="x", padx=12, pady=(8,4))
        
        tk.Label(header, text=day_date.strftime("%A, %B %d, %Y"), bg=self._BG_CARD, 
                fg=self._FG_TEXT, font=("Arial", 11, "bold")).pack(side="left")

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

        details = f"Total: {total:,} prints  •  Peak hour: {busiest_hour_idx:02d}:00 ({busiest_hour_val:,} prints)"
        tk.Label(outer, text=details, bg=self._BG_CARD, fg="#9ca3af", 
                font=("Arial", 9)).pack(anchor="w", padx=12, pady=(0,6))

        body = tk.Frame(outer, bg=self._BG_CARD)
        body.pack(fill="both", expand=True, padx=12, pady=(0,8))

        left = tk.Frame(body, bg=self._BG_CARD)
        left.pack(side="left", fill="both", expand=True)

        right = tk.Frame(body, bg=self._BG_CARD)
        right.pack(side="left", fill="y", padx=(12,0))

        fig = Figure(figsize=(8.5,2.2), dpi=100)
        ax = fig.add_subplot(111)
        x = np.arange(len(slot_counts))
        ax.bar(x, slot_counts, linewidth=0, color=self._ACCENT)
        step = 4
        ax.set_xticks(np.arange(0, len(slots), step))
        ax.set_xticklabels([slots[i] for i in range(0, len(slots), step)], rotation=45, fontsize=8)
        ax.set_ylabel("Prints", fontsize=9)
        ax.grid(axis="y", linestyle=":", linewidth=0.5, alpha=0.3)
        fig.tight_layout()
        self._mat_style(fig, ax)

        canvas = FigureCanvasTkAgg(fig, master=left)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

        tk.Label(right, text="Machine Totals", bg=self._BG_CARD, fg=self._FG_TEXT, 
                font=("Arial", 10, "bold")).pack(anchor="w")
        
        machine_totals = fetch_machine_totals_by_date(day_date)
        if not machine_totals:
            tk.Label(right, text="No prints", bg=self._BG_CARD, 
                    fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", pady=(6,0))
        else:
            for m_id, tot in machine_totals:
                name = f"Machine {m_id}"
                try:
                    machines = fetch_machines(active_only=False)
                    for mm in machines:
                        if mm["id"] == m_id:
                            name = mm.get("name") or name
                            break
                except:
                    pass
                tk.Label(right, text=f"{name}: {tot:,}", bg=self._BG_CARD, 
                        fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", pady=(3,0))

    def _mat_style(self, fig, ax):
        """Apply dark theme styling to matplotlib charts"""
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
                spine.set_color("#374151")
        except:
            pass

    def _export_csv(self):
        """Export analytics data to CSV"""
        days = int(self.range_var.get())
        mid_label = self.machine_var.get()
        mid = self._machine_map.get(mid_label)

        end_dt = round_to_30(datetime.now())
        start_dt = end_dt - timedelta(days=days) + timedelta(seconds=1)
        
        if self.date_from_var.get() and self.date_to_var.get():
            try:
                start_dt = datetime.combine(datetime.strptime(self.date_from_var.get(), "%Y-%m-%d").date(), datetime.min.time())
                end_dt = datetime.combine(datetime.strptime(self.date_to_var.get(), "%Y-%m-%d").date(), datetime.max.time())
            except:
                pass

        rows = fetch_intervals(machine_id=mid, days=days)
        rows = [(dt, clicks) for dt, clicks in rows if dt >= start_dt and dt <= end_dt]
        
        if not rows:
            messagebox.showinfo("Export", "No analytics data for selected period.")
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = (mid_label or "all_machines").replace(" ", "_").replace("/", "_")
        path = os.path.join(os.path.abspath(os.getcwd()), f"analytics_{safe_name}_{timestamp}.csv")
        
        try:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["timestamp", "prints"])
                for dt, clicks in rows:
                    w.writerow([dt.strftime("%Y-%m-%d %H:%M:%S"), clicks])
            messagebox.showinfo("Export", f"Successfully exported to:\n{path}")
        except Exception as e:
            messagebox.showerror("Export Error", str(e))


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