"""Firestore `reviews` 컬렉션 백업 (읽기 전용).

수동으로 하던 REST 내보내기를 자동화한다. 기존 백업 파일과 동일한 형식
(Firestore REST 문서 원문의 리스트)으로 `annotations/firebase_backup/reviews_raw_YYYYMMDD.json`
에 저장한다. 같은 날짜 재실행 시 덮어쓴다(원자적 쓰기).

사용:
    python scripts/review/backup_reviews.py

설정: config/firebase.json 의 projectId·apiKey 사용 (open 규칙이라 GET 가능).
"""
from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import common  # noqa: E402

FIREBASE_CONFIG = common.ROOT / "config" / "firebase.json"
BACKUP_DIR = common.ANNOTATIONS / "firebase_backup"
PAGE_SIZE = 300


def load_firebase_config() -> dict:
    if not FIREBASE_CONFIG.exists():
        raise SystemExit(f"설정 없음: {FIREBASE_CONFIG} (firebase.example.json 참고)")
    return json.loads(FIREBASE_CONFIG.read_text(encoding="utf-8"))


def fetch_all_reviews(cfg: dict) -> list[dict]:
    """reviews 컬렉션 전체를 페이지네이션으로 GET. 문서 원문 리스트 반환."""
    base = (f"https://firestore.googleapis.com/v1/projects/{cfg['projectId']}"
            f"/databases/(default)/documents/reviews")
    docs, token = [], None
    while True:
        q = {"pageSize": str(PAGE_SIZE), "key": cfg["apiKey"]}
        if token:
            q["pageToken"] = token
        with urllib.request.urlopen(f"{base}?{urllib.parse.urlencode(q)}", timeout=30) as r:
            page = json.loads(r.read().decode("utf-8"))
        docs.extend(page.get("documents", []))
        token = page.get("nextPageToken")
        if not token:
            return docs


def _decode_value(v: dict):
    """Firestore 타입 래핑 값 → 파이썬 값."""
    if "stringValue" in v:
        return v["stringValue"]
    if "arrayValue" in v:
        return [_decode_value(x) for x in v["arrayValue"].get("values", [])]
    if "mapValue" in v:
        return {k: _decode_value(x) for k, x in v["mapValue"].get("fields", {}).items()}
    for k in ("integerValue", "doubleValue", "booleanValue", "timestampValue", "nullValue"):
        if k in v:
            return v[k]
    return None


def decode_doc(doc: dict) -> dict:
    """문서 원문 → {image_id, labels, decision, reviewer, at}. pull_reviews가 재사용."""
    fields = {k: _decode_value(v) for k, v in doc.get("fields", {}).items()}
    return {
        "image_id": doc["name"].rsplit("/", 1)[-1],
        "labels": fields.get("labels") or {},
        "decision": fields.get("decision") or "",
        "reviewer": fields.get("reviewer") or "",
        "at": fields.get("at") or "",
    }


def main() -> int:
    cfg = load_firebase_config()
    docs = fetch_all_reviews(cfg)
    out = BACKUP_DIR / f"reviews_raw_{date.today():%Y%m%d}.json"
    common.write_json_atomic(out, json.dumps(docs, ensure_ascii=False))

    from collections import Counter
    decoded = [decode_doc(d) for d in docs]
    dec = Counter(r["decision"] or "(없음)" for r in decoded)
    rev = Counter(r["reviewer"] or "(익명)" for r in decoded)
    print(f"백업: {len(docs)}건 → {common.rel(out)}")
    print(f"  결정: {dict(dec)} · 검수자: {dict(rev)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
