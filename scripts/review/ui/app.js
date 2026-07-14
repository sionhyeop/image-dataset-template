const A=window.__APP__, IMAGES=A.IMAGES, DATA=A.DATA, TAX=A.TAX, AXMETA=A.AXMETA, DASH=A.DASH, FB=A.FB, EMB=A.EMB, POSES=A.POSES||{}, PIDX=A.PIDX||null, SUG=A.SUG||{}, SIGPROF=A.SIGPROF||null, CURATION=A.CURATION||null;
// 신호 인덱스는 app.py 가 annotations/*_index.json 을 자동 발견해 SIGS 로 싣는다.
// 새 시각 모델을 붙여도 payload 스키마를 안 바꾼다 — 여기서 이름으로 꺼내 쓸 뿐이다.
const SIGS=A.SIGS||{}, VIDX=SIGS.visual||A.VIDX||null, CIDX=SIGS.color||A.CIDX||null, DIDX=SIGS.dino||null;
const IDS=Object.keys(DATA);

/* 의미 역할 — 앱은 'where' 같은 축 이름을 알면 안 된다. taxonomy._semantic 에 물어본다.
   역할이 선언되지 않으면 그 기능만 조용히 꺼진다(인물 없는 도메인에서 자동 비활성).
   실제로 이 장치가 없던 시절, 인물과 무관한 taxonomy 를 넣으면 renderDash 가
   TAX.where 에 접근해 **로드 즉시 TypeError** 로 죽었다. 화면이 통째로 안 떴다. */
const SEM=(A.SCHEMA&&A.SCHEMA.semantic)||{};
const semAxis=(role)=>{const r=SEM[role];const ax=r&&r.axis;return (ax&&TAX[ax])?ax:null;};
const COMP=AXMETA.filter(a=>a.comp).map(a=>a.key);        // 커버리지·통계 대상 축
const PLACE_AXIS=semAxis('place');                        // '어디서' 축 (없으면 null)
const COUNT_AXIS=semAxis('count');                        // 피사체 개수 축
const COUNT_SINGLE=(SEM.count&&SEM.count.single)||null;   // '단독' 코드
// 베타 교차분석 두 축 — 선언이 없으면 커버리지 축 중 멀티축 2개를 자동으로 고른다
const _pair=SEM.pair||{};
const PAIR_X=(TAX[_pair.x]&&_pair.x)||COMP.find(a=>AXMETA.find(m=>m.key===a).multi)||COMP[0]||null;
const PAIR_Y=(TAX[_pair.y]&&_pair.y)||COMP.find(a=>a!==PAIR_X&&AXMETA.find(m=>m.key===a).multi)
             ||COMP.find(a=>a!==PAIR_X)||null;
const axName=(k)=>{const m=AXMETA.find(a=>a.key===k);return m?m.name:k;};
/* 도메인 특정 스코어(taxonomy._extra_fields). 첫 번째를 '대표성 점수'로 쓴다 —
   커버리지 탭 정렬·베타 패널의 대표사진 선택 기준. 없으면 0(순서 무의미)으로 폴백한다.
   예전엔 insta_prob 가 코드에 박혀 있어 다른 도메인에선 전부 0 이 되어 정렬이 무너졌다. */
const EXTRA=A.EXTRA||[];
const repScore=(id)=>{const x=(DATA[id]||{}).x||{};
  return EXTRA.length?(parseFloat(x[EXTRA[0]])||0):0;};
const KEY='comp_review_v3', WHOKEY='comp_reviewer';
let edits=JSON.parse(localStorage.getItem(KEY)||'{}');   // id -> {labels,decision,reviewer,at}
let reviewer=localStorage.getItem(WHOKEY)||'';
let undoStack=[];
let pendingOnly=true, curId=null, selFilterIds=null, trashOnly=false, revFilter=new Set();
const active={}; AXMETA.forEach(a=>active[a.key]=new Set()); active.status=new Set();

// ---------- 공용 ----------
function saveLocal(){localStorage.setItem(KEY,JSON.stringify(edits));}
function toast(m){const t=document.getElementById('toast');t.textContent=m;t.style.display='block';clearTimeout(t._t);t._t=setTimeout(()=>t.style.display='none',1600);}
function curLabels(id){const e=edits[id];if(e&&e.labels){const L=e.labels;
    // 멀티 축은 항상 배열로 정규화(구형 단일문자열 라벨/동시편집 방어)
    AXMETA.forEach(a=>{if(a.multi&&!Array.isArray(L[a.key]))L[a.key]=L[a.key]?[L[a.key]]:[];});return L;}
  const d=DATA[id];const o={};AXMETA.forEach(a=>o[a.key]=a.multi?[...(d[a.key]||[])]:(d[a.key]||''));return o;}
function decisionOf(id){return edits[id]&&edits[id].decision;}
function isDiscarded(id){return decisionOf(id)==='discard';}   // 버리기=데이터셋에서 제거된 것으로 취급(휴지통에서만 노출). 베타 추천/별자리/포즈에서 전면 제외.
function reviewedCount(){return IDS.filter(id=>decisionOf(id)).length;}

// ---------- 탭 ----------
document.querySelectorAll('.nav .tab').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.nav .tab').forEach(x=>x.classList.toggle('on',x===b));
  ['review','cov','beta','dash'].forEach(t=>document.getElementById('tab-'+t).classList.toggle('on',t===b.dataset.tab));
  const t=b.dataset.tab;
  // 탭 전환 시 항상 맨 위부터 — 이전 탭의 스크롤 위치가 남아 대시보드가 중간부터 보이던 문제
  window.scrollTo(0,0);
  if(t==='cov')renderCoverage(); else if(t==='beta')renderBeta(); else if(t==='dash')renderDash();
  requestAnimationFrame(()=>window.scrollTo(0,0));   // 렌더 후 레이아웃 변동(iframe lazy 등) 보정
});
function countUp(el,target){if(!el)return;let s=null;const dur=700;
  function step(ts){if(!s)s=ts;const p=Math.min(1,(ts-s)/dur);el.textContent=Math.round(target*(1-Math.pow(1-p,3)));if(p<1)requestAnimationFrame(step);}
  requestAnimationFrame(step);}

// ---------- 대시보드 ----------
// 실시간: 검수 편집분(curLabels)으로 전수 재집계(버리기 제외). 정적 DASH는 필터 초기 칩 카운트용으로만 남음.
let _dashDirty=true;
function dashCounts(){const c={};AXMETA.forEach(a=>c[a.key]={});let used=0;
  for(const id of IDS){if(decisionOf(id)==='discard')continue;used++;
    const L=curLabels(id);
    for(const a of AXMETA){const vals=a.multi?(L[a.key]||[]):(L[a.key]?[L[a.key]]:[]);for(const v of vals)if(v)c[a.key][v]=(c[a.key][v]||0)+1;}}
  return {counts:c,used};}
// 부족 라벨 전광판: 구도 4축에서 10장 미만 코드를 실시간(curLabels) 집계해 흐르는 배너로.
// 클릭하면 해당 라벨 필터로 점프 — 검수자가 "지금 뭐가 필요한지" 보면서 작업.
// 부족 라벨 기준·집계 축 — taxonomy(_coverage_min, _axes.composition) 에서 내려온다.
// 예전엔 축 목록이 여기 하드코딩돼 있어 taxonomy 를 바꿔도 전광판이 따라오지 않았다.
const TK_MIN=A.COVERAGE_MIN||10, TK_SOFT=TK_MIN*3, TK_AXES=AXMETA.filter(a=>a.comp).map(a=>a.key);
function _tkBind(box){box.querySelectorAll('[data-code]').forEach(el2=>{el2.onclick=()=>{
    AXMETA.forEach(x=>active[x.key].clear());selFilterIds=null;
    active[el2.dataset.ax].add(el2.dataset.code);
    document.querySelectorAll('#filters .chip').forEach(c=>{
      c.classList.toggle('active',c.dataset.ax===el2.dataset.ax&&c.dataset.code===el2.dataset.code);});
    applyFilter();toast(`${TAX[el2.dataset.ax][el2.dataset.code]} 필터 적용 — 이 라벨을 채워주세요`);};});}
function _tkRow(tr,items,goal,emptyMsg){
  if(!items.length){tr.style.animation='none';tr.innerHTML=`<span class="tk-ok">${emptyMsg}</span>`;return;}
  const html=items.map(it=>`<span class="tk-item" data-ax="${it.a}" data-code="${it.code}" title="${it.nm} · ${TAX[it.a][it.code]}">`+
    `<span class="tk-ax">${it.nm}</span><span class="lbl">${TAX[it.a][it.code]}</span><span class="need">${goal-it.n}장 필요</span><span class="cnt">(${it.n}/${goal})</span></span>`).join('');
  tr.innerHTML=html+html;   // 2배 복제 = 이음새 없는 루프
  tr.style.animation='';
  // 항목이 많을수록 항목당 시간을 줄여 더 빨리 흐르게 (적으면 3s/개, 많으면 1.4s/개)
  const n=items.length,persec=n<=4?3.0:(n<=8?2.0:1.4);
  tr.style.setProperty('--tkdur',Math.max(8,Math.round(n*persec))+'s');
  _tkBind(tr);}
function renderTicker(){const t1=document.getElementById('tktrack1'),t2=document.getElementById('tktrack2'),pins=document.getElementById('tkpins');
  if(!t1||!t2)return;
  const {counts}=dashCounts();
  const zero=[],urgent=[],soft=[];   // 0장=고정 핀(항상 보임) · 1행: 1~9장 · 2행: 10~29장
  for(const a of TK_AXES){const nm=(AXMETA.find(x=>x.key===a)||{}).name||a;
    for(const code of Object.keys(TAX[a])){const n=counts[a][code]||0;
      if(n===0)zero.push({a,code,nm});
      else if(n<TK_MIN)urgent.push({a,code,n,nm});
      else if(n<TK_SOFT)soft.push({a,code,n,nm});}}
  urgent.sort((x,y)=>x.n-y.n);soft.sort((x,y)=>x.n-y.n);
  if(pins){pins.innerHTML=zero.map(it=>`<span class="tk-pin" data-ax="${it.a}" data-code="${it.code}" `+
      `title="${it.nm} · 한 장도 없음 — 최우선">미사용 <b>${TAX[it.a][it.code]}</b> (0/${TK_MIN})</span>`).join('');
    _tkBind(pins);}
  _tkRow(t1,urgent,TK_MIN,zero.length?'· 미사용 라벨부터 채워주세요':'✓ 10장 미만 라벨 없음');
  _tkRow(t2,soft,TK_SOFT,'✓ 모든 라벨 30장 이상 확보');}
// LoRA 스토리 iframe 연동: 파인튜닝 지표 수신 + 사이클마다 새 사진 공급(페이로드 재사용 = 추가 용량 0)
let _ftm={acc:'80.0',loss:'1.82'};
function _loraPickPhotos(n){
  const koOf=c=>{for(const a of AXMETA){if(TAX[a.key][c])return TAX[a.key][c];}return c;};
  let pool=IDS.filter(id=>decisionOf(id)==='keep'&&POSES[id]);   // 사람 검수 keep + 포즈 보유 우선
  if(pool.length<n*2)pool=IDS.filter(id=>POSES[id]);             // 부족하면 포즈 있는 전체 approved
  const picks=[],used=new Set();
  let guard=pool.length*4;
  while(picks.length<Math.min(n,pool.length)&&guard-->0){
    const id=pool[Math.floor(Math.random()*pool.length)];
    if(used.has(id))continue;used.add(id);picks.push(id);}
  return picks.map(id=>{
    const L=curLabels(id),e=edits[id]||{};
    const codes=[];AXMETA.forEach(a=>{const v=L[a.key];(a.multi?(v||[]):(v?[v]:[])).forEach(c=>{if(c)codes.push(c);});});
    // 표시용 대표 라벨 — 커버리지 축 순서대로 앞에서 3개(축 이름을 알 필요가 없다)
    const first=(ax)=>{const v=L[ax];return Array.isArray(v)?v[0]:v;};
    const preds=[];
    for(const ax of COMP){if(preds.length>=3)break;const v=first(ax);
      if(v)preds.push([axName(ax),TAX[ax][v]||v,84+(preds.length*3+v.length)%12]);}
    const ko=preds.map(p=>p[1]);
    return {src:IMAGES[id],id,who:e.reviewer||'',label:ko.slice(0,2).join(' · ')||id,
      tags:ko.map((k,i)=>k+' '+(preds[i][0])),codes,preds,kp:POSES[id]};   // kp=[x,y,v]×33
  });
}
if(typeof window.addEventListener==='function')window.addEventListener('message',e=>{const d=e.data;if(!d)return;
  if(d.type==='lora-metrics'){
    _ftm.acc=(+d.acc).toFixed(1);_ftm.loss=(+d.loss).toFixed(2);
    const a=document.getElementById('ftmAcc'),l=document.getElementById('ftmLoss');
    if(a)a.textContent=_ftm.acc+'%';if(l)l.textContent='Loss '+_ftm.loss;return;}
  if(d.type==='lora-need-photos'&&e.source){
    try{e.source.postMessage({type:'lora-photos',photos:_loraPickPhotos(d.n||4)},'*');}catch(_){}}
});
function renderDash(){
  const el=document.getElementById('tab-dash');
  if(el._done&&!_dashDirty)return;
  const {counts,used}=dashCounts();
  const covered=COMP.reduce((s,a)=>s+Object.keys(TAX[a]).filter(k=>(counts[a][k]||0)>=TK_MIN).length,0);
  const totalCodes=COMP.reduce((s,a)=>s+Object.keys(TAX[a]).length,0);
  // 스토리 임베드는 A.STORY 가 있을 때만. 없는 데이터셋에서 깨진 빈 박스가 뜨면 안 된다.
  const story=A.STORY?`<div class="lora-story"><iframe src="lora_story.html" loading="lazy" title="LoRA 학습 스토리 — 검수 라벨이 정확도가 되기까지"></iframe></div>`:'';
  let h=`${story}<div class="tiles">
    <div class="tile"><b id="dtot">0</b>사용 이미지</div>
    <div class="tile"><b>${covered}/${totalCodes}</b>충분한 코드 ≥${TK_MIN}</div>
    <div class="tile"><b id="drev">0</b>검수완료</div>
    <div class="tile ftm" title="위 LoRA 학습 스토리의 실시간 지표"><span class="cap">🎯 파인튜닝 지표<small>LoRA 스토리 라이브</small></span><b id="ftmAcc">${_ftm.acc}%</b><span id="ftmLoss">Loss ${_ftm.loss}</span></div></div>`;
  for(const a of AXMETA){
    const c=counts[a.key]||{}; const mx=Math.max(1,...Object.values(c));
    h+=`<div class="axsec"><h3>${a.name}</h3>`;
    for(const [code,ko] of Object.entries(TAX[a.key])){const n=c[code]||0;const low=n<TK_MIN&&a.comp;
      h+=`<div class="brow${low?' low':''}"><span class="lbl">${ko} <em>${code}</em></span>
        <span class="track"><span class="fill" style="width:${Math.round(100*n/mx)}%"></span></span>
        <span class="num">${n}${low?' ⚠':''}</span></div>`;}
    h+='</div>';
  }
  el.innerHTML=h;
  if(el._done){document.getElementById('dtot').textContent=used;document.getElementById('drev').textContent=reviewedCount();}
  else{countUp(document.getElementById('dtot'),used);countUp(document.getElementById('drev'),reviewedCount());}
  el._done=true;_dashDirty=false;
}

// ---------- 필터 + 그리드 ----------
// 섹션 내 비슷한 결끼리 묶어 줄바꿈(알아보기 쉽게). 순수 UI 편의라 없어도 동작한다
// (선언이 없으면 그 축의 칩이 한 줄로 나온다). taxonomy._chip_groups 에서 주입.
const AXIS_GROUPS=(A.SCHEMA&&A.SCHEMA.chip_groups)||{};
// 배타 그룹: 같은 줄 안에서 하나만 성립하는 코드들 — taxonomy._exclusive 에서 주입(하드코딩 제거)
const EXCL_GROUPS=(A.SCHEMA&&A.SCHEMA.exclusive)||{};
const exclOf=(ax,code)=>{const gs=EXCL_GROUPS[ax];if(!gs)return null;
  for(const g of gs)if(g.includes(code))return g;return null;};
function axGroups(key){const all=Object.keys(TAX[key]),g=AXIS_GROUPS[key];if(!g)return [all];
  const seen=new Set(),out=[];
  for(const grp of g){const f=grp.filter(c=>c in TAX[key]);f.forEach(c=>seen.add(c));if(f.length)out.push(f);}
  const rest=all.filter(c=>!seen.has(c));if(rest.length)out.push(rest);return out;}
function buildFilters(){
  let h='';
  for(const a of AXMETA){h+=`<div class="fsec"><h4>${a.name}</h4><div class="chipgroups">`;
    for(const grp of axGroups(a.key)){h+='<div class="chiprow">';
      for(const code of grp){const ko=TAX[a.key][code];const n=(DASH.counts[a.key]||{})[code]||0;
        h+=`<button class="chip" data-ax="${a.key}" data-code="${code}"${n?'':' disabled'}>${ko} <em>${n}</em></button>`;}
      h+='</div>';}
    h+='</div></div>';}
  document.getElementById('filters').innerHTML=h;
  document.querySelectorAll('#filters .chip').forEach(c=>{
    c.onclick=()=>{const s=active[c.dataset.ax];const k=c.dataset.code;
      if(s.has(k)){s.delete(k);c.classList.remove('active');}else{s.add(k);c.classList.add('active');}applyFilter();};});
}
function buildGrid(){
  const g=document.getElementById('grid');
  g.innerHTML=IDS.map(id=>`<figure data-id="${id}"><img loading="lazy" src="${IMAGES[id]}"><span class="bd"></span><span class="who"></span></figure>`).join('');
  g.querySelectorAll('figure').forEach(f=>{f.onclick=()=>goPhoto(f.dataset.id);paintCard(f.dataset.id);});
}
function paintCard(id){const f=document.querySelector(`#grid figure[data-id="${id}"]`);if(!f)return;
  const dec=decisionOf(id);f.classList.toggle('reviewed',dec==='keep');f.classList.toggle('discarded',dec==='discard');
  const bd=f.querySelector('.bd');bd.textContent=dec==='keep'?'✓':dec==='discard'?'🗑':'';
  const e=edits[id];f.querySelector('.who').textContent=(e&&e.reviewer)?e.reviewer:'';}
// 라벨 필터가 하나라도 켜지면 '검색 모드' — 미검수만 게이팅을 무시하고 전체(검수분 포함)에서 찾는다.
function anyFilterActive(){return AXMETA.some(a=>active[a.key].size>0);}
function refilterActive(){return pendingOnly||trashOnly||revFilter.size>0||anyFilterActive();}  // 결정 변경 시 그리드 재필터 필요 여부
function revMatch(id){const e=edits[id];return e&&revFilter.has(e.reviewer);}
function applyFilter(){let n=0;const filt=anyFilterActive();const revOn=revFilter.size>0;const searchMode=filt||revOn;
  for(const id of IDS){const f=document.querySelector(`#grid figure[data-id="${id}"]`);const L=curLabels(id);let ok=true;
    for(const a of AXMETA){const sel=active[a.key];if(!sel.size)continue;const v=L[a.key];const vals=a.multi?v:[v];if(!vals.some(x=>sel.has(x))){ok=false;break;}}
    const dec=decisionOf(id);
    if(trashOnly){ if(dec!=='discard')ok=false; }        // 휴지통: 버리기한 것만
    else{
      if(ok&&dec==='discard')ok=false;                   // 버리기는 항상 숨김(검색 모드 포함) — 휴지통에서만 보임
      if(ok&&pendingOnly&&!searchMode&&dec)ok=false;      // 필터/검수자 없을 때만 미검수만 적용(필터·검수자=전체 검색)
    }
    if(ok&&revOn&&!revMatch(id))ok=false;                // 검수자 다중 선택 시 그 검수자들이 라벨한 컷만
    if(ok&&selFilterIds&&!selFilterIds.has(id))ok=false;
    f.classList.toggle('hide',!ok);if(ok)n++;}
  document.getElementById('cnt').textContent=n;refreshFilterCounts();}
