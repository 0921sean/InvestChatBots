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
DAILY_QUOTA = 40           # A가 한 발굴 사이클에 올리는 종목 수(+S 병목 별도). 사이클마다 토큰 버킷 리셋(12/18/24)이라
                           # 대형주(~59)급으로 크게 봐도 됨. 운영값 — 조정 쉬움.

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


def _s_sourcing_note(codes, names) -> str:
    """S가 자체 소싱한 병목 종목을 '어떤 초크포인트 맥락에서 물어왔는지' 종목별 한 줄로 설명(haiku). 폴백."""
    fallback = ("제가 오늘 물어온 병목 종목은 "
                + ", ".join(_tk(c, names.get(c)) for c in codes) + "입니다. 사슬 뒤를 봅니다.")
    try:
        from agents import _call_claude_cli
        lst = "\n".join(f"- {c} ({names.get(c, c)})" for c in codes)
        # 사실 위주(회사가 공급망에서 뭘 만드는지)로 프레이밍 → 조언 거부 회피
        sysp = ("너는 반도체·AI 인프라 공급망에 밝은 기술 설명가다. 아래 회사들이 AI 인프라 사슬"
                "(광통신·인터커넥트·이더넷 스위칭·HBM·전력/냉각·화합물 기판 등)에서 각각 무슨 부품을 만들고 "
                "어느 초크포인트에 위치하는지 한 줄씩 'TICKER — 역할' 형식으로 사실 위주 설명해라. "
                "매수·투자판단·권유 아니고 '이 회사가 사슬에서 뭘 하는지'만. 반말, 간결히.")
        prompt = f"회사 목록:\n{lst}\n\n각 회사의 공급망 내 부품·역할 한 줄씩:"
        out = (_call_claude_cli(sysp, prompt, timeout=30, model="haiku") or "").strip()
        refuse = any(x in out.lower() for x in ("제공할 수 없", "할 수 없습니다", "죄송", "cannot", "can't", "financial analysis"))
        return fallback if (not out or refuse) else "오늘 제가 물어온 병목 종목입니다 (사슬 뒤를 봅니다):\n" + _clean_md(out)
    except Exception as e:
        logger.warning(f"S 소싱 노트 실패: {e}")
        return fallback


# ── 발굴 3인(P/W/S) 분석 (네트워크 + LLM) ─────────────────
import re                                          # noqa: E402

NEW_DESK_ORDER = ["P", "W", "S"]                   # (레거시) 4봇 경쟁 발굴 순서
# 통일 데스크 결정자 (설계 docs/AIFUND_PIVOT.md)
LARGECAP_BOTS = ["P", "W", "H"]                    # 대형주 = 성장주·가치·실적왕(H) → 관심종목 캐시
DISCOVERY_BOTS = ["P", "W", "S"]                   # 발굴주 = 성장주·가치·병목 → 즉시매수
# 데스크별 사이징 — 대형주 집중(고확신), 발굴주 분산(리스크). 각 시드 DESK_SEED(5,000만) 기준.
DESK_SIZING = {"대형주": {"weight": 0.08, "max": 12},   # 종목당 8%(400만) · 최대 12
               "발굴주": {"weight": 0.04, "max": 20}}   # 종목당 4%(200만) · 최대 20
_VERDICT_RE = re.compile(r"\[결정\]\s*(매수|관망|매도)")


def _desk_amount(desk: str) -> float:
    from db import DESK_SEED
    return DESK_SEED * DESK_SIZING[desk]["weight"]


def _desk_can_open(desk: str, open_count: int) -> bool:
    return open_count < DESK_SIZING[desk]["max"]


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


# 재무 키(캐시 대상) — 리밋으로 빠졌을 때 last-good로 채운다.
_FUND_KEYS = ("per", "per_fwd", "eps", "roe", "peg", "eps_growth", "gross_margin",
              "op_margin", "profit_margin", "rev_growth", "fcf", "market_cap",
              "revenue", "debt_ratio", "business_summary")


