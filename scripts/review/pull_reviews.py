"""Firestore `reviews` → review_decisions.csv 변환 (읽기 전용).

앱의 '내보내기' 버튼을 브라우저 없이 대신한다: Firestore 를 REST 로 읽어
apply_review.py 가 받는 형식 그대로 annotations/review_decisions.csv 를 만든다.
실행 전 backup_reviews 로 원문 백업을 먼저 남긴다.

사용:
    python scripts/review/pull_reviews.py            # 백업 + CSV 생성
    python scripts/review/pull_reviews.py --no-backup

다음 단계:
    python scripts/review/apply_review.py annotations/review_decisions.csv --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import common  # noqa: E402
import backup_reviews  # noqa: E402

OUT_CSV = common.ANNOTATIONS / "review_decisions.csv"
# 앱 export 버튼과 동일한 컬럼. **taxonomy 에서 파생한다.**
# 예전엔 인물 축 7개가 리터럴로 박혀 있어, 다른 도메인의 검수 결과가 여기서 조용히 사라졌다
# — 팀이 라벨을 붙였는데 CSV 에 안 들어오는, 가장 알아채기 어려운 종류의 버그다.
AXES = list(common.LABEL_AXES)
MULTI = set(common.MULTI_AXES)
FIELDS = ["image_id"] + AXES + ["decision", "status", "reviewer", "reviewed_at"]


def to_row(r: dict) -> dict | None:
    """decode_doc 결과 → CSV 행. decision 없는 문서는 앱 export 처럼 제외."""
    if not r["decision"]:
        return None
    row = {"image_id": r["image_id"], "decision": r["decision"],
           "status": "discarded" if r["decision"] == "discard" else "approved",
           "reviewer": r["reviewer"], "reviewed_at": r["at"]}
    labels = r["labels"]
    for ax in AXES:
        v = labels.get(ax, "")
        if isinstance(v, list):
            v = ";".join(str(x) for x in v)
        elif v is None:
            v = ""
        row[ax] = str(v)
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Firestore reviews → review_decisions.csv")
    ap.add_argument("--no-backup", action="store_true", help="원문 백업 생략")
    args = ap.parse_args()

    cfg = backup_reviews.load_firebase_config()
    if not args.no_backup:
        backup_reviews.main()
    docs = backup_reviews.fetch_all_reviews(cfg)
    decoded = [backup_reviews.decode_doc(d) for d in docs]
    rows = [r for r in (to_row(d) for d in decoded) if r]
    dropped = len(decoded) - len(rows)

    common.write_rows(OUT_CSV, FIELDS, rows)
    print(f"변환: {len(rows)}건 → {common.rel(OUT_CSV)}"
          + (f" (decision 없는 {dropped}건 제외)" if dropped else ""))
    print("다음: python scripts/review/apply_review.py annotations/review_decisions.csv --dry-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
