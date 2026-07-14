"""파이프라인 정합성 검사 (읽기 전용).

taxonomy.yaml 을 기준으로 프롬프트·앱·데이터·산출물이 서로 어긋나지 않았는지
자동 검증한다. 위반이 있으면 exit 1 (CI/훅에서 사용 가능).

검사 항목:
  1. taxonomy ↔ clip_label 프롬프트 dict 코드 1:1
  2. app.py 소스의 코드 리터럴이 전부 taxonomy 에 존재 (stale 참조 검출)
  3. master CSV 사용 코드 ⊆ taxonomy (+ 미사용 코드는 정보로 출력)
  4. reference_index.parquet: 필수 컬럼·행수·embed_model 계열 일치
  5. 산출 JSON 스키마 스탬프 (embedding.json / pose_index.json)
  6. 파일 계층 수량: approved = parquet = embedding 좌표, curated/768 ≥ approved

사용: python scripts/check/check_consistency.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

FAILS: list[str] = []
WARNS: list[str] = []


def fail(msg: str) -> None:
    FAILS.append(msg)
    print(f"  ✗ {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def warn(msg: str) -> None:
    WARNS.append(msg)
    print(f"  ⚠ {msg}")


def todo(msg: str) -> None:
    """**아직 안 돌린 것**과 **틀어진 것**은 다르다.

    산출물이 통째로 없는 건 파이프라인의 다음 단계일 뿐이다. 그걸 '실패'로 찍으면
    갓 클론한 사람에게 템플릿이 고장난 것처럼 보인다. 실패는 '있는데 안 맞을 때'만.
    """
    print(f"  · {msg}")


def check_prompts(tax: dict) -> None:
    print("[1] taxonomy ↔ prompts.yaml")
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "label"))
    import clip_label
    axes = clip_label.AXIS_PROMPTS         # prompts.yaml 의 axes 블록
    if not axes:
        warn(f"prompts.yaml 이 없거나 비어 있다 ({clip_label.PROMPTS_PATH.name}) — 자동라벨 불가")
        return

    # 자동라벨이 필요한 축(assisted) 중 프롬프트가 없는 것 — 새 도메인에서 가장 먼저 만나는 일
    need = [a for a in common.LABEL_AXES
            if common.AXIS_MODE.get(a, "assisted") == "assisted" and a not in axes]
    if need:
        fail(f"프롬프트가 없는 축: {need} — prompts.yaml 의 axes 에 영문 zero-shot 문구를 써야 한다")

    for axis, prompts in axes.items():
        if axis not in tax:
            fail(f"prompts.yaml 에 taxonomy 에 없는 축 '{axis}'")
            continue
        t, p = set(tax[axis]), set(prompts)
        if t == p:
            ok(f"{axis}: {len(t)}코드 일치")
        else:
            if p - t:
                fail(f"{axis}: 프롬프트에만 있는 코드 {sorted(p - t)}")
            if t - p:
                fail(f"{axis}: 프롬프트 누락 코드 {sorted(t - p)}")

    # 이진 판정(filters)이 선언한 CSV 컬럼이 실제 스키마에 있는가
    for name, f in (clip_label.FILTERS or {}).items():
        fld = f.get("field")
        if fld and fld not in common.MASTER_FIELDS:
            fail(f"filters.{name}.field '{fld}' 가 MASTER_FIELDS 에 없다 "
                 f"— taxonomy._extra_fields 에 추가하라")
    if clip_label.FILTERS:
        ok(f"이진 판정 {len(clip_label.FILTERS)}개 (필드 스키마 일치)")

    if clip_label.MODEL_NAME != common.CLIP_MODEL:
        fail(f"CLIP 모델 불일치: clip_label {clip_label.MODEL_NAME} vs common {common.CLIP_MODEL}")
    else:
        ok(f"CLIP 모델 계열 일치: {common.CLIP_MODEL}/{common.CLIP_PRETRAINED}")


def check_app_codes(all_codes: set[str]) -> None:
    print("[2] UI 코드 리터럴 (stale 참조)")
    ui = Path(__file__).resolve().parent.parent / "review" / "ui"
    src = "\n".join((ui / n).read_text(encoding="utf-8") for n in ("index.html", "app.js"))
    used = set(re.findall(r"\b([WPFSGNE]\d{2}_[a-z0-9_]+)\b", src))
    stale = used - all_codes
    if stale:
        fail(f"taxonomy 에 없는 코드 참조: {sorted(stale)}")
    else:
        ok(f"{len(used)}개 코드 리터럴 전부 taxonomy 에 존재")


def check_master(tax: dict) -> int:
    print("[3] master CSV 라벨 코드")
    rows = common.read_rows(common.MASTER_META)
    approved = [r for r in rows if r.get("status") == "approved"]
    bad = 0
    for r in rows:
        for axis in common.LABEL_AXES:
            for c in common.split_codes(axis, r.get(axis)):
                if c not in tax.get(axis, {}):
                    bad += 1
                    if bad <= 5:
                        fail(f"{r['image_id']} {axis}: 미정의 코드 '{c}'")
    if not bad:
        ok(f"전 {len(rows)}행 코드 유효 (approved {len(approved)})")
    unused = [f"{a}:{c}" for a in common.COMP_AXES for c in tax.get(a, {})
              if not any(c in common.split_codes(a, r.get(a)) for r in approved)]
    if unused:
        print(f"  ℹ approved 에서 미사용(백필 후보): {', '.join(unused)}")
    return len(approved)


def check_parquet(n_approved: int) -> None:
    print("[4] reference_index.parquet")
    path = common.ANNOTATIONS / "reference_index.parquet"
    if not path.exists():
        todo("parquet 없음 — 아직 build_index 를 안 돌렸다 "
             "(python -m scripts.index.build_index)")
        return
    import pandas as pd
    df = pd.read_parquet(path)
    need = {"image_id", "pose_kp", "pose_ok", "embed", "rarity", "cluster_id",
            "index_version", "embed_model"}
    missing = need - set(df.columns)
    if missing:
        fail(f"필수 컬럼 누락: {sorted(missing)}")
    else:
        ok(f"필수 컬럼 {len(need)}개 존재 (index_version {df.index_version.dropna().unique().tolist()})")
    if len(df) != n_approved:
        fail(f"행수 {len(df)} ≠ approved {n_approved} — build_index 재실행 필요")
    else:
        ok(f"행수 = approved = {n_approved}")
    want = f"{common.CLIP_MODEL}/{common.CLIP_PRETRAINED}"
    models = set(df.embed_model.dropna().unique())
    if models != {want}:
        fail(f"embed_model {models} ≠ {want}")
    else:
        ok(f"embed_model 전행 {want}")


def check_json_stamps(n_approved: int) -> None:
    print("[5] 산출 JSON 스키마 스탬프")
    emb_p = common.ANNOTATIONS / "embedding.json"
    if emb_p.exists():
        emb = json.loads(emb_p.read_text(encoding="utf-8"))
        schema = (emb.get("meta") or {}).get("schema")
        if schema != "embed-map-0.1":
            fail(f"embedding.json schema '{schema}' ≠ embed-map-0.1 — embed_map.py 재실행 필요")
        elif len(emb.get("coords", {})) != n_approved:
            fail(f"embedding 좌표 {len(emb['coords'])} ≠ approved {n_approved}")
        else:
            ok(f"embedding.json embed-map-0.1 · 좌표 {n_approved}")
    else:
        warn("embedding.json 없음")
    pidx_p = common.ANNOTATIONS / "pose_index.json"
    if pidx_p.exists():
        pidx = json.loads(pidx_p.read_text(encoding="utf-8"))
        schema = (pidx.get("meta") or {}).get("schema")
        if schema != "pose-index-0.2":
            fail(f"pose_index.json schema '{schema}' ≠ pose-index-0.2 — pose_match.py 재실행 필요")
        else:
            ok(f"pose_index.json pose-index-0.2 · 매칭대상 {pidx['meta'].get('n')}")
    else:
        warn("pose_index.json 없음")


def check_files(n_approved: int) -> None:
    print("[6] 파일 계층 수량")
    n_cur = sum(1 for p in common.CURATED_IMAGES.glob("*") if p.suffix.lower() in common.IMAGE_EXTS)
    n_768 = sum(1 for p in (common.PROCESSED / "768").glob("*") if p.suffix.lower() in common.IMAGE_EXTS)
    for name, n in (("curated/images", n_cur), ("processed/768", n_768)):
        if n == 0 and n_approved:
            todo(f"{name} 비어 있음 — 아직 "
                 f"{'promote.py' if name.startswith('curated') else 'resize.py'} 를 안 돌렸다")
        elif n < n_approved:
            fail(f"{name} {n} < approved {n_approved} — 이미지 누락")
        elif n > n_approved:
            warn(f"{name} {n} > approved {n_approved} — 잉여 {n - n_approved}장 (검수 discard 분, cleanup.py 후보)")
        else:
            ok(f"{name} = approved = {n}")


def check_imports() -> None:
    """모든 스크립트가 실제로 import 되는가.

    ast.parse 만으로는 부족하다 — 폴더를 옮기면 문법은 멀쩡한데 `from crawl.sources import ...`
    같은 임포트가 깨진다. 실제로 커밋 f798e10 이 이 상태로 푸시됐고 문법 검사는 통과했다.
    무거운 의존(torch/open_clip 등)은 없을 수 있으므로 ModuleNotFoundError 중 외부 패키지는
    경고로만 처리하고, **저장소 내부 모듈**이 안 잡히는 것만 실패로 본다.
    """
    import importlib
    print("[0] 스크립트 임포트")
    scripts = common.ROOT / "scripts"
    sys.path.insert(0, str(scripts))
    # 저장소 안에 있는 이름들 — 이게 안 잡히면 임포트 경로가 깨진 것(실패).
    # 그 밖의 이름은 torch/open_clip 같은 미설치 외부 패키지일 뿐이다(경고).
    internal = {p.name for p in scripts.iterdir() if p.is_dir() and (p / "__init__.py").exists()}
    internal |= {p.stem for p in scripts.rglob("*.py")}
    n = 0
    for p in sorted(scripts.rglob("*.py")):
        if p.name == "__init__.py":
            continue
        mod = ".".join(p.relative_to(scripts).with_suffix("").parts)
        sys.path.insert(0, str(p.parent))   # 스크립트로 직접 실행할 때와 같은 경로 조건
        try:
            importlib.import_module(mod)
            n += 1
        except ModuleNotFoundError as e:
            missing = (e.name or "").split(".")[0]
            if missing in internal:
                fail(f"{mod}: 내부 모듈 '{e.name}' 을 못 찾음 — 폴더 이동 후 임포트 미수정")
            else:
                warn(f"{mod}: 외부 패키지 '{missing}' 미설치 (검사 스킵)")
        except Exception as e:
            fail(f"{mod}: {type(e).__name__} {e}")
        finally:
            sys.path.pop(0)
    ok(f"{n}개 스크립트 임포트 성공")


def main() -> int:
    tax = common.load_taxonomy()
    all_codes = {c for codes in tax.values() for c in codes}
    check_imports()
    check_prompts(tax)
    check_app_codes(all_codes)
    n_approved = check_master(tax)
    check_parquet(n_approved)
    check_json_stamps(n_approved)
    check_files(n_approved)
    print()
    if FAILS:
        print(f"실패 {len(FAILS)}건 · 경고 {len(WARNS)}건")
        return 1
    print(f"전 항목 통과 (경고 {len(WARNS)}건)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
