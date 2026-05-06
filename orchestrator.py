"""
AI 투자 토론 그룹 — 섹터 라운드 시스템
섹터 토론(3라운드) → 종목 분석(5봇 결정) → 포트폴리오 실행
"""
import json
import logging
import re
import threading
import time
import random
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat")

from db import (
    create_round, complete_round, save_message, get_recent_messages,
    get_latest_market_snapshot, save_market_snapshot, save_consensus,
    get_consensus_notes, buy_shared_position, sell_shared_position,
    get_shared_portfolio, get_open_positions_by_symbol,
)
from agents import call_agent
from prompts import (
    AGENT_ORDER, AGENT_PROFILES,
    format_history_compact,
    build_sector_discussion_prompt,
    build_stock_analysis_prompt,
    build_user_response_prompt,
    build_feedback_prompt,
    build_summarize_prompt,
)
from fetchers import (
    fetch_market_data, fetch_news, fetch_technical_analysis,
    build_market_summary, fetch_stock_data, format_stock_data,
)
from telegram_fetcher import fetch_telegram_messages, get_cached_context
from notifier import notify, is_credit_error, is_rate_limit

# ── 섹터 + 종목 설정 ────────────────────────────────────
SECTORS = [
    {
        "name": "반도체",
        "description": "메모리·파운드리·반도체 장비",
        "stocks": [
            {"name": "삼성전자",   "code": "005930", "yf": "005930.KS"},
            {"name": "SK하이닉스", "code": "000660", "yf": "000660.KS"},
            {"name": "한미반도체", "code": "042700", "yf": "042700.KS"},
        ],
    },
    {
        "name": "2차전지",
        "description": "EV·ESS 배터리 셀·소재",
        "stocks": [
            {"name": "LG에너지솔루션", "code": "373220", "yf": "373220.KS"},
            {"name": "삼성SDI",        "code": "006400", "yf": "006400.KS"},
            {"name": "에코프로비엠",   "code": "247540", "yf": "247540.KS"},
        ],
    },
    {
        "name": "바이오·헬스케어",
        "description": "신약개발·의료기기·CMO",
        "stocks": [
            {"name": "셀트리온",         "code": "068270", "yf": "068270.KS"},
            {"name": "삼성바이오로직스", "code": "207940", "yf": "207940.KS"},
            {"name": "유한양행",         "code": "000100", "yf": "000100.KS"},
        ],
    },
    {
        "name": "방산·우주",
        "description": "국방·항공우주·방위산업",
        "stocks": [
            {"name": "한화에어로스페이스", "code": "012450", "yf": "012450.KS"},
            {"name": "LIG넥스원",         "code": "079550", "yf": "079550.KS"},
            {"name": "현대로템",           "code": "064350", "yf": "064350.KS"},
        ],
    },
    {
        "name": "자동차·모빌리티",
        "description": "완성차·부품·자율주행",
        "stocks": [
            {"name": "현대차",   "code": "005380", "yf": "005380.KS"},
            {"name": "기아",     "code": "000270", "yf": "000270.KS"},
            {"name": "현대모비스", "code": "012330", "yf": "012330.KS"},
        ],
    },
]

# ── 라운드 설정 ──────────────────────────────────────────
SECTOR_DISCUSSION_ROUNDS = 3   # 섹터 토론 라운드 수
BUY_MAJORITY = 4               # 매수 결정에 필요한 최소 투표 수 (7봇 중 과반)
SELL_MAJORITY = 4              # 매도 결정에 필요한 최소 투표 수
DEFAULT_BUY_PCT = 10           # 기본 매수 비중 (잔액의 %)
MAX_BUY_PCT = 20               # 최대 매수 비중
MARKET_REFRESH_HOURS = 2

# ── 상태 ─────────────────────────────────────────────────
_sector_idx: int = 0
_phase: str = "sector_discussion"   # "sector_discussion" | "stock_analysis"
_stock_idx: int = 0
_discussion_round: int = 0
_user_active_until: float = 0.0
_stop_on_credit: bool = False
_agent_fail_count: dict = {}
USER_IDLE_SECONDS = 300

_state_lock = threading.Lock()


