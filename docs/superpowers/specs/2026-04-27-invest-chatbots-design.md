# InvestChat Design Spec
**Date:** 2026-04-27

## Overview

A Slack-style web app where 4 AI investment analysts with distinct personalities debate market conditions daily, learn from each conversation, and let the user participate in real-time. The user reads their discussions to inform investment decisions.

---

## Personas

| Nickname | Style | Age | Model | Color |
|---|---|---|---|---|
| **Compounder** | Long-term value investor | 60s | Claude Sonnet | Gold |
| **Razor** | Short-term momentum trader | 30s | GPT-4o | Blue |
| **Moonshot** | High-risk, high-reward seeker | 40s | Gemini | Pink |
| **Tortoise** | Conservative, capital-preservation | 50s | Claude Sonnet | Green |

Each persona has a rich system prompt defining:
- Core investment beliefs held firmly
- Areas open to being convinced
- Speech patterns, vocabulary, references
- How they address other agents by nickname

---

## Architecture

```
[Data Fetchers]          [Orchestrator]         [Storage]
Yahoo Finance    →       FastAPI backend    →    SQLite
News RSS feeds   →       (Python)               - messages
                                                 - market_snapshots
                                                 - rounds
                                                 - agent_state
         ↕ REST API (2-3s polling)
[Browser UI]
Vanilla HTML/CSS/JS — Slack-style chat
```

**No WebSocket, no streaming.** The browser polls `/api/messages?since=<id>` every 2-3 seconds. Simple, lightweight, sufficient.

---

## Conversation Flow

### Automated Round
1. Daily scheduler (APScheduler) triggers at 08:00 KST
2. Fetchers pull Yahoo Finance data + Korean financial news RSS
3. Orchestrator composes a market summary prompt
4. Agents respond sequentially: Compounder → Razor → Moonshot → Tortoise
5. Each agent receives: system prompt + agent state + last 30 messages + market snapshot
6. Responses saved to SQLite, UI polls and renders them

### User Injection
1. User sends a message via the input box
2. One agent (rotating) produces a **conversation summary**: current consensus, what each person has argued, how the user's message connects
3. All 4 agents respond sequentially to the user's message
4. Conversation continues normally

### Manual Trigger
- "New Round" button in UI starts a round on demand with the latest market data

---

## Memory System

### Short-term (per-turn context)
- Last 30 messages passed as conversation history on every agent call
- Agents naturally reference what others said ("As Razor mentioned...")

### Long-term (character evolution)
- Every 10 rounds, the system generates a **character update** for each agent:
  - What positions have shifted
  - What they've been convinced of
  - What they remain firm on
- Stored in `agent_state` table, prepended to system prompt going forward
- Example: *"After recent discussions, I've warmed slightly to Moonshot's AI software thesis, though I remain cautious on valuation."*

---

## Data Sources

| Source | Library | Data | Update Frequency |
|---|---|---|---|
| Yahoo Finance | `yfinance` | Price, volume, % change for major indices + Korean stocks | Per round |
| Naver Finance RSS | `feedparser` | Korean financial news headlines | Per round |
| Yonhap Economy RSS | `feedparser` | Korean economic news | Per round |

---

## Database Schema

```sql
messages (
  id INTEGER PRIMARY KEY,
  round_id INTEGER,
  agent_name TEXT,          -- 'Compounder' | 'Razor' | 'Moonshot' | 'Tortoise' | 'User' | 'System'
  model TEXT,               -- 'claude' | 'gpt-4o' | 'gemini' | null
  content TEXT,
  timestamp DATETIME
)

rounds (
  id INTEGER PRIMARY KEY,
  topic TEXT,
  market_snapshot_id INTEGER,
  status TEXT,              -- 'running' | 'complete'
  started_at DATETIME
)

market_snapshots (
  id INTEGER PRIMARY KEY,
  data JSON,                -- raw fetched data
  created_at DATETIME
)

agent_state (
  agent_name TEXT PRIMARY KEY,
  evolution_notes TEXT,     -- accumulated character evolution summary
  round_count INTEGER,
  updated_at DATETIME
)
```

---

## API Endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/api/messages?since=<id>` | Poll for new messages |
| `POST` | `/api/user-message` | Inject user message, trigger agent responses |
| `POST` | `/api/rounds/start` | Manually start a new round |
| `GET` | `/api/market` | Latest market snapshot |
| `GET` | `/api/agents` | Agent states + evolution notes |

---

## UI Layout

**Left sidebar:**
- App title
- Channel list:
  - `#war-room` — main AI debate thread (v1 scope)
  - `#market-pulse` — auto-posts each market snapshot as a structured card (v1 scope)
  - `#my-notes` — user-only scratchpad (out of v1 scope, UI placeholder only)
- Members list with online indicators and model labels
- Live market ticker (KOSPI, NASDAQ, key sectors)

**Main chat area:**
- Channel header with round counter and "New Round" / "Today's Summary" buttons
- Message thread with agent avatar (colored initial), nickname, role tag, timestamp
- User messages right-aligned in gold
- System messages (round start, market update) centered in gray pills
- "Agent is typing..." indicator while generation is in progress
- Input box at bottom, always available

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI |
| Scheduler | APScheduler |
| Database | SQLite via `sqlite3` |
| LLM clients | `anthropic`, `openai`, `google-generativeai` |
| Data fetching | `yfinance`, `feedparser` |
| Frontend | Vanilla HTML/CSS/JS (single `index.html`) |
| Dev server | Uvicorn |

---

## Key Design Decisions

- **No WebSocket:** AI conversations are sequential and async — polling every 2-3s gives adequate UX without streaming infrastructure overhead.
- **Two Claude instances:** Compounder and Tortoise both use Claude but with fundamentally different system prompts. They will naturally sound different.
- **Evolution via prompt injection:** Agent "learning" is implemented as accumulated context prepended to the system prompt, not fine-tuning. Cheap, controllable, reversible.
- **Summary agent rotates:** To avoid one agent always dominating the summary role, the rotation cycles Compounder → Razor → Moonshot → Tortoise.
- **Korean + global data mix:** Yahoo Finance covers global indices; Korean RSS feeds ground the conversation in local market context.
