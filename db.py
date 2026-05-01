import sqlite3
import os

def _conn():
    path = os.getenv("DB_PATH", "investchat.db")
    return sqlite3.connect(path)

def _migrate():
    """기존 DB에 누락된 컬럼을 안전하게 추가."""
    with _conn() as con:
        for sql in [
            "ALTER TABLE messages ADD COLUMN summary TEXT",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass

def init_db():
    _migrate()
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            agent_name TEXT NOT NULL,
            model TEXT,
            content TEXT NOT NULL,
            summary TEXT,
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
        CREATE TABLE IF NOT EXISTS consensus_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            price_at TEXT,
            basis TEXT,
            verified INTEGER DEFAULT 0,
            result TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS virtual_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            entry_price REAL,
            quantity REAL,
            amount REAL,
            target_price REAL,
            stop_loss REAL,
            target_date TEXT,
            reasoning TEXT,
            status TEXT DEFAULT 'open',
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            outcome TEXT,
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS agent_portfolio (
            agent_name TEXT PRIMARY KEY,
            balance REAL DEFAULT 10000000,
            total_pnl REAL DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS shared_portfolio (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL DEFAULT 30000000,
            total_pnl REAL DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (1, 30000000);
        """)

def save_message(round_id, agent_name, model, content, summary=None):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO messages (round_id, agent_name, model, content, summary) VALUES (?,?,?,?,?)",
            (round_id, agent_name, model, content, summary)
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

def complete_position(pos_id, exit_price, outcome_note):
    """close_position의 별칭 + 공유 포트폴리오 업데이트."""
    result = close_position(pos_id, exit_price, outcome_note)
    if result:
        update_shared_portfolio(result["pnl"])
    return result

def save_market_snapshot(data_json):
    with _conn() as con:
        cur = con.execute("INSERT INTO market_snapshots (data) VALUES (?)", (data_json,))
        return cur.lastrowid

def save_consensus(round_id, content):
    with _conn() as con:
        con.execute(
            "INSERT INTO consensus_notes (round_id, content) VALUES (?,?)",
            (round_id, content)
        )

def get_consensus_notes(limit=20):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM consensus_notes ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

def save_prediction(agent_name, symbol, direction, price_at, basis):
    with _conn() as con:
        con.execute(
            "INSERT INTO predictions (agent_name, symbol, direction, price_at, basis) VALUES (?,?,?,?,?)",
            (agent_name, symbol, direction, str(price_at), basis)
        )

def get_unverified_predictions():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM predictions WHERE verified=0 ORDER BY created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

def mark_prediction_verified(pred_id, result):
    with _conn() as con:
        con.execute(
            "UPDATE predictions SET verified=1, result=? WHERE id=?",
            (result, pred_id)
        )

def open_position(agent_name, symbol, direction, entry_price, amount,
                  target_price, stop_loss, target_date, reasoning):
    quantity = round(amount / entry_price, 4) if entry_price else 0
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO virtual_positions
            (agent_name, symbol, direction, entry_price, quantity, amount,
             target_price, stop_loss, target_date, reasoning)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (agent_name, symbol, direction, entry_price, quantity, amount,
              target_price, stop_loss, target_date, reasoning))
        return cur.lastrowid

def get_open_positions(agent_name=None):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if agent_name:
            rows = con.execute(
                "SELECT * FROM virtual_positions WHERE status='open' AND agent_name=? ORDER BY opened_at",
                (agent_name,)).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM virtual_positions WHERE status='open' ORDER BY opened_at").fetchall()
        return [dict(r) for r in rows]

def close_position(pos_id, exit_price, outcome_note):
    with _conn() as con:
        row = dict(con.execute(
            "SELECT * FROM virtual_positions WHERE id=?", (pos_id,)).fetchone() or {})
        if not row:
            return None
        entry = row.get("entry_price", 0) or 0
        qty = row.get("quantity", 0) or 0
        direction = row.get("direction", "매수")
        if entry and qty:
            if direction == "매수":
                pnl = (exit_price - entry) * qty
                pnl_pct = (exit_price - entry) / entry * 100
            else:
                pnl = (entry - exit_price) * qty
                pnl_pct = (entry - exit_price) / entry * 100
        else:
            pnl, pnl_pct = 0, 0
        con.execute("""
            UPDATE virtual_positions SET status='closed', exit_price=?, pnl=?, pnl_pct=?,
            outcome=?, closed_at=CURRENT_TIMESTAMP WHERE id=?
        """, (exit_price, round(pnl, 0), round(pnl_pct, 2), outcome_note, pos_id))
        _update_portfolio(con, row["agent_name"], pnl)
        return {"pnl": pnl, "pnl_pct": pnl_pct, "outcome": outcome_note}

def _update_portfolio(con, agent_name, pnl):
    con.execute("""
        INSERT INTO agent_portfolio (agent_name, balance, total_pnl, win_count, loss_count)
        VALUES (?, 10000000 + ?, ?, ?, ?)
        ON CONFLICT(agent_name) DO UPDATE SET
            balance = balance + ?,
            total_pnl = total_pnl + ?,
            win_count = win_count + ?,
            loss_count = loss_count + ?,
            updated_at = CURRENT_TIMESTAMP
    """, (agent_name, pnl, max(0, pnl), 1 if pnl >= 0 else 0, 1 if pnl < 0 else 0,
          pnl, pnl, 1 if pnl >= 0 else 0, 1 if pnl < 0 else 0))

def get_shared_portfolio():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM shared_portfolio WHERE id=1").fetchone()
        return dict(row) if row else {"balance": 30000000, "total_pnl": 0, "win_count": 0, "loss_count": 0}

def update_shared_portfolio(pnl: float):
    with _conn() as con:
        con.execute("""UPDATE shared_portfolio SET
            balance = balance + ?,
            total_pnl = total_pnl + ?,
            win_count = win_count + ?,
            loss_count = loss_count + ?,
            updated_at = CURRENT_TIMESTAMP
            WHERE id = 1""",
            (pnl, pnl, 1 if pnl >= 0 else 0, 1 if pnl < 0 else 0))

def get_portfolio(agent_name=None):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if agent_name:
            row = con.execute("SELECT * FROM agent_portfolio WHERE agent_name=?", (agent_name,)).fetchone()
            return dict(row) if row else {"agent_name": agent_name, "balance": 10000000, "total_pnl": 0, "win_count": 0, "loss_count": 0}
        rows = con.execute("SELECT * FROM agent_portfolio ORDER BY total_pnl DESC").fetchall()
        return [dict(r) for r in rows]

def get_all_positions(limit=20):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM virtual_positions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def get_latest_market_snapshot():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM market_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