def is_user_active() -> bool:
    return time.time() < _user_active_until


def is_market_open() -> str:
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return "NONE"
    h, m = now.hour, now.minute
    if 9 <= h < 15 or (h == 15 and m <= 30):
        return "KRX"
    if (h == 22 and m >= 30) or h > 22 or h < 5:
        return "US"
    return "NONE"


def get_current_state() -> dict:
    sector = SECTORS[_sector_idx]
    stock = sector["stocks"][_stock_idx] if _phase == "stock_analysis" else None
    return {
        "sector_idx": _sector_idx,
        "sector": sector["name"],
        "sector_desc": sector["description"],
        "phase": _phase,
        "discussion_round": _discussion_round,
        "stock_idx": _stock_idx,
        "stock": stock["name"] if stock else None,
        "stocks_total": len(sector["stocks"]),
        "market_status": is_market_open(),
    }


# ── 시황 데이터 ───────────────────────────────────────────
def _get_or_refresh_market_summary():
    snap = get_latest_market_snapshot()
    if snap:
        created = datetime.fromisoformat(snap["created_at"].replace(" ", "T"))
        age_hours = (datetime.utcnow() - created).total_seconds() / 3600
        if age_hours < MARKET_REFRESH_HOURS:
            d = json.loads(snap["data"])
            return build_market_summary(d.get("data", {}), d.get("news", []), d.get("ta")), False
    data = fetch_market_data()
    news = fetch_news(max_items=8)
    try:
        ta = fetch_technical_analysis()
    except Exception:
        ta = {}
    summary = build_market_summary(data, news, ta)
    save_market_snapshot(json.dumps({"data": data, "news": news, "ta": ta}))
    return summary, True


# ── 메시지 저장 + 요약 ────────────────────────────────────
def _do_summarize(msg_id: int, agent_name: str, content: str):
    try:
        summary = call_agent(
            "황소",
            "투자 발언을 1문장으로 요약. 종목/방향/근거만. 한국어.",
            build_summarize_prompt(agent_name, content),
        )
        with __import__("db")._conn() as con:
            con.execute("UPDATE messages SET summary=? WHERE id=?", (summary, msg_id))
    except Exception:
        pass


def _save_msg(round_id, agent_name, content):
    msg_id = save_message(round_id, agent_name, None, content)
    if content and not content.startswith("["):
        threading.Thread(target=_do_summarize, args=(msg_id, agent_name, content), daemon=True).start()
    return msg_id


# ── 결정 파싱 ─────────────────────────────────────────────
def _parse_decision(text: str) -> tuple[str, int]:
    """봇 응답에서 [결정] 파싱. (decision, weight_pct) 반환."""
    m = re.search(r'\[결정\]\s*(매수|관망|매도)', text)
    if not m:
        return "관망", DEFAULT_BUY_PCT
    decision = m.group(1)
    weight_m = re.search(r'제안비중[:\s]*(\d+)%', text)
    weight = int(weight_m.group(1)) if weight_m else DEFAULT_BUY_PCT
    weight = min(weight, MAX_BUY_PCT)
    return decision, weight


def _tally_votes(decisions: list[tuple[str, int]]) -> tuple[str, float]:
    """(final_decision, avg_weight_pct)"""
    buy_votes  = [(d, w) for d, w in decisions if d == "매수"]
    sell_votes = [(d, w) for d, w in decisions if d == "매도"]
    if len(buy_votes) >= BUY_MAJORITY:
        avg_w = sum(w for _, w in buy_votes) / len(buy_votes)
        return "매수", avg_w
    if len(sell_votes) >= SELL_MAJORITY:
        return "매도", 0.0
    return "관망", 0.0


