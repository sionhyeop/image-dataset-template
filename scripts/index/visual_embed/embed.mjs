// 데이터셋 시각 임베딩 — 브라우저 포즈캠과 '동일한' tfjs MobileNet v1 1.0 224 를 Node(wasm)에서 실행
// (같은 패키지·같은 가중치 = 같은 임베딩 공간 → 라이브 카메라와 직접 비교 가능)
// 사용: node embed.mjs /tmp/visual_ids.json ../../annotations/visual_embed_raw.json
import fs from 'fs';
import * as tf from '@tensorflow/tfjs';
import { setWasmPaths } from '@tensorflow/tfjs-backend-wasm';
import '@tensorflow/tfjs-backend-wasm';
import * as mobilenet from '@tensorflow-models/mobilenet';
import jpeg from 'jpeg-js';

const [,, idsPath, outPath] = process.argv;
setWasmPaths('./node_modules/@tensorflow/tfjs-backend-wasm/dist/');
await tf.setBackend('wasm'); await tf.ready();
console.log('backend:', tf.getBackend());

const items = JSON.parse(fs.readFileSync(idsPath, 'utf8'));
const model = await mobilenet.load({ version: 1, alpha: 1.0 });   // 브라우저와 반드시 동일 설정
console.log('model loaded · items:', items.length);

const out = {};
let n = 0; const t0 = Date.now();
for (const { id, path } of items) {
  try {
    const raw = jpeg.decode(fs.readFileSync(path), { useTArray: true, formatAsRGBA: false });
    const emb = tf.tidy(() => {
      const t = tf.tensor3d(raw.data, [raw.height, raw.width, 3], 'int32');
      return model.infer(t, true);                                 // (1,1024) 임베딩
    });
    const v = Array.from(await emb.data());
    emb.dispose();
    // L2 정규화 후 저장(코사인 = 내적)
    const norm = Math.hypot(...v) || 1;
    out[id] = v.map(x => +(x / norm).toFixed(6));
  } catch (e) { console.error('skip', id, e.message); }
  if (++n % 100 === 0) console.log(`${n}/${items.length} · ${((Date.now()-t0)/1000).toFixed(0)}s`);
}
fs.writeFileSync(outPath, JSON.stringify(out));
console.log('저장:', outPath, '·', Object.keys(out).length, '개 ·', ((Date.now()-t0)/1000).toFixed(0)+'s');