// 실시간 라벨 반영: 검수 편집분(curLabels)으로 필터 칩 카운트 재계산. 미검수만/영역선택 반영해 그리드와 항상 일치.
let _fcTimer=null,_covDirty=true;
function liveAxisCounts(){const c={};AXMETA.forEach(a=>c[a.key]={});const filt=anyFilterActive();const revOn=revFilter.size>0;const searchMode=filt||revOn;
  for(const id of IDS){
    const dec=decisionOf(id);
    if(trashOnly){ if(dec!=='discard')continue; }           // 휴지통: 버리기한 것만(그리드와 일치)
    else{
      if(dec==='discard')continue;                          // 버리기 항상 제외
      if(pendingOnly&&!searchMode&&dec)continue;             // 필터/검수자 없을 때만 미검수만
    }
    if(revOn&&!revMatch(id))continue;                       // 검수자 다중 선택 제한
    if(selFilterIds&&!selFilterIds.has(id))continue;        // 영역선택 제한 반영
    const L=curLabels(id);
    for(const a of AXMETA){const vals=a.multi?(L[a.key]||[]):(L[a.key]?[L[a.key]]:[]);for(const v of vals)if(v)c[a.key][v]=(c[a.key][v]||0)+1;}}
  return c;}
function refreshFilterCounts(){const box=document.getElementById('filters');if(!box)return;const c=liveAxisCounts();
  box.querySelectorAll('.chip').forEach(ch=>{const n=(c[ch.dataset.ax]||{})[ch.dataset.code]||0;const em=ch.querySelector('em');if(em)em.textContent=n;
    if(!ch.classList.contains('active'))ch.disabled=n===0;});}
function scheduleLiveRefresh(){_covDirty=true;_dashDirty=true;if(_fcTimer)return;_fcTimer=setTimeout(()=>{_fcTimer=null;refreshFilterCounts();renderTicker();
  const dt=document.getElementById('tab-dash');if(dt&&dt.classList.contains('on'))renderDash();},450);}

// ---------- 편집창 ----------
function openPanel(id){curId=id;const d=DATA[id];const dec=decisionOf(id);const e=edits[id]||{};
  document.getElementById('pimg').src=IMAGES[id];
  const pan=document.getElementById('panel');pan.classList.toggle('reviewed',dec==='keep');pan.classList.toggle('discarded',dec==='discard');
  const st=document.getElementById('pstate');
  if(dec==='keep'){st.className='rev';st.textContent='✓ 검수 완료'+(e.reviewer?' · '+e.reviewer:'')+(e.at?' · '+e.at.slice(0,16).replace('T',' '):'');}
  else if(dec==='discard'){st.className='dis';st.textContent='🗑 버려진 이미지'+(e.reviewer?' · '+e.reviewer:'');}
  else{st.className='pend';st.textContent='● 미검수 — 라벨 확인 후 검수완료, 이상하면 버리기';}
  document.getElementById('pkeep').classList.toggle('active',dec==='keep');
  document.getElementById('pdiscard').classList.toggle('active',dec==='discard');
  // derived 축(시스템이 채우는 축)은 칩을 띄우지 않고 여기 읽기전용으로만 보여준다.
  // 축 감사에서 dead 로 판정된 축 — 사람이 시간을 쓸 가치가 없다(예: 96%가 1인).
  const derv=AXMETA.filter(a=>a.mode==='derived').map(a=>{const v=curLabels(id)[a.key];
    const codes=a.multi?(v||[]):(v?[v]:[]);if(!codes.length)return null;
    return `${a.name} ${codes.map(c=>TAX[a.key][c]||c).join('·')}`;}).filter(Boolean).join(' · ');
  document.getElementById('pmeta').textContent=
    `${id} · 상태 ${d.status}`
    + EXTRA.map(f=>{const v=(d.x||{})[f];return v?` · ${f} ${v}`:'';}).join('')
    + (derv?` · 🤖 ${derv}`:'');
  const ais=document.getElementById('aisug');const sgs=SUG[id];
  ais.innerHTML=sgs?('🤖 AI(인물축): '+Object.entries(sgs).map(([ax,v])=>{const nm=(AXMETA.find(x=>x.key===ax)||{}).name||ax;
    return `${nm} <b>${TAX[ax][v[0]]||v[0]}</b> ${Math.round(v[1]*100)}%${v[2]?'✓':''}`;}).join(' · ')+' <span style="color:#667">— 점선 칩이 AI 제안</span>'):'';
  const L=curLabels(id);const box=document.getElementById('paxes');box.innerHTML='';
  for(const a of AXMETA){
    if(a.mode==='derived')continue;          // 시스템이 채우는 축 — 위 pmeta 에 읽기전용 표시
    const hh=document.createElement('h4');hh.textContent=a.name;
    if(a.mode==='human'){const s=document.createElement('small');   // 모델이 못 읽는 축
      s.textContent=' 사람 전용';s.style.cssText='color:#889;font-weight:400';hh.appendChild(s);}
    box.appendChild(hh);
    const groups=document.createElement('div');groups.className='chipgroups';
    for(const grp of axGroups(a.key)){const row=document.createElement('div');row.className='chiprow';
      for(const code of grp){const ko=TAX[a.key][code];const b=document.createElement('button');b.className='chip';b.textContent=ko;
        const on=a.multi?L[a.key].includes(code):L[a.key]===code;if(on)b.classList.add('on');
        const sg=SUG[id]&&SUG[id][a.key];   // 인물축 AI 제안(프로브) — 라벨은 바꾸지 않고 힌트만
        if(sg&&sg[0]===code){b.classList.add('ai');b.title=`AI 추천 ${Math.round(sg[1]*100)}%${sg[2]?' · 자동확정 후보':''}`;}
        b.onclick=()=>{const lab=curLabels(id);
          if(a.multi){const i=lab[a.key].indexOf(code);
            if(i>=0)lab[a.key].splice(i,1);
            else{const g=exclOf(a.key,code);   // 배타 줄: 같은 줄의 기존 선택 해제 후 추가
              if(g)lab[a.key]=lab[a.key].filter(c=>!g.includes(c));
              lab[a.key].push(code);}}
          else lab[a.key]=lab[a.key]===code?'':code;
          setEdit(id,{labels:lab});openPanel(id);};
        row.appendChild(b);}
      groups.appendChild(row);}
    box.appendChild(groups);}
  document.getElementById('ov').style.display='flex';}
function closePanel(){document.getElementById('ov').style.display='none';curId=null;}
function visibleIds(){return IDS.filter(id=>{const f=document.querySelector(`#grid figure[data-id="${id}"]`);return f&&!f.classList.contains('hide');});}
// 다음/이전 대상 = 표시중(필터·미검수만 반영)인 이미지. curId 가 필터에서 빠져도(검수됨) 그리드 위치로 이어감.
function nextVisible(){const vis=visibleIds();const ci=vis.indexOf(curId);
  if(ci>=0)return ci+1<vis.length?vis[ci+1]:null;
  const gi=IDS.indexOf(curId);return vis.find(v=>IDS.indexOf(v)>gi)||null;}
function prevVisible(){const vis=visibleIds();const ci=vis.indexOf(curId);
  if(ci>=0)return ci>0?vis[ci-1]:null;
  const gi=IDS.indexOf(curId);let r=null;for(const v of vis){if(IDS.indexOf(v)<gi)r=v;else break;}return r;}
// 방문 경로 스택 — '이전'이 되짚어감. 검수완료로 필터에서 사라진 이미지도 여기로 복귀 가능.
let navTrail=[];
function goPhoto(id){if(!id)return;if(curId&&curId!==id){navTrail.push(curId);if(navTrail.length>300)navTrail.shift();}openPanel(id);}
function forward(){const nx=nextVisible();   // 다음: 표시중(미검수만이면 미검수) 다음 이미지
  if(nx)goPhoto(nx);else toast(pendingOnly?'미검수 이미지 끝':'마지막 사진입니다');}
function backward(){                          // 이전: 방문 경로 되짚기(막히면 표시중 이전으로 폴백)
  if(navTrail.length){openPanel(navTrail.pop());return;}
  const pv=prevVisible();if(pv)openPanel(pv);else toast('첫 사진입니다');}
function setEdit(id,patch){const prev=edits[id]?JSON.parse(JSON.stringify(edits[id])):null;
  undoStack.push({id,prev});if(undoStack.length>50)undoStack.shift();
  // 실제 편집(라벨 변경 등) 시각·작성자 자동 기록 — 접속·활동의 '마지막 편집' 갱신.
  // at 을 명시한 patch(결정/취소)는 그대로 존중. 접속만 하고 편집 안 하면 여기로 안 오므로 시각 유지됨.
  if(!('at' in patch))patch=Object.assign({at:new Date().toISOString(),reviewer:reviewer||(edits[id]&&edits[id].reviewer)||''},patch);
  edits[id]=Object.assign({},edits[id],patch);saveLocal();syncPush(id);paintCard(id);refreshCounts();scheduleLiveRefresh();}
function decide(id,decision){
  // 같은 결정 버튼을 다시 누르면 취소(미검수로 되돌림), 현재 이미지에 머문다
  if(decisionOf(id)===decision){
    const lab=curLabels(id);setEdit(id,{labels:lab,decision:'',reviewer:'',at:''});
    if(refilterActive())applyFilter();
    if(curId===id)openPanel(id);
    toast(decision==='keep'?'검수 완료 취소됨':'버리기 취소됨');
    return;
  }
  if(!reviewer){askWho(true);if(!reviewer)return;}
  const lab=curLabels(id);setEdit(id,{labels:lab,decision,reviewer,at:new Date().toISOString()});
  const nx=nextVisible();if(refilterActive())applyFilter();
  // 자동넘김 시 방금 검수한 컷을 경로에 남겨 '이전'으로 되돌아올 수 있게(미검수 필터에서 사라져도)
  if(nx)goPhoto(nx);else{closePanel();toast('마지막 이미지까지 검수했습니다');}}
function refreshCounts(){const rc=reviewedCount();const pct=IDS.length?Math.round(100*rc/IDS.length):0;
  document.getElementById('revn').textContent=rc;
  document.getElementById('totn').textContent=IDS.length;
  document.getElementById('revpct').textContent=pct+'%';
  document.getElementById('ring').style.setProperty('--p',pct+'%');
  document.getElementById('progbar').style.width=pct+'%';
  const dr=document.getElementById('drev');if(dr)dr.textContent=rc;
  const tn=document.getElementById('trashn');if(tn)tn.textContent=IDS.filter(id=>decisionOf(id)==='discard').length;
  if(rc>0&&rc===IDS.length&&!window._celeb){window._celeb=true;if(typeof confetti==='function'){confetti();toast('🎉 전체 검수 완료!');}}
  renderRanking();renderPresence();}   // 검수 활동 갱신 시 접속·활동 목록도 함께
// 검수자별 랭킹 — edits 의 decision+reviewer 집계(실시간 동기화 시 refreshCounts 로 갱신)
function renderRanking(){const box=document.getElementById('ranklist');if(!box)return;
  const c={};for(const id in edits){const e=edits[id];if(e&&e.decision&&e.reviewer)c[e.reviewer]=(c[e.reviewer]||0)+1;}
  const arr=Object.entries(c).sort((a,b)=>b[1]-a[1]);
  const mine=document.getElementById('rankmine');
  if(mine){const my=reviewer&&c[reviewer]?c[reviewer]:0;const rank=reviewer?arr.findIndex(x=>x[0]===reviewer)+1:0;
    mine.textContent=reviewer?(rank?`내 ${rank}위·${my}장`:'나 0장'):'';}
  if(!arr.length){box.innerHTML='<li class="empty">아직 검수 기록 없음</li>';return;}
  const esc=s=>String(s).replace(/[<>&]/g,m=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[m])),medal=['🥇','🥈','🥉'];
  box.innerHTML=arr.map(([nm,n],i)=>`<li class="${nm===reviewer?'me':''}"><span class="rk">${medal[i]||(i+1)}</span><span class="nm" title="${esc(nm)}">${esc(nm)}</span><span class="n">${n}</span></li>`).join('');}

// 접속·활동: presence(하트비트 ts) + 검수 기록(edits[].at)을 합쳐 검수자별 최근 활동 표시
let _presTs={};   // reviewer -> 마지막 하트비트(ms)
function relTime(ms){if(!ms)return '';const s=(Date.now()-ms)/1000;
  if(s<90)return '방금 전';if(s<3600)return Math.round(s/60)+'분 전';
  if(s<86400)return Math.round(s/3600)+'시간 전';return Math.round(s/86400)+'일 전';}
function renderPresence(){const box=document.getElementById('wholist');if(!box)return;
  // 활동 시각 = 검수 기록(edits.at)만. presence 는 온라인 점 판정에만 사용 —
  // presence 컬렉션엔 beforeunload 미발동으로 남은 좀비 세션 문서(검수 0건 이름 포함)가 쌓이기 때문.
  const last={};                                    // reviewer -> 최근 검수(ms)
  for(const id in edits){const e=edits[id];if(!e||!e.reviewer||!e.at)continue;
    const t=Date.parse(e.at)||0;if(t>(last[e.reviewer]||0))last[e.reviewer]=t;}
  const now=Date.now();
  const online=new Set(Object.keys(_presTs).filter(nm=>now-_presTs[nm]<60000));
  online.forEach(nm=>{if(!(nm in last))last[nm]=0;});   // 접속 중이면 검수 0건이어도 표시
  const arr=Object.entries(last).sort((a,b)=>(online.has(b[0])-online.has(a[0]))||(b[1]-a[1]));
  if(!arr.length){box.innerHTML='<li class="empty">'+(FB?'아직 활동 없음':'실시간 OFF (로컬 모드)')+'</li>';return;}
  const esc=s=>String(s).replace(/[<>&]/g,m=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[m]));
  box.innerHTML=arr.map(([nm,t])=>{const on=online.has(nm);const ago=t?relTime(t):'';
    // 활동시각(마지막 편집)은 접속 중에도 보존해 함께 표시. 편집 없이 접속만 하면 이전 편집시각 유지.
    const status=on?(ago?`접속 중 · ${ago}`:'접속 중'):(ago||'기록 없음');
    return `<li class="${nm===reviewer?'me':''}"><span class="wdot${on?' on':''}"></span>`+
      `<span class="nm" title="${esc(nm)}">${esc(nm)}</span>`+
      `<span class="ago">${status}</span></li>`;}).join('');}
setInterval(renderPresence,60000);   // 상대시간 주기 갱신

document.getElementById('pkeep').onclick=()=>decide(curId,'keep');
document.getElementById('pdiscard').onclick=()=>decide(curId,'discard');
document.getElementById('pnext').onclick=forward;
document.getElementById('pprev').onclick=backward;
document.getElementById('pclose').onclick=closePanel;
document.getElementById('ov').onclick=e=>{if(e.target.id==='ov')closePanel();};
document.getElementById('pimg').onclick=()=>{document.getElementById('zoomimg').src=IMAGES[curId];document.getElementById('zoom').style.display='flex';};
document.getElementById('zoom').onclick=()=>document.getElementById('zoom').style.display='none';
document.addEventListener('keydown',e=>{
  if(e.ctrlKey&&e.key==='z'){doUndo();return;}
  if(document.getElementById('ov').style.display==='none')return;
  if(e.key==='a'||e.key==='A')decide(curId,'keep');
  else if(e.key==='d'||e.key==='D')decide(curId,'discard');
  else if(e.key==='ArrowRight'){forward();}
  else if(e.key==='ArrowLeft'){backward();}
  else if(e.key==='Escape')closePanel();});

function doUndo(){const u=undoStack.pop();if(!u){toast('되돌릴 항목 없음');return;}
  if(u.prev)edits[u.id]=u.prev;else delete edits[u.id];saveLocal();syncPush(u.id);paintCard(u.id);refreshCounts();
  if(curId===u.id)openPanel(u.id);toast('되돌렸습니다');}
document.getElementById('undo').onclick=doUndo;

// ---------- 커버리지 (insta 점수순 상위) ----------
function renderCoverage(){const el=document.getElementById('tab-cov');if(el._done&&!_covDirty)return;
  let h='';
  for(const ax of COMP){for(const [code,ko] of Object.entries(TAX[ax])){
    const items=IDS.filter(id=>{const L=curLabels(id);const v=L[ax];return AXMETA.find(a=>a.key===ax).multi?v.includes(code):v===code;})
      .sort((a,b)=>repScore(b)-repScore(a));
    const low=items.length<10;
    h+=`<div class="cvrow"><h3>${ax} · ${code} <small>${ko}</small> — ${items.length}장${low?' <span class="warn">⚠ 10 미만</span>':''}</h3>
      <div class="cvscroll">${items.slice(0,14).map(id=>`<img src="${IMAGES[id]}" data-id="${id}">`).join('')}</div></div>`;
  }}
  el.innerHTML=h;el._done=true;_covDirty=false;
  el.querySelectorAll('img').forEach(im=>im.onclick=()=>openPanel(im.dataset.id));}

// ---------- 검수자 / 내보내기 ----------
function askWho(force){const n=prompt('검수자 이름',reviewer||'');
  if(n&&n.trim()){reviewer=n.trim();localStorage.setItem(WHOKEY,reviewer);document.getElementById('who').textContent=reviewer;presencePush();}
  else if(force)document.getElementById('who').textContent='(미입력)';}
document.getElementById('setwho').onclick=()=>askWho(false);
document.getElementById('reset').onclick=()=>{AXMETA.forEach(a=>active[a.key].clear());selFilterIds=null;
  document.querySelectorAll('#filters .chip').forEach(c=>c.classList.remove('active'));applyFilter();};
document.getElementById('pendingOnly').onchange=e=>{pendingOnly=e.target.checked;applyFilter();};
document.getElementById('trash').onclick=e=>{trashOnly=!trashOnly;e.currentTarget.classList.toggle('active',trashOnly);
  document.getElementById('pendingOnly').disabled=trashOnly;applyFilter();
  if(trashOnly)toast(document.getElementById('trashn').textContent+'장 버리기함 표시 (다시 눌러 해제)');};
// ---- 검수자별 다중 선택 필터 ----
function reviewerCounts(){const c={};for(const id of IDS){const e=edits[id];if(e&&e.decision&&e.reviewer)c[e.reviewer]=(c[e.reviewer]||0)+1;}return c;}
function updateRevBtn(){const n=revFilter.size;const b=document.getElementById('revbtn');b.classList.toggle('active',n>0);
  document.getElementById('revn').textContent=n?n:'';}
function buildRevMenu(){const m=document.getElementById('revmenu');const c=reviewerCounts();
  // 사라진 검수자(이름 변경 등) 선택은 정리
  for(const nm of[...revFilter])if(!(nm in c))revFilter.delete(nm);
  const names=Object.keys(c).sort((a,b)=>c[b]-c[a]||a.localeCompare(b));
  if(!names.length){m.innerHTML='<div class="empty">아직 검수 기록이 없습니다</div>';return;}
  let h='<div class="rvhd"><span>검수자별 보기 (다중 선택)</span><button id="revclear">모두 해제</button></div>';
  h+=names.map(nm=>`<label><input type="checkbox" value="${nm}"${revFilter.has(nm)?' checked':''}><span class="rvnm">${nm}</span><span class="rc">${c[nm]}</span></label>`).join('');
  m.innerHTML=h;
  m.querySelectorAll('input[type=checkbox]').forEach(cb=>cb.onchange=()=>{cb.checked?revFilter.add(cb.value):revFilter.delete(cb.value);updateRevBtn();applyFilter();});
  const clr=document.getElementById('revclear');if(clr)clr.onclick=()=>{revFilter.clear();buildRevMenu();updateRevBtn();applyFilter();};}
document.getElementById('revbtn').onclick=e=>{e.stopPropagation();const m=document.getElementById('revmenu');const open=m.hidden;if(open)buildRevMenu();m.hidden=!open;};
document.addEventListener('click',e=>{const rf=document.getElementById('revfilt'),m=document.getElementById('revmenu');
  if(m&&!m.hidden&&rf&&!rf.contains(e.target))m.hidden=true;});