def _fetch_brief_data(code, yf_ticker, name, market="US"):
    """fetch_stock_data + 일일 재무 캐시. 재무가 오면 캐시 갱신, 리밋으로 비면 last-good로 폴백.
    → API 리밋에도 P/W/H가 판단 근거(재무)를 늘 확보."""
    from fetchers import fetch_stock_data
    import db
    data = fetch_stock_data(code, yf_ticker, name, market=market)
    fresh = {k: data[k] for k in _FUND_KEYS if k in data}
    if fresh:                                         # 재무 확보 → 캐시 갱신
        try:
            db.save_fundamentals_cache(code, _today_kst(), fresh)
        except Exception as e:
            logger.warning(f"재무 캐시 저장 실패 {code}: {e}")
    else:                                             # 리밋 등으로 재무 없음 → last-good 폴백
        cached = None
        try:
            cached = db.get_fundamentals_cache(code)
        except Exception:
            pass
        if cached:
            for k, v in cached.items():
                data.setdefault(k, v)
            data.pop("_data_unavailable", None)
            data["_fund_cached"] = True
    return data


def build_research_brief(code, name, yf_ticker, market="US"):
    """A의 리서치 브리프 — P/W/S에게 넘길 최적화 다이제스트(뉴스 없이·중립 팩트만).
    현재 재무 + 심화(마진·성장·FCF) + 분기 다기간 추세. A가 종목당 1회 준비 → P/W/S 공용.
    반환: (packet_text, business_summary)."""
    from fetchers import format_stock_data
    data = _fetch_brief_data(code, yf_ticker, name, market=market)
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
        "위 종목을 네 투자 원칙으로 평가해줘. 팀 채팅에 올리는 것처럼 **마크다운·소제목(##·**) 없이 자연스러운 3~4문장**으로,",
        "네 관점의 핵심 근거(밸류에이션·성장·해자·리스크 등)를 구체 수치와 함께 풀어서 말해줘.",
        "그리고 **마지막 줄만** 반드시 `[결정] 관망 | 이유: (한 줄 요약)` 형식으로 끝내라. 결정은 매수/관망/매도 중 하나.",
    ]
    return "\n".join(parts)


def _parse_verdict(text) -> str:
    """응답에서 [결정] 추출. 없으면 '관망'(보수적 기본값)."""
    m = _VERDICT_RE.search(text or "")
    return m.group(1) if m else "관망"


def _decision_line(verdict: str, reason: str = "") -> str:
    """종목 판단 표준 포맷 `[결정] {verdict} | 이유: {reason}`."""
    reason = (reason or "").strip() or "근거 미확보"
    return f"[결정] {verdict} | 이유: {reason}"


def format_stock_verdict(reasoning: str, verdict: str, name: str = "") -> str:
    """봇 종목 판단을 `[결정] X | 이유: …` 한 줄로 정규화(요약 필요할 때)."""
    txt = (reasoning or "").strip()
    m = re.search(r"\[결정\]\s*(?:매수|관망|매도)\s*\|\s*이유\s*[:：]\s*(.+)", txt)
    if m:
        return _decision_line(verdict, m.group(1).strip())
    body = _VERDICT_RE.split(txt)[0].strip()
    reason = ""
    if body:
        reason = [s for s in re.split(r"[.\n]", body) if s.strip()][-1].strip()[:80] if body else ""
    return _decision_line(verdict, reason)


def _verdict_reason(reasoning: str) -> str:
    """봇 [결정] 줄에서 '이유:' 뒤 텍스트만 추출(매수 이유 요약용). 없으면 ''."""
    m = re.search(r"\[결정\]\s*(?:매수|관망|매도)\s*\|\s*이유\s*[:：]\s*(.+)", reasoning or "")
    return m.group(1).strip() if m else ""


def _clean_md(text: str) -> str:
    """피드 노출용 — 마크다운 소제목(##)·볼드(**) 제거, 빈 줄 정리."""
    text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text or "")
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def verdict_message(reasoning: str, verdict: str, name: str = "") -> str:
    """봇 종목 판단 피드 메시지 — 근거 본문 전체 + 끝줄 [결정]. 마크다운 정리, [결정] 없으면 붙임."""
    txt = _clean_md(reasoning)
    if re.search(r"\[결정\]\s*(?:매수|관망|매도)", txt):
        return txt
    return (txt + "\n\n" + _decision_line(verdict)) if txt else _decision_line(verdict)


