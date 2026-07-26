import sqlite3
import os

TOTAL_BALANCE = 100_000_000    # (레거시) 초기 총액 기준값 — 2026-07-27 두 계좌 독립 운용으로 '총액 통일' 폐기
LONG_RATIO = 0.75              # 장기 시드 비율 (INITIAL_BALANCE 산출용)
INITIAL_BALANCE = int(TOTAL_BALANCE * LONG_RATIO)     # 장기(메인) 시드 = 7,500만 (불변)
TRADING_BALANCE = 100_000_000  # 트레이딩(서브) 시드 = 1억 (2026-07-27 증액: 전략 포텐셜 테스트, MAX_POSITIONS 33과 세트) → 총 시스템 1.75억

# 계좌 구분: main=장기(메인 사이클) / sub=트레이딩(서브 사이클)
_ACCT_ID = {"main": 1, "sub": 2}


def _acct_id(account: str) -> int:
    return _ACCT_ID.get(account or "main", 1)


def _conn():
    path = os.getenv("DB_PATH", "investchat.db")
    return sqlite3.connect(path)


def _migrate_accounts(con):
    """shared_portfolio를 다계좌(메인/트레이딩)로 전환 — CHECK(id=1) 제거 + 서브 계좌 시드.
    idempotent: CHECK 없으면 시드 INSERT만 수행."""
    # 주의: INSERT OR IGNORE는 CHECK 위반도 '무시'(예외 X)하므로,
    # 제약 존재 여부를 스키마(sql)에서 직접 읽어 판단한다.
    ddl_row = con.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='shared_portfolio'"
    ).fetchone()
    if ddl_row and "CHECK" in (ddl_row[0] or ""):
        # 제약 제거 위해 테이블 재생성 (데이터 보존)
        con.executescript(f"""
            CREATE TABLE shared_portfolio_new (
                id INTEGER PRIMARY KEY,
                balance REAL DEFAULT {INITIAL_BALANCE},
                invested REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO shared_portfolio_new SELECT * FROM shared_portfolio;
            DROP TABLE shared_portfolio;
            ALTER TABLE shared_portfolio_new RENAME TO shared_portfolio;
        """)
    con.execute("INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (1, ?)",
                (INITIAL_BALANCE,))
    con.execute("INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (2, ?)",
                (TRADING_BALANCE,))


def _migrate():
    with _conn() as con:
        for sql in [
            "ALTER TABLE messages ADD COLUMN summary TEXT",
            "ALTER TABLE messages ADD COLUMN translation TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN code TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN market TEXT DEFAULT 'KRX'",
            "ALTER TABLE virtual_positions ADD COLUMN exit_reasoning TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN account TEXT DEFAULT 'main'",
            "ALTER TABLE virtual_positions ADD COLUMN strategy TEXT",       # 트레이딩 규칙엔진: momentum/meanrev
            "ALTER TABLE virtual_positions ADD COLUMN stop_price REAL",     # 모멘텀 손절가(진입 시점 최근 저점)
            "ALTER TABLE virtual_positions ADD COLUMN half_exited INTEGER DEFAULT 0",  # 모멘텀 v2 절반익절 여부
            "ALTER TABLE cycle_state ADD COLUMN market TEXT DEFAULT 'KRX'",
            "ALTER TABLE visit_log ADD COLUMN verified INTEGER DEFAULT 0",
        ]:
            try:
                con.execute(sql)
            except Exception:
                pass
        try:
            con.execute("UPDATE virtual_positions SET account='main' WHERE account IS NULL")
            _migrate_accounts(con)
        except Exception:
            pass


def save_benchmark_snapshot(date: str, bot_nav, qqq, schd, kospi, kosdaq):
    """하루 1개 벤치마크 스냅샷(봇 NAV + 지수). 같은 날 재호출은 값 갱신(UPSERT)."""
    with _conn() as con:
        con.execute("""
            INSERT INTO benchmark_daily (date, bot_nav, qqq, schd, kospi, kosdaq)
            VALUES (?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                bot_nav=COALESCE(excluded.bot_nav, bot_nav),
                qqq=COALESCE(excluded.qqq, qqq), schd=COALESCE(excluded.schd, schd),
                kospi=COALESCE(excluded.kospi, kospi), kosdaq=COALESCE(excluded.kosdaq, kosdaq)
        """, (date, bot_nav, qqq, schd, kospi, kosdaq))