document.getElementById('jump').onclick=()=>{const id=IDS.find(id=>!decisionOf(id));if(id)goPhoto(id);else toast('모두 검수됨');};
document.getElementById('export').onclick=()=>{
  const cols=['image_id'].concat(AXMETA.map(a=>a.key)).concat(['decision','status','reviewer','reviewed_at']);
  const rows=[cols];
  for(const id of IDS){const e=edits[id];if(!e||!e.decision)continue;const L=e.labels||curLabels(id);
    const r=[id];for(const a of AXMETA)r.push(a.multi?(L[a.key]||[]).join(';'):(L[a.key]||''));
    r.push(e.decision);r.push(e.decision==='discard'?'discarded':'approved');r.push(e.reviewer||'');r.push(e.at||'');rows.push(r);}
  const csv=rows.map(r=>r.map(x=>'"'+String(x).replace(/"/g,'""')+'"').join(',')).join('\n');
  const b=new Blob(['﻿'+csv],{type:'text/csv'});const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='review_decisions.csv';a.click();};

// ---------- Firebase 실시간 (선택) ----------
let syncPush=()=>{}, presencePush=()=>{};
function initFirebase(){
  const s1=document.createElement('script');s1.src='https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js';
  s1.onload=()=>{const s2=document.createElement('script');s2.src='https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js';
    s2.onload=startFirebase;document.head.appendChild(s2);};
  document.head.appendChild(s1);}
function startFirebase(){
  firebase.initializeApp(FB);const db=firebase.firestore();
  const col=db.collection('reviews'), pres=db.collection('presence');
  // 실시간 결정 반영
  col.onSnapshot(snap=>{let touched=false;
    snap.docChanges().forEach(ch=>{const id=ch.doc.id;const v=ch.doc.data();
      if(!DATA[id])return; edits[id]=Object.assign({},edits[id],{labels:v.labels,decision:v.decision,reviewer:v.reviewer,at:v.at});
      paintCard(id);if(curId===id)openPanel(id);touched=true;});
    if(touched){saveLocal();refreshCounts();scheduleLiveRefresh();if(refilterActive())applyFilter();}});
  // 결정을 firestore 로
  syncPush=(id)=>{const e=edits[id];if(!e)return;col.doc(id).set({labels:e.labels||curLabels(id),decision:e.decision||'',reviewer:e.reviewer||reviewer,at:e.at||new Date().toISOString()},{merge:true}).catch(()=>{});};
  // 접속자 표시
  const sid=Math.random().toString(36).slice(2);const me=pres.doc(sid);
  presencePush=()=>me.set({reviewer:reviewer||'익명',ts:Date.now()}).catch(()=>{});
  presencePush();setInterval(presencePush,20000);
  window.addEventListener('beforeunload',()=>me.delete());
  pres.onSnapshot(snap=>{_presTs={};snap.forEach(d=>{const v=d.data();const nm=v.reviewer||'익명';
      _presTs[nm]=Math.max(_presTs[nm]||0,v.ts||0);});
    renderPresence();});
  toast('실시간 협업 연결됨 (Firebase)');
}

// ---------- 관계맵 (CLIP 임베딩 2D 산점도) ----------
const PAL=['#4a90e2','#e2724a','#50b06a','#c94ab0','#d9b23b','#3fb6c9','#e2544a','#8a6ad9','#7fae3f','#d94a7a','#5a9b8a','#b0894a'];
let mapS=null;
function renderMap(){
  const wrap=document.getElementById('mapwrap');
  if(!EMB||!EMB.coords){wrap.innerHTML='<div style="padding:30px;color:#888">관계맵 데이터가 없습니다. <b>python scripts/index/embed_map.py</b> 후 <b>python scripts/review/app.py</b> 재생성.</div>';return;}
  if(mapS)return;
  const cv=document.getElementById('mapcanvas'),tip=document.getElementById('maptip'),tlbl=document.getElementById('maptiplbl');
  const axsel=document.getElementById('mapaxis');
  axsel.innerHTML=AXMETA.map(a=>`<option value="${a.key}">${a.name}</option>`).join('')
    +'<option value="__cluster">🎨 군집(시각유형)</option><option value="__rarity">🔥 희귀도</option>';
  const CO=EMB.coords, AT=EMB.attr||{}, CLU=EMB.clusters||[], KNN=EMB.knn||{};
  const pts=Object.keys(CO).map(id=>({id,x:CO[id][0],y:CO[id][1],c:CO[id][2]}));
  mapS={scale:1,ox:0,oy:0,axis:'__cluster',hidden:new Set(),iso:null,anim:0,drag:false,moved:false,density:false,selRect:null,dpr:window.devicePixelRatio||1};
  axsel.value='__cluster';
  const heat=v=>{v=Math.max(0,Math.min(1,v));return `rgb(${Math.round(60+195*v)},${Math.round(90+40*v)},${Math.round(230*(1-v))})`;};
  const catFor=id=>{const d=DATA[id];const a=AXMETA.find(x=>x.key===mapS.axis);const v=d[mapS.axis];return a&&a.multi?((v&&v[0])||''):v;};
  function colorFor(p){if(mapS.axis==='__cluster')return PAL[((p.c%PAL.length)+PAL.length)%PAL.length];
    if(mapS.axis==='__rarity')return heat(AT[p.id]?AT[p.id][0]/0.6:0);
    const c=catFor(p.id);const i=Object.keys(TAX[mapS.axis]).indexOf(c);return i<0?'#555':PAL[i%PAL.length];}
  function visible(p){if(isDiscarded(p.id))return false;   // 버리기한 컷은 관계맵에서 제외(그리기·히트·영역선택 모두)
    if(mapS.iso!=null&&p.c!==mapS.iso)return false;
    if(mapS.axis!=='__cluster'&&mapS.axis!=='__rarity'&&mapS.hidden.has(catFor(p.id)))return false;return true;}
  const sx=p=>(p.x*cv.width)*mapS.scale+mapS.ox, sy=p=>(p.y*cv.height)*mapS.scale+mapS.oy;
  function draw(){const ctx=cv.getContext('2d');ctx.clearRect(0,0,cv.width,cv.height);
    if(mapS.density){ctx.globalAlpha=1;for(const p of pts){if(!visible(p))continue;const R=24*mapS.dpr;
      const g=ctx.createRadialGradient(sx(p),sy(p),0,sx(p),sy(p),R);g.addColorStop(0,'rgba(90,150,235,0.09)');g.addColorStop(1,'rgba(90,150,235,0)');
      ctx.fillStyle=g;ctx.fillRect(sx(p)-R,sy(p)-R,2*R,2*R);}}
    for(const p of pts){if(!visible(p))continue;const rr=AT[p.id]?AT[p.id][0]:0;const r=(2.6+rr*4)*mapS.dpr;
      ctx.globalAlpha=0.86*mapS.anim;ctx.fillStyle=colorFor(p);ctx.beginPath();ctx.arc(sx(p),sy(p),r,0,7);ctx.fill();}
    ctx.globalAlpha=1;
    if(mapS.selRect){const s=mapS.selRect;ctx.strokeStyle='#fff';ctx.lineWidth=1.5;ctx.setLineDash([6,4]);ctx.strokeRect(s.x0,s.y0,s.x1-s.x0,s.y1-s.y0);ctx.setLineDash([]);}}
  function legend(){const el=document.getElementById('maplegend');
    if(mapS.axis==='__rarity'){el.innerHTML=`<span><i style="background:${heat(0)}"></i>흔함</span><span><i style="background:${heat(1)}"></i>희귀 (점 클수록 희귀)</span>`;return;}
    if(mapS.axis==='__cluster'){el.innerHTML='<span style="color:#9ab">16개 시각유형(색=군집) · 우측 "군집목록" 체크로 대표/크기 보기</span>';return;}
    const codes=Object.keys(TAX[mapS.axis]);
    el.innerHTML=codes.map((c,i)=>`<span data-c="${c}" class="${mapS.hidden.has(c)?'off':''}"><i style="background:${PAL[i%PAL.length]}"></i>${TAX[mapS.axis][c]}</span>`).join('');
    el.querySelectorAll('span[data-c]').forEach(s=>s.onclick=()=>{const c=s.dataset.c;mapS.hidden.has(c)?mapS.hidden.delete(c):mapS.hidden.add(c);legend();draw();});}
  function resize(){cv.width=wrap.clientWidth*mapS.dpr;cv.height=wrap.clientHeight*mapS.dpr;draw();}
  function hit(mx,my){let b=null,bd=1e9;for(const p of pts){if(!visible(p))continue;const dx=sx(p)-mx,dy=sy(p)-my,d=dx*dx+dy*dy;if(d<bd){bd=d;b=p;}}return b&&bd<(11*mapS.dpr)**2?b:null;}
  function showKnn(id){const strip=document.getElementById('knnstrip');const nb=[id].concat((KNN[id]||[]).filter(x=>!isDiscarded(x)));
    strip.innerHTML='<span class="title">비슷한<br>구도 ▶</span>'+nb.map((x,i)=>`<div class="k ${i===0?'self':''}" data-id="${x}"><img src="${IMAGES[x]}"><small>${i===0?'선택':'#'+i}</small></div>`).join('');
    strip.querySelectorAll('.k').forEach(k=>k.onclick=()=>openPanel(k.dataset.id));}
  function doSelect(){const s=mapS.selRect;const x0=Math.min(s.x0,s.x1),x1=Math.max(s.x0,s.x1),y0=Math.min(s.y0,s.y1),y1=Math.max(s.y0,s.y1);
    const ids=pts.filter(p=>visible(p)&&sx(p)>=x0&&sx(p)<=x1&&sy(p)>=y0&&sy(p)<=y1).map(p=>p.id);
    const el=document.getElementById('mapsel');if(!ids.length){el.style.display='none';return;}
    el.style.display='block';el.innerHTML=`<b>${ids.length}장</b> 선택 <button id="selview">검수에서 보기</button><button id="selcopy">ID복사</button><button class="warn" id="seldis">모두 버리기</button>`;
    document.getElementById('selview').onclick=()=>{selFilterIds=new Set(ids);document.querySelector('.nav .tab[data-tab="review"]').click();applyFilter();toast(ids.length+'장만 검수에 표시 (필터초기화로 해제)');};
    document.getElementById('selcopy').onclick=()=>{try{navigator.clipboard.writeText(ids.join('\n'));toast('ID '+ids.length+'개 복사됨');}catch(e){toast('복사 실패');}};
    document.getElementById('seldis').onclick=()=>{if(!confirm(ids.length+'장을 버리기로 표시할까요?'))return;if(!reviewer)askWho(true);ids.forEach(id=>setEdit(id,{labels:curLabels(id),decision:'discard',reviewer,at:new Date().toISOString()}));toast(ids.length+'장 버리기');};}
  function buildClusterPanel(){const cp=document.getElementById('clusterpanel');
    cp.innerHTML='<div style="font-size:11px;color:#9ad;margin-bottom:4px">시각유형 '+CLU.length+'개 · 클릭=격리</div>'+
      CLU.slice().sort((a,b)=>b.size-a.size).map(c=>{
        const _tg=(ax)=>{const t=ax&&c.tags&&c.tags[ax];return (t&&t[0])?(TAX[ax][t[0][0]]||t[0][0]):'';};
        const t=[_tg(PAIR_X),_tg(PAIR_Y)].filter(Boolean).join(' / ');
        return `<div class="cl ${mapS.iso===c.idx?'on':''}" data-c="${c.idx}"><img src="${IMAGES[c.rep]||''}"><div><b>${c.size}장</b><small>${t}</small></div></div>`;}).join('');
    cp.querySelectorAll('.cl').forEach(el=>el.onclick=()=>{const c=+el.dataset.c;mapS.iso=(mapS.iso===c?null:c);
      mapS.axis='__cluster';axsel.value='__cluster';legend();buildClusterPanel();draw();});}
  axsel.onchange=()=>{mapS.axis=axsel.value;mapS.hidden.clear();legend();draw();};
  document.getElementById('mapdensity').onchange=e=>{mapS.density=e.target.checked;draw();};
  document.getElementById('mapclusters').onchange=e=>{const cp=document.getElementById('clusterpanel');cp.style.display=e.target.checked?'block':'none';if(e.target.checked)buildClusterPanel();};
  cv.onmousemove=e=>{const mx=e.offsetX*mapS.dpr,my=e.offsetY*mapS.dpr;
    if(mapS.selRect){mapS.selRect.x1=mx;mapS.selRect.y1=my;draw();return;}
    if(mapS.drag){mapS.ox+=e.movementX*mapS.dpr;mapS.oy+=e.movementY*mapS.dpr;mapS.moved=true;draw();return;}
    const b=hit(mx,my);
    if(b){const d=DATA[b.id];tip.src=IMAGES[b.id];tip.style.display='block';tip.style.left=(e.offsetX+14)+'px';tip.style.top=(e.offsetY+14)+'px';
      tlbl.style.display='block';tlbl.style.left=(e.offsetX+14)+'px';tlbl.style.top=(e.offsetY+140)+'px';
      const _lbl=(ax)=>{if(!ax)return '';const v=d[ax];const cs=Array.isArray(v)?v:(v?[v]:[]);
        return cs.map(c=>TAX[ax][c]||c).join('·')||'-';};
      tlbl.textContent=[_lbl(PAIR_X),_lbl(PAIR_Y)].filter(Boolean).join('/')
        +` · 희귀 ${(AT[b.id]?AT[b.id][0]:0).toFixed(2)}`;cv._hit=b.id;}
    else{tip.style.display='none';tlbl.style.display='none';cv._hit=null;}};
  cv.onmouseleave=()=>{tip.style.display='none';tlbl.style.display='none';};
  cv.onmousedown=e=>{const mx=e.offsetX*mapS.dpr,my=e.offsetY*mapS.dpr;
    if(e.shiftKey)mapS.selRect={x0:mx,y0:my,x1:mx,y1:my};else{mapS.drag=true;mapS.moved=false;cv.style.cursor='grabbing';}};
  window.addEventListener('mouseup',()=>{if(!mapS)return;if(mapS.selRect){doSelect();mapS.selRect=null;draw();}mapS.drag=false;cv.style.cursor='grab';});
  cv.onclick=()=>{if(!mapS.moved&&cv._hit)showKnn(cv._hit);};
  cv.onwheel=e=>{e.preventDefault();const mx=e.offsetX*mapS.dpr,my=e.offsetY*mapS.dpr,f=e.deltaY<0?1.15:1/1.15;
    mapS.ox=mx-(mx-mapS.ox)*f;mapS.oy=my-(my-mapS.oy)*f;mapS.scale*=f;draw();};
  document.getElementById('mapreset').onclick=()=>{mapS.scale=1;mapS.ox=0;mapS.oy=0;mapS.iso=null;document.getElementById('mapsel').style.display='none';document.getElementById('knnstrip').innerHTML='';legend();buildClusterPanel();draw();};
  window.addEventListener('resize',resize);
  legend();
  (function start(t){cv.width=wrap.clientWidth*mapS.dpr;cv.height=wrap.clientHeight*mapS.dpr;
    if((cv.width<10||cv.height<10)&&t<60){requestAnimationFrame(()=>start(t+1));return;}
    mapS.anim=0;(function a(){mapS.anim=Math.min(1,mapS.anim+0.05);draw();if(mapS.anim<1)requestAnimationFrame(a);})();})(0);
}

// ---------- 조합 매트릭스 (인사이트/회의용) ----------
function renderCombos(){const sx=document.getElementById('cx'),sy=document.getElementById('cy');
  if(!sx._init){const opt=AXMETA.map(a=>`<option value="${a.key}">${a.name}</option>`).join('');
    sx.innerHTML=opt;sy.innerHTML=opt;sx.value=PAIR_X||COMP[0];sy.value=PAIR_Y||COMP[1]||COMP[0];
    sx._init=true;sx.onchange=sy.onchange=drawCombo;}
  drawCombo();}
let comboTip=null;
function drawCombo(){const ax=document.getElementById('cx').value,ay=document.getElementById('cy').value;
  const A=AXMETA.find(a=>a.key===ax),B=AXMETA.find(a=>a.key===ay);
  const xc=Object.keys(TAX[ax]),yc=Object.keys(TAX[ay]);const cnt={},rep={};let mx=0;
  for(const id of IDS){if(decisionOf(id)==='discard')continue;   // 실시간: 검수 라벨 반영·버려진 컷 제외
    const d=curLabels(id);const xs=A.multi?d[ax]:(d[ax]?[d[ax]]:[]),ys=B.multi?d[ay]:(d[ay]?[d[ay]]:[]);
    for(const x of xs)for(const y of ys){const k=x+'|'+y;cnt[k]=(cnt[k]||0)+1;if(!rep[k])rep[k]=id;mx=Math.max(mx,cnt[k]);}}
  let low=0,empty=0;
  let h='<table class="mtx"><thead><tr><th></th>'+xc.map(x=>`<th>${TAX[ax][x].slice(0,6)}</th>`).join('')+'</tr></thead><tbody>';
  for(const y of yc){h+=`<tr><th class="row">${TAX[ay][y]}</th>`;
    for(const x of xc){const n=cnt[x+'|'+y]||0,a=mx?n/mx:0;let bg,txt=n||'';
      if(n===0){bg='#141414';empty++;}
      else if(n<10){bg=`rgba(217,140,60,${(0.25+0.55*(n/10)).toFixed(3)})`;low++;txt=n;}
      else bg=`rgba(74,144,226,${(0.18+0.82*a).toFixed(3)})`;
      h+=`<td data-x="${x}" data-y="${y}" data-n="${n}" style="background:${bg}" title="${TAX[ay][y]} × ${TAX[ax][x]}: ${n}장${n>0&&n<10?' (10까지 '+(10-n)+' 부족)':''}">${txt}</td>`;}
    h+='</tr>';}
  h+='</tbody></table>';
  const w=document.getElementById('combowrap');
  w.innerHTML=`<div style="font-size:12px;color:#9ab;margin:4px 0">파랑=충분(10↑) · 주황=부족(1~9) · 검정=없음　|　<b style="color:#e8a04a">부족 조합 ${low}개</b> · 빈 조합 ${empty}개</div>`+h;
  if(!comboTip){comboTip=document.createElement('img');comboTip.style.cssText='position:fixed;width:120px;height:120px;object-fit:cover;border:2px solid #fff;border-radius:6px;display:none;z-index:80;pointer-events:none';document.body.appendChild(comboTip);}
  w.querySelectorAll('td').forEach(td=>{const k=td.dataset.x+'|'+td.dataset.y;
    td.onmousemove=e=>{if(rep[k]){comboTip.src=IMAGES[rep[k]];comboTip.style.display='block';comboTip.style.left=(e.clientX+16)+'px';comboTip.style.top=(e.clientY+16)+'px';}};
    td.onmouseleave=()=>comboTip.style.display='none';
    td.onclick=()=>{comboTip.style.display='none';selFilterIds=null;
      AXMETA.forEach(a=>active[a.key].clear());active[ax].add(td.dataset.x);active[ay].add(td.dataset.y);
      document.querySelectorAll('#filters .chip').forEach(c=>c.classList.remove('active'));
      [[ax,td.dataset.x],[ay,td.dataset.y]].forEach(([a,code])=>{const el=document.querySelector(`#filters .chip[data-ax="${a}"][data-code="${code}"]`);if(el)el.classList.add('active');});
      document.querySelector('.nav .tab[data-tab="review"]').click();applyFilter();
      const n=+td.dataset.n;if(n>0&&n<10)toast(`이 조합 ${n}장 — ${10-n}장 부족. queries.yaml 보강검색어나 seeds.csv 시드핀으로 채우세요`);};});}

// ---------- 🧪 베타 탭 ----------
// 신체부위별 색상(머리/팔/몸통/다리) — 스켈레톤 의미론 명확화
const POSE_COL={head:'#f6c445',arm:'#4a90e2',torso:'#50b06a',leg:'#c94ab0'};
const POSE_GROUPS=[
  {c:POSE_COL.head, e:[[0,11],[0,12]]},
  {c:POSE_COL.arm,  e:[[11,13],[13,15],[12,14],[14,16]]},
  {c:POSE_COL.torso,e:[[11,12],[11,23],[12,24],[23,24]]},
  {c:POSE_COL.leg,  e:[[23,25],[25,27],[24,26],[26,28]]},
];
// 각 키포인트 색상(부위 소속)
const POSE_PTCOL=(()=>{const m={};[0,7,8].forEach(i=>m[i]=POSE_COL.head);
  [13,15,14,16].forEach(i=>m[i]=POSE_COL.arm);[11,12,23,24].forEach(i=>m[i]=POSE_COL.torso);
  [25,27,26,28].forEach(i=>m[i]=POSE_COL.leg);return m;})();
const POSE_ATTR_KO={p:{stand:'서기',sit:'앉기',mid:'중간자세',unknown:'자세미상'},
  a:{down:'팔내림',one_up:'한팔올림',both_up:'양팔올림',crossed:'팔짱'},
  f:{front:'정면',side:'측면',unknown:'방향미상'}};
// 자세(p)가 측정이 아닌 추정(geom/label)이고 미상이 아니면 true → '추정' 배지용
const pInfer=at=>at&&at.ps&&at.ps!=='measured'&&at.p!=='unknown';
const pKo=at=>`${POSE_ATTR_KO.p[at.p]||at.p}${pInfer(at)?'<i class="est">추정</i>':''}`;
// pose_index.knn([[id,dist],..]) → 이웃 id 배열로 정규화
// 별자리용 이웃맵: 순수 포즈거리 상위 6 고정(별자리 층 크기 유지 — 재랭킹은 포즈뷰어/겹쳐보기만)
const POSE_NB=(()=>{const o={};if(PIDX&&PIDX.knn)for(const k in PIDX.knn)o[k]=PIDX.knn[k].slice(0,6).map(x=>x[0]);return o;})();
// 라벨 가중 재랭킹(A안): 포즈거리 후보(K=24)를 검수 반영 라벨(curLabels) 일치도로 재정렬
let _lblWeight=true;
const _LBL_LAMBDA=0.25,_DMAX=(PIDX&&PIDX.meta&&PIDX.meta.dist_max)||0.35;
function _jac(x,y){const X=new Set(x||[]),Y=new Set(y||[]);let i=0;X.forEach(v=>{if(Y.has(v))i++;});const u=X.size+Y.size-i;return u?i/u:0;}
function labelSim(a,b){const A=curLabels(a),B=curLabels(b);
  return 0.4*_jac(A.where,B.where)+0.4*_jac(A.pose_action,B.pose_action)
        +0.2*(A.shot_size&&A.shot_size===B.shot_size?1:0);}
function lblMatchTags(a,b){const A=curLabels(a),B=curLabels(b);const t=[];
  if((A.where||[]).some(v=>(B.where||[]).includes(v)))t.push('장소');
  if((A.pose_action||[]).some(v=>(B.pose_action||[]).includes(v)))t.push('포즈');
  if(A.shot_size&&A.shot_size===B.shot_size)t.push('프레이밍');return t;}
function poseCands(id){const raw=((PIDX&&PIDX.knn&&PIDX.knn[id])||[]).filter(x=>!isDiscarded(x[0]));  // 버리기한 컷은 닮은 컷 후보에서 제외
  if(!_lblWeight)return raw;
  return raw.map(x=>[x[0],x[1],labelSim(id,x[0])])
    .sort((p,q)=>(p[1]/_DMAX-_LBL_LAMBDA*p[2])-(q[1]/_DMAX-_LBL_LAMBDA*q[2]));}
function renderBeta(){const el=document.getElementById('betamain');
  el.innerHTML=`<div class="beta">
    <div class="bcard main"><h3>✨ 유사 별자리 그래프 <small style="color:#889" id="conmodelbl"></small></h3>
      <canvas id="conCanvas"></canvas>
      <div id="coninfo"><div class="r" style="color:#889">노드에 마우스를 올리거나(모바일은 탭) <b>왜 엮였는지</b>(거리·공유 속성)를 보여줘요.</div></div>
      <div style="text-align:center;margin-top:8px">
        <button class="btn" id="conmode">🔀 CLIP↔포즈</button>
        <button class="btn" id="conback">◀ 뒤로</button>
        <button class="btn" id="conrand">🎲 랜덤 중심</button>
        <button class="btn" id="confBtn">🎉 컨페티</button></div>
      <div style="font-size:12px;color:#889;text-align:center;margin-top:6px">중심(노랑)에서 이웃→그이웃으로 퍼짐 · 멀수록 흐려짐 · 노드 드래그/탭=그 컷으로 이동 · 중심 탭=상세</div></div>
    <div class="bcard wide"><h3>🦴 포즈 뷰어 & 🎞️ 겹쳐보기 <small style="color:#777">(부위색 스켈레톤 · 닮은 컷 골반·몸통 정렬 겹침)</small></h3>
      <div class="poserow">
        <div class="posecol">
          <div class="posecap">스켈레톤 뷰어 <span style="color:#667">— 선택 포즈</span></div>
          <canvas id="poseCanvas"></canvas>
          <div class="poselegend"><span><i style="background:${POSE_COL.head}"></i>머리·목</span><span><i style="background:${POSE_COL.arm}"></i>팔</span><span><i style="background:${POSE_COL.torso}"></i>몸통</span><span><i style="background:${POSE_COL.leg}"></i>다리</span></div>
          <div id="poseattr" class="poseattr"></div></div>
        <div class="posecol">
          <div class="posecap">겹쳐보기 <span style="color:#667">— 닮은 컷 정렬·온온스킨</span></div>
          <canvas id="overlayCanvas"></canvas>
          <div class="ovlctrl">
            <button class="btn" id="ovlPlay">▶ 재생</button>
            <button class="btn on" id="ovlOnion">🧅 잔상</button>
            <input type="range" id="ovlScrub" min="0" max="0" value="0" step="1">
            <span class="ovlcap" id="ovlcap"></span></div></div>
      </div>
      <div style="text-align:center;margin:8px 0"><button class="btn" id="poserand">🎲 랜덤 포즈</button> <span style="font-size:11px;color:#889">썸네일/닮은컷 클릭 → 좌우 동시 갱신 · 겹쳐보기는 슬라이더·드래그·▶재생으로 넘기기</span></div>
      <div class="posesim-title">🎯 이 포즈와 <b>실제로</b> 닮은 컷 <small style="color:#777">(디스크립터 거리 + 라벨 가중)</small>
        <button class="btn on" id="lblw" style="margin-left:8px;font-size:11px;padding:2px 8px">🏷️ 라벨 가중</button></div>
      <div class="posesim" id="posesim"></div>
      <div class="posegrid" id="posegrid"></div></div>
  </div>`;
  betaPose();betaConstellation();betaDiversity();   // 장소별 통계는 상시 펼침, 보충 우선순위는 하단 토글에서 지연 렌더
  const lw=document.getElementById('lblw');if(lw){lw.classList.toggle('on',_lblWeight);
    lw.onclick=()=>{_lblWeight=!_lblWeight;lw.classList.toggle('on',_lblWeight);
      if(_poseCur)drawPose(_poseCur);toast(_lblWeight?'라벨 가중 추천 ON':'순수 포즈거리 추천');};}
  document.getElementById('confBtn').onclick=()=>confetti();
  wireBetaToggles();
  if(_betaResize)window.removeEventListener('resize',_betaResize);
  _betaResize=()=>{if(_poseCur)drawPose(_poseCur);if(_conResize)_conResize();};
  window.addEventListener('resize',_betaResize);}
// 베타 하단 접이식(장소별 통계·관계맵·조합) — 열 때 지연 렌더
function wireBetaToggles(){document.querySelectorAll('#tab-beta .tgh').forEach(h=>{
  h.onclick=()=>{const body=document.getElementById(h.dataset.body);const open=body.style.display==='block';
    body.style.display=open?'none':'block';h.querySelector('.tgl').textContent=open?'▶ 열기':'▼ 닫기';
    if(!open){const r=h.dataset.render;
      if(r==='map')renderMap(); else if(r==='combo')renderCombos(); else if(r==='insight')betaInsight();}};});}
/* 베타 교차분석 — 두 축의 조합 커버리지. 어느 축인지는 taxonomy._semantic.pair 가 정한다.
   (선언이 없으면 커버리지 축 중 멀티축 2개를 자동으로 고른다. 축이 1개뿐이면 패널을 숨긴다) */
const _cellVals=(L,ax)=>{const v=L[ax];return Array.isArray(v)?v:(v?[v]:[]);};
function pairCounts(){const cc={};for(const id of IDS){if(decisionOf(id)==='discard')continue;const L=curLabels(id);
  for(const x of _cellVals(L,PAIR_X))for(const y of _cellVals(L,PAIR_Y))cc[x+'|'+y]=(cc[x+'|'+y]||0)+1;}return cc;}
function pairFilter(x,y,msg){                    // 두 축의 한 조합만 검수에서 보기
  selFilterIds=null;AXMETA.forEach(a=>active[a.key].clear());
  active[PAIR_X].add(x);active[PAIR_Y].add(y);
  document.querySelectorAll('#filters .chip').forEach(c=>c.classList.remove('active'));
  [[PAIR_X,x],[PAIR_Y,y]].forEach(([a,code])=>{const e=document.querySelector(`#filters .chip[data-ax="${a}"][data-code="${code}"]`);if(e)e.classList.add('active');});
  document.querySelector('.nav .tab[data-tab="review"]').click();applyFilter();if(msg)toast(msg);}

function betaInsight(){
  const box=document.getElementById('binsight');if(!box)return;
  if(!PAIR_X||!PAIR_Y||PAIR_X===PAIR_Y){box.innerHTML='<div class="sum">교차 분석할 축이 2개 미만입니다.</div>';return;}
  const TARGET=TK_MIN,cc=pairCounts();
  const rows=[];let empty=0,low=0;
  for(const x of Object.keys(TAX[PAIR_X]))for(const y of Object.keys(TAX[PAIR_Y])){
    const n=cc[x+'|'+y]||0;if(n===0)empty++;else if(n<TARGET)low++;rows.push({x,y,n});}
  rows.sort((a,b)=>a.n-b.n);const top=rows.slice(0,6);
  const sum=`<div class="sum">총 <b>${IDS.length}장</b> · 빈 조합 <b>${empty}</b>개 · 부족(&lt;${TARGET}) <b>${low}</b>개 — 아래 순서로 채우면 균형↑</div>`;
  const li=top.map((r,i)=>{const need=Math.max(0,TARGET-r.n),pct=Math.min(100,Math.round(100*r.n/TARGET));
    return `<li class="${r.n===0?'hot':''}"><span class="rk">${i+1}</span>`+
      `<span class="nm">${TAX[PAIR_X][r.x]} × ${TAX[PAIR_Y][r.y]}</span>`+
      `<span class="gap"><span style="width:${pct}%"></span></span>`+
      `<span class="ct">${r.n}/${TARGET}${need?' · '+need+'장↑':' ✓'}</span></li>`;}).join('');
  box.innerHTML=sum+`<ol class="prio">${li}</ol>`;
  box.querySelectorAll('.prio li').forEach((el,i)=>{const r=top[i];
    el.onclick=()=>pairFilter(r.x,r.y,r.n===0?'이 조합은 아직 0장 — 촬영/수집 대상':'');});}

// 축 X 별 축 Y 구성 — 대표 사진 막대(넓을수록 많고·밝을수록 대표·%는 사진 위 오버레이)
let _divSort='skew';
function betaDiversity(){
  const box=document.getElementById('divbox');if(!box)return;
  if(!PAIR_X||!PAIR_Y||PAIR_X===PAIR_Y){box.innerHTML='<div class="sum">교차 분석할 축이 2개 미만입니다.</div>';return;}
  const PK=Object.keys(TAX[PAIR_Y]);
  const wp={},rep={};
  for(const id of IDS){if(decisionOf(id)==='discard')continue;const L=curLabels(id),ip=repScore(id);
    for(const x of _cellVals(L,PAIR_X))for(const y of _cellVals(L,PAIR_Y)){
      (wp[x]=wp[x]||{});wp[x][y]=(wp[x][y]||0)+1;
      const k=x+'|'+y;if(!(k in rep)||ip>rep[k].ip)rep[k]={id,ip};}}
  let locs=Object.keys(TAX[PAIR_X]).map(w=>{const dist=wp[w]||{};const tot=Object.values(dist).reduce((a,b)=>a+b,0);
    const sorted=Object.entries(dist).sort((a,b)=>b[1]-a[1]);
    return {w,tot,sorted,skew:tot?sorted[0][1]/tot:0,kinds:sorted.length};});
  const sorters={skew:(a,b)=>b.skew-a.skew, tot:(a,b)=>b.tot-a.tot, name:(a,b)=>0};
  locs.sort(sorters[_divSort]||sorters.skew);
  const blocks=locs.map(L=>{const {w,tot,sorted,skew}=L;
    if(!tot)return `<div class="locblock empty"><div class="lochead">${TAX[PAIR_X][w]} <span class="tot">0장</span></div></div>`;
    const maxp=sorted[0][1]/tot,sk=Math.round(skew*100);
    const tiles=sorted.map(([p,n],i)=>{const pct=n/tot,pctR=Math.round(pct*100);
      const veil=(0.12+0.60*(1-pct/maxp)).toFixed(2);const id=(rep[w+'|'+p]||{}).id;
      return `<div class="ptile" data-pose="${p}" data-where="${w}" data-g="${(pct*100).toFixed(2)}" style="flex-grow:0;background-image:url(${id?IMAGES[id]:''})" title="${TAX[PAIR_X][w]} · ${TAX[PAIR_Y][p]} — ${n}장 (${pctR}%)">
        <div class="veil" style="background:rgba(6,8,12,${veil})"></div>${i===0?'<span class="crown">👑</span>':''}
        <div class="ptlbl"><b>${pctR}%</b><small>${TAX[PAIR_Y][p]}</small></div></div>`;}).join('');
    const badge=sk>=50?`<span class="skew">🎯 ${TAX[PAIR_Y][sorted[0][0]]} 쏠림 ${sk}%</span>`:`<span class="ok">다양 ${L.kinds}종</span>`;
    return `<div class="locblock"><div class="lochead">${TAX[PAIR_X][w]} <span class="tot">${tot}장</span>${badge}</div><div class="photobar">${tiles}</div></div>`;}).join('');
  box.innerHTML=`<div style="font-size:11px;color:#889;margin-bottom:8px">넓을수록 많고·밝을수록 대표 · <b>사진 클릭</b>=그 조합만 검수보기 · 아래 <b>범례에 마우스</b>=축별 위치 비교</div>${blocks}`;
  requestAnimationFrame(()=>box.querySelectorAll('.ptile').forEach(t=>t.style.flexGrow=t.dataset.g));
  box.querySelectorAll('.ptile').forEach(t=>t.onclick=()=>{const w=t.dataset.where,p=t.dataset.pose;
    pairFilter(w,p,`${TAX[PAIR_X][w]} × ${TAX[PAIR_Y][p]} 만 표시`);});
  const lg=document.getElementById('divlegend');
  if(lg){lg.innerHTML=PK.map(p=>`<span class="plg" data-pose="${p}">${TAX[PAIR_Y][p]}</span>`).join('')+
    `<span class="divsort"><button data-s="skew" class="${_divSort==='skew'?'on':''}">쏠림순</button><button data-s="tot" class="${_divSort==='tot'?'on':''}">장수순</button><button data-s="name" class="${_divSort==='name'?'on':''}">이름순</button></span>`;
    lg.querySelectorAll('.plg').forEach(el=>{const pc=el.dataset.pose;
      el.onmouseenter=()=>{el.classList.add('on');box.querySelectorAll('.ptile').forEach(t=>{const m=t.dataset.pose===pc;t.style.opacity=m?'1':'0.14';const v=t.querySelector('.veil');if(v&&m)v.style.opacity='0';});};
      el.onmouseleave=()=>{el.classList.remove('on');box.querySelectorAll('.ptile').forEach(t=>{t.style.opacity='1';const v=t.querySelector('.veil');if(v)v.style.opacity='1';});};});
    lg.querySelectorAll('.divsort button').forEach(b=>b.onclick=()=>{_divSort=b.dataset.s;betaDiversity();});}}
// 캔버스 버퍼를 CSS 레이아웃 크기에 맞춰 반응형으로(화면 폭 따라 조절)
function fitCanvas(cv){const w=cv.clientWidth,h=cv.clientHeight;if(w>0&&h>0){if(cv.width!==w)cv.width=w;if(cv.height!==h)cv.height=h;return true;}return false;}
function drawPose(id){const cv=document.getElementById('poseCanvas');if(!cv)return;_poseCur=id;fitCanvas(cv);const ctx=cv.getContext('2d');const W=cv.width,H=cv.height;
  const img=new Image();img.onload=()=>{const ar=img.width/img.height;let dw=W,dh=W/ar;if(dh>H){dh=H;dw=H*ar;}const ox=(W-dw)/2,oy=(H-dh)/2;
    ctx.clearRect(0,0,W,H);ctx.globalAlpha=0.72;ctx.drawImage(img,ox,oy,dw,dh);ctx.globalAlpha=1;const kp=POSES[id];if(!kp)return;
    const px=i=>ox+kp[i*3]*dw,py=i=>oy+kp[i*3+1]*dh,pv=i=>kp[i*3+2];
    ctx.lineWidth=4;ctx.lineCap='round';
    for(const g of POSE_GROUPS){ctx.strokeStyle=g.c;for(const c of g.e){if(pv(c[0])>0.3&&pv(c[1])>0.3){ctx.beginPath();ctx.moveTo(px(c[0]),py(c[0]));ctx.lineTo(px(c[1]),py(c[1]));ctx.stroke();}}}
    for(let i=0;i<33;i++){if(pv(i)>0.3&&POSE_PTCOL[i]){ctx.fillStyle=POSE_PTCOL[i];ctx.beginPath();ctx.arc(px(i),py(i),4.5,0,7);ctx.fill();}}};img.src=IMAGES[id];
  showPoseAttr(id);showPoseSim(id);if(document.getElementById('overlayCanvas'))betaOverlay(id);}
function showPoseAttr(id){const box=document.getElementById('poseattr');if(!box)return;const at=PIDX&&PIDX.attr&&PIDX.attr[id];
  if(!at){box.innerHTML='';return;}
  box.innerHTML=`<span>${pKo(at)}</span><span>${POSE_ATTR_KO.a[at.a]||at.a}</span><span>${POSE_ATTR_KO.f[at.f]||at.f}</span><span>기울기 ${at.l>0?'+':''}${at.l}°</span>`;}
function showPoseSim(id){const box=document.getElementById('posesim');if(!box)return;const nb=poseCands(id).slice(0,6);
  if(!nb.length){box.innerHTML='<span class="empty">이 포즈와 임계 이내로 닮은 컷이 없어요 (희귀 포즈)</span>';return;}
  box.innerHTML=nb.map(([nid,d])=>{const tags=_lblWeight?lblMatchTags(id,nid):[];
    return `<div class="ps" data-id="${nid}"><img src="${IMAGES[nid]}"><small>d ${d.toFixed(2)}${tags.length?` <i class="est">${tags.join('·')}</i>`:''}</small></div>`;}).join('');
  box.querySelectorAll('.ps').forEach(p=>p.onclick=()=>drawPose(p.dataset.id));}
function betaPose(){const ids=((PIDX&&PIDX.attr)?Object.keys(PIDX.attr):Object.keys(POSES)).filter(id=>!isDiscarded(id));const grid=document.getElementById('posegrid');
  if(!ids.length){grid.innerHTML='<span style="color:#888">포즈 데이터 없음</span>';return;}
  grid.innerHTML=ids.slice(0,60).map(id=>`<img src="${IMAGES[id]}" data-id="${id}">`).join('');
  grid.querySelectorAll('img').forEach(im=>im.onclick=()=>drawPose(im.dataset.id));
  document.getElementById('poserand').onclick=()=>drawPose(ids[Math.floor(Math.random()*ids.length)]);drawPose(ids[0]);}
// ---------- 포즈 겹쳐보기(스켈레톤 정렬 온온스킨 + 스크러버) ----------
let _ovlSeq=[],_ovlIdx=0,_ovlOnion=true,_ovlPlay=null,_ovlImgs={};
// 골반 중심을 캔버스 앵커로, 몸통 길이를 기준크기로 → 서로 다른 컷의 스켈레톤이 겹치게 정렬(등축 스케일, 왜곡 없음)
// 골반중심·몸통길이 등축 정렬의 공통 단위(u = 몸통길이 배수) 계산
function ovlUnits(id){const kp=POSES[id],im=_ovlImgs[id];if(!kp||!im||!im.complete||!im.width)return null;
  const v=i=>kp[i*3+2];if(v(23)<.3||v(24)<.3||v(11)<.3||v(12)<.3)return null;
  const hcx=(kp[23*3]+kp[24*3])/2,hcy=(kp[23*3+1]+kp[24*3+1])/2,scx=(kp[11*3]+kp[12*3])/2,scy=(kp[11*3+1]+kp[12*3+1])/2;
  const tl=Math.hypot((scx-hcx)*im.width,(scy-hcy)*im.height);if(tl<4)return null;
  return {kp,im,hcx,hcy,tl};}
// 셀 채움: 전 프레임 스켈레톤 union bbox(몸통 단위)를 여백 조금 남기고 꽉 차게 —
// 공통 스케일 k 하나만 쓰므로 프레임 간 등축 정렬은 그대로 유지(이미지는 잘려도 무방).
function ovlFit(W,H){let x0=1e9,y0=1e9,x1=-1e9,y1=-1e9,any=false;
  for(const id of _ovlSeq){const u=ovlUnits(id);if(!u)continue;any=true;
    for(let i=0;i<33;i++){if(u.kp[i*3+2]<.3)continue;
      const ux=(u.kp[i*3]-u.hcx)*u.im.width/u.tl,uy=(u.kp[i*3+1]-u.hcy)*u.im.height/u.tl;
      if(ux<x0)x0=ux;if(ux>x1)x1=ux;if(uy<y0)y0=uy;if(uy>y1)y1=uy;}}
  if(!any)return null;
  const M=16,k=Math.min((W-2*M)/Math.max(x1-x0,1e-6),(H-2*M)/Math.max(y1-y0,1e-6));
  return {k,ax:W/2-(x0+x1)/2*k,ay:H/2-(y0+y1)/2*k};}
function ovlXf(id,W,H,fit){const u=ovlUnits(id);if(!u||!fit)return null;
  const S=fit.k/u.tl,dw=u.im.width*S,dh=u.im.height*S;
  return {im:u.im,kp:u.kp,dx:fit.ax-u.hcx*dw,dy:fit.ay-u.hcy*dh,dw,dh};}
function ovlSkel(ctx,t,color,lw,dots){const {kp,dx,dy,dw,dh}=t,px=i=>dx+kp[i*3]*dw,py=i=>dy+kp[i*3+1]*dh,pv=i=>kp[i*3+2];
  ctx.lineCap='round';ctx.lineWidth=lw;
  for(const g of POSE_GROUPS){ctx.strokeStyle=color||g.c;for(const c of g.e){if(pv(c[0])>.3&&pv(c[1])>.3){ctx.beginPath();ctx.moveTo(px(c[0]),py(c[0]));ctx.lineTo(px(c[1]),py(c[1]));ctx.stroke();}}}
  if(dots)for(let i=0;i<33;i++){if(pv(i)>.3&&POSE_PTCOL[i]){ctx.fillStyle=POSE_PTCOL[i];ctx.beginPath();ctx.arc(px(i),py(i),4,0,7);ctx.fill();}}}
function drawOverlay(){const cv=document.getElementById('overlayCanvas');if(!cv)return;fitCanvas(cv);const ctx=cv.getContext('2d');const W=cv.width,H=cv.height;
  ctx.clearRect(0,0,W,H);
  if(!_ovlSeq.length){ctx.fillStyle='#667';ctx.font='14px system-ui';ctx.textAlign='center';ctx.fillText('닮은 컷이 없어요 (희귀 포즈) — 다른 포즈를 골라보세요',W/2,H/2);ctx.textAlign='start';return;}
  const cur=_ovlSeq[_ovlIdx];const fit=ovlFit(W,H);
  if(_ovlOnion)for(const id of _ovlSeq){if(id===cur)continue;const t=ovlXf(id,W,H,fit);if(t){ctx.globalAlpha=0.10;ctx.drawImage(t.im,t.dx,t.dy,t.dw,t.dh);}}
  ctx.globalAlpha=1;
  for(const id of _ovlSeq){if(id===cur)continue;const t=ovlXf(id,W,H,fit);if(t)ovlSkel(ctx,t,'rgba(150,162,184,0.22)',1.6,false);}
  const tc=ovlXf(cur,W,H,fit);
  if(tc){ctx.globalAlpha=0.92;ctx.drawImage(tc.im,tc.dx,tc.dy,tc.dw,tc.dh);
    ctx.globalAlpha=0.2;ovlSkel(ctx,tc,null,4,true);ctx.globalAlpha=1;}   // 스켈레톤 20% — 이미지 전환이 비쳐 보이게
  else{ctx.fillStyle='#889';ctx.font='13px system-ui';ctx.textAlign='center';ctx.fillText('이 컷은 정렬 기준(골반·어깨)이 안 보여요',W/2,H/2);ctx.textAlign='start';}
  const cap=document.getElementById('ovlcap');if(cap){const d=((PIDX&&PIDX.knn&&PIDX.knn[_ovlSeq[0]])||[]).find(x=>x[0]===cur);
    cap.textContent=`${_ovlIdx+1} / ${_ovlSeq.length}`+(_ovlIdx===0?' · 기준 컷':(d?` · 포즈거리 ${d[1].toFixed(2)}`:''));}}
function ovlScrubAt(e,cv){const rc=cv.getBoundingClientRect();const f=Math.max(0,Math.min(1,(e.clientX-rc.left)/rc.width));
  const i=Math.round(f*Math.max(0,_ovlSeq.length-1));if(i!==_ovlIdx){_ovlIdx=i;const sc=document.getElementById('ovlScrub');if(sc)sc.value=i;drawOverlay();}}
function stopOvlPlay(){if(_ovlPlay){cancelAnimationFrame(_ovlPlay);_ovlPlay=null;const b=document.getElementById('ovlPlay');if(b)b.textContent='▶ 재생';}}
function toggleOvlPlay(){const b=document.getElementById('ovlPlay');if(!b)return;if(_ovlPlay){stopOvlPlay();return;}
  if(_ovlSeq.length<2)return;b.textContent='⏸ 정지';let acc=0;
  const step=()=>{acc++;if(acc%34===0){_ovlIdx=(_ovlIdx+1)%_ovlSeq.length;const sc=document.getElementById('ovlScrub');if(sc)sc.value=_ovlIdx;drawOverlay();}_ovlPlay=requestAnimationFrame(step);};
  _ovlPlay=requestAnimationFrame(step);}
function betaOverlay(id){const cv=document.getElementById('overlayCanvas');if(!cv)return;stopOvlPlay();
  const nb=poseCands(id).map(x=>x[0]);
  _ovlSeq=[id,...nb].filter(x=>POSES[x]).slice(0,9);_ovlIdx=0;
  _ovlSeq.forEach(x=>{if(!_ovlImgs[x]){const im=new Image();im.onload=()=>drawOverlay();im.src=IMAGES[x];_ovlImgs[x]=im;}});
  const sc=document.getElementById('ovlScrub');if(sc){sc.max=Math.max(0,_ovlSeq.length-1);sc.value=0;sc.oninput=()=>{stopOvlPlay();_ovlIdx=+sc.value;drawOverlay();};}
  const on=document.getElementById('ovlOnion');if(on){on.classList.toggle('on',_ovlOnion);on.onclick=()=>{_ovlOnion=!_ovlOnion;on.classList.toggle('on',_ovlOnion);drawOverlay();};}
  const pb=document.getElementById('ovlPlay');if(pb)pb.onclick=()=>toggleOvlPlay();
  cv.onpointerdown=e=>{try{cv.setPointerCapture(e.pointerId);}catch(_){}cv._drag=true;stopOvlPlay();ovlScrubAt(e,cv);e.preventDefault();};
  cv.onpointermove=e=>{if(cv._drag)ovlScrubAt(e,cv);};
  cv.onpointerup=e=>{cv._drag=false;try{cv.releasePointerCapture(e.pointerId);}catch(_){}};
  drawOverlay();}
let _conRAF=null,_poseCur=null,_conResize=null,_betaResize=null;
function betaConstellation(){const cv=document.getElementById('conCanvas');if(!cv)return;
  const ctx=cv.getContext('2d');
  if(_conRAF)cancelAnimationFrame(_conRAF);   // 이전 렌더의 물리 루프 중단(재진입 누수 방지)
  fitCanvas(cv);let W=cv.width||900,H=cv.height||520;
  const CLIP_NB=(EMB&&EMB.knn)||{};
  const havePose=Object.keys(POSE_NB).length>0, haveClip=Object.keys(CLIP_NB).length>0;
  const imgs={};const preload=list=>list.forEach(id=>{if(id&&!imgs[id]){const im=new Image();im.src=IMAGES[id];imgs[id]=im;}});
  let mode=havePose?'pose':'clip';   // 요구사항: 기본은 '실제 포즈' 유사
  const src=()=>mode==='pose'?POSE_NB:CLIP_NB;
  const keys=()=>Object.keys(src());
  const pickKeys=()=>keys().filter(x=>!isDiscarded(x));   // 중심 후보(랜덤/초기/모드전환)에서 버리기 제외
  let center=null,nodes=[],edges=[],hist=[];
  const lbl=document.getElementById('conmodelbl');
  const setLbl=()=>{if(lbl)lbl.textContent='('+(mode==='pose'?'🎯 실제 포즈 유사':'🖼️ CLIP 시각 유사')+' · 중심에서 퍼지는 별자리)';};
  // 방사형: 중심(hop0)→직접 이웃(hop1)→그 이웃(hop2). 부모-자식 트리 간선만 = 얽힘 최소·중심에서 퍼지는 느낌.
  function buildGraph(rootId){const S=src();const set=new Set([rootId]);const arr=[{id:rootId,hop:0,parent:null}];
    const nb=id=>(S[id]||[]).filter(x=>!isDiscarded(x));   // 버리기한 이웃은 별자리에서 제외
    const L1=[];for(const x of nb(rootId).slice(0,6)){if(!set.has(x)){set.add(x);L1.push(x);arr.push({id:x,hop:1,parent:rootId});}}
    for(const p of L1){for(const x of nb(p).slice(0,3)){if(set.size>=16)break;if(!set.has(x)){set.add(x);arr.push({id:x,hop:2,parent:p});}}}
    return arr;}
  function build(id){const arr=buildGraph(id);const old={};nodes.forEach(n=>old[n.id]=n);
    // 타원 배치(x=폭, y=높이). 링 간격은 이미지가 겹치지 않을 만큼. 최종 겹침 방지는 step()의 충돌 분리가 보장.
    const cx=W/2,cy=H/2,RX1=W*0.15,RY1=H*0.18,RX2=W*0.3,RY2=H*0.34;
    const L1=arr.filter(a=>a.hop===1),ang={};
    L1.forEach((a,i)=>ang[a.id]=-Math.PI/2+2*Math.PI*i/Math.max(1,L1.length));
    const byPar={};arr.filter(a=>a.hop===2).forEach(a=>(byPar[a.parent]=byPar[a.parent]||[]).push(a));
    Object.keys(byPar).forEach(p=>{const ch=byPar[p],base=ang[p]||0,sp=0.6;
      ch.forEach((a,i)=>ang[a.id]=base+(ch.length<2?0:-sp+2*sp*i/(ch.length-1)));});
    const idx={};arr.forEach((a,i)=>idx[a.id]=i);
    nodes=arr.map(a=>{const o=old[a.id],root=a.hop===0,rx=a.hop===1?RX1:RX2,ry=a.hop===1?RY1:RY2;
      const tx=root?cx:cx+Math.cos(ang[a.id])*rx,ty=root?cy:cy+Math.sin(ang[a.id])*ry;
      const sx=o?o.x:(old[a.parent]?old[a.parent].x:cx),sy=o?o.y:(old[a.parent]?old[a.parent].y:cy);
      return {id:a.id,x:sx,y:sy,tx,ty,hop:a.hop,parent:a.parent,r:root?62:a.hop===1?48:38,root};});
    edges=[];nodes.forEach(n=>{if(n.parent!=null&&idx[n.parent]!=null)edges.push([idx[n.parent],idx[n.id]]);});
    preload(arr.map(a=>a.id));hoverId=null;connSet=null;dragNode=null;}
  function go(id){if(center&&center!==id)hist.push(center);center=id;build(id);}
  let hoverId=null,dragNode=null,dragMoved=false,downX=0,downY=0,connSet=null;
  // 차분한 이징 + 간선 결합. 홈(방사 목표)으로 부드럽게 오되, 연결된 노드는 서로 상대위치를 유지하려 해서
  // 한 노드를 끌면 이웃이 딸려오고 놓으면 웹이 함께 되돌아간다(위치기반 완화 = 통통 튕김 없이 생동감).
  const EASE=0.10,LINK=0.26,GAP=8;
  function step(){for(const n of nodes){if(n===dragNode)continue;n.x+=(n.tx-n.x)*EASE;n.y+=(n.ty-n.y)*EASE;}
    // 간선 결합(드래그 시 이웃 딸림)
    for(const [i,j] of edges){const a=nodes[i],b=nodes[j];
      const dx=(a.x-b.x)-(a.tx-b.tx),dy=(a.y-b.y)-(a.ty-b.ty);
      if(a!==dragNode){a.x-=dx*LINK*0.5;a.y-=dy*LINK*0.5;}
      if(b!==dragNode){b.x+=dx*LINK*0.5;b.y+=dy*LINK*0.5;}}
    // 충돌 분리 — 이미지가 서로 겹치지 않게 밀어냄
    for(let i=0;i<nodes.length;i++)for(let k=i+1;k<nodes.length;k++){const a=nodes[i],b=nodes[k];
      let dx=b.x-a.x,dy=b.y-a.y,d=Math.hypot(dx,dy)||0.01,mn=a.r+b.r+GAP;
      if(d<mn){const p=(mn-d)/d,ux=dx*p,uy=dy*p;
        if(a===dragNode){b.x+=ux;b.y+=uy;}else if(b===dragNode){a.x-=ux;a.y-=uy;}
        else{a.x-=ux*0.5;a.y-=uy*0.5;b.x+=ux*0.5;b.y+=uy*0.5;}}}
    // 캔버스 안으로
    for(const n of nodes){if(n===dragNode)continue;n.x=Math.max(n.r,Math.min(W-n.r,n.x));n.y=Math.max(n.r,Math.min(H-n.r,n.y));}}
  function cover(im,x,y,r){const ar=im.width/im.height;let dw=2*r,dh=2*r;if(ar>1)dw=2*r*ar;else dh=2*r/ar;ctx.drawImage(im,x-dw/2,y-dh/2,dw,dh);}
  const hopA=h=>h===0?1:h===1?0.84:0.42;   // 중심에서 멀수록 투명(눈 피로↓·포커스↑)
  const nodeAt=(mx,my)=>{let best=null;for(const n of nodes){if((mx-n.x)**2+(my-n.y)**2<n.r*n.r&&(!best||n.hop<best.hop))best=n;}return best;};
  function setConn(id){if(!id){connSet=null;return;}const s=new Set([id,center]);
    for(const [i,j] of edges){if(nodes[i].id===id)s.add(nodes[j].id);else if(nodes[j].id===id)s.add(nodes[i].id);}connSet=s;}
  function roundRect(x,y,w,h,r){ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath();}
  // 왜 엮였나 — 캔버스 오버레이 한 줄(공유 특징·거리). 패널 안 봐도 딱 이해.
  function shortWhy(nid){const ca=PIDX&&PIDX.attr&&PIDX.attr[center],na=PIDX&&PIDX.attr&&PIDX.attr[nid];
    if(mode==='pose'){const lst=(PIDX&&PIDX.knn&&PIDX.knn[center])||[],hit=lst.find(x=>x[0]===nid),sh=[];
      if(ca&&na)['p','a','f'].forEach(k=>{if(ca[k]===na[k]&&na[k]!=='unknown')sh.push(POSE_ATTR_KO[k][na[k]]);});
      const base=sh.length?sh.join('·'):'포즈 근접';return hit?`${base} · 거리 ${hit[1].toFixed(2)}`:`${base} · 간접`;}
    const lst=(EMB&&EMB.knn&&EMB.knn[center])||[],rank=lst.indexOf(nid);return rank>=0?`CLIP 유사 #${rank+1}`:'간접 유사';}
  function badge(cx,ty,text){ctx.font='bold 13px system-ui';const w=ctx.measureText(text).width+18,h=24,
    x=Math.max(4,Math.min(W-w-4,cx-w/2)),y=Math.max(4,ty-h);
    ctx.fillStyle='rgba(18,22,30,0.94)';ctx.strokeStyle=mode==='pose'?'#3f6a4a':'#3a4a6a';ctx.lineWidth=1.5;roundRect(x,y,w,h,7);ctx.fill();ctx.stroke();
    ctx.fillStyle='#eaf2ff';ctx.textAlign='left';ctx.textBaseline='middle';ctx.fillText(text,x+9,y+h/2+1);ctx.textAlign='start';ctx.textBaseline='alphabetic';}
  function draw(){ctx.clearRect(0,0,W,H);
    // 스포크(부모→자식). 깊을수록·멀수록 옅게 → 중심에서 퍼지는 결, 시선 집중.
    for(const [i,j] of edges){const a=nodes[i],b=nodes[j],deep=Math.max(a.hop,b.hop),touch=hoverId&&(a.id===hoverId||b.id===hoverId);
      let al=deep===1?0.5:0.2;if(connSet&&!touch)al*=0.35;if(touch)al=0.85;
      ctx.strokeStyle=touch?'rgba(255,210,74,'+al+')':(mode==='pose'?'rgba(90,180,120,'+al+')':'rgba(120,160,230,'+al+')');
      ctx.lineWidth=touch?2.4:1.3;ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.lineTo(b.x,b.y);ctx.stroke();}
    for(const n of nodes.slice().sort((a,b)=>b.hop-a.hop)){const im=imgs[n.id];let al=hopA(n.hop);if(connSet&&!connSet.has(n.id))al*=0.35;if(n.id===hoverId)al=1;
      ctx.globalAlpha=al;
      if(im&&im.complete&&im.width){ctx.save();ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,7);ctx.clip();cover(im,n.x,n.y,n.r);ctx.restore();}
      else{ctx.fillStyle='#1a1d24';ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,7);ctx.fill();}
      ctx.strokeStyle=n.root?'#ffd24a':(n.id===hoverId?'#ffd24a':(mode==='pose'?'#50b06a':'#5b8cff'));
      ctx.lineWidth=n.root?3:(n.id===hoverId?2.5:1.4);ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,7);ctx.stroke();}
    ctx.globalAlpha=1;
    if(hoverId){const n=nodes.find(x=>x.id===hoverId);if(n&&!n.root)badge(n.x,n.y-n.r-6,shortWhy(n.id));}}
  function loop(){step();draw();_conRAF=requestAnimationFrame(loop);}
  // 왜 엮였나: 거리·공유 속성(포즈모드) 또는 CLIP 순위(시각모드)
  function attrChips(id,cmp){const at=PIDX&&PIDX.attr&&PIDX.attr[id];if(!at)return '';
    return [['p',pKo(at)],['a',POSE_ATTR_KO.a[at.a]],['f',POSE_ATTR_KO.f[at.f]]].map(([k,t])=>{
      const hi=cmp&&cmp[k]===at[k]&&at[k]!=='unknown';return `<span class="chip${hi?' hi':''}">${t}</span>`;}).join('')
      +`<span class="chip">기울기 ${at.l>0?'+':''}${at.l}°</span>`;}
  function relText(nid){const ca=PIDX&&PIDX.attr&&PIDX.attr[center],na=PIDX&&PIDX.attr&&PIDX.attr[nid];
    if(mode==='pose'){const lst=(PIDX&&PIDX.knn&&PIDX.knn[center])||[],hit=lst.find(x=>x[0]===nid);
      const sh=[];if(ca&&na)['p','a','f'].forEach(k=>{if(ca[k]===na[k]&&na[k]!=='unknown')sh.push(POSE_ATTR_KO[k][na[k]]);});
      return (hit?`중심과 <b>직접 닮음</b> · 포즈거리 <b>${hit[1].toFixed(2)}</b>`:`중심의 <b>이웃의 이웃</b>(간접 연결)`)+(sh.length?` · 공유 ${sh.join('·')}`:'');}
    const lst=(EMB&&EMB.knn&&EMB.knn[center])||[],rank=lst.indexOf(nid);
    return rank>=0?`중심과 <b>CLIP 시각 유사</b> · 순위 #${rank+1}`:`중심 이웃과 시각 유사(간접)`;}
  function showConInfo(n){const box=document.getElementById('coninfo');if(!box)return;
    if(!n||n.root){const c=center,cnt=Math.max(0,nodes.length-1);
      box.innerHTML=`<img src="${IMAGES[c]}"><div class="r"><b>중심 컷</b> · 연결 ${cnt}개 <span style="color:#889">— 노드에 올리면 근거 표시</span><div class="why">${attrChips(c,null)}</div></div>`;return;}
    box.innerHTML=`<img src="${IMAGES[n.id]}"><div class="r"><div>${relText(n.id)}</div><div class="why">${attrChips(n.id,PIDX&&PIDX.attr&&PIDX.attr[center])}</div></div>`;}
  const XY=e=>{const rc=cv.getBoundingClientRect();return [(e.clientX-rc.left)*(W/rc.width),(e.clientY-rc.top)*(H/rc.height)];};
  // Pointer Events = 마우스·터치·펜 통합(모바일에서도 노드 드래그). setPointerCapture 로 캔버스 밖까지 추적.
  cv.onpointerdown=e=>{const [mx,my]=XY(e);const n=nodeAt(mx,my);if(n){dragNode=n;dragMoved=false;downX=mx;downY=my;
    cv.style.cursor='grabbing';try{cv.setPointerCapture(e.pointerId);}catch(_){}e.preventDefault();}};
  cv.onpointermove=e=>{const [mx,my]=XY(e);
    if(dragNode){dragNode.x=mx;dragNode.y=my;
      if((mx-downX)**2+(my-downY)**2>16)dragMoved=true;
      if(hoverId!==dragNode.id){hoverId=dragNode.id;setConn(dragNode.id);showConInfo(dragNode);}e.preventDefault();return;}
    const n=nodeAt(mx,my),id=n?n.id:null;cv.style.cursor=n?'grab':'default';
    if(id!==hoverId){hoverId=id;setConn(id);showConInfo(n);}};
  cv.onpointerup=e=>{if(dragNode){if(!dragMoved){if(dragNode.root)openPanel(dragNode.id);else{go(dragNode.id);showConInfo(null);}}
    dragNode=null;cv.style.cursor='grab';try{cv.releasePointerCapture(e.pointerId);}catch(_){}}};
  cv.onpointercancel=()=>{dragNode=null;};
  cv.onpointerleave=()=>{if(!dragNode){hoverId=null;connSet=null;showConInfo(null);cv.style.cursor='default';}};
  document.getElementById('conrand').onclick=()=>{const ks=pickKeys();if(ks.length){go(ks[Math.floor(Math.random()*ks.length)]);showConInfo(null);}};
  document.getElementById('conback').onclick=()=>{if(hist.length){center=hist.pop();build(center);showConInfo(null);}else toast('처음 별자리입니다');};
  document.getElementById('conmode').onclick=()=>{
    if(mode==='pose'&&!haveClip){toast('CLIP 관계맵 없음 — embed_map.py 필요');return;}
    if(mode==='clip'&&!havePose){toast('포즈 인덱스 없음 — pose_match.py 필요');return;}
    mode=mode==='pose'?'clip':'pose';setLbl();hist=[];
    let c=center;if(!src()[c]||isDiscarded(c))c=pickKeys()[0]||keys()[0];center=c;build(c);showConInfo(null);};
  _conResize=()=>{if(fitCanvas(cv)){W=cv.width;H=cv.height;if(center)build(center);}};
  setLbl();
  if(!keys().length){ctx.fillStyle='#888';ctx.font='14px system-ui';ctx.fillText('별자리 데이터 없음',W/2-70,H/2);return;}
  go(pickKeys()[0]||keys()[0]);showConInfo(null);loop();}
