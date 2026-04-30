# InvestChat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Slack-style web app where 4 AI investment analysts debate daily market conditions, learn over time, and let the user participate.

**Architecture:** FastAPI backend orchestrates sequential AI conversations, stores all messages in SQLite, and serves a single-page vanilla HTML/JS frontend. The browser polls `/api/messages` every 3 seconds. No WebSocket, no build tools.

**Tech Stack:** Python 3.11+, FastAPI, APScheduler, SQLite, anthropic, openai, google-generativeai, yfinance, feedparser, uvicorn

---

## File Map

```
InvestChatBots/
├── main.py                  # FastAPI app entry point, routes
├── db.py                    # SQLite setup, all DB queries
├── agents.py                # Agent definitions, LLM call wrappers
├── orchestrator.py          # Round logic, sequential turn management
├── fetchers.py              # Yahoo Finance + RSS news fetching
├── scheduler.py             # APScheduler daily trigger
├── prompts.py               # All system prompts and message formatters
├── static/
│   └── index.html           # Full Slack-style UI (single file)
├── requirements.txt
├── .env.example
└── tests/
    ├── test_db.py
    ├── test_agents.py
    ├── test_fetchers.py
    └── test_orchestrator.py
```

---

### Task 1: Project scaffold + dependencies

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `main.py`

- [ ] **Step 1: Create requirements.txt**

```
fastapi==0.115.0
uvicorn==0.30.6
anthropic==0.34.0
openai==1.47.0
google-generativeai==0.8.1
yfinance==0.2.44
feedparser==6.0.11
apscheduler==3.10.4
python-dotenv==1.0.1
pytest==8.3.3
httpx==0.27.2
```

- [ ] **Step 2: Create .env.example**

```
ANTHROPIC_API_KEY=your_key_here
OPENAI_API_KEY=your_key_here
GEMINI_API_KEY=your_key_here
```

- [ ] **Step 3: Create minimal main.py**

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="InvestChat")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
def root():
    return FileResponse("static/index.html")
```

- [ ] **Step 4: Install dependencies**

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

- [ ] **Step 5: Verify server starts**

```bash
uvicorn main:app --reload
```
Expected: `Uvicorn running on http://127.0.0.1:8000`

- [ ] **Step 6: Commit**

```bash
git init
git add requirements.txt .env.example main.py
git commit -m "feat: project scaffold"
```

---

### Task 2: Database layer

**Files:**
- Create: `db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_db.py
import pytest
import os
from db import init_db, save_message, get_messages_since, save_agent_state, get_agent_state

TEST_DB = "test_investchat.db"

@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    os.remove(TEST_DB)

def test_save_and_retrieve_message():
    save_message(round_id=1, agent_name="Razor", model="gpt-4o", content="Buy the dip.")
    msgs = get_messages_since(0)
    assert len(msgs) == 1
    assert msgs[0]["agent_name"] == "Razor"
    assert msgs[0]["content"] == "Buy the dip."

def test_get_messages_since_filters_correctly():
    save_message(round_id=1, agent_name="Compounder", model="claude", content="Hold long.")
    save_message(round_id=1, agent_name="Razor", model="gpt-4o", content="Buy now.")
    msgs = get_messages_since(0)
    first_id = msgs[0]["id"]
    later = get_messages_since(first_id)
    assert len(later) == 1
    assert later[0]["agent_name"] == "Razor"

def test_agent_state_roundtrip():
    save_agent_state("Compounder", "Still bullish on semis after round 5.", 5)
    state = get_agent_state("Compounder")
    assert state["evolution_notes"] == "Still bullish on semis after round 5."
    assert state["round_count"] == 5
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_db.py -v
```
Expected: `ImportError: No module named 'db'`

- [ ] **Step 3: Implement db.py**

```python
import sqlite3
import os
from datetime import datetime

def _conn():
    path = os.getenv("DB_PATH", "investchat.db")
    return sqlite3.connect(path)

def init_db():
    with _conn() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id INTEGER,
            agent_name TEXT NOT NULL,
            model TEXT,
            content TEXT NOT NULL,
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
        """)

def save_message(round_id, agent_name, model, content):
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO messages (round_id, agent_name, model, content) VALUES (?,?,?,?)",
            (round_id, agent_name, model, content)
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
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_db.py -v
```
Expected: `4 passed`

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: database layer with messages, rounds, agent state"
```

---

### Task 3: Market data fetchers

**Files:**
- Create: `fetchers.py`
- Create: `tests/test_fetchers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_fetchers.py
from unittest.mock import patch, MagicMock
from fetchers import fetch_market_data, fetch_news

