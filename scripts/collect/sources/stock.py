"""무료 스톡 이미지 API: Unsplash / Pexels / Pixabay.

라이선스와 작가 정보가 응답에 포함되므로 저작권 추적이 명확하다.
키가 없는 제공자는 자동으로 건너뛴다.
"""
from __future__ import annotations

import time
from pathlib import Path

import requests

# 세 제공자를 한 소스로 묶되, 다운로드된 파일명에 어느 제공자인지 접두어로 남긴다.
NAME = "stock"


def available() -> bool:
    import os
    return any(os.environ.get(k, "").strip()
               for k in ("UNSPLASH_ACCESS_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY"))


def collect(category: str, queries: list[str], out_dir: Path, limit: int) -> list[dict]:
    import os
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict] = []
    if not queries:
        return records

    providers = []
    if os.environ.get("UNSPLASH_ACCESS_KEY", "").strip():
        providers.append(_unsplash)
    if os.environ.get("PEXELS_API_KEY", "").strip():
        providers.append(_pexels)
    if os.environ.get("PIXABAY_API_KEY", "").strip():
        providers.append(_pixabay)
    if not providers:
        return records

    per = max(1, limit // (len(queries) * len(providers)))
    idx = 0
    for q in queries:
        for prov in providers:
            try:
                hits = prov(q, per)
            except Exception as e:
                print(f"    [stock:{prov.__name__.strip('_')}] '{q}' 실패: {e}")
                continue
            for hit in hits:
                dest = out_dir / f"{hit['provider']}_{idx:06d}.jpg"
                if _download(hit["download_url"], dest):
                    idx += 1
                    records.append({
                        "raw_file": str(dest),
                        "source": NAME,
                        "category": category,
                        "search_query": q,
                        "source_url": hit["source_url"],
                        "license": hit["license"],
                        "author": hit["author"],
                    })
    return records


def _download(url: str, dest: Path) -> bool:
    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(8192):
                f.write(chunk)
        time.sleep(0.3)
        return True
    except Exception as e:
        print(f"    [stock] 다운로드 실패 {url}: {e}")
        return False


def _unsplash(query: str, count: int) -> list[dict]:
    import os
    key = os.environ["UNSPLASH_ACCESS_KEY"].strip()
    r = requests.get(
        "https://api.unsplash.com/search/photos",
        params={"query": query, "per_page": min(count, 30), "orientation": "portrait"},
        headers={"Authorization": f"Client-ID {key}"},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("results", []):
        out.append({
            "provider": "unsplash",
            "download_url": p["urls"]["regular"],
            "source_url": p["links"]["html"],
            "license": "Unsplash License",
            "author": (p.get("user") or {}).get("name", ""),
        })
    return out


def _pexels(query: str, count: int) -> list[dict]:
    import os
    key = os.environ["PEXELS_API_KEY"].strip()
    r = requests.get(
        "https://api.pexels.com/v1/search",
        params={"query": query, "per_page": min(count, 80), "orientation": "portrait"},
        headers={"Authorization": key},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("photos", []):
        out.append({
            "provider": "pexels",
            "download_url": p["src"]["large"],
            "source_url": p["url"],
            "license": "Pexels License",
            "author": p.get("photographer", ""),
        })
    return out


def _pixabay(query: str, count: int) -> list[dict]:
    import os
    key = os.environ["PIXABAY_API_KEY"].strip()
    r = requests.get(
        "https://pixabay.com/api/",
        params={"key": key, "q": query, "per_page": min(max(count, 3), 200),
                "image_type": "photo", "orientation": "vertical"},
        timeout=30,
    )
    r.raise_for_status()
    out = []
    for p in r.json().get("hits", []):
        out.append({
            "provider": "pixabay",
            "download_url": p.get("largeImageURL") or p["webformatURL"],
            "source_url": p.get("pageURL", ""),
            "license": "Pixabay License",
            "author": p.get("user", ""),
        })
    return out
