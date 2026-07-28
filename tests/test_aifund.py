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
