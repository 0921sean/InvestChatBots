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
            "ALTER TABLE virtual_positions ADD COLUMN exit_reasoning TEXT",
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
        CREATE TABLE IF NOT EXISTS blog_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            blog_id TEXT NOT NULL,
            log_no TEXT NOT NULL,
            post_date TEXT,
            stock_name TEXT,
            stock_code TEXT,
            mentioned_price REAL,
            judgment TEXT,
            excerpt TEXT,
            extracted_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE IF NOT EXISTS lecture_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
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
        CREATE TABLE IF NOT EXISTS visit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            visitor_id TEXT,
            user_agent TEXT,
            referer TEXT,
            path TEXT,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_visit_log_ts ON visit_log(ts);
        CREATE INDEX IF NOT EXISTS idx_visit_log_source ON visit_log(source);
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
        CREATE TABLE IF NOT EXISTS watched_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            code TEXT,
            market TEXT DEFAULT 'KR',
            source TEXT,
            first_seen DATE DEFAULT (date('now')),
            last_seen DATE DEFAULT (date('now')),
            mention_count INTEGER DEFAULT 1,
            UNIQUE(name)
        );
        CREATE TABLE IF NOT EXISTS member_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            group_id TEXT,
            messages_collected INTEGER DEFAULT 0,
            profile TEXT,
            notified INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS token_usage_daily (
            date TEXT PRIMARY KEY,
            calls INTEGER DEFAULT 0,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_create_tokens INTEGER DEFAULT 0,
            cost_usd REAL DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS donations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            note TEXT,
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)


def add_donation(amount: float, note: str = ""):
    with _conn() as con:
        con.execute("INSERT INTO donations (amount, note) VALUES (?, ?)", (amount, note))


def get_donations_total() -> dict:
    """누적 후원액 + 최근 5건."""
    with _conn() as con:
        import sqlite3
        con.row_factory = sqlite3.Row
        total = con.execute("SELECT COALESCE(SUM(amount), 0) AS s FROM donations").fetchone()["s"]
        rows = con.execute(
            "SELECT id, amount, note, added_at FROM donations ORDER BY id DESC LIMIT 5"
        ).fetchall()
        return {"total": total, "recent": [dict(r) for r in rows]}


def delete_donation(donation_id: int):
    with _conn() as con:
        con.execute("DELETE FROM donations WHERE id=?", (donation_id,))


# ── 토큰 사용량 트래킹 ──────────────────────────────────────
def record_token_usage(input_tokens: int, output_tokens: int,
                        cache_read: int = 0, cache_create: int = 0,
                        cost_usd: float = 0.0):
    """Claude CLI 1회 호출 분량을 오늘 누적값에 더한다."""
    with _conn() as con:
        con.execute("""
            INSERT INTO token_usage_daily
              (date, calls, input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd, updated_at)
            VALUES (date('now','localtime'), 1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(date) DO UPDATE SET
                calls = calls + 1,
                input_tokens = input_tokens + EXCLUDED.input_tokens,
                output_tokens = output_tokens + EXCLUDED.output_tokens,
                cache_read_tokens = cache_read_tokens + EXCLUDED.cache_read_tokens,
                cache_create_tokens = cache_create_tokens + EXCLUDED.cache_create_tokens,
                cost_usd = cost_usd + EXCLUDED.cost_usd,
                updated_at = CURRENT_TIMESTAMP
        """, (input_tokens, output_tokens, cache_read, cache_create, cost_usd))


def get_token_usage_today() -> dict:
    """오늘 누적 토큰 사용량 (없으면 0)."""
    with _conn() as con:
        import sqlite3
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM token_usage_daily WHERE date=date('now','localtime')"
        ).fetchone()
        if row:
            return dict(row)
        return {"date": None, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read_tokens": 0, "cache_create_tokens": 0, "cost_usd": 0.0,
                "updated_at": None}


