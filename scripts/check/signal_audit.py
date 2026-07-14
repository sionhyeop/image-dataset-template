"""신호 감사 — **어떤 신호가 쓸 만한가**를 진단한다. (가중치를 정하는 도구가 아니다)

무엇을 재나:
    각 신호(시각·색감·포즈…)가 홀로 얼마나 쓸 만한지를 두 잣대로 잰다.
      · 의미재현 — 오프라인 CLIP(ViT-H/14) 이웃을 얼마나 되살리나 ('결이 비슷한가')
      · 라벨일치 — 추천한 컷이 쿼리와 같은 라벨을 갖는 비율 ('장소·거리감이 같은가')
    두 목표는 다르다. 하나만 최적화하면 다른 하나가 조용히 망가지므로 반드시 함께 본다.

    신호 목록은 annotations/*_index.json 에서 자동 발견하고, 골드는 CLIP 임베딩에서
    자동으로 만든다. 축 이름도 코드도 모른다 — 새 이미지셋에서 그냥 다시 돌리면 된다.

⚠ 여기서 나온 가중치를 앱에 그대로 넣지 말 것:
    이 스크립트는 **임베딩끼리의 거리**로 가중치를 맞춘다. 그런데 실제 camMatch 는
    하드필터를 먼저 걸고, 다른 정규화를 쓰고, 프레이밍 페널티·장소 보너스를 더한다.
    오프라인 최적값을 그대로 넣었더니 실제로는 나빠졌다
    (종합 62.7% → 60.8%, 프레이밍 68.4% → 65.2% 로 합격선 미달).
    **대리 목표로 최적화하지 말 것.** 앱에 넣을 가중치는 실제 파이프라인을 통과시켜 찾는다:
        node scripts/check/tune_camw.js --write

    이 스크립트의 쓸모는 따로 있다 — "어떤 신호를 더 좋은 것으로 갈아끼워야 하는가".
    실측: 시각 39.6% / 색감 7.6% / 포즈 6.2% → 시각 신호가 전부를 지고 있다.
    따라서 지렛대는 가중치 조정이 아니라 **시각 신호 교체**(MobileNet → DINOv2, +8.6%p)다.

출력:
    annotations/signal_profile.json — signals(신호별 진단) + combos(참고용 오프라인 가중치)

사용:
    python scripts/check/signal_audit.py            # 표만
    python scripts/check/signal_audit.py --write    # signal_profile.json 저장
"""
from __future__ import annotations

import argparse
import base64
import itertools
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

GOLD_K = 40      # CLIP 이웃 상위 K 개를 '결이 비슷한 것'으로 본다
TOP_K = 10       # 신호가 추천한 상위 K 개 중 몇 개가 골드 안에 드는가
GRID = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


def _dequant(v):
    """인덱스 JSON 의 벡터 표현을 float 벡터로. 세 가지 표현을 모두 받는다."""
    import numpy as np
    if isinstance(v, dict):                       # {b: base64(int8), s: scale}
        raw = base64.b64decode(v["b"]) if isinstance(v["b"], str) else bytes(bytearray(v["b"]))
        return np.frombuffer(raw, dtype=np.int8).astype(np.float32) * float(v["s"])
    if isinstance(v, str):                        # base64(uint8)
        return np.frombuffer(base64.b64decode(v), dtype=np.uint8).astype(np.float32)
    return np.asarray(v, dtype=np.float32)


def _l2(x):
    import numpy as np
    x = np.asarray(x, dtype=np.float32)
    return x / (np.linalg.norm(x, axis=1, keepdims=True) + 1e-8)


def _discover(ids: list[str]) -> dict[str, dict]:
    """annotations/ 에서 신호 인덱스를 자동 발견한다. 축 이름도 코드도 모른다."""
    import numpy as np
    import pandas as pd

    sigs: dict[str, dict] = {}
    for path in sorted(common.ANNOTATIONS.glob("*_index.json")):
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        emb = d.get("emb")
        if not isinstance(emb, dict) or len(emb) < 100:
            continue
        sid = path.stem.replace("_index", "")
        sigs[sid] = {"model": d.get("model", sid), "table": emb,
                     "dim": d.get("dim") or len(_dequant(next(iter(emb.values()))))}

    # 포즈 디스크립터는 parquet 안에 있다(별도 인덱스 파일이 아님)
    df = pd.read_parquet(common.ANNOTATIONS / "reference_index.parquet")
    if "desc" in df.columns:
        tbl = {r["image_id"]: np.asarray(r["desc"], dtype=np.float32)
               for _, r in df.iterrows()
               if r.get("desc_ok") and isinstance(r.get("desc"), (list, np.ndarray))}
        if len(tbl) >= 100:
            sigs["pose"] = {"model": "blazepose-desc", "table": tbl,
                            "dim": len(next(iter(tbl.values())))}
    return sigs


