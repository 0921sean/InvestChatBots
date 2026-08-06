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


def test_run_discovery_desk_approval_mode_queues_not_buys(monkeypatch):
    # BUY_APPROVAL_REQUIRED=True면 즉시 체결 대신 결재 상신, 사이클은 계속
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "BUY_APPROVAL_REQUIRED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "source_today",
                        lambda m="US": {"new": ["NVDA"], "catalyst": [], "names": {"NVDA": "NVIDIA"}, "briefing": "b"})
    monkeypatch.setattr(aifund, "source_bottleneck", lambda m="US": {"codes": [], "names": {}})
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "_business_brief", lambda name, code, biz: ("수직 SaaS 회사", True))
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, m="US", brief=None: ({"P": "매수"}.get(bot, "관망"), "PEG 매력"))
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    submitted, bought = [], []
    monkeypatch.setattr(aifund, "_submit_buy_approval",
                        lambda desk, acct, ticker, code, *a, **k: (submitted.append((desk, code, k.get("stock_desc"))), True)[1])
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (bought.append(1), (1, None))[1])
    r = aifund.run_discovery_desk()
    assert submitted == [("발굴주", "NVDA", "수직 SaaS 회사")]   # 결재 상신(설명 첨부)
    assert bought == []                                          # 즉시 체결 안 함
    assert r["buys"] == []                                       # 승인 대기 — 체결 목록 없음


def test_submit_buy_approval_queues_narrates_notifies_and_dedups(tmp_path, monkeypatch):
    import db, notifier
    monkeypatch.setenv("DB_PATH", str(tmp_path / "buy.db"))
    db.init_db()
    narr, noti = [], []
    monkeypatch.setattr(aifund, "_narrate", lambda b, c, model="rule": narr.append((b, c)))
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: noti.append(1))
    ok = aifund._submit_buy_approval("대형주", "대형주", "NVIDIA", "NVDA", 120.5, 5_000_000,
                                     ["P", "W", "H"], "US", stock_desc="AI 반도체", reason="확신",
                                     q_comment="돌파 신호", speaker="Q")
    assert ok is True
    rows = db.get_pending_buys("pending")
    assert len(rows) == 1 and rows[0]["q_comment"] == "돌파 신호" and rows[0]["decision_price"] == 120.5
    assert narr and narr[0][0] == "Q" and "결재" in narr[0][1] and noti
    # 같은 종목·데스크 중복 상신은 skip(내레이션·알림도 안 함)
    narr.clear(); noti.clear()
    assert aifund._submit_buy_approval("대형주", "대형주", "NVIDIA", "NVDA", 130, 5_000_000, ["P"], "US") is False
    assert narr == [] and noti == []


def test_discovery_s_pick_is_s_only_not_committee(monkeypatch):
    # S 병목픽은 P/W 재투표 없이 S 독자 판단 → 매수면 S 단독 승인으로 처리
    import db, fetchers
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "source_today",
                        lambda m="US": {"new": [], "catalyst": [], "names": {}, "briefing": "b"})  # A픽 없음
    monkeypatch.setattr(aifund, "source_bottleneck",
                        lambda m="US": {"codes": ["POET"], "names": {"POET": "POET"}})              # S픽만
    monkeypatch.setattr(aifund, "_s_sourcing_note", lambda *a, **k: "note")
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "_business_brief", lambda name, code, biz: ("광통신 병목", True))
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    analyzed = []
    def rec(code, name, tk, bot, m="US", brief=None):
        analyzed.append((code, bot)); return "매수", "InP 병목"
    monkeypatch.setattr(aifund, "analyze_stock", rec)
    monkeypatch.setattr(db, "get_open_positions_by_symbol", lambda *a, **k: [])
    monkeypatch.setattr(db, "get_open_positions", lambda *a, **k: [])
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 12.0)
    calls = []
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (calls.append(a[0]), (1, None))[1])
    r = aifund.run_discovery_desk()
    assert analyzed == [("POET", "S")]        # S만 판단 — P/W 안 붙음
    assert r["buys"] == ["POET"]              # S 단독 매수


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
    monkeypatch.setattr(aifund, "_merr_macro_note", lambda: "")                        # M 거시 코멘트 LLM 호출 목킹
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