function confetti(){const cv=document.getElementById('confetti');if(!cv)return;cv.style.display='block';cv.width=window.innerWidth;cv.height=window.innerHeight;const ctx=cv.getContext('2d');
  const P=[];for(let i=0;i<170;i++)P.push({x:cv.width/2,y:cv.height/3,vx:(Math.random()-0.5)*15,vy:Math.random()*-13-4,c:PAL[i%PAL.length],s:4+Math.random()*6,life:1});
  let t=0;(function anim(){t++;ctx.clearRect(0,0,cv.width,cv.height);let alive=false;
    for(const p of P){p.vy+=0.35;p.x+=p.vx;p.y+=p.vy;p.life-=0.011;if(p.life>0){alive=true;ctx.globalAlpha=Math.max(0,p.life);ctx.fillStyle=p.c;ctx.fillRect(p.x,p.y,p.s,p.s);}}
    ctx.globalAlpha=1;if(alive&&t<170)requestAnimationFrame(anim);else cv.style.display='none';})();}

// ---------- 시작 ----------
document.getElementById('who').textContent=reviewer||'-';
// ---------- 포즈캠: 카메라 → BlazePose(JS) → 21d 디스크립터 → pose_index 실시간 매칭 ----------
// 파이썬 scripts/index/descriptor/pose_descriptor.py(v0.1)와 동일 수식 — PIDX.desc 와 같은 공간이어야 매칭 성립.
const PD_W=[1.5,1.5,1.5,1.5,1,1,1,1,1,1,1,1,1,1,1,1,1,1,.7,.7,.7];
const PD_VIS=.5, PD_MINV=13, PD_MINC=10;
const PD_LIVE_MINV=8, PD_ANCHOR_VIS=.15;   // 라이브 완화: 상반신만 보여도 매칭(엉덩이는 저신뢰 추정 좌표 허용)
function pdCompute(kp,live){               // kp: flat [x,y,vis]×33 · live=카메라(부분 신체 허용)
  const pt=i=>kp[i*3+2]>=PD_VIS?[kp[i*3],kp[i*3+1]]:null;
  // 좌표계 앵커(어깨·엉덩이)는 라이브에서 낮은 신뢰도의 '추정 좌표'도 허용 — MediaPipe 가 프레임 밖도 외삽함
  const anc=i=>kp[i*3+2]>=(live?PD_ANCHOR_VIS:PD_VIS)?[kp[i*3],kp[i*3+1]]:null;
  const f=new Array(21).fill(0);let valid=0;
  const ls=anc(11),rs=anc(12),lh=anc(23),rh=anc(24);
  if(!ls||!rs||!lh||!rh)return null;
  const hc=[(lh[0]+rh[0])/2,(lh[1]+rh[1])/2];
  const tx=(ls[0]+rs[0])/2-hc[0], ty=(ls[1]+rs[1])/2-hc[1];
  const bs=Math.hypot(tx,ty);if(bs<1e-6)return null;
  const theta=Math.atan2(tx,-ty);
  const c=Math.cos(-theta),s=Math.sin(-theta);
  const np_=i=>{const p=pt(i);if(!p)return null;
    const x=(p[0]-hc[0])/bs,y=(p[1]-hc[1])/bs;return [c*x-s*y,s*x+c*y];};
  const P={};[0,7,8,11,12,13,14,15,16,23,24,25,26,27,28].forEach(i=>P[i]=np_(i));
  const set=(i,v)=>{if(v!=null&&isFinite(v)){f[i]=v;valid|=(1<<i);}};
  const ang=(a,b,cc)=>{if(!a||!b||!cc)return null;
    const ux=a[0]-b[0],uy=a[1]-b[1],vx=cc[0]-b[0],vy=cc[1]-b[1];
    const nu=Math.hypot(ux,uy),nv=Math.hypot(vx,vy);if(nu<1e-9||nv<1e-9)return null;
    return Math.acos(Math.max(-1,Math.min(1,(ux*vx+uy*vy)/(nu*nv))));};
  const seg=(a,b)=>{if(!a||!b)return null;const dx=b[0]-a[0],dy=b[1]-a[1];
    return Math.hypot(dx,dy)<1e-9?null:Math.atan2(dx,-dy);};
  const A=(v)=>v==null?null:v/Math.PI;
  set(0,A(ang(P[11],P[13],P[15])));set(1,A(ang(P[12],P[14],P[16])));
  set(2,A(ang(P[23],P[25],P[27])));set(3,A(ang(P[24],P[26],P[28])));
  [[11,13],[12,14],[23,25],[24,26],[13,15],[14,16]].forEach(([a,b],k)=>{
    const g=seg(P[a],P[b]);if(g!=null){set(4+k*2,Math.sin(g));set(5+k*2,Math.cos(g));}});
  set(16,Math.sin(theta));set(17,Math.cos(theta));
  if(P[0]&&P[7]&&P[8]){const sw=Math.hypot(P[11][0]-P[12][0],P[11][1]-P[12][1]);
    if(sw>1e-6)set(18,Math.max(-1,Math.min(1,P[0][0]/sw)));}
  set(19,Math.min(1.5,Math.hypot(P[11][0]-P[12][0],P[11][1]-P[12][1]))/1.5);
  if(P[27]&&P[28])set(20,Math.min(2,Math.abs(P[27][0]-P[28][0]))/2);
  return {f,v:valid};
}
const PD_SWAPS=[[0,1],[2,3],[4,6],[5,7],[8,10],[9,11],[12,14],[13,15]],PD_NEG=[4,6,8,10,12,14,18];
function pdMirror(d){const f=d.f.slice();let v=d.v;
  for(const[i,j]of PD_SWAPS){const t=f[i];f[i]=f[j];f[j]=t;
    const bi=v>>i&1,bj=v>>j&1;v=(v&~(1<<i))&~(1<<j)|(bj<<i)|(bi<<j);}
  for(const i of PD_NEG)if(v>>i&1)f[i]=-f[i];
  return {f,v};}