# ── 매수/매도 실행 ────────────────────────────────────────
def _execute_buy(stock: dict, price: float, weight_pct: float, reasoning: str, round_id: int):
    pf = get_shared_portfolio()
    balance = pf["balance"]
    amount = round(balance * (weight_pct / 100))
    if amount < 100_000:
        _save_msg(round_id, "System", f"⚠️ 잔액 부족으로 {stock['name']} 매수 건너뜀 (잔액: ₩{balance:,.0f})")
        return

    pos_id, err = buy_shared_position(
        symbol=stock["name"],
        code=stock["code"],
        entry_price=price,
        amount=amount,
        reasoning=reasoning,
    )
    if err:
        _save_msg(round_id, "System", f"⚠️ 매수 실패: {err}")
        return

    msg = (f"🟢 매수 체결: {stock['name']} ({stock['code']})\n"
           f"가격: ₩{price:,.0f} | 투자금: ₩{amount:,.0f} ({weight_pct:.0f}%)\n"
           f"근거: {reasoning}")
    _save_msg(round_id, "System", msg)
    notify(f"🟢 매수: {stock['name']}", f"₩{price:,.0f} | ₩{amount:,.0f} 투자\n{reasoning}", priority="high", cooldown=0)


def _execute_sell(stock: dict, price: float, reasoning: str, round_id: int):
    positions = get_open_positions_by_symbol(stock["name"])
    if not positions:
        _save_msg(round_id, "System", f"ℹ️ {stock['name']} — 보유 중인 포지션 없음. 매도 건너뜀.")
        return

    total_pnl = 0.0
    for pos in positions:
        pnl, err = sell_shared_position(pos["id"], price)
        if pnl is not None:
            total_pnl += pnl

    direction = "이익" if total_pnl >= 0 else "손실"
    msg = (f"🔴 매도 체결: {stock['name']} ({stock['code']})\n"
           f"가격: ₩{price:,.0f} | {direction}: ₩{abs(total_pnl):,.0f}\n"
           f"근거: {reasoning}")
    _save_msg(round_id, "System", msg)
    notify(f"🔴 매도: {stock['name']}", f"₩{price:,.0f} | {direction} ₩{abs(total_pnl):,.0f}", priority="high", cooldown=0)

    # 피드백 라운드
    threading.Thread(target=_run_feedback_round, args=(stock["name"], total_pnl, reasoning), daemon=True).start()


def _run_feedback_round(stock_name: str, pnl: float, trade_summary: str):
    pnl_pct = 0.0
    try:
        round_id = create_round(f"피드백: {stock_name}")
        prompt = build_feedback_prompt(stock_name, pnl, pnl_pct, trade_summary)
        for agent_name in AGENT_ORDER[:3]:
            try:
                system = AGENT_PROFILES[agent_name]["system"]
                resp = call_agent(agent_name, system, prompt)
                _save_msg(round_id, agent_name, resp)
                time.sleep(0.5)
            except Exception:
                pass
        complete_round(round_id)
    except Exception as e:
        logger.error(f"피드백 오류: {e}")


# ── 섹터 토론 라운드 ──────────────────────────────────────
def _run_sector_discussion_round(round_id: int, market_summary: str):
    global _discussion_round
    sector = SECTORS[_sector_idx]
    _discussion_round += 1

    if _discussion_round == 1:
        # 섹터 시작 시 텔레그램 메시지 갱신
        threading.Thread(
            target=fetch_telegram_messages, kwargs={"force": True}, daemon=True
        ).start()
        _save_msg(round_id, "System",
                  f"📂 [{sector['name']}] 섹터 분석 시작\n"
                  f"{sector['description']}\n"
                  f"종목: {', '.join(s['name'] for s in sector['stocks'])}\n"
                  f"3라운드 토론 후 종목별 분석으로 이어집니다.")

    history = get_recent_messages(15)
    history_text = format_history_compact([m for m in history if m["agent_name"] not in ("System",)])

    # 텔레그램 여론 컨텍스트 (첫 봇에만 포함, 이후는 대화 히스토리로 전파됨)
    tg_context = get_cached_context(max_msgs=20)

    for i, agent_name in enumerate(AGENT_ORDER):
        system = AGENT_PROFILES[agent_name]["system"]
        prompt = build_sector_discussion_prompt(
            sector["name"], sector["description"],
            market_summary, history_text, agent_name, _discussion_round,
            tg_context=tg_context if i == 0 else "",
        )
        try:
            resp = call_agent(agent_name, system, prompt)
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"
        _save_msg(round_id, agent_name, resp)
        history = get_recent_messages(15)
        history_text = format_history_compact([m for m in history if m["agent_name"] not in ("System",)])
        time.sleep(random.uniform(0.2, 0.5))

    # 3라운드 완료 → 합의 추출 + 종목 분석으로 전환
    if _discussion_round >= SECTOR_DISCUSSION_ROUNDS:
        _extract_sector_consensus(round_id, sector, market_summary)


