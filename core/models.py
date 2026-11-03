# core/models.py
# Updated for Print Shop Manager v14
# + Added SERIAL NUMBER SUPPORT
# + Safe serial update (no crash if column missing)
# + All existing logic preserved (today clicks, snapshots, CSV)

from core.db import POOL
import csv
from datetime import datetime, timedelta, date
from typing import List, Tuple, Optional


# ======================================================
# MACHINE LIST / CRUD
# ======================================================
def fetch_machines(active_only=True):
    conn = POOL.get()
    try:
        c = conn.cursor()
        # serial column may or may not exist -> safe select
        try:
            if active_only:
                c.execute(
                    "SELECT id,name,ip,brand,model,is_color,serial "
                    "FROM machines WHERE active=1 ORDER BY id"
                )
            else:
                c.execute(
                    "SELECT id,name,ip,brand,model,is_color,serial "
                    "FROM machines ORDER BY id"
                )
        except Exception:
            # fallback for DBs without serial column
            if active_only:
                c.execute(
                    "SELECT id,name,ip,brand,model,is_color "
                    "FROM machines WHERE active=1 ORDER BY id"
                )
            else:
                c.execute(
                    "SELECT id,name,ip,brand,model,is_color "
                    "FROM machines ORDER BY id"
                )
        return [dict(r) for r in c.fetchall()]
    finally:
        POOL.put(conn)


def insert_machine(name, ip, brand, model, is_color):
    conn = POOL.get()
    c = conn.cursor()

    # attempt with serial column
    try:
        c.execute(
            "INSERT INTO machines (name,ip,brand,model,is_color,serial) "
            "VALUES (?,?,?,?,?,NULL)",
            (name, ip, brand, model, is_color),
        )
    except Exception:
        # fallback if serial column doesn't exist
        c.execute(
            "INSERT INTO machines (name,ip,brand,model,is_color) "
            "VALUES (?,?,?,?,?)",
            (name, ip, brand, model, is_color),
        )

    conn.commit()
    POOL.put(conn)


def update_machine(mid, name, ip, brand, model, is_color):
    conn = POOL.get()
    c = conn.cursor()

    # attempt including serial
    try:
        c.execute(
            "UPDATE machines SET name=?,ip=?,brand=?,model=?,is_color=? "
            "WHERE id=?",
            (name, ip, brand, model, is_color, mid),
        )
    except Exception:
        pass

    conn.commit()
    POOL.put(conn)


def update_machine_serial(mid: int, serial: str):
    """
    Store SNMP-fetched serial into DB.
    Safe: if column doesn't exist, we ignore error.
    """
    conn = POOL.get()
    c = conn.cursor()
    try:
        c.execute("UPDATE machines SET serial=? WHERE id=?", (serial, mid))
        conn.commit()
    except Exception:
        # DB has no serial column -> ignore
        pass
    POOL.put(conn)


def update_machine_last_online(mid: int):
    """
    Update the last_online timestamp for a machine to now.
    Safe: if column doesn't exist, we ignore error.
    
    Args:
        mid: Machine ID to update
    """
    conn = POOL.get()
    c = conn.cursor()
    try:
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("UPDATE machines SET last_online=? WHERE id=?", (now, mid))
        conn.commit()
    except Exception:
        # DB has no last_online column -> ignore
        pass
    POOL.put(conn)


def fetch_machine_last_online(mid: int) -> str:
    """
    Fetch the last_online timestamp for a machine.
    Safe: if column doesn't exist, returns None.
    
    Args:
        mid: Machine ID
        
    Returns:
        Last online datetime string or None if not available
    """
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute("SELECT last_online FROM machines WHERE id=?", (mid,))
        row = c.fetchone()
        if row and row[0]:
            return row[0]
        return None
    except Exception:
        # Column doesn't exist
        return None
    finally:
        POOL.put(conn)


def fetch_machine_last_online_or_latest_counter(mid: int) -> str:
    """
    Fetch the last_online timestamp for a machine.
    If not available, fall back to the timestamp of the latest counter.
    This ensures machines that were active before the feature was added still show a time.
    
    Args:
        mid: Machine ID
        
    Returns:
        Last online datetime string or latest counter timestamp or None
    """
    # First try to get explicit last_online
    last_online = fetch_machine_last_online(mid)
    if last_online:
        return last_online
    
    # Fall back to latest counter timestamp
    try:
        latest = fetch_latest_counter(mid)
        if latest and latest.get("timestamp"):
            return latest["timestamp"]
    except Exception:
        pass
    
    return None


def format_last_online(timestamp: str) -> str:
    """
    Format last_online timestamp to "HH:MM DD-MM-YY" format.
    
    Args:
        timestamp: Timestamp string in "YYYY-MM-DD HH:MM:SS" format
        
    Returns:
        Formatted string like "16:48 19-03-26" or empty string if invalid
    """
    if not timestamp:
        return ""
    try:
        from datetime import datetime
        dt = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%H:%M %d-%m-%y")
    except Exception:
        return ""


def deactivate_machine(mid):
    conn = POOL.get()
    c = conn.cursor()
    c.execute("UPDATE machines SET active=0 WHERE id=?", (mid,))
    conn.commit()
    POOL.put(conn)


# ======================================================
# COUNTER STORAGE
# ======================================================
def insert_counter(mid, total):
    conn = POOL.get()
    try:
        c = conn.cursor()
        # Use local time explicitly to avoid UTC timezone mismatches with date.today()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute(
            "INSERT INTO machine_counters (machine_id,total_impressions,timestamp) VALUES (?,?,?)",
            (mid, total, now_str),
        )
        conn.commit()
    finally:
        POOL.put(conn)


