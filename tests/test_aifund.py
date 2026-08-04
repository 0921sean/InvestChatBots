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
    assert "[결정] 관망 | 이유:" in p              # 출력 포맷 강제(이유 포함)
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


# ── 하루 사이클 오케스트레이션 ────────────────────────────
def test_run_new_desk_cycle_noop_when_disabled():
    assert aifund.run_new_desk_cycle() == {
        "discovery": {}, "discovery_review": {}, "largecap_select": {}, "largecap_execute": {}}


def test_run_discovery_desk_or_gate_buys(monkeypatch):
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "source_today",
                        lambda m="US": {"new": ["NVDA"], "catalyst": [], "names": {"NVDA": "NVIDIA"}, "briefing": "b"})
    monkeypatch.setattr(aifund, "source_bottleneck", lambda m="US": {"codes": [], "names": {}})
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "_business_brief", lambda name, code, biz: ("소개", True))  # 이해 게이트 통과(명확)
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, m="US", brief=None: ({"P": "매수", "W": "관망", "S": "관망"}.get(bot, "관망"), ""))
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    calls = []
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (calls.append(k.get("account")), (1, None))[1])
    r = aifund.run_discovery_desk()
    assert r["buys"] == ["NVDA"]                  # P 매수 → 발굴주 즉시매수
    assert calls == ["발굴주"]                     # 발굴주 계좌로 체결


def test_run_discovery_desk_comprehension_gate_blocks(monkeypatch):
    """A가 사업 불명확 판정 → P/W/S 분석·매수 스킵(하드 차단)."""
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "source_today",
                        lambda m="US": {"new": ["NVDA"], "catalyst": [], "names": {"NVDA": "NVIDIA"}, "briefing": "b"})
    monkeypatch.setattr(aifund, "source_bottleneck", lambda m="US": {"codes": [], "names": {}})
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "_business_brief", lambda name, code, biz: ("", False))  # 사업 불명확
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    analyzed = []
    monkeypatch.setattr(aifund, "analyze_stock", lambda *a, **k: analyzed.append(1) or ("매수", ""))
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (1, None))
    r = aifund.run_discovery_desk()
    assert r["buys"] == []                          # 불명확 → 매수 차단
    assert analyzed == []                           # P/W/S 분석도 안 함(스킵)


def test_run_largecap_select_2stage_daily(monkeypatch):
    import db
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "_largecap_sectors", lambda: {"반도체": ["AAPL"]})   # 섹터 단위
    monkeypatch.setattr(aifund, "stock_name", lambda c: "애플")
    monkeypatch.setattr(db, "get_fund_reports", lambda n=300: [])                     # 오늘 첫 실행(스킵 없음)
    cleared = []
    monkeypatch.setattr(db, "clear_watchlist", lambda: cleared.append(True))          # 매일 재분석
    opin = []
    monkeypatch.setattr(aifund, "_sector_opinion", lambda bot, sec, names, m="US": f"{bot} 섹터의견")
    monkeypatch.setattr(aifund, "_store_sector_consensus", lambda t, sec, ops: opin.append((sec, dict(ops))))
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, m="US", brief=None: ({"P": "관망", "W": "매수", "H": "관망"}.get(bot, "관망"), ""))
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    watched = []
    monkeypatch.setattr(db, "add_to_watch", lambda code, name, approved: watched.append((code, approved)))
    r = aifund.run_largecap_select()
    assert cleared == [True]                          # 매일 watch 초기화
    assert r["sectors"] == ["반도체"]                  # 섹터 토론 수행
    assert opin and opin[0][0] == "반도체"             # 섹터 합의 저장
    assert r["watched"] == ["AAPL"] and watched == [("AAPL", "W")]   # W 매수 → 그날 관심종목


def test_run_largecap_execute_q_entry(monkeypatch):
    import db, backtest
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "_spy_uptrend", lambda: True)
    monkeypatch.setattr(aifund, "_q_explain", lambda *a, **k: "Q 설명")
    monkeypatch.setattr(db, "get_watchlist", lambda status="watching": [{"code": "AAPL", "name": "애플"}])
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(backtest, "_fetch", lambda codes, period="2y": {"AAPL": {"close": [1, 2, 3]}})
    monkeypatch.setattr(aifund, "q_entry_signal", lambda closes, up: "M")
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    marks = []
    monkeypatch.setattr(db, "mark_watch", lambda code, status: marks.append((code, status)))
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (1, None))
    r = aifund.run_largecap_execute()
    assert r["bought"] == ["AAPL"] and marks == [("AAPL", "bought")]   # Q 진입 타이밍 → 대형주 매수