def _extract_sector_consensus(round_id: int, sector: dict, market_summary: str):
    global _phase, _stock_idx, _discussion_round
    try:
        recent = get_recent_messages(30)
        convo = "\n".join(
            f"{m['agent_name']}: {m['content'][:150]}"
            for m in recent if m["agent_name"] not in ("System", "User")
        )
        summary_prompt = (
            f"{sector['name']} 섹터 토론 내용:\n{convo}\n\n"
            f"위 토론을 바탕으로 섹터 합의를 3줄 이내로 요약하세요.\n"
            f"형식: 전망(긍정/부정/중립) | 핵심 이유 | 주의 사항\n한국어."
        )
        consensus = call_agent("황소", AGENT_PROFILES["황소"]["system"], summary_prompt)
        save_consensus(round_id, f"[{sector['name']} 섹터]\n{consensus}")
        _save_msg(round_id, "System",
                  f"📊 [{sector['name']}] 섹터 토론 완료\n{consensus}\n\n"
                  f"→ 종목 분석 시작: {sector['stocks'][0]['name']} 부터")
    except Exception as e:
        logger.error(f"합의 추출 오류: {e}")
        _save_msg(round_id, "System",
                  f"📊 [{sector['name']}] 섹터 토론 완료 → 종목 분석 시작")

    with _state_lock:
        _phase = "stock_analysis"
        _stock_idx = 0
        _discussion_round = 0


# ── 종목 분석 라운드 ──────────────────────────────────────
def _run_stock_analysis_round(round_id: int, market_summary: str):
    global _phase, _stock_idx, _sector_idx, _discussion_round

    sector = SECTORS[_sector_idx]
    stock = sector["stocks"][_stock_idx]

    # 종목 데이터 페치
    _save_msg(round_id, "System", f"🔍 {stock['name']} ({stock['code']}) 데이터 수집 중...")
    try:
        stock_data = fetch_stock_data(stock["code"], stock["yf"], stock["name"])
        stock_data_text = format_stock_data(stock_data)
        current_price = stock_data.get("price", 0) or 0
    except Exception as e:
        logger.error(f"종목 데이터 오류 {stock['name']}: {e}")
        stock_data_text = f"=== {stock['name']} ({stock['code']}) ===\n데이터 수집 실패"
        current_price = 0

    tg_context = get_cached_context(max_msgs=15)
    _save_msg(round_id, "System",
              f"📌 [{sector['name']}] {stock['name']} 분석\n{stock_data_text}\n\n각자 매수/관망/매도 의견을 주세요.")

    decisions: list[tuple[str, int]] = []
    decision_summary: list[str] = []

    history = get_recent_messages(10)
    history_text = format_history_compact([m for m in history if m["agent_name"] not in ("System",)])

    for i, agent_name in enumerate(AGENT_ORDER):
        system = AGENT_PROFILES[agent_name]["system"]
        prompt = build_stock_analysis_prompt(
            stock_data_text, sector["name"], market_summary, history_text, agent_name,
            tg_context=tg_context if i == 0 else "",
        )
        try:
            resp = call_agent(agent_name, system, prompt)
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"

        _save_msg(round_id, agent_name, resp)
        decision, weight = _parse_decision(resp)
        decisions.append((decision, weight))
        decision_summary.append(f"{agent_name}: {decision}")

        history = get_recent_messages(10)
        history_text = format_history_compact([m for m in history if m["agent_name"] not in ("System",)])
        time.sleep(random.uniform(0.2, 0.5))

    # 투표 결과
    final_decision, avg_weight = _tally_votes(decisions)
    vote_text = " | ".join(decision_summary)
    _save_msg(round_id, "System",
              f"🗳 투표 결과: {vote_text}\n→ 최종: {final_decision}")

    # 실행
    if final_decision == "매수" and current_price > 0:
        buy_reasoning = " / ".join(
            f"{AGENT_ORDER[i]}: {decisions[i][0]}" for i in range(len(decisions)) if decisions[i][0] == "매수"
        )
        _execute_buy(stock, current_price, avg_weight, buy_reasoning, round_id)
    elif final_decision == "매도":
        sell_reasoning = " / ".join(
            f"{AGENT_ORDER[i]}: {decisions[i][0]}" for i in range(len(decisions)) if decisions[i][0] == "매도"
        )
        _execute_sell(stock, current_price, sell_reasoning, round_id)

    # 다음 종목 or 다음 섹터로
    with _state_lock:
        _stock_idx += 1
        if _stock_idx >= len(sector["stocks"]):
            _save_msg(round_id, "System",
                      f"✅ [{sector['name']}] 섹터 모든 종목 분석 완료!\n"
                      f"다음 섹터: {SECTORS[(_sector_idx + 1) % len(SECTORS)]['name']}")
            _sector_idx = (_sector_idx + 1) % len(SECTORS)
            _phase = "sector_discussion"
            _stock_idx = 0
            _discussion_round = 0
        else:
            next_stock = sector["stocks"][_stock_idx]["name"]
            _save_msg(round_id, "System",
                      f"다음 종목 → {next_stock}")


