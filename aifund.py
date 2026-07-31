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

logger = logging.getLogger("investchat.aifund")

# ── 토글 · 상한 ──────────────────────────────────────────
NEW_DESK_ENABLED = False   # 새 데스크 전체 토글(컷오버 전까지 off). 재개: True + 재시작.
DAILY_QUOTA = 10           # A가 하루에 올리는 종목 수(=P/W/S 분량). 운영값 — 조정 쉬움.

def _narrate(bot, content, model="rule"):
    """AI펀드 관전 피드에 한 줄 — desk='fund'로 저장해 committee 피드와 분리.
    발화자는 전략 노출 방지를 위해 알파벳 한 글자(A/P/W/S/Q)로만 표기.
    실패해도 사이클은 계속(피드는 부가 기능)."""
    from db import save_message
    try:
        save_message(None, bot, model, content, desk="fund")
    except Exception as e:
        logger.warning(f"내레이션 실패 {bot}: {e}")


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
    verdicts, reasonings = {}, {}
    for bot in NEW_DESK_ORDER:
        try:
            v, rz = analyze_stock(code, name, yf_ticker, bot, market, brief=brief)
        except Exception as e:
            logger.warning(f"발굴 분석 실패 {code}@{bot}: {e}")
            v, rz = "관망", ""
        verdicts[bot] = v
        reasonings[bot] = rz
    approved = any(v == "매수" for v in verdicts.values())
    return {"code": code, "name": name, "verdicts": verdicts,
            "reasonings": reasonings, "approved": approved}


# ── P/W/S 매수·청산 실행 (봇별 독립 계좌 — 경쟁) ────────────
def execute_buys(code, name, verdicts, market="US"):
    """각 봇이 '매수'면 자기 계좌(P/W/S)에 매수. 경쟁 구조 — 봇별 독립.
    이미 보유·자리 상한(MAX_POSITIONS)·현금 부족이면 스킵. 사이징 = FUND_SEED×WEIGHT_PCT.
    ⚠️ NEW_DESK_ENABLED=False면 no-op(라이브 무변경). 반환: 매수 체결한 봇 리스트."""
    if not NEW_DESK_ENABLED:
        return []
    from db import (buy_shared_position, get_open_positions,
                    get_open_positions_by_symbol, FUND_SEED)
    from fetchers import fetch_stock_price
    from trading_strategies import position_amount, can_open

    price = fetch_stock_price(code if market == "US" else f"{code}.KS")
    if not price:
        return []
    bought = []
    for bot, v in verdicts.items():
        if v != "매수":
            continue
        if get_open_positions_by_symbol(name, account=bot):            # 이미 보유
            continue
        if not can_open(len(get_open_positions(account=bot))):         # 자리 상한
            continue
        amt = position_amount(FUND_SEED)                               # 2% × 1억
        _, err = buy_shared_position(name, code, price, amt, f"{bot} 발굴 매수",
                                     market, account=bot)
        if not err:
            bought.append(bot)
            logger.info(f"[AI펀드] {bot} 매수 {name} {amt:,.0f}")
    return bought


def execute_thesis_sell(bot, position, verdict, price):
    """봇이 보유 종목 재분석 → '매도'면 자기 계좌에서 청산(논지 훼손). 그 외 no-op.
    반환: 청산 여부."""
    if verdict != "매도":
        return False
    from db import sell_shared_position
    _, err = sell_shared_position(position["id"], price,
                                  exit_reasoning=f"{bot} 논지 훼손 청산")
    return not err


# ── Q 라이브 실행 (B+M 블렌드 룰봇 — LLM 없음, 전 유니버스 자체 스캔) ──
def q_entry_signal(closes, in_uptrend):
    """Q 진입 신호(최신 바): M(미너비니, 시장필터) 우선 → B(볼린저 복귀확인).
    반환: 'M' | 'B' | None (전략 태그)."""
    import backtest as bt
    from trading_strategies import meanrev_entry
    if bt.minervini_entry(closes, in_uptrend):
        return "M"
    if meanrev_entry(closes):
        return "B"
    return None


def q_exit_signal(closes, strat):
    """Q 청산 신호(최신 바): 진입 전략(M/B)의 청산 규칙."""
    import backtest as bt
    from trading_strategies import meanrev_exit
    return bt.minervini_exit(closes) if strat == "M" else meanrev_exit(closes)


