// 포즈캠 검증 — JS 디스크립터(pdCompute)가 파이썬 정답(PIDX.desc)과 같은 공간인지 패리티 확인
// 사용: node tests/js/verify_posecam.js [index.html 경로]
const fs = require('fs'); const vm = require('vm'); const path = require('path');
// 앱 경로는 데이터셋을 따라간다 (python 의 common.DATASET_DIR 과 같은 규칙).
const _DS = process.env.DATASET_DIR || 'datasets/example_plants';
const _APP = path.resolve(__dirname, '..', '..', _DS, 'data', '04_samples', 'index.html');
const DEFAULT = _APP;
const html = fs.readFileSync(process.argv[2] || DEFAULT, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

function makeEl(){const cls=new Set();const el={style:{setProperty:()=>{},removeProperty:()=>{}},dataset:{},innerHTML:'',textContent:'',value:'',disabled:false,hidden:false,
  classList:{add:()=>{},remove:()=>{},toggle:()=>{},contains:()=>false},
  querySelector:()=>makeEl(),querySelectorAll:()=>[],appendChild:()=>{},addEventListener:()=>{},setAttribute:()=>{},play:()=>Promise.resolve(),
  getContext:()=>new Proxy({},{get:(t,p)=>(p==='canvas'?el:()=>{})}),getBoundingClientRect:()=>({left:0,top:0,width:300,height:300})};return el;}
const documentStub={getElementById:()=>makeEl(),querySelector:()=>makeEl(),querySelectorAll:()=>[],createElement:()=>makeEl(),head:{appendChild:()=>{}},body:{appendChild:()=>{}},addEventListener:()=>{}};
Object.assign(globalThis,{document:documentStub,window:globalThis,localStorage:{getItem:()=>null,setItem:()=>{}},requestAnimationFrame:()=>{},innerWidth:1200,innerHeight:800,prompt:()=>'t',confirm:()=>true});
vm.runInThisContext(scripts[0],{filename:'payload.js'});
vm.runInThisContext(scripts[1],{filename:'app.js'});

const R = vm.runInThisContext(`(${function(){
  const out={pass:[],fail:[]};const ok=(n,c,d)=>(c?out.pass:out.fail).push(n+(d?' :: '+d:''));
  const ids=Object.keys(PIDX.desc).filter(id=>POSES[id]).slice(0,40);
  ok('패리티 표본 확보(≥20)', ids.length>=20, ids.length+'장');
  let maxDiff=0,sumDiff=0,nDiff=0,maskSame=0,selfBad=0;
  for(const id of ids){
    const d=pdCompute(POSES[id]);                    // poses.json kp(소수3) → JS 디스크립터
    const ref=PIDX.desc[id];                         // 파이썬 정답(parquet kp 원본)
    if(!d){selfBad++;continue;}
    if(d.v===ref.v)maskSame++;
    const common=d.v&ref.v;
    for(let i=0;i<21;i++)if(common>>i&1){
      const diff=Math.abs(d.f[i]-ref.f[i]);maxDiff=Math.max(maxDiff,diff);sumDiff+=diff;nDiff++;}
    const sd=pdDist(d,ref);                          // 자기 자신과의 거리 ≈ 0 이어야
    if(sd==null||sd>0.05)selfBad++;
  }
  ok('평균 |Δ특징| < 0.01', nDiff>0 && sumDiff/nDiff<0.01, (sumDiff/nDiff).toFixed(4));
  ok('최대 |Δ특징| < 0.05', maxDiff<0.05, maxDiff.toFixed(4));
  ok('valid 마스크 동일 ≥ 90%', maskSame/ids.length>=0.9, maskSame+'/'+ids.length);
  ok('자기거리 ≤ 0.05 전원', selfBad===0, '이상 '+selfBad+'건');
  // 매칭: 저장 디스크립터를 라이브로 가장하면 자기 자신이 1위여야
  let top1=0;
  for(const id of ids.slice(0,15)){
    const m=pdMatch(PIDX.desc[id],3);
    if(m.length&&m[0][0]===id)top1++;
  }
  ok('셀프 매칭 top-1 ≥ 14/15', top1>=14, top1+'/15');
  // 미러 일관성: mirror(mirror(x)) == x
  const d0=PIDX.desc[ids[0]];const mm=pdMirror(pdMirror(d0));
  ok('mirror 멱등(2회=원본)', mm.v===d0.v && mm.f.every((x,i)=>Math.abs(x-d0.f[i])<1e-9));
  // 부분 신체(상반신만): 다리 특징을 지운 디스크립터로도 매칭이 나오고, 자기 자신이 top-3 안
  const LEGS=[2,3,6,7,12,13,14,15,20];
  let partialOk=0,partialAny=0;
  for(const id of ids.slice(0,10)){
    const src=PIDX.desc[id];let v=src.v;LEGS.forEach(i=>{v&=~(1<<i);});
    const part={f:src.f.slice(),v};
    const m=pdMatch(part,6);
    if(m.length)partialAny++;
    if(m.slice(0,3).some(x=>x[0]===id))partialOk++;
  }
  ok('상반신 매칭 결과 존재 10/10', partialAny===10, partialAny+'/10');
  ok('상반신 셀프 top-3 ≥ 8/10', partialOk>=8, partialOk+'/10');
  // --- P1 시각 인덱스: 디코딩 무결성 + 시각 셀프매칭 ---
  ok('VIDX 존재·규모', !!VIDX&&Object.keys(VIDX.emb).length>1700, VIDX?Object.keys(VIDX.emb).length+'장':'없음');
  ok('visInit 디코딩', visInit()===true);
  const vids=Object.keys(_visEmb).slice(0,30);
  let normOk=0;for(const id of vids){let nn=0;const e=_visEmb[id];for(let i=0;i<e.length;i++)nn+=e[i]*e[i];
    if(Math.abs(Math.sqrt(nn)-1)<0.05)normOk++;}
  ok('임베딩 L2노름≈1 (양자화 후) 30/30', normOk===30, normOk+'/30');
  let vSelf=0;
  for(const id of vids.slice(0,10)){_visLive=_visEmb[id];const t=visTop(3);if(t.length&&t[0][0]===id)vSelf++;}
  _visLive=null;
  ok('시각 셀프매칭 top-1 10/10', vSelf===10, vSelf+'/10');
  // v4 융합 셀프: 자기 시각+포즈를 라이브로 → 자기 자신 top-1 (필터는 열어둠)
  let fSelf=0;const fids=ids.filter(id=>_visEmb[id]).slice(0,10);
  for(const id of fids){_visLive=_visEmb[id];_camDir=null;_camFrame=null;_camNum=null;_camWhere='';
    const m=camMatch(PIDX.desc[id]);if(m.top.length&&m.top[0].id===id)fSelf++;}
  _visLive=null;
  ok('융합 셀프매칭 top-1 ≥9/10', fSelf>=9, fSelf+'/'+fids.length);
  // v4 방향 게이트: 방향 필터 시 top 내 '불일치 방향' 컷이 늘지 않음
  let dirGate=0,dirT=0;
  for(const id of ids.slice(0,10)){const vl=_visEmb[id];if(!vl)continue;_visLive=vl;_camFrame=null;
    _camDir=null;const A=camMatch(PIDX.desc[id]).top.slice(0,10);
    _camDir='back';const B=camMatch(PIDX.desc[id]).top.slice(0,10);_camDir=null;
    const mis=t=>t.filter(e2=>{const cd=dirOf(e2.id);return cd&&cd!=='back';}).length;
    if(mis(B)<=mis(A))dirGate++;dirT++;}
  _visLive=null;
  ok('방향 필터: 불일치 컷 비증가 (게이트)', dirT>0&&dirGate===dirT, dirGate+'/'+dirT);
  // v4 프레이밍 게이트: 전신(0) 라이브면 얼빡(F03) 컷 절대 미포함
  let frGate=0,frT=0;
  for(const id of ids.slice(0,10)){const vl=_visEmb[id];if(!vl)continue;_visLive=vl;_camDir=null;
    _camFrame=0;const B=camMatch(PIDX.desc[id]).top.slice(0,10);_camFrame=null;
    if(B.filter(e2=>curLabels(e2.id).shot_size==='F03_closeup').length===0)frGate++;frT++;}
  _visLive=null;
  ok('프레이밍 게이트: 전신 시 얼빡 미포함', frT>0&&frGate===frT, frGate+'/'+frT);
  // 색감: 라이브 색감=특정 컷 → 그 컷이 색감 top-1
  ok('CIDX 존재·규모', !!CIDX&&Object.keys(CIDX.emb).length>1700, CIDX?Object.keys(CIDX.emb).length+'장':'없음');
  ok('colInit 디코딩', colInit()===true);
  const lab=srgb2lab(255,0,0);
  ok('srgb2lab 기준값 일치', Math.abs(lab[0]-53.24)<0.5&&Math.abs(lab[1]-80.09)<0.7&&Math.abs(lab[2]-67.20)<0.7,
     lab.map(x=>x.toFixed(1)).join(','));
  const cids=Object.keys(_colEmb).slice(0,10);let cSelf=0;
  for(const id of cids){_colLive=_colEmb[id];const t=colorTop(3);if(t.length&&t[0][0]===id)cSelf++;}
  _colLive=null;
  ok('색감 셀프매칭 top-1 10/10', cSelf===10, cSelf+'/10');
  // 회귀: 색감-단독(포즈·시각 없음) 후보도 d 가 유한값 — 카드 렌더가 죽지 않아야
  _visLive=null;_colLive=_colEmb[Object.keys(_colEmb)[0]];
  const cOnly=camMatch(null);
  ok('색감-단독 매칭 결과 존재', cOnly.top.length>=6, cOnly.top.length+'개');
  ok('색감-단독 d 전부 유한값', cOnly.top.every(e2=>e2.d!=null&&isFinite(e2.d)),
     cOnly.top.map(e2=>e2.d==null?'null':(+e2.d).toFixed(2)).join(','));
  _colLive=null;
  // 랭킹 스무딩: 같은 입력 반복 시 결과 안정(동일 top-6 유지)
  _camRank=new Map();
  const s1=camSmooth(camMatch(PIDX.desc[ids[0]]).top).map(e=>e.id).join();
  const s2=camSmooth(camMatch(PIDX.desc[ids[0]]).top).map(e=>e.id).join();
  ok('랭킹 스무딩 안정성(동일 입력=동일 top6)', s1===s2);
  return out;
}})()`, { filename:'posecam_test.js' });

console.log(R.pass.map(p=>'  ✓ '+p).join('\n'));
if (R.fail.length){ console.log('\nFAIL '+R.fail.length); R.fail.forEach(f=>console.log('  ✗ '+f)); process.exit(1); }
console.log('\nPASS '+R.pass.length);
process.exit(0);
