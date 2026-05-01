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
from db import init_db, get_messages_since, get_latest_market_snapshot, get_agent_state, get_consensus_notes, get_portfolio, get_all_positions, get_shared_portfolio
from orchestrator import run_round, handle_user_message, is_user_active, USER_IDLE_SECONDS, run_analysis, evaluate_positions, is_market_open
import orchestrator as _orch
from prompts import AGENT_ORDER, AGENT_PROFILES
from scheduler import start_scheduler

load_dotenv()
init_db()

# ── 대화 루프 상태 ──────────────────────────────────────
_loop_running = False
_loop_thread: threading.Thread | None = None
_loop_lock = threading.Lock()

def _conversation_loop():
    global _loop_running
    while True:
        with _loop_lock:
            if not _loop_running:
                break
        try:
            run_round()
        except Exception as e:
            logger.error(f"[loop] run_round 오류: {type(e).__name__}: {e}")
        # 라운드 사이 5초 대기 (폴링이 메시지 받을 시간 확보)
        for _ in range(2):
            with _loop_lock:
                if not _loop_running:
                    return
            time.sleep(1)
# ──────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _loop_running, _loop_thread
    scheduler = start_scheduler()
    # 서버 시작 시 대화 루프 자동 시작
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
def api_messages(since: int = 0):
    return get_messages_since(since)


@app.get("/api/market")
def api_market():
    snap = get_latest_market_snapshot()
    return snap or {}


@app.get("/api/agents")
def api_agents():
    result = []
    for name in AGENT_ORDER:
        state = get_agent_state(name)
        profile = AGENT_PROFILES[name]
        result.append({
            "name": name,
            "description": profile["description"],
            "color": profile["color"],
            "model_provider": profile["model_provider"],
            "evolution_notes": state["evolution_notes"],
            "round_count": state["round_count"],
        })
    return result


@app.get("/api/consensus")
def api_consensus():
    return get_consensus_notes()


@app.get("/api/user/active")
def api_user_active():
    return {"active": is_user_active(), "resumes_in": max(0, int(USER_IDLE_SECONDS - (time.time() - 0)))}


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


@app.get("/api/conversation/status")
def api_conversation_status():
    return {"running": _loop_running}


@app.post("/api/conversation/start")
def api_conversation_start():
    global _loop_running, _loop_thread
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
    return {"ok": True, "running": False}


class UserMessage(BaseModel):
    content: str


@app.post("/api/user-message")
def api_user_message(body: UserMessage):
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Empty message")
    handle_user_message(body.content.strip())
    return {"ok": True}


@app.post("/api/summary/request")
def api_request_summary():
    import threading
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
        result = call_agent(
            "밸류에이터",
            "투자 토론 요약 전문가입니다.",
            f"""다음은 AI 투자 그룹의 최근 대화입니다:

{conversation}

아래 형식으로 요약하세요 (총 5줄 이내):
📌 주요 논의 종목/섹터: [종목명들]
💬 핵심 논점: [한 줄]
✅ 긍정 의견: [한 줄]
⚠️ 우려 사항: [한 줄]
🎯 현재 방향: [매수검토/관망/없음]

반드시 한국어로."""
        )
        notify("📋 InvestChat 요약", result, priority="default", cooldown=0)
    except Exception as e:
        notify("📋 요약 실패", str(e)[:100], priority="low", cooldown=0)

@app.post("/api/rounds/start")
def api_start_round():
    run_round()
    return {"ok": True}


class AnalysisTopic(BaseModel):
    topic: str = ""

@app.post("/api/analysis/run")
def api_run_analysis(body: AnalysisTopic):
    import threading
    threading.Thread(target=run_analysis, args=(body.topic or None,), daemon=True).start()
    return {"ok": True, "topic": body.topic or "auto"}

@app.post("/api/positions/evaluate")
def api_evaluate_positions():
    import threading
    threading.Thread(target=evaluate_positions, daemon=True).start()
    return {"ok": True}

@app.get("/api/portfolio")
def api_portfolio():
    return get_portfolio()

@app.get("/api/shared-portfolio")
def api_shared_portfolio():
    pf = get_shared_portfolio()
    pf["market_status"] = is_market_open()
    pf["positions"] = get_all_positions(20)
    return pf

@app.get("/api/positions")
def api_positions():
    return get_all_positions(50)
