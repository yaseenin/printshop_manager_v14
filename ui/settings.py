# ui/settings.py
# Settings Tab for Print Shop Manager
# - Theme, Auto-refresh, Auto-backup, Startup, Logging, UI behavior
# - Persists to settings.json in project root
# - Registry autostart (HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run) on Windows

import os
import sys
import json
import shutil
import platform
import subprocess
from datetime import datetime
import threading

import tkinter as tk
from tkinter import ttk, messagebox, filedialog

import sqlite3
from datetime import timedelta
from core.db import POOL

try:
    import winreg
except Exception:
    winreg = None

from ui.theme import apply_theme, CURRENT_THEME

# Settings file path (project root)
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "settings.json")
BACKUP_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "backup")
LOGS_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

# Default settings
DEFAULT_SETTINGS = {
    "theme": "dark",                 # 'dark' or 'light'
    "accent": "blue",                # blue, green, purple, red
    "dashboard_refresh": 10,         # seconds
    "machines_refresh": 30,          # seconds
    "auto_backup_enabled": False,
    "auto_backup_days": 7,
    "start_minimized_to_tray": False,
    "auto_start_registry": False,
    "auto_hide_splash_if_no_printers": True,
    "disable_sounds": False,
    "enable_logging": True,
}


def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as fh:
                return {**DEFAULT_SETTINGS, **json.load(fh)}
    except Exception:
        pass
    return dict(DEFAULT_SETTINGS)


def save_settings(s):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=2)
    except Exception as e:
        print("[SETTINGS] Save failed:", e)


def ensure_folders():
    try:
        os.makedirs(BACKUP_FOLDER, exist_ok=True)
        os.makedirs(LOGS_FOLDER, exist_ok=True)
    except Exception:
        pass


def find_db_files():
    """
    Heuristic search for .db files inside the project root (to back up).
    Avoids system folders.
    """
    root = os.path.dirname(os.path.dirname(__file__))
    dbs = []
    for dirpath, dirnames, filenames in os.walk(root):
        # skip virtualenv/system folders
        if any(part.startswith(".") or part in ("venv", "__pycache__") for part in dirpath.split(os.sep)):
            continue
        for fn in filenames:
            if fn.endswith(".db") or fn.endswith(".sqlite") or fn.endswith(".sqlite3"):
                dbs.append(os.path.join(dirpath, fn))
    return dbs


def perform_backup():
    ensure_folders()
    dbs = find_db_files()
    if not dbs:
        raise RuntimeError("No database files found to backup.")
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(BACKUP_FOLDER, f"backup_{now}")
    os.makedirs(dest_dir, exist_ok=True)
    for p in dbs:
        try:
            shutil.copy2(p, dest_dir)
        except Exception as e:
            print("[BACKUP] Failed to copy:", p, e)
    return dest_dir


def open_logs_folder():
    ensure_folders()
    if platform.system() == "Windows":
        os.startfile(LOGS_FOLDER)
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", LOGS_FOLDER])
    else:
        subprocess.Popen(["xdg-open", LOGS_FOLDER])


def clear_logs():
    ensure_folders()
    for fname in os.listdir(LOGS_FOLDER):
        try:
            path = os.path.join(LOGS_FOLDER, fname)
            if os.path.isfile(path):
                os.remove(path)
        except Exception:
            pass


def set_registry_autostart(enabled: bool, name: str = "PrintShopManager"):
    """
    Writes HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run entry so app appears
    in Task Manager -> Startup.
    Uses sys.executable + script path on Python; for frozen exe adjust accordingly.
    """
    if platform.system() != "Windows" or winreg is None:
        raise RuntimeError("Registry autostart is only available on Windows.")

    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Run",
                             0, winreg.KEY_ALL_ACCESS)
    except Exception:
        # create key if it doesn't exist
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run")

    try:
        if enabled:
            # Build command: prefer using the actual script path if possible
            try:
                script_path = os.path.abspath(sys.argv[0])
            except Exception:
                script_path = os.path.abspath(__file__)
            # If running as frozen exe, sys.executable points to the exe
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}"'
            else:
                # Use python exe + script path to run same script
                cmd = f'"{sys.executable}" "{script_path}"'
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(key, name)
            except FileNotFoundError:
                pass
    finally:
        try:
            key.Close()
        except Exception:
            pass


