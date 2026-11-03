# main.py — Print Shop Manager v14 with integrated thread management and logging

import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
import threading
import os
import time
from datetime import datetime, timedelta
from importlib import import_module

import pystray
from pystray import MenuItem as item

import config
from core.logging_setup import setup_logging
from core.thread_manager import BackgroundWorker, THREAD_MANAGER
from core.db import init_db, close_pool, quick_db_check, check_db_integrity_async
from core.consumables_db import init_consumables_db
from core.models import fetch_machines, fetch_latest_counters, insert_counter
from core.analytics_recorder import start_recorder, stop_recorder
from core.ping import ping_ip
from ui.theme import apply_theme, CURRENT_THEME
from api_server import start_api

logger = setup_logging(__name__)

# ============================================================
# WINDOWS TASKBAR ICON FIX
# ============================================================
try:
    import ctypes
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
        "printshop.manager.v14"
    )
except Exception:
    pass


# ============================================================
# BACKGROUND WORKERS
# ============================================================
class MidnightSnapshotWorker(BackgroundWorker):
    """
    Takes daily snapshot of all printer counters at midnight.
    """
    
    def __init__(self):
        super().__init__("MidnightSnapshot", interval_seconds=config.MIDNIGHT_CHECK_INTERVAL)
    
    def run(self):
        """Execute midnight snapshot logic."""
        now = datetime.now()
        nxt = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=2, microsecond=0
        )
        wait_seconds = (nxt - now).total_seconds()
        
        # Only run if we're close to midnight (within 5 seconds)
        if wait_seconds > config.MIDNIGHT_CHECK_INTERVAL:
            return
        
        try:
            machines = fetch_machines(active_only=True)
            mids = [m["id"] for m in machines]
            latests = fetch_latest_counters(mids)
            
            saved = 0
            for m in machines:
                latest = latests.get(m["id"])
                if latest:
                    insert_counter(m["id"], latest["total_impressions"])
                    saved += 1
            
            logger.info(f"Midnight snapshot: saved {saved}/{len(machines)} counters")
        except Exception as e:
            logger.exception(f"Midnight snapshot error: {e}")


# ============================================================
# SPLASH SCREEN
# ============================================================
def show_splash(root) -> tk.Toplevel:
    """Create and display splash screen."""
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


# ============================================================
# TAB LOADER
# ============================================================
def safe_create(module_name: str, class_name: str, parent, root, style):
    """Safely create UI tabs with flexible signatures."""
    mod = import_module(module_name)
    cls = getattr(mod, class_name)

    for args in ((parent, root, style), (parent, root), (parent,)):
        try:
            inst = cls(*args)
            frame = inst.get_frame() if hasattr(inst, "get_frame") else inst.frame
            return inst, frame
        except TypeError:
            continue

    logger.warning(f"Failed to create {class_name} from {module_name}")
    return None, ttk.Frame(parent)


# ============================================================
# SYSTEM TRAY
# ============================================================
def start_tray(root, dashboard):
    """Initialize system tray icon and menu."""
    import webbrowser
    import platform
    import subprocess

    def show_app(icon, item):
        root.after(0, lambda: (root.deiconify(), root.lift(), root.focus_force()))

    def refresh(icon, item):
        if dashboard and hasattr(dashboard, "refresh"):
            root.after(0, dashboard.refresh)

    def open_web(icon, item):
        webbrowser.open(f"http://{config.API_HOST}:{config.API_PORT}")

    def open_logs(icon, item):
        log_path = str(config.LOGS_DIR)
        if not os.path.exists(log_path):
            os.makedirs(log_path, exist_ok=True)
        if platform.system() == "Windows":
            os.startfile(log_path)
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", log_path])
        else:
            subprocess.Popen(["xdg-open", log_path])

    def restart_analytics(icon, item):
        logger.info("Restarting analytics recorder from tray menu")
        stop_recorder()
        start_recorder()

    def quit_app(icon, item):
        logger.info("Quit requested from system tray")
        icon.stop()
        root.after(0, root.quit)

    def stats_loop(icon_ref):
        """Update tray icon statistics periodically."""
        time.sleep(5)
        while not root._shutdown_event.is_set():
            try:
                if dashboard and hasattr(dashboard, "stats"):
                    s = dashboard.stats
                    icon_ref.title = (
                        f"Print Shop Manager\n"
                        f"Online: {s['online']}/{s['total']}\n"
                        f"Today: {s['today']:,} prints"
                    )
                else:
                    icon_ref.title = "Print Shop Manager\nRunning..."
            except Exception as e:
                logger.debug(f"Stats loop error: {e}")
            
            root._shutdown_event.wait(timeout=5)

    try:
        tray_img = Image.open("ui/icons/app_icon.png").convert("RGBA")
    except Exception:
        logger.warning("Failed to load tray icon, using default")
        tray_img = Image.new("RGBA", (64, 64), (59, 130, 246, 255))

    icon = pystray.Icon(
        "Print Shop Manager",
        tray_img,
        "Print Shop Manager",
        menu=pystray.Menu(
            item("Show App", show_app, default=True),
            item("Refresh Printers", refresh),
            pystray.Menu.SEPARATOR,
            item("Open Web API", open_web),
            item("Open Logs Folder", open_logs),
            item("Restart Analytics", restart_analytics),
            pystray.Menu.SEPARATOR,
            item("Quit", quit_app),
        ),
    )

    root._tray_icon = icon
    threading.Thread(target=stats_loop, args=(icon,), daemon=True, name="TrayStatsLoop").start()
    threading.Thread(target=icon.run, daemon=True, name="TrayIconRunner").start()
    logger.info("System tray initialized")


