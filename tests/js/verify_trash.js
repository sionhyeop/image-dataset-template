// 휴지통/버리기 게이팅 검증 — applyFilter 의 cnt(가시 장수)를 독립 재구현과 대조
const fs = require('fs'); const vm = require('vm'); const path = require('path');
// 앱 경로는 데이터셋을 따라간다 (python 의 common.DATASET_DIR 과 같은 규칙).
const _DS = process.env.DATASET_DIR || 'datasets/composition';
const _APP = path.resolve(__dirname, '..', '..', _DS, 'data', '04_samples', 'index.html');
const DEFAULT = _APP;
const htmlPath = process.argv[2] || DEFAULT;
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);

function makeEl(id){const cls=new Set();const el={id,style:{setProperty:()=>{},removeProperty:()=>{}},dataset:{},innerHTML:'',textContent:'',value:'',disabled:false,
  classList:{add:(...a)=>a.forEach(c=>cls.add(c)),remove:(...a)=>a.forEach(c=>cls.delete(c)),toggle:(c,f)=>{(f===undefined?!cls.has(c):f)?cls.add(c):cls.delete(c);},contains:c=>cls.has(c)},
  querySelector:()=>makeEl('q'),querySelectorAll:()=>[],appendChild:()=>{},removeChild:()=>{},remove:()=>{},addEventListener:()=>{},removeEventListener:()=>{},setAttribute:()=>{},focus:()=>{},blur:()=>{},click:()=>{},getBoundingClientRect:()=>({left:0,top:0,width:300,height:300}),getContext:()=>new Proxy({},{get:(t,p)=>(p==='canvas'?el:()=>{})})};return el;}
const els={};
const documentStub={getElementById:id=>(els[id]=els[id]||makeEl(id)),querySelector:()=>makeEl('q'),querySelectorAll:()=>[],createElement:t=>makeEl('new-'+t),head:{appendChild:()=>{}},body:{appendChild:()=>{}},addEventListener:()=>{}};
Object.assign(globalThis,{document:documentStub,window:globalThis,localStorage:{getItem:()=>null,setItem:()=>{},removeItem:()=>{}},requestAnimationFrame:()=>{},innerWidth:1200,innerHeight:800,prompt:()=>'tester',alert:()=>{},confirm:()=>true});
vm.runInThisContext(scripts[0],{filename:'payload.js'});
vm.runInThisContext(scripts[1],{filename:'app.js'});

