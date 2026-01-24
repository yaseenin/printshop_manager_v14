from core.db import POOL

conn = POOL.get()
c = conn.cursor()

c.execute("""
SELECT SUM(clicks)
FROM analytics_30min
WHERE ts >= date('now')
""")

print("Analytics DB total today:", c.fetchone()[0])

POOL.put(conn)
