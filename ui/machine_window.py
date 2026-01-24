# ui/machine_window.py
# Machine Settings & Analytics (Ultra Clean Minimal Dark)
# Compatible with Print Shop Manager v14 dashboard
# - Modal, centered
# - Dark theme consistent with ui.theme.apply_theme
# - Edit machine, SNMP refresh, icon browse (saves ui/icons/brand_model.png)
# - Line + Bar graphs (matplotlib)
# - Open Web UI, Ping Test
# - Calls on_update callback to refresh dashboard tile instantly

import os
import time
import threading
import webbrowser
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

from core.models import (
    update_machine,
    fetch_latest_counter,
    fetch_daily_rows,
    insert_counter,
    fetch_machines,
)
from core.snmp import probe_and_read_counter
from core.ping import ping_ip

from ui.theme import apply_theme, CURRENT_THEME


def _sanitize_filename(s: str) -> str:
    parts = []
    for ch in s:
        if ch.isalnum() or ch in ("-", "_"):
            parts.append(ch)
        elif ch.isspace():
            parts.append("_")
    return "".join(parts) or "icon"


class MachineWindow:
    """
    Modal machine window — ultra clean minimal dark theme.
    on_update_callback(mid) will be invoked when machine is saved or icon changed.
    """

    def __init__(self, root, machine_id, on_update_callback=None):
        self.root = root
        self.mid = machine_id
        self.on_update = on_update_callback

        # Top-level window
        self.win = tk.Toplevel(root)
        self.win.title("Machine Settings & Analytics")
        self.win.geometry("700x660")
        self.win.resizable(True, True)  # resizable
        self.win.transient(root)
        self.win.grab_set()  # Modal

        # Apply theme and colors
        self.style = ttk.Style()
        self.colors = apply_theme(self.win, self.style, CURRENT_THEME)
        # Fallbacks if theme keys missing
        self._set_fallback_colors()

        # Ensure icons folder exists
        self.icons_folder = os.path.join(os.path.dirname(__file__), "icons")
        os.makedirs(self.icons_folder, exist_ok=True)

        # Load machine record using fetch_machines
        rec = self._get_machine_record()
        if not rec:
            messagebox.showerror("Error", "Machine not found.")
            self.win.destroy()
            return
        self.machine = rec

        # Prepare styles for dark minimal look
        self._prepare_styles()

        # Load icon cache & preview
        self._load_icon_images()

        # Store all daily rows for graph toggles
        self._all_rows = []

        # Build UI
        self._build_ui()

        # Fill values and fetch counters/graphs
        self._populate_fields()
        self._update_counter_info_async()

        # Center on parent
        self._center()

    # -------------------------
    # Helpers
    # -------------------------
    def _set_fallback_colors(self):
        # Ensure keys exist; keep safe defaults
        c = self.colors
        self._BG_FRAME = c.get("BG_FRAME", "#0b1220")
        self._BG_CARD = c.get("BG_CARD", "#0f1726")
        self._FG_TEXT = c.get("FG_TEXT", "#e5e7eb")
        self._ACCENT = c.get("ACCENT_BLUE", "#3b82f6")
        self._ONLINE = c.get("ONLINE_GREEN", "#16a34a")
        self._OFFLINE = c.get("OFFLINE_RED", "#dc2626")

    def _prepare_styles(self):
        # Background style for frames
        bg = self._BG_FRAME
        card = self._BG_CARD
        fg = self._FG_TEXT

        # general window background
        try:
            self.win.configure(bg=bg)
        except Exception:
            pass

        # TLabel, TFrame background adjustments
        self.style.configure("MP.TFrame", background=bg)
        self.style.configure("Card.TLabelframe", background=card, borderwidth=0)
        self.style.configure("Card.TLabelframe.Label", background=card, foreground=fg)
        self.style.configure("Card.TFrame", background=card)
        self.style.configure("Dark.TLabel", background=card, foreground=fg)
        self.style.configure("Small.TLabel", background=bg, foreground=fg, font=("Arial", 9))
        self.style.configure("Title.TLabel", background=bg, foreground=fg, font=("Arial", 14, "bold"))
        # Entry style (fieldbackground is the inner bg)
        try:
            self.style.configure("Dark.TEntry", fieldbackground=card, foreground=fg, background=card)
        except Exception:
            pass
        # Buttons
        self.style.configure("Accent.TButton", background=card, foreground=fg)
        self.style.map("Accent.TButton",
                       background=[("active", self._ACCENT), ("!active", card)])

    def _get_machine_record(self):
        machines = fetch_machines()
        for m in machines:
            if m["id"] == self.mid:
                return m
        return None

    # -------------------------
    # Icon utilities
    # -------------------------
    def _load_icon_images(self):
        """Load default and machine-specific icon (if present)."""
        self.icon_cache = {}

        def load(path, size=(60, 60)):
            try:
                img = Image.open(path).convert("RGBA")
                return ImageTk.PhotoImage(img.resize(size))
            except Exception:
                return None

        default_path = os.path.join(self.icons_folder, "default.png")
        self.icon_cache["default"] = load(default_path) if os.path.exists(default_path) else None

        brand = (self.machine.get("brand") or "").strip()
        model = (self.machine.get("model") or "").strip()
        fname = f"{_sanitize_filename(brand)}_{_sanitize_filename(model)}.png"
        fpath = os.path.join(self.icons_folder, fname)
        self.machine_icon_filename = fname if os.path.exists(fpath) else None
        self.icon_cache["machine"] = load(fpath) if os.path.exists(fpath) else self.icon_cache["default"]

    def _save_icon_to_brand_model(self, src_path):
        """Copy and convert the chosen image to ui/icons/<brand>_<model>.png (PNG)."""
        ext = os.path.splitext(src_path)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg"):
            messagebox.showerror("Icon", "Please select PNG or JPG image.")
            return False

        brand = (self.brand_var.get().strip() or self.machine.get("brand", "")).strip()
        model = (self.model_var.get().strip() or self.machine.get("model", "")).strip()
        safe = f"{_sanitize_filename(brand)}_{_sanitize_filename(model)}.png"
        dest = os.path.join(self.icons_folder, safe)
        try:
            img = Image.open(src_path).convert("RGBA")
            img.save(dest, format="PNG")
            # update preview widget
            imgtk = ImageTk.PhotoImage(img.resize((60, 60)))
            self.icon_preview_lbl.config(image=imgtk)
            self.icon_preview_lbl.image = imgtk
            self.machine_icon_filename = safe
            return True
        except Exception as e:
            messagebox.showerror("Icon Save Error", str(e))
            return False

    # -------------------------
    # UI Build (dark minimal)
    # -------------------------
    def _build_ui(self):
        bg = self._BG_FRAME
        card = self._BG_CARD
        fg = self._FG_TEXT

        # Root container frame
        root_fr = ttk.Frame(self.win, style="MP.TFrame")
        root_fr.pack(fill="both", expand=True, padx=8, pady=8)

        # Header: icon preview + title
        header = ttk.Frame(root_fr, style="MP.TFrame")
        header.pack(fill="x", pady=(0, 8))

        self.icon_preview_lbl = tk.Label(header, bg=bg)
        icon_img = self.icon_cache.get("machine") or self.icon_cache.get("default")
        if icon_img:
            self.icon_preview_lbl.config(image=icon_img)
            self.icon_preview_lbl.image = icon_img
        else:
            self.icon_preview_lbl.config(text="No icon", fg=fg, bg=bg, font=("Arial", 10))
        self.icon_preview_lbl.pack(side="left", padx=(4, 12))

        title_block = ttk.Frame(header, style="MP.TFrame")
        title_block.pack(side="left", fill="x", expand=True)
        self.title_lbl = ttk.Label(title_block, text=self.machine.get("name", "Unnamed"),
                                   style="Title.TLabel")
        self.title_lbl.pack(anchor="w")
        subtitle = f"Brand: {self.machine.get('brand','')}   Model: {self.machine.get('model','')}"
        self.sub_lbl = ttk.Label(title_block, text=subtitle, style="Small.TLabel")
        self.sub_lbl.pack(anchor="w", pady=(2, 0))

        # Icon settings card
        icon_card = ttk.LabelFrame(root_fr, text="Printer Icon", style="Card.TLabelframe")
        icon_card.pack(fill="x", pady=(4, 8))
        icon_card_inner = ttk.Frame(icon_card, style="Card.TFrame")
        icon_card_inner.pack(fill="x", padx=8, pady=8)

        ttk.Label(icon_card_inner, text="Current Icon:", style="Dark.TLabel").pack(side="left", padx=(0, 6))
        ttk.Button(icon_card_inner, text="Browse…", style="Accent.TButton", command=self._on_browse_icon) \
            .pack(side="right")
        ttk.Button(icon_card_inner, text="Use Default", style="Accent.TButton", command=self._on_use_default_icon) \
            .pack(side="right", padx=(0, 6))

        # Machine settings card
        settings_card = ttk.LabelFrame(root_fr, text="Machine Settings", style="Card.TLabelframe")
        settings_card.pack(fill="x", pady=(4, 8))
        settings_inner = ttk.Frame(settings_card, style="Card.TFrame")
        settings_inner.pack(fill="x", padx=8, pady=8)

        lbl_cfg = {"style": "Dark.TLabel"}
        entry_cfg = {"style": "Dark.TEntry"}

        ttk.Label(settings_inner, text="Name:", **lbl_cfg).grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.name_var = tk.StringVar()
        ttk.Entry(settings_inner, textvariable=self.name_var, **entry_cfg).grid(row=0, column=1, sticky="w", padx=6,
                                                                                pady=4)

        ttk.Label(settings_inner, text="Brand:", **lbl_cfg).grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.brand_var = tk.StringVar()
        ttk.Entry(settings_inner, textvariable=self.brand_var, **entry_cfg).grid(row=1, column=1, sticky="w", padx=6,
                                                                                 pady=4)

        ttk.Label(settings_inner, text="Model:", **lbl_cfg).grid(row=2, column=0, sticky="w", padx=6, pady=4)
        self.model_var = tk.StringVar()
        ttk.Entry(settings_inner, textvariable=self.model_var, **entry_cfg).grid(row=2, column=1, sticky="w", padx=6,
                                                                                 pady=4)

        ttk.Label(settings_inner, text="IP:", **lbl_cfg).grid(row=3, column=0, sticky="w", padx=6, pady=4)
        self.ip_var = tk.StringVar()
        ttk.Entry(settings_inner, textvariable=self.ip_var, **entry_cfg).grid(row=3, column=1, sticky="w", padx=6,
                                                                              pady=4)

        # Counters card
        stats_card = ttk.LabelFrame(root_fr, text="Counters & Actions", style="Card.TLabelframe")
        stats_card.pack(fill="x", pady=(4, 8))
        stats_inner = ttk.Frame(stats_card, style="Card.TFrame")
        stats_inner.pack(fill="x", padx=8, pady=8)

        self.lbl_status = ttk.Label(stats_inner, text="Status: Checking...", style="Small.TLabel")
        self.lbl_status.pack(anchor="w", pady=(0, 4))

        self.lbl_current = ttk.Label(stats_inner, text="Current Counter: loading...", style="Dark.TLabel")
        self.lbl_current.pack(anchor="w", pady=(0, 4))

        self.lbl_yesterday = ttk.Label(stats_inner, text="Yesterday: loading...", style="Dark.TLabel")
        self.lbl_yesterday.pack(anchor="w", pady=(0, 4))

        self.lbl_today = ttk.Label(stats_inner, text="Today's Clicks: loading...", style="Dark.TLabel",
                                   font=("Arial", 11, "bold"))
        self.lbl_today.pack(anchor="w", pady=(2, 4))

        # Last refreshed time label
        self.lbl_last_refresh = ttk.Label(stats_inner, text="Last refresh: --", style="Small.TLabel")
        self.lbl_last_refresh.pack(anchor="w", pady=(0, 6))

        btn_row = ttk.Frame(stats_inner, style="Card.TFrame")
        btn_row.pack(fill="x", pady=(6, 2))

        ttk.Button(btn_row, text="Save Changes", style="Accent.TButton", command=self._on_save).pack(side="left")
        ttk.Button(btn_row, text="SNMP Refresh", style="Accent.TButton", command=self._on_snmp_refresh).pack(
            side="left", padx=6)
        ttk.Button(btn_row, text="Open Web UI", style="Accent.TButton", command=self._on_open_webui).pack(
            side="left", padx=6)
        ttk.Button(btn_row, text="Ping Test", style="Accent.TButton", command=self._on_ping_test).pack(side="left",
                                                                                                       padx=6)

        # Notebook with tabs below counters (full width)
        self.nb = ttk.Notebook(root_fr)
        self.nb.pack(fill="both", expand=True, pady=(6, 0))

        self.line_frame = ttk.Frame(self.nb, style="MP.TFrame")
        self.nb.add(self.line_frame, text="Line Graph")

        self.bar_frame = ttk.Frame(self.nb, style="MP.TFrame")
        self.nb.add(self.bar_frame, text="Bar Graph")

        # Graph range toggle (7 / 30 days)
        toggle_frame = ttk.Frame(root_fr, style="MP.TFrame")
        toggle_frame.pack(fill="x", pady=(4, 0))
        ttk.Label(toggle_frame, text="Graph Range:", style="Small.TLabel").pack(side="left", padx=(2, 4))
        ttk.Button(toggle_frame, text="Last 7 days", style="Accent.TButton",
                   command=lambda: self._rebuild_graphs_for_days(7)).pack(side="left", padx=4)
        ttk.Button(toggle_frame, text="Last 30 days", style="Accent.TButton",
                   command=lambda: self._rebuild_graphs_for_days(30)).pack(side="left", padx=4)

        # Graph placeholders
        self.line_canvas = None
        self.bar_canvas = None
        self._init_graph_placeholders()

    # -------------------------
    # Populate values
    # -------------------------
    def _populate_fields(self):
        m = self.machine
        self.name_var.set(m.get("name", ""))
        self.brand_var.set(m.get("brand", ""))
        self.model_var.set(m.get("model", ""))
        self.ip_var.set(m.get("ip", ""))
        self.title_lbl.config(text=self.name_var.get() or "Unnamed")
        self.sub_lbl.config(text=f"Brand: {self.brand_var.get()}   Model: {self.model_var.get()}")

    # -------------------------
    # Browse icon handler
    # -------------------------
    def _on_browse_icon(self):
        path = filedialog.askopenfilename(
            title="Choose icon image",
            filetypes=[("Images", "*.png;*.jpg;*.jpeg"), ("PNG", "*.png"), ("JPG", "*.jpg;*.jpeg")]
        )
        if not path:
            return
        ok = self._save_icon_to_brand_model(path)
        if ok:
            # instant dashboard update
            if self.on_update:
                try:
                    self.on_update(self.mid)
                except Exception:
                    pass

    def _on_use_default_icon(self):
        # remove custom file if exists for brand_model and show default
        brand = (self.brand_var.get() or self.machine.get("brand", "")).strip()
        model = (self.model_var.get() or self.machine.get("model", "")).strip()
        safe = f"{_sanitize_filename(brand)}_{_sanitize_filename(model)}.png"
        target = os.path.join(self.icons_folder, safe)
        try:
            if os.path.exists(target):
                os.remove(target)
            # reload preview
            default_path = os.path.join(self.icons_folder, "default.png")
            if os.path.exists(default_path):
                img = Image.open(default_path).resize((60, 60))
                imgtk = ImageTk.PhotoImage(img)
                self.icon_preview_lbl.config(image=imgtk)
                self.icon_preview_lbl.image = imgtk
            if self.on_update:
                try:
                    self.on_update(self.mid)
                except Exception:
                    pass
        except Exception as e:
            messagebox.showerror("Icon Error", str(e))

    # -------------------------
    # Save machine changes
    # -------------------------
    def _on_save(self):
        try:
            # preserve is_color from DB (not editable here)
            original_is_color = self.machine.get("is_color", 0)

            update_machine(
                self.mid,
                self.name_var.get().strip(),   # name
                self.ip_var.get().strip(),     # ip  (matches DB order)
                self.brand_var.get().strip(),  # brand
                self.model_var.get().strip(),  # model
                original_is_color              # is_color preserved
            )
            # update local copy and UI
            self.machine = self._get_machine_record()
            self.title_lbl.config(text=self.name_var.get() or "Unnamed")
            self.sub_lbl.config(text=f"Brand: {self.brand_var.get()}   Model: {self.model_var.get()}")
            messagebox.showinfo("Saved", "Machine settings updated.")

            # notify dashboard to refresh this tile
            if self.on_update:
                try:
                    self.on_update(self.mid)
                except Exception:
                    pass

            # refresh counters and graphs
            self._update_counter_info_async()
        except Exception as e:
            messagebox.showerror("Save Error", str(e))

    # -------------------------
    # SNMP refresh (background)
    # -------------------------
    def _on_snmp_refresh(self):
        threading.Thread(target=self._snmp_refresh_worker, daemon=True).start()

    def _snmp_refresh_worker(self):
        ip = self.ip_var.get().strip() or self.machine.get("ip", "")
        try:
            val = probe_and_read_counter(ip)
            if val is not None:
                insert_counter(self.mid, int(val))
                time.sleep(0.12)
            else:
                self.win.after(1, lambda: messagebox.showwarning("SNMP", "SNMP returned no value."))
        except Exception as e:
            self.win.after(1, lambda: messagebox.showerror("SNMP Error", str(e)))
        # update UI after SNMP finishes
        self.win.after(50, self._update_counter_info_async)

    # -------------------------
    # Open Web UI
    # -------------------------
    def _on_open_webui(self):
        ip = self.ip_var.get().strip() or self.machine.get("ip", "")
        if not ip:
            messagebox.showerror("Open Web UI", "No IP address available.")
            return
        try:
            webbrowser.open(f"http://{ip}", new=2)
        except Exception as e:
            messagebox.showerror("Open Web UI", str(e))

    # -------------------------
    # Ping test
    # -------------------------
    def _on_ping_test(self):
        ip = self.ip_var.get().strip() or self.machine.get("ip", "")
        if not ip:
            messagebox.showerror("Ping Test", "No IP address available.")
            return

        def run_ping():
            try:
                start = time.time()
                ok = ping_ip(ip)
                elapsed = int((time.time() - start) * 1000)
                text = f"🟢 Online • {elapsed} ms" if ok else "🔴 Offline"
                color = self._ONLINE if ok else self._OFFLINE
                self.win.after(1, lambda: self.lbl_status.config(text=text, foreground=color))
            except:
                self.win.after(1,
                               lambda: self.lbl_status.config(text="🔴 Offline", foreground=self._OFFLINE))

        threading.Thread(target=run_ping, daemon=True).start()

    # -------------------------
    # Counters and graphs
    # -------------------------
    def _update_counter_info_async(self):
        threading.Thread(target=self._update_counter_info_worker, daemon=True).start()

    def _update_counter_info_worker(self):
        latest = fetch_latest_counter(self.mid)
        current = latest["total_impressions"] if latest else 0
        rows = fetch_daily_rows(self.mid, days=30)
        self._all_rows = rows[:] if rows else []
        self.win.after(1, lambda: self._apply_counter_info(current, rows))

    def _apply_counter_info(self, current_ctr, rows):
        # rows: list of (date, clicks)
        if rows and len(rows) >= 2:
            yesterday_clicks = rows[-2][1]
            today_clicks = rows[-1][1]
            yesterday_total = current_ctr - today_clicks
        elif rows and len(rows) == 1:
            yesterday_clicks = 0
            today_clicks = rows[-1][1]
            yesterday_total = current_ctr - today_clicks
        else:
            yesterday_clicks = 0
            today_clicks = 0
            yesterday_total = None

        # Labels
        self.lbl_current.config(text=f"Current Counter: {current_ctr:,}")
        if yesterday_total is None:
            self.lbl_yesterday.config(text="Yesterday: No data")
        else:
            self.lbl_yesterday.config(text=f"Yesterday: {yesterday_total:,} ({yesterday_clicks} clicks)")
        self.lbl_today.config(text=f"Today's Clicks: {today_clicks}")

        # Last refreshed timestamp
        stamp = time.strftime("%d %b %Y, %I:%M:%S %p")
        self.lbl_last_refresh.config(text=f"Last refresh: {stamp}")

        # quick ping to update live status
        def _ping_then_set():
            try:
                ok = ping_ip(self.ip_var.get().strip() or self.machine.get("ip", ""))
                text = "🟢 Online" if ok else "🔴 Offline"
                color = self._ONLINE if ok else self._OFFLINE
                self.win.after(1, lambda: self.lbl_status.config(text=text, foreground=color))
            except:
                self.win.after(1,
                               lambda: self.lbl_status.config(text="🔴 Offline", foreground=self._OFFLINE))

        threading.Thread(target=_ping_then_set, daemon=True).start()

        # build graphs (default 30 days)
        self._build_graphs(rows, days=30)

    # -------------------------
    # Graphing utils (matplotlib)
    # -------------------------
    def _init_graph_placeholders(self):
        fig = Figure(figsize=(7, 3), dpi=100)
        ax = fig.add_subplot(111)
        ax.set_title("No data")
        self.line_canvas = FigureCanvasTkAgg(fig, master=self.line_frame)
        self.line_canvas.get_tk_widget().pack(fill="both", expand=True)

        fig2 = Figure(figsize=(7, 3), dpi=100)
        ax2 = fig2.add_subplot(111)
        ax2.set_title("No data")
        self.bar_canvas = FigureCanvasTkAgg(fig2, master=self.bar_frame)
        self.bar_canvas.get_tk_widget().pack(fill="both", expand=True)

    def _build_graphs(self, rows, days=30):
        if not rows:
            self._clear_line_plot("No data")
            self._clear_bar_plot("No data")
            return

        data = rows[-days:] if days and len(rows) > days else rows
        dates = [r[0] for r in data]
        clicks = [r[1] for r in data]

        # Line
        try:
            fig = Figure(figsize=(7, 3), dpi=100)
            ax = fig.add_subplot(111)
            ax.plot(dates, clicks, marker="o", linewidth=1.5)
            ax.set_title("Daily Clicks (Line)")
            ax.set_ylabel("Clicks")
            ax.set_xlabel("Date")
            ax.grid(True, linestyle=":", linewidth=0.5)
            fig.autofmt_xdate(rotation=45)

            self.line_canvas.get_tk_widget().destroy()
            self.line_canvas = FigureCanvasTkAgg(fig, master=self.line_frame)
            self.line_canvas.get_tk_widget().pack(fill="both", expand=True)
            self.line_canvas.draw()
        except Exception:
            self._clear_line_plot("Error")

        # Bar
        try:
            fig2 = Figure(figsize=(7, 3), dpi=100)
            ax2 = fig2.add_subplot(111)
            ax2.bar(dates, clicks)
            ax2.set_title("Daily Clicks (Bar)")
            ax2.set_ylabel("Clicks")
            ax2.set_xlabel("Date")
            ax2.grid(axis="y", linestyle=":", linewidth=0.5)
            fig2.autofmt_xdate(rotation=45)

            self.bar_canvas.get_tk_widget().destroy()
            self.bar_canvas = FigureCanvasTkAgg(fig2, master=self.bar_frame)
            self.bar_canvas.get_tk_widget().pack(fill="both", expand=True)
            self.bar_canvas.draw()
        except Exception:
            self._clear_bar_plot("Error")

    def _rebuild_graphs_for_days(self, days):
        if not self._all_rows:
            return
        self._build_graphs(self._all_rows, days=days)

    def _clear_line_plot(self, msg="No data"):
        fig = Figure(figsize=(7, 3), dpi=100)
        ax = fig.add_subplot(111)
        ax.set_title(msg)
        try:
            self.line_canvas.get_tk_widget().destroy()
        except Exception:
            pass
        self.line_canvas = FigureCanvasTkAgg(fig, master=self.line_frame)
        self.line_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.line_canvas.draw()

    def _clear_bar_plot(self, msg="No data"):
        fig = Figure(figsize=(7, 3), dpi=100)
        ax = fig.add_subplot(111)
        ax.set_title(msg)
        try:
            self.bar_canvas.get_tk_widget().destroy()
        except Exception:
            pass
        self.bar_canvas = FigureCanvasTkAgg(fig, master=self.bar_frame)
        self.bar_canvas.get_tk_widget().pack(fill="both", expand=True)
        self.bar_canvas.draw()

    # -------------------------
    # Center window helper
    # -------------------------
    def _center(self):
        self.win.update_idletasks()
        w = self.win.winfo_width()
        h = self.win.winfo_height()
        pw = self.root.winfo_width()
        ph = self.root.winfo_height()
        px = self.root.winfo_x()
        py = self.root.winfo_y()
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # -------------------------
    # Cleanup on close
    # -------------------------
    def close(self):
        try:
            self.win.destroy()
        except:
            pass
