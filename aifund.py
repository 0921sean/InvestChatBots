"""
AI 펀드 팀룸 — 설계 docs/AIFUND_PIVOT.md.
2단계: A(소싱봇). 유니버스 랜덤 발굴 + 어닝 카탈리스트로 캐시 무효화 + 모닝 브리핑.

⚠️ NEW_DESK_ENABLED=False — 아직 어떤 사이클에도 배선 안 됨(라이브 무변경).
순수 로직(select_candidates/build_briefing)은 네트워크 없이 테스트 가능,
네트워크(_recent_earnings_date)는 격리.
"""
import logging
import os
import random
import threading
import time as _time
from datetime import datetime, timezone, timedelta

from db import get_cached_codes, get_thesis, invalidate_thesis

logger = logging.getLogger("investchat.aifund")

# ── 토글 · 상한 ──────────────────────────────────────────
NEW_DESK_ENABLED = os.getenv("NEW_DESK_ENABLED", "").lower() in ("1", "true", "yes")  # 컷오버 게이트(.env). off면 fund 잡 no-op.
# 6시 병목 큐레이션 게이트(.env). off면 큐레이션 no-op — 웹서치=구독 토큰 추가 소모라 명시 활성화(로드맵 ②d).
BOTTLENECK_CURATION_ENABLED = os.getenv("BOTTLENECK_CURATION_ENABLED", "").lower() in ("1", "true", "yes")
BOTTLENECK_CURATION_QUOTA = 5   # 하루 1콜로 올리는 후보 상한(토큰 절약). 운영값.
# 매수 결재 게이트(.env). on이면 봇 매수 판단이 즉시 체결 대신 '오너 승인 대기'(/owner/approvals).
# 사이클은 결재만 올리고 계속 진행(논블로킹). 청산·손절은 자동(매수만 결재). off면 기존처럼 자동 매수.
BUY_APPROVAL_REQUIRED = os.getenv("BUY_APPROVAL_REQUIRED", "").lower() in ("1", "true", "yes")
# M 거시 브리핑 게이트(.env). on이면 매일 아침 블로그 새 글 크롤 + 거시 브리핑 생성(구독 토큰).
MACRO_BRIEFING_ENABLED = os.getenv("MACRO_BRIEFING_ENABLED", "").lower() in ("1", "true", "yes")
# 관찰 단계 게이트(.env). on이면 발굴주 매수 판단이 즉시 결재/체결이 아니라 '관찰 등록' →
# 사이클마다 재관찰(자기 논지 vs 현재) → 최소 OBSERVATION_MIN_REVIEWS회 후에도 확신이면 그때 결재.
OBSERVATION_REQUIRED = os.getenv("OBSERVATION_REQUIRED", "").lower() in ("1", "true", "yes")
OBSERVATION_MIN_REVIEWS = 2     # 확신 상신 전 최소 재관찰 횟수(발굴 사이클 0/12/18 기준 ≈ 하루)
# 워크데이 모델(.env WORKDAY_ENABLED): 고정 시간표 폐지 — 출근(아침 블록) 후 종일 스터디 라운드.
# 토큰 소진 시 쉬었다가 충전되면 재출근(keeper가 매시 확인). P/W 공부 시간이 길어짐(라운드×딥스터디).
WORKDAY_ENABLED = os.getenv("WORKDAY_ENABLED", "").lower() in ("1", "true", "yes")
WORKDAY_END_HOUR = 22           # 이 시각(KST) 넘으면 퇴근
WORKDAY_ROUND_QUOTA = 8         # 발굴 라운드당 신규 후보 수(작게·깊게 — 하루 여러 라운드)
WORKDAY_BREAK_SEC = 20 * 60     # 라운드 간 휴식(데이터소스·피드 페이싱)
DAILY_QUOTA = 40           # A가 한 발굴 사이클에 올리는 종목 수(+S 병목 별도). 사이클마다 토큰 버킷 리셋(12/18/24)이라
                           # 대형주(~59)급으로 크게 봐도 됨. 운영값 — 조정 쉬움.

# 공개 표기용 이니셜 치환(ROT13) — 내부 코드/DB는 A/P/W/H/S/Q/M 그대로 두고 '출력·표시'에서만 치환해
# 전략 유추 방지(오너는 ROT13으로 복호화). API 응답(main)·표시 콘텐츠에서만 사용. 미등록 이름은 그대로.
_FUND_PUB = {"A": "N", "P": "C", "W": "J", "H": "U", "S": "F", "Q": "D", "M": "Z", "R": "E"}


def pub_letter(name):
    """펀드 봇 내부 이니셜 → 공개 표기(ROT13). 위원회 한글명 등은 그대로 통과."""
    return _FUND_PUB.get(name, name)


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
    """티커 → 회사명. 이름은 안 변하니 프로세스 캐시 → DB 영속 캐시 → yfinance 순. DB에 있으면 .info 안 침(병목 제거)."""
    if code in _name_cache:
        return _name_cache[code]
    from db import get_stock_name, save_stock_name
    cached = get_stock_name(code)
    if cached:
        _name_cache[code] = cached
        return cached
    nm = code
    try:
        import yfinance as yf
        info = yf.Ticker(code).info or {}
        nm = (info.get("shortName") or info.get("displayName") or info.get("longName") or code).strip()
    except Exception as e:
        logger.debug(f"이름 조회 실패 {code}: {e}")
    if nm and nm != code:
        save_stock_name(code, nm)                    # 성공 조회만 영속 저장(다음 사이클부턴 .info 스킵)
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


def _hardcoded_bottleneck_seed():
    """하드코딩 병목 시드 — 비공개(strategy_private), 없으면 example(빈값) 폴백. DB 백필 원천."""
    try:
        from strategy_private import US_BOTTLENECK_SEED as seed
    except Exception:
        try:
            from strategy_private_example import US_BOTTLENECK_SEED as seed
        except Exception:
            seed = []
    return list(seed or [])


def _bottleneck_seed():
    """S 워치리스트 = DB에서 승인(approved)된 병목 시드(로드맵 ②b: 사람 큐레이션).
    테이블이 비어 있으면 최초 1회 하드코딩 시드를 approved로 백필 → 라이브 동작 그대로 보존."""
    from db import get_bottleneck_seed_rows, get_bottleneck_seeds, backfill_bottleneck_seeds
    if not get_bottleneck_seed_rows():                   # 최초 1회만: 하드코딩 → approved
        backfill_bottleneck_seeds(_hardcoded_bottleneck_seed())
    return get_bottleneck_seeds("approved")


def source_bottleneck(market="US", quota=None):
    """S 자체 소싱 — 병목 시드를 '상시 워치리스트'로 매일 재평가('발굴'이 S의 엣지).
    영구 thesis 캐시로 빼면 초기 소진 후 S가 영영 놀게 되므로(구 버그), 시드에서
    '오늘 이미 분석함' + '이미 보유 중'만 제외하고 나머지를 재소싱. 반환: {'codes','names'}."""
    quota = quota or DAILY_QUOTA
    seed = _bottleneck_seed()
    if market != "US" or not seed:                   # 병목 시드는 미장 전용
        return {"codes": [], "names": {}}
    from db import get_fund_reports, get_open_positions
    today = _today_kst()
    done_today = {r["code"] for r in get_fund_reports(300) if r.get("date") == today}
    held = {p.get("code") for p in get_open_positions(account="발굴주")}
    codes = [c for c in seed if c not in done_today and c not in held][:quota]
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


def submit_bottleneck_candidates(candidates, source="agent") -> list:
    """②d 큐레이션 산출물을 결재 큐에 올림 — pending 등록 + S '결재 올림' 내레이션 + 오너 ntfy.
    candidates: [{'ticker','rationale'}] 또는 [ticker,...]. 반환: 새로 올라간 티커 목록.
    ※ 승인은 사람(Admin /owner/seeds). 여기선 소싱만 — 매수·평가 안 함."""
    from db import add_bottleneck_seed
    norm = []
    for c in candidates or []:
        if isinstance(c, dict):
            norm.append((str(c.get("ticker", "")).strip().upper(), c.get("rationale", "")))
        else:
            norm.append((str(c).strip().upper(), ""))
    added = [t for t, why in norm if t and add_bottleneck_seed(t, why, source=source)]
    if not added:
        return []
    lst = ", ".join(added)
    _narrate("S", f"오늘 사슬 뒤에서 병목 후보를 물어왔습니다: {lst}. 워치리스트 편입은 사장님 결재로 올립니다. 🧾")
    try:
        from notifier import notify
        site = os.getenv("SITE_URL", "").rstrip("/")
        link = (site + "/owner/approvals") if site else "/owner/approvals"   # 결재함 통합
        notify(f"🔩 병목 시드 결재 {len(added)}건", f"{lst}\n승인/반려: {link}",
               priority="default", cooldown=0)
    except Exception as e:
        logger.warning(f"병목 결재 알림 실패: {e}")
    return added


