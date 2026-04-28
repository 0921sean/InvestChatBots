import os
import threading
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from db import init_db, get_messages_since, get_latest_market_snapshot, get_agent_state, get_consensus_notes
from orchestrator import run_round, handle_user_message
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
        except Exception:
            pass
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


@app.post("/api/rounds/start")
def api_start_round():
    run_round()
    return {"ok": True}
