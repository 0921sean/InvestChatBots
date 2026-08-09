import sqlite3
import os

TOTAL_BALANCE = 100_000_000    # (레거시) 초기 총액 기준값 — 2026-07-27 두 계좌 독립 운용으로 '총액 통일' 폐기
LONG_RATIO = 0.75              # (레거시) 옛 장기 시드 비율 — 아래가 명시값이라 미사용
INITIAL_BALANCE = 70_000_000   # 장기(메인) 시드 = 7,000만 (2026-08-01 총 1억을 70/30 재분할, 포지션 비율 축소 재작성). 매수금액 기준은 orchestrator.INITIAL_CAPITAL로 분리(불변)
TRADING_BALANCE = 30_000_000   # 트레이딩(서브) 시드 = 3,000만 (2026-08-01 70/30 재분할). 비중·보유상한 등 튜닝값은 strategy_private.py

# 계좌 구분: main=장기(메인 사이클) / sub=트레이딩(서브 사이클)
# AI펀드 4봇 경쟁 계좌(P/W/S/Q=id 3~6) — committee(1/2)와 분리. 각 가상 1억.
_ACCT_ID = {"main": 1, "sub": 2, "P": 3, "W": 4, "S": 5, "Q": 6,
            "대형주": 7, "발굴주": 8, "Q지수": 9}   # 7/8 = 통일 데스크, 9 = Q 지수 스윙(자기 전략 포워드 테스트)
FUND_ACCOUNTS = ("P", "W", "S", "Q")   # (레거시) 4봇 경쟁 계좌 — 통일 데스크로 대체 중
FUND_SEED = 100_000_000                # 봇당 가상 1억
# 통일 데스크(대형주/발굴주/Q지수) — v1 대체. 설계 docs/AIFUND_PIVOT.md
DESK_ACCOUNTS = ("대형주", "발굴주", "Q지수")
DESK_SEED = 50_000_000                 # 데스크당 가상 5,000만
Q_INDEX_SEED = 10_000_000              # Q 지수 스윙 소액 계좌(1,000만) — 봇 개성 실험
ACCT_SEED = {"대형주": DESK_SEED, "발굴주": DESK_SEED, "Q지수": Q_INDEX_SEED}


def _acct_id(account: str) -> int:
    return _ACCT_ID.get(account or "main", 1)


def ensure_fund_accounts():
    """AI펀드 4봇 계좌(P/W/S/Q, 각 1억)를 shared_portfolio에 시드(INSERT OR IGNORE).
    ⚠️ 전역 init_db에서 자동 호출 안 함 — 새 데스크 파이프라인이 활성화 시 호출(라이브 DB 무영향).
    committee 계좌(1/2)는 안 건드림. 멱등."""
    with _conn() as con:
        for acct in FUND_ACCOUNTS:
            con.execute("INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (?, ?)",
                        (_ACCT_ID[acct], FUND_SEED))


def ensure_desk_accounts():
    """통일 데스크 계좌(대형주/발굴주 각 5,000만 · Q지수 1,000만)를 시드(INSERT OR IGNORE).
    멱등·committee 무영향. ⚠️ 실제 자본 재편·committee 승계는 별도 마이그레이션(백업 후)."""
    with _conn() as con:
        for acct in DESK_ACCOUNTS:
            con.execute("INSERT OR IGNORE INTO shared_portfolio (id, balance) VALUES (?, ?)",
                        (_ACCT_ID[acct], ACCT_SEED[acct]))


def add_to_watch(code: str, name: str, approved_by: str):
    """대형주 관심종목 캐시에 추가(P/W/H 선정). 이미 있으면 찬성봇·상태만 갱신(재관심)."""
    with _conn() as con:
        con.execute(
            "INSERT INTO largecap_watch (code, name, approved_by, status) VALUES (?,?,?,'watching') "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name, approved_by=excluded.approved_by, "
            "status=CASE WHEN largecap_watch.status='bought' THEN 'bought' ELSE 'watching' END",
            (code, name, approved_by))