_CURATION_SYS = (
    "너는 AI 인프라 공급망의 '초크포인트(병목)' 리서처다. 유명 대형주(엔비디아·브로드컴 같은 '참치')가 "
    "아니라, 그게 없으면 사슬 전체가 안 돌아가는 무명·상류 병목 기업('깻잎')을 찾는다. "
    "관심 영역: 광통신·실리콘포토닉스, 인터커넥트·리타이머, InP/화합물 반도체 기판, 첨단 패키징(CoWoS·HBM 소부장), "
    "데이터센터 전력·열관리, 특수 소재. "
    "반드시 웹서치로 '지금'의 신호를 조사해라 — 최근 실적·capex·공급 부족·수주·특허/논문 등. "
    "미국 상장 종목(티커)만. 아래 '제외 목록'에 있는 티커는 절대 내지 마라. "
    "이건 투자 권유가 아니라 '공급망에서 어디가 병목인지' 후보 조사다. "
    "출력은 오직 JSON 배열만: [{\"ticker\":\"...\",\"rationale\":\"왜 병목인지 한글 한 줄(근거)\"}]. "
    "설명·머리말·코드펜스 없이 JSON 배열 하나만 출력."
)


def _parse_candidates_json(text: str) -> list:
    """LLM 출력에서 JSON 배열만 추출 → [{'ticker','rationale'}]. 실패 시 빈 리스트."""
    import json
    s = (text or "").strip()
    if "[" in s and "]" in s:
        s = s[s.index("["): s.rindex("]") + 1]
    try:
        arr = json.loads(s)
    except Exception:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        if isinstance(it, dict) and it.get("ticker"):
            out.append({"ticker": str(it["ticker"]).strip().upper(),
                        "rationale": str(it.get("rationale", "")).strip()})
    return out


def _research_bottleneck_candidates(exclude, limit):
    """Claude 웹서치로 초크포인트 후보 조사(하루 1콜, 구독 토큰). 제외목록 준수. 반환 [{ticker,rationale}]."""
    from agents import _call_claude_cli
    ex = ", ".join(sorted(exclude)) or "(없음)"
    user = (f"제외 목록(이미 다룬 티커, 절대 내지 말 것): {ex}\n\n"
            f"위 영역에서 지금 주목할 만한 초크포인트 후보를 웹서치로 조사해 최대 {limit}개만 JSON 배열로 내라.")
    out = _call_claude_cli(_CURATION_SYS, user, timeout=240, model="sonnet",
                           allowed_tools=["WebSearch"])
    cands = _parse_candidates_json(out)
    seen, uniq = set(exclude), []
    for c in cands:
        t = c["ticker"]
        if t and t not in seen and t.isalpha() and len(t) <= 5:   # 미국 보통주 티커 형태만
            seen.add(t)
            uniq.append(c)
    return uniq[:limit]


def run_bottleneck_curation(limit=None):
    """②d 6시 큐레이션 — Claude 웹서치로 초크포인트 후보 조사 → pending 결재 큐(승인은 사람).
    하루 1콜·구독 토큰. BOTTLENECK_CURATION_ENABLED=False면 no-op. 토큰 소진 시 조용히 skip(다음날 재개)."""
    if not BOTTLENECK_CURATION_ENABLED:
        return {"added": [], "skipped": "disabled"}
    limit = limit or BOTTLENECK_CURATION_QUOTA
    from db import get_bottleneck_seeds, get_bottleneck_seed_rows
    def _utc_ts_is_today_kst(ts):
        try:
            return (datetime.fromisoformat(ts) + timedelta(hours=9)).strftime("%Y-%m-%d") == _today_kst()
        except Exception:
            return False
    if any(_utc_ts_is_today_kst(r.get("created_at") or "") and r.get("source") == "agent"
           for r in get_bottleneck_seed_rows()):                 # 하루 1콜(워크데이 재출근 중복 방지, KST 기준)
        return {"added": [], "skipped": "already_today"}
    _bottleneck_seed()                                           # 빈 DB 첫 실행 대비: 하드코딩 시드 approved 백필 '먼저'
    #   (안 하면 큐레이션 pending이 테이블을 채워 백필이 영구 skip → 기존 S 워치리스트가 사라짐)
    exclude = set(get_bottleneck_seeds(None))                     # 이미 pending/approved/rejected인 건 재출현 금지
    try:
        cands = _research_bottleneck_candidates(exclude, limit)
    except Exception as e:
        from agents import ClaudeTokenExhausted
        if isinstance(e, ClaudeTokenExhausted):
            logger.warning("병목 큐레이션 토큰 소진 — 다음 스케줄에 재개")
            return {"added": [], "skipped": "token_exhausted"}
        logger.error(f"병목 큐레이션 실패: {e}", exc_info=True)
        return {"added": [], "error": str(e)}
    added = submit_bottleneck_candidates(cands, source="agent")   # pending + S 결재 올림 + 오너 ntfy
    return {"added": added, "researched": [c["ticker"] for c in cands]}


# ── 발굴 3인(P/W/S) 분석 (네트워크 + LLM) ─────────────────
import re                                          # noqa: E402

NEW_DESK_ORDER = ["P", "W", "S"]                   # (레거시) 4봇 경쟁 발굴 순서
# 통일 데스크 결정자 (설계 docs/AIFUND_PIVOT.md)
LARGECAP_BOTS = ["P", "W", "H"]                    # 대형주 = 성장주·가치·실적왕(H) → 관심종목 캐시
DISCOVERY_BOTS = ["P", "W", "S"]                   # 발굴주 = 성장주·가치·병목 → 즉시매수
# 데스크별 사이징 — 대형주 집중(고확신), 발굴주 분산(리스크). 각 시드 DESK_SEED(5,000만) 기준.
DESK_SIZING = {"대형주": {"weight": 0.10, "max": 10},   # 종목당 10%(500만) · 최대 10
               "발굴주": {"weight": 0.05, "max": 20}}   # 종목당 5%(250만) · 최대 20
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


def _persona_hash(bot) -> str:
    """페르소나 프롬프트 버전 추적용 짧은 해시 — 판단 재현성(어느 버전이 판단했나)."""
    try:
        import hashlib
        from prompts import AGENT_PROFILES
        return hashlib.sha1((AGENT_PROFILES[bot].get("system") or "").encode()).hexdigest()[:10]
    except Exception:
        return ""


def log_decision(source, bot, code, name, verdict, reasoning, packet="", model="sonnet"):
    """봇 판단을 decision_log에 기록(P0 성과 계측·재현성). 어떤 실패도 매매 로직에 전파 안 함."""
    try:
        from db import add_decision_log
        add_decision_log(_today_kst(), source, bot, code, name, verdict, reasoning,
                         packet, _persona_hash(bot), model)
    except Exception:
        pass


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
    log_decision("분석", bot, code, name, verdict, reasoning, packet=packet)   # P0: 채점 가능하게 기록
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
    """Q 진입 신호(최신 바): M(추세돌파, 시장필터) 우선 → B(평균회귀 복귀확인).
    반환: 'M' | 'B' | None (전략 태그)."""
    import backtest as bt
    from trading_strategies import meanrev_entry
    if bt.trend_entry(closes, in_uptrend):
        return "M"
    if meanrev_entry(closes):
        return "B"
    return None


def q_exit_signal(closes, strat):
    """Q 청산 신호(최신 바): 진입 전략(M/B)의 청산 규칙."""
    import backtest as bt
    from trading_strategies import meanrev_exit
    return bt.trend_exit(closes) if strat == "M" else meanrev_exit(closes)


