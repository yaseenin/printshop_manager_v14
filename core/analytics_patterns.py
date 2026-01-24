# core/analytics_patterns.py
# FINAL — reads analytics_30min using ts

from datetime import datetime, timedelta
from core.db import POOL


def get_heatmap_matrix(days: int):
    """
    Returns 7 x 24 matrix
    Rows: Mon..Sun
    Cols: Hour 0..23
    """

    matrix = [[0 for _ in range(24)] for _ in range(7)]
    start = (datetime.now() - timedelta(days=days)).date().isoformat()

    conn = POOL.get()
    c = conn.cursor()

    c.execute("""
        SELECT ts, clicks
        FROM analytics_30min
        WHERE ts >= ?
    """, (start,))

    for r in c.fetchall():
        try:
            dt = datetime.fromisoformat(r["ts"])
            day = dt.weekday()      # 0 = Monday
            hour = dt.hour
            matrix[day][hour] += int(r["clicks"])
        except Exception:
            continue

    POOL.put(conn)
    return matrix
