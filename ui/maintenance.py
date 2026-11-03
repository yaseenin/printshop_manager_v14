# ui/maintenance.py
# Maintenance Tab – Consumables, Yield Summary & Replacement Logs

import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

from core.models import fetch_machines, fetch_latest_counter
from core.consumables_db import (
    get_machine_consumables,
    reset_consumable,
    ensure_machine_consumables,
    get_logs,
)

from ui.theme import apply_theme, CURRENT_THEME

CARD_WIDTH = 420
COLS = 3


class MaintenanceTab:
    def __init__(self, parent, root=None, style=None, rebuild_callback=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()
        self.rebuild_callback = rebuild_callback

        self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        self.BG_FRAME = self.colors.get("BG_FRAME", "#020617")
        self.BG_CARD = self.colors.get("BG_CARD", "#0f172a")
        self.FG_TEXT = self.colors.get("FG_TEXT", "#e5e7eb")

        self._loading = False
        self._machines_data = []
        self._machine_widgets = {}

        self.frame = ttk.Frame(parent)

        # -------------------------
        # INNER SUB-TABS
        # -------------------------
        self.subtabs = ttk.Notebook(self.frame)
        self.subtabs.pack(fill="both", expand=True)

        # MAIN PAGES
        self.page_consumables = ttk.Frame(self.subtabs)
        self.page_yield = ttk.Frame(self.subtabs)
        self.page_logs = ttk.Frame(self.subtabs)

        self.subtabs.add(self.page_consumables, text="Consumables")
        self.subtabs.add(self.page_yield, text="Yield Summary")
        self.subtabs.add(self.page_logs, text="Replacement Log")

        self.subtabs.bind("<<NotebookTabChanged>>", self._on_tab_change)

        # Build UI
        self._build_consumables_ui()
        self._build_yield_ui()
        self._build_logs_ui()

        # initial load
        self.frame.after(500, self.refresh)

    def get_frame(self):
        return self.frame

    # Mouse wheel scrolling helpers for canvas
    def _on_canvas_enter(self):
        self.canvas.bind_all("<MouseWheel>", self._on_canvas_scroll)
        self.canvas.bind_all("<Button-4>", self._on_canvas_scroll)
        self.canvas.bind_all("<Button-5>", self._on_canvas_scroll)

    def _on_canvas_leave(self):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_canvas_scroll(self, event):
        if event.num == 5 or event.delta < 0:
            self.canvas.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            self.canvas.yview_scroll(-3, "units")
        return "break"

    # Mouse wheel scrolling helpers for treeviews
    def _on_treeview_scroll(self, treeview, event):
        if event.num == 5 or event.delta < 0:
            treeview.yview_scroll(3, "units")
        elif event.num == 4 or event.delta > 0:
            treeview.yview_scroll(-3, "units")
        return "break"

    # -------------------------------------------------------------------
    #  BUILD – PAGE 1 – CONSUMABLES
    # -------------------------------------------------------------------
    def _build_consumables_ui(self):

        top = ttk.Frame(self.page_consumables)
        top.pack(fill="x", padx=10, pady=(6, 4))

        ttk.Label(
            top,
            text="Maintenance – Consumables",
            font=("Arial", 16, "bold"),
            foreground=self.FG_TEXT,
        ).pack(side="left")

        self.status_lbl = ttk.Label(
            top, text="Loading…", foreground=self.FG_TEXT
        )
        self.status_lbl.pack(side="left", padx=(20, 0))

        ttk.Button(top, text="Refresh", command=self.refresh).pack(side="right")

        # Scroll Area
        container = ttk.Frame(self.page_consumables)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        self.canvas = tk.Canvas(container, bg=self.BG_FRAME, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)

        vsb = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        vsb.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=vsb.set)

        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")),
        )
        
        # Enable mouse wheel scrolling on canvas
        self.canvas.bind("<Enter>", lambda _: self._on_canvas_enter())
        self.canvas.bind("<Leave>", lambda _: self._on_canvas_leave())
        self.canvas.bind("<MouseWheel>", self._on_canvas_scroll)
        self.canvas.bind("<Button-4>", self._on_canvas_scroll)
        self.canvas.bind("<Button-5>", self._on_canvas_scroll)

    # -------------------------------------------------------------------
    #  BUILD – PAGE 2 – YIELD SUMMARY
    # -------------------------------------------------------------------
    def _build_yield_ui(self):

        top_frame = ttk.Frame(self.page_yield)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))
        
        ttk.Label(
            top_frame,
            text="Yield Summary (Used clicks per consumable)",
            font=("Arial", 16, "bold"),
            foreground=self.FG_TEXT,
        ).pack(side="left")

        # Filter
        ttk.Label(top_frame, text="Filter:", foreground=self.FG_TEXT).pack(side="left", padx=(20, 5))
        self.yield_machine_var = tk.StringVar(value="All Machines")
        self.yield_machine_combo = ttk.Combobox(
            top_frame, textvariable=self.yield_machine_var, state="readonly", width=30
        )
        self.yield_machine_combo.pack(side="left")
        self.yield_machine_combo.bind("<<ComboboxSelected>>", lambda e: self._load_yield())

        frame = ttk.Frame(self.page_yield)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        columns = ("machine", "consumable", "category",
                   "last", "used", "percent")

        self.yield_tree = ttk.Treeview(
            frame, columns=columns, show="headings", height=20
        )
        self.yield_tree.pack(fill="both", expand=True)
        
        # Enable mouse wheel scrolling on yield treeview
        self.yield_tree.bind("<Enter>", lambda _: self._bind_treeview(self.yield_tree))
        self.yield_tree.bind("<Leave>", lambda _: self._unbind_treeview())
        self.yield_tree.bind("<MouseWheel>", lambda e: self._on_treeview_scroll(self.yield_tree, e))
        self.yield_tree.bind("<Button-4>", lambda e: self._on_treeview_scroll(self.yield_tree, e))
        self.yield_tree.bind("<Button-5>", lambda e: self._on_treeview_scroll(self.yield_tree, e))

        headers = {
            "machine": "Machine",
            "consumable": "Consumable",
            "category": "Category",
            "last": "Last Counter",
            "used": "Used",
            "percent": "% Used",
        }

        for col, txt in headers.items():
            self.yield_tree.heading(col, text=txt)
            self.yield_tree.column(col, width=140, anchor="w")

        ttk.Button(top_frame, text="Refresh", command=self._load_yield).pack(side="right")

    # -------------------------------------------------------------------
    #  BUILD – PAGE 3 – LOG VIEWER
    # -------------------------------------------------------------------
    def _build_logs_ui(self):

        top_frame = ttk.Frame(self.page_logs)
        top_frame.pack(fill="x", padx=10, pady=(10, 5))

        ttk.Label(
            top_frame,
            text="Consumable Replacement Log",
            font=("Arial", 16, "bold"),
            foreground=self.FG_TEXT,
        ).pack(side="left")

        # Filter
        ttk.Label(top_frame, text="Filter:", foreground=self.FG_TEXT).pack(side="left", padx=(20, 5))
        self.log_machine_var = tk.StringVar(value="All Machines")
        self.log_machine_combo = ttk.Combobox(
            top_frame, textvariable=self.log_machine_var, state="readonly", width=30
        )
        self.log_machine_combo.pack(side="left")
        self.log_machine_combo.bind("<<ComboboxSelected>>", lambda e: self._load_logs())

        frame = ttk.Frame(self.page_logs)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        cols = ("ts", "machine", "consumable",
                "old", "new", "yield", "note")

        self.logs_tree = ttk.Treeview(
            frame,
            columns=cols,
            show="headings",
            height=25,
        )
        self.logs_tree.pack(fill="both", expand=True)
        
        # Enable mouse wheel scrolling on logs treeview
        self.logs_tree.bind("<Enter>", lambda _: self._bind_treeview(self.logs_tree))
        self.logs_tree.bind("<Leave>", lambda _: self._unbind_treeview())
        self.logs_tree.bind("<MouseWheel>", lambda e: self._on_treeview_scroll(self.logs_tree, e))
        self.logs_tree.bind("<Button-4>", lambda e: self._on_treeview_scroll(self.logs_tree, e))
        self.logs_tree.bind("<Button-5>", lambda e: self._on_treeview_scroll(self.logs_tree, e))

        headers = {
            "ts": "Date",
            "machine": "Machine",          # fixed to show name
            "consumable": "Consumable",
            "old": "Old Ctr",
            "new": "New Ctr",
            "yield": "Yield Used",
            "note": "Note",
        }

        for col, txt in headers.items():
            self.logs_tree.heading(col, text=txt)
            self.logs_tree.column(col, width=140, anchor="w")

        ttk.Button(top_frame, text="Refresh", command=self._load_logs).pack(side="right")

    # -------------------------------------------------------------------
    #  TAB CHANGE = auto refresh correct page
    # -------------------------------------------------------------------
    def _on_tab_change(self, event):
        tab = self.subtabs.tab(self.subtabs.select(), "text")
        if tab == "Consumables":
            self.refresh()
        elif tab == "Yield Summary":
            self._load_yield()
        elif tab == "Replacement Log":
            self._load_logs()

    def _bind_treeview(self, treeview):
        treeview.bind_all("<MouseWheel>", lambda e: self._on_treeview_scroll(treeview, e))
        treeview.bind_all("<Button-4>", lambda e: self._on_treeview_scroll(treeview, e))
        treeview.bind_all("<Button-5>", lambda e: self._on_treeview_scroll(treeview, e))

    def _unbind_treeview(self):
        try:
            self.yield_tree.unbind_all("<MouseWheel>")
            self.yield_tree.unbind_all("<Button-4>")
            self.yield_tree.unbind_all("<Button-5>")
            self.logs_tree.unbind_all("<MouseWheel>")
            self.logs_tree.unbind_all("<Button-4>")
            self.logs_tree.unbind_all("<Button-5>")
        except Exception:
            pass

    # -------------------------------------------------------------------
    #  MAIN REFRESH
    # -------------------------------------------------------------------
    def refresh(self):
        if self._loading:
            return

        self._loading = True
        self.status_lbl.config(text="Loading…")

        threading.Thread(target=self._load_worker, daemon=True).start()

    # -------------------------------------------------------------------
    #  LOAD MACHINES + CONSUMABLES
    # -------------------------------------------------------------------
    def _load_worker(self):

        try:
            try:
                machines = fetch_machines(active_only=True)
            except:
                machines = []

            data = []
            for m in machines:
                mid = m["id"]
                name = m.get("name", "") or "Unnamed"
                ip = m.get("ip", "") or ""

                try:
                    latest = fetch_latest_counter(mid)
                    current_ctr = int(latest["total_impressions"]) if latest else 0
                except:
                    current_ctr = 0

                try:
                    ensure_machine_consumables(mid, current_ctr)
                except:
                    pass

                try:
                    cons = get_machine_consumables(mid)
                except:
                    cons = []

                data.append(
                    {
                        "id": mid,
                        "name": name,
                        "ip": ip,
                        "current_ctr": current_ctr,
                        "consumables": cons,
                    }
                )

            self._machines_data = data
            if self.root:
                self.root.after(1, self._render_consumables)

        finally:
            self._loading = False

    # -------------------------------------------------------------------
    #  RENDER CONSUMABLES PAGE (3-COLUMN GRID)
    # -------------------------------------------------------------------
    def _render_consumables(self):
        for child in self.inner.winfo_children():
            child.destroy()

        self._machine_widgets.clear()

        if not self._machines_data:
            ttk.Label(
                self.inner,
                text="No machines found.",
                foreground=self.FG_TEXT,
            ).grid(row=0, column=0, padx=10, pady=20, sticky="w")
            self.status_lbl.config(text="0 machine(s) loaded.")
            return

        # configure 3 columns so tiles form a grid
        for c in range(COLS):
            self.inner.grid_columnconfigure(c, weight=1)

        for idx, mdata in enumerate(self._machines_data):
            self._build_machine_block(idx, mdata)

        self.status_lbl.config(
            text=f"{len(self._machines_data)} machine(s) loaded."
        )
        self._update_filter_dropdowns()

        # ensure canvas scrollregion updated
        self.canvas.update_idletasks()

    # ------------------
    # CATEGORY MAPPING
    # ------------------
    def _friendly_group(self, cat):
        cat = (cat or "").lower()
        return {
            "toner": "TONERS",
            "drum": "DRUM SYSTEM",
            "developer": "DEVELOPER SYSTEM",
            "laser": "LASER / IMAGING CLEAN",
            "transfer": "TRANSFER SYSTEM",
            "fuser": "FUSER SYSTEM",
            "roller": "FEED & MEDIA ROLLERS",
            "waste": "WASTE & CLEANING",
        }.get(cat, cat.upper())

    # -------------------------------------------------------------------
    #  BUILD BLOCK FOR EACH MACHINE (now placed in 3-column grid)
    # -------------------------------------------------------------------
    def _build_machine_block(self, idx, mdata):

        mid = mdata["id"]
        name = mdata["name"]
        ip = mdata["ip"]
        ctr = mdata["current_ctr"]
        cons_list = mdata["consumables"]

        # compute grid position
        row = idx // COLS
        col = idx % COLS

        outer = tk.Frame(
            self.inner,
            bg=self.BG_CARD,
            bd=1,
            relief="solid",
            width=CARD_WIDTH,
        )
        outer.grid(row=row, column=col, padx=8, pady=8, sticky="n")
        outer.grid_propagate(False)

        # HEADER
        head = tk.Frame(outer, bg=self.BG_CARD)
        head.pack(fill="x", padx=10, pady=(8, 4))

        tk.Label(
            head,
            text=f"{name} ({ip})",
            bg=self.BG_CARD,
            fg=self.FG_TEXT,
            font=("Arial", 12, "bold"),
        ).pack(side="left")

        tk.Label(
            head,
            text=f"Current Counter: {ctr:,}",
            bg=self.BG_CARD,
            fg=self.FG_TEXT,
            font=("Arial", 10),
        ).pack(side="right")

        # BODY
        body = tk.Frame(outer, bg=self.BG_CARD)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        # CATEGORY DROPDOWN
        cat_frame = tk.Frame(body, bg=self.BG_CARD)
        cat_frame.pack(fill="x")

        tk.Label(
            cat_frame,
            text="Category:",
            bg=self.BG_CARD,
            fg=self.FG_TEXT,
        ).pack(side="left")

        cats = ["ALL CATEGORIES"] + sorted(
            {self._friendly_group(c["category"]) for c in cons_list}
        )

        cat_var = tk.StringVar(value=cats[0])
        cat_combo = ttk.Combobox(
            cat_frame, textvariable=cat_var, values=cats, state="readonly", width=30
        )
        cat_combo.pack(side="left", padx=6)

        # CONSUMABLE DROPDOWN
        drop_frame = tk.Frame(body, bg=self.BG_CARD)
        drop_frame.pack(fill="x", pady=4)

        tk.Label(
            drop_frame, text="Consumable:", bg=self.BG_CARD, fg=self.FG_TEXT
        ).pack(side="left")

        cons_var = tk.StringVar()
        cons_combo = ttk.Combobox(
            drop_frame, textvariable=cons_var, width=50, state="readonly"
        )
        cons_combo.pack(side="left", padx=6)

        # INFO
        info = tk.Frame(body, bg=self.BG_CARD)
        info.pack(fill="x")

        lbl_used = tk.Label(info, text="Used clicks: -", bg=self.BG_CARD, fg=self.FG_TEXT)
        lbl_used.pack(anchor="w")

        lbl_last = tk.Label(info, text="Last changed counter: -", bg=self.BG_CARD, fg=self.FG_TEXT)
        lbl_last.pack(anchor="w")

        lbl_note = tk.Label(info, text="Note: ", bg=self.BG_CARD, fg=self.FG_TEXT)
        lbl_note.pack(anchor="w")

        # BUTTONS
        btn_frame = tk.Frame(body, bg=self.BG_CARD)
        btn_frame.pack(fill="x", pady=4)

        ttk.Button(
            btn_frame,
            text="Mark Replaced / Serviced",
            command=lambda m=mid: self._on_reset_consumable(m),
        ).pack(side="left")

        ttk.Button(
            btn_frame, text="Refresh Machine", command=self.refresh
        ).pack(side="right")

        # STORE
        self._machine_widgets[mid] = {
            "current_ctr": ctr,
            "consumables": cons_list,
            "cat_var": cat_var,
            "cat_combo": cat_combo,
            "cons_var": cons_var,
            "cons_combo": cons_combo,
            "lbl_used": lbl_used,
            "lbl_last": lbl_last,
            "lbl_note": lbl_note,
        }

        cat_combo.bind("<<ComboboxSelected>>",
                       lambda e, m=mid: self._populate_consumable_dropdown(m))
        cons_combo.bind("<<ComboboxSelected>>",
                        lambda e, m=mid: self._update_consumable_info(m))

        self._populate_consumable_dropdown(mid)

    def _update_filter_dropdowns(self):
        items = ["All Machines"]
        for m in self._machines_data:
            items.append(f"{m['name']} ({m['id']})")
        
        if hasattr(self, 'yield_machine_combo'):
            curr = self.yield_machine_var.get()
            self.yield_machine_combo['values'] = items
            if curr not in items:
                self.yield_machine_combo.current(0)
        
        if hasattr(self, 'log_machine_combo'):
            curr = self.log_machine_var.get()
            self.log_machine_combo['values'] = items
            if curr not in items:
                self.log_machine_combo.current(0)

    # -------------------------------------------------------------------
    # DROPDOWN HANDLERS
    # -------------------------------------------------------------------
    def _populate_consumable_dropdown(self, mid):
        w = self._machine_widgets[mid]
        cons_list = w["consumables"]
        cat_filter = w["cat_var"].get()

        items = []
        mapping = {}
        last_group = None

        for c in cons_list:
            group = self._friendly_group(c["category"])
            if cat_filter != "ALL CATEGORIES" and group != cat_filter:
                continue

            if group != last_group:
                items.append(f"--- {group} ---")
                last_group = group

            disp = c["name"]
            items.append(disp)
            mapping[disp] = c["code"]

        w["display_map"] = mapping
        w["cons_combo"].configure(values=items)

        # Select first valid
        for it in items:
            if it in mapping:
                w["cons_var"].set(it)
                break

        self._update_consumable_info(mid)

    def _update_consumable_info(self, mid):
        w = self._machine_widgets[mid]
        disp = w["cons_var"].get()
        mapping = w.get("display_map", {})

        code = mapping.get(disp)
        if not code:
            w["lbl_used"].config(text="Used clicks: -")
            w["lbl_last"].config(text="Last changed counter: -")
            w["lbl_note"].config(text="Note: ")
            return

        # locate
        last_ctr = 0
        note = ""
        for c in w["consumables"]:
            if c["code"] == code:
                last_ctr = c["last_counter"]
                note = c.get("note", "")
                break

        used = max(0, w["current_ctr"] - last_ctr)

        w["lbl_used"].config(text=f"Used clicks: {used:,}")
        w["lbl_last"].config(text=f"Last changed counter: {last_ctr:,}")
        w["lbl_note"].config(text=f"Note: {note}")

    # -------------------------------------------------------------------
    # RESET CONSUMABLE
    # -------------------------------------------------------------------
    def _on_reset_consumable(self, mid):
        w = self._machine_widgets[mid]
        disp = w["cons_var"].get()
        mapping = w.get("display_map", {})
        code = mapping.get(disp)

        if not code:
            messagebox.showinfo("Select Consumable",
                                "Select a specific consumable.")
            return

        if not messagebox.askyesno(
            "Confirm",
            f"Mark replaced/serviced?\nMachine: {mid}\nConsumable: {disp}",
        ):
            return

        note = simpledialog.askstring(
            "Optional Note",
            "Enter note for this replacement:",
            parent=self.root,
        )

        # fresh counter
        latest = fetch_latest_counter(mid)
        current_ctr = int(latest["total_impressions"]) if latest else 0

        try:
            reset_consumable(mid, code, current_ctr, note)
            messagebox.showinfo("Updated",
                                f"Updated to {current_ctr:,} clicks.")
        except Exception as e:
            messagebox.showerror("Error", str(e))
            return

        self.refresh()

    # -------------------------------------------------------------------
    # YIELD SUMMARY LOADER
    # -------------------------------------------------------------------
    def _load_yield(self):
        self.yield_tree.delete(*self.yield_tree.get_children())

        target_mid = None
        sel = self.yield_machine_var.get()
        if sel != "All Machines":
            try:
                target_mid = int(sel.split("(")[-1].strip(")"))
            except:
                pass

        for m in self._machines_data:
            mid = m["id"]
            if target_mid is not None and mid != target_mid:
                continue

            name = m["name"]
            ctr = m["current_ctr"]

            for c in m["consumables"]:
                used = max(0, ctr - c["last_counter"])
                pct = (
                    f"{(used / max(1, c['last_counter'])) * 100:.1f}%"
                    if c["last_counter"] > 0
                    else "-"
                )

                self.yield_tree.insert(
                    "",
                    "end",
                    values=(
                        name,
                        c["name"],
                        self._friendly_group(c["category"]),
                        f"{c['last_counter']:,}",
                        f"{used:,}",
                        pct,
                    ),
                )

    # -------------------------------------------------------------------
    # LOG VIEWER LOADER (machine name lookup)
    # -------------------------------------------------------------------
    def _load_logs(self):
        self.logs_tree.delete(*self.logs_tree.get_children())

        target_mid = None
        sel = self.log_machine_var.get()
        if sel != "All Machines":
            try:
                target_mid = int(sel.split("(")[-1].strip(")"))
            except:
                pass

        # build lookup mid -> name from current machines list
        mid_to_name = {m["id"]: m["name"] for m in self._machines_data}

        for row in get_logs():
            # get_logs() returns: (ts, mid, code, consumable_name, old_ctr, new_ctr, used, note)
            try:
                ts, mid, code, consumable_name, old_ctr, new_ctr, used, note = row
            except Exception:
                # fallback to previous ordering if different
                ts = row[0]
                mid = row[1] if len(row) > 1 else None
                code = row[2] if len(row) > 2 else ""
                consumable_name = row[3] if len(row) > 3 else ""
                old_ctr = row[4] if len(row) > 4 else 0
                new_ctr = row[5] if len(row) > 5 else 0
                used = row[6] if len(row) > 6 else 0
                note = row[7] if len(row) > 7 else ""

            if target_mid is not None and mid != target_mid:
                continue

            # resolve machine name; if not available, show "Machine <id>"
            machine = mid_to_name.get(mid, f"Machine {mid}" if mid is not None else "Unknown")

            self.logs_tree.insert(
                "",
                "end",
                values=(
                    ts,
                    machine,
                    consumable_name,
                    f"{old_ctr:,}",
                    f"{new_ctr:,}",
                    f"{used:,}",
                    note,
                ),
            )
