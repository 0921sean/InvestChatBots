"""
Discovery Engine — 매 라운드 시작 시 "오늘 뭐가 움직였나?" 자동 스캔.
pykrx 거래량/가격 급변 종목 + 뉴스 언급 종목 → 가장 흥미로운 이벤트 반환.
"""
import logging
from datetime import datetime, timedelta
from fetchers import fetch_news

logger = logging.getLogger("investchat")

# 관심 종목 유니버스 (KRX 코드: 이름)
WATCH_UNIVERSE = {
    "005930": "삼성전자",
    "000660": "SK하이닉스",
    "015890": "이수페타시스",
    "006400": "삼성SDI",
    "000270": "기아",
    "005380": "현대차",
    "012450": "한화에어로스페이스",
    "089010": "켐트로닉스",
    "086520": "에코프로",
    "066970": "LG이노텍",
    "000104": "대덕전자",
    "042700": "한미반도체",
    "009150": "삼성전기",
    "032830": "삼성생명",
    "003490": "대한항공",
}

GLOBAL_WATCH = {
    "NVDA": "엔비디아",
    "TSM": "TSMC",
    "LRCX": "램리서치",
    "ASML": "ASML",
}


def _get_krx_movers(threshold_pct: float = 3.0, vol_ratio: float = 2.0) -> list[dict]:
    """pykrx로 당일 급등락/거래량 급증 종목 탐지."""
    movers = []
    try:
        from pykrx import stock as krx
        today = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")

        for code, name in WATCH_UNIVERSE.items():
            try:
                df = krx.get_market_ohlcv(start, today, code)
                if df is None or len(df) < 2:
                    continue

                price     = float(df["종가"].iloc[-1])
                prev_price = float(df["종가"].iloc[-2])
                vol_today = float(df["거래량"].iloc[-1])
                vol_avg   = float(df["거래량"].iloc[:-1].mean())

                chg_pct  = (price - prev_price) / prev_price * 100 if prev_price else 0
                vol_mult  = vol_today / vol_avg if vol_avg > 0 else 1

                if abs(chg_pct) >= threshold_pct or vol_mult >= vol_ratio:
                    movers.append({
                        "name": name,
                        "code": code,
                        "price": price,
                        "change_pct": round(chg_pct, 2),
                        "volume_ratio": round(vol_mult, 1),
                        "signal": _classify_signal(chg_pct, vol_mult),
                    })
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"[discovery] pykrx 스캔 실패: {e}")

    return sorted(movers, key=lambda x: abs(x["change_pct"]) + x["volume_ratio"], reverse=True)


def _classify_signal(chg_pct: float, vol_mult: float) -> str:
    if chg_pct >= 5:
        return "급등🔥"
    elif chg_pct >= 3:
        return "강세▲"
    elif chg_pct <= -5:
        return "급락💧"
    elif chg_pct <= -3:
        return "약세▼"
    elif vol_mult >= 3:
        return "거래량폭발📊"
    elif vol_mult >= 2:
        return "거래량급증📈"
    return "주목"


def _extract_stock_mentions(news: list[dict]) -> list[str]:
    """뉴스에서 관심 종목 언급 탐지."""
    all_names = list(WATCH_UNIVERSE.values()) + list(GLOBAL_WATCH.values())
    mentions = []
    for item in news:
        text = item.get("title", "") + " " + item.get("summary", "")
        for name in all_names:
            if name in text and name not in mentions:
                mentions.append(name)
    return mentions


def discover_events(n: int = 2) -> str:
    """
    오늘 가장 주목할 이벤트 반환.
    n: 반환할 이벤트 수.
    """
    events = []

    # 1. 가격/거래량 급변 종목
    movers = _get_krx_movers()
    for m in movers[:n]:
        line = (f"{m['name']} {m['signal']} "
                f"({m['change_pct']:+.1f}%, 거래량 {m['volume_ratio']}배)")
        events.append(line)

    # 2. 뉴스에서 종목 언급 찾기
    news = fetch_news(max_items=15)
    mentioned = _extract_stock_mentions(news)
    if mentioned and len(events) < n:
        events.append(f"뉴스 언급: {', '.join(mentioned[:3])}")

    # 3. 주목할 뉴스 헤드라인 (어닝/공급망 키워드)
    hot_keywords = ["어닝", "실적", "서프라이즈", "공급망", "수급", "증설", "신고가", "수혜", "관세", "금리"]
    hot_news = [n["title"] for n in news
                if any(k in n["title"] for k in hot_keywords)][:2]

    if not events and not hot_news:
        return ""

    result = "📡 오늘의 주목 이벤트\n"
    for e in events:
        result += f"• {e}\n"
    for h in hot_news:
        result += f"• 뉴스: {h}\n"

    return result.strip()
