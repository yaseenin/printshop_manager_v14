# ui/machines.py
# Machines Tab - Table + Actions + Auto-Refresh
# - Full-width table
# - Columns: Name, Brand, Model, IP, Status, Today Clicks, Current Counter
# - Actions: Edit, Ping, SNMP Refresh, Web UI, Deactivate
# - Auto-refresh options: 30s, 60s, 120s, Off
# - Uses MachineWindow for editing

import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed

import tkinter as tk
from tkinter import ttk, messagebox

from core.models import (
    fetch_machines,
    fetch_daily_rows,
    fetch_latest_counter,
    insert_machine,
    insert_counter,
    deactivate_machine,
)
from core.snmp import probe_and_read_counter
from core.ping import ping_ip
from ui.theme import apply_theme, CURRENT_THEME


class MachinesTab:
    def __init__(self, parent, root=None, style=None, rebuild_callback=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()
        self.rebuild_callback = rebuild_callback

        # Apply theme
        self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        self._BG_FRAME = self.colors.get("BG_FRAME", "#020617")
        self._BG_CARD = self.colors.get("BG_CARD", "#0f172a")
        self._FG_TEXT = self.colors.get("FG_TEXT", "#e5e7eb")
        self._ACCENT = self.colors.get("ACCENT_BLUE", "#3b82f6")
        self._ONLINE = self.colors.get("ONLINE_GREEN", "#16a34a")
        self._OFFLINE = self.colors.get("OFFLINE_RED", "#dc2626")

        # State
        self._loading = False
        self._refresh_job = None
        self._all_rows = []  # list of dicts for all machines
        self._sort_column = "name"
        self._sort_reverse = False

        # UI state
        self.search_var = tk.StringVar()
        self.refresh_mode = tk.StringVar(value="off")  # "30", "60", "120", "off"

        # Frame
        self.frame = ttk.Frame(parent)
        self._build_ui()

        # Initial load
        self.frame.after(2000, self.refresh)

    def get_frame(self):
        return self.frame

    # ------------------------------------------------------------------
    # UI BUILD
    # ------------------------------------------------------------------
    def _build_ui(self):
        # Top bar
        top = ttk.Frame(self.frame)
        top.pack(fill="x", padx=10, pady=(6, 4))

        title = ttk.Label(
            top,
            text="Machines",
            font=("Arial", 16, "bold"),
            foreground=self._FG_TEXT,
        )
        title.pack(side="left")

        # Search box
        search_frame = ttk.Frame(top)
        search_frame.pack(side="left", padx=(20, 0))
        ttk.Label(search_frame, text="Search:", foreground=self._FG_TEXT).pack(
            side="left", padx=(0, 4)
        )
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var, width=24)
        search_entry.pack(side="left")
        search_entry.bind("<KeyRelease>", lambda e: self._rebuild_tree())

        # Auto-refresh options
        refresh_frame = ttk.Frame(top)
        refresh_frame.pack(side="left", padx=(20, 0))
        ttk.Label(refresh_frame, text="Auto-refresh:", foreground=self._FG_TEXT).pack(
            side="left", padx=(0, 4)
        )

        def make_rr(label, value):
            rb = ttk.Radiobutton(
                refresh_frame,
                text=label,
                value=value,
                variable=self.refresh_mode,
                command=self._on_refresh_mode_change,
            )
            rb.pack(side="left")
            return rb

        self.rb_30 = make_rr("30s", "30")
        self.rb_60 = make_rr("60s", "60")
        self.rb_120 = make_rr("120s", "120")
        self.rb_off = make_rr("Off", "off")

        # Spacer
        ttk.Label(top, text=" ").pack(side="left", padx=10)

        # Buttons: Add, Refresh
        btn_add = ttk.Button(
            top, text="Add Machine", command=self._on_add_machine
        )
        btn_add.pack(side="right", padx=(4, 0))

        btn_refresh = ttk.Button(
            top, text="Refresh Now", command=self.refresh
        )
        btn_refresh.pack(side="right")

        # Table container
        table_frame = ttk.Frame(self.frame)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        self.tree = ttk.Treeview(
            table_frame,
            columns=(
                "name",
                "brand",
                "model",
                "ip",
                "status",
                "today",
                "total",
            ),
            show="headings",
            selectmode="browse",
        )

        # Scrollbar
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # Configure columns
        col_defs = {
            "name": ("Name", 180),
            "brand": ("Brand", 110),
            "model": ("Model", 110),
            "ip": ("IP Address", 130),
            "status": ("Status", 100),
            "today": ("Today's Clicks", 110),
            "total": ("Current Counter", 130),
        }
        for cid, (text, width) in col_defs.items():
            self.tree.heading(
                cid,
                text=text,
                command=lambda c=cid: self._on_heading_click(c),
            )
            self.tree.column(cid, width=width, anchor="w", stretch=True)

        # Tags for status coloring
        self.tree.tag_configure("online", foreground=self._ONLINE)
        self.tree.tag_configure("offline", foreground=self._OFFLINE)

        # Bind double-click to edit
        self.tree.bind("<Double-1>", self._on_double_click)

        # Right-click context menu
        self.menu = tk.Menu(self.tree, tearoff=0)
        self.menu.add_command(label="Edit", command=self._menu_edit)
        self.menu.add_command(label="Ping Test", command=self._menu_ping)
        self.menu.add_command(label="SNMP Refresh", command=self._menu_snmp)
        self.menu.add_command(label="Open Web UI", command=self._menu_web)
        self.menu.add_separator()
        self.menu.add_command(label="Deactivate Machine", command=self._menu_deactivate)

        self.tree.bind("<Button-3>", self._on_right_click)

        # Status bar
        self.status_lbl = ttk.Label(
            self.frame,
            text="Ready.",
            foreground=self._FG_TEXT,
        )
        self.status_lbl.pack(fill="x", padx=10, pady=(0, 6))

    # ------------------------------------------------------------------
    # AUTO-REFRESH HANDLING
    # ------------------------------------------------------------------
    def _on_refresh_mode_change(self):
        # Cancel previous job
        if self._refresh_job is not None and self.root is not None:
            try:
                self.root.after_cancel(self._refresh_job)
            except Exception:
                pass
            self._refresh_job = None

        mode = self.refresh_mode.get()
        if mode == "off":
            self.status_lbl.config(text="Auto-refresh disabled.")
            return

        self.status_lbl.config(text=f"Auto-refresh every {mode}s enabled.")
        self._schedule_refresh()

    def _schedule_refresh(self):
        mode = self.refresh_mode.get()
        if mode == "off" or self.root is None:
            return
        try:
            interval = int(mode)
        except Exception:
            return

        def _do():
            self.refresh()
            self._schedule_refresh()

        self._refresh_job = self.root.after(interval * 1000, _do)

    # ------------------------------------------------------------------
    # REFRESH / DATA LOAD
    # ------------------------------------------------------------------
    def refresh(self):
        """Public refresh API."""
        if self._loading:
            return
        self._loading = True
        self.status_lbl.config(text="Loading machine list…")
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        try:
            try:
                machines = fetch_machines(active_only=True)
            except Exception:
                machines = []

            # Pre-ping in parallel to speed up loading
            ping_results = {}
            with ThreadPoolExecutor(max_workers=8) as executor:
                future_to_ip = {executor.submit(ping_ip, m.get("ip", "")): m["id"] for m in machines if m.get("ip")}
                for future in as_completed(future_to_ip):
                    mid = future_to_ip[future]
                    try:
                        ping_results[mid] = future.result()
                    except Exception:
                        ping_results[mid] = False

            rows = []
            today_str = time.strftime("%Y-%m-%d")

            for m in machines:
                mid = m["id"]
                name = m.get("name", "") or "Unnamed"
                brand = m.get("brand", "") or ""
                model = m.get("model", "") or ""
                ip = m.get("ip", "") or ""

                # ping
                online = ping_results.get(mid, False)

                # latest counter
                try:
                    latest = fetch_latest_counter(mid)
                    total_ctr = int(latest["total_impressions"]) if latest else 0
                except Exception:
                    total_ctr = 0

                # today's clicks from last 2 days
                try:
                    dr = fetch_daily_rows(mid, days=2) or []
                except Exception:
                    dr = []
                today_clicks = 0
                if dr:
                    last_date, last_clicks = dr[-1]
                    if last_date == today_str:
                        today_clicks = int(last_clicks or 0)

                rows.append(
                    {
                        "id": mid,
                        "name": name,
                        "brand": brand,
                        "model": model,
                        "ip": ip,
                        "online": online,
                        "today": today_clicks,
                        "total": total_ctr,
                    }
                )

            self._all_rows = rows
            if self.root is not None:
                self.root.after(1, self._rebuild_tree)
        finally:
            self._loading = False

    # ------------------------------------------------------------------
    # TABLE BUILD
    # ------------------------------------------------------------------
    def _rebuild_tree(self, _event=None):
        # Clear existing
        for item in self.tree.get_children():
            self.tree.delete(item)

        # Filter by search
        q = (self.search_var.get() or "").strip().lower()
        rows = list(self._all_rows)

        if q:
            def _match(r):
                return (
                    q in r["name"].lower()
                    or q in r["brand"].lower()
                    or q in r["model"].lower()
                    or q in r["ip"].lower()
                )
            rows = [r for r in rows if _match(r)]

        # Sort by selected column
        col = self._sort_column
        rev = self._sort_reverse

        def _sort_key(r):
            if col in ("today", "total"):
                return r.get(col, 0)
            elif col == "status":
                return 0 if r.get("online") else 1
            else:
                return (r.get(col, "") or "").lower()

        rows.sort(key=_sort_key, reverse=rev)

        # Insert
        for r in rows:
            mid = r["id"]
            tags = ("online",) if r["online"] else ("offline",)
            status_txt = "Online" if r["online"] else "Offline"
            self.tree.insert(
                "",
                "end",
                iid=str(mid),
                values=(
                    r["name"],
                    r["brand"],
                    r["model"],
                    r["ip"],
                    status_txt,
                    r["today"],
                    f"{r['total']:,}",
                ),
                tags=tags,
            )

        self.status_lbl.config(
            text=f"{len(rows)} machine(s) shown. Total machines: {len(self._all_rows)}"
        )

    def _on_heading_click(self, column):
        # Map visible column id to internal key
        col_map = {
            "name": "name",
            "brand": "brand",
            "model": "model",
            "ip": "ip",
            "status": "status",
            "today": "today",
            "total": "total",
        }
        key = col_map.get(column, "name")
        if self._sort_column == key:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = key
            self._sort_reverse = False
        self._rebuild_tree()

    # ------------------------------------------------------------------
    # ROW SELECTION HELPERS
    # ------------------------------------------------------------------
    def _get_selected_mid(self):
        sel = self.tree.selection()
        if not sel:
            return None
        try:
            return int(sel[0])
        except Exception:
            return None

    def _find_row(self, mid):
        for r in self._all_rows:
            if r["id"] == mid:
                return r
        return None

    # ------------------------------------------------------------------
    # CONTEXT MENU
    # ------------------------------------------------------------------
    def _on_right_click(self, event):
        try:
            row_id = self.tree.identify_row(event.y)
            if row_id:
                self.tree.selection_set(row_id)
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _menu_edit(self):
        self._do_edit()

    def _menu_ping(self):
        self._do_ping()

    def _menu_snmp(self):
        self._do_snmp_refresh()

    def _menu_web(self):
        self._do_open_web()

    def _menu_deactivate(self):
        self._do_deactivate()

    # ------------------------------------------------------------------
    # ACTIONS
    # ------------------------------------------------------------------
    def _on_double_click(self, event):
        # Double-click -> edit
        self._do_edit()

    def _do_edit(self):
        mid = self._get_selected_mid()
        if mid is None:
            messagebox.showinfo("Edit", "Please select a machine.")
            return

        # lazy import to avoid circular dependencies
        try:
            from ui.machine_window import MachineWindow
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open Machine window:\n{e}")
            return

        def _cb(_mid):
            # callback after save/icon update
            self.refresh()

        try:
            MachineWindow(self.root, mid, on_update_callback=_cb)
        except Exception as e:
            messagebox.showerror("Error", f"Cannot open Machine window:\n{e}")

    def _do_ping(self):
        mid = self._get_selected_mid()
        if mid is None:
            messagebox.showinfo("Ping", "Please select a machine.")
            return

        row = self._find_row(mid)
        if not row:
            messagebox.showerror("Ping", "Machine not found in data.")
            return

        ip = row["ip"]
        if not ip:
            messagebox.showerror("Ping", "No IP configured for this machine.")
            return

        def worker():
            start = time.time()
            try:
                ok = ping_ip(ip)
                ms = int((time.time() - start) * 1000)
            except Exception:
                ok = False
                ms = 0

            def done():
                if ok:
                    messagebox.showinfo("Ping", f"{ip} is ONLINE • {ms} ms")
                else:
                    messagebox.showwarning("Ping", f"{ip} is OFFLINE or unreachable.")
                self.refresh()

            if self.root:
                self.root.after(1, done)

        threading.Thread(target=worker, daemon=True).start()

    def _do_snmp_refresh(self):
        mid = self._get_selected_mid()
        if mid is None:
            messagebox.showinfo("SNMP", "Please select a machine.")
            return

        row = self._find_row(mid)
        if not row:
            messagebox.showerror("SNMP", "Machine not found in data.")
            return

        ip = row["ip"]
        if not ip:
            messagebox.showerror("SNMP", "No IP configured for this machine.")
            return

        def worker():
            try:
                val = probe_and_read_counter(ip)
                if val is not None:
                    insert_counter(mid, int(val))
                    time.sleep(0.15)
                    msg = f"SNMP updated.\nNew counter: {val}"
                    ok = True
                else:
                    msg = "SNMP returned no value."
                    ok = False
            except Exception as e:
                msg = f"SNMP Error:\n{e}"
                ok = False

            def done():
                if ok:
                    messagebox.showinfo("SNMP", msg)
                else:
                    messagebox.showwarning("SNMP", msg)
                self.refresh()

            if self.root:
                self.root.after(1, done)

        threading.Thread(target=worker, daemon=True).start()

    def _do_open_web(self):
        mid = self._get_selected_mid()
        if mid is None:
            messagebox.showinfo("Web UI", "Please select a machine.")
            return

        row = self._find_row(mid)
        if not row:
            messagebox.showerror("Web UI", "Machine not found in data.")
            return

        ip = row["ip"]
        if not ip:
            messagebox.showerror("Web UI", "No IP configured for this machine.")
            return

        try:
            webbrowser.open(f"http://{ip}", new=2)
        except Exception as e:
            messagebox.showerror("Web UI", str(e))

    def _do_deactivate(self):
        mid = self._get_selected_mid()
        if mid is None:
            messagebox.showinfo("Deactivate", "Please select a machine.")
            return

        row = self._find_row(mid)
        if not row:
            messagebox.showerror("Deactivate", "Machine not found in data.")
            return

        name = row["name"]
        if not messagebox.askyesno(
            "Deactivate",
            f"Deactivate machine:\n\n{name}\n\nIt will be hidden from Dashboard & Reports.",
        ):
            return

        try:
            deactivate_machine(mid)
            messagebox.showinfo("Deactivate", "Machine deactivated.")
            self.refresh()
            if self.rebuild_callback:
                try:
                    self.rebuild_callback()
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Deactivate", str(e))

    # ------------------------------------------------------------------
    # ADD MACHINE
    # ------------------------------------------------------------------
    def _on_add_machine(self):
        # Simple modal form to add a machine
        if not self.root:
            return

        win = tk.Toplevel(self.root)
        win.title("Add Machine")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        try:
            win.configure(bg=self._BG_FRAME)
        except Exception:
            pass

        frm = ttk.Frame(win)
        frm.pack(fill="both", expand=True, padx=10, pady=10)

        name_var = tk.StringVar()
        brand_var = tk.StringVar()
        model_var = tk.StringVar()
        ip_var = tk.StringVar()

        def add_row(row, label, var):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="e", padx=4, pady=4)
            ttk.Entry(frm, textvariable=var, width=30).grid(row=row, column=1, padx=4, pady=4)

        add_row(0, "Name:", name_var)
        add_row(1, "Brand:", brand_var)
        add_row(2, "Model:", model_var)
        add_row(3, "IP:", ip_var)

        def on_save():
            name = name_var.get().strip()
            ip = ip_var.get().strip()
            brand = brand_var.get().strip()
            model = model_var.get().strip()

            if not name or not ip:
                messagebox.showerror("Add Machine", "Name and IP are required.")
                return
            try:
                # Default is_color=1 (color) – can be edited later in MachineWindow if needed
                insert_machine(name, ip, brand, model, 1)
                messagebox.showinfo("Add Machine", "Machine added.")
                win.destroy()
                self.refresh()
                if self.rebuild_callback:
                    try:
                        self.rebuild_callback()
                    except Exception:
                        pass
            except Exception as e:
                messagebox.showerror("Add Machine", str(e))

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=4, column=0, columnspan=2, pady=(10, 0))
        ttk.Button(btn_row, text="Cancel", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(btn_row, text="Save", command=on_save).pack(side="right")

        # Center on parent
        self._center_window(win)

    def _center_window(self, win):
        win.update_idletasks()
        if not self.root:
            return
        w = win.winfo_width()
        h = win.winfo_height()
        pw = self.root.winfo_width()
        ph = self.root.winfo_height()
        px = self.root.winfo_x()
        py = self.root.winfo_y()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        win.geometry(f"{w}x{h}+{x}+{y}")
