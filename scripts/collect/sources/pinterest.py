"""Pinterest 검색 결과 다운로더 (gallery-dl 서브프로세스).

Pinterest 는 공식 검색 API 가 없어 gallery-dl 로 검색 페이지를 긁는다.
차단/약관 리스크가 있으므로 rate-limit 을 두고, 실패해도 다른 소스는 계속 진행한다.
로그인 콘텐츠가 필요하면 GALLERY_DL_COOKIES 환경변수에 cookies.txt 경로를 지정.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

NAME = "pinterest"


def available() -> bool:
    # gallery-dl 바이너리가 PATH 에 없어도(venv) 모듈로 실행하므로 import 여부로 판단
    return importlib.util.find_spec("gallery_dl") is not None


def _search_url(query: str) -> str:
    q = urllib.parse.quote(query)
    return f"https://www.pinterest.com/search/pins/?q={q}"


def _related_url(seed_pin_url: str) -> str:
    """시드 핀 URL 뒤에 #related 를 붙여 gallery-dl 연관핀 extractor 를 트리거."""
    u = seed_pin_url.split("#")[0].rstrip("/")
    return u + "/#related"


def _run(out_dir: Path, url: str, count: int, query_tag: str) -> list[dict]:
    """gallery-dl 로 url 을 count 개 내려받고 새로 생긴 파일의 raw_metadata 행을 반환."""
    cookies = os.environ.get("GALLERY_DL_COOKIES", "").strip()
    cmd = [
        sys.executable, "-m", "gallery_dl",
        "--dest", str(out_dir),
        "--range", f"1-{count}",
        "--sleep", "1.0-2.5",          # rate limit
        "--sleep-request", "1.0",
        "--write-metadata",             # 각 이미지 옆에 .json (원본 URL 등)
        "-o", "output.mode=null",
    ]
    if cookies:
        cmd += ["--cookies", cookies]
    cmd.append(url)

    before = _list_images(out_dir)
    try:
        subprocess.run(cmd, timeout=300, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception as e:
        print(f"    [pinterest] '{query_tag}' 실패: {e}")
        return []
    after = _list_images(out_dir)
    return sorted(after - before)


def collect(category: str, queries: list[str], out_dir: Path, limit: int) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    if not queries:
        return records
    per_query = max(1, limit // len(queries))
    for q in queries:
        for p in _run(out_dir, _search_url(q), per_query, q):
            records.append({
                "raw_file": str(p), "source": NAME, "category": category,
                "search_query": q, "source_url": _read_source_url(p),
                "license": "unknown", "author": _read_author(p),
            })
    return records


def collect_related(category: str, seed_url: str, out_dir: Path, count: int) -> list[dict]:
    """방식 B — 시드 핀의 연관핀을 수집. 검색보다 구도/무드 적합도가 높다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    tag = f"related:{seed_url}"
    for p in _run(out_dir, _related_url(seed_url), count, tag):
        records.append({
            "raw_file": str(p), "source": NAME, "category": category,
            "search_query": tag, "source_url": _read_source_url(p) or seed_url,
            "license": "unknown", "author": _read_author(p),
        })
    return records


def _list_images(root: Path) -> set[Path]:
    exts = {".jpg", ".jpeg", ".png", ".webp"}
    return {p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in exts}


def _read_source_url(image_path: Path) -> str:
    """사이드카 json 의 핀 id 로 핀 페이지 URL 을 구성 (출처 추적용)."""
    meta = image_path.with_suffix(image_path.suffix + ".json")
    if not meta.exists():
        return ""
    try:
        d = json.loads(meta.read_text(encoding="utf-8"))
        pin_id = d.get("id") or d.get("pin_id")
        if pin_id:
            return f"https://www.pinterest.com/pin/{pin_id}/"
    except Exception:
        pass
    return ""


def _read_author(image_path: Path) -> str:
    meta = image_path.with_suffix(image_path.suffix + ".json")
    if not meta.exists():
        return ""
    try:
        d = json.loads(meta.read_text(encoding="utf-8"))
        pinner = d.get("pinner") or {}
        return str(pinner.get("username") or pinner.get("full_name") or "")
    except Exception:
        return ""
