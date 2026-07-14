// 앱 JS 런타임 검증 (node DOM 스텁 + vm) — 대시보드/조합 실시간 집계 16케이스
// 사용: node tests/js/verify_app.js [index.html 경로]
const fs = require('fs');
const vm = require('vm');

const path = require('path');
// 앱 경로는 데이터셋을 따라간다 (python 의 common.DATASET_DIR 과 같은 규칙).
const _DS = process.env.DATASET_DIR || 'datasets/example_plants';
const _APP = path.resolve(__dirname, '..', '..', _DS, 'data', '04_samples', 'index.html');
const DEFAULT = _APP;
const html = fs.readFileSync(process.argv[2] || DEFAULT, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
if (scripts.length < 2) { console.error('script blocks not found:', scripts.length); process.exit(1); }

// ---- DOM 스텁 ----
function makeEl(id) {
  const cls = new Set();
  const el = {
    id, style: { setProperty: () => {}, removeProperty: () => {} }, dataset: {}, _handlers: {}, innerHTML: '', textContent: '', value: '', src: '',
    width: 300, height: 300, disabled: false,
    classList: {
      add: (...a) => a.forEach(c => cls.add(c)),
      remove: (...a) => a.forEach(c => cls.delete(c)),
      toggle: (c, f) => { (f === undefined ? !cls.has(c) : f) ? cls.add(c) : cls.delete(c); },
      contains: c => cls.has(c),
    },
    querySelector: () => makeEl('q'),
    querySelectorAll: () => [],
    appendChild: () => {}, removeChild: () => {}, remove: () => {},
    addEventListener: () => {}, removeEventListener: () => {},
    setAttribute: () => {}, focus: () => {}, blur: () => {}, click: () => {},
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 300, height: 300 }),
    getContext: () => new Proxy({}, { get: (t, p) => (p === 'canvas' ? el : () => {}) }),
  };
  return el;
}
const els = {};
const documentStub = {
  getElementById: id => (els[id] = els[id] || makeEl(id)),
  querySelector: () => makeEl('q'),
  querySelectorAll: () => [],
  createElement: tag => makeEl('new-' + tag),
  head: { appendChild: () => {} },
  body: { appendChild: () => {} },
  addEventListener: () => {},
};
Object.assign(globalThis, {
  document: documentStub,
  window: globalThis,
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
  requestAnimationFrame: () => {},   // countUp 애니메이션 무시(최초렌더 텍스트는 검증 대상 아님)
  innerWidth: 1200, innerHeight: 800,
  prompt: () => 'tester', alert: () => {}, confirm: () => true,
});

// ---- 앱 로드 (payload + main) — top-level let/const는 같은 컨텍스트의 전역 렉시컬 스코프 공유 ----
vm.runInThisContext(scripts[0], { filename: 'payload.js' });
vm.runInThisContext(scripts[1], { filename: 'app.js' });