function pdDistC(a,b,minc){let ws=0,d2=0,cnt=0;
  for(let i=0;i<21;i++)if((a.v>>i&1)&&(b.v>>i&1)){const w=PD_W[i];ws+=w;
    const df=a.f[i]-b.f[i];d2+=w*df*df;cnt++;}
  return (cnt<minc||ws<=0)?null:{d:Math.sqrt(d2/ws),cnt};}
function pdDist(a,b){const r=pdDistC(a,b,PD_MINC);return r?r.d:null;}
function pdMatch(live,k){                  // PIDX.desc 전수 + 미러 → top-k [id,score]
  if(!PIDX||!PIDX.desc)return [];
  // 부분 신체 대응: 라이브 유효 특징이 적으면 공통 하한도 함께 낮추되(6까지),
  // 공통 특징이 적은 매칭에는 페널티를 줘서 '몇 개만 우연히 비슷'이 상위로 오지 않게.
  const minc=Math.min(PD_MINC,Math.max(6,bitCount(live.v)-2));
  const mir=pdMirror(live),out=[];
  for(const id in PIDX.desc){const e=PIDX.desc[id];
    const r1=pdDistC(live,e,minc),r2=pdDistC(mir,e,minc);
    const r=(!r1)?r2:(!r2?r1:(r1.d<=r2.d?r1:r2));
    if(r)out.push([id, r.d + 0.05*(1-r.cnt/21)]);   // 공통특징 페널티(최대 +0.05)
  }
  out.sort((x,y)=>x[1]-y[1]);return out.slice(0,k||6);}

