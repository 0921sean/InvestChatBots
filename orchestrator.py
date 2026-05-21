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
    log_stock_analyzed, was_stock_analyzed_today,
    get_evolution_notes, save_evolution_notes,
    get_active_lecture_notes, get_messages_for_round,
)
from agents import call_agent, is_claude_token_exhausted, ClaudeTokenExhausted
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
    detect_and_fetch_stocks,
)
from telegram_fetcher import fetch_telegram_messages, get_cached_context, extract_mentioned_tickers
from blog_fetcher import (get_recent_blog_context, refresh_all_blogs,
                           extract_signals_for_stock, format_historical_signals)
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
SECTOR_DISCUSSION_ROUNDS = 1   # 섹터 토론 라운드 수 (간단하게 1라운드)
BUY_MAJORITY = 5               # 매수 결정에 필요한 최소 투표 수 (8봇 중 과반)
SELL_MAJORITY = 5              # 매도 결정에 필요한 최소 투표 수
INITIAL_CAPITAL = 100_000_000  # 초기 자본 (매수 금액 기준)

# 확신도 티어: 매수 투표 수 → 초기 자본 대비 비중
BUY_CONVICTION_TIERS = {
    8: 0.15,   # 만장일치 → 15%
    7: 0.12,   # 7/8 → 12%
    6: 0.08,   # 6/8 → 8%
    5: 0.05,   # 턱걸이 → 5%
}
MARKET_REFRESH_HOURS = 2
CYCLE_REST_HOURS = 20          # 전 섹터 완료 후 다음 사이클까지 대기

# ── 상태 ─────────────────────────────────────────────────
QUANT_BOTS = {"퀀트중독자", "원칙주의자", "경력직"}

def _build_system(agent_name: str) -> str:
    """봇 시스템 프롬프트에 누적 학습 메모 주입."""
    base = AGENT_PROFILES[agent_name]["system"]
    notes = get_evolution_notes(agent_name)
    injection = f"\n\n[나의 투자 학습 메모 — 과거 매매에서 얻은 교훈]\n{notes}" if notes else ""
    return base.replace("{evolution_notes}", injection)


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
_pause_requested: bool = False  # 일시정지 플래그


def request_pause():
    globals()["_pause_requested"] = True


def clear_pause():
    globals()["_pause_requested"] = False


def is_paused() -> bool:
    return _pause_requested
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
            "드가자",
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
        return "관망", 0
    decision = m.group(1)
    return decision, 0


def _extract_reason(response: str) -> str:
    """봇 응답에서 매수 이유 추출. 여러 포맷 대응."""
    # 1순위: "[결정] 매수 | ... | 이유: TEXT" 형식
    m = re.search(r'\[결정\].*?이유[:\s]+(.+?)(?:\n|$)', response)
    if m:
        return m.group(1).strip()[:120]
    # 2순위: 텍스트 내 "이유:" 뒤
    m = re.search(r'이유[:\s]+(.+?)(?:\n|$)', response)
    if m:
        return m.group(1).strip()[:120]
    # 3순위: [결정] 줄을 제외한 마지막 의미 있는 줄
    lines = [l.strip() for l in response.split('\n')
             if l.strip() and not l.strip().startswith('[결정]')]
    return lines[-1][:120] if lines else response[:120]


def _tally_votes(decisions: list[tuple[str, int]]) -> tuple[str, float]:
    """(final_decision, buy_amount) — buy_amount는 확신도 티어 기반 고정 금액"""
    buy_votes  = [d for d, _ in decisions if d == "매수"]
    sell_votes = [d for d in (d for d, _ in decisions) if d == "매도"]
    if len(buy_votes) >= BUY_MAJORITY:
        pct = BUY_CONVICTION_TIERS.get(len(buy_votes), 0.05)
        amount = INITIAL_CAPITAL * pct
        return "매수", amount
    if len(sell_votes) >= SELL_MAJORITY:
        return "매도", 0.0
    return "관망", 0.0


