"""픽스처 이미지 받아오기 — 가짜 도메인(식물)을 **눈으로 볼 수 있는** 데이터셋으로 만든다.

왜 진짜 이미지인가:
    픽스처는 오랫동안 0행이었고, 그다음엔 단색 사각형 24장이었다. 둘 다 "테스트는 통과하는데
    열어보면 아무 의미가 없는" 상태다. 사람이 열어서 확인할 수 없는 산출물은 신뢰받지 못한다.

왜 위키미디어 공용인가:
    이 저장소는 **공개 템플릿**이다. 크롤한 이미지를 커밋하면 README 의 저작권 경고와
    정면으로 충돌한다(license=unknown, experiment_only). 그래서 재배포가 명시적으로 허용된
    라이선스(CC0 · Public Domain · CC BY · CC BY-SA)만 받고, 저작자·라이선스·원본 URL 을
    master_metadata.csv 에 그대로 남긴다 — 템플릿이 시키는 그대로.

한 번만 돌리면 된다. 결과물(24장 · 장당 ~6KB)은 저장소에 커밋되므로 테스트는 네트워크가 필요없다.

사용:
    python tests/fixtures/fetch_fixture_images.py
"""
from __future__ import annotations

import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FIXTURE = ROOT / "datasets" / "example_plants"
IMG_DIR = FIXTURE / "data" / "01_curated" / "images"
API = "https://commons.wikimedia.org/w/api.php"
UA = "composition-dataset-template/1.0 (test fixture; https://github.com/sionhyeop)"

# 재배포가 허용되는 라이선스만. 그 외는 건너뛴다.
OK_LICENSE = re.compile(r"^(CC0|Public domain|CC BY(-SA)? [\d.]+)$", re.I)

# 위키미디어 퍼블릭도메인의 상당수는 **인터넷아카이브 문헌 스캔**이다 — 종묘 카탈로그,
# 병리학 보고서, 식물도감 표제지… 라이선스는 완벽하지만 식물 사진이 아니다.
# 파일명의 "(IA ...)" 표식과 문헌 낱말로 먼저 거른다(픽셀을 받기 전에 — 싸다).
BAD_TITLE = re.compile(r"\(IA[ _]|catalogu?e|index of|reporter|bulletin|annual report|herbarium",
                       re.I)

# 축 코드 ↔ 검색어. 가짜 도메인이지만 이미지는 진짜다.
SPECIES = {
    "V01_monstera": "Monstera plant",
    "V02_ficus": "Ficus elastica leaves",
    "V03_succulent": "succulent plant pot",
}
SETTING = ["B01_windowsill", "B02_floor", "B03_shelf"]
LIGHTING = ["L01_natural", "L02_artificial", "L03_backlit"]
HEALTH = ["H01_healthy", "H02_wilting"]
PER_SPECIES = 8


def _fetch(url: str, tries: int = 5) -> bytes:
    """위키미디어는 429 를 준다 — 간격을 두고 물러섰다가 다시 시도한다."""
    import time
    for n in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                time.sleep(0.4)          # 예의상 간격
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code != 429 or n == tries - 1:
                raise
            wait = 2 ** n
            print(f"    429 — {wait}초 대기 후 재시도")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def _get(**params) -> dict:
    params.update(format="json", action="query")
    url = API + "?" + urllib.parse.urlencode(params)
    return json.loads(_fetch(url))


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def looks_like_a_plant_photo(data: bytes) -> bool:
    """**내용을 본다.** 라이선스가 깨끗한 것과 쓸 만한 사진인 것은 전혀 다른 얘기다.

    처음 이 스크립트는 라이선스만 보고 24장을 받았는데, 13장이 스캔된 고서 페이지였다
    ("Historic, Archive Document" — 위키미디어의 퍼블릭도메인 상당수가 생물다양성 문헌 스캔이다).
    컨택트시트로 **눈으로 보고서야** 알았다. 받아놓고 안 보면 이렇게 된다.

    두 가지만 잰다:
      · 채도 — 스캔된 종이는 무채색이다. 사진은 색이 있다.
      · 밝은 픽셀 비율 — 종이는 화면의 대부분이 흰색이다.

    처음엔 '초록 픽셀 10% 이상'도 요구했는데, 그게 **고무나무를 죽였다** —
    Ficus elastica 는 적자색·얼룩무늬 품종이 많아 초록이 거의 없다.
    필터를 좁게 잡으면 스캔만이 아니라 진짜 데이터도 같이 죽는다. 게이트는 최소한만.
    """
    import io
    from PIL import Image

    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return False
    im.thumbnail((64, 64))
    px = list(im.getdata())
    n = len(px)
    if not n:
        return False
    sat = sum(max(p) - min(p) for p in px) / n           # 평균 채도 (0~255)
    pale = sum(1 for p in px if min(p) > 200) / n        # 거의 흰색인 픽셀 비율
    return sat >= 20 and pale <= 0.45