def q_veto(closes, in_uptrend):
    """Q veto(보조지표) — 대형주에서 '지금 사면 안 되는 자리'만 막는다. 하드 진입게이트 아님:
    veto 아니면 P/W/H 확신을 따라 매수('장기 메인 + 퀀트 보조지표' 철학).
    veto 조건(명백한 위험만): ①약세장(SPY<200MA) ②종목 200일선 아래(장기추세 이탈=낙하칼 회피).
    과매수(볼밴 상단)는 veto 안 함 — 건강한 상승추세도 밴드 상단을 타서 오판 위험. 장기픽은 타이밍보다 종목이 중요.
    반환 (veto: bool, 이유: str)."""
    import backtest as bt
    if not in_uptrend:
        return True, "약세장(시장 200MA 아래)"
    if len(closes) >= 200:
        s200 = bt._sma(closes, 200)
        if s200 and closes[-1] < s200:
            return True, "200일선 아래(장기추세 이탈)"
    return False, ""


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
        strat = "M" if "추세돌파" in (pos.get("reasoning") or "") else "B"
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
        label = "추세돌파" if tag == "M" else "평균회귀"
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
    "P": ["출근. 오늘도 생활 속에서 크게 자랄 놈 찾아봅니다.",
          "왔어요. PEG 싼 성장주 뭐 없나 보죠.",
          "출근! 스토리 살아있는 놈으로 골라봅니다."],
    "W": ["출근했습니다. 좋은 회사를 적정가에.",
          "도착. 능력범위 안, 해자 있는 것만 봅니다.",
          "출근이요. 10년 들고 갈 만한지부터 봅니다."],
    "H": ["출근. 실적으로 증명된 놈만 봅니다.",
          "왔습니다. 성장률·마진부터 확인하죠.",
          "출근! 숫자 안 나오면 안 삽니다."],
    "S": ["출근 — 오늘의 곡괭이 찾으러 갑니다.",
          "왔습니다. 남들 완제품 볼 때 저는 사슬 뒤를 봐요.",
          "출근! 공급망 초크포인트부터 직접 뒤져봅니다."],
}
_CLOCK_OUT = {
    "A": ["오늘 리서치 끝 — 퇴근합니다. 다들 수고했어요 🫡",
          "정리 끝났습니다. 퇴근할게요, 내일 또 좋은 종목으로.",
          "계좌 성적은 우측에서 보세요. 퇴근합니다."],
    "P": ["퇴근. 오늘 고른 놈들 잘 자라라~",
          "이만 퇴근합니다. 인내가 수익이죠.",
          "먼저 퇴근할게요. 좋은 회사는 시간이 증명해요."],
    "W": ["퇴근. 서두르지 않습니다, 늘 그렇듯.",
          "관점 정리 끝, 퇴근합니다.",
          "이만 퇴근이요. 가격이 오면 그때 더 담죠."],
    "H": ["퇴근. 결국 실적이 답이죠.",
          "숫자 확인 끝, 퇴근합니다.",
          "먼저 퇴근이요. 다음 실적 시즌도 지켜보죠."],
    "S": ["퇴근 — 곡괭이는 조용히 값을 합니다.",
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
# 대형주 핸드오프 — P/W/H가 선정 후 타이밍 담당(Q)에게 넘김. 발신자 라벨이 정체를 보이므로 본문엔 레터 미기재.
_HANDOFF_LINES = ["{name} 괜찮아 보여요. 진입 타이밍 봐주세요 🙏",
                  "{name} 관심종목으로 올립니다 — 언제 들어갈지 판단 부탁해요.",
                  "{name} 펀더는 좋네요. 타이밍은 담당에게 맡길게요."]


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
               "담백하게 2~3문장 한국어로. 제목·머리말·불릿 없이 바로 소개 문장만. 투자판단·주가 얘기 금지.")
        prompt = f"[회사] {name}\n[영문 개요]\n{business_summary}\n\n바로 소개 문장(제목 없이):"
        return _clean_md((_call_claude_cli(sys, prompt, timeout=30, model="haiku") or "").strip())
    except Exception as e:
        logger.warning(f"한글 요약 실패 {name}: {e}")
        return ""


def _business_brief(name, code, business_summary):
    """A의 '사업 이해 게이트' — 실제로 뭘 만들어 어떻게 버는지 소개 + 명확/불명확 판정(한 콜).
    반환 (소개_한글, clear). clear=False면 발굴주 매수 차단(P·W·S가 홍보 문구만 보고 사는 것 방지).
    사업 개요 자체가 없거나 호출 실패면 보수적으로 불명확(False).
    ⚠️ LLM 호출(haiku) — NEW_DESK 발굴 사이클 안에서만."""
    if not business_summary:
        return "", False
    try:
        from agents import _call_claude_cli
        sys = ("너는 기업을 처음 보는 사람에게 소개하는 애널리스트다. 평가·전망·추천 없이 "
               "이 회사가 ①뭘 팔아서 어떻게 버는지 + ②대표 제품·고객·업계 위상을 담백하게 2~3문장 한국어로. "
               "제목·머리말·불릿 없이 소개 문장만. "
               "그다음 판정: 제공된 정보로 '이 회사가 실제로 뭘 만들어 어떻게 매출을 내는지' 구체적으로 설명 가능하면 명확, "
               "홍보성 버즈워드뿐이거나 사업 실체가 모호하면 불명확. "
               "맨 마지막 줄에 반드시 `[명확도] 명확` 또는 `[명확도] 불명확`만.")
        prompt = f"[회사] {name} ({code})\n[영문 개요]\n{business_summary}\n\n소개 문장(제목 없이) 후 마지막 줄에 [명확도]:"
        out = (_call_claude_cli(sys, prompt, timeout=30, model="haiku") or "").strip()
        m = re.search(r"\[명확도\]\s*(불명확|명확)", out)
        clear = not (m and m.group(1) == "불명확")            # 명시적 '불명확'만 차단(소개는 썼는데 태그 누락 시엔 통과)
        summary = _clean_md(re.sub(r"\[명확도\].*$", "", out, flags=re.S).strip())
        return summary, clear
    except Exception as e:
        logger.warning(f"사업 이해 판정 실패 {name}: {e}")
        return "", False


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


def _first_sentence(text) -> str:
    """소개 첫 문장(A 채팅용 '뭐하는 회사') — 첫 마침표까지."""
    text = (text or "").strip().replace("\n", " ")
    if not text:
        return ""
    m = re.search(r"[.!?]\s", text) or re.search(r"[.!?]$", text)
    return text[:m.end()].strip() if m else text[:90].strip()


def _stock_data_msg(name, code, brief, intro_desc="") -> str:
    """A가 종목 차례를 알리며 (발굴주면 한 줄 소개 +) 핵심 재무를 올리고 P/W/H에게 넘기는 멘트."""
    packet = (brief or ("", ""))[0] or ""
    keys = ("현재가", "PER", "PEG", "EPS", "ROE", "마진", "성장", "매출", "잉여현금")
    keep = [ln.strip() for ln in packet.splitlines() if any(k in ln for k in keys)][:6]
    body = " · ".join(keep) if keep else "핵심 재무 데이터가 잘 안 잡히네요"
    intro = random.choice(_STOCK_INTRO).format(tk=_tk(code, name))
    desc = f" — {intro_desc}" if intro_desc else ""              # 발굴주: 뭐하는 회사 한 줄
    return f"{intro}{desc}\n📊  {body}\n{random.choice(_STOCK_HANDOFF)}"


def source_largecap(market="US", quota=None):
    """대형주 소싱 — 고정 유니버스에서 오늘 볼 종목(캐시된 건 제외, 매일 통일성). 반환 {'codes','names'}."""
    quota = quota or DAILY_QUOTA
    u = _largecap_universe()
    if market != "US" or not u:
        return {"codes": [], "names": {}}
    cached = set(get_cached_codes())
    codes = [c for c in u if c not in cached][:quota] or u[:quota]   # 다 봤으면 다시 처음부터
    return {"codes": codes, "names": {c: stock_name(c) for c in codes}}


def _store_report(today, code, name, brief, desk_tag, summary=None):
    """A 리포트 저장(재무 + 발굴주는 '처음 보는 사람용' 소개). 실패해도 무시.
    대형주는 다 아는 메가캡이라 사업 소개 생략(재무만) — 토큰도 아낌.
    summary 주면 재계산 안 함(루프에서 A 채팅용으로 한 번 만든 걸 재사용)."""
    from db import record_fund_report
    packet, biz = brief or ("", "")
    if summary is None:
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


# ── Q 지수 스윙 데스크 (봇 개성: 자기 전략을 지수로 포워드 테스트, 소액·자동) ──
# 1배 인버스만(SH·PSQ) — 레버리지 인버스(SQQQ 등)는 일일 리밸런싱 decay로 스윙에 부적합.
Q_INDEX_ENABLED = os.getenv("Q_INDEX_ENABLED", "").lower() in ("1", "true", "yes")
Q_INDEX_PAIRS = {"QQQ": "PSQ", "SPY": "SH"}     # 지수 → 1배 인버스
Q_INDEX_SLOT = 5_000_000                        # 슬롯당 500만 (2슬롯 = 시드 1,000만)


def q_index_signal(closes):
    """지수 스윙 진입 신호(일봉): 'long'(200MA 위 + 20일 신고가) / 'inverse'(200MA 아래 + 20일 신저가) / None.
    Q의 추세 철학을 지수에 대칭 적용 — 상승추세 돌파는 롱, 하락추세 이탈은 인버스."""
    if len(closes) < 200:
        return None
    import backtest as bt
    c, s200 = closes[-1], bt._sma(closes, 200)
    if c > s200 and c >= max(closes[-20:]):
        return "long"
    if c < s200 and c <= min(closes[-20:]):
        return "inverse"
    return None


def q_index_exit(closes, side):
    """지수 스윙 청산(일봉): 롱=50MA 이탈 / 인버스=50MA 회복(추세 반전 미러)."""
    import backtest as bt
    s50 = bt._sma(closes, 50)
    if s50 is None:
        return False
    return closes[-1] < s50 if side == "long" else closes[-1] > s50


def run_q_index_desk():
    """Q 지수 계좌(1,000만) 일일 점검 — QQQ/SPY 스윙 + 1배 인버스. 자동매매(소액·룰·결재 없음).
    Q_INDEX_ENABLED=False면 no-op. 반환 {'bought','sold'}."""
    if not (NEW_DESK_ENABLED and Q_INDEX_ENABLED):
        return {"bought": [], "sold": []}
    import backtest as bt
    from db import (ensure_desk_accounts, get_open_positions, buy_shared_position,
                    sell_shared_position)
    ensure_desk_accounts()
    tickers = list(Q_INDEX_PAIRS) + list(Q_INDEX_PAIRS.values())
    data = bt._fetch(tickers, period="2y")
    held = get_open_positions(account="Q지수")
    held_by_code = {(p.get("code") or p["symbol"]): p for p in held}
    bought, sold, notes = [], [], []
    for idx, inv in Q_INDEX_PAIRS.items():
        o = data.get(idx)
        if not o or len(o.get("close") or []) < 200:
            continue
        closes = o["close"]
        # ① 보유분 청산 판정 — 롱(idx)·인버스(inv) 각각 지수 기준 미러 룰
        for code, side in ((idx, "long"), (inv, "inverse")):
            p = held_by_code.get(code)
            if p and q_index_exit(closes, side):
                px_o = data.get(code)
                px = (px_o["close"][-1] if px_o and px_o.get("close") else None)
                if px and not sell_shared_position(p["id"], px, exit_reasoning=f"Q지수 {side} 청산(50MA 반전)")[1]:
                    sold.append(code)
                    held_by_code.pop(code, None)
                    notes.append(f"{code} 청산(추세 반전)")
        # ② 신규 진입 — 같은 지수의 롱·인버스 동시 보유 금지
        sig = q_index_signal(closes)
        if not sig:
            continue
        code = idx if sig == "long" else inv
        other = inv if sig == "long" else idx
        if code in held_by_code or other in held_by_code:
            continue
        px_o = data.get(code)
        px = (px_o["close"][-1] if px_o and px_o.get("close") else None)
        if not px:
            continue
        label = "추세 돌파 롱" if sig == "long" else "하락추세 인버스"
        _, err = buy_shared_position(code, code, px, Q_INDEX_SLOT,
                                     f"Q지수 {label} ({idx} 기준)", "US", account="Q지수")
        if not err:
            bought.append(code)
            held_by_code[code] = {"code": code}
            notes.append(f"{code} 진입({label})")
            log_decision("Q지수", "Q", code, code, "진입", label, model="rule")   # P0
    if notes:
        _narrate("Q", "📊 지수 계좌 점검 — " + " · ".join(notes) + ". 제 전략이 지수에서도 통하는지 직접 증명해보겠습니다.")
    return {"bought": bought, "sold": sold}


def _approver_of(position):
    """발굴주 포지션 reasoning에서 첫 매수 찬성봇 추출. 못 찾으면 P."""
    r = position.get("reasoning") or ""
    if "(" in r:
        first = r.split("(")[-1].rstrip(")").split(",")[0].strip()
        if first in DISCOVERY_BOTS:
            return first
    return "P"


# ── 매수 결재 (봇 판단 → pending_buy → 오너 승인 시 체결) ──
def _report_summary(code) -> str:
    """해당 종목의 최신 A 사업 요약(있으면) — 대형주 결재에 '종목 설명'으로 첨부."""
    from db import get_fund_reports
    for r in get_fund_reports(200):
        if r.get("code") == code and (r.get("summary") or "").strip():
            return r["summary"].strip()
    return ""


_ROLE_KO = {"P": "성장주", "W": "가치", "H": "실적", "S": "병목", "Q": "타이밍", "M": "거시", "R": "리스크"}


def _clean_reason(rz: str) -> str:
    """봇 응답에서 [결정] 표기 줄만 걷어내고 근거 본문은 살린다(상세 보고용)."""
    txt = re.sub(r"\[결정\][^\n]*", "", rz or "").strip()
    return _clean_md(txt).strip() if txt else ""


def _compose_buy_report(approvers, reasonings) -> str:
    """승인 봇들의 판단 근거를 역할별로 정중·상세하게 묶는다(상사 보고체)."""
    lines = []
    for b in approvers or []:
        r = _clean_reason((reasonings or {}).get(b, ""))
        if r:
            lines.append(f"· {_ROLE_KO.get(b, b)} 담당 의견 — {r}")
    return "\n".join(lines)


def _submit_buy_approval(desk, account, ticker, code, price, amount, approvers, market,
                         stock_desc="", reason="", q_comment=None, speaker=None) -> bool:
    """봇 매수 판단을 즉시 체결 대신 결재 큐에 상신 + 정중한 '결재 건의' 내레이션 + 오너 ntfy.
    같은 종목·데스크가 이미 대기중이면 조용히 skip(사이클마다 중복 상신 방지). 반환: 상신했으면 True."""
    from db import add_pending_buy
    pid = add_pending_buy(ticker, code, desk, account, market, amount, price,
                          ",".join(approvers) if approvers else "", stock_desc, reason, q_comment)
    if not pid:
        return False
    px = f"${price:,.2f}" if market == "US" else f"₩{price:,.0f}"
    _narrate(speaker or (approvers[0] if approvers else "A"),
             f"📋 사장님, {_tk(code, ticker)} 매수를 건의드립니다. 판단가 {px} 기준으로 상세 사유를 "
             f"결재함에 올려두었습니다 — 검토 후 승인 부탁드리겠습니다. 🙇")
    try:
        from notifier import notify
        site = os.getenv("SITE_URL", "").rstrip("/")
        link = (site + "/owner/approvals") if site else "/owner/approvals"
        head = (reason or "").splitlines()[0][:80] if reason else stock_desc[:80]
        notify(f"🧾 매수 결재 건의 — {ticker} ({desk})",
               f"{ticker} @ {px} · 투자금 ₩{amount:,.0f}\n{head}\n검토·승인: {link}",
               priority="default", cooldown=0)
    except Exception as e:
        logger.warning(f"매수 결재 알림 실패: {e}")
    return True


# ── 관찰 단계 (신중한 매수: 매수 판단 → 며칠 관찰 → 확신 시 결재) ──
_OBS_RE = re.compile(r"\[관찰\]\s*(확신|유지|철회)")


def _register_observation(desk, name, code, price, approvers, reasonings, stock_desc, market):
    """매수 판단 종목을 '관찰'로 등록 — 바로 안 사고 지켜보기 시작. 중복 등록은 조용히 skip."""
    from db import add_observation
    thesis = _compose_buy_report(approvers, reasonings)
    oid = add_observation(code, name, desk, market, ",".join(approvers), thesis, stock_desc, price)
    if oid:
        _narrate(approvers[0],
                 f"👀 {_tk(code, name)} — 사고 싶은 마음은 있는데, 서두르지 않겠습니다. "
                 f"현재가 기준으로 며칠 지켜보면서 논지가 유지되는지 확인한 뒤 결재 올릴게요.")
    return oid


def _observation_prompt(obs, brief_packet, cur_price) -> str:
    """재관찰 프롬프트 — 자기 과거 논지 + 당시 가격 vs 현재 데이터로 확신 재평가."""
    days = (obs.get("review_count") or 0) + 1
    return (f"[관찰 재점검 {days}회차] 너는 앞서 {obs['name']} ({obs['code']})를 매수 후보로 보고 "
            f"이런 논지를 남겼다 (당시 가격 {obs.get('price_at') or '?'}):\n{obs.get('thesis') or '(논지 기록 없음)'}\n\n"
            f"현재 가격: {cur_price or '?'}\n현재 데이터:\n{brief_packet or '(데이터 부족 — 보수적으로)'}\n\n"
            "그때의 논지가 지금도 유지·강화되고 있나? 서두를 필요 없다 — 확신이 없으면 더 지켜보고, "
            "논지가 훼손됐으면 솔직하게 철회해라. 반드시 아래 형식으로:\n"
            "[관찰] 확신 | 이유: ...  (이제 사도 된다고 확신)\n"
            "[관찰] 유지 | 이유: ...  (계속 지켜본다)\n"
            "[관찰] 철회 | 이유: ...  (논지 훼손 — 관찰 중단)")


def run_observation_review(market="US"):
    """관찰 중 종목 재점검(발굴 사이클마다) — 승인봇별 재평가 → 전원 철회=drop /
    확신 도달(최소 OBSERVATION_MIN_REVIEWS회차)=결재 상신. 반환 {'reviewed','convinced','dropped'}."""
    if not (NEW_DESK_ENABLED and OBSERVATION_REQUIRED):
        return {"reviewed": [], "convinced": [], "dropped": []}
    from db import get_observations, record_observation_review
    from fetchers import fetch_stock_price
    from prompts import AGENT_PROFILES
    from agents import call_agent
    obs_list = get_observations("observing", desk="발굴주")
    reviewed, convinced, dropped = [], [], []
    today = _today_kst()
    for obs in obs_list:
        code, name = obs["code"], obs.get("name") or obs["code"]
        brief = build_research_brief(code, name, code, market)          # 현재 데이터로 재평가
        packet = (brief or ("", ""))[0]
        price = fetch_stock_price(code if market == "US" else f"{code}.KS")
        bots = [b for b in (obs.get("bots") or "").split(",") if b]
        verdicts, notes = {}, {}
        for bot in bots:
            try:
                out = call_agent(bot, AGENT_PROFILES[bot]["system"],
                                 _observation_prompt(obs, packet, price), model="sonnet", trim=False)
            except Exception as e:
                logger.warning(f"관찰 재점검 실패 {code}@{bot}: {e}")
                out = "[관찰] 유지 | 이유: 점검 실패 — 다음에 다시"
            m = _OBS_RE.search(out or "")
            verdicts[bot] = m.group(1) if m else "유지"
            log_decision("관찰", bot, code, name, verdicts[bot], out or "", packet=packet)   # P0
            notes[bot] = _clean_reason(re.sub(r"\[관찰\][^\n]*", "", out or "")) or \
                _verdict_reason((out or "").replace("[관찰]", "[결정] 관망 |"))
            _narrate(bot, f"👀 {_tk(code, name)} 관찰 {obs['review_count'] + 1}회차 — " + (out or "").strip()[:300],
                     model="sonnet")
        review = {"date": today, "price": price,
                  "verdicts": verdicts, "notes": notes}
        n_now = (obs.get("review_count") or 0) + 1
        keep = [b for b in bots if verdicts.get(b) != "철회"]
        conv = [b for b in bots if verdicts.get(b) == "확신"]
        if not keep:                                                    # 전원 철회
            record_observation_review(obs["id"], review, status="dropped")
            dropped.append(code)
            _narrate(bots[0], f"{_tk(code, name)} — 지켜본 결과 논지가 훼손됐습니다. 관찰 중단합니다. 무리해서 살 이유가 없죠.")
        elif conv and n_now >= OBSERVATION_MIN_REVIEWS:                 # 확신 + 충분히 지켜봄 → 결재
            record_observation_review(obs["id"], review, status="convinced")
            convinced.append(code)
            story = _observation_story(obs, review, n_now)
            _submit_buy_approval("발굴주", "발굴주", name, code, price or obs.get("price_at"),
                                 _desk_amount("발굴주"), conv, market,
                                 stock_desc=obs.get("stock_desc") or "", reason=story, speaker=conv[0])
        else:                                                           # 계속 관찰
            record_observation_review(obs["id"], review)
        reviewed.append(code)
    return {"reviewed": reviewed, "convinced": convinced, "dropped": dropped}


def _observation_story(obs, last_review, n_reviews) -> str:
    """결재용 관찰 서사 — 처음 논지부터 확신까지의 흐름을 정중하게 요약."""
    import json
    lines = [f"· 최초 검토 — 당시 가격 {obs.get('price_at') or '?'}에서 매수 후보로 선정했습니다.",
             f"{obs.get('thesis') or ''}".strip(),
             f"· 이후 {n_reviews}회에 걸쳐 재점검하며 논지를 확인했습니다."]
    try:
        hist = json.loads(obs.get("reviews") or "[]") + [last_review]
        for i, r in enumerate(hist, 1):
            for bot, note in (r.get("notes") or {}).items():
                if note:
                    lines.append(f"  - {i}회차({r.get('date', '')}) {_ROLE_KO.get(bot, bot)} — {note[:150]}")
    except Exception:
        pass
    lines.append(f"· 현재가 {last_review.get('price') or '?'} 기준으로도 논지가 유효하다고 판단해 매수를 건의드립니다.")
    return "\n".join(x for x in lines if x)


def _narrate_macro_context():
    """M: 오늘 거시 브리핑이 있으면 요지 한 줄을 발굴 피드에 공유(브리핑 재사용 — 추가 LLM 호출 없음)."""
    try:
        from db import get_latest_macro_briefing
        b = get_latest_macro_briefing()
        if b and b.get("date") == _today_kst() and (b.get("content") or "").strip():
            head = b["content"].strip().split("\n")[0][:220]
            _narrate("M", f"오늘 거시 환경 참고입니다 — {head}", model="haiku")
    except Exception as e:
        logger.debug(f"M 발굴 코멘트 skip: {e}")


# ── R 리스크 오피서 (비투표: 포트폴리오 '전체' 리스크 일일 점검) ──
RISK_OFFICER_ENABLED = os.getenv("RISK_OFFICER_ENABLED", "").lower() in ("1", "true", "yes")


def _portfolio_digest() -> str:
    """펀드 전 계좌 보유·현금 요약(순수) — R 점검 인풋. 집중도 상위 노출 포함."""
    from db import DESK_ACCOUNTS, ACCT_SEED, get_shared_portfolio, get_open_positions
    lines, all_pos, total_inv, total_cash = [], [], 0.0, 0.0
    for acct in DESK_ACCOUNTS:
        pf = get_shared_portfolio(acct) or {}
        pos = get_open_positions(account=acct)
        cash = pf.get("balance") or 0
        inv = sum((p.get("amount") or 0) for p in pos)
        total_cash += cash
        total_inv += inv
        all_pos += [(p["symbol"], p.get("amount") or 0) for p in pos]
        lines.append(f"[{acct}] 현금 {cash / 1e6:,.0f}백만 · 투자 {inv / 1e6:,.0f}백만 · "
                     + (", ".join(f"{s}({a / 1e6:,.0f}백만)" for s, a in
                                  [(p['symbol'], p.get('amount') or 0) for p in pos]) or "보유 없음"))
    total = total_cash + total_inv
    if total and all_pos:
        top = sorted(all_pos, key=lambda x: -x[1])[:3]
        lines.append("집중도 상위: " + ", ".join(f"{s} {a / total * 100:.0f}%" for s, a in top)
                     + f" · 현금 비중 {total_cash / total * 100:.0f}%")
    return "\n".join(lines)


def _fetch_closes_dated(codes, period="3mo"):
    """{code: (dates[], closes[])} — 주간 채점용 날짜 정렬 종가(yfinance 1콜). 실패 종목은 빠짐."""
    out = {}
    try:
        import yfinance as yf
        df = yf.download(list(codes), period=period, interval="1d", auto_adjust=True,
                         group_by="ticker", progress=False, threads=True)
        for c in codes:
            try:
                sub = df[c] if len(codes) > 1 else df
                closes = sub["Close"].dropna()
                out[c] = ([d.strftime("%Y-%m-%d") for d in closes.index], [float(x) for x in closes.values])
            except Exception:
                continue
    except Exception as e:
        logger.warning(f"주간 채점 시세 실패: {e}")
    return out


def _ret_after(dates, closes, decision_date, ndays=5):
    """판단일 이후 첫 종가 → n거래일 뒤 수익률. 데이터 부족 시 None."""
    idx = next((i for i, d in enumerate(dates) if d >= decision_date), None)
    if idx is None or idx + ndays >= len(closes) or not closes[idx]:
        return None
    return closes[idx + ndays] / closes[idx] - 1


def run_weekly_report():
    """P0 주간 성과 리포트(오너 전용·결정적 계산·LLM 0) — 봇별 판단 채점 + 결재 부가가치 + 데이터 헬스.
    저장(weekly_report) + 오너 ntfy. 반환 dict."""
    from db import (get_decision_logs, save_weekly_report, get_pending_buys,
                    get_fetch_health, get_observations)
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone(timedelta(hours=9)))
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    rows = [r for r in get_decision_logs(before_date=cutoff, source="분석")
            if r.get("verdict") in ("매수", "관망", "매도")]
    codes = sorted({r["code"] for r in rows})
    px = _fetch_closes_dated(codes) if codes else {}
    stats = {}                                                   # (bot, verdict) → [rets]
    for r in rows:
        d = px.get(r["code"])
        if not d:
            continue
        ret = _ret_after(d[0], d[1], r["date"])
        if ret is None:
            continue
        stats.setdefault((r["bot"], r["verdict"]), []).append(ret)
    lines = [f"📊 주간 성과 리포트 ({today})", "", "■ 봇별 판단 채점 (판단 7일 후 수익률, '분석' 판단만)"]
    if stats:
        for (bot, v), rets in sorted(stats.items()):
            avg = sum(rets) / len(rets)
            win = sum(1 for x in rets if x > 0) / len(rets)
            note = "적중↑" if (v == "매수" and avg > 0) or (v == "관망" and avg <= 0) else "재점검 필요"
            lines.append(f"  {_ROLE_KO.get(bot, bot)}({bot}) {v}: {len(rets)}건 · 평균 {avg*100:+.1f}% · 상승률 {win*100:.0f}% [{note}]")
    else:
        lines.append("  (채점 가능한 7일 경과 판단이 아직 없음 — 데이터 축적 중)")
    appr = [p for p in get_pending_buys(None) if p["status"] in ("approved", "rejected")
            and (p.get("decided_at") or "")[:10] <= cutoff]
    if appr:
        lines += ["", "■ 사장님 결재 부가가치 (판단가 대비 최신가)"]
        pcodes = sorted({p["code"] for p in appr})
        ppx = _fetch_closes_dated(pcodes)
        for st in ("approved", "rejected"):
            rets = []
            for p in appr:
                if p["status"] != st or p["code"] not in ppx or not p.get("decision_price"):
                    continue
                closes = ppx[p["code"]][1]
                if closes:
                    rets.append(closes[-1] / p["decision_price"] - 1)
            if rets:
                lbl = "승인(매수)" if st == "approved" else "거부(패스)"
                lines.append(f"  {lbl}: {len(rets)}건 · 평균 {sum(rets)/len(rets)*100:+.1f}%")
    obs_c = len(get_observations("convinced")); obs_d = len(get_observations("dropped"))
    lines += ["", f"■ 관찰 단계: 확신 전환 {obs_c} · 철회 {obs_d}"]
    fh = get_fetch_health(7)
    if fh:
        tot_ok = sum(r["ok"] for r in fh); tot_fail = sum(r["fail"] for r in fh)
        rate = tot_fail / max(tot_ok + tot_fail, 1) * 100
        lines += ["", f"■ 데이터 헬스(7일): 성공 {tot_ok} · 실패 {tot_fail} ({rate:.0f}%)"
                  + (" ⚠️ 실패율 높음 — 소스 점검 필요" if rate > 30 else "")]
    content = "\n".join(lines)
    save_weekly_report(today, content)
    try:
        from notifier import notify
        site = os.getenv("SITE_URL", "").rstrip("/")
        notify("📊 주간 성과 리포트", f"봇 판단 채점 도착 — {(site or '') + '/owner/approvals'}", cooldown=0)
    except Exception:
        pass
    return {"date": today, "graded": sum(len(v) for v in stats.values()), "chars": len(content)}