const T = vm.runInThisContext(`(${function () {
  const out = { pass: [], fail: [] };
  const ok = (name, cond, detail) => (cond ? out.pass : out.fail).push(name + (detail ? ' :: ' + detail : ''));

  // ---- 1. 초기 렌더 = 정적 DASH와 일치 (edits 비어있음) ----
  const d0 = dashCounts();
  ok('init used == DASH.total', d0.used === DASH.total, d0.used + ' vs ' + DASH.total);
  let mismatch = 0;
  for (const a of AXMETA) for (const k of Object.keys(TAX[a.key])) {
    if ((d0.counts[a.key][k] || 0) !== ((DASH.counts[a.key] || {})[k] || 0)) mismatch++;
  }
  ok('init per-axis counts == DASH.counts', mismatch === 0, mismatch + ' mismatches');
  // 축 이름을 테스트가 알면 안 된다 — payload 에서 파생한다.
  // (예전엔 ['where','pose_action',...] 을 박아둬서 인물 아닌 도메인에선 TypeError 로 죽었다)
  const comp = AXMETA.filter(a => a.comp).map(a => a.key);
  const TKMIN = A.COVERAGE_MIN || 10;
  const cov0 = comp.reduce((s, a) => s + Object.keys(TAX[a]).filter(k => (d0.counts[a][k] || 0) >= TKMIN).length, 0);
  ok('init covered == DASH.covered', cov0 === DASH.covered, cov0 + ' vs ' + DASH.covered);
  const dashEl = document.getElementById('tab-dash');
  ok('renderDash ran at init', dashEl._done === true && dashEl.innerHTML.includes('충분한 코드'), '');
  ok('dash tile shows live covered', dashEl.innerHTML.includes('<b>' + cov0 + '/'), '');

  // ---- 2. 가짜 검수 주입: 라벨 이동 1건 + discard 1건 ----
  // 대상 축도 payload 에서 고른다: 코드가 2개 이상인 첫 구도축.
  // 콤보 패널이 실제로 그리는 축(_semantic.pair)을 그대로 쓴다 — 그래야 4번 검증이 성립한다.
  const AX = (typeof PAIR_X !== 'undefined' && PAIR_X) || comp.find(a => Object.keys(TAX[a]).length >= 2);
  const AY = (typeof PAIR_Y !== 'undefined' && PAIR_Y) || comp.find(a => a !== AX);
  // payload 는 멀티축을 배열로, 단일축을 **문자열**로 싣는다. 배열만 가정하면
  // 단일축이 pair.x 인 도메인(인테리어의 space)에서 조용히 빈 목록이 된다.
  const AXM = AXMETA.find(a => a.key === AX) || {};
  const asArr = v => (Array.isArray(v) ? v : (v ? [v] : []));
  const singles = IDS.filter(id => asArr(DATA[id][AX]).length === 1 && !edits[id]);
  const idMove = singles[0], idDrop = singles[1];
  const fromW = asArr(DATA[idMove][AX])[0];
  const toW = Object.keys(TAX[AX]).find(w => w !== fromW);
  const Lm = curLabels(idMove); Lm[AX] = AXM.multi ? [toW] : toW;
  edits[idMove] = { labels: Lm, decision: 'keep', reviewer: 'test', at: 'now' };
  edits[idDrop] = { labels: curLabels(idDrop), decision: 'discard', reviewer: 'test', at: 'now' };
  const dropW = asArr(edits[idDrop].labels[AX])[0];

  const d1 = dashCounts();
  ok('discard: used -1', d1.used === d0.used - 1, d1.used + ' vs ' + (d0.used - 1));
  const expFrom = (d0.counts[AX][fromW] || 0) - 1 - (dropW === fromW ? 1 : 0);
  const expTo = (d0.counts[AX][toW] || 0) + 1 - (dropW === toW ? 1 : 0);
  ok('label move: from-code -1', (d1.counts[AX][fromW] || 0) === expFrom, fromW + ' ' + d1.counts[AX][fromW] + ' vs ' + expFrom);
  ok('label move: to-code +1', (d1.counts[AX][toW] || 0) === expTo, toW + ' ' + d1.counts[AX][toW] + ' vs ' + expTo);

  // ---- 3. 더티플래그 & 재렌더 경로 ----
  const htmlBefore = dashEl.innerHTML;
  renderDash();  // dirty 아님 → 스킵
  ok('clean skip: no re-render', dashEl.innerHTML === htmlBefore, '');
  _dashDirty = true;
  renderDash();  // 재렌더 → 라이브 숫자 & textContent 직접 설정 경로
  ok('dirty re-render: html changed', dashEl.innerHTML !== htmlBefore, '');
  ok('re-render sets dtot directly', String(document.getElementById('dtot').textContent) === String(d1.used),
     document.getElementById('dtot').textContent + ' vs ' + d1.used);
  ok('re-render sets drev', String(document.getElementById('drev').textContent) === '2', document.getElementById('drev').textContent);
  ok('dirty cleared', _dashDirty === false, '');

  // ---- 4. 조합(콤보) 라이브 ----
  renderCombos(); // cx/cy 초기화 + drawCombo (기본 PAIR_X × PAIR_Y)
  const cellRe = (h, x) => { const m = h.match(new RegExp('data-x="' + x + '"[^>]*data-n="(\\d+)"', 'g')) || [];
    return m.reduce((s, c) => s + +c.match(/data-n="(\d+)"/)[1], 0); };
  const w = document.getElementById('combowrap');
  const h1 = w.innerHTML;
  const sumFrom1 = cellRe(h1, fromW), sumTo1 = cellRe(h1, toW);
  // edits 제거 상태와 비교하기 위해 임시 롤백
  const bakM = edits[idMove], bakD = edits[idDrop];
  delete edits[idMove]; delete edits[idDrop];
  drawCombo();
  const h0 = w.innerHTML;
  const sumFrom0 = cellRe(h0, fromW), sumTo0 = cellRe(h0, toW);
  edits[idMove] = bakM; edits[idDrop] = bakD;
  // idMove 의 Y축 라벨 수(멀티면 여러 개)만큼 셀 합이 이동한다
  const yN = asArr(bakM.labels[AY]).length;
  const dropYN = asArr(bakD.labels[AY]).length;
  const expFromSum = sumFrom0 - yN - (dropW === fromW ? dropYN : 0);
  const expToSum = sumTo0 + yN - (dropW === toW ? dropYN : 0);
  ok('combo live: from-col sum moved', sumFrom1 === expFromSum, sumFrom1 + ' vs ' + expFromSum);
  ok('combo live: to-col sum moved', sumTo1 === expToSum, sumTo1 + ' vs ' + expToSum);
  ok('combo rerender uses curLabels (baseline differs)', h1 !== h0, '');

  return out;
}})()`, { filename: 'test.js' });

console.log('PASS', T.pass.length);
T.pass.forEach(p => console.log('  ✓', p));
if (T.fail.length) { console.log('FAIL', T.fail.length); T.fail.forEach(f => console.log('  ✗', f)); process.exit(1); }
process.exit(0);