def run_q_desk(market="US"):
    """Q(B+M 블렌드) 라이브 실행 — 전 유니버스 자체 스캔 → Q 계좌(id 6) 매수/청산.
    ⚠️ NEW_DESK_ENABLED=False면 no-op(라이브 무변경). 룰·무비용, 하루 1회.
    반환: {'bought','sold'}."""
    if not NEW_DESK_ENABLED:
        return {"bought": [], "sold": []}
    import backtest as bt
    from trading_engine import load_universe
    from db import (buy_shared_position, sell_shared_position, get_open_positions,
                    get_open_positions_by_symbol, FUND_SEED)
    from trading_strategies import position_amount, can_open

    data = bt._fetch([s.split(".")[0] for s in load_universe(market)], period="2y")
    spy = bt._fetch_bench("SPY", period="2y")
    in_uptrend = len(spy["close"]) >= 200 and spy["close"][-1] > bt._sma(spy["close"], 200)

    sold, bought = [], []
    for pos in get_open_positions(account="Q"):            # 청산 먼저
        o = data.get(pos["code"])
        if not o:
            continue
        strat = "M" if "미너비니" in (pos.get("reasoning") or "") else "B"
        if q_exit_signal(o["close"], strat):
            _, err = sell_shared_position(pos["id"], o["close"][-1], exit_reasoning=f"Q {strat} 청산")
            if not err:
                sold.append(pos["symbol"])

    q_count = len(get_open_positions(account="Q"))
    for code, o in data.items():                           # 진입
        if not can_open(q_count):
            break
        if get_open_positions_by_symbol(code, account="Q"):
            continue
        tag = q_entry_signal(o["close"], in_uptrend)
        if not tag:
            continue
        label = "미너비니" if tag == "M" else "볼린저"
        _, err = buy_shared_position(code, code, o["close"][-1], position_amount(FUND_SEED),
                                     f"Q {label} 매수", market, account="Q")
        if not err:
            bought.append(code)
            q_count += 1
    return {"bought": bought, "sold": sold}


# ── 하루 사이클 오케스트레이션 (A → P/W/S → Q) ──────────────
def run_new_desk_cycle(market="US"):
    """AI펀드 하루 흐름: 계좌 준비 → A 소싱 → P/W/S 각 후보 분석·매수/논지청산 → Q 전 유니버스 스캔·매매.
    ⚠️ NEW_DESK_ENABLED=False면 no-op(라이브 무변경). 스케줄러 배선은 추후 Phase(라이브 orchestrator).
    반환: {'briefing','buys','sells','q'}."""
    empty = {"briefing": "", "buys": [], "sells": [], "q": {"bought": [], "sold": []}}
    if not NEW_DESK_ENABLED:
        return empty
    from db import ensure_fund_accounts, get_open_positions_by_symbol
    from fetchers import fetch_stock_price
    ensure_fund_accounts()                                    # 4계좌 시드(멱등)
    _narrate("A", "다들 출근했습니다. 오늘 볼 종목부터 추려볼게요.")

    src = source_today(market)                                # A 소싱 + 브리프
    _narrate("A", src["briefing"])                            # 모닝 브리핑
    buys, sells = [], []
    for code in src["new"] + src["catalyst"]:
        name = src["names"].get(code, code)
        r = analyze_candidate(code, name, code, market)       # P/W/S 각자 verdict
        for bot in NEW_DESK_ORDER:                            # 각자 분석 발언(실제 논지)
            _narrate(bot, r.get("reasonings", {}).get(bot) or f"[결정] {r['verdicts'].get(bot, '관망')} — {name}",
                     model="sonnet")
        for b in execute_buys(code, name, r["verdicts"], market):
            _narrate(b, f"💰 {name} 매수 체결 — 내 계좌에 담았어요.")
            buys.append((b, code))
        price = None
        for bot, v in r["verdicts"].items():                  # 논지 청산(보유 봇이 매도 시)
            if v != "매도":
                continue
            held = get_open_positions_by_symbol(name, account=bot)
            if not held:
                continue
            price = price or fetch_stock_price(code if market == "US" else f"{code}.KS")
            if price and execute_thesis_sell(bot, held[0], v, price):
                _narrate(bot, f"🔻 {name} 청산 — 논지가 훼손돼 내 계좌에서 뺐어요.")
                sells.append((bot, code))

    q = run_q_desk(market)                                    # Q 전 유니버스 스캔·매매
    _narrate("Q", f"전 유니버스 B+M 스캔 완료 — 신규 매수 {len(q['bought'])}건 / 청산 {len(q['sold'])}건.")
    _snapshot_fund_nav()                                      # 수익률 스파크라인용 일일 NAV
    _narrate("A", "오늘 일과 끝. 계좌별 성적은 우측 패널에서 볼 수 있어요. 다들 수고했습니다 🫡")
    return {"briefing": src["briefing"], "buys": buys, "sells": sells, "q": q}


def _snapshot_fund_nav():
    """각 봇 계좌의 오늘 NAV(현금+투자원가)를 기록 — 수익률 그래프용. 실패해도 무시."""
    from db import (FUND_ACCOUNTS, get_shared_portfolio, get_open_positions,
                    record_fund_nav)
    today = _today_kst()
    for acct in FUND_ACCOUNTS:
        try:
            cash = (get_shared_portfolio(acct) or {}).get("balance") or 0
            inv = sum((p.get("amount") or 0) for p in get_open_positions(account=acct))
            record_fund_nav(acct, today, cash + inv)
        except Exception as e:
            logger.warning(f"NAV 스냅샷 실패 {acct}: {e}")
