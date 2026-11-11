/* core 홈의 기기 목록(설정 devices 가 있는 jarvis 에서만 보인다).
 * jarvis 업데이트는 그 게이트웨이가 origin main 을 fast-forward 로 받고, 코드가 바뀌었으면 스스로 재시작한다. */
(function(){
 "use strict";
 const P=window.JARVIS,{html,fmt}=P,$=s=>document.querySelector(s);
 const sec=$("#devices");if(!sec)return;
 // 헤더 탭 바의 '기기' 탭은 core 에서만 보인다
 // (shell.css 가 data-core 없는 탭을 숨긴다: 게이트웨이에서 처음 그릴 때도 깜빡이지 않게)
 const showNav=on=>document.querySelectorAll('[data-sh-sec="devices"]').forEach(a=>a.toggleAttribute("data-core",on));
 if(P.device){ // 다른 기기를 보는 중: 어느 기기인지와 돌아갈 길
  const bar=$("#devBar");bar.hidden=false;
  const show=d=>P.render(bar,html`<span><b>${(d&&d.host)||P.device}</b> 기기를 보고 있습니다.</span>${d&&!d.reachable?html` <span class="dev-err">${d.ok_at?"응답 없음":"접속 불가"}${d.error?": "+d.error:""}</span>`:""} <a href="/#devices" data-all-devices>전체 기기로</a>`);
  show(null);
  const poll=()=>fetch("/api/devices",{cache:"no-store"}).then(r=>r.ok?r.json():null).then(d=>{show(d&&d.devices.find(x=>x.id===P.device));}).catch(()=>{}).finally(()=>setTimeout(poll,10000));
  poll();
  return;
 }
 // 셸이 기억한 core 값이 있으면 확인 전에도 섹션을 연다(목록은 응답이 오면 채운다). 게이트웨이면 아래 load 가 404 로 다시 숨긴다
 if(document.querySelector('[data-sh-sec="devices"][data-core]'))sec.hidden=false;
 const list=$("#devList"),tiles=new Map(),vers=new Map();
 function head(d,now){
  const [tone,state]=!d.reachable?["crit",d.ok_at?"응답 없음":"접속 불가"]:[d.status||"ok",{ok:"정상",warn:"주의",crit:"심각"}[d.status]||"정상"];
  const al=d.alerts||{};
  // 제목은 기기가 보고한 호스트명(수집 전이면 설정 id)
  return html`<header class="tile-head"><h3 class="tile-title">${d.host||d.id}</h3><span class="badge" data-tone="${tone}">${state}</span><span class="tile-aside">${d.model||""}${String(d.url||"").startsWith("ssh:")?" · SSH 경유":""}</span></header>
<p class="dev-stats">CPU ${fmt.pct(d.cpu_pct)} · 메모리 ${fmt.pct(d.mem_pct)} · 디스크 ${fmt.pct(d.disk_pct)} · 경보 ${al.crit??"–"}/${al.warn??"–"} · ${d.ok_at?fmt.ago(d.ok_at,now)+" 수집":"수집 전"}${d.version?html` · <span title="${d.commit?"커밋 "+d.commit:""}">jarvis v${d.version}</span>`:d.commit?" · jarvis "+d.commit:""}${d.error?" · "+d.error:""}</p>`;
 }
 function tile(d){
  const el=document.createElement("article");el.className="tile span-6 m-6 dev-tile";el.dataset.dev=d.id;
  P.render(el,html`<div class="dev-head"></div><p class="dev-acts"><button type="button" class="seg-action" data-jarvis-update="${d.id}">jarvis 업데이트</button><a class="seg-action" href="${"/?device="+encodeURIComponent(d.id)}">상세 보기</a><span class="dev-prog"></span><span class="dev-note" role="status"></span></p>`);
  return el;
 }
 async function load(){
  let d;
  try{
   const r=await fetch("/api/devices",{cache:"no-store"});
   if(r.status===404){sec.hidden=true;showNav(false);return;} // core 가 아닌 jarvis(게이트웨이)
   if(!r.ok)throw new Error("HTTP "+r.status);
   d=await r.json();
  }catch(e){setTimeout(load,30000);return;}
  const first=sec.hidden;sec.hidden=false;showNav(true);
  const ja=$("#devJarvisAll");if(ja)ja.hidden=false;
 if(first&&location.hash==="#devices")sec.scrollIntoView(); // 첫 렌더의 앵커 이동은 섹션이 숨어 있을 때 지나갔다
  for(const x of d.devices){
   if(!tiles.has(x.id)){const t=tile(x);tiles.set(x.id,t);list.append(t);}
   P.render(tiles.get(x.id).querySelector(".dev-head"),head(x,d.generated_at));
   if(x.reachable&&x.version)vers.set(x.id,x.version);
  }
  setTimeout(load,10000);
 }
 // 카드의 빈 곳을 눌러도 상세 보기로 간다. 조작 요소·글자 선택은 제외한다
 list.addEventListener("click",e=>{
  const t=e.target.closest(".dev-tile");
  if(!t||e.target.closest("a,button,input,select,textarea,label")||String(getSelection()))return;
  location.href="/?device="+encodeURIComponent(t.dataset.dev);
 });
 // jarvis 업데이트 진행: 스피너(시작) → 진행 막대(받는 중, 받은 바이트/전체) → 스피너(풀기·재시작) → 바뀐 버전.
 // 단계는 그 게이트웨이의 GET /api/jarvis/update 로, 재시작이 끝났는지는 버전이 바뀐 것으로 본다(그 경로가 없는 옛 게이트웨이는 기기 목록의 버전)
 const prog=new Map(),sleep=ms=>new Promise(r=>setTimeout(r,ms)),RESTART_WAIT=180000;
 const noteOf=id=>tiles.get(id)?.querySelector(".dev-note");
 function say(id,text,bad){const n=noteOf(id);if(n){n.textContent=text;n.classList.toggle("dev-err",!!bad);}}
 function drawProg(id){
  const el=tiles.get(id)?.querySelector(".dev-prog"),p=prog.get(id);if(!el)return;
  P.render(el,!p?"":p.pct!=null?html`<span class="dev-meter" role="progressbar" aria-label="jarvis 받는 중" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${p.pct}"><i style="${"width:"+p.pct+"%"}"></i></span>`
   :html`<span class="dev-spin" role="img" aria-label="jarvis 업데이트 중"></span>`);
 }
 function stop(id,text,bad){prog.delete(id);drawProg(id);say(id,text,bad);}
 async function watch(id){
  const p={from:vers.get(id),until:0};prog.set(id,p);drawProg(id);say(id,"업데이트 시작…");
  while(prog.get(id)===p){
   let now=null;
   try{
    const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/jarvis/update",{cache:"no-store"});
    if(r.ok){
     const o=await r.json();now=o.version;
     const pct=o.busy&&o.stage==="download"&&o.total?Math.min(100,Math.floor(o.got/o.total*100)):null;
     if(pct!=null){p.pct=pct;say(id,"받는 중 "+pct+"%");}
     else if(p.pct!=null&&o.busy){p.pct=null;say(id,"마무리 중…");}
    }
   }catch(e){} // 재시작하는 동안은 응답이 없다
   const v=now||vers.get(id);
   if(p.from&&v&&v!==p.from){stop(id,`jarvis v${p.from} → v${v}`);return;}
   if(p.until&&Date.now()>p.until){stop(id,"재시작을 확인하지 못했습니다. 상세 보기에서 버전을 확인하세요",true);return;}
   drawProg(id);await sleep(500);
  }
 }
 // POST 결과: 최신이거나 실패면 진행을 끝내고, 재시작이면 버전이 바뀔 때까지 스피너로 기다린다
 function finish(id,ok,o){
  const p=prog.get(id);if(!p)return;
  if(!ok){stop(id,o.error||("HTTP "+o.status),true);return;}
  if(!o.restart){stop(id,`jarvis v${o.to} 최신`);return;}
  p.pct=null;p.until=Date.now()+RESTART_WAIT;p.from=p.from||o.running;drawProg(id);say(id,`v${o.running||"?"} → v${o.to} 재시작 중…`);
 }
 list.addEventListener("click",async e=>{
  const ju=e.target.closest("[data-jarvis-update]");if(!ju)return;
  const id=ju.dataset.jarvisUpdate;
  if(prog.has(id)||!confirm(`${id} 에서 jarvis 를 GitHub main 으로 받습니다. 코드가 바뀌면 그 기기 jarvis 가 몇 초 동안 재시작됩니다. 계속할까요?`))return;
  ju.disabled=true;watch(id);
  try{finish(id,true,await jarvisUpdate("/api/devices/"+encodeURIComponent(id)+"/jarvis/update"));}
  catch(err){finish(id,false,{error:err.message||String(err)});}
  finally{ju.disabled=false;}
 });
 async function jarvisUpdate(url){
  const r=await fetch(url,{method:"POST",headers:{"X-Jarvis-Action":"jarvis-update"},cache:"no-store"}),o=await r.json().catch(()=>({})); // 재시작 중인 core 앞의 Caddy 는 HTML 502 를 준다
  if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
  return o;
 }
 const jAll=$("#devJarvisAll");
 if(jAll)jAll.addEventListener("click",async()=>{
  const note=$("#devUpdateNote");
  if(!confirm("목록의 모든 기기에서 jarvis 를 GitHub main 으로 받고, 코드가 바뀐 기기는 jarvis 를 재시작합니다. 이 core 는 마지막에 재시작합니다. 계속할까요?"))return;
  jAll.disabled=true;note.textContent="모든 기기에서 받는 중…";note.classList.remove("dev-err");
  for(const id of tiles.keys())if(!prog.has(id))watch(id);
  try{
   const o=await jarvisUpdate("/api/devices/jarvis/update"),rows=o.devices||[];
   for(const x of rows)finish(x.id,x.status===200,x);
   const bad=rows.filter(x=>x.status!==200);
   note.textContent=`jarvis: ${rows.length-bad.length}대 받음`+(bad.length?" · 실패 "+bad.map(x=>x.id).join(", "):"");
   note.classList.toggle("dev-err",bad.length>0);
  }catch(err){
   note.textContent=err.message||String(err);note.classList.add("dev-err");
   for(const id of [...prog.keys()])if(!prog.get(id).until)stop(id,"");
  }
  jAll.disabled=false;
 });
 load();
})();