def analyze_stock(code, name, yf_ticker, bot, market="US", brief=None):
    """발굴봇 1인이 한 종목 분석 → thesis 캐시에 저장. 반환: (verdict, reasoning).
    brief: A가 준비한 (packet_text, business_summary). 없으면 직접 준비(단독 호출용)."""
    from agents import call_agent
    from prompts import AGENT_PROFILES
    from db import save_thesis

    packet, business = brief if brief else build_research_brief(code, name, yf_ticker, market)
    prompt = build_analysis_prompt(name, code, packet, business)
    # trim=False: 끝줄 [결정]이 문장단위 트림에 잘리지 않게(잘리면 관망 오폴백·메시지 끊김)
    reasoning = call_agent(bot, AGENT_PROFILES[bot]["system"], prompt, model="sonnet", trim=False)
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
    "P": ["P 출근. 오늘도 생활 속에서 크게 자랄 놈 찾아봅니다.",
          "P 왔어요. PEG 싼 성장주 뭐 없나 보죠.",
          "출근! 스토리 살아있는 놈으로 골라봅니다."],
    "W": ["W 출근했습니다. 좋은 회사를 적정가에.",
          "W 도착. 능력범위 안, 해자 있는 것만 봅니다.",
          "출근이요. 10년 들고 갈 만한지부터 봅니다."],
    "H": ["H 출근. 실적으로 증명된 놈만 봅니다.",
          "H 왔습니다. 성장률·마진부터 확인하죠.",
          "출근! 숫자 안 나오면 안 삽니다."],
    "S": ["S 출근 — 오늘의 곡괭이 찾으러 갑니다.",
          "S 왔습니다. 남들 완제품 볼 때 저는 사슬 뒤를 봐요.",
          "출근! 공급망 초크포인트부터 직접 뒤져봅니다."],
}
_CLOCK_OUT = {
    "A": ["오늘 리서치 끝 — A 퇴근합니다. 다들 수고했어요 🫡",
          "정리 끝났습니다. A 퇴근할게요, 내일 또 좋은 종목으로.",
          "계좌 성적은 우측에서 보세요. A 퇴근합니다."],
    "P": ["P 퇴근. 오늘 고른 놈들 잘 자라라~",
          "P 이만 퇴근합니다. 인내가 수익이죠.",
          "먼저 퇴근할게요. 좋은 회사는 시간이 증명해요."],
    "W": ["W 퇴근. 서두르지 않습니다, 늘 그렇듯.",
          "W 관점 정리 끝, 퇴근합니다.",
          "이만 퇴근이요. 가격이 오면 그때 더 담죠."],
    "H": ["H 퇴근. 결국 실적이 답이죠.",
          "숫자 확인 끝, H 퇴근합니다.",
          "먼저 퇴근이요. 다음 실적 시즌도 지켜보죠."],
    "S": ["S 퇴근 — 곡괭이는 조용히 값을 합니다.",
          "병목 점검 끝. 사슬은 안 끊기니까요. 퇴근!",
          "먼저 퇴근합니다. 다음 초크포인트는 내일 또."],
}
_A_DONE = [
    "{desk} {n}종목 리서치 정리해 올렸어요 — 우측 '리서치 보드' 참고!",
    "{n}종목 다 봤습니다. 뭐하는 회사인지 리포트로 정리해뒀어요.",
    "{desk} 리포트 {n}건 업데이트. 숫자는 거기서 확인하세요.",
]
_BUY_LINES = ["💰 {name} 매수 체결 — 내 계좌에 담았어요.",
              "{name} 샀습니다. 논지대로 갑니다.",
              "{name} 편입 완료. 지켜보죠."]
_SELL_LINES = ["🔻 {name} 청산 — 논지가 훼손돼 뺐어요.",
               "{name} 던졌습니다. 얘기가 달라졌네요.",
               "{name} 정리 — 이유가 사라졌으니 홀드할 근거도 없죠."]
# 대형주 핸드오프 — P/W/H가 선정 후 Q에게 타이밍 넘김("살펴봐주세요")
_HANDOFF_LINES = ["{name} 괜찮아 보여요. Q, 진입 타이밍 봐주세요 🙏",
                  "{name} 관심종목으로 올립니다 — Q, 언제 들어갈지 판단 부탁해요.",
                  "{name} 펀더는 좋네요. 타이밍은 Q에게 맡길게요."]


def _line(pool, bot):
    return random.choice(pool.get(bot) or [""])