// --- 라벨 가중 재랭킹(A안을 포즈캠에 이식): 거리/DMAX − 0.25·라벨일치 ---
// 라이브 프레임엔 라벨이 없으므로 ① 포즈 top-8 후보의 검수 라벨에서 포즈·프레이밍 '합의'를 추정하고
// ② 장소는 사용자가 모달에서 직접 지정(포즈로는 장소를 알 수 없음). 가용 축만으로 정규화.
let _camLbl=true,_camWhere='';
let _camScaleLive=null,_camNum=null,_scaleCache=null;   // 거리감(화면 대비 인물 크기)·인원수
function subjScale(kp){                                  // flat [x,y,vis]×33 → 보이는 관절의 세로 점유율
  let mn=2,mx=-1,n=0;
  for(let i=0;i<33;i++){if(kp[i*3+2]<PD_VIS)continue;const y=kp[i*3+1];
    if(y<mn)mn=y;if(y>mx)mx=y;n++;}
  return n>=6?Math.max(0,Math.min(1,mx-mn)):null;}
function scaleOf(id){                                    // 데이터셋 컷의 인물 크기(POSES 캐시)
  if(!_scaleCache)_scaleCache={};
  if(!(id in _scaleCache))_scaleCache[id]=POSES[id]?subjScale(POSES[id]):null;
  return _scaleCache[id];}
// --- 프레이밍(거리감): 스켈레톤 세로크기는 외삽 탓에 무의미 → '어느 관절이 보이는가'로 판정(64% 실측) ---
function liveFraming(kp){                                 // 0=전신 1=반신 2=얼빡
  const v=i=>kp[i*3+2];
  if(Math.max(v(27),v(28))>=PD_VIS)return 0;              // 발목 보임=전신
  if(Math.max(v(25),v(26),v(23),v(24))>=PD_VIS)return 1;  // 무릎/골반 보임=반신
  return 2;}
// 프레이밍 매핑도 taxonomy._semantic.framing 에서(하드코딩 제거)
const _FR=(A.SCHEMA&&A.SCHEMA.semantic&&A.SCHEMA.semantic.framing)||null;
const _FR_AXIS=(_FR&&_FR.axis)||null;   // 선언 없으면 프레이밍 기능이 꺼진다
const _SHOT_IDX=_FR?{[_FR.full]:0,[_FR.half]:1,[_FR.closeup]:2}:{};
let _framingCache=null;
function framingOf(id){                                   // 데이터셋 컷: shot_size 라벨(신뢰) → 인덱스
  if(!_framingCache)_framingCache={};
  if(!(id in _framingCache)){const ss=curLabels(id)[_FR_AXIS];
    _framingCache[id]=(ss in _SHOT_IDX)?_SHOT_IDX[ss]:null;}
  return _framingCache[id];}
// --- 인물 방향(정면/측면/뒷모습) — 방향 불일치 컷은 사실상 걸러냄(최중요 축) ---
let _camDir=null,_dirVotes=[],_dirCache=null;
function liveFacing(kp){                                  // 어깨폭/몸통 비 + 얼굴 가시성 휴리스틱
  const v=i=>kp[i*3+2],x=i=>kp[i*3],y=i=>kp[i*3+1];
  if(v(11)<PD_ANCHOR_VIS||v(12)<PD_ANCHOR_VIS)return null;
  const sw=Math.abs(x(11)-x(12));
  const torso=Math.hypot((x(11)+x(12))/2-(x(23)+x(24))/2,(y(11)+y(12))/2-(y(23)+y(24))/2);
  if(torso<1e-4)return null;
  if(sw/torso<0.38)return 'side';                         // 어깨가 겹쳐 보임 = 옆모습
  const face=Math.max(v(0),v(2),v(5));                    // 코·양눈 — 안 보이면 뒷모습
  return face<0.45?'back':'front';}
function dirOf(id){                                       // 컷 방향: 검수 라벨(S10/S11/S14) 우선 → 포즈 attr 폴백
  if(!_dirCache)_dirCache={};
  if(!(id in _dirCache)){
    const FA=(A.SCHEMA&&A.SCHEMA.semantic&&A.SCHEMA.semantic.facing)||null;
    const cs=(FA&&FA.axis?curLabels(id)[FA.axis]:null)||[];let r=null;
    if(FA){if(FA.back&&cs.includes(FA.back))r='back';
      else if(FA.side&&cs.includes(FA.side))r='side';
      else if(FA.front&&cs.includes(FA.front))r='front';}
    else{const a=PIDX&&PIDX.attr&&PIDX.attr[id];
      if(a&&a.f==='front')r='front';else if(a&&a.f==='side')r='side';}
    _dirCache[id]=r;}
  return _dirCache[id];}
const CAM_DMAX=(PIDX&&PIDX.meta&&PIDX.meta.dist_max)||0.35;
/* 이웃 top-8 의 라벨 합의 — '이 장면은 아마 이런 라벨일 것이다'를 추정한다.
   어느 축을 쓸지는 taxonomy 가 정한다: 커버리지 축 전부를 대상으로 하되,
   멀티축은 3표 이상(여러 개 가능), 단일축은 과반(4표) 이어야 채택한다.
   pose_action/shot_size 같은 축 이름을 코드가 알 필요가 없다. */
function camConsensus(raw){
  const votes={};                                          // ax → {code: 표수}
  raw.slice(0,8).forEach(([id])=>{const L=curLabels(id);
    for(const ax of COMP){const v=L[ax];
      (Array.isArray(v)?v:(v?[v]:[])).forEach(c=>{(votes[ax]=votes[ax]||{});votes[ax][c]=(votes[ax][c]||0)+1;});}});
  const picked={},labels=[];
  for(const ax of COMP){
    const m=AXMETA.find(a=>a.key===ax),vs=votes[ax]||{};
    if(m&&m.multi){const cs=Object.keys(vs).filter(c=>vs[c]>=3);   // 8중 3표 이상 = 합의
      if(cs.length){picked[ax]=cs;cs.forEach(c=>labels.push(TAX[ax][c]||c));}}
    else{let best=null,bn=0;for(const c in vs)if(vs[c]>bn){bn=vs[c];best=c;}
      if(bn>=4){picked[ax]=best;labels.push(TAX[ax][best]||best);}}  // 단일축은 과반(4표)
  }
  // 포즈 보너스는 '포즈 축'이 아니라 '멀티 커버리지 축 합의' 전반에 적용된다
  const poses=[].concat(...COMP.filter(ax=>Array.isArray(picked[ax])).map(ax=>picked[ax]));
  return {picked,labels,poses};
}
// --- P1: MobileNet 시각 유사 (VIDX = 빌드타임에 '동일한 tfjs 모델'로 계산한 128d PCA 임베딩) ---
/* PCA 사영 인덱스(시각 신호) 공통 처리 — 어떤 시각 모델이든 스키마가 같으므로 코드도 하나다.
   MobileNet(visual) · DINOv2(dino) 가 같은 함수를 쓴다. 새 모델을 붙여도 여기는 안 고친다.
   갤러리 임베딩과 라이브 임베딩이 같은 공간에 있으려면 **같은 런타임·같은 전처리**로 뽑아야
   한다 — 그래서 오프라인 임베더가 파이썬이 아니라 Node 다(scripts/index/*_embed/). */
function b64i8(b){const s=atob(b),a=new Int8Array(s.length);for(let i=0;i<s.length;i++)a[i]=(s.charCodeAt(i)<<24)>>24;return a;}
const _sig={};                                                 // id → {emb, proj}
function sigInit(id){                                          // int8 양자화 해제(1회)
  const IDX=SIGS[id];
  if(_sig[id])return true;
  if(!IDX||!IDX.emb||!IDX.comps)return false;
  const D=IDX.dim,emb={};
  for(const k in IDX.emb){const e=IDX.emb[k],q=b64i8(e.b),f=new Float32Array(D);
    for(let i=0;i<D;i++)f[i]=q[i]*e.s;emb[k]=f;}
  const proj=IDX.comps.map((b,r)=>{const q=b64i8(b),f=new Float32Array(q.length);
    for(let i=0;i<q.length;i++)f[i]=q[i]*IDX.comps_s[r];return f;});
  _sig[id]={emb,proj};return true;}
function sigProject(id,raw){                                   // 라이브 원본 → (x−mean)·compsᵀ → L2
  const IDX=SIGS[id],S=_sig[id],D=IDX.dim;
  const e=new Float32Array(raw.length);
  for(let i=0;i<e.length;i++)e[i]=raw[i]-IDX.mean[i];
  const y=new Float32Array(D);let n2=0;
  for(let r=0;r<D;r++){const c=S.proj[r];let t=0;for(let i=0;i<c.length;i++)t+=c[i]*e[i];y[r]=t;n2+=t*t;}
  const n=Math.sqrt(n2)||1;for(let r=0;r<D;r++)y[r]/=n;return y;}
function visCos(a,b){let t=0;for(let i=0;i<a.length;i++)t+=a[i]*b[i];return t;}

// visual(MobileNet) — 기존 이름 유지(다른 코드가 참조). 내부는 위 공통 함수를 쓴다.
let _visEmb=null,_visModel=null,_visLive=null;
function visInit(){if(!sigInit('visual'))return false;_visEmb=_sig.visual.emb;return true;}
function visProject(raw){return sigProject('visual',raw);}

// dino(DINOv2) — 구도/구조 유사도에 강한 자기지도 모델. 시각 신호의 상위 호환.
// 실측(CLIP 이웃 재현율): MobileNet 39.6% → DINOv2 46.1%. 대신 100~300ms 라 스로틀이 필요.
let _dinoEmb=null,_dinoModel=null,_dinoLive=null,_dinoP=null,_dinoBusy=false,_dinoLast=0,_dinoReady=false,_dinoBackend='',_dinoPct=0;
/* 추출 간격은 **기기가 정한다.** 고정 800ms 로 두면 WebGPU 로 40ms 만에 끝나는 기기도
   800ms 를 기다리고(느려 보임), WASM 으로 400ms 걸리는 기기는 큐가 밀린다.
   실제 추론 시간을 재서 그 2배로 맞춘다 — 항상 여유 50% 를 남기므로 절대 밀리지 않는다.
   (실측: WASM/q4f16 386ms → 간격 ~770ms · WebGPU 라면 훨씬 촘촘해진다) */
