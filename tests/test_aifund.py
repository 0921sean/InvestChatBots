"""AI펀드 2단계 — A 소싱봇 순수 로직 검증 (네트워크 없음). 설계 docs/AIFUND_PIVOT.md."""
import os
import sys
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import aifund


def test_select_excludes_cached_and_respects_quota():
    universe = [f"S{i}" for i in range(100)]
    cached = {"S0", "S1", "S2"}
    new, cat = aifund.select_candidates(universe, cached, catalyst_codes=[], quota=10,
                                        rng=random.Random(42))
    assert len(new) == 10                      # 분량만큼
    assert not (set(new) & cached)             # 이미 본 건 제외
    assert cat == []


def test_catalyst_prioritized_and_counts_toward_quota():
    universe = [f"S{i}" for i in range(100)]
    cached = {"S0", "S1", "S2", "S3", "S4"}
    # S1,S2가 재료 떠서 재분석 대상
    new, cat = aifund.select_candidates(universe, cached, catalyst_codes=["S1", "S2"],
                                        quota=5, rng=random.Random(1))
    assert set(cat) == {"S1", "S2"}            # 카탈리스트 그대로
    assert len(new) == 3                        # 5 - 2 = 3 신규
    assert not (set(new) & cached)


def test_catalyst_hard_capped_at_quota():
    # 어닝 몰린 날: 카탈리스트 15개여도 하루 상한(10) 초과 안 함, 신규는 0
    universe = [f"S{i}" for i in range(100)]
    cached = {f"S{i}" for i in range(20)}
    cats = [f"S{i}" for i in range(15)]
    new, cat = aifund.select_candidates(universe, cached, cats, quota=10, rng=random.Random(1))
    assert len(cat) == 10 and new == []


def test_no_dup_between_new_and_catalyst():
    universe = [f"S{i}" for i in range(30)]
    cached = {"S0", "S1", "S2"}
    new, cat = aifund.select_candidates(universe, cached, ["S0", "S1"], quota=10, rng=random.Random(3))
    assert not (set(new) & set(cat))          # 카탈리스트가 신규로 새지 않음
    assert set(cat) == {"S0", "S1"} and not (set(new) & cached)


def test_catalyst_only_if_cached():
    # 캐시에 없는 코드는 카탈리스트로 안 침(의미 없음)
    new, cat = aifund.select_candidates(["A", "B", "C"], cached_codes=set(),
                                        catalyst_codes=["Z"], quota=2, rng=random.Random(0))
    assert cat == []
    assert len(new) == 2


def test_briefing_lists_only_actionable():
    msg = aifund.build_briefing(new=["COHR"], catalyst=["CRDO"])
    assert "CRDO" in msg and "업데이트" in msg   # 카탈리스트 = 갱신
    assert "COHR" in msg and "신규" in msg
    assert "2개" in msg


def test_briefing_includes_names():
    msg = aifund.build_briefing(new=["MSTR"], catalyst=["CRDO"],
                                names={"MSTR": "MicroStrategy", "CRDO": "Credo Technology"})
    assert "MSTR (MicroStrategy)" in msg          # 티커+회사명 병기
    assert "CRDO (Credo Technology)" in msg


def test_label_falls_back_to_ticker():
    assert aifund._label("XYZ", {}) == "XYZ"
    assert aifund._label("XYZ", {"XYZ": "XYZ"}) == "XYZ"   # 이름==티커면 병기 안 함


def test_briefing_empty_day():
    msg = aifund.build_briefing(new=[], catalyst=[])
    assert "없" in msg and "쉬" in msg           # 조용히 쉼


def test_toggle_off_by_default():
    assert aifund.NEW_DESK_ENABLED is False      # 컷오버 전까지 off (라이브 무변경)


def test_build_analysis_prompt_has_essentials():
    p = aifund.build_analysis_prompt("엔비디아", "NVDA", "PER: 30x\nROE: 100%", "AI infra company")
    assert "NVDA" in p and "[결정]" in p and "AI infra company" in p and "렌즈" in p


def test_parse_verdict():
    assert aifund._parse_verdict("어쩌고.\n[결정] 매수 | 이유: 성장") == "매수"
    assert aifund._parse_verdict("[결정] 관망 | 이유: 밸류") == "관망"
    assert aifund._parse_verdict("나는 매도가 맞다고 본다") == "매도"
    assert aifund._parse_verdict("판단 애매") == "관망"


def test_new_desk_order():
    assert aifund.NEW_DESK_ORDER == ["P", "W", "S"]
