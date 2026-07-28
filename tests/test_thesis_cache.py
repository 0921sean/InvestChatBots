"""AI펀드 피봇 1단계 — 종목별 논지 캐시 검증 (설계 docs/AIFUND_PIVOT.md)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    import importlib
    import db as _db
    importlib.reload(_db)
    _db.init_db()
    return _db


def test_save_and_get_thesis(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    db.save_thesis("NVDA", "P", "매수", "PEG 매력·성장 초입", catalyst_key="2026-08-20")
    row = db.get_thesis("NVDA", "P")
    assert row["verdict"] == "매수" and "PEG" in row["reasoning"]
    assert row["catalyst_key"] == "2026-08-20"
    assert db.get_thesis("NVDA", "W") is None      # 아직 W 논지 없음


def test_upsert_overwrites(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    db.save_thesis("MU", "S", "관망", "밴드 안")
    db.save_thesis("MU", "S", "매수", "병목 수혜 확인")   # 재분석 → 덮어씀
    assert db.get_thesis("MU", "S")["verdict"] == "매수"
    assert len(db.get_thesis("MU")) == 1                  # (code,bot) 유니크


def test_cached_codes_and_invalidate(tmp_path, monkeypatch):
    db = _fresh_db(tmp_path, monkeypatch)
    db.save_thesis("AAPL", "P", "관망", "x")
    db.save_thesis("AAPL", "W", "매수", "해자")
    db.save_thesis("COHR", "S", "매수", "광통신")
    assert db.get_cached_codes() == {"AAPL", "COHR"}
    assert len(db.get_thesis("AAPL")) == 2                # P·W 둘 다
    db.invalidate_thesis("AAPL")                          # 카탈리스트 → 무효화
    assert db.get_cached_codes() == {"COHR"}
    assert db.get_thesis("AAPL") == {}
