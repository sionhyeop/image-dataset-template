"""train/val/test split 생성 (가이드 9절).

approved 이미지를 WHERE 기준 stratified 로 80/10/10 분할한다.
같은 pHash 그룹(변형본)은 반드시 같은 split 에 들어가 데이터 누수를 막는다.
file_path 는 processed 768 경로를 가리키게 한다(없으면 curated 원본).

랜덤이 아니라 phash 문자열 해시로 결정적으로 배분한다(재실행 시 동일 결과).

사용:
    python make_splits.py                       # 80/10/10, 768px
    python make_splits.py --size 512 --val 0.15 --test 0.15
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

# 분할 CSV 의 컬럼도 taxonomy 가 정한다. 리터럴로 박아두면 새 도메인의 학습 분할에
# 라벨이 통째로 빠진 채 저장된다(파일은 멀쩡히 만들어지므로 알아채기 어렵다).
SPLIT_FIELDS = ["image_id", "file_path", *common.LABEL_AXES]


def group_key(row: dict) -> str:
    """변형본 묶음 키. phash 있으면 phash, 없으면 image_id."""
    return row.get("phash") or row.get("image_id")


def bucket(key: str, val_ratio: float, test_ratio: float) -> str:
    """결정적 해시로 train/val/test 배정 (같은 key -> 항상 같은 split)."""
    hv = int(hashlib.md5(key.encode()).hexdigest(), 16) % 10000 / 10000.0
    if hv < test_ratio:
        return "test"
    if hv < test_ratio + val_ratio:
        return "val"
    return "train"


def main() -> int:
    ap = argparse.ArgumentParser(description="train/val/test split 생성")
    ap.add_argument("--size", type=int, default=768, help="processed 크기 폴더")
    ap.add_argument("--val", type=float, default=0.10)
    ap.add_argument("--test", type=float, default=0.10)
    args = ap.parse_args()

    rows = common.read_rows(common.MASTER_META)
    approved = [r for r in rows if r.get("status") == "approved"]
    if not approved:
        print("approved 이미지가 없습니다. promote.py 를 먼저 실행하세요.")
        return 1

    # 그룹 단위로 split 배정 (같은 그룹 = 같은 bucket)
    group_bucket: dict[str, str] = {}
    for r in approved:
        g = group_key(r)
        if g not in group_bucket:
            group_bucket[g] = bucket(g, args.val, args.test)

    splits = defaultdict(list)
    processed_dir = common.PROCESSED / str(args.size)
    for r in approved:
        name = Path(r["file_path"]).name
        proc = processed_dir / name
        fp = proc.relative_to(common.DATASET_ROOT) if proc.exists() else Path(r["file_path"])
        splits[group_bucket[group_key(r)]].append({
            "image_id": r["image_id"],
            "file_path": str(fp),
            **{ax: r.get(ax, "") for ax in common.LABEL_AXES},
        })

    for name in ("train", "val", "test"):
        out = common.SPLITS / f"{name}.csv"
        common.write_rows(out, SPLIT_FIELDS, splits.get(name, []))
        print(f"  {name}: {len(splits.get(name, []))}장 -> {common.rel(out)}")

    total = sum(len(v) for v in splits.values())
    print(f"총 {total}장 분할 완료 (그룹 {len(group_bucket)}개 기준 누수 방지)")
    print("다음 단계: python scripts/check/validate.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