def get_watchlist(status: str = "watching") -> list:
    """대형주 관심종목 조회(기본 진입대기 'watching'). status=None이면 전체."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if status is None:
            rows = con.execute("SELECT * FROM largecap_watch ORDER BY added_at DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM largecap_watch WHERE status=? ORDER BY added_at DESC",
                               (status,)).fetchall()
        return [dict(r) for r in rows]


def mark_watch(code: str, status: str):
    """관심종목 상태 변경 — 'bought'(Q 진입) / 'dropped'(관심 해제)."""
    with _conn() as con:
        con.execute("UPDATE largecap_watch SET status=? WHERE code=?", (status, code))


def clear_watchlist():
    """대형주 관심종목 초기화 — 매일 재분석 전 진입대기('watching')만 비운다(보유 'bought'는 유지)."""
    with _conn() as con:
        con.execute("DELETE FROM largecap_watch WHERE status != 'bought'")


def save_fundamentals_cache(code: str, date: str, data: dict):
    """종목 재무 last-good 캐시 저장(JSON). API 리밋 시 폴백용."""
    import json
    with _conn() as con:
        con.execute(
            "INSERT INTO fundamentals_cache (code, date, data) VALUES (?,?,?) "
            "ON CONFLICT(code) DO UPDATE SET date=excluded.date, data=excluded.data, "
            "updated_at=CURRENT_TIMESTAMP",
            (code, date, json.dumps(data, ensure_ascii=False)))


def get_fundamentals_cache(code: str) -> dict | None:
    """종목 재무 last-good 캐시 조회. 없으면 None."""
    import json
    with _conn() as con:
        row = con.execute("SELECT data FROM fundamentals_cache WHERE code=?", (code,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def save_stock_name(code: str, name: str):
    """회사명 영속 캐시 저장(이름은 안 변함). yfinance .info 반복 호출 제거용."""
    if not name or name == code:
        return
    with _conn() as con:
        con.execute("INSERT INTO stock_names (code, name) VALUES (?,?) "
                    "ON CONFLICT(code) DO UPDATE SET name=excluded.name, updated_at=CURRENT_TIMESTAMP",
                    (code, name))


def get_stock_name(code: str) -> str | None:
    """회사명 영속 캐시 조회. 없으면 None."""
    with _conn() as con:
        row = con.execute("SELECT name FROM stock_names WHERE code=?", (code,)).fetchone()
    return row[0] if row and row[0] else None


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
            "ALTER TABLE messages ADD COLUMN desk TEXT",   # NULL=committee(라이브) / 'fund'=AI펀드 4봇 관전 내레이션
            "ALTER TABLE virtual_positions ADD COLUMN code TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN market TEXT DEFAULT 'KRX'",
            "ALTER TABLE virtual_positions ADD COLUMN exit_reasoning TEXT",
            "ALTER TABLE virtual_positions ADD COLUMN account TEXT DEFAULT 'main'",
            "ALTER TABLE virtual_positions ADD COLUMN strategy TEXT",       # 트레이딩 규칙엔진: momentum/meanrev
            "ALTER TABLE virtual_positions ADD COLUMN stop_price REAL",     # 모멘텀 손절가(진입 시점 최근 저점)
            "ALTER TABLE virtual_positions ADD COLUMN half_exited INTEGER DEFAULT 0",  # 모멘텀 v2 절반익절 여부
            "ALTER TABLE cycle_state ADD COLUMN market TEXT DEFAULT 'KRX'",
            "ALTER TABLE visit_log ADD COLUMN verified INTEGER DEFAULT 0",
            "ALTER TABLE benchmark_daily ADD COLUMN spy REAL",
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


def save_benchmark_snapshot(date: str, bot_nav, qqq, schd, kospi, kosdaq, spy=None):
    """하루 1개 벤치마크 스냅샷(봇 NAV + 지수). 같은 날 재호출은 값 갱신(UPSERT)."""
    with _conn() as con:
        con.execute("""
            INSERT INTO benchmark_daily (date, bot_nav, qqq, spy, schd, kospi, kosdaq)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET
                bot_nav=COALESCE(excluded.bot_nav, bot_nav),
                qqq=COALESCE(excluded.qqq, qqq), spy=COALESCE(excluded.spy, spy),
                schd=COALESCE(excluded.schd, schd),
                kospi=COALESCE(excluded.kospi, kospi), kosdaq=COALESCE(excluded.kosdaq, kosdaq)
        """, (date, bot_nav, qqq, spy, schd, kospi, kosdaq))


def get_benchmark_snapshots() -> list:
    """날짜 오름차순 전체 스냅샷 (baseline=첫, latest=끝)."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT * FROM benchmark_daily ORDER BY date").fetchall()]


