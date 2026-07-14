"""게이트 감사 — 사람 눈에 닿기 전에 무엇으로 걸러야 하나, 임계는 몇인가.

문제:
    수집한 이미지의 상당수는 사람이 보자마자 버린다. 그걸 사람이 보기 전에 자동으로
    걸러내면 검수 노동이 그만큼 줄어든다. 하지만 어떤 신호로, 어떤 임계로 자를지를
    손으로 찍으면 좋은 사진까지 같이 죽는다.

핵심 구분 — **차단(gate)과 정렬(rank)은 다른 신호가 맡아야 한다:**
    · 차단에 쓸 수 있는 신호 = 임계 하나로 자를 때 '버릴 것'만 골라 죽는 신호.
      실측: 객체검출(person_score) 은 버릴 사진의 48.3% 를 자르며 살릴 사진은 2.1% 만 잃는다.
    · 정렬에만 쓸 수 있는 신호 = 점수가 연속적이라 자를 경계가 없는 신호.
      실측: 목적문장 CLIP 유사도로 차단하면 버릴 사진 69% 를 자르는 대신 **살릴 사진 37% 도 죽는다.**
      대신 검수 순서를 정하는 데는 탁월하다(상위 절반의 keep 밀도 60% → 83%).

이 스크립트는 사람의 keep/discard 판정을 정답으로 놓고, review_queue.csv 의 모든 수치
컬럼에 대해 위 두 성질을 측정한다. 컬럼 이름도 도메인도 모른다.

출력:
    annotations/curation_profile.json — gates(차단 신호·임계) / rank(정렬 신호)

사용:
    python scripts/check/gate_audit.py
    python scripts/check/gate_audit.py --write --max-keep-loss 0.05
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

MIN_N = 100          # 이보다 표본이 적으면 판정 보류
MIN_CUT = 0.10       # 버릴 사진을 최소 이만큼은 잘라야 게이트로 쓸 값어치가 있다
RANK_LIFT_MIN = 0.05  # 상위 절반 keep 밀도가 전체보다 이만큼 높아야 정렬 신호로 인정


def main() -> int:
    ap = argparse.ArgumentParser(description="큐레이션 게이트·랭킹 신호를 사람 판정으로 측정")
    ap.add_argument("--write", action="store_true", help="annotations/curation_profile.json 저장")
    ap.add_argument("--max-keep-loss", type=float, default=0.05,
                    help="게이트가 잃어도 되는 '살릴 사진'의 최대 비율 (기본 5%%)")
    args = ap.parse_args()

    q = {r["image_id"]: r for r in common.read_rows(common.ANNOTATIONS / "review_queue.csv")}
    dec = [r for r in common.read_rows(common.ANNOTATIONS / "review_decisions.csv")
           if r.get("decision") in ("keep", "discard") and r["image_id"] in q]
    if len(dec) < MIN_N:
        print(f"사람 판정 {len(dec)}건 — 게이트 감사 불가 (최소 {MIN_N}건)")
        return 1

    # 수치 컬럼을 자동 발견 (컬럼 이름을 모른다 — 도메인 무관)
    sample = q[dec[0]["image_id"]]
    feats = []
    for c in sample:
        if c in ("image_id", "file_path", "width", "height"):
            continue
        vals = []
        for r in dec:
            v = q[r["image_id"]].get(c, "")
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                vals = None
                break
        if vals and len(set(vals)) > 2:      # 상수·이진값은 게이트 후보로 무의미
            feats.append((c, vals))

    y = [r["decision"] == "keep" for r in dec]
    K, D = sum(y), len(y) - sum(y)
    base = K / len(y)
    print(f"사람 판정 {len(dec)}건 (keep {K} / discard {D}) · 원본 keep 밀도 {base:.1%}")
    print(f"수치 신호 {len(feats)}개 자동 발견: {[c for c, _ in feats]}\n")

    V = dict(feats)

    def gate_of(c: str):
        """'살릴 것 손실 ≤ 예산' 안에서 버릴 것을 최대한 자르는 임계."""
        vals, best = V[c], None
        for t in sorted(set(vals)):
            cut = sum(1 for v, k in zip(vals, y) if not k and v < t) / max(1, D)
            loss = sum(1 for v, k in zip(vals, y) if k and v < t) / max(1, K)
            if loss <= args.max_keep_loss and (best is None or cut > best[1]):
                best = (t, cut, loss)
        return best

    def rank_lift(c: str, idx: list[int]) -> float:
        """idx 안에서 c 로 정렬했을 때 상위 절반의 keep 밀도 - idx 전체의 keep 밀도.
        **게이트를 통과한 집합 안에서 재야 한다** — 정렬은 게이트 뒤에 오기 때문이다.
        게이트 전에 재면 게이트가 이미 한 일을 정렬의 공으로 돌리게 된다."""
        if len(idx) < 20:
            return 0.0
        d0 = sum(1 for i in idx if y[i]) / len(idx)
        top = sorted(idx, key=lambda i: -V[c][i])[:len(idx) // 2]
        return (sum(1 for i in top if y[i]) / max(1, len(top))) - d0

    ALL = list(range(len(y)))
    print(f"{'신호':18s}{'게이트 임계':>10s}{'버릴것 차단':>11s}{'살릴것 손실':>11s}"
          f"{'단독 정렬':>10s}  역할")
    print("-" * 78)
    gates = []
    for c, vals in feats:
        g = gate_of(c)
        lift = rank_lift(c, ALL)
        # 값의 가짓수가 아주 적은 신호(예: -3/1/2 세 값)는 '정렬'로 쓰면 사실상 게이트의
        # 재표현일 뿐이다. 진짜 정렬 신호는 연속적이어야 한다.
        discrete = len(set(vals)) <= 5
        role = []
        if g and g[1] >= MIN_CUT:
            role.append("차단")
            gates.append({"signal": c, "min": round(g[0], 4),
                          "cuts_discard": round(g[1], 4), "loses_keep": round(g[2], 4),
                          "composite": c == "final_score"})
        if lift >= RANK_LIFT_MIN and not discrete:
            role.append("정렬")
        print(f"{c:18s}{(f'{g[0]:.2f}' if g else '—'):>10s}"
              f"{(f'{g[1]:.1%}' if g else '—'):>11s}{(f'{g[2]:.1%}' if g else '—'):>11s}"
              f"{lift:>+10.1%}  {'+'.join(role) if role else '쓸모없음'}"
              + ("  ※다른 신호의 합성값" if c == "final_score" else "")
              + ("  ※값 가짓수 적음" if discrete else ""))

    # 게이트는 합성값이 아닌 것 중 가장 많이 자르는 것을 고른다(합성값은 순환 위험).
    prim = [g for g in gates if not g["composite"]]
    prim.sort(key=lambda g: -g["cuts_discard"])
    print(f"\n■ 차단(gate) — 사람 눈에 닿기 전에 자른다. 살릴 것 손실 예산 {args.max_keep_loss:.0%}")
    for g in gates:
        tag = "  ← 채택" if prim and g["signal"] == prim[0]["signal"] else \
              ("  (합성값 — 게이트로는 제외)" if g["composite"] else "")
        print(f"  · {g['signal']} >= {g['min']}  →  버릴 것의 {g['cuts_discard']:.1%} 차단"
              f" (살릴 것 {g['loses_keep']:.1%} 손실){tag}")
    if not prim:
        print("  없음 — 임계 하나로 안전하게 자를 수 있는 신호가 없다.")
        ranks, passed = [], ALL
    else:
        g = prim[0]
        passed = [i for i in ALL if V[g["signal"]][i] >= g["min"]]
        d1 = sum(1 for i in passed if y[i]) / max(1, len(passed))
        # 정렬은 **게이트를 통과한 집합 안에서** 재고, 게이트 신호 자신은 후보에서 뺀다.
        ranks = [{"signal": c, "lift": round(rank_lift(c, passed), 4)}
                 for c, vals in feats
                 if c != g["signal"] and c != "final_score" and len(set(vals)) > 5]
        ranks = [r for r in ranks if r["lift"] >= RANK_LIFT_MIN]
        ranks.sort(key=lambda r: -r["lift"])

        print(f"\n■ 정렬(rank) — 게이트 통과분 {len(passed)}장(keep 밀도 {d1:.1%}) 안에서 순서만 정한다")
        if ranks:
            for r in ranks:
                print(f"  · {r['signal']}  상위 절반 keep 밀도 {d1 + r['lift']:.1%}"
                      f"  ({r['lift']:+.1%})")
        else:
            print("  게이트 통과분 안에서는 유의미하게 순서를 매기는 신호가 없다.")

        print("\n■ 파이프라인 효과")
        line = f"  원본 {base:.1%}  →  [{g['signal']} 게이트] {d1:.1%}"
        if ranks:
            top = sorted(passed, key=lambda i: -V[ranks[0]['signal']][i])[:len(passed) // 2]
            d2 = sum(1 for i in top if y[i]) / max(1, len(top))
            line += f"  →  [{ranks[0]['signal']} 상위 절반] {d2:.1%}"
        print(line)
        print(f"  검수 노동: {len(ALL)}장 → {len(passed)}장"
              f" ({1 - len(passed)/len(ALL):.0%} 감소, 살릴 것 {g['loses_keep']:.1%} 손실)")

    # --- final_score 가중치 — **실제 정렬을 통과시켜** 측정한다 -----------------
    # 신호별 단독 리프트의 비율로 가중치를 주면 안 된다. 상관된 신호가 이중계산되기 때문이다.
    # (이번 프로젝트에서 프록시 최적화에 세 번 데였다 — 벤치가 곧 목표 함수다)
    # final_score 가 존재하는 이유는 '검수 순서'다. 그러니 그 순서로 채점한다:
    #   게이트 통과분을 final_score 로 정렬 → 상위 절반의 keep 밀도가 곧 점수.
    sigs = [c for c, _ in feats if c != "final_score"]
    idx = passed if prim else ALL
    d0 = sum(1 for i in idx if y[i]) / max(1, len(idx))

    def score_w(w: dict) -> float:
        """이 가중치로 정렬했을 때 상위 절반의 keep 밀도."""
        val = {i: sum(w.get(c, 0) * V[c][i] for c in sigs) for i in idx}
        top = sorted(idx, key=lambda i: -val[i])[:max(1, len(idx) // 2)]
        return sum(1 for i in top if y[i]) / len(top)

    # 좌표 하강 — 신호 하나씩 격자를 훑어 개선되면 채택. 전수 격자는 신호가 늘면 폭발한다.
    GRID = [0.0, 0.5, 1.0, 1.5, 2.0, 3.0]
    best_w = {c: 1.0 for c in sigs}
    best = score_w(best_w)
    for _ in range(3):                       # 3회 순회면 대개 수렴
        for c in sigs:
            for g in GRID:
                cand = {**best_w, c: g}
                sc = score_w(cand)
                if sc > best + 1e-9:
                    best, best_w = sc, cand
    hand = {"clip_score": 2.0, "person_score": 1.0, "resolution_score": 1.0,
            "aspect_score": 1.0, "keyword_score": 1.0}
    hand_sc = score_w({c: hand.get(c, 0) for c in sigs})
    measured_w = {c: round(v, 2) for c, v in best_w.items()}

    print(f"\n■ final_score 가중치 — 실제 정렬로 채점 (게이트 통과분 {len(idx)}장, 기준 {d0:.1%})")
    print(f"  {'':22s}{'상위절반 keep':>13s}")
    print(f"  {'손으로 찍음':21s}{hand_sc:13.1%}   "
          + " ".join(f"{c.split('_')[0]}={hand.get(c, 0):.1f}" for c in sigs))
    print(f"  {'측정된 최적':21s}{best:13.1%}   "
          + " ".join(f"{c.split('_')[0]}={measured_w[c]:.1f}" for c in sigs)
          + f"   ({best - hand_sc:+.1%})")
    print("  → taxonomy._curation.score.weights 에 옮겨라")

    prof = {
        "schema": "curation-profile-0.2",
        "n": len(dec), "keep_density": round(base, 4),
        "max_keep_loss": args.max_keep_loss,
        "gates": prim[:1], "gates_all": gates, "rank": ranks,
        "weights": measured_w,          # → taxonomy._curation.score.weights
    }
    if args.write:
        p = common.ANNOTATIONS / "curation_profile.json"
        common.write_json_atomic(p, json.dumps(prof, ensure_ascii=False, indent=2))
        print(f"\n저장: {common.rel(p)}")
        print("   → taxonomy.yaml 의 _curation.gates / _curation.rank 에 반영하라")
    else:
        print("\n(--write 를 붙이면 curation_profile.json 저장)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
