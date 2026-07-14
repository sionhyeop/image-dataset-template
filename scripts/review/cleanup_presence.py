"""Firestore `presence` 좀비 세션 문서 정리.

접속자 문서는 beforeunload 에서만 삭제되므로 탭 강제종료·모바일 등에서 잔존한다.
ts 가 오래된 문서만 지운다 — 활성 세션은 20초마다 하트비트로 자기 문서를 다시
쓰므로(set) 실수로 지워져도 즉시 복구된다.

사용:
    python scripts/review/cleanup_presence.py             # 10분 초과 좀비 삭제
    python scripts/review/cleanup_presence.py --dry-run   # 대상만 출력
    python scripts/review/cleanup_presence.py --minutes 60
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backup_reviews  # noqa: E402  (config 로더 재사용)


def main() -> int:
    ap = argparse.ArgumentParser(description="presence 좀비 문서 정리")
    ap.add_argument("--minutes", type=float, default=10, help="이보다 오래된 문서 삭제 (기본 10분)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = backup_reviews.load_firebase_config()
    base = (f"https://firestore.googleapis.com/v1/projects/{cfg['projectId']}"
            f"/databases/(default)/documents/presence")
    with urllib.request.urlopen(f"{base}?pageSize=300&key={cfg['apiKey']}", timeout=30) as r:
        docs = json.loads(r.read()).get("documents", [])

    now_ms = time.time() * 1000
    cutoff = args.minutes * 60_000
    kept, removed = 0, 0
    for d in docs:
        f = d.get("fields", {})
        nm = f.get("reviewer", {}).get("stringValue", "?")
        ts = float(f.get("ts", {}).get("integerValue",
                   f.get("ts", {}).get("doubleValue", 0)) or 0)
        age_min = (now_ms - ts) / 60_000
        if age_min <= args.minutes:
            kept += 1
            continue
        tag = "[dry] " if args.dry_run else ""
        print(f"  {tag}삭제: {nm!r} ({age_min/60:.1f}시간 전)")
        if not args.dry_run:
            req = urllib.request.Request(
                f"https://firestore.googleapis.com/v1/{d['name']}?{urllib.parse.urlencode({'key': cfg['apiKey']})}",
                method="DELETE")
            urllib.request.urlopen(req, timeout=30).read()
        removed += 1

    print(f"{'삭제 예정' if args.dry_run else '삭제'} {removed}건 · 활성 유지 {kept}건 (기준 {args.minutes:g}분)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
