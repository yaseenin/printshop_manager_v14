# api_server.py — v14 FULL, STABLE, FLUTTER-SAFE

from flask import Flask, jsonify, request
from datetime import datetime, date, timedelta
import socket
import subprocess
import platform

from core.db import POOL
from core.models import (
    fetch_machines,
    fetch_daily_rows,
    fetch_latest_counter,
)
from core.ping import ping_ip

app = Flask(__name__)

# -------------------------------------------------
# ONLINE CHECK
# -------------------------------------------------
def is_machine_online(ip, timeout=1):
    return ping_ip(ip, timeout_ms=timeout*1000)


# -------------------------------------------------
# ROW HELPERS (🔥 MISSING BEFORE)
# -------------------------------------------------
def get_date_from_row(r):
    return r["date"] if isinstance(r, dict) else r[0]

def get_prints_from_row(r):
    return r["prints"] if isinstance(r, dict) else r[1]


# -------------------------------------------------
# ROOT
# -------------------------------------------------
@app.route("/")
def index():
    return jsonify({"status": "PrintShop API v14 running"})


# -------------------------------------------------
# DASHBOARD
# -------------------------------------------------
@app.route("/dashboard")
def dashboard():
    machines = fetch_machines(active_only=False)

    online = 0
    offline = 0
    today_total = 0
    today = date.today().isoformat()

    for m in machines:
        mid = m["id"]
        ip = m["ip"]

        if is_machine_online(ip):
            online += 1
        else:
            offline += 1

        for r in fetch_daily_rows(mid):
            if get_date_from_row(r) == today:
                today_total += get_prints_from_row(r)

    return jsonify({
        "total_machines": len(machines),
        "online": online,
        "offline": offline,
        "today_prints": today_total,
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    })


# -------------------------------------------------
# MACHINES (CARDS)
# -------------------------------------------------
@app.route("/machines")
def machines():
    out = []
    today = date.today().isoformat()

    for m in fetch_machines(active_only=False):
        mid = m["id"]
        ip = m["ip"]

        today_p = 0
        yesterday_p = 0

        rows = fetch_daily_rows(mid)
        for r in rows:
            d = get_date_from_row(r)
            p = get_prints_from_row(r)
            if d == today:
                today_p = p
            elif d < today:
                yesterday_p = p

        out.append({
            "id": mid,
            "name": m["name"],
            "brand": m["brand"],
            "model": m["model"],
            "ip": ip,
            "online": is_machine_online(ip),
            "today": today_p,
            "yesterday": yesterday_p,
            "current_counter": fetch_latest_counter(mid),
        })

    return jsonify(out)


# -------------------------------------------------
# REPORTS (🔥 FIXED)
# -------------------------------------------------
@app.route("/reports")
def reports():
    out = []
    machines = fetch_machines(active_only=False)

    for m in machines:
        mid = m["id"]
        for r in fetch_daily_rows(mid):
            out.append({
                "machine_id": mid,
                "machine_name": m["name"],
                "date": get_date_from_row(r),
                "prints": get_prints_from_row(r),
            })

    return jsonify(out)


# -------------------------------------------------
# ANALYTICS — DAILY AGGREGATE
# -------------------------------------------------
@app.route("/analytics/daily")
def analytics_daily():
    days = int(request.args.get("days", 7))
    start = (date.today() - timedelta(days=days-1)).isoformat()

    totals = {}

    for m in fetch_machines(active_only=False):
        for r in fetch_daily_rows(m["id"]):
            d = get_date_from_row(r)
            if d >= start:
                totals[d] = totals.get(d, 0) + get_prints_from_row(r)

    data = [{"date": d, "clicks": totals[d]} for d in sorted(totals)]

    return jsonify({
        "range_days": days,
        "data": data
    })


# -------------------------------------------------
# ANALYTICS — HEATMAP (30min × 7 days)
# -------------------------------------------------
@app.route("/analytics/heatmap")
def analytics_heatmap():
    days = int(request.args.get("days", 7))
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    matrix = [[0 for _ in range(48)] for _ in range(7)]
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    conn = POOL.get()
    c = conn.cursor()

    c.execute("""
        SELECT ts, clicks
        FROM analytics_30min
        WHERE ts >= ?
    """, (start,))

    for ts, clicks in c.fetchall():
        dt = datetime.fromisoformat(ts)
        day = dt.weekday()
        slot = dt.hour * 2 + (1 if dt.minute >= 30 else 0)
        matrix[day][slot] += clicks

    POOL.put(conn)

    return jsonify({
        "days": day_names,
        "slots": matrix
    })


# -------------------------------------------------
# ANALYTICS — LIVE SLOT (TODAY)
# -------------------------------------------------
@app.route("/analytics/live")
def analytics_live():
    today = date.today().isoformat()
    out = {}

    conn = POOL.get()
    c = conn.cursor()

    c.execute("""
        SELECT ts, SUM(clicks)
        FROM analytics_30min
        WHERE ts LIKE ?
        GROUP BY ts
        ORDER BY ts
    """, (today + "%",))

    for ts, clicks in c.fetchall():
        out[ts[11:16]] = clicks

    POOL.put(conn)
    return jsonify(out)


# -------------------------------------------------
# ANALYTICS — PEAK HOUR
# -------------------------------------------------
@app.route("/analytics/peak")
def analytics_peak():
    conn = POOL.get()
    c = conn.cursor()

    c.execute("""
        SELECT strftime('%H', ts) AS hour, SUM(clicks) AS total
        FROM analytics_30min
        GROUP BY hour
        ORDER BY total DESC
        LIMIT 1
    """)

    row = c.fetchone()
    POOL.put(conn)

    if not row:
        return jsonify({"hour": None, "clicks": 0})

    return jsonify({
        "hour": int(row[0]),
        "clicks": row[1]
    })


# -------------------------------------------------
# START
# -------------------------------------------------
def start_api():
    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False,
        use_reloader=False
    )
