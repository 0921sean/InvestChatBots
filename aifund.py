"""
AI 펀드 팀룸 — 설계 docs/AIFUND_PIVOT.md.
2단계: A(소싱봇). 유니버스 랜덤 발굴 + 어닝 카탈리스트로 캐시 무효화 + 모닝 브리핑.

⚠️ NEW_DESK_ENABLED=False — 아직 어떤 사이클에도 배선 안 됨(라이브 무변경).
순수 로직(select_candidates/build_briefing)은 네트워크 없이 테스트 가능,
네트워크(_recent_earnings_date)는 격리.
"""
import logging
import random
from datetime import datetime, timezone, timedelta

from db import get_cached_codes, get_thesis, invalidate_thesis

try:                                                   # 손절 임계값은 튜닝값(비공개)
    from strategy_private import FUND_STOP_PCT
except ImportError:
    from strategy_private_example import FUND_STOP_PCT

logger = logging.getLogger("investchat.aifund")

# ── 토글 · 상한 ──────────────────────────────────────────
NEW_DESK_ENABLED = False   # 새 데스크 전체 토글(컷오버 전까지 off). 재개: True + 재시작.
DAILY_QUOTA = 10           # A가 하루에 올리는 종목 수(=P/W/S 분량). 운영값 — 조정 쉬움.


def _today_kst() -> str:
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")


# ── 순수 선택 로직 (네트워크 X — 테스트 가능) ──────────────
def select_candidates(universe, cached_codes, catalyst_codes, quota, rng=random):
    """오늘 볼 종목 = 카탈리스트(재분석 필요, 우선) + 신규(미캐시에서 랜덤).
    - catalyst: 이미 본(cached) 종목 중 재료 뜬 것 → 재분석.
    - new: 아직 안 본 종목에서 남는 분량만큼 랜덤. (필터 없음 = 사각지대 없음)
    반환: (new_list, catalyst_list)"""
    cached = set(cached_codes)
    catalyst = [c for c in catalyst_codes if c in cached][:quota]  # 재분석 우선, 하루 상한(quota) 초과분은 다음날
    n_new = max(0, quota - len(catalyst))
    pool = [c for c in universe if c not in cached]                # 이미 본(캐시) 종목 제외 = 신규만
    new = rng.sample(pool, min(n_new, len(pool))) if pool and n_new else []
    return new, catalyst


def _label(code, names):
    """티커에 회사명 병기 — 'MSTR (MicroStrategy)'. 이름 없으면 티커만."""
    nm = (names or {}).get(code)
    return f"{code} ({nm})" if nm and nm != code else code


def build_briefing(new, catalyst, names=None):
    """A의 모닝 브리핑 — 봐야 할 것(신규+카탈리스트)만, 이름·이유와 함께. 없으면 조용히 쉼.
    names: {code: 회사명} (티커만으론 뭔 회사인지 모르니 병기)."""
    if not new and not catalyst:
        return "오늘은 새로 볼 종목이 없네요. 재료 뜬 것도 없고 — 다들 편히 쉬어요~ 🫡"
    lines = [f"오늘 볼 종목 {len(new) + len(catalyst)}개예요:"]
    for c in catalyst:
        lines.append(f"· {_label(c, names)} — 어닝/재료 떴어요 🔄 실적 반영 업데이트 필요")
    for c in new:
        lines.append(f"· {_label(c, names)} — 신규, 첫 분석")
    lines.append("잘 부탁드려요~")
    return "\n".join(lines)


# ── 네트워크: 회사명 조회 (이름 캐시) ─────────────────────
_name_cache = {}


def stock_name(code) -> str:
    """티커 → 회사명(yfinance shortName). 실패 시 티커. 이름은 안 변하니 캐시."""
    if code in _name_cache:
        return _name_cache[code]
    nm = code
    try:
        import yfinance as yf
        info = yf.Ticker(code).info or {}
        nm = (info.get("shortName") or info.get("displayName") or info.get("longName") or code).strip()
    except Exception as e:
        logger.debug(f"이름 조회 실패 {code}: {e}")
    _name_cache[code] = nm
    return nm


# ── 네트워크: 어닝 카탈리스트 감지 (격리) ──────────────────
def _recent_earnings_date(code) -> str | None:
    """가장 최근 어닝 발표일(YYYY-MM-DD) — yfinance. 없으면 None."""
    try:
        import yfinance as yf
        df = yf.Ticker(code).earnings_dates
        if df is None or df.empty:
            return None
        today = _today_kst()
        past = [d for d in df.index if d.strftime("%Y-%m-%d") <= today]
        return max(past).strftime("%Y-%m-%d") if past else None
    except Exception as e:
        logger.debug(f"어닝일 조회 실패 {code}: {e}")
        return None


def catalyst_codes(cached_codes) -> list:
    """캐시된 종목 중 '분석 이후 어닝이 발표된' 것 → 재분석 대상(카탈리스트)."""
    out = []
    for code in cached_codes:
        th = get_thesis(code)
        if not th:
            continue
        analyzed = min(r["analyzed_at"] for r in th.values())[:10]  # 가장 이른 분석일
        ed = _recent_earnings_date(code)
        if ed and ed > analyzed:                                    # 어닝이 분석보다 최근
            out.append(code)
    return out


