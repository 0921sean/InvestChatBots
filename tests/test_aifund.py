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


# ── 발굴 분석(P/W/S) 순수 로직 ──────────────────────────
def test_parse_verdict():
    assert aifund._parse_verdict("근거들\n[결정] 매수") == "매수"
    assert aifund._parse_verdict("[결정]  관망") == "관망"
    assert aifund._parse_verdict("음... [결정] 매도야") == "매도"
    assert aifund._parse_verdict("결정 표기 없음") == "관망"   # 없으면 보수적 기본값
    assert aifund._parse_verdict("") == "관망"


def test_extra_fundamentals_renders_present_only():
    txt = aifund._extra_fundamentals(
        {"gross_margin": 0.62, "op_margin": 0.31, "rev_growth": 0.42, "fcf": 2.7e9})
    assert "총마진 62.0%" in txt and "영업마진 31.0%" in txt
    assert "매출성장(YoY) 42.0%" in txt and "잉여현금흐름 2.7B" in txt
    assert "순마진" not in txt                    # 없는 값은 생략
    assert aifund._extra_fundamentals({}) == ""   # 아무것도 없으면 빈 문자열


def test_build_analysis_prompt():
    p = aifund.build_analysis_prompt("Credo", "CRDO", "패킷", "광통신 병목 회사")
    assert "Credo (CRDO)" in p and "패킷" in p
    assert "사업 개요" in p and "광통신 병목 회사" in p
    assert "[결정] 매수" in p                      # 출력 포맷 강제
    p2 = aifund.build_analysis_prompt("X", "X", "패킷")   # 사업요약 없어도 동작
    assert "사업 개요" not in p2


def _mock_brief(monkeypatch):
    # A 브리프 준비를 네트워크 없이 (analyze_candidate가 종목당 1회 호출)
    monkeypatch.setattr(aifund, "build_research_brief", lambda *a, **k: ("패킷", "사업"))


def test_analyze_candidate_gate(monkeypatch):
    # 관대 게이트: 한 명이라도 매수면 통과
    _mock_brief(monkeypatch)
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, market="US", brief=None:
                        ({"P": "매수", "W": "관망", "S": "매도"}[bot], "r"))
    r = aifund.analyze_candidate("CRDO", "Credo", "CRDO")
    assert r["approved"] is True
    assert r["verdicts"] == {"P": "매수", "W": "관망", "S": "매도"}


def test_analyze_candidate_all_wait_rejected(monkeypatch):
    _mock_brief(monkeypatch)
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, market="US", brief=None: ("관망", "r"))
    r = aifund.analyze_candidate("XYZ", "Xyz", "XYZ")
    assert r["approved"] is False                 # 아무도 매수 안 하면 탈락


def test_analyze_candidate_survives_bot_error(monkeypatch):
    # 봇 하나 터져도 관망 처리하고 계속(전체 실패 방지)
    _mock_brief(monkeypatch)
    def flaky(code, name, tk, bot, market="US", brief=None):
        if bot == "W":
            raise RuntimeError("LLM 타임아웃")
        return ("매수" if bot == "P" else "관망"), "r"
    monkeypatch.setattr(aifund, "analyze_stock", flaky)
    r = aifund.analyze_candidate("CRDO", "Credo", "CRDO")
    assert r["verdicts"]["W"] == "관망"           # 예외 → 관망
    assert r["approved"] is True                   # P 매수라 통과


def test_quarterly_trend_computes_direction():
    # 매출 가속 + 마진 개선 시나리오 (최신→과거 순 입력)
    e9 = 1e9
    rev = [100 * e9, 90 * e9, 80 * e9, 70 * e9, 60 * e9]   # 최신 100B, 1년 전 60B → YoY +67%
    gross = [62 * e9, 54 * e9, 46 * e9, 38 * e9, 30 * e9]  # 총마진 62% → (3분기 전) 57.5% = 개선
    txt = aifund.quarterly_trend(rev, gross, [], [])
    assert "매출" in txt and "YoY +67%" in txt
    assert "총마진 62%(↑개선)" in txt
    assert "60.0B → " in txt and "100.0B" in txt   # 오래된→최신 흐름


def test_quarterly_trend_needs_min_quarters():
    assert aifund.quarterly_trend([100, 90], [], [], []) == ""   # 3분기 미만이면 빈
    assert aifund.quarterly_trend([], [], [], []) == ""


# ── P/W/S 매수·청산 실행 (봇별 독립) ──────────────────────
def test_execute_buys_per_bot(monkeypatch):
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])   # 미보유
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])             # 자리 있음
    calls = []
    def mock_buy(*a, **k):
        calls.append((k["account"], a[0])); return (1, None)
    monkeypatch.setattr(db, "buy_shared_position", mock_buy)
    bought = aifund.execute_buys("NVDA", "NVDA", {"P": "매수", "W": "관망", "S": "매도"})
    assert bought == ["P"]                         # 매수한 봇만
    assert calls == [("P", "NVDA")]                # P 계좌에만


def test_execute_buys_noop_when_disabled():
    assert aifund.execute_buys("NVDA", "NVDA", {"P": "매수"}) == []   # 토글 off


def test_execute_buys_skips_already_held(monkeypatch):
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [{"id": 1}])  # 보유중
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    def no_buy(*a, **k):
        raise AssertionError("이미 보유인데 사면 안 됨")
    monkeypatch.setattr(db, "buy_shared_position", no_buy)
    assert aifund.execute_buys("NVDA", "NVDA", {"P": "매수"}) == []


def test_execute_thesis_sell(monkeypatch):
    import db
    calls = []
    monkeypatch.setattr(db, "sell_shared_position",
                        lambda pos_id, price, exit_reasoning="": (calls.append(pos_id), (0, None))[1])
    assert aifund.execute_thesis_sell("W", {"id": 7}, "매도", 50.0) is True
    assert calls == [7]
    assert aifund.execute_thesis_sell("W", {"id": 7}, "관망", 50.0) is False   # 매도 아니면 no-op


# ── Q 라이브 실행 신호 ────────────────────────────────────
def test_q_entry_signal_prefers_minervini(monkeypatch):
    import backtest as bt, trading_strategies as ts
    monkeypatch.setattr(bt, "minervini_entry", lambda c, up: True)
    assert aifund.q_entry_signal([1, 2, 3], True) == "M"          # M 우선
    monkeypatch.setattr(bt, "minervini_entry", lambda c, up: False)
    monkeypatch.setattr(ts, "meanrev_entry", lambda c: True)
    assert aifund.q_entry_signal([1, 2, 3], True) == "B"          # M 없으면 B
    monkeypatch.setattr(ts, "meanrev_entry", lambda c: False)
    assert aifund.q_entry_signal([1, 2, 3], True) is None         # 둘 다 X


def test_run_q_desk_noop_when_disabled():
    assert aifund.run_q_desk() == {"bought": [], "sold": []}      # 토글 off
