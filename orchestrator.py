"""
AI 투자 토론 그룹 — 섹터 라운드 시스템
섹터 토론 → 종목 분석(장기 데스크 7봇 결정) → 포트폴리오 실행
"""
import json
import logging
import os
import re
import threading
import time
import random
from datetime import datetime, timezone, timedelta

logger = logging.getLogger("investchat")

from db import (
    INITIAL_BALANCE, TRADING_BALANCE,
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
    AGENT_ORDER, AGENT_PROFILES, TRADING_AGENT_ORDER,
    format_history_compact,
    build_sector_discussion_prompt,
    build_stock_analysis_prompt,
    build_user_response_prompt,
    build_chat_summary_prompt, CHAT_SUMMARY_SYSTEM,
    build_feedback_prompt,
    build_summarize_prompt,
    build_holdings_review_prompt,
    build_bottleneck_comment_prompt,
)
# 공급망 병목 비투표 자문 봇 이름(비공개). 없으면 example(빈값=비활성)로 폴백.
try:
    from strategy_private import BOTTLENECK_ADVISOR_NAME
except ImportError:
    try:
        from strategy_private_example import BOTTLENECK_ADVISOR_NAME
    except ImportError:
        BOTTLENECK_ADVISOR_NAME = ""
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
        "name": "미국 빅테크·플랫폼",
        "description": "AI·클라우드·플랫폼 메가캡",
        "stocks": [
            {"name": "애플",         "code": "AAPL",  "yf": "AAPL",  "market": "US", "exchange": "NASDAQ"},
            {"name": "마이크로소프트", "code": "MSFT",  "yf": "MSFT",  "market": "US", "exchange": "NASDAQ"},
            {"name": "알파벳",       "code": "GOOGL", "yf": "GOOGL", "market": "US", "exchange": "NASDAQ"},
            {"name": "메타",         "code": "META",  "yf": "META",  "market": "US", "exchange": "NASDAQ"},
            {"name": "아마존",       "code": "AMZN",  "yf": "AMZN",  "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 AI·반도체",
        "description": "AI 인프라·팹리스·장비",
        "stocks": [
            {"name": "엔비디아",  "code": "NVDA", "yf": "NVDA", "market": "US", "exchange": "NASDAQ"},
            {"name": "TSMC",     "code": "TSM",  "yf": "TSM",  "market": "US", "exchange": "NYSE"},
            {"name": "브로드컴",  "code": "AVGO", "yf": "AVGO", "market": "US", "exchange": "NASDAQ"},
            {"name": "AMD",      "code": "AMD",  "yf": "AMD",  "market": "US", "exchange": "NASDAQ"},
            {"name": "팔란티어",  "code": "PLTR", "yf": "PLTR", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 전기차·모빌리티",
        "description": "EV·자율주행·완성차",
        "stocks": [
            {"name": "테슬라",  "code": "TSLA", "yf": "TSLA", "market": "US", "exchange": "NASDAQ"},
            {"name": "리비안",  "code": "RIVN", "yf": "RIVN", "market": "US", "exchange": "NASDAQ"},
            {"name": "GM",     "code": "GM",   "yf": "GM",   "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 금융",
        "description": "은행·투자은행·카드",
        "stocks": [
            {"name": "JP모건",         "code": "JPM", "yf": "JPM", "market": "US", "exchange": "NYSE"},
            {"name": "뱅크오브아메리카", "code": "BAC", "yf": "BAC", "market": "US", "exchange": "NYSE"},
            {"name": "골드만삭스",      "code": "GS",  "yf": "GS",  "market": "US", "exchange": "NYSE"},
            {"name": "비자",           "code": "V",   "yf": "V",   "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 헬스케어·제약",
        "description": "제약·의료보험·바이오",
        "stocks": [
            {"name": "일라이릴리",     "code": "LLY", "yf": "LLY", "market": "US", "exchange": "NYSE"},
            {"name": "유나이티드헬스",  "code": "UNH", "yf": "UNH", "market": "US", "exchange": "NYSE"},
            {"name": "존슨앤존슨",     "code": "JNJ", "yf": "JNJ", "market": "US", "exchange": "NYSE"},
            {"name": "화이자",         "code": "PFE", "yf": "PFE", "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 소비재·리테일",
        "description": "리테일·외식·소비 브랜드",
        "stocks": [
            {"name": "코스트코",  "code": "COST", "yf": "COST", "market": "US", "exchange": "NASDAQ"},
            {"name": "월마트",    "code": "WMT",  "yf": "WMT",  "market": "US", "exchange": "NYSE"},
            {"name": "맥도날드",  "code": "MCD",  "yf": "MCD",  "market": "US", "exchange": "NYSE"},
            {"name": "나이키",    "code": "NKE",  "yf": "NKE",  "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 에너지",
        "description": "정유·가스·셰일",
        "stocks": [
            {"name": "엑슨모빌",      "code": "XOM", "yf": "XOM", "market": "US", "exchange": "NYSE"},
            {"name": "셰브론",       "code": "CVX", "yf": "CVX", "market": "US", "exchange": "NYSE"},
            {"name": "코노코필립스",  "code": "COP", "yf": "COP", "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 산업재·방산",
        "description": "항공·기계·방위산업",
        "stocks": [
            {"name": "보잉",       "code": "BA",  "yf": "BA",  "market": "US", "exchange": "NYSE"},
            {"name": "캐터필러",    "code": "CAT", "yf": "CAT", "market": "US", "exchange": "NYSE"},
            {"name": "록히드마틴",  "code": "LMT", "yf": "LMT", "market": "US", "exchange": "NYSE"},
            {"name": "RTX",       "code": "RTX", "yf": "RTX", "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 미디어·통신",
        "description": "스트리밍·미디어·통신",
        "stocks": [
            {"name": "넷플릭스",  "code": "NFLX",  "yf": "NFLX",  "market": "US", "exchange": "NASDAQ"},
            {"name": "디즈니",    "code": "DIS",   "yf": "DIS",   "market": "US", "exchange": "NYSE"},
            {"name": "컴캐스트",  "code": "CMCSA", "yf": "CMCSA", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    # ── US 섹터 확장 (2026-07-27, US-only 집중 + 공급망 병목 테마) ──
    {
        "name": "미국 광통신·옵티컬",
        "description": "옵티컬 트랜시버·광부품 (AI 인프라 병목)",
        "stocks": [
            {"name": "코히런트",  "code": "COHR", "yf": "COHR", "market": "US", "exchange": "NYSE"},
            {"name": "루멘텀",    "code": "LITE", "yf": "LITE", "market": "US", "exchange": "NASDAQ"},
            {"name": "AOI",       "code": "AAOI", "yf": "AAOI", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 전력·데이터센터 인프라",
        "description": "데이터센터 전력·냉각·전력설비 (AI 인프라 병목)",
        "stocks": [
            {"name": "버티브",    "code": "VRT", "yf": "VRT", "market": "US", "exchange": "NYSE"},
            {"name": "이튼",      "code": "ETN", "yf": "ETN", "market": "US", "exchange": "NYSE"},
            {"name": "콴타서비스", "code": "PWR", "yf": "PWR", "market": "US", "exchange": "NYSE"},
        ],
    },
    {
        "name": "미국 스토리지·메모리",
        "description": "HBM·메모리·스토리지 (AI 인프라 병목)",
        "stocks": [
            {"name": "마이크론",     "code": "MU",  "yf": "MU",  "market": "US", "exchange": "NASDAQ"},
            {"name": "웨스턴디지털", "code": "WDC", "yf": "WDC", "market": "US", "exchange": "NASDAQ"},
            {"name": "씨게이트",     "code": "STX", "yf": "STX", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 클라우드·SaaS",
        "description": "엔터프라이즈 SaaS·데이터 플랫폼",
        "stocks": [
            {"name": "서비스나우",     "code": "NOW",  "yf": "NOW",  "market": "US", "exchange": "NYSE"},
            {"name": "스노우플레이크", "code": "SNOW", "yf": "SNOW", "market": "US", "exchange": "NYSE"},
            {"name": "데이터독",       "code": "DDOG", "yf": "DDOG", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 사이버보안",
        "description": "차세대 보안·엔드포인트·클라우드 보안",
        "stocks": [
            {"name": "팔로알토",         "code": "PANW", "yf": "PANW", "market": "US", "exchange": "NASDAQ"},
            {"name": "크라우드스트라이크", "code": "CRWD", "yf": "CRWD", "market": "US", "exchange": "NASDAQ"},
            {"name": "지스케일러",       "code": "ZS",   "yf": "ZS",   "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 핀테크·결제",
        "description": "결제 네트워크·핀테크·크립토",
        "stocks": [
            {"name": "마스터카드", "code": "MA",   "yf": "MA",   "market": "US", "exchange": "NYSE"},
            {"name": "페이팔",    "code": "PYPL", "yf": "PYPL", "market": "US", "exchange": "NASDAQ"},
            {"name": "코인베이스", "code": "COIN", "yf": "COIN", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 바이오테크·유전자",
        "description": "바이오테크·유전자치료·mRNA",
        "stocks": [
            {"name": "버텍스",   "code": "VRTX", "yf": "VRTX", "market": "US", "exchange": "NASDAQ"},
            {"name": "크리스퍼", "code": "CRSP", "yf": "CRSP", "market": "US", "exchange": "NASDAQ"},
            {"name": "모더나",   "code": "MRNA", "yf": "MRNA", "market": "US", "exchange": "NASDAQ"},
        ],
    },
    {
        "name": "미국 우주·양자·로보틱스",
        "description": "우주·위성·양자컴퓨팅·로보틱스 (꿈주)",
        "stocks": [
            {"name": "로켓랩",         "code": "RKLB", "yf": "RKLB", "market": "US", "exchange": "NASDAQ"},
            {"name": "아이온큐",       "code": "IONQ", "yf": "IONQ", "market": "US", "exchange": "NYSE"},
            {"name": "인튜이티브서지컬", "code": "ISRG", "yf": "ISRG", "market": "US", "exchange": "NASDAQ"},
        ],
    },
]

# ── 라운드 설정 ──────────────────────────────────────────
SECTOR_DISCUSSION_ROUNDS = 1   # 섹터 토론 라운드 수 (간단하게 1라운드)
BUY_MAJORITY = 3               # 장기 위원회 5봇 중 과반(조절 가능 상수)
SELL_MAJORITY = 3              # 매도 과반 (5봇 중 3). 실적왕 thesis 훼손은 단독 트리거(별도)
INITIAL_CAPITAL = 75_000_000  # 장기 매수금액 기준 (계좌 시드 INITIAL_BALANCE=1억과 분리 — 시드 올려도 매수금액 불변)

# 확신도 티어: 매수 투표 수(5봇) → 플랫 금액(2026-07-27, 1억 계좌). 값 ≥1 = 절대금액.
BUY_CONVICTION_TIERS = {
    5: 15_000_000,   # 만장일치 → 1,500만
    4: 10_000_000,   # 4/5 → 1,000만
    3: 5_000_000,    # 턱걸이(과반) → 500만
}
MARKET_REFRESH_HOURS = 2
CYCLE_REST_HOURS = 20          # 전 섹터 완료 후 다음 사이클까지 대기

# 트레이딩 데스크(서브 사이클) — 공격적·별도 계좌, 5봇 중 3표(과반) 매수
TRADING_INITIAL_CAPITAL = TRADING_BALANCE
TRADING_BUY_MAJORITY = 3
TRADING_SELL_MAJORITY = 3
TRADING_BUY_CONVICTION_TIERS = {5: 0.20, 4: 0.14, 3: 0.09}  # 매수표 → 자본 대비 비중

# ── 상태 ─────────────────────────────────────────────────
QUANT_BOTS = {"퀀트중독자", "기본농부"}

def _build_system(agent_name: str) -> str:
    """봇 시스템 프롬프트에 누적 학습 메모 주입."""
    base = AGENT_PROFILES[agent_name]["system"]
    notes = get_evolution_notes(agent_name)
    injection = f"\n\n[나의 투자 학습 메모 — 과거 매매에서 얻은 교훈]\n{notes}" if notes else ""
    return base.replace("{evolution_notes}", injection)


def _format_portfolio_for_prompt(account: str = "main") -> str:
    """현재 보유 포트폴리오 요약 — 봇이 매수 판단 시 종목·섹터 비중 확인용.
    같은 종목 중복 보유는 합산. 비중 = (매수 금액 / 총 자산) %. 계좌별 분리."""
    from db import get_open_positions, get_shared_portfolio
    positions = get_open_positions(account=account)
    if not positions:
        return "보유 종목 없음 (포트폴리오 비어있음)"
    try:
        portfolio = get_shared_portfolio(account)
        balance = portfolio.get("balance", 0)
        invested = portfolio.get("invested", 0)
        total_assets = balance + invested
    except Exception:
        total_assets = sum((p.get("amount") or 0) for p in positions)
        balance = 0
        invested = total_assets

    # 같은 종목(code) 중복 매수는 합산 — KRX/KR 표기 차이는 무시
    by_code = {}
    for p in positions:
        code = p.get("code", "") or ""
        symbol = p.get("symbol", "?")
        market = p.get("market", "KR")
        # KRX와 KR은 같은 시장으로 통일
        market_norm = "US" if market == "US" else "KR"
        key = (symbol, code)  # market 빼고 합산
        amt = p.get("amount") or 0
        if key not in by_code:
            by_code[key] = {"amount": 0, "count": 0, "market": market_norm}
        by_code[key]["amount"] += amt
        by_code[key]["count"] += 1

    lines = [f"잔액 ₩{balance/1_000_000:.1f}M · 투자 ₩{invested/1_000_000:.1f}M · 총 ₩{total_assets/1_000_000:.1f}M"]
    sorted_items = sorted(by_code.items(), key=lambda x: -x[1]["amount"])
    for (symbol, code), info in sorted_items:
        amt = info["amount"]
        pct = (amt / total_assets * 100) if total_assets else 0
        cnt_note = f" (×{info['count']})" if info["count"] > 1 else ""
        lines.append(f"  · {symbol} ({info['market']}): ₩{amt/1_000_000:.1f}M, 비중 {pct:.1f}%{cnt_note}")
    return "\n".join(lines)


def _load_state():
    """DB에서 사이클 상태 복원."""
    s = load_cycle_state()
    globals()["_sector_idx"]      = s["sector_idx"]
    globals()["_phase"]           = s["phase"]
    globals()["_stock_idx"]       = s["stock_idx"]
    globals()["_discussion_round"] = s["discussion_round"]
    globals()["_cycle_done_at"]   = s["cycle_done_at"]
    globals()["_market"]          = s.get("market", "KRX")

def _persist_state():
    """현재 상태를 DB에 저장."""
    save_cycle_state(_sector_idx, _phase, _stock_idx, _discussion_round, _cycle_done_at, _market)

_sector_idx: int = 0
_phase: str = "sector_discussion"
_stock_idx: int = 0
_discussion_round: int = 0
_cycle_done_at: float = 0.0
_market: str = "KRX"           # 현재 진행 중인 사이클 시장 (KRX=국장 06시 / US=미장 18시)
_load_state()  # 서버 시작 시 DB에서 복원
_user_active_until: float = 0.0
_stop_on_credit: bool = False
_agent_fail_count: dict = {}
_pause_requested: bool = False  # 일시정지 플래그
# 메인·서브 사이클 시작 시점 토큰 스냅샷 (정기 cron으로 도는 경우 ntfy 보고용)
_main_cycle_start_token: dict | None = None
_main_cycle_start_epoch: float = 0.0
_post_check_start_token: dict | None = None
_post_check_start_epoch: float = 0.0
# 세트 종료 후 토큰 회복 감지 polling (1시간 간격)
_recovery_polling_active: bool = False
_last_recovery_at: float = 0.0  # 마지막 회복 감지 시각 (epoch)
_last_set_ended_at: float = 0.0  # 마지막 세트 종료 시각


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


def _norm_market(m) -> str:
    """'KR'/'KRX'/'US' 등을 'KRX' 또는 'US'로 정규화."""
    return "US" if str(m).upper() == "US" else "KRX"


def _active_sectors() -> list:
    """현재 사이클 시장(_market)에 해당하는 섹터만 반환.
    각 섹터의 첫 종목 market 필드로 시장을 판별한다."""
    return [s for s in SECTORS
            if s.get("stocks") and _norm_market(s["stocks"][0].get("market")) == _market]


def get_current_state() -> dict:
    sectors = _active_sectors()
    # 방어: _sector_idx가 활성 섹터 범위를 벗어나면 0으로 클램프
    idx = _sector_idx if 0 <= _sector_idx < len(sectors) else 0
    sector = sectors[idx] if sectors else {"name": "?", "description": "", "stocks": []}
    stock = sector["stocks"][_stock_idx] if _phase == "stock_analysis" and _stock_idx < len(sector["stocks"]) else None
    cooldown_remaining_h = 0.0
    next_cycle = None
    if _phase == "cycle_rest":
        # 다음 사이클 시작까지의 시간. 국장 휴식 → 오늘 18:00 미장, 미장 휴식 → 다음 평일 06:00 국장
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        if _market == "KRX":
            # 국장 끝남 → 오늘 18:00 미장. 이미 지났으면 다음 평일 06:00 국장
            next_start = now.replace(hour=18, minute=0, second=0, microsecond=0)
            if now < next_start and now.weekday() < 5:
                next_cycle = "US"
            else:
                next_start = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
                while next_start.weekday() >= 5:
                    next_start += timedelta(days=1)
                next_cycle = "KR"
        else:
            # 미장 끝남(오늘 양쪽 완료) → 다음 평일 06:00 국장
            next_start = (now + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
            # 오늘 아직 06시 전이면 오늘 06시 (드문 경우)
            today6 = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now < today6 and now.weekday() < 5:
                next_start = today6
            while next_start.weekday() >= 5:
                next_start += timedelta(days=1)
            next_cycle = "KR"
        cooldown_remaining_h = round((next_start - now).total_seconds() / 3600, 1)

    # 워치리스트·보유 점검 진행 중 여부 (락 상태로 판별)
    activity = None
    try:
        if _watchlist_lock.locked():
            activity = "watchlist"
        elif _holdings_review_lock.locked():
            activity = "holdings_review"
    except Exception:
        pass
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
        "total_sectors": len(sectors),
        "market": _market,
        "next_cycle": next_cycle,
        "activity": activity,
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


def _spoken_so_far(round_msgs) -> list:
    """이번 라운드에서 이미 발언한 봇 이름(순서 유지·중복 제거).
    미발언 봇을 인용하는 환각을 막기 위해 프롬프트에 주입한다."""
    seen = []
    for m in round_msgs:
        n = m["agent_name"]
        if n not in ("System", "User") and n not in seen:
            seen.append(n)
    return seen


# ── 결정 파싱 ─────────────────────────────────────────────
def _parse_decision(text: str) -> tuple[str, int]:
    """봇 응답에서 [결정] 파싱. (decision, weight_pct) 반환.
    '홀드'는 보유 점검 컨텍스트의 별칭 — 내부적으로 '관망'으로 정규화."""
    m = re.search(r'\[결정\]\s*(매수|관망|홀드|매도)', text)
    if not m:
        return "관망", 0
    decision = m.group(1)
    if decision == "홀드":
        decision = "관망"
    return decision, 0


# 장기 위원회 규칙 기술봇 — LLM 아닌 결정론적 신호로 참여(명세 §3: 기술=진입 타이밍 보조)
_RULE_BOTS = {"차트천재": "momentum", "역추세봇": "meanrev"}


def _rule_bot_message(agent_name: str, ohlc, context: str) -> tuple[str, str]:
    """규칙 기술봇의 (메시지, 결정). ohlc=(closes,lows,price) 또는 None.
    context='buy'(종목분석) / 'sell'(보유점검). 데이터 없으면 판단 보류→관망."""
    import trading_strategies as ts
    strat = _RULE_BOTS[agent_name]
    if not ohlc:
        return "일봉 데이터가 부족해서 이번엔 차트로 판단하기가 좀 그래 — 일단 관망할게.\n[결정] 관망", "관망"
    closes, _lows, _price = ohlc
    if context == "buy":
        if strat == "momentum":
            sig = ts.momentum_signal(closes)
            if sig:
                r = ts.rsi(closes) or 0
                return (f"차트 딱 보니까 MACD 골든크로스 떴고 RSI도 {r:.0f}이라 {sig}형 진입 자리야 — 타이밍 좋아, 나는 매수.\n[결정] 매수", "매수")
            return "차트상 아직 골든크로스도 안 났고 RSI도 조건 미달이야 — 진입 타이밍 아니라 이번엔 관망할게.\n[결정] 관망", "관망"
        if ts.meanrev_entry(closes):
            return "볼린저 하단 찍고 다시 올라오는 게 확인됐어 — 과매도 반등 자리라 나는 매수 관점이야.\n[결정] 매수", "매수"
        return "아직 밴드 안에서 그냥 흐르는 중이라 복귀 신호가 안 보여 — 나는 관망할게.\n[결정] 관망", "관망"
    # context == "sell" (보유 점검)
    if strat == "momentum":
        if ts.momentum_exit(closes):
            return "MACD 데드크로스에 RSI도 50 밑으로 빠졌어 — 추세 꺾인 거라 나는 매도 관점이야.\n[결정] 매도", "매도"
        return "아직 추세는 살아있어 — 나는 홀드 유지할게.\n[결정] 관망", "관망"
    if ts.meanrev_exit(closes):
        return "볼린저 중심선 회복했거나 하단 다시 이탈했어 — 청산 신호라 나는 매도 관점이야.\n[결정] 매도", "매도"
    return "아직 밴드 안이라 나는 홀드할게.\n[결정] 관망", "관망"


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


# ── 채팅 답변 UX: 질문 의도 라우팅 + 입문자 요약 ──────────
_INTENT_DECISION_KW = ("살까", "사도", "사야", "사?", "살만", "살 만", "팔까", "팔아", "팔지",
                       "팔 만", "매수", "매도", "들고", "보유", "물렸", "물려", "담을", "담아",
                       "비중", "손절", "익절", "교체", "갈아", "줍줍")
_INTENT_REASON_KW = ("왜", "이유", "때문", "무슨 일", "뭔 일", "뭔일", "어떻게 이렇", "왜이",
                     "왜 이", "어째서")


def _classify_chat_intent(text: str) -> str:
    """채팅 질문 의도 — 'decision'(살까/팔까·스탠스) vs 'reason'(왜/이유·설명)."""
    t = text or ""
    if any(k in t for k in _INTENT_DECISION_KW):
        return "decision"
    if any(k in t for k in _INTENT_REASON_KW):
        return "reason"
    return "decision"   # 종목 질문 기본: 스탠스 요구('어때?'·'전망')


_HOLDINGS_KW = ("뭐 들고", "뭐들고", "들고 있", "들고있", "들고 계", "보유", "포지션",
                "가지고 있", "뭐 샀", "뭐 사", "산 거", "산거", "뭐 담")


def _is_holdings_q(text: str) -> bool:
    return any(k in (text or "") for k in _HOLDINGS_KW)


def _holdings_context() -> str:
    """봇 실제 보유 현황(사실 기준) — 보유 질문에 지어내기 방지용으로 주입."""
    from db import get_open_positions, get_shared_portfolio
    lines = ["=== 봇 실제 보유 현황 (가상 포트폴리오, 사실) ==="]
    for acct, label in (("main", "장기"), ("sub", "트레이딩")):
        pf = get_shared_portfolio(acct)
        pos = get_open_positions(account=acct)
        if pos:
            items = ", ".join(f"{p['symbol']}({p.get('market', '')})" for p in pos)
        else:
            items = "보유 없음(현금 대기)"
        lines.append(f"{label} 계좌: {items} · 현금 {pf.get('balance', 0):,.0f}원")
    lines.append("※ 위 목록이 실제 보유 전부다. 목록에 없는 종목을 보유했다고 말하지 말 것.")
    return "\n".join(lines)


_EASY_DECISION = {
    "매수": "봇 다수가 '지금 관심 가져볼 만하다'는 쪽이에요.",
    "관망": "봇 다수가 '지금 당장보다 조금 더 지켜보자'는 쪽이에요.",
    "매도": "봇 다수가 '지금은 비워두는 게 낫다'는 쪽이에요.",
}


def _build_decision_summary(bot_responses, stock_name: str) -> str:
    """살까/팔까 질문의 종합 블록 — 쉬운 한 줄 + 종합(카운트) + 갈린 곳. (LLM 호출 0)"""
    decisions = [_parse_decision(resp) for _, resp in bot_responses]
    final, _ = _tally_votes(decisions)
    c = {k: sum(1 for d, _ in decisions if d == k) for k in ("매수", "관망", "매도")}
    emoji = {"매수": "🟢", "매도": "🔴", "관망": "🟡"}.get(final, "⚪")
    # 소수의견(종합과 다른 봇) = 어디서 갈렸는지
    diverge = []
    for (d, _), (name, resp) in zip(decisions, bot_responses):
        if d != final:
            diverge.append(f"{name}({d}: {_extract_reason(resp)[:20]})")
    split_line = ("봇 의견 대체로 일치." if not diverge
                  else "갈린 곳 — " + ", ".join(diverge[:3]))
    return (f"📌 한 줄 정리\n"
            f"{emoji} 쉽게: {_EASY_DECISION.get(final, '의견이 갈렸어요.')}\n"
            f"🧭 종합: [{stock_name}] 봇 {len(decisions)}명 → "
            f"매수 {c['매수']} · 관망 {c['관망']} · 매도 {c['매도']} → {final}\n"
            f"{split_line}")


def _ensure_decision_line(text: str) -> str:
    """봇 종목 분석 응답에 [결정] 줄이 없으면 안전기본값(관망)+근거를 덧붙여
    '결정 없이 텍스트만' 출력을 구조적으로 차단한다. (앱단 스키마 강제 — API tool_use 대체)
    이미 [결정]이 있으면 원문 그대로 반환."""
    t = text or ""
    if re.search(r'\[결정\]\s*(매수|관망|홀드|매도)', t):
        return t
    reason = _extract_reason(t).strip() or "근거 미상 — 보수적 관망"
    return t.rstrip() + f"\n[결정] 관망 | 이유: {reason}"


def _strip_name_prefix(name: str, text: str) -> str:
    """맨 앞 자기 이름 호칭 제거 — '차트천재요.' '역추세봇:' '실적왕,' 등 (전 봇 공통)."""
    if not text or not name:
        return text
    pat = re.compile(rf"^\s*{re.escape(name)}\s*(요[.!·]?|입니다[.!]?|이에요[.!]?|[:：\-–,])\s*")
    return pat.sub("", text, count=1).lstrip()


def _tally_votes(decisions: list[tuple[str, int]],
                 buy_majority: int = BUY_MAJORITY, sell_majority: int = SELL_MAJORITY,
                 tiers: dict = BUY_CONVICTION_TIERS, capital: float = INITIAL_CAPITAL
                 ) -> tuple[str, float]:
    """(final_decision, buy_amount) — buy_amount는 확신도 티어 기반 고정 금액.
    메인/트레이딩 데스크가 각자 임계값·티어·자본을 넘겨 쓴다."""
    buy_votes  = [d for d, _ in decisions if d == "매수"]
    sell_votes = [d for d in (d for d, _ in decisions) if d == "매도"]
    if len(buy_votes) >= buy_majority:
        tval = tiers.get(len(buy_votes), min(tiers.values()) if tiers else 0.05)
        amount = tval if tval >= 1 else capital * tval   # ≥1=절대금액(장기 플랫 티어) / <1=자본대비 비율(트레이딩)
        return "매수", amount
    if len(sell_votes) >= sell_majority:
        return "매도", 0.0
    return "관망", 0.0


# ── 매수/매도 실행 ────────────────────────────────────────
def _execute_buy(stock: dict, price: float, buy_amount: float,
                 buy_votes: list[tuple[str, str]], round_id: int, account: str = "main"):
    """
    buy_votes: [(bot_name, full_response_text), ...] — 매수 투표한 봇들의 발언
    buy_amount: 확신도 티어 기반 고정 금액 (초기 자본 대비)
    account: 'main'(장기) / 'sub'(트레이딩) — 계좌별 잔액·포지션 분리
    """
    # 오늘 이미 매수한 종목은 재매수 금지 (계좌별)
    from db import _conn as db_conn
    symbol = stock.get("code") or stock.get("name", "")
    with db_conn() as con:
        already = con.execute(
            "SELECT id FROM virtual_positions WHERE (symbol=? OR code=?) AND status='open' AND account=? AND date(opened_at)=date('now')",
            (stock["name"], symbol, account)
        ).fetchone()
    if already:
        _save_msg(round_id, "System", f"⚠️ {stock['name']} 오늘 이미 매수 완료 — 하루 1회 제한")
        return

    pf = get_shared_portfolio(account)
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
        account=account,
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


def _execute_sell(stock: dict, price: float, reasoning: str, round_id: int, account: str = "main"):
    positions = get_open_positions_by_symbol(stock["name"], account=account)
    if not positions:
        _save_msg(round_id, "System", f"ℹ️ {stock['name']} — 보유 중인 포지션 없음. 매도 건너뜀.")
        return

    total_pnl = 0.0
    for pos in positions:
        pnl, err = sell_shared_position(pos["id"], price, exit_reasoning=reasoning)
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
        for agent_name in get_speak_order():
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
    sector = _active_sectors()[_sector_idx]
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

    for i, agent_name in enumerate(get_speak_order()):
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
            spoken_names=_spoken_so_far(round_msgs),
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
    import json
    consensus_json = None
    chat_summary = None  # 채팅 메시지용 사람 읽기 좋은 텍스트

    try:
        recent = get_recent_messages(30)
        convo = "\n".join(
            f"{m['agent_name']}: {m['content'][:150]}"
            for m in recent if m["agent_name"] not in ("System", "User")
        )
        # JSON 강제 프롬프트 — 옛 포맷 잡담·중첩 평가 차단 + 구체적 내용 유도
        bots_csv = ", ".join(AGENT_ORDER)
        summary_prompt = (
            f"{sector['name']} 섹터 토론 내용:\n{convo}\n\n"
            f"위 토론을 종합해 섹터 합의를 JSON으로만 출력하세요. 다른 텍스트·코드블록·마크다운 금지.\n\n"
            f"형식:\n"
            f'{{"outlook":"긍정|중립|부정", "decision":"매수|관망|매도", '
            f'"headline":"15자 이내 핵심 한 줄 요약", '
            f'"thesis":"3-4문장 핵심 합의 — 구체 수치·종목명·근거 포함", '
            f'"drivers":["촉진 요인 한 줄(구체적)", ...], '
            f'"risks":["리스크 한 줄(구체적)", ...], '
            f'"key_picks":["주목 종목명들"], '
            f'"spokesperson":"봇이름", '
            f'"quote":"그 봇의 합의 결론 한 줄(20-40자, 봇 어투로)"}}\n\n'
            f"규칙:\n"
            f"- outlook: 정확히 '긍정/중립/부정' 셋 중 하나만. 중첩 표기('중립(부정 우위)') 금지.\n"
            f"- decision: 정확히 '매수/관망/매도' 셋 중 하나만. 섹터 전체 권고 기준.\n"
            f"- headline: 12-15자 요약 (예: 'HBM 사이클 초입, 매력적', '본업 정체·배당만 매력').\n"
            f"- thesis: 평서문 3-4문장. PER·ROE·EPS 등 구체 수치, 종목명, 다수 봇이 합의한 핵심 근거 포함. 봇 호명·이모지·**bold** 금지.\n"
            f"- drivers: 2-4개. 각 한 줄로 구체적 촉진 요인.\n"
            f"- risks: 1-3개. 각 한 줄로 구체적 리스크.\n"
            f"- key_picks: 다수가 동의한 종목 0-3개 (종목명만).\n"
            f"- spokesperson: 이번 합의를 가장 잘 대표하는 봇 1명. 다음 중 하나만 정확히: {bots_csv}\n"
            f"- quote: spokesperson이 토론에서 한 발언을 그 봇 어투로 한 줄 정리 (20-40자). 결론·핵심·태도가 드러나야 함.\n"
            f"- 핵심: 토론에서 나온 구체 숫자·내러티브를 추상화하지 말고 그대로 살리세요.\n"
            f"- **필수: 모든 필드를 빈 값 없이 채울 것.** 특히 thesis·spokesperson·quote는 위 토론에 실제 나온 내용으로 반드시 작성 — 빈 문자열·'-'·'미정'·null 금지.\n"
            f"- 출력은 여는 중괄호로 시작해 닫는 중괄호로 끝나는 JSON 하나뿐. 앞뒤 설명·코드블록 금지."
        )
        # 합의 추출 — claude haiku 사용 (opus는 큰 한국어 prompt에 60s+ 걸려 timeout)
        raw = call_agent("드가자", AGENT_PROFILES["드가자"]["system"], summary_prompt, timeout=120, model="haiku")
        consensus_json = _parse_consensus_json(raw, sector["name"])
    except Exception as e:
        logger.error(f"합의 추출 오류: {e}")
        consensus_json = {
            "v": 2, "sector": sector["name"],
            "outlook": "중립", "decision": "관망",
            "thesis": f"⚠️ 합의 생성 오류로 토론 결과 표시 못함 ({type(e).__name__}).",
            "risks": "", "key_picks": [],
        }

    # 봇_한마디 fallback 후보(토론 마지막 봇 발언) — 강제 계층에 넘김
    _fb_sp, _fb_qt = "", ""
    try:
        _bot_msgs_fb = [m for m in get_recent_messages(30)
                        if m["agent_name"] not in ("System", "User")]
        if _bot_msgs_fb:
            _last = _bot_msgs_fb[-1]
            _fb_sp = _last["agent_name"]
            _raw_q = (_last["content"] or "").strip().replace("\n", " ")
            _m_end = re.search(r"[.?!…]", _raw_q)
            _fb_qt = _raw_q[:_m_end.end()] if (_m_end and _m_end.end() < 80) \
                else (_raw_q[:60] + ("…" if len(_raw_q) > 60 else ""))
    except Exception:
        pass
    # 4필수 필드 스키마 강제 — 빈/엉뚱 출력도 항상 채워 '-'로 끝나지 않게
    consensus_json = _enforce_consensus_schema(consensus_json, sector["name"], _fb_sp, _fb_qt)

    # DB 저장: v=2 JSON 형태로 통일
    try:
        save_consensus(round_id, json.dumps(consensus_json, ensure_ascii=False))
    except Exception as e:
        logger.error(f"합의 저장 실패: {e}")

    # 채팅에 사람 읽기 좋은 텍스트로 표시
    o = consensus_json.get("outlook", "중립")
    d = consensus_json.get("decision", "관망")
    o_emoji = {"긍정":"🟢", "중립":"🟡", "부정":"🔴"}.get(o, "⚪")
    headline = consensus_json.get("headline", "")
    lines = [
        f"📊 [{sector['name']}] 섹터 토론 완료",
        f"{o_emoji} 방향: {o} · 결론: {d}" + (f" · {headline}" if headline else ""),
        f"",
        f"핵심: {consensus_json['thesis']}",
    ]
    drivers = consensus_json.get("drivers") or []
    if drivers:
        lines.append("드라이버:")
        lines.extend(f"  • {x}" for x in drivers)
    risks = consensus_json.get("risks") or []
    if risks:
        lines.append("리스크:")
        lines.extend(f"  • {x}" for x in risks)
    picks = consensus_json.get("key_picks") or []
    if picks:
        lines.append(f"주목 종목: {', '.join(picks)}")
    # 봇 한마디 — 실제 내용 있으면 표시, 없으면 '-'(정직한 빈값)
    sp, qt = consensus_json["spokesperson"], consensus_json["quote"]
    if sp != "-" and qt != "-":
        lines.append(f"{sp} 한마디: \"{qt}\"")
    else:
        lines.append("봇 한마디: -")
    lines.append("")
    lines.append(f"→ 종목 분석 시작: {sector['stocks'][0]['name']} 부터")
    _save_msg(round_id, "System", "\n".join(lines))

    with _state_lock:
        globals()["_phase"] = "stock_analysis"
        globals()["_stock_idx"] = 0
        globals()["_discussion_round"] = 0
        _persist_state()


def _consensus_fallback_regex(raw: str, sector_name: str) -> dict:
    """JSON 파싱 다 실패했을 때 — 정규식으로 필드별 개별 추출."""
    import re as _re

    def _grab_str(key, default=""):
        # "key": "value" 추출. value 안의 escaped 따옴표도 어느 정도 처리
        m = _re.search(rf'"{key}"\s*:\s*"((?:[^"\\]|\\.)*?)"', raw, _re.DOTALL)
        return (m.group(1).replace('\\"', '"').replace('\\n', ' ') if m else default).strip()

    def _grab_array(key):
        m = _re.search(rf'"{key}"\s*:\s*\[(.*?)\]', raw, _re.DOTALL)
        if not m: return []
        # 배열 안 문자열 항목들 추출
        items = _re.findall(r'"((?:[^"\\]|\\.)*?)"', m.group(1))
        return [it.replace('\\"', '"').strip() for it in items if it.strip()]

    outlook = _grab_str("outlook", "중립")
    if outlook not in ["긍정", "중립", "부정"]:
        # 텍스트 내 첫 매칭
        m = _re.search(r'(긍정|중립|부정)', raw)
        outlook = m.group(1) if m else "중립"

    decision = _grab_str("decision", "관망")
    if decision not in ["매수", "관망", "매도"]:
        m = _re.search(r'"decision"[^"]*"(매수|관망|매도)"', raw)
        decision = m.group(1) if m else "관망"

    thesis = _grab_str("thesis", "")
    if not thesis:
        # 본문에서 따옴표 안 가장 긴 문자열을 thesis로
        candidates = _re.findall(r'"((?:[^"\\]|\\.){50,})"', raw)
        if candidates:
            thesis = max(candidates, key=len)[:400]

    spokesperson = _grab_str("spokesperson", "")
    if spokesperson not in AGENT_ORDER:
        spokesperson = ""

    return {
        "v": 2,
        "sector": sector_name,
        "outlook": outlook,
        "decision": decision,
        "headline": _grab_str("headline", "")[:30],
        "thesis": (thesis or "—")[:600],
        "drivers": _grab_array("drivers")[:4],
        "risks": _grab_array("risks")[:3],
        "key_picks": _grab_array("key_picks")[:5],
        "spokesperson": spokesperson,
        "quote": _grab_str("quote", "")[:120],
        "parse_recovered": True,  # 정규식 fallback으로 복구됨 표시
    }


def _parse_consensus_json(raw: str, sector_name: str) -> dict:
    """LLM 응답에서 JSON 추출·정규화. 실패 시 raw에서 정규식으로 필드 재추출."""
    import json, re as _re
    raw_original = raw  # fallback용 백업
    raw = (raw or "").strip()
    # ```json ... ``` 블록 제거
    raw = _re.sub(r'^```(?:json)?\s*', '', raw)
    raw = _re.sub(r'\s*```$', '', raw)
    # 첫 { 부터 마지막 } 까지만 슬라이스
    s, e = raw.find('{'), raw.rfind('}')
    if s != -1 and e != -1 and e > s:
        raw = raw[s:e+1]

    # 1차 시도: 순수 JSON
    try:
        d = json.loads(raw)
    except Exception:
        # 2차 시도: 흔한 LLM 오류 수정
        cleaned = raw
        # 작은따옴표를 큰따옴표로 (단 한국어 안의 따옴표는 보존 어려움)
        cleaned = _re.sub(r"(?<=[\{\,]\s)'(\w+)'(?=\s*:)", r'"\1"', cleaned)
        # 마지막 콤마 제거 (}, 또는 ,])
        cleaned = _re.sub(r',(\s*[}\]])', r'\1', cleaned)
        try:
            d = json.loads(cleaned)
        except Exception:
            # 3차 fallback: 정규식으로 필드별 재추출
            return _consensus_fallback_regex(raw, sector_name)

    # 정규화
    def _norm(val, allowed, default):
        v = (val or "").strip()
        for a in allowed:
            if a in v: return a
        return default

    # risks·drivers는 배열 또는 문자열 둘 다 수용
    def _to_list(v, limit):
        if v is None: return []
        if isinstance(v, str):
            # 문자열이면 줄바꿈/세미콜론으로 분리 시도
            parts = [p.strip().lstrip("•-· ").strip() for p in _re.split(r'[\n;]', v) if p.strip()]
            return parts[:limit]
        if isinstance(v, list):
            return [str(x).strip().lstrip("•-· ").strip() for x in v if str(x).strip()][:limit]
        return []

    # spokesperson 화이트리스트 검증 — AGENT_ORDER(7봇) 중 하나만, 아니면 빈값
    spokesperson_raw = (d.get("spokesperson") or "").strip()
    spokesperson = spokesperson_raw if spokesperson_raw in AGENT_ORDER else ""

    return {
        "v": 2,
        "sector": sector_name,
        "outlook": _norm(d.get("outlook"), ["긍정", "중립", "부정"], "중립"),
        "decision": _norm(d.get("decision"), ["매수", "관망", "매도"], "관망"),
        "headline": (d.get("headline") or "").strip()[:30],
        "thesis": (d.get("thesis") or "—").strip()[:600],
        "drivers": _to_list(d.get("drivers"), 4),
        "risks": _to_list(d.get("risks"), 3),
        "key_picks": [str(x).strip() for x in (d.get("key_picks") or []) if str(x).strip()][:5],
        "spokesperson": spokesperson,
        "quote": (d.get("quote") or "").strip()[:120],
    }


def _enforce_consensus_schema(d, sector_name: str,
                              fallback_sp: str = "", fallback_qt: str = "") -> dict:
    """섹터 합의 4필드의 구조를 항상 완성해 반환. (앱단 — 합성 금지)
    방향=outlook, 결론=decision는 enum 기본값(중립/관망). 핵심이유=thesis,
    봇_한마디=spokesperson/quote는 실제 내용 우선, 없으면 '-'(정직한 빈값).
    가짜 문장 합성은 하지 않는다 — 근본 해결은 추후 API tool_use로."""
    EMPTY = {"", "-", "—", "–", ".", "...", "…"}
    src = d if isinstance(d, dict) else {}

    def _val(v):
        s = v.strip() if isinstance(v, str) else ("" if v is None else str(v).strip())
        return s if s and s not in EMPTY else ""

    out = dict(src)
    # 방향 / 결론 — enum 강제 (범주형이라 '-' 대신 기본값)
    out["outlook"] = src.get("outlook") if src.get("outlook") in ("긍정", "중립", "부정") else "중립"
    out["decision"] = src.get("decision") if src.get("decision") in ("매수", "관망", "매도") else "관망"
    # 핵심이유 — 모델 출력만 사용, 없으면 '-'
    out["thesis"] = (_val(src.get("thesis"))[:600]) or "-"
    # 봇_한마디 — 모델 → 토론 마지막 봇 발언(실데이터) → '-'
    sp = _val(src.get("spokesperson")) or _val(fallback_sp)
    qt = _val(src.get("quote")) or _val(fallback_qt)
    out["spokesperson"] = sp or "-"
    out["quote"] = (qt[:120]) if qt else "-"
    return out


def _advance_stock(round_id: int, sector: dict):
    """현재 종목 완료 후 다음 종목 또는 다음 섹터로 상태 전환."""
    cycle_just_finished = False
    finished_market = None
    with _state_lock:
        sectors = _active_sectors()
        mkt_label = "한국" if _market == "KRX" else "미국"
        globals()["_stock_idx"] += 1
        if _stock_idx >= len(sector["stocks"]):
            next_sector_idx = _sector_idx + 1
            is_last = next_sector_idx >= len(sectors)
            if is_last:
                first_name = sectors[0]["name"] if sectors else ""
                _save_msg(round_id, "System",
                          f"✅ [{sector['name']}] 섹터 완료\n\n"
                          f"🎉 오늘 {mkt_label} 전체 {len(sectors)}개 섹터 분석 완료!\n"
                          f"봇들이 휴식합니다. (다음 사이클 시각에 자동 재개)")
                globals()["_sector_idx"] = 0
                globals()["_phase"] = "cycle_rest"
                globals()["_cycle_done_at"] = time.time()
                cycle_just_finished = True
                finished_market = _market   # 백그라운드 점검용 — 전역 변경 레이스 방지
            else:
                next_name = sectors[next_sector_idx]["name"]
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

    # 메인 사이클이 방금 끝났으면 → 메인 토큰 ntfy → 손절·점검 (순차 백그라운드)
    if cycle_just_finished:
        # ① 메인 사이클 토큰 사용량 ntfy (정기 cron으로 도는 경우)
        if _main_cycle_start_token is not None:
            try:
                from main import _send_baseline_report
                _send_baseline_report(_main_cycle_start_token, _main_cycle_start_epoch,
                                      reason="정상 완료 (정기 06:00 메인)")
            except Exception as e:
                logger.exception(f"메인 사이클 ntfy 실패: {e}")
        # ② 손절·점검 직전 토큰 스냅샷
        try:
            from main import get_token_usage_today
            globals()["_post_check_start_token"] = get_token_usage_today()
            globals()["_post_check_start_epoch"] = time.time()
        except Exception:
            pass

        def _post_cycle_checks(m=finished_market):
            try:
                evaluate_positions(market=m)  # -20% 도달 종목 자동 손절 (해당 시장만)
            except Exception as e:
                logger.exception(f"손절 체크 실패: {e}")
            try:
                review_holdings(market=m)  # 매도 4표 이상 청산 (해당 시장만)
            except Exception as e:
                logger.exception(f"보유 점검 실패: {e}")
            # ③ 손절·점검 끝나면 토큰 사용량 ntfy
            try:
                _send_post_check_report()
            except Exception as e:
                logger.exception(f"손절·점검 ntfy 실패: {e}")
            # ④ 세트 종료 — 토큰 회복 polling 시작 (1시간 간격)
            try:
                _start_recovery_polling("메인+점검")
            except Exception as e:
                logger.exception(f"recovery polling 시작 실패: {e}")
        threading.Thread(target=_post_cycle_checks, daemon=True).start()


def _start_recovery_polling(set_name: str):
    """세트(메인+점검 / 서브) 종료 후 토큰 윈도우 회복 감지용 background polling.
    1시간 간격 ping. 회복 감지 시 ntfy 보내고 종료. 다음 세트 시작 전까지 동작."""
    global _recovery_polling_active
    if _recovery_polling_active:
        logger.info("recovery polling 이미 활성 — 중복 시작 skip")
        return
    import time as _t_init
    globals()["_recovery_polling_active"] = True
    globals()["_last_set_ended_at"] = _t_init.time()

    def _loop():
        import time as _t
        from agents import _call_claude_cli, is_claude_token_exhausted, _clear_token_exhausted
        from notifier import notify
        from main import get_token_usage_today
        start_epoch = _t.time()
        try:
            for _ in range(24):  # 최대 24시간
                _t.sleep(3600)  # 1시간
                if not _recovery_polling_active:
                    return  # 외부에서 종료 요청 (다음 세트 시작 등)
                try:
                    _call_claude_cli("test", "ping", timeout=20)
                    # ping 성공 → 윈도우 회복
                    if is_claude_token_exhausted():
                        _clear_token_exhausted()
                    globals()["_last_recovery_at"] = _t.time()
                    elapsed_min = int((_t.time() - start_epoch) / 60)
                    try:
                        cur = get_token_usage_today()
                        cur_calls = cur.get("calls", 0)
                        cur_cost = cur.get("cost_usd", 0)
                        body = (f"{set_name} 세트 종료 후 {elapsed_min}분, ping OK\n"
                                f"오늘 누적: {cur_calls}콜 · ${cur_cost:.2f}\n"
                                f"다음 세트 풀로 준비됨")
                    except Exception:
                        body = f"{set_name} 세트 종료 후 {elapsed_min}분, ping OK"
                    notify("✅ Claude 토큰 윈도우 회복", body,
                           priority="default", cooldown=0)
                    return
                except Exception as e:
                    logger.debug(f"recovery polling ping 실패: {e}")
                    continue
        finally:
            globals()["_recovery_polling_active"] = False

    threading.Thread(target=_loop, daemon=True).start()
    logger.info(f"recovery polling 시작 — {set_name} (1시간 간격)")


def _stop_recovery_polling():
    """다음 세트 시작 시 polling 중단 (다음 세트가 새 polling 시작)."""
    globals()["_recovery_polling_active"] = False


def _send_post_check_report():
    """손절 체크 + 보유 종목 점검 후 토큰 차이 ntfy."""
    if _post_check_start_token is None:
        return
    from main import get_token_usage_today
    from notifier import notify
    end_token = get_token_usage_today()
    elapsed_min = (time.time() - _post_check_start_epoch) / 60
    calls = (end_token.get("calls") or 0) - (_post_check_start_token.get("calls") or 0)
    inp = (end_token.get("input_tokens") or 0) - (_post_check_start_token.get("input_tokens") or 0)
    out = (end_token.get("output_tokens") or 0) - (_post_check_start_token.get("output_tokens") or 0)
    cache_r = (end_token.get("cache_read_tokens") or 0) - (_post_check_start_token.get("cache_read_tokens") or 0)
    cache_c = (end_token.get("cache_create_tokens") or 0) - (_post_check_start_token.get("cache_create_tokens") or 0)
    cost = (end_token.get("cost_usd") or 0) - (_post_check_start_token.get("cost_usd") or 0)
    total = inp + out + cache_r + cache_c
    def _fmt_n(n):
        if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
        if n >= 1_000: return f"{n/1_000:.1f}K"
        return str(n)
    notify(
        f"🛡 보유 종목 점검 완료 — {elapsed_min:.0f}분",
        f"손절 + 5봇 점검\n"
        f"총: {calls}콜 · {_fmt_n(total)} 토큰 · ${cost:.2f}",
        priority="high", cooldown=0,
    )


# ── 종목 분석 라운드 ──────────────────────────────────────
def _run_stock_analysis_round(round_id: int, market_summary: str):
    global _phase, _stock_idx, _sector_idx, _discussion_round

    sector = _active_sectors()[_sector_idx]
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

    # 규칙 기술봇(차트천재·역추세봇)용 일봉 1회 조회 (LLM 아닌 결정론적 신호)
    _tech_ohlc = None
    try:
        import trading_engine
        _tech_ohlc = trading_engine.fetch_one(stock.get("code"), _market)
    except Exception as e:
        logger.debug(f"기술신호 OHLC 조회 실패: {e}")

    for i, agent_name in enumerate(get_speak_order()):
        if agent_name in already_responded:
            continue  # 이미 응답한 봇 스킵
        if _pause_requested:
            break
        # 규칙 기술봇 — LLM 호출 없이 결정론적 신호로 참여
        if agent_name in _RULE_BOTS:
            msg, decision = _rule_bot_message(agent_name, _tech_ohlc, "buy")
            _save_msg(round_id, agent_name, msg)
            decisions.append((decision, 0))
            decision_summary.append(f"{agent_name}: {decision}")
            bot_responses.append((agent_name, msg))
            continue
        if is_claude_token_exhausted():
            _handle_agent_error(agent_name, ClaudeTokenExhausted())
            return
        round_msgs = get_messages_for_round(round_id)
        history_text = format_history_compact([m for m in round_msgs if m["agent_name"] not in ("System",)])
        system = _build_system(agent_name)
        prompt = build_stock_analysis_prompt(
            stock_data_text, sector["name"], market_summary, history_text, agent_name,
            tg_context=extra_context,
            portfolio_text=_format_portfolio_for_prompt(),
            spoken_names=_spoken_so_far(round_msgs),
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

        # 결정 없이 텍스트만 끝나는 출력 차단 — 저장 전 [결정] 줄 보강 (오류 응답 제외)
        if "응답 오류" not in resp:
            resp = _strip_name_prefix(agent_name, _ensure_decision_line(resp))
        _save_msg(round_id, agent_name, resp)
        decision, weight = _parse_decision(resp)
        decisions.append((decision, weight))
        decision_summary.append(f"{agent_name}: {decision}")
        bot_responses.append((agent_name, resp))

    # 전체 응답 오류 = 토큰 소진으로 판단
    error_count = sum(1 for _, r in bot_responses if "응답 오류" in r)
    if error_count == len(bot_responses) and len(bot_responses) > 0:
        from agents import _set_token_exhausted
        _set_token_exhausted()
        _save_msg(round_id, "System",
                  f"⏸ Claude 토큰 소진으로 중단됨 — {stock['name']} 분석 미완료.\n"
                  f"토큰 충전되면 자동으로 다음 슬롯에 재개됩니다.")
        _handle_agent_error("전체", Exception("모든 봇 응답 오류 — 토큰 소진으로 판단"))
        return

    # 투표 결과
    final_decision, buy_amount = _tally_votes(decisions)
    vote_text = " | ".join(decision_summary)
    _save_msg(round_id, "System",
              f"🗳 투표 결과: {vote_text}\n→ 최종: {final_decision}")

    # 공급망 병목 비투표 자문 코멘트 (미장 종목분석 한정, Phase 2 B1)
    _maybe_bottleneck_comment(round_id, stock, market_summary)

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


# ── 공급망 병목 비투표 자문 코멘트 (Phase 2 B1) ──
def _maybe_bottleneck_comment(round_id: int, stock: dict, market_summary: str):
    """미장 종목분석 라운드 뒤 병목 관점 코멘트 1건(비투표). 투표 집계와 무관(명시 호출).
    가드레일·비용: 미장 한정, 토큰 소진 시 스킵, 페르소나 비활성(빈 이름)이면 스킵."""
    if _market != "US" or not BOTTLENECK_ADVISOR_NAME:
        return
    if BOTTLENECK_ADVISOR_NAME not in AGENT_PROFILES or is_claude_token_exhausted():
        return
    try:
        system = _build_system(BOTTLENECK_ADVISOR_NAME)
        prompt = build_bottleneck_comment_prompt(stock.get("name", ""), market_summary)
        resp = call_agent(BOTTLENECK_ADVISOR_NAME, system, prompt)
        if resp:
            _save_msg(round_id, BOTTLENECK_ADVISOR_NAME, resp)
    except Exception as e:
        logger.debug(f"병목 자문 코멘트 스킵: {e}")


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


# ── 메인 사이클 비활성 토글 ────────────────────────────
_main_disabled = False

# ── 봇 발언 순서 랜덤화 토글 ────────────────────────────
# 서버 재시작 시에도 켜진 상태 유지 (사용자 선호)
_random_speak_order = True


def enable_random_speak_order():
    """다음 라운드부터 봇 발언 순서를 랜덤화."""
    global _random_speak_order
    _random_speak_order = True


def disable_random_speak_order():
    global _random_speak_order
    _random_speak_order = False


def is_random_speak_order() -> bool:
    return _random_speak_order


def get_speak_order(roster: list = None) -> list:
    """발언 순서. roster 미지정 시 메인 AGENT_ORDER. 토글 ON이면 매 호출 shuffle."""
    bots = list(roster) if roster else list(AGENT_ORDER)
    if _random_speak_order:
        random.shuffle(bots)
    return bots


def pause_main_cycle():
    """메인 사이클 비활성 — 6시 reset도, run_round도 다음 호출부터 스킵."""
    global _main_disabled
    _main_disabled = True


def resume_main_cycle():
    global _main_disabled
    _main_disabled = False


def is_main_disabled() -> bool:
    return _main_disabled


# ── 시장별 리셋 (스케줄러가 06시=국장 / 18시=미장 호출) ────────────
# 국장(KR) 사이클 임시 off 토글 (2026-07-27, US-only 집중 실험). 재개: True로 바꾸고 서버 재시작.
# ⚠️ run_round의 'hour<6 & KRX' §4 가드와는 별개 — 그건 그대로 둔다.
KR_CYCLE_ENABLED = False


def reset_market_cycle(market: str = "KRX"):
    """평일 06:00(KRX) / 18:00(US) 스케줄러가 호출 — 해당 시장 사이클 리셋 + 시작 안내."""
    market = _norm_market(market)
    if _main_disabled:
        logger.info("메인 사이클 비활성 — reset 스킵")
        return
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    # 주말(토·일)엔 메인 사이클 안 돔 — reset도 안 함, 시작 안내도 안 함
    if now.weekday() >= 5:
        logger.info(f"주말({['월','화','수','목','금','토','일'][now.weekday()]}) — reset_market_cycle({market}) 스킵")
        return

    # 직전 사이클이 미완(cycle_rest 아님)인데 새 시장 리셋이 들어오면 경고 (국장 우선)
    if _phase != "cycle_rest":
        logger.warning(f"⚠️ 직전 사이클({_market}, phase={_phase}) 미완 상태에서 {market} 리셋 — 덮어쓰고 진행")

    # 메인 시작 시점 토큰 스냅샷 — cycle_just_finished 시 ntfy 보고용
    try:
        from main import get_token_usage_today
        globals()["_main_cycle_start_token"] = get_token_usage_today()
        globals()["_main_cycle_start_epoch"] = now.timestamp()
    except Exception as e:
        logger.debug(f"메인 시작 토큰 스냅샷 실패: {e}")

    # 이전 세트의 recovery polling 중단
    _stop_recovery_polling()

    with _state_lock:
        globals()["_market"] = market
        globals()["_phase"] = "sector_discussion"
        globals()["_sector_idx"] = 0
        globals()["_stock_idx"] = 0
        globals()["_discussion_round"] = 0
        globals()["_cycle_done_at"] = 0.0
        _persist_state()

    mkt_label = "국장(한국)" if market == "KRX" else "미장(미국)"
    sectors = _active_sectors()
    # 채팅 로그에 사이클 시작 안내
    try:
        round_id = create_round(f"{mkt_label} 메인 사이클 시작 — {now.strftime('%m-%d')}")
        order = ' → '.join(s['name'] for s in sectors[:5])
        last = sectors[-1]['name'] if sectors else ""
        _save_msg(round_id, "System",
                  f"🌅 {mkt_label} 메인 사이클 시작 ({now.strftime('%Y-%m-%d %H:%M KST')})\n"
                  f"오늘 분석 대상: {len(sectors)}개 섹터\n"
                  f"순서: {order} → ... → {last}\n"
                  f"섹터별 3라운드 토론 + 종목 분석 후 자동으로 보유 종목 점검까지 진행됩니다.")
        complete_round(round_id)
    except Exception as e:
        logger.debug(f"reset 메시지 기록 실패: {e}")

    first = sectors[0]['name'] if sectors else "?"
    logger.info(f"✅ {mkt_label} 사이클 리셋 완료 — {first} 섹터부터 시작")


def reset_kr_cycle():
    """스케줄러: 평일 06:00 — 국장 메인 사이클. 지수 벤치마크 일일 스냅샷도 여기서 점검."""
    try:
        from datetime import datetime, timezone, timedelta
        import benchmark
        today = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        benchmark.capture_snapshot(today)
    except Exception as e:
        logger.warning(f"벤치마크 스냅샷 실패: {e}")
    if not KR_CYCLE_ENABLED:
        logger.info("국장 사이클 비활성(KR_CYCLE_ENABLED=False) — 벤치마크만, 사이클 스킵")
        return
    reset_market_cycle("KRX")


def reset_us_cycle():
    """스케줄러: 평일 18:00 — 미장 메인 사이클."""
    reset_market_cycle("US")


# 하위호환 별칭 (기존 호출처/측정 엔드포인트 대비) — 기본 국장
def reset_daily_cycle():
    reset_market_cycle("KRX")


# ── 텔레그램 워치리스트 스캔 ──────────────────────────────
_watchlist_lock = threading.Lock()
_watchlist_disabled = False  # True면 새 슬롯은 스킵, 진행 중도 다음 봇 호출 직전 중단

# 서브 사이클 1회당 분석할 최대 종목 수 (후보가 많으면 무작위 샘플)
SUB_CYCLE_MAX_STOCKS = 8   # 서브 슬롯당 분석 종목 상한 (토큰 절약 ↔ 발굴 폭 균형). 소싱 개선 후 20→8


def pause_watchlist():
    """워치리스트 비활성 + 현재 진행 중 즉시 중단 요청."""
    global _watchlist_disabled
    _watchlist_disabled = True


def resume_watchlist():
    global _watchlist_disabled
    _watchlist_disabled = False


def is_watchlist_disabled() -> bool:
    return _watchlist_disabled


def run_telegram_watchlist(force: bool = False, market: str = None):
    """[트레이딩 계좌] 서브 사이클 = 규칙엔진 스캔(2026-07-09 컷오버).
    차트천재(모멘텀)·역추세봇(볼린저) 규칙으로 유니버스 스캔 매수/매도. LLM·토큰 사용 없음.
    구 5봇 LLM 경로는 `_run_telegram_watchlist_inner`에 보존(미사용). market=None이면 KR·US 둘 다."""
    if _watchlist_disabled and not force:
        logger.info("워치리스트 비활성 상태 — 이번 슬롯 스킵")
        return
    if _pause_requested and not force:
        return
    # 이미 실행 중이면 건너뜀 (동시 실행 방지)
    if not _watchlist_lock.acquire(blocking=False):
        logger.info("트레이딩 규칙엔진 이미 실행 중 — 이번 스케줄 건너뜀")
        return

    try:
        import trading_engine
        for mk in ([market] if market else ["KRX", "US"]):
            trading_engine.run_trading_cycle(mk)
    finally:
        _watchlist_lock.release()


def run_watchlist_kr():
    """스케줄러: 평일 09:05 KST(개장 직후) — 국장 트레이딩 규칙엔진."""
    if not KR_CYCLE_ENABLED:
        logger.info("국장 서브 사이클 비활성(KR_CYCLE_ENABLED=False) — 스킵")
        return
    run_telegram_watchlist(market="KRX")


def run_watchlist_us():
    """스케줄러: 평일 09:35 ET(개장 직후·서머타임) — 미장 트레이딩 규칙엔진."""
    run_telegram_watchlist(market="US")


def _run_telegram_watchlist_inner(force: bool = False, market: str = None):
    """워치리스트 실제 실행 로직."""
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)

    if not force:
        # 요일 가드는 scheduler.py cron이 책임 (월요일 0시 제외 / 12·18시 평일만)
        # 여기선 메인 진행 중일 때만 막음
        if _phase != "cycle_rest" and now.hour >= 6:
            return
        # 사용자 활동/일시정지 중에도 정기 cron은 막음 (측정·수동 트리거는 force=True로 우회)
        if is_user_active() or _pause_requested:
            return
    # force=True는 모든 가드 우회 (측정·수동 트리거 의도)

    # 이전 세트의 recovery polling 중단 (서브 시작)
    _stop_recovery_polling()

    # 정기 cron으로 도는 경우(force=False)에도 토큰 사용량 ntfy 보고
    sub_start_token = None
    sub_start_epoch = None
    if not force:
        try:
            from main import get_token_usage_today
            sub_start_token = get_token_usage_today()
            sub_start_epoch = now.timestamp()
        except Exception:
            pass

    # 토큰 소진 시 — force(측정/수동)면 즉시 종료, 정기 cron이면 60분 대기
    if is_claude_token_exhausted():
        from agents import _call_claude_cli
        from notifier import notify as _notify
        if force:
            _notify("⏸ 서브 사이클 측정 중단", "Claude 토큰 소진 — 회복 후 다시 트리거하세요",
                    priority="high", cooldown=0)
            return
        _notify("⏸ 서브 사이클 대기", "Claude 토큰 소진 — 충전 후 자동 재개", priority="default", cooldown=1800)
        for _ in range(60):  # 최대 60분 대기
            time.sleep(60)
            try:
                _call_claude_cli("test", "ping")
                _notify("▶ 서브 사이클 재개", "토큰 복구됨", priority="default", cooldown=0)
                break
            except ClaudeTokenExhausted:
                continue
        else:
            return  # 60분 후에도 안 되면 포기

    # 텔레그램 수집 (계정 차단 등으로 비활성일 수 있음 — TELEGRAM_ENABLED)
    from telegram_fetcher import telegram_enabled
    tg_available = False
    if telegram_enabled():
        try:
            fetch_telegram_messages(force=True)
            tg_available = True
        except Exception as e:
            logger.debug(f"텔레그램 수집 실패: {e}")

    tg_tickers = extract_mentioned_tickers(hours=3) if tg_available else []

    # 뉴스/공시/관심 종목 항상 병렬 수집 (텔레그램과 무관하게)
    news_tickers = []
    try:
        from news_fetcher import collect_all_news_stocks, get_watched_stocks_for_watchlist
        collect_all_news_stocks()  # 뉴스에서 종목 수집 → watched_stocks 저장
        watched = get_watched_stocks_for_watchlist()
        news_tickers = [{"name": s["name"], "code": s.get("code",""), "market": s.get("market","KR"),
                         "source": s.get("source","")}
                        for s in watched]
    except Exception as e:
        logger.debug(f"뉴스 소스 오류: {e}")

    # 네이버 급등·거래량 상위 발굴 (국장 전용 — 로그인·LLM 불필요, pykrx 대체)
    naver_tickers = []
    if market is None or _norm_market(market) == "KRX":
        try:
            from discovery_naver import fetch_kr_movers
            naver_tickers = fetch_kr_movers()
        except Exception as e:
            logger.debug(f"네이버 발굴 오류: {e}")

    # 야후 급등·거래량 상위 발굴 (미장 전용 — yfinance 내장 스크리너)
    us_tickers = []
    if market is None or _norm_market(market) == "US":
        try:
            from discovery_us import fetch_us_movers
            us_tickers = fetch_us_movers()
        except Exception as e:
            logger.debug(f"야후 미국 발굴 오류: {e}")

    # 소스 합치기 (중복 제거)
    seen_names = set()
    tickers = []
    for t in tg_tickers + news_tickers + naver_tickers + us_tickers:
        if t["name"] not in seen_names:
            seen_names.add(t["name"])
            tickers.append(t)

    src_parts = (["텔레그램"] if tg_available else []) + ["뉴스"]
    if naver_tickers:
        src_parts.append("네이버발굴")
    if us_tickers:
        src_parts.append("야후발굴")
    source_label = "+".join(src_parts)

    # 메인 섹터 종목 이름+코드 목록 (워치리스트에서 제외)
    main_stock_names = {s["name"] for sector in SECTORS for s in sector["stocks"]}
    main_stock_codes = {s["code"] for sector in SECTORS for s in sector["stocks"]}

    # 시장 필터 (KRX=국장 12시 / US=미장 24시). market=None이면 전체.
    mkt_filter = _norm_market(market) if market else None

    # 오늘 이미 분석했거나 메인 섹터 종목은 제외 (이름 또는 코드로 체크) + 시장 필터
    new_tickers = [t for t in tickers
                   if not was_stock_analyzed_today("워치리스트", t["name"])
                   and t["name"] not in main_stock_names
                   and t.get("code", "") not in main_stock_codes
                   and (mkt_filter is None or _norm_market(t.get("market")) == mkt_filter)]

    # watched_stocks에서도 메인 종목 제거 (뉴스가 재추가한 경우 대비)
    try:
        from db import _conn as db_conn
        with db_conn() as con:
            ph = ",".join("?" * len(main_stock_names))
            con.execute(f"DELETE FROM watched_stocks WHERE name IN ({ph})", list(main_stock_names))
    except Exception:
        pass

    # new_tickers 중 실제 가격 있는 종목만 사전 필터링
    import yfinance as _yf
    valid_tickers = []
    for t in new_tickers:
        c = t.get("code", "")
        if not c or c.upper() in ("NONE", ""):
            continue
        try:
            sym = c if t.get("market") == "US" else f"{c}.KS"
            info = _yf.Ticker(sym).fast_info
            price = getattr(info, 'last_price', None) or getattr(info, 'previous_close', None)
            if price:
                valid_tickers.append(t)
        except Exception:
            pass

    # 후보가 많으면 N개만 선정. 단 어닝 종목은 우선 채움(누적 모니터링), 나머지 슬롯은 무작위.
    import random as _random
    candidate_count = len(valid_tickers)
    if candidate_count > SUB_CYCLE_MAX_STOCKS:
        priority = [t for t in valid_tickers if "earnings" in (t.get("source") or "").lower()]
        rest = [t for t in valid_tickers if "earnings" not in (t.get("source") or "").lower()]
        if len(priority) >= SUB_CYCLE_MAX_STOCKS:
            valid_tickers = _random.sample(priority, SUB_CYCLE_MAX_STOCKS)
        else:
            fill = _random.sample(rest, SUB_CYCLE_MAX_STOCKS - len(priority)) if rest else []
            valid_tickers = priority + fill

    # 스캔 결과 채팅에 기록 — "오늘 분석 시작" 명시
    scan_round_id = create_round(f"{source_label} 서브 사이클 스캔")
    if valid_tickers:
        sample_note = (f"후보 {candidate_count}개 → 무작위 {len(valid_tickers)}개 선정"
                       if candidate_count > SUB_CYCLE_MAX_STOCKS
                       else f"후보 {candidate_count}개 전체 분석")
        _save_msg(scan_round_id, "System",
                  f"📡 [{source_label}] 서브 사이클 스캔 완료\n"
                  f"{sample_note}\n\n"
                  f"🎯 오늘 분석 시작 → {', '.join(t['name'] for t in valid_tickers)}")
    else:
        _save_msg(scan_round_id, "System",
                  f"📡 [{source_label}] 서브 사이클 스캔 완료\n→ 분석할 신규 종목 없음")
    complete_round(scan_round_id)

    if not valid_tickers:
        return

    market_summary, _ = _get_or_refresh_market_summary()

    for ticker in valid_tickers:
        if _watchlist_disabled:
            logger.info("워치리스트 비활성 — 종목 루프 중단")
            return
        name = ticker["name"]
        code = ticker["code"]
        market = ticker.get("market", "KR")

        # 코드 없으면 스킵 (log도 안 함)
        if not code or code.upper() in ("NONE", ""):
            continue

        # 가격 조회 — TradingView 실패 시 yfinance fallback
        try:
            yf_sym = code if market == "US" else f"{code}.KS"
            stock_data = fetch_stock_data(code, yf_sym, name,
                                          market="US" if market == "US" else "KRX",
                                          exchange="NASDAQ" if market == "US" else "KRX")
            current_price = stock_data.get("price", 0) or 0
            # TradingView 가격 없으면 yfinance로 직접 조회
            if not current_price:
                import yfinance as yf
                t = yf.Ticker(yf_sym)
                current_price = t.fast_info.last_price or t.fast_info.previous_close or 0
                if current_price:
                    stock_data["price"] = current_price
            if not current_price:
                continue
            stock_data_text = format_stock_data(stock_data)
        except Exception:
            continue

        # 실제 분석할 종목만 로그 기록
        log_stock_analyzed("워치리스트", name)
        try:
            from db import upsert_watched_stock
            upsert_watched_stock(name, code, "US" if market == "US" else "KR", source_label)
        except Exception:
            pass

        round_id = create_round(f"서브 사이클 — {name}")
        tg_ctx = get_cached_context(max_msgs=10)
        _save_msg(round_id, "System",
                  f"📡 텔레그램 언급 종목: {name}\n{stock_data_text}\n\n빠르게 한 마디씩.")

        decisions, bot_responses = [], []
        token_exhausted = False
        for agent_name in get_speak_order(TRADING_AGENT_ORDER):   # 트레이딩 데스크 봇
            if _watchlist_disabled:
                _save_msg(round_id, "System", f"⏸ 서브 사이클 비활성화 — {name} 분석 중단")
                complete_round(round_id)
                logger.info("워치리스트 비활성 신호 — 봇 루프 중단")
                return
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
                portfolio_text=_format_portfolio_for_prompt(account="sub"),
                spoken_names=_spoken_so_far(round_msgs),
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
            _save_msg(round_id, "System",
                      f"⏸ Claude 토큰 소진 — 서브 사이클 중단 ({name} 분석 미완).\n"
                      f"다음 서브 사이클 슬롯(KST 00·12·18시) 또는 토큰 충전 후 자동 재개.")
            complete_round(round_id)
            break

        # 전체 응답 오류 = 토큰 소진으로 판단
        err_cnt = sum(1 for _, r in bot_responses if "응답 오류" in r)
        if err_cnt == len(bot_responses) and len(bot_responses) > 0:
            from agents import _set_token_exhausted
            _set_token_exhausted()
            _save_msg(round_id, "System",
                      f"⏸ 모든 봇 응답 실패 — 토큰 소진 판단. 다음 슬롯에서 자동 재개.")
            complete_round(round_id)
            break

        # 응답한 봇들 기준으로 투표 집계 (트레이딩 데스크 기준: 5봇 중 3표·자본 3천만)
        if decisions:
            final, buy_amount = _tally_votes(
                decisions, TRADING_BUY_MAJORITY, TRADING_SELL_MAJORITY,
                TRADING_BUY_CONVICTION_TIERS, TRADING_INITIAL_CAPITAL)
            buy_count = sum(1 for d, _ in decisions if d == "매수")
            vote_text = " | ".join(f"{n}: {_parse_decision(r)[0]}" for n, r in bot_responses)
            _save_msg(round_id, "System",
                      f"🗳 트레이딩 데스크 투표: {vote_text}\n→ 최종: {final} ({buy_count}표)")
            if final == "매수" and current_price > 0:
                buy_votes = [(n, r) for (n, r) in bot_responses if _parse_decision(r)[0] == "매수"]
                _execute_buy({"name": name, "code": code, "market": market}, current_price, buy_amount, buy_votes, round_id, account="sub")

        complete_round(round_id)
        time.sleep(1)

    # 피델리티 멤버 분석 — 이미 수집된 캐시 재사용 (세션 재오픈 없음)
    try:
        from member_analyzer import run_member_analysis_from_cache
        run_member_analysis_from_cache()
    except Exception as e:
        logger.debug(f"멤버 분석 오류: {e}")

    # 서브 사이클 완료 메시지
    done_round_id = create_round("서브 사이클 완료")
    _save_msg(done_round_id, "System",
              f"✅ 서브 사이클 완료 — 다음 슬롯 대기 (KST 00·12·18시)")
    complete_round(done_round_id)

    # 정기 cron(force=False) 끝나면 토큰 사용량 ntfy
    if sub_start_token is not None:
        try:
            from main import _send_watchlist_report
            _send_watchlist_report(sub_start_token, sub_start_epoch,
                                   reason=f"정상 완료 (정기 {now.strftime('%H:%M')} KST)")
        except Exception as e:
            logger.exception(f"서브 사이클 ntfy 실패: {e}")

    # 서브 세트 종료 — 토큰 회복 polling 시작 (1시간 간격)
    try:
        _start_recovery_polling(f"서브 {now.strftime('%H:%M')}")
    except Exception as e:
        logger.exception(f"recovery polling 시작 실패: {e}")


# ── 메인 루프 ─────────────────────────────────────────────
def run_round(market_summary=None, force=False):
    """force=True면 weekday/시간 체크 우회 (측정용 1회 실행)."""
    if _main_disabled and not force:
        return
    # 평일(월~금)에만 실행 — force 시 우회
    if not force:
        from datetime import datetime, timezone, timedelta
        KST = timezone(timedelta(hours=9))
        now = datetime.now(KST)
        if now.weekday() >= 5:
            return
        # 자정~06시엔 국장 사이클만 차단 (6시 전 국장 실행 금지 — 절대 규칙).
        # 진행 중인 미장(US) 사이클은 자정을 넘겨 마저 끝낼 수 있게 허용.
        if 0 <= now.hour < 6 and _market == "KRX":
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

    round_id = create_round(f"{_active_sectors()[_sector_idx]['name']} — {_phase}")

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
def chat_queue_worker():
    """채팅 질문 큐를 FIFO로 1건씩 직렬 처리 (안전장치 1/3). 데몬 스레드로 기동."""
    from db import claim_next_chat_question, complete_chat_question
    while True:
        try:
            q = claim_next_chat_question()
            if not q:
                time.sleep(1.0)
                continue
            try:
                handle_user_message(q["content"])
                complete_chat_question(q["id"], "done")
            except Exception as e:
                logger.error(f"채팅 큐 처리 오류 (id={q['id']}): {e}")
                complete_chat_question(q["id"], "error")
        except Exception as e:
            logger.error(f"채팅 큐 워커 루프 오류: {e}")
            time.sleep(2.0)


# 라이브(fund)에서 유저 채팅에 답하는 봇 — 현재 데스크 판단봇(P·린치/W·버핏/H·실적/S·병목).
# A(애널리스트)·Q(룰 타이밍봇)는 대화 페르소나가 없어 제외.
FUND_CHAT_ORDER = ["P", "W", "H", "S"]


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

    # "뭐 들고 있어?" 류 보유 질문 → 실제 포지션을 사실로 주입(지어내기 방지)
    prompt_ctx = stock_data_text
    if not stock_data_text and _is_holdings_q(user_text):
        try:
            prompt_ctx = _holdings_context()
            _save_msg(round_id, "System", f"📌 봇 실제 보유 현황(사실 기준)\n{prompt_ctx}")
        except Exception as e:
            logger.debug(f"보유 컨텍스트 오류: {e}")

    history = get_recent_messages(15)
    history_text = format_history_compact([m for m in history if m["agent_name"] != "System"])

    # 질문 의도 — '왜/이유'면 설명(결정 강제 X), '살까/팔까·어때'면 스탠스+[결정]
    intent = _classify_chat_intent(user_text)

    # 응답 봇 — 라이브(ROOT_IS_FUND)면 현재 데스크 봇(P·W·H·S), 아니면 committee(v1 보존).
    # 단, 메타질문(휴장·장시간)은 1봇만 간결히.
    _fund_mode = os.getenv("ROOT_IS_FUND", "").lower() in ("1", "true", "yes")
    responders = list(FUND_CHAT_ORDER) if _fund_mode else list(AGENT_ORDER)
    random.shuffle(responders)
    _META_KW = ("장 쉬", "장 열", "휴장", "개장", "장 시간", "장 마감", "장 시작", "거래일", "몇 시")
    if not stock_data_text and any(k in user_text for k in _META_KW):
        responders = responders[:1]
    bot_responses: list[tuple[str, str]] = []
    spoken_names: list[str] = []   # 이번 채팅에서 이미 답한 봇 (미발언 봇 인용 방지)

    for agent_name in responders:
        system = _build_system(agent_name)
        prompt = build_user_response_prompt(user_text, history_text, agent_name,
                                            stock_context=prompt_ctx,
                                            spoken_names=list(spoken_names), intent=intent)
        try:
            resp = call_agent(agent_name, system, prompt)
        except Exception as e:
            _handle_agent_error(agent_name, e)
            resp = f"[{agent_name} 응답 오류]"
        if "응답 오류" not in resp:
            resp = _strip_name_prefix(agent_name, resp)   # 이름 프리픽스 제거(전 봇 공통)
        _save_msg(round_id, agent_name, resp)
        bot_responses.append((agent_name, resp))
        spoken_names.append(agent_name)
        time.sleep(random.uniform(0.2, 0.5))

    # ── 한 줄 정리(요약) — 입문자 훅. 대화 끝(피드 최신)에 배치 ──
    # decision(살까/팔까)+종목: 투표 집계 종합(LLM 0). reason·자유질문: 쉬운 말 요약(haiku 1콜).
    summary_msg = None
    stock_name = ""
    if stock_data_text:
        stock_name = stock_data_text.split("\n")[0].replace("===", "").strip().split("(")[0].strip()

    if stock_data_text and intent == "decision":
        try:
            summary_msg = _build_decision_summary(bot_responses, stock_name)
            # ntfy — 매수/매도 합의만
            decisions = [_parse_decision(resp) for _, resp in bot_responses]
            final, _ = _tally_votes(decisions)
            c = {k: sum(1 for d, _ in decisions if d == k) for k in ("매수", "관망", "매도")}
            emoji = {"매수": "🟢", "매도": "🔴", "관망": "🟡"}.get(final, "⚪")
            nlines = [f"{emoji} [{stock_name}] 합의: {final}  매수{c['매수']} 관망{c['관망']} 매도{c['매도']}"]
            for name, resp in bot_responses:
                d, _ = _parse_decision(resp)
                de = {"매수": "🟢", "매도": "🔴", "관망": "🟡"}.get(d, "⚪")
                nlines.append(f"{de}{name}: {_extract_reason(resp)[:60]}")
            notify("\n".join(nlines), f"[{stock_name}] 봇 합의: {final}")
        except Exception as e:
            logger.debug(f"결정 요약/알림 오류: {e}")
    else:
        # reason(왜/이유) 또는 자유 질문 → 봇 발언을 쉬운 말 1~2문장으로 (haiku 1콜)
        try:
            answers_text = "\n".join(f"{n}: {r[:200]}" for n, r in bot_responses
                                     if "응답 오류" not in r)
            if answers_text:
                sp = build_chat_summary_prompt(user_text, answers_text)
                raw = call_agent("P" if _fund_mode else "드가자", CHAT_SUMMARY_SYSTEM, sp, timeout=60, model="haiku")
                raw = (raw or "").strip()
                # 거부/캐릭터 이탈(Claude Code 정체 등) 감지 → 사용자에게 안 보이게 폐기
                _refusal = ("claude code", "투자 봇이 아니", "어시스턴트", "소프트웨어 엔지니어",
                            "제 역할이 아니", "디버깅", "죄송하지만 저는")
                if raw and not any(m in raw.lower() for m in _refusal):
                    summary_msg = f"📌 한 줄 정리\n🟢 쉽게: {raw}"
        except Exception as e:
            logger.debug(f"채팅 요약 오류: {e}")

    if summary_msg:
        _save_msg(round_id, "System", summary_msg + "\n※ 가상 계좌 기준 의견이며 투자 권유가 아닙니다.")

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

        for agent_name in get_speak_order():
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

def evaluate_positions(market: str = None):
    """보유 포지션 현재가 재평가 + -20% 손절 자동 실행.
    market 지정 시 해당 시장(KRX/US) 포지션만 평가."""
    from db import get_open_positions
    from fetchers import fetch_stock_price
    mkt = _norm_market(market) if market else None
    label = "" if not mkt else (" (한국)" if mkt == "KRX" else " (미국)")
    round_id = create_round("position-eval")
    save_message(round_id, "System", None, f"📊 보유 포지션 현재가 평가 중...{label}")

    positions = get_open_positions(market=mkt)
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
                stop_reason = (f"자동 손절 ({STOP_LOSS_PCT*100:.0f}% 도달)\n"
                               f"진입가 {entry:,.2f} → 현재가 {current:,.2f}\n"
                               f"손실률: {pnl_pct*100:.1f}%")
                pnl, err = sell_shared_position(pos["id"], current, exit_reasoning=stop_reason)
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


# ── 메인 사이클 완료 후 보유 종목 점검 ──────────────────
_holdings_review_lock = threading.Lock()


def review_holdings(force: bool = False, market: str = None):
    """모든 보유 포지션에 대해 봇 토론 — 홀드 vs 매도 판단.
    매도 5표(과반) 이상 → 매도 실행. 그 외 → 보유 유지.
    메인 사이클 완료 직후 자동 호출 (_advance_stock → cycle_rest 진입 시).
    market 지정 시 해당 시장(KRX/US) 포지션만 점검.
    `force=True`면 _phase 체크 우회 (수동 트리거용)."""
    if not _holdings_review_lock.acquire(blocking=False):
        logger.info("보유 점검 이미 실행 중 — 이번 호출 건너뜀")
        return

    try:
        _review_holdings_inner(force=force, market=market)
    finally:
        _holdings_review_lock.release()


def _review_holdings_inner(force: bool = False, market: str = None):
    from datetime import datetime, timezone, timedelta
    from db import get_open_positions
    from fetchers import fetch_stock_news, format_stock_news
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST)
    mkt = _norm_market(market) if market else None
    mkt_label = "" if not mkt else ("한국 " if mkt == "KRX" else "미국 ")

    # 메인 사이클 진행 중이면 동시 실행 방지 (force=True면 우회)
    if not force and _phase != "cycle_rest":
        logger.info(f"메인 사이클 진행 중(_phase={_phase}) — 보유 점검 건너뜀")
        return
    if _pause_requested or is_user_active():
        return
    if is_claude_token_exhausted():
        notify("⏸ 보유 점검 대기", "Claude 토큰 소진 — 충전 후 재시도",
               priority="default", cooldown=1800)
        return

    positions = get_open_positions(market=mkt, account="main")  # 장기 계좌만 — 트레이딩은 규칙엔진 전담
    intro_round_id = create_round(f"{mkt_label}보유 종목 점검")
    if not positions:
        _save_msg(intro_round_id, "System", f"✅ {mkt_label}보유 종목 없음 — 점검 생략")
        complete_round(intro_round_id)
        return

    names = ", ".join(p["symbol"] for p in positions)
    _save_msg(intro_round_id, "System",
              f"📋 {mkt_label}보유 종목 점검 — {len(positions)}개 (메인 사이클 완료 후)\n"
              f"대상: {names}\n"
              f"각 종목별로 5봇이 홀드/매도 판단 → 매도 {SELL_MAJORITY}표(과반) 또는 실적왕 thesis 훼손 시 청산")
    complete_round(intro_round_id)

    market_summary, _ = _get_or_refresh_market_summary()
    sold, kept = [], []
    token_exhausted = False
    paused = False

    for pos in positions:
        if _pause_requested:
            paused = True
            break
        if is_claude_token_exhausted():
            token_exhausted = True
            break

        symbol = pos["symbol"]
        code = pos.get("code") or ""
        market = pos.get("market", "KRX")
        entry = pos.get("entry_price") or 0
        buy_reasoning = pos.get("reasoning", "") or ""

        # 보유일수 계산
        days_held = 0
        try:
            opened_at = pos.get("opened_at", "")
            if opened_at:
                opened_dt = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=KST)
                days_held = (now - opened_dt).days
        except Exception:
            pass

        if not code:
            continue

        # 가격/데이터 조회
        try:
            yf_sym = code if market == "US" else f"{code}.KS"
            stock_data = fetch_stock_data(
                code, yf_sym, symbol,
                market="US" if market == "US" else "KRX",
                exchange="NASDAQ" if market == "US" else "KRX",
            )
            current = stock_data.get("price", 0) or 0
            if not current:
                import yfinance as yf
                t = yf.Ticker(yf_sym)
                current = t.fast_info.last_price or t.fast_info.previous_close or 0
                if current:
                    stock_data["price"] = current
            if not current:
                continue
            stock_data_text = format_stock_data(stock_data)
        except Exception as e:
            logger.debug(f"보유 점검 데이터 조회 실패 {symbol}: {e}")
            continue

        pnl_pct = ((current - entry) / entry * 100) if entry else 0.0

        # 최신 뉴스 (있으면 추가)
        try:
            news_items = fetch_stock_news(symbol, yf_sym, max_items=5)
            news_text = format_stock_news(symbol, news_items) if news_items else ""
        except Exception:
            news_text = ""

        round_id = create_round(f"보유 점검 — {symbol}")
        currency = "$" if market == "US" else "₩"
        header = (f"📊 보유 종목 점검: {symbol} ({code})\n"
                  f"진입가 {currency}{entry:,.2f} → 현재가 {currency}{current:,.2f} "
                  f"({pnl_pct:+.1f}%, 보유 {days_held}일)\n"
                  f"매수 근거: {(buy_reasoning or '(기록 없음)')[:300]}")
        if news_text:
            header += f"\n\n{news_text}"
        _save_msg(round_id, "System", header)

        decisions, bot_responses = [], []
        _tech_ohlc = None
        try:
            import trading_engine
            _tech_ohlc = trading_engine.fetch_one(code, market)
        except Exception as e:
            logger.debug(f"기술신호 OHLC 조회 실패: {e}")
        for agent_name in get_speak_order():
            if _pause_requested:
                break
            # 규칙 기술봇 — LLM 없이 결정론적 청산신호로 참여
            if agent_name in _RULE_BOTS:
                msg, decision = _rule_bot_message(agent_name, _tech_ohlc, "sell")
                _save_msg(round_id, agent_name, msg)
                decisions.append((decision, 0))
                bot_responses.append((agent_name, msg))
                continue
            if is_claude_token_exhausted():
                token_exhausted = True
                break
            round_msgs = get_messages_for_round(round_id)
            history_text = format_history_compact(
                [m for m in round_msgs if m["agent_name"] not in ("System",)]
            )
            system = _build_system(agent_name)
            prompt = build_holdings_review_prompt(
                symbol, code, entry, current, pnl_pct, days_held,
                buy_reasoning, stock_data_text, market_summary,
                history_text, agent_name,
            )
            resp = None
            for attempt in range(2):
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
                if "응답 오류" not in resp:
                    resp = _strip_name_prefix(agent_name, resp)
                _save_msg(round_id, agent_name, resp)
                d, _ = _parse_decision(resp)
                decisions.append((d, 0))
                bot_responses.append((agent_name, resp))

        if token_exhausted:
            complete_round(round_id)
            break

        # 전체 응답 오류 = 토큰 소진으로 판단
        err_cnt = sum(1 for _, r in bot_responses if "응답 오류" in r)
        if err_cnt == len(bot_responses) and len(bot_responses) > 0:
            from agents import _set_token_exhausted
            _set_token_exhausted()
            complete_round(round_id)
            token_exhausted = True
            break

        # 매도 / 홀드(=관망) 집계
        sell_count = sum(1 for d, _ in decisions if d == "매도")
        hold_count = sum(1 for d, _ in decisions if d == "관망")
        vote_text = " | ".join(
            f"{n}: {_parse_decision(r)[0].replace('관망','홀드')}"
            for n, r in bot_responses
        )

        # 실적왕(피터린치) 펀더 thesis 훼손 = 단독 매도 트리거 (§3 매매 로직)
        siljeokwang_sell = any(n == "실적왕" and _parse_decision(r)[0] == "매도"
                               for n, r in bot_responses)
        if sell_count >= SELL_MAJORITY or siljeokwang_sell:
            sell_reasons = []
            for n, r in bot_responses:
                if _parse_decision(r)[0] == "매도":
                    sell_reasons.append(f"• {n}: {_extract_reason(r)}")
            trigger = ("매도 과반" if sell_count >= SELL_MAJORITY else "실적왕 thesis 훼손(단독)")
            sell_reasoning = f"보유 점검 — {trigger}\n" + "\n".join(sell_reasons)
            _save_msg(round_id, "System",
                      f"🗳 점검 투표: {vote_text}\n"
                      f"→ {trigger} — 매도 실행")
            _execute_sell({"name": symbol, "code": code, "market": market},
                          current, sell_reasoning, round_id)
            sold.append(symbol)
        else:
            _save_msg(round_id, "System",
                      f"🗳 점검 투표: {vote_text}\n"
                      f"→ 보유 유지 (홀드 {hold_count}표 / 매도 {sell_count}표)")
            kept.append(symbol)

        complete_round(round_id)
        time.sleep(1)

    # 최종 요약
    done_round_id = create_round("보유 점검 완료")
    if paused:
        _save_msg(done_round_id, "System",
                  f"⏸ 일시정지 요청으로 중단 — 매도: {len(sold)}, 유지: {len(kept)}, 남은 종목: {len(positions)-len(sold)-len(kept)}")
    elif token_exhausted:
        _save_msg(done_round_id, "System",
                  f"⏸ 토큰 소진으로 중단 — 매도: {len(sold)}, 유지: {len(kept)}, 남은 종목: {len(positions)-len(sold)-len(kept)}")
    else:
        summary = (f"✅ 보유 점검 완료\n"
                   f"매도: {len(sold)}개" + (f" ({', '.join(sold)})" if sold else "") + "\n"
                   f"유지: {len(kept)}개" + (f" ({', '.join(kept)})" if kept else ""))
        _save_msg(done_round_id, "System", summary)
        notify("📋 보유 점검 완료",
               f"매도 {len(sold)} / 유지 {len(kept)}",
               priority="default", cooldown=0)
    complete_round(done_round_id)
