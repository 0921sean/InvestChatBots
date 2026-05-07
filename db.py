import sqlite3
import os

INITIAL_BALANCE = 100_000_000  # 1억


def _conn():
    path = os.getenv("DB_PATH", "investchat.db")
    return sqlite3.connect(path)


def _migrate():
    with _conn() as con:
        for sql in [
            "ALTER TABLE messages ADD COLUMN summary TEXT",
            "ALTER TABLE messages ADD COLUMN translation TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN code TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN market TEXT DEFAULT 'KRX'",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass


def init_db():
    _migrate()
    with _conn() as con:
        con.executescript(f"""
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
        CREATE TABLE IF NOT EXISTS blog_posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_id TEXT NOT NULL,
            log_no TEXT NOT NULL,
            title TEXT,
            post_date TEXT,
            content TEXT,
            fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(blog_id, log_no)
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
        CREATE TABLE IF NOT EXISTS virtual_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            code TEXT,
            direction TEXT NOT NULL DEFAULT '매수',
            entry_price REAL,
            quantity REAL,
            amount REAL,
            reasoning TEXT,
            status TEXT DEFAULT 'open',
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            opened_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            closed_at DATETIME
        );
        CREATE TABLE IF NOT EXISTS shared_portfolio (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL DEFAULT {INITIAL_BALANCE},
            invested REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (1, {INITIAL_BALANCE});
        CREATE TABLE IF NOT EXISTS agent_state (
            agent_name TEXT PRIMARY KEY,
            evolution_notes TEXT DEFAULT '',
            round_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS daily_analysis_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, sector_name, stock_name)
        );
        CREATE TABLE IF NOT EXISTS cycle_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            sector_idx INTEGER DEFAULT 0,
            phase TEXT DEFAULT 'sector_discussion',
            stock_idx INTEGER DEFAULT 0,
            discussion_round INTEGER DEFAULT 0,
            cycle_done_at REAL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO cycle_state (id) VALUES (1);
        """)


# ── 기본 메시지/라운드 ─────────────────────────────────────
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


# ── 포트폴리오 ────────────────────────────────────────────
def get_shared_portfolio():
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM shared_portfolio WHERE id=1").fetchone()
        return dict(row) if row else {
            "balance": INITIAL_BALANCE, "invested": 0,
            "total_pnl": 0, "win_count": 0, "loss_count": 0
        }


def buy_shared_position(symbol: str, code: str, entry_price: float, amount: float,
                        reasoning: str, market: str = "KRX"):
    """잔액에서 amount 차감 후 포지션 오픈. (pos_id, error) 반환."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT balance FROM shared_portfolio WHERE id=1").fetchone()
        if not row or row["balance"] < amount:
            return None, f"잔액 부족 (보유: ₩{row['balance']:,.0f})" if row else "포트폴리오 없음"

        quantity = round(amount / entry_price, 4) if entry_price else 0
        cur = con.execute("""
            INSERT INTO virtual_positions (symbol, code, direction, entry_price, quantity, amount, reasoning, market)
            VALUES (?, ?, '매수', ?, ?, ?, ?, ?)
        """, (symbol, code, entry_price, quantity, amount, reasoning, market))

        con.execute("""
            UPDATE shared_portfolio SET
                balance = balance - ?,
                invested = invested + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (amount, amount))

        return cur.lastrowid, None


