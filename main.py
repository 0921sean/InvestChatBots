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
)
import time as _time_module
from orchestrator import (
    run_round, handle_user_message, is_user_active, USER_IDLE_SECONDS,
    is_market_open, get_current_state, evaluate_positions, review_holdings,
    SECTORS, request_pause, clear_pause,
)
import orchestrator as _orch
from prompts import AGENT_ORDER, AGENT_PROFILES
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
    _loop_running = True
    _loop_thread = threading.Thread(target=_conversation_loop, daemon=True)
    _loop_thread.start()
    yield
    with _loop_lock:
        _loop_running = False
    scheduler.shutdown()


app = FastAPI(title="InvestChat", lifespan=lifespan)
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
}


@app.middleware("http")
async def owner_guard(request: Request, call_next):
    if (request.method, request.url.path) in _OWNER_ONLY_ROUTES:
        if request.cookies.get(COOKIE_NAME) != OWNER_TOKEN:
            return JSONResponse(
                {"detail": "owner only — 읽기 전용 접속자는 조작 불가"},
                status_code=403,
            )
    return await call_next(request)


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


@app.get("/")
def root():
    return FileResponse("static/index.html")


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
    result = []
    for name in AGENT_ORDER:
        profile = AGENT_PROFILES[name]
        # system 프롬프트에서 {evolution_notes} 플레이스홀더 제거 후 전달
        system_clean = profile["system"].replace("{evolution_notes}", "").strip()
        result.append({
            "name": name,
            "description": profile["description"],
            "color": profile["color"],
            "model_provider": profile["model_provider"],
            "model_label": MODEL_LABEL.get(profile["model_id"], profile["model_id"]),
            "system": system_clean,
            # '감독'은 사용자 본인 페르소나라 UI Members에선 숨김
            "hidden_from_members": (name == "감독"),
        })
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
                        "key_picks": d.get("key_picks", [])}
        except Exception:
            pass

    # v1 raw text — 섹터명·전망·결정·핵심 이유·주의 사항 추출 시도
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

    # 핵심 이유 / 주의 사항 추출 — '|' 또는 줄단위 모두 시도
    thesis = ""
    risks = ""

    # 패턴 1: "핵심 이유: ... | 주의 사항: ..."
    pat1 = _re.search(r'핵심\s*이유[:\s|]*(.+?)(?=\s*\|?\s*주의\s*사항|$)', content, _re.DOTALL)
    if pat1:
        thesis = pat1.group(1).strip().rstrip('|').strip()
    pat2 = _re.search(r'주의\s*사항[:\s|]*(.+?)(?=\n\n|\[결정\]|$)', content, _re.DOTALL)
    if pat2:
        risks = pat2.group(1).strip()

    # 마크다운 ** 제거
    thesis = _re.sub(r'\*\*', '', thesis)[:600]
    risks = _re.sub(r'\*\*', '', risks)[:400]

    return {**base, "v": 1, "sector": sector, "outlook": outlook,
            "decision": decision, "thesis": thesis, "risks": risks,
            "raw": content}


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
    "사냥꾼": "경력직",
    "가치파": "감독", "장기파": "감독",
    "안정파": "INTJ",
    "퀀트": "퀀트중독자",
    "회의파": "원칙주의자",
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


@app.get("/api/portfolio")
def api_portfolio():
    global _price_cache, _price_cache_at
    pf = get_shared_portfolio()
    pf["market_status"] = is_market_open()
    pf["initial_balance"] = 100_000_000

    positions = get_all_positions(30)

    # 캐시 만료 시 백그라운드 갱신 트리거 (응답은 즉시)
    if _time_module.time() - _price_cache_at > _PRICE_CACHE_TTL:
        threading.Thread(target=_refresh_prices_bg, daemon=True).start()

    # 캐시된 현재가 적용 + 옛 봇 이름 치환
    for p in positions:
        if p["status"] == "open" and p["symbol"] in _price_cache:
            current = _price_cache[p["symbol"]]
            entry   = p.get("entry_price") or 0
            qty     = p.get("quantity") or 0
            p["current_price"] = current
            if entry and qty:
                p["unrealized_pnl"]     = round((current - entry) * qty, 0)
                p["unrealized_pnl_pct"] = round((current - entry) / entry * 100, 2)
        # 매수 근거에 옛 봇 이름 있으면 새 봇 이름으로 치환
        if p.get("reasoning"):
            p["reasoning"] = _remap_old_bot_names(p["reasoning"])

    pf["positions"] = positions
    return pf


@app.get("/api/positions")
def api_positions():
    return get_all_positions(50)


@app.post("/api/positions/evaluate")
def api_evaluate_positions():
    threading.Thread(target=evaluate_positions, daemon=True).start()
    return {"ok": True}


@app.post("/api/holdings/review")
def api_holdings_review():
    """월요일 보유 점검 수동 트리거 (요일 무관)."""
    threading.Thread(target=lambda: review_holdings(force=True), daemon=True).start()
    return {"ok": True}


@app.get("/api/token-usage")
def api_token_usage():
    """오늘 + 최근 7일 Claude 토큰 사용량. 현재 소진 여부 포함."""
    from agents import is_claude_token_exhausted
    today = get_token_usage_today()
    recent = get_token_usage_recent(7)
    return {
        "today": today,
        "recent": recent,
        "exhausted": is_claude_token_exhausted(),
    }


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
        notify("📋 InvestChat 요약", result, priority="default", cooldown=0)
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
            "message": "메인 프로세스를 시작합니다. 반도체 섹터부터 분석을 시작해요."}


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
