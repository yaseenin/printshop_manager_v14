# ui/dashboard.py
# Print Shop Manager v14 - Dashboard (icons, fast ping + SNMP, instant tile update)
# Includes serial number auto-detection and display.

import os
import threading
import time
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from core.models import fetch_machines, insert_counter, fetch_latest_counter, fetch_daily_rows
from core.ping import ping_ip
from core.snmp import probe_and_read_counter, probe_and_read_serial, probe_and_read_toner_levels, probe_and_read_paper_trays, probe_and_read_printer_errors
# update_machine_serial may not exist in older models.py — we call it defensively
try:
    from core.models import update_machine_serial, fetch_machine_last_online_or_latest_counter, format_last_online
except Exception:
    update_machine_serial = None
    fetch_machine_last_online_or_latest_counter = None
    format_last_online = None

from ui.theme import apply_theme, CURRENT_THEME

# tile sizing
TILE_W = 350
TILE_H = 420
COLS = 4

ICON_SIZE = (92, 120)


def _safe_config(widget, **kwargs):
    """Update a widget only when a value has actually changed.
    Prevents Tkinter from queuing unnecessary redraws and eliminates flicker."""
    try:
        changed = {}
        for key, new_val in kwargs.items():
            try:
                if str(widget.cget(key)) != str(new_val):
                    changed[key] = new_val
            except Exception:
                changed[key] = new_val
        if changed:
            widget.config(**changed)
    except Exception:
        pass


