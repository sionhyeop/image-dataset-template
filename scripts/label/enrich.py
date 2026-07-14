"""인물 속성 라벨 백필 (성별/인원수/표정).

master_metadata.csv 의 각 이미지에 대해 AI 로 인물 속성을 추정해 채운다:
  - person_count : YOLOv8n 사람 검출 수 (1→N01, 2→N02, 3+→N03)
  - gender       : CLIP zero-shot (여성/남성/혼성/불명)
  - expression   : CLIP zero-shot (웃음/무표정/기타)
이후 사람이 검수 갤러리에서 수정한다. 이미 값이 있는 행은 스킵(재개 가능).

사용:
    python enrich.py                # 비어 있는 행만
    python enrich.py --force        # 전체 다시
    python enrich.py --status approved
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402


def count_codes() -> dict:
    """개수 → 코드 매핑. taxonomy._semantic.count 가 선언한다.

    코드 문자열을 파이썬에 박아두면 다른 도메인에서 미정의 코드를 CSV 에 써넣게 된다.
    """
    c = common.load_semantic().get("count") or {}
    return {1: c.get("single", ""), 2: c.get("pair", ""), 3: c.get("many", "")}


def count_persons(det, path: Path) -> str:
    """주 피사체 개수 → taxonomy 코드. 검출기가 없으면 빈 값."""
    if det is None:
        return ""
    n = det.count(path)
    if not n:
        return ""
    return count_codes().get(min(n, 3), "")


def main() -> int:
    common.load_env()
    ap = argparse.ArgumentParser(description="인물 속성 백필")
    ap.add_argument("--force", action="store_true", help="이미 값 있는 행도 다시")
    ap.add_argument("--status", help="특정 status 만")
    args = ap.parse_args()

    # 인물 도메인이 아니면 할 일이 없다 — **에러가 아니라 정상 스킵**이다.
    # (taxonomy._semantic 에 facing/framing/count 가 없으면 인물 개념이 없는 도메인)
    if not common.has_capability("person"):
        print("인물 도메인이 아닙니다 (taxonomy._semantic 에 facing/framing/count 없음) — 건너뜁니다.")
        return 0

    rows = common.read_rows(common.MASTER_META)
    if not rows:
        print("master_metadata.csv 가 비어 있습니다.")
        return 1

    try:
        from clip_label import ClipLabeler
        clip = ClipLabeler()
    except Exception as e:
        print(f"CLIP 로드 실패: {e}")
        return 1
    from detect import Detector
    det = Detector()

    # CLIP 이 채울 축 = 'derived' 모드 중 prompts.yaml 에 프롬프트가 있는 것.
    # (person_count 는 프롬프트가 없다 — 객체검출이 채운다. 아래 count_axis 참조)
    clip_axes = [a for a in common.LABEL_AXES
                 if common.AXIS_MODE.get(a) == "derived" and a in clip.axes]
    # 개수 축은 taxonomy._semantic.count 가 지목한다 (없으면 개수 백필을 건너뛴다)
    count_axis = ((common.load_semantic().get("count") or {}).get("axis"))
    probe = clip_axes[0] if clip_axes else count_axis
    if not probe:
        print("백필할 derived 축이 없습니다 — 건너뜁니다.")
        return 0
    print(f"백필 축: CLIP {clip_axes} · 객체검출 {count_axis or '없음'}")

    todo = [r for r in rows
            if (args.force or not r.get(probe))
            and (not args.status or r.get("status") == args.status)
            and (common.image_path(r)).exists()]
    print(f"백필 대상 {len(todo)}장 (전체 {len(rows)})...")

    done = 0
    for r in todo:
        path = common.image_path(r)
        if clip_axes:
            attrs = clip.classify_axes(path, clip_axes)
            if attrs:
                r.update(attrs)
        if count_axis:
            r[count_axis] = count_persons(det, path)
        done += 1
        if done % 100 == 0:
            print(f"  ...{done}장")

    common.backup_csv(common.MASTER_META)
    common.write_rows(common.MASTER_META, common.MASTER_FIELDS, rows)
    print(f"완료: {done}장 인물 속성 채움 → {common.rel(common.MASTER_META)}")
    print("다음: 검수 앱(app.py)에서 사람이 수정")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
