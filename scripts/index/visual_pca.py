"""시각 인덱스 생성 — MobileNet 1024d 원본 임베딩 → PCA 128d → int8 양자화 → visual_index.json.

입력: annotations/visual_embed_raw.json (scripts/index/visual_embed/embed.mjs 산출, 브라우저와 동일 tfjs 모델)
출력: annotations/visual_index.json — 앱 payload(VIDX)로 임베드:
  mean[1024]·comps(int8 b64, 행별 scale) = 라이브 1024d 를 같은 128d 공간으로 사영하는 행렬
  emb {id:{b:int8 b64, s:scale}}         = 데이터셋 128d 정규화 임베딩

사용: python scripts/index/visual_pca.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

DIM = 128
RAW = common.ANNOTATIONS / "visual_embed_raw.json"
OUT = common.ANNOTATIONS / "visual_index.json"


def q8(v):
    """float 벡터 → (int8 base64, scale)."""
    import numpy as np
    s = float(np.abs(v).max()) / 127.0 or 1e-9
    q = np.clip(np.round(v / s), -127, 127).astype("int8")
    return base64.b64encode(q.tobytes()).decode(), round(s, 8)


def main() -> int:
    import numpy as np
    from sklearn.decomposition import PCA

    raw = json.loads(RAW.read_text(encoding="utf-8"))
    ids = sorted(raw)
    X = np.asarray([raw[i] for i in ids], dtype=np.float32)      # (n,1024) L2 정규화 상태
    print(f"원본 임베딩 {X.shape} 로드")

    pca = PCA(n_components=DIM, random_state=42).fit(X)
    Y = pca.transform(X)                                          # (n,128)
    Y /= np.linalg.norm(Y, axis=1, keepdims=True) + 1e-9          # 코사인용 재정규화
    evr = float(pca.explained_variance_ratio_.sum())
    print(f"PCA {DIM}d · 설명분산 {evr:.1%}")

    comps_b, comps_s = zip(*(q8(c) for c in pca.components_))     # 행별 양자화
    emb = {}
    for k, i in enumerate(ids):
        b, s = q8(Y[k])
        emb[i] = {"b": b, "s": s}

    out = {
        "schema": "visual-index-0.1", "model": "mobilenet_v1_1.0_224(tfjs)",
        "dim": DIM, "evr": round(evr, 4),
        "mean": [round(float(x), 5) for x in pca.mean_],
        "comps": list(comps_b), "comps_s": list(comps_s),
        "emb": emb,
    }
    common.write_json_atomic(OUT, json.dumps(out, ensure_ascii=False))
    print(f"저장: {common.rel(OUT)} ({OUT.stat().st_size//1024}KB · {len(emb)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