# ── 매수/매도 실행 ────────────────────────────────────────
def _execute_buy(stock: dict, price: float, buy_amount: float,
                 buy_votes: list[tuple[str, str]], round_id: int):
    """
    buy_votes: [(bot_name, full_response_text), ...] — 매수 투표한 봇들의 발언
    buy_amount: 확신도 티어 기반 고정 금액 (초기 자본 대비)
    """
    pf = get_shared_portfolio()
    balance = pf["balance"]
    amount = min(round(buy_amount), balance)  # 잔액 초과 방지
    if amount < 100_000:
        _save_msg(round_id, "System", f"⚠️ 잔액 부족으로 {stock['name']} 매수 건너뜀 (잔액: ₩{balance:,.0f})")
        return

    # 매수 이유 정리
    reasons = []
    for bot_name, response in buy_votes:
        reason_text = _extract_reason(response)
        reasons.append(f"• {bot_name}: {reason_text}")

    reasoning_summary = "\n".join(reasons)
    pos_id, err = buy_shared_position(
        symbol=stock["name"],
        code=stock["code"],
        entry_price=price,
        amount=amount,
        reasoning=reasoning_summary,
        market=stock.get("market", "KRX"),
    )
    if err:
        _save_msg(round_id, "System", f"⚠️ 매수 실패: {err}")
        return

    currency = "$" if stock.get("market") == "US" else "₩"
    price_fmt = f"{currency}{price:,.2f}" if stock.get("market") == "US" else f"₩{price:,.0f}"
    chat_msg = (f"🟢 매수 체결: {stock['name']} ({stock['code']})\n"
                f"가격: {price_fmt} | 투자금: ₩{amount:,.0f}\n"
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

    # 피드백 + 학습 라운드 (백그라운드)
    # stock 정보 (yf ticker 등) 찾기
    stock_info = next(
        (s for sec in SECTORS for s in sec["stocks"] if s["name"] == stock["name"]),
        {"name": stock["name"], "code": stock.get("code",""), "yf": stock.get("code","") + ".KS"}
    )
    threading.Thread(
        target=_run_feedback_round,
        args=(stock_info, total_pnl, reasoning, positions[0].get("reasoning","") if positions else ""),
        daemon=True
    ).start()


def _run_feedback_round(stock_info: dict, pnl: float, sell_reasoning: str, buy_reasoning: str):
    """매도 후 피드백 라운드: 뉴스 수집 → 봇별 반성 → evolution_notes 업데이트."""
    from fetchers import fetch_stock_news, format_stock_news
    stock_name = stock_info["name"]
    yf_ticker = stock_info.get("yf", stock_info.get("code","") + ".KS")

    try:
        round_id = create_round(f"피드백: {stock_name}")
        pnl_pct = 0.0  # 포지션 청산 시 계산됐으나 여기선 부호만 중요

        # 1. 해당 종목 최신 뉴스/공시 수집
        news_items = fetch_stock_news(stock_name, yf_ticker, max_items=8)
        news_text = format_stock_news(stock_name, news_items)

        direction = "이익" if pnl >= 0 else "손실"
        _save_msg(round_id, "System",
                  f"📋 [{stock_name}] 투자 피드백\n"
                  f"결과: {direction} ₩{abs(pnl):,.0f}\n"
                  f"매수 근거: {buy_reasoning[:200]}\n\n{news_text}")

        # 2. 봇별 반성 생성 + evolution_notes 업데이트
        for agent_name in AGENT_ORDER:
            try:
                current_notes = get_evolution_notes(agent_name)
                reflection_prompt = f"""[{stock_name}] 투자 피드백

매수 당시 근거:
{buy_reasoning}

매도 이유:
{sell_reasoning}

결과: {direction} ₩{abs(pnl):,.0f}

최신 뉴스/공시:
{news_text}

기존 학습 메모:
{current_notes or '(없음)'}

당신은 '{agent_name}'입니다. 위 내용을 바탕으로:
1. 이 투자에서 당신의 관점({AGENT_PROFILES[agent_name]['description']})으로 무엇을 놓쳤거나 잘했나요?
2. 앞으로 비슷한 상황에서 더 나은 판단을 위해 기억해야 할 점 1-2가지를 **기존 학습 메모에 추가하는 형태**로 작성하세요.

형식:
• [날짜 없이 교훈 요약]: 구체적 내용

2문장 이내. 한국어."""

                system = _build_system(agent_name)
                reflection = call_agent(agent_name, system, reflection_prompt)
                _save_msg(round_id, agent_name, reflection)

                # evolution_notes 업데이트
                new_notes = (current_notes + "\n" + reflection).strip() if current_notes else reflection
                # 너무 길어지면 최근 5개 포인트만 유지
                lines = [l for l in new_notes.split("\n") if l.strip()]
                if len(lines) > 10:
                    lines = lines[-10:]
                save_evolution_notes(agent_name, "\n".join(lines))

                time.sleep(0.5)
            except Exception as e:
                logger.debug(f"피드백 {agent_name}: {e}")

        complete_round(round_id)
        logger.info(f"피드백 완료: {stock_name}, 봇 학습 메모 업데이트됨")

    except Exception as e:
        logger.error(f"피드백 라운드 오류: {e}")


# ── 섹터 토론 라운드 ──────────────────────────────────────
def _run_sector_discussion_round(round_id: int, market_summary: str):
    global _discussion_round
    sector = SECTORS[_sector_idx]
    _discussion_round += 1
    _persist_state()  # 재시작 후에도 진행 중인 라운드 번호 유지

    if _discussion_round == 1:
        # 섹터 시작 시 텔레그램 + 블로그 갱신
        threading.Thread(target=fetch_telegram_messages, kwargs={"force": True}, daemon=True).start()
        threading.Thread(target=refresh_all_blogs, daemon=True).start()

        # 텔레그램 언급 종목은 워치리스트에서 별도 처리 — 섹터에 추가하지 않음

        _save_msg(round_id, "System",
                  f"📂 [{sector['name']}] 섹터 분석 시작\n"
                  f"{sector['description']}\n"
                  f"종목: {', '.join(s['name'] for s in sector['stocks'])}\n"
                  f"3라운드 토론 후 종목별 분석으로 이어집니다.")

    # 텔레그램 + 블로그 컨텍스트 — 모든 봇에 제공
    tg_context = get_cached_context(max_msgs=20)
    blog_context = get_recent_blog_context(days=30, max_posts=10)
    extra_context = "\n\n".join(filter(None, [tg_context, blog_context]))

    for i, agent_name in enumerate(AGENT_ORDER):
        if _pause_requested:
            break
        # 현재 라운드 메시지만 히스토리로 사용 (이전 섹터/종목 오염 방지)
        round_msgs = get_messages_for_round(round_id)
        history_text = format_history_compact([m for m in round_msgs if m["agent_name"] not in ("System",)])
        system = _build_system(agent_name)
        prompt = build_sector_discussion_prompt(
            sector["name"], sector["description"],
            market_summary, history_text, agent_name, _discussion_round,
            tg_context=extra_context,
        )
        try:
            resp = call_agent(agent_name, system, prompt)
        except ClaudeTokenExhausted:
            _handle_agent_error(agent_name, ClaudeTokenExhausted())
            return
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"
        _save_msg(round_id, agent_name, resp)
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
        consensus = call_agent("드가자", AGENT_PROFILES["드가자"]["system"], summary_prompt)
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


def _advance_stock(round_id: int, sector: dict):
    """현재 종목 완료 후 다음 종목 또는 다음 섹터로 상태 전환."""
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
            _save_msg(round_id, "System", f"다음 종목 → {next_stock}")
        _persist_state()


# ── 종목 분석 라운드 ──────────────────────────────────────
def _run_stock_analysis_round(round_id: int, market_summary: str):
    global _phase, _stock_idx, _sector_idx, _discussion_round

    sector = SECTORS[_sector_idx]
    stock = sector["stocks"][_stock_idx]

    # 오늘 이미 분석한 종목이면 건너뜀
    if was_stock_analyzed_today(sector["name"], stock["name"]):
        _save_msg(round_id, "System",
                  f"⏭ {stock['name']} — 오늘 이미 분석 완료, 건너뜁니다.")
        _advance_stock(round_id, sector)
        return

    # 분석 시작 즉시 기록 (매수 실행 전) — 서버 재시작해도 중복 분석 방지
    log_stock_analyzed(sector["name"], stock["name"])

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
    blog_context = get_recent_blog_context(days=14, max_posts=5)

    # 네이버 종목토론방 여론
    board_context = ""
    try:
        from naver_board_fetcher import fetch_board_posts
        board_context = fetch_board_posts(stock["code"], stock["name"],
                                          market=stock.get("market", "KRX"))
    except Exception as e:
        logger.debug(f"종목토론방 오류: {e}")

    # 과거 유사 구간 신호 — 백그라운드 추출 후 주입
    historical_context = ""
    if current_price > 0:
        try:
            signals = extract_signals_for_stock(
                stock["name"], stock.get("code", ""), current_price
            )
            historical_context = format_historical_signals(signals, stock["name"], current_price)
        except Exception as e:
            logger.debug(f"신호 추출 오류: {e}")

    # 모든 봇에 컨텍스트 제공
    extra_context = "\n\n".join(filter(None, [tg_context, blog_context, board_context, historical_context]))
    _save_msg(round_id, "System",
              f"📌 [{sector['name']}] {stock['name']} 분석\n{stock_data_text}\n\n각자 매수/관망/매도 의견을 주세요.")

    # 소셜 감성 컨텍스트 추가
    try:
        from sentiment_fetcher import get_sentiment_context
        us_ticker = stock.get("code") if stock.get("market") == "US" else None
        sentiment_ctx = get_sentiment_context(
            stock_name=stock["name"],
            sector_name=sector["name"],
            us_ticker=us_ticker
        )
        if sentiment_ctx:
            extra_context = "\n\n".join(filter(None, [extra_context, sentiment_ctx]))
    except Exception as e:
        logger.debug(f"감성 컨텍스트 오류: {e}")

    # ── 체크포인트: 이미 응답한 봇 건너뜀 ──────────────
    # 토큰 소진 등으로 중단됐을 때 기존 응답 재활용
    existing_msgs = get_messages_for_round(round_id)
    already_responded = {m["agent_name"] for m in existing_msgs
                         if m["agent_name"] not in ("System",)}
    if already_responded:
        logger.info(f"체크포인트 재개: {already_responded} 건너뜀")

    decisions: list[tuple[str, int]] = []
    decision_summary: list[str] = []
    bot_responses: list[tuple[str, str]] = []   # (bot_name, full_response)

    # 기존 응답 복원
    for m in existing_msgs:
        if m["agent_name"] not in ("System",):
            d, w = _parse_decision(m["content"])
            decisions.append((d, w))
            decision_summary.append(f"{m['agent_name']}: {d}")
            bot_responses.append((m["agent_name"], m["content"]))

    for i, agent_name in enumerate(AGENT_ORDER):
        if agent_name in already_responded:
            continue  # 이미 응답한 봇 스킵
        if _pause_requested:
            break
        if is_claude_token_exhausted():
            _handle_agent_error(agent_name, ClaudeTokenExhausted())
            return
        round_msgs = get_messages_for_round(round_id)
        history_text = format_history_compact([m for m in round_msgs if m["agent_name"] not in ("System",)])
        system = _build_system(agent_name)
        prompt = build_stock_analysis_prompt(
            stock_data_text, sector["name"], market_summary, history_text, agent_name,
            tg_context=extra_context,
        )
        resp = None
        for attempt in range(2):  # 최대 2회 시도
            try:
                resp = call_agent(agent_name, system, prompt)
                break
            except ClaudeTokenExhausted:
                _handle_agent_error(agent_name, ClaudeTokenExhausted())
                return  # 토큰 소진 → 즉시 중단
            except Exception as e:
                if attempt == 1:
                    _handle_agent_error(agent_name, e)
                    resp = f"[{agent_name} 응답 오류]"
                else:
                    time.sleep(3)
        if not resp:
            resp = f"[{agent_name} 응답 오류]"

        _save_msg(round_id, agent_name, resp)
        decision, weight = _parse_decision(resp)
        decisions.append((decision, weight))
        decision_summary.append(f"{agent_name}: {decision}")
        bot_responses.append((agent_name, resp))

    # 투표 결과
    final_decision, buy_amount = _tally_votes(decisions)
    vote_text = " | ".join(decision_summary)
    _save_msg(round_id, "System",
              f"🗳 투표 결과: {vote_text}\n→ 최종: {final_decision}")

    # 실행
    if final_decision == "매수" and current_price > 0:
        buy_votes = [
            (bot_responses[i][0], bot_responses[i][1])
            for i in range(len(decisions)) if decisions[i][0] == "매수"
        ]
        _execute_buy(stock, current_price, buy_amount, buy_votes, round_id)
    elif final_decision == "매도":
        sell_reasoning = " / ".join(
            f"{AGENT_ORDER[i]}: {decisions[i][0]}" for i in range(len(decisions)) if decisions[i][0] == "매도"
        )
        _execute_sell(stock, current_price, sell_reasoning, round_id)

    _advance_stock(round_id, sector)


# ── 오류 처리 ─────────────────────────────────────────────
def _handle_agent_error(agent_name: str, e: Exception):
    count = _agent_fail_count.get(agent_name, 0) + 1
    _agent_fail_count[agent_name] = count
    if isinstance(e, ClaudeTokenExhausted):
        # 토큰 소진은 루프에서 처리하므로 여기선 로그만
        logger.warning(f"Claude 토큰 소진 — {agent_name}")
        return
    if is_credit_error(e):
        notify(f"⚠️ 크레딧 소진", str(e)[:100], priority="high", cooldown=3600)  # 1시간에 1번
    elif is_rate_limit(e):
        notify(f"🔄 API 한도", str(e)[:80], priority="default", cooldown=600)   # 10분에 1번
    if count == 3:  # 3회 연속 오류는 딱 1번만 알림
        notify(f"🚨 {agent_name} 3회 연속 응답 실패", f"{type(e).__name__}", priority="high", cooldown=1800)


# ── 일일 리셋 (스케줄러가 7시에 1회 호출) ────────────────
def reset_daily_cycle():
    """매일 아침 7시 스케줄러가 호출 — 사이클 리셋 후 루프 시작."""
    with _state_lock:
        globals()["_phase"] = "sector_discussion"
        globals()["_sector_idx"] = 0
        globals()["_discussion_round"] = 0
        globals()["_cycle_done_at"] = 0.0
        _persist_state()
    logger.info("✅ 일일 사이클 리셋 완료 — 반도체 섹터부터 시작")


# ── 텔레그램 워치리스트 스캔 ──────────────────────────────
def run_telegram_watchlist():
    """2시간마다 텔레그램 신규 종목 감지 → 봇 1라운드 즉석 토론 + 매수 판단."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    # 주말엔 안 함
    if now.weekday() >= 5:
        return
    # 메인 사이클이 실제로 진행 중인 시간대(오전 6시~오전 6시 이전)엔 막기
    # 단, 오전 6시 이전이면 메인이 아직 안 시작했으니 워치리스트는 돌아도 됨
    if _phase != "cycle_rest" and now.hour >= 6:
        return
    if is_user_active() or _pause_requested:
        return
    if is_claude_token_exhausted():
        return

    # 텔레그램 수집 (실패해도 계속)
    tg_available = False
    try:
        fetch_telegram_messages(force=True)
        tg_available = True
    except Exception as e:
        logger.debug(f"텔레그램 수집 실패: {e}")

    tickers = extract_mentioned_tickers(hours=3) if tg_available else []

    # 텔레그램 불가 시 뉴스/공시/관심 종목으로 대체
    if not tickers:
        try:
            from news_fetcher import collect_all_news_stocks, get_watched_stocks_for_watchlist
            collect_all_news_stocks()  # 뉴스에서 종목 수집 → watched_stocks 저장
            watched = get_watched_stocks_for_watchlist()
            tickers = [{"name": s["name"], "code": s.get("code",""), "market": s.get("market","KR")}
                       for s in watched]
            if tickers:
                logger.info(f"뉴스/관심 종목 {len(tickers)}개로 워치리스트 대체")
        except Exception as e:
            logger.debug(f"뉴스 대체 소스 오류: {e}")

    # 오늘 이미 분석한 종목 제외
    new_tickers = [t for t in tickers if not was_stock_analyzed_today("워치리스트", t["name"])]

    # 스캔 결과 채팅에 기록
    source_label = "텔레그램" if tg_available else "뉴스/공시"
    scan_round_id = create_round(f"{source_label} 워치리스트 스캔")
    all_names = [t["name"] for t in tickers]
    if new_tickers:
        _save_msg(scan_round_id, "System",
                  f"📡 [{source_label}] 워치리스트 스캔 완료\n언급 종목: {', '.join(all_names) if all_names else '없음'}\n신규 분석 대상: {', '.join(t['name'] for t in new_tickers)}")
    else:
        _save_msg(scan_round_id, "System",
                  f"📡 [{source_label}] 워치리스트 스캔 완료\n언급 종목: {', '.join(all_names) if all_names else '없음'}\n→ 신규 종목 없음")
    complete_round(scan_round_id)

    if not new_tickers:
        return

    market_summary, _ = _get_or_refresh_market_summary()

    for ticker in new_tickers[:3]:  # 최대 3종목
        name = ticker["name"]
        code = ticker["code"]
        market = ticker.get("market", "KR")
        log_stock_analyzed("워치리스트", name)
        # 워치리스트에서 분석한 종목 영구 저장
        try:
            from db import upsert_watched_stock
            upsert_watched_stock(name, code, "US" if market == "US" else "KR", source_label)
        except Exception:
            pass

        # 종목 데이터 수집
        try:
            yf_sym = code if market == "US" else f"{code}.KS"
            stock_data = fetch_stock_data(code, yf_sym, name,
                                          market="US" if market == "US" else "KRX",
                                          exchange="NASDAQ" if market == "US" else "KRX")
            stock_data_text = format_stock_data(stock_data)
            current_price = stock_data.get("price", 0) or 0
        except Exception:
            stock_data_text = f"=== {name} ({code}) ===\n데이터 수집 실패"
            current_price = 0

        round_id = create_round(f"워치리스트 — {name}")
        tg_ctx = get_cached_context(max_msgs=10)
        _save_msg(round_id, "System",
                  f"📡 텔레그램 언급 종목: {name}\n{stock_data_text}\n\n빠르게 한 마디씩.")

        decisions, bot_responses = [], []
        token_exhausted = False
        for agent_name in AGENT_ORDER:
            if _pause_requested or (datetime.now(KST).hour == 6 and _phase != "cycle_rest"):
                break
            if is_claude_token_exhausted():
                token_exhausted = True
                break
            round_msgs = get_messages_for_round(round_id)
            history_text = format_history_compact([m for m in round_msgs if m["agent_name"] not in ("System",)])
            system = _build_system(agent_name)
            prompt = build_stock_analysis_prompt(
                stock_data_text, "워치리스트", market_summary, history_text, agent_name,
                tg_context=tg_ctx,
            )
            resp = None
            for attempt in range(2):  # 최대 2회 시도
                try:
                    resp = call_agent(agent_name, system, prompt)
                    break
                except ClaudeTokenExhausted:
                    _handle_agent_error(agent_name, ClaudeTokenExhausted())
                    token_exhausted = True
                    break
                except Exception as e:
                    if attempt == 1:
                        _handle_agent_error(agent_name, e)
                        resp = f"[{agent_name} 응답 오류]"
                    else:
                        time.sleep(3)
            if token_exhausted:
                break
            if resp:
                _save_msg(round_id, agent_name, resp)
                d, w = _parse_decision(resp)
                decisions.append((d, w))
                bot_responses.append((agent_name, resp))

        if token_exhausted:
            complete_round(round_id)
            break

        # 응답한 봇들 기준으로 투표 집계
        if decisions:
            final, buy_amount = _tally_votes(decisions)
            buy_count = sum(1 for d, _ in decisions if d == "매수")
            vote_text = " | ".join(f"{n}: {_parse_decision(r)[0]}" for n, r in bot_responses)
            _save_msg(round_id, "System",
                      f"🗳 워치리스트 투표: {vote_text}\n→ 최종: {final} ({buy_count}표)")
            if final == "매수" and current_price > 0:
                buy_votes = [(n, r) for (n, r) in bot_responses if _parse_decision(r)[0] == "매수"]
                _execute_buy({"name": name, "code": code, "market": market}, current_price, buy_amount, buy_votes, round_id)

        complete_round(round_id)
        time.sleep(1)

    # 피델리티 멤버 분석 — 이미 수집된 캐시 재사용 (세션 재오픈 없음)
    try:
        from member_analyzer import run_member_analysis_from_cache
        run_member_analysis_from_cache()
    except Exception as e:
        logger.debug(f"멤버 분석 오류: {e}")

    # 워치리스트 완료 메시지
    done_round_id = create_round("텔레그램 워치리스트 완료")
    _save_msg(done_round_id, "System",
              f"✅ 워치리스트 스캔 완료 — 다음 실행 2시간 후")
    complete_round(done_round_id)


# ── 메인 루프 ─────────────────────────────────────────────
def run_round(market_summary=None):
    # 평일(월~금)에만 실행
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return

    if now.hour < 6:
        return

    if is_user_active():
        return


    # 사이클 휴식 중 → 아무것도 하지 않음
    if _phase == "cycle_rest":
        return

    # Claude 토큰 소진 → 복구될 때까지 대기
    if is_claude_token_exhausted():
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

    # 메시지에서 종목 감지 → TradingView 데이터 자동 페치
    stock_data_text = ""
    try:
        stock_data_text = detect_and_fetch_stocks(user_text)
        if stock_data_text:
            _save_msg(round_id, "System", f"📊 종목 데이터 자동 조회\n{stock_data_text}")
    except Exception as e:
        logger.debug(f"종목 감지 오류: {e}")

    history = get_recent_messages(15)
    history_text = format_history_compact([m for m in history if m["agent_name"] != "System"])

    # 항상 7봇 전원 응답
    responders = list(AGENT_ORDER)
    bot_responses: list[tuple[str, str]] = []

    for agent_name in responders:
        system = _build_system(agent_name)
        prompt = build_user_response_prompt(user_text, history_text, agent_name,
                                            stock_context=stock_data_text)
        try:
            resp = call_agent(agent_name, system, prompt)
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"
        _save_msg(round_id, agent_name, resp)
        bot_responses.append((agent_name, resp))
        time.sleep(random.uniform(0.2, 0.5))

    # 종목 질문이었으면 → 합의 결과 알림 발송
    if stock_data_text:
        try:
            decisions = [_parse_decision(resp) for _, resp in bot_responses]
            final, _ = _tally_votes(decisions)
            emoji = {"매수": "🟢", "매도": "🔴", "관망": "🟡"}.get(final, "⚪")
            vote_counts = {
                "매수": sum(1 for d, _ in decisions if d == "매수"),
                "관망": sum(1 for d, _ in decisions if d == "관망"),
                "매도": sum(1 for d, _ in decisions if d == "매도"),
            }
            # 종목명 추출 (stock_data_text 첫 줄에서)
            stock_name = stock_data_text.split("\n")[0].replace("===", "").strip().split("(")[0].strip()
            lines = [f"{emoji} [{stock_name}] 합의: {final}  매수{vote_counts['매수']} 관망{vote_counts['관망']} 매도{vote_counts['매도']}"]
            for agent_name, resp in bot_responses:
                decision, _ = _parse_decision(resp)
                reason = _extract_reason(resp)
                d_emoji = {"매수": "🟢", "매도": "🔴", "관망": "🟡"}.get(decision, "⚪")
                lines.append(f"{d_emoji}{agent_name}: {reason[:60]}")
            notify("\n".join(lines), f"[{stock_name}] 봇 합의: {final}")
        except Exception as e:
            logger.debug(f"종목 알림 오류: {e}")

    complete_round(round_id)

    # 답변 완료 → 봇 루프 즉시 재개 (대기 시간 리셋)
    _user_active_until = time.time()


# ── 포지션 평가 (외부 호출 가능) ──────────────────────────
STOP_LOSS_PCT = -0.20  # -20% 자동 손절


def _run_stoploss_feedback(symbol: str):
    """손절 후 봇들 피드백 라운드 — 왜 틀렸는지, 무엇을 배울 수 있는지."""
    if is_claude_token_exhausted() or _pause_requested:
        return
    try:
        market_summary, _ = _get_or_refresh_market_summary()
        feedback_round_id = create_round(f"손절 피드백 — {symbol}")
        save_message(feedback_round_id, "System", None,
                     f"📋 {symbol} 손절(-20%) 후 피드백 라운드\n"
                     f"매수 당시 어떤 판단이 틀렸는지, 앞으로 같은 실수를 피하려면 어떻게 해야 하는지 짧게 의견을 주세요.")

        for agent_name in AGENT_ORDER:
            if is_claude_token_exhausted() or _pause_requested:
                break
            system = _build_system(agent_name)
            round_msgs = get_messages_for_round(feedback_round_id)
            history_text = format_history_compact([m for m in round_msgs if m["agent_name"] not in ("System",)])
            prompt = (f"[{symbol} 손절 피드백]\n\n"
                      f"시황: {market_summary[:200]}\n\n"
                      f"[다른 봇 의견]\n{history_text or '(첫 번째 발언)'}\n\n"
                      f"{agent_name}, {symbol}이 -20% 손절됐습니다. "
                      f"본인 관점에서 매수 당시 무엇을 놓쳤는지, 앞으로 어떤 기준을 추가해야 하는지 1-2문장으로 솔직하게 말해주세요. 반드시 한국어.")
            try:
                resp = call_agent(agent_name, system, prompt)
            except ClaudeTokenExhausted:
                break
            except Exception as e:
                _handle_agent_error(agent_name, e)
                resp = f"[{agent_name} 응답 오류]"
            save_message(feedback_round_id, agent_name, None, resp)

        save_message(feedback_round_id, "System", None,
                     f"✅ {symbol} 손절 피드백 완료 — 위 반성을 다음 투자에 반영합니다.")
        complete_round(feedback_round_id)
        logger.info(f"[{symbol}] 손절 피드백 완료")
    except Exception as e:
        logger.debug(f"손절 피드백 오류 {symbol}: {e}")

def evaluate_positions():
    """보유 포지션 현재가 재평가 + -20% 손절 자동 실행."""
    from db import get_open_positions
    from fetchers import fetch_stock_price
    round_id = create_round("position-eval")
    save_message(round_id, "System", None, "📊 보유 포지션 현재가 평가 중...")

    positions = get_open_positions()
    stopped = []

    for pos in positions:
        try:
            symbol = pos["symbol"]
            code = pos["code"]
            entry = pos.get("entry_price") or 0
            market = pos.get("market", "KRX")
            if not entry:
                continue

            # 현재가 조회
            if market == "US":
                current = fetch_stock_price(code)
            else:
                current = fetch_stock_price(f"{code}.KS")

            if not current:
                continue

            pnl_pct = (current - entry) / entry

            if pnl_pct <= STOP_LOSS_PCT:
                # 손절 실행
                pnl, err = sell_shared_position(pos["id"], current)
                if not err:
                    msg = (f"🔴 손절 체결: {symbol}\n"
                           f"진입가 ₩{entry:,.0f} → 현재가 ₩{current:,.0f}\n"
                           f"손실: {pnl_pct:.1%} ({pnl:+,.0f}원)")
                    save_message(round_id, "System", None, msg)
                    notify(f"🔴 손절: {symbol}", f"{pnl_pct:.1%} | ₩{current:,.0f}", priority="high", cooldown=0)
                    stopped.append(symbol)

        except Exception as e:
            logger.debug(f"포지션 평가 오류 {pos.get('symbol')}: {e}")

    if not stopped:
        save_message(round_id, "System", None, f"✅ 손절 라인(-20%) 도달 종목 없음")

    complete_round(round_id)

    # 손절 발생 시 봇들 피드백 라운드
    if stopped:
        for symbol in stopped:
            _run_stoploss_feedback(symbol)
