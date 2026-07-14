"""통합 라벨링 앱 — 대시보드 · 검수 · 커버리지를 한 페이지 탭으로.

self-contained index.html 하나를 생성한다.
- 헤더 탭 네비: 대시보드 / 검수 / 커버리지
- 검수 편집창: 7축 라벨 칩 토글 + "버리기(discard)" 버튼 + 이미지 줌
- 동적 UX: 키보드 단축키, 진행바, undo, 미검수 점프, 접속자 표시
- 실시간 협업: config/firebase.json 이 있으면 Firestore 로 실시간 동기화,
  없으면 localStorage + CSV 로 로컬 동작

사용:
    python app.py                  # 승인분
    python app.py --status all      # 전체(거부 포함, 검수용)
    python app.py --thumb 190
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402


def _schema_meta() -> dict:
    """taxonomy.yaml 의 _exclusive/_semantic 을 앱 payload 로 전달(축 코드 하드코딩 제거)."""
    tx = common.load_yaml(common.TAXONOMY_PATH) or {}
    return {"exclusive": tx.get("_exclusive", {}), "semantic": tx.get("_semantic", {}),
            "chip_groups": tx.get("_chip_groups", {})}

AXES = common.AXES              # 축 정의 SSOT (common.py)
MULTI = common.MULTI_AXES


def thumb(path: Path, size: int) -> str | None:
    from PIL import Image
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((size, size))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=70)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="통합 라벨링 앱 생성")
    ap.add_argument("--status", default="approved", help="approved | all")
    ap.add_argument("--thumb", type=int, default=190)
    args = ap.parse_args()

    taxonomy = common.load_taxonomy()
    rows = common.read_rows(common.MASTER_META)
    if args.status != "all":
        rows = [r for r in rows if r.get("status") == args.status]

    IMAGES, DATA = {}, {}
    axis_counts = {ax: Counter() for ax, _, _ in AXES}
    for r in rows:
        p = common.image_path(r)
        uri = thumb(p, args.thumb)
        if not uri:
            continue
        iid = r["image_id"]
        d = {ax: ([s.strip() for s in (r.get(ax) or "").split(";") if s.strip()]
                  if ax in MULTI else (r.get(ax) or "")) for ax, _, _ in AXES}
        d["status"] = r.get("status", "")
        d["url"] = r.get("source_url", "")
        # 도메인 특정 스코어(taxonomy._extra_fields) 를 그대로 싣는다.
        # 예전엔 korean_prob/insta_prob 가 kp/ip 로 박혀 있어, 다른 도메인에선 빈 값이 되어
        # 커버리지 정렬과 대표사진 선택이 무너졌다.
        d["x"] = {f: r.get(f, "") for f in common.EXTRA_FIELDS}
        IMAGES[iid] = uri
        DATA[iid] = d
        for ax, _, multi in AXES:
            for v in (d[ax] if multi else ([d[ax]] if d[ax] else [])):
                axis_counts[ax][v] += 1

    # 대시보드 통계
    comp = common.COMP_AXES
    covered = sum(1 for a in comp for c in taxonomy[a]
                  if axis_counts[a].get(c, 0) >= common.COVERAGE_MIN)
    total_codes = sum(len(taxonomy[a]) for a in comp)
    dash = {
        "total": len(DATA), "covered": covered, "total_codes": total_codes,
        "counts": {ax: dict(axis_counts[ax]) for ax, _, _ in AXES},
    }

    # firebase 설정 (있으면 실시간)
    fb_path = common.ROOT / "config" / "firebase.json"
    fb = json.loads(fb_path.read_text(encoding="utf-8")) if fb_path.exists() else None

    # 관계맵 데이터 (embed_map.py 산출물: coords/attr/clusters/knn). 현재 표시분만.
    emb_path = common.ANNOTATIONS / "embedding.json"
    emb = json.loads(emb_path.read_text(encoding="utf-8")) if emb_path.exists() else None
    if emb and "coords" in emb:
        emb["coords"] = {k: v for k, v in emb["coords"].items() if k in DATA}
        emb["attr"] = {k: v for k, v in emb.get("attr", {}).items() if k in DATA}
        emb["knn"] = {k: [x for x in v if x in DATA]
                      for k, v in emb.get("knn", {}).items() if k in DATA}
    elif emb:
        emb = None  # 구 스키마는 무시

    # 포즈 스켈레톤 (베타 탭용)
    poses_path = common.ANNOTATIONS / "poses.json"
    poses = json.loads(poses_path.read_text(encoding="utf-8")) if poses_path.exists() else {}
    poses = {k: v for k, v in poses.items() if k in DATA}

    # 포즈 정밀 매칭 인덱스 (pose_match.py 산출물: knn/clusters/cluster_of/attr)
    pidx_path = common.ANNOTATIONS / "pose_index.json"
    pidx = json.loads(pidx_path.read_text(encoding="utf-8")) if pidx_path.exists() else None
    if pidx and "knn" in pidx:
        pidx["knn"] = {k: [x for x in v if x[0] in DATA]
                       for k, v in pidx.get("knn", {}).items() if k in DATA}
        pidx["cluster_of"] = {k: v for k, v in pidx.get("cluster_of", {}).items() if k in DATA}
        pidx["attr"] = {k: v for k, v in pidx.get("attr", {}).items() if k in DATA}
        pidx["desc"] = {k: v for k, v in pidx.get("desc", {}).items() if k in DATA}
    elif pidx:
        pidx = None

    # 신호 인덱스 — annotations/*_index.json 을 **자동 발견**해 싣는다.
    # 새 시각 모델을 붙일 때 이 파이썬을 고치지 않는다: build_signal.sh 로 인덱스를 만들면 끝.
    #   visual_index.json  MobileNet (tfjs)         · dino_index.json  DINOv2 (transformers.js)
    #   color_index.json   Lab 3×3 격자 (순수 산술)
    SIGS = {}
    for p in sorted(common.ANNOTATIONS.glob("*_index.json")):
        sid = p.stem.replace("_index", "")
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d.get("emb"), dict):
            continue
        d["emb"] = {k: v for k, v in d["emb"].items() if k in DATA}
        if d["emb"]:
            SIGS[sid] = d
    vidx = SIGS.get("visual")     # 하위호환 (app.js 가 VIDX/CIDX 도 계속 읽는다)
    cidx = SIGS.get("color")

    # 신호 프로필 (check/signal_audit.py 산출물 — 포즈캠 가중치를 '측정된 값'으로 구동).
    # 없으면 app.js 의 폴백 상수로 동작한다(기능 유지, 품질만 옛날 값).
    sp_path = common.ANNOTATIONS / "signal_profile.json"
    sigprof = json.loads(sp_path.read_text(encoding="utf-8")) if sp_path.exists() else None

    # 인물 축 AI 제안 (label/probe_suggest.py 산출물 — 있으면 편집창 힌트로)
    sug_path = common.ANNOTATIONS / "probe_suggestions.csv"
    sug = {}
    for r in common.read_rows(sug_path):
        if r["image_id"] in DATA:
            sug.setdefault(r["image_id"], {})[r["axis"]] = [
                r["code"], round(float(r["conf"]), 2), bool(r.get("auto_ok"))]

    payload = {
        "IMAGES": IMAGES, "DATA": DATA,
        "TAX": {ax: taxonomy[ax] for ax, _, _ in AXES},
        "SCHEMA": _schema_meta(),   # taxonomy._exclusive/_semantic — 하드코딩 제거(템플릿화)
        # mode: assisted(자동+검수) | human(사람 전용) | derived(시스템이 채움 — 검수 칩 없음)
        # comp: 커버리지·부족라벨 집계 대상 축 (taxonomy._axes.composition)
        "AXMETA": [{"key": ax, "name": nm, "multi": m,
                    "mode": common.AXIS_MODE.get(ax, "assisted"),
                    "comp": ax in common.COMP_AXES} for ax, nm, m in AXES],
        "COVERAGE_MIN": common.COVERAGE_MIN,
        # 도메인 특정 컬럼 목록 — 첫 번째를 '대표성 점수'로 쓴다(정렬·대표사진 선택).
        # 없으면 앱이 알아서 다른 기준(품질 점수)으로 폴백한다.
        "EXTRA": common.EXTRA_FIELDS,
        "DASH": dash, "FB": fb, "EMB": emb, "POSES": poses, "PIDX": pidx, "SUG": sug,
        "SIGS": SIGS,        # 자동 발견된 시각·색 신호 인덱스 (app.js 가 VIDX/CIDX 를 여기서 뽑는다)
        "SIGPROF": sigprof,  # 신호 가중치 (check/tune_camw.js 가 실제 파이프라인으로 측정)
        "CURATION": common.load_curation(),   # 목적·게이트·랭킹 (taxonomy._curation)
        # 대시보드 상단의 스토리 임베드는 **있으면 켜고 없으면 끈다.**
        # 예전엔 app.js 가 iframe 을 무조건 그려서, 이 파일이 없는 데이터셋에선
        # 깨진 빈 박스가 떴다. 프로젝트 고유 콘텐츠를 코드가 당연시하면 안 된다.
        "STORY": (common.SAMPLES / "lora_story.html").exists()
                 or (common.ROOT / "docs" / "lora_story.html").exists(),
    }
    # '</' 이스케이프: source_url/notes 등 문자열에 '</script>' 가 섞여도 인라인 스크립트가
    # 끊기지 않게 한다("<\/" 는 JSON/JS 모두에서 '/'와 동일).
    payload_js = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    # 데이터셋 이름 — taxonomy._name. 없으면 폴더명을 쓴다(하드코딩 제거).
    tx = common.load_yaml(common.TAXONOMY_PATH) or {}
    name = tx.get("_name") or common.DATASET_ROOT.name
    page = (_load_template()
            .replace("/*DATA*/", "window.__APP__=" + payload_js + ";")
            .replace("/*TITLE*/", f"원격 라벨링 · {name}")
            .replace("/*SUBTITLE*/", name))
    out = common.SAMPLES / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)   # 새 데이터셋에는 이 폴더가 없다
    out.write_text(page, encoding="utf-8")
    print(f"통합 앱 생성: {common.rel(out)}  ({len(DATA)}장, {out.stat().st_size/1e6:.1f}MB)")
    print(f"  실시간 협업: {'ON (Firebase 설정 감지)' if fb else 'OFF (config/firebase.json 없음 → 로컬 모드)'}")
    return 0


# --- UI 템플릿 (ui/index.html + app.css + app.js 를 조립) -------------------
# 프론트엔드를 파이썬에서 분리 → 디자인/로직을 각각 독립적으로 수정 가능(템플릿화).
# 산출물 index.html 은 분리 전과 바이트 동일.
_UI = Path(__file__).resolve().parent / "ui"


def _load_template() -> str:
    html = (_UI / "index.html").read_text(encoding="utf-8")
    html = html.replace("/*CSS*/", (_UI / "app.css").read_text(encoding="utf-8"))
    html = html.replace("/*JS*/", (_UI / "app.js").read_text(encoding="utf-8"))
    return html


if __name__ == "__main__":
    raise SystemExit(main())
