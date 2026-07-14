#!/usr/bin/env bash
# 검수 동기화 원클릭: Firestore → master CSV 반영 → 인덱스/앱 재생성 → 검증 → 배포
#
# 사용:
#   bash scripts/review/sync_reviews.sh            # dry-run 확인 후 y 입력으로 진행
#   bash scripts/review/sync_reviews.sh --yes      # 확인 생략 (자동화용)
#   bash scripts/review/sync_reviews.sh --no-deploy  # 배포 제외 (로컬 재생성까지만)
set -euo pipefail
cd "$(dirname "$0")/../.."   # → 저장소 루트
PY=.venv/bin/python

# 경로는 전부 common.py 에서 받는다 (DATASET_DIR 로 데이터셋을 바꿔도 그대로 따라온다)
DEC="$($PY -c 'import sys;sys.path.insert(0,"scripts");import common;print(common.ANNOTATIONS/"review_decisions.csv")')"

YES=0; DEPLOY=1
for a in "$@"; do
  case "$a" in
    --yes) YES=1 ;;
    --no-deploy) DEPLOY=0 ;;
    *) echo "알 수 없는 옵션: $a"; exit 2 ;;
  esac
done

echo "== 1/6 Firestore 백업 + review_decisions.csv 생성 =="
$PY scripts/review/pull_reviews.py

echo "== 2/6 dry-run =="
$PY scripts/review/apply_review.py "$DEC" --dry-run
if [ "$YES" != "1" ]; then
  read -r -p "위 내용대로 master 에 반영할까요? [y/N] " ans
  [ "${ans,,}" = "y" ] || { echo "중단"; exit 0; }
fi

echo "== 3/6 반영 (자동 백업 포함) =="
$PY scripts/review/apply_review.py "$DEC"

echo "== 4/6 인덱스·산출물 재생성 =="
$PY scripts/index/build_index.py
$PY scripts/index/embed_map.py
$PY scripts/index/pose_match.py
$PY scripts/review/app.py

echo "== 5/6 정합성 검사 =="
$PY scripts/check/check_consistency.py

if [ "$DEPLOY" = "1" ]; then
  echo "== 6/6 배포 =="
  bash scripts/review/deploy.sh
else
  echo "== 6/6 배포 생략 (--no-deploy) =="
fi