def _q_snapshot(closes) -> str:
    """Q가 보는 기술적 상태 한 줄 — 현재가·200일선·20일선·볼밴 %b·52주고점比. (순수 계산)"""
    import backtest as bt
    px = closes[-1]
    parts = [f"현재가 {px:.1f}"]
    if len(closes) >= 200:
        s200 = bt._sma(closes, 200)
        parts.append(f"200일선 {s200:.1f}({'위' if px > s200 else '아래'})")
    if len(closes) >= 20:
        import statistics
        w = closes[-20:]
        mb, sd = sum(w) / 20, statistics.pstdev(w)
        up_b, lo_b = mb + 2 * sd, mb - 2 * sd
        pctb = (px - lo_b) / (up_b - lo_b) if up_b != lo_b else 0.5
        parts.append(f"볼밴 %b {pctb:.2f}")
    hi = max(closes[-252:]) if len(closes) >= 20 else max(closes)
    if hi:
        parts.append(f"52주 고점 대비 {(px / hi - 1) * 100:+.1f}%")
    return " · ".join(parts)


def _q_say(name, closes, verdict) -> str:
    """Q가 기술적 스냅샷을 근거로 판단(진입/대기/홀드/청산)과 이유를 1~2문장 설명(haiku). 폴백 규칙.
    ⚠️ LLM 호출 — 대형주 집행(NEW_DESK_ENABLED) 안에서만."""
    snap = _q_snapshot(closes)
    fb = {
        "진입": f"{name} — {snap}. 진입 신호 떠서 담습니다.",
        "대기": f"{name} — {snap}. 아직 진입 자리가 아니라 지켜봅니다.",
        "홀드": f"{name} — {snap}. 청산 신호 없어 계속 보유.",
        "청산": f"{name} — {snap}. 청산 신호 떠서 정리합니다.",
    }.get(verdict, f"{name} — {snap}.")
    try:
        from agents import _call_claude_cli
        # '이미 규칙이 낸 판단'의 코멘트로 프레이밍 → 조언 거부 회피(_q_explain과 동일 트릭)
        sysp = ("너는 추세추종·평균회귀 블렌드 '규칙'으로만 돌아가는 가상계좌 퀀트봇 Q다. "
                "규칙이 이미 이 종목의 판단(진입/대기/홀드/청산)을 냈다 — 조언 요청이 아니라, "
                "'규칙이 왜 이 결론을 냈는지'를 기술적 숫자를 근거로 팀원에게 반말로 전하는 캐주얼 코멘트다. "
                "딱 1~2문장. 면책·주의·질문·인사·조언성 표현 금지, 내 가상계좌 관점.")
        prompt = f"{name} 기술적 상태: {snap}\n규칙 결론: {verdict}\n왜 그 결론인지 팀에 한 줄로:"
        out = (_call_claude_cli(sysp, prompt, timeout=25, model="haiku") or "").strip().split("\n\n")[0].strip().lstrip("> ").strip('"')
        refuse = any(x in out.lower() for x in (
            "can't", "cannot", "확인할 수 없", "필요합니다", "roleplay", "실시간",
            "제공할 수 없", "할 수 없습니다", "죄송", "조언", "금융 자문", "거래 신호를"))
        if not out or refuse:
            return fb
        if not out.startswith(name):                          # 무슨 종목인지 앞에 명시
            out = f"{name} — {out}"
        return out
    except Exception as e:
        logger.warning(f"Q 코멘트 실패 {name}: {e}")
        return fb


def _q_explain(name, action, tag, approvers=None):
    """Q가 매매 이유를 대화체로 설명(haiku, 저비용). 실패 시 규칙 기반 폴백.
    ⚠️ LLM 호출 — 대형주 집행(NEW_DESK_ENABLED) 안에서만."""
    strat = "추세 돌파" if tag == "M" else "밴드 되돌림"
    fallback = f"{name} {action} — {strat} 신호."
    try:
        from agents import _call_claude_cli
        who = f"{approvers}가 올린 " if approvers else ""
        sysp = ("너는 규칙(추세추종·평균회귀 블렌드)으로만 매매하는 가상계좌 퀀트봇 Q다. "
                "규칙 신호가 떠서 이미 매매를 끝냈다 — 이건 분석 요청이 아니라 '이미 한 매매'의 캐주얼 코멘트다. "
                "그 규칙이 왜 이 자리를 잡았는지 팀원에게 반말로 딱 1~2문장. 면책·주의·질문·인사 금지. "
                "미래 보장·권유 금지, 내 가상계좌 관점으로만.")
        prompt = f"{who}{name}에 '{strat}' 규칙 신호가 떠서 방금 {action}함. 팀에 한 줄 코멘트로 전해줘."
        out = (_call_claude_cli(sysp, prompt, timeout=30, model="haiku") or "").strip()
        out = out.split("\n\n")[0].strip().lstrip("> ").strip('"')   # 첫 문단만, 인용부호 제거
        refuse = any(x in out.lower() for x in ("can't", "cannot", "확인할 수 없", "필요합니다", "roleplay", "실시간"))
        return fallback if (not out or refuse) else out
    except Exception as e:
        logger.warning(f"Q 설명 실패 {name}: {e}")
        return fallback


