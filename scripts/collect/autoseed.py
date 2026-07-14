"""자동 시드 확보 (방식 B 부트스트랩).

희소 축을 검색 수집한 결과(raw_metadata)에서 각 카테고리의 대표 핀 URL 을 골라
<데이터셋>/seeds.csv 에 추가한다. 이후 seeds.py 로 연관핀을 확장하면,
사람이 시드를 일일이 고르지 않아도 관련도 높은 이미지를 늘릴 수 있다.

사용:
    python autoseed.py                       # 기본 희소 타깃
    python autoseed.py --categories P04_jumping S01_low_angle
    python autoseed.py --per 4 --count 50    # 카테고리당 시드 4개, 시드당 연관 50
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

# 시드 목록은 도메인 데이터다 — 코드가 아니라 데이터셋에 산다.
SEED_CSV = common.DATASET_ROOT / "seeds.csv"
SEED_HEADER = ["axis", "category_id", "seed_pin_url", "related_count"]

# 기본 희소 타깃 (검색으로 잘 안 모이는 축)
DEFAULT_TARGETS = [
    "P04_jumping", "P11_lying", "P10_standing",
    "S12_silhouette", "S14_front_view",
    "S01_low_angle", "S03_high_angle", "S04_backlight_silhouette",
    "S10_back_view", "S11_side_view",
]

PREFIX_AXIS = {"P": "POSE", "F": "FRAME", "S": "STYLE", "W": "WHERE"}


def existing_seed_urls() -> set[str]:
    urls = set()
    for r in common.read_rows(SEED_CSV):
        u = (r.get("seed_pin_url") or "").strip()
        if "/pin/" in u:
            urls.add(u)
    return urls


def main() -> int:
    ap = argparse.ArgumentParser(description="자동 시드 확보")
    ap.add_argument("--categories", nargs="*", default=DEFAULT_TARGETS)
    ap.add_argument("--per", type=int, default=3, help="카테고리당 시드 핀 수")
    ap.add_argument("--count", type=int, default=40, help="시드당 연관핀 목표")
    args = ap.parse_args()

    targets = set(args.categories)
    have = existing_seed_urls()

    # raw_metadata 에서 타깃 카테고리의 핀 URL 수집 (중복 없이 순서 유지)
    picked: dict[str, list[str]] = {c: [] for c in targets}
    for r in common.read_rows(common.RAW_META):
        cat = r.get("category", "")
        url = (r.get("source_url") or "").strip()
        if cat in targets and "/pin/" in url and url not in have:
            lst = picked[cat]
            if url not in lst and len(lst) < args.per:
                lst.append(url)

    # seeds.csv 에 append (헤더 없으면 생성)
    new_rows = []
    for cat, urls in picked.items():
        axis = PREFIX_AXIS.get(cat[:1], "")
        for u in urls:
            new_rows.append({"axis": axis, "category_id": cat,
                             "seed_pin_url": u, "related_count": args.count})

    if not new_rows:
        print("추가할 시드가 없습니다. 먼저 희소 축을 검색 수집하세요 "
              "(crawl.py --source pinterest --axis pose_action/shot_size/camera_style).")
        return 1

    exists = SEED_CSV.exists()
    with open(SEED_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SEED_HEADER)
        if not exists:
            w.writeheader()
        for row in new_rows:
            w.writerow(row)

    print(f"시드 {len(new_rows)}개 추가 → {common.rel(SEED_CSV)}")
    for cat in sorted(picked):
        print(f"  {cat}: {len(picked[cat])}개")
    print("다음: python scripts/collect/seeds.py  (연관핀 확장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
