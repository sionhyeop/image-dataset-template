#!/usr/bin/env bash
# 배포 — 생성된 앱을 데이터셋의 사이트 폴더로 복사하고 Vercel 에 올린다.
#
# 사이트 폴더를 스크립트에 박아두지 않는다. 데이터셋이 둘이 되는 순간
# "review_site" 같은 상수는 반드시 남의 데이터셋을 덮어쓴다(음식 도메인에서 실제로 겪음).
# sites/<데이터셋>/ 안에 .vercel(프로젝트 링크)이 있으므로 vercel 이 알아서 올바른 프로젝트로 간다.
#
# 사용:
#   bash scripts/review/deploy.sh                       # 기본 데이터셋
#   DATASET_DIR=datasets/food bash scripts/review/deploy.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=.venv/bin/python

# 경로에 공백이 있다(이 저장소 경로부터가 그렇다). 한 줄에 하나씩 받아야 안전하다.
{ read -r SITE; read -r SAMPLES; read -r DS; } < <(
  $PY -c 'import sys;sys.path.insert(0,"scripts");import common
print(common.SITE); print(common.SAMPLES); print(common.DATASET)')

APP="$SAMPLES/index.html"
[ -f "$APP" ] || { echo "앱이 없습니다: $APP  — 먼저 python scripts/review/app.py"; exit 1; }
[ -d "$SITE" ] || {
  echo "사이트 폴더가 없습니다: $SITE"
  echo "새 데이터셋의 배포 루트를 만들려면 (기존 것을 베끼면 헤더 설정이 딸려온다):"
  echo "  mkdir -p '$SITE' && cp sites/composition/vercel.json sites/composition/.gitignore '$SITE'/"
  echo "  cd '$SITE' && vercel link      # 새 Vercel 프로젝트에 연결"
  exit 1
}

cp "$APP" "$SITE/index.html"
[ -f "$SAMPLES/lora_story.html" ] && cp "$SAMPLES/lora_story.html" "$SITE/lora_story.html"

echo "== 배포: $DS → $SITE =="
vercel deploy "$SITE" --prod --yes
echo "완료 — 팀에 하드 새로고침(Ctrl+Shift+R) 안내하세요."
