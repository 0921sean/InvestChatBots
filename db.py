import sqlite3
import os

def _conn():
    path = os.getenv("DB_PATH", "investchat.db")
    return sqlite3.connect(path)

def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            agent_name TEXT NOT NULL,
            model TEXT,
            content TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rounds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT,
            status TEXT DEFAULT 'running',
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_name TEXT PRIMARY KEY,
            evolution_notes TEXT DEFAULT '',
            round_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)

def save_message(round_id, agent_name, model, content):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO messages (round_id, agent_name, model, content) VALUES (?,?,?,?)",
            (round_id, agent_name, model, content)
        )
        return cur.lastrowid

def get_messages_since(since_id, limit=100):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM messages WHERE id > ? ORDER BY id ASC LIMIT ?",
            (since_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

def get_recent_messages(n=30):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM messages ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return list(reversed([dict(r) for r in rows]))

def save_agent_state(agent_name, evolution_notes, round_count):
    with _conn() as con:
        con.execute("""
            INSERT INTO agent_state (agent_name, evolution_notes, round_count, updated_at)
            VALUES (?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(agent_name) DO UPDATE SET
                evolution_notes=excluded.evolution_notes,
                round_count=excluded.round_count,
                updated_at=excluded.updated_at
        """, (agent_name, evolution_notes, round_count))

def get_agent_state(agent_name):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM agent_state WHERE agent_name=?", (agent_name,)
        ).fetchone()
        return dict(row) if row else {"agent_name": agent_name, "evolution_notes": "", "round_count": 0}

def create_round(topic):
    with _conn() as con:
        cur = con.execute("INSERT INTO rounds (topic) VALUES (?)", (topic,))
        return cur.lastrowid

def complete_round(round_id):
    with _conn() as con:
        con.execute("UPDATE rounds SET status='complete' WHERE id=?", (round_id,))

def save_market_snapshot(data_json):
    with _conn() as con:
        cur = con.execute("INSERT INTO market_snapshots (data) VALUES (?)", (data_json,))
        return cur.lastrowid

def get_latest_market_snapshot():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM market_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
