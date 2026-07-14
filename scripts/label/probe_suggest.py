"""P2 — 인물 축 프로브 제안 생성 (검수 보조).

probe_eval.py 실측에서 혼합이 제로샷을 이긴 인물 3축(gender/person_count/expression)에 대해:
  1) 골든셋(검수 keep 사람 라벨)으로 축별 로지스틱 프로브 학습
  2) GroupKFold CV 로 '정밀도 95% 유지' 확신도 임계 산출
  3) 미검수 approved 전량 예측 → annotations/probe_suggestions.csv
     (image_id, axis, code, conf, auto_ok)  — 앱이 읽어 편집창에 AI 힌트 표시.

사용: python scripts/label/probe_suggest.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

# 프로브 제안이 필요한 축 = 'human' 모드 축, 즉 zero-shot 이 못 읽는다고 축 감사가 판정한 축.
# (healthy 축은 자동라벨이 이미 잘 하고, derived 축은 사람이 아예 안 본다 → 제안이 무의미)
# taxonomy._axes.mode 에서 파생되므로 도메인이 바뀌어도 따라온다.
AXES = [k for k in common.LABEL_AXES if common.AXIS_MODE.get(k) == "human"]
PRECISION = 0.95
OUT = common.ANNOTATIONS / "probe_suggestions.csv"
FIELDS = ["image_id", "axis", "code", "conf", "auto_ok"]


def main() -> int:
    import numpy as np
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import GroupKFold

    cur = {r["image_id"]: r for r in common.read_rows(common.MASTER_META)}
    golden = {i: r for i, r in cur.items()
              if r.get("reviewed") == "true" and r["status"] == "approved"}
    target = [i for i, r in cur.items()
              if r["status"] == "approved" and r.get("reviewed") != "true"]

    df = pd.read_parquet(common.ANNOTATIONS / "reference_index.parquet")
    emb = {r.image_id: np.asarray(r.embed, dtype=np.float32)
           for r in df.itertuples() if r.embed is not None and len(r.embed)}
    g_ids = [i for i in sorted(golden) if i in emb]
    t_ids = [i for i in target if i in emb]
    Xg = np.stack([emb[i] for i in g_ids])
    Xt = np.stack([emb[i] for i in t_ids])
    groups = np.array([golden[i].get("phash") or i for i in g_ids])
    print(f"골든 {len(g_ids)} · 대상(미검수 approved) {len(t_ids)}")

    rows = []
    for ax in AXES:
        y = np.array([(common.split_codes(ax, golden[i].get(ax)) or [""])[0] for i in g_ids])
        ok = y != ""
        Xa, ya, Ga = Xg[ok], y[ok], groups[ok]
        codes = sorted(set(ya))

        # --- CV 로 정밀도 95% 확신도 임계 ---
        conf_cv = np.zeros(len(ya)); pred_cv = np.empty(len(ya), dtype=object)
        for tr, te in GroupKFold(n_splits=min(5, len(set(Ga)))).split(Xa, groups=Ga):
            clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xa[tr], ya[tr])
            p = clf.predict_proba(Xa[te])
            conf_cv[te] = p.max(1)
            pred_cv[te] = clf.classes_[p.argmax(1)]
        order = np.argsort(-conf_cv)
        hits = np.cumsum((pred_cv == ya)[order])
        k = np.arange(1, len(order) + 1)
        okk = np.where(hits / k >= PRECISION)[0]
        thr = float(conf_cv[order][okk[-1]]) if len(okk) else 1.01
        cover = (okk[-1] + 1) / len(order) if len(okk) else 0.0

        # --- 전량 학습 → 미검수 예측 ---
        clf = LogisticRegression(max_iter=2000, class_weight="balanced").fit(Xa, ya)
        p = clf.predict_proba(Xt)
        pred = clf.classes_[p.argmax(1)]
        conf = p.max(1)
        n_auto = int((conf >= thr).sum())
        print(f"  {ax:14} 코드 {len(codes)} · CV커버리지 {cover:4.0%} · 임계 {thr:.2f} "
              f"· 자동확정 후보 {n_auto}/{len(t_ids)} ({n_auto/len(t_ids):.0%})")
        for i, iid in enumerate(t_ids):
            rows.append({"image_id": iid, "axis": ax, "code": pred[i],
                         "conf": f"{conf[i]:.3f}", "auto_ok": "true" if conf[i] >= thr else ""})

    common.write_rows(OUT, FIELDS, rows)
    print(f"저장: {common.rel(OUT)} ({len(rows)}행) — app.py 재생성 시 편집창 AI 힌트로 표시")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