def record_fund_nav(bot: str, date: str, equity: float):
    """AI펀드 봇별 일일 NAV 스냅샷(수익률 스파크라인용). 같은 날 재호출은 갱신."""
    with _conn() as con:
        con.execute(
            "INSERT INTO fund_nav (date, bot, equity) VALUES (?,?,?) "
            "ON CONFLICT(date, bot) DO UPDATE SET equity=excluded.equity",
            (date, bot, equity))


def get_fund_nav_history(bot: str, days: int = 30) -> list:
    """봇 NAV 최근 days개 (날짜 오름차순)."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT date, equity FROM fund_nav WHERE bot=? ORDER BY date DESC LIMIT ?",
            (bot, days)).fetchall()
        return list(reversed([dict(r) for r in rows]))


def record_fund_report(date: str, code: str, name: str, summary: str, packet: str, desk: str = None):
    """A 리서치 리포트(사업개요+핵심재무) 저장 — 관전 패널 표시용. 같은 날 재호출은 갱신."""
    with _conn() as con:
        con.execute(
            "INSERT INTO fund_report (date, code, name, summary, packet, desk) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(date, code) DO UPDATE SET name=excluded.name, summary=excluded.summary, "
            "packet=excluded.packet, desk=excluded.desk",
            (date, code, name, summary, packet, desk))


def get_fund_reports(limit: int = 30) -> list:
    """최근 A 리포트 (최신순)."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM fund_report ORDER BY date DESC, created_at DESC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]


# ── S 병목 시드 큐레이션 (로드맵 ②a/②b) ─────────────────────
def add_bottleneck_seed(ticker: str, rationale: str = "", source: str = "agent") -> bool:
    """병목 후보를 pending으로 등록. 이미 있으면(승인/반려/대기 무관) 무시 — 재승인 되돌림 방지.
    반환: 새로 추가됐으면 True."""
    ticker = (ticker or "").strip().upper()
    if not ticker:
        return False
    with _conn() as con:
        cur = con.execute(
            "INSERT OR IGNORE INTO bottleneck_seed (ticker, rationale, source) VALUES (?,?,?)",
            (ticker, rationale, source))
        return cur.rowcount > 0


def get_bottleneck_seeds(status: str = "approved") -> list:
    """해당 status의 티커 목록(등록순). status=None이면 전체 티커."""
    with _conn() as con:
        if status is None:
            rows = con.execute("SELECT ticker FROM bottleneck_seed ORDER BY created_at").fetchall()
        else:
            rows = con.execute(
                "SELECT ticker FROM bottleneck_seed WHERE status=? ORDER BY created_at",
                (status,)).fetchall()
        return [r[0] for r in rows]