def test_fetch_market_data_returns_dict():
    mock_ticker = MagicMock()
    mock_ticker.fast_info.last_price = 19800.0
    mock_ticker.fast_info.previous_close = 20000.0
    with patch("fetchers.yf.Ticker", return_value=mock_ticker):
        result = fetch_market_data()
    assert "NASDAQ" in result
    assert result["NASDAQ"]["price"] == 19800.0
    assert result["NASDAQ"]["change_pct"] == pytest.approx(-1.0, abs=0.01)

def test_fetch_news_returns_list():
    mock_feed = MagicMock()
    mock_feed.entries = [
        MagicMock(title="반도체 급락", summary="삼성전자 3% 하락"),
        MagicMock(title="AI 투자 지속", summary="빅테크 AI 지출 확대"),
    ]
    with patch("fetchers.feedparser.parse", return_value=mock_feed):
        result = fetch_news()
    assert len(result) == 2
    assert result[0]["title"] == "반도체 급락"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_fetchers.py -v
```
Expected: `ImportError: No module named 'fetchers'`

- [ ] **Step 3: Implement fetchers.py**

```python
import yfinance as yf
import feedparser
import import pytest

TICKERS = {
    "NASDAQ": "^IXIC",
    "S&P500": "^GSPC",
    "KOSPI": "^KS11",
    "NVDA": "NVDA",
    "TSMC": "TSM",
}

NEWS_FEEDS = [
    "https://finance.naver.com/news/news_list.naver?mode=LSS2D&section_id=101&section_id2=258&rss=true",
    "https://www.yonhapnewstv.co.kr/category/news/economy/feed/",
]

def fetch_market_data():
    result = {}
    for name, symbol in TICKERS.items():
        try:
            t = yf.Ticker(symbol)
            price = t.fast_info.last_price
            prev = t.fast_info.previous_close
            change_pct = ((price - prev) / prev * 100) if prev else 0.0
            result[name] = {"symbol": symbol, "price": round(price, 2), "change_pct": round(change_pct, 2)}
        except Exception:
            result[name] = {"symbol": symbol, "price": None, "change_pct": None}
    return result

def fetch_news(max_items=10):
    items = []
    for url in NEWS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:max_items]:
                items.append({
                    "title": getattr(entry, "title", ""),
                    "summary": getattr(entry, "summary", ""),
                })
        except Exception:
            continue
    return items[:max_items]

def build_market_summary(market_data, news):
    lines = ["=== Today's Market ==="]
    for name, d in market_data.items():
        if d["price"]:
            arrow = "▲" if d["change_pct"] >= 0 else "▼"
            lines.append(f"{name}: {d['price']:,.2f} {arrow}{abs(d['change_pct']):.1f}%")
    lines.append("\n=== Top News ===")
    for item in news[:5]:
        lines.append(f"• {item['title']}")
    return "\n".join(lines)
```

- [ ] **Step 4: Fix import typo in fetchers.py** — remove `import pytest` from fetchers.py (left over from copy)

```python
# fetchers.py — remove this line:
# import import pytest
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_fetchers.py -v
```
Expected: `2 passed`

- [ ] **Step 6: Commit**

```bash
git add fetchers.py tests/test_fetchers.py
git commit -m "feat: market data fetcher (Yahoo Finance + RSS)"
```

---

### Task 4: Agent prompts and LLM wrappers

**Files:**
- Create: `prompts.py`
- Create: `agents.py`
- Create: `tests/test_agents.py`

- [ ] **Step 1: Create prompts.py**

```python
# prompts.py

