import sqlite3
import os
from datetime import datetime

DB_PATH = "consumables.db"


# ---------------------------------------------------
#  DB CONNECT
# ---------------------------------------------------
def _connect():
    return sqlite3.connect(DB_PATH)


# ---------------------------------------------------
#  FULL MASTER LIST
# ---------------------------------------------------
MASTER_CONSUMABLES = [

    # ---------------- TONERS ----------------
    ("TONER_BLACK", "Toner – Black (K)", "toner"),
    ("TONER_CYAN", "Toner – Cyan (C)", "toner"),
    ("TONER_MAGENTA", "Toner – Magenta (M)", "toner"),
    ("TONER_YELLOW", "Toner – Yellow (Y)", "toner"),

    # ---------------- DRUM ----------------
    ("DRUM_BLACK", "Drum – Black", "drum"),
    ("DRUM_CYAN", "Drum – Cyan", "drum"),
    ("DRUM_MAGENTA", "Drum – Magenta", "drum"),
    ("DRUM_YELLOW", "Drum – Yellow", "drum"),
    ("DRUM_AIO", "Drum – All-in-One (Xerox)", "drum"),

    # ---------------- DEVELOPER SYSTEM ----------------
    ("DEV_UNIT_BLACK", "Developer Unit – Black", "developer"),
    ("DEV_UNIT_CYAN", "Developer Unit – Cyan", "developer"),
    ("DEV_UNIT_MAGENTA", "Developer Unit – Magenta", "developer"),
    ("DEV_UNIT_YELLOW", "Developer Unit – Yellow", "developer"),

    ("DEV_ROLLER", "Developer Roller", "developer"),
    ("DEV_POWDER", "Developer Powder", "developer"),
    ("DEV_BUSH", "Developer Bush", "developer"),
    ("DEV_SEAL", "Developer Seal", "developer"),
    ("DEV_SENSOR", "Developer Sensor", "developer"),

    ("DEV_CLEANED", "Developer Cleaned", "developer"),
    ("DEV_ADJUSTED", "Developer Adjusted", "developer"),

    # -------- LASER / IMAGING CLEAN ----------
    ("LASER_CLEAN_BLACK", "Laser System Clean – Black", "laser"),
    ("LASER_CLEAN_CYAN", "Laser System Clean – Cyan", "laser"),
    ("LASER_CLEAN_MAGENTA", "Laser System Clean – Magenta", "laser"),
    ("LASER_CLEAN_YELLOW", "Laser System Clean – Yellow", "laser"),

    # ---------------- TRANSFER SYSTEM ----------------
    ("TRANSFER_BELT", "Transfer Belt (IBT Belt)", "transfer"),
    ("TRANSFER_BELT_CLEANER", "IBT Belt Cleaner", "transfer"),
    ("TRANSFER_ROLLER", "Transfer Roller", "transfer"),
    ("SECOND_BIAS_ROLLER", "2nd Bias Roller", "transfer"),
    ("SECOND_BIAS_CLEANER", "2nd Bias Cleaner", "transfer"),

    # ---------------- FUSER SYSTEM ----------------
    ("FUSER_UNIT", "Fuser Unit", "fuser"),
    ("FUSER_FILM", "Fuser Film", "fuser"),
    ("FUSER_PRESSURE_ROLLER", "Fuser Pressure Roller", "fuser"),
    ("FUSER_HEAT_SINK", "Fuser Heat Sink", "fuser"),
    ("FUSER_BUSH", "Fuser Bush", "fuser"),
    ("FUSER_KIT", "Fuser Kit", "fuser"),

    # ---------------- ROLLERS ----------------
    ("T1_PICKUP", "Tray 1 Pickup Roller", "roller"),
    ("T2_PICKUP", "Tray 2 Pickup Roller", "roller"),
    ("T3_PICKUP", "Tray 3 Pickup Roller", "roller"),
    ("T4_PICKUP", "Tray 4 Pickup Roller", "roller"),
    ("T5_PICKUP", "Tray 5 Pickup Roller", "roller"),

    ("BYPASS_PICKUP", "Bypass Pickup Roller", "roller"),
    ("ADF_PICKUP", "ADF Pickup Roller", "roller"),
    ("SEPARATION_PAD", "Separation Pad", "roller"),
    ("GENERIC_FEED_ROLLER", "Paper Feed Roller (Generic)", "roller"),

    # ---------------- WASTE & CLEANING ----------------
    ("WASTE_TONER", "Waste Toner Box", "waste"),
    ("CLEANING_UNIT", "Cleaning Unit", "waste"),
]


