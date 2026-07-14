"""common.py — 원자적 쓰기·백업·멀티라벨 분리."""
import common


def test_split_codes_multi():
    """멀티/단일 축은 **taxonomy 가 정한다.** 테스트가 축 이름을 알면 안 된다.

    예전엔 'where'·'gender' 를 박아뒀는데, 그 축이 없는 도메인을 기본값으로 두면
    이 테스트가 깨진다 — 코드가 아니라 테스트가 도메인에 묶여 있던 것이다.
    """
    multi = next(iter(common.MULTI_AXES))
    single = next(a for a in common.LABEL_AXES if a not in common.MULTI_AXES)

    assert common.split_codes(multi, "A;B") == ["A", "B"]
    assert common.split_codes(multi, " A ; ") == ["A"]
    assert common.split_codes(single, "A;B") == ["A;B"]   # 단일축은 안 쪼갠다
    assert common.split_codes(single, "A") == ["A"]
    assert common.split_codes(single, "") == []
    assert common.split_codes(multi, None) == []
    assert common.split_codes(multi, "nan") == []         # pandas 문자열화 방어


def test_write_rows_atomic(tmp_path):
    p = tmp_path / "t.csv"
    common.write_rows(p, ["a", "b"], [{"a": "1", "b": "2"}])
    assert common.read_rows(p) == [{"a": "1", "b": "2"}]
    assert not p.with_suffix(".csv.tmp").exists()


def test_write_rows_crash_preserves_original(tmp_path):
    p = tmp_path / "t.csv"
    common.write_rows(p, ["a"], [{"a": "1"}])
    orig = p.read_text()
    try:
        common.write_rows(p, ["a"], [{"a": "2"}, None])   # None 행 → 도중 예외
    except Exception:
        pass
    assert p.read_text() == orig


def test_backup_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "BACKUPS", tmp_path / "_backups")
    p = tmp_path / "m.csv"
    p.write_text("a\n1\n")
    bak = common.backup_csv(p)
    assert bak.exists() and bak.read_text() == "a\n1\n"
    assert common.backup_csv(tmp_path / "none.csv") is None


def test_write_json_atomic(tmp_path):
    p = tmp_path / "o.json"
    common.write_json_atomic(p, '{"x":1}')
    assert p.read_text() == '{"x":1}'
    assert not p.with_suffix(".json.tmp").exists()