def get_bottleneck_seed_rows(status: str = None) -> list:
    """전체(또는 status별) 시드 행 — Admin 승인 UI용. 최신 등록이 위."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if status is None:
            rows = con.execute("SELECT * FROM bottleneck_seed ORDER BY created_at DESC").fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM bottleneck_seed WHERE status=? ORDER BY created_at DESC",
                (status,)).fetchall()
        return [dict(r) for r in rows]


def decide_bottleneck_seed(ticker: str, status: str) -> bool:
    """pending 시드를 approved/rejected로 확정(decided_at 기록). 반환: 갱신됐으면 True."""
    ticker = (ticker or "").strip().upper()
    if status not in ("approved", "rejected"):
        return False
    with _conn() as con:
        cur = con.execute(
            "UPDATE bottleneck_seed SET status=?, decided_at=CURRENT_TIMESTAMP WHERE ticker=?",
            (status, ticker))
        return cur.rowcount > 0


def backfill_bottleneck_seeds(tickers: list, source: str = "seed") -> int:
    """테이블이 비어 있을 때만 하드코딩 시드를 approved로 백필(라이브 동작 보존·1회성).
    반환: 삽입한 개수(이미 시드가 있으면 0)."""
    if not tickers:
        return 0
    with _conn() as con:
        if con.execute("SELECT 1 FROM bottleneck_seed LIMIT 1").fetchone():
            return 0                                   # 이미 큐레이션 시작됨 — 손대지 않음
        n = 0
        for t in tickers:
            t = (t or "").strip().upper()
            if not t:
                continue
            con.execute(
                "INSERT OR IGNORE INTO bottleneck_seed (ticker, status, source, decided_at) "
                "VALUES (?, 'approved', ?, CURRENT_TIMESTAMP)", (t, source))
            n += 1
        return n


# ── 매수 결재 큐 (봇 판단 → 오너 승인 → 체결) ─────────────────
def has_pending_buy(code: str, desk: str) -> bool:
    """해당 종목·데스크에 이미 대기중(pending) 결재가 있나 — 사이클마다 중복 상신 방지."""
    with _conn() as con:
        return con.execute(
            "SELECT 1 FROM pending_buy WHERE code=? AND desk=? AND status='pending' LIMIT 1",
            (code, desk)).fetchone() is not None


def add_pending_buy(ticker, code, desk, account, market, amount, decision_price,
                    approvers, stock_desc="", reason="", q_comment=None) -> int:
    """매수 결재 상신 → pending. 같은 종목·데스크가 이미 대기중이면 0(중복 방지). 반환: 새 id(또는 0)."""
    if has_pending_buy(code, desk):
        return 0
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO pending_buy (ticker, code, desk, account, market, amount, decision_price, "
            "approvers, stock_desc, reason, q_comment) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (ticker, code, desk, account, market, amount, decision_price,
             approvers, stock_desc, reason, q_comment))
        return cur.lastrowid


def get_pending_buys(status: str = "pending") -> list:
    """결재 큐 목록(최신 상신이 위). status=None이면 전체."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if status is None:
            rows = con.execute("SELECT * FROM pending_buy ORDER BY created_at DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM pending_buy WHERE status=? ORDER BY created_at DESC",
                               (status,)).fetchall()
        return [dict(r) for r in rows]


def get_pending_buy(pid: int) -> dict:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM pending_buy WHERE id=?", (pid,)).fetchone()
        return dict(r) if r else None


def decide_pending_buy(pid: int, status: str) -> dict:
    """pending 결재를 approved/rejected로 확정. 반환: 확정된 행(체결 실행용) 또는 None(없음/이미 결정)."""
    if status not in ("approved", "rejected"):
        return None
    with _conn() as con:
        cur = con.execute(
            "UPDATE pending_buy SET status=?, decided_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'",
            (status, pid))
        if cur.rowcount == 0:
            return None
    return get_pending_buy(pid)


# ── 관찰 단계 (신중한 매수: 등록 → 재관찰 → 확신 시 결재) ──────
def add_observation(code, name, desk, market, bots, thesis, stock_desc, price_at) -> int:
    """관찰 등록. 같은 종목·데스크 진행중(observing)이 있으면 0(중복 방지). 반환: 새 id(또는 0)."""
    with _conn() as con:
        if con.execute("SELECT 1 FROM observation WHERE code=? AND desk=? AND status='observing'",
                       (code, desk)).fetchone():
            return 0
        con.execute("DELETE FROM observation WHERE code=? AND desk=? AND status!='observing'",
                    (code, desk))                       # 과거 종료 기록은 새 관찰로 대체
        cur = con.execute(
            "INSERT INTO observation (code, name, desk, market, bots, thesis, stock_desc, price_at) "
            "VALUES (?,?,?,?,?,?,?,?)", (code, name, desk, market, bots, thesis, stock_desc, price_at))
        return cur.lastrowid


