import sqlite3

conn = sqlite3.connect("mydata.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE my_table (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT,
    value REAL
)
""")

cursor.executemany("INSERT INTO my_table (name, value) VALUES (?, ?)", [
    ("Alpha", 10.5),
    ("Beta", 20.1),
    ("Gamma", 30.2)
])

conn.commit()
conn.close()

print("Database created.")
