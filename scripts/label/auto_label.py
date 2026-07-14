"""AI 자동 라벨링 (가이드 11절 4단계).

dedup 을 통과한 이미지에 대해:
  1) 1차 라벨 - 수집 검색어의 카테고리 코드를 해당 축 초기값으로 설정
  2) 2차 라벨 - ANTHROPIC_API_KEY 가 있으면 claude 비전으로 4축을 재분류하고
     subject_visible / quality_score / watermark 를 판정
결과를 annotations/master_metadata.csv 에 기록한다. 이미 라벨된 이미지는 스킵하므로
중단 후 재개할 수 있다.

사용:
    python auto_label.py                 # AI 사용 (키 있으면)
    python auto_label.py --no-ai         # 검색어 1차 라벨만
    python auto_label.py --limit 20      # 앞 20장만 (비용 테스트)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

KEPT_CSV = common.ANNOTATIONS / "dedup_kept.csv"
MODEL = "claude-haiku-4-5"

# 카테고리 코드 접두어 -> 축
# 코드가 어느 축에 속하는지 — taxonomy 를 역인덱싱한다.
# 예전엔 {"W":"where","P":"pose_action"} 처럼 **코드 접두어 규약**을 가정했다. 새 도메인이
# 그 규약을 따를 이유가 없다(V01_monstera 의 'V'가 뭔지 코드가 알 리 없다).
_CODE_AXIS = {c: ax for ax, codes in common.load_taxonomy().items()
              if isinstance(codes, dict) for c in codes}


def axis_of(code: str) -> str | None:
    return _CODE_AXIS.get(code)


def _clip_available() -> bool:
    import importlib.util
    return importlib.util.find_spec("open_clip") is not None


def image_id_from_raw(raw_file: str) -> str:
    # data/.../raw_000001.jpg -> raw_000001
    return Path(raw_file).stem


def build_prompt(taxonomy: dict) -> str:
    """비전 라벨러 프롬프트 — **taxonomy 가 쓴다.**

    예전엔 "당신은 인물 사진의 '구도'를 분류하는 어노테이터입니다" 로 시작해 4축을 못박고,
    "사람이 뚜렷이 보이지 않으면 person_visible=false" 라고 지시했다. 음식 데이터셋에
    그대로 쓰면 라벨러가 사람을 찾다가 전부 버린다. 목적 문장은 _curation.purpose 에서,
    축·코드는 taxonomy 에서, 멀티 여부는 _axes 에서 온다.
    """
    purpose = (common.load_curation().get("purpose") or "").strip()
    lines = [f"당신은 이미지를 분류하는 어노테이터입니다. 데이터셋 목적: {purpose or '(미지정)'}",
             f"이미지를 보고 아래 {len(taxonomy)}개 축의 라벨을 지정하세요. "
             "각 축의 코드 중에서만 고르세요.\n"]
    for axis, codes in taxonomy.items():
        multi = " (해당하는 것 여러 개 가능)" if axis in common.MULTI_AXES else " (하나만)"
        lines.append(f"[{axis}]{multi}")
        for code, ko in codes.items():
            lines.append(f"  {code} = {ko}")
        lines.append("")
    lines.append("판단 규칙:")
    lines.append(f"- 이 데이터셋의 주 피사체가 뚜렷이 보이지 않으면 subject_visible=false. "
                 f"(목적: {purpose or '위 참조'})")
    lines.append("- 애매하면 가장 가까운 코드를 고르되 notes 에 이유를 적으세요.")
    lines.append("- quality_score: 학습 데이터로서의 품질 1(나쁨)~5(좋음).")
    lines.append("- watermark: 워터마크/로고가 크게 있으면 true.")
    return "\n".join(lines)


def label_tool(taxonomy: dict) -> dict:
    """비전 라벨러의 tool 스키마도 taxonomy 에서 만든다."""
    props = {"subject_visible": {"type": "boolean"},
             "quality_score": {"type": "integer", "minimum": 1, "maximum": 5},
             "watermark": {"type": "boolean"},
             "notes": {"type": "string"}}
    for axis in taxonomy:
        props[axis] = ({"type": "array", "items": {"type": "string"}}
                       if axis in common.MULTI_AXES else {"type": "string"})
    return {
        "name": "label_image",
        "description": "이미지의 축별 라벨과 품질을 반환",
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": ["subject_visible", *taxonomy, "quality_score", "watermark"],
        },
    }


def encode_image(path: Path, max_side: int = 768) -> tuple[str, str]:
    from PIL import Image
    with Image.open(path) as im:
        im = im.convert("RGB")
        w, h = im.size
        scale = min(1.0, max_side / max(w, h))
        if scale < 1.0:
            im = im.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode(), "image/jpeg"


def ai_label(client, prompt: str, tool: dict, path: Path) -> dict | None:
    b64, media = encode_image(path)
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            tools=[tool],
            tool_choice={"type": "tool", "name": "label_image"},
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": media, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
    except Exception as e:
        print(f"    [ai] 실패: {e}")
        return None
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input
    return None


def validate_codes(labels: dict, valid: dict[str, set]) -> dict:
    """AI 가 축을 벗어난 코드를 냈으면 빈 값으로 정리."""
    out = dict(labels)
    for axis in common.LABEL_AXES:          # 축 이름을 코드가 알 필요가 없다
        if axis not in valid or axis not in out:
            continue
        v = out[axis]
        if isinstance(v, list):             # 멀티축: 유효 코드만 남긴다
            out[axis] = ";".join(c for c in v if c in valid[axis])
        elif v not in valid[axis]:          # 단일축: 무효면 비운다
            out[axis] = ""
    return out


def main() -> int:
    common.load_env()
    ap = argparse.ArgumentParser(description="자동 라벨링 (Anthropic 비전 또는 로컬 CLIP)")
    ap.add_argument("--backend", choices=["auto", "anthropic", "clip", "none"],
                    default="auto",
                    help="auto=키 있으면 anthropic, 없으면 clip, 둘 다 없으면 none. "
                         "clip=로컬 CLIP(키 불필요), none=검색어 1차 라벨만")
    ap.add_argument("--no-ai", action="store_true", help="(구버전 호환) --backend none 과 동일")
    ap.add_argument("--limit", type=int, help="처리할 최대 이미지 수")
    # 스타일 필터: 외국/스톡 느낌 이미지를 rejected 로 (CLIP 백엔드에서만 동작)
    ap.add_argument("--style-filter", action="store_true",
                    help="한국인·인스타 감성이 아닌 이미지를 rejected 처리 (CLIP 전용)")
    ap.add_argument("--korean-min", type=float, default=0.5,
                    help="한국/동아시아 인물 확률 하한 (기본 0.5)")
    ap.add_argument("--insta-min", type=float, default=0.4,
                    help="인스타 감성 확률 하한 (기본 0.4)")
    args = ap.parse_args()

    taxonomy = common.load_taxonomy()
    valid = common.all_taxonomy_codes(taxonomy)

    kept = common.read_rows(KEPT_CSV)
    if not kept:
        print("dedup_kept.csv 가 비어 있습니다. 먼저 dedup.py 를 실행하세요.")
        return 1

    # 재개: 이미 master 에 있는 image_id 는 스킵
    labeled = {r["image_id"] for r in common.read_rows(common.MASTER_META)}

    # --- 백엔드 결정 ---------------------------------------------------------
    backend = "none" if args.no_ai else args.backend
    has_key = bool(common.env("ANTHROPIC_API_KEY"))
    if backend == "auto":
        backend = "anthropic" if has_key else ("clip" if _clip_available() else "none")

    label_fn = None   # (path) -> labels dict | None
    if backend == "anthropic":
        try:
            import anthropic
            client = anthropic.Anthropic()
            prompt = build_prompt(taxonomy)
            tool = label_tool(taxonomy)
            label_fn = lambda p: ai_label(client, prompt, tool, p)  # noqa: E731
            print("백엔드: Anthropic 비전 라벨링")
        except Exception as e:
            print(f"Anthropic 초기화 실패 -> CLIP 시도: {e}")
            backend = "clip"

    if backend == "clip":
        try:
            from clip_label import ClipLabeler
            print("백엔드: 로컬 CLIP 라벨링 (최초 1회 모델 다운로드) ...")
            clip = ClipLabeler()
            label_fn = clip.label
            print("CLIP 모델 로드 완료")
        except Exception as e:
            print(f"CLIP 사용 불가 -> 검색어 1차 라벨만: {e}")
            backend = "none"

    if backend == "none":
        print("자동 라벨링 없음 -> 검색어 기반 1차 라벨만 기록")

    # --limit 은 **카테고리별로 고르게** 자른다.
    # 앞에서부터 N장을 자르면 크롤이 카테고리 순으로 저장하므로 첫 카테고리만 라벨링된다.
    # 그 상태로 코드 분포를 보면 "한 코드가 97% — 축이 죽었다"는 오판이 나온다
    # (인테리어 도메인 부트스트랩에서 실제로 space 축을 접을 뻔했다).
    todo = [r for r in kept if image_id_from_raw(r["raw_file"]) not in labeled]
    n_skip = len(kept) - len(todo)
    if args.limit and args.limit < len(todo):
        by_cat: dict[str, list] = {}
        for r in todo:
            by_cat.setdefault(r.get("category", ""), []).append(r)
        picked, i = [], 0
        while len(picked) < args.limit:
            added = False
            for rows_ in by_cat.values():          # 라운드로빈
                if i < len(rows_) and len(picked) < args.limit:
                    picked.append(rows_[i])
                    added = True
            if not added:
                break
            i += 1
        todo = picked
        print(f"표본 {len(todo)}장 (카테고리 {len(by_cat)}개에서 고르게)")

    n_done = n_style_rej = 0
    for row in todo:
        iid = image_id_from_raw(row["raw_file"])

        path = common.raw_path(row)
        if not path.exists():
            continue

        # 1차 라벨: 검색 카테고리
        cat = row.get("category", "")
        ax = axis_of(cat)
        rec = {
            "image_id": iid,
            "file_path": row["raw_file"],   # promote 전까지는 raw 경로
            "source": row["source"],
            "source_url": row.get("source_url", ""),
            "downloaded_at": row.get("downloaded_at", ""),
            "width": row.get("width", ""),
            "height": row.get("height", ""),
            # 1차 라벨은 '수집 카테고리'가 속한 축에만 들어간다 (축 이름 무관)
            **{a: (cat if ax == a else "") for a in common.LABEL_AXES},
            "quality_score": "",
            "status": "needs_review",
            "notes": "",
            "license": row.get("license", ""),
            "author": row.get("author", ""),
            "usage_allowed": _usage(row.get("license", "")),
            "phash": row.get("phash", ""),
            **{f: "" for f in common.EXTRA_FIELDS},   # 도메인 특정 컬럼
        }

        if label_fn is not None:
            labels = label_fn(path)
            if labels:
                labels = validate_codes(labels, valid)
                for axis in common.LABEL_AXES:        # 축 이름을 코드가 알 필요가 없다
                    if labels.get(axis):
                        rec[axis] = labels[axis]
                rec["quality_score"] = labels.get("quality_score", "")
                for f in common.EXTRA_FIELDS:         # 도메인 특정 컬럼(korean_prob 등)
                    if f in labels:
                        rec[f] = labels[f]
                notes = labels.get("notes", "") or ""
                # 거절 규칙은 **prompts.yaml 의 filters 가 선언한다.**
                # 예전엔 '사람이 안 보이면 버린다'가 코드에 박혀 있어, 인물 없는 도메인에서
                # 데이터가 전량 rejected 됐다. 이제는 그 도메인의 prompts.yaml 이
                # subject_visible 을 자기 피사체로 정의하고 reject 조건도 스스로 정한다.
                bad = [r for r in (labels.get("_reject") or {}).values() if not r["ok"]]
                if bad:
                    rec["status"] = "rejected"
                    notes = (bad[0]["note"] + ". " + notes).strip()
                elif labels.get("watermark"):
                    rec["status"] = "needs_review"
                    notes = ("워터마크 있음. " + notes).strip()
                # 스타일 필터: 외국/스톡 느낌이면 rejected
                if args.style_filter and rec["status"] != "rejected":
                    kp = labels.get("korean_prob", 1.0)
                    ip = labels.get("insta_prob", 1.0)
                    if kp < args.korean_min or ip < args.insta_min:
                        rec["status"] = "rejected"
                        notes = (f"외국/스톡 느낌(한국인 {kp:.2f}/인스타 {ip:.2f}). "
                                 + notes).strip()
                        n_style_rej += 1
                rec["notes"] = notes

        common.append_row(common.MASTER_META, common.MASTER_FIELDS, rec)
        # 자동라벨 불변 스냅샷 — master 는 사람 검수로 덮이므로, '모델이 원래 뭐라 했는지'를
        # 따로 남겨야 나중에 축 감사(check/axis_audit.py)가 모델을 채점할 수 있다.
        common.append_row(common.AUTO_LABELS, common.AUTO_LABEL_FIELDS,
                          {**rec, "labeled_at": common.today()})
        n_done += 1
        if n_done % 10 == 0:
            print(f"  ...{n_done}장 라벨링")

    print(f"라벨링 완료: 신규 {n_done}장 | 기존 스킵 {n_skip}장"
          + (f" | 스타일필터 rejected {n_style_rej}장" if args.style_filter else ""))
    print(f"메타데이터: {common.rel(common.MASTER_META)}")
    _report(args.limit)
    print("\n다음 단계: 검수 앱(scripts/review/app.py)에서 검수 후 promote.py")
    return 0


def _report(limited: int | None) -> None:
    """새 도메인 진단 — 코드 분포를 **여기서** 보여준다.

    이게 없으면 taxonomy 초안이 좋은지 나쁜지 알 방법이 사람이 CSV 를 직접 세는 것뿐이다.
    새 도메인에서 가장 먼저 알아야 할 세 가지를 그대로 찍는다:
      · 전량 rejected 인가?        → prompts.yaml 의 subject_visible 이 틀렸다
      · 0회 코드가 있나?           → 프롬프트가 나쁘거나 데이터에 없는 코드다
      · 한 코드가 90% 이상인가?    → 축에 정보가 없다(죽음). 쪼개거나 접어라
    """
    from collections import Counter

    rows = common.read_rows(common.MASTER_META)
    if not rows:
        return
    n = len(rows)
    st = Counter(r.get("status", "") for r in rows)
    rej = st.get("rejected", 0)
    print(f"\n■ 진단 (총 {n}장) — status {dict(st)}")
    if rej >= n * 0.9:
        print("  ✗ 거의 전량 rejected — prompts.yaml 의 filters.subject_visible 이 이 도메인의")
        print("    주 피사체를 가리키고 있는지 확인하라. (인물용 문장이 남아 있으면 음식·공간은 전멸한다)")

    tx = common.load_taxonomy()
    dead, zero_all = [], []
    for ax in common.LABEL_AXES:
        codes = list(tx.get(ax) or {})
        if not codes:
            continue
        c = Counter()
        for r in rows:
            c.update(common.split_codes(ax, r.get(ax)))
        zeros = [k for k in codes if not c[k]]
        top_share = (max(c.values()) / n) if c else 0.0
        mark = "💀 죽음" if top_share >= 0.9 else ("⚠ 쏠림" if top_share >= 0.7 else "✓")
        print(f"  {mark} {ax:14s} " + " · ".join(
            f"{k.split('_', 1)[-1]}:{c[k]}" for k in sorted(codes, key=lambda k: -c[k])[:5]))
        if zeros:
            print(f"      0회 코드: {', '.join(zeros)}")
            zero_all += zeros
        if top_share >= 0.9:
            dead.append(ax)

    if dead:
        print(f"\n  💀 한 코드가 90% 이상인 축: {', '.join(dead)}")
        print("     — 이 축은 정보량이 거의 없다. 코드를 쪼개거나 축을 접어라.")
    if zero_all:
        print(f"\n  ⚠ 0회 코드 {len(zero_all)}개 — 프롬프트가 나쁘거나 데이터에 없는 코드다.")
    if limited:
        print("\n  ℹ --limit 표본 기준이다. 판정을 믿으려면 전량 라벨링 후 다시 보라.")


def _usage(license_str: str) -> str:
    """라이선스 문자열로 사용 가능성 추정. unknown 이면 실험용으로만."""
    if not license_str or license_str == "unknown":
        return "experiment_only"
    return "yes"


if __name__ == "__main__":
    raise SystemExit(main())
