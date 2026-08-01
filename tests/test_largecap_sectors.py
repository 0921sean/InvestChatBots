"""C1 — 섹터 태그 + [결정] X | 이유: 포맷 정규화."""
import aifund


def test_sector_of_and_universe_consistency():
    secs = aifund._largecap_sectors()
    assert secs, "섹터 맵이 비어있으면 안 됨(private or example)"
    uni = aifund._largecap_universe()
    # 평탄화 = 유니버스와 일치
    flat = [t for codes in secs.values() for t in codes]
    assert flat == uni
    # 각 코드의 섹터 역조회
    first = uni[0]
    assert aifund.sector_of(first) in secs
    assert aifund.sector_of("ZZZZ_없음") == "기타"


def test_decision_line_format():
    assert aifund._decision_line("관망", "고평가") == "[결정] 관망 | 이유: 고평가"
    assert aifund._decision_line("매수", "") == "[결정] 매수 | 이유: 근거 미확보"


def test_format_stock_verdict_extracts_existing():
    rz = "PEG가 높다.\n[결정] 매도 | 이유: PEG 4.6 고평가"
    assert aifund.format_stock_verdict(rz, "매도") == "[결정] 매도 | 이유: PEG 4.6 고평가"


def test_format_stock_verdict_synthesizes_when_missing():
    rz = "마진이 정체되어 성장 신뢰도가 낮다\n[결정] 관망"
    out = aifund.format_stock_verdict(rz, "관망")
    assert out.startswith("[결정] 관망 | 이유:")
    assert "성장 신뢰도" in out


def test_format_stock_verdict_empty_reasoning():
    assert aifund.format_stock_verdict("", "관망") == "[결정] 관망 | 이유: 근거 미확보"
