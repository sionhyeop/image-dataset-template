"""멀티소스 이미지 크롤러.

queries.yaml 을 읽어 소스(bing/pinterest/stock)별로 이미지를 수집하고,
data/00_raw/{날짜}/{소스}/{카테고리}/raw_NNNNNN.jpg 로 저장한 뒤
annotations/raw_metadata.csv 에 출처/검색어/라이선스를 기록한다.

사용 예:
    python crawl.py                                  # 전체 소스 x 전체 WHERE 카테고리
    python crawl.py --source bing --category W01_cafe --limit 30
    python crawl.py --axis where --limit 100
    python crawl.py --smoke                           # 소량 스모크 테스트
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# scripts/ 를 import 경로에 추가
SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
from collect.sources import bing, pinterest, stock  # noqa: E402

SOURCES = {"bing": bing, "pinterest": pinterest, "stock": stock}


def flatten_queries(queries_cfg: dict, axis: str | None, category: str | None) -> dict[str, list[str]]:
    """{카테고리코드: [검색어...]} 로 평탄화. ko+en 합침."""
    result: dict[str, list[str]] = {}
    for ax, cats in queries_cfg.items():
        if axis and ax != axis:
            continue
        for cat, langs in cats.items():
            if category and cat != category:
                continue
            terms: list[str] = []
            for lang_terms in langs.values():
                terms.extend(lang_terms)
            result[cat] = terms
    return result


def next_raw_index() -> int:
    """기존 raw_metadata 기준으로 다음 raw 파일 순번."""
    rows = common.read_rows(common.RAW_META)
    return len(rows) + 1


def main() -> int:
    common.load_env()
    ap = argparse.ArgumentParser(description="멀티소스 구도 이미지 크롤러")
    ap.add_argument("--source", choices=list(SOURCES), help="특정 소스만 (기본: 사용 가능한 전부)")
    ap.add_argument("--axis", help="특정 축만 (기본: queries.yaml 의 전 축)")
    ap.add_argument("--category", help="특정 카테고리 코드만 (예: W01_cafe)")
    ap.add_argument("--limit", type=int, default=150, help="카테고리·소스당 목표 수집량")
    ap.add_argument("--smoke", action="store_true", help="스모크 테스트: bing 만, 2개 카테고리 x 10장")
    args = ap.parse_args()

    queries_cfg = common.load_queries()

    if args.smoke:
        args.source = args.source or "bing"
        args.limit = min(args.limit, 10)

    # 대상 소스 결정
    if args.source:
        sources = {args.source: SOURCES[args.source]}
    else:
        sources = {n: m for n, m in SOURCES.items() if m.available()}
        if not sources:
            print("사용 가능한 소스가 없습니다. icrawler/gallery-dl 설치 또는 API 키를 확인하세요.")
            return 1

    axis = args.axis
    if args.smoke and not args.category:
        # 'where' 리터럴은 인물 도메인 전용이다 — queries.yaml 의 첫 축을 쓴다.
        axis = next(iter(queries_cfg), None)

    cat_queries = flatten_queries(queries_cfg, axis, args.category)
    if args.smoke and not args.category:
        cat_queries = dict(list(cat_queries.items())[:2])  # 앞 2개 카테고리만

    if not cat_queries:
        print("대상 카테고리가 없습니다. --axis/--category 를 확인하세요.")
        return 1

    day = common.today()
    idx = next_raw_index()
    total = 0

    for cat, terms in cat_queries.items():
        for sname, smod in sources.items():
            if not smod.available():
                print(f"[skip] {sname}: 사용 불가 (미설치/키 없음)")
                continue
            out_dir = common.RAW / day / sname / cat
            print(f"[{sname}] {cat}  검색어 {len(terms)}개, 목표 {args.limit}장...")
            records = smod.collect(cat, terms, out_dir, args.limit)
            # icrawler/gallery-dl 이 만든 임의 파일명을 raw_NNNNNN 로 정규화
            for rec in records:
                src_path = Path(rec["raw_file"])
                if not src_path.exists():
                    continue
                ext = src_path.suffix.lower() or ".jpg"
                new_name = f"raw_{idx:06d}{ext}"
                new_path = out_dir / new_name
                if src_path != new_path:
                    shutil.move(str(src_path), str(new_path))
                    # 사이드카 json 도 같이 정리
                    sidecar = src_path.with_suffix(src_path.suffix + ".json")
                    if sidecar.exists():
                        sidecar.unlink()
                rel = new_path.relative_to(common.DATASET_ROOT)
                common.append_row(common.RAW_META, common.RAW_META_FIELDS, {
                    "raw_file": str(rel),
                    "source": rec["source"],
                    "category": rec["category"],
                    "search_query": rec["search_query"],
                    "source_url": rec["source_url"],
                    "downloaded_at": day,
                    "license": rec["license"],
                    "author": rec["author"],
                })
                idx += 1
                total += 1
            print(f"    -> {len(records)}장 수집")

    print(f"\n완료: 총 {total}장 수집. 메타데이터: {common.rel(common.RAW_META)}")
    print("다음 단계: python scripts/curate/dedup.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
