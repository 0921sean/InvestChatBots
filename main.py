import os
import secrets
import threading
import time
import logging

logger = logging.getLogger("investchat")
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from db import (
    init_db, get_messages_since, get_latest_market_snapshot,
    get_consensus_notes, get_shared_portfolio, get_all_positions, get_open_positions,
    add_lecture_note, get_active_lecture_notes, clear_lecture_notes,
    get_token_usage_today, get_token_usage_recent,
    add_donation, get_donations_total, delete_donation,
)
import time as _time_module
from orchestrator import (
    run_round, handle_user_message, is_user_active, USER_IDLE_SECONDS,
    is_market_open, get_current_state, evaluate_positions, review_holdings,
    SECTORS, request_pause, clear_pause,
)
import orchestrator as _orch
from prompts import AGENT_ORDER, AGENT_PROFILES, TRADING_AGENT_ORDER
from scheduler import start_scheduler

load_dotenv()
init_db()

# ── 권한 (owner 토큰 기반) ──────────────────────────────────
# .env의 OWNER_TOKEN을 사용. 없으면 매 부팅마다 새 토큰 생성 후 로그에 출력.
OWNER_TOKEN = os.getenv("OWNER_TOKEN")
if not OWNER_TOKEN:
    OWNER_TOKEN = secrets.token_urlsafe(24)
    logger.warning(f"OWNER_TOKEN env var not set — generated ephemeral token: {OWNER_TOKEN}")
    logger.warning(f"Owner 모드 진입: http://localhost:8001/owner/{OWNER_TOKEN}")
# /admin 로그인 페이지용 비번 (선택) — .env에 ADMIN_PASSWORD 설정 시 OWNER_TOKEN 대신 이걸 사용
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
COOKIE_NAME = "investchat_owner"


def is_owner(request: Request) -> bool:
    return request.cookies.get(COOKIE_NAME) == OWNER_TOKEN


def require_owner(request: Request):
    if not is_owner(request):
        raise HTTPException(status_code=403, detail="owner only — 읽기 전용 접속자는 조작 불가")

_loop_running = False
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()
_round_lock = threading.Lock()  # 동시에 라운드 하나만 실행