def rebuild_analytics_db():
    """
    Reconstructs the analytics_30min table from the raw machine_counters log.
    Useful if analytics data is missing or incorrect.
    """
    conn = POOL.get()
    try:
        c = conn.cursor()
        
        # 1. Wipe existing analytics
        c.execute("DELETE FROM analytics_30min")
        c.execute("DELETE FROM analytics_baseline")
        
        # 2. Get all machines
        c.execute("SELECT id FROM machines")
        mids = [r[0] for r in c.fetchall()]
        
        for mid in mids:
            # 3. Fetch raw history
            c.execute("SELECT timestamp, total_impressions FROM machine_counters WHERE machine_id=? ORDER BY timestamp ASC", (mid,))
            rows = c.fetchall()
            if not rows:
                continue
            
            prev_ts = None
            prev_val = 0
            
            for r in rows:
                try:
                    curr_ts = datetime.strptime(r[0], "%Y-%m-%d %H:%M:%S")
                    curr_val = int(r[1])
                except Exception:
                    continue
                
                if prev_ts is None:
                    prev_ts = curr_ts
                    prev_val = curr_val
                    continue
                
                delta = curr_val - prev_val
                duration = (curr_ts - prev_ts).total_seconds()
                
                # Skip resets, zero duration, or impossible rates (e.g. > 5 pages/sec)
                if delta < 0 or duration <= 0:
                    prev_ts = curr_ts
                    prev_val = curr_val
                    continue
                
                rate = delta / duration
                if rate > 5.0:
                    # Likely a data spike/error
                    prev_ts = curr_ts
                    prev_val = curr_val
                    continue
                
                # Distribute into 30m slots
                cursor = prev_ts
                while cursor < curr_ts:
                    minute = 0 if cursor.minute < 30 else 30
                    slot_start = cursor.replace(minute=minute, second=0, microsecond=0)
                    slot_end = slot_start + timedelta(minutes=30)
                    
                    seg_end = min(curr_ts, slot_end)
                    seg_duration = (seg_end - cursor).total_seconds()
                    
                    if seg_duration > 0:
                        clicks = int(rate * seg_duration)
                        if clicks > 0:
                            ts_str = slot_start.strftime("%Y-%m-%d %H:%M:%S")
                            try:
                                c.execute("INSERT INTO analytics_30min (machine_id, ts, clicks) VALUES (?,?,?)", (mid, ts_str, clicks))
                            except sqlite3.IntegrityError:
                                c.execute("UPDATE analytics_30min SET clicks = clicks + ? WHERE machine_id=? AND ts=?", (clicks, mid, ts_str))
                    
                    cursor = seg_end
                
                prev_ts = curr_ts
                prev_val = curr_val
                
            # Restore baseline for this machine so recorder picks up smoothly
            if prev_ts is not None:
                c.execute(
                    "INSERT OR REPLACE INTO analytics_baseline (machine_id, last_total, last_ts) VALUES (?, ?, ?)",
                    (mid, prev_val, prev_ts.strftime("%Y-%m-%d %H:%M:%S"))
                )

        conn.commit()
    finally:
        POOL.put(conn)


