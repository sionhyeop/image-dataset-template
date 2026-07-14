// 포즈캠 가중치 튜너 — **실제 파이프라인을 통과시켜** 최적화한다.
//
// 왜 오프라인 최적화로는 안 되나:
//   scripts/check/signal_audit.py 는 임베딩끼리의 거리로 가중치를 맞춘다. 그런데 camMatch 는
//   (1) 방향·프레이밍·장소·인원 하드필터를 먼저 걸고 (2) 다른 정규화를 쓰고
//   (3) 프레이밍 페널티·장소 보너스를 더한다. 그래서 오프라인 최적값을 그대로 넣으면
//   실제로는 나빠진다(실측: 종합 62.7% → 60.8%, 프레이밍 68.4% → 65.2% 로 목표 미달).
//   **대리 목표로 최적화하지 말 것.** 벤치가 곧 목표 함수다.
//
// 이 스크립트는 camMatch 를 그대로 호출하며 가중치만 갈아끼우고, bench_posecam.js 와
// 같은 지표로 채점해 최적 벌을 찾는다. 결과를 annotations/signal_profile.json 의
// live 블록에 써두면 app.js 의 camW() 가 그걸 최우선으로 읽는다.
//
// 사용: node scripts/check/tune_camw.js [--write]
const fs = require('fs'), vm = require('vm'), path = require('path');
const ROOT = path.join(__dirname, '..', '..');
const html = fs.readFileSync(path.join(ROOT, 'data', '04_samples', 'index.html'), 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

function makeEl() {
  const el = { style: { setProperty: () => {} }, dataset: {}, innerHTML: '', textContent: '', value: '',
    classList: { add: () => {}, remove: () => {}, toggle: () => {}, contains: () => false },
    querySelector: () => makeEl(), querySelectorAll: () => [], appendChild: () => {},
    addEventListener: () => {}, getContext: () => new Proxy({}, { get: (t, p) => (p === 'canvas' ? el : () => {}) }) };
  return el;
}
Object.assign(globalThis, {
  document: { getElementById: () => makeEl(), querySelector: () => makeEl(), querySelectorAll: () => [],
    createElement: () => makeEl(), head: { appendChild: () => {} }, body: { appendChild: () => {} },
    addEventListener: () => {} },
  window: globalThis, localStorage: { getItem: () => null, setItem: () => {} },
  requestAnimationFrame: () => {}, innerWidth: 1200, innerHeight: 800,
  prompt: () => 't', confirm: () => true, atob: s => Buffer.from(s, 'base64').toString('binary'),
});
vm.runInThisContext(scripts[0]);
vm.runInThisContext(scripts[1]);

// camW() 를 갈아끼울 수 있게 훅을 심는다 (app.js 는 SIGPROF 에서 읽지만 여기선 강제 주입).

vm.runInThisContext('visInit();colInit();dinoInit();');        // 시각·색 인덱스 역양자화 (bench 와 동일)

const run = vm.runInThisContext(`(${function (W) {
  camW = () => W;                                   // 실제 camMatch 가 이 벌을 쓴다
  const KNN = (window.__APP__.EMB && window.__APP__.EMB.knn) || {};
  const q = Object.keys(PIDX.desc).filter(id => KNN[id] && _visEmb[id] && _colEmb[id] && POSES[id]);
  const step = Math.max(1, Math.floor(q.length / 200)), Q = q.filter((_, i) => i % step === 0);
  const _SI = { F01_full_body: 0, F02_half_body: 1, F03_closeup: 2 };
  const liveFr = kp => { const v = i => kp[i * 3 + 2];
    if (Math.max(v(27), v(28)) >= 0.5) return 0;
    if (Math.max(v(25), v(26), v(23), v(24)) >= 0.5) return 1; return 2; };
  let clipHit = 0, shotAg = 0, shotN = 0, shotAdj = 0, dirAg = 0, dirN = 0,
      frMAD = 0, frN = 0, whereAg = 0, whereN = 0, n = 0;
  for (const qi of Q) {
    _colLive = _colEmb[qi]; _visLive = _visEmb[qi]; _dinoLive = _dinoEmb ? _dinoEmb[qi] : null;
    _camScaleLive = scaleOf(qi); _camDir = dirOf(qi);
    _camFrame = liveFr(POSES[qi]);
    const pc = curLabels(qi).person_count; _camNum = pc ? (pc === 'N01_solo' ? 1 : 2) : null;
    const top = camMatch(PIDX.desc[qi]).top.filter(e => e.id !== qi).slice(0, 10);
    if (!top.length) continue; n++;
    const gold = new Set((KNN[qi] || []).slice(0, 40));
    clipHit += top.filter(e => gold.has(e.id)).length / top.length;
    const qL = curLabels(qi);
    const frs = top.map(e => _SI[curLabels(e.id).shot_size]).filter(x => x != null);
    if (frs.length) {
      shotN++; shotAg += frs.filter(f => f === _camFrame).length / frs.length;
      shotAdj += frs.filter(f => Math.abs(f - _camFrame) <= 1).length / frs.length;
      frN++; frMAD += frs.reduce((s, f) => s + Math.abs(f - _camFrame), 0) / frs.length;
    }
    if (_camDir) {
      const ds = top.map(e => dirOf(e.id)).filter(Boolean);
      if (ds.length) { dirN++; dirAg += ds.filter(d => d === _camDir).length / ds.length; }
    }
    if (qL.where && qL.where.length) {
      const ws = top.map(e => curLabels(e.id).where || []);
      whereN++; whereAg += ws.filter(w => w.some(c => qL.where.includes(c))).length / ws.length;
    }
  }
  const R = { n, clip: clipHit / n, shot: shotAg / (shotN || 1), shotAdj: shotAdj / (shotN || 1),
    frMAD: frMAD / (frN || 1), dir: dirAg / (dirN || 1), where: whereAg / (whereN || 1) };
  // 종합 = bench_posecam.js 의 합격선을 모두 지키면서 라벨/별자리 평균을 최대화
  R.pass = R.shot >= 0.68 && R.shotAdj >= 0.90 && R.frMAD <= 0.42 && R.dir >= 0.85 && R.where >= 0.55;
  R.score = (R.shot + R.shotAdj + R.dir + R.where + R.clip) / 5;
  return R;
}})`);

// 신호 목록은 payload 에서 자동 발견 — 새 시각 모델을 붙여도 이 파일을 안 고친다.
const KEYS = ['vis', 'col', 'pose'];
if (vm.runInThisContext('!!(typeof _dinoEmb!=="undefined" && _dinoEmb)')) KEYS.push('dino');
console.log(`신호: ${KEYS.join(', ')}`);

// 2단계 탐색: 성긴 격자로 훑고 최적 근방만 정밀. 신호가 늘면 전수 격자는 폭발한다.
const seen = new Map();
const norm = (combo) => {
  const t = combo.reduce((s, x) => s + x, 0);
  if (t <= 0) return null;
  const W = {};
  KEYS.forEach((k, i) => W[k] = combo[i] / t);
  return W;
};
const keyOf = W => KEYS.map(k => W[k].toFixed(2)).join('|');
function evalW(W) {
  if (!W) return;
  const k = keyOf(W);
  if (seen.has(k)) return;
  seen.set(k, { key: k, W, ...run(W) });
}
function product(vals, n) {                          // n중 데카르트 곱
  let acc = [[]];
  for (let i = 0; i < n; i++) acc = acc.flatMap(a => vals.map(v => [...a, v]));
  return acc;
}
const pick = () => {
  const all = [...seen.values()];
  const ok = all.filter(r => r.pass).sort((a, b) => b.score - a.score);
  return (ok[0] || all.sort((a, b) => b.score - a.score)[0]);
};

process.stdout.write('1단계 (성긴 격자)… ');
for (const c of product([0, 0.2, 0.4, 0.7, 1.0], KEYS.length)) evalW(norm(c));
let best = pick();
console.log(`${seen.size}개 · 잠정 최적 ${KEYS.map(k => `${k}=${best.W[k].toFixed(2)}`).join(' ')}`);

process.stdout.write('2단계 (최적 근방 정밀)… ');
const around = v => [...new Set([0, v - 0.10, v - 0.05, v, v + 0.05, v + 0.10].filter(x => x >= 0 && x <= 1))];
const axes = KEYS.map(k => around(best.W[k]));
let combos = [[]];
for (const a of axes) combos = combos.flatMap(c => a.map(v => [...c, v]));
for (const c of combos) evalW(norm(c));
const results = [...seen.values()];
console.log(`총 ${results.length}개 조합 평가`);

const HAND = { vis: 0.66, col: 0.22, pose: 0.12, dino: 0 };   // 현재(Phase 1) 값과 dino 미사용
const hand = { W: Object.fromEntries(KEYS.map(k => [k, HAND[k] || 0])), ...run(HAND) };
const passing = results.filter(r => r.pass).sort((a, b) => b.score - a.score);
best = pick();

const fmt = r => KEYS.map(k => `${k}=${(r.W[k] || 0).toFixed(2)}`).join(' ');
const row = (label, r) => `  ${label.padEnd(16)}${(r.score * 100).toFixed(1).padStart(6)}%` +
  `  프레이밍 ${(r.shot * 100).toFixed(1)}%  방향 ${(r.dir * 100).toFixed(1)}%` +
  `  장소 ${(r.where * 100).toFixed(1)}%  별자리 ${(r.clip * 100).toFixed(1)}%` +
  `  ${r.pass ? '✓' : '✗'}  ${fmt(r)}`;

console.log(`\n포즈캠 가중치 튜닝 — 실제 camMatch 를 통과시켜 채점 (쿼리 ${hand.n}장)`);
console.log(`조합 ${results.length}개 탐색 · 합격선 통과 ${passing.length}개\n`);
console.log(`  ${''.padEnd(16)}${'종합'.padStart(5)}`);
console.log(row('손으로 찍음', hand));
console.log(row('측정된 최적', best));
console.log('\n  상위 5개:');
passing.slice(0, 5).forEach((r, i) => console.log(row(`  ${i + 1}위`, r)));

if (process.argv.includes('--write')) {
  const p = path.join(ROOT, 'annotations', 'signal_profile.json');
  const prof = fs.existsSync(p) ? JSON.parse(fs.readFileSync(p, 'utf8')) : { schema: 'signal-profile-0.2' };
  const nameOf = { vis: 'visual', col: 'color', pose: 'pose', dino: 'dino' };
  const packW = W => Object.fromEntries(KEYS.map(k => [nameOf[k], +(W[k] || 0).toFixed(3)]));
  prof.live = {
    note: '실제 camMatch 를 통과시켜 벤치 지표로 최적화한 값. 오프라인 가중치는 쓰지 않는다.',
    weights: packW(best.W),
    score: +best.score.toFixed(4), pass: best.pass, n: best.n,
    metrics: { shot: +best.shot.toFixed(4), dir: +best.dir.toFixed(4),
               where: +best.where.toFixed(4), clip: +best.clip.toFixed(4) },
    baseline: { weights: packW(hand.W), score: +hand.score.toFixed(4), pass: hand.pass },
  };
  fs.writeFileSync(p, JSON.stringify(prof, null, 2), 'utf8');
  console.log(`\n저장: annotations/signal_profile.json (live 블록)`);
  console.log('   다음: python scripts/review/app.py && node tests/js/bench_posecam.js');
}