const R = vm.runInThisContext(`(${function(){
  const out={pass:[],fail:[]};const ok=(n,c,d)=>(c?out.pass:out.fail).push(n+(d?' :: '+d:''));
  const cnt=()=>parseInt(document.getElementById('cnt').textContent,10);
  const clearFilters=()=>AXMETA.forEach(a=>active[a.key].clear());
  // 독립 재구현: intended 게이팅
  function expected(){const filt=AXMETA.some(a=>active[a.key].size>0);const revOn=revFilter.size>0;const search=filt||revOn;let n=0;
    for(const id of IDS){const L=curLabels(id);let m=true;
      for(const a of AXMETA){const sel=active[a.key];if(!sel.size)continue;const v=L[a.key];const vals=a.multi?v:[v];if(!vals.some(x=>sel.has(x))){m=false;break;}}
      const dec=decisionOf(id);
      if(trashOnly){ if(dec!=='discard')m=false; }
      else{ if(m&&dec==='discard')m=false; if(m&&pendingOnly&&!search&&dec)m=false; }
      if(m&&revOn&&!(edits[id]&&revFilter.has(edits[id].reviewer)))m=false;
      if(m)n++;}
    return n;}

  // --- 초기: edits 없음, 미검수만, 필터 없음 → 전량 ---
  clearFilters();pendingOnly=true;trashOnly=false;
  edits={}; applyFilter();
  ok('A 초기 미검수만=전량', cnt()===IDS.length, cnt()+' vs '+IDS.length);

  // --- 3장에 결정 부여: [0]=버리기 [1]=검수완료 [2]=버리기 ---
  const d0=IDS[0], k1=IDS[1], d2=IDS[2];
  const w0=(curLabels(d0).where||[])[0];   // 검색모드 필터에 쓸 장소 코드
  edits[d0]={labels:curLabels(d0),decision:'discard',reviewer:'t'};
  edits[k1]={labels:curLabels(k1),decision:'keep',reviewer:'t'};
  edits[d2]={labels:curLabels(d2),decision:'discard',reviewer:'t'};

  // --- B 미검수만+필터없음: 버리기2·검수1 모두 숨김 → -3 ---
  clearFilters();pendingOnly=true;trashOnly=false;applyFilter();
  ok('B 미검수만: 검수/버리기 3장 숨김', cnt()===IDS.length-3, cnt()+' vs '+(IDS.length-3));
  ok('B == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());

  // --- C 휴지통 모드: 버리기 2장만 ---
  clearFilters();trashOnly=true;applyFilter();
  ok('C 휴지통=버리기 2장만', cnt()===2, cnt()+' (버리기 2)');
  ok('C == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());

  // --- D 검색모드(장소 필터): 버리기는 여전히 숨김, 검수완료는 보임 ---
  trashOnly=false;pendingOnly=true;clearFilters();
  if(w0){active.where.add(w0);}
  applyFilter();
  const withW=IDS.filter(id=>(curLabels(id).where||[]).includes(w0));
  const discInW=withW.filter(id=>decisionOf(id)==='discard').length;
  ok('D 검색모드=장소전체-버리기', cnt()===withW.length-discInW, cnt()+' vs '+(withW.length-discInW)+' (버리기 '+discInW+' 제외)');
  ok('D == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());
  ok('D 검색모드에 버리기 미포함(그 장소 버리기≥1)', discInW>=1 && cnt()<withW.length, '버리기 '+discInW);

  // --- E 휴지통+장소필터: 그 장소의 버리기만 ---
  trashOnly=true;applyFilter();
  ok('E 휴지통+필터=장소내 버리기만', cnt()===discInW, cnt()+' vs '+discInW);
  ok('E == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());

  // --- F 배지 카운트 ---
  refreshCounts();
  ok('F 휴지통 배지=버리기 총수', Number(document.getElementById('trashn').textContent)===2, ''+document.getElementById('trashn').textContent);

  // --- 검수자 필터: 두 검수자가 라벨한 keep 컷을 묶어서 표시(discard·미검수 제외, 미검수만 무시) ---
  trashOnly=false;pendingOnly=true;clearFilters();revFilter=new Set();
  // 서로 다른 검수자 keep 4장 + 한 검수자 discard 1장 세팅
  const A1=IDS[10],A2=IDS[11],B1=IDS[12],B2=IDS[13],Ad=IDS[14];
  edits[A1]={labels:curLabels(A1),decision:'keep',reviewer:'검수자A'};
  edits[A2]={labels:curLabels(A2),decision:'keep',reviewer:'검수자A'};
  edits[B1]={labels:curLabels(B1),decision:'keep',reviewer:'검수자B'};
  edits[B2]={labels:curLabels(B2),decision:'keep',reviewer:'검수자B'};
  edits[Ad]={labels:curLabels(Ad),decision:'discard',reviewer:'검수자A'};
  // 검수자A만: keep 2장 (discard 1장은 제외)
  revFilter=new Set(['검수자A']);applyFilter();
  ok('G 검수자1=그의 keep 2장(discard 제외)', cnt()===2, cnt()+' vs 2');
  ok('G == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());
  // 검수자A+검수자B 묶기: keep 4장
  revFilter=new Set(['검수자A','검수자B']);applyFilter();
  ok('H 검수자2명 묶기=keep 4장', cnt()===4, cnt()+' vs 4');
  ok('H == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());
  ok('H 미검수만 무시(검색 모드)', pendingOnly===true, 'pendingOnly still on');
  // 검수자 필터 + 장소 필터 교차: 그 검수자들의 keep 중 해당 장소만
  const wv=(curLabels(A1).where||[])[0];if(wv)active.where.add(wv);applyFilter();
  ok('I 검수자+장소 교차 == 독립재구현', cnt()===expected(), cnt()+' vs '+expected());
  clearFilters();revFilter=new Set();

  // --- 베타 제외: poseCands 는 버리기한 이웃을 제외 ---
  if(typeof poseCands==='function' && PIDX && PIDX.knn){
    const seed=Object.keys(PIDX.knn).find(id=>(PIDX.knn[id]||[]).length>=2);
    if(seed){edits={};const before=poseCands(seed).length;
      const victim=PIDX.knn[seed][0][0];
      edits[victim]={labels:curLabels(victim),decision:'discard',reviewer:'t'};
      const after=poseCands(seed);
      ok('J poseCands 버리기 이웃 제외', after.length===before-1 && !after.some(x=>x[0]===victim), before+'→'+after.length);
    }
  }
  return out;
}})()`, { filename: 'trash_test_fn.js' });

console.log(R.pass.map(p=>'  ✓ '+p).join('\n'));
if (R.fail.length){ console.log('\nFAIL '+R.fail.length); R.fail.forEach(f=>console.log('  ✗ '+f)); process.exit(1); }
console.log('\nPASS '+R.pass.length);
process.exit(0);   // 앱 setInterval(하트비트)이 이벤트 루프를 붙잡으므로 명시 종료