let _dinoMs=0;                                                 // 최근 추론 소요(EMA)
const DINO_MIN=150, DINO_MAX=1500;
const dinoGap=()=>_dinoMs?Math.min(DINO_MAX,Math.max(DINO_MIN,_dinoMs*2)):600;
function dinoInit(){if(!sigInit('dino'))return false;_dinoEmb=_sig.dino.emb;return true;}
function dinoProject(raw){return sigProject('dino',raw);}
// --- 색감(Lab 3×3, 27d): 파이썬 color_index.py 와 동일 수식 — 모델 없이 순수 산술 ---
let _colEmb=null,_colLive=null,_colCv=null;
const COL_W=[.7,1.2,1.2];                                 // 색상(a·b) 강조, 밝기(L)는 완화
function srgb2lab(r,g,b){
  const l=u=>{u/=255;return u<=0.04045?u/12.92:Math.pow((u+0.055)/1.055,2.4);};
  const R=l(r),G=l(g),B=l(b);
  const X=(.4124564*R+.3575761*G+.1804375*B)/.95047,
        Y=(.2126729*R+.7151522*G+.0721750*B),
        Z=(.0193339*R+.1191920*G+.9503041*B)/1.08883;
  const d=Math.pow(6/29,3),f=t=>t>d?Math.cbrt(t):t/(3*Math.pow(6/29,2))+4/29;
  const fx=f(X),fy=f(Y),fz=f(Z);
  return [116*fy-16,500*(fx-fy),200*(fy-fz)];}
function colInit(){
  if(_colEmb||!CIDX)return !!_colEmb;
  _colEmb={};for(const id in CIDX.emb){const q=b64i8(CIDX.emb[id]),f=new Float32Array(27);
    for(let i=0;i<27;i++)f[i]=(i%3===0)?q[i]/1.27:q[i];_colEmb[id]=f;}
  return true;}
function liveColor(v){                                     // 카메라 프레임 → Lab 3×3 그리드 (~1ms)
  if(!_colCv){_colCv=document.createElement('canvas');_colCv.width=48;_colCv.height=60;}
  const g=_colCv.getContext('2d',{willReadFrequently:true});
  try{g.drawImage(v,0,0,48,60);}catch(e){return null;}
  const px=g.getImageData(0,0,48,60).data;
  const acc=new Float64Array(27),cnt=new Float64Array(9);
  for(let y=0;y<60;y++){const gy=Math.min(2,(y/20)|0);
    for(let x=0;x<48;x++){const gx=Math.min(2,(x/16)|0),c=gy*3+gx,o=(y*48+x)*4;
      const lab=srgb2lab(px[o],px[o+1],px[o+2]);
      acc[c*3]+=lab[0];acc[c*3+1]+=lab[1];acc[c*3+2]+=lab[2];cnt[c]++;}}
  const out=new Float32Array(27);
  for(let c=0;c<9;c++)for(let k=0;k<3;k++)out[c*3+k]=acc[c*3+k]/cnt[c];
  return out;}
function colorDist(a,b){let d2=0,ws=0;
  for(let i=0;i<27;i++){const w=COL_W[i%3];ws+=w;const df=a[i]-b[i];d2+=w*df*df;}
  return Math.sqrt(d2/ws);}                                // 대략 ΔE 스케일(0~60)
function colorTop(k){
  if(!_colLive||!_colEmb)return [];
  const out=[];for(const id in _colEmb)out.push([id,colorDist(_colLive,_colEmb[id])]);
  out.sort((x,y)=>x[1]-y[1]);return out.slice(0,k);}
function visTop(k){                                            // 시각 단독 top-k (포즈 실패 폴백)
  if(!_visLive||!_visEmb)return [];
  const out=[];for(const id in _visEmb)out.push([id,1-visCos(_visLive,_visEmb[id])]);
  out.sort((x,y)=>x[1]-y[1]);return out.slice(0,k);}

/* 매칭 v4 — 하드 필터(방향·프레이밍·장소) → 분위기(MobileNet) 랭킹.
   실측: 라이브 신호로 CLIP 별자리 결 직접재현 상한 ~14%(MobileNet≠CLIP). 대신 사람이 '일관'으로 느끼는
   축(거리감=프레이밍·방향·장소)을 필터로 고정하고 그 안을 분위기 중심 랭킹 → 묶음이 서로 닮음.
   프레이밍: 스켈레톤 크기(외삽 오염) 대신 '보이는 관절'로 판정, 전신↔얼빡만 배제(인접은 허용). */
/* 신호 가중치 — 손으로 찍지 않는다. signal_profile.json 의 live 블록을 읽는다.
   그 값은 scripts/check/tune_camw.js 가 **이 camMatch 를 그대로 통과시켜** 벤치 지표로
   최적화한 것이다. 오프라인(임베딩끼리의 거리) 최적값은 쓰지 않는다 — 하드필터·정규화·
   보너스를 거치고 나면 순위가 달라져 실제로는 더 나빠졌다(종합 62.7% → 60.8%).
   프로필이 없으면 폴백값으로 동작한다(기능 유지, 품질만 옛날 값). */
const CAM_W_FALLBACK={vis:.41,col:.46,pose:.13,dino:0};
function camW(){
  const w=SIGPROF&&SIGPROF.live&&SIGPROF.live.weights;
  return w?{vis:w.visual||0, col:w.color||0, pose:w.pose||0, dino:w.dino||0}:CAM_W_FALLBACK;
}
let _camFrame=null;                                        // 라이브 프레이밍 0/1/2
function camMatch(d){
  const ids=_visEmb?Object.keys(_visEmb):(_colEmb?Object.keys(_colEmb):[]);
  if(!ids.length)return {top:[],est:null,mode:'none',ctx:[]};
  const ctx=[];let cand=ids;
  if(_camDir){const f=cand.filter(id=>{const cd=dirOf(id);return !cd||cd===_camDir;});
    if(f.length>=20){cand=f;ctx.push({front:'정면',side:'측면',back:'뒷모습'}[_camDir]);}}
  if(_camFrame!=null){const f=cand.filter(id=>{const fr=framingOf(id);return fr==null||Math.abs(fr-_camFrame)<=1;});
    if(f.length>=20){cand=f;ctx.push(['전신','반신','얼빡'][_camFrame]+'권');}}
  if(_camLbl&&_camWhere&&PLACE_AXIS){const f=cand.filter(id=>{const v=curLabels(id)[PLACE_AXIS];
      return (Array.isArray(v)?v:(v?[v]:[])).includes(_camWhere);});
    if(f.length>=10){cand=f;ctx.push(TAX[PLACE_AXIS][_camWhere]);}}
  if(_camLbl&&_camNum!=null){const f=cand.filter(id=>{const pc=curLabels(id).person_count;
      return !pc||((_camNum>=2)===(pc!==COUNT_SINGLE));});
    if(f.length>=10){cand=f;ctx.push(_camNum>=2?'2인+':'1인');}}
  const poseD={};let raw=[];
  if(d){raw=pdMatch(d,80);raw.forEach(([id,ds])=>poseD[id]=ds/CAM_DMAX);}
  const est=raw.length?camConsensus(raw):null;
  const haveCol=!!(_colLive&&_colEmb), haveVis=!!(_visLive&&_visEmb), haveDino=!!(_dinoLive&&_dinoEmb);
  // 장소 추정: 시각 top-15 이웃의 '장소' 축 합의(과반) — 라이브엔 장소 라벨이 없으므로.
  // 어느 축이 장소인지는 taxonomy._semantic.place 가 정한다(미선언 도메인에선 이 기능만 꺼진다).
  let whereEst=null;
  if(haveVis&&!_camWhere&&PLACE_AXIS){const vt=visTop(15),wc={};
    vt.forEach(([id])=>{const v=curLabels(id)[PLACE_AXIS];
      (Array.isArray(v)?v:(v?[v]:[])).forEach(c=>wc[c]=(wc[c]||0)+1);});
    let mx=0;for(const c in wc)if(wc[c]>mx){mx=wc[c];whereEst=c;}
    if(mx<6)whereEst=null;}
  const W=camW();
  const scored=cand.map(id=>{
    const tags=[];let num=0,den=0;
    if(haveVis&&_visEmb[id]){const vN=1-visCos(_visLive,_visEmb[id]);
      num+=W.vis*Math.min(1.2,vN/0.6);den+=W.vis;if(vN<0.30)tags.push('분위기');}
    if(haveDino&&_dinoEmb[id]){const dN=1-visCos(_dinoLive,_dinoEmb[id]);   // 구도/구조 유사도
      num+=W.dino*Math.min(1.2,dN/0.6);den+=W.dino;if(dN<0.30)tags.push('구도');}
    if(haveCol&&_colEmb[id]){const cd=colorDist(_colLive,_colEmb[id]);
      num+=W.col*Math.min(1.2,cd/32);den+=W.col;if(cd<13)tags.push('색감');}
    const pN=poseD[id];
    if(pN!=null){num+=W.pose*Math.min(1.5,pN);den+=W.pose;if(pN<0.55)tags.push('포즈');}
    let score=den>0?num/den:1;
    if(_camFrame!=null){const fr=framingOf(id);
      if(fr!=null){if(fr===_camFrame)tags.push('거리감');score+=0.07*Math.abs(fr-_camFrame);}}
    // 라벨 합의 보너스 — 이웃들이 합의한 코드를 이 후보도 갖고 있으면 가점(축 이름 무관)
    if(_camLbl&&est&&est.poses.length){const L2=curLabels(id);
      const has=COMP.some(ax=>{const v=L2[ax];
        return (Array.isArray(v)?v:(v?[v]:[])).some(c=>est.poses.includes(c));});
      if(has)score-=0.03;}
    if(whereEst&&(curLabels(id).where||[]).includes(whereEst)){score-=0.10;if(!tags.includes('장소'))tags.push('장소');}
    const rep=(pN!=null)?pN*CAM_DMAX:(haveVis&&_visEmb[id]?(1-visCos(_visLive,_visEmb[id])):(haveCol&&_colEmb[id]?colorDist(_colLive,_colEmb[id])/32:0));
    return {id,d:rep,tags,score};
  });
  scored.sort((a2,b2)=>a2.score-b2.score);
  const mode=haveVis?'fuse':(haveCol?'vis':(raw.length?'pose':'none'));
  return {top:scored.slice(0,10),est,mode,ctx};
}

/* 랭킹 히스테리시스: 점수를 시간축 EMA — 후보가 틱마다 들락거리지 않게(일관성 수정 ②) */
let _camRank=new Map();
function camSmooth(top){
  const seen=new Set();
  top.forEach(e=>{seen.add(e.id);
    const prev=_camRank.get(e.id);
    _camRank.set(e.id,{e,score:prev?prev.score*.55+e.score*.45:e.score});});
  for(const[id,r]of _camRank){if(!seen.has(id)){r.score+=.035;if(r.score>2.5)_camRank.delete(id);}}
  return [..._camRank.values()].sort((a,b)=>a.score-b.score).slice(0,6).map(r=>r.e);
}

// --- 카메라 UI/루프 (모두 온디바이스 — 프레임은 기기 밖으로 나가지 않음) ---
const CAM_EDGES=[[11,12,'#e8c04a'],[11,23,'#e8c04a'],[12,24,'#e8c04a'],[23,24,'#e8c04a'],
  [11,13,'#5b8cff'],[13,15,'#5b8cff'],[12,14,'#7aa2ff'],[14,16,'#7aa2ff'],
  [23,25,'#43c07a'],[25,27,'#43c07a'],[24,26,'#5fd49a'],[26,28,'#5fd49a']];
// 모바일(터치·좁은 화면)이면 후면 카메라부터, 데스크톱은 전면(웹캠) — 미러도 그에 맞춰 초기화
const _camIsMobile=(()=>{try{return (navigator.maxTouchPoints>0&&innerWidth<820)||/Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent||'');}catch(e){return false;}})();
let _mpP=null,_camStream=null,_camRAF=null,_camFacing=_camIsMobile?'environment':'user',_camDevice='',_camEMA=null,_camLastInfer=0,_camLastStrip=0;
let _camMirror=!_camIsMobile;                          // 데스크톱 전면=미러(셀피 감각) · 모바일 후면=반전 해제 · 🪞 수동 전환
// 미러+줌 결합 CSS 변환. 하드웨어 줌(applyConstraints) 지원 시 프레임 자체가 확대되므로 CSS 배율은 1.
let _camZoom=1,_camZoomCap=null;        // _camZoomCap={min,max,step} (네이티브 지원 시)
function applyMirror(){
  const z=_camZoomCap?1:_camZoom, sx=(_camMirror?-1:1)*z;
  const t=`scale(${sx},${z})`;
  ['camv','camcv','camimg'].forEach(id=>{const el=document.getElementById(id);
    if(el){el.style.transformOrigin='center';el.style.transform=(id==='camimg'?`scale(${_camZoomCap?1:_camZoom})`:t);}});
}
function camSetZoom(z){
  if(_camZoomCap){z=Math.max(_camZoomCap.min,Math.min(_camZoomCap.max,z));_camZoom=z;
    try{_camStream&&_camStream.getVideoTracks()[0].applyConstraints({advanced:[{zoom:z}]});}catch(e){}}
  else{_camZoom=Math.max(1,Math.min(3.5,z));applyMirror();}
  const lb=document.getElementById('camzlbl');if(lb)lb.textContent=(_camZoomCap?_camZoom:_camZoom).toFixed(1)+'×';
}
function camZoomStep(dir){
  const step=_camZoomCap?(_camZoomCap.step||( (_camZoomCap.max-_camZoomCap.min)/10 )):0.4;
  camSetZoom(_camZoom+dir*step);
}
let _tfP=null;
function loadScript(u){return new Promise((res,rej)=>{const sc=document.createElement('script');
  sc.src=u;sc.onload=res;sc.onerror=rej;document.head.appendChild(sc);});}
function loadVisModel(){                               // tfjs + MobileNet v1 1.0 224 — 빌드 인덱스와 동일 모델
  if(_tfP)return _tfP;
  _tfP=(async()=>{
    if(!window.tf)await loadScript('https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@4.20.0/dist/tf.min.js');
    if(!window.mobilenet)await loadScript('https://cdn.jsdelivr.net/npm/@tensorflow-models/mobilenet@2.1.1/dist/mobilenet.min.js');
    _visModel=await mobilenet.load({version:1,alpha:1.0});
    visInit();return _visModel;})();
  _tfP.catch(()=>{_tfP=null;});
  return _tfP;}
/* DINOv2-small (transformers.js) — 오프라인 인덱스를 만든 것과 **같은 라이브러리·같은 모델**.
   전처리(리사이즈·크롭·정규화)가 같아야 갤러리 벡터와 라이브 벡터가 같은 공간에 놓인다.
   그래서 인덱스도 Node 의 transformers.js 로 뽑았다(scripts/index/dino_embed/).

   **dtype 은 인덱스가 정한다 — 절대 다른 걸로 대체하지 않는다.** 양자화 오차를 양쪽이
   똑같이 겪어야 같은 공간에 남기 때문이다. 인덱스가 fp32 인데 브라우저가 q4f16 을 쓰면
   코사인이 0.86 으로 떨어져(실측) 추천이 무너진다. 반대로 **양쪽 다 q4f16 이면 멀쩡하다**
   (CLIP 재현율 54.7% → 53.1%, −1.6%p 뿐인데 다운로드는 44MB → 14MB).
   그래서 dtype 이 안 맞으면 폴백하지 않고 **dino 를 끈다**(나머지 신호로 계속 동작).
   기기 백엔드(WebGPU/WASM)만 폴백한다 — 그건 벡터를 바꾸지 않는다.
   int8 은 onnxruntime-web 에 ConvInteger 구현이 없어 로드 자체가 실패한다. */
function loadDinoModel(){
  if(_dinoP)return _dinoP;
  if(!SIGS.dino){_dinoP=Promise.resolve(null);return _dinoP;}
  _dinoP=(async()=>{
    const T=await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.7.6');
    T.env.allowLocalModels=false;
    // WASM 멀티스레드는 SharedArrayBuffer 가 필요하고, 그건 cross-origin isolation 이 있어야
    // 켜진다(vercel.json 의 COOP/COEP 헤더). 되면 코어 수만큼 스레드를 쓴다 — 2~4배 빨라진다.
    // 안 되면(헤더가 없거나 브라우저 미지원) 조용히 싱글스레드로 동작한다.
    try{ if(self.crossOriginIsolated)
      T.env.backends.onnx.wasm.numThreads=Math.min(4,navigator.hardwareConcurrency||1); }catch(e){}
    const id='onnx-community/dinov2-small';
    // 44MB 를 받는 동안 침묵하면 '멈춘 것'처럼 보인다. 진행률을 상태줄에 흘린다.
    const prog=x=>{if(x.status==='progress'&&x.file&&x.file.endsWith('.onnx'))
      _dinoPct=Math.round(x.progress||0);
      if(x.status==='done'&&x.file&&x.file.endsWith('.onnx'))_dinoPct=100;};
    const proc=await T.AutoProcessor.from_pretrained(id);
    // 인덱스를 만든 것과 **똑같은** dtype·입력크기로 추론한다. 다른 값으로 대체하지 않는다.
    const dt=SIGS.dino.dtype||'fp16';
    if(SIGS.dino.input_size){
      proc.image_processor.size={shortest_edge:SIGS.dino.input_size};
      proc.image_processor.crop_size={height:SIGS.dino.input_size,width:SIGS.dino.input_size};
    }
    // navigator.gpu 가 **있어도** 어댑터를 못 얻는 기기가 있다(헤드리스·GPU 없는 노트북 등).
    // 'navigator.gpu 가 있으면 WebGPU' 로 단정하면 그런 기기에서 로드가 통째로 실패한다.
    // 그래서 어댑터를 실제로 요청해 성공할 때만 WebGPU 를 후보에 넣는다.
    // 기기 폴백(webgpu→wasm)은 벡터를 바꾸지 않으므로 안전하다. dtype 폴백은 안 한다.
    let gpuOK=false;
    try{gpuOK=!!(navigator.gpu&&await navigator.gpu.requestAdapter());}catch(e){}
    let model=null,err=null;
    for(const dev of (gpuOK?['webgpu','wasm']:['wasm'])){
      try{model=await T.AutoModel.from_pretrained(id,{dtype:dt,device:dev,progress_callback:prog});
        _dinoBackend=dev+'/'+dt;break;}
      catch(e){err=e;}
    }
    if(!model)throw err||new Error('dinov2 로드 실패');
    _dinoModel={T,proc,model};
    dinoInit();
    _dinoReady=true;
    return _dinoModel;})();
  _dinoP.catch(()=>{_dinoP=null;_dinoModel=null;});
  return _dinoP;}
async function dinoExtract(el){                        // <video>|<img> → 384d CLS → PCA 사영
  if(!_dinoModel||!_dinoEmb)return null;
  const {T,proc,model}=_dinoModel;
  const img=await T.RawImage.fromCanvas?T.RawImage.fromCanvas(_dinoCv(el)):null;
  if(!img)return null;
  const out=await model(await proc(img));
  const h=out.last_hidden_state, D=h.dims[2], v=new Float32Array(D);
  for(let i=0;i<D;i++)v[i]=h.data[i];                   // CLS 토큰
  let n=0;for(let i=0;i<D;i++)n+=v[i]*v[i];n=Math.sqrt(n)||1;
  for(let i=0;i<D;i++)v[i]/=n;                          // Node 임베더와 동일하게 L2 정규화
  return dinoProject(v);}
let _dcv=null;
function _dinoCv(el){                                   // 비디오/이미지 → 캔버스(RawImage 입력용)
  if(!_dcv)_dcv=document.createElement('canvas');
  const w=el.videoWidth||el.naturalWidth||el.width, h=el.videoHeight||el.naturalHeight||el.height;
  _dcv.width=w;_dcv.height=h;_dcv.getContext('2d').drawImage(el,0,0,w,h);
  return _dcv;}
function loadPoseModel(){if(_mpP)return _mpP;
  _mpP=import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs')
    .then(async m=>{
      const files=await m.FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
      return m.PoseLandmarker.createFromOptions(files,{
        baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task'},
        runningMode:'VIDEO',numPoses:2});})   // 인원(1/2인) 구분용
    .catch(e=>{_mpP=null;throw e;});
  return _mpP;}