def get_benchmark_snapshots() -> list:
    """날짜 오름차순 전체 스냅샷 (baseline=첫, latest=끝)."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM benchmark_daily ORDER BY date").fetchall()]


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
        CREATE TABLE IF NOT EXISTS benchmark_daily (
            date TEXT PRIMARY KEY,
            bot_nav REAL, qqq REAL, schd REAL, kospi REAL, kosdaq REAL,
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
            closed_at DATETIME,
            account TEXT DEFAULT 'main'
        );
        CREATE TABLE IF NOT EXISTS shared_portfolio (
            id INTEGER PRIMARY KEY,
            balance REAL DEFAULT {INITIAL_BALANCE},
            invested REAL DEFAULT 0,
            total_pnl REAL DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (1, {INITIAL_BALANCE});
        INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (2, {TRADING_BALANCE});
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
            verified INTEGER DEFAULT 0,
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
            market TEXT DEFAULT 'KRX',
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
        CREATE TABLE IF NOT EXISTS token_capacity (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            estimated_max_cost_usd REAL DEFAULT 0,
            last_calibrated_at DATETIME,
            last_known_pct REAL DEFAULT 0,
            last_known_cost_usd REAL DEFAULT 0,
            notes TEXT
        );
        INSERT OR IGNORE INTO token_capacity (id, estimated_max_cost_usd) VALUES (1, 0);
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            visitor_id TEXT,
            user_agent TEXT,
            referer TEXT,
            is_read INTEGER DEFAULT 0,
            ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_feedback_ts ON feedback(ts);

        CREATE TABLE IF NOT EXISTS chat_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            visitor_id TEXT,
            status TEXT DEFAULT 'pending',   -- pending | processing | done | error
            day TEXT NOT NULL,               -- KST 날짜 'YYYY-MM-DD' (일일 캡 카운트 기준)
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            done_at DATETIME
        );
        CREATE INDEX IF NOT EXISTS idx_chat_queue_status ON chat_queue(status, id);
        CREATE INDEX IF NOT EXISTS idx_chat_queue_day ON chat_queue(day);
        """)


# ── 채팅 질문 큐 (안전장치 1/3: 하드 캡 + FIFO) ──────────────
def _kst_date() -> str:
    """오늘 KST 날짜 'YYYY-MM-DD' — 일일 캡 카운트 기준."""
    from datetime import datetime, timezone, timedelta
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


def enqueue_chat_question(content: str, visitor_id: str = None, daily_cap: int = 30) -> dict:
    """오늘 캡 미달이면 큐에 넣고 순번 반환, 도달이면 거부.
    반환: {accepted, closed, queue_id, position, today_count, cap, remaining}.
    거부는 INSERT하지 않으므로 today_count = day별 row 수와 동일."""
    day = _kst_date()
    with _conn() as con:
        today_count = con.execute(
            "SELECT COUNT(*) FROM chat_queue WHERE day=?", (day,)
        ).fetchone()[0]
        if today_count >= daily_cap:
            return {"accepted": False, "closed": True, "queue_id": None,
                    "position": None, "today_count": today_count,
                    "cap": daily_cap, "remaining": 0}
        cur = con.execute(
            "INSERT INTO chat_queue (content, visitor_id, status, day) VALUES (?, ?, 'pending', ?)",
            (content, visitor_id, day))
        qid = cur.lastrowid
        # 순번 = 아직 안 끝난(pending/processing) 질문 중 내 id 이하 개수
        position = con.execute(
            "SELECT COUNT(*) FROM chat_queue WHERE status IN ('pending','processing') AND id<=?",
            (qid,)).fetchone()[0]
        new_count = today_count + 1
        return {"accepted": True, "closed": False, "queue_id": qid,
                "position": position, "today_count": new_count,
                "cap": daily_cap, "remaining": max(0, daily_cap - new_count)}