# ============================================================
# INITIALIZATION
# ============================================================
def init_application() -> None:
    """Initialize application components."""
    logger.info("Initializing application...")
    
    try:
        # Database - use fast check for startup (full check runs async)
        init_db()
        db_ok = quick_db_check()
        if db_ok:
            logger.info("Database initialized and verified")
            # Run full integrity check in background
            check_db_integrity_async(
                callback=lambda result: logger.info(f"Background integrity check: {'passed' if result else 'FAILED'}")
            )
        else:
            logger.warning("Database quick check failed, but continuing")
        
        # Consumables
        init_consumables_db()
        logger.info("Consumables database initialized")
        
        # Analytics
        start_recorder()
        logger.info("Analytics recorder started")
        
    except Exception as e:
        logger.exception(f"Initialization error: {e}")
        raise


# ============================================================
# MAIN
# ============================================================
def main():
    """Main application entry point."""
    logger.info("=== Print Shop Manager v14 Starting ===")
    
    # Initialize application
    init_application()
    
    # Start API server in background (MUST be before Tkinter root)
    logger.info(f"Starting API server on {config.API_HOST}:{config.API_PORT}")
    threading.Thread(target=start_api, daemon=True, name="APIServer").start()
    
    # Create root window
    root = tk.Tk()
    root.title("Print Shop Manager v14")
    root.geometry("1570x800")
    
    # Shutdown event for coordinated shutdown
    root._shutdown_event = threading.Event()
    
    # Set window icon
    try:
        root.iconbitmap("ui/icons/app_icon.ico")
    except Exception:
        logger.debug("Failed to set window icon")
    
    # Show splash screen
    if config.ENABLE_SPLASH_SCREEN:
        splash = show_splash(root)
        root._splash = splash
    else:
        root._splash = None
    
    # Apply theme
    style = ttk.Style()
    style.theme_use("clam")
    apply_theme(root, style, CURRENT_THEME)

    # Apply theme
    style = ttk.Style()
    style.theme_use("clam")
    apply_theme(root, style, CURRENT_THEME)
    
    # Create notebook with tabs
    notebook = ttk.Notebook(root)
    notebook.pack(fill="both", expand=True)
    
    instances = {}
    
    tabs = {
        "Dashboard": ("ui.dashboard", "DashboardTab"),
        "Machines": ("ui.machines", "MachinesTab"),
        "Reports": ("ui.reports", "ReportsTab"),
        "Maintenance": ("ui.maintenance", "MaintenanceTab"),
        "Settings": ("ui.settings", "SettingsTab"),
        "Analytics": ("ui.analytics", "AnalyticsTab"),
        "Prints Insights": ("ui.prints_overview", "PrintsOverviewTab"),
    }
    
    for title, (mod, cls) in tabs.items():
        inst, frame = safe_create(mod, cls, notebook, root, style)
        notebook.add(frame, text=title)
        instances[cls.lower()] = inst
    
    dashboard = instances.get("dashboardtab")
    
    # Start background workers
    midnight_worker = MidnightSnapshotWorker()
    THREAD_MANAGER.register(midnight_worker)
    THREAD_MANAGER.start_all()
    logger.info(f"Background workers started: {THREAD_MANAGER.worker_count()} workers")
    
    # Refresh dashboard
    if dashboard and hasattr(dashboard, "refresh"):
        root.after(200, dashboard.refresh)
    
    # Start system tray
    if config.ENABLE_TRAY_ICON:
        start_tray(root, dashboard)
    
    # Cleanup on exit
    def on_exit():
        logger.info("Application shutdown initiated")
        
        # Signal shutdown to all threads
        root._shutdown_event.set()
        
        # Stop background workers
        try:
            THREAD_MANAGER.stop_all(timeout=config.THREAD_SHUTDOWN_TIMEOUT)
        except Exception as e:
            logger.exception(f"Error stopping workers: {e}")
        
        # Stop analytics
        try:
            stop_recorder()
            logger.info("Analytics recorder stopped")
        except Exception as e:
            logger.exception(f"Error stopping recorder: {e}")
        
        # Shutdown UI instances
        for inst in instances.values():
            if inst and hasattr(inst, "shutdown"):
                try:
                    inst.shutdown()
                except Exception as e:
                    logger.debug(f"Error shutting down instance: {e}")
        
        # Stop tray icon
        try:
            if hasattr(root, '_tray_icon'):
                root._tray_icon.stop()
        except Exception as e:
            logger.debug(f"Error stopping tray icon: {e}")
        
        # Close database
        try:
            close_pool()
            logger.info("Database connections closed")
        except Exception as e:
            logger.exception(f"Error closing database: {e}")
        
        root.destroy()
        logger.info("=== Application shutdown complete ===")
    
    root.protocol("WM_DELETE_WINDOW", on_exit)
    
    logger.info("Application UI ready")
    root.mainloop()


if __name__ == "__main__":
    main()
