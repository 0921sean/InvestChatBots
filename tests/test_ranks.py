"""봇 직위 산정(ranks.py) 테스트 — 픽 성과 크레딧·사다리·A 고정."""
import ranks


def test_approvers_parsing():
    assert ranks._approvers("발굴주 매수 (P,W)") == {"P", "W"}
    assert ranks._approvers("Q 되돌림 진입 · 관심 P,W,H") == {"Q", "P", "W", "H"}
    assert ranks._approvers("committee 승계 (실적왕+역추세봇 매수)") == {"H"}
    assert ranks._approvers("") == set()


def test_rank_ladder_thresholds():
    assert ranks._rank_for(-5) == "신입"
    assert ranks._rank_for(0) == "주임"
    assert ranks._rank_for(12) == "과장"
    assert ranks._rank_for(150) == "상무"


def test_compute_and_A_fixed():
    positions = [
        {"status": "closed", "pnl_pct": 20.0, "reasoning": "Q 되돌림 진입 · 관심 H"},
        {"status": "open", "unrealized_pnl_pct": 40.0, "reasoning": "발굴주 매수 (P)"},
        {"status": "open", "unrealized_pnl_pct": None, "reasoning": "발굴주 매수 (W)"},  # 값 없음 → 제외
    ]
    rk = ranks.compute_ranks(positions)
    assert rk["H"] == "차장"        # 20% → 차장
    assert rk["Q"] == "차장"        # 20% → 차장
    assert rk["P"] == "부장"        # 40% → 부장
    assert "W" not in rk            # 수익률 값 없어 픽 제외
    # A는 픽이 있어도 신입 고정
    assert ranks.rank_of("A", {"A": "부장"}) == "신입"
    assert ranks.rank_of("W", rk) == "신입"   # 픽 없으면 신입