class DashboardTab:
    def __init__(self, parent, root=None, style=None):
        self.parent = parent
        self.root = root
        self.style = style
        self.colors = apply_theme(root, style, CURRENT_THEME) if (root and style) else None

        self.frame = ttk.Frame(parent)
        self.topbar = ttk.Frame(self.frame)
        self.topbar.pack(fill="x", pady=6, padx=8)

        ttk.Label(self.topbar, text="Print Shop Manager v14",
                  font=("Arial", 18, "bold")).pack(side="left", padx=8)

        self.btn_refresh = ttk.Button(self.topbar, text="🔄 Refresh", command=self.refresh)
        self.btn_refresh.pack(side="right", padx=8)

        # Controls
        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill="x", pady=(0, 6), padx=8)
        ttk.Label(ctrl, text="Show:").pack(side="left", padx=(8, 2))
        self.show_var = tk.StringVar(value="All")
        self.show_combo = ttk.Combobox(ctrl, textvariable=self.show_var,
                                       values=["All", "Online", "Offline"], state="readonly", width=10)
        self.show_combo.pack(side="left")
        ttk.Label(ctrl, text="Sort:").pack(side="left", padx=(10, 2))
        self.sort_var = tk.StringVar(value="Online first")
        self.sort_combo = ttk.Combobox(ctrl, textvariable=self.sort_var,
                                       values=["Online first", "Offline first",
                                               "By Name A→Z", "By Name Z→A",
                                               "Today clicks high→low", "Today clicks low→high"],
                                       state="readonly", width=28)
        self.sort_combo.pack(side="left")

        self.count_lbl = ttk.Label(ctrl, text="0 Devices", font=("Arial", 10, "bold"))
        self.count_lbl.pack(side="right", padx=10)

        # canvas
        self.canvas = tk.Canvas(self.frame, bg=self.colors["BG_FRAME"] if self.colors else "#0f172a",
                                highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll = ttk.Scrollbar(self.frame, orient="vertical", command=self.canvas.yview)
        self.scroll.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.tiles_container = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.tiles_container, anchor="nw")
        self.tiles_container.bind(
            "<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        # Enable mouse wheel scrolling everywhere
        self.canvas.bind("<Enter>", self._on_canvas_enter)
        self.canvas.bind("<Leave>", self._on_canvas_leave)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", self._on_mousewheel)  # Linux scroll up
        self.canvas.bind("<Button-5>", self._on_mousewheel)  # Linux scroll down

        # tile map keeps references
        self.tile_map: Dict[int, dict] = {}

        # icon cache
        self.icons_folder = os.path.join(os.path.dirname(__file__), "icons")
        os.makedirs(self.icons_folder, exist_ok=True)
        self.icon_cache: Dict[str, ImageTk.PhotoImage] = {}

        # running flag
        self._running = False

        # Persistent Executors
        self.icon_executor = ThreadPoolExecutor(max_workers=2)
        self.net_executor = ThreadPoolExecutor(max_workers=2)

        # Stats for System Tray (Shared state)
        self.stats = {"online": 0, "total": 0, "today": 0}

        # Summary tile widget cache — built once, updated in-place
        # {"frame": tk.Frame, "container": tk.Frame, "labels": {d_str: {"col": .., "date": lbl, "val": lbl}}}
        self._summary_widgets: dict = {}

        # NOTE: first refresh is triggered from main.py (dashboard.refresh())

    # -------------------------
    # ICON LOADING UTIL
    # -------------------------
    def _sanitize(self, s: str) -> str:
        if not s:
            return ""
        return "".join([c if (c.isalnum() or c in ("-", "_")) else "_" for c in str(s)]).strip("_")

    def _get_icon_for(self, brand: str, model: str):
        b = (brand or "").strip()
        m = (model or "").strip()
        b_s = self._sanitize(b)
        m_s = self._sanitize(m)

        candidates = []
        if b_s or m_s:
            candidates.append(f"{b_s}_{m_s}.png")
        if b:
            candidates.append(f"{b.strip().lower()}.png")
        candidates.append("default.png")

        for name in candidates:
            key = f"icon::{name}"
            if key in self.icon_cache:
                return self.icon_cache[key]
            path = os.path.join(self.icons_folder, name)
            if os.path.exists(path):
                try:
                    img = Image.open(path).convert("RGBA").resize(ICON_SIZE)
                    p = ImageTk.PhotoImage(img)
                    self.icon_cache[key] = p
                    return p
                except Exception:
                    continue
        return None

    def _invalidate_icon_cache_for(self, brand: str, model: str):
        b_s = self._sanitize(brand or "")
        m_s = self._sanitize(model or "")
        keys = []
        if b_s or m_s:
            keys.append(f"icon::{b_s}_{m_s}.png")
        if brand:
            keys.append(f"icon::{brand.strip().lower()}.png")
        keys.append("icon::default.png")
        for k in keys:
            if k in self.icon_cache:
                del self.icon_cache[k]

    # -------------------------
    # REFRESH ENTRY
    # -------------------------
    def refresh(self):
        if self._running:
            return
        self._running = True
        try:
            self.btn_refresh.config(text="Refreshing…", state="disabled")
        except Exception:
            pass
        threading.Thread(target=self._worker, daemon=True).start()

    def _close_splash(self):
        if hasattr(self.root, "_splash"):
            try:
                self.root._splash.destroy()
            except Exception:
                pass
            if hasattr(self.root, "_splash"):
                try:
                    del self.root._splash
                except Exception:
                    pass

    # -------------------------
    # WORKER: PHASED (ping then SNMP counters then serial)
    # -------------------------
    def _worker(self):
        try:
            machines = fetch_machines()
        except Exception:
            machines = []

        is_first_load = len(self.tile_map) == 0

        if is_first_load and machines:
            self.frame.after(100, lambda: self._update_ui(machines, {}, {}, {}, render_placeholders=True))

        self.frame.after(100, self._close_splash)

        if not machines:
            self.frame.after(150, self._finish_refresh)
            return

        # PHASE 1 - FAST PING
        ping_map = {}
        ex = self.net_executor
        try:
            futures = {ex.submit(ping_ip, m['ip']): m['id'] for m in machines if m.get('ip')}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    ping_map[mid] = fut.result()
                except Exception:
                    ping_map[mid] = False
        except Exception:
            for m in machines:
                ping_map[m['id']] = False

        # PHASE 2 - SNMP read & insert counters
        snmp_counters = {}
        try:
            futures = {ex.submit(probe_and_read_counter, m['ip']): m['id']
                       for m in machines if ping_map.get(m['id'])}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    val = fut.result()
                    if val is not None:
                        snmp_counters[mid] = int(val)
                        insert_counter(mid, int(val))
                except Exception:
                    pass
        except Exception:
            pass

        # PHASE 3 - SNMP read serial (best-effort)
        try:
            futures = {ex.submit(probe_and_read_serial, m['ip']): m
                       for m in machines if ping_map.get(m['id'])}
            for fut in as_completed(futures):
                m = futures[fut]
                mid = m['id']
                try:
                    serial = fut.result()
                except Exception:
                    serial = None
                if serial:
                    if update_machine_serial:
                        try:
                            update_machine_serial(mid, serial)
                        except Exception:
                            pass
                    try:
                        m['serial'] = serial
                    except Exception:
                        pass
        except Exception:
            pass

        # PHASE 3.5 - Fetch printer details (toner, trays, errors)
        printer_details = {}
        try:
            futures = {
                ex.submit(lambda ip=m['ip']: {
                    'toner': probe_and_read_toner_levels(ip),
                    'trays': probe_and_read_paper_trays(ip),
                    'alerts': probe_and_read_printer_errors(ip)
                }): m['id']
                for m in machines if ping_map.get(m['id'])
            }
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    printer_details[mid] = fut.result()
                except Exception:
                    printer_details[mid] = {'toner': {}, 'trays': {}, 'alerts': []}
        except Exception:
            for m in machines:
                printer_details[m['id']] = {'toner': {}, 'trays': {}, 'alerts': []}

        # PHASE 4 - fetch daily rows
        daily_rows = {}
        try:
            futures = {ex.submit(fetch_daily_rows, m['id'], 8): m for m in machines}
            for fut in as_completed(futures):
                m = futures[fut]
                mid = m['id']
                try:
                    daily_rows[mid] = fut.result() or []
                except Exception:
                    daily_rows[mid] = []
        except Exception:
            for m in machines:
                daily_rows[m['id']] = []

        # PHASE 5 - Fetch latest counters
        latest_counters = {}
        try:
            futures = {ex.submit(fetch_latest_counter, m['id']): m['id'] for m in machines}
            for fut in as_completed(futures):
                mid = futures[fut]
                try:
                    latest_counters[mid] = fut.result()
                except Exception:
                    latest_counters[mid] = None
        except Exception:
            for m in machines:
                latest_counters[m['id']] = None

        try:
            self.frame.after(200, lambda: self._update_ui(
                machines, ping_map, daily_rows, latest_counters, snmp_counters, printer_details))
        except Exception:
            self.frame.after(200, self._finish_refresh)

    # -------------------------
    # FULL UI UPDATE
    # -------------------------
    def _update_ui(self, machines, ping_map, daily_rows, latest_counters,
                   snmp_counters=None, printer_details=None, render_placeholders=False):
        snmp_counters = snmp_counters or {}
        printer_details = printer_details or {}

        # Stats for Tray
        _today_str = time.strftime("%Y-%m-%d")
        _tray_online = sum(1 for m in machines if ping_map.get(m['id'], False))
        _tray_today = 0
        for m in machines:
            d_rows = daily_rows.get(m['id'], [])
            if d_rows:
                last_date, last_clicks = d_rows[-1]
                if last_date == _today_str:
                    _tray_today += int(last_clicks)
        self.stats = {"online": _tray_online, "total": len(machines), "today": _tray_today}

        infos = [(m['id'], m.get('name', ''), m.get('ip', ''), m.get('brand', ''),
                  m.get('model', ''), m.get('is_color', 1), m.get('serial', ''))
                 for m in machines]

        # filter
        mode = self.show_var.get()
        if mode == "Online":
            infos = [i for i in infos if ping_map.get(i[0], False)]
        elif mode == "Offline":
            infos = [i for i in infos if not ping_map.get(i[0], False)]

        # sort
        s = self.sort_var.get()
        if s == "Online first":
            infos.sort(key=lambda x: (0 if ping_map.get(x[0], False) else 1, (x[1] or "").lower()))
        elif s == "Offline first":
            infos.sort(key=lambda x: (0 if not ping_map.get(x[0], False) else 1, (x[1] or "").lower()))
        elif s == "By Name A→Z":
            infos.sort(key=lambda x: (x[1] or "").lower())
        elif s == "By Name Z→A":
            infos.sort(key=lambda x: (x[1] or "").lower(), reverse=True)
        elif s == "Today clicks high→low":
            infos.sort(
                key=lambda x: (daily_rows.get(x[0], [[None, 0]])[-1][1] if daily_rows.get(x[0]) else 0),
                reverse=True)

        current_ids = set(m['id'] for m in machines)
        total_machines = len(machines)

        # --- Summary Tile (no-flicker) ---
        global_daily: dict = {}
        today_obj = date.today()
        
        # If today is Monday, show 14 days (last Monday thru today)
        # Otherwise show 7 days
        is_monday = today_obj.weekday() == 0
        num_days = 14 if is_monday else 7
        
        for i in range(num_days):
            d_str = (today_obj - timedelta(days=num_days - 1 - i)).isoformat()
            global_daily[d_str] = 0
        for mid, rows in daily_rows.items():
            for r_date, r_clicks in rows:
                if r_date in global_daily:
                    global_daily[r_date] += int(r_clicks)
        self._draw_summary_tile(global_daily)

        # --- Machine tiles ---
        for idx, (mid, name, ip, brand, model, is_color, serial) in enumerate(infos):
            # Compute display values
            if render_placeholders:
                current_ctr = "..."
                today_clicks = "..."
                yesterday_clicks = "..."
                yesterday_total = "..."
            elif latest_counters:
                latest = latest_counters.get(mid)
                current_ctr = latest['total_impressions'] if latest else 0
                rows = daily_rows.get(mid, [])
                if len(rows) >= 2:
                    yesterday_clicks = rows[-2][1]
                    today_clicks = rows[-1][1]
                    yesterday_total = current_ctr - today_clicks
                elif len(rows) == 1:
                    yesterday_clicks = 0
                    today_clicks = rows[-1][1]
                    yesterday_total = current_ctr - today_clicks
                else:
                    yesterday_clicks = 0
                    today_clicks = 0
                    yesterday_total = None
            else:
                if mid in self.tile_map:
                    continue  # Don't overwrite real data with empty data
                current_ctr = "..."
                today_clicks = "..."
                yesterday_clicks = "..."
                yesterday_total = "..."

            # ---- CREATE tile the first time ----
            if mid not in self.tile_map:
                fr = tk.Frame(self.tiles_container,
                              bg=self.colors["BG_CARD"] if self.colors else "#1f2933",
                              bd=0, relief="flat", highlightthickness=1,
                              highlightcolor=self.colors.get("ACCENT_BLUE", "#3b82f6"),
                              highlightbackground="#374151")
                fr.configure(width=TILE_W, height=TILE_H)
                fr.grid_propagate(False)

                # Header with icon and basic info (improved styling)
                header = tk.Frame(fr, bg=fr["bg"])
                header.place(x=0, y=0, width=TILE_W, height=140)

                icon_img = self._get_icon_for(brand, model)
                if icon_img:
                    icon_lbl = tk.Label(header, image=icon_img, bg=header["bg"])
                    icon_lbl.image = icon_img
                else:
                    icon_lbl = tk.Label(header, text="🖨️", bg=header["bg"], font=("Arial", 20))
                icon_lbl.pack(side="left", padx=6, pady=4)

                info_frame = tk.Frame(header, bg=header["bg"])
                info_frame.pack(side="left", fill="both", expand=True, padx=6, pady=4)

                name_lbl = tk.Label(info_frame, text=(name or "Unnamed"), bg=header["bg"],
                                    fg=self.colors["FG_TEXT"] if self.colors else "#e5e7eb",
                                    font=("Arial", 13, "bold"))
                name_lbl.pack(anchor="w")

                brand_lbl = tk.Label(info_frame, text=f"{brand or ''} {model or ''}", bg=header["bg"], fg="#9ca3af",
                                    font=("Arial", 9))
                brand_lbl.pack(anchor="w")

                ip_lbl = tk.Label(info_frame, text=f"IP: {ip}", bg=header["bg"], fg="#9ca3af", font=("Arial", 9))
                ip_lbl.pack(anchor="w")

                serial_lbl = tk.Label(info_frame, text=f"SN: {serial or ''}", bg=header["bg"], fg="#9ca3af",
                                     font=("Arial", 8))
                serial_lbl.pack(anchor="w")

                status_lbl = tk.Label(info_frame, text="Checking...",
                                      bg="#fbbf24", fg="#000000", font=("Arial", 8, "bold"), padx=3, pady=1)
                status_lbl.pack(anchor="w", pady=(2, 0))

                last_seen_lbl = tk.Label(info_frame, text="",
                                         bg=header["bg"], fg="#fbbf24", font=("Arial", 7))
                last_seen_lbl.pack(anchor="w", pady=(0, 0))

                # Counter info frame - below status (improved styling)
                counter_frame = tk.Frame(header, bg=header["bg"])
                counter_frame.pack(side="right", fill="y", padx=6, pady=4)

                today_lbl = tk.Label(counter_frame, text="—", 
                                    bg=header["bg"], fg="#3b82f6", font=("Arial", 18, "bold"))
                today_lbl.pack(anchor="center")

                today_label = tk.Label(counter_frame, text="Today", 
                                      bg=header["bg"], fg="#9ca3af", font=("Arial", 7))
                today_label.pack(anchor="center")

                yesterday_lbl = tk.Label(counter_frame, text="—", 
                                        bg=header["bg"], fg="#fbbf24", font=("Arial", 12))
                yesterday_lbl.pack(anchor="center", pady=(4, 0))

                yesterday_label = tk.Label(counter_frame, text="Yesterday", 
                                          bg=header["bg"], fg="#9ca3af", font=("Arial", 7))
                yesterday_label.pack(anchor="center")

                # Content area with scrollable text (improved styling)
                content = tk.Frame(fr, bg=fr["bg"])
                content.place(x=0, y=140, width=TILE_W, height=TILE_H-140)
                content.pack_propagate(False)

                # Use Text widget for detailed scrollable info with better styling
                detail_text = tk.Text(content, bg=self.colors["BG_FRAME"] if self.colors else "#0f172a",
                                     fg=self.colors["FG_TEXT"] if self.colors else "#e5e7eb",
                                     font=("Courier", 9), borderwidth=0, highlightthickness=0,
                                     wrap="word", relief="flat")
                detail_text.pack(fill="both", expand=True, padx=5, pady=4)
                detail_text.config(state="disabled")
                
                # Enable mouse wheel scrolling on the detail text widget too
                detail_text.bind("<MouseWheel>", lambda e: self._on_text_scroll(e, detail_text))
                detail_text.bind("<Button-4>", lambda e: self._on_text_scroll(e, detail_text))
                detail_text.bind("<Button-5>", lambda e: self._on_text_scroll(e, detail_text))

                self.tile_map[mid] = {
                    "frame": fr,
                    "icon": icon_lbl,
                    "name": name_lbl,
                    "brand": brand_lbl,
                    "ip": ip_lbl,
                    "serial": serial_lbl,
                    "status": status_lbl,
                    "last_seen": last_seen_lbl,
                    "today": today_lbl,
                    "yesterday": yesterday_lbl,
                    "detail_text": detail_text,
                    "meta": (brand, model),
                    # Shadow values
                    "_val_status": None,
                    "_val_serial": None,
                    "_val_today": None,
                    "_val_yesterday": None,
                    "_val_details": None,
                    "_val_last_seen": None,
                }
                self._bind_click(fr, mid)

            # ---- UPDATE tile (every refresh, no-flicker) ----
            w = self.tile_map[mid]
            old_meta = w.get("meta", ("", ""))

            # Icon — only reload if brand/model changed
            if old_meta != (brand, model):
                self._invalidate_icon_cache_for(brand, model)
                icon_img = self._get_icon_for(brand, model)
                if icon_img:
                    try:
                        w["icon"].config(image=icon_img)
                        w["icon"].image = icon_img
                    except Exception:
                        pass
                w["meta"] = (brand, model)

            # Static-ish text fields
            _safe_config(w["name"], text=(name or "Unnamed"))
            _safe_config(w["brand"], text=f"{brand or ''} {model or ''}")
            _safe_config(w["ip"], text=f"IP: {ip}")

            new_serial = f"SN: {serial or ''}"
            if w["_val_serial"] != new_serial:
                w["serial"].config(text=new_serial)
                w["_val_serial"] = new_serial

            # Status badge
            if ping_map:
                online = ping_map.get(mid, False)
                new_status = "Online" if online else "Offline"
                if w["_val_status"] != new_status:
                    w["status"].config(
                        text=new_status,
                        bg=self.colors["ONLINE_GREEN"] if online else self.colors["OFFLINE_RED"],
                        fg="#ffffff"
                    )
                    w["_val_status"] = new_status
                
                # Update last_seen label below status
                if not online:
                    last_online = fetch_machine_last_online_or_latest_counter(mid) if fetch_machine_last_online_or_latest_counter else None
                    if last_online and format_last_online:
                        formatted_time = format_last_online(last_online)
                        last_seen_text = f"Last: {formatted_time}" if formatted_time else ""
                    else:
                        last_seen_text = ""
                else:
                    last_seen_text = ""
                
                if w["_val_last_seen"] != last_seen_text:
                    w["last_seen"].config(text=last_seen_text)
                    w["_val_last_seen"] = last_seen_text

            # Today's and Yesterday's clicks (displayed in header)
            rows = daily_rows.get(mid, [])
            
            if len(rows) >= 1:
                today_clicks = rows[-1][1]
                today_text = f"{today_clicks:,}" if isinstance(today_clicks, int) else f"{today_clicks}"
            else:
                today_text = "—"
            
            if w["_val_today"] != today_text:
                w["today"].config(text=today_text)
                w["_val_today"] = today_text

            if len(rows) >= 2:
                yesterday_clicks = rows[-2][1]
                yesterday_text = f"{yesterday_clicks:,}" if isinstance(yesterday_clicks, int) else f"{yesterday_clicks}"
            else:
                yesterday_text = "—"
            
            if w["_val_yesterday"] != yesterday_text:
                w["yesterday"].config(text=yesterday_text)
                w["_val_yesterday"] = yesterday_text

            # Build details text for the content area (device status, total pages, errors, toner, trays)
            detail_lines = []
            
            # Device Status
            if ping_map:
                device_status = "🟢 Running" if ping_map.get(mid, False) else "🔴 Offline"
                detail_lines.append(f"Device: {device_status}")
            else:
                detail_lines.append("Device: Checking...")
            
            # Total Pages
            if latest_counters and latest_counters.get(mid):
                total_pages = latest_counters[mid].get('total_impressions', 0)
                if isinstance(total_pages, int):
                    detail_lines.append(f"Total: {total_pages:,}")
                else:
                    detail_lines.append(f"Total: {total_pages}")
            
            detail_lines.append("")

            # Active Errors
            if mid in printer_details and printer_details[mid].get('alerts'):
                alerts = printer_details[mid].get('alerts', [])
                if alerts:
                    detail_lines.append("⚠️ ERRORS:")
                    for alert in alerts[:3]:
                        msg = alert.get('message', 'Alert')
                        if 'low' in msg.lower() or 'paper' in msg.lower():
                            emoji = "⚠️"
                        elif 'service' in msg.lower() or 'shutdown' in msg.lower():
                            emoji = "🔧"
                        elif 'tray' in msg.lower() or 'output' in msg.lower():
                            emoji = "📤"
                        else:
                            emoji = "⚠️"
                        clean_msg = msg.replace('Device: ', '').replace('Status: ', '').strip()
                        detail_lines.append(f" {emoji} {clean_msg[:40]}")
                    if len(alerts) > 3:
                        detail_lines.append(f" ... +{len(alerts)-3} more")
                else:
                    detail_lines.append("✓ No errors")
            else:
                detail_lines.append("✓ No errors")
            
            detail_lines.append("")

            # Toner Levels
            if mid in printer_details and printer_details[mid].get('toner'):
                toner = printer_details[mid].get('toner', {})
                if toner:
                    detail_lines.append("🎨 TONER:")
                    for color_name in ['Black', 'Cyan', 'Magenta', 'Yellow']:
                        color_key = color_name.lower()
                        if color_key in toner:
                            info = toner[color_key]
                            level = info.get('level', 0)
                            status = info.get('status', 'ok')
                            if status == "critical":
                                detail_lines.append(f" 🔴 {color_name}: {level}%")
                            elif status == "low":
                                detail_lines.append(f" 🟡 {color_name}: {level}%")
                            else:
                                detail_lines.append(f" ✓ {color_name}: {level}%")
            
            detail_lines.append("")

            # Paper Trays
            if mid in printer_details and printer_details[mid].get('trays'):
                trays = printer_details[mid].get('trays', {})
                if trays:
                    detail_lines.append("📄 TRAYS:")
                    for i in range(1, 5):
                        key = f'tray{i}'
                        if key in trays:
                            tray_info = trays[key]
                            name = tray_info.get('name', f'Tray {i}')
                            status = tray_info.get('status', 'unknown')
                            sheets = tray_info.get('sheets', 0)
                            if status.lower() == 'ok':
                                detail_lines.append(f" ✓ {name}: {sheets}")
                            else:
                                detail_lines.append(f" ✗ {name}: {status.upper()}")
            
            details_text = "\n".join(detail_lines)
            if w["_val_details"] != details_text:
                try:
                    detail_text_widget = w["detail_text"]
                    detail_text_widget.config(state="normal")
                    detail_text_widget.delete(1.0, tk.END)
                    detail_text_widget.insert(1.0, details_text)
                    detail_text_widget.config(state="disabled")
                    w["_val_details"] = details_text
                except Exception:
                    pass

            # Splash progress
            if hasattr(self.root, "_splash_progress"):
                loaded = idx + 1
                online_total = sum(1 for m in machines if ping_map.get(m['id'], False)) if ping_map else 0
                try:
                    self.root._splash_progress(loaded, total_machines, online_total,
                                               total_machines - online_total)
                except Exception:
                    pass

            # Grid placement — only re-grid if position changed
            r = idx // COLS
            c = idx % COLS
            try:
                fr = w["frame"]
                info = fr.grid_info()
                if not info or int(info.get("row", -1)) != r + 1 or int(info.get("column", -1)) != c:
                    fr.grid(row=r + 1, column=c, padx=12, pady=12)
            except Exception:
                pass

        # Remove tiles for deleted machines
        for mid in list(self.tile_map.keys()):
            if mid not in current_ids:
                try:
                    self.tile_map[mid]["frame"].destroy()
                except Exception:
                    pass
                del self.tile_map[mid]

        # Device count label
        total = len(infos)
        if ping_map:
            online_count = sum(1 for i in infos if ping_map.get(i[0], False))
            new_count = f"{online_count}/{total} Online"
        else:
            new_count = f"{total} Devices"
        _safe_config(self.count_lbl, text=new_count)

        self.frame.after(100, self._finish_refresh)

    # -------------------------
    # Summary Tile — build once, update in-place (no flicker)
    # -------------------------
    def _draw_summary_tile(self, global_daily: dict):
        """
        First call: create the frame and all labels, cache references in
        self._summary_widgets.
        Subsequent calls: update only the value labels whose text changed.
        The widget tree is never torn down, so there is zero flicker.
        """
        sorted_dates = sorted(global_daily.keys(), reverse=True)   # newest → oldest
        today_str = date.today().isoformat()

        # ---- First-time construction ----
        if not self._summary_widgets:
            frame = tk.Frame(self.tiles_container,
                             bg=self.colors["BG_CARD"] if self.colors else "#1f2933",
                             bd=0, relief="flat", highlightthickness=1,
                             highlightcolor=self.colors.get("ACCENT_BLUE", "#3b82f6"),
                             highlightbackground="#374151")
            frame.grid(row=0, column=0, columnspan=COLS, sticky="ew", padx=12, pady=12)

            container = tk.Frame(frame, bg=frame["bg"])
            container.pack(expand=True, fill="both", padx=10, pady=10)

            self._summary_widgets = {
                "frame": frame,
                "container": container,
                "labels": {},
                "dates": sorted_dates[:],
                "num_days": len(sorted_dates),
            }
            for d_str in sorted_dates:
                self._summary_widgets["labels"][d_str] = self._make_summary_col(
                    container, d_str, global_daily[d_str], d_str == today_str, len(sorted_dates)
                )
            return

        # ---- Day-set changed (e.g. midnight rollover) — rebuild once ----
        if self._summary_widgets.get("dates") != sorted_dates:
            for child in self._summary_widgets["frame"].winfo_children():
                child.destroy()
            container = tk.Frame(self._summary_widgets["frame"],
                                 bg=self._summary_widgets["frame"]["bg"])
            container.pack(expand=True, fill="both", padx=10, pady=10)
            self._summary_widgets["container"] = container
            self._summary_widgets["labels"] = {}
            self._summary_widgets["dates"] = sorted_dates[:]
            self._summary_widgets["num_days"] = len(sorted_dates)
            for d_str in sorted_dates:
                self._summary_widgets["labels"][d_str] = self._make_summary_col(
                    container, d_str, global_daily[d_str], d_str == today_str, len(sorted_dates)
                )
            return

        # ---- Normal refresh: update value labels only ----
        labels = self._summary_widgets["labels"]
        num_days = self._summary_widgets.get("num_days", 7)
        for d_str in sorted_dates:
            if d_str not in labels:
                continue
            val = global_daily.get(d_str, 0)
            is_today = (d_str == today_str)
            val_lbl = labels[d_str].get("val")
            if val_lbl:
                new_text = f"{val:,}"
                _safe_config(val_lbl, text=new_text)
                # Keep font/colour correct in case today rolled over since last build
                if is_today:
                    val_font = ("Arial", 16, "bold") if num_days > 10 else ("Arial", 20, "bold")
                    _safe_config(val_lbl,
                                 font=val_font,
                                 fg=self.colors["ACCENT_BLUE"] if self.colors else "#3b82f6")
                else:
                    val_font = ("Arial", 9, "bold") if num_days > 10 else ("Arial", 12, "bold")
                    _safe_config(val_lbl,
                                 font=val_font,
                                 fg=self.colors["FG_TEXT"] if self.colors else "#e5e7eb")

    def _make_summary_col(self, container, d_str: str, val: int, is_today: bool, total_days: int = 7) -> dict:
        """Create one day-column inside the summary bar and return widget refs."""
        bg = container["bg"]
        col = tk.Frame(container, bg=bg)
        col.pack(side="left", expand=True, fill="both")

        try:
            dt = date.fromisoformat(d_str)
            day_txt = f"{dt.strftime('%a')}\n{dt.strftime('%m-%d')}"
        except Exception:
            day_txt = d_str

        fg_date = (self.colors["FG_TEXT"] if self.colors else "#e5e7eb") if is_today else "#9ca3af"
        # Smaller font for date when showing 14 days
        date_font_size = 8 if total_days > 10 else 9
        date_lbl = tk.Label(col, text=day_txt, bg=bg, fg=fg_date, font=("Arial", date_font_size))
        date_lbl.pack(side="top", pady=(0, 2) if total_days > 10 else (0, 4))

        # Adjust value font sizes based on number of days
        if is_today:
            val_font = ("Arial", 16, "bold") if total_days > 10 else ("Arial", 20, "bold")
        else:
            val_font = ("Arial", 9, "bold") if total_days > 10 else ("Arial", 12, "bold")
        
        fg_val = (self.colors["ACCENT_BLUE"] if self.colors else "#3b82f6") if is_today else (
            self.colors["FG_TEXT"] if self.colors else "#e5e7eb")
        val_lbl = tk.Label(col, text=f"{val:,}", bg=bg, fg=fg_val, font=val_font)
        val_lbl.pack(side="top")

        return {"col": col, "date": date_lbl, "val": val_lbl}

    # -------------------------
    # Tile click -> open machine window
    # -------------------------
    def _bind_click(self, widget, mid):
        for w in [widget] + list(widget.winfo_children()):
            try:
                w.bind("<Button-1>", lambda e, mid=mid: self._open_machine_window(mid))
            except Exception:
                pass
        try:
            widget.bind("<Button-1>", lambda e, m=mid: self._open_machine_window(m), add="+")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_click(child, mid)

    def _open_machine_window(self, mid):
        try:
            from ui.machine_window import MachineWindow
        except Exception:
            return
        try:
            MachineWindow(self.root, mid, on_update_callback=self._machine_updated)
        except Exception:
            try:
                MachineWindow(self.root, mid)
            except Exception:
                pass

    # -------------------------
    # Called by MachineWindow when a machine is updated
    # -------------------------
    def _machine_updated(self, mid):
        # Simply refresh the dashboard to show updated information
        self.refresh()

    # -------------------------
    # Mouse wheel scrolling helpers
    # -------------------------
    def _on_canvas_enter(self, event):
        """Bind mouse wheel when entering canvas."""
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _on_canvas_leave(self, event):
        """Unbind mouse wheel when leaving canvas."""
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        """Handle mouse wheel scroll events."""
        if event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-3, "units")

    def _on_text_scroll(self, event, text_widget):
        """Handle mouse wheel scroll events on text widgets."""
        if event.num == 5 or event.delta < 0:
            text_widget.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            text_widget.yview_scroll(-3, "units")
        return "break"  # Prevent default behavior

    # -------------------------
    # finish refresh  
    # -------------------------
    def _finish_refresh(self):
        self._running = False
        try:
            self.btn_refresh.config(text="Refresh", state="normal")
        except Exception:
            pass

        def schedule_next():
            try:
                self.frame.after(10000, self.refresh)
            except Exception:
                try:
                    self.frame.after(1000, schedule_next)
                except Exception:
                    pass

        try:
            self.frame.after(500, schedule_next)  # Schedule refresh with 500ms delay
        except Exception:
            pass

    # -------------------------
    # Printer detail formatting helpers
    # -------------------------
    def _format_toner_display(self, toner_dict: dict) -> str:
        """Format toner info for display - COMPACT."""
        if not toner_dict:
            return "Toner: ---"
        
        # Show only colors that exist, in compact format
        colors_list = []
        for color in ['black', 'cyan', 'magenta', 'yellow']:
            if color in toner_dict:
                level = toner_dict[color].get('level', 0)
                # Only show if level is available
                if level >= 0:
                    colors_list.append(f"{color[0].upper()}:{level}%")
        
        if not colors_list:
            return "Toner: ---"
        
        # If all colors present, show compact: B:85% C:75% M:90% Y:80%
        # If only black: show just that one
        if len(colors_list) == 1:
            return f"Toner: {colors_list[0]}"
        elif len(colors_list) <= 2:
            return "Toner: " + "  ".join(colors_list)
        else:
            # Color printer: show most critical ones first
            return "Toner: " + "  ".join(colors_list[:3])  # Show first 3
    
    def _get_toner_color(self, toner_dict: dict) -> str:
        """Get color based on worst toner status."""
        if not toner_dict:
            return "#9ca3af"
        
        worst = 'ok'
        for color in toner_dict.values():
            status = color.get('status', 'ok')
            if status == 'critical':
                worst = 'critical'
                break
            elif status == 'low' and worst != 'critical':
                worst = 'low'
        
        return "#ff4757" if worst == 'critical' else "#ffa502" if worst == 'low' else "#00ff88"
    
    def _format_tray_display(self, tray_dict: dict) -> str:
        """Format paper tray info for display - Show problems or OK."""
        if not tray_dict:
            return "Trays: OK"
        
        # Find problematic trays
        problems = []
        all_ok = True
        for i in range(1, 5):
            key = f'tray{i}'
            if key in tray_dict:
                status = tray_dict[key]['status']
                if status != 'ok':
                    all_ok = False
                    # Get tray name if available
                    tray_name = tray_dict[key].get('name', f'T{i}').split()[-1]  # Get last word of name
                    problems.append(f"{tray_name}:{status[0].upper()}")
        
        if all_ok or not problems:
            return "Trays: OK"
        else:
            # Show problematic trays: "Tray1:E T4:L" (Tray1 Empty, Tray4 Low)
            return "Trays: " + " ".join(problems)
    
    def _format_alerts_display(self, alerts: list) -> str:
        """Format alerts for display - Show actual alert messages."""
        if not alerts:
            return "✓ OK"
        
        # Show the actual alert messages, not just count
        alert_msgs = []
        for alert in alerts[:2]:  # Show first 2 alerts max
            msg = alert.get('message', 'Alert')
            # Clean up message: extract just the key part
            msg = msg.split(' - ')[0].strip() if ' - ' in msg else msg
            # Remove redundant prefixes like "Device:", "Status:"
            if msg.startswith('Device:'):
                msg = msg.replace('Device:', '').strip()
            if msg.startswith('Status:'):
                msg = msg.replace('Status:', '').strip()
            # Truncate longer messages
            msg = msg[:18] if len(msg) > 18 else msg
            alert_msgs.append(msg)
        
        if len(alerts) > 2:
            # If more than 2 alerts, show count
            alert_str = "; ".join(alert_msgs) + f"; +{len(alerts)-2} more"
        else:
            alert_str = "; ".join(alert_msgs)
        
        return f"⚠ {alert_str}"

    def shutdown(self):
        self._running = True
        if hasattr(self, 'icon_executor'):
            self.icon_executor.shutdown(wait=False)
        if hasattr(self, 'net_executor'):
            self.net_executor.shutdown(wait=False)
        self.icon_cache.clear()
        for w in self.tile_map.values():
            if "icon" in w:
                w["icon"].image = None
        self.tile_map.clear()

    def get_frame(self):
        return self.frame
