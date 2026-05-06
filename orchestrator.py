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
    save_cycle_state, load_cycle_state,
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
# market: "KRX" | "US"
# KRX 종목: code=종목코드, yf=종목코드.KS
# US 종목:  code=티커, yf=티커, screener="america", exchange="NASDAQ"|"NYSE"
SECTORS = [
    # ── 국내 ──────────────────────────────────────────────
    {
        "name": "반도체",
        "description": "메모리·파운드리·반도체 장비",
        "stocks": [
            {"name": "삼성전자",   "code": "005930", "yf": "005930.KS", "market": "KRX"},
            {"name": "SK하이닉스", "code": "000660", "yf": "000660.KS", "market": "KRX"},
            {"name": "한미반도체", "code": "042700", "yf": "042700.KS", "market": "KRX"},
        ],
    },
    {
        "name": "2차전지",
        "description": "EV·ESS 배터리 셀·소재",
        "stocks": [
            {"name": "LG에너지솔루션", "code": "373220", "yf": "373220.KS", "market": "KRX"},
            {"name": "삼성SDI",        "code": "006400", "yf": "006400.KS", "market": "KRX"},
            {"name": "에코프로비엠",   "code": "247540", "yf": "247540.KS", "market": "KRX"},
        ],
    },
    {
        "name": "바이오·헬스케어",
        "description": "신약개발·의료기기·CMO",
        "stocks": [
            {"name": "삼성바이오로직스", "code": "207940", "yf": "207940.KS", "market": "KRX"},
            {"name": "셀트리온",         "code": "068270", "yf": "068270.KS", "market": "KRX"},
            {"name": "유한양행",         "code": "000100", "yf": "000100.KS", "market": "KRX"},
        ],
    },
    {
        "name": "방산·우주",
        "description": "국방·항공우주·방위산업",
        "stocks": [
            {"name": "한화에어로스페이스", "code": "012450", "yf": "012450.KS", "market": "KRX"},
            {"name": "LIG넥스원",         "code": "079550", "yf": "079550.KS", "market": "KRX"},
            {"name": "현대로템",           "code": "064350", "yf": "064350.KS", "market": "KRX"},
        ],
    },
    {
        "name": "자동차·모빌리티",
        "description": "완성차·부품·자율주행",
        "stocks": [
            {"name": "현대차",     "code": "005380", "yf": "005380.KS", "market": "KRX"},
            {"name": "기아",       "code": "000270", "yf": "000270.KS", "market": "KRX"},
            {"name": "현대모비스", "code": "012330", "yf": "012330.KS", "market": "KRX"},
        ],
    },
    {
        "name": "조선·해운",
        "description": "선박 수주·해운 운임",
        "stocks": [
            {"name": "HD한국조선해양", "code": "009540", "yf": "009540.KS", "market": "KRX"},
            {"name": "한화오션",       "code": "042660", "yf": "042660.KS", "market": "KRX"},
            {"name": "HMM",           "code": "011200", "yf": "011200.KS", "market": "KRX"},
        ],
    },
    {
        "name": "인터넷·플랫폼",
        "description": "포털·게임·커머스·핀테크",
        "stocks": [
            {"name": "NAVER",   "code": "035420", "yf": "035420.KS", "market": "KRX"},
            {"name": "카카오",  "code": "035720", "yf": "035720.KS", "market": "KRX"},
            {"name": "크래프톤", "code": "259960", "yf": "259960.KS", "market": "KRX"},
        ],
    },
    {
        "name": "금융·보험",
        "description": "은행·증권·보험",
        "stocks": [
            {"name": "KB금융",   "code": "105560", "yf": "105560.KS", "market": "KRX"},
            {"name": "신한지주", "code": "055550", "yf": "055550.KS", "market": "KRX"},
            {"name": "삼성화재", "code": "000810", "yf": "000810.KS", "market": "KRX"},
        ],
    },
    {
        "name": "철강·소재",
        "description": "철강·비철금속·화학 소재",
        "stocks": [
            {"name": "POSCO홀딩스", "code": "005490", "yf": "005490.KS", "market": "KRX"},
            {"name": "현대제철",    "code": "004020", "yf": "004020.KS", "market": "KRX"},
            {"name": "LG화학",      "code": "051910", "yf": "051910.KS", "market": "KRX"},
        ],
    },
    {
        "name": "에너지",
        "description": "정유·신재생에너지·가스",
        "stocks": [
            {"name": "SK이노베이션", "code": "096770", "yf": "096770.KS", "market": "KRX"},
            {"name": "한국가스공사", "code": "036460", "yf": "036460.KS", "market": "KRX"},
            {"name": "한화솔루션",   "code": "009830", "yf": "009830.KS", "market": "KRX"},
        ],
    },
    {
        "name": "통신",
        "description": "이동통신·네트워크·IDC",
        "stocks": [
            {"name": "SK텔레콤", "code": "017670", "yf": "017670.KS", "market": "KRX"},
            {"name": "KT",       "code": "030200", "yf": "030200.KS", "market": "KRX"},
            {"name": "LG유플러스", "code": "032640", "yf": "032640.KS", "market": "KRX"},
        ],
    },
    # ── 미국 ──────────────────────────────────────────────
    {
        "name": "미국 빅테크",
        "description": "AI·클라우드·플랫폼 메가캡",
        "stocks": [
            {"name": "엔비디아",     "code": "NVDA", "yf": "NVDA", "market": "US", "exchange": "NASDAQ"},
            {"name": "마이크로소프트", "code": "MSFT", "yf": "MSFT", "market": "US", "exchange": "NASDAQ"},
            {"name": "알파벳",       "code": "GOOGL", "yf": "GOOGL", "market": "US", "exchange": "NASDAQ"},
            {"name": "메타",         "code": "META", "yf": "META", "market": "US", "exchange": "NASDAQ"},
            {"name": "애플",         "code": "AAPL", "yf": "AAPL", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 AI·반도체",
        "description": "AI 인프라·팹리스·장비",
        "stocks": [
            {"name": "TSMC",    "code": "TSM",  "yf": "TSM",  "market": "US", "exchange": "NYSE"},
            {"name": "브로드컴", "code": "AVGO", "yf": "AVGO", "market": "US", "exchange": "NASDAQ"},
            {"name": "팔란티어", "code": "PLTR", "yf": "PLTR", "market": "US", "exchange": "NYSE"},
            {"name": "AMD",     "code": "AMD",  "yf": "AMD",  "market": "US", "exchange": "NASDAQ"},
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
CYCLE_REST_HOURS = 20          # 전 섹터 완료 후 다음 사이클까지 대기

# ── 상태 ─────────────────────────────────────────────────
def _load_state():
    """DB에서 사이클 상태 복원."""
    s = load_cycle_state()
    globals()["_sector_idx"]      = s["sector_idx"]
    globals()["_phase"]           = s["phase"]
    globals()["_stock_idx"]       = s["stock_idx"]
    globals()["_discussion_round"] = s["discussion_round"]
    globals()["_cycle_done_at"]   = s["cycle_done_at"]

def _persist_state():
    """현재 상태를 DB에 저장."""
    save_cycle_state(_sector_idx, _phase, _stock_idx, _discussion_round, _cycle_done_at)

_sector_idx: int = 0
_phase: str = "sector_discussion"
_stock_idx: int = 0
_discussion_round: int = 0
_cycle_done_at: float = 0.0
_load_state()  # 서버 시작 시 DB에서 복원
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
    stock = sector["stocks"][_stock_idx] if _phase == "stock_analysis" and _stock_idx < len(sector["stocks"]) else None
    cooldown_remaining_h = 0.0
    if _phase == "cycle_rest" and _cycle_done_at:
        cooldown_remaining_h = max(0.0, round(CYCLE_REST_HOURS - (time.time() - _cycle_done_at) / 3600, 1))
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
        "cooldown_remaining_h": cooldown_remaining_h,
        "total_sectors": len(SECTORS),
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
def _execute_buy(stock: dict, price: float, weight_pct: float,
                 buy_votes: list[tuple[str, str]], round_id: int):
    """
    buy_votes: [(bot_name, full_response_text), ...] — 매수 투표한 봇들의 발언
    """
    pf = get_shared_portfolio()
    balance = pf["balance"]
    amount = round(balance * (weight_pct / 100))
    if amount < 100_000:
        _save_msg(round_id, "System", f"⚠️ 잔액 부족으로 {stock['name']} 매수 건너뜀 (잔액: ₩{balance:,.0f})")
        return

    # 매수 이유 정리: [결정] 태그 이후 이유 부분 추출
    reasons = []
    for bot_name, response in buy_votes:
        # "[결정] 매수 | 제안비중: X% | 이유: ..." 에서 이유 부분 추출
        m = re.search(r'이유[:\s]*(.+?)(?:\n|$)', response)
        reason_text = m.group(1).strip() if m else response.split('\n')[0][:80]
        reasons.append(f"• {bot_name}: {reason_text}")

    reasoning_summary = "\n".join(reasons)
    pos_id, err = buy_shared_position(
        symbol=stock["name"],
        code=stock["code"],
        entry_price=price,
        amount=amount,
        reasoning=reasoning_summary,
    )
    if err:
        _save_msg(round_id, "System", f"⚠️ 매수 실패: {err}")
        return

    currency = "$" if stock.get("market") == "US" else "₩"
    price_fmt = f"{currency}{price:,.2f}" if stock.get("market") == "US" else f"₩{price:,.0f}"
    chat_msg = (f"🟢 매수 체결: {stock['name']} ({stock['code']})\n"
                f"가격: {price_fmt} | 투자금: ₩{amount:,.0f} ({weight_pct:.0f}%)\n"
                f"매수 근거:\n{reasoning_summary}")
    _save_msg(round_id, "System", chat_msg)

    # ntfy 알림 — 봇별 이유 포함
    notify_body = (f"{price_fmt} | ₩{amount:,.0f} 투자\n\n"
                   f"매수 이유:\n{reasoning_summary}")
    notify(f"🟢 매수 결정: {stock['name']}", notify_body, priority="high", cooldown=0)


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
        globals()["_phase"] = "stock_analysis"
        globals()["_stock_idx"] = 0
        globals()["_discussion_round"] = 0
        _persist_state()


# ── 종목 분석 라운드 ──────────────────────────────────────
def _run_stock_analysis_round(round_id: int, market_summary: str):
    global _phase, _stock_idx, _sector_idx, _discussion_round

    sector = SECTORS[_sector_idx]
    stock = sector["stocks"][_stock_idx]

    # 종목 데이터 페치
    _save_msg(round_id, "System", f"🔍 {stock['name']} ({stock['code']}) 데이터 수집 중...")
    try:
        stock_data = fetch_stock_data(
            stock["code"], stock["yf"], stock["name"],
            market=stock.get("market", "KRX"),
            exchange=stock.get("exchange", "KRX"),
        )
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
    bot_responses: list[tuple[str, str]] = []   # (bot_name, full_response)

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
        bot_responses.append((agent_name, resp))

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
        buy_votes = [
            (bot_responses[i][0], bot_responses[i][1])
            for i in range(len(decisions)) if decisions[i][0] == "매수"
        ]
        _execute_buy(stock, current_price, avg_weight, buy_votes, round_id)
    elif final_decision == "매도":
        sell_reasoning = " / ".join(
            f"{AGENT_ORDER[i]}: {decisions[i][0]}" for i in range(len(decisions)) if decisions[i][0] == "매도"
        )
        _execute_sell(stock, current_price, sell_reasoning, round_id)

    # 다음 종목 or 다음 섹터로
    with _state_lock:
        globals()["_stock_idx"] += 1
        if _stock_idx >= len(sector["stocks"]):
            next_sector_idx = _sector_idx + 1
            is_last = next_sector_idx >= len(SECTORS)

            if is_last:
                _save_msg(round_id, "System",
                          f"✅ [{sector['name']}] 섹터 완료\n\n"
                          f"🎉 오늘 전체 {len(SECTORS)}개 섹터 분석 완료!\n"
                          f"봇들이 {CYCLE_REST_HOURS}시간 휴식 후 내일 다시 반도체 섹터부터 시작합니다.")
                globals()["_sector_idx"] = 0
                globals()["_phase"] = "cycle_rest"
                globals()["_cycle_done_at"] = time.time()
            else:
                next_name = SECTORS[next_sector_idx]["name"]
                _save_msg(round_id, "System",
                          f"✅ [{sector['name']}] 섹터 완료 → 다음: {next_name}")
                globals()["_sector_idx"] = next_sector_idx
                globals()["_phase"] = "sector_discussion"

            globals()["_stock_idx"] = 0
            globals()["_discussion_round"] = 0
        else:
            next_stock = sector["stocks"][_stock_idx]["name"]
            _save_msg(round_id, "System",
                      f"다음 종목 → {next_stock}")
        _persist_state()


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

    # 전체 사이클 완료 후 휴식 — 20시간 후 다시 시작
    if _phase == "cycle_rest":
        elapsed = (time.time() - _cycle_done_at) / 3600
        if elapsed < CYCLE_REST_HOURS:
            return
        # 휴식 끝 → 첫 섹터부터 다시
        with _state_lock:
            globals()["_phase"] = "sector_discussion"
            globals()["_sector_idx"] = 0
            globals()["_discussion_round"] = 0
            _persist_state()

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