async function camStart(){
  const v=document.getElementById('camv');
  camStopStream();
  const cons={video:_camDevice?{deviceId:{exact:_camDevice}}:{facingMode:_camFacing,width:{ideal:960}},audio:false};
  _camStream=await navigator.mediaDevices.getUserMedia(cons);
  v.srcObject=_camStream;await v.play();
  // 카메라 목록 채우기(권한 후에만 라벨이 보임)
  const sel=document.getElementById('camsel');
  const devs=(await navigator.mediaDevices.enumerateDevices()).filter(d=>d.kind==='videoinput');
  sel.innerHTML=devs.map((d,i)=>`<option value="${d.deviceId}">${d.label||('카메라 '+(i+1))}</option>`).join('');
  const cur=_camStream.getVideoTracks()[0].getSettings().deviceId;if(cur)sel.value=cur;
  const track=_camStream.getVideoTracks()[0];
  const fm=track.getSettings().facingMode;
  if(fm)_camMirror=(fm==='user');                      // 후면(environment)이면 자동으로 반전 해제
  // 줌 능력 감지: 하드웨어 줌 지원(주로 모바일)이면 그걸, 아니면 CSS 줌으로 폴백
  _camZoom=1;_camZoomCap=null;
  try{const cap=track.getCapabilities&&track.getCapabilities();
    if(cap&&cap.zoom&&cap.zoom.max>cap.zoom.min){_camZoomCap={min:cap.zoom.min,max:cap.zoom.max,step:cap.zoom.step||0};
      _camZoom=track.getSettings().zoom||cap.zoom.min;}}catch(e){}
  applyMirror();
  const lb=document.getElementById('camzlbl');if(lb)lb.textContent=_camZoom.toFixed(1)+'×';
}
function camStopStream(){if(_camStream){_camStream.getTracks().forEach(t=>t.stop());_camStream=null;}}
function camClose(){if(_camRAF)cancelAnimationFrame(_camRAF);_camRAF=null;camStopStream();
  document.getElementById('camwrap').hidden=true;}
function camDrawSkel(lm,media){
  const v=media||document.getElementById('camv'),cv=document.getElementById('camcv');
  if(cv.width!==v.clientWidth||cv.height!==v.clientHeight){cv.width=v.clientWidth;cv.height=v.clientHeight;}
  const g=cv.getContext('2d');g.clearRect(0,0,cv.width,cv.height);
  if(!lm)return;
  // object-fit:contain 보정
  const vw=v.videoWidth||v.naturalWidth||1,vh=v.videoHeight||v.naturalHeight||1,scale=Math.min(cv.width/vw,cv.height/vh);
  const dw=vw*scale,dh=vh*scale,ox=(cv.width-dw)/2,oy=(cv.height-dh)/2;
  const X=i=>ox+lm[i].x*dw,Y=i=>oy+lm[i].y*dh,V=i=>(lm[i].visibility==null?1:lm[i].visibility);
  g.lineWidth=3;g.lineCap='round';g.shadowBlur=8;
  for(const[a,b,c]of CAM_EDGES){if(V(a)<PD_VIS||V(b)<PD_VIS)continue;
    g.strokeStyle=c;g.shadowColor=c;g.globalAlpha=.9;
    g.beginPath();g.moveTo(X(a),Y(a));g.lineTo(X(b),Y(b));g.stroke();}
  g.globalAlpha=1;g.shadowBlur=0;g.fillStyle='#fff';
  for(const i of[0,11,12,13,14,15,16,23,24,25,26,27,28])if(V(i)>=PD_VIS){
    g.beginPath();g.arc(X(i),Y(i),3,0,7);g.fill();}
}
function camRenderStrip(top){
  const el=document.getElementById('camrec');
  if(!top.length){el.innerHTML='<span class="empty">닮은 컷을 찾는 중…</span>';return;}
  el.innerHTML=top.map(e=>{
    const src=IMAGES[e.id];if(!src)return '';
    const dd=(e.d!=null&&isFinite(e.d))?('거리 '+(+e.d).toFixed(2)):'유사';
    return `<div class="ck" data-id="${e.id}"><img src="${src}">`+
      `<small>${dd}</small>${e.tags&&e.tags.length?`<span class="tg">✓ ${e.tags.join('·')}</span>`:''}</div>`;}).join('');
  el.querySelectorAll('.ck').forEach(k=>k.onclick=()=>{camClose();goPhoto(k.dataset.id);});
}
async function camOpen(){
  const wrap=document.getElementById('camwrap');wrap.hidden=false;
  _camMode='live';                                       // 업로드 상태였으면 라이브 UI로 복귀
  document.getElementById('camimg').hidden=true;document.getElementById('camv').hidden=false;
  document.getElementById('camlive').hidden=true;document.getElementById('camimgx').hidden=true;
  ['camflip','cammir','camsel'].forEach(id=>document.getElementById(id).style.display='');
  const stat=document.getElementById('camstat');
  if(!(PIDX&&PIDX.desc&&Object.keys(PIDX.desc).length)){stat.textContent='포즈 인덱스 없음 — pose_match.py 재생성 필요';return;}
  if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){stat.textContent='이 브라우저는 카메라를 지원하지 않습니다 (HTTPS 필요)';return;}
  stat.textContent='카메라 권한 요청 중…';
  try{await camStart();}catch(e){stat.textContent='카메라 접근 실패: '+e.name;return;}
  stat.textContent='포즈 모델 로딩 중… (최초 1회, ~5MB)';
  _camRank=new Map();_camEMA=null;_visLive=null;_camScaleLive=null;_camNum=null;_colLive=null;_camDir=null;_dirVotes=[];_camFrame=null;
  _dinoLive=null;_dinoLast=0;
  if(VIDX)loadVisModel().catch(()=>{});                 // 시각 모델은 백그라운드 병렬(실패해도 포즈로 동작)
  if(SIGS.dino)loadDinoModel().catch(()=>{});           // DINOv2 도 병렬(실패해도 나머지 신호로 동작)
  let lm;try{lm=await loadPoseModel();}catch(e){stat.textContent='모델 로딩 실패 — 인터넷 연결 확인';return;}
  stat.textContent='포즈를 취해보세요';
  const v=document.getElementById('camv');
  const loop=()=>{ _camRAF=requestAnimationFrame(loop);
    if(v.readyState<2)return;
    const now=performance.now();
    if(now-_camLastInfer<120)return;            // 추론 ~8fps 스로틀(표시는 60fps)
    _camLastInfer=now;
    let res;try{res=lm.detectForVideo(v,now);}catch(e){return;}
    const lms=res.landmarks&&res.landmarks[0];
    _camNum=res.landmarks?res.landmarks.length:null;      // 감지된 인원수(최대 2)
    camDrawSkel(lms);
    let d=null,nv=0;
    if(lms){
      const kp=[];for(let i=0;i<33;i++){const p=lms[i];kp.push(p.x,p.y,p.visibility==null?1:p.visibility);}
      _camFrame=liveFraming(kp);                          // 프레이밍(전신/반신/얼빡) — 보이는 관절 기준
      const sc=subjScale(kp);
      if(sc!=null)_camScaleLive=_camScaleLive==null?sc:_camScaleLive*.6+sc*.4;
      const fc=liveFacing(kp);                            // 방향: 최근 5프레임 다수결
      if(fc){_dirVotes.push(fc);if(_dirVotes.length>5)_dirVotes.shift();
        const cnt={};_dirVotes.forEach(x=>cnt[x]=(cnt[x]||0)+1);
        _camDir=Object.keys(cnt).sort((a,b)=>cnt[b]-cnt[a])[0];}
      d=pdCompute(kp,true);nv=d?bitCount(d.v):0;
      if(d&&nv<PD_LIVE_MINV)d=null;
      // EMA 스무딩(일관성 수정 ①): 마스크 전체 일치 조건 제거 — 두 프레임 모두 유효한 '특징별'로 블렌드
      if(d){if(_camEMA)for(let i=0;i<21;i++)if((d.v>>i&1)&&(_camEMA.v>>i&1))d.f[i]=_camEMA.f[i]*.6+d.f[i]*.4;
        _camEMA=d;}
    }
    if(now-_camLastStrip>400){_camLastStrip=now;
      try{
      // 시각 임베딩 갱신(strip 틱마다 1회 — MobileNet ~15ms, 온디바이스)
      if(CIDX&&v.videoWidth){colInit();const lc=liveColor(v);
        if(lc)_colLive=_colLive?_colLive.map((x,i)=>x*.5+lc[i]*.5):lc;}   // 색감 EMA
      if(_visModel&&VIDX&&v.videoWidth){try{
        const emb=tf.tidy(()=>_visModel.infer(v,true));
        const raw=emb.dataSync();emb.dispose();_visLive=visProject(raw);
      }catch(e){}}
      // DINOv2 는 100~300ms 라 매 틱(400ms) 마다 돌리면 UI 가 밀린다. 별도 스로틀 + 비동기.
      // 장면은 그렇게 빨리 바뀌지 않으므로 직전 값을 유지해도 추천 품질에 지장이 없다.
      if(_dinoModel&&_dinoEmb&&v.videoWidth&&!_dinoBusy&&now-_dinoLast>dinoGap()){
        _dinoBusy=true;_dinoLast=now;const t0=performance.now();
        dinoExtract(v).then(y=>{if(y)_dinoLive=y;
            const ms=performance.now()-t0;                     // 실측 → 다음 간격에 반영
            _dinoMs=_dinoMs?_dinoMs*0.7+ms*0.3:ms;})
          .catch(()=>{}).finally(()=>{_dinoBusy=false;});
      }
      const m=camMatch(d);
      if(!d&&!_visLive&&!_colLive){stat.textContent=lms?'포즈 특징 부족 — 몸이 더 보이게 (시각 모델 로딩 중이면 잠시 후 폴백)':'인물이 감지되지 않았어요';return;}
      stat.textContent=(m.mode==='fuse'?(nv>=PD_MINV?'🟢 포즈+분위기 융합':'🟡 부분 포즈+분위기')+' — 특징 '+nv+'/21'
        :m.mode==='pose'?(nv>=PD_MINV?'🟢 전신 매칭':'🟡 부분 매칭')+' — 특징 '+nv+'/21'
        :'🔵 분위기 매칭(인물 없이 장면 유사)');
      const bits=[];
      if(m.est)bits.push(...m.est.labels);
      if(_camFrame!=null)bits.push(['전신','반신','얼빡'][_camFrame]);
      if(_camDir)bits.push({front:'정면',side:'측면',back:'뒷모습'}[_camDir]);
      if(_camNum!=null)bits.push(_camNum>=2?'2인+':'1인');
      document.getElementById('camest').textContent=bits.length?('추정: '+bits.filter(Boolean).join('·')):'';
      const basis=[_colLive&&'색감',_visLive&&'분위기',_dinoLive&&'구도',d&&'포즈',_camScaleLive!=null&&'거리감'].filter(Boolean).join('→');
      document.getElementById('camctx').innerHTML=
        (m.ctx&&m.ctx.length?'필터 <b>'+m.ctx.join('·')+'</b>':'필터 없음(전체)')+(basis?' · 정렬 '+basis:'');
      camRenderStrip(camSmooth(m.top));
      }catch(err){stat.textContent='매칭 오류(다음 프레임 재시도): '+err.message;}
    }
  };
  loop();
}
function bitCount(v){let n=0;while(v){n+=v&1;v>>>=1;}return n;}

// ---------- 정지 이미지 업로드 분석 (성능 테스트) — 라이브와 동일 파이프라인 1회 실행 ----------
let _camMode='live',_stillTs=1e6,_stillPose=null;
function loadStillPoseModel(){                          // 정지용은 IMAGE 런닝모드 별도 인스턴스
  if(_stillPose)return _stillPose;
  _stillPose=import('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/vision_bundle.mjs')
    .then(async m=>{const files=await m.FilesetResolver.forVisionTasks('https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.14/wasm');
      return m.PoseLandmarker.createFromOptions(files,{
        baseOptions:{modelAssetPath:'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task'},
        runningMode:'IMAGE',numPoses:2});})
    .catch(e=>{_stillPose=null;throw e;});
  return _stillPose;}
async function camAnalyzeFile(file){
  if(!file)return;
  // 라이브 정지
  if(_camRAF)cancelAnimationFrame(_camRAF);_camRAF=null;camStopStream();
  _camMode='still';
  document.getElementById('camv').hidden=true;
  document.getElementById('camlive').hidden=false;
  ['camflip','cammir','camsel'].forEach(id=>document.getElementById(id).style.display='none');
  const img=document.getElementById('camimg');img.hidden=false;
  document.getElementById('camimgx').hidden=false;              // 삭제(✕) 버튼 노출
  _camZoom=1;_camZoomCap=null;_camMirror=false;applyMirror();   // 정지 이미지: CSS 줌·미러 해제로 시작
  document.getElementById('camzlbl').textContent='1.0×';
  const stat=document.getElementById('camstat');stat.textContent='이미지 로딩 중…';
  const url=URL.createObjectURL(file);
  await new Promise((res,rej)=>{img.onload=res;img.onerror=rej;img.src=url;});
  stat.textContent='모델 로딩·분석 중…';
  // 신호 리셋(정지=단발이므로 EMA/랭킹 히스테리시스 없음)
  _camEMA=null;_visLive=null;_colLive=null;_camScaleLive=null;_camDir=null;_camFrame=null;_camNum=null;_camRank=new Map();
  let d=null,nv=0,lms=null;
  try{const lm=await loadStillPoseModel();const r=lm.detect(img);
    lms=r.landmarks&&r.landmarks[0];_camNum=r.landmarks?r.landmarks.length:null;}catch(e){}
  if(lms){
    const kp=[];for(let i=0;i<33;i++){const p=lms[i];kp.push(p.x,p.y,p.visibility==null?1:p.visibility);}
    _camFrame=liveFraming(kp);const sc=subjScale(kp);if(sc!=null)_camScaleLive=sc;
    _camDir=liveFacing(kp);d=pdCompute(kp,true);nv=d?bitCount(d.v):0;if(d&&nv<PD_LIVE_MINV)d=null;
  }
  camDrawSkel(lms,img);
  try{if(CIDX){colInit();const lc=liveColor(img);if(lc)_colLive=lc;}
    if(VIDX){await loadVisModel().catch(()=>{});
      if(_visModel){const emb=tf.tidy(()=>_visModel.infer(img,true));const raw=emb.dataSync();emb.dispose();_visLive=visProject(raw);}}
    if(SIGS.dino){await loadDinoModel().catch(()=>{});   // 업로드는 1장뿐이라 스로틀 없이 정확도 우선
      _dinoLive=await dinoExtract(img).catch(()=>null);}
  }catch(e){}
  const m=camMatch(d);
  camUpdateStatus(m,d,nv,'still');
  camRenderStrip(m.top.slice(0,10));
  URL.revokeObjectURL(url);
}
function camUpdateStatus(m,d,nv,mode){                  // 상태·추정·맥락 표시 (라이브/정지 공용)
  const stat=document.getElementById('camstat');
  if(!d&&!_visLive&&!_colLive){stat.textContent=mode==='still'?'분석 신호 부족 — 다른 사진을 시도해 보세요':'인물 미감지';return;}
  const pre=mode==='still'?'🖼️ 업로드 분석 · ':'';
  stat.textContent=pre+(m.mode==='fuse'?(nv>=PD_MINV?'🟢 포즈+분위기':'🟡 부분 포즈+분위기')+' '+nv+'/21'
    :m.mode==='pose'?(nv>=PD_MINV?'🟢 포즈 매칭':'🟡 부분 매칭')+' '+nv+'/21':'🔵 분위기 매칭');
  // 구도 신호(DINOv2)는 44MB 라 늦게 붙는다. 받는 중이면 그 사실을 숨기지 않는다.
  if(SIGS.dino&&!_dinoReady&&_dinoP)
    stat.textContent+=`  ·  🧭 구도 모델 ${_dinoPct?_dinoPct+'%':'준비 중'} (44MB, 최초 1회)`;
  const bits=[];
  if(m.est)bits.push(...m.est.labels);
  if(_camFrame!=null)bits.push(['전신','반신','얼빡'][_camFrame]);
  if(_camDir)bits.push({front:'정면',side:'측면',back:'뒷모습'}[_camDir]);
  if(_camNum!=null)bits.push(_camNum>=2?'2인+':'1인');
  document.getElementById('camest').textContent=bits.length?('추정: '+bits.filter(Boolean).join('·')):'';
  const basis=[_colLive&&'색감',_visLive&&'분위기',d&&'포즈',_camFrame!=null&&'거리감'].filter(Boolean).join('→');
  document.getElementById('camctx').innerHTML=(m.ctx&&m.ctx.length?'필터 <b>'+m.ctx.join('·')+'</b>':'필터 없음(전체)')+(basis?' · 정렬 '+basis:'');
}
// 포즈캠 버튼에 마우스를 올리는(혹은 터치하는) 순간 무거운 모델을 미리 받기 시작한다.
// DINOv2 는 44MB·10초라 클릭 후에 받기 시작하면 '멈춘 것처럼' 느껴진다. 클릭 전 몇 초를 번다.
// (앱 로드 시 미리 받지는 않는다 — 포즈캠을 안 쓰는 사람에게 44MB 를 물리면 안 된다)
const _pcBtn=document.getElementById('posecam');
let _pcWarm=false;
const camWarm=()=>{if(_pcWarm)return;_pcWarm=true;
  if(SIGS.dino)loadDinoModel().catch(()=>{});
  if(VIDX)loadVisModel().catch(()=>{});
  loadPoseModel().catch(()=>{});};
_pcBtn.addEventListener('mouseenter',camWarm);
_pcBtn.addEventListener('touchstart',camWarm,{passive:true});
_pcBtn.onclick=camOpen;
document.getElementById('camup').onclick=()=>document.getElementById('camfile').click();
document.getElementById('camfile').onchange=e=>{const f=e.target.files&&e.target.files[0];if(f)camAnalyzeFile(f);e.target.value='';};
function camExitStill(){   // 업로드 이미지 삭제 → 라이브 복귀
  const img=document.getElementById('camimg');img.hidden=true;if(img.src)img.removeAttribute('src');
  document.getElementById('camv').hidden=false;
  document.getElementById('camlive').hidden=true;
  document.getElementById('camimgx').hidden=true;
  ['camflip','cammir','camsel'].forEach(id=>document.getElementById(id).style.display='');
  document.getElementById('camcv').getContext('2d').clearRect(0,0,9999,9999);
  document.getElementById('camrec').innerHTML='<span class="empty">포즈가 잡히면 닮은 컷이 여기에…</span>';
  document.getElementById('camest').textContent='';document.getElementById('camctx').innerHTML='';
  camOpen();}
document.getElementById('camlive').onclick=camExitStill;
document.getElementById('camimgx').onclick=camExitStill;
document.getElementById('camclose').onclick=camClose;
// 라벨 가중 UI: 장소 옵션은 taxonomy 에서, 변경 즉시 다음 틱에 재랭킹
(function(){const sel=document.getElementById('camwhere');if(!sel)return;
  if(!PLACE_AXIS){                                  // '장소' 역할이 없는 도메인 → 이 필터를 숨긴다
    const lab=sel.previousSibling;if(lab&&lab.remove)lab.remove();
    if(sel.remove)sel.remove();return;}
  if(sel.children&&sel.insertAdjacentHTML&&sel.children.length<=1)
    for(const[code,ko]of Object.entries(TAX[PLACE_AXIS]))
      sel.insertAdjacentHTML('beforeend',`<option value="${code}">${ko}</option>`);})();
document.getElementById('camlbl').onchange=e=>{_camLbl=e.target.checked;_camLastStrip=0;};
{const _cw=document.getElementById('camwhere');
 if(_cw)_cw.onchange=e=>{_camWhere=e.target.value;_camLastStrip=0;};}
document.getElementById('camflip').onclick=()=>{_camFacing=_camFacing==='user'?'environment':'user';
  _camMirror=(_camFacing==='user');_camDevice='';applyMirror();camStart().catch(()=>{});};
document.getElementById('cammir').onclick=()=>{_camMirror=!_camMirror;applyMirror();};
document.getElementById('camzin').onclick=()=>camZoomStep(1);
document.getElementById('camzout').onclick=()=>camZoomStep(-1);
(function(){const st=document.querySelector('.camstage');
  if(st&&st.addEventListener)st.addEventListener('wheel',e=>{e.preventDefault();camZoomStep(e.deltaY<0?1:-1);},{passive:false});})();
document.getElementById('camsel').onchange=e=>{_camDevice=e.target.value;camStart().catch(()=>{});};
document.getElementById('camwrap').addEventListener('click',e=>{if(e.target.id==='camwrap')camClose();});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!document.getElementById('camwrap').hidden)camClose();});

renderDash();buildFilters();buildGrid();applyFilter();refreshCounts();refreshFilterCounts();renderTicker();
if(!reviewer)askWho(true);
if(FB){initFirebase();}else{toast('로컬 모드 (실시간 OFF) — 저장은 CSV 내보내기');}
