# config.py — Centralized configuration for Print Shop Manager v14

import os
from pathlib import Path

# ============================================================
# PATHS
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
BACKUP_DIR = BASE_DIR / "backup"
ASSETS_DIR = BASE_DIR / "assets"

LOGS_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

# ============================================================
# DATABASE
# ============================================================
# Prefer existing old DB if present for seamless upgrade
POSSIBLE_OLD_DB = BASE_DIR.parent / "printshop.db"
DEFAULT_V14_DB = BASE_DIR / "printshop_v14.db"
DB_PATH = str(POSSIBLE_OLD_DB if POSSIBLE_OLD_DB.exists() else DEFAULT_V14_DB)

CONSUMABLES_DB_PATH = str(BASE_DIR / "consumables.db")

# Connection pool
DB_POOL_SIZE = 10
DB_POOL_TIMEOUT = 30  # seconds
DB_CONNECTION_CHECK_INTERVAL = 300  # seconds

# ============================================================
# SNMP
# ============================================================
SNMP_COMMUNITY = "public"
SNMP_TIMEOUT = 1  # seconds
SNMP_RETRIES = 1
SNMP_PORT = 161

# SNMP OID Candidates for counter/serial
OID_PAGE_COUNTER = [
    "1.3.6.1.2.1.43.10.2.1.4.1.1",       # Standard Page Counter
    "1.3.6.1.2.1.43.11.1.1.6.1",         # Xerox Total Impressions
    "1.3.6.1.4.1.1602.1.11.1.3.0"        # Canon Device ID Block
]

OID_SERIAL_NUMBER = [
    "1.3.6.1.4.1.1602.1.11.1.3.0",       # Canon Device ID block
    "1.3.6.1.2.1.43.5.1.1.17.1",         # Canon pure serial
    "1.3.6.1.2.1.43.5.1.1.2.1",          # Generic printer serial
    "1.3.6.1.4.1.253.8.53.13.2.1.3.1",   # Xerox serial
]

# ============================================================
# PING
# ============================================================
PING_TIMEOUT_MS = 1000  # milliseconds

# ============================================================
# THREADING & TIMERS
# ============================================================
# Midnight snapshot worker
MIDNIGHT_CHECK_INTERVAL = 5  # seconds (check interval, not actual snapshot time)

# Analytics recorder
ANALYTICS_POLL_INTERVAL = 300  # 5 minutes - reduced CPU impact
ANALYTICS_TABLE = "analytics_30min"
ANALYTICS_BASELINE_TABLE = "analytics_baseline"

# API & Web
API_WORKERS = 20
API_HOST = "0.0.0.0"  # Listen on all network interfaces (localhost + LAN)
API_PORT = 5000

# Dashboard refresh (milliseconds)
DASHBOARD_REFRESH_MS = 2000  # 2 seconds - Default, overridable by settings.json

# ============================================================
# UI
# ============================================================
DASHBOARD_TILE_WIDTH = 350
DASHBOARD_TILE_HEIGHT = 420
DASHBOARD_TILE_COLUMNS = 4
DASHBOARD_ICON_SIZE = (92, 120)

# ============================================================
# LOGGING
# ============================================================
LOG_LEVEL = "INFO"  # DEBUG, INFO, WARNING, ERROR, CRITICAL
LOG_FILE_NAME = "printshop_manager.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_BACKUP_COUNT = 5  # Keep 5 rotated logs
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ============================================================
# BACKUP
# ============================================================
AUTO_BACKUP_ENABLED = False  # Overridable by settings.json
AUTO_BACKUP_DAYS = 7
AUTO_BACKUP_ON_STARTUP = True

# ============================================================
# TIMEOUTS & DELAYS
# ============================================================
THREAD_SHUTDOWN_TIMEOUT = 10  # seconds
CONNECTION_HEALTH_CHECK_INTERVAL = 300  # seconds

# ============================================================
# FEATURE FLAGS
# ============================================================
ENABLE_SPLASH_SCREEN = True
ENABLE_TRAY_ICON = True
ENABLE_ANALYTICS = True
ENABLE_API_SERVER = True
