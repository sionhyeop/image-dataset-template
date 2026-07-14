"""검출기 채점 — 백엔드를 바꾸기 전에 **같은 조건으로** 비교한다.

왜 필요한가:
    YOLOv8(ultralytics)은 **AGPL-3.0** 이다. 공개 저장소 + 웹배포면 전염 위험이 있어
    RT-DETR(Apache-2.0)로 갈아타려 한다. 하지만 "라이선스가 깨끗하다"는 이유로
    성능을 잃으면 안 된다. 검출기는 큐레이션 게이트의 심장이다 —
    실측으로 버릴 것의 48.3% 를 사람 눈에 닿기 전에 차단한다.

무엇을 재나:
    사람이 검수한 개수 축(taxonomy._semantic.count) 라벨을 정답지로 놓고,
    각 백엔드의 개수 판정을 채점한다. 다수결 베이스라인도 함께 본다
    (개수 축은 한쪽으로 쏠리기 쉬워서, 정확도만 보면 속는다 — 실제로 YOLO 의 84.9% 는
     '그냥 1인이라고 다 찍기'(95.5%)보다 못했다).

사용:
    python scripts/check/detector_score.py            # 전 백엔드 비교
    python scripts/check/detector_score.py -n 300
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="객체 검출 백엔드 비교 (사람 정답 기준)")
    ap.add_argument("-n", type=int, default=300, help="표본 장수")
    ap.add_argument("--backends", default="rtdetr,yolo", help="쉼표 구분")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from detect import Detector, config

    sem = common.load_semantic().get("count") or {}
    axis = sem.get("axis")
    if not axis:
        print("taxonomy._semantic.count.axis 가 없습니다 — 개수 축이 없는 도메인입니다.")
        return 1
    codes = {1: sem.get("single", ""), 2: sem.get("pair", ""), 3: sem.get("many", "")}

    gold = [r for r in common.read_rows(common.MASTER_META)
            if (r.get("reviewer") or "").strip() and r.get("status") == "approved"
            and (r.get(axis) or "").strip()
            and (common.image_path(r)).exists()]
    if len(gold) < 50:
        print(f"사람 검수 {len(gold)}장 — 채점 불가")
        return 1
    random.seed(args.seed)
    random.shuffle(gold)
    gold = gold[:args.n]

    cfg = config()
    print(f"검출 대상 {cfg['classes']} · 최소 면적 {cfg['min_area']:.0%} · conf {cfg['conf']}")
    print(f"사람 정답 {len(gold)}장 (개수 축: {axis})\n")

    # 다수결 베이스라인 — 최빈 코드로 전부 찍었을 때
    dist = Counter(r[axis] for r in gold)
    top, tn = dist.most_common(1)[0]
    base = tn / len(gold)

    print(f"{'백엔드':10s}{'라이선스':>12s}{'정확도':>9s}{'다수결':>9s}{'리프트':>9s}{'ms/장':>8s}")
    print("-" * 60)
    print(f"{'(다수결)':10s}{'—':>12s}{base:9.1%}{base:9.1%}{0.0:+9.1%}{'—':>8s}"
          f"   ← '{top}' 로 전부 찍기")

    lic = {"rtdetr": "Apache-2.0", "yolo": "AGPL-3.0 ⚠"}
    results = {}
    for be in [b.strip() for b in args.backends.split(",") if b.strip()]:
        det = Detector(backend=be)
        if not det.available:
            print(f"{be:10s}{lic.get(be, '?'):>12s}{'로드 실패':>9s}")
            continue
        import time
        hit = n = 0
        t0 = time.time()
        for r in gold:
            c = det.count(common.image_path(r))
            if c is None:
                continue
            n += 1
            pred = codes.get(min(c, 3), "") if c > 0 else ""
            hit += (pred == r[axis])
        el = (time.time() - t0) / max(1, n) * 1000
        acc = hit / max(1, n)
        results[be] = acc
        print(f"{be:10s}{lic.get(be, '?'):>12s}{acc:9.1%}{base:9.1%}{acc-base:+9.1%}{el:8.0f}")

    if "rtdetr" in results and "yolo" in results:
        d = results["rtdetr"] - results["yolo"]
        print(f"\nRT-DETR vs YOLO: {d:+.1%}p")
        if d >= -0.02:
            print("✅ RT-DETR 채택 가능 — 성능 손실이 2%p 이내이고 라이선스가 깨끗하다(Apache-2.0).")
        else:
            print("⚠ RT-DETR 이 2%p 넘게 뒤진다 — 채택 전 min_area/conf 를 재튜닝하라.")

    print("\n⚠ 위 '정확도'는 개수 판정 정확도일 뿐이다. 두 검출기 모두 다수결에 지는 것이 정상이다")
    print("   — 개수 축은 한쪽으로 쏠려 있어(96%가 1인) 정보량이 거의 없기 때문이다(axis_audit: dead).")
    print("   **검출기의 진짜 가치는 큐레이션 게이트다** — 버릴 것을 사람 눈에 닿기 전에 차단하는 것.")
    print("   그 성능은 gate_audit.py 로 잰다 (실측 기준: 버릴 것 48.3% 차단 / 살릴 것 2.1% 손실).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