_risk_done_date = None                                           # 하루 1회 가드(재출근 중복 방지)


def run_risk_review():
    """R 리스크 오피서 일일 점검(비투표) — 펀드 전 계좌를 전천후 렌즈로 훑고 피드에 코멘트.
    RISK_OFFICER_ENABLED=False면 no-op. haiku 1콜, 실패 시 skip."""
    global _risk_done_date
    if not (NEW_DESK_ENABLED and RISK_OFFICER_ENABLED):
        return {"skipped": "disabled"}
    if _risk_done_date == _today_kst():
        return {"skipped": "already_today"}
    _risk_done_date = _today_kst()
    digest = _portfolio_digest()
    try:
        from agents import call_agent
        from prompts import AGENT_PROFILES
        prompt = (f"오늘({_today_kst()}) 우리 가상 펀드 현황:\n{digest}\n\n"
                  "포트폴리오 '전체' 관점에서 리스크 점검 코멘트를 3~4문장으로. "
                  "집중도·상관관계·시나리오·현금 완충을 렌즈로, 개별 종목 추천 없이.")
        out = (call_agent("R", AGENT_PROFILES["R"]["system"], prompt, timeout=90,
                          model="haiku", trim=False) or "").strip()
    except Exception as e:
        logger.warning(f"R 리스크 점검 실패: {e}")
        return {"error": str(e)}
    if out:
        _narrate("R", "🛡️ 오늘의 포트폴리오 리스크 점검 — " + _clean_md(out), model="haiku")
    return {"chars": len(out)}


