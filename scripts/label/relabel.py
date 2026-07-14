"""재라벨 — taxonomy 가 바뀐 뒤 기존 이미지를 다시 자동라벨한다.

왜 필요한가:
    auto_label.py 는 '이미 master 에 있는 image_id 는 스킵'한다(재개용). 그래서 taxonomy 에
    코드를 추가해도 기존 이미지에는 **영원히 붙지 않는다.** 실제로 이 데이터셋에서
    P10_standing·S14_front_view 등 8개 코드가 자동라벨 0건이었고, 사람이 345회를
    맨손으로 붙이고 있었다. taxonomy 는 반드시 진화하므로 이 도구가 없으면 안 된다.
    (check/axis_audit.py 가 'never_predicted' 로 이 상태를 잡아낸다)

안전 원칙:
    · auto_labels.csv  — 항상 갱신. 모델의 '최신 의견'이며 축 감사의 채점 대상이다.
    · master_metadata.csv — **사람이 검수하지 않은 행만** 갱신. 사람 라벨은 절대 덮지 않는다.

사용:
    python scripts/label/relabel.py --dry-run     # 뭐가 바뀌는지만
    python scripts/label/relabel.py               # 실제 반영
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS / "label"))

import common  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="taxonomy 변경 후 기존 이미지 재라벨")
    ap.add_argument("--dry-run", action="store_true", help="바뀌는 것만 보고 쓰지 않음")
    ap.add_argument("--limit", type=int, help="처리할 최대 장수 (테스트용)")
    ap.add_argument("--status", default="approved", help="대상 status (기본 approved)")
    # 축별로 나눠 반영해야 한다. 라벨러를 고치면 어떤 축은 좋아지고 어떤 축은 나빠진다
    # (실측: camera_style +7.7%p / pose_action -6.3%p). check/labeler_score.py 로
    # 개선이 확인된 축만 넘겨라. 전 축을 한꺼번에 덮으면 좋아진 축이 나빠진 축에 묻힌다.
    ap.add_argument("--axes", help="반영할 축만 콤마로 (예: camera_style). 생략하면 전 축")
    args = ap.parse_args()

    from clip_label import ClipLabeler

    rows = common.read_rows(common.MASTER_META)
    targets = [r for r in rows if r.get("status") == args.status
               and (common.image_path(r)).exists()]
    if args.limit:
        targets = targets[:args.limit]
    if not targets:
        print(f"대상 없음 (status={args.status})")
        return 1

    print(f"재라벨 대상 {len(targets)}장 — CLIP 로드 중…")
    labeler = ClipLabeler()

    auto = {r["image_id"]: r for r in common.read_rows(common.AUTO_LABELS)}
    by_id = {r["image_id"]: r for r in rows}
    axes = [k for k, _, _ in common.AXES]
    if args.axes:
        want = {a.strip() for a in args.axes.split(",") if a.strip()}
        bad = want - set(axes)
        if bad:
            print(f"알 수 없는 축: {sorted(bad)}")
            return 1
        axes = [a for a in axes if a in want]
        print(f"반영 축 한정: {axes}")

    n_new = Counter()          # 이번에 처음 붙은 코드
    n_master = n_skip_human = 0
    for i, r in enumerate(targets):
        out = labeler.label(common.image_path(r))
        if not out:
            continue
        iid = r["image_id"]
        # 기존 자동라벨 행에서 출발한다. --axes 로 일부 축만 갱신할 때 나머지 축이
        # 빈칸으로 날아가면 안 된다(라벨러가 안 다루는 gender/person_count 도 마찬가지).
        rec = dict(auto.get(iid) or {})
        rec.update({"image_id": iid, "labeled_at": common.today(),
                    "quality_score": out.get("quality_score", rec.get("quality_score", "")),
                    "status": r.get("status", "")})
        for ax in axes:
            v = out.get(ax)
            if v is None:
                continue                     # 라벨러가 안 다루는 축 — 기존값 유지
            new = ";".join(v) if isinstance(v, list) else str(v)
            prev = set(common.split_codes(ax, rec.get(ax)))
            for c in set(common.split_codes(ax, new)) - prev:
                n_new[c] += 1
            rec[ax] = new
        auto[iid] = rec

        # master: 사람이 손댄 행은 절대 안 건드린다
        if (r.get("reviewer") or "").strip():
            n_skip_human += 1
        else:
            hit = False
            for ax in axes:
                if rec.get(ax) and by_id[iid].get(ax) != rec[ax]:
                    by_id[iid][ax] = rec[ax]
                    hit = True
            n_master += hit
        if (i + 1) % 200 == 0:
            print(f"  …{i+1}/{len(targets)}", flush=True)

    print(f"\n■ 자동라벨(auto_labels.csv) 갱신: {len(targets)}장")
    if n_new:
        print("  이번에 새로 붙은 코드:")
        for c, n in n_new.most_common(12):
            print(f"    {c:26s} {n:5d}회")
    print(f"\n■ master 갱신 대상: {n_master}행  (사람 검수 {n_skip_human}행은 보존)")

    if args.dry_run:
        print("\n(--dry-run — 쓰지 않음)")
        return 0

    common.write_rows(common.AUTO_LABELS, common.AUTO_LABEL_FIELDS, list(auto.values()))
    common.backup_csv(common.MASTER_META)
    common.write_rows(common.MASTER_META, common.MASTER_FIELDS, rows)
    print("\n✅ auto_labels.csv · master_metadata.csv 반영")
    print("   다음: python scripts/check/axis_audit.py  (never_predicted 가 사라졌는지 확인)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
