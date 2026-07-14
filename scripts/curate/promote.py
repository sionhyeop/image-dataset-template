"""검수 통과 이미지를 curated 로 승격 (가이드 11절 5단계).

master_metadata.csv 에서 status 가 approved 인 이미지를
data/01_curated/images/img_NNNNNN.jpg 로 복사하고 file_path 를 갱신한다.
--auto 를 주면 needs_review 중 quality_score >= 3 도 자동 승인한다.

image_id(raw_000001)는 그대로 두고, curated 파일명만 img_000001 로 맞춘다.

사용:
    python promote.py            # status=approved 만
    python promote.py --auto     # + needs_review & quality>=3 자동 승인
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402


def next_img_index() -> int:
    existing = list(common.CURATED_IMAGES.glob("img_*.jpg"))
    if not existing:
        return 1
    nums = [int(p.stem.split("_")[1]) for p in existing if p.stem.split("_")[1].isdigit()]
    return (max(nums) + 1) if nums else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="curated 승격")
    ap.add_argument("--auto", action="store_true",
                    help="needs_review 중 quality_score>=3 도 승인")
    args = ap.parse_args()

    rows = common.read_rows(common.MASTER_META)
    if not rows:
        print("master_metadata.csv 가 비어 있습니다. 먼저 auto_label.py 를 실행하세요.")
        return 1

    common.CURATED_IMAGES.mkdir(parents=True, exist_ok=True)
    idx = next_img_index()
    n_promoted = n_skip = 0

    for row in rows:
        # 이미 curated 로 옮겨진 것 (file_path 가 01_curated 를 가리킴) 은 스킵
        if "01_curated/images" in row.get("file_path", ""):
            n_skip += 1
            continue

        status = row.get("status", "")
        approve = status == "approved"
        if args.auto and status == "needs_review":
            try:
                approve = int(row.get("quality_score") or 0) >= 3
            except ValueError:
                approve = False
        if not approve:
            continue

        src = common.image_path(row)
        if not src.exists():
            print(f"  원본 없음, 스킵: {row['file_path']}")
            continue

        dest = common.CURATED_IMAGES / f"img_{idx:06d}.jpg"
        while dest.exists():
            idx += 1
            dest = common.CURATED_IMAGES / f"img_{idx:06d}.jpg"
        shutil.copy2(src, dest)
        row["file_path"] = str(dest.relative_to(common.DATASET_ROOT))
        row["status"] = "approved"
        idx += 1
        n_promoted += 1

    common.backup_csv(common.MASTER_META)
    common.write_rows(common.MASTER_META, common.MASTER_FIELDS, rows)
    print(f"승격: {n_promoted}장 -> data/01_curated/images/ | 기존 스킵: {n_skip}")
    print("다음 단계: python scripts/curate/resize.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