def claim_next_chat_question() -> dict | None:
    """가장 오래된 pending 1건을 processing으로 잠그고 반환 (FIFO). 없으면 None.
    단일 워커 전용."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM chat_queue WHERE status='pending' ORDER BY id LIMIT 1"
        ).fetchone()
        if not row:
            return None
        con.execute("UPDATE chat_queue SET status='processing' WHERE id=?", (row["id"],))
        return dict(row)


def complete_chat_question(qid: int, status: str = "done"):
    """처리 끝난 질문을 done(또는 error)으로 마감."""
    with _conn() as con:
        con.execute(
            "UPDATE chat_queue SET status=?, done_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, qid))


def get_chat_queue_status(daily_cap: int = 30, max_list: int = 20) -> dict:
    """캡 현황 + 대기 목록(처리중 1건 포함, 순번 부여) 공개용."""
    day = _kst_date()
    with _conn() as con:
        con.row_factory = sqlite3.Row
        today_count = con.execute(
            "SELECT COUNT(*) FROM chat_queue WHERE day=?", (day,)).fetchone()[0]
        processing = con.execute(
            "SELECT id, content FROM chat_queue WHERE status='processing' ORDER BY id LIMIT 1"
        ).fetchone()
        pend_rows = con.execute(
            "SELECT id, content FROM chat_queue WHERE status='pending' ORDER BY id LIMIT ?",
            (max_list,)).fetchall()
    waiting = ([processing] if processing else []) + list(pend_rows)
    queue = [{"position": i, "preview": (w["content"] or "")[:40]}
             for i, w in enumerate(waiting, start=1)]
    return {
        "cap": daily_cap,
        "today_count": today_count,
        "remaining": max(0, daily_cap - today_count),
        "closed": today_count >= daily_cap,
        "waiting": len(waiting),
        "queue": queue,
    }


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


def get_user_chats(limit: int = 50) -> list[dict]:
    """최근 사용자 채팅 라운드 — 질문 + 봇 답변만 (owner 검토용). 최신순.
    System 메시지(종목 데이터·합의 알림)는 제외."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rounds = con.execute(
            "SELECT id, started_at FROM rounds WHERE topic='user-message' ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
        out = []
        for r in rounds:
            msgs = con.execute(
                "SELECT agent_name, content, timestamp FROM messages WHERE round_id=? ORDER BY id",
                (r["id"],)
            ).fetchall()
            question, ts, answers = "", r["started_at"], []
            for m in msgs:
                if m["agent_name"] == "User":
                    question = m["content"]
                    ts = m["timestamp"] or ts
                elif m["agent_name"] != "System":
                    answers.append({"agent": m["agent_name"], "content": m["content"]})
            out.append({"round_id": r["id"], "question": question, "ts": ts, "answers": answers})
        return out


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
def get_shared_portfolio(account: str = "main"):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM shared_portfolio WHERE id=?", (_acct_id(account),)).fetchone()
        return dict(row) if row else {
            "balance": INITIAL_BALANCE if account == "main" else TRADING_BALANCE, "invested": 0,
            "total_pnl": 0, "win_count": 0, "loss_count": 0
        }


