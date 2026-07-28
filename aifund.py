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
