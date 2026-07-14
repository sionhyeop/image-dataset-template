"""라벨러 임계값 자동 튜닝 — 사람이 찍던 숫자를 사람 정답으로 측정해서 정한다.

문제:
    멀티축에서 '몇 개의 코드를 붙일 것인가'는 임계값이 정한다(MULTI_REL, GROUP_MIN…).
    이걸 손으로 찍으면 축마다 최적값이 다르므로 반드시 틀린다. 실제로 손으로 찍은
    한 벌의 임계값을 쓰자 camera_style 은 +5.9%p 좋아지고 pose_action 은 -9.6%p 나빠졌다
    (장당 코드수 0.96 → 2.10 로 과다 예측). 한 벌로는 안 된다.

해법:
    사람 검수 라벨을 정답지로 놓고 **축별로** 임계값을 스윕해 micro-F1 을 최대화한다.
    도메인 지식이 필요 없다 — 새 이미지셋에서도 그냥 다시 돌리면 된다.

비용:
    reference_index.parquet 에 CLIP 이미지 임베딩이 이미 있으므로 이미지를 다시 인코딩하지
    않는다. 텍스트 프롬프트만 한 번 인코딩하면 나머지는 행렬곱 — 수천 조합을 몇 초에 훑는다.

출력:
    annotations/label_profile.json — clip_label.py 가 있으면 읽어서 쓴다(없으면 기본값).

사용:
    python scripts/check/tune_labels.py            # 표만
    python scripts/check/tune_labels.py --write    # label_profile.json 저장
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402

# 스윕 격자 — 촘촘할 필요 없다. 넓게 훑고 최적 근방을 고른다.
GRID_REL = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 1.01]   # 1.01 = 사실상 argmax 1개
GRID_ABS = [0.02, 0.05, 0.10, 0.20]
GRID_MAX = [1, 2, 3]
GRID_GROUP = [0.0, 0.35, 0.45, 0.55, 0.65, 0.75]              # 0.0 = 그룹에서 항상 1개 고름


def micro_f1(pairs) -> float:
    tp = sum(len(p & h) for p, h in pairs)
    fp = sum(len(p - h) for p, h in pairs)
    fn = sum(len(h - p) for p, h in pairs)
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def main() -> int:
    ap = argparse.ArgumentParser(description="라벨러 임계값을 사람 정답으로 튜닝")
    ap.add_argument("--write", action="store_true", help="annotations/label_profile.json 저장")
    args = ap.parse_args()

    import numpy as np
    import open_clip
    import pandas as pd
    import torch

    import clip_label as CL

    # --- 사람 정답 + 기존 CLIP 임베딩 (이미지 재인코딩 없음) ---------------------
    gold = {r["image_id"]: r for r in common.read_rows(common.MASTER_META)
            if (r.get("reviewer") or "").strip() and r.get("status") == "approved"}
    df = pd.read_parquet(common.ANNOTATIONS / "reference_index.parquet")
    df = df[df["image_id"].isin(gold) & df["embed"].notna()]
    if len(df) < 50:
        print(f"사람 검수 + 임베딩 교집합 {len(df)}장 — 튜닝 불가 (최소 50장)")
        return 1
    ids = df["image_id"].tolist()
    img = np.stack(df["embed"].to_numpy()).astype(np.float32)
    img /= np.linalg.norm(img, axis=1, keepdims=True) + 1e-8
    print(f"사람 정답 {len(ids)}장 · CLIP 임베딩 재사용 (이미지 인코딩 0회)")

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    model, _, _ = open_clip.create_model_and_transforms(CL.MODEL_NAME, pretrained=CL.PRETRAINED)
    model.eval().to(dev)
    tok = open_clip.get_tokenizer(CL.MODEL_NAME)
    IT = torch.from_numpy(img).to(dev)

    # 프롬프트는 prompts.yaml 이 SSOT 다. 예전엔 clip_label 의 WHERE_PROMPTS 같은
    # 모듈 상수를 직접 잡았는데, 그 상수들이 사라진 뒤로 이 스크립트는 AttributeError 로
    # 죽어 있었다 — 아무도 안 돌려봐서 몰랐다.
    PROMPTS = dict(CL.AXIS_PROMPTS)
    excl = (common.load_yaml(common.TAXONOMY_PATH) or {}).get("_exclusive", {})

    profile, rows = {}, []
    for ax, pmap in PROMPTS.items():
        codes = list(pmap)
        with torch.no_grad():
            tf = model.encode_text(tok(list(pmap.values())).to(dev))
            tf /= tf.norm(dim=-1, keepdim=True)
        P = (100.0 * IT @ tf.T).softmax(dim=-1).cpu().numpy()   # (N, C)
        human = [set(common.split_codes(ax, gold[i].get(ax))) for i in ids]
        keep = [k for k, h in enumerate(human) if h]
        P, human = P[keep], [human[k] for k in keep]
        n = len(human)

        if ax not in common.MULTI_AXES:                          # 단일축: 튜닝할 게 없다
            pred = [{codes[int(P[k].argmax())]} for k in range(n)]
            f1 = micro_f1(list(zip(pred, human)))
            profile[ax] = {"mode": "single"}
            rows.append((ax, n, f1, f1, "argmax (단일축)", 1.0))
            continue

        groups = [[i for i, c in enumerate(codes) if c in g] for g in excl.get(ax, [])]
        groups = [g for g in groups if len(g) > 1]
        grouped = {i for g in groups for i in g}
        rest = [i for i in range(len(codes)) if i not in grouped]

        # 현재(손으로 찍은) 설정의 F1 — 비교 기준
        def build(gmin, rel, absmin, mx):
            out = []
            for k in range(n):
                p = P[k]
                sel = set()
                for g in groups:
                    sub = p[g] / max(1e-9, p[g].sum())          # 그룹 안에서만 재정규화
                    b = int(sub.argmax())
                    if float(sub[b]) >= gmin:
                        sel.add(codes[g[b]])
                if rest:
                    sr = p[rest] / max(1e-9, p[rest].sum())
                    thr = max(absmin, rel * float(sr.max()))
                    picked = sorted([j for j in range(len(rest)) if float(sr[j]) >= thr],
                                    key=lambda j: -sr[j])[:mx]
                    sel.update(codes[rest[j]] for j in picked)
                if not sel:
                    sel = {codes[int(p.argmax())]}
                out.append(sel)
            return list(zip(out, human))

        cur = micro_f1(build(CL.GROUP_MIN, CL.MULTI_REL, CL.MULTI_ABS_MIN, CL.MULTI_MAX))
        best, bcfg = -1.0, None
        for gmin in (GRID_GROUP if groups else [0.0]):
            for rel in (GRID_REL if rest else [1.01]):
                for absmin in (GRID_ABS if rest else [0.0]):
                    for mx in (GRID_MAX if rest else [0]):
                        f1 = micro_f1(build(gmin, rel, absmin, mx))
                        if f1 > best:
                            best, bcfg = f1, (gmin, rel, absmin, mx)
        gmin, rel, absmin, mx = bcfg
        profile[ax] = {"mode": "multi", "group_min": gmin, "rel": rel,
                       "abs_min": absmin, "max_extra": mx}
        avg = sum(len(p) for p, _ in build(*bcfg)) / n
        rows.append((ax, n, cur, best,
                     f"group≥{gmin:.2f} rel={rel:.2f} abs={absmin:.2f} max={mx}", avg))

    print(f"\n{'축':13s}{'n':>5s}{'손으로 찍은 값':>13s}{'튜닝 후':>10s}{'개선':>8s}  최적 설정")
    print("-" * 92)
    for ax, n, cur, best, cfg, avg in rows:
        print(f"{ax:13s}{n:5d}{cur:13.1%}{best:10.1%}{best-cur:+8.1%}  {cfg}  (장당 {avg:.2f}개)")

    if args.write:
        p = common.ANNOTATIONS / "label_profile.json"
        common.write_json_atomic(p, json.dumps(
            {"schema": "label-profile-0.1", "n_gold": len(ids), "axes": profile},
            ensure_ascii=False, indent=2))
        print(f"\n저장: {common.rel(p)}  → clip_label.py 가 읽어서 쓴다")
        print("   다음: python scripts/check/labeler_score.py  (정말 나아졌는지 재확인)")
    else:
        print("\n(--write 를 붙이면 label_profile.json 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
