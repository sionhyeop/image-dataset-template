"""라벨러 채점 — 지금 라벨러가 기록된 자동라벨보다 나은지 사람 정답으로 확인한다.

언제 쓰나: 프롬프트를 고쳤을 때, 코드 선택 규칙을 바꿨을 때, 모델을 갈아끼웠을 때.
**반드시 relabel.py 로 덮어쓰기 전에 돌린다.** '코드가 나오기 시작했다'와 '더 정확해졌다'는
전혀 다른 얘기다. 실제로 배타그룹 softmax 를 도입했을 때 8개 코드가 0회→예측되기 시작했지만
그게 정확도 향상을 뜻하지는 않았다.

micro-F1 으로 잰다. '교집합이 하나라도 있으면 정답'으로 세면 코드를 많이 뱉을수록 점수가
올라가므로(전부 찍으면 100%) 과다 예측이 보상받는다. F1 은 정밀도를 함께 보므로 안전하다.

사용:
    python scripts/check/labeler_score.py            # 300장 표본
    python scripts/check/labeler_score.py -n 600
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402


def micro_f1(pairs) -> float:
    tp = sum(len(p & h) for p, h in pairs)
    fp = sum(len(p - h) for p, h in pairs)
    fn = sum(len(h - p) for p, h in pairs)
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def main() -> int:
    ap = argparse.ArgumentParser(description="현재 라벨러 vs 기록된 자동라벨 (사람 정답 기준)")
    ap.add_argument("-n", type=int, default=300, help="표본 장수")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    from clip_label import ClipLabeler

    gold = [r for r in common.read_rows(common.MASTER_META)
            if (r.get("reviewer") or "").strip() and r.get("status") == "approved"
            and (common.image_path(r)).exists()]
    if len(gold) < 50:
        print(f"사람 검수 {len(gold)}장 — 채점 불가 (최소 50장)")
        return 1
    random.seed(args.seed)
    random.shuffle(gold)
    gold = gold[:args.n]
    old = {r["image_id"]: r for r in common.read_rows(common.AUTO_LABELS)}

    # 라벨러가 실제로 다루는 축만 채점 (derived 축은 YOLO 등 다른 경로가 채운다)
    axes = [k for k in common.LABEL_AXES if common.AXIS_MODE.get(k) != "derived"]

    print(f"사람 정답 {len(gold)}장으로 채점 — CLIP 로드 중…")
    lab = ClipLabeler()

    stat = {a: {"old": [], "new": [], "co": 0, "cn": 0} for a in axes}
    for i, r in enumerate(gold):
        out = lab.label(common.image_path(r))
        if not out:
            continue
        for a in axes:
            h = set(common.split_codes(a, r.get(a)))
            if not h or out.get(a) is None:
                continue
            o = set(common.split_codes(a, (old.get(r["image_id"]) or {}).get(a)))
            v = out[a]
            n = set(v) if isinstance(v, list) else {v}
            stat[a]["old"].append((o, h))
            stat[a]["new"].append((n, h))
            stat[a]["co"] += len(o)
            stat[a]["cn"] += len(n)
        if (i + 1) % 100 == 0:
            print(f"  …{i+1}/{len(gold)}", flush=True)

    print(f"\n{'축':13s}{'n':>5s}{'기록된 F1':>11s}{'현재 F1':>10s}{'변화':>9s}   장당 코드수")
    print("-" * 68)
    allo, alln = [], []
    for a in axes:
        s = stat[a]
        if not s["old"]:
            continue
        o, n = micro_f1(s["old"]), micro_f1(s["new"])
        allo += s["old"]
        alln += s["new"]
        m = len(s["old"])
        flag = "✅" if n > o + 0.01 else ("❌" if n < o - 0.01 else "➖")
        print(f"{a:13s}{m:5d}{o:11.1%}{n:10.1%}{n-o:+9.1%} {flag}   "
              f"{s['co']/m:.2f} → {s['cn']/m:.2f}")
    print("-" * 68)
    fo, fn_ = micro_f1(allo), micro_f1(alln)
    print(f"{'전체':13s}{len(allo):5d}{fo:11.1%}{fn_:10.1%}{fn_-fo:+9.1%}")

    # 판정은 반드시 **축별로** 한다. 전체 평균으로 판단하면 한 축의 큰 개선이 다른 축의
    # 퇴보를 가려버린다(실측: camera_style +7.3%p 가 pose_action -7.5%p 를 덮어 전체 +1.3%).
    win = [a for a in axes if stat[a]["old"]
           and micro_f1(stat[a]["new"]) > micro_f1(stat[a]["old"]) + 0.01]
    lose = [a for a in axes if stat[a]["old"]
            and micro_f1(stat[a]["new"]) < micro_f1(stat[a]["old"]) - 0.01]
    if win:
        print(f"\n✅ 나아진 축: {', '.join(win)}")
        print(f"   python scripts/label/relabel.py --axes {','.join(win)}")
    if lose:
        print(f"\n❌ 나빠진 축: {', '.join(lose)} — 반영하지 말 것.")
        print("   프롬프트가 나쁘거나, CLIP 이 못 읽는 축이다(check/axis_audit.py 판정 참조).")
    if not win:
        print("\n반영할 축 없음.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