def _summarize_ko(name, business_summary):
    """영문 사업요약 → '뭐하는 회사'를 1~2문장 한국어로(가벼운 haiku). 실패 시 빈 문자열.
    ⚠️ LLM 호출 — run_new_desk_cycle(NEW_DESK_ENABLED) 안에서만 불린다."""
    if not business_summary:
        return ""
    try:
        from agents import _call_claude_cli
        sys = ("너는 기업을 처음 보는 사람에게 소개하는 애널리스트다. 평가·전망·추천 없이, "
               "이 회사가 ①뭘 팔아서 어떻게 버는지 쉬운 말로 + ②'아 이런 회사구나' 싶은 포인트"
               "(대표 제품·서비스, 업계 위상·규모, 어디에 쓰이는지 등 처음 보는 사람이 궁금해할 것)를 "
               "담백하게 2~3문장 한국어로. 투자판단·주가 얘기는 하지 마라.")
        prompt = f"[회사] {name}\n[영문 개요]\n{business_summary}\n\n처음 보는 사람용 소개 2~3문장:"
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


def _largecap_sectors() -> dict:
    """{섹터: [코드]} — 대형주 섹터 토론용. private → example → {}."""
    try:
        from strategy_private import LARGECAP_SECTORS as s
    except Exception:
        try:
            from strategy_private_example import LARGECAP_SECTORS as s
        except Exception:
            s = {}
    return dict(s or {})


def sector_of(code: str) -> str:
    """코드의 섹터명. 못 찾으면 '기타'."""
    for sec, codes in _largecap_sectors().items():
        if code in codes:
            return sec
    return "기타"


def _strip_decision(text: str) -> str:
    """[결정] 줄 제거 — 섹터 의견·합의엔 종목 결정이 새지 않게."""
    lines = [ln for ln in (text or "").splitlines() if "[결정]" not in ln]
    return "\n".join(lines).strip()


def _sector_opinion(bot, sector, names, market="US"):
    """봇 1인의 섹터 관점(자유 형식, 짧게) — 종목 [결정] 아님. LLM. [결정]이 새면 제거."""
    from agents import call_agent
    from prompts import AGENT_PROFILES
    lst = ", ".join(names[:6])
    prompt = (f"'{sector}' 섹터를 네 투자 원칙으로 지금 어떻게 보는지 2~3문장으로 코멘트해줘. "
              f"이 섹터 대표주: {lst}. 특정 종목 판단·[결정] 표기는 절대 하지 말고 섹터 전반의 큰 그림만. "
              "미래 수익 보장·매수 권유 금지, 내 가상계좌 관점으로만.")
    return _strip_decision(call_agent(bot, AGENT_PROFILES[bot]["system"], prompt, model="sonnet"))


def _summarize_consensus(sector, opinions) -> str:
    """A가 P/W/H 섹터 의견을 2~3문장 합의로 요약(리서치 보드용). [결정]·종목 언급 없음. 실패 시 폴백."""
    valid = {b: o for b, o in opinions.items() if o}
    if not valid:
        return ""
    joined = "\n".join(f"{b}: {(o or '')[:250]}" for b, o in valid.items())   # 의견 트리밍 — 요약 프롬프트 비대·타임아웃 방지
    fallback = f"{sector} — 세 애널리스트 모두 개별 종목 밸류에이션 확인 전 판단 유보 기조."
    try:
        from agents import _call_claude_cli
        sysp = ("너는 애널리스트 A다. 세 동료의 섹터 의견을 팀 게시판에 올릴 2~3문장 '합의 요약'으로 정리한다. "
                "공통 시각과 갈리는 지점을 중립적으로. 특정 종목명·[결정]·매수권유 금지, 섹터 큰 그림만.")
        prompt = f"'{sector}' 섹터 동료 의견:\n{joined}\n\n합의 요약 2~3문장:"
        out = _strip_decision((_call_claude_cli(sysp, prompt, timeout=60, model="haiku") or "").strip())
        return out or fallback
    except Exception as e:
        logger.warning(f"섹터 합의 요약 실패 {sector}: {e}")
        return fallback