def buy_shared_position(symbol: str, code: str, entry_price: float, amount: float,
                        reasoning: str, market: str = "KRX", account: str = "main"):
    """해당 계좌 잔액에서 amount 차감 후 포지션 오픈. (pos_id, error) 반환."""
    aid = _acct_id(account)
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT balance FROM shared_portfolio WHERE id=?", (aid,)).fetchone()
        if not row or row["balance"] < amount:
            return None, f"잔액 부족 (보유: ₩{row['balance']:,.0f})" if row else "포트폴리오 없음"

        quantity = round(amount / entry_price, 4) if entry_price else 0
        cur = con.execute("""
            INSERT INTO virtual_positions (symbol, code, direction, entry_price, quantity, amount, reasoning, market, account)
            VALUES (?, ?, '매수', ?, ?, ?, ?, ?, ?)
        """, (symbol, code, entry_price, quantity, amount, reasoning, market, account))

        con.execute("""
            UPDATE shared_portfolio SET
                balance = balance - ?,
                invested = invested + ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (amount, amount, aid))

        return cur.lastrowid, None


def sell_shared_position(pos_id: int, exit_price: float, exit_reasoning: str = ""):
    """포지션 청산 후 해당 계좌 잔액에 반환. (pnl, error) 반환."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM virtual_positions WHERE id=?", (pos_id,)).fetchone()
        if not row:
            return None, "포지션 없음"
        row = dict(row)
        if row["status"] != "open":
            return None, "이미 청산된 포지션"
        aid = _acct_id(row.get("account") or "main")

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
            WHERE id = ?
        """, (return_amount, amount, pnl, 1 if pnl >= 0 else 0, 1 if pnl < 0 else 0, aid))

        return pnl, None


def sell_partial(pos_id: int, fraction: float, exit_price: float, exit_reasoning: str = ""):
    """포지션의 fraction(0~1)만 부분 청산. 잔량은 유지(half_exited=1). (실현손익, error) 반환.
    fraction>=1이면 전량 청산으로 위임."""
    if fraction >= 0.999:
        return sell_shared_position(pos_id, exit_price, exit_reasoning)
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM virtual_positions WHERE id=?", (pos_id,)).fetchone()
        if not row:
            return None, "포지션 없음"
        row = dict(row)
        if row["status"] != "open":
            return None, "이미 청산된 포지션"
        aid = _acct_id(row.get("account") or "main")
        entry = row["entry_price"] or 0
        qty = row["quantity"] or 0
        amount = row["amount"] or 0

        sold_qty = qty * fraction
        sold_amount = amount * fraction               # 매도분 원금(원가)
        pnl = (exit_price - entry) * sold_qty if entry else 0.0
        return_amount = sold_amount + pnl             # 원금 + 실현손익

        # 포지션 축소(잔량 유지) + 절반익절 플래그
        con.execute("""
            UPDATE virtual_positions SET
                quantity = quantity - ?, amount = amount - ?, half_exited = 1,
                exit_reasoning = COALESCE(exit_reasoning,'') || ?
            WHERE id=?
        """, (round(sold_qty, 4), round(sold_amount, 0), f"[부분청산 {int(fraction*100)}%: {exit_reasoning}] ", pos_id))
        # 계좌 반영(부분매도는 win/loss 카운트 안 함 — 전량 청산 시 1회 집계)
        con.execute("""
            UPDATE shared_portfolio SET
                balance = balance + ?, invested = MAX(0, invested - ?),
                total_pnl = total_pnl + ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (return_amount, sold_amount, pnl, aid))
        return pnl, None


def get_open_positions_by_symbol(symbol: str, account: str = None) -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if account:
            rows = con.execute(
                "SELECT * FROM virtual_positions WHERE symbol=? AND status='open' AND account=?",
                (symbol, account)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM virtual_positions WHERE symbol=? AND status='open'",
                (symbol,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_all_positions(limit=30, account: str = None) -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if account:
            rows = con.execute(
                "SELECT * FROM virtual_positions WHERE account=? ORDER BY id DESC LIMIT ?",
                (account, limit)
            ).fetchall()
        else:
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
                     discussion_round: int, cycle_done_at: float,
                     market: str = "KRX"):
    with _conn() as con:
        con.execute("""
            INSERT INTO cycle_state (id, sector_idx, phase, stock_idx, discussion_round, cycle_done_at, market, updated_at)
            VALUES (1, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                sector_idx=excluded.sector_idx,
                phase=excluded.phase,
                stock_idx=excluded.stock_idx,
                discussion_round=excluded.discussion_round,
                cycle_done_at=excluded.cycle_done_at,
                market=excluded.market,
                updated_at=excluded.updated_at
        """, (sector_idx, phase, stock_idx, discussion_round, cycle_done_at, market))


def load_cycle_state() -> dict:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM cycle_state WHERE id=1").fetchone()
        if row:
            return dict(row)
    return {"sector_idx": 0, "phase": "sector_discussion",
            "stock_idx": 0, "discussion_round": 0, "cycle_done_at": 0.0,
            "market": "KRX"}


def get_open_positions(market: str = None, account: str = None) -> list:
    clauses = ["status='open'"]
    params = []
    if market:
        clauses.append("market=?"); params.append(market)
    if account:
        clauses.append("account=?"); params.append(account)
    sql = "SELECT * FROM virtual_positions WHERE " + " AND ".join(clauses) + " ORDER BY opened_at"
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(sql, params).fetchall()]


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
# 같은 (visitor_id, source) 조합 → VISIT_DEDUP_SECONDS 이내는 1건으로 합침
# (너무 짧으면 polling·연타로 부풀려지고, 너무 길면 PV가 안 늘어 의미 없음)
VISIT_DEDUP_SECONDS = 60  # 1분

