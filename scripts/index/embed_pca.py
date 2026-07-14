"""시각 신호 인덱스 생성 — 원본 임베딩 → PCA → int8 양자화 → <signal>_index.json.

신호 무관(도메인 무관)하다. 새 시각 모델을 추가할 때 이 파일을 복사하지 않는다:
Node 임베더가 <signal>_embed_raw.json 을 뱉게 한 뒤 --signal 만 바꿔 돌리면 된다.

출력 스키마 (앱 payload 로 실려 브라우저가 라이브 벡터를 같은 공간으로 사영한다):
  mean[D_raw]                    PCA 평균
  comps(int8 b64) · comps_s      사영 행렬 (행별 양자화)
  emb {id: {b: int8 b64, s}}     데이터셋 임베딩 (PCA 후 L2 정규화)

**중요**: 원본 임베딩은 반드시 브라우저와 같은 런타임·같은 전처리로 뽑아야 한다.
그래서 임베더가 Python 이 아니라 Node 다 (visual_embed/, dino_embed/).

사용:
    python scripts/index/embed_pca.py --signal visual --model 'mobilenet_v1_1.0_224(tfjs)'
    python scripts/index/embed_pca.py --signal dino   --model 'dinov2-small(transformers.js)'
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402


def q8(v):
    """float 벡터 → (int8 base64, scale)."""
    import numpy as np
    s = float(np.abs(v).max()) / 127.0 or 1e-9
    q = np.clip(np.round(v / s), -127, 127).astype("int8")
    return base64.b64encode(q.tobytes()).decode(), round(s, 8)


def main() -> int:
    ap = argparse.ArgumentParser(description="원본 임베딩 → PCA → int8 인덱스")
    ap.add_argument("--signal", required=True, help="신호 이름 (visual | dino | …)")
    ap.add_argument("--model", default="", help="모델 표시명 (기록용)")
    ap.add_argument("--dim", type=int, default=128, help="PCA 차원")
    # 브라우저는 **인덱스와 똑같은 dtype·입력크기**로 추론해야 한다. 다르면 양자화 오차가
    # 한쪽에만 생겨 벡터가 다른 공간에 놓인다(fp32 인덱스 + q4f16 브라우저 = 코사인 0.86 → 붕괴).
    # 그래서 인덱스에 못박아 기록하고, app.js 가 이 값을 읽어 그대로 쓴다.
    ap.add_argument("--dtype", default="", help="임베더가 쓴 dtype (브라우저가 그대로 따라간다)")
    ap.add_argument("--input-size", type=int, default=0, help="임베더가 쓴 입력 픽셀 (0=모델 기본)")
    args = ap.parse_args()

    import numpy as np
    from sklearn.decomposition import PCA

    raw_p = common.ANNOTATIONS / f"{args.signal}_embed_raw.json"
    out_p = common.ANNOTATIONS / f"{args.signal}_index.json"
    if not raw_p.exists():
        print(f"원본 임베딩 없음: {common.rel(raw_p)}")
        print(f"  먼저 scripts/index/{args.signal}_embed/embed.mjs 를 돌리세요.")
        return 1

    raw = json.loads(raw_p.read_text(encoding="utf-8"))
    ids = sorted(raw)
    X = np.asarray([raw[i] for i in ids], dtype=np.float32)      # L2 정규화 상태
    dim = min(args.dim, X.shape[1], X.shape[0])
    print(f"[{args.signal}] 원본 {X.shape} 로드")

    pca = PCA(n_components=dim, random_state=42).fit(X)
    Y = pca.transform(X)
    Y /= np.linalg.norm(Y, axis=1, keepdims=True) + 1e-9          # 코사인용 재정규화
    evr = float(pca.explained_variance_ratio_.sum())
    print(f"[{args.signal}] PCA {dim}d · 설명분산 {evr:.1%}")

    comps_b, comps_s = zip(*(q8(c) for c in pca.components_))
    emb = {}
    for k, i in enumerate(ids):
        b, s = q8(Y[k])
        emb[i] = {"b": b, "s": s}

    out = {
        "schema": "visual-index-0.2",
        "model": args.model or args.signal,
        # 브라우저가 그대로 따라야 하는 추론 설정 — 어긋나면 벡터가 다른 공간에 놓인다
        "dtype": args.dtype or None,
        "input_size": args.input_size or None,
        "dim": dim, "evr": round(evr, 4),
        "mean": [round(float(x), 5) for x in pca.mean_],
        "comps": list(comps_b), "comps_s": list(comps_s),
        "emb": emb,
    }
    common.write_json_atomic(out_p, json.dumps(out, ensure_ascii=False))
    print(f"저장: {common.rel(out_p)} ({out_p.stat().st_size//1024}KB · {len(emb)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
