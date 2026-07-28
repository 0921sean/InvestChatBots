"""장기 위원회 5봇 개편 검증 — 명세 §3."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── 로스터 (프롬프트) ────────────────────────────────────────
def test_roster_is_five_committee():
    import prompts
    assert prompts.AGENT_ORDER == ["실적왕", "드가자", "INTJ", "차트천재", "역추세봇"]
    assert len(prompts.AGENT_ORDER) == 5

def test_removed_bots_not_in_roster_but_profiles_kept():
    import prompts
    for b in ("퀀트중독자", "기본농부"):
        assert b not in prompts.AGENT_ORDER
        assert b in prompts.AGENT_PROFILES        # 프로필 보존
    assert "역추세봇" in prompts.AGENT_PROFILES     # 빅픽처 개명
    assert "빅픽처" not in prompts.AGENT_ORDER

def test_peterlynch_prompt_has_garp_keys():
    import prompts
    sys_txt = prompts.AGENT_PROFILES["실적왕"]["system"]
    for kw in ("PEG", "GARP", "판단 보류", "고성장"):
        assert kw in sys_txt


# ── 투표 상수 · 과반 (오케스트레이터) ────────────────────────
def test_majority_constants():
    import orchestrator as o
    assert o.BUY_MAJORITY == 3 and o.SELL_MAJORITY == 3
    assert set(o.BUY_CONVICTION_TIERS) == {3, 4, 5}
    # 2026-07-27 플랫 티어(절대금액): 3표=500만 / 4표=1000만 / 5표=1500만
    assert o.BUY_CONVICTION_TIERS == {3: 5_000_000, 4: 10_000_000, 5: 15_000_000}

def test_tally_buy_needs_majority():
    import orchestrator as o
    buy3 = [("매수", 0)] * 3 + [("관망", 0)] * 2
    assert o._tally_votes(buy3)[0] == "매수"
    buy2 = [("매수", 0)] * 2 + [("관망", 0)] * 3
    assert o._tally_votes(buy2)[0] == "관망"

def test_tally_sell_needs_majority():
    import orchestrator as o
    sell3 = [("매도", 0)] * 3 + [("관망", 0)] * 2
    assert o._tally_votes(sell3)[0] == "매도"


# ── 규칙 기술봇 결정론적 참여 ────────────────────────────────
def test_rule_bot_momentum_buy_signal(monkeypatch):
    import orchestrator as o
    import trading_strategies as ts
    monkeypatch.setattr(ts, "momentum_signal", lambda c: "A")
    monkeypatch.setattr(ts, "rsi", lambda c, period=ts.RSI_PERIOD: 60.0)
    msg, dec = o._rule_bot_message("차트천재", ([1.0] * 40, [1.0] * 40, 1.0), "buy")
    assert dec == "매수" and "[결정] 매수" in msg

def test_rule_bot_momentum_no_signal(monkeypatch):
    import orchestrator as o
    import trading_strategies as ts
    monkeypatch.setattr(ts, "momentum_signal", lambda c: None)
    _msg, dec = o._rule_bot_message("차트천재", ([1.0] * 40, [1.0] * 40, 1.0), "buy")
    assert dec == "관망"

def test_rule_bot_meanrev_sell(monkeypatch):
    import orchestrator as o
    import trading_strategies as ts
    monkeypatch.setattr(ts, "meanrev_exit", lambda c: True)
    msg, dec = o._rule_bot_message("역추세봇", ([1.0] * 40, [1.0] * 40, 1.0), "sell")
    assert dec == "매도" and "[결정] 매도" in msg

def test_rule_bot_no_data_holds():
    import orchestrator as o
    msg, dec = o._rule_bot_message("차트천재", None, "buy")
    assert dec == "관망" and "[결정] 관망" in msg   # 대화체 전환 후 안정 문구


# ── PEG 데이터 주입 (fetchers) ───────────────────────────────
def test_peg_injected_when_present():
    import fetchers
    block = fetchers.format_stock_data(
        {"name": "테스트", "code": "000000", "market": "US", "price": 100,
         "peg": 0.8, "eps_growth": 0.3})
    assert "PEG: 0.80 (매력)" in block
    assert "EPS성장률: +30%" in block

def test_us_eps_uses_dollar_unit():
    import fetchers
    block = fetchers.format_stock_data(
        {"name": "NVDA", "code": "NVDA", "market": "US", "price": 200, "eps": 7.0})
    assert "EPS: $7.00" in block      # US는 $ 단위(원 아님)

def test_peg_marked_missing_when_absent():
    import fetchers
    block = fetchers.format_stock_data(
        {"name": "테스트", "code": "000000", "market": "US", "price": 100})
    assert "PEG: 데이터 없음" in block
