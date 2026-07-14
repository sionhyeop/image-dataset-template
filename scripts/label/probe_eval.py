"""P0+P1 — 사람 검수 라벨 평가 하네스 + 선형 프로브.

골든셋   = 검수 keep(reviewed=true & approved) 이미지의 사람 라벨(현재 master).
베이스라인 = 검수 반영 직전 백업 CSV 의 CLIP H/14 zero-shot 라벨.
프로브   = reference_index.parquet 의 H/14 임베딩 위 축별 로지스틱 회귀
          (pHash 그룹 KFold CV — near-duplicate 누수 방지, make_splits 와 동일 원칙).

출력: 축별 zero-shot vs 프로브 macro/micro-F1 비교표 + 자동확정 커버리지
      (CV 확신도 기준, 정밀도 95% 유지 시 검수 생략 가능 비율).

사용:
    python scripts/label/probe_eval.py
    python scripts/label/probe_eval.py --backup annotations/_backups/master_metadata.20260710_104848.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

DEFAULT_BACKUP = common.ANNOTATIONS / "_backups/master_metadata.20260710_104848.csv"
MIN_SUPPORT = 2      # 골든셋 내 이 미만 코드는 프로브 학습·평가에서 제외(제로샷 폴백 유지 대상)
SUP_GATE = 8         # 하이브리드: 지원 수 이 이상인 코드만 프로브 판단, 미만은 제로샷 유지
CONF_GATE = 0.5      # 하이브리드(단일축): 프로브 확신도 미만이면 제로샷 라벨 유지
AUTOCONF_PREC = 0.95  # 자동확정 목표 정밀도


def load_golden(backup_path: Path):
    cur = {r["image_id"]: r for r in common.read_rows(common.MASTER_META)}
    golden = {i: r for i, r in cur.items()
              if r.get("reviewed") == "true" and r["status"] == "approved"}
    bak = {r["image_id"]: r for r in common.read_rows(backup_path)}
    missing = [i for i in golden if i not in bak]
    if missing:
        raise SystemExit(f"백업에 없는 골든 행 {len(missing)}건 — 백업 파일 확인 필요")
    return golden, bak


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--backup", type=Path, default=DEFAULT_BACKUP,
                    help="검수 반영 직전 master 백업(=zero-shot 라벨 출처)")
    ap.add_argument("--folds", type=int, default=5)
    args = ap.parse_args()

    import numpy as np
    import pandas as pd
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score
    from sklearn.model_selection import GroupKFold

    golden, bak = load_golden(args.backup)
    ids = sorted(golden)
    print(f"골든셋 {len(ids)}장 (검수 keep) · zero-shot 출처: {args.backup.name}\n")

    df = pd.read_parquet(common.ANNOTATIONS / "reference_index.parquet")
    emb = {r.image_id: np.asarray(r.embed, dtype=np.float32)
           for r in df.itertuples() if r.embed is not None and len(r.embed)}
    kpdesc = {r.image_id: np.asarray(r.desc, dtype=np.float32)
              for r in df.itertuples()
              if getattr(r, "desc_ok", False) and r.desc is not None and len(r.desc)}
    ids = [i for i in ids if i in emb]
    X = np.stack([emb[i] for i in ids])
    groups = np.array([golden[i].get("phash") or i for i in ids])
    print(f"임베딩 매칭 {len(ids)}장 · {X.shape[1]}차원 · pHash 그룹 {len(set(groups))}\n")

    header = (f"{'축':14} {'평가코드':>5} {'제로샷mF1':>9} {'프로브mF1':>9} {'혼합mF1':>9} {'Δ':>6}   "
              f"{'제로샷μ':>8} {'프로브μ':>8} {'혼합μ':>8} {'Δ':>6}")
    print(header)
    print("-" * len(header))
    print(f"(혼합 = 지원≥{SUP_GATE} 코드만 프로브, 나머지 제로샷 유지 · 단일축은 확신도 {CONF_GATE}+ 조건 추가)")

    autoconf = []
    for ax in common.LABEL_AXES:
        multi = ax in common.MULTI_AXES
        # 축별 표본: 사람 라벨이 비어있지 않은 행만
        rows = [(k, common.split_codes(ax, golden[k].get(ax)),
                 common.split_codes(ax, bak[k].get(ax))) for k in ids]
        rows = [(k, h, z) for k, h, z in rows if h]
        if len(rows) < 20:
            print(f"{ax:14} 표본 {len(rows)} < 20 — 건너뜀")
            continue
        idx = [ids.index(k) for k, _, _ in rows]
        Xa, Ga = X[idx], groups[idx]

        codes = sorted({c for _, h, _ in rows for c in h})
        support = {c: sum(c in h for _, h, _ in rows) for c in codes}
        eval_codes = [c for c in codes if support[c] >= MIN_SUPPORT]
        if not eval_codes:
            continue
        ci = {c: j for j, c in enumerate(eval_codes)}

        def binarize(labsets):
            Y = np.zeros((len(labsets), len(eval_codes)))
            for r_, labs in enumerate(labsets):
                for c in labs:
                    if c in ci:
                        Y[r_, ci[c]] = 1
            return Y

        Yh = binarize([h for _, h, _ in rows])
        Yz = binarize([z for _, _, z in rows])

        # ---- 프로브: GroupKFold CV 확률 예측 ----
        def cv_prob(Xf, Gf, Yf):
            n_folds = min(args.folds, len(set(Gf)))
            out = np.zeros_like(Yf)
            for tr, te in GroupKFold(n_splits=n_folds).split(Xf, groups=Gf):
                for j in range(Yf.shape[1]):
                    ytr = Yf[tr, j]
                    if ytr.sum() < 1 or ytr.sum() == len(ytr):   # 폴드에 한 클래스뿐
                        out[te, j] = ytr.mean()
                        continue
                    clf = LogisticRegression(max_iter=2000, C=1.0, class_weight="balanced")
                    clf.fit(Xf[tr], ytr)
                    out[te, j] = clf.predict_proba(Xf[te])[:, 1]
            return out

        prob = cv_prob(Xa, Ga, Yh)

        if multi:
            Yp = (prob >= 0.5).astype(float)
            none_pred = Yp.sum(1) == 0                     # 최소 1코드 보장(앱 시맨틱)
            Yp[none_pred, prob[none_pred].argmax(1)] = 1
        else:
            Yp = np.zeros_like(Yh)
            Yp[np.arange(len(Yp)), prob.argmax(1)] = 1

        # ---- 하이브리드: 표본 충분(SUP_GATE↑) 코드만 프로브, 나머지는 제로샷 유지 ----
        gated = np.array([support[c] >= SUP_GATE for c in eval_codes])
        Yb = Yz.copy()
        if multi:
            Yb[:, gated] = Yp[:, gated]
            none_pred = Yb.sum(1) == 0
            Yb[none_pred] = Yz[none_pred]
        else:
            conf = prob.max(1)
            top = prob.argmax(1)
            use_probe = (conf >= CONF_GATE) & gated[top]   # 확신 있고 학습 표본도 충분할 때만
            Yb[use_probe] = 0
            Yb[np.where(use_probe)[0], top[use_probe]] = 1

        zs_ma = f1_score(Yh, Yz, average="macro", zero_division=0)
        pr_ma = f1_score(Yh, Yp, average="macro", zero_division=0)
        hy_ma = f1_score(Yh, Yb, average="macro", zero_division=0)
        zs_mi = f1_score(Yh, Yz, average="micro", zero_division=0)
        pr_mi = f1_score(Yh, Yp, average="micro", zero_division=0)
        hy_mi = f1_score(Yh, Yb, average="micro", zero_division=0)
        print(f"{ax:14} {len(eval_codes):>5} {zs_ma:>9.3f} {pr_ma:>9.3f} {hy_ma:>9.3f} {hy_ma-zs_ma:>+6.3f}   "
              f"{zs_mi:>8.3f} {pr_mi:>8.3f} {hy_mi:>8.3f} {hy_mi-zs_mi:>+6.3f}")

        # ---- 포즈 축 전용 실험: CLIP 임베딩 대신 21차원 키포인트 디스크립터 ----
        # 어느 축이 '포즈 축'인지는 taxonomy._semantic.pose 가 정한다 (없으면 이 실험은 생략)
        if ax == (common.load_semantic().get("pose") or {}).get("axis"):
            sub = [t for t, (k, _, _) in enumerate(rows) if k in kpdesc]
            if len(sub) >= 30:
                Xk = np.stack([kpdesc[rows[t][0]] for t in sub])
                probk = cv_prob(Xk, Ga[sub], Yh[sub])
                Ypk = (probk >= 0.5).astype(float)
                none_pred = Ypk.sum(1) == 0
                Ypk[none_pred, probk[none_pred].argmax(1)] = 1
                kp_ma = f1_score(Yh[sub], Ypk, average="macro", zero_division=0)
                kp_mi = f1_score(Yh[sub], Ypk, average="micro", zero_division=0)
                zsk_ma = f1_score(Yh[sub], Yz[sub], average="macro", zero_division=0)
                zsk_mi = f1_score(Yh[sub], Yz[sub], average="micro", zero_division=0)
                print(f"{'└ 키포인트 21d':13} {len(sub):>4}장 {zsk_ma:>9.3f} {kp_ma:>9.3f} {'—':>9} {kp_ma-zsk_ma:>+6.3f}   "
                      f"{zsk_mi:>8.3f} {kp_mi:>8.3f} {'—':>8} {kp_mi-zsk_mi:>+6.3f}")

        # ---- 자동확정 커버리지(단일축): 확신도 내림차순으로 정밀도 95% 유지 최대 구간 ----
        if not multi:
            conf = prob.max(1)
            correct = (Yp * Yh).sum(1) > 0
            order = np.argsort(-conf)
            hits = np.cumsum(correct[order])
            k = np.arange(1, len(order) + 1)
            okk = np.where(hits / k >= AUTOCONF_PREC)[0]
            cover = (okk[-1] + 1) / len(order) if len(okk) else 0.0
            thr = conf[order][okk[-1]] if len(okk) else 1.0
            autoconf.append((ax, cover, thr))

    if autoconf:
        print(f"\n자동확정 미리보기 (CV 확신도 기준, 정밀도 ≥{AUTOCONF_PREC:.0%} 유지):")
        for ax, cover, thr in autoconf:
            print(f"  {ax:14} 검수 생략 가능 {cover:5.0%}  (확신도 임계 {thr:.2f})")

    print("\n주의: n=104 소표본 · 검수자가 CLIP 라벨을 보고 수정하는 UI라 제로샷에 유리한"
          " 앵커 편향 가능 · 희소 코드(<2)는 제로샷 폴백 유지.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
