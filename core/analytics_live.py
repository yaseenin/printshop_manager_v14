# core/analytics_live.py
# LIVE analytics reader (USES core.db POOL — CORRECT)

from datetime import date
from core.db import POOL


def get_live_slot_data():
    """
    Returns: dict { "HH:MM": clicks } for today
    Reads from SAME DB as analytics_recorder
    """

    today = date.today().isoformat()
    result = {}

    conn = POOL.get()
    try:
        c = conn.cursor()

        # adjust table name ONLY if yours differs
        c.execute("""
            SELECT slot, clicks
            FROM analytics_slots
            WHERE date = ?
        """, (today,))

        for r in c.fetchall():
            result[r["slot"]] = int(r["clicks"])

    except Exception:
        result = {}
    finally:
        POOL.put(conn)

    return result


def get_recorder_status():
    # Recorder is started at app init
    return True, "next 30-min slot"