# 오너 승인 시드 평가 프레임 — S가 '병목 여부'를 재심사(이중 게이트)하며 전부 관망하던 것 교정.
SEED_FRAME = ("[오너 승인 병목 워치리스트] 이 종목의 '병목 여부'는 오너가 이미 검토·승인했다. "
              "병목인지 재심사하지 말고 다음만 평가하라: ① 진입 가격 — 시총이 병목 강도와 TAM 대비 "
              "합리적인가(프리미엄이 '있다'는 이유가 아니라 '과한 정도'인지) ② 생존 리스크 — 희석·현금 "
              "소진·고객 집중 ③ 타이밍 — 지금 담을 자리인가. '이미 알려졌다/비싸다'는 말로 기계적으로 "
              "거르지 마라. 셋 다 감내 가능하면 매수, 하나가 치명적이면 그 이유로만 관망하라.")


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
    src = source_today(market, quota=WORKDAY_ROUND_QUOTA if WORKDAY_ENABLED else None)
    _narrate("A", src["briefing"])
    _narrate_macro_context()                                  # M: 오늘 브리핑 요지 한 줄(재사용 — 추가 토큰 0)
    s_src = source_bottleneck(market)
    if s_src["codes"]:                                        # S가 자체 소싱한 병목 종목 + 맥락(왜 병목인지)을 직접 알림
        _narrate("S", _s_sourcing_note(s_src["codes"], s_src["names"]))
    # 후보마다 '판단할 봇' 태깅 — A 발굴픽은 P/W/S 위원회, S 병목픽은 S 독자(P/W 재투표 안 받음).
    cands = [(c, src["names"].get(c, c), DISCOVERY_BOTS) for c in src["new"] + src["catalyst"]] \
        + [(c, s_src["names"].get(c, c), ["S"]) for c in s_src["codes"]]
    if not cands:
        _narrate("A", _line(_CLOCK_OUT, "A"))
        return {"buys": []}
    for bot in DISCOVERY_BOTS:
        _narrate(bot, _line(_CLOCK_IN, bot))
    today = _today_kst()
    buys = []
    for code, name, bots in cands:
        brief = build_research_brief(code, name, code, market)       # A가 데이터 준비
        if bots == ["S"] and brief:                                  # 시드 재프레임: 병목 여부는 오너가 이미 승인 —
            brief = (SEED_FRAME + "\n\n" + (brief[0] or ""), brief[1])   # S는 진입가·희석·타이밍만 평가
        biz_ko, clear = _business_brief(name, code, (brief or ("", ""))[1])  # A 사업 이해 판정(소개+명확도)
        _store_report(today, code, name, brief, "발굴주", summary=biz_ko)
        _narrate("A", _stock_data_msg(name, code, brief, intro_desc=_first_sentence(biz_ko)))  # A가 먼저 올림(짧은 소개+데이터)
        if not clear:                                                # 이해 게이트: 사업 불명확 → 판단봇 안 붙이고 매수 차단
            _narrate("A", f"{_tk(code, name)} — 이 회사가 실제로 뭘 해서 버는지 명확히 설명하기 어렵네요(홍보 문구 위주/정보 부족). 이해 못 하는 종목은 안 삽니다, 패스할게요. 🙅")
            continue
        verdicts, reasonings = {}, {}
        for bot in bots:                                             # A픽=P/W/S 위원회 / S픽=S 독자
            try:
                v, rz = analyze_stock(code, name, code, bot, market, brief=brief)
            except Exception as e:
                logger.warning(f"발굴 분석 실패 {code}@{bot}: {e}")
                v, rz = "관망", ""
            verdicts[bot], reasonings[bot] = v, rz
            _narrate(bot, verdict_message(rz, v, name), model="sonnet")
        approvers = [b for b in bots if verdicts.get(b) == "매수"]
        if not approvers or get_open_positions_by_symbol(name, account="발굴주"):
            continue
        if not _desk_can_open("발굴주", len(get_open_positions(account="발굴주"))):
            continue
        price = fetch_stock_price(code if market == "US" else f"{code}.KS")
        if not price:
            continue
        reason = _verdict_reason(reasonings.get(approvers[0], ""))    # 대표 승인봇의 매수 이유(자동매수 로그용)
        if WORKDAY_ENABLED:                                           # 딥스터디: 매수 확신을 스스로 반박해보고 살아남아야 진행
            survived, deep = _deep_study(approvers[0], code, name, reasonings.get(approvers[0], ""), brief)
            if not survived:
                continue                                              # 반박에 무너짐 → 이번엔 패스(공부 기록은 피드·로그에 남음)
            if deep:
                reasonings[approvers[0]] = (reasonings.get(approvers[0], "") + "\n[딥스터디] " + deep)
        if OBSERVATION_REQUIRED:                                      # 신중한 매수: 바로 안 사고 관찰 등록 → 재관찰로 확신 쌓기
            _register_observation("발굴주", name, code, price, approvers, reasonings, biz_ko, market)
            continue
        if BUY_APPROVAL_REQUIRED:                                     # 즉시 체결 대신 오너 결재로(사이클은 계속)
            report = _compose_buy_report(approvers, reasonings) or reason   # 승인 봇별 상세 근거(상사 보고체)
            _submit_buy_approval("발굴주", "발굴주", name, code, price, _desk_amount("발굴주"),
                                 approvers, market, stock_desc=biz_ko, reason=report, speaker=approvers[0])
            continue
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
def _merr_macro_note() -> str:
    """M(거시 자문)의 오늘 시장 국면 코멘트 — '지금' 지수 데이터를 매크로 렌즈로 읽음(haiku 1콜).
    ⚠️ 과거 시황이 아니라 현재 데이터 기준. 종목·매매 추천 아님. 실패/토큰소진 시 빈문자(스킵)."""
    from db import get_benchmark_snapshots
    snaps = get_benchmark_snapshots() or []
    ctx = []
    if len(snaps) >= 2:
        prev, cur = snaps[-2], snaps[-1]
        for key, lbl in (("spy", "S&P500"), ("qqq", "나스닥100"), ("kospi", "코스피")):
            p, c = prev.get(key), cur.get(key)
            if p and c:
                ctx.append(f"{lbl} {c:.0f}({(c / p - 1) * 100:+.1f}%)")
    ctx_str = " · ".join(ctx) if ctx else "지수 데이터 부족(판단은 보수적으로)"
    try:
        from agents import call_agent
        from prompts import AGENT_PROFILES
        prompt = (f"오늘({_today_kst()}) 현재 시장 데이터(전일 대비): {ctx_str}.\n"
                  "이 '지금' 데이터 기준으로 시장 국면을 거시적으로 2~3문장 읽어줘. "
                  "과거 특정 시황을 현재처럼 말하지 말 것. 종목·매매 추천 금지.")
        return (call_agent("M", AGENT_PROFILES["M"]["system"], prompt, timeout=60, model="haiku") or "").strip()
    except Exception as e:
        logger.warning(f"M 매크로 노트 실패: {e}")
        return ""