def search(term: str, want: int) -> list[dict]:
    # 내용 필터로 많이 떨어져 나가므로 후보를 넉넉히 잡는다
    d = _get(generator="search", gsrsearch=term, gsrnamespace=6,
             gsrlimit=want * 8, prop="imageinfo",
             iiprop="url|extmetadata", iiurlwidth=190)
    out = []
    for pg in (d.get("query", {}).get("pages") or {}).values():
        ii = (pg.get("imageinfo") or [{}])[0]
        meta = ii.get("extmetadata") or {}
        lic = _strip_html(meta.get("LicenseShortName", {}).get("value", ""))
        if not OK_LICENSE.match(lic):
            continue                      # 재배포 불가 라이선스는 안 받는다
        if BAD_TITLE.search(pg["title"]):
            continue                      # 문헌 스캔 — 픽셀을 받기도 전에 거른다
        if not ii.get("thumburl"):
            continue
        data = _fetch(ii["thumburl"])
        if not looks_like_a_plant_photo(data):
            print(f"    (내용 미달로 버림: {pg['title'][:44]})")
            continue
        out.append({
            "data": data,
            "page": ii.get("descriptionurl", ""),
            "license": lic,
            "author": _strip_html(meta.get("Artist", {}).get("value", ""))[:60] or "unknown",
        })
        if len(out) >= want:
            break
    return out


def main() -> int:
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    rows, i = [], 0
    for code, term in SPECIES.items():
        hits = search(term, PER_SPECIES)
        print(f"{code}: {len(hits)}장 (재배포 가능 라이선스만)")
        if len(hits) < PER_SPECIES:
            print(f"  ⚠ {PER_SPECIES}장에 못 미침 — 검색어를 넓히세요")
        for h in hits:
            name = f"img_{i:06d}.jpg"
            data = h["data"]           # search() 가 이미 받아서 내용까지 확인했다
            (IMG_DIR / name).write_bytes(data)
            rows.append({
                "image_id": f"img_{i:06d}",
                "file_path": f"data/01_curated/images/{name}",
                "source": "wikimedia_commons",
                "source_url": h["page"],
                "downloaded_at": "2026-07-14",
                "width": "190", "height": "190",
                # 라벨은 결정적으로 돌린다 — 픽스처의 목적은 '코드가 도메인에 안 묶였나'이지
                # '라벨이 정확한가'가 아니다. (정확도는 실도메인이 본다)
                "species": code,
                "setting": SETTING[i % 3] + (f";{SETTING[(i + 1) % 3]}" if i % 8 == 0 else ""),
                "lighting": LIGHTING[i % 3],
                "health": HEALTH[i % 2],
                "quality_score": "4",
                "status": "approved",
                "license": h["license"],
                "author": h["author"],
                "usage_allowed": "redistributable",
            })
            print(f"  {name}  {len(data)//1024}KB  {h['license']}")
            i += 1

    import common  # noqa: E402  (DATASET_DIR 없이도 MASTER_FIELDS 만 쓰므로 여기서 import)
    fields = common.MASTER_FIELDS
    out = FIXTURE / "annotations" / "master_metadata.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})

    total = sum((IMG_DIR / f"img_{n:06d}.jpg").stat().st_size for n in range(i))
    print(f"\n✅ {i}장 · {total/1024:.0f}KB → {common.rel(IMG_DIR)}")
    print(f"   {common.rel(out)} 갱신 (출처·라이선스·저작자 기록됨)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