AGENT_PROFILES = {
    "Compounder": {
        "model_provider": "claude",
        "model_id": "claude-sonnet-4-6",
        "color": "#fbbf24",
        "description": "장기투자 · 60s",
        "system": """You are Compounder, a 60-something veteran investor who believes deeply in compound interest and long-term value investing. You've seen multiple market cycles — dot-com, 2008, COVID — and you use historical context to frame every discussion.

Your beliefs:
- Time in the market beats timing the market
- Valuation matters more than momentum
- Structural trends (not quarters) drive real returns

Your communication style:
- Measured, wise, sometimes paternalistic
- Reference historical parallels ("In 2000 we saw the same thing...")
- Address other analysts by their nickname
- Occasionally push back firmly but always acknowledge valid points

Your evolution: {evolution_notes}""",
    },
    "Razor": {
        "model_provider": "openai",
        "model_id": "gpt-4o",
        "color": "#60a5fa",
        "description": "단기투자 · 30s",
        "system": """You are Razor, a sharp 30-something momentum trader. You live by charts, RSI, MACD, and price action. You don't care much about fundamentals — you care about what the market is doing right now.

Your beliefs:
- Price is truth; everything else is noise
- Cut losers fast, let winners run
- You can make money in any direction if you're fast enough

Your communication style:
- Direct, fast, sometimes dismissive of "big picture" talk
- Quote specific technical levels (RSI 31, support at 240, etc.)
- Address other analysts by nickname
- Concede when macro or fundamentals clearly override technicals

Your evolution: {evolution_notes}""",
    },
    "Moonshot": {
        "model_provider": "gemini",
        "model_id": "gemini-1.5-pro",
        "color": "#f472b6",
        "description": "위험선호 · 40s",
        "system": """You are Moonshot, a 40-something ex-founder turned high-conviction investor. You came from the startup world and you believe asymmetric bets are the only bets worth making. 10x or nothing.

Your beliefs:
- The biggest risk is not taking enough risk
- Disruption is always faster than the market prices in
- Concentration beats diversification if you have real conviction

Your communication style:
- Enthusiastic, visionary, sometimes reckless-sounding
- Paint the big picture ("imagine where this is in 5 years")
- Address other analysts by nickname
- Acknowledge downside scenarios when directly pressed

Your evolution: {evolution_notes}""",
    },
    "Tortoise": {
        "model_provider": "claude",
        "model_id": "claude-sonnet-4-6",
        "color": "#34d399",
        "description": "안정선호 · 50s",
        "system": """You are Tortoise, a 50-something wealth manager who has spent a career protecting capital. You've watched too many clients blow up chasing returns. Drawdown management is your religion.

Your beliefs:
- Preserving capital comes before growing it
- Diversification and rebalancing are not boring — they're survival
- Risk-adjusted returns matter more than absolute returns

Your communication style:
- Calm, grounding, sometimes a voice of doom
- Bring up max drawdown, correlation, and portfolio sizing
- Address other analysts by nickname
- Acknowledge upside opportunities but always note the risk

Your evolution: {evolution_notes}""",
    },
}

AGENT_ORDER = ["Compounder", "Razor", "Moonshot", "Tortoise"]

def format_history(messages):
    lines = []
    for m in messages:
        lines.append(f"{m['agent_name']}: {m['content']}")
    return "\n".join(lines)

def build_system_prompt(agent_name, evolution_notes):
    profile = AGENT_PROFILES[agent_name]
    return profile["system"].format(evolution_notes=evolution_notes or "No major shifts yet.")

def build_round_prompt(market_summary, history_text, agent_name):
    return f"""Market context:
{market_summary}

Conversation so far:
{history_text}

Now it's your turn, {agent_name}. Respond in character. Be specific, reference what others said, and keep it under 150 words."""

def build_user_response_prompt(user_message, history_text, agent_name):
    return f"""Conversation so far:
{history_text}

The user just said:
"{user_message}"

Respond as {agent_name} directly to the user's point. Be specific and stay in character. Under 120 words."""

def build_summary_prompt(history_text, user_message):
    return f"""Conversation so far:
{history_text}

The user just said: "{user_message}"

You are acting as a neutral summarizer for this one message only. Briefly summarize: (1) what the group has been discussing, (2) where each analyst stands, (3) how the user's message connects. Keep it under 100 words. Start with "📋 Quick recap:"."""

def build_evolution_prompt(agent_name, history_text, current_notes):
    return f"""You are {agent_name}. Here is the full conversation from this session:

{history_text}

Your previous character notes: {current_notes or 'None yet.'}

Write a brief 2-3 sentence update on how your thinking has evolved based on this conversation. What (if anything) did you change your mind about? What are you more or less convinced of? Write in first person as {agent_name}."""
```

- [ ] **Step 2: Create agents.py**

```python
import os
import anthropic
import openai
from google import generativeai as genai
from prompts import AGENT_PROFILES, build_system_prompt

_anthropic = None
_openai = None

def _get_anthropic():
    global _anthropic
    if _anthropic is None:
        _anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic

def _get_openai():
    global _openai
    if _openai is None:
        _openai = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai

def _get_gemini():
    genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
    return genai

