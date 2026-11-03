
import sqlite3, os, queue
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
# prefer existing old DB if present for seamless upgrade
possible_old = os.path.join(BASE_DIR, '..', 'printshop.db')
default_v14 = os.path.join(BASE_DIR, 'printshop_v14.db')
if os.path.exists(possible_old):
    DB_PATH = possible_old
else:
    DB_PATH = default_v14

class ConnectionPool:
    def __init__(self, path=DB_PATH, size=10):
        self._path = path
        self._size = size
        self._q = queue.Queue(maxsize=size)
        for _ in range(size):
            conn = sqlite3.connect(self._path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            self._q.put(conn)

    def get(self):
        return self._q.get()

    def put(self, conn):
        try:
            self._q.put(conn, block=False)
        except Exception:
            try:
                conn.close()
            except:
                pass

    def close_all(self):
        while not self._q.empty():
            conn = self._q.get_nowait()
            try:
                conn.close()
            except:
                pass

POOL = ConnectionPool()

def init_db():
    os.makedirs(os.path.dirname(DB_PATH) or '.', exist_ok=True)
    conn = POOL.get()
    try:
        c = conn.cursor()
        c.execute("""CREATE TABLE IF NOT EXISTS machines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            ip TEXT NOT NULL,
            brand TEXT,
            model TEXT,
            is_color INTEGER DEFAULT 1,
            active INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS machine_counters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER NOT NULL,
            timestamp DATETIME DEFAULT (datetime('now','localtime')),
            total_impressions INTEGER
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS maintenance_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id INTEGER,
            timestamp DATETIME DEFAULT (datetime('now','localtime')),
            type TEXT, note TEXT, counter INTEGER
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_m_active ON machines(active)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_c_mid ON machine_counters(machine_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_mlog_mid_time ON maintenance_logs(machine_id, timestamp)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_c_timestamp ON machine_counters(timestamp)")
        conn.commit()
    finally:
        POOL.put(conn)

def close_pool():
    POOL.close_all()


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