def sell_shared_position(pos_id: int, exit_price: float):
    """포지션 청산 후 잔액에 반환. (pnl, error) 반환."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM virtual_positions WHERE id=?", (pos_id,)).fetchone()
        if not row:
            return None, "포지션 없음"
        row = dict(row)
        if row["status"] != "open":
            return None, "이미 청산된 포지션"

        entry = row["entry_price"] or 0
        qty = row["quantity"] or 0
        amount = row["amount"] or 0

        if entry and qty:
            pnl = (exit_price - entry) * qty
            pnl_pct = (exit_price - entry) / entry * 100
        else:
            pnl, pnl_pct = 0.0, 0.0

        return_amount = amount + pnl  # 원금 + 손익

        con.execute("""
            UPDATE virtual_positions SET
                status='closed', exit_price=?, pnl=?, pnl_pct=?, closed_at=CURRENT_TIMESTAMP
            WHERE id=?
        """, (exit_price, round(pnl, 0), round(pnl_pct, 2), pos_id))

        con.execute("""
            UPDATE shared_portfolio SET
                balance = balance + ?,
                invested = MAX(0, invested - ?),
                total_pnl = total_pnl + ?,
                win_count = win_count + ?,
                loss_count = loss_count + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
        """, (return_amount, amount, pnl, 1 if pnl >= 0 else 0, 1 if pnl < 0 else 0))

        return pnl, None


def get_open_positions_by_symbol(symbol: str) -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM virtual_positions WHERE symbol=? AND status='open'",
            (symbol,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_positions(limit=30) -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM virtual_positions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def log_stock_analyzed(sector_name: str, stock_name: str):
    """오늘 분석한 종목 기록. 중복 무시."""
    from datetime import date
    today = date.today().isoformat()
    with _conn() as con:
        try:
            con.execute(
                "INSERT OR IGNORE INTO daily_analysis_log (date, sector_name, stock_name) VALUES (?,?,?)",
                (today, sector_name, stock_name)
            )
        except Exception:
            pass


def was_stock_analyzed_today(sector_name: str, stock_name: str) -> bool:
    from datetime import date
    today = date.today().isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM daily_analysis_log WHERE date=? AND sector_name=? AND stock_name=?",
            (today, sector_name, stock_name)
        ).fetchone()
    return row is not None


def get_today_analyzed_stocks() -> list[tuple[str, str]]:
    from datetime import date
    today = date.today().isoformat()
    with _conn() as con:
        rows = con.execute(
            "SELECT sector_name, stock_name FROM daily_analysis_log WHERE date=? ORDER BY analyzed_at",
            (today,)
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def save_blog_post(blog_id: str, log_no: str, title: str, post_date: str, content: str):
    with _conn() as con:
        try:
            con.execute("""
                INSERT OR IGNORE INTO blog_posts (blog_id, log_no, title, post_date, content)
                VALUES (?, ?, ?, ?, ?)
            """, (blog_id, log_no, title, post_date, content))
        except Exception:
            pass


def blog_post_exists(blog_id: str, log_no: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM blog_posts WHERE blog_id=? AND log_no=?", (blog_id, log_no)
        ).fetchone()
    return row is not None


def get_recent_blog_posts(since: str, limit: int = 30) -> list[dict]:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT blog_id, log_no, title, post_date, content
            FROM blog_posts
            WHERE post_date >= ?
            ORDER BY post_date DESC, id DESC
            LIMIT ?
        """, (since, limit)).fetchall()
    return [dict(r) for r in rows]


def get_blog_post_count(blog_id: str) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) FROM blog_posts WHERE blog_id=?", (blog_id,)
        ).fetchone()
    return row[0] if row else 0


def get_evolution_notes(agent_name: str) -> str:
    with _conn() as con:
        row = con.execute(
            "SELECT evolution_notes FROM agent_state WHERE agent_name=?", (agent_name,)
        ).fetchone()
        return row[0] if row and row[0] else ""


def save_evolution_notes(agent_name: str, notes: str):
    with _conn() as con:
        con.execute("""
            INSERT INTO agent_state (agent_name, evolution_notes, round_count)
            VALUES (?, ?, 1)
            ON CONFLICT(agent_name) DO UPDATE SET
                evolution_notes = excluded.evolution_notes,
                updated_at = CURRENT_TIMESTAMP
        """, (agent_name, notes))


def save_cycle_state(sector_idx: int, phase: str, stock_idx: int,
                     discussion_round: int, cycle_done_at: float):
    with _conn() as con:
        con.execute("""
            INSERT INTO cycle_state (id, sector_idx, phase, stock_idx, discussion_round, cycle_done_at, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                sector_idx=excluded.sector_idx,
                phase=excluded.phase,
                stock_idx=excluded.stock_idx,
                discussion_round=excluded.discussion_round,
                cycle_done_at=excluded.cycle_done_at,
                updated_at=excluded.updated_at
        """, (sector_idx, phase, stock_idx, discussion_round, cycle_done_at))


def load_cycle_state() -> dict:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM cycle_state WHERE id=1").fetchone()
        if row:
            return dict(row)
    return {"sector_idx": 0, "phase": "sector_discussion",
            "stock_idx": 0, "discussion_round": 0, "cycle_done_at": 0.0}


def get_open_positions() -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM virtual_positions WHERE status='open' ORDER BY opened_at"
        ).fetchall()
        return [dict(r) for r in rows]
