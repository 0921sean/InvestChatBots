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


def _bottleneck_seed():
    """병목 시드 유니버스 — 비공개(strategy_private), 없으면 example(빈값) 폴백."""
    try:
        from strategy_private import US_BOTTLENECK_SEED as seed
    except Exception:
        try:
            from strategy_private_example import US_BOTTLENECK_SEED as seed
        except Exception:
            seed = []
    return list(seed or [])


def source_bottleneck(market="US", quota=None):
    """S 자체 소싱 — 병목 시드에서 오늘 안 본 곡괭이만 골라 반환('발굴'이 S의 엣지).
    A 리스트를 안 받고 S가 자기 유니버스를 직접 뒤진다. 반환: {'codes','names'}."""
    quota = quota or DAILY_QUOTA
    seed = _bottleneck_seed()
    if market != "US" or not seed:                   # 병목 시드는 미장 전용
        return {"codes": [], "names": {}}
    cached = set(get_cached_codes())                 # 이미 분석된 건 제외(재분석은 카탈리스트가)
    codes = [c for c in seed if c not in cached][:quota]
    return {"codes": codes, "names": {c: stock_name(c) for c in codes}}


# ── 발굴 3인(P/W/S) 분석 (네트워크 + LLM) ─────────────────
import re                                          # noqa: E402

NEW_DESK_ORDER = ["P", "W", "S"]                   # (레거시) 4봇 경쟁 발굴 순서
# 통일 데스크 결정자 (설계 docs/AIFUND_PIVOT.md)
LARGECAP_BOTS = ["P", "W", "H"]                    # 대형주 = 린치·버핏·실적왕(H) → 관심종목 캐시
DISCOVERY_BOTS = ["P", "W", "S"]                   # 발굴주 = 린치·버핏·병목 → 즉시매수
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


def analyze_candidate(code, name, yf_ticker, market="US", bots=None):
    """지정 봇들이 한 종목을 각자 분석(기본 P/W/S). bots로 데스크 분리 가능(P/W vs S).
    반환: {'code','name','verdicts','reasonings','approved','brief'}."""
    bots = bots or NEW_DESK_ORDER
    brief = build_research_brief(code, name, yf_ticker, market)   # A가 1회 준비(리포트+분석 공용)
    verdicts, reasonings = {}, {}
    for bot in bots:
        try:
            v, rz = analyze_stock(code, name, yf_ticker, bot, market, brief=brief)
        except Exception as e:
            logger.warning(f"발굴 분석 실패 {code}@{bot}: {e}")
            v, rz = "관망", ""
        verdicts[bot] = v
        reasonings[bot] = rz
    approved = any(v == "매수" for v in verdicts.values())
    return {"code": code, "name": name, "verdicts": verdicts,
            "reasonings": reasonings, "approved": approved, "brief": brief}


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