# 봇/스크립트 UA 키워드 — 사람 아님, 방문 통계에서 제외 (direct 왜곡 방지)
_BOT_UA_KEYWORDS = ("bot", "crawl", "spider", "python", "curl", "wget",
                    "go-http", "headlesschrome", "scrapy", "okhttp",
                    "java/", "postman", "facebookexternalhit", "slackbot",
                    "axios", "node-fetch", "libwww", "httpclient",
                    # 보안 스캐너·자동화 도구
                    "scanner", "audit", "palo alto", "paloalto", "expanse", "censys",
                    "masscan", "zgrab", "nmap", "dalvik", "aiohttp",
                    "uptime", "monitoring", "fetch", "http-client",
                    "nuclei", "wpscan", "sqlmap", "nikto", "gobuster", "feroxbuster",
                    "dirbuster", "shodan", "internetmeasurement", "l9scan", "l9tcpid",
                    "netcraft",
                    # HTTP 라이브러리·헤드리스
                    "httpx", "urllib", "requests", "winhttp", "powershell", "guzzle",
                    "restsharp", "phantomjs", "puppeteer", "playwright", "selenium",
                    "wkhtmltopdf", "lua-resty", "apache-httpclient", "wininet",
                    # SEO·분석·성능 봇
                    "ahrefs", "semrush", "mj12", "dataforseo", "lighthouse",
                    "pagespeed", "gtmetrix", "pingdom", "statuscake", "uptimerobot",
                    "site24x7", "newrelic", "datadog", "validator", "google-read-aloud",
                    # 링크 미리보기 스크레이퍼 (인앱 브라우저 아님 — 사람 아님)
                    "facebookcatalog", "kakaotalk-scrap", "skypeuripreview",
                    "embedly", "vkshare", "tumblr", "redditbot", "discordbot",
                    "telegrambot", "twitterbot", "whatsapp",
                    # 합성 봇팜 (구형 iOS 13.2.3 단일 UA 256개 — 사람 트래픽 아님)
                    "iphone os 13_2_3")
# 정확히 일치하면 봇 (플랫폼 정보 없는 깡통 UA)
_BOT_UA_EXACT = ("mozilla/5.0", "mozilla", "-")
# 통계 쿼리용 SQL 조건: 봇 UA가 아닌 행만 (빈/NULL UA도 봇 취급)
_NOT_BOT_SQL = ("user_agent IS NOT NULL AND TRIM(user_agent) != '' AND "
                + " AND ".join(f"LOWER(user_agent) NOT LIKE '%{k}%'"
                               for k in _BOT_UA_KEYWORDS)
                + " AND LOWER(TRIM(user_agent)) NOT IN ("
                + ",".join(f"'{k}'" for k in _BOT_UA_EXACT) + ")")


def _is_bot_ua(ua: str) -> bool:
    """봇/스크립트/빈 UA 여부."""
    if not ua or not ua.strip():
        return True
    u = ua.lower().strip()
    if u in _BOT_UA_EXACT:
        return True
    return any(k in u for k in _BOT_UA_KEYWORDS)


def _normalize_source(s) -> str:
    """방문 출처(?s=) 정규화 — 소문자·허용문자([a-z0-9_-])만·최대 32자.
    화이트리스트 없이 임의 태그 허용하되, 공백·특수문자·태그 등 오염/인젝션 문자는 제거.
    유효 문자가 하나도 없으면 'direct'."""
    import re
    s = re.sub(r'[^a-z0-9_-]', '', (s or "").strip().lower())
    return s[:32] or "direct"


