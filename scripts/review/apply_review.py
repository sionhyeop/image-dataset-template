"""검수 결과 반영.

검수 갤러리에서 내보낸 review_decisions.csv 를 master_metadata.csv 에 병합한다.
여러 검수자의 CSV 를 순서대로 여러 개 넘길 수 있다(나중 파일이 우선).

반영 필드: where, pose_action, shot_size, camera_style, gender, person_count,
           expression, reviewed, reviewer, reviewed_at

사용:
    python apply_review.py review_decisions.csv
    python apply_review.py 검수_A.csv 검수_B.csv       # 여러 명 취합
    python apply_review.py review_decisions.csv --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402

LABEL_FIELDS = common.LABEL_AXES          # 축 정의 SSOT (common.py)
MULTI_FIELDS = common.MULTI_AXES
# 통합 앱 export: decision(keep|discard), status(approved|discarded), reviewer, reviewed_at


def main() -> int:
    ap = argparse.ArgumentParser(description="검수 결과를 master_metadata 에 반영")
    ap.add_argument("csvs", nargs="+", help="review_decisions.csv (여러 개 가능)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    master = common.read_rows(common.MASTER_META)
    if not master:
        print("master_metadata.csv 가 비어 있습니다.")
        return 1
    index = {r["image_id"]: r for r in master}

    valid = common.all_taxonomy_codes(common.load_taxonomy())
    updated, changed_labels, marked, skipped, bad = 0, 0, 0, 0, 0

    for csv_path in args.csvs:
        p = Path(csv_path)
        if not p.exists():
            print(f"파일 없음: {csv_path}")
            continue
        for row in common.read_rows(p):
            iid = row.get("image_id", "")
            tgt = index.get(iid)
            if not tgt:
                skipped += 1
                continue
            touched = False
            for f in LABEL_FIELDS:
                if f not in row:
                    continue
                val = (row.get(f) or "").strip()
                # 코드 유효성 검사 (빈 값은 허용 = 라벨 제거).
                # 무효 코드는 그것만 버리고 유효분은 반영(전체 스킵하면 멀티라벨이 통째로 유실).
                if val:
                    codes = val.split(";") if f in MULTI_FIELDS else [val]
                    good = [c for c in codes if c and c in valid[f]]
                    dropped = [c for c in codes if c and c not in valid[f]]
                    if dropped:
                        bad += len(dropped)
                        print(f"  [경고] {iid} {f}: taxonomy 에 없는 코드 무시 → {';'.join(dropped)}")
                    if not good:
                        continue   # 유효 코드가 하나도 없으면 이 필드는 건드리지 않음
                    val = ";".join(good)
                if val != (tgt.get(f) or ""):
                    if not args.dry_run:
                        tgt[f] = val
                    changed_labels += 1
                    touched = True
            # 검수 메타: decision(keep/discard) → reviewed + status
            decision = (row.get("decision") or "").strip()
            reviewed_flag = decision or (row.get("reviewed") or "").strip()
            if reviewed_flag:
                new_status = (row.get("status") or "").strip()
                if not new_status:
                    new_status = "discarded" if decision == "discard" else "approved"
                for f, v in (("reviewed", "true"), ("reviewer", row.get("reviewer", "")),
                             ("reviewed_at", row.get("reviewed_at", "")), ("status", new_status)):
                    if v != (tgt.get(f) or ""):
                        if not args.dry_run:
                            tgt[f] = v
                        touched = True
                marked += 1
            if touched:
                updated += 1

    if not args.dry_run:
        bak = common.backup_csv(common.MASTER_META)
        if bak:
            try:
                bak = common.rel(bak)
            except ValueError:   # 백업 위치가 ROOT 밖(테스트 등)이면 절대경로 그대로
                pass
            print(f"백업: {bak}")
        common.write_rows(common.MASTER_META, common.MASTER_FIELDS, master)

    tag = "[dry-run] " if args.dry_run else ""
    print(f"{tag}반영: {updated}장 갱신 | 라벨 변경 {changed_labels}건 | 검수완료 표시 {marked}장")
    if skipped:
        print(f"  master 에 없는 image_id {skipped}건 무시")
    if bad:
        print(f"  taxonomy 에 없는 코드 {bad}건 무시")
    if not args.dry_run:
        print("다음: build_index → app.py 재생성으로 반영 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
