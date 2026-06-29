"""방문 통계 ?s= 소스 처리 검증.

- 화이트리스트 없음: 임의 태그(ig_icb 등)도 그대로 source 버킷으로 집계.
- 정규화: 소문자·허용문자([a-z0-9_-])·최대 32자. 오염/인젝션 문자 제거. 비면 'direct'.
"""
import os
import pytest
from db import init_db, _normalize_source, log_visit, get_visit_stats

TEST_DB = "test_visit_source.db"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setenv("DB_PATH", TEST_DB)
    init_db()
    yield
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# ── 정규화 ────────────────────────────────────────────────
def test_normalize_arbitrary_tag_passthrough():
    assert _normalize_source("ig_icb") == "ig_icb"      # 임의 신규 태그 OK
    assert _normalize_source("insta") == "insta"
    assert _normalize_source("mail") == "mail"


def test_normalize_lowercases_and_strips():
    assert _normalize_source("IG_ICB") == "ig_icb"      # 소문자화
    assert _normalize_source("  Kakao  ") == "kakao"    # 공백 trim


def test_normalize_removes_unsafe_chars():
    assert _normalize_source("ig icb!") == "igicb"             # 공백·! 제거
    assert _normalize_source("<script>alert(1)</script>") == "scriptalert1script"  # 인젝션 문자 제거
    assert "<" not in _normalize_source("<b>x</b>")


def test_normalize_length_limit_and_empty():
    assert len(_normalize_source("a" * 100)) == 32
    assert _normalize_source("") == "direct"
    assert _normalize_source(None) == "direct"
    assert _normalize_source("!!!@@@") == "direct"      # 전부 불허문자 → direct


# ── 집계 (어드버서리얼: 코드 수정 없이 새 태그 집계) ──────────
def test_arbitrary_tag_buckets_in_stats():
    assert log_visit(source="ig_icb", visitor_id="v1", user_agent=UA, verified=1) is True
    stats = get_visit_stats(days=30, verified_only=True)
    sources = {r["source"]: r for r in stats["by_source"]}
    assert "ig_icb" in sources, f"ig_icb 버킷 없음: {list(sources)}"
    assert sources["ig_icb"]["uv"] >= 1


def test_dirty_tag_normalized_into_same_bucket():
    # 'IG_ICB!'와 'ig_icb'는 정규화 후 같은 버킷
    log_visit(source="ig_icb", visitor_id="v1", user_agent=UA, verified=1)
    log_visit(source="IG_ICB!", visitor_id="v2", user_agent=UA, verified=1)
    stats = get_visit_stats(days=30, verified_only=True)
    sources = {r["source"]: r for r in stats["by_source"]}
    assert set(sources) == {"ig_icb"}, f"버킷 분리됨: {list(sources)}"
    assert sources["ig_icb"]["uv"] == 2
