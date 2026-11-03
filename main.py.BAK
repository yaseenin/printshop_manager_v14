# main.py — Print Shop Manager v14 (FINAL, FEATURE-SAFE, DASHBOARD-COMPATIBLE)

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import threading
import os

import pystray
from pystray import MenuItem as item

import threading
from api_server import start_api

threading.Thread(target=start_api, daemon=True).start()

from datetime import datetime, timedelta
from importlib import import_module

from core.db import init_db, close_pool
from core.consumables_db import init_consumables_db
from core.models import fetch_machines, fetch_latest_counter, insert_counter

from core.analytics_recorder import start_recorder, stop_recorder
from ui.theme import apply_theme, CURRENT_THEME

# -----------------------------------------------------
# WINDOWS TASKBAR ICON FIX (AppUserModelID)
# -----------------------------------------------------
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "printshop.manager.v14"
    )
except Exception:
    pass
# -----------------------------------------------------
# MIDNIGHT SNAPSHOT SYSTEM (UNCHANGED)
# -----------------------------------------------------
stop_evt = threading.Event()

def midnight_worker():
    while not stop_evt.is_set():
        now = datetime.now()
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=2, microsecond=0
        )
        wait = max(5, (nxt - now).total_seconds())
        stop_evt.wait(wait)

        if stop_evt.is_set():
            break

        try:
            for m in fetch_machines(active_only=True):
                latest = fetch_latest_counter(m["id"])
                if latest:
                    insert_counter(m["id"], latest["total_impressions"])
            print("[MIDNIGHT] Snapshot saved.")
        except Exception as e:
            print("[MIDNIGHT][ERROR]", e)

threading.Thread(target=midnight_worker, daemon=True).start()

# -----------------------------------------------------
# SPLASH SCREEN (COLOR-KEY TRANSPARENT)
# -----------------------------------------------------
def show_splash(root):
    TRANSPARENT = "#ff00ff"

    splash = tk.Toplevel(root)
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)
    splash.configure(bg=TRANSPARENT)

    try:
        splash.wm_attributes("-transparentcolor", TRANSPARENT)
    except Exception:
        pass

    try:
        img = Image.open("assets/splash.png")
    except Exception:
        img = Image.new("RGB", (420, 220), (255, 0, 255))

    splash_img = ImageTk.PhotoImage(img)

    w, h = img.size
    x = (splash.winfo_screenwidth() - w) // 2
    y = (splash.winfo_screenheight() - h) // 2
    splash.geometry(f"{w}x{h}+{x}+{y}")

    canvas = tk.Canvas(
        splash,
        width=w,
        height=h,
        bg=TRANSPARENT,
        highlightthickness=0,
        bd=0
    )
    canvas.pack()
    canvas.create_image(0, 0, anchor="nw", image=splash_img)

    splash.image = splash_img
    splash.update()
    return splash

# -----------------------------------------------------
# SAFE TAB LOADER (UNCHANGED)
# -----------------------------------------------------
def safe_create(module_name, class_name, parent, root, style):
    mod = import_module(module_name)
    cls = getattr(mod, class_name)

    for args in ((parent, root, style), (parent, root), (parent,)):
        try:
            inst = cls(*args)
            frame = inst.get_frame() if hasattr(inst, "get_frame") else inst.frame
            return inst, frame
        except TypeError:
            continue

    return None, ttk.Frame(parent)

# -----------------------------------------------------
# SYSTEM TRAY (UNCHANGED)
# -----------------------------------------------------
def start_tray(root, dashboard):
    def show_app(icon, item):
        root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))

    def refresh(icon, item):
        if dashboard and hasattr(dashboard, "refresh"):
            root.after(0, dashboard.refresh)

    def quit_app(icon, item):
        icon.stop()
        root.after(0, root.quit)

    try:
        tray_img = Image.open("ui/icons/app_icon.png").convert("RGBA")
    except Exception:
        tray_img = Image.new("RGBA", (64, 64), (59, 130, 246, 255))

    icon = pystray.Icon(
        "Print Shop Manager",
        tray_img,
        "Print Shop Manager",
        menu=(
            item("Show", show_app),
            item("Refresh Printers", refresh),
            item("Quit", quit_app),
        ),
    )

    root._tray_icon = icon
    threading.Thread(target=icon.run, daemon=True).start()

# -----------------------------------------------------
# MAIN
# -----------------------------------------------------
def main():
    # ---- INIT ----
    init_db()
    init_consumables_db()
    start_recorder()
    print("[INIT] Analytics recorder started")

    root = tk.Tk()
    root.title("Print Shop Manager v14")
    root.geometry("1366x768")

    # ✅ TITLEBAR ICON (THIS WAS MISSING)
    try:
        root.iconbitmap("ui/icons/app_icon.ico")  # MUST be .ico on Windows
    except Exception:
        pass

    # ---- SPLASH ----
    splash = show_splash(root)
    root._splash = splash  # Dashboard closes this

    # ---- THEME ----
    style = ttk.Style()
    style.theme_use("clam")
    apply_theme(root, style, CURRENT_THEME)

    # ---- NOTEBOOK ----
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)

    instances = {}

    tabs = {
        "Dashboard": ("ui.dashboard", "DashboardTab"),
        "Machines": ("ui.machines", "MachinesTab"),
        "Reports": ("ui.reports", "ReportsTab"),
        "Maintenance": ("ui.maintenance", "MaintenanceTab"),
        "Settings": ("ui.settings", "SettingsTab"),
    }

    for title, (mod, cls) in tabs.items():
        inst, frame = safe_create(mod, cls, notebook, root, style)
        notebook.add(frame, text=title)
        instances[cls.lower()] = inst

    inst, frame = safe_create("ui.analytics", "AnalyticsTab", notebook, root, style)
    notebook.add(frame, text="Analytics")
    instances["analyticstab"] = inst

    dashboard = instances.get("dashboardtab")

    if dashboard and hasattr(dashboard, "refresh"):
        root.after(200, dashboard.refresh)

    start_tray(root, dashboard)

    def on_exit():
        stop_evt.set()
        stop_recorder()

        # Shutdown instances to clear Tkinter images before root destruction
        for inst in instances.values():
            if hasattr(inst, "shutdown"):
                try:
                    inst.shutdown()
                except Exception:
                    pass

        try:
            root._tray_icon.stop()
        except Exception:
            pass
        close_pool()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_exit)
    root.mainloop()

if __name__ == "__main__":
    main()
