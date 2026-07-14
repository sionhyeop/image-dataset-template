// 포즈캠 추천 품질 벤치마크 — 각 이미지를 '라이브 입력'으로 시뮬레이션해 top-K 추천을 채점.
// gold = CLIP 별자리(EMB.knn, 사용자가 '결이 비슷하다'고 평가한 것). 임계 통과까지 튜닝 반복용.
// 사용: node tests/js/bench_posecam.js [index.html]
const fs=require('fs'),vm=require('vm'),path=require('path');
// 앱 경로는 데이터셋을 따라간다 (python 의 common.DATASET_DIR 과 같은 규칙).
const _DS = process.env.DATASET_DIR || 'datasets/example_plants';
const _APP = path.resolve(__dirname, '..', '..', _DS, 'data', '04_samples', 'index.html');
const html=fs.readFileSync(process.argv[2]||_APP,'utf8');
const scripts=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
function makeEl(){const cls=new Set();const el={style:{setProperty:()=>{}},dataset:{},innerHTML:'',textContent:'',value:'',classList:{add:()=>{},remove:()=>{},toggle:()=>{},contains:()=>false},querySelector:()=>makeEl(),querySelectorAll:()=>[],appendChild:()=>{},addEventListener:()=>{},getContext:()=>new Proxy({},{get:(t,p)=>(p==='canvas'?el:()=>{})})};return el;}
Object.assign(globalThis,{document:{getElementById:()=>makeEl(),querySelector:()=>makeEl(),querySelectorAll:()=>[],createElement:()=>makeEl(),head:{appendChild:()=>{}},body:{appendChild:()=>{}},addEventListener:()=>{}},window:globalThis,localStorage:{getItem:()=>null,setItem:()=>{}},requestAnimationFrame:()=>{},innerWidth:1200,innerHeight:800,prompt:()=>'t',confirm:()=>true,atob:s=>Buffer.from(s,'base64').toString('binary')});
vm.runInThisContext(scripts[0]);vm.runInThisContext(scripts[1]);

const R=vm.runInThisContext(`(${function(){
  visInit();colInit();dinoInit();
  const KNN=(window.__APP__.EMB&&window.__APP__.EMB.knn)||{};
  const std=a=>{if(a.length<2)return 0;const m=a.reduce((s,x)=>s+x,0)/a.length;return Math.sqrt(a.reduce((s,x)=>s+(x-m)*(x-m),0)/a.length);};
  // 쿼리: 4신호 모두 있는 이미지(=포즈캠 최상 조건). 최대 200장 샘플.
  const q=Object.keys(PIDX.desc).filter(id=>KNN[id]&&_visEmb[id]&&_colEmb[id]&&POSES[id]);
  const step=Math.max(1,Math.floor(q.length/200)),Q=q.filter((_,i)=>i%step===0);
  const _SI={F01_full_body:0,F02_half_body:1,F03_closeup:2};
  const liveFr=kp=>{const v=i=>kp[i*3+2];if(Math.max(v(27),v(28))>=0.5)return 0;if(Math.max(v(25),v(26),v(23),v(24))>=0.5)return 1;return 2;};
  let clipHit=0,shotAg=0,shotN=0,shotAdj=0,dirAg=0,dirN=0,frMAD=0,frN=0,whereAg=0,whereN=0,tSum=0,n=0;
  for(const qi of Q){
    _colLive=_colEmb[qi];_visLive=_visEmb[qi];_dinoLive=_dinoEmb?_dinoEmb[qi]:null;
    _camScaleLive=scaleOf(qi);_camDir=dirOf(qi);
    _camFrame=liveFr(POSES[qi]);                              // 라이브 프레이밍(가시성)
    const pc=curLabels(qi).person_count;_camNum=pc?(pc==='N01_solo'?1:2):null;
    const t0=Date.now();const top=camMatch(PIDX.desc[qi]).top.filter(e=>e.id!==qi).slice(0,10);tSum+=Date.now()-t0;
    if(!top.length)continue;n++;
    const gold=new Set((KNN[qi]||[]).slice(0,40));
    clipHit+=top.filter(e=>gold.has(e.id)).length/top.length;
    const qL=curLabels(qi);
    // 프레이밍 일치: 라이브 프레이밍 vs 추천 컷 shot_size 라벨(진짜 거리감 신호)
    const frs=top.map(e=>_SI[curLabels(e.id).shot_size]).filter(x=>x!=null);
    if(frs.length){shotAg+=frs.filter(x=>x===_camFrame).length/frs.length;
      shotAdj+=frs.filter(x=>Math.abs(x-_camFrame)<=1).length/frs.length;
      frMAD+=frs.reduce((s,x)=>s+Math.abs(x-_camFrame),0)/frs.length;shotN++;frN++;}
    const qd=dirOf(qi);if(qd){const ds=top.map(e=>dirOf(e.id)).filter(Boolean);
      if(ds.length){dirAg+=ds.filter(x=>x===qd).length/ds.length;dirN++;}}
    if((qL.where||[]).length){const ws=top.map(e=>curLabels(e.id).where||[]);
      whereAg+=ws.filter(w=>w.some(c=>(qL.where||[]).includes(c))).length/ws.length;whereN++;}
  }
  _colLive=_visLive=null;
  return {n,clip:clipHit/n,shot:shotAg/shotN,shotAdj:shotAdj/shotN,frMAD:frMAD/frN,
    dir:dirAg/dirN,where:whereAg/whereN,ms:tSum/n};
}})()`);

const pct=x=>(x*100).toFixed(1)+'%';
console.log(`\n포즈캠 추천 품질 (쿼리 ${R.n}장, gold=CLIP top-40)`);
console.log('─'.repeat(52));
// CLIP 겹침은 라이브 신호 상한(~14%)이라 정보용, 나머지는 사람이 느끼는 일관성 축(합격 기준)
const rows=[
  ['프레이밍 정확일치','shot',pct(R.shot),'≥68%',R.shot>=0.68],
  ['프레이밍 인접이내(거리감)','shotAdj',pct(R.shotAdj),'≥90%',R.shotAdj>=0.90],
  ['프레이밍 편차(MAD, ↓)','frMAD',R.frMAD.toFixed(3),'≤0.42',R.frMAD<=0.42],
  ['방향(앞/옆/뒤) 일치','dir',pct(R.dir),'≥85%',R.dir>=0.85],
  ['장소 일치','where',pct(R.where),'≥55%',R.where>=0.55],
  ['쿼리당 시간(ms, ↓)','ms',R.ms.toFixed(1),'≤20',R.ms<=20],
  ['[참고] CLIP 별자리 겹침','clip',pct(R.clip),'정보용',true],
];
let passN=0;
for(const [nm,,val,thr,ok] of rows){console.log(`${ok?'✓':'✗'} ${nm.padEnd(22)} ${String(val).padStart(8)}  (목표 ${thr})`);if(ok)passN++;}
console.log('─'.repeat(52));
console.log(`통과 ${passN}/${rows.length}  ·  종합점수 ${pct((R.clip+R.shot+R.dir+R.where)/4)} (라벨/별자리 평균)`);
process.exit(passN===rows.length?0:1);