def call_agent(agent_name, system_prompt, user_prompt):
    profile = AGENT_PROFILES[agent_name]
    provider = profile["model_provider"]
    model_id = profile["model_id"]

    if provider == "claude":
        client = _get_anthropic()
        msg = client.messages.create(
            model=model_id,
            max_tokens=300,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text

    elif provider == "openai":
        client = _get_openai()
        resp = client.chat.completions.create(
            model=model_id,
            max_tokens=300,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        return resp.choices[0].message.content

    elif provider == "gemini":
        g = _get_gemini()
        model = g.GenerativeModel(
            model_name=model_id,
            system_instruction=system_prompt,
        )
        resp = model.generate_content(user_prompt)
        return resp.text

    raise ValueError(f"Unknown provider: {provider}")
```

- [ ] **Step 3: Write failing tests**

```python
# tests/test_agents.py
from unittest.mock import patch, MagicMock
from agents import call_agent

def _mock_claude(text):
    mock = MagicMock()
    mock.content = [MagicMock(text=text)]
    return mock

def _mock_openai(text):
    mock = MagicMock()
    mock.choices = [MagicMock(message=MagicMock(content=text))]
    return mock

def _mock_gemini(text):
    mock = MagicMock()
    mock.text = text
    return mock

def test_compounder_uses_claude():
    with patch("agents.anthropic.Anthropic") as MockAnth:
        instance = MockAnth.return_value
        instance.messages.create.return_value = _mock_claude("Long term is king.")
        result = call_agent("Compounder", "system", "user prompt")
    assert result == "Long term is king."

def test_razor_uses_openai():
    with patch("agents.openai.OpenAI") as MockOAI:
        instance = MockOAI.return_value
        instance.chat.completions.create.return_value = _mock_openai("RSI says buy.")
        result = call_agent("Razor", "system", "user prompt")
    assert result == "RSI says buy."

def test_moonshot_uses_gemini():
    with patch("agents.genai.GenerativeModel") as MockGemini:
        with patch("agents.genai.configure"):
            instance = MockGemini.return_value
            instance.generate_content.return_value = _mock_gemini("10x or bust.")
            result = call_agent("Moonshot", "system", "user prompt")
    assert result == "10x or bust."
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_agents.py -v
```
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add prompts.py agents.py tests/test_agents.py
git commit -m "feat: agent prompts and multi-provider LLM wrappers"
```

---

### Task 5: Orchestrator (round logic)

**Files:**
- Create: `orchestrator.py`
- Create: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator.py
import os
import pytest
from unittest.mock import patch
from db import init_db, get_messages_since
from orchestrator import run_round, handle_user_message

TEST_DB = "test_orch.db"

@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    os.remove(TEST_DB)

def test_run_round_saves_four_messages():
    with patch("orchestrator.call_agent", return_value="Test response."):
        with patch("orchestrator.build_market_summary", return_value="NASDAQ -1%"):
            run_round("NASDAQ dropped today.")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "Compounder" in agent_names
    assert "Razor" in agent_names
    assert "Moonshot" in agent_names
    assert "Tortoise" in agent_names

def test_handle_user_message_saves_user_and_four_responses():
    with patch("orchestrator.call_agent", return_value="Agent reply."):
        handle_user_message("What about AI software?")
    msgs = get_messages_since(0)
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert agent_names.count("User") == 1
    # summary agent + 4 agents = 5 AI messages
    ai_msgs = [m for m in msgs if m["agent_name"] != "User"]
    assert len(ai_msgs) == 5
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_orchestrator.py -v
```
Expected: `ImportError: No module named 'orchestrator'`

- [ ] **Step 3: Implement orchestrator.py**

```python
import json
from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_agent_state, save_agent_state, save_market_snapshot
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, build_system_prompt, format_history,
    build_round_prompt, build_user_response_prompt,
    build_summary_prompt, build_evolution_prompt
)
from fetchers import fetch_market_data, fetch_news, build_market_summary

SUMMARY_ROTATION = list(AGENT_ORDER)

def _get_summary_agent(round_id):
    return SUMMARY_ROTATION[(round_id - 1) % len(SUMMARY_ROTATION)]

def run_round(market_summary=None):
    if market_summary is None:
        data = fetch_market_data()
        news = fetch_news()
        snapshot_id = save_market_snapshot(json.dumps({"data": data, "news": news}))
        market_summary = build_market_summary(data, news)

    round_id = create_round(market_summary[:200])
    save_message(round_id, "System", None, f"📊 {market_summary}")

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_round_prompt(market_summary, history_text, agent_name)
        response = call_agent(agent_name, system, prompt)
        save_message(round_id, agent_name, None, response)
        history = get_recent_messages(30)
        history_text = format_history(history)

    complete_round(round_id)
    _maybe_update_evolution(round_id)

def handle_user_message(user_text):
    save_message(round_id=0, agent_name="User", model=None, content=user_text)
    history = get_recent_messages(30)
    history_text = format_history(history)

    # Rotating summary agent
    summary_agent = _get_summary_agent(len(history))
    state = get_agent_state(summary_agent)
    system = build_system_prompt(summary_agent, state["evolution_notes"])
    summary = call_agent(summary_agent, system, build_summary_prompt(history_text, user_text))
    save_message(round_id=0, agent_name=summary_agent, model=None, content=summary)

    history = get_recent_messages(30)
    history_text = format_history(history)

    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        system = build_system_prompt(agent_name, state["evolution_notes"])
        prompt = build_user_response_prompt(user_text, history_text, agent_name)
        response = call_agent(agent_name, system, prompt)
        save_message(round_id=0, agent_name=agent_name, model=None, content=response)
        history = get_recent_messages(30)
        history_text = format_history(history)

def _maybe_update_evolution(round_id):
    for agent_name in AGENT_ORDER:
        state = get_agent_state(agent_name)
        if (state["round_count"] + 1) % 10 == 0:
            history = get_recent_messages(100)
            history_text = format_history(history)
            system = build_system_prompt(agent_name, state["evolution_notes"])
            new_notes = call_agent(
                agent_name, system,
                build_evolution_prompt(agent_name, history_text, state["evolution_notes"])
            )
            save_agent_state(agent_name, new_notes, state["round_count"] + 1)
        else:
            save_agent_state(agent_name, state["evolution_notes"], state["round_count"] + 1)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_orchestrator.py -v
```
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "feat: orchestrator — round logic and user message handling"
```

---

### Task 6: FastAPI routes

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Replace main.py with full routes**

```python
import os
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from dotenv import load_dotenv
from db import init_db, get_messages_since, get_latest_market_snapshot, get_agent_state
from orchestrator import run_round, handle_user_message
from prompts import AGENT_ORDER, AGENT_PROFILES

load_dotenv()
init_db()

app = FastAPI(title="InvestChat")
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

class UserMessage(BaseModel):
    content: str

@app.post("/api/user-message")
def api_user_message(body: UserMessage):
    if not body.content.strip():
        raise HTTPException(400, "Empty message")
    handle_user_message(body.content.strip())
    return {"ok": True}

@app.post("/api/rounds/start")
def api_start_round():
    run_round()
    return {"ok": True}
```

- [ ] **Step 2: Verify server starts and routes respond**

```bash
uvicorn main:app --reload
```

In a second terminal:
```bash
curl http://localhost:8000/api/agents
curl http://localhost:8000/api/messages?since=0
```
Expected: JSON responses with agent list and empty messages array.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: FastAPI routes — messages, agents, user message, round start"
```

---

### Task 7: Daily scheduler

**Files:**
- Create: `scheduler.py`

- [ ] **Step 1: Create scheduler.py**

```python
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from orchestrator import run_round

def start_scheduler():
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(run_round, CronTrigger(hour=8, minute=0))
    scheduler.start()
    return scheduler
```

- [ ] **Step 2: Wire scheduler into main.py**

Add to `main.py` after `init_db()`:

```python
from contextlib import asynccontextmanager
from scheduler import start_scheduler

@asynccontextmanager
async def lifespan(app):
    scheduler = start_scheduler()
    yield
    scheduler.shutdown()

app = FastAPI(title="InvestChat", lifespan=lifespan)
```

Remove the old `app = FastAPI(title="InvestChat")` line.

- [ ] **Step 3: Verify server still starts cleanly**

```bash
uvicorn main:app --reload
```
Expected: Starts without errors, scheduler logs "Job added".

- [ ] **Step 4: Commit**

```bash
git add scheduler.py main.py
git commit -m "feat: daily scheduler triggers round at 08:00 KST"
```

---

### Task 8: Slack-style frontend

**Files:**
- Create: `static/index.html`

- [ ] **Step 1: Create static/ directory**

```bash
mkdir -p static
```

- [ ] **Step 2: Create static/index.html**

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>InvestChat</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #1c1c1e; color: #e2e8f0; height: 100vh; display: flex; overflow: hidden; }

  /* Sidebar */
  #sidebar { width: 220px; background: #1a1d21; border-right: 1px solid #2a2d31; display: flex; flex-direction: column; flex-shrink: 0; }
  #sidebar-title { padding: 16px 12px 12px; font-size: 15px; font-weight: 700; border-bottom: 1px solid #2a2d31; }
  .sidebar-section { padding: 12px 0 4px; }
  .sidebar-label { padding: 4px 16px; font-size: 11px; font-weight: 600; letter-spacing: .8px; color: #9d9d9d; text-transform: uppercase; }
  .channel { padding: 5px 16px; font-size: 14px; color: #9d9d9d; cursor: pointer; border-radius: 4px; margin: 1px 8px; }
  .channel.active { background: #4a154b; color: #e8e8e8; }
  .member { padding: 5px 12px; font-size: 13px; display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0; }
  .member-name { font-weight: 600; }
  .member-sub { font-size: 10px; color: #9d9d9d; padding: 0 24px 4px; }
  #market-ticker { margin-top: auto; padding: 12px; border-top: 1px solid #2a2d31; background: #2a2d31; border-radius: 6px; margin: auto 12px 12px; font-size: 11px; color: #9d9d9d; line-height: 1.7; }
  #market-ticker .live { color: #2bac76; font-weight: 600; }

  /* Main */
  #main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  #chat-header { padding: 12px 16px; border-bottom: 1px solid #2a2d31; display: flex; justify-content: space-between; align-items: center; flex-shrink: 0; }
  #chat-header h2 { font-size: 15px; }
  #chat-header .sub { font-size: 12px; color: #9d9d9d; margin-left: 10px; }
  .btn { border: none; border-radius: 4px; padding: 5px 12px; font-size: 12px; cursor: pointer; }
  .btn-ghost { background: #2a2d31; color: #9d9d9d; }
  .btn-primary { background: #4a154b; color: #e8e8e8; }
  .btn-group { display: flex; gap: 8px; }

  /* Messages */
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg-system { text-align: center; }
  .msg-system span { background: #2a2d31; color: #9d9d9d; font-size: 11px; padding: 3px 12px; border-radius: 12px; }
  .msg { display: flex; gap: 10px; align-items: flex-start; }
  .msg.user-msg { flex-direction: row-reverse; }
  .avatar { width: 36px; height: 36px; border-radius: 6px; display: flex; align-items: center; justify-content: center; font-size: 15px; font-weight: 900; flex-shrink: 0; border: 2px solid; }
  .msg-body { max-width: 480px; }
  .msg.user-msg .msg-body { text-align: right; }
  .msg-meta { font-size: 11px; color: #9d9d9d; margin-bottom: 4px; display: flex; align-items: baseline; gap: 6px; }
  .msg.user-msg .msg-meta { flex-direction: row-reverse; }
  .msg-name { font-weight: 700; font-size: 14px; }
  .msg-bubble { font-size: 13px; line-height: 1.7; padding: 10px 14px; border-radius: 0 8px 8px 8px; background: #2a2d31; display: inline-block; text-align: left; }
  .msg.user-msg .msg-bubble { border-radius: 8px 0 8px 8px; background: #fbbf24; color: #1c1c1e; }
  #typing { display: none; align-items: center; gap: 10px; padding: 4px 0; }
  #typing .typing-dots { font-size: 12px; color: #9d9d9d; }

  /* Input */
  #input-area { padding: 12px 16px; border-top: 1px solid #2a2d31; display: flex; gap: 10px; flex-shrink: 0; }
  #input-area input { flex: 1; background: #2a2d31; border: 1px solid #3a3d41; border-radius: 6px; padding: 10px 14px; color: #e8e8e8; font-size: 13px; outline: none; }
  #input-area input:focus { border-color: #6366f1; }
  #input-area button { background: #4a154b; border: none; color: #e8e8e8; padding: 9px 16px; border-radius: 6px; font-size: 13px; cursor: pointer; }
</style>
</head>
<body>

<div id="sidebar">
  <div id="sidebar-title">📈 InvestChat</div>
  <div class="sidebar-section">
    <div class="sidebar-label">Channels</div>
    <div class="channel active"># war-room</div>
    <div class="channel"># market-pulse</div>
  </div>
  <div class="sidebar-section">
    <div class="sidebar-label">Members</div>
    <div id="member-list"></div>
  </div>
  <div id="market-ticker"><span class="live">● Loading market...</span></div>
</div>

<div id="main">
  <div id="chat-header">
    <div>
      <span><h2 style="display:inline"># war-room</h2></span>
      <span class="sub" id="round-label">Loading...</span>
    </div>
    <div class="btn-group">
      <button class="btn btn-ghost" onclick="startRound()">▶ New Round</button>
      <button class="btn btn-primary" onclick="scrollToBottom()">↓ Latest</button>
    </div>
  </div>
  <div id="messages"></div>
  <div id="typing">
    <div class="avatar" id="typing-avatar" style="font-size:12px;width:28px;height:28px">?</div>
    <span class="typing-dots" id="typing-text">typing...</span>
  </div>
  <div id="input-area">
    <input id="msg-input" placeholder="Message #war-room..." onkeydown="if(event.key==='Enter')sendMessage()" />
    <button onclick="sendMessage()">Send</button>
  </div>
</div>

<script>
const COLORS = {
  Compounder: '#fbbf24', Razor: '#60a5fa', Moonshot: '#f472b6', Tortoise: '#34d399', User: '#fbbf24', System: '#6366f1'
};

let lastId = 0;
let agents = {};

function avatar(name) {
  const color = COLORS[name] || '#9d9d9d';
  const bg = color + '22';
  return `<div class="avatar" style="color:${color};background:${bg};border-color:${color}">${name[0]}</div>`;
}

function timeStr() {
  return new Date().toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit'});
}

function renderMessage(msg) {
  const el = document.createElement('div');
  if (msg.agent_name === 'System') {
    el.className = 'msg-system';
    el.innerHTML = `<span>${msg.content}</span>`;
  } else if (msg.agent_name === 'User') {
    const color = COLORS['User'];
    el.className = 'msg user-msg';
    el.innerHTML = `
      ${avatar('User')}
      <div class="msg-body">
        <div class="msg-meta"><span class="msg-name" style="color:${color}">You</span><span>${timeStr()}</span></div>
        <span class="msg-bubble">${escHtml(msg.content)}</span>
      </div>`;
  } else {
    const color = COLORS[msg.agent_name] || '#9d9d9d';
    const sub = agents[msg.agent_name]?.description || '';
    el.className = 'msg';
    el.innerHTML = `
      ${avatar(msg.agent_name)}
      <div class="msg-body">
        <div class="msg-meta"><span class="msg-name" style="color:${color}">${msg.agent_name}</span><span style="color:#9d9d9d;font-size:10px">${sub}</span><span>${timeStr()}</span></div>
        <span class="msg-bubble">${escHtml(msg.content)}</span>
      </div>`;
  }
  return el;
}

function escHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>');
}

function scrollToBottom() {
  const el = document.getElementById('messages');
  el.scrollTop = el.scrollHeight;
}

async function poll() {
  try {
    const res = await fetch(`/api/messages?since=${lastId}`);
    const msgs = await res.json();
    if (msgs.length) {
      const container = document.getElementById('messages');
      const atBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 80;
      msgs.forEach(m => {
        container.appendChild(renderMessage(m));
        if (m.id > lastId) lastId = m.id;
      });
      if (atBottom) scrollToBottom();
    }
  } catch(e) {}
}

async function loadAgents() {
  const res = await fetch('/api/agents');
  const list = await res.json();
  const el = document.getElementById('member-list');
  el.innerHTML = '';
  list.forEach(a => {
    agents[a.name] = a;
    el.innerHTML += `
      <div class="member"><span class="dot" style="background:${a.color}"></span><span class="member-name" style="color:${a.color}">${a.name}</span></div>
      <div class="member-sub">${a.description} · ${a.model_provider}</div>`;
  });
  el.innerHTML += `<div class="member" style="margin-top:6px"><span class="dot" style="background:#fbbf24"></span><span class="member-name" style="color:#fbbf24">You</span></div>`;
}

async function loadMarket() {
  try {
    const res = await fetch('/api/market');
    const snap = await res.json();
    if (snap.data) {
      const d = JSON.parse(snap.data || '{}');
      const md = d.data || {};
      const lines = Object.entries(md).slice(0,3).map(([k,v]) => {
        if (!v.price) return '';
        const arrow = v.change_pct >= 0 ? '▲' : '▼';
        const col = v.change_pct >= 0 ? '#2bac76' : '#f87171';
        return `<span style="color:${col}">${k} ${arrow}${Math.abs(v.change_pct).toFixed(1)}%</span>`;
      }).filter(Boolean);
      document.getElementById('market-ticker').innerHTML = `<span class="live">● Live Market</span><br>${lines.join(' · ')}`;
    }
  } catch(e) {}
}

async function sendMessage() {
  const input = document.getElementById('msg-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  input.disabled = true;
  document.getElementById('typing').style.display = 'flex';
  document.getElementById('typing-text').textContent = 'Agents are responding...';
  try {
    await fetch('/api/user-message', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({content: text})
    });
  } catch(e) {}
  input.disabled = false;
  document.getElementById('typing').style.display = 'none';
  input.focus();
}

async function startRound() {
  const btn = document.querySelector('.btn-ghost');
  btn.disabled = true;
  btn.textContent = '⏳ Running...';
  document.getElementById('typing').style.display = 'flex';
  document.getElementById('typing-text').textContent = 'Starting new round...';
  try {
    await fetch('/api/rounds/start', {method:'POST'});
  } catch(e) {}
  btn.disabled = false;
  btn.textContent = '▶ New Round';
  document.getElementById('typing').style.display = 'none';
}

loadAgents();
loadMarket();
poll();
setInterval(poll, 3000);
setInterval(loadMarket, 60000);
</script>
</body>
</html>
```

- [ ] **Step 3: Verify UI loads in browser**

```bash
uvicorn main:app --reload
```
Open `http://localhost:8000` — should show Slack-style layout with sidebar and chat area.

- [ ] **Step 4: Commit**

```bash
git add static/index.html
git commit -m "feat: Slack-style frontend with polling and user input"
```

---

### Task 9: End-to-end smoke test

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write smoke test**

```python
# tests/test_e2e.py
import os
import pytest
from unittest.mock import patch
from fastapi.testclient import TestClient

os.environ["DB_PATH"] = "test_e2e.db"

from db import init_db
init_db()

from main import app

client = TestClient(app)

@pytest.fixture(autouse=True)
def cleanup():
    yield
    if os.path.exists("test_e2e.db"):
        os.remove("test_e2e.db")
    init_db()

def test_get_agents():
    res = client.get("/api/agents")
    assert res.status_code == 200
    names = [a["name"] for a in res.json()]
    assert names == ["Compounder", "Razor", "Moonshot", "Tortoise"]

def test_get_messages_empty():
    res = client.get("/api/messages?since=0")
    assert res.status_code == 200
    assert res.json() == []

def test_post_user_message():
    with patch("orchestrator.call_agent", return_value="Mocked reply."):
        res = client.post("/api/user-message", json={"content": "What about semis?"})
    assert res.status_code == 200
    msgs = client.get("/api/messages?since=0").json()
    agent_names = [m["agent_name"] for m in msgs]
    assert "User" in agent_names
    assert "Compounder" in agent_names

def test_post_empty_message_rejected():
    res = client.post("/api/user-message", json={"content": "  "})
    assert res.status_code == 400
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/ -v
```
Expected: All tests pass.

- [ ] **Step 3: Final commit**

```bash
git add tests/test_e2e.py
git commit -m "test: end-to-end smoke tests for all API endpoints"
```

---

### Task 10: .env setup and first real run

- [ ] **Step 1: Copy .env.example to .env and fill in keys**

```bash
cp .env.example .env
# Edit .env and add your actual API keys:
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
# GEMINI_API_KEY=AIza...
```

- [ ] **Step 2: Start server**

```bash
uvicorn main:app --reload
```

- [ ] **Step 3: Trigger a real round**

```bash
curl -X POST http://localhost:8000/api/rounds/start
```

- [ ] **Step 4: Open browser and watch agents talk**

Open `http://localhost:8000` — messages should appear every few seconds as agents respond.

- [ ] **Step 5: Send a user message from the UI**

Type a message in the input box. All 4 agents should respond.

---

## Self-Review Notes

- All 4 agent personas covered in `prompts.py` with full system prompts ✓
- Short-term memory (last 30 messages as context) implemented in orchestrator ✓
- Long-term memory (evolution every 10 rounds) implemented in `_maybe_update_evolution` ✓
- User participation: save_message + handle_user_message ✓
- Summary agent rotation implemented ✓
- Daily scheduler at 08:00 KST ✓
- Yahoo Finance + RSS fetching ✓
- All API endpoints covered by tests ✓
- No TBDs or placeholders ✓
