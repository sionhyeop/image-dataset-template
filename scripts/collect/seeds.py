"""시드핀 연관핀 수집 (방식 B — pinterest v2 문서).

<데이터셋>/seeds.csv 의 시드 핀마다 Pinterest 추천엔진의 연관핀을 수집한다.
검색은 뒤로 갈수록 관련도가 떨어지지만, 연관핀은 구도/무드 적합도가 높아
검색으로 잘 안 모이는 희소 축(점프·뒷모습·로우앵글 등) 보강에 좋다.

저장 위치: data/00_raw/{날짜}/pinterest_related/{category}/raw_NNNNNN.jpg
raw_metadata.csv 에 source=pinterest, search_query="related:<핀URL>" 로 기록.

사용:
    python seeds.py                          # <데이터셋>/seeds.csv 전체
    python seeds.py --category P04_jumping   # 특정 카테고리만
    python seeds.py --count 40               # 시드당 연관핀 수 override
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
from collect.sources import pinterest  # noqa: E402

# 시드 목록은 도메인 데이터다 — 코드가 아니라 데이터셋에 산다.
SEED_CSV = common.DATASET_ROOT / "seeds.csv"


def read_seeds() -> list[dict]:
    """주석/빈 행을 걸러 유효한 시드만 반환."""
    seeds = []
    for r in common.read_rows(SEED_CSV):
        axis = (r.get("axis") or "").strip()
        url = (r.get("seed_pin_url") or "").strip()
        if axis.startswith("#") or "/pin/" not in url:
            continue
        seeds.append({
            "axis": axis,
            "category_id": (r.get("category_id") or "").strip(),
            "seed_pin_url": url,
            "related_count": int(r.get("related_count") or 50),
        })
    return seeds


def next_raw_index() -> int:
    return len(common.read_rows(common.RAW_META)) + 1


def main() -> int:
    common.load_env()
    ap = argparse.ArgumentParser(description="시드핀 연관핀 수집 (방식 B)")
    ap.add_argument("--category", help="특정 category_id 만")
    ap.add_argument("--count", type=int, help="시드당 연관핀 수 override")
    args = ap.parse_args()

    if not pinterest.available():
        print("gallery-dl(gallery_dl) 이 설치돼 있지 않습니다.")
        return 1
    if not SEED_CSV.exists():
        print(f"{common.rel(SEED_CSV)} 가 없습니다.")
        return 1

    seeds = read_seeds()
    if args.category:
        seeds = [s for s in seeds if s["category_id"] == args.category]
    if not seeds:
        print("유효한 시드 핀이 없습니다. <데이터셋>/seeds.csv 에 '.../pin/<번호>/' URL 을 추가하세요.")
        return 1

    day = common.today()
    idx = next_raw_index()
    total = 0
    for s in seeds:
        cat = s["category_id"]
        count = args.count or s["related_count"]
        out_dir = common.RAW / day / "pinterest_related" / cat
        print(f"[related] {cat}  시드 {s['seed_pin_url']}  연관핀 {count}개...")
        records = pinterest.collect_related(cat, s["seed_pin_url"], out_dir, count)
        for rec in records:
            src = Path(rec["raw_file"])
            if not src.exists():
                continue
            ext = src.suffix.lower() or ".jpg"
            new_path = out_dir / f"raw_{idx:06d}{ext}"
            if src != new_path:
                shutil.move(str(src), str(new_path))
                sidecar = src.with_suffix(src.suffix + ".json")
                if sidecar.exists():
                    sidecar.unlink()
            common.append_row(common.RAW_META, common.RAW_META_FIELDS, {
                "raw_file": str(new_path.relative_to(common.DATASET_ROOT)),
                "source": rec["source"], "category": cat,
                "search_query": rec["search_query"], "source_url": rec["source_url"],
                "downloaded_at": day, "license": rec["license"], "author": rec["author"],
            })
            idx += 1
            total += 1
        print(f"    -> {len(records)}장 수집")

    print(f"\n완료: 연관핀 총 {total}장. 다음: dedup.py → auto_label.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
