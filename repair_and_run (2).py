r"""
repair_and_run.py  -  Print Shop Manager v14 recovery helper

Put this file in the PROJECT folder (the one that contains main.py and core\),
then run:

    python repair_and_run.py "E:\path\to\backup\printshop.db"

or just run it and paste the path when asked.

What it does:
  1. Copies the backup DB (+ -wal/-shm if present) to a scratch folder (never touches the original)
  2. Repairs the corrupted idx_c_timestamp index (falls back to a full rebuild if needed)
  3. Installs the fixed DB where core/db.py will look for it (old file is kept as a backup)
  4. Patches core/db.py: adds quick_db_check, check_db_integrity_async and the idx_c_timestamp index
  5. Launches main.py
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
WORK_DIR = PROJECT_DIR.parent / "dbfix"
STAMP = time.strftime("%Y%m%d_%H%M%S")

DB_PY = PROJECT_DIR / "core" / "db.py"
MAIN_PY = PROJECT_DIR / "main.py"

INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_c_timestamp ON machine_counters(timestamp)"

DB_FUNCTIONS = '''

# ---- added by repair_and_run.py ----
import threading
import logging

_log = logging.getLogger(__name__)


def quick_db_check() -> bool:
    """Fast startup check: a pooled connection works and the schema is readable."""
    conn = None
    try:
        conn = POOL.get()
        conn.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        return True
    except sqlite3.DatabaseError as e:
        _log.warning(f"quick_db_check failed: {e}")
        return False
    finally:
        if conn is not None:
            POOL.put(conn)


def check_db_integrity_async(callback=None) -> None:
    """Run PRAGMA integrity_check in a background thread, then call callback(bool)."""
    def _run():
        ok = False
        try:
            con = sqlite3.connect(DB_PATH)
            try:
                ok = con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            finally:
                con.close()
        except Exception as e:
            _log.warning(f"Integrity check error: {e}")
        if callback:
            try:
                callback(ok)
            except Exception:
                _log.exception("Integrity check callback failed")

    threading.Thread(target=_run, daemon=True, name="DBIntegrityCheck").start()
'''


def say(msg=""):
    print(msg, flush=True)


# ------------------------------------------------------------------ DB helpers
def integrity(path):
    """Return ('ok', None) or ('bad', reason)."""
    try:
        con = sqlite3.connect(path)
        try:
            rows = con.execute("PRAGMA integrity_check").fetchall()
        finally:
            con.close()
        if rows == [("ok",)]:
            return True, None
        return False, "; ".join(str(r[0]) for r in rows[:5])
    except sqlite3.DatabaseError as e:
        return False, str(e)


def drop_and_recreate_index(path):
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute("PRAGMA writable_schema=ON")
        con.execute("DELETE FROM sqlite_master WHERE type='index' AND name='idx_c_timestamp'")
        con.execute("PRAGMA writable_schema=OFF")
    finally:
        con.close()
    # reopen so the schema is reloaded cleanly, then rebuild the index from the table
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.execute(INDEX_SQL)
    finally:
        con.close()


CHUNK = 20000
MIN_CHUNK = 64


def _copy_range(s, d, name, marks, lo, hi, stats):
    """Copy rows with lo <= rowid < hi. If that range hits a damaged page, split it and
    retry each half, so only the truly unreadable rows are skipped."""
    try:
        rows = s.execute(
            f'SELECT * FROM "{name}" WHERE rowid >= ? AND rowid < ?', (lo, hi)
        ).fetchall()
    except sqlite3.DatabaseError:
        if hi - lo <= MIN_CHUNK:
            stats["skipped_ids"] += hi - lo
            return
        mid = (lo + hi) // 2
        _copy_range(s, d, name, marks, lo, mid, stats)
        _copy_range(s, d, name, marks, mid, hi, stats)
        return
    if rows:
        d.executemany(f'INSERT OR IGNORE INTO "{name}" VALUES ({marks})', rows)
        stats["copied"] += len(rows)


def salvage_table(s, d, name):
    """Return (rows_copied, rowid_slots_skipped) for one table."""
    stats = {"copied": 0, "skipped_ids": 0}
    try:
        ncols = len(s.execute(f'SELECT * FROM "{name}" LIMIT 0').description)
    except sqlite3.DatabaseError as e:
        say(f"   ! table {name}: cannot read table definition ({e})")
        return 0, 0
    marks = ",".join("?" * ncols)

    def bound(fn):
        try:
            return ("ok", s.execute(f'SELECT {fn}(rowid) FROM "{name}"').fetchone()[0])
        except sqlite3.DatabaseError:
            return ("err", None)

    (lo_state, lo), (hi_state, hi) = bound("MIN"), bound("MAX")
    if lo_state == "ok" and lo is None:
        return 0, 0                      # genuinely empty table
    if lo_state == "err":
        lo = 1                           # first page unreadable: start from the beginning
    if hi_state == "err":
        # last page unreadable: use the AUTOINCREMENT counter as the upper bound if we can
        try:
            row = s.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (name,)).fetchone()
            hi = row[0] if row else None
        except sqlite3.DatabaseError:
            hi = None
        if hi is None:
            say(f"   ! table {name}: cannot find the last row id; reading until the first damaged page")
            try:
                cur = s.execute(f'SELECT * FROM "{name}"')
                while True:
                    batch = cur.fetchmany(5000)
                    if not batch:
                        break
                    d.executemany(f'INSERT OR IGNORE INTO "{name}" VALUES ({marks})', batch)
                    stats["copied"] += len(batch)
            except sqlite3.DatabaseError:
                pass
            return stats["copied"], 0
    start = lo
    while start <= hi:
        _copy_range(s, d, name, marks, start, min(start + CHUNK, hi + 1), stats)
        start += CHUNK
        if (start - lo) % (CHUNK * 25) == 0:
            say(f"   ... {name}: {stats['copied']} rows so far")
            d.commit()
    return stats["copied"], stats["skipped_ids"]


def rebuild_database(src, dst):
    """Copy every table's rows into a brand-new DB, then recreate indexes/views/triggers."""
    if os.path.exists(dst):
        os.remove(dst)
    s = sqlite3.connect(src)
    s.execute("PRAGMA writable_schema=ON")
    d = sqlite3.connect(dst)
    try:
        objs = s.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE sql IS NOT NULL"
        ).fetchall()
        tables = [o for o in objs if o[0] == "table" and not o[1].startswith("sqlite_")]
        others = [o for o in objs if o[0] in ("index", "view", "trigger")]

        for _, name, _, sql in tables:
            d.execute(sql)

        for _, name, _, _ in tables:
            copied, skipped = salvage_table(s, d, name)
            note = f"  (~{skipped} row slots in damaged pages skipped)" if skipped else ""
            say(f"   table {name}: {copied} rows copied{note}")
            d.commit()

        for typ, name, _, sql in others:
            try:
                d.execute(sql)
            except sqlite3.DatabaseError as e:
                say(f"   ! could not recreate {typ} {name}: {e}")
        d.execute(INDEX_SQL)
        d.commit()
    finally:
        s.close()
        d.close()