def log_visit(source: str, visitor_id: str | None, user_agent: str = "",
              referer: str = "", path: str = "/", verified: int = 0) -> bool:
    """방문 1건 기록. 동일 (visitor_id, source)가 VISIT_DEDUP_SECONDS 이내면 skip.
    봇/스크립트 UA는 기록하지 않음. verified=1 → JS 비콘으로 검증된 실방문.
    반환값: 실제로 새로 기록했으면 True, 중복/봇으로 skip했으면 False."""
    if _is_bot_ua(user_agent):
        return False
    source = _normalize_source(source)   # 임의 태그 허용 + 정규화(오염 방지)
    with _conn() as con:
        if visitor_id:
            row = con.execute(f"""
                SELECT 1 FROM visit_log
                WHERE visitor_id=? AND COALESCE(source,'')=COALESCE(?,'')
                  AND ts > datetime('now', '-{VISIT_DEDUP_SECONDS} seconds')
                LIMIT 1
            """, (visitor_id, source)).fetchone()
            if row:
                return False
        con.execute("""
            INSERT INTO visit_log (source, visitor_id, user_agent, referer, path, verified)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (source or None, visitor_id, user_agent[:200], referer[:300], path[:200], verified))
        return True


def purge_visits_for(visitor_id: str) -> int:
    """특정 visitor_id의 방문 기록 전부 삭제 (owner 본인 방문 지표 정리용). 삭제 건수 반환."""
    if not visitor_id:
        return 0
    with _conn() as con:
        cur = con.execute("DELETE FROM visit_log WHERE visitor_id=?", (visitor_id,))
        return cur.rowcount


def get_visit_stats(days: int = 30, verified_only: bool = True) -> dict:
    """방문 통계 — 총합 / 출처별 / 날짜별(신규·재방문). KST 기준.
    재방문 판정: visitor_id가 '서로 다른 날(KST)' 2일 이상 방문 → 재방문자.
    날짜별 신규/재방문은 전체 기간 기준 첫 방문일로 판정.
    verified_only=True → JS 비콘 검증 실방문만(전환형). False → 봇 UA필터만(구 방식)."""
    nb = f"({_NOT_BOT_SQL})" + (" AND verified=1" if verified_only else "")
    win = "date(ts,'+9 hours') >= date('now','+9 hours','-' || ? || ' day')"
    with _conn() as con:
        con.row_factory = sqlite3.Row
        totals = con.execute(f"""
            SELECT COUNT(DISTINCT visitor_id) AS uv, COUNT(*) AS pv
            FROM visit_log WHERE {nb} AND {win}
        """, (days,)).fetchone()
        by_source = con.execute(f"""
            SELECT COALESCE(source,'direct') AS source,
                   COUNT(DISTINCT visitor_id) AS uv, COUNT(*) AS pv
            FROM visit_log WHERE {nb} AND {win}
            GROUP BY COALESCE(source,'direct')
            ORDER BY uv DESC
        """, (days,)).fetchall()
        # 날짜별 신규/재방문 + PV (전체기간 첫방문일 기준)
        by_day = con.execute(f"""
            WITH vd AS (
                SELECT visitor_id, date(ts,'+9 hours') AS d
                FROM visit_log WHERE {nb}
                GROUP BY visitor_id, date(ts,'+9 hours')
            ),
            firsts AS (SELECT visitor_id, MIN(d) AS fd FROM vd GROUP BY visitor_id),
            pv AS (
                SELECT date(ts,'+9 hours') AS d, COUNT(*) AS pv
                FROM visit_log WHERE {nb}
                GROUP BY date(ts,'+9 hours')
            )
            SELECT vd.d AS day,
                   COUNT(DISTINCT vd.visitor_id) AS uv,
                   COUNT(DISTINCT CASE WHEN vd.d = f.fd THEN vd.visitor_id END) AS new_uv,
                   COUNT(DISTINCT CASE WHEN vd.d > f.fd THEN vd.visitor_id END) AS returning_uv,
                   MAX(COALESCE(pv.pv,0)) AS pv
            FROM vd
            JOIN firsts f ON vd.visitor_id = f.visitor_id
            LEFT JOIN pv ON pv.d = vd.d
            WHERE vd.d >= date('now','+9 hours','-' || ? || ' day')
            GROUP BY vd.d ORDER BY vd.d DESC
        """, (days,)).fetchall()
        # 재방문자 총합 (윈도우 내 활동 + 전체기간 2일 이상 방문)
        ret = con.execute(f"""
            WITH vd AS (
                SELECT visitor_id, date(ts,'+9 hours') AS d
                FROM visit_log WHERE {nb}
                GROUP BY visitor_id, date(ts,'+9 hours')
            )
            SELECT COUNT(*) AS ruv FROM (
                SELECT visitor_id FROM vd GROUP BY visitor_id HAVING COUNT(*) >= 2
            ) r
            WHERE r.visitor_id IN (
                SELECT DISTINCT visitor_id FROM vd
                WHERE d >= date('now','+9 hours','-' || ? || ' day')
            )
        """, (days,)).fetchone()
        # ── 코호트 재방문: 첫 방문일(cohort) 기준 D1·D7 복귀 ──
        # D1 = 첫 방문 다음날(+1일) 복귀, D7 = 첫 방문 후 7일 내(+1~+7일) 복귀
        kst_today = con.execute("SELECT date('now','+9 hours')").fetchone()[0]
        cohort_rows = con.execute(f"""
            WITH vd AS (
                SELECT visitor_id, date(ts,'+9 hours') AS d
                FROM visit_log WHERE {nb}
                GROUP BY visitor_id, date(ts,'+9 hours')
            ),
            firsts AS (SELECT visitor_id, MIN(d) AS fd FROM vd GROUP BY visitor_id)
            SELECT f.fd AS cohort,
                   COUNT(DISTINCT f.visitor_id) AS cohort_size,
                   COUNT(DISTINCT CASE WHEN vd.d = date(f.fd,'+1 day') THEN f.visitor_id END) AS d1,
                   COUNT(DISTINCT CASE WHEN vd.d > f.fd AND vd.d <= date(f.fd,'+7 day') THEN f.visitor_id END) AS d7
            FROM firsts f
            JOIN vd ON vd.visitor_id = f.visitor_id
            WHERE f.fd >= date('now','+9 hours','-' || ? || ' day')
            GROUP BY f.fd ORDER BY f.fd DESC
        """, (days,)).fetchall()
        cohorts = [dict(r) for r in cohort_rows]

        from datetime import date as _date, timedelta as _td
        def _pd(s):
            y, m, dd = map(int, s.split('-')); return _date(y, m, dd)
        ty = _pd(kst_today)
        d1_cut = (ty - _td(days=1)).isoformat()   # 이 날짜 이하 코호트만 D1 측정 가능(성숙)
        d7_cut = (ty - _td(days=7)).isoformat()
        d1_base = sum(c["cohort_size"] for c in cohorts if c["cohort"] <= d1_cut)
        d1_ret  = sum(c["d1"] for c in cohorts if c["cohort"] <= d1_cut)
        d7_base = sum(c["cohort_size"] for c in cohorts if c["cohort"] <= d7_cut)
        d7_ret  = sum(c["d7"] for c in cohorts if c["cohort"] <= d7_cut)
        retention = {
            "d1_rate": round(d1_ret / d1_base * 100, 1) if d1_base else None,
            "d7_rate": round(d7_ret / d7_base * 100, 1) if d7_base else None,
            "d1_base": d1_base, "d1_ret": d1_ret,
            "d7_base": d7_base, "d7_ret": d7_ret,
        }

        uv = totals["uv"] or 0
        ruv = ret["ruv"] or 0
        return {
            "window_days": days,
            "today": kst_today,
            "totals": {
                "unique_visitors": uv,
                "page_views": totals["pv"],
                "returning_visitors": ruv,
                "returning_rate": round(ruv / uv * 100, 1) if uv else 0.0,
            },
            "retention": retention,
            "cohorts": cohorts,
            "by_source": [dict(r) for r in by_source],
            "by_day": [dict(r) for r in by_day],
        }


def get_buy_debate(pos_id: int) -> dict:
    """포지션의 매수 직전 라운드 토론 전체를 반환. opened_at에 가장 가까운
    '매수 체결' System 메시지의 라운드를 찾아 그 라운드 메시지를 순서대로 준다."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        pos = con.execute(
            "SELECT symbol, opened_at FROM virtual_positions WHERE id=?", (pos_id,)
        ).fetchone()
        if not pos:
            return {"found": False, "messages": []}
        sym = pos["symbol"]
        r = con.execute("""
            SELECT round_id FROM messages
            WHERE agent_name='System' AND content LIKE ?
            ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC, id DESC
            LIMIT 1
        """, (f"🟢 매수 체결: {sym}%", pos["opened_at"])).fetchone()
        if not r:
            return {"found": False, "symbol": sym, "messages": []}
        rid = r["round_id"]
        rows = con.execute(
            "SELECT agent_name, content, timestamp FROM messages WHERE round_id=? ORDER BY id",
            (rid,)
        ).fetchall()
        return {"found": True, "round_id": rid, "symbol": sym,
                "messages": [dict(x) for x in rows]}