# ── 오류 처리 ─────────────────────────────────────────────
def _handle_agent_error(agent_name: str, e: Exception):
    count = _agent_fail_count.get(agent_name, 0) + 1
    _agent_fail_count[agent_name] = count
    if is_credit_error(e):
        notify(f"⚠️ {agent_name} 크레딧 소진", str(e)[:100], priority="high")
    elif is_rate_limit(e):
        notify(f"🔄 {agent_name} API 한도", str(e)[:80], priority="default")
    if count % 3 == 0:
        notify(f"🚨 {agent_name} {count}회 연속 오류", f"{type(e).__name__}", priority="high", cooldown=0)


# ── 메인 루프 ─────────────────────────────────────────────
def run_round(market_summary=None):
    if is_user_active():
        return

    if market_summary is None:
        market_summary, market_refreshed = _get_or_refresh_market_summary()
    else:
        market_refreshed = False

    round_id = create_round(f"{SECTORS[_sector_idx]['name']} — {_phase}")

    if market_refreshed:
        save_message(round_id, "System", None, f"📊 시황 업데이트\n{market_summary[:500]}")

    try:
        if _phase == "sector_discussion":
            _run_sector_discussion_round(round_id, market_summary)
        elif _phase == "stock_analysis":
            _run_stock_analysis_round(round_id, market_summary)
    except Exception as e:
        logger.error(f"[run_round] {type(e).__name__}: {e}")
        save_message(round_id, "System", None, f"⚠️ 라운드 오류: {type(e).__name__}")

    complete_round(round_id)


# ── 사용자 메시지 처리 ─────────────────────────────────────
def handle_user_message(user_text: str):
    global _user_active_until
    _user_active_until = time.time() + USER_IDLE_SECONDS

    round_id = create_round("user-message")
    save_message(round_id, "User", None, user_text)

    history = get_recent_messages(15)
    history_text = format_history_compact([m for m in history if m["agent_name"] != "System"])

    # 3봇만 응답 (빠른 반응)
    responders = random.sample(AGENT_ORDER, 3)
    for agent_name in responders:
        system = AGENT_PROFILES[agent_name]["system"]
        prompt = build_user_response_prompt(user_text, history_text, agent_name)
        try:
            resp = call_agent(agent_name, system, prompt)
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"
        _save_msg(round_id, agent_name, resp)
        time.sleep(random.uniform(0.2, 0.5))

    complete_round(round_id)


# ── 포지션 평가 (외부 호출 가능) ──────────────────────────
def evaluate_positions():
    """보유 종목 현재가로 재평가 알림."""
    from db import get_open_positions_by_symbol
    import fetchers
    round_id = create_round("position-eval")
    save_message(round_id, "System", None, "📊 보유 포지션 현재가 평가 중...")
    complete_round(round_id)