# ── 관전 멘트 풀 (매번 다르게 — 봇별 varied) ───────────────────
# 퇴근 라인은 세션(출근/퇴근) 판정에 쓰이므로 반드시 '퇴근' 단어 포함.
_CLOCK_IN = {
    "A": ["다들 출근했습니다. 오늘 볼 종목부터 추려볼게요.",
          "좋은 아침. 밤사이 뭐 터졌나 훑고 리스트 뽑습니다.",
          "출근이요~ 오늘의 후보 정리해서 곧 올릴게요."],
    "P": ["린치 출근. 오늘도 생활 속 텐배거 찾아봅니다.",
          "P 왔어요. PEG 싼 성장주 뭐 없나 보죠.",
          "출근! 스토리 살아있는 놈으로 골라봅니다."],
    "W": ["버핏 관점으로 출근했습니다. 좋은 회사를 적정가에.",
          "W 도착. 능력범위 안, 해자 있는 것만 봅니다.",
          "출근이요. 10년 들고 갈 만한지부터 봅니다."],
    "S": ["병목 헌터 출근 — 오늘의 곡괭이 찾으러 갑니다.",
          "S 왔습니다. 남들 완제품 볼 때 저는 사슬 뒤를 봐요.",
          "출근! 공급망 초크포인트부터 직접 뒤져봅니다."],
}
_CLOCK_OUT = {
    "A": ["오늘 리서치 끝 — A 퇴근합니다. 다들 수고했어요 🫡",
          "정리 끝났습니다. A 퇴근할게요, 내일 또 좋은 종목으로.",
          "계좌 성적은 우측에서 보세요. A 퇴근합니다."],
    "P": ["P 퇴근. 오늘 고른 놈들 잘 자라라~",
          "린치 이만 퇴근합니다. 인내가 수익이죠.",
          "먼저 퇴근할게요. 좋은 회사는 시간이 증명해요."],
    "W": ["W 퇴근. 서두르지 않습니다, 늘 그렇듯.",
          "버핏 관점 정리 끝, 퇴근합니다.",
          "이만 퇴근이요. 가격이 오면 그때 더 담죠."],
    "S": ["S 퇴근 — 곡괭이는 조용히 값을 합니다.",
          "병목 점검 끝. 사슬은 안 끊기니까요. 퇴근!",
          "먼저 퇴근합니다. 다음 초크포인트는 내일 또."],
}
_A_DONE = [
    "{desk} {n}종목 리서치 정리해 올렸어요 — 우측 'A 리서치' 참고!",
    "{n}종목 다 봤습니다. 뭐하는 회사인지 리포트로 정리해뒀어요.",
    "{desk} 리포트 {n}건 업데이트. 숫자는 거기서 확인하세요.",
]
_BUY_LINES = ["💰 {name} 매수 체결 — 내 계좌에 담았어요.",
              "{name} 샀습니다. 논지대로 갑니다.",
              "{name} 편입 완료. 지켜보죠."]
_SELL_LINES = ["🔻 {name} 청산 — 논지가 훼손돼 뺐어요.",
               "{name} 던졌습니다. 얘기가 달라졌네요.",
               "{name} 정리 — 이유가 사라졌으니 홀드할 근거도 없죠."]


def _line(pool, bot):
    return random.choice(pool.get(bot) or [""])


def _summarize_ko(name, business_summary):
    """영문 사업요약 → '뭐하는 회사'를 1~2문장 한국어로(가벼운 haiku). 실패 시 빈 문자열.
    ⚠️ LLM 호출 — run_new_desk_cycle(NEW_DESK_ENABLED) 안에서만 불린다."""
    if not business_summary:
        return ""
    try:
        from agents import _call_claude_cli
        sys = ("너는 기업을 한 줄로 요약하는 애널리스트다. 평가·전망·추천 없이 "
               "'이 회사가 뭘 팔아서 어떻게 버는지'만 담백하게 1~2문장 한국어로.")
        prompt = f"[회사] {name}\n[영문 개요]\n{business_summary}\n\n한국어 1~2문장으로만 요약."
        return (_call_claude_cli(sys, prompt, timeout=30, model="haiku") or "").strip()
    except Exception as e:
        logger.warning(f"한글 요약 실패 {name}: {e}")
        return ""


# ── 하루 사이클 오케스트레이션 (A → P/W/S → Q) ──────────────
# ── 통일 데스크 파이프라인 (대형주 / 발굴주) — 설계 docs/AIFUND_PIVOT.md ──
def _largecap_universe():
    try:
        from strategy_private import LARGECAP_UNIVERSE as u
    except Exception:
        try:
            from strategy_private_example import LARGECAP_UNIVERSE as u
        except Exception:
            u = []
    return list(u or [])


def source_largecap(market="US", quota=None):
    """대형주 소싱 — 고정 유니버스에서 오늘 볼 종목(캐시된 건 제외, 매일 통일성). 반환 {'codes','names'}."""
    quota = quota or DAILY_QUOTA
    u = _largecap_universe()
    if market != "US" or not u:
        return {"codes": [], "names": {}}
    cached = set(get_cached_codes())
    codes = [c for c in u if c not in cached][:quota] or u[:quota]   # 다 봤으면 다시 처음부터
    return {"codes": codes, "names": {c: stock_name(c) for c in codes}}


