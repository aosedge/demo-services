import uuid
import time

try:
    import sqlite3
    
    print("Sqlite3 is installed.")
except ImportError:
    print("Sqlite3 is not installed.")
    exit()

conn = sqlite3.connect('/storage/sqllite_example.db')
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
''')

uid = str(uuid.uuid4())
current_time = time.strftime('%Y-%m-%d %H:%M:%S')

cursor.execute('INSERT INTO sessions (uid, created_at) VALUES (?, ?)', (uid, current_time))

conn.commit()

cursor.execute('SELECT * FROM sessions')
rows = cursor.fetchall()

conn.close()

while True:
    print("[AosEdge] Print session list:")
    for row in rows:
        print(row)
        
    time.sleep(5) 