def test_source_bottleneck_resources_seed_minus_done_and_held(monkeypatch):
    # 상시 워치리스트: 시드에서 '오늘 이미 분석' + '보유 중'만 제외하고 매일 재소싱
    import db
    monkeypatch.setattr(aifund, "_bottleneck_seed", lambda: ["COHR", "LITE", "AAOI"])
    monkeypatch.setattr(aifund, "_today_kst", lambda: "2026-08-05")
    monkeypatch.setattr(db, "get_fund_reports", lambda n=300: [{"date": "2026-08-05", "code": "LITE"}])  # LITE 오늘 분석함
    monkeypatch.setattr(db, "get_open_positions", lambda account=None: [{"code": "AAOI"}])                # AAOI 보유 중
    monkeypatch.setattr(aifund, "stock_name", lambda c: c)
    r = aifund.source_bottleneck("US")
    assert r["codes"] == ["COHR"]                        # 시드 - 오늘분석(LITE) - 보유(AAOI)
    assert set(r["names"]) == {"COHR"}


def test_bottleneck_seed_reads_db_approved_and_backfills_once(tmp_path, monkeypatch):
    # DB approved 시드를 읽는다(로드맵 ②b). 빈 테이블은 하드코딩을 1회 approved 백필.
    import db
    monkeypatch.setenv("DB_PATH", str(tmp_path / "seed.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "_hardcoded_bottleneck_seed", lambda: ["COHR", "LITE"])
    assert set(aifund._bottleneck_seed()) == {"COHR", "LITE"}      # 최초 1회 백필 → approved
    # 사람이 새 후보를 승인/반려하면 즉시 반영, 백필은 다시 안 함
    db.add_bottleneck_seed("AXTI", "InP 상류")
    assert set(aifund._bottleneck_seed()) == {"COHR", "LITE"}      # pending은 아직 제외
    db.decide_bottleneck_seed("AXTI", "approved")
    db.decide_bottleneck_seed("COHR", "rejected")
    assert set(aifund._bottleneck_seed()) == {"LITE", "AXTI"}      # 승인 반영 + 반려 제외


def test_submit_bottleneck_candidates_queues_narrates_notifies(tmp_path, monkeypatch):
    # ②d 산출물 → pending 등록 + S '결재 올림' 내레이션 + 오너 ntfy (승인은 사람)
    import db, notifier
    monkeypatch.setenv("DB_PATH", str(tmp_path / "sub.db"))
    db.init_db()
    narrated, notified = [], []
    monkeypatch.setattr(aifund, "_narrate", lambda bot, content, model="rule": narrated.append((bot, content)))
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: notified.append((a, k)))
    added = aifund.submit_bottleneck_candidates(
        [{"ticker": "axti", "rationale": "InP 상류"}, "SIVE"], source="agent")
    assert set(added) == {"AXTI", "SIVE"}
    assert db.get_bottleneck_seeds("pending") == ["AXTI", "SIVE"]   # approved 아님 — 사람 결재 대기
    assert db.get_bottleneck_seeds("approved") == []
    assert narrated and narrated[0][0] == "S" and "AXTI" in narrated[0][1]
    assert len(notified) == 1
    # 중복 제출은 새로 안 올라가고 알림/내레이션도 안 함
    narrated.clear(); notified.clear()
    assert aifund.submit_bottleneck_candidates(["AXTI"]) == []
    assert narrated == [] and notified == []


def test_pub_letter_rot13_maps_fund_bots_only():
    assert aifund.pub_letter("A") == "N" and aifund.pub_letter("P") == "C"
    assert aifund.pub_letter("W") == "J" and aifund.pub_letter("H") == "U"
    assert aifund.pub_letter("S") == "F" and aifund.pub_letter("Q") == "D"
    assert aifund.pub_letter("M") == "Z"
    assert aifund.pub_letter("드가자") == "드가자"      # 위원회 한글명 통과
    assert aifund.pub_letter("User") == "User"


def test_merr_macro_note_uses_current_data(tmp_path, monkeypatch):
    # M은 '지금' 지수 데이터를 매크로 렌즈로 읽음 — call_agent 목킹, 벤치마크 최근 2행 주입
    import db, agents, prompts
    monkeypatch.setenv("DB_PATH", str(tmp_path / "m.db"))
    db.init_db()
    monkeypatch.setitem(prompts.AGENT_PROFILES, "M", {"system": "매크로 자문"})  # 비공개 페르소나 없는 CI 대비
    monkeypatch.setattr(db, "get_benchmark_snapshots",
                        lambda: [{"date": "2026-08-05", "spy": 500, "qqq": 400},
                                 {"date": "2026-08-06", "spy": 505, "qqq": 396}])
    captured = {}
    def fake(name, system, prompt, timeout=60, model=None, trim=True):
        captured["prompt"] = prompt
        return "유동성은 아직 우호적이지만 밸류 부담이 커진 국면이다."
    monkeypatch.setattr(agents, "call_agent", fake)
    note = aifund._merr_macro_note()
    assert "국면" in note
    assert "S&P500" in captured["prompt"] and "+1.0%" in captured["prompt"]   # 현재 데이터 주입
    assert "과거" in captured["prompt"]                                        # 과거시황 금지 지시


def test_parse_candidates_json_extracts_and_filters():
    # 코드펜스·머리말 섞여도 JSON 배열만 추출, ticker 없는 항목은 버림, 대문자 정규화
    txt = ('여기 후보입니다:\n```json\n'
           '[{"ticker":"axti","rationale":"InP 상류"},'
           '{"rationale":"티커없음"},'
           '{"ticker":"SIVE","rationale":"실리콘포토닉스"}]\n```')
    out = aifund._parse_candidates_json(txt)
    assert [c["ticker"] for c in out] == ["AXTI", "SIVE"]
    assert aifund._parse_candidates_json("설명만 있고 JSON 없음") == []


def test_curation_backfills_before_research_preserving_watchlist(tmp_path, monkeypatch):
    # 빈 DB 첫 실행: 큐레이션이 하드코딩 시드를 approved 백필 '먼저' → 워치리스트 보존 + 중복 방지
    import db, agents, notifier
    monkeypatch.setenv("DB_PATH", str(tmp_path / "bf.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "BOTTLENECK_CURATION_ENABLED", True)
    monkeypatch.setattr(aifund, "_hardcoded_bottleneck_seed", lambda: ["COHR", "MU"])
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: None)
    # 조사 결과에 기존 시드(COHR)가 섞여도 제외돼야 함
    monkeypatch.setattr(agents, "_call_claude_cli",
                        lambda s, u, timeout=60, model=None, allowed_tools=None:
                        '[{"ticker":"COHR","rationale":"기존"},{"ticker":"NBIS","rationale":"신규"}]')
    r = aifund.run_bottleneck_curation(limit=5)
    assert set(db.get_bottleneck_seeds("approved")) == {"COHR", "MU"}   # 백필 보존
    assert r["added"] == ["NBIS"]                                       # 기존 COHR 제외, 신규만
    assert db.get_bottleneck_seeds("pending") == ["NBIS"]


def test_run_bottleneck_curation_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(aifund, "BOTTLENECK_CURATION_ENABLED", False)
    assert aifund.run_bottleneck_curation()["skipped"] == "disabled"


def test_run_bottleneck_curation_researches_excludes_and_queues(tmp_path, monkeypatch):
    import db, agents, notifier
    monkeypatch.setenv("DB_PATH", str(tmp_path / "cur.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "BOTTLENECK_CURATION_ENABLED", True)
    db.add_bottleneck_seed("COHR", "이미 다룸")               # 제외 대상(테이블에 이미 있음)
    db.decide_bottleneck_seed("COHR", "rejected")            # 이미 결재됨 — 재출현 금지 확인용
    narr, noti = [], []
    monkeypatch.setattr(aifund, "_narrate", lambda b, c, model="rule": narr.append((b, c)))
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: noti.append(1))
    # 웹서치 CLI 응답을 목킹: COHR(제외)·AXTI·SIVE·TOOLONG(형식탈락)·nvda(소문자→NVDA)
    fake = ('[{"ticker":"COHR","rationale":"x"},{"ticker":"AXTI","rationale":"InP"},'
            '{"ticker":"SIVE","rationale":"포토닉스"},{"ticker":"TOOLONG","rationale":"y"},'
            '{"ticker":"nvda","rationale":"z"}]')
    monkeypatch.setattr(agents, "_call_claude_cli",
                        lambda sys_, usr, timeout=60, model=None, allowed_tools=None: fake)
    r = aifund.run_bottleneck_curation(limit=5)
    assert set(r["added"]) == {"AXTI", "SIVE", "NVDA"}          # COHR 제외 · TOOLONG 형식탈락
    assert set(db.get_bottleneck_seeds("pending")) == {"AXTI", "SIVE", "NVDA"}
    assert narr and noti                                        # S 결재 올림 + 오너 알림 1회


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


def test_compose_buy_report_role_labeled_and_strips_decision():
    # 결재 상세 사유 = 승인 봇별 역할라벨 + 근거 본문([결정] 줄 제거)
    rep = aifund._compose_buy_report(
        ["P", "S"],
        {"P": "PEG 0.9로 저평가.\n[결정] 매수 | 이유: 성장 지속",
         "S": "InP 상류 병목이라 대체 불가.",
         "W": "관망 근거"})               # 미승인 봇(W)은 제외
    assert "성장주 담당 의견 — PEG 0.9로 저평가." in rep
    assert "병목 담당 의견 — InP 상류 병목이라 대체 불가." in rep
    assert "관망" not in rep and "[결정]" not in rep   # 승인봇만 · 결정줄 제거


def test_run_macro_briefing_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(aifund, "MACRO_BRIEFING_ENABLED", False)
    assert aifund.run_macro_briefing()["skipped"] == "disabled"


def test_run_macro_briefing_uses_blog_and_index(tmp_path, monkeypatch):
    import db, agents, prompts
    monkeypatch.setenv("DB_PATH", str(tmp_path / "brief.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "MACRO_BRIEFING_ENABLED", True)
    monkeypatch.setattr(aifund, "_fetch_new_blog_posts", lambda: 0)          # 크롤 목킹
    monkeypatch.setattr(db, "get_recent_blog_posts",
                        lambda since, limit=8: [{"post_date": "2026-08-05", "title": "연준 스탠스", "content": "유동성 축소 관점"}])
    monkeypatch.setattr(db, "get_benchmark_snapshots",
                        lambda: [{"spy": 500, "qqq": 400}, {"spy": 505, "qqq": 396}])
    monkeypatch.setitem(prompts.AGENT_PROFILES, "M", {"system": "거시 자문"})
    cap = {}
    monkeypatch.setattr(agents, "call_agent",
                        lambda name, sys_, usr, timeout=60, model=None, trim=True: (cap.update(p=usr), "오늘 시장은 관망 국면입니다.")[1])
    r = aifund.run_macro_briefing()
    assert r["posts"] == 1 and r["chars"] > 0
    assert "연준 스탠스" in cap["p"] and "S&P500" in cap["p"] and "과거" in cap["p"]   # 블로그+지수+현재기준 지시
    assert db.get_latest_macro_briefing()["content"] == "오늘 시장은 관망 국면입니다."


def test_discovery_observation_mode_registers_not_buys(tmp_path, monkeypatch):
    # OBSERVATION_REQUIRED=True: 매수 판단 → 관찰 등록만(결재도 체결도 안 함)
    import db, fetchers
    monkeypatch.setenv("DB_PATH", str(tmp_path / "obs.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "OBSERVATION_REQUIRED", True)
    monkeypatch.setattr(aifund, "BUY_APPROVAL_REQUIRED", True)   # 관찰이 결재보다 먼저
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(db, "ensure_desk_accounts", lambda: None)
    monkeypatch.setattr(aifund, "source_today",
                        lambda m="US": {"new": ["NVDA"], "catalyst": [], "names": {"NVDA": "NVIDIA"}, "briefing": "b"})
    monkeypatch.setattr(aifund, "source_bottleneck", lambda m="US": {"codes": [], "names": {}})
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("", ""))
    monkeypatch.setattr(aifund, "_business_brief", lambda name, code, biz: ("AI 반도체", True))
    monkeypatch.setattr(aifund, "analyze_stock",
                        lambda code, name, tk, bot, m="US", brief=None: ({"P": "매수"}.get(bot, "관망"), "PEG 매력"))
    monkeypatch.setattr(aifund, "_store_report", lambda *a, **k: None)
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: 100.0)
    submitted, bought = [], []
    monkeypatch.setattr(aifund, "_submit_buy_approval", lambda *a, **k: (submitted.append(1), True)[1])
    monkeypatch.setattr(db, "buy_shared_position", lambda *a, **k: (bought.append(1), (1, None))[1])
    r = aifund.run_discovery_desk()
    assert submitted == [] and bought == [] and r["buys"] == []   # 결재도 체결도 없음
    obs = db.get_observations("observing")
    assert len(obs) == 1 and obs[0]["code"] == "NVDA" and obs[0]["price_at"] == 100.0
    assert obs[0]["bots"] == "P"


def _obs_env(tmp_path, monkeypatch, price=110.0):
    import db, fetchers, prompts, agents
    monkeypatch.setenv("DB_PATH", str(tmp_path / "rev.db"))
    db.init_db()
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "OBSERVATION_REQUIRED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    monkeypatch.setattr(aifund, "build_research_brief", lambda code, name, tk, m="US": ("현재 패킷", ""))
    monkeypatch.setattr(fetchers, "fetch_stock_price", lambda *a, **k: price)
    monkeypatch.setitem(prompts.AGENT_PROFILES, "P", prompts.AGENT_PROFILES.get("P", {"system": "성장주"}))
    return db, agents


def test_observation_review_convinced_submits_with_story(tmp_path, monkeypatch):
    db, agents = _obs_env(tmp_path, monkeypatch)
    oid = db.add_observation("NVDA", "NVIDIA", "발굴주", "US", "P", "· 성장주 담당 의견 — PEG 매력", "AI 반도체", 100.0)
    db.record_observation_review(oid, {"date": "2026-08-06", "price": 105.0,
                                       "verdicts": {"P": "유지"}, "notes": {"P": "아직 지켜봄"}})  # 1회차 완료 상태
    monkeypatch.setattr(agents, "call_agent",
                        lambda name, sys_, usr, timeout=60, model=None, trim=True:
                        "논지 그대로 강해짐.\n[관찰] 확신 | 이유: 실적 가이던스 상향")
    subs = []
    monkeypatch.setattr(aifund, "_submit_buy_approval",
                        lambda desk, acct, name, code, price, amt, bots, m, **k: (subs.append((code, k.get("reason"))), True)[1])
    r = aifund.run_observation_review()
    assert r["convinced"] == ["NVDA"]
    assert db.get_observations("convinced")            # 상태 전이
    code, story = subs[0]
    assert code == "NVDA" and "재점검" in story and "매수를 건의" in story   # 관찰 서사 첨부


def test_observation_review_all_withdraw_drops(tmp_path, monkeypatch):
    db, agents = _obs_env(tmp_path, monkeypatch)
    db.add_observation("XYZ", "Xyz", "발굴주", "US", "P", "논지", "회사", 50.0)
    monkeypatch.setattr(agents, "call_agent",
                        lambda *a, **k: "[관찰] 철회 | 이유: 마진 급락, 논지 훼손")
    subs = []
    monkeypatch.setattr(aifund, "_submit_buy_approval", lambda *a, **k: (subs.append(1), True)[1])
    r = aifund.run_observation_review()
    assert r["dropped"] == ["XYZ"] and subs == []
    assert db.get_observations("dropped") and db.get_observations("observing") == []


def test_observation_review_conviction_too_early_keeps_observing(tmp_path, monkeypatch):
    # 첫 재점검(1회차)에 확신이어도 최소 횟수(2) 전이면 계속 관찰
    db, agents = _obs_env(tmp_path, monkeypatch)
    db.add_observation("ABC", "Abc", "발굴주", "US", "P", "논지", "회사", 10.0)
    monkeypatch.setattr(agents, "call_agent", lambda *a, **k: "[관찰] 확신 | 이유: 좋아짐")
    subs = []
    monkeypatch.setattr(aifund, "_submit_buy_approval", lambda *a, **k: (subs.append(1), True)[1])
    r = aifund.run_observation_review()
    assert r["convinced"] == [] and subs == []
    obs = db.get_observations("observing")[0]
    assert obs["review_count"] == 1                    # 기록은 남고 계속 관찰


# ── Q 지수 스윙 데스크 ──────────────────────────────────
def _idx_series(trend="up"):
    # 200일선 위 상승추세 + 마지막 봉 신고가 (또는 미러)
    if trend == "up":
        return [100 + i * 0.5 for i in range(260)]                  # 단조 상승 → 항상 신고가
    return [230 - i * 0.5 for i in range(260)]                      # 단조 하락 → 항상 신저가


def test_q_index_signal_long_inverse_none():
    assert aifund.q_index_signal(_idx_series("up")) == "long"       # 200MA 위 + 신고가
    assert aifund.q_index_signal(_idx_series("down")) == "inverse"  # 200MA 아래 + 신저가
    flat = [100.0] * 260
    assert aifund.q_index_signal(flat) is None                      # 돌파 없음(신고가=신저가 동시라 200MA 경계)
    assert aifund.q_index_signal([100] * 50) is None                # 데이터 부족


def test_q_index_exit_mirror():
    up, down = _idx_series("up"), _idx_series("down")
    assert aifund.q_index_exit(down, "long") is True                # 롱: 50MA 이탈 → 청산
    assert aifund.q_index_exit(up, "long") is False
    assert aifund.q_index_exit(up, "inverse") is True               # 인버스: 50MA 회복 → 청산
    assert aifund.q_index_exit(down, "inverse") is False


def test_run_q_index_desk_enters_long_and_inverse(tmp_path, monkeypatch):
    import db, backtest as bt
    monkeypatch.setenv("DB_PATH", str(tmp_path / "qi.db"))
    db.init_db(); db.ensure_desk_accounts()
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "Q_INDEX_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    # QQQ 상승(롱 신호) · SPY 하락(인버스 신호)
    data = {"QQQ": {"close": _idx_series("up")}, "PSQ": {"close": _idx_series("down")},
            "SPY": {"close": _idx_series("down")}, "SH": {"close": _idx_series("up")}}
    monkeypatch.setattr(bt, "_fetch", lambda tickers, period="2y": data)
    r = aifund.run_q_index_desk()
    assert set(r["bought"]) == {"QQQ", "SH"}                        # QQQ 롱 + SPY 인버스(SH)
    pos = db.get_open_positions(account="Q지수")
    assert {p["code"] for p in pos} == {"QQQ", "SH"}
    assert db.get_shared_portfolio("Q지수")["balance"] == 10_000_000 - 2 * aifund.Q_INDEX_SLOT
    # 재실행: 이미 보유 → 중복 진입 없음(멱등)
    r2 = aifund.run_q_index_desk()
    assert r2["bought"] == []


def test_run_q_index_desk_exits_on_reversal(tmp_path, monkeypatch):
    import db, backtest as bt
    monkeypatch.setenv("DB_PATH", str(tmp_path / "qx.db"))
    db.init_db(); db.ensure_desk_accounts()
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "Q_INDEX_ENABLED", True)
    monkeypatch.setattr(aifund, "_narrate", lambda *a, **k: None)
    db.buy_shared_position("QQQ", "QQQ", 200.0, 5_000_000, "Q지수 추세 돌파 롱 (QQQ 기준)", "US", account="Q지수")
    data = {"QQQ": {"close": _idx_series("down")}, "PSQ": {"close": _idx_series("up")},
            "SPY": {"close": [100.0] * 260}, "SH": {"close": [100.0] * 260}}
    monkeypatch.setattr(bt, "_fetch", lambda tickers, period="2y": data)
    r = aifund.run_q_index_desk()
    assert "QQQ" in r["sold"]                                       # 50MA 이탈 → 롱 청산
    assert db.get_open_positions(account="Q지수") == [] or \
        all(p["code"] != "QQQ" for p in db.get_open_positions(account="Q지수"))


def test_run_q_index_desk_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(aifund, "Q_INDEX_ENABLED", False)
    assert aifund.run_q_index_desk() == {"bought": [], "sold": []}


def test_narrate_macro_context_reuses_today_briefing(tmp_path, monkeypatch):
    import db
    monkeypatch.setenv("DB_PATH", str(tmp_path / "mc.db"))
    db.init_db()
    said = []
    monkeypatch.setattr(aifund, "_narrate", lambda b, c, model="rule": said.append((b, c)))
    aifund._narrate_macro_context()                                  # 브리핑 없음 → 침묵
    assert said == []
    db.save_macro_briefing(aifund._today_kst(), "시장은 유동성 대기 국면입니다.\n상세...")
    aifund._narrate_macro_context()                                  # 오늘 브리핑 요지 재사용(LLM 0콜)
    assert said and said[0][0] == "M" and "유동성 대기" in said[0][1]
    db.save_macro_briefing("2000-01-01", "옛날 브리핑")               # 오늘자 아니면 침묵 유지


def test_run_risk_review_digest_and_narration(tmp_path, monkeypatch):
    import db, agents, prompts
    monkeypatch.setenv("DB_PATH", str(tmp_path / "risk.db"))
    db.init_db(); db.ensure_desk_accounts()
    db.buy_shared_position("엔비디아", "NVDA", 100.0, 5_000_000, "테스트", "US", account="대형주")
    monkeypatch.setattr(aifund, "NEW_DESK_ENABLED", True)
    monkeypatch.setattr(aifund, "RISK_OFFICER_ENABLED", True)
    monkeypatch.setitem(prompts.AGENT_PROFILES, "R", {"system": "리스크 오피서"})
    said, cap = [], {}
    monkeypatch.setattr(aifund, "_narrate", lambda b, c, model="rule": said.append((b, c)))
    monkeypatch.setattr(agents, "call_agent",
                        lambda name, sys_, usr, timeout=60, model=None, trim=True:
                        (cap.update(p=usr), "집중도가 높습니다. 현금 완충은 충분합니다.")[1])
    r = aifund.run_risk_review()
    assert r["chars"] > 0
    assert "엔비디아" in cap["p"] and "집중도 상위" in cap["p"]      # 다이제스트에 보유·집중도 포함
    assert said and said[0][0] == "R" and "리스크 점검" in said[0][1]


def test_run_risk_review_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(aifund, "RISK_OFFICER_ENABLED", False)
    assert aifund.run_risk_review()["skipped"] == "disabled"


def test_pub_letter_includes_R():
    assert aifund.pub_letter("R") == "E"


# ── P0 성과 계측 (decision_log · 주간 리포트 · 헬스) ──────────
def test_analyze_stock_logs_decision(tmp_path, monkeypatch):
    import db, agents, prompts
    monkeypatch.setenv("DB_PATH", str(tmp_path / "dl.db"))
    db.init_db()
    monkeypatch.setitem(prompts.AGENT_PROFILES, "P", {"system": "성장주 페르소나"})
    monkeypatch.setattr(agents, "call_agent",
                        lambda *a, **k: "PEG 매력적.\n[결정] 매수 | 이유: 성장 지속")
    v, rz = aifund.analyze_stock("NVDA", "NVIDIA", "NVDA", "P", brief=("입력패킷", "사업"))
    assert v == "매수"
    logs = db.get_decision_logs()
    assert len(logs) == 1
    L = logs[0]
    assert (L["bot"], L["code"], L["verdict"], L["source"]) == ("P", "NVDA", "매수", "분석")
    assert L["packet"] == "입력패킷" and len(L["persona_hash"]) == 10   # 재현성: 입력+버전


def test_log_decision_never_raises(monkeypatch):
    # DB가 깨져도 매매 로직에 예외 전파 금지
    import db
    monkeypatch.setattr(db, "add_decision_log", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    aifund.log_decision("분석", "P", "X", "X", "매수", "r")   # 예외 없이 통과해야


def test_ret_after_computes_forward_return():
    dates = [f"2026-08-{d:02d}" for d in range(1, 15)]
    closes = [100 + i for i in range(14)]
    r = aifund._ret_after(dates, closes, "2026-08-03", ndays=5)
    assert abs(r - (107 / 102 - 1)) < 1e-9                      # 판단일 첫 종가 102 → 5거래일 뒤 107
    assert aifund._ret_after(dates, closes, "2026-08-12", ndays=5) is None   # 미래 데이터 부족
    assert aifund._ret_after(dates, closes, "2026-09-01", ndays=5) is None   # 판단일 이후 데이터 없음


def test_run_weekly_report_grades_and_saves(tmp_path, monkeypatch):
    import db, notifier
    monkeypatch.setenv("DB_PATH", str(tmp_path / "wr.db"))
    db.init_db()
    # 8일 전 판단 2건(매수 적중 P · 관망 기회비용 W)
    from datetime import datetime, timezone, timedelta
    old = (datetime.now(timezone(timedelta(hours=9))) - timedelta(days=8)).strftime("%Y-%m-%d")
    db.add_decision_log(old, "분석", "P", "AAA", "A사", "매수", "r", "pkt", "h", "sonnet")
    db.add_decision_log(old, "분석", "W", "AAA", "A사", "관망", "r", "pkt", "h", "sonnet")
    dates = [(datetime.now(timezone(timedelta(hours=9))) - timedelta(days=10 - i)).strftime("%Y-%m-%d") for i in range(10)]
    monkeypatch.setattr(aifund, "_fetch_closes_dated",
                        lambda codes, period="3mo": {"AAA": (dates, [100, 101, 102, 103, 104, 105, 106, 107, 108, 110])})
    monkeypatch.setattr(notifier, "notify", lambda *a, **k: None)
    r = aifund.run_weekly_report()
    assert r["graded"] == 2
    rep = db.get_latest_weekly_report()
    assert "봇별 판단 채점" in rep["content"] and "성장주(P) 매수" in rep["content"]
    assert "적중↑" in rep["content"]                             # 매수 후 상승 = 적중


def test_record_fetch_counts_and_returns_today(tmp_path, monkeypatch):
    import db
    monkeypatch.setenv("DB_PATH", str(tmp_path / "fh.db"))
    db.init_db()
    assert db.record_fetch("price", True) == (1, 0)
    assert db.record_fetch("price", False) == (1, 1)
    assert db.record_fetch("price", False) == (1, 2)
    rows = db.get_fetch_health()
    assert rows[0]["ok"] == 1 and rows[0]["fail"] == 2