def _store_report(today, code, name, brief, desk_tag):
    """A 리포트 저장(한글요약+재무). 실패해도 무시."""
    from db import record_fund_report
    packet, summary = brief or ("", "")
    summary = _summarize_ko(name, summary) or summary
    try:
        record_fund_report(today, code, name, summary, packet, desk_tag)
    except Exception as e:
        logger.warning(f"리포트 저장 실패 {code}: {e}")


def _spy_uptrend():
    """SPY 200일선 위 여부(시장 필터)."""
    import backtest as bt
    spy = bt._fetch_bench("SPY", period="2y")
    return len(spy["close"]) >= 200 and spy["close"][-1] > bt._sma(spy["close"], 200)


def _approver_of(position):
    """발굴주 포지션 reasoning에서 첫 매수 찬성봇 추출. 못 찾으면 P."""
    r = position.get("reasoning") or ""
    if "(" in r:
        first = r.split("(")[-1].rstrip(")").split(",")[0].strip()
        if first in DISCOVERY_BOTS:
            return first
    return "P"


# ── 발굴주 데스크 (A/S 발굴 → P/W/S OR게이트 즉시매수 → 논지청산) ──
def run_discovery_desk(market="US"):
    """발굴주 매수 슬롯(06시) — A 발굴 + S 병목 → P/W/S OR게이트 → 발굴주 계좌 즉시매수. 반환 {'buys'}."""
    if not NEW_DESK_ENABLED:
        return {"buys": []}
    from db import (ensure_desk_accounts, get_open_positions_by_symbol,
                    buy_shared_position, get_open_positions, DESK_SEED)
    from fetchers import fetch_stock_price
    from trading_strategies import position_amount, can_open
    ensure_desk_accounts()
    _narrate("A", _line(_CLOCK_IN, "A"))
    src = source_today(market)
    _narrate("A", src["briefing"])
    s_src = source_bottleneck(market)
    cands = [(c, src["names"].get(c, c)) for c in src["new"] + src["catalyst"]] \
        + [(c, s_src["names"].get(c, c)) for c in s_src["codes"]]
    if not cands:
        _narrate("A", _line(_CLOCK_OUT, "A"))
        return {"buys": []}
    for bot in DISCOVERY_BOTS:
        _narrate(bot, _line(_CLOCK_IN, bot))
    today = _today_kst()
    buys = []
    for code, name in cands:
        r = analyze_candidate(code, name, code, market, bots=DISCOVERY_BOTS)
        _store_report(today, code, name, r.get("brief"), "발굴주")
        for bot in DISCOVERY_BOTS:
            _narrate(bot, r.get("reasonings", {}).get(bot) or f"[결정] {r['verdicts'].get(bot, '관망')} — {name}", model="sonnet")
        approvers = [b for b in DISCOVERY_BOTS if r["verdicts"].get(b) == "매수"]
        if not approvers or get_open_positions_by_symbol(name, account="발굴주"):
            continue
        if not can_open(len(get_open_positions(account="발굴주"))):
            continue
        price = fetch_stock_price(code if market == "US" else f"{code}.KS")
        if not price:
            continue
        _, err = buy_shared_position(name, code, price, position_amount(DESK_SEED),
                                     f"발굴주 매수 ({','.join(approvers)})", market, account="발굴주")
        if not err:
            buys.append(code)
            _narrate(approvers[0], random.choice(_BUY_LINES).format(name=name))
    _narrate("A", random.choice(_A_DONE).format(desk="발굴", n=len(cands)))
    for bot in DISCOVERY_BOTS:
        _narrate(bot, _line(_CLOCK_OUT, bot))
    _narrate("A", _line(_CLOCK_OUT, "A"))
    return {"buys": buys}