class SettingsTab:
    def __init__(self, parent, root=None, style=None):
        self.parent = parent
        self.root = root
        self.style = style or ttk.Style()

        self.colors = apply_theme(parent, self.style, CURRENT_THEME) or {}
        self.bg = self.colors.get("BG_FRAME", "#020617")
        self.fg = self.colors.get("FG_TEXT", "#e5e7eb")

        ensure_folders()
        self.settings = load_settings()

        self.frame = ttk.Frame(parent)
        self._build_ui()

    def get_frame(self):
        return self.frame

    def _build_ui(self):
        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(8, 6), padx=10)

        ttk.Label(top, text="Settings", font=("Arial", 16, "bold"), foreground=self.fg).pack(side="left")

        body = ttk.Frame(self.frame)
        body.pack(fill="both", expand=True, padx=10, pady=6)

        # Two column layout: left = controls, right = helper/info
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(body, width=280)
        right.pack(side="right", fill="y", padx=(12, 0))

        # -----------------------
        # Theme Settings
        # -----------------------
        sec_theme = ttk.LabelFrame(left, text="Theme Settings")
        sec_theme.pack(fill="x", pady=(0, 8))

        self.theme_var = tk.StringVar(value=self.settings.get("theme", "dark"))
        ttk.Radiobutton(sec_theme, text="Dark", variable=self.theme_var, value="dark", command=self._on_theme_change).pack(side="left", padx=6, pady=6)
        ttk.Radiobutton(sec_theme, text="Light", variable=self.theme_var, value="light", command=self._on_theme_change).pack(side="left", padx=6, pady=6)

        ttk.Label(sec_theme, text="Accent:").pack(side="left", padx=(12, 6))
        self.accent_cb = ttk.Combobox(sec_theme, values=["blue", "green", "purple", "red"], state="readonly", width=10)
        self.accent_cb.set(self.settings.get("accent", "blue"))
        self.accent_cb.pack(side="left", padx=4)
        self.accent_cb.bind("<<ComboboxSelected>>", lambda e: self._on_accent_change())

        # -----------------------
        # Auto-refresh settings
        # -----------------------
        sec_refresh = ttk.LabelFrame(left, text="Auto-refresh Settings")
        sec_refresh.pack(fill="x", pady=(0, 8))

        ttk.Label(sec_refresh, text="Dashboard (seconds):").grid(row=0, column=0, sticky="w", padx=6, pady=6)
        self.db_refresh_var = tk.IntVar(value=self.settings.get("dashboard_refresh", 10))
        db_spin = ttk.Spinbox(sec_refresh, from_=5, to=600, increment=5, textvariable=self.db_refresh_var, width=8)
        db_spin.grid(row=0, column=1, padx=6, pady=6)

        ttk.Label(sec_refresh, text="Machines (seconds):").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.mach_refresh_var = tk.IntVar(value=self.settings.get("machines_refresh", 30))
        mach_spin = ttk.Spinbox(sec_refresh, from_=10, to=600, increment=5, textvariable=self.mach_refresh_var, width=8)
        mach_spin.grid(row=1, column=1, padx=6, pady=6)

        # -----------------------
        # Auto-backup settings
        # -----------------------
        sec_backup = ttk.LabelFrame(left, text="Auto-backup")
        sec_backup.pack(fill="x", pady=(0, 8))

        self.auto_backup_var = tk.BooleanVar(value=self.settings.get("auto_backup_enabled", False))
        ttk.Checkbutton(sec_backup, text="Enable auto-backup", variable=self.auto_backup_var).grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=6)

        ttk.Label(sec_backup, text="Backup interval (days):").grid(row=1, column=0, sticky="w", padx=6, pady=6)
        self.backup_days_var = tk.IntVar(value=self.settings.get("auto_backup_days", 7))
        ttk.Spinbox(sec_backup, from_=1, to=365, textvariable=self.backup_days_var, width=6).grid(row=1, column=1, padx=6, pady=6)

        ttk.Button(sec_backup, text="Backup Now", command=self._on_backup_now).grid(row=2, column=0, columnspan=2, pady=(6, 10))

        # -----------------------
        # Startup & UI Behavior
        # -----------------------
        sec_start = ttk.LabelFrame(left, text="Startup & UI Behavior")
        sec_start.pack(fill="x", pady=(0, 8))

        self.start_minimized_var = tk.BooleanVar(value=self.settings.get("start_minimized_to_tray", False))
        ttk.Checkbutton(sec_start, text="Start minimized to tray", variable=self.start_minimized_var).pack(anchor="w", padx=6, pady=4)

        self.autostart_var = tk.BooleanVar(value=self.settings.get("auto_start_registry", False))
        ttk.Checkbutton(sec_start, text="Auto-start at boot (visible in Task Manager)", variable=self.autostart_var).pack(anchor="w", padx=6, pady=4)

        self.auto_hide_splash_var = tk.BooleanVar(value=self.settings.get("auto_hide_splash_if_no_printers", True))
        ttk.Checkbutton(sec_start, text="Auto-hide splash when no printers", variable=self.auto_hide_splash_var).pack(anchor="w", padx=6, pady=4)

        self.disable_sounds_var = tk.BooleanVar(value=self.settings.get("disable_sounds", False))
        ttk.Checkbutton(sec_start, text="Disable sounds / notifications", variable=self.disable_sounds_var).pack(anchor="w", padx=6, pady=4)

        # -----------------------
        # Logging
        # -----------------------
        sec_logs = ttk.LabelFrame(left, text="Data & Maintenance")
        sec_logs.pack(fill="x", pady=(0, 8))

        self.enable_logging_var = tk.BooleanVar(value=self.settings.get("enable_logging", True))
        ttk.Checkbutton(sec_logs, text="Enable application log", variable=self.enable_logging_var).pack(anchor="w", padx=6, pady=4)

        row = ttk.Frame(sec_logs)
        row.pack(fill="x", padx=6, pady=6)
        ttk.Button(row, text="Open Logs", command=self._on_open_logs).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Clear Logs", command=self._on_clear_logs).pack(side="left", padx=(0, 6))
        ttk.Button(row, text="Rebuild Analytics", command=self._on_rebuild_analytics).pack(side="left")

        # -----------------------
        # Save / Restore
        # -----------------------
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="Save Settings", command=self._on_save).pack(side="right", padx=6)
        ttk.Button(btns, text="Reset to Defaults", command=self._on_reset).pack(side="right")

        # -----------------------
        # Right column - info & paths
        # -----------------------
        ttk.Label(right, text="Folders & Paths", font=("Arial", 11, "bold")).pack(anchor="w", pady=(4, 8))
        ttk.Label(right, text=f"Settings: {os.path.basename(SETTINGS_FILE)}", wraplength=260).pack(anchor="w", pady=(0, 4))
        ttk.Label(right, text=f"Backups folder: {os.path.basename(BACKUP_FOLDER)}", wraplength=260).pack(anchor="w", pady=(0, 4))
        ttk.Label(right, text=f"Logs folder: {os.path.basename(LOGS_FOLDER)}", wraplength=260).pack(anchor="w", pady=(0, 4))

        ttk.Separator(right).pack(fill="x", pady=(8, 8))

        ttk.Label(right, text="Quick actions", font=("Arial", 10, "bold")).pack(anchor="w", pady=(4, 6))
        ttk.Button(right, text="Open Backup Folder", command=lambda: self._open_folder(BACKUP_FOLDER)).pack(fill="x", pady=(0, 6))
        ttk.Button(right, text="Open Project Folder", command=lambda: self._open_folder(os.path.dirname(os.path.dirname(__file__)))).pack(fill="x")

    # -----------------------
    # Handlers
    # -----------------------
    def _on_theme_change(self):
        # apply to running UI (best-effort)
        t = self.theme_var.get()
        a = self.accent_cb.get() or "blue"
        s = load_settings()
        s["theme"] = t
        s["accent"] = a
        save_settings(s)
        try:
            apply_theme(self.frame, self.style, CURRENT_THEME)
        except Exception:
            pass
        messagebox.showinfo("Theme", "Theme preference saved. Restart may be required for all changes to apply.")

    def _on_accent_change(self):
        self._on_theme_change()

    def _on_backup_now(self):
        def worker():
            try:
                dest = perform_backup()
                messagebox.showinfo("Backup", f"Backup completed:\n{dest}")
            except Exception as e:
                messagebox.showerror("Backup Failed", str(e))
        threading.Thread(target=worker, daemon=True).start()

    def _open_folder(self, path):
        if not os.path.exists(path):
            os.makedirs(path, exist_ok=True)
        try:
            if platform.system() == "Windows":
                os.startfile(path)
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("Open Folder", str(e))

    def _on_open_logs(self):
        try:
            open_logs_folder()
        except Exception as e:
            messagebox.showerror("Open Logs", str(e))

    def _on_clear_logs(self):
        if not messagebox.askyesno("Clear Logs", "Delete all log files?"):
            return
        try:
            clear_logs()
            messagebox.showinfo("Clear Logs", "Logs cleared.")
        except Exception as e:
            messagebox.showerror("Clear Logs", str(e))

    def _on_rebuild_analytics(self):
        if not messagebox.askyesno("Rebuild Analytics", 
            "This will wipe the current Analytics history and reconstruct it from the raw counter logs.\n\n"
            "This fixes 'wrong previous data' but may take a few seconds.\n\nContinue?"):
            return
            
        def worker():
            try:
                rebuild_analytics_db()
                messagebox.showinfo("Success", "Analytics data rebuilt successfully.")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to rebuild: {e}")
        
        threading.Thread(target=worker, daemon=True).start()

    def _on_save(self):
        # gather settings from UI
        s = load_settings()
        s["theme"] = self.theme_var.get()
        s["accent"] = self.accent_cb.get() or s.get("accent", "blue")
        s["dashboard_refresh"] = int(self.db_refresh_var.get())
        s["machines_refresh"] = int(self.mach_refresh_var.get())
        s["auto_backup_enabled"] = bool(self.auto_backup_var.get())
        s["auto_backup_days"] = int(self.backup_days_var.get())
        s["start_minimized_to_tray"] = bool(self.start_minimized_var.get())
        s["auto_start_registry"] = bool(self.autostart_var.get())
        s["auto_hide_splash_if_no_printers"] = bool(self.auto_hide_splash_var.get())
        s["disable_sounds"] = bool(self.disable_sounds_var.get())
        s["enable_logging"] = bool(self.enable_logging_var.get())

        save_settings(s)

        # apply registry autostart (Windows)
        try:
            if s["auto_start_registry"]:
                set_registry_autostart(True)
            else:
                # remove if exists
                try:
                    set_registry_autostart(False)
                except Exception:
                    pass
        except Exception as e:
            messagebox.showwarning("Auto-start", f"Failed to set auto-start: {e}")

        messagebox.showinfo("Settings", "Settings saved.")

    def _on_reset(self):
        if not messagebox.askyesno("Reset", "Reset settings to defaults?"):
            return
        save_settings(dict(DEFAULT_SETTINGS))
        messagebox.showinfo("Reset", "Settings reset to defaults. Please reload the application.")

# End of file