def _store_sector_consensus(today, sector, opinions):
    """섹터 합의(A 요약) → 리서치 보드 저장. 실패해도 무시."""
    from db import record_fund_report
    summary = _summarize_consensus(sector, opinions)
    try:
        record_fund_report(today, f"섹터:{sector}", sector, summary, "", "섹터합의")
    except Exception as e:
        logger.warning(f"섹터 합의 저장 실패 {sector}: {e}")


# 대화용 종목 표기 = 티커 (상세종목명). 현재 섹터 박스는 티커만(별도).
def _tk(code, name=None) -> str:
    nm = name or stock_name(code)
    return f"{code} ({nm})" if nm and nm != code else code


# A가 종목 차례를 알리는 인트로(varied) — P/W/H에게 자연스럽게 넘긴다. {tk}=티커(상세명).
_STOCK_INTRO = [
    "자, 다음은 {tk} 볼 차례네요.",
    "이번엔 {tk} 올려봅니다.",
    "다음 종목은 {tk}입니다.",
    "{tk}, 이건 어떻게들 보세요?",
]
_STOCK_HANDOFF = ["세 분 판단 부탁해요.", "다들 어떻게 보시는지?", "의견 주세요!", "판단 넘길게요."]


def _stock_data_msg(name, code, brief) -> str:
    """A가 종목 차례를 알리며 핵심 재무(=TradingView 데이터)를 올리고 P/W/H에게 넘기는 멘트."""
    packet = (brief or ("", ""))[0] or ""
    keys = ("현재가", "PER", "PEG", "EPS", "ROE", "마진", "성장", "매출", "잉여현금")
    keep = [ln.strip() for ln in packet.splitlines() if any(k in ln for k in keys)][:6]
    body = " · ".join(keep) if keep else "핵심 재무 데이터가 잘 안 잡히네요"
    intro = random.choice(_STOCK_INTRO).format(tk=_tk(code, name))
    return f"{intro} 📊  {body}\n{random.choice(_STOCK_HANDOFF)}"


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
    """A 리포트 저장(재무 + 발굴주는 '처음 보는 사람용' 소개). 실패해도 무시.
    대형주는 다 아는 메가캡이라 사업 소개 생략(재무만) — 토큰도 아낌."""
    from db import record_fund_report
    packet, biz = brief or ("", "")
    summary = (_summarize_ko(name, biz) or biz) if desk_tag == "발굴주" else ""
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
                    buy_shared_position, get_open_positions)
    from fetchers import fetch_stock_price
    ensure_desk_accounts()
    _narrate("A", _line(_CLOCK_IN, "A"))
    src = source_today(market)
    _narrate("A", src["briefing"])
    s_src = source_bottleneck(market)
    if s_src["codes"]:                                        # S가 자체 소싱한 병목 종목 + 맥락(왜 병목인지)을 직접 알림
        _narrate("S", _s_sourcing_note(s_src["codes"], s_src["names"]))
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
        _narrate("A", _stock_data_msg(name, code, r.get("brief")))   # A가 종목+재무 제시(대형주와 일관)
        for bot in DISCOVERY_BOTS:
            _narrate(bot, verdict_message(r.get("reasonings", {}).get(bot, ""), r["verdicts"].get(bot, "관망"), name), model="sonnet")
        approvers = [b for b in DISCOVERY_BOTS if r["verdicts"].get(b) == "매수"]
        if not approvers or get_open_positions_by_symbol(name, account="발굴주"):
            continue
        if not _desk_can_open("발굴주", len(get_open_positions(account="발굴주"))):
            continue
        price = fetch_stock_price(code if market == "US" else f"{code}.KS")
        if not price:
            continue
        reason = _verdict_reason(r["reasonings"].get(approvers[0], ""))   # 대표 승인봇의 매수 이유
        rz = f"발굴주 매수 ({','.join(approvers)})" + (f" — {approvers[0]}: {reason}" if reason else "")
        _, err = buy_shared_position(name, code, price, _desk_amount("발굴주"), rz, market, account="발굴주")
        if not err:
            buys.append(code)
            buy_msg = random.choice(_BUY_LINES).format(name=_tk(code, name))
            if reason:
                buy_msg += f" ({approvers[0]} 판단: {reason})"
            _narrate(approvers[0], buy_msg)
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