# ── 피드백 ────────────────────────────────────────────────
def add_feedback(content: str, visitor_id: str = None,
                 user_agent: str = "", referer: str = "") -> int:
    """피드백 1건 저장. ID 반환."""
    with _conn() as con:
        cur = con.execute("""
            INSERT INTO feedback (content, visitor_id, user_agent, referer)
            VALUES (?, ?, ?, ?)
        """, (content[:5000], visitor_id, user_agent[:200], referer[:300]))
        return cur.lastrowid


def feedback_rate_limited(visitor_id: str, window_min: int = 5, max_n: int = 3) -> bool:
    """같은 visitor가 window_min 내 max_n 건 이상이면 True (차단)."""
    if not visitor_id:
        return False
    with _conn() as con:
        row = con.execute("""
            SELECT COUNT(*) FROM feedback
            WHERE visitor_id=? AND ts > datetime('now', '-' || ? || ' minutes')
        """, (visitor_id, window_min)).fetchone()
        return (row[0] if row else 0) >= max_n


def list_feedback(limit: int = 100, unread_only: bool = False) -> list[dict]:
    """피드백 목록 (최신순)."""
    with _conn() as con:
        import sqlite3
        con.row_factory = sqlite3.Row
        sql = "SELECT id, content, visitor_id, user_agent, is_read, ts FROM feedback"
        if unread_only:
            sql += " WHERE is_read=0"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = con.execute(sql, (limit,)).fetchall()
        return [dict(r) for r in rows]


