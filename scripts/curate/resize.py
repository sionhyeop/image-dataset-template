"""학습용 리사이즈/전처리 (가이드 11절 6단계).

curated 로 승격된(approved & file_path 가 01_curated 인) 이미지를
data/02_processed/{512,768,1024}/img_NNNNNN.jpg 로 짧은 변 기준 리사이즈.
원본보다 키우지는 않는다(업스케일 방지). JPEG q=90.

사용:
    python resize.py                  # 512,768,1024 전부
    python resize.py --sizes 768      # 특정 크기만
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

DEFAULT_SIZES = [512, 768, 1024]


def resize_to(src: Path, dest: Path, short_side: int) -> bool:
    from PIL import Image
    try:
        with Image.open(src) as im:
            im = im.convert("RGB")
            w, h = im.size
            cur_short = min(w, h)
            scale = min(1.0, short_side / cur_short)  # 업스케일 안 함
            nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
            im = im.resize((nw, nh), Image.LANCZOS)
            dest.parent.mkdir(parents=True, exist_ok=True)
            im.save(dest, format="JPEG", quality=90)
        return True
    except Exception as e:
        print(f"  리사이즈 실패 {src.name}: {e}")
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description="학습용 리사이즈")
    ap.add_argument("--sizes", nargs="*", type=int, default=DEFAULT_SIZES)
    args = ap.parse_args()

    rows = common.read_rows(common.MASTER_META)
    curated = [r for r in rows
               if r.get("status") == "approved"
               and "01_curated/images" in r.get("file_path", "")]
    if not curated:
        print("승격된 이미지가 없습니다. 먼저 promote.py 를 실행하세요.")
        return 1

    counts = {s: 0 for s in args.sizes}
    for r in curated:
        src = common.image_path(r)
        if not src.exists():
            continue
        name = Path(r["file_path"]).name  # img_000001.jpg
        for s in args.sizes:
            dest = common.PROCESSED / str(s) / name
            if dest.exists():
                continue
            if resize_to(src, dest, s):
                counts[s] += 1

    for s in args.sizes:
        print(f"  {s}px: {counts[s]}장 생성 -> data/02_processed/{s}/")
    print("다음 단계: python scripts/curate/make_splits.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
