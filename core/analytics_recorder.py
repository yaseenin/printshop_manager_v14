# core/analytics_recorder.py
# ---------------------------------------------------------
# Printer Analytics Recorder (30-minute slots)
# SAFE, CORRECT, NON-DUPLICATING
# ---------------------------------------------------------

import threading
import logging
from datetime import datetime, timedelta
import sqlite3
import time

from core.db import POOL
from core.models import fetch_machines, insert_counter
from core.snmp import probe_and_read_counter
from core.ping import ping_ip
# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
TABLE = "analytics_30min"
BASELINE_TABLE = "analytics_baseline"
POLL_SECONDS = 60

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("analytics")

# ---------------------------------------------------------
# THREAD STATE
# ---------------------------------------------------------
_stop_evt = threading.Event()
_thread = None
_last_slot = None


# ---------------------------------------------------------
# TIME HELPERS
# ---------------------------------------------------------
def round_to_30(dt: datetime) -> datetime:
    """Round DOWN to 30-minute slot"""
    return dt.replace(
        minute=0 if dt.minute < 30 else 30,
        second=0,
        microsecond=0
    )


# ---------------------------------------------------------
# DB INIT
# ---------------------------------------------------------
def init_tables():
    conn = POOL.get()
    try:
        c = conn.cursor()

        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                machine_id INTEGER NOT NULL,
                ts TEXT NOT NULL,
                clicks INTEGER NOT NULL,
                UNIQUE(machine_id, ts)
            )
        """)

        c.execute(f"""
            CREATE TABLE IF NOT EXISTS {BASELINE_TABLE} (
                machine_id INTEGER PRIMARY KEY,
                last_total INTEGER NOT NULL,
                last_ts TEXT NOT NULL
            )
        """)

        conn.commit()
    finally:
        POOL.put(conn)


# ---------------------------------------------------------
# BASELINE HELPERS (FIXED: 3 columns)
# ---------------------------------------------------------
def get_baseline(mid: int):
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute(
            f"SELECT last_total, last_ts FROM {BASELINE_TABLE} WHERE machine_id=?",
            (mid,)
        )
        row = c.fetchone()
        if not row:
            return None, None
        return int(row[0]), datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    finally:
        POOL.put(conn)


def set_baseline(mid: int, total: int, ts: datetime):
    conn = POOL.get()
    try:
        c = conn.cursor()
        # Retry logic for database locked errors
        for attempt in range(5):
            try:
                c.execute(
                    f"""
                    INSERT OR REPLACE INTO {BASELINE_TABLE}
                    (machine_id, last_total, last_ts)
                    VALUES (?, ?, ?)
                    """,
                    (mid, int(total), ts.strftime("%Y-%m-%d %H:%M:%S"))
                )
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 4:
                    time.sleep(0.25)
                    continue
                raise
    finally:
        POOL.put(conn)


# ---------------------------------------------------------
# SLOT WRITE (ADD-SAFE)
# ---------------------------------------------------------
def add_slot(mid: int, slot: datetime, clicks: int):
    if clicks <= 0:
        return

    conn = POOL.get()
    try:
        c = conn.cursor()
        ts = slot.strftime("%Y-%m-%d %H:%M:%S")

        # Retry logic for database locked errors
        for attempt in range(5):
            try:
                try:
                    c.execute(
                        f"""
                        INSERT INTO {TABLE} (machine_id, ts, clicks)
                        VALUES (?, ?, ?)
                        """,
                        (mid, ts, clicks)
                    )
                except sqlite3.IntegrityError:
                    c.execute(
                        f"""
                        UPDATE {TABLE}
                        SET clicks = clicks + ?
                        WHERE machine_id=? AND ts=?
                        """,
                        (clicks, mid, ts)
                    )
                conn.commit()
                break
            except sqlite3.OperationalError as e:
                if "locked" in str(e) and attempt < 4:
                    time.sleep(0.25)
                    continue
                raise
    finally:
        POOL.put(conn)


# ---------------------------------------------------------
# CORE RECORD LOGIC (TRUTH SOURCE)
# ---------------------------------------------------------
def record_once(slot: datetime):
    machines = fetch_machines(active_only=True)

    for m in machines:
        # Yield to main thread to prevent UI freezing
        time.sleep(0.5)

        mid = m["id"]
        ip = m.get("ip")
        if not ip:
            continue

        try:
            try:
                # Check connectivity first to avoid expensive SNMP timeouts
                if not ping_ip(ip, timeout_ms=500):
                    log.debug("Ping failed for %s (%s), skipping analytics slot.", m.get("name"), ip)
                    continue

                # Get LIVE counter via SNMP, not stale from DB
                current = probe_and_read_counter(ip)
                if current is None:
                    log.warning("SNMP read failed for %s (%s), skipping analytics slot.", m.get("name"), ip)
                    continue
                
                current = int(current)

                # Keep the historical log up-to-date
                insert_counter(mid, current)

            except Exception as e:
                log.exception("Counter read failed for MID %s (%s): %s", mid, ip, e)
                continue

            last_total, _ = get_baseline(mid)

            # First-time baseline recovery is still needed for app restarts
            if last_total is None:
                conn = POOL.get()
                try:
                    c = conn.cursor()
                    # Fetch last few rows to find a stable previous value
                    c.execute(
                        "SELECT total_impressions, timestamp FROM machine_counters WHERE machine_id=? ORDER BY id DESC LIMIT 5",
                        (mid,)
                    )
                    rows = c.fetchall()
                    
                    # Look for a counter that is OLDER than 2 minutes.
                    # This ignores the entry we (or the dashboard) just inserted on startup.
                    cutoff = datetime.now() - timedelta(minutes=2)
                    
                    for r in rows:
                        # r[1] is timestamp string
                        try:
                            ts = datetime.strptime(r[1], "%Y-%m-%d %H:%M:%S")
                            if ts < cutoff:
                                last_total = int(r[0])
                                break
                        except Exception:
                            continue

                finally:
                    POOL.put(conn)

                # If still no baseline, it's a new machine. Set baseline and skip delta.
                if last_total is None:
                    set_baseline(mid, current, slot)
                    continue

            delta = current - last_total

            if delta < 0:
                log.warning("Counter reset detected for MID %s. New baseline set to %s.", mid, current)
                set_baseline(mid, current, slot)
                continue

            if delta > 0:
                add_slot(mid, slot, delta)

            # Always update baseline to the latest known counter
            set_baseline(mid, current, slot)
        except Exception as e:
            log.error(f"Error processing analytics for machine {mid}: {e}")
            continue


# ---------------------------------------------------------
# THREAD LOOP
# ---------------------------------------------------------
def _loop():
    global _last_slot

    init_tables()
    log.info("[RECORDER] Started")

    while not _stop_evt.is_set():
        try:
            now = datetime.now()
            slot = round_to_30(now)

            # The record_once function calculates deltas from the last baseline.
            # It must be called repeatedly to capture usage within a slot.
            record_once(slot)

            # Log only when the slot changes for cleaner output
            if _last_slot is None or slot > _last_slot:
                _last_slot = slot
                log.info(f"[RECORDER] Now recording for slot: {slot}")
        except Exception:
            log.exception("[RECORDER] Unhandled exception in recorder loop")

        _stop_evt.wait(POLL_SECONDS)

    log.info("[RECORDER] Stopped")


# ---------------------------------------------------------
# PUBLIC API (WHAT main.py EXPECTS)
# ---------------------------------------------------------
def start_recorder():
    global _thread

    if _thread and _thread.is_alive():
        return

    _stop_evt.clear()
    _thread = threading.Thread(target=_loop, daemon=True)
    _thread.start()


def stop_recorder():
    _stop_evt.set()
    if _thread:
        _thread.join(timeout=2)


# ---------------------------------------------------------
# BACKWARD-SAFE ALIASES (optional future use)
# ---------------------------------------------------------
start = start_recorder
stop = stop_recorder
