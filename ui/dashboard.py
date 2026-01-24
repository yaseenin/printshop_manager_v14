# ui/dashboard.py
# Print Shop Manager v14 - Dashboard (icons, fast ping + SNMP, instant tile update)
# Includes serial number auto-detection and display.

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict

import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk

from core.models import fetch_machines, insert_counter, fetch_latest_counter, fetch_daily_rows
from core.ping import ping_ip
from core.snmp import probe_and_read_counter, probe_and_read_serial
# update_machine_serial may not exist in older models.py — we call it defensively
try:
    from core.models import update_machine_serial
except Exception:
    update_machine_serial = None

from ui.theme import apply_theme, CURRENT_THEME

# tile sizing
TILE_W = 320
TILE_H = 200
COLS = 3

ICON_SIZE = (92, 120)  # Medium icons (48x48)


class DashboardTab:
    def __init__(self, parent, root=None, style=None):
        self.parent = parent
        self.root = root
        self.style = style
        self.colors = apply_theme(root, style, CURRENT_THEME) if (root and style) else None

        self.frame = ttk.Frame(parent)
        self.topbar = ttk.Frame(self.frame)
        self.topbar.pack(fill="x", pady=6)

        ttk.Label(self.topbar, text="Print Shop Manager v14",
                  font=("Arial", 18, "bold")).pack(side="left", padx=8)

        self.btn_refresh = ttk.Button(self.topbar, text="Refresh", command=self.refresh)
        self.btn_refresh.pack(side="right", padx=8)

        # Controls
        ctrl = ttk.Frame(self.frame)
        ctrl.pack(fill="x", pady=(0, 6))
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

        # tile map keeps references
        self.tile_map: Dict[int, dict] = {}

        # icon cache
        self.icons_folder = os.path.join(os.path.dirname(__file__), "icons")
        os.makedirs(self.icons_folder, exist_ok=True)
        self.icon_cache: Dict[str, ImageTk.PhotoImage] = {}

        # running flag
        self._running = False

        # NOTE: first refresh is triggered from main.py (dashboard.refresh())

    # -------------------------
    # ICON LOADING UTIL
    # -------------------------
    def _sanitize(self, s: str) -> str:
        if not s:
            return ""
        return "".join([c if (c.isalnum() or c in ("-", "_")) else "_" for c in str(s)]).strip("_")

    def _get_icon_for(self, brand: str, model: str):
        """
        Priority:
        1) ui/icons/<brand>_<model>.png
        2) ui/icons/<brand_lower>.png
        3) ui/icons/default.png
        Returns a PhotoImage or None.
        Caches images for speed.
        """
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
        """Safely closes the splash screen if it exists."""
        if hasattr(self.root, "_splash"):
            try:
                self.root._splash.destroy()
            except Exception:
                pass
            # Check again before deleting attribute
            if hasattr(self.root, "_splash"):
                try:
                    del self.root._splash
                except Exception:
                    pass

    # -------------------------
    # WORKER: PHASED (ping then SNMP counters then serial)
    # -------------------------
    def _worker(self):
        # load machines
        try:
            machines = fetch_machines()
        except Exception:
            machines = []

        # PHASE 0: Render placeholder tiles immediately and close splash
        self.frame.after(1, lambda: self._update_ui(machines, {}, {}, {}))
        self.frame.after(100, self._close_splash)

        if not machines:
            self.frame.after(150, self._finish_refresh)
            return

        # PHASE 1 - FAST PING
        ping_map = {}
        try:
            with ThreadPoolExecutor(max_workers=8) as ex:
                futures = {ex.submit(ping_ip, m['ip']): m['id'] for m in machines}
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
        try:
            # Reduce workers to prevent UI freeze, and skip offline machines
            with ThreadPoolExecutor(max_workers=3) as ex:
                # Only probe machines that responded to ping
                futures = {ex.submit(probe_and_read_counter, m['ip']): m for m in machines if ping_map.get(m['id'])}
                for fut in as_completed(futures):
                    m = futures[fut]
                    mid = m['id']
                    try:
                        val = fut.result()
                    except Exception:
                        val = None
                    if val is not None:
                        try:
                            insert_counter(mid, int(val))
                        except Exception:
                            pass
        except Exception:
            pass

        # PHASE 3 - SNMP read serial (best-effort, non-blocking)
        try:
            with ThreadPoolExecutor(max_workers=3) as ex:
                futures = {ex.submit(probe_and_read_serial, m['ip']): m for m in machines if ping_map.get(m['id'])}
                for fut in as_completed(futures):
                    m = futures[fut]
                    mid = m['id']
                    try:
                        serial = fut.result()
                    except Exception:
                        serial = None
                    if serial:
                        # try to persist serial in DB if models exposes update_machine_serial
                        if update_machine_serial:
                            try:
                                update_machine_serial(mid, serial)
                            except Exception:
                                pass
                        # Also update local machine dict so UI shows it immediately
                        try:
                            m['serial'] = serial
                        except Exception:
                            pass
        except Exception:
            pass

        # fetch daily rows for all machines
        daily_rows = {}
        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
                futures = {ex.submit(fetch_daily_rows, m['id'], 2): m for m in machines}
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

        # PHASE 5 - Fetch latest counters to avoid slow DB calls in UI thread
        latest_counters = {}
        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
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

        # schedule final UI update (full)
        try:
            self.frame.after(1, lambda: self._update_ui(machines, ping_map, daily_rows, latest_counters))
        except Exception:
            self.frame.after(1, self._finish_refresh)
            pass

    # -------------------------
    # FULL UI UPDATE (after SNMP)
    # -------------------------
    def _update_ui(self, machines, ping_map, daily_rows, latest_counters):
        # prepare infos
        infos = [(m['id'], m.get('name', ''), m.get('ip', ''), m.get('brand', ''), m.get('model', ''), m.get('is_color', 1), m.get('serial', ''))
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
            infos.sort(key=lambda x: (daily_rows.get(x[0], [[None, 0]])[-1][1] if daily_rows.get(x[0]) else 0),
                       reverse=True)

        # render tiles
        current_ids = set(m['id'] for m in machines)
        total_machines = len(machines)

        for idx, (mid, name, ip, brand, model, is_color, serial) in enumerate(infos):
            # Use pre-fetched data if available, otherwise show placeholder
            if latest_counters:
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
                # Initial placeholder render
                current_ctr = "..."
                today_clicks = "..."
                yesterday_clicks = "..."
                yesterday_total = "..."

            if mid not in self.tile_map:
                # create tile frame
                fr = tk.Frame(self.tiles_container,
                              bg=self.colors["BG_CARD"] if self.colors else "#1f2933",
                              bd=1, relief="solid", highlightthickness=1)
                fr.configure(width=TILE_W, height=TILE_H)
                fr.grid_propagate(False)

                # icon (image or placeholder)
                icon_img = self._get_icon_for(brand, model)
                if icon_img:
                    icon_lbl = tk.Label(fr, image=icon_img, bg=fr["bg"])
                    icon_lbl.image = icon_img
                else:
                    icon_lbl = tk.Label(fr, text="🖨️", bg=fr["bg"], font=("Arial", 18))
                icon_lbl.place(x=12, y=12)

                # name & meta
                name_lbl = tk.Label(fr, text=(name or "Unnamed"), bg=fr["bg"],
                                    fg=self.colors["FG_TEXT"] if self.colors else "#e5e7eb",
                                    font=("Arial", 11, "bold"))
                name_lbl.place(x=12 + ICON_SIZE[0] + 12, y=12)

                brand_lbl = tk.Label(fr, text=f"{brand or ''} {model or ''}", bg=fr["bg"],
                                     fg="#9ca3af")
                brand_lbl.place(x=12 + ICON_SIZE[0] + 12, y=36)

                ip_lbl = tk.Label(fr, text=f"IP: {ip}", bg=fr["bg"], fg="#9ca3af")
                ip_lbl.place(x=12 + ICON_SIZE[0] + 12, y=56)

                # serial label
                serial_lbl = tk.Label(fr, text=f"SN: {serial or ''}", bg=fr["bg"], fg="#9ca3af")
                serial_lbl.place(x=12 + ICON_SIZE[0] + 12, y=76)

                status_lbl = tk.Label(fr, text="Checking...",
                                      bg="#fbbf24", fg="#000000", font=("Arial", 9, "bold"))
                status_lbl.place(x=12 + ICON_SIZE[0] + 12, y=98)

                # yesterday inline
                ytext = "Yesterday: ..."

                yest_lbl = tk.Label(fr, text=ytext, bg=fr["bg"], fg="#fbbf24", font=("Arial", 10, "bold"))
                yest_lbl.place(x=12, y=120)

                # current counter
                curr_lbl = tk.Label(fr, text=f"Current Counter: ...", bg=fr["bg"],
                                    fg=self.colors["FG_TEXT"] if self.colors else "#e5e7eb")
                curr_lbl.place(x=12, y=142)

                # today clicks (bigger)
                today_lbl = tk.Label(fr, text=f"Today's Clicks: ...", bg=fr["bg"],
                                     fg=self.colors["ACCENT_BLUE"] if self.colors else "#3b82f6",
                                     font=("Arial", 15, "bold"))
                today_lbl.place(x=12, y=164)

                # store widgets
                self.tile_map[mid] = {
                    "frame": fr,
                    "icon": icon_lbl,
                    "name": name_lbl,
                    "brand": brand_lbl,
                    "ip": ip_lbl,
                    "serial": serial_lbl,
                    "status": status_lbl,
                    "yest": yest_lbl,
                    "current": curr_lbl,
                    "today": today_lbl,
                    "meta": (brand, model)
                }

                # bind clicks for tile (open machine window)
                self._bind_click(fr, mid)

            # This block will run for both creation and update
            w = self.tile_map[mid]
            
            # update widgets
            old_meta = w.get("meta", ("", ""))

            # update icon if brand/model changed
            if old_meta != (brand, model):
                self._invalidate_icon_cache_for(brand, model)
                icon_img = self._get_icon_for(brand, model)
                if icon_img:
                    try:
                        w["icon"].config(image=icon_img)
                        w["icon"].image = icon_img
                    except Exception:
                        pass

            try:
                w["name"].config(text=(name or "Unnamed"))
                w["brand"].config(text=f"{brand or ''} {model or ''}")
                w["ip"].config(text=f"IP: {ip}")
                # update serial label
                try:
                    w["serial"].config(text=f"SN: {serial or ''}")
                except Exception:
                    pass

                w["meta"] = (brand, model)
            except Exception:
                pass

            # Update text fields if data is available
            if latest_counters:
                w["current"].config(text=f"Current Counter: {current_ctr:,}")

                # animate today's clicks
                try:
                    current_text = w["today"].cget("text")
                    old_val_str = current_text.split(":")[-1].strip()
                    old_val = int(old_val_str) if old_val_str.isdigit() else 0
                except Exception:
                    old_val = 0
                self._animate_count(w["today"], old_val, today_clicks)

                if yesterday_total is None:
                    w["yest"].config(text="Yesterday: No data")
                else:
                    w["yest"].config(text=f"Yesterday: {yesterday_total:,} ({yesterday_clicks} clicks)")

            # Update status based on ping_map
            if ping_map:
                online = ping_map.get(mid, False)
                w["status"].config(
                    text="Online" if online else "Offline",
                    bg=self.colors["ONLINE_GREEN"] if online else self.colors["OFFLINE_RED"],
                    fg="#ffffff"
                )

            # Splash progress update (for splash in main.py)
            if hasattr(self.root, "_splash_progress"):
                loaded = idx + 1
                if ping_map:
                    online_total = sum(1 for m in machines if ping_map.get(m['id'], False))
                else:
                    online_total = 0
                offline_total = total_machines - online_total
                try:
                    self.root._splash_progress(loaded, total_machines, online_total, offline_total)
                except Exception:
                    pass

            # place tile
            r = idx // COLS
            c = idx % COLS
            try:
                self.tile_map[mid]["frame"].grid(row=r, column=c, padx=12, pady=12)
            except Exception:
                pass

        # remove tiles for deleted machines
        for mid in list(self.tile_map.keys()):
            if mid not in current_ids:
                try:
                    self.tile_map[mid]["frame"].destroy()
                except Exception:
                    pass
                del self.tile_map[mid]

        # update counts
        total = len(infos)
        if ping_map:
            online_count = sum(1 for m in infos if ping_map.get(m[0], False))
            self.count_lbl.config(text=f"{online_count}/{total} Online")
        else:
            self.count_lbl.config(text=f"{total} Devices")

        # done
        try:
            self.frame.after(1, self._finish_refresh)
        except Exception:
            pass

    # -------------------------
    # Tile click -> open machine window with callback
    # -------------------------
    def _bind_click(self, widget, mid):
        for w in [widget] + list(widget.winfo_children()):
            try:
                w.bind("<Button-1>", lambda e, mid=mid: self._open_machine_window(mid))
            except Exception:
                pass

    def _open_machine_window(self, mid):
        # lazy import to avoid circular imports
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
    # Called by MachineWindow when a machine is updated (instant update)
    # -------------------------
    def _machine_updated(self, mid):
        """
        Try to refresh only that tile for speed.
        If the machine is not present in tile_map (filtered), do a full refresh.
        """
        try:
            machines = fetch_machines()
        except Exception:
            machines = []

        # find machine record
        m = None
        for mm in machines:
            if mm["id"] == mid:
                m = mm
                break

        if not m:
            self.refresh()
            return

        # fetch latest counter + daily rows
        latest = fetch_latest_counter(mid)
        current_ctr = latest["total_impressions"] if latest else 0
        rows = fetch_daily_rows(mid, days=2) or []
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

        # update tile if exists
        if mid in self.tile_map:
            w = self.tile_map[mid]
            self._invalidate_icon_cache_for(m.get("brand", ""), m.get("model", ""))
            icon_img = self._get_icon_for(m.get("brand", ""), m.get("model", ""))
            if icon_img:
                try:
                    w["icon"].config(image=icon_img)
                    w["icon"].image = icon_img
                except Exception:
                    pass
            try:
                w["name"].config(text=m.get("name", ""))
                w["brand"].config(text=f"{m.get('brand','')} {m.get('model','')}")
                w["ip"].config(text=f"IP: {m.get('ip','')}")
                # update serial display
                try:
                    w["serial"].config(text=f"SN: {m.get('serial','')}")
                except Exception:
                    pass
                if yesterday_total is None:
                    w["yest"].config(text="Yesterday: No data")
                else:
                    w["yest"].config(text=f"Yesterday: {yesterday_total:,} ({yesterday_clicks} clicks)")
                w["current"].config(text=f"Current Counter: {current_ctr:,}")

                try:
                    current_text = w["today"].cget("text")
                    old_val = int(current_text.split(":")[-1].strip())
                except Exception:
                    old_val = 0
                self._animate_count(w["today"], old_val, today_clicks)

                # Check status in background to avoid hanging UI
                def check_status():
                    try:
                        is_online = ping_ip(m.get("ip", ""))
                    except Exception:
                        is_online = False
                    self.frame.after(0, lambda: w["status"].config(
                        text="Online" if is_online else "Offline",
                        bg=self.colors["ONLINE_GREEN"] if is_online else self.colors["OFFLINE_RED"]
                    ))
                threading.Thread(target=check_status, daemon=True).start()
            except Exception:
                pass
        else:
            self.refresh()

    # -------------------------
    # Smooth count animation for "Today's Clicks"
    # -------------------------
    def _animate_count(self, label, old, new):
        """Update the counter label instantly as requested."""
        label.config(text=f"Today's Clicks: {new}")



    # -------------------------
    # finish refresh
    # -------------------------
    def _finish_refresh(self):
        self._running = False
        try:
            self.btn_refresh.config(text="Refresh", state="normal")
        except Exception:
            pass

        # Auto-refresh is now scheduled from here
        self.frame.after(10000, self.refresh)

    def shutdown(self):
        self._running = True  # Prevent further refreshes
        self.icon_cache.clear()
        for w in self.tile_map.values():
            if "icon" in w:
                w["icon"].image = None
        self.tile_map.clear()

    def get_frame(self):
        return self.frame