def get_observations(status: str = "observing", desk: str = None) -> list:
    """관찰 목록(오래된 것부터 — 먼저 등록된 것부터 재검토)."""
    q = "SELECT * FROM observation WHERE status=?"
    args = [status]
    if desk:
        q += " AND desk=?"
        args.append(desk)
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(q + " ORDER BY started_at", args).fetchall()]


def record_observation_review(oid: int, review: dict, status: str = None):
    """재관찰 1회 기록(JSON append + count++). status 주면 상태 전이(convinced/dropped)."""
    import json
    with _conn() as con:
        con.row_factory = sqlite3.Row
        row = con.execute("SELECT reviews FROM observation WHERE id=?", (oid,)).fetchone()
        if not row:
            return
        arr = json.loads(row["reviews"] or "[]")
        arr.append(review)
        if status:
            con.execute("UPDATE observation SET reviews=?, review_count=review_count+1, status=?, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(arr, ensure_ascii=False), status, oid))
        else:
            con.execute("UPDATE observation SET reviews=?, review_count=review_count+1, "
                        "updated_at=CURRENT_TIMESTAMP WHERE id=?", (json.dumps(arr, ensure_ascii=False), oid))


# ── P0 성과 계측 (decision_log · 주간 리포트 · 데이터 헬스) ────
def add_decision_log(date, source, bot, code, name, verdict, reasoning, packet, persona_hash, model):
    """봇 판단 1건 기록(재현성: 입력 패킷·페르소나 해시 포함). 실패해도 예외 안 냄(매매 로직 보호)."""
    try:
        with _conn() as con:
            con.execute(
                "INSERT INTO decision_log (date, source, bot, code, name, verdict, reasoning, packet, "
                "persona_hash, model) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (date, source, bot, code, name, verdict, (reasoning or "")[:4000],
                 (packet or "")[:6000], persona_hash, model))
    except Exception:
        pass


def get_decision_logs(before_date: str = None, source: str = None, limit: int = 2000) -> list:
    """판단 기록 조회 — 주간 채점용(before_date 이하), 최신순."""
    q, args = "SELECT * FROM decision_log WHERE 1=1", []
    if before_date:
        q += " AND date <= ?"; args.append(before_date)
    if source:
        q += " AND source = ?"; args.append(source)
    q += " ORDER BY date DESC, id DESC LIMIT ?"; args.append(limit)
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(q, args).fetchall()]


def save_weekly_report(date: str, content: str):
    with _conn() as con:
        con.execute("INSERT INTO weekly_report (date, content) VALUES (?,?) "
                    "ON CONFLICT(date) DO UPDATE SET content=excluded.content", (date, content))


def get_latest_weekly_report() -> dict:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM weekly_report ORDER BY date DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def get_risk_flag(name: str) -> int:
    with _conn() as con:
        r = con.execute("SELECT value FROM risk_flag WHERE name=?", (name,)).fetchone()
        return r[0] if r else 0


def set_risk_flag(name: str, value: int):
    with _conn() as con:
        con.execute("INSERT INTO risk_flag (name, value, updated_at) VALUES (?,?,CURRENT_TIMESTAMP) "
                    "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
                    (name, value))


def record_fetch(source: str, ok: bool):
    """데이터 fetch 성공/실패 카운트(일자별). 반환: 오늘 (ok, fail) — 임계 알림 판단용. 실패해도 무해."""
    try:
        from datetime import datetime, timezone, timedelta
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        with _conn() as con:
            con.execute("INSERT INTO fetch_health (date, source, ok, fail) VALUES (?,?,?,?) "
                        "ON CONFLICT(date, source) DO UPDATE SET ok=ok+excluded.ok, fail=fail+excluded.fail",
                        (today, source, 1 if ok else 0, 0 if ok else 1))
            r = con.execute("SELECT ok, fail FROM fetch_health WHERE date=? AND source=?",
                            (today, source)).fetchone()
            return (r[0], r[1]) if r else (0, 0)
    except Exception:
        return (0, 0)


