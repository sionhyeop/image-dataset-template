"""데이터셋 최종 점검 (가이드 10절).

- 라벨 코드가 taxonomy 에 있는지 (오타/미정의 코드 탐지)
- file_path 실재 여부
- 주축 카테고리별 장수와 최소 수량(기본 100) 미달 경고
- 조합 다양성: 주축 안에서 부축 분포가 한쪽으로 쏠렸는지
- split 간 pHash 그룹 누수 여부

주축·부축은 taxonomy._semantic.pair 가 정한다(없으면 구도축 앞 두 개).
예전엔 where/pose_action 이 리터럴로 박혀 있어 다른 도메인에서 KeyError 로 죽었다.

읽기 전용. 문제를 출력만 하고 파일은 건드리지 않는다.

사용:
    python validate.py               # master 전체
    python validate.py --min 100     # 카테고리 최소 수량 기준 변경
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="데이터셋 검증")
    ap.add_argument("--min", type=int, default=100, help="카테고리별 최소 권장 수량")
    args = ap.parse_args()

    taxonomy = common.load_taxonomy()
    valid = common.all_taxonomy_codes(taxonomy)
    rows = common.read_rows(common.MASTER_META)
    if not rows:
        print("master_metadata.csv 가 비어 있습니다.")
        return 1

    problems = 0

    # 1) 라벨 코드 무결성 — 축은 taxonomy 가 정한다.
    # (예전엔 ("where","pose_action","shot_size") 와 "camera_style" 이 리터럴로 박혀 있어
    #  다른 도메인에서 KeyError 로 죽었다. 픽스처가 0행이던 시절엔 이 줄에 도달조차 못 해
    #  아무도 몰랐다 — 빈 테스트는 통과하는 게 아니라 아무것도 안 보는 것이다.)
    print("== 1. 라벨 코드 무결성 ==")
    bad = 0
    for r in rows:
        for axis in common.LABEL_AXES:
            for code in common.split_codes(axis, r.get(axis)):
                if code not in valid.get(axis, {}):
                    print(f"  [{r['image_id']}] {axis}='{code}' 미정의 코드")
                    bad += 1
    print(f"  미정의 코드 {bad}건" + ("" if bad == 0 else "  <- taxonomy.yaml 확인"))
    problems += bad

    # 2) 파일 실재
    print("\n== 2. 파일 존재 여부 ==")
    missing = sum(1 for r in rows if not (common.image_path(r)).exists())
    print(f"  경로 있으나 파일 없음: {missing}건")
    problems += missing

    # 3) 상태 분포
    print("\n== 3. status 분포 ==")
    for st, c in Counter(r.get("status", "") for r in rows).most_common():
        print(f"  {st or '(빈값)'}: {c}장")

    approved = [r for r in rows if r.get("status") == "approved"]
    print(f"  approved 총 {len(approved)}장")

    # 주축/부축 — taxonomy._semantic.pair 가 정한다. 없으면 구도축 앞 두 개.
    sem = common.load_semantic()
    comp = [a for a in common.LABEL_AXES if a in common.COMP_AXES]
    AXNAME = {k: n for k, n, _ in common.AXES}
    pair = sem.get("pair") or {}
    AX = pair.get("x") if pair.get("x") in valid else (comp[0] if comp else None)
    AY = pair.get("y") if pair.get("y") in valid else next((a for a in comp if a != AX), None)

    # 4) 주축 카테고리별 수량
    if AX:
        name = AXNAME.get(AX, AX)
        print(f"\n== 4. {name}({AX}) 카테고리별 수량 (approved, 최소 {args.min}) ==")
        counts = Counter(c for r in approved for c in common.split_codes(AX, r.get(AX)))
        for code in taxonomy[AX]:
            n = counts.get(code, 0)
            flag = "  ⚠ 부족" if n < args.min else ""
            print(f"  {code}: {n}장{flag}")

    # 5) 조합 다양성 (주축 안에서 부축이 한쪽으로 쏠렸는가)
    if AX and AY:
        nx, ny = AXNAME.get(AX, AX), AXNAME.get(AY, AY)
        print(f"\n== 5. 조합 다양성 ({nx} 내 {ny} 쏠림) ==")
        combos = defaultdict(Counter)
        for r in approved:
            for x in common.split_codes(AX, r.get(AX)):
                for y in common.split_codes(AY, r.get(AY)):
                    combos[x][y] += 1
        for x, counter in combos.items():
            tot = sum(counter.values())
            top_y, top_n = counter.most_common(1)[0]
            ratio = top_n / tot if tot else 0
            warn = f"  ⚠ 한 {ny}에 쏠림" if ratio > 0.7 and tot >= 10 else ""
            print(f"  {x}: {tot}장, 최다 {top_y} {top_n}장({ratio:.0%}){warn}")

    # 6) split 누수 (pHash 그룹이 여러 split 에 걸침)
    print("\n== 6. split 누수 검사 ==")
    group_split = defaultdict(set)
    for split_name in ("train", "val", "test"):
        for r in common.read_rows(common.SPLITS / f"{split_name}.csv"):
            iid = r["image_id"]
            master = next((m for m in rows if m["image_id"] == iid), None)
            g = (master.get("phash") if master else "") or iid
            group_split[g].add(split_name)
    leaks = {g: s for g, s in group_split.items() if len(s) > 1}
    if not group_split:
        print("  split 파일이 아직 없습니다 (make_splits.py 미실행).")
    elif leaks:
        print(f"  ⚠ 누수 {len(leaks)}건 (같은 그룹이 여러 split 에):")
        for g, s in list(leaks.items())[:10]:
            print(f"    {g[:16]}...: {sorted(s)}")
        problems += len(leaks)
    else:
        print("  누수 없음 ✓")

    print(f"\n총 문제 {problems}건")
    return 0 if problems == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
