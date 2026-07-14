"""Bing 이미지 검색 크롤러 (icrawler). API 키 불필요."""
from __future__ import annotations

from pathlib import Path

NAME = "bing"


def available() -> bool:
    try:
        import icrawler  # noqa: F401
        return True
    except Exception:
        return False


def collect(category: str, queries: list[str], out_dir: Path, limit: int) -> list[dict]:
    """out_dir 에 이미지를 내려받고 raw_metadata 행 리스트를 반환.

    icrawler 는 파일명을 000001.jpg 처럼 저장한다. 우리는 그걸 그대로 두고
    상대경로만 기록한 뒤, crawl.py 에서 최종 raw_NNNNNN 로 리네임한다.
    """
    from icrawler.builtin import BingImageCrawler

    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    if not queries:
        return records

    # 여러 검색어에 limit 를 분배
    per_query = max(1, limit // len(queries))
    for q in queries:
        before = set(p.name for p in out_dir.iterdir() if p.is_file())
        crawler = BingImageCrawler(
            downloader_threads=4,
            storage={"root_dir": str(out_dir)},
            log_level=40,  # ERROR only
        )
        try:
            crawler.crawl(keyword=q, max_num=per_query, file_idx_offset="auto")
        except Exception as e:
            print(f"    [bing] '{q}' 실패: {e}")
            continue
        after = set(p.name for p in out_dir.iterdir() if p.is_file())
        for name in sorted(after - before):
            records.append({
                "raw_file": str((out_dir / name)),
                "source": NAME,
                "category": category,
                "search_query": q,
                "source_url": "",          # icrawler 는 원본 URL 을 파일에 남기지 않음
                "license": "unknown",
                "author": "",
            })
    return records
