"""main.py 공개 API 응답 검증."""
import pytest


def test_api_positions_excludes_experimental(tmp_path, monkeypatch):
    # #245 반전봇 등 실험 계좌는 공개 포지션 API에 나오면 안 된다
    import db, main
    monkeypatch.setenv("DB_PATH", str(tmp_path / "pos.db"))
    db.init_db(); db.ensure_desk_accounts()
    db.buy_shared_position("공개종목", "PUB", 100.0, 1_000_000, "테스트", "US", account="대형주")
    db.buy_shared_position("실험종목", "EXP", 100.0, 1_000_000, "[반전T+1] 테스트", "US", account="반전봇")
    rows = main.api_positions()
    accounts = {r.get("account") for r in rows}
    assert "대형주" in accounts and "반전봇" not in accounts