def _fetch_new_blog_posts() -> int:
    """블로그 새 글 RSS 크롤 → blog_posts 저장. 실패해도 무시. 반환: 새 글 수(대략)."""
    n = 0
    try:
        from blog_fetcher import get_blog_ids, fetch_blog_rss
        for bid in get_blog_ids():
            try:
                got = fetch_blog_rss(bid)
                n += len(got) if got else 0
            except Exception as e:
                logger.warning(f"블로그 RSS 실패 {bid}: {e}")
    except Exception as e:
        logger.warning(f"블로그 fetch 준비 실패: {e}")
    return n


def run_macro_briefing():
    """매일 아침 M 거시 브리핑 — 블로그 새 글 크롤 → 최근 글 + 현재 지수 → M이 오너 보고 작성 → 저장.
    /owner/approvals 상단에 표시. MACRO_BRIEFING_ENABLED=False면 no-op. 실패/토큰소진 시 skip."""
    if not MACRO_BRIEFING_ENABLED:
        return {"skipped": "disabled"}
    from db import (get_recent_blog_posts, save_macro_briefing, get_benchmark_snapshots,
                    get_latest_macro_briefing)
    _b = get_latest_macro_briefing()
    if _b and _b.get("date") == _today_kst():                 # 하루 1회(워크데이 재출근 시 중복 생성 방지)
        return {"skipped": "already_today"}
    _fetch_new_blog_posts()
    since = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=14)).strftime("%Y-%m-%d")
    posts = get_recent_blog_posts(since, limit=8)
    blog_txt = "\n\n".join(f"[{p.get('post_date', '')}] {p.get('title', '')}\n{(p.get('content') or '')[:800]}"
                           for p in posts) or "(최근 블로그 글 없음)"
    snaps = get_benchmark_snapshots() or []
    idx = []
    if len(snaps) >= 2:
        prev, cur = snaps[-2], snaps[-1]
        for k, lbl in (("spy", "S&P500"), ("qqq", "나스닥100"), ("kospi", "코스피")):
            p, c = prev.get(k), cur.get(k)
            if p and c:
                idx.append(f"{lbl} {c:.0f}({(c / p - 1) * 100:+.1f}%)")
    idx_txt = " · ".join(idx) or "지수 데이터 부족(보수적으로)"
    today = _today_kst()
    try:
        from agents import call_agent
        from prompts import AGENT_PROFILES
        user = (f"오늘({today}) 현재 지수(전일 대비): {idx_txt}\n\n"
                f"[최근 시장 블로그 글 발췌]\n{blog_txt}\n\n"
                "위 '현재 지수'와 '최근 블로그'를 근거로 사장님께 올릴 오늘의 거시 시장 브리핑을 써줘. "
                "① 지금 시장 국면 한 문단 ② 블로그에서 주목할 매크로 포인트 2~3개(불릿) "
                "③ 오늘 매수 결재 판단 시 참고할 점 한 줄. 정중한 보고체(존댓말). "
                "반드시 '지금' 기준 — 과거 시황을 현재처럼 말하지 말 것. 종목 추천·매수 권유 아님.")
        content = (call_agent("M", AGENT_PROFILES["M"]["system"], user, timeout=120,
                              model="haiku", trim=False) or "").strip()
    except Exception as e:
        logger.error(f"M 브리핑 생성 실패: {e}", exc_info=True)
        return {"error": str(e)}
    if content:
        save_macro_briefing(today, content)
    return {"date": today, "posts": len(posts), "chars": len(content)}


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
    if not done_secs:                                    # 오늘 첫 실행에만 M 거시 시장 코멘트(하루 1콜)
        _note = _merr_macro_note()
        if _note:
            _narrate("M", _note, model="haiku")
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
        for code in codes:                                          # 2단계: A가 데이터 먼저 → P/W/H 순차 판단
            if code in done_codes:                                  # 이미 분석한 종목 스킵(resume)
                continue
            name = names.get(code, code)
            brief = build_research_brief(code, name, code, market)   # A가 데이터 준비
            _store_report(today, code, name, brief, "대형주")        # 대형주는 소개 생략(재무만)
            _narrate("A", _stock_data_msg(name, code, brief))       # A가 먼저 올림
            verdicts, reasonings = {}, {}
            for bot in LARGECAP_BOTS:                               # P/W/H 순차 — 각자 끝나는 대로 하나씩
                try:
                    v, rz = analyze_stock(code, name, code, bot, market, brief=brief)
                except Exception as e:
                    logger.warning(f"대형주 분석 실패 {code}@{bot}: {e}")
                    v, rz = "관망", ""
                verdicts[bot], reasonings[bot] = v, rz
                _narrate(bot, verdict_message(rz, v, name), model="sonnet")
            approvers = [b for b in LARGECAP_BOTS if verdicts.get(b) == "매수"]
            if approvers:
                add_to_watch(code, name, ",".join(approvers))
                watched.append(code)
                _narrate(approvers[0], random.choice(_HANDOFF_LINES).format(name=name))   # 핸드오프 → Q
            held = get_open_positions_by_symbol(name, account="대형주")   # 강한 펀더매도(2인+) 안전판
            sellers = [b for b in LARGECAP_BOTS if verdicts.get(b) == "매도"]
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
    _narrate("Q", "출근 — 미장 장중, 대형주 진입/청산 타이밍 봅니다.")
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
        veto, why = q_veto(o["close"], up)                    # 보조지표 veto: '사면 안 되는 자리'만 막고 나머진 P/W/H 확신 따름
        q_note = _q_say(_tk(w["code"], w.get("name")), o["close"], "대기" if veto else "진입")
        _narrate("Q", q_note)                                 # 티커(상세명)·이유 (결재에도 이 멘트 첨부)
        log_decision("Q타이밍", "Q", w["code"], w.get("name"), "대기" if veto else "진입",
                     q_note, packet=(why or ""), model="rule")   # P0
        if veto:
            continue
        appr = w.get("approved_by") or ""                     # 관심등록 찬성봇(P/W/H) — 픽 성과 크레딧용
        if BUY_APPROVAL_REQUIRED:                             # 즉시 체결 대신 오너 결재로(Q 멘트까지 전달)
            appr_ko = "·".join(_ROLE_KO.get(a, a) for a in appr.split(",") if a)
            report = (f"· 펀더 심사 — {appr_ko} 담당이 이 종목을 관심종목으로 선정했습니다"
                      f"(재무·성장 기준 통과).\n· 진입 타이밍 — 아래 타이밍 담당 코멘트 참고. "
                      f"지금이 매수 자리라 판단합니다.")
            _submit_buy_approval("대형주", "대형주", w["name"], w["code"], o["close"][-1],
                                 _desk_amount("대형주"), [a for a in appr.split(",") if a], market,
                                 stock_desc=_report_summary(w["code"]),
                                 reason=report, q_comment=q_note, speaker="Q")
            continue
        rz = "Q 타이밍 승인 (P/W/H 확신·타이밍 이상무)" + (f" · 관심 {appr}" if appr else "")
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
            strat = "B" if "되돌림" in (p.get("reasoning") or "") else "M"   # veto·추세 매수는 M(50MA 이탈까지 홀드, 덜 eager)
            exiting = bool(q_exit_signal(o["close"], strat))
            _narrate("Q", _q_say(_tk(code), o["close"], "청산" if exiting else "홀드"))
            if exiting and not sell_shared_position(p["id"], o["close"][-1], exit_reasoning=f"Q {'추세' if strat == 'M' else '되돌림'} 익절/손절")[1]:
                sold.append(p["symbol"])
    _narrate("Q", "오늘 대형주 타이밍 점검 끝 — 퇴근합니다. 🫡")   # ③ 퇴근
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


