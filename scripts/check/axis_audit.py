"""축 감사 — 라벨 축이 '살아있는지' 데이터로 판정한다. (도메인 무관)

사람 검수 라벨을 정답지로 놓고 자동라벨(CLIP/YOLO 등)을 채점하되,
정확도만 보지 않고 **다수결 베이스라인 대비 리프트**와 **분포 엔트로피**를 함께 본다.
이 둘이 축의 운명을 가른다:

  · 엔트로피 낮음        → 한 코드에 몰빵. 축이 아니라 '큐레이션 조건'이다.
                           (예: 95%가 1인 → person_count 는 축이 아니라 "1인만 모은다"는 조건)
  · 엔트로피 높은데 리프트 낮음 → 축은 의미 있는데 모델이 못 읽는다. 전문 모델 or 사람 전용.
  · 리프트 높음          → 자동라벨 신뢰. 사람은 검수만.

새 도메인에 이 파이프라인을 얹으면 taxonomy 초안은 반드시 틀린다. 이 스크립트가
'어디가 틀렸는지'를 사람 대신 말해주는 것이 템플릿화의 핵심이다.

사용:
    python scripts/check/axis_audit.py                 # 표로 출력
    python scripts/check/axis_audit.py --json          # annotations/axis_audit.json 저장
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

# 판정 임계 — 도메인 무관하게 의미가 통하는 값들만 쓴다.
ENTROPY_DEAD = 0.35      # 이하면 축이 한 코드에 몰빵 → 정보량 없음
ENTROPY_WEAK = 0.50
LIFT_BLIND = 0.10        # 이하면 모델이 축을 못 읽음
LIFT_OK = 0.15
MIN_GOLD = 30            # 이보다 적으면 판정 보류
DEAD_CODE_SUPPORT = 3    # 사람 정답에 이보다 적게 나온 코드 = 죽은 코드
DOMINANT_SHARE = 0.80    # 한 코드가 이 이상 차지 = 축 무의미
CONFUSE_SHARE = 0.40     # 모델이 A라 했는데 사람은 B가 이 이상 = 혼동쌍
COND_SPREAD = 0.18       # 다른 축의 값에 따라 F1 이 이만큼 갈리면 = 조건부 축
COND_MIN_N = 30          # 조건 구간별 최소 표본


def _micro_f1(pairs: list[tuple[set, set]]) -> float:
    """예측/정답 코드 집합 쌍들의 micro-F1. 단일축(집합 크기 1)이면 정확도와 같다."""
    tp = sum(len(p & h) for p, h in pairs)
    fp = sum(len(p - h) for p, h in pairs)
    fn = sum(len(h - p) for p, h in pairs)
    return 0.0 if tp == 0 else 2 * tp / (2 * tp + fp + fn)


def _norm_entropy(counter: Counter) -> float:
    """정규화 엔트로피 (0=한 코드 몰빵, 1=완전 균등)."""
    tot = sum(counter.values())
    if tot == 0 or len(counter) < 2:
        return 0.0
    h = -sum((v / tot) * math.log2(v / tot) for v in counter.values() if v)
    return h / math.log2(len(counter))


def _exclusive_groups(axis: str) -> list[set[str]]:
    """taxonomy._exclusive 가 선언한 '동시에 참일 수 없는' 코드 그룹. 없으면 빈 리스트."""
    tx = common.load_yaml(common.TAXONOMY_PATH) or {}
    return [set(g) for g in (tx.get("_exclusive", {}) or {}).get(axis, [])]


def _load_auto() -> dict[str, dict]:
    """자동라벨 스냅샷. auto_labels.csv 가 정본, 없으면 가장 오래된 백업으로 폴백."""
    p = common.ANNOTATIONS / "auto_labels.csv"
    if p.exists():
        return {r["image_id"]: r for r in common.read_rows(p)}
    backups = sorted((common.ANNOTATIONS / "_backups").glob("master_metadata.*.csv"))
    if not backups:
        return {}
    print(f"  ⚠ auto_labels.csv 없음 → 백업으로 폴백: {backups[0].name}", file=sys.stderr)
    return {r["image_id"]: r for r in common.read_rows(backups[0])}


def audit() -> dict:
    auto = _load_auto()
    taxonomy = common.load_taxonomy()
    # 정답지 = 사람이 검수한 행 (reviewer 가 찍힌 것)
    gold_rows = [r for r in common.read_rows(common.MASTER_META)
                 if (r.get("reviewer") or "").strip() and r.get("status") == "approved"]

    out: dict = {"n_gold": len(gold_rows), "axes": {}}
    if len(gold_rows) < MIN_GOLD:
        out["error"] = f"사람 검수 {len(gold_rows)}장 — 최소 {MIN_GOLD}장 필요"
        return out

    single_axes = [k for k, _, m in common.AXES if not m]   # 조건 분할에 쓸 축(단일값이어야 함)

    for ax, name, multi in common.AXES:
        pairs = []  # (모델 예측 집합, 사람 정답 집합)
        cond = []   # 같은 순서로, 그 사진의 다른 축 값들 — 조건부 축 탐지용
        for r in gold_rows:
            human = set(common.split_codes(ax, r.get(ax)))
            if not human:
                continue        # 사람이 비워둔 축은 채점 불가
            a = auto.get(r["image_id"])
            pairs.append((set(common.split_codes(ax, a.get(ax))) if a else set(), human))
            cond.append({c: (r.get(c) or "") for c in single_axes if c != ax})
        n = len(pairs)
        if n < MIN_GOLD:
            out["axes"][ax] = {"name": name, "n": n, "verdict": "unknown",
                               "reason": f"표본 {n}장 — 판정 보류"}
            continue

        human_cnt = Counter(c for _, h in pairs for c in h)
        top_code, top_n = human_cnt.most_common(1)[0]
        # 채점: 멀티축은 micro-F1. '교집합이 하나라도 있으면 정답'으로 세면 코드를 많이
        # 뱉을수록 점수가 오르므로(전부 찍으면 100%) 과다 예측이 보상받는다. F1 은 정밀도를
        # 함께 보므로 그 함정이 없다. 단일축은 F1 이 곧 정확도와 같다.
        # 베이스라인도 같은 잣대로 — 최빈 코드 하나만 전부에게 붙였을 때의 F1.
        acc = _micro_f1(pairs)
        baseline = _micro_f1([({top_code}, h) for _, h in pairs])
        lift = acc - baseline
        ent = _norm_entropy(human_cnt)
        share = top_n / max(1, sum(human_cnt.values()))

        # --- 조건부 축 탐지 ---------------------------------------------------
        # 어떤 축은 '다른 축의 값에 따라' 답할 수 있고 없고가 갈린다. 예: 얼빡샷에서는
        # 다리가 안 보이므로 서기/앉기를 맞히라는 건 이미지에 답이 없는 문제다.
        # 다른 단일축의 코드로 표본을 쪼개 F1 이 크게 갈리면 그 축은 '조건부'다.
        # 모델을 바꿔서 해결될 문제가 아니므로, 반드시 구분해서 보고해야 한다.
        conds = []
        for cax in single_axes:
            if cax == ax:
                continue
            buckets: dict[str, list] = defaultdict(list)
            for k, pr in enumerate(pairs):
                v = cond[k].get(cax)
                if v:
                    buckets[v].append(pr)
            scored = {v: _micro_f1(b) for v, b in buckets.items() if len(b) >= COND_MIN_N}
            if len(scored) < 2:
                continue
            hi, lo = max(scored, key=scored.get), min(scored, key=scored.get)
            spread = scored[hi] - scored[lo]
            if spread >= COND_SPREAD:
                conds.append({
                    "by": cax, "spread": round(spread, 3),
                    "best": [hi, round(scored[hi], 3), len(buckets[hi])],
                    "worst": [lo, round(scored[lo], 3), len(buckets[lo])],
                    "msg": f"{cax} 가 {hi} 일 땐 F1 {scored[hi]:.0%}, {lo} 일 땐 {scored[lo]:.0%} "
                           f"— 이 축은 조건부로만 답할 수 있다. {lo} 인 사진에는 라벨을 요구하지 마라.",
                })
        conds.sort(key=lambda c: -c["spread"])

        # --- 판정 -----------------------------------------------------------
        if ent < ENTROPY_DEAD or share >= DOMINANT_SHARE:
            verdict, reason = "dead", (
                f"'{top_code}' 가 {share:.0%} 를 차지 — 축이 아니라 큐레이션 조건이다. "
                f"축에서 빼고 수집 게이트로 강등하라.")
        elif lift < LIFT_BLIND:
            verdict, reason = "model_blind", (
                f"분포는 고른데(엔트로피 {ent:.2f}) 모델이 못 읽는다(리프트 {lift:+.1%}). "
                f"전문 모델을 붙이거나 사람 전용 축으로 표시하라.")
        elif lift < LIFT_OK or ent < ENTROPY_WEAK:
            verdict, reason = "weak", "프롬프트 보강 또는 코드 병합 검토."
        else:
            verdict, reason = "healthy", "자동라벨 신뢰 가능 — 사람은 검수만."

        # --- 코드 단위 진단 ---------------------------------------------------
        pred_cnt = Counter(c for p, _ in pairs for c in p)
        codes = []
        for c in taxonomy.get(ax, {}):
            sup, pre = human_cnt.get(c, 0), pred_cnt.get(c, 0)
            tp = sum(1 for p, h in pairs if c in p and c in h)
            codes.append({
                "code": c, "support": sup, "predicted": pre,
                "precision": round(tp / pre, 3) if pre else None,
                "recall": round(tp / sup, 3) if sup else None,
                "dead": sup < DEAD_CODE_SUPPORT,
            })

        # 혼동쌍은 '동시에 참일 수 없는' 코드끼리만 의미가 있다. 단일축은 축 전체가,
        # 멀티축은 taxonomy._exclusive 로 선언된 그룹 안에서만 배타적이다.
        # (멀티축에서 이 제약 없이 세면 공존 가능한 코드가 허위 혼동쌍으로 잡힌다.)
        excl_groups = _exclusive_groups(ax) if multi else [set(taxonomy.get(ax, {}))]
        confuse: dict[str, Counter] = defaultdict(Counter)
        for p, h in pairs:
            for grp in excl_groups:
                for c in (p & grp) - h:      # 모델이 이 그룹에서 c 를 골랐는데
                    for t in (h & grp) - p:  # 사람은 같은 그룹에서 t 를 골랐다
                        confuse[c][t] += 1

        sugg = []
        for c in codes:
            # 모델이 '한 번도' 출력한 적 없는 코드 = 정확도 문제가 아니라 파이프라인 문제.
            # taxonomy 에 코드를 추가했는데 자동라벨을 재실행하지 않으면 여기 걸린다.
            # (auto_label.py 는 이미 라벨된 이미지를 스킵하므로 신규 코드가 영원히 안 붙는다)
            if c["predicted"] == 0 and c["support"] > 0:
                sugg.append({"kind": "never_predicted", "code": c["code"],
                             "msg": f"{c['code']} — 사람은 {c['support']}회 썼는데 모델은 0회. "
                                    f"자동라벨이 taxonomy 보다 낡았다. 재라벨이 필요하다."})
            elif c["dead"]:
                sugg.append({"kind": "dead_code", "code": c["code"],
                             "msg": f"{c['code']} — 사람 정답에 {c['support']}회. "
                                    f"데이터에 없는 코드다. 삭제 후보."})
            elif c["precision"] is not None and c["precision"] < 0.30 and c["predicted"] >= 10:
                sugg.append({"kind": "over_predicted", "code": c["code"],
                             "msg": f"{c['code']} — 모델이 {c['predicted']}회 붙였는데 정밀도 "
                                    f"{c['precision']:.0%}. 프롬프트가 너무 넓다."})
            elif c["recall"] is not None and c["recall"] < 0.30 and c["support"] >= 10:
                sugg.append({"kind": "missed", "code": c["code"],
                             "msg": f"{c['code']} — 사람은 {c['support']}회 썼는데 재현율 "
                                    f"{c['recall']:.0%}. 모델이 못 찾는다."})
        for c, tgt in confuse.items():
            t, cnt = tgt.most_common(1)[0]
            tot = sum(tgt.values())
            if tot >= DEAD_CODE_SUPPORT and cnt / tot >= CONFUSE_SHARE:
                sugg.append({"kind": "confusable", "code": c, "with": t,
                             "msg": f"{c} ↔ {t} — 모델이 {c} 라 한 것 중 {cnt/tot:.0%} 가 "
                                    f"실제로는 {t}. 병합하거나 프롬프트를 갈라라."})

        out["axes"][ax] = {
            "name": name, "multi": multi, "n": n,
            "baseline_f1": round(baseline, 4), "auto_f1": round(acc, 4),
            "lift": round(lift, 4), "entropy": round(ent, 3),
            "top_code": top_code, "top_share": round(share, 3),
            "verdict": verdict, "reason": reason, "conditional": conds,
            "codes": codes, "suggestions": sugg,
        }
    return out


ICON = {"healthy": "🟢", "weak": "🟡", "dead": "🔴", "model_blind": "🔴", "unknown": "⚪"}


def report(res: dict) -> int:
    if "error" in res:
        print(f"❌ {res['error']}")
        return 1
    print(f"축 감사 — 사람 검수 {res['n_gold']}장 기준\n")
    print(f"{'축':14s}{'n':>6s}{'다수결F1':>10s}{'자동F1':>9s}{'리프트':>9s}{'엔트로피':>9s}  판정")
    print("-" * 74)
    for ax, a in res["axes"].items():
        if a["verdict"] == "unknown":
            print(f"{a['name']:14s}{a['n']:6d}{'':>34s}  ⚪ {a['reason']}")
            continue
        print(f"{a['name']:14s}{a['n']:6d}{a['baseline_f1']:10.1%}{a['auto_f1']:9.1%}"
              f"{a['lift']:+9.1%}{a['entropy']:9.2f}  {ICON[a['verdict']]} {a['verdict']}")
    # 파이프라인 결함 먼저 — 축 설계 문제가 아니라 '자동라벨이 낡은' 문제라 처방이 다르다.
    stale = [(a["name"], s["code"]) for a in res["axes"].values()
             for s in a.get("suggestions", []) if s["kind"] == "never_predicted"]
    if stale:
        print(f"\n⚠ 자동라벨이 taxonomy 보다 낡음 — 모델이 한 번도 출력한 적 없는 코드 {len(stale)}개")
        for nm, c in stale:
            print(f"    [{nm}] {c}")
        print("    → auto_label.py 는 이미 라벨된 이미지를 스킵한다. 신규 코드는 재라벨해야 붙는다.")
        print("    → 그때까지 이 코드들은 100% 사람 손으로만 붙는다(검수 부담).")

    condl = [(a["name"], c) for a in res["axes"].values() for c in a.get("conditional", [])]
    if condl:
        print("\n■ 조건부 축 — 다른 축의 값에 따라 '답이 이미지에 없는' 축")
        for nm, c in condl:
            print(f"  · [{nm}] {c['msg']}")

    bad = [a for a in res["axes"].values() if a["verdict"] in ("dead", "model_blind")]
    if bad:
        print("\n■ 재설계가 필요한 축")
        for a in bad:
            print(f"  {ICON[a['verdict']]} {a['name']}: {a['reason']}")
    sugg = [(a["name"], s) for a in res["axes"].values() for s in a.get("suggestions", [])]
    if sugg:
        print("\n■ 코드 단위 제안")
        for nm, s in sugg[:20]:
            print(f"  · [{nm}] {s['msg']}")
        if len(sugg) > 20:
            print(f"  … 외 {len(sugg)-20}건 (--json 으로 전량 확인)")
    print("\n다수결F1 = 최빈 코드 하나만 전부에게 붙였을 때 · 리프트 = 자동라벨이 그걸 이긴 폭 (축의 실제 가치)")
    print("micro-F1 을 쓰는 이유: '교집합≥1' 로 세면 코드를 많이 뱉을수록 점수가 올라 과다예측이 보상받는다")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="라벨 축이 데이터에서 살아있는지 감사")
    ap.add_argument("--json", action="store_true", help="annotations/axis_audit.json 으로 저장")
    args = ap.parse_args()
    res = audit()
    rc = report(res)
    if args.json:
        p = common.ANNOTATIONS / "axis_audit.json"
        common.write_json_atomic(p, json.dumps(res, ensure_ascii=False, indent=2))
        print(f"\n저장: {common.rel(p)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
