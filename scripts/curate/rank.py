"""검수 우선순위 랭킹 (pinterest v2 문서 10장).

라벨된 이미지에 점수를 매겨 review_queue.csv 를 final_score 내림차순으로 정렬한다.
사람이 상위부터 훑으면 되므로 대량 검수가 빨라진다.

  final_score = 2.0*clip_score + person_score + resolution_score
                + aspect_score + keyword_score

- person_score : 주 피사체 검출 (1개 +2, 여러 개 +1, 미검출 -3) — label/detect.py
- clip_score   : 이미지와 배정 장소 프롬프트의 코사인 유사도를 카테고리 내
                 z-score 정규화 후 -2~+2 클리핑. + category_match 플래그
- resolution   : 긴 변 1200+ +2 / 800+ +1 / 500미만 -2
- aspect (w/h) : 0.60~0.85 +2 / 0.85~1.10 +1 / 1.30초과 -1
- keyword      : CLIP 배정 장소가 수집 카테고리와 일치하면 +1

점수 규칙·가중치는 taxonomy._curation.score 가 정한다(도메인마다 다르다).

torch/open_clip/검출기 미설치 시 해당 항을 0으로 두고 나머지로 진행한다.

사용:
    python rank.py                # 전체 라벨분
    python rank.py --status approved   # 승인분만
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402

REVIEW_CSV = common.ANNOTATIONS / "review_queue.csv"
# 'place' 슬롯에 어느 축의 값이 들어갈지는 taxonomy._semantic.place 가 정한다
# (place 선언이 없는 도메인이면 빈 값). 예전엔 이 컬럼 이름이 'where' 였다 —
# "슬롯 이름일 뿐"이라고 주석을 달아뒀지만, 결국 인물 도메인의 축 이름이었다.
REVIEW_FIELDS = [
    "image_id", "file_path", "collected_category", "place", "category_name",
    "source", "width", "height",
    "person_score", "clip_score", "category_match",
    "resolution_score", "aspect_score", "keyword_score", "final_score",
    "source_url", "review_status",
]


# 점수 규칙 — **도메인이 정한다** (taxonomy._curation.score).
# 세로 인물사진 우대(종횡비 0.60~0.85 → +2)를 코드에 박아두면, 음식(정사각·탑다운)과
# 풍경(가로)은 −1 점을 먹어 검수 순서가 뒤집힌다. 데이터를 버리진 않지만 사람이
# '좋은 것부터' 못 본다. 선언이 없으면 아래 기본값(인물 포트레이트 기준)을 쓴다.
_SCORE = common.load_curation().get("score") or {}

_ASPECT_RULES = _SCORE.get("aspect") or [
    {"lo": 0.60, "hi": 0.85, "points": 2},
    {"lo": 0.85, "hi": 1.10, "points": 1},
    {"lo": 1.30, "hi": 99, "points": -1},
]
_RES_RULES = _SCORE.get("resolution") or [
    {"min": 1200, "points": 2},
    {"min": 800, "points": 1},
    {"max": 500, "points": -2},
]
_WEIGHTS = _SCORE.get("weights") or {
    "clip_score": 2.0, "person_score": 1.0, "resolution_score": 1.0,
    "aspect_score": 1.0, "keyword_score": 1.0,
}


def resolution_score(long_side: int) -> int:
    """긴 변 픽셀 → 점수. 규칙은 위에서부터 처음 맞는 것을 쓴다."""
    for r in _RES_RULES:
        if "min" in r and long_side >= float(r["min"]):
            return int(r["points"])
        if "max" in r and long_side < float(r["max"]):
            return int(r["points"])
    return 0


def aspect_score(w: int, h: int) -> int:
    """종횡비(가로/세로) → 점수. 구간은 [lo, hi]."""
    if h == 0:
        return 0
    r = w / h
    for rule in _ASPECT_RULES:
        if float(rule.get("lo", 0)) <= r <= float(rule.get("hi", 1e9)):
            return int(rule["points"])
    return 0


def load_clip():
    try:
        from clip_label import ClipLabeler
        return ClipLabeler()
    except Exception as e:
        print(f"  CLIP 사용 불가 (clip_score=0): {e}")
        return None


def person_score_of(det, path: Path) -> int:
    """주 피사체 1개 +2 · 여러 개 +1 · 없음 −3.

    검출·면적필터·대상클래스는 label/detect.py 가 taxonomy._curation.detect 를 보고 처리한다.
    (예전엔 이 파일과 enrich.py 가 각자 YOLO 를 로드하고 classes=[0] 을 박아뒀다 — SSOT 아님)
    """
    n = det.count(path)
    if n is None:
        return 0            # 검출기 없음 = 모름. 0 이지 −3 이 아니다(전량 차단 방지)
    if n == 1:
        return 2
    if n >= 2:
        return 1
    return -3


def main() -> int:
    common.load_env()
    ap = argparse.ArgumentParser(description="검수 우선순위 랭킹")
    ap.add_argument("--status", help="특정 status 만 (예: approved)")
    args = ap.parse_args()

    taxonomy = common.load_taxonomy()
    # 검색어 점수는 '어디서 찍었나' 축의 한글 표시명과 파일명을 대조한다.
    # 그런 축이 없는 도메인(제품·풍경 등)에서는 이 점수가 그냥 0 이 된다.
    sem = (common.load_yaml(common.TAXONOMY_PATH) or {}).get("_semantic", {}) or {}
    place_axis = (sem.get("place") or {}).get("axis")
    where_names = taxonomy.get(place_axis, {}) if place_axis else {}
    rows = common.read_rows(common.MASTER_META)
    if args.status:
        rows = [r for r in rows if r.get("status") == args.status]
    rows = [r for r in rows if (common.image_path(r)).exists()]
    if not rows:
        print("대상 이미지가 없습니다. auto_label.py 를 먼저 실행하세요.")
        return 1

    # 수집 당시 카테고리(원본 검색 카테고리) 맵: image_id -> category
    collected = {r["raw_file"].split("/")[-1].rsplit(".", 1)[0]: r.get("category", "")
                 for r in common.read_rows(common.ANNOTATIONS / "dedup_kept.csv")}

    clip = load_clip()
    # 인물 검출 점수는 **인물 도메인에서만** 의미가 있다. 인물 없는 도메인에서 켜면
    # 전 이미지가 person_score = -3 을 먹고, _curation.gates 가 그걸 차단하면 데이터가
    # 통째로 사라진다. (실측: 인물 게이트는 버릴 것 48.3% 차단 / 살릴 것 2.1% 손실 — 강력하지만
    #  그건 '주 피사체가 사람'인 도메인 얘기다)
    if common.has_capability("person"):
        from detect import Detector
        det = Detector()
    else:
        print("  인물 도메인이 아닙니다 — person_score 를 건너뜁니다.")
        det = None

    print(f"랭킹 대상 {len(rows)}장 점수 계산 중...")
    recs = []
    cos_by_cat: dict[str, list] = {}   # z-score 용
    for i, r in enumerate(rows, 1):
        path = common.image_path(r)
        w = int(r.get("width") or 0)
        h = int(r.get("height") or 0)
        where = (r.get(place_axis, "") or "").split(";")[0] if place_axis else ""
        rec = {
            "image_id": r["image_id"], "file_path": r["file_path"],
            "collected_category": collected.get(r["image_id"], ""),
            "place": where, "category_name": where_names.get(where, ""),
            "source": r.get("source", ""), "width": w, "height": h,
            "resolution_score": resolution_score(max(w, h)),
            "aspect_score": aspect_score(w, h),
            "source_url": r.get("source_url", ""),
            "review_status": r.get("status", ""),
            "person_score": 0, "clip_score": 0.0, "_cos": None,
        }
        # category_match / keyword_score
        coll = rec["collected_category"]
        # 예전엔 coll.startswith("W") 로 판정했다 — 코드 접두어 규약을 가정한 것이다.
        # 새 도메인이 그 규약을 따를 이유가 없다. 코드 집합으로 직접 본다.
        match = (coll in where_names and coll == where)
        rec["category_match"] = match
        rec["keyword_score"] = 1 if match else 0
        # person — 인물 도메인이 아니면 0 으로 남는다(−3 을 주면 전량 차단된다)
        if det is not None:
            rec["person_score"] = person_score_of(det, path)
        # clip 코사인 (배정 where 프롬프트와)
        if clip is not None and where and place_axis in getattr(clip, "axes", {}):
            try:
                img = clip._encode_image(path)
                codes, feats = clip.axes[place_axis]
                if where in codes:
                    idx = codes.index(where)
                    cos = float((img @ feats[idx].unsqueeze(1))[0, 0])
                    rec["_cos"] = cos
                    cos_by_cat.setdefault(where, []).append(cos)
            except Exception:
                pass
        recs.append(rec)
        if i % 50 == 0:
            print(f"  ...{i}장")

    # clip_score: 카테고리 내 z-score 정규화 후 -2~+2 클리핑
    stats = {}
    for cat, vals in cos_by_cat.items():
        m = statistics.mean(vals)
        sd = statistics.pstdev(vals) or 1.0
        stats[cat] = (m, sd)
    for rec in recs:
        if rec["_cos"] is not None and rec["place"] in stats:
            m, sd = stats[rec["place"]]
            z = (rec["_cos"] - m) / sd
            rec["clip_score"] = round(max(-2.0, min(2.0, z)), 3)
        del rec["_cos"]
        # final_score = Σ(신호 × 가중치). 가중치도 도메인이 정한다.
        rec["final_score"] = round(
            sum(float(wgt) * float(rec.get(sig, 0) or 0)
                for sig, wgt in _WEIGHTS.items()), 3)

    # 카테고리별 final_score 내림차순
    recs.sort(key=lambda x: (x["place"], -x["final_score"]))
    common.write_rows(REVIEW_CSV, REVIEW_FIELDS, recs)

    scores = [r["final_score"] for r in recs]
    print(f"\n완료: {len(recs)}장 → {common.rel(REVIEW_CSV)}")
    print(f"  final_score 범위 {min(scores):.1f} ~ {max(scores):.1f}, 중앙값 {statistics.median(scores):.1f}")
    wrong = sum(1 for r in recs
                if r["collected_category"] in where_names and not r["category_match"])
    print(f"  category_match=False(장소 재분류됨) {wrong}장 → 먼저 훑어 wrong_category 검수")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