# ---------------------------------------------------
#  INIT DATABASE
# ---------------------------------------------------
def init_consumables_db():
    conn = _connect()
    c = conn.cursor()

    # Main table
    c.execute("""
        CREATE TABLE IF NOT EXISTS consumables (
            machine_id INTEGER,
            code TEXT,
            name TEXT,
            category TEXT,
            last_counter INTEGER DEFAULT 0,
            note TEXT,
            PRIMARY KEY (machine_id, code)
        );
    """)

    # Log table
    c.execute("""
        CREATE TABLE IF NOT EXISTS consumable_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER,
            code TEXT,
            name TEXT,
            old_counter INTEGER,
            new_counter INTEGER,
            yield_used INTEGER,
            note TEXT,
            ts TEXT
        );
    """)

    conn.commit()
    conn.close()


# ---------------------------------------------------
#  SEED ALL CONSUMABLES FOR A MACHINE
# ---------------------------------------------------
def ensure_machine_consumables(machine_id: int, current_ctr: int):
    """
    Ensure every machine has the entire master consumable set.
    Only inserts missing rows.
    """
    conn = _connect()
    c = conn.cursor()

    c.execute("""
        SELECT code FROM consumables WHERE machine_id = ?
    """, (machine_id,))
    existing = {row[0] for row in c.fetchall()}

    inserts = []
    for code, name, category in MASTER_CONSUMABLES:
        if code not in existing:
            inserts.append((machine_id, code, name, category, current_ctr, ""))

    if inserts:
        c.executemany("""
            INSERT INTO consumables
            (machine_id, code, name, category, last_counter, note)
            VALUES (?, ?, ?, ?, ?, ?)
        """, inserts)

    conn.commit()
    conn.close()


# ---------------------------------------------------
#  FETCH MACHINE CONSUMABLES
# ---------------------------------------------------
def get_machine_consumables(machine_id: int):
    conn = _connect()
    c = conn.cursor()
    c.execute("""
        SELECT code, name, category, last_counter, note
        FROM consumables
        WHERE machine_id = ?
        ORDER BY category, code
    """, (machine_id,))
    rows = c.fetchall()
    conn.close()

    return [
        {
            "code": r[0],
            "name": r[1],
            "category": r[2],
            "last_counter": r[3],
            "note": r[4],
        }
        for r in rows
    ]


# ---------------------------------------------------
#  WRITE LOG ENTRY
# ---------------------------------------------------
def log_consumable_event(machine_id, code, name, old_ctr, new_ctr, note):
    conn = _connect()
    c = conn.cursor()

    used = max(0, new_ctr - old_ctr)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    c.execute("""
        INSERT INTO consumable_logs
        (machine_id, code, name, old_counter, new_counter, yield_used, note, ts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (machine_id, code, name, old_ctr, new_ctr, used, note or "", ts))

    conn.commit()
    conn.close()


# ---------------------------------------------------
#  RESET CONSUMABLE (WITH LOGGING)
# ---------------------------------------------------
def reset_consumable(machine_id: int, code: str, new_counter: int, note: str = ""):
    conn = _connect()
    c = conn.cursor()

    # Fetch old counter & name
    c.execute("""
        SELECT last_counter, name
        FROM consumables
        WHERE machine_id = ? AND code = ?
    """, (machine_id, code))
    row = c.fetchone()

    old_ctr = int(row[0]) if row else 0
    name = row[1] if row else code

    # Update baseline
    c.execute("""
        UPDATE consumables
        SET last_counter = ?, note = ?
        WHERE machine_id = ? AND code = ?
    """, (new_counter, note, machine_id, code))

    conn.commit()
    conn.close()

    # Also write log
    log_consumable_event(machine_id, code, name, old_ctr, new_counter, note)


# ---------------------------------------------------
#  FETCH LOGS
# ---------------------------------------------------
def get_logs():
    conn = _connect()
    c = conn.cursor()

    c.execute("""
        SELECT ts, machine_id, code, name, old_counter, new_counter, yield_used, note
        FROM consumable_logs
        ORDER BY id DESC
    """)

    rows = c.fetchall()
    conn.close()
    return rows
