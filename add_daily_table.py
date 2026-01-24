import sqlite3

conn = sqlite3.connect("printshop_v14.db")
c = conn.cursor()

c.execute("ALTER TABLE machines ADD COLUMN serial TEXT;")

conn.commit()
conn.close()

print("✔ Serial column added.")
