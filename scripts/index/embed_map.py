"""관계맵 데이터 생성 — reference_index.parquet 소비.

레퍼런스 인덱스(scripts/index/build_index.py 산출)의 사전계산을 읽어
앱 관계맵/조합 뷰가 쓸 embedding.json 을 만든다. CLIP 을 재계산하지 않는다.

인덱스에서 사용:
  embed        → 2D 좌표(UMAP) + 최근접 이웃(k-NN)
  cluster_id   → 군집(시각 유형) + 대표이미지/대표라벨/크기
  rarity       → 희귀도(희귀 구도 강조)
  sharpness    → 선명도(품질 참고)

출력: annotations/embedding.json =
  { coords:{id:[x,y,cluster]}, attr:{id:[rarity,sharpNorm]},
    clusters:[{idx,size,rep,cx,cy,tags}], knn:{id:[ids]} }

사용:
    python scripts/index/embed_map.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

INDEX_PATH = common.ANNOTATIONS / "reference_index.parquet"
TAG_AXES = common.COMP_AXES   # 구도 4축 SSOT (common.py)


def reduce_2d(X):
    import numpy as np
    n = len(X)
    # umap 이 내부적으로 import 하는 표준 패키지(coverage 등)가 scripts/coverage.py 와
    # 충돌하지 않도록, umap 임포트 동안 scripts/ 를 sys.path 에서 잠시 제거한다.
    saved = list(sys.path)
    sys.path[:] = [p for p in sys.path if p != str(SCRIPTS)]
    try:
        import umap
        return umap.UMAP(n_neighbors=min(15, n - 1), min_dist=0.12,
                         metric="cosine", random_state=42).fit_transform(X)
    except Exception as e:
        print(f"  UMAP 미사용({e}) → PCA")
    finally:
        sys.path[:] = saved
    Xc = X - X.mean(0)
    _, _, Vt = np.linalg.svd(Xc, full_matrices=False)
    return Xc @ Vt[:2].T


def main() -> int:
    import numpy as np
    import pandas as pd

    if not INDEX_PATH.exists():
        print(f"{INDEX_PATH.name} 가 없습니다. 먼저 build_index 를 실행하세요.")
        return 1

    approved = {r["image_id"] for r in common.read_rows(common.MASTER_META)
                if r.get("status") == "approved"}
    df = pd.read_parquet(INDEX_PATH)
    df = df[df.image_id.isin(approved)].copy()
    df = df[df.embed.map(lambda v: v is not None and len(v) > 0)].reset_index(drop=True)
    if len(df) == 0:
        print("인덱스에 임베딩이 없습니다.")
        return 1
    print(f"인덱스에서 {len(df)}장 사용 (승인 {len(approved)}장 중)")

    ids = df.image_id.tolist()
    X = np.stack(df.embed.map(lambda v: np.asarray(v, dtype=np.float32))).astype(np.float32)
    X = X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-8)
    n = len(ids)

    print("2D 차원축소...")
    Y = np.asarray(reduce_2d(X), dtype="float32")
    mn, mx = Y.min(0), Y.max(0)
    Yn = (Y - mn) / np.where(mx - mn == 0, 1, mx - mn)

    # 군집: 인덱스의 cluster_id 사용
    cl = df.cluster_id.fillna(-1).astype(int).to_numpy()
    meta = {r["image_id"]: r for r in common.read_rows(common.MASTER_META)}
    clusters = []
    for c in sorted(set(cl)):
        if c < 0:
            continue
        idx = np.where(cl == c)[0]
        centroid = X[idx].mean(0)
        rep = ids[idx[int((X[idx] @ centroid).argmax())]]
        cx, cy = float(Yn[idx, 0].mean()), float(Yn[idx, 1].mean())
        tags = {}
        for ax in TAG_AXES:
            cnt = Counter()
            for j in idx:
                v = meta.get(ids[j], {}).get(ax, "")
                for t in common.split_codes(ax, v):   # 멀티축(;)은 코드별 분리 집계
                    cnt[t] += 1
            tags[ax] = cnt.most_common(3)
        clusters.append({"idx": int(c), "size": int(len(idx)), "rep": rep,
                         "cx": round(cx, 4), "cy": round(cy, 4), "tags": tags})

    # k-NN
    print("최근접 이웃...")
    S = X @ X.T
    np.fill_diagonal(S, -1)
    kk = min(6, n - 1)
    knn = {}
    for i in range(n):
        nb = np.argpartition(-S[i], kk)[:kk]
        knn[ids[i]] = [ids[j] for j in nb[np.argsort(-S[i, nb])]]

    # 속성: 희귀도, 선명도(0~1 정규화)
    rar = df.rarity.fillna(0).to_numpy()
    sh = df.sharpness.fillna(0).to_numpy()
    shn = (sh - sh.min()) / (sh.max() - sh.min() + 1e-9)
    coords = {ids[i]: [round(float(Yn[i, 0]), 4), round(float(Yn[i, 1]), 4), int(cl[i])] for i in range(n)}
    attr = {ids[i]: [round(float(rar[i]), 3), round(float(shn[i]), 3)] for i in range(n)}

    out = {"meta": {"schema": "embed-map-0.1", "n": n},
           "coords": coords, "attr": attr, "clusters": clusters, "knn": knn}
    common.write_json_atomic(common.ANNOTATIONS / "embedding.json",
                             json.dumps(out, ensure_ascii=False))

    # 포즈 스켈레톤(베타 탭용): pose_ok 행의 33키포인트 [x,y,visibility]
    poses = {}
    for _, r in df.iterrows():
        if bool(r.get("pose_ok")) and r.get("pose_kp") is not None:
            kp = np.asarray(r["pose_kp"], dtype=float).reshape(-1, 4)  # 33×[x,y,z,vis]
            poses[r["image_id"]] = [round(float(v), 3) for pt in kp for v in (pt[0], pt[1], pt[3])]
    common.write_json_atomic(common.ANNOTATIONS / "poses.json", json.dumps(poses))

    print(f"완료: {n}장 · 군집 {len(clusters)}개 · 포즈 {len(poses)}개 → embedding.json / poses.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