def _topk(emb):
    """각 행의 최근접 TOP_K 이웃 인덱스."""
    import numpy as np
    sim = emb @ emb.T
    np.fill_diagonal(sim, -2.0)
    return np.argpartition(-sim, TOP_K, axis=1)[:, :TOP_K]


def _overlap(emb, gold: dict, ids: list[str]) -> float:
    """골드(CLIP 이웃)를 얼마나 재현하나 — '의미가 비슷한가'."""
    hit = tot = 0
    for i, row in enumerate(_topk(emb)):
        g = gold.get(ids[i])
        if not g:
            continue
        hit += sum(1 for j in row if ids[j] in g)
        tot += TOP_K
    return hit / max(1, tot)


def _label_agree(emb, ids: list[str], labels: dict, axes: list[str]) -> float:
    """추천된 top-K 가 쿼리와 **같은 라벨**을 갖는 비율 — '결이 비슷한가'.

    CLIP 재현율과는 다른 목표다. 사용자가 포즈캠에서 실제로 보는 것은 이쪽에 가깝다
    (장소가 같은가, 거리감이 같은가, 방향이 같은가). 둘 중 하나로 몰래 최적화하면 안 된다.
    """
    tot = agree = 0
    for i, row in enumerate(_topk(emb)):
        q = labels.get(ids[i])
        if not q:
            continue
        for j in row:
            c = labels.get(ids[j])
            if not c:
                continue
            for ax in axes:
                qa, ca = q.get(ax), c.get(ax)
                if not qa or not ca:
                    continue
                tot += 1
                agree += bool(qa & ca)
    return agree / max(1, tot)


