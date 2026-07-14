// 데이터셋 DINOv2 임베딩 — 브라우저 포즈캠과 **동일한 런타임·동일한 전처리**를 Node 에서 실행.
//
// 왜 Python(transformers) 이 아니라 Node(transformers.js) 인가:
//   갤러리 임베딩과 라이브 카메라 임베딩이 **같은 공간**에 있어야 거리를 비교할 수 있다.
//   전처리(리사이즈 보간·크롭·정규화)가 조금만 달라도 벡터가 어긋나 추천이 무너진다.
//   그래서 브라우저가 쓸 바로 그 라이브러리·그 모델·그 프로세서를 Node 에서 그대로 돌린다.
//   (MobileNet 도 같은 이유로 tfjs 를 Node 에서 돌렸다 — visual_embed/embed.mjs)
//
// 왜 DINOv2 인가: MobileNet 은 ImageNet 분류용 특징이라 색·질감엔 강하지만 배치·구도엔 둔감하다.
//   실측(CLIP ViT-H 이웃 재현율): MobileNet 39.6% vs DINOv2-small 48.2% (+8.6%p)
//
// 사용: node embed.mjs /tmp/dino_ids.json ../../annotations/dino_embed_raw.json
import fs from 'fs';
import { AutoModel, AutoProcessor, RawImage } from '@huggingface/transformers';

// dtype 은 **브라우저가 쓸 것과 같아야 한다.** 양자화 오차도 똑같이 겪어야 갤러리 벡터와
// 라이브 벡터가 같은 공간에 남는다. (fp32 인덱스 + q4f16 브라우저 = 코사인 0.86 → 붕괴)
// 사용: node embed.mjs ids.json out.json [dtype] [size]
const [, , idsPath, outPath, DTYPE = 'fp16', SIZE = ''] = process.argv;
const MODEL = 'onnx-community/dinov2-small';   // 브라우저에서 로드할 것과 동일한 레포

const items = JSON.parse(fs.readFileSync(idsPath, 'utf8'));
console.log(`모델 로드: ${MODEL} · dtype ${DTYPE}${SIZE ? ` · 입력 ${SIZE}px` : ''} · 대상 ${items.length}장`);

const processor = await AutoProcessor.from_pretrained(MODEL);
if (SIZE) {                       // 입력 해상도를 줄이면 패치 수가 줄어 추론이 빨라진다
  const s = +SIZE;                // (DINOv2 는 patch14 라 14의 배수가 안전)
  processor.image_processor.size = { shortest_edge: s };
  processor.image_processor.crop_size = { height: s, width: s };
}
const model = await AutoModel.from_pretrained(MODEL, { dtype: DTYPE });

const out = {};
let n = 0;
const t0 = Date.now();
for (const { id, path } of items) {
  try {
    const image = await RawImage.read(path);
    const inputs = await processor(image);
    const { last_hidden_state } = await model(inputs);
    // CLS 토큰(첫 번째) = 이미지 전역 표현
    const [, , D] = last_hidden_state.dims;
    const data = last_hidden_state.data;
    const v = Array.from({ length: D }, (_, i) => data[i]);
    let norm = 0;
    for (const x of v) norm += x * x;
    norm = Math.sqrt(norm) || 1;
    out[id] = v.map(x => +(x / norm).toFixed(6));   // L2 정규화 (코사인 = 내적)
  } catch (e) {
    console.error('skip', id, e.message);
  }
  if (++n % 100 === 0) {
    const el = (Date.now() - t0) / 1000;
    console.log(`${n}/${items.length} · ${el.toFixed(0)}s · ${(el / n * 1000).toFixed(0)}ms/장`);
  }
}
fs.writeFileSync(outPath, JSON.stringify(out));
console.log(`저장: ${outPath} · ${Object.keys(out).length}개 · ${((Date.now() - t0) / 1000).toFixed(0)}s`);
