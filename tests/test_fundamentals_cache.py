"""C3 — 재무 last-good 캐시 폴백(API 리밋에도 P/W/H가 재무 확보)."""
import aifund


def test_caches_fresh_fundamentals(monkeypatch):
    saved = {}
    monkeypatch.setattr(aifund, "_today_kst", lambda: "2026-08-02")
    import fetchers
    monkeypatch.setattr(fetchers, "fetch_stock_data",
                        lambda code, tk, name, market="US": {"name": name, "code": code, "per": 30.0, "peg": 1.2})
    import db
    monkeypatch.setattr(db, "save_fundamentals_cache", lambda code, date, data: saved.update({code: data}))
    monkeypatch.setattr(db, "get_fundamentals_cache", lambda code: None)
    data = aifund._fetch_brief_data("NVDA", "NVDA", "엔비디아")
    assert data["per"] == 30.0
    assert saved["NVDA"] == {"per": 30.0, "peg": 1.2}      # 재무 확보 → 캐시 저장


def test_falls_back_to_cache_on_ratelimit(monkeypatch):
    monkeypatch.setattr(aifund, "_today_kst", lambda: "2026-08-02")
    import fetchers
    # 리밋: 재무 없이 플래그만
    monkeypatch.setattr(fetchers, "fetch_stock_data",
                        lambda code, tk, name, market="US": {"name": name, "code": code, "_data_unavailable": True})
    import db
    monkeypatch.setattr(db, "save_fundamentals_cache", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_fundamentals_cache", lambda code: {"per": 28.0, "peg": 1.1})
    data = aifund._fetch_brief_data("NVDA", "NVDA", "엔비디아")
    assert data["per"] == 28.0 and data["peg"] == 1.1       # last-good로 채워짐
    assert data.get("_fund_cached") is True
    assert "_data_unavailable" not in data                  # 폴백 성공 시 실패플래그 제거


def test_no_cache_no_fresh_stays_unavailable(monkeypatch):
    monkeypatch.setattr(aifund, "_today_kst", lambda: "2026-08-02")
    import fetchers
    monkeypatch.setattr(fetchers, "fetch_stock_data",
                        lambda code, tk, name, market="US": {"name": name, "code": code, "_data_unavailable": True})
    import db
    monkeypatch.setattr(db, "save_fundamentals_cache", lambda *a, **k: None)
    monkeypatch.setattr(db, "get_fundamentals_cache", lambda code: None)
    data = aifund._fetch_brief_data("ZZZ", "ZZZ", "무명")
    assert data.get("_data_unavailable") is True            # 캐시도 없으면 정직하게 실패 표시
