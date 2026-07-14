"""색감 인덱스 — 각 approved 컷의 Lab 3×3 그리드 평균(27차원) → int8 → color_index.json.

포즈캠의 '색감 최우선' 매칭용. 라이브 카메라 프레임도 브라우저에서 같은 수식(sRGB→Lab,
3×3 셀 평균)으로 계산해 직접 비교한다 — 모델 없이 순수 산술이라 공간 일치가 보장됨.

양자화: L×1.27, a·b 그대로 (int8, ±127 클램프). 사용: python scripts/index/color_index.py
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

W, H, G = 48, 60, 3
OUT = common.ANNOTATIONS / "color_index.json"


def rgb_to_lab(arr):
    """arr: (h,w,3) float 0..1 sRGB → (h,w,3) Lab (D65). 브라우저 srgb2lab 와 동일 수식."""
    import numpy as np
    lin = np.where(arr <= 0.04045, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)
    X = lin @ np.array([0.4124564, 0.3575761, 0.1804375])
    Y = lin @ np.array([0.2126729, 0.7151522, 0.0721750])
    Z = lin @ np.array([0.0193339, 0.1191920, 0.9503041])
    Xn, Yn, Zn = 0.95047, 1.0, 1.08883
    d = (6 / 29) ** 3

    def f(t):
        return np.where(t > d, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)

    fx, fy, fz = f(X / Xn), f(Y / Yn), f(Z / Zn)
    return np.stack([116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)], axis=-1)


def main() -> int:
    import numpy as np
    from PIL import Image

    rows = [r for r in common.read_rows(common.MASTER_META) if r["status"] == "approved"]
    out = {}
    for r in rows:
        # common.ROOT 는 **저장소 루트**다 — 데이터 경로로 쓰면 다른 데이터셋을 가리킨다.
        # 데이터는 common.PROCESSED(= DATASET_ROOT/data/02_processed) 아래에 있다.
        # 이 버그는 음식 도메인을 실제로 돌려보고서야 드러났다(색 인덱스가 0장이었다).
        p = common.PROCESSED / "512" / (Path(r["file_path"]).stem + ".jpg")
        if not p.exists():
            continue
        im = Image.open(p).convert("RGB").resize((W, H))
        lab = rgb_to_lab(np.asarray(im, dtype=np.float64) / 255.0)
        vec = []
        for gy in range(G):
            for gx in range(G):
                cell = lab[gy * H // G:(gy + 1) * H // G, gx * W // G:(gx + 1) * W // G]
                m = cell.mean(axis=(0, 1))
                vec += [m[0] * 1.27, m[1], m[2]]        # L 스케일 → int8 범위
        q = np.clip(np.round(vec), -127, 127).astype("int8")
        out[r["image_id"]] = base64.b64encode(q.tobytes()).decode()

    common.write_json_atomic(OUT, json.dumps(
        {"schema": "color-index-0.1", "grid": f"{G}x{G}", "dim": G * G * 3, "emb": out},
        ensure_ascii=False))
    print(f"저장: {common.rel(OUT)} ({OUT.stat().st_size//1024}KB · {len(out)}장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