# ------------------------------------------------------------------ steps
def step1_copy_backup(source):
    say("\n[1/5] Copying backup DB to scratch folder ...")
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    work_db = WORK_DIR / f"work_{STAMP}.db"
    shutil.copy2(source, work_db)
    for ext in ("-wal", "-shm"):
        side = Path(str(source) + ext)
        if side.exists():
            if "--with-wal" in sys.argv:
                shutil.copy2(side, str(work_db) + ext)
                say(f"   copied {side.name}")
            else:
                say(f"   ignoring {side.name} (use --with-wal to include it)")
    say(f"   working copy: {work_db}")
    return work_db


def step2_repair(work_db):
    say("\n[2/5] Checking / repairing database ...")
    ok, why = integrity(work_db)
    if ok:
        say("   already healthy")
    else:
        say(f"   problem: {why}")
        say("   trying: drop & rebuild idx_c_timestamp ...")
        try:
            drop_and_recreate_index(work_db)
        except sqlite3.DatabaseError as e:
            say(f"   index repair failed: {e}")
        ok, why = integrity(work_db)

        if not ok:
            # most common cause: -wal/-shm that don't match the .db
            wal, shm = Path(str(work_db) + "-wal"), Path(str(work_db) + "-shm")
            if wal.exists() or shm.exists():
                say("   trying again without the -wal/-shm files ...")
                for p in (wal, shm):
                    if p.exists():
                        p.rename(str(p) + ".removed")
                try:
                    drop_and_recreate_index(work_db)
                except sqlite3.DatabaseError as e:
                    say(f"   index repair failed: {e}")
                ok, why = integrity(work_db)

        if not ok:
            say(f"   still failing ({why}); rebuilding the whole DB table by table ...")
            rebuilt = WORK_DIR / f"rebuilt_{STAMP}.db"
            rebuild_database(str(work_db), str(rebuilt))
            work_db = rebuilt
            ok, why = integrity(work_db)

    if not ok:
        say(f"\nERROR: database is still not healthy: {why}")
        say("Stopping so nothing gets overwritten. Try an older backup of the DB.")
        sys.exit(1)

    # fold any WAL content into the main file so we install a single clean file
    con = sqlite3.connect(work_db, isolation_level=None)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if "machine_counters" not in {t for (t,) in tables}:
            say("\nERROR: this file is not the print shop database (no machine_counters table).")
            say("Nothing was changed. Run the script again and pick a different file.")
            sys.exit(1)
        con.execute(INDEX_SQL)
        for (t,) in tables:
            n = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            say(f"   {t}: {n} rows")
    finally:
        con.close()
    say("   integrity_check: ok")
    return work_db