def get_token_usage_recent(days: int = 7) -> list:
    """최근 N일 사용량 (날짜 내림차순)."""
    with _conn() as con:
        import sqlite3
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM token_usage_daily ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]


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
        rows = con.execute("""
            SELECT m.*, r.topic AS round_topic
            FROM messages m LEFT JOIN rounds r ON m.round_id = r.id
            WHERE m.id > ? ORDER BY m.id ASC LIMIT ?
        """, (since_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_recent_messages(n=30):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT m.*, r.topic AS round_topic
            FROM messages m LEFT JOIN rounds r ON m.round_id = r.id
            ORDER BY m.id DESC LIMIT ?
        """, (n,)).fetchall()
        return list(reversed([dict(r) for r in rows]))


def get_messages_for_round(round_id: int) -> list[dict]:
    """현재 round_id의 메시지만 반환 — 이전 토론 컨텍스트 오염 방지."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM messages WHERE round_id=? ORDER BY id", (round_id,)
        ).fetchall()
        return [dict(r) for r in rows]


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


def sell_shared_position(pos_id: int, exit_price: float, exit_reasoning: str = ""):
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
                status='closed', exit_price=?, pnl=?, pnl_pct=?, closed_at=CURRENT_TIMESTAMP,
                exit_reasoning=?
            WHERE id=?
        """, (exit_price, round(pnl, 0), round(pnl_pct, 2), exit_reasoning, pos_id))

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


def add_lecture_note(content: str):
    with _conn() as con:
        con.execute("INSERT INTO lecture_notes (content) VALUES (?)", (content,))


def get_active_lecture_notes(hours: int = 48) -> list[dict]:
    """최근 N시간 강의 노트 반환."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM lecture_notes
            WHERE created_at >= datetime('now', ?, 'localtime')
            ORDER BY created_at ASC
        """, (f'-{hours} hours',)).fetchall()
    return [dict(r) for r in rows]


def clear_lecture_notes():
    with _conn() as con:
        con.execute("DELETE FROM lecture_notes")


def save_blog_signal(blog_id: str, log_no: str, post_date: str,
                     stock_name: str, stock_code: str,
                     mentioned_price: float, judgment: str, excerpt: str):
    with _conn() as con:
        con.execute("""
            INSERT OR IGNORE INTO blog_signals
            (blog_id, log_no, post_date, stock_name, stock_code, mentioned_price, judgment, excerpt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (blog_id, log_no, post_date, stock_name, stock_code,
              mentioned_price, judgment, excerpt))


def get_matching_signals(stock_name: str, current_price: float,
                         tolerance: float = 0.15, limit: int = 5) -> list[dict]:
    """현재가 ±tolerance 범위 내 과거 블로그 신호."""
    low  = current_price * (1 - tolerance)
    high = current_price * (1 + tolerance)
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM blog_signals
            WHERE stock_name = ?
              AND mentioned_price BETWEEN ? AND ?
            ORDER BY post_date DESC
            LIMIT ?
        """, (stock_name, low, high, limit)).fetchall()
    return [dict(r) for r in rows]


def signal_already_extracted(blog_id: str, log_no: str, stock_name: str) -> bool:
    with _conn() as con:
        row = con.execute(
            "SELECT 1 FROM blog_signals WHERE blog_id=? AND log_no=? AND stock_name=?",
            (blog_id, log_no, stock_name)
        ).fetchone()
    return row is not None


def get_posts_mentioning(stock_name: str, limit: int = 20) -> list[dict]:
    """종목명이 제목이나 본문에 언급된 블로그 글."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM blog_posts
            WHERE (title LIKE ? OR content LIKE ?)
              AND content != ''
            ORDER BY post_date DESC
            LIMIT ?
        """, (f"%{stock_name}%", f"%{stock_name}%", limit)).fetchall()
    return [dict(r) for r in rows]


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


# ── 관심 종목 (영구 보관) ─────────────────────────────────
def upsert_watched_stock(name: str, code: str = None, market: str = "KR", source: str = ""):
    """종목 언급 시 관심 목록에 추가 또는 카운트 증가."""
    with _conn() as con:
        con.execute("""
            INSERT INTO watched_stocks (name, code, market, source, last_seen, mention_count)
            VALUES (?, ?, ?, ?, date('now'), 1)
            ON CONFLICT(name) DO UPDATE SET
                last_seen = date('now'),
                mention_count = mention_count + 1,
                code = COALESCE(EXCLUDED.code, code),
                source = EXCLUDED.source
        """, (name, code, market, source))


def get_watched_stocks(limit: int = 50) -> list[dict]:
    """관심 종목 목록 (최근 언급 순)."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT * FROM watched_stocks 
            ORDER BY last_seen DESC, mention_count DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


# ── 방문 로그 (출처 트래킹) ────────────────────────────────
def log_visit(source: str, visitor_id: str | None, user_agent: str = "",
              referer: str = "", path: str = "/") -> bool:
    """방문 1건 기록. 같은 visitor_id + 같은 source는 24h 내 중복 기록 안 함.
    반환값: 실제로 새로 기록했으면 True, 중복으로 skip했으면 False."""
    with _conn() as con:
        if visitor_id:
            row = con.execute("""
                SELECT 1 FROM visit_log
                WHERE visitor_id=? AND COALESCE(source,'')=COALESCE(?,'')
                  AND ts > datetime('now', '-1 day')
                LIMIT 1
            """, (visitor_id, source)).fetchone()
            if row:
                return False
        con.execute("""
            INSERT INTO visit_log (source, visitor_id, user_agent, referer, path)
            VALUES (?, ?, ?, ?, ?)
        """, (source or None, visitor_id, user_agent[:200], referer[:300], path[:200]))
        return True


def get_visit_stats(days: int = 30) -> dict:
    """방문 통계 — 출처별 / 일자별 / 총합. KST 기준."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        totals = con.execute("""
            SELECT COUNT(DISTINCT visitor_id) AS uv, COUNT(*) AS pv
            FROM visit_log
            WHERE ts > datetime('now', '-' || ? || ' day')
        """, (days,)).fetchone()
        by_source = con.execute("""
            SELECT COALESCE(source,'direct') AS source,
                   COUNT(DISTINCT visitor_id) AS uv,
                   COUNT(*) AS pv
            FROM visit_log
            WHERE ts > datetime('now', '-' || ? || ' day')
            GROUP BY COALESCE(source,'direct')
            ORDER BY uv DESC
        """, (days,)).fetchall()
        by_day = con.execute("""
            SELECT date(ts, '+9 hours') AS day,
                   COALESCE(source,'direct') AS source,
                   COUNT(DISTINCT visitor_id) AS uv,
                   COUNT(*) AS pv
            FROM visit_log
            WHERE ts > datetime('now', '-' || ? || ' day')
            GROUP BY day, COALESCE(source,'direct')
            ORDER BY day DESC, uv DESC
        """, (days,)).fetchall()
        return {
            "window_days": days,
            "totals": {"unique_visitors": totals["uv"], "page_views": totals["pv"]},
            "by_source": [dict(r) for r in by_source],
            "by_day": [dict(r) for r in by_day],
        }