# ── 대형주 데스크 (매일 전 섹터 재분석: 섹터 토론 → 종목 [결정] → 그날 관심종목 / Q 진입·청산) ──
def run_largecap_select(market="US"):
    """대형주 선정 슬롯(06시) — 전 섹터 매일 재분석. 섹터별 P/W/H 의견(섹터 합의) → 종목별 [결정] OR게이트 → 그날 관심종목.
    캐시 없이 매일 다시 합격을 낸다(그날 Q도 합격 신호면 매수). 보유중 강한 펀더매도(2인+)면 즉시청산.
    반환 {'watched','sold','sectors'}."""
    if not NEW_DESK_ENABLED:
        return {"watched": [], "sold": [], "sectors": []}
    from db import (ensure_desk_accounts, add_to_watch, clear_watchlist,
                    get_open_positions_by_symbol, sell_shared_position, get_fund_reports)
    from fetchers import fetch_stock_price
    ensure_desk_accounts()
    sectors = _largecap_sectors()
    if market != "US" or not sectors:
        return {"watched": [], "sold": [], "sectors": []}
    today = _today_kst()
    # 오늘 이미 끝낸 것 파악 → 재시작 시 처음부터 안 하고 이어감(idempotent resume, 토큰 절약).
    reps = [r for r in get_fund_reports(300) if r.get("date") == today]
    done_codes = {r["code"] for r in reps if r.get("desk") != "섹터합의"}
    done_secs = {r["name"] for r in reps if r.get("desk") == "섹터합의"}
    if not done_secs:                                    # 오늘 첫 실행일 때만 어제 관심종목 비움
        clear_watchlist()
    _narrate("A", _line(_CLOCK_IN, "A"))
    for bot in LARGECAP_BOTS:
        _narrate(bot, _line(_CLOCK_IN, bot))
    watched, sold, done = [], [], []
    for sector, codes in sectors.items():
        if sector in done_secs and all(c in done_codes for c in codes):
            continue                                     # 섹터 완전 완료 → 스킵(resume)
        names = {c: stock_name(c) for c in codes}
        if sector not in done_secs:                      # 1단계: 섹터 토론(합의 없을 때만 — 재분석 스킵)
            _narrate("A", f"{sector} 섹터, 오늘 어떻게 보세요?")
            opinions = {}
            for bot in LARGECAP_BOTS:
                try:
                    op = _sector_opinion(bot, sector, list(names.values()), market)
                except Exception as e:
                    logger.warning(f"섹터 의견 실패 {sector}@{bot}: {e}")
                    op = ""
                opinions[bot] = op
                if op:
                    _narrate(bot, op, model="sonnet")
            _store_sector_consensus(today, sector, opinions)
        done.append(sector)
        for code in codes:                                          # 2단계: 종목별 A가 데이터 올림 → P/W/H 판단
            if code in done_codes:                                  # 이미 분석한 종목 스킵(resume)
                continue
            name = names.get(code, code)
            r = analyze_candidate(code, name, code, market, bots=LARGECAP_BOTS)
            _store_report(today, code, name, r.get("brief"), "대형주")
            _narrate("A", _stock_data_msg(name, code, r.get("brief")))   # A가 종목+재무 제시
            for bot in LARGECAP_BOTS:
                _narrate(bot, verdict_message(r.get("reasonings", {}).get(bot, ""), r["verdicts"].get(bot, "관망"), name), model="sonnet")
            approvers = [b for b in LARGECAP_BOTS if r["verdicts"].get(b) == "매수"]
            if approvers:
                add_to_watch(code, name, ",".join(approvers))
                watched.append(code)
                _narrate(approvers[0], random.choice(_HANDOFF_LINES).format(name=name))   # 핸드오프 → Q
            held = get_open_positions_by_symbol(name, account="대형주")   # 강한 펀더매도(2인+) 안전판
            sellers = [b for b in LARGECAP_BOTS if r["verdicts"].get(b) == "매도"]
            if held and len(sellers) >= 2:
                price = fetch_stock_price(code if market == "US" else f"{code}.KS")
                if price and not sell_shared_position(held[0]["id"], price, exit_reasoning=f"펀더 청산({','.join(sellers)})")[1]:
                    sold.append(code)
                    _narrate(sellers[0], random.choice(_SELL_LINES).format(name=name))
    _narrate("A", random.choice(_A_DONE).format(desk="대형주", n=len(watched)))
    for bot in LARGECAP_BOTS:
        _narrate(bot, _line(_CLOCK_OUT, bot))
    _narrate("A", _line(_CLOCK_OUT, "A"))
    return {"watched": watched, "sold": sold, "sectors": done}


