"""중복 제거 + 1차 품질 필터 (가이드 11절 2~3단계).

- pHash 로 거의 같은 이미지를 그룹핑, 그룹당 최고 해상도 1장만 남김
- 짧은 변 < MIN_SIDE, 손상 파일, 극단적 종횡비는 저품질로 분류
- 걸러진 이미지는 data/01_curated/rejected/{duplicate,low_quality}/ 로 이동
- 통과한 raw 이미지의 pHash 는 stage 통과 목록(dedup_kept.csv)에 기록해
  auto_label 단계에서 재사용한다.

사용:
    python dedup.py                # raw_metadata 의 모든 raw 이미지 처리
    python dedup.py --hamming 8    # 중복 판정 임계값 조정
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

# 수집 필터 — **도메인이 정한다** (taxonomy._curation.intake).
# 512/3.0 은 인물 포트레이트 기준이다. 파노라마 풍경 도메인은 max_aspect 를 늘려야 하고,
# 썸네일 제품샷 도메인은 min_side 를 낮춰야 한다. 선언이 없으면 아래 기본값을 쓴다.
_INTAKE = common.load_curation().get("intake") or {}
MIN_SIDE = int(_INTAKE.get("min_side", 512))       # 짧은 변 최소 픽셀
MAX_ASPECT = float(_INTAKE.get("max_aspect", 3.0))  # 최대 종횡비 (가로/세로 또는 세로/가로)
DEFAULT_HAMMING = int(_INTAKE.get("phash_hamming", 8))  # 지각 해시 중복 임계

KEPT_CSV = common.ANNOTATIONS / "dedup_kept.csv"
KEPT_FIELDS = ["raw_file", "source", "category", "search_query",
               "source_url", "downloaded_at", "license", "author",
               "width", "height", "phash"]


def phash_of(path: Path):
    import imagehash
    from PIL import Image
    try:
        with Image.open(path) as im:
            return imagehash.phash(im.convert("RGB"))
    except Exception:
        return None


def parse_hash(s: str):
    import imagehash
    try:
        return imagehash.hex_to_hash(s)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="pHash 중복 제거 + 1차 필터")
    ap.add_argument("--hamming", type=int, default=DEFAULT_HAMMING, help="중복 판정 해밍거리 임계값")
    args = ap.parse_args()

    raw_rows = common.read_rows(common.RAW_META)
    if not raw_rows:
        print("raw_metadata.csv 가 비어 있습니다. 먼저 crawl.py 를 실행하세요.")
        return 1

    # 이전에 통과한 이미지들 = 잠긴 기준점. 새 이미지는 이들과도 비교해야 하며,
    # 이들 자신은 다시 평가·교체·기록하지 않는다.
    # entries: 각 항목 {"hash", "locked", "row"}. locked=True 면 기존 통과분.
    prior = common.read_rows(KEPT_CSV)
    already = {r["raw_file"] for r in prior}

    entries: list[dict] = []
    for r in prior:
        h = parse_hash(r.get("phash", ""))
        if h is not None:
            entries.append({"hash": h, "locked": True, "row": r})

    new_count = 0
    n_dup = n_lowq = n_missing = n_skip = 0

    (common.REJECTED / "duplicate").mkdir(parents=True, exist_ok=True)
    (common.REJECTED / "low_quality").mkdir(parents=True, exist_ok=True)

    for row in raw_rows:
        rel = row["raw_file"]
        if rel in already:
            n_skip += 1
            continue
        path = common.raw_path(rel)
        if not path.exists():
            n_missing += 1
            continue

        dims = common.image_dimensions(path)
        if dims is None:
            _reject(path, "low_quality")
            n_lowq += 1
            continue
        w, h = dims
        short = min(w, h)
        aspect = max(w / h, h / w)
        if short < MIN_SIDE or aspect > MAX_ASPECT:
            _reject(path, "low_quality")
            n_lowq += 1
            continue

        ph = phash_of(path)
        if ph is None:
            _reject(path, "low_quality")
            n_lowq += 1
            continue

        # 기존 통과분(잠긴 것 + 이번 실행 신규) 과 중복 비교
        match = None
        for e in entries:
            if (ph - e["hash"]) <= args.hamming:
                match = e
                break

        if match is None:
            new_row = dict(row, width=w, height=h, phash=str(ph))
            entries.append({"hash": ph, "locked": False, "row": new_row})
            new_count += 1
        elif match["locked"]:
            # 이미 기록된 기준점과 중복 -> 새 것을 버린다 (기준점은 CSV 재기록 방지)
            _reject(path, "duplicate")
            n_dup += 1
        else:
            # 이번 실행 신규 통과분과 중복: 더 큰 해상도를 남긴다
            keep_row = match["row"]
            keep_area = int(keep_row["width"]) * int(keep_row["height"])
            if w * h > keep_area:
                _reject(common.raw_path(keep_row), "duplicate")
                match["row"] = dict(row, width=w, height=h, phash=str(ph))
                match["hash"] = ph
            else:
                _reject(path, "duplicate")
            n_dup += 1

    # 이번 실행에서 새로 통과한 것만 append (잠긴 기준점은 이미 CSV 에 있음)
    for e in entries:
        if not e["locked"]:
            common.append_row(KEPT_CSV, KEPT_FIELDS, e["row"])

    print(f"통과: {new_count}장 | 중복 제거: {n_dup} | 저품질: {n_lowq} "
          f"| 원본없음: {n_missing} | 기존처리 스킵: {n_skip}")
    print(f"통과 목록: {common.rel(KEPT_CSV)}")
    print("다음 단계: python scripts/label/auto_label.py")
    return 0


def _reject(path: Path, reason: str) -> None:
    dest_dir = common.REJECTED / reason
    dest = dest_dir / path.name
    n = 1
    while dest.exists():
        dest = dest_dir / f"{path.stem}_{n}{path.suffix}"
        n += 1
    try:
        shutil.move(str(path), str(dest))
    except Exception as e:
        print(f"    이동 실패 {path}: {e}")


if __name__ == "__main__":
    raise SystemExit(main())