def test_desk_rosters_and_H_profile():
    from prompts import AGENT_PROFILES
    assert aifund.LARGECAP_BOTS == ["P", "W", "H"]      # 대형주 결정자
    assert aifund.DISCOVERY_BOTS == ["P", "W", "S"]     # 발굴주 결정자
    assert "H" in AGENT_PROFILES                        # H(실적왕 개명) 로드됨
    # analyze_candidate가 대형주 봇들(P/W/H)로 돌 수 있어야
    import random
    r = aifund.analyze_candidate  # 존재 확인(시그니처 bots 인자)
    assert "bots" in r.__code__.co_varnames


def test_source_bottleneck_self_sources_seed_minus_cached(monkeypatch):
    monkeypatch.setattr(aifund, "_bottleneck_seed", lambda: ["COHR", "LITE", "AAOI"])
    monkeypatch.setattr(aifund, "get_cached_codes", lambda: ["LITE"])   # LITE는 이미 분석됨
    monkeypatch.setattr(aifund, "stock_name", lambda c: c)
    r = aifund.source_bottleneck("US")
    assert r["codes"] == ["COHR", "AAOI"]                # 시드 - 캐시, S가 직접 고름
    assert set(r["names"]) == {"COHR", "AAOI"}


def test_source_bottleneck_us_only_and_empty_seed(monkeypatch):
    monkeypatch.setattr(aifund, "_bottleneck_seed", lambda: ["COHR"])
    monkeypatch.setattr(aifund, "get_cached_codes", lambda: [])
    monkeypatch.setattr(aifund, "stock_name", lambda c: c)
    assert aifund.source_bottleneck("KRX")["codes"] == []   # 병목 시드는 미장 전용
    monkeypatch.setattr(aifund, "_bottleneck_seed", lambda: [])
    assert aifund.source_bottleneck("US")["codes"] == []    # 시드 없으면 비활성


def test_run_largecap_execute_q_exit(monkeypatch):
    import db, backtest
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "_spy_uptrend", lambda: True)
    monkeypatch.setattr(aifund, "_q_explain", lambda *a, **k: "Q 설명")
    monkeypatch.setattr(db, "get_watchlist", lambda status="watching": [])
    pos = {"id": 9, "code": "AAPL", "symbol": "애플", "reasoning": "Q 볼린저 진입"}
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [pos])
    monkeypatch.setattr(backtest, "_fetch", lambda codes, period="2y": {"AAPL": {"close": [3, 2, 1]}})
    monkeypatch.setattr(aifund, "q_exit_signal", lambda closes, strat: True)
    sells = []
    monkeypatch.setattr(db, "sell_shared_position", lambda pid, price, exit_reasoning="": (sells.append(pid), (0, None))[1])
    r = aifund.run_largecap_execute()
    assert r["sold"] == ["애플"] and sells == [9]   # Q B+M 청산(익절/손절)


def test_desk_sizing_largecap_concentrated_discovery_diversified():
    # 대형주 집중(8%×12) / 발굴주 분산(4%×20)
    assert aifund._desk_amount("대형주") == 5_000_000
    assert aifund._desk_amount("발굴주") == 2_500_000
    assert aifund._desk_can_open("대형주", 9) and not aifund._desk_can_open("대형주", 10)
    assert aifund._desk_can_open("발굴주", 19) and not aifund._desk_can_open("발굴주", 20)


def test_run_largecap_select_resume_skips_done(monkeypatch):
    import db
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "_largecap_sectors", lambda: {"A섹터": ["X"], "B섹터": ["Y", "Z"]})
    monkeypatch.setattr(aifund, "stock_name", lambda c: c)
    monkeypatch.setattr(aifund, "_today_kst", lambda: "2026-08-02")
    # A섹터 완전완료, B섹터 합의됨+Y완료 → Z만 새로 분석해야(resume)
    monkeypatch.setattr(db, "get_fund_reports", lambda n=300: [
        {"date": "2026-08-02", "desk": "섹터합의", "name": "A섹터", "code": "섹터:A섹터"},
        {"date": "2026-08-02", "desk": "섹터합의", "name": "B섹터", "code": "섹터:B섹터"},
        {"date": "2026-08-02", "desk": "대형주", "name": "X", "code": "X"},
        {"date": "2026-08-02", "desk": "대형주", "name": "Y", "code": "Y"},
    ])
    cleared = []
    monkeypatch.setattr(db, "clear_watchlist", lambda: cleared.append(1))
    monkeypatch.setattr(aifund, "_sector_opinion", lambda *a, **k: "op")
    monkeypatch.setattr(aifund, "_store_sector_consensus", lambda *a, **k: None)
    analyzed = []
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    def _as(code, name, tk, bot, m="US", brief=None):
        if code not in analyzed:
            analyzed.append(code)
        return ("관망", "")
    monkeypatch.setattr(aifund, "analyze_stock", _as)
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    monkeypatch.setattr(db, "add_to_watch", lambda *a, **k: None)
    aifund.run_largecap_select()
    assert analyzed == ["Z"]      # X(완료섹터)·Y(완료종목) 스킵, Z만
    assert cleared == []          # 합의 있으니 watchlist 안 비움(resume)