def run_discovery_review(market="US"):
    """발굴주 점검 슬롯(12시) — 매수 찬성봇이 재분석해 매도면 청산. 반환 {'sells'}."""
    if not NEW_DESK_ENABLED:
        return {"sells": []}
    from db import get_open_positions, sell_shared_position
    from fetchers import fetch_stock_price
    sold = []
    for p in get_open_positions(account="발굴주"):
        code = p.get("code") or p["symbol"]
        name = p["symbol"]
        bot = _approver_of(p)
        try:
            v, _ = analyze_stock(code, name, code, bot, market)
        except Exception as e:
            logger.warning(f"발굴주 재분석 실패 {code}: {e}")
            continue
        if v != "매도":
            continue
        price = fetch_stock_price(code if market == "US" else f"{code}.KS")
        if price and not sell_shared_position(p["id"], price, exit_reasoning=f"{bot} 논지 훼손 청산")[1]:
            sold.append(code)
            _narrate(bot, random.choice(_SELL_LINES).format(name=name))
    return {"sells": sold}


# ── 대형주 데스크 (P/W/H 선정→관심종목 캐시 / Q 진입·청산 타이밍) ──
def run_largecap_select(market="US"):
    """대형주 선정 슬롯(18시) — A 소싱 → P/W/H OR게이트 → 관심종목 캐시. 보유중 강한 펀더매도(2인+)면 즉시청산.
    반환 {'watched','sold'}."""
    if not NEW_DESK_ENABLED:
        return {"watched": [], "sold": []}
    from db import (ensure_desk_accounts, add_to_watch, get_open_positions_by_symbol,
                    sell_shared_position)
    from fetchers import fetch_stock_price
    ensure_desk_accounts()
    _narrate("A", _line(_CLOCK_IN, "A"))
    src = source_largecap(market)
    if not src["codes"]:
        _narrate("A", _line(_CLOCK_OUT, "A"))
        return {"watched": [], "sold": []}
    for bot in LARGECAP_BOTS:
        _narrate(bot, _line(_CLOCK_IN, bot))
    today = _today_kst()
    watched, sold = [], []
    for code in src["codes"]:
        name = src["names"].get(code, code)
        r = analyze_candidate(code, name, code, market, bots=LARGECAP_BOTS)
        _store_report(today, code, name, r.get("brief"), "대형주")
        for bot in LARGECAP_BOTS:
            _narrate(bot, r.get("reasonings", {}).get(bot) or f"[결정] {r['verdicts'].get(bot, '관망')} — {name}", model="sonnet")
        approvers = [b for b in LARGECAP_BOTS if r["verdicts"].get(b) == "매수"]
        if approvers:
            add_to_watch(code, name, ",".join(approvers))
            watched.append(code)
            _narrate("A", f"👀 {name} 관심종목 등록 — {'·'.join(approvers)} 매수의견. Q 진입 타이밍 대기.")
        held = get_open_positions_by_symbol(name, account="대형주")   # 강한 펀더매도(2인+) 안전판
        sellers = [b for b in LARGECAP_BOTS if r["verdicts"].get(b) == "매도"]
        if held and len(sellers) >= 2:
            price = fetch_stock_price(code if market == "US" else f"{code}.KS")
            if price and not sell_shared_position(held[0]["id"], price, exit_reasoning=f"펀더 청산({','.join(sellers)})")[1]:
                sold.append(code)
                _narrate(sellers[0], random.choice(_SELL_LINES).format(name=name))
    _narrate("A", random.choice(_A_DONE).format(desk="대형주", n=len(src["codes"])))
    for bot in LARGECAP_BOTS:
        _narrate(bot, _line(_CLOCK_OUT, bot))
    _narrate("A", _line(_CLOCK_OUT, "A"))
    return {"watched": watched, "sold": sold}