def step3_install(fixed_db):
    say("\n[3/5] Installing database where the app will look for it ...")
    # same rule as core/db.py: ..\printshop.db if it exists, else printshop_v14.db in the project
    parent_db = PROJECT_DIR.parent / "printshop.db"
    target = parent_db if parent_db.exists() else PROJECT_DIR / "printshop_v14.db"

    for ext in ("", "-wal", "-shm"):
        old = Path(str(target) + ext)
        if old.exists():
            keep = WORK_DIR / f"{old.name}.before_{STAMP}"
            shutil.move(str(old), str(keep))
            say(f"   moved existing {old.name} -> {keep}")

    shutil.copy2(fixed_db, target)
    say(f"   installed: {target}")
    return target


def step4_patch_db_py():
    say("\n[4/5] Patching core/db.py ...")
    if not DB_PY.exists():
        say(f"   ERROR: {DB_PY} not found. Is this script inside the project folder?")
        sys.exit(1)

    text = DB_PY.read_text(encoding="utf-8")
    original = text

    if "idx_c_timestamp" not in text:
        marker = "idx_mlog_mid_time"
        lines = text.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if marker in line and "CREATE INDEX" in line:
                indent = line[: len(line) - len(line.lstrip())]
                lines.insert(i + 1, f'{indent}c.execute("{INDEX_SQL}")\n')
                text = "".join(lines)
                say("   added idx_c_timestamp to init_db()")
                break
        else:
            say("   ! could not find where to add idx_c_timestamp (skipped, DB already has it)")
    else:
        say("   idx_c_timestamp already in init_db()")

    if "def quick_db_check" not in text:
        text = text.rstrip() + "\n" + DB_FUNCTIONS
        say("   added quick_db_check and check_db_integrity_async")
    else:
        say("   quick_db_check already present")

    if text != original:
        backup = DB_PY.with_name(f"db.py.bak_{STAMP}")
        shutil.copy2(DB_PY, backup)
        DB_PY.write_text(text, encoding="utf-8")
        say(f"   saved (old version kept as {backup.name})")
    else:
        say("   nothing to change")


def step5_run():
    say("\n[5/5] Starting the app ...\n")
    if not MAIN_PY.exists():
        say(f"ERROR: {MAIN_PY} not found.")
        sys.exit(1)
    # show which DB core/db.py resolves to
    subprocess.call(
        [sys.executable, "-c", "from core.db import DB_PATH; print('App will use DB:', DB_PATH)"],
        cwd=str(PROJECT_DIR),
    )
    sys.exit(subprocess.call([sys.executable, str(MAIN_PY)], cwd=str(PROJECT_DIR)))


def pick_source():
    """List .db files in the project folder and its parent, let the user pick by number."""
    cands = []
    for folder in (PROJECT_DIR, PROJECT_DIR.parent):
        for p in sorted(folder.glob("*.db*")):
            if p.is_file() and not p.name.endswith(("-wal", "-shm")):
                cands.append(p)
    if not cands:
        say("No .db files found. Re-run with the full path in quotes.")
        sys.exit(1)
    say("\nDatabase files found:")
    for i, p in enumerate(cands, 1):
        st = p.stat()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime))
        say(f"  {i:2}. {p.name:35} {st.st_size / 1_048_576:9.1f} MB   {when}   ({p.parent.name})")
    choice = input("\nNumber of the backup to repair (or paste a full path): ").strip().strip('"')
    if choice.isdigit() and 1 <= int(choice) <= len(cands):
        return cands[int(choice) - 1]
    return Path(choice)


def main():
    say("=== Print Shop Manager: repair & run ===")
    say(f"Project folder: {PROJECT_DIR}")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if args:
        source = Path(args[0].strip().strip('"'))
    else:
        source = pick_source()
    if not source.is_file():
        say(f"ERROR: file not found: {source}")
        sys.exit(1)

    work_db = step1_copy_backup(source)
    fixed = step2_repair(work_db)
    step3_install(fixed)
    step4_patch_db_py()
    step5_run()


if __name__ == "__main__":
    main()
