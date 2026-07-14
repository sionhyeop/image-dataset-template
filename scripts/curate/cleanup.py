"""고아 파일 정리.

master_metadata.csv 가 더 이상 참조하지 않는 curated/processed 이미지를 제거한다.
(전체 재라벨링 등으로 승인 목록이 바뀌면 이전 img_*.jpg 잔재가 남는데, 그것들을 정리)
원본(00_raw)은 그대로 두므로 promote/resize 로 언제든 재생성 가능하다.

사용:
    python cleanup.py            # 실제 삭제
    python cleanup.py --dry-run  # 삭제 대상만 출력
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="고아 이미지 정리")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    rows = common.read_rows(common.MASTER_META)
    # 현재 master 가 참조하는 curated 파일명 집합
    # 승인분만 유지 대상. discarded/rejected 로 바뀐 것은 고아로 간주해 정리된다.
    referenced = {Path(r["file_path"]).name
                  for r in rows
                  if "01_curated/images" in r.get("file_path", "")
                  and r.get("status") == "approved"}

    targets = [common.CURATED_IMAGES] + [common.PROCESSED / s for s in ("512", "768", "1024")]
    removed = 0
    for d in targets:
        if not d.exists():
            continue
        orphans = [p for p in d.glob("img_*.jpg") if p.name not in referenced]
        for p in orphans:
            if args.dry_run:
                print(f"  [dry] {common.rel(p)}")
            else:
                p.unlink()
            removed += 1
        if orphans:
            print(f"{common.rel(d)}: {'삭제 대상' if args.dry_run else '삭제'} {len(orphans)}장")

    kept = len(referenced)
    print(f"\n{'삭제 예정' if args.dry_run else '삭제'} 총 {removed}장 | 참조 유지 {kept}장(승인분)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