def run_largecap_execute(market="US"):
    """대형주 집행 슬롯(24시, 미장 장중) — Q가 관심종목 진입 타이밍 매수 + 보유 B+M 익절/손절. 반환 {'bought','sold'}."""
    if not NEW_DESK_ENABLED:
        return {"bought": [], "sold": []}
    import backtest as bt
    from db import (ensure_desk_accounts, get_watchlist, mark_watch, buy_shared_position,
                    sell_shared_position, get_open_positions, get_open_positions_by_symbol, DESK_SEED)
    from trading_strategies import position_amount, can_open
    ensure_desk_accounts()
    _narrate("Q", "Q 출근 — 미장 장중, 대형주 진입/청산 타이밍 봅니다.")
    up = _spy_uptrend()
    watch = get_watchlist("watching")
    held = get_open_positions(account="대형주")
    codes = list({w["code"] for w in watch} | {(p.get("code") or p["symbol"]) for p in held})
    data = bt._fetch(codes, period="2y") if codes else {}
    sold, bought = [], []
    for p in held:                                            # 청산: Q B+M 익절/손절
        o = data.get(p.get("code") or p["symbol"])
        if not o:
            continue
        strat = "M" if "미너비니" in (p.get("reasoning") or "") else "B"
        if q_exit_signal(o["close"], strat) and not sell_shared_position(p["id"], o["close"][-1], exit_reasoning=f"Q {strat} 익절/손절")[1]:
            sold.append(p["symbol"])
            _narrate("Q", f"✅ {p['symbol']} 청산 — Q {'미너비니' if strat == 'M' else '볼린저'} 익절/손절.")
    n = len(get_open_positions(account="대형주"))
    for w in watch:                                           # 진입: Q 타이밍
        if not can_open(n):
            break
        o = data.get(w["code"])
        if not o or get_open_positions_by_symbol(w["name"], account="대형주"):
            continue
        tag = q_entry_signal(o["close"], up)
        if not tag:
            continue
        _, err = buy_shared_position(w["name"], w["code"], o["close"][-1], position_amount(DESK_SEED),
                                     f"Q {'미너비니' if tag == 'M' else '볼린저'} 진입", market, account="대형주")
        if not err:
            mark_watch(w["code"], "bought")
            bought.append(w["code"])
            n += 1
            _narrate("Q", f"⏱️ {w['name']} 진입 — {'미너비니 돌파' if tag == 'M' else '볼린저 복귀'} 타이밍.")
    return {"bought": bought, "sold": sold}


# ── 하루 오케스트레이션 (수동/테스트) — 스케줄러(Phase 2)는 슬롯별로 위 함수 호출 ──
def run_new_desk_cycle(market="US"):
    """통일 데스크 하루 전체(수동/테스트용): 발굴주 매수·점검 + 대형주 선정·집행 + NAV 스냅샷.
    ⚠️ NEW_DESK_ENABLED=False면 no-op. 스케줄러(Phase 2)는 슬롯별 개별 함수 호출."""
    if not NEW_DESK_ENABLED:
        return {"discovery": {}, "discovery_review": {}, "largecap_select": {}, "largecap_execute": {}}
    d = run_discovery_desk(market)
    dr = run_discovery_review(market)
    ls = run_largecap_select(market)
    le = run_largecap_execute(market)
    _snapshot_fund_nav()
    return {"discovery": d, "discovery_review": dr, "largecap_select": ls, "largecap_execute": le}


def _snapshot_fund_nav():
    """데스크 계좌(대형주/발굴주) 오늘 NAV(현금+투자원가) 기록 — 수익률 그래프용. 실패해도 무시."""
    from db import (DESK_ACCOUNTS, get_shared_portfolio, get_open_positions,
                    record_fund_nav)
    today = _today_kst()
    for acct in DESK_ACCOUNTS:
        try:
            cash = (get_shared_portfolio(acct) or {}).get("balance") or 0
            inv = sum((p.get("amount") or 0) for p in get_open_positions(account=acct))
            record_fund_nav(acct, today, cash + inv)
        except Exception as e:
            logger.warning(f"NAV 스냅샷 실패 {acct}: {e}")
