# core/analytics_insights.py
# FINAL — Insights from analytics_30min

from datetime import datetime, timedelta
from core.db import POOL


def get_insights():
    insights = {
        "busiest_day": "Collecting data…",
        "busiest_time": "Collecting data…",
        "quietest_period": "Collecting data…",
        "trend": "Collecting data…"
    }

    conn = POOL.get()
    c = conn.cursor()

    c.execute("""
        SELECT ts, clicks
        FROM analytics_30min
    """)
    rows = c.fetchall()

    if not rows:
        POOL.put(conn)
        return insights

    day_totals = {}
    hour_totals = {}

    for r in rows:
        dt = datetime.fromisoformat(r["ts"])
        day = dt.strftime("%A")
        hour = dt.hour
        clicks = int(r["clicks"])

        day_totals[day] = day_totals.get(day, 0) + clicks
        hour_totals[hour] = hour_totals.get(hour, 0) + clicks

    insights["busiest_day"] = max(day_totals, key=day_totals.get)
    busiest_hour = max(hour_totals, key=hour_totals.get)
    quietest_hour = min(hour_totals, key=hour_totals.get)

    insights["busiest_time"] = f"{busiest_hour:02d}:00 – {busiest_hour:02d}:59"
    insights["quietest_period"] = f"{quietest_hour:02d}:00 – {quietest_hour:02d}:59"

    # trend: last 7 vs previous 7 days
    today = datetime.now().date()
    last7 = today - timedelta(days=7)
    prev7 = today - timedelta(days=14)

    daily = {}
    for r in rows:
        d = r["ts"][:10]
        daily[d] = daily.get(d, 0) + int(r["clicks"])

    last7_total = sum(v for k, v in daily.items() if last7.isoformat() <= k <= today.isoformat())
    prev7_total = sum(v for k, v in daily.items() if prev7.isoformat() <= k < last7.isoformat())

    if prev7_total > 0:
        pct = ((last7_total - prev7_total) / prev7_total) * 100
        insights["trend"] = f"{'↑' if pct >= 0 else '↓'} {abs(pct):.1f}% vs previous week"

    POOL.put(conn)
    return insights