def fetch_latest_counter(mid):
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT timestamp,total_impressions FROM machine_counters "
            "WHERE machine_id=? ORDER BY id DESC LIMIT 1",
            (mid,),
        )
        r = c.fetchone()
        return dict(r) if r else None
    finally:
        POOL.put(conn)


def fetch_latest_counters(mids: List[int]) -> dict:
    """
    Return a mapping of machine_id -> latest counter dict (or None) for the given mids.
    Performs a single query to reduce DB roundtrips.
    """
    if not mids:
        return {}

    # Build placeholders
    placeholders = ",".join(["?"] * len(mids))

    conn = POOL.get()
    try:
        c = conn.cursor()
        # Join with subquery that finds the max id per machine
        q = f"""
            SELECT mc.machine_id, mc.timestamp, mc.total_impressions
            FROM machine_counters mc
            JOIN (
                SELECT machine_id, MAX(id) AS maxid
                FROM machine_counters
                WHERE machine_id IN ({placeholders})
                GROUP BY machine_id
            ) sub ON mc.machine_id=sub.machine_id AND mc.id=sub.maxid
        """
        c.execute(q, tuple(mids))
        rows = c.fetchall()
        result = {row[0]: {"timestamp": row[1], "total_impressions": row[2]} for row in rows}
        # Ensure all mids present
        for mid in mids:
            result.setdefault(mid, None)
        return result
    finally:
        POOL.put(conn)


# ======================================================
# TODAY CLICKS CALCULATION
# (Your stable version preserved)
# ======================================================
def get_today_clicks(mid, current_counter):
    """
    Ensures today clicks are correct even if app started late or printer offline.
    Uses machine_counters table only.
    """

    conn = POOL.get()
    try:
        c = conn.cursor()
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        today_str = date.today().strftime("%Y-%m-%d")

        # Return connection and perform insert via central helper.
        # Set conn to None so finally won't double-put if insert_counter raises.
        POOL.put(conn)
        conn = None
        try:
            insert_counter(mid, current_counter)
        except Exception:
            # Preserve existing behavior: ignore insert errors here.
            pass

        # Re-acquire connection for the select
        conn = POOL.get()
        c = conn.cursor()

        # Calculate: Current - Max(Yesterday or older)
        c.execute(
            "SELECT MAX(total_impressions) FROM machine_counters WHERE machine_id=? AND date(timestamp) < ?",
            (mid, today_str)
        )
        row = c.fetchone()
        prev_max = row[0] if row and row[0] else 0

        if prev_max == 0:
            return 0

        return max(0, current_counter - prev_max)
    finally:
        if conn is not None:
            POOL.put(conn)


# ======================================================
# DAILY HISTORY FOR REPORTS
# ======================================================
def fetch_daily_rows(mid, days=30):
    conn = POOL.get()
    c = conn.cursor()
    
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)

    # Fetch all daily maximums in a slightly wider window
    c.execute(
        """
        SELECT
            date(timestamp) AS day,
            MAX(total_impressions) AS max_total
        FROM machine_counters
        WHERE machine_id=? AND date(timestamp) >= ?
        GROUP BY day
        ORDER BY day ASC
        """,
        (mid, (start_date - timedelta(days=1)).strftime("%Y-%m-%d")),
    )
    all_maxes = {row[0]: row[1] for row in c.fetchall()}
    POOL.put(conn)

    results = []
    
    # Establish baseline from BEFORE the window starts
    conn = POOL.get()
    c = conn.cursor()
    c.execute(
        "SELECT MAX(total_impressions) FROM machine_counters WHERE machine_id=? AND date(timestamp) < ?",
        (mid, start_date.strftime("%Y-%m-%d"))
    )
    row = c.fetchone()
    last_max = row[0] if row and row[0] else 0
    POOL.put(conn)

    # Iterate through range
    current_date = start_date
    while current_date <= end_date:
        d_str = current_date.strftime("%Y-%m-%d")
        if d_str in all_maxes:
            current_max = all_maxes[d_str]
            # If last_max is 0 (new machine), assume 0 clicks for first day to avoid spike
            clicks = max(0, current_max - last_max) if last_max > 0 else 0
            results.append((d_str, clicks))
            last_max = current_max
        else:
            results.append((d_str, 0))
        current_date += timedelta(days=1)

    return results


# ======================================================
# AUTO-RECONSTRUCTION OF START / END COUNTERS FOR REPORTS
# ======================================================
def build_daily_start_end_from_latest(machine_id: int, days: int = 30):
    rows = fetch_daily_rows(machine_id, days=days) or []
    if not rows:
        return []

    latest = fetch_latest_counter(machine_id)
    if not latest or "total_impressions" not in latest:
        raise RuntimeError("Missing latest counter")

    end_now = int(latest["total_impressions"])
    reconstructed = []
    curr_end = end_now

    for (date_str, clicks) in reversed(rows):
        clicks = int(clicks or 0)
        start = max(curr_end - clicks, 0)
        reconstructed.append({"date": date_str, "start": start, "end": curr_end, "clicks": clicks})
        curr_end = start

    reconstructed.reverse()
    return reconstructed


# ======================================================
# CSV EXPORT LOGIC
# ======================================================
def export_daily_detailed_csv(machine_id: int, out_path: str, days: int = 30) -> str:
    rows = build_daily_start_end_from_latest(machine_id, days=days)
    if not rows:
        raise RuntimeError("No daily data")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["Date", "Start Counter", "End Counter", "Clicks"])
        for r in rows:
            writer.writerow([r["date"], r["start"], r["end"], r["clicks"]])

    return out_path
