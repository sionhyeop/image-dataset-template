#!/usr/bin/env bash
# 시각 신호 인덱스 원클릭: 이미지 목록 → Node 임베더 → PCA → <signal>_index.json
#
# 임베더가 Node 인 이유: 갤러리 임베딩과 라이브 카메라 임베딩이 **같은 공간**에 있어야 한다.
# 전처리가 조금만 달라도 벡터가 어긋나 추천이 무너지므로, 브라우저가 쓸 바로 그 런타임을 쓴다.
#
# 사용:
#   bash scripts/index/build_signal.sh dino    'dinov2-small(transformers.js)'
#   bash scripts/index/build_signal.sh visual  'mobilenet_v1_1.0_224(tfjs)'
set -euo pipefail
cd "$(dirname "$0")/../.."          # → composition_dataset
PY=.venv/bin/python

SIG="${1:?사용: build_signal.sh <signal> [모델표시명] [dtype] [입력px]}"
MODEL="${2:-$SIG}"
DTYPE="${3:-}"          # 브라우저가 그대로 따라 쓴다 (인덱스에 기록됨)
SIZE="${4:-}"
DIR="scripts/index/${SIG}_embed"
[ -d "$DIR" ] || { echo "임베더 없음: $DIR"; exit 1; }

# 산출 경로는 **데이터셋 루트 기준**이다. "annotations/..." 로 박아두면 DATASET_DIR 을
# 바꿔도 인물 데이터셋에 써버린다(음식 도메인을 실제로 돌려보고서야 드러난 버그).
DS_ROOT="$($PY -c 'import sys;sys.path.insert(0,"scripts");import common;print(common.DATASET_ROOT)')"
IDS="/tmp/${SIG}_ids.json"
RAW="$DS_ROOT/annotations/${SIG}_embed_raw.json"

echo "== 1/3 대상 이미지 목록 (approved) =="
$PY - "$IDS" <<'EOF'
import json, sys
sys.path.insert(0, "scripts")
import common
rows = [r for r in common.read_rows(common.MASTER_META) if r.get("status") == "approved"]
items = [{"id": r["image_id"], "path": str(common.image_path(r))}
         for r in rows if common.image_path(r).exists()]
open(sys.argv[1], "w").write(json.dumps(items))
print(f"  {len(items)}장 → {sys.argv[1]}")
EOF

echo "== 2/3 임베딩 (Node — 브라우저와 동일 런타임) =="
[ -d "$DIR/node_modules" ] || (cd "$DIR" && npm install --silent)
(cd "$DIR" && node embed.mjs "$IDS" "$RAW" ${DTYPE:+"$DTYPE"} ${SIZE:+"$SIZE"})

echo "== 3/3 PCA → int8 인덱스 =="
PCA_ARGS=(--signal "$SIG" --model "$MODEL")
[ -n "$DTYPE" ] && PCA_ARGS+=(--dtype "$DTYPE")
[ -n "$SIZE" ] && PCA_ARGS+=(--input-size "$SIZE")
$PY scripts/index/embed_pca.py "${PCA_ARGS[@]}"
echo "완료 — 다음: python scripts/review/app.py"
