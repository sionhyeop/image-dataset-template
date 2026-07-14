"""통합 무결성 — 정합성 CLI·디스크립터 셀프테스트를 테스트로 래핑."""
from check import check_consistency
from index.descriptor import pose_descriptor


def test_descriptor_self_test():
    """21차원 디스크립터 불변성·golden 파리티 (스크립트 내장 셀프테스트)."""
    pose_descriptor._self_test()


def test_check_consistency_passes():
    """taxonomy↔프롬프트↔앱↔CSV↔parquet↔JSON↔파일수량 전 항목 통과."""
    check_consistency.FAILS.clear()
    check_consistency.WARNS.clear()
    assert check_consistency.main() == 0