# ── A 하루치 소싱 (조립) ──────────────────────────────────
def source_today(market="US", quota=None, rng=random):
    """A의 하루 소싱: 카탈리스트 감지 → 캐시 무효화 → 선택 → 브리핑.
    반환: {'new','catalyst','briefing'}. (매매·분석은 안 함 — 소싱만)"""
    from trading_engine import load_universe
    quota = quota or DAILY_QUOTA
    universe = [s.split(".")[0] for s in load_universe(market)]
    cached = get_cached_codes()
    catalyst = catalyst_codes(cached)
    new, cat = select_candidates(universe, cached, catalyst, quota, rng)
    for c in cat:                          # 오늘 선택된 카탈리스트만 무효화(재분석 예정) — 초과분은 캐시 유지→다음날 재출현
        invalidate_thesis(c)
    names = {c: stock_name(c) for c in new + cat}   # 티커에 회사명 병기
    return {"new": new, "catalyst": cat, "names": names,
            "briefing": build_briefing(new, cat, names)}


# ── 발굴 3인(P/W/S) 분석 (네트워크 + LLM) ─────────────────
import re                                          # noqa: E402

NEW_DESK_ORDER = ["P", "W", "S"]                   # 발굴 순서(피터린치·버핏·병목)
_VERDICT_RE = re.compile(r"\[결정\]\s*(매수|관망|매도)")


def _extra_fundamentals(data) -> str:
    """심화 재무(마진·매출성장·FCF) — 새 데스크 패킷 보강. 있는 값만.
    현 committee의 format_stock_data는 안 건드리고 여기서만 덧붙인다."""
    def pct(x):
        return f"{x * 100:.1f}%"
    rows = []
    for key, label in (("gross_margin", "총마진"), ("op_margin", "영업마진"),
                       ("profit_margin", "순마진"), ("rev_growth", "매출성장(YoY)")):
        if isinstance(data.get(key), (int, float)):
            rows.append(f"{label} {pct(data[key])}")
    if isinstance(data.get("fcf"), (int, float)):
        rows.append(f"잉여현금흐름 {data['fcf'] / 1e9:.1f}B")
    return ("\n심화재무: " + " · ".join(rows)) if rows else ""


def quarterly_trend(rev, gross, op, net):
    """분기 손익 리스트(최신→과거)로 매출·마진 추세 요약(순수 — 네트워크 X). 최소 3분기 필요.
    매출: 오래된→최신 흐름 + YoY / 마진: 최신값 + 3분기 전 대비 개선↑·악화↓·정체→. 없으면 ''."""
    if not rev or len(rev) < 3:
        return ""
    parts = [f"매출 {' → '.join(f'{v / 1e9:.1f}B' for v in reversed(rev[:5]) if v)}"
             + (f" (YoY {(rev[0] / rev[4] - 1) * 100:+.0f}%)" if len(rev) >= 5 and rev[4] else "")]
    for label, profit in (("총마진", gross), ("영업마진", op), ("순마진", net)):
        if profit and len(profit) >= 3 and rev[0] and rev[2]:
            now, old = profit[0] / rev[0] * 100, profit[2] / rev[2] * 100
            arrow = "↑개선" if now > old + 0.5 else "↓악화" if now < old - 0.5 else "→정체"
            parts.append(f"{label} {now:.0f}%({arrow})")
    return "분기추세(최신순): " + " / ".join(parts)


def _fetch_quarterly(code):
    """yfinance 분기 손익 → (매출, 총이익, 영업이익, 순이익) 리스트(최신→과거). 실패 시 빈."""
    try:
        import yfinance as yf
        q = yf.Ticker(code).quarterly_financials
        if q is None or q.empty:
            return [], [], [], []
        def row(n):
            return [float(x) for x in q.loc[n].dropna().head(5)] if n in q.index else []
        return (row("Total Revenue"), row("Gross Profit"),
                row("Operating Income"), row("Net Income"))
    except Exception as e:
        logger.debug(f"분기재무 조회 실패 {code}: {e}")
        return [], [], [], []


def build_research_brief(code, name, yf_ticker, market="US"):
    """A의 리서치 브리프 — P/W/S에게 넘길 최적화 다이제스트(뉴스 없이·중립 팩트만).
    현재 재무 + 심화(마진·성장·FCF) + 분기 다기간 추세. A가 종목당 1회 준비 → P/W/S 공용.
    반환: (packet_text, business_summary)."""
    from fetchers import fetch_stock_data, format_stock_data
    data = fetch_stock_data(code, yf_ticker, name, market=market)
    packet = format_stock_data(data) + _extra_fundamentals(data)
    trend = quarterly_trend(*_fetch_quarterly(code))
    if trend:
        packet += "\n" + trend
    return packet, data.get("business_summary", "")


