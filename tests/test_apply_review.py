"""apply_review.py — 검수 병합 시맨틱 (멀티축·부분무효·discard·멱등)."""
import sys

import common
from review import apply_review


def _setup(tmp_path, monkeypatch, decisions_rows):
    """임시 master + review_decisions 를 만들고 apply_review.main 실행 준비."""
    master = tmp_path / "master.csv"
    monkeypatch.setattr(common, "MASTER_META", master)
    monkeypatch.setattr(common, "BACKUPS", tmp_path / "_backups")
    common.write_rows(master, common.MASTER_FIELDS, [
        {"image_id": "raw_1", "where": "W01_cafe", "pose_action": "P01_sitting",
         "status": "approved"},
        {"image_id": "raw_2", "where": "W02_beach_sea", "status": "approved"},
    ])
    dec = tmp_path / "review_decisions.csv"
    fields = ["image_id"] + common.LABEL_AXES + ["decision", "status", "reviewer", "reviewed_at"]
    common.write_rows(dec, fields, decisions_rows)
    return master, dec


def _run(dec, dry=False):
    argv = ["apply_review.py", str(dec)] + (["--dry-run"] if dry else [])
    sys.argv = argv
    assert apply_review.main() == 0


def test_multi_label_and_discard(tmp_path, monkeypatch):
    master, dec = _setup(tmp_path, monkeypatch, [
        {"image_id": "raw_1", "where": "W01_cafe;W07_stairs", "decision": "keep",
         "reviewer": "T", "reviewed_at": "2026-07-10"},
        {"image_id": "raw_2", "decision": "discard", "reviewer": "T"},
    ])
    _run(dec)
    rows = {r["image_id"]: r for r in common.read_rows(master)}
    assert rows["raw_1"]["where"] == "W01_cafe;W07_stairs"
    assert rows["raw_1"]["reviewed"] == "true" and rows["raw_1"]["status"] == "approved"
    assert rows["raw_2"]["status"] == "discarded" and rows["raw_2"]["reviewed"] == "true"


def test_partial_invalid_code_keeps_valid(tmp_path, monkeypatch, capsys):
    master, dec = _setup(tmp_path, monkeypatch, [
        {"image_id": "raw_1", "where": "W07_stairs;W99_bogus", "decision": "keep"},
    ])
    _run(dec)
    rows = {r["image_id"]: r for r in common.read_rows(master)}
    assert rows["raw_1"]["where"] == "W07_stairs"        # 유효분만 반영
    assert "W99_bogus" in capsys.readouterr().out        # 무효분은 경고


def test_all_invalid_leaves_field(tmp_path, monkeypatch):
    master, dec = _setup(tmp_path, monkeypatch, [
        {"image_id": "raw_1", "where": "W99_bogus", "decision": "keep"},
    ])
    _run(dec)
    rows = {r["image_id"]: r for r in common.read_rows(master)}
    assert rows["raw_1"]["where"] == "W01_cafe"          # 기존 값 유지


def test_idempotent(tmp_path, monkeypatch):
    master, dec = _setup(tmp_path, monkeypatch, [
        {"image_id": "raw_1", "where": "W07_stairs", "decision": "keep",
         "reviewer": "T", "reviewed_at": "2026-07-10"},
    ])
    _run(dec)
    once = master.read_text()
    _run(dec)
    assert master.read_text() == once


def test_dry_run_no_write(tmp_path, monkeypatch):
    master, dec = _setup(tmp_path, monkeypatch, [
        {"image_id": "raw_1", "where": "W07_stairs", "decision": "keep"},
    ])
    before = master.read_text()
    _run(dec, dry=True)
    assert master.read_text() == before
