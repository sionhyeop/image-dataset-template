"""열화 입력 프로브 — 라이브 카메라처럼 '깨끗하지 않은' 입력에서도 신호가 버티는가.

왜 필요한가:
    포즈캠 벤치는 각 이미지의 **갤러리 임베딩을 그대로 라이브 입력으로 재사용**한다. 즉
    '라이브 추출이 완벽하다'고 가정한다. 실제 카메라 프레임은 해상도·압축·블러가 다르므로
    그 가정은 낙관적이다. 어떤 모델은 이 열화에 강인하고 어떤 모델은 무너진다.
    강인함이 다르면 **벤치에서 이긴 모델이 실전에서 질 수 있다.**

무엇을 하나:
    원본 이미지를 앱 썸네일과 같은 수준(짧은 변 190px, JPEG q70)으로 열화시켜 다시 임베딩하고,
    그 벡터를 '라이브'로 삼아 인덱스(원본 기반)에서 이웃을 찾는다. 원본을 라이브로 썼을 때와
    이웃이 얼마나 겹치는지가 곧 **그 신호의 열화 내성**이다.

사용:
    python scripts/check/degrade_probe.py            # 전 시각 신호 비교
    python scripts/check/degrade_probe.py --n 300    # 표본 축소(빠르게)
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

THUMB = 190       # 앱 썸네일과 동일 (app.py --thumb)
QUALITY = 70
TOP_K = 10


def _dequant(v):
    import numpy as np
    raw = base64.b64decode(v["b"])
    return np.frombuffer(raw, dtype=np.int8).astype(np.float32) * float(v["s"])


def main() -> int:
    ap = argparse.ArgumentParser(description="저화질 입력에 대한 신호별 내성 측정")
    ap.add_argument("--n", type=int, default=400, help="표본 장수")
    args = ap.parse_args()

    import numpy as np
    from PIL import Image

    rows = [r for r in common.read_rows(common.MASTER_META) if r.get("status") == "approved"]
    rows = [r for r in rows if (common.image_path(r)).exists()][:args.n]
    print(f"표본 {len(rows)}장 — 원본을 {THUMB}px/q{QUALITY} 로 열화시켜 다시 임베딩한다")

    tmp = Path(tempfile.mkdtemp(prefix="degrade_"))
    items = []
    for r in rows:
        p = tmp / f"{r['image_id']}.jpg"
        with Image.open(common.image_path(r)) as im:
            im = im.convert("RGB")
            im.thumbnail((THUMB, THUMB))
            im.save(p, format="JPEG", quality=QUALITY)
        items.append({"id": r["image_id"], "path": str(p)})
    ids_json = tmp / "ids.json"
    ids_json.write_text(json.dumps(items), encoding="utf-8")

    results = {}
    for sig in ("dino", "visual"):
        idx_p = common.ANNOTATIONS / f"{sig}_index.json"
        emb_dir = SCRIPTS / "index" / f"{sig}_embed"
        if not idx_p.exists() or not emb_dir.exists():
            continue
        idx_head = json.loads(idx_p.read_text(encoding="utf-8"))
        raw_out = tmp / f"{sig}_degraded.json"
        # 인덱스를 만든 것과 **똑같은 dtype·입력크기**로 열화 임베딩을 뽑아야 한다.
        # 다르면 여기서 재는 게 '열화 내성'이 아니라 'dtype 불일치'가 되어버린다.
        argv = ["node", "embed.mjs", str(ids_json), str(raw_out)]
        if idx_head.get("dtype"):
            argv.append(str(idx_head["dtype"]))
            argv.append(str(idx_head.get("input_size") or ""))
        print(f"\n[{sig}] 열화 이미지 임베딩 (dtype={idx_head.get('dtype') or '기본'})…")
        rc = subprocess.run(argv, cwd=emb_dir, capture_output=True, text=True)
        if rc.returncode or not raw_out.exists():
            print(f"  실패: {rc.stderr[-200:]}")
            continue

        idx = idx_head
        deg = json.loads(raw_out.read_text(encoding="utf-8"))
        keep = [i["id"] for i in items if i["id"] in deg and i["id"] in idx["emb"]]
        if len(keep) < 50:
            print(f"  표본 부족 {len(keep)}")
            continue

        # 갤러리(원본 기반, PCA 후) 벡터
        G = np.stack([_dequant(idx["emb"][i]) for i in keep])
        G /= np.linalg.norm(G, axis=1, keepdims=True) + 1e-9

        # 열화 라이브 벡터를 **앱과 똑같은 방식**으로 같은 PCA 공간에 사영한다
        mean = np.asarray(idx["mean"], dtype=np.float32)
        comps = np.stack([_dequant({"b": b, "s": s})
                          for b, s in zip(idx["comps"], idx["comps_s"])])
        L = np.stack([np.asarray(deg[i], dtype=np.float32) for i in keep])
        Y = (L - mean) @ comps.T
        Y /= np.linalg.norm(Y, axis=1, keepdims=True) + 1e-9

        # ① 자기 자신과의 코사인 (같은 이미지인데 벡터가 얼마나 흔들리나)
        self_cos = float(np.mean(np.sum(Y * G, axis=1)))
        # ② 이웃 유지율 — 진짜 중요한 것. 열화 전 top-K 이웃을 열화 후에도 찾아내는가.
        S_clean = G @ G.T
        np.fill_diagonal(S_clean, -2)
        S_deg = Y @ G.T
        for k in range(len(keep)):
            S_deg[k, k] = -2
        keep_rate = []
        for k in range(len(keep)):
            a = set(np.argpartition(-S_clean[k], TOP_K)[:TOP_K])
            b = set(np.argpartition(-S_deg[k], TOP_K)[:TOP_K])
            keep_rate.append(len(a & b) / TOP_K)
        results[sig] = (self_cos, float(np.mean(keep_rate)), len(keep))

    print(f"\n{'신호':10s}{'자기 코사인':>12s}{'이웃 유지율':>12s}   해석")
    print("-" * 62)
    for sig, (sc, kr, n) in sorted(results.items(), key=lambda x: -x[1][1]):
        note = "강인" if kr >= 0.6 else ("보통" if kr >= 0.4 else "취약 — 라이브에서 무너질 수 있다")
        print(f"{sig:10s}{sc:12.3f}{kr:12.1%}   {note}  (n={n})")
    print("\n이웃 유지율 = 깨끗한 입력으로 찾은 top-10 이웃을 열화 입력으로도 몇 개나 찾아내는가.")
    print("이 값이 낮은 신호는 벤치(완벽 추출 가정)에서 이겨도 실제 카메라에서는 질 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
