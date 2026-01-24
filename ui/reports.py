# ui/reports.py
# ============================================================
# Advanced Reports – FULL FEATURE + OPTIMIZED
# - Cached history (fast navigation)
# - Line / Bar / Table tabs restored
# - Tooltip restored
# - Mouse wheel scrolling restored
# ============================================================

import os
import csv
import threading
from collections import defaultdict
from datetime import datetime, date, timedelta

import tkinter as tk
from tkinter import ttk, messagebox

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.dates as mdates

from core.models import (
    fetch_machines,
    fetch_daily_rows,
    fetch_latest_counter,
)
from core.ping import ping_ip
from ui.theme import apply_theme, CURRENT_THEME

try:
    from tkcalendar import DateEntry
    TKCAL_AVAILABLE = True
except Exception:
    TKCAL_AVAILABLE = False


MAX_LOOKBACK_DAYS = 365


class ReportsTab:
    def __init__(self, parent, root=None, style=None, rebuild_callback=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()
        self.rebuild_callback = rebuild_callback

        # Theme
        self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        self._BG_FRAME = self.colors.get("BG_FRAME", "#020617")
        self._BG_CARD = self.colors.get("BG_CARD", "#0f172a")
        self._FG_TEXT = self.colors.get("FG_TEXT", "#e5e7eb")
        self._ACCENT = self.colors.get("ACCENT_BLUE", "#3b82f6")
        self._ONLINE = self.colors.get("ONLINE_GREEN", "#16a34a")
        self._OFFLINE = self.colors.get("OFFLINE_RED", "#dc2626")

        # Time state
        self.days_var = tk.IntVar(value=7)
        self.window_offset = 0
        self.custom_range = None

        # Data
        self.machines = []
        self.rows_by_mid = {}
        self.combined_rows = []
        self.latest_counters = {}
        self.online_status = {}
        self.today_clicks = {}

        # Caches
        self._history_cache = {}   # mid -> [(date, clicks)]
        self._meta_cache = {}      # mid -> (latest_counter:int, online:bool)

        self._loading = False

        self.frame = ttk.Frame(parent)
        self._build_static_ui()
        self.frame.after(200, self.refresh)

    def get_frame(self):
        return self.frame

    # =========================================================
    # UI
    # =========================================================
    def _build_static_ui(self):
        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=(6, 4))

        ttk.Label(top, text="Reports", font=("Arial", 16, "bold"),
                  foreground=self._FG_TEXT).pack(side="left")

        ttk.Button(top, text="←", width=3, command=self._go_back).pack(side="left", padx=(10, 2))
        ttk.Button(top, text="→", width=3, command=self._go_forward).pack(side="left")

        rf = ttk.Frame(top)
        rf.pack(side="left", padx=(16, 0))
        ttk.Label(rf, text="Range:", foreground=self._FG_TEXT).pack(side="left")

        for lbl, val in (("7 days", 7), ("30 days", 30), ("90 days", 90)):
            ttk.Button(rf, text=lbl, command=lambda v=val: self._set_range(v)).pack(side="left", padx=2)

        if TKCAL_AVAILABLE:
            dp = ttk.Frame(top)
            dp.pack(side="left", padx=12)

            ttk.Label(dp, text="From", foreground=self._FG_TEXT).pack(side="left")
            self.dp_start = DateEntry(dp, width=10)
            self.dp_start.pack(side="left", padx=2)

            ttk.Label(dp, text="To", foreground=self._FG_TEXT).pack(side="left")
            self.dp_end = DateEntry(dp, width=10)
            self.dp_end.pack(side="left", padx=2)

            ttk.Button(dp, text="Apply", command=self._apply_custom_range).pack(side="left", padx=4)

        ttk.Button(top, text="Export All", command=self._on_export_all).pack(side="right")

        self.subtitle_lbl = ttk.Label(self.frame, text="", foreground=self._FG_TEXT)
        self.subtitle_lbl.pack(anchor="w", padx=12)

        # Scroll area
        self.canvas = tk.Canvas(self.frame, bg=self._BG_FRAME, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        # Mouse wheel scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

        self.status_lbl = ttk.Label(self.frame, text="Loading…", foreground=self._FG_TEXT)
        self.status_lbl.pack(anchor="w", padx=10, pady=4)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # =========================================================
    # Navigation
    # =========================================================
    def _set_range(self, days):
        self.days_var.set(days)
        self.window_offset = 0
        self.custom_range = None
        self.refresh()

    def _go_back(self):
        self.window_offset += self.days_var.get()
        self.custom_range = None
        self.refresh()

    def _go_forward(self):
        self.window_offset = max(0, self.window_offset - self.days_var.get())
        self.custom_range = None
        self.refresh()

    def _apply_custom_range(self):
        start = self.dp_start.get_date()
        end = self.dp_end.get_date()
        if start > end:
            messagebox.showerror("Invalid range", "Start date must be before end date")
            return
        self.custom_range = (start, end)
        self.refresh()

    # =========================================================
    # Data loading (cached)
    # =========================================================
    def refresh(self):
        if self._loading:
            return
        self._loading = True
        threading.Thread(target=self._load_data_worker, daemon=True).start()

    def _load_data_worker(self):
        today = date.today()

        if self.custom_range:
            start, end = self.custom_range
        else:
            end = today - timedelta(days=self.window_offset)
            start = end - timedelta(days=self.days_var.get() - 1)

        self.subtitle_lbl.config(
            text=f"Usage – {start.strftime('%d %b %Y')} → {end.strftime('%d %b %Y')}"
        )

        machines = fetch_machines(active_only=True) or []
        self.machines = machines

        rows_by_mid = {}
        combined = defaultdict(int)

        for m in machines:
            mid = m["id"]

            if mid not in self._history_cache:
                raw = fetch_daily_rows(mid, days=MAX_LOOKBACK_DAYS)
                history = []
                for d, c in raw:
                    if isinstance(d, str):
                        d = datetime.strptime(d, "%Y-%m-%d").date()
                    elif isinstance(d, datetime):
                        d = d.date()
                    history.append((d, int(c or 0)))
                self._history_cache[mid] = history

                try:
                    row = fetch_latest_counter(mid)
                    latest = int(row["total_impressions"]) if row else 0
                except Exception:
                    latest = 0

                try:
                    online = ping_ip(m.get("ip", ""))
                except Exception:
                    online = False

                self._meta_cache[mid] = (latest, online)

            history = self._history_cache[mid]
            rows = [(d, c) for d, c in history if start <= d <= end]
            rows = self._normalize_rows(rows, start, end)

            rows_by_mid[mid] = rows
            for d, c in rows:
                combined[d] += c

            latest, online = self._meta_cache[mid]
            self.latest_counters[mid] = latest
            self.online_status[mid] = online
            self.today_clicks[mid] = rows[-1][1] if rows else 0

        self.rows_by_mid = rows_by_mid
        self.combined_rows = self._normalize_rows(list(combined.items()), start, end)

        self.frame.after(1, self._build_sections_ui)
        self._loading = False

    # =========================================================
    # Normalize
    # =========================================================
    def _normalize_rows(self, rows, start, end):
        data = {d: c for d, c in rows}
        out = []
        cur = start
        while cur <= end:
            out.append((cur, data.get(cur, 0)))
            cur += timedelta(days=1)
        return out

    # =========================================================
    # UI build
    # =========================================================
    def _build_sections_ui(self):
        for w in self.inner.winfo_children():
            w.destroy()

        self._create_card("All Machines (Combined)",
                          f"Last {self.days_var.get()} days",
                          self.combined_rows,
                          False, None)

        for m in self.machines:
            mid = m["id"]
            rows = self.rows_by_mid.get(mid, [])
            subtitle = (
                f"Today: {rows[-1][1] if rows else 0}  •  "
                f"Counter: {self.latest_counters.get(mid, 0):,}"
            )
            self._create_card(
                f"{m.get('name','')} — {m.get('ip','')}",
                subtitle,
                rows,
                True,
                m
            )

        self.status_lbl.config(text=f"Loaded {len(self.machines)} machine(s)")

    # =========================================================
    # Card with tabs
    # =========================================================
    def _create_card(self, title, subtitle, rows, is_machine, machine):
        card = tk.Frame(self.inner, bg=self._BG_CARD, bd=1, relief="solid")
        card.pack(fill="x", padx=14, pady=6)

        header = tk.Frame(card, bg=self._BG_CARD)
        header.pack(fill="x", padx=10, pady=6)

        tk.Label(header, text=title, bg=self._BG_CARD,
                 fg=self._FG_TEXT, font=("Arial", 11, "bold")).pack(side="left")

        if is_machine:
            online = self.online_status.get(machine["id"], False)
            tk.Label(header,
                     text="🟢 Online" if online else "🔴 Offline",
                     bg=self._ONLINE if online else self._OFFLINE,
                     fg="white", padx=8).pack(side="right")

        if subtitle:
            tk.Label(card, text=subtitle, bg=self._BG_CARD,
                     fg="#9ca3af", font=("Arial", 9)).pack(anchor="w", padx=10)

        body = tk.Frame(card, bg=self._BG_CARD)
        body.pack(fill="both", expand=True, padx=8, pady=6)

        tabs = tk.Frame(body, bg=self._BG_CARD)
        tabs.pack(anchor="w")

        content = tk.Frame(body, bg=self._BG_CARD)
        content.pack(fill="both", expand=True)

        current = tk.StringVar(value="Line")

        def mk_tab(name):
            b = tk.Button(
                tabs, text=name, bd=0,
                bg=self._ACCENT if current.get() == name else "#1f2933",
                fg="#000000" if current.get() == name else self._FG_TEXT,
                padx=10, pady=2
            )

            def click():
                current.set(name)
                for x in tabs.winfo_children():
                    x.configure(bg="#1f2933", fg=self._FG_TEXT)
                b.configure(bg=self._ACCENT, fg="#000000")

                if name == "Line":
                    self._build_line_chart(content, rows, title)
                elif name == "Bar":
                    self._build_bar_chart(content, rows, title)
                else:
                    self._build_table(content, rows)

            b.configure(command=click)
            b.pack(side="left", padx=2)
            return b

        mk_tab("Line")
        mk_tab("Bar")
        mk_tab("Table")

        self._build_line_chart(content, rows, title)

    # =========================================================
    # Charts + tooltip
    # =========================================================
    def _attach_line_tooltip(self, fig, ax, xs, ys):
        annot = ax.annotate("", xy=(0, 0), xytext=(10, 10),
                            textcoords="offset points",
                            bbox=dict(boxstyle="round", fc="#111827", ec="#60a5fa"),
                            color="white", fontsize=9)
        annot.set_visible(False)

        def hover(event):
            if event.inaxes == ax:
                for x, y in zip(xs, ys):
                    if abs(mdates.date2num(x) - event.xdata) < 0.4:
                        annot.xy = (x, y)
                        annot.set_text(f"{x.strftime('%d %b %Y')}\nClicks: {y}")
                        annot.set_visible(True)
                        fig.canvas.draw_idle()
                        return
            annot.set_visible(False)
            fig.canvas.draw_idle()

        fig.canvas.mpl_connect("motion_notify_event", hover)

    def _build_line_chart(self, parent, rows, title):
        for w in parent.winfo_children():
            w.destroy()

        xs = [datetime.combine(d, datetime.min.time()) for d, _ in rows]
        ys = [c for _, c in rows]

        fig = Figure(figsize=(6.5, 2.8), dpi=100)
        ax = fig.add_subplot(111)
        ax.plot(xs, ys, marker="o", linewidth=1.5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        fig.autofmt_xdate()
        ax.set_title(title)
        ax.set_ylabel("Clicks")

        self._attach_line_tooltip(fig, ax, xs, ys)

        canvas = FigureCanvasTkAgg(fig, parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    def _build_bar_chart(self, parent, rows, title):
        for w in parent.winfo_children():
            w.destroy()

        xs = [datetime.combine(d, datetime.min.time()) for d, _ in rows]
        ys = [c for _, c in rows]

        fig = Figure(figsize=(6.5, 2.8), dpi=100)
        ax = fig.add_subplot(111)
        ax.bar(xs, ys, color="#60a5fa")
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
        fig.autofmt_xdate()
        ax.set_title(title)
        ax.set_ylabel("Clicks")

        canvas = FigureCanvasTkAgg(fig, parent)
        canvas.get_tk_widget().pack(fill="both", expand=True)
        canvas.draw()

    # =========================================================
    # Table
    # =========================================================
    def _build_table(self, parent, rows):
        for w in parent.winfo_children():
            w.destroy()

        for d, c in reversed(rows):
            tk.Label(
                parent,
                text=f"{d.strftime('%d %b %Y')} — {c}",
                bg=self._BG_CARD,
                fg=self._FG_TEXT
            ).pack(anchor="w", padx=6)

    # =========================================================
    # Export
    # =========================================================
    def _on_export_all(self):
        if not self.combined_rows:
            messagebox.showinfo("Export", "No data")
            return

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(os.getcwd(), f"reports_{ts}.csv")

        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Date", "Clicks"])
            for d, c in self.combined_rows:
                w.writerow([d, c])

        messagebox.showinfo("Export", f"Saved to {path}")
