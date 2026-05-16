import os
import threading
import time
import logging

logger = logging.getLogger("investchat")
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from db import (
    init_db, get_messages_since, get_latest_market_snapshot,
    get_consensus_notes, get_shared_portfolio, get_all_positions, get_open_positions,
    add_lecture_note, get_active_lecture_notes, clear_lecture_notes,
)
import time as _time_module
from orchestrator import (
    run_round, handle_user_message, is_user_active, USER_IDLE_SECONDS,
    is_market_open, get_current_state, evaluate_positions,
    SECTORS, request_pause, clear_pause,
)
import orchestrator as _orch
from prompts import AGENT_ORDER, AGENT_PROFILES
from scheduler import start_scheduler

load_dotenv()
init_db()

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
        })
    return result


@app.get("/api/consensus")
def api_consensus():
    return get_consensus_notes()


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

    # 캐시된 현재가 적용
    for p in positions:
        if p["status"] == "open" and p["symbol"] in _price_cache:
            current = _price_cache[p["symbol"]]
            entry   = p.get("entry_price") or 0
            qty     = p.get("quantity") or 0
            p["current_price"] = current
            if entry and qty:
                p["unrealized_pnl"]     = round((current - entry) * qty, 0)
                p["unrealized_pnl_pct"] = round((current - entry) / entry * 100, 2)

    pf["positions"] = positions
    return pf


@app.get("/api/positions")
def api_positions():
    return get_all_positions(50)


@app.post("/api/positions/evaluate")
def api_evaluate_positions():
    threading.Thread(target=evaluate_positions, daemon=True).start()
    return {"ok": True}


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
    import threading
    with _orch._state_lock:
        _orch.__dict__  # touch
    # globals 직접 수정
    _orch_globals = vars(_orch)
    _orch_globals["_phase"] = "sector_discussion"
    _orch_globals["_sector_idx"] = 0
    _orch_globals["_discussion_round"] = 0
    _orch_globals["_cycle_done_at"] = 0.0
    _orch._persist_state()
    return {"ok": True, "phase": "sector_discussion"}


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