def main() -> int:
    ap = argparse.ArgumentParser(description="신호별 품질 진단 (가중치는 tune_camw.js 가 정한다)")
    ap.add_argument("--write", action="store_true", help="annotations/signal_profile.json 저장")
    # 오프라인 가중치 격자는 **기본으로 돌지 않는다.** 그 값은 실제 파이프라인에서 더 나빴고
    # (종합 62.7% → 60.8%), 신호가 늘면 조합이 지수적으로 폭발한다(4신호 = 14,641조합 × N²).
    # 앱에 넣을 가중치는 node scripts/check/tune_camw.js 가 실제 camMatch 로 찾는다.
    ap.add_argument("--fit-offline", action="store_true",
                    help="(연구용) 오프라인 가중치 격자 탐색 — 앱에는 쓰지 말 것")
    args = ap.parse_args()

    import numpy as np
    import pandas as pd

    df = pd.read_parquet(common.ANNOTATIONS / "reference_index.parquet")
    df = df[df["embed"].notna()].reset_index(drop=True)
    ids = df["image_id"].tolist()
    if len(ids) < 200:
        print(f"이미지 {len(ids)}장 — 신호 감사 불가 (최소 200장)")
        return 1

    # 골드: CLIP 이웃 top-K. 사람이 '결이 비슷하다'고 느끼는 것의 프록시.
    clip = _l2(np.stack(df["embed"].to_numpy()))
    sim = clip @ clip.T
    np.fill_diagonal(sim, -2.0)
    gold = {ids[i]: set(ids[j] for j in np.argpartition(-sim[i], GOLD_K)[:GOLD_K])
            for i in range(len(ids))}
    del sim
    print(f"골드: CLIP {clip.shape[1]}d 이웃 top-{GOLD_K}  ·  {len(ids)}장")

    sigs = _discover(ids)
    if not sigs:
        print("신호 인덱스를 찾지 못했습니다 (annotations/*_index.json)")
        return 1

    # 두 번째 목표: 라벨 일치. 사용자가 포즈캠에서 실제로 보는 것은 '장소가 같은가,
    # 거리감이 같은가' 쪽이다. 집계 축은 taxonomy._axes.composition 에서 온다(도메인 무관).
    idset = set(ids)
    labels = {r["image_id"]: {ax: set(common.split_codes(ax, r.get(ax)))
                              for ax in common.COMP_AXES}
              for r in common.read_rows(common.MASTER_META) if r["image_id"] in idset}
    comp_axes = list(common.COMP_AXES)

    # --- 신호별 단독 재현력 ---------------------------------------------------
    print(f"\n{'신호':10s}{'모델':22s}{'차원':>5s}{'장수':>7s}{'단독 재현율':>12s}")
    print("-" * 60)
    live = {}   # 전 이미지에 값이 있는 신호만 가중치 결합에 쓴다
    for sid, s in sigs.items():
        keep = [i for i in ids if i in s["table"]]
        if len(keep) < 100:
            continue
        sub = _l2(np.stack([_dequant(s["table"][i]) for i in keep]))
        g2 = {k: gold[k] & set(keep) for k in keep}
        ov = _overlap(sub, g2, keep)
        s["overlap"] = round(ov, 4)
        s["n"] = len(keep)
        print(f"{sid:10s}{str(s['model'])[:21]:22s}{s['dim']:5d}{len(keep):7d}{ov:12.1%}")
        live[sid] = (keep, sub)

    # --- 가중치 최적화 ---------------------------------------------------------
    # 라이브에서 어떤 신호가 잡히는지는 매 프레임 달라진다(포즈는 사람이 안 보이면 없다).
    # 그러므로 **가용 신호 조합별로 따로** 맞춰야 한다. 하나의 가중치 벡터를 전 상황에
    # 쓰면, 포즈가 있는 표본에서 맞춘 값이 포즈 없는 1300여 장에 잘못 적용된다.
    HAND = {"visual": 0.66, "color": 0.22, "pose": 0.12}   # 지금 앱이 쓰는(손으로 찍은) 값

    def fit(keys: list[str], subset: list[str]):
        """subset 위에서 keys 신호들의 가중치를 격자 탐색으로 최적화.

        목표가 둘이므로(의미 재현 / 라벨 일치) 각각의 최적을 따로 구하고, 둘을 절충한
        균형점도 함께 낸다. 하나만 최적화하면 다른 하나가 조용히 망가진다.
        """
        mats = {s: _l2(np.stack([_dequant(sigs[s]["table"][i]) for i in subset])) for s in keys}
        g = {k: gold[k] & set(subset) for k in subset}
        solo = {s: {"clip": _overlap(mats[s], g, subset),
                    "label": _label_agree(mats[s], subset, labels, comp_axes)} for s in keys}
        cand = []
        for combo in itertools.product(GRID, repeat=len(keys)):
            t = sum(combo)
            if t <= 0:
                continue
            w = {k: c / t for k, c in zip(keys, combo)}
            f = _l2(np.hstack([mats[k] * w[k] for k in keys]))
            cand.append((w, _overlap(f, g, subset), _label_agree(f, subset, labels, comp_axes)))
        # 두 지표를 각자 0~1 로 정규화한 뒤 균등 절충 — 어느 한쪽 스케일이 크다고 이기지 않게
        cs = [c[1] for c in cand]
        ls = [c[2] for c in cand]
        cr, lr = (max(cs) - min(cs)) or 1, (max(ls) - min(ls)) or 1
        best_clip = max(cand, key=lambda c: c[1])
        best_lab = max(cand, key=lambda c: c[2])
        best_bal = max(cand, key=lambda c: (c[1] - min(cs)) / cr + (c[2] - min(ls)) / lr)

        hw = {k: HAND.get(k, 0.0) for k in keys}
        t = sum(hw.values())
        hand = None
        if t > 0:
            hw = {k: v / t for k, v in hw.items()}
            hf = _l2(np.hstack([mats[k] * hw[k] for k in keys]))
            hand = (_overlap(hf, g, subset), _label_agree(hf, subset, labels, comp_axes))
        return {"keys": keys, "n": len(subset), "solo": solo, "hand": hand, "hand_weights": hw,
                "best_clip": best_clip, "best_label": best_lab, "best_balanced": best_bal}

    # 신호별 라벨일치도 함께 보여준다 (의미재현만 보면 색·포즈의 값어치를 놓친다)
    lab_solo = {}
    for s in live:
        keep, sub = live[s]
        lab_solo[s] = _label_agree(sub, keep, labels, comp_axes)
    print(f"\n{'신호':10s}{'의미재현':>10s}{'라벨일치':>10s}   진단")
    print("-" * 60)
    rank = sorted(live, key=lambda s: -sigs[s]["overlap"])
    for s in rank:
        tag = "★ 주축" if s == rank[0] else ("게이트용(랭커 아님)" if sigs[s]["overlap"] < 0.15 else "")
        print(f"{s:10s}{sigs[s]['overlap']:10.1%}{lab_solo[s]:10.1%}   {tag}")
    print(f"\n지렛대는 **주축 신호 교체**다 — {rank[0]} 이 사실상 순위를 결정한다.")
    print("앱에 넣을 가중치는 여기서 정하지 않는다: node scripts/check/tune_camw.js --write")

    fits = {}
    if not args.fit_offline:
        prof_min = {
            "schema": "signal-profile-0.3",
            "gold": f"clip_knn{GOLD_K}", "top_k": TOP_K, "label_axes": comp_axes,
            "signals": {s: {"model": str(sigs[s]["model"]), "dim": sigs[s]["dim"],
                            "n": sigs[s]["n"], "overlap": sigs[s]["overlap"],
                            "label": round(lab_solo[s], 4)} for s in live},
        }
        if args.write:
            p = common.ANNOTATIONS / "signal_profile.json"
            old = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
            if "live" in old:
                prof_min["live"] = old["live"]      # tune_camw.js 가 쓴 가중치는 보존한다
            common.write_json_atomic(p, json.dumps(prof_min, ensure_ascii=False, indent=2))
            print(f"\n저장: {common.rel(p)} (신호 진단만 — 가중치는 tune_camw.js 소관)")
        return 0

    # --- 이하 --fit-offline 전용 (연구용). 앱에는 쓰지 말 것. -----------------
    always = sorted(s for s in live if sigs[s]["n"] >= len(ids) * 0.95)
    sometimes = sorted(s for s in live if s not in always)
    if always:
        fits["+".join(always)] = fit(always, [i for i in ids
                                              if all(i in sigs[s]["table"] for s in always)])
    for s in sometimes:                      # 가끔 잡히는 신호를 하나씩 추가한 조합
        keys = sorted(always + [s])
        sub = [i for i in ids if all(i in sigs[k]["table"] for k in keys)]
        if len(sub) >= 100:
            fits["+".join(keys)] = fit(keys, sub)

    print("\n■ (연구용) 오프라인 가중치 — ⚠ 앱에 넣지 말 것. 실제 파이프라인에선 더 나빴다.")
    print(f"  목표가 둘이다 — 의미재현(CLIP 이웃 top-{GOLD_K} 겹침) / 라벨일치({'·'.join(comp_axes)})")
    for name, f in fits.items():
        print(f"\n  [{name}]  표본 {f['n']}장   ※ 같은 표본에서만 비교해야 의미가 있다")
        print(f"      {'':22s}{'의미재현':>9s}{'라벨일치':>9s}")
        for s in f["keys"]:
            print(f"      단독 {s:17s}{f['solo'][s]['clip']:9.1%}{f['solo'][s]['label']:9.1%}")
        if f["hand"]:
            print(f"      {'손으로 찍음':20s}{f['hand'][0]:9.1%}{f['hand'][1]:9.1%}   "
                  + " ".join(f"{k}={f['hand_weights'][k]:.2f}" for k in f["keys"]))
        for lbl, key in (("의미재현 최적", "best_clip"), ("라벨일치 최적", "best_label"),
                         ("절충(권장)", "best_balanced")):
            w, c, l = f[key]
            print(f"      {lbl:20s}{c:9.1%}{l:9.1%}   "
                  + " ".join(f"{k}={w[k]:.2f}" for k in f["keys"]))

    def _pack(t):
        w, c, l = t
        return {"weights": {k: round(v, 3) for k, v in w.items()},
                "clip": round(c, 4), "label": round(l, 4)}

    prof = {
        "schema": "signal-profile-0.2",
        "gold": f"clip_knn{GOLD_K}", "top_k": TOP_K, "label_axes": comp_axes,
        "signals": {s: {"model": str(sigs[s]["model"]), "dim": sigs[s]["dim"],
                        "n": sigs[s]["n"], "overlap": sigs[s]["overlap"]} for s in live},
        # 라이브에서 잡힌 신호 집합을 키로 찾아 쓴다. 없으면 앱이 균등 가중으로 폴백.
        # weights = 절충안. 다른 목표를 원하면 alt 에서 골라 쓰면 된다.
        "combos": {name: {
            "n": f["n"],
            "weights": _pack(f["best_balanced"])["weights"],
            "balanced": _pack(f["best_balanced"]),
            "alt": {"max_clip": _pack(f["best_clip"]), "max_label": _pack(f["best_label"])},
            "solo": {k: {"clip": round(v["clip"], 4), "label": round(v["label"], 4)}
                     for k, v in f["solo"].items()},
            "hand_tuned": ({"weights": {k: round(v, 3) for k, v in f["hand_weights"].items()},
                            "clip": round(f["hand"][0], 4), "label": round(f["hand"][1], 4)}
                           if f["hand"] else None),
        } for name, f in fits.items()},
    }
    if args.write:
        p = common.ANNOTATIONS / "signal_profile.json"
        common.write_json_atomic(p, json.dumps(prof, ensure_ascii=False, indent=2))
        print(f"\n저장: {common.rel(p)}  → app.py 가 payload 로 실어 camMatch 가 읽는다")
    else:
        print("\n(--write 를 붙이면 signal_profile.json 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