def _conversation_loop():
    global _loop_running
    from agents import is_claude_token_exhausted, ClaudeTokenExhausted
    from agents import _call_claude_cli
    from notifier import notify
    _token_notified = False

    while True:
        with _loop_lock:
            if not _loop_running:
                break

        # 토큰 소진 → 복구될 때까지 1분마다 재시도
        if is_claude_token_exhausted():
            if not _token_notified:
                notify("⏸ Claude 토큰 소진", "자동 재시도 중. 복구 시 자동 재개됩니다.", priority="high", cooldown=0)
                _token_notified = True
            try:
                _call_claude_cli("test", "ping")  # 토큰 복구 확인
                _token_notified = False
                notify("▶ Claude 토큰 복구", "분석을 자동 재개합니다.", priority="default", cooldown=0)
                # 복구 즉시 워치리스트도 재실행
                try:
                    from orchestrator import run_telegram_watchlist
                    threading.Thread(target=run_telegram_watchlist, daemon=True).start()
                except Exception:
                    pass
            except Exception:
                time.sleep(60)
                continue

        if not _round_lock.acquire(blocking=False):
            time.sleep(1)
            continue
        try:
            run_round()
        except Exception as e:
            logger.error(f"[loop] {type(e).__name__}: {e}")
        finally:
            _round_lock.release()
        for _ in range(2):
            with _loop_lock:
                if not _loop_running:
                    return
            time.sleep(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop_running, _loop_thread
    # 서버 재시작 시 걸린 라운드 자동 정리
    from db import _conn as db_conn
    with db_conn() as con:
        cleaned = con.execute("UPDATE rounds SET status='complete' WHERE status='running'").rowcount
        if cleaned:
            logger.info(f"시작 시 미완료 라운드 {cleaned}개 정리")
    scheduler = start_scheduler()
    app.state.scheduler = scheduler  # 디버그 API에서 참조

    # ── 일회성 측정 잡 (수동 등록) ──────────────────────────
    # 2026-05-30 20:00 KST 서브 사이클 토큰 측정
    try:
        from apscheduler.triggers.date import DateTrigger
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        target = datetime(2026, 5, 30, 20, 0, 0, tzinfo=KST)
        if target > datetime.now(KST):
            scheduler.add_job(
                measure_watchlist,
                DateTrigger(run_date=target),
                id="oneoff_watchlist_measure_20h",
                replace_existing=True,
            )
            print(f"[lifespan] 일회성 서브 측정 등록: {target.isoformat()}", flush=True)
            logger.info(f"일회성 서브 측정 등록: {target.isoformat()}")
    except Exception as e:
        print(f"[lifespan] 일회성 잡 등록 실패: {e}", flush=True)
        logger.error(f"일회성 잡 등록 실패: {e}")

    _loop_running = True
    _loop_thread = threading.Thread(target=_conversation_loop, daemon=True)
    _loop_thread.start()
    yield
    with _loop_lock:
        _loop_running = False
    scheduler.shutdown()


app = FastAPI(title="InvestChatBots", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── owner-only 미들웨어 ────────────────────────────────────
# 아래 (method, path) 조합은 토큰 소비/시스템 변경을 일으키는 작업이라
# OWNER_TOKEN 쿠키 없는 익명 접속자는 호출 차단.
_OWNER_ONLY_ROUTES = {
    ("POST", "/api/conversation/start"),
    ("POST", "/api/conversation/stop"),
    ("POST", "/api/user-message"),
    ("POST", "/api/positions/evaluate"),
    ("POST", "/api/holdings/review"),
    ("POST", "/api/summary/request"),
    ("POST", "/api/stop-on-credit/on"),
    ("POST", "/api/stop-on-credit/off"),
    ("POST", "/api/cycle/reset"),
    ("POST", "/api/main-cycle/run"),
    ("POST", "/api/lecture-note"),
    ("DELETE", "/api/lecture-notes"),
    ("POST", "/api/owner/logout"),
    ("POST", "/api/donations"),
    # /api/admin/login은 공개 (비번 검증 자체가 인증)
    ("POST", "/api/watchlist/run"),
    ("POST", "/api/main-cycle/measure"),
    ("POST", "/api/watchlist/pause"),
    ("POST", "/api/watchlist/resume"),
    ("POST", "/api/main/pause"),
    ("POST", "/api/main/resume"),
    ("POST", "/api/random-speak/on"),
    ("POST", "/api/random-speak/off"),
}

# /api/donations/{id} DELETE는 path가 동적이라 미들웨어에서 startswith 추가 검사
_OWNER_ONLY_PREFIXES = [
    ("DELETE", "/api/donations/"),
]


@app.middleware("http")
async def owner_guard(request: Request, call_next):
    method, path = request.method, request.url.path
    is_owner_only = (method, path) in _OWNER_ONLY_ROUTES
    if not is_owner_only:
        for m, prefix in _OWNER_ONLY_PREFIXES:
            if method == m and path.startswith(prefix):
                is_owner_only = True
                break

    # 피드백 관련 요청만 상세 로그 (디버그)
    is_fb = "feedback" in path
    if is_fb:
        client_host = request.client.host if request.client else "?"
        is_owner_cookie = request.cookies.get(COOKIE_NAME) == OWNER_TOKEN
        print(f"[fb-req] {method} {path} from={client_host} cookie={'owner' if is_owner_cookie else 'visitor'} ua={request.headers.get('user-agent','')[:40]}", flush=True)

    if is_owner_only and request.cookies.get(COOKIE_NAME) != OWNER_TOKEN:
        print(f"[owner_guard] 차단: {method} {path} (UA: {request.headers.get('user-agent','')[:60]})", flush=True)
        return JSONResponse(
            {"detail": "owner only — 읽기 전용 접속자는 조작 불가"},
            status_code=403,
        )
    response = await call_next(request)
    if is_fb:
        print(f"[fb-resp] {method} {path} → {response.status_code}", flush=True)
    return response


# ── owner 로그인 / 신원 확인 ────────────────────────────────
@app.get("/owner/{token}")
def owner_login(token: str):
    """OWNER_TOKEN과 일치하면 쿠키 세팅 후 홈으로 리다이렉트.
    이 URL은 본인만 알아야 하므로 .env에 OWNER_TOKEN을 직접 두고 사용."""
    if token != OWNER_TOKEN:
        raise HTTPException(status_code=403, detail="invalid owner token")
    resp = RedirectResponse(url="/")
    resp.set_cookie(
        COOKIE_NAME, OWNER_TOKEN,
        max_age=60 * 60 * 24 * 365,  # 1년
        httponly=True, samesite="lax",
    )
    return resp


@app.post("/api/owner/logout")
def owner_logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/whoami")
def whoami(request: Request):
    return {"owner": is_owner(request)}


# ── /admin 로그인 페이지 ────────────────────────────────
@app.get("/admin")
def admin_page(request: Request):
    """인증 완료면 메인 SPA를 /admin URL 그대로 유지하며 서빙.
    인증 안 됐으면 로그인 폼."""
    if is_owner(request):
        return FileResponse(
            "static/index.html",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return FileResponse(
        "static/admin.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


class AdminLoginBody(BaseModel):
    password: str


@app.post("/api/admin/login")
def admin_login(body: AdminLoginBody):
    """ADMIN_PASSWORD가 설정돼 있으면 그것과 비교, 아니면 OWNER_TOKEN.
    호환성: 옛 OWNER_TOKEN을 입력해도 통과 (URL 토큰 방식 사용자 대비)."""
    pw = body.password.strip()
    ok = False
    if ADMIN_PASSWORD and pw == ADMIN_PASSWORD:
        ok = True
    elif pw == OWNER_TOKEN:
        ok = True
    if not ok:
        _time_module.sleep(0.4)  # brute force 방어
        raise HTTPException(status_code=403, detail="비밀번호가 일치하지 않습니다")
    resp = JSONResponse({"ok": True, "redirect": "/admin"})
    resp.set_cookie(
        COOKIE_NAME, OWNER_TOKEN,  # 쿠키 값은 OWNER_TOKEN 그대로 (모든 가드와 일치)
        max_age=60 * 60 * 24 * 365,
        httponly=True, samesite="lax",
    )
    return resp


@app.get("/")
def root(request: Request, s: str = ""):
    # 모바일 브라우저(iOS Safari·카카오톡 인앱)는 Cache-Control: no-cache 무시하고
    # ETag·Last-Modified로 304 보내는 경우 있음. 매번 다른 마커를 박아
    # ETag 자체를 매번 다르게 만들어 강제로 새 HTML 받게 함.
    import time as _t
    with open("static/index.html", "rb") as f:
        html = f.read()
    marker = f"<!-- build {int(_t.time()*1000)} -->".encode()
    html = html.replace(b"</head>", marker + b"</head>", 1)
    headers = {
        "Cache-Control": "no-cache, no-store, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }
    resp = Response(content=html, media_type="text/html; charset=utf-8", headers=headers)
    # 방문 로그 — ?s=kakao|kora|insta|mail 출처 기록 (없으면 'direct')
    # 본인(owner 쿠키) 방문은 통계에서 제외 — direct·재방문율 데이터 오염 방지
    try:
        from db import log_visit
        vsid = request.cookies.get("vsid")
        if not vsid:
            vsid = secrets.token_urlsafe(12)
            resp.set_cookie("vsid", vsid, max_age=60 * 60 * 24 * 365,
                            httponly=True, samesite="lax")
        # 방문 집계는 JS 비콘(POST /api/visit)에서만 — 봇(JS 미실행)은 빠짐 (전환형)
        if is_owner(request):
            # 본인(owner) — 이 vsid의 과거 방문 기록도 일괄 정리 (지표 오염 제거, 1회성·idempotent)
            from db import purge_visits_for
            purge_visits_for(vsid)
    except Exception as e:
        logger.debug(f"visit cookie 실패: {e}")
    return resp


@app.post("/api/visit")
def api_visit(request: Request, s: str = ""):
    """JS 실방문 비콘 — 페이지가 실제 브라우저에서 로드되어 JS가 실행됐을 때만 호출.
    봇·스캐너·링크 미리보기는 JS를 실행하지 않으므로 집계에서 제외된다. owner는 제외."""
    try:
        vsid = request.cookies.get("vsid")
        if is_owner(request):
            if vsid:
                from db import purge_visits_for
                purge_visits_for(vsid)
            return {"ok": True, "owner": True}
        from db import log_visit
        log_visit(
            source=(s or "").strip().lower()[:32] or "direct",
            visitor_id=vsid,
            user_agent=request.headers.get("user-agent", ""),
            referer=request.headers.get("referer", ""),
            path="/",
            verified=1,
        )
    except Exception as e:
        logger.debug(f"visit beacon 실패: {e}")
    return {"ok": True}


@app.get("/api/messages")
def api_messages(since: int = 0, limit: int = 100):
    return get_messages_since(since, limit=limit)


@app.get("/api/messages/latest")
def api_messages_latest(n: int = 200):
    """페이지 초기 로드용 — 최근 N개 메시지 한 번에 반환."""
    from db import get_recent_messages
    return get_recent_messages(n)


@app.get("/api/market")
def api_market():
    snap = get_latest_market_snapshot()
    return snap or {}


@app.get("/api/agents")
def api_agents():
    MODEL_LABEL = {
        "claude-sonnet-4-6": "Claude Sonnet",
        "claude-haiku-4-5-20251001": "Claude Haiku",
        "gemini-2.5-flash": "Gemini Flash",
        "gemini-2.5-pro": "Gemini Pro",
        "gpt-4o": "GPT-4o",
        "gpt-4o-mini": "GPT-4o mini",
    }
    def _entry(name, desk):
        profile = AGENT_PROFILES[name]
        # system 프롬프트에서 {evolution_notes} 플레이스홀더 제거 후 전달
        system_clean = profile["system"].replace("{evolution_notes}", "").strip()
        return {
            "name": name,
            "description": profile["description"],
            "color": profile["color"],
            "model_provider": profile["model_provider"],
            "model_label": MODEL_LABEL.get(profile["model_id"], profile["model_id"]),
            "system": system_clean,
            "desk": desk,                  # main=장기 / trading=트레이딩
            "hidden_from_members": False,
        }

    result = [_entry(n, "main") for n in AGENT_ORDER]
    result += [_entry(n, "trading") for n in TRADING_AGENT_ORDER]
    return result


@app.get("/api/consensus")
def api_consensus():
    """섹터 합의 노트 — v2 JSON과 v1 옛 raw 텍스트 모두 통일된 구조로 변환."""
    import json as _json
    notes = get_consensus_notes()
    result = []
    for n in notes:
        item = _normalize_consensus(n.get("content", ""), n.get("id"), n.get("created_at"))
        result.append(item)
    return result


def _normalize_consensus(content: str, note_id, created_at) -> dict:
    """v2 JSON이면 그대로, v1 raw 텍스트면 sector/outlook/decision/thesis/risks 추출 시도."""
    import json as _json, re as _re
    content = (content or "").strip()
    base = {"id": note_id, "created_at": created_at}
    # v2 JSON?
    if content.startswith("{") and content.endswith("}"):
        try:
            d = _json.loads(content)
            if d.get("v") == 2:
                return {**base, "v": 2, "sector": d.get("sector"),
                        "outlook": d.get("outlook", "중립"),
                        "decision": d.get("decision", "관망"),
                        "headline": d.get("headline", ""),
                        "thesis": d.get("thesis", ""),
                        "drivers": d.get("drivers", []),
                        "risks": d.get("risks", []),
                        "key_picks": d.get("key_picks", []),
                        "spokesperson": d.get("spokesperson", ""),
                        "quote": d.get("quote", "")}
        except Exception:
            pass

    # v1 raw text — 메타데이터만 추출, 본문은 cleaned raw로 단일 thesis
    sector_m = _re.search(r'\[([^\]]+?)\s*섹터\]', content)
    sector = sector_m.group(1) if sector_m else None

    outlook = "중립"
    o_m = _re.search(r'전망[^가-힣]*?(긍정|중립|부정)', content)
    if o_m:
        outlook = o_m.group(1)

    decision = "관망"
    d_m = _re.search(r'\[결정\]\s*(매수|관망|매도)', content)
    if d_m:
        decision = d_m.group(1)

    # 본문 청소: 섹터 헤더·마크다운·[결정] 줄·잡담 제거
    cleaned = content
    cleaned = _re.sub(r'\[[^\]]+?\s*섹터\]\s*\n?', '', cleaned)  # [반도체 섹터] 헤더 제거
    cleaned = _re.sub(r'\*\*', '', cleaned)                        # ** 마크다운 제거
    cleaned = _re.sub(r'\[결정\][^\n]*\n?', '', cleaned)           # [결정] ... 종목용 라인 제거
    # "전망(긍정/부정/중립) | 핵심 이유 | 주의 사항" 같은 빈 헤더 라인 제거
    cleaned = _re.sub(r'전망\s*\(\s*긍정\s*/\s*부정\s*/\s*중립\s*\)\s*\|?\s*핵심\s*이유\s*\|?\s*주의\s*사항\s*\n?', '', cleaned)
    cleaned = cleaned.strip()
    thesis = cleaned[:1000]

    return {**base, "v": 1, "sector": sector, "outlook": outlook,
            "decision": decision, "thesis": thesis, "risks": [], "drivers": [],
            "key_picks": [], "raw": content}


@app.get("/api/state")
def api_state():
    state = get_current_state()
    state["running"] = _loop_running
    return state


@app.get("/api/sectors")
def api_sectors():
    return [{"name": s["name"], "description": s["description"],
             "stocks": [st["name"] for st in s["stocks"]]} for s in SECTORS]


@app.get("/api/conversation/status")
def api_conversation_status():
    return {"running": _loop_running}


@app.post("/api/conversation/start")
def api_conversation_start():
    global _loop_running, _loop_thread
    clear_pause()  # 일시정지 플래그 해제
    with _loop_lock:
        if _loop_running:
            return {"ok": True, "running": True}
        _loop_running = True
    _loop_thread = threading.Thread(target=_conversation_loop, daemon=True)
    _loop_thread.start()
    return {"ok": True, "running": True}


@app.post("/api/conversation/stop")
def api_conversation_stop():
    global _loop_running
    with _loop_lock:
        _loop_running = False
    request_pause()  # 현재 실행 중인 에이전트 루프도 즉시 중단
    return {"ok": True, "running": False}


class UserMessage(BaseModel):
    content: str


@app.post("/api/user-message")
def api_user_message(body: UserMessage):
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    handle_user_message(body.content.strip())
    return {"ok": True}


_price_cache: dict = {}          # {symbol: price}
_price_cache_at: float = 0.0
_PRICE_CACHE_TTL = 180           # 3분 캐시

# 옛 봇 이름 → 새 봇 이름 매핑 (포트폴리오 모달의 매수 근거 표시용)
# 형식: "구)<옛이름>" 표기로 과거 봇 이름임을 명시
_OLD_BOT_MAP = {
    "황소": "드가자", "강세파": "드가자", "모험파": "드가자",
    "매크로": "빅픽처",
    "모멘텀": "차트천재", "트레이더": "차트천재", "단기파": "차트천재",
    "사냥꾼": "기본농부", "경력직": "기본농부",
    "가치파": "실적왕", "장기파": "실적왕", "감독": "실적왕",  # 감독은 실적왕에 흡수
    "안정파": "INTJ",
    "퀀트": "퀀트중독자",
    "회의파": "퀀트중독자", "원칙주의자": "퀀트중독자",        # 원칙주의자는 퀀트중독자에 흡수
}
import re as _re
# 봇 이름 뒤에 콜론(:)이 직접 오는 경우만 치환 — '매크로 환경' 같은 일반 단어 사용은 안전
_OLD_BOT_PAT = _re.compile(r'(?<![가-힣\w])(' + '|'.join(_OLD_BOT_MAP.keys()) + r')(?=\s*[:：])')


def _remap_old_bot_names(text: str) -> str:
    """매수 근거 텍스트의 '옛봇:' 헤더를 '새봇(구):' 로 치환."""
    if not text:
        return text
    return _OLD_BOT_PAT.sub(lambda m: f"{_OLD_BOT_MAP[m.group(1)]}(구)", text)

def _refresh_prices_bg():
    """백그라운드에서 현재가 갱신."""
    global _price_cache, _price_cache_at
    from fetchers import fetch_position_prices
    open_pos = [p for p in get_open_positions() if p["status"] == "open"]
    if not open_pos:
        return
    prices = fetch_position_prices(open_pos)
    _price_cache = prices
    _price_cache_at = time.time()


def _enrich_positions(positions):
    """현재가 캐시 적용 + 옛 봇 이름 치환."""
    for p in positions:
        if p["status"] == "open" and p["symbol"] in _price_cache:
            current = _price_cache[p["symbol"]]
            entry   = p.get("entry_price") or 0
            qty     = p.get("quantity") or 0
            p["current_price"] = current
            if entry and qty:
                p["unrealized_pnl"]     = round((current - entry) * qty, 0)
                p["unrealized_pnl_pct"] = round((current - entry) / entry * 100, 2)
        if p.get("reasoning"):
            p["reasoning"] = _remap_old_bot_names(p["reasoning"])
        if p.get("exit_reasoning"):
            p["exit_reasoning"] = _remap_old_bot_names(p["exit_reasoning"])
    return positions


@app.get("/api/portfolio")
def api_portfolio():
    global _price_cache, _price_cache_at

    # 캐시 만료 시 백그라운드 갱신 트리거 (응답은 즉시)
    if _time_module.time() - _price_cache_at > _PRICE_CACHE_TTL:
        threading.Thread(target=_refresh_prices_bg, daemon=True).start()

    # 메인(장기) 계좌
    pf = get_shared_portfolio("main")
    pf["market_status"] = is_market_open()
    pf["initial_balance"] = 100_000_000
    pf["positions"] = _enrich_positions(get_all_positions(30, account="main"))

    # 서브(트레이딩) 계좌
    sub = get_shared_portfolio("sub")
    sub["initial_balance"] = 30_000_000
    sub["positions"] = _enrich_positions(get_all_positions(30, account="sub"))
    pf["sub"] = sub

    return pf


@app.get("/api/positions")
def api_positions():
    return get_all_positions(50)


@app.get("/api/buy-debate")
def api_buy_debate(pos_id: int):
    """포지션 매수 직전 봇 토론 전체 (라운드 메시지). 옛 봇 이름은 새 이름으로 치환."""
    from db import get_buy_debate
    d = get_buy_debate(pos_id)
    for m in d.get("messages", []):
        m["content"] = _remap_old_bot_names(m.get("content") or "")
    return d


@app.post("/api/positions/evaluate")
def api_evaluate_positions():
    threading.Thread(target=evaluate_positions, daemon=True).start()
    return {"ok": True}


@app.post("/api/holdings/review")
def api_holdings_review():
    """월요일 보유 점검 수동 트리거 (요일 무관)."""
    threading.Thread(target=lambda: review_holdings(force=True), daemon=True).start()
    return {"ok": True}


@app.post("/api/watchlist/run")
def api_watchlist_run():
    """서브 사이클 수동 트리거 — 스케줄 무관 즉시 1회 실행."""
    from orchestrator import run_telegram_watchlist
    threading.Thread(target=run_telegram_watchlist, daemon=True).start()
    return {"ok": True, "triggered": True}


@app.post("/api/main-cycle/measure")
def api_main_cycle_measure():
    """측정용 — weekday/시간 체크 우회해서 메인 사이클 강제 1회 실행.
    백그라운드 스레드로 끝까지 (cycle_rest까지) 자동 진행 + 완료 시 ntfy 발송."""
    global _loop_running
    import orchestrator as o
    from db import create_round, save_message, complete_round
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    # 일반 conversation_loop 정지 (중복 라운드 방지) + pause 풀어주기
    with _loop_lock:
        _loop_running = False
    o.clear_pause()  # measure_loop은 _pause_requested 영향 안 받게

    # 상태 리셋
    with o._state_lock:
        vars(o)["_phase"] = "sector_discussion"
        vars(o)["_sector_idx"] = 0
        vars(o)["_discussion_round"] = 0
        vars(o)["_cycle_done_at"] = 0.0
        o._persist_state()

    # 시작 안내 (채팅 메시지)
    round_id = create_round(f"📐 측정용 메인 사이클 — {now.strftime('%Y-%m-%d %H:%M')}")
    save_message(round_id, "System", None,
                 f"🔬 토큰 baseline 측정 — 메인 사이클 강제 1회 실행\n"
                 f"시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')}\n"
                 f"대상: [{o._market}] {len(o._active_sectors())}섹터 × (3라운드 토론 + 종목 분석)\n"
                 f"종료 시 ntfy 푸시로 자동 보고")
    complete_round(round_id)

    # 시작 시점 토큰 스냅샷 + 시작 시각
    start_token = get_token_usage_today()
    start_epoch = now.timestamp()

    # 백그라운드 — force run_round 반복 (cycle_rest까지)
    def _loop():
        import time as _t
        from agents import is_claude_token_exhausted
        max_iter = 2000          # 안전망 — 무한 루프 방지
        no_progress_count = 0
        last_state = (o._phase, o._sector_idx, o._stock_idx, o._discussion_round)
        terminated_reason = None
        for _ in range(max_iter):
            if o._phase == "cycle_rest":
                terminated_reason = "정상 완료 (cycle_rest 도달)"
                break
            # 토큰 소진 감지 → 안전 종료
            if is_claude_token_exhausted():
                terminated_reason = "Claude 토큰 소진 — 부분 결과로 종료"
                logger.warning(f"[측정] {terminated_reason}")
                break
            try:
                o.run_round(force=True)
            except Exception as e:
                logger.error(f"[측정] 라운드 오류: {e}")
                terminated_reason = f"예외: {type(e).__name__}"
                break
            # 진척이 없으면(같은 phase·sector·stock 10번 반복) 강제 종료
            cur_state = (o._phase, o._sector_idx, o._stock_idx, o._discussion_round)
            if cur_state == last_state:
                no_progress_count += 1
                if no_progress_count >= 10:
                    terminated_reason = "진척 없음 (토큰·네트워크·로직 이상)"
                    logger.warning(f"[측정] {terminated_reason}: {cur_state}")
                    break
            else:
                no_progress_count = 0
                last_state = cur_state
            _t.sleep(0.5)
        else:
            terminated_reason = f"최대 반복({max_iter}) 도달"
        logger.info(f"[측정] 종료: {terminated_reason}")
        _send_baseline_report(start_token, start_epoch, terminated_reason)

    threading.Thread(target=_loop, daemon=True).start()
    return {"ok": True, "started_at": now.isoformat()}


def _send_baseline_report(start_token: dict, start_epoch: float, reason: str = "정상 완료"):
    """측정 종료 후 토큰 차이 계산 + 봇별/단계별 분해 + ntfy 푸시."""
    try:
        from db import _conn
        from notifier import notify
        from datetime import datetime, timezone, timedelta
        import sqlite3

        KST = timezone(timedelta(hours=9))
        end_dt = datetime.now(KST)
        elapsed_min = (end_dt.timestamp() - start_epoch) / 60

        # 현재 token_usage_daily
        end_token = get_token_usage_today()
        calls = (end_token.get("calls") or 0) - (start_token.get("calls") or 0)
        inp = (end_token.get("input_tokens") or 0) - (start_token.get("input_tokens") or 0)
        out = (end_token.get("output_tokens") or 0) - (start_token.get("output_tokens") or 0)
        cache_r = (end_token.get("cache_read_tokens") or 0) - (start_token.get("cache_read_tokens") or 0)
        cache_c = (end_token.get("cache_create_tokens") or 0) - (start_token.get("cache_create_tokens") or 0)
        cost = (end_token.get("cost_usd") or 0) - (start_token.get("cost_usd") or 0)
        total_tokens = inp + out + cache_r + cache_c

        # 단계별·봇별 분해 (messages 기반)
        sector_disc_calls = 0
        stock_analysis_calls = 0
        bot_calls = {}
        with _conn() as con:
            con.row_factory = sqlite3.Row
            rounds = con.execute("""
                SELECT id, topic FROM rounds
                WHERE started_at >= datetime(?, 'unixepoch')
            """, (start_epoch,)).fetchall()
            for r in rounds:
                topic = r["topic"] or ""
                ids_in_round = con.execute("""
                    SELECT agent_name, COUNT(*) c FROM messages
                    WHERE round_id=? AND agent_name NOT IN ('System','User')
                    GROUP BY agent_name
                """, (r["id"],)).fetchall()
                cnt = sum(row["c"] for row in ids_in_round)
                if "sector_discussion" in topic:
                    sector_disc_calls += cnt
                elif "stock_analysis" in topic:
                    stock_analysis_calls += cnt
                for row in ids_in_round:
                    bot_calls[row["agent_name"]] = bot_calls.get(row["agent_name"], 0) + row["c"]

        # 봇별 요약 (순서 유지)
        from prompts import AGENT_ORDER
        bot_summary = " · ".join(f"{n} {bot_calls.get(n,0)}" for n in AGENT_ORDER)

        # ntfy 메시지 (간결하게) — 종료 사유에 따라 제목 변형
        if "토큰 소진" in reason:
            title_prefix = "⏸ baseline 토큰소진 종료"
        elif "정상" in reason:
            title_prefix = "📐 baseline 완료"
        else:
            title_prefix = "⚠️ baseline 비정상 종료"
        title = f"{title_prefix} — {elapsed_min:.0f}분"
        body = (
            f"종료 사유: {reason}\n"
            f"총: {calls}콜 · {_fmt_n(total_tokens)} 토큰 · ${cost:.2f}\n"
            f"섹터토론 {sector_disc_calls}콜 · 종목분석 {stock_analysis_calls}콜\n"
            f"{bot_summary}\n"
            f"하루 1회 기준 ≈ ₩{cost*1380:,.0f}/일"
        )
        notify(title, body, priority="high", cooldown=0)
        logger.info(f"[측정] ntfy 발송 완료: {title}")
    except Exception as e:
        logger.error(f"[측정] 결과 보고 실패: {e}")


def _fmt_n(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return str(n)


def measure_watchlist():
    """서브 사이클 토큰 측정용 — force=True로 1회 실행 후 ntfy 보고."""
    from datetime import datetime, timezone, timedelta
    from db import create_round, save_message, complete_round
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    round_id = create_round(f"🔬 서브 사이클 측정 — {now.strftime('%Y-%m-%d %H:%M')}")
    save_message(round_id, "System", None,
                 f"🔬 토큰 측정 — 서브 사이클 1회 강제 실행\n"
                 f"시작: {now.strftime('%Y-%m-%d %H:%M:%S KST')}\n"
                 f"종료 시 ntfy 푸시로 자동 보고")
    complete_round(round_id)

    start_token = get_token_usage_today()
    start_epoch = now.timestamp()

    def _run():
        import orchestrator as o
        from orchestrator import run_telegram_watchlist
        # 측정용 — 일시정지 명시적으로 해제 (force=True가 가드 무시하지만 안전망)
        o.clear_pause()
        reason = "정상 완료"
        try:
            run_telegram_watchlist(force=True)
        except Exception as e:
            reason = f"예외: {type(e).__name__}: {e}"
            logger.exception(f"[서브 측정] 오류")
        _send_watchlist_report(start_token, start_epoch, reason)

    threading.Thread(target=_run, daemon=True).start()


def _send_watchlist_report(start_token: dict, start_epoch: float, reason: str = "정상 완료"):
    """서브 사이클 측정 종료 후 토큰 차이 + 봇별 분해 ntfy."""
    try:
        from db import _conn
        from notifier import notify
        from datetime import datetime, timezone, timedelta
        import sqlite3
        KST = timezone(timedelta(hours=9))
        end_dt = datetime.now(KST)
        elapsed_min = (end_dt.timestamp() - start_epoch) / 60
        end_token = get_token_usage_today()
        calls = (end_token.get("calls") or 0) - (start_token.get("calls") or 0)
        inp = (end_token.get("input_tokens") or 0) - (start_token.get("input_tokens") or 0)
        out = (end_token.get("output_tokens") or 0) - (start_token.get("output_tokens") or 0)
        cache_r = (end_token.get("cache_read_tokens") or 0) - (start_token.get("cache_read_tokens") or 0)
        cache_c = (end_token.get("cache_create_tokens") or 0) - (start_token.get("cache_create_tokens") or 0)
        cost = (end_token.get("cost_usd") or 0) - (start_token.get("cost_usd") or 0)
        total = inp + out + cache_r + cache_c

        bot_calls = {}
        stock_cnt = 0
        with _conn() as con:
            con.row_factory = sqlite3.Row
            rounds = con.execute("""
                SELECT id, topic FROM rounds
                WHERE started_at >= datetime(?, 'unixepoch')
            """, (start_epoch,)).fetchall()
            for r in rounds:
                topic = r["topic"] or ""
                if "서브" in topic or "워치" in topic or "watchlist" in topic.lower():
                    stock_cnt += 1
                rows = con.execute("""
                    SELECT agent_name, COUNT(*) c FROM messages
                    WHERE round_id=? AND agent_name NOT IN ('System','User')
                    GROUP BY agent_name
                """, (r["id"],)).fetchall()
                for row in rows:
                    bot_calls[row["agent_name"]] = bot_calls.get(row["agent_name"], 0) + row["c"]

        from prompts import AGENT_ORDER
        bot_summary = " · ".join(f"{n} {bot_calls.get(n,0)}" for n in AGENT_ORDER)
        title_prefix = "🔬 서브 측정 완료" if "정상" in reason else "⚠️ 서브 측정 비정상 종료"
        title = f"{title_prefix} — {elapsed_min:.0f}분"
        body = (
            f"종료 사유: {reason}\n"
            f"분석 종목 수: {stock_cnt}개\n"
            f"총: {calls}콜 · {_fmt_n(total)} 토큰 · ${cost:.2f}\n"
            f"{bot_summary}\n"
            f"하루 3슬롯(00·12·18시) 기준 ≈ ₩{cost*3*1380:,.0f}/일"
        )
        notify(title, body, priority="high", cooldown=0)
        logger.info(f"[서브 측정] ntfy 발송 완료: {title}")
    except Exception as e:
        logger.error(f"[서브 측정] 결과 보고 실패: {e}")


@app.post("/api/main-cycle/resume-force")
def api_main_cycle_resume_force():
    """현재 phase 유지하면서 force=True로 메인 사이클 재개.
    측정 도중 코드 패치를 위해 서버 재시작한 경우 이어가기."""
    global _loop_running
    import orchestrator as o
    from db import create_round, save_message, complete_round
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    with _loop_lock:
        _loop_running = False
    o.clear_pause()

    _secs = o._active_sectors()
    sector_name = _secs[o._sector_idx]["name"] if 0 <= o._sector_idx < len(_secs) else "?"
    round_id = create_round(f"▶ 메인 사이클 재개 — {now.strftime('%H:%M')}")
    save_message(round_id, "System", None,
                 f"▶ 메인 사이클 재개 (코드 패치 후 이어가기)\n"
                 f"[{o._market}] sector {o._sector_idx + 1}/{len(_secs)} ({sector_name}) · phase={o._phase}\n"
                 f"force=True 모드로 끝까지 진행")
    complete_round(round_id)

    start_token = get_token_usage_today()
    start_epoch = now.timestamp()

    def _loop():
        import time as _t
        from agents import is_claude_token_exhausted
        max_iter = 2000
        no_progress_count = 0
        last_state = (o._phase, o._sector_idx, o._stock_idx, o._discussion_round)
        terminated_reason = None
        for _ in range(max_iter):
            if o._phase == "cycle_rest":
                terminated_reason = "정상 완료 (cycle_rest 도달)"
                break
            if is_claude_token_exhausted():
                terminated_reason = "Claude 토큰 소진"
                break
            try:
                o.run_round(force=True)
            except Exception as e:
                logger.error(f"[재개] {e}")
                terminated_reason = f"예외: {type(e).__name__}"
                break
            cur_state = (o._phase, o._sector_idx, o._stock_idx, o._discussion_round)
            if cur_state == last_state:
                no_progress_count += 1
                if no_progress_count >= 10:
                    terminated_reason = "진척 없음"
                    break
            else:
                no_progress_count = 0
                last_state = cur_state
            _t.sleep(0.5)
        else:
            terminated_reason = f"최대 반복({max_iter}) 도달"
        _send_baseline_report(start_token, start_epoch, terminated_reason)

    threading.Thread(target=_loop, daemon=True).start()
    return {"ok": True, "started_at": now.isoformat(),
            "resumed_at_sector": o._sector_idx,
            "sector_name": sector_name, "phase": o._phase}


@app.post("/api/sub-cycle/measure")
def api_sub_cycle_measure():
    """수동 — 서브 사이클 토큰 측정 즉시 1회 실행 (force, 토큰 사용량 ntfy)."""
    measure_watchlist()
    from datetime import datetime, timezone, timedelta
    return {"ok": True, "started_at": datetime.now(timezone(timedelta(hours=9))).isoformat()}


class FeedbackBody(BaseModel):
    content: str


# 우회 endpoint — 어떤 환경(SW·확장·차단기)이 '/api/feedback'를 가로채면 이쪽으로 시도
@app.post("/api/contact")
@app.post("/api/note")
def api_feedback_aliases(body: FeedbackBody, request: Request):
    return api_feedback_create(body, request)


@app.post("/api/feedback")
def api_feedback_create(body: FeedbackBody, request: Request):
    """누구나 — 피드백 1건 전송. ntfy 알림 + DB 저장. 5분 내 3건 초과 시 차단."""
    text = (body.content or "").strip()
    if len(text) < 5:
        raise HTTPException(400, "내용이 너무 짧습니다 (5자 이상)")
    if len(text) > 5000:
        raise HTTPException(400, "내용이 너무 깁니다 (5000자 이하)")
    vsid = request.cookies.get("vsid")
    from db import add_feedback, feedback_rate_limited
    if feedback_rate_limited(vsid, window_min=5, max_n=3):
        raise HTTPException(429, "잠시 후 다시 시도해 주세요 (5분에 3건 제한)")
    fid = add_feedback(
        content=text,
        visitor_id=vsid,
        user_agent=request.headers.get("user-agent", ""),
        referer=request.headers.get("referer", ""),
    )
    # ntfy 알림 — 본인에게 즉시 전달
    try:
        from notifier import notify
        preview = text[:200] + ("…" if len(text) > 200 else "")
        notify("📨 새 피드백", preview, priority="high", cooldown=0)
    except Exception as e:
        logger.debug(f"피드백 ntfy 실패: {e}")
    return {"ok": True, "id": fid}


@app.get("/api/admin/feedback")
def api_admin_feedback_list(request: Request, limit: int = 100, unread_only: bool = False):
    """피드백 목록 (owner only)."""
    if not is_owner(request):
        raise HTTPException(403, "owner only")
    from db import list_feedback, feedback_unread_count
    return {
        "items": list_feedback(limit=limit, unread_only=unread_only),
        "unread_count": feedback_unread_count(),
    }


@app.post("/api/admin/feedback/read")
def api_admin_feedback_read(request: Request, id: int = 0):
    """읽음 처리. id=0이면 전체 일괄."""
    if not is_owner(request):
        raise HTTPException(403, "owner only")
    from db import mark_feedback_read
    mark_feedback_read(id if id else None)
    return {"ok": True}


@app.get("/api/admin/visits")
def api_admin_visits(request: Request, days: int = 30):
    """방문 통계 (owner-only) — 출처별 / 일자별 / 총합."""
    if not is_owner(request):
        raise HTTPException(403, "owner only")
    from db import get_visit_stats
    return {
        "verified": get_visit_stats(days=days, verified_only=True),   # JS 실방문
        "all": get_visit_stats(days=days, verified_only=False),       # 구 방식(UA필터)
    }


@app.get("/api/_debug/state")
def api_debug_state():
    """디버그 — 서버 프로세스 내부 상태."""
    import time as _t
    import orchestrator as o
    from agents import _claude_token_exhausted, _claude_retry_after, is_claude_token_exhausted
    return {
        "now_epoch": _t.time(),
        "claude_token_exhausted": _claude_token_exhausted,
        "claude_retry_after": _claude_retry_after,
        "claude_retry_after_relative": _claude_retry_after - _t.time(),
        "is_exhausted_now": is_claude_token_exhausted(),
        "pause_requested": o._pause_requested,
        "watchlist_disabled": o._watchlist_disabled,
        "user_active": o.is_user_active(),
        "user_active_until": o._user_active_until,
        "phase": o._phase,
        "watchlist_lock_locked": o._watchlist_lock.locked(),
    }


@app.post("/api/_debug/claude-ping")
def api_debug_claude_ping(request: Request):
    """Claude 토큰 상태 확인 — 작은 prompt 호출해서 회복 여부 즉시 판단.
    owner-only. 비용 ~$0.001, 5-10초 소요. 자동 polling X (수동 호출만)."""
    if not is_owner(request):
        raise HTTPException(403, "owner only")
    import time as _t
    from agents import _call_claude_cli, _clear_token_exhausted, is_claude_token_exhausted
    start = _t.time()
    try:
        result = _call_claude_cli("test", "ping back: pong", timeout=20)
        elapsed = _t.time() - start
        # 이전이 exhausted였다면 클리어
        was_exhausted = is_claude_token_exhausted()
        if was_exhausted:
            _clear_token_exhausted()
        return {
            "ok": True,
            "status": "토큰 정상 (회복됨)" if was_exhausted else "토큰 정상",
            "previously_exhausted": was_exhausted,
            "ping_response": (result or "")[:80],
            "elapsed_seconds": round(elapsed, 1),
        }
    except Exception as e:
        elapsed = _t.time() - start
        msg = str(e)[:200]
        return {
            "ok": False,
            "status": "토큰 소진 또는 오류",
            "error": msg,
            "elapsed_seconds": round(elapsed, 1),
        }


@app.get("/api/_debug/jobs")
def api_debug_jobs():
    """디버그 — 현재 스케줄러 잡 목록 + 다음 실행 시각."""
    try:
        sch = app.state.scheduler
    except AttributeError:
        return {"jobs": [], "error": "scheduler not initialized"}
    return {"jobs": [
        {"id": j.id, "next_run": j.next_run_time.isoformat() if j.next_run_time else None,
         "trigger": str(j.trigger)}
        for j in sch.get_jobs()
    ]}


@app.post("/api/watchlist/pause")
def api_watchlist_pause():
    """서브 사이클 즉시 중단 + 다음 슬롯도 스킵."""
    from orchestrator import pause_watchlist
    pause_watchlist()
    return {"ok": True, "disabled": True}


@app.post("/api/watchlist/resume")
def api_watchlist_resume():
    """서브 사이클 재가동."""
    from orchestrator import resume_watchlist
    resume_watchlist()
    return {"ok": True, "disabled": False}


@app.get("/api/watchlist/status")
def api_watchlist_status():
    from orchestrator import is_watchlist_disabled
    return {"disabled": is_watchlist_disabled()}


@app.post("/api/main/pause")
def api_main_pause():
    """메인 사이클 즉시 중단 + 6시 reset도 스킵."""
    from orchestrator import pause_main_cycle, request_pause
    pause_main_cycle()
    request_pause()  # 현재 진행 중인 봇 루프도 다음 봇 호출 직전 중단
    return {"ok": True, "disabled": True}


@app.post("/api/main/resume")
def api_main_resume():
    """메인 사이클 재가동."""
    from orchestrator import resume_main_cycle, clear_pause
    resume_main_cycle()
    clear_pause()
    return {"ok": True, "disabled": False}


@app.get("/api/main/status")
def api_main_status():
    from orchestrator import is_main_disabled
    return {"disabled": is_main_disabled()}


@app.post("/api/random-speak/on")
def api_random_speak_on():
    """봇 발언 순서 랜덤화 ON — 다음 라운드부터 매 호출 shuffle."""
    from orchestrator import enable_random_speak_order
    enable_random_speak_order()
    return {"ok": True, "random": True}


@app.post("/api/random-speak/off")
def api_random_speak_off():
    """봇 발언 순서를 AGENT_ORDER 고정 순서로."""
    from orchestrator import disable_random_speak_order
    disable_random_speak_order()
    return {"ok": True, "random": False}


@app.get("/api/random-speak/status")
def api_random_speak_status():
    from orchestrator import is_random_speak_order
    return {"random": is_random_speak_order()}


@app.get("/api/token-usage")
def api_token_usage():
    """오늘 + 최근 7일 Claude 토큰 사용량 + 세트 상태."""
    from agents import is_claude_token_exhausted
    import orchestrator as o
    import time as _t
    today = get_token_usage_today()
    recent = get_token_usage_recent(7)

    # 세트 상태 판정
    if is_claude_token_exhausted():
        set_state = "exhausted"
    elif o._phase in ("sector_discussion", "stock_analysis"):
        set_state = "main_running"
    elif o.is_watchlist_running() if hasattr(o, "is_watchlist_running") else o._watchlist_lock.locked():
        set_state = "sub_running"
    elif o._holdings_review_lock.locked():
        set_state = "post_check_running"
    elif o._recovery_polling_active:
        set_state = "polling"  # 세트 종료 후 회복 대기
    elif o._last_recovery_at > 0 and (_t.time() - o._last_recovery_at) < 3600:
        set_state = "recovered"  # 회복 감지 후 1시간 안 — 풀 시각화
    else:
        set_state = "idle"

    # 토큰 한도 추정 (사용자 calibrate 기반)
    from db import get_token_capacity
    cap = get_token_capacity()
    est_max = cap.get("estimated_max_cost_usd", 0) or 0
    cur_cost = today.get("cost_usd", 0) or 0
    # 마지막 회복 이후 사용량 = 누적 - 회복 시점 추정. 회복 시점에 0으로 리셋되는 게 정확.
    # 단순화: 누적 cost를 estimated_max에서 차감.
    # max=0이면 한도 모름 → pct=0
    pct_used = (cur_cost / est_max * 100) if est_max > 0 else 0
    pct_remaining = max(0, 100 - pct_used)

    return {
        "today": today,
        "recent": recent,
        "exhausted": is_claude_token_exhausted(),
        "set_state": set_state,
        "polling_active": o._recovery_polling_active,
        "last_recovery_at": o._last_recovery_at,
        "last_set_ended_at": o._last_set_ended_at,
        "capacity": {
            "estimated_max_cost_usd": est_max,
            "current_cost_usd": cur_cost,
            "pct_used": round(pct_used, 1),
            "pct_remaining": round(pct_remaining, 1),
            "last_calibrated_at": cap.get("last_calibrated_at"),
            "last_known_pct": cap.get("last_known_pct"),
            "last_known_cost_usd": cap.get("last_known_cost_usd"),
        },
    }


class CalibrateBody(BaseModel):
    percent_used: float  # 사용자가 Claude에서 본 사용률 (0~100)
    notes: str = ""


@app.post("/api/admin/token-capacity/calibrate")
def api_calibrate_capacity(body: CalibrateBody, request: Request):
    """사용자가 Claude에서 본 사용률(%) → 최대 한도 추정.
    예: '85% 사용 중' 알려주면 → 현재 누적 cost / 0.85 = max 추정."""
    if not is_owner(request):
        raise HTTPException(403, "owner only")
    if body.percent_used <= 0 or body.percent_used > 100:
        raise HTTPException(400, "percent_used는 0~100")
    today = get_token_usage_today()
    cur_cost = today.get("cost_usd", 0) or 0
    if cur_cost <= 0:
        raise HTTPException(400, "오늘 사용량이 아직 없어 calibrate 불가")
    from db import calibrate_token_capacity
    result = calibrate_token_capacity(body.percent_used, cur_cost, body.notes)
    return {"ok": True, **result}


# ── 후원 누적 (수동 카운터) ─────────────────────────────────
# 환산 기준 (실측 반영):
#   USD/KRW ≈ 1380 (고정)
#   Haiku 4.5: input $1/1M, output $5/1M
#   실제 사용 패턴(output 비중 높음·cache 토큰 포함)으론 평균 ~$9/1M
#   → ₩1 ≈ 80 tokens (₩50K 충전 ≈ 4M 토큰)
USD_TO_KRW = 1380
TOKENS_PER_KRW = 80


def _enrich_donations(d: dict) -> dict:
    krw = d.get("total", 0) or 0
    d["total_krw"] = krw
    d["total_usd"] = round(krw / USD_TO_KRW, 2)
    d["total_tokens"] = int(krw * TOKENS_PER_KRW)  # 참고치
    return d


@app.get("/api/donations")
def api_donations():
    """공개 — 누적 후원액 + 최근 5건 + USD/토큰 환산."""
    return _enrich_donations(get_donations_total())


class DonationIn(BaseModel):
    amount: float
    note: str | None = ""


@app.post("/api/donations")
def api_add_donation(body: DonationIn):
    """owner — 후원 항목 추가 (KRW 기준). 채팅에 시스템 메시지 + 봇 9명 감사 인사."""
    if body.amount <= 0:
        raise HTTPException(status_code=400, detail="amount must be > 0")
    add_donation(body.amount, body.note or "")

    # 백그라운드로 채팅 알림 + 봇 감사 인사 (응답은 즉시)
    threading.Thread(
        target=_run_donation_thanks,
        args=(body.amount, body.note or ""),
        daemon=True,
    ).start()

    return {"ok": True, **_enrich_donations(get_donations_total())}


def _run_donation_thanks(amount: float, note: str):
    """후원 알림 + 9봇 한마디씩 감사 인사. 각 봇 캐릭터 살린 톤."""
    from db import create_round, save_message, complete_round
    from agents import call_agent, is_claude_token_exhausted
    from orchestrator import AGENT_ORDER, _build_system
    from prompts import AGENT_PROFILES
    try:
        # 1) 시스템 알림 메시지 (별도 라운드)
        total = _enrich_donations(get_donations_total())
        amount_tokens = int(amount * TOKENS_PER_KRW)
        donor = (note.strip() or "익명의 후원자")
        note_disp = f" · {note}" if note else ""
        round_id = create_round(f"후원 — {donor}")
        save_message(round_id, "System", None,
                     f"💰 충전: ₩{int(amount):,} (~{_fmt_tokens(amount_tokens)} 토큰){note_disp}\n"
                     f"누적 충전: ₩{int(total['total_krw']):,} · {_fmt_tokens(total['total_tokens'])} 토큰")

        # 2) 봇 9명 감사 인사 — 토큰 소진 아닐 때만
        if is_claude_token_exhausted():
            save_message(round_id, "System", None, "⏸ 토큰 소진 — 봇 감사 인사는 충전 후 자동 이어집니다.")
            complete_round(round_id)
            return

        save_message(round_id, "System", None, f"🙏 {donor}님께 봇들이 한마디씩...")

        amount_str = f"₩{int(amount):,}"

        # 봇별 캐릭터 힌트 — 유머·드립·과장 허용
        char_hints = {
            "드가자": "흥분 폭주. '와 미쳤다', '담아담아!', '풀매수 사이렌!', '이게 알파다' 같은 감탄사 폭격.",
            "INTJ": "겉은 절제·논리지만 속은 흔들리는 츤데레. '리스크 관리 측면에서… 인정합니다', '비합리적이긴 한데 고맙긴 하네요'.",
            "퀀트중독자": "후원을 지표로 패러디 + 깐깐 기준 통과 인정. 'ROE 무한대 / 영업익 +∞% — 제 기준 처음으로 통과시킵니다.', '기준 봤어요? 이번엔 예외 인정.'",
            "빅픽처": "거시 비유 과장. '2008년 이후 최대 유동성', '연준 피벗급 호재', '매크로 변곡점' 같은 거시 드립.",
            "차트천재": "차트 용어 폭주. '역대급 골든크로스', 'EMA 박살 돌파', '추세 전환 확정' 같은 차트 드립.",
            "기본농부": "20년 베테랑이 동요. '제가 20년간 이런 후원 처음', '닷컴 버블 이후 최대 임팩트' 같은 노련함+감격 믹스.",
            "실적왕": "PEG·5년 비전·10배 어휘 황당 드립. 'PEG 0짜리 후원자.', '경영진 신뢰 만점.', '이건 그냥 텐배거 동반자십니다.' 같은 톤.",
        }

        for agent_name in AGENT_ORDER:
            if is_claude_token_exhausted():
                save_message(round_id, "System", None, "⏸ 도중에 토큰 소진 — 나머지 봇 인사 생략.")
                break
            system = _build_system(agent_name)
            desc = AGENT_PROFILES[agent_name].get("description", "")
            hint = char_hints.get(agent_name, "본인 캐릭터·말투 그대로")
            prompt = (
                f"방금 '{donor}'님이 {amount_str}을 후원해주셨습니다.\n\n"
                f"당신은 '{agent_name}' — {desc}\n\n"
                f"{donor}님께 **재밌게** 감사 한마디. 본인 투자 어휘를 후원에 비유해서 "
                f"드립처럼 표현하세요. 평범한 '감사합니다' 금지.\n\n"
                f"캐릭터 톤: {hint}\n\n"
                f"요구사항:\n"
                f"- 1-2문장, 100자 이내\n"
                f"- 본인 캐릭터·말버릇 폭주 (감탄사·과장·드립 적극 OK)\n"
                f"- 본인의 투자 어휘(차트·매크로·실적·PEG·EMA 등)를 후원에 비유\n"
                f"- {donor}님을 호명\n"
                f"- 따분한 격식체('감사합니다', '도움이 됩니다') 사용 자제\n"
                f"- 이모지 1-2개, 마크다운 금지\n"
                f"- 반드시 한국어"
            )
            try:
                resp = call_agent(agent_name, system, prompt)
                save_message(round_id, agent_name, None, resp)
            except Exception as e:
                logger.debug(f"감사 인사 실패 {agent_name}: {e}")

        complete_round(round_id)
    except Exception as e:
        logger.warning(f"후원 감사 인사 오류: {e}")


def _fmt_tokens(n: int) -> str:
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000:     return f"{n/1_000_000:.2f}M"
    if n >= 1_000:         return f"{n/1_000:.1f}K"
    return str(n)


@app.delete("/api/donations/{donation_id}")
def api_delete_donation(donation_id: int):
    """owner — 특정 후원 항목 삭제."""
    delete_donation(donation_id)
    return {"ok": True, **_enrich_donations(get_donations_total())}


@app.post("/api/summary/request")
def api_request_summary():
    threading.Thread(target=_generate_summary, daemon=True).start()
    return {"ok": True}


def _generate_summary():
    from db import get_recent_messages
    from agents import call_agent
    from notifier import notify
    msgs = get_recent_messages(30)
    agent_msgs = [m for m in msgs if m["agent_name"] not in ("System", "User")]
    if not agent_msgs:
        notify("📋 요약", "아직 대화가 없습니다.", priority="default", cooldown=0)
        return
    conversation = "\n".join(f"{m['agent_name']}: {m['content'][:120]}" for m in agent_msgs[-20:])
    try:
        from prompts import AGENT_PROFILES
        result = call_agent(
            "드가자",
            AGENT_PROFILES["드가자"]["system"],
            f"""다음은 AI 투자 그룹의 최근 대화입니다:\n\n{conversation}\n\n
5줄 이내로 요약:
📌 현재 섹터/종목:
💬 핵심 논점:
✅ 매수 의견:
⚠️ 우려:
🎯 포지션:
반드시 한국어."""
        )
        notify("📋 InvestChatBots 요약", result, priority="default", cooldown=0)
    except Exception as e:
        notify("📋 요약 실패", str(e)[:100], priority="low", cooldown=0)


@app.get("/api/stop-on-credit")
def api_get_stop_on_credit():
    return {"enabled": _orch._stop_on_credit}


@app.post("/api/stop-on-credit/on")
def api_stop_on_credit_on():
    _orch._stop_on_credit = True
    return {"enabled": True}


@app.post("/api/stop-on-credit/off")
def api_stop_on_credit_off():
    _orch._stop_on_credit = False
    return {"enabled": False}


@app.post("/api/cycle/reset")
def api_cycle_reset():
    """사이클 상태 강제 리셋 — 첫 섹터부터 다시 시작."""
    _orch_globals = vars(_orch)
    _orch_globals["_phase"] = "sector_discussion"
    _orch_globals["_sector_idx"] = 0
    _orch_globals["_discussion_round"] = 0
    _orch_globals["_cycle_done_at"] = 0.0
    _orch._persist_state()
    return {"ok": True, "phase": "sector_discussion"}


@app.post("/api/main-cycle/run")
def api_main_cycle_run():
    """메인 프로세스 실행 버튼 — 오늘 이미 했으면 알리고, 안 했으면 시작."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    # 주말(토·일)에는 실행 안 함
    if now.weekday() >= 5:
        return {"ok": True, "weekend": True,
                "message": "오늘은 장이 안 하는 날입니다."}

    cycle_done_at = _orch._cycle_done_at
    already_done = False
    if cycle_done_at > 0:
        done_date = datetime.fromtimestamp(cycle_done_at, tz=KST).date()
        if done_date >= now.date():
            already_done = True

    if already_done:
        return {"ok": True, "already_done": True,
                "message": f"오늘 메인 프로세스는 이미 완료됐어요. ({datetime.fromtimestamp(cycle_done_at, tz=KST).strftime('%H:%M')} 완료)"}

    # 현재 진행 중인 것 멈추고 메인 프로세스 시작
    _orch.request_pause()
    _time_module.sleep(1)
    _orch.clear_pause()
    _orch.reset_daily_cycle()

    # 루프가 안 켜져 있으면 켜기
    global _loop_running, _loop_thread
    clear_pause()
    with _loop_lock:
        if not _loop_running:
            _loop_running = True
            _loop_thread = threading.Thread(target=_conversation_loop, daemon=True)
            _loop_thread.start()

    return {"ok": True, "already_done": False,
            "message": "메인 프로세스를 시작합니다. 첫 섹터부터 분석을 시작해요."}


class LectureNote(BaseModel):
    content: str

@app.post("/api/lecture-note")
def api_add_lecture_note(body: LectureNote):
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Empty note")
    add_lecture_note(body.content.strip())
    return {"ok": True}

@app.get("/api/lecture-notes")
def api_get_lecture_notes():
    return get_active_lecture_notes()

@app.delete("/api/lecture-notes")
def api_clear_lecture_notes():
    clear_lecture_notes()
    return {"ok": True}

@app.get("/api/user/active")
def api_user_active():
    return {"active": is_user_active()}