# ── 워크데이 모델 (시간표 폐지 — 출근 → 아침 블록 → 종일 스터디 라운드 → 퇴근) ──
_DEEP_RE = re.compile(r"\[딥스터디\]\s*(유지|철회)")


def _deep_study(bot, code, name, first_reasoning, brief):
    """악마의 변호인 — 매수 확신을 스스로 반박(최악 시나리오)해보고 살아남는지 검증(1콜).
    반환: (살아남음 여부, 딥 노트). 실패 시 (True, "") — 스터디 불가가 매수 차단 사유는 아님."""
    try:
        from agents import call_agent
        from prompts import AGENT_PROFILES
        packet = (brief or ("", ""))[0]
        prompt = (f"방금 너는 {name} ({code})에 매수 판단을 내렸다:\n{first_reasoning}\n\n"
                  f"데이터:\n{packet}\n\n이제 반대편에 서라. 이 매수가 틀릴 수 있는 시나리오를 "
                  "구체적으로 2~3개 들고(밸류에이션·경쟁·수요 둔화 등), 각각에 네 논지가 버티는지 따져라. "
                  "버티면 [딥스터디] 유지 | 이유: … / 반박이 더 세면 [딥스터디] 철회 | 이유: … 로 끝내라.")
        out = call_agent(bot, AGENT_PROFILES[bot]["system"], prompt, model="sonnet", trim=False) or ""
        m = _DEEP_RE.search(out)
        survived = (m.group(1) == "유지") if m else True
        _narrate(bot, f"🧑‍🎓 {_tk(code, name)} 반대 논리 점검 — " + out.strip()[:320], model="sonnet")
        log_decision("딥스터디", bot, code, name, "유지" if survived else "철회", out, packet=packet)
        return survived, _clean_reason(re.sub(r"\[딥스터디\][^\n]*", "", out))
    except Exception as e:
        logger.warning(f"딥스터디 실패 {code}@{bot}: {e}")
        return True, ""