def get_fetch_health(days: int = 7) -> list:
    with _conn() as con:
        con.row_factory = sqlite3.Row
        return [dict(r) for r in con.execute(
            "SELECT date, source, ok, fail FROM fetch_health ORDER BY date DESC LIMIT ?",
            (days * 4,)).fetchall()]


# ── M 거시 브리핑 (매일 아침 오너 보고) ──────────────────────
def save_macro_briefing(date: str, content: str):
    """오늘 브리핑 저장(하루 1건, 같은 날 재생성은 갱신)."""
    with _conn() as con:
        con.execute("INSERT INTO macro_briefing (date, content) VALUES (?,?) "
                    "ON CONFLICT(date) DO UPDATE SET content=excluded.content", (date, content))


def get_latest_macro_briefing() -> dict:
    """가장 최근 브리핑(dict) 또는 None."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        r = con.execute("SELECT * FROM macro_briefing ORDER BY date DESC LIMIT 1").fetchone()
        return dict(r) if r else None


def set_position_opened_at(pos_id: int, ts: str):
    """포지션 매수 시각 덮어쓰기 — 오너 승인 체결 시 '결재 상신 시각(봇 판단 시점)'으로 정합."""
    with _conn() as con:
        con.execute("UPDATE virtual_positions SET opened_at=? WHERE id=?", (ts, pos_id))


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
            desk TEXT,                                  -- NULL=committee(라이브) / 'fund'=AI펀드 관전
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
            bot_nav REAL, qqq REAL, spy REAL, schd REAL, kospi REAL, kosdaq REAL,
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
        CREATE TABLE IF NOT EXISTS fund_nav (
            date TEXT NOT NULL,
            bot TEXT NOT NULL,
            equity REAL,
            PRIMARY KEY (date, bot)
        );
        CREATE TABLE IF NOT EXISTS fund_report (
            date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            summary TEXT,                              -- 사업 개요(뭐하는 회사)
            packet TEXT,                               -- 핵심 재무 다이제스트
            desk TEXT,                                 -- 'PW'(A 일반풀) / 'S'(병목 자체소싱)
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, code)
        );
        -- S 병목 시드 큐레이션(로드맵 ②): 발굴=Claude / 승인=사람 / 평가·매매=S봇. status 흐름 pending→approved/rejected.
        CREATE TABLE IF NOT EXISTS bottleneck_seed (
            ticker TEXT PRIMARY KEY,
            rationale TEXT,                            -- 왜 병목인지(초크포인트 근거)
            status TEXT DEFAULT 'pending',             -- pending(결재대기) / approved(S 워치리스트) / rejected
            source TEXT,                               -- 'seed'(하드코딩 이관) / 'agent'(6시 큐레이션) / 'manual'
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            decided_at DATETIME                        -- 승인/반려 시각
        );
        -- 매수 결재 큐: 봇이 매수 판단 시 즉시 체결 대신 오너 승인 대기(사이클은 계속). 승인=사람.
        CREATE TABLE IF NOT EXISTS pending_buy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,                      -- 표시 이름(회사명/심볼)
            code TEXT NOT NULL,                        -- 매수·가격용 코드
            desk TEXT NOT NULL,                        -- 대형주 / 발굴주
            account TEXT NOT NULL,
            market TEXT DEFAULT 'US',
            amount REAL,                               -- 매수 금액(사이징)
            decision_price REAL,                       -- 봇 판단 시점 가격 = 체결가(승인 늦어도 이 가격)
            approvers TEXT,                            -- 찬성 봇(내부 레터, 예 'P,W,S')
            stock_desc TEXT,                           -- A 사업 설명
            reason TEXT,                               -- 매수 판단 이유(친절)
            q_comment TEXT,                            -- 대형주 Q 타이밍 멘트
            status TEXT DEFAULT 'pending',             -- pending / approved / rejected
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            decided_at DATETIME
        );
        -- 관찰 단계(신중한 매수): 매수 판단 시 바로 결재가 아니라 며칠 관찰 → 확신 쌓이면 결재 상신.
        CREATE TABLE IF NOT EXISTS observation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT,
            desk TEXT NOT NULL,                        -- 발굴주 (대형주는 매일 재분석 구조라 미적용)
            market TEXT DEFAULT 'US',
            bots TEXT,                                 -- 첫 매수 판단 봇들(내부 레터 'P,S')
            thesis TEXT,                               -- 첫 판단 논지(봇별 요약)
            stock_desc TEXT,                           -- A 사업 설명(결재 첨부용)
            price_at REAL,                             -- 등록 시점 가격
            review_count INTEGER DEFAULT 0,            -- 재관찰 횟수
            reviews TEXT DEFAULT '[]',                 -- JSON 배열: date·bot·verdict·note·price
            status TEXT DEFAULT 'observing',           -- observing / convinced / dropped
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, desk)
        );
        -- P0 성과 계측: 모든 봇 판단을 입력 데이터·페르소나 버전과 함께 기록(재현성·사후 채점).
        CREATE TABLE IF NOT EXISTS decision_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,                        -- KST 판단일
            source TEXT,                               -- 분석 / 관찰 / Q타이밍 / Q지수
            bot TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            verdict TEXT,                              -- 매수/관망/매도/확신/유지/철회/진입/대기/청산
            reasoning TEXT,                            -- 봇 응답 원문
            packet TEXT,                               -- 판단 시점 입력 데이터(재현성·사후 분석)
            persona_hash TEXT,                         -- 페르소나 프롬프트 해시(버전 추적)
            model TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_decision_log_date ON decision_log(date);
        CREATE INDEX IF NOT EXISTS idx_decision_log_code ON decision_log(code);
        -- 주간 성과 리포트(오너 전용 — 매매용 정확도 지표는 관전과 분리, 결재식 보고)
        CREATE TABLE IF NOT EXISTS weekly_report (
            date TEXT PRIMARY KEY,
            content TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        -- 리스크 회로차단기 상태(킬스위치 등 영속 플래그 — 재시작에도 유지)
        CREATE TABLE IF NOT EXISTS risk_flag (
            name TEXT PRIMARY KEY,
            value INTEGER DEFAULT 0,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        -- 데이터 소스 헬스(yfinance 등 실패 추적 — 실패율 경고)
        CREATE TABLE IF NOT EXISTS fetch_health (
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            ok INTEGER DEFAULT 0,
            fail INTEGER DEFAULT 0,
            PRIMARY KEY (date, source)
        );
        -- M 거시 브리핑: 매일 아침 오너에게 올리는 시장 국면 + 블로그 다이제스트(하루 1건).
        CREATE TABLE IF NOT EXISTS macro_briefing (
            date TEXT PRIMARY KEY,                     -- KST 날짜
            content TEXT,                              -- 브리핑 본문
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS largecap_watch (
            code TEXT PRIMARY KEY,                     -- 대형주 관심종목 캐시(P/W/H가 선정 → Q가 진입 타이밍 감시)
            name TEXT,
            approved_by TEXT,                          -- 매수 찬성 봇들(예 'P,H')
            status TEXT DEFAULT 'watching',            -- watching(진입대기) / bought / dropped
            added_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS fundamentals_cache (
            code TEXT PRIMARY KEY,                     -- 종목 재무 last-good 캐시(API 리밋 폴백용)
            date TEXT,                                 -- 마지막 갱신일(KST)
            data TEXT,                                 -- JSON: 재무 키만
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS stock_names (
            code TEXT PRIMARY KEY,                     -- 종목 회사명 영속 캐시(이름은 안 변함 → yfinance .info 반복 제거)
            name TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );
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
        -- AI펀드 피봇: 종목별 논지 캐시 (봇당 1행). 카탈리스트(어닝 등)로 무효화까지 재분석 안 함.
        CREATE TABLE IF NOT EXISTS thesis_cache (
            stock_code TEXT NOT NULL,
            bot TEXT NOT NULL,               -- P / W / S
            verdict TEXT,                    -- 매수 / 관망 / 매도
            reasoning TEXT,                  -- 봇 논지(캐시)
            catalyst_key TEXT,               -- 무효화 기준(예: 다음 어닝일 as-of)
            analyzed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(stock_code, bot)
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
    _migrate()   # 갓 만든 DB에도 ALTER 컬럼(market·account 등) 반영 — 생성 전 1차는 기존 DB용, 멱등


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
def save_message(round_id, agent_name, model, content, summary=None, desk=None):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO messages (round_id, agent_name, model, content, summary, desk) VALUES (?,?,?,?,?,?)",
            (round_id, agent_name, model, content, summary, desk)
        )
        return cur.lastrowid


def _desk_clause(desk):
    # desk=None → committee(라이브, m.desk IS NULL) / 'fund' → AI펀드 내레이션 / 'all' → 전체
    if desk == "all":
        return ""
    if desk == "fund":
        return " AND m.desk = 'fund'"
    return " AND m.desk IS NULL"


def get_messages_since(since_id, limit=100, desk=None):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(f"""
            SELECT m.*, r.topic AS round_topic
            FROM messages m LEFT JOIN rounds r ON m.round_id = r.id
            WHERE m.id > ?{_desk_clause(desk)} ORDER BY m.id ASC LIMIT ?
        """, (since_id, limit)).fetchall()
        return [dict(r) for r in rows]


def get_recent_messages(n=30, desk=None):
    with _conn() as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(f"""
            SELECT m.*, r.topic AS round_topic
            FROM messages m LEFT JOIN rounds r ON m.round_id = r.id
            WHERE 1=1{_desk_clause(desk)} ORDER BY m.id DESC LIMIT ?
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