def build_analysis_prompt(name, code, packet_text, business_summary="") -> str:
    """발굴봇 1인에게 줄 종목 분석 프롬프트. 데이터 패킷 + '꿈꾸는 것'(사업요약)."""
    parts = [f"[분석 종목] {name} ({code})", "", packet_text]
    if business_summary:
        parts += ["", f"[사업 개요] {business_summary}"]
    parts += [
        "",
        "위 종목을 네 투자 원칙으로 판단해줘. 네 프레임워크에 비춰 왜 그런지 근거를 대고,",
        "마지막 줄은 반드시 `[결정] 매수` / `[결정] 관망` / `[결정] 매도` 중 하나로 끝내.",
    ]
    return "\n".join(parts)


def _parse_verdict(text) -> str:
    """응답에서 [결정] 추출. 없으면 '관망'(보수적 기본값)."""
    m = _VERDICT_RE.search(text or "")
    return m.group(1) if m else "관망"


def analyze_stock(code, name, yf_ticker, bot, market="US", brief=None):
    """발굴봇 1인이 한 종목 분석 → thesis 캐시에 저장. 반환: (verdict, reasoning).
    brief: A가 준비한 (packet_text, business_summary). 없으면 직접 준비(단독 호출용)."""
    from agents import call_agent
    from prompts import AGENT_PROFILES
    from db import save_thesis

    packet, business = brief if brief else build_research_brief(code, name, yf_ticker, market)
    prompt = build_analysis_prompt(name, code, packet, business)
    reasoning = call_agent(bot, AGENT_PROFILES[bot]["system"], prompt, model="sonnet")
    verdict = _parse_verdict(reasoning)
    save_thesis(code, bot, verdict, reasoning)
    return verdict, reasoning


def analyze_candidate(code, name, yf_ticker, market="US"):
    """P/W/S 3인이 한 종목을 각자 분석. 관대 게이트: 한 명이라도 매수면 통과.
    반환: {'code','name','verdicts','approved'}."""
    brief = build_research_brief(code, name, yf_ticker, market)   # A가 1회 준비 → P/W/S 공용
    verdicts = {}
    for bot in NEW_DESK_ORDER:
        try:
            v, _ = analyze_stock(code, name, yf_ticker, bot, market, brief=brief)
        except Exception as e:
            logger.warning(f"발굴 분석 실패 {code}@{bot}: {e}")
            v = "관망"
        verdicts[bot] = v
    approved = any(v == "매수" for v in verdicts.values())
    return {"code": code, "name": name, "verdicts": verdicts, "approved": approved}


# ── T 하드 스탑로스 (룰엔진 — LLM 없음, 급락 안전망) ──────────
def stops_to_execute(positions, prices, stop_pct=None):
    """손절 대상 판정 (순수 함수 — 네트워크 X).
    positions: [{'id','symbol','code','entry_price',...}] (get_open_positions 형식)
    prices: {code: 현재가}. stop_pct: 손절 비율(음수, 예 -0.22). None이면 FUND_STOP_PCT.
    반환: [{'pos','current','pnl_pct'}] — pnl이 stop_pct 이하로 떨어진 것만."""
    thr = FUND_STOP_PCT if stop_pct is None else stop_pct
    hits = []
    for pos in positions:
        entry = pos.get("entry_price") or 0
        current = prices.get(pos.get("code"))
        if not entry or not current:
            continue                                   # 데이터 없으면 건너뜀(오판 방지)
        pnl_pct = (current - entry) / entry
        if pnl_pct <= thr:
            hits.append({"pos": pos, "current": current, "pnl_pct": pnl_pct})
    return hits


def run_fund_stops(market="US", account="fund"):
    """AI펀드 보유 종목 하드 스탑 체크 → 도달분 강제 청산. 논지 주인 재분석과 별개인 안전망.
    ⚠️ NEW_DESK_ENABLED=False면 no-op(라이브 무변경). 매수 실행이 배선된 뒤 스케줄러가 호출."""
    if not NEW_DESK_ENABLED:
        return []
    from db import get_open_positions, sell_shared_position
    from fetchers import fetch_stock_price

    positions = get_open_positions(market=market, account=account)
    prices = {}
    for pos in positions:
        code = pos.get("code")
        prices[code] = fetch_stock_price(code if market == "US" else f"{code}.KS")
    stopped = []
    for hit in stops_to_execute(positions, prices):
        pos, current, pnl_pct = hit["pos"], hit["current"], hit["pnl_pct"]
        reason = (f"T 하드 스탑로스 ({FUND_STOP_PCT * 100:.0f}% 도달) — 안전망 청산\n"
                  f"진입가 {pos['entry_price']:,.2f} → 현재가 {current:,.2f} ({pnl_pct * 100:.1f}%)")
        _, err = sell_shared_position(pos["id"], current, exit_reasoning=reason)
        if not err:
            stopped.append(pos["symbol"])
            logger.info(f"[AI펀드] 손절 {pos['symbol']} {pnl_pct * 100:.1f}%")
    return stopped
