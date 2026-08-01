"""봇 직위(職位) 산정 — 각 봇이 '매수 찬성한' 종목들의 평균 수익률로 승진.
가상 데스크 관전용. A(애널리스트)=신입 고정, 나머지는 픽 성과로 사다리를 오른다.

크레딧 규칙: 포지션 reasoning에서 찬성봇을 뽑아, 그 봇의 픽 목록에 수익률을 더한다.
- 발굴주 "발굴주 매수 (P,W)"        → P·W
- 대형주 "Q 되돌림 진입 · 관심 P,W,H" → Q(집행)·P·W·H(관심등록)
- 승계   "committee 승계 (실적왕…)"   → H(구 실적왕)
수익률 = 청산이면 실현(pnl_pct), 보유면 평가(unrealized_pnl_pct). 값 없으면 픽에서 제외.
"""
import re

# 한국 직급 사다리 (첫 직위 = 신입) — 픽 평균 수익률%로 승진
_LADDER = [
    (100.0, "상무"),
    (70.0, "이사"),
    (40.0, "부장"),
    (20.0, "차장"),
    (10.0, "과장"),
    (5.0, "대리"),
    (0.0, "주임"),
]
_BASE = "신입"                       # 픽 없음 or 평균 손실
_LETTERS = {"P", "W", "H", "S", "Q"}


def _approvers(reasoning: str) -> set:
    """reasoning 텍스트에서 찬성봇 레터 집합 추출."""
    r = reasoning or ""
    apprs = set()
    if "실적왕" in r:                # committee 승계 = H(구 실적왕)
        apprs.add("H")
    m = re.search(r"\(([^)]*)\)", r)  # "(P,W)" 그룹
    if m:
        apprs |= {t.strip() for t in m.group(1).split(",") if t.strip() in _LETTERS}
    for tok in r.replace("·", " ").replace(",", " ").split():  # "Q 진입", "관심 P W H"
        if tok in _LETTERS:
            apprs.add(tok)
    return apprs


def _rank_for(avg_return: float) -> str:
    for thr, title in _LADDER:
        if avg_return >= thr:
            return title
    return _BASE


def compute_ranks(positions) -> dict:
    """데스크 포지션 목록 → {bot: 직위}. A는 호출측에서 신입 고정."""
    picks: dict = {}                 # bot -> [수익률%, ...]
    for p in positions:
        ret = p.get("pnl_pct") if p.get("status") == "closed" else p.get("unrealized_pnl_pct")
        if ret is None:
            continue
        for b in _approvers(p.get("reasoning", "")):
            picks.setdefault(b, []).append(ret)
    return {b: _rank_for(sum(v) / len(v)) for b, v in picks.items()}


def rank_of(bot: str, ranks: dict) -> str:
    """봇 직위 조회. A(애널리스트·비매매)는 신입 고정."""
    if bot == "A":
        return _BASE
    return ranks.get(bot, _BASE)