def run_largecap_execute(market="US"):
    """대형주 집행 슬롯(24시, 미장 장중) — Q가 관심종목 진입 타이밍 매수 + 보유 B+M 익절/손절. 반환 {'bought','sold'}."""
    if not NEW_DESK_ENABLED:
        return {"bought": [], "sold": []}
    import backtest as bt
    from db import (ensure_desk_accounts, get_watchlist, mark_watch, buy_shared_position,
                    sell_shared_position, get_open_positions)
    ensure_desk_accounts()
    _narrate("Q", "Q 출근 — 미장 장중, 대형주 진입/청산 타이밍 봅니다.")
    up = _spy_uptrend()
    watch = get_watchlist("watching")
    held = get_open_positions(account="대형주")
    held_codes = {(p.get("code") or p["symbol"]) for p in held}
    cand = [w for w in watch if w["code"] not in held_codes]  # 신규 후보 = 관심종목 중 아직 미보유(재승인 보유분은 추가매수 X)
    codes = list({w["code"] for w in watch} | held_codes)
    brief = []
    if cand:
        brief.append(f"관심종목 {', '.join(w['code'] for w in cand)}")
    if held_codes:
        brief.append(f"보유분 {', '.join(sorted(held_codes))}")
    if brief:
        _narrate("Q", "오늘 볼 종목 — " + " · ".join(brief) + ". 후보 타이밍부터 봅니다.")
    data = bt._fetch(codes, period="2y") if codes else {}
    sold, bought = [], []
    n = len(held)
    for w in cand:                                            # ① 신규 후보 진입 타이밍
        if not _desk_can_open("대형주", n):
            break
        o = data.get(w["code"])
        if not o:
            continue
        tag = q_entry_signal(o["close"], up)
        _narrate("Q", _q_say(_tk(w["code"], w.get("name")), o["close"], "진입" if tag else "대기"))   # 티커(상세명)·이유
        if not tag:
            continue
        appr = w.get("approved_by") or ""                     # 관심등록 찬성봇(P/W/H) — 픽 성과 크레딧용
        rz = f"Q {'추세돌파' if tag == 'M' else '되돌림'} 진입" + (f" · 관심 {appr}" if appr else "")
        _, err = buy_shared_position(w["name"], w["code"], o["close"][-1], _desk_amount("대형주"),
                                     rz, market, account="대형주")
        if not err:
            mark_watch(w["code"], "bought")
            bought.append(w["code"])
            n += 1
            _narrate("Q", f"⏱️ {_tk(w['code'], w.get('name'))} 매수 체결 — 내 계좌에 담았습니다.")
    if held:                                                  # ② 보유 종목 점검(추가매수 없음 — 홀드/청산만)
        _narrate("Q", "이제 갖고 있는 종목들 점검할게요.")
        for p in held:
            code = p.get("code") or p["symbol"]
            o = data.get(code)
            if not o:
                continue
            strat = "M" if "추세돌파" in (p.get("reasoning") or "") else "B"
            exiting = bool(q_exit_signal(o["close"], strat))
            _narrate("Q", _q_say(_tk(code), o["close"], "청산" if exiting else "홀드"))
            if exiting and not sell_shared_position(p["id"], o["close"][-1], exit_reasoning=f"Q {'추세' if strat == 'M' else '되돌림'} 익절/손절")[1]:
                sold.append(p["symbol"])
    _narrate("Q", "오늘 대형주 타이밍 점검 끝 — Q 퇴근합니다. 🫡")   # ③ 퇴근
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