# ── AI펀드 피봇: 종목별 논지 캐시 (설계 docs/AIFUND_PIVOT.md) ──
def save_thesis(stock_code, bot, verdict, reasoning, catalyst_key=None):
    """봇(P/W/S)의 종목 결론을 캐시(UPSERT). 카탈리스트로 무효화 전까진 재분석 안 함."""
    with _conn() as con:
        con.execute("""
            INSERT INTO thesis_cache (stock_code, bot, verdict, reasoning, catalyst_key, analyzed_at)
            VALUES (?,?,?,?,?,CURRENT_TIMESTAMP)
            ON CONFLICT(stock_code, bot) DO UPDATE SET
                verdict=excluded.verdict, reasoning=excluded.reasoning,
                catalyst_key=excluded.catalyst_key, analyzed_at=excluded.analyzed_at
        """, (stock_code, bot, verdict, reasoning, catalyst_key))


def get_thesis(stock_code, bot=None):
    """캐시된 논지 조회. bot 지정 시 dict 1건(없으면 None), 미지정 시 봇→dict 맵."""
    with _conn() as con:
        con.row_factory = sqlite3.Row
        if bot is not None:
            row = con.execute(
                "SELECT * FROM thesis_cache WHERE stock_code=? AND bot=?", (stock_code, bot)
            ).fetchone()
            return dict(row) if row else None
        rows = con.execute("SELECT * FROM thesis_cache WHERE stock_code=?", (stock_code,)).fetchall()
        return {r["bot"]: dict(r) for r in rows}


def get_cached_codes() -> set:
    """논지 캐시가 하나라도 있는 종목코드 집합 (= '이미 본' 종목 — 신규 발굴에서 제외용)."""
    with _conn() as con:
        return {r[0] for r in con.execute("SELECT DISTINCT stock_code FROM thesis_cache").fetchall()}


def invalidate_thesis(stock_code):
    """카탈리스트(어닝 등) 발생 시 해당 종목 캐시 삭제 → A가 재발굴하면 재분석."""
    with _conn() as con:
        con.execute("DELETE FROM thesis_cache WHERE stock_code=?", (stock_code,))


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