def mark_feedback_read(fid: int = None):
    """fid 주면 그것만, 아니면 전체 읽음 처리."""
    with _conn() as con:
        if fid:
            con.execute("UPDATE feedback SET is_read=1 WHERE id=?", (fid,))
        else:
            con.execute("UPDATE feedback SET is_read=1 WHERE is_read=0")


def feedback_unread_count() -> int:
    with _conn() as con:
        row = con.execute("SELECT COUNT(*) FROM feedback WHERE is_read=0").fetchone()
        return row[0] if row else 0


# ── Claude 토큰 한도 추정 (사용자 calibrate) ───────────────
def get_token_capacity() -> dict:
    """현재 추정 최대 토큰 한도 + 마지막 calibrate 정보."""
    with _conn() as con:
        import sqlite3
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT * FROM token_capacity WHERE id=1").fetchone()
        return dict(row) if row else {"estimated_max_cost_usd": 0}


def calibrate_token_capacity(percent_used: float, current_cost: float, notes: str = "") -> dict:
    """사용자가 알려준 사용률 + 현재 누적 비용으로 최대 한도 추정.
    estimated_max = current_cost / (percent_used/100)
    예: 85% 사용 시점 누적 $170 → max ≈ $200
    """
    if percent_used <= 0 or percent_used > 100:
        raise ValueError("percent_used는 0~100 사이")
    est_max = current_cost / (percent_used / 100.0)
    with _conn() as con:
        con.execute("""
            UPDATE token_capacity
            SET estimated_max_cost_usd=?,
                last_calibrated_at=CURRENT_TIMESTAMP,
                last_known_pct=?,
                last_known_cost_usd=?,
                notes=?
            WHERE id=1
        """, (est_max, percent_used, current_cost, notes[:200]))
    return {
        "estimated_max_cost_usd": est_max,
        "last_known_pct": percent_used,
        "last_known_cost_usd": current_cost,
    }