_workday_lock = threading.Lock()
_workday_date = None                                             # 오늘 첫 출근 여부(멘트 구분)


def run_workday():
    """워크데이 오케스트레이터 — keeper(매시)가 호출. 이미 근무 중이면 no-op.
    출근 → 아침 블록(전부 멱등·하루 1회 가드) → 종일 발굴 스터디 라운드 → WORKDAY_END_HOUR 퇴근.
    토큰 소진 시 루프가 쉬고, 프로세스가 죽어도 keeper가 다음 시각에 재출근."""
    global _workday_date
    if not (NEW_DESK_ENABLED and WORKDAY_ENABLED):
        return {"skipped": "disabled"}
    if not _workday_lock.acquire(blocking=False):
        return {"skipped": "already_working"}
    try:
        from agents import is_claude_token_exhausted
        first = _workday_date != _today_kst()
        _workday_date = _today_kst()
        _narrate("A", "🌅 다들 출근했습니다 — 오늘도 각자 페이스로 갑니다. 급할 것 없어요, 깊게 봅시다."
                 if first else "다시 자리에 앉았습니다 — 이어서 보던 것들 계속 봅니다.")
        run_macro_briefing()                                     # 하루 1회 가드 내장
        run_bottleneck_curation()                                # 하루 1회 가드 내장
        run_largecap_cycle()                                     # 오늘 완료 섹터는 idempotent 스킵
        run_q_index_desk()                                       # 멱등(중복 보유 방지)
        run_risk_review()                                        # 하루 1회 가드 내장
        rounds = 0
        while datetime.now(timezone(timedelta(hours=9))).hour < WORKDAY_END_HOUR:
            if is_claude_token_exhausted():                      # 토큰 소진 → 쉬었다가 충전되면 재개
                _time.sleep(15 * 60)
                continue
            run_discovery_cycle()                                # 관찰 재점검 + 발굴 라운드(라운드 쿼터·딥스터디)
            rounds += 1
            _time.sleep(WORKDAY_BREAK_SEC)
        _narrate("A", f"오늘 근무 끝 — 스터디 {rounds}라운드 돌았습니다. 다들 퇴근합니다 🫡")
        return {"rounds": rounds}
    except Exception as e:
        logger.error(f"워크데이 실패: {e}", exc_info=True)
        return {"error": str(e)}
    finally:
        _workday_lock.release()


# ── 스케줄러 슬롯 (컷오버) — 06 대형주 / 12·18·24 발굴. NEW_DESK_ENABLED=False면 각 내부 함수가 no-op ──
def run_largecap_cycle(market="US"):
    """06시 대형주 슬롯 — 섹터 토론+종목 [결정] 선정 → Q 진입 타이밍 집행 → NAV 스냅샷."""
    if not NEW_DESK_ENABLED:
        return {}
    try:
        sel = run_largecap_select(market)
        exe = run_largecap_execute(market)
        _snapshot_fund_nav()
        return {"select": sel, "execute": exe}
    except Exception as e:
        logger.error(f"대형주 사이클 실패: {e}", exc_info=True)
        return {"error": str(e)}


def run_discovery_cycle(market="US"):
    """12·18·24 발굴 슬롯 — 관찰 재점검 → A/S 발굴 → P/W/S OR게이트 (관찰등록/결재/매수) + 보유 점검 → NAV."""
    if not NEW_DESK_ENABLED:
        return {}
    try:
        obs = run_observation_review(market)     # 지켜보던 종목부터 재점검(확신 도달 시 결재 상신)
        desk = run_discovery_desk(market)
        review = run_discovery_review(market)
        _snapshot_fund_nav()
        return {"observation": obs, "desk": desk, "review": review}
    except Exception as e:
        logger.error(f"발굴 사이클 실패: {e}", exc_info=True)
        return {"error": str(e)}


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
