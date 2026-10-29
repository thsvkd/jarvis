/* core 홈의 기기 목록, Orca 페어링, Orca 업데이트(설정 devices 가 있는 jarvis 에서만 보인다).
 * 페어링 코드는 원격 셸 권한이라 스냅샷에 없다. 페어링을 누를 때 그 기기 하나만 /api/devices/<id>/orca(viewers 전용)에서 받는다.
 * 업데이트 확인은 버전만 비교한다. 업데이트 진행은 타일 한 줄에만 적고, 페어링 QR 은 그 버튼을 눌렀을 때만 연다.
 * 업데이트는 각 플랫폼의 공식 패키지·업데이터를 쓰고 전역 스킬을 갱신한다. 열린 작업은 게이트웨이가 보호한다.
 * jarvis 업데이트는 그 게이트웨이가 origin main 을 fast-forward 로 받고, 코드가 바뀌었으면 스스로 재시작한다. */
(function(){
 "use strict";
 const P=window.JARVIS,{html,fmt}=P,$=s=>document.querySelector(s);
 const sec=$("#devices");if(!sec)return;
 // 섹션 바로가기(공용 헤더·좁은 화면 막대)의 '기기'는 core 에서만 보인다
 // (CSS 가 data-core 없는 링크를 숨긴다: 게이트웨이에서 처음 그릴 때도 깜빡이지 않게)
 const showNav=on=>document.querySelectorAll('[data-sh-sec="devices"],[data-lsec="devices"]').forEach(a=>a.toggleAttribute("data-core",on));
 if(P.device){ // 다른 기기를 보는 중: 어느 기기인지와 돌아갈 길
  const bar=$("#devBar");bar.hidden=false;
  const show=d=>P.render(bar,html`<span><b>${(d&&d.host)||P.device}</b> 기기를 보고 있습니다.</span>${d&&!d.reachable?html` <span class="dev-err">${d.ok_at?"응답 없음":"접속 불가"}${d.error?": "+d.error:""}</span>`:""} <a href="/#devices" data-all-devices>전체 기기로</a>`);
  show(null);
  const poll=()=>fetch("/api/devices",{cache:"no-store"}).then(r=>r.ok?r.json():null).then(d=>{show(d&&d.devices.find(x=>x.id===P.device));}).catch(()=>{}).finally(()=>setTimeout(poll,10000));
  poll();
  return;
 }
 const list=$("#devList"),tiles=new Map();
 const NAME={desktop:"데스크톱",mobile:"모바일"};
 function qr(rows,label){
  if(!rows)return "";
  let d="";rows.forEach((r,y)=>{for(let x=0;x<r.length;x++)if(r[x]==="1")d+="M"+x+" "+y+"h1v1h-1z";});
  return html`<svg class="orca-qr" viewBox="0 0 ${rows.length} ${rows.length}" role="img" aria-label="${label} 페어링 QR"><rect width="100%" height="100%" fill="#fff"/><path d="${d}" fill="#000"/></svg>`;
 }
 function code(kind,c,o){
  const name=NAME[kind];
  if(!c){
   const job=o.issue&&o.issue.scope===kind?o.issue:null;
   if(job&&job.state==="running")return html`<div class="orca-code" data-kind="${kind}"><h4 class="sub-h">${name}</h4><p class="orca-none">발급하고 있습니다. 그 기기 Orca 가 잠시 재시작됩니다(1~2분)…</p></div>`;
   const fail=job&&job.state==="failed"?html`<p class="dev-err">지난 발급 실패: ${job.error||"원인 모름"}</p>`:"";
   // 그 scope 기록이 한 번도 없을 때만 게이트웨이가 issuable 에 넣는다(처음 한 번 serve 를 그 모드로 다시 띄워 만든다)
   if((o.issuable||[]).includes(kind))return html`<div class="orca-code" data-kind="${kind}"><h4 class="sub-h">${name}</h4><p class="orca-none">이 기기 Orca 에 ${name} 페어링 기록이 없습니다. 발급하면 그 기기 Orca 를 잠시 내리고 serve 로 코드를 한 번 만든 뒤 다시 띄웁니다.</p>${fail}<button type="button" class="seg-action" data-orca-issue="${kind}">${name} 코드 발급</button></div>`;
   return html`<div class="orca-code" data-kind="${kind}"><h4 class="sub-h">${name}</h4><p class="orca-none">이 기기 Orca 에 ${name} 페어링 기록이 없고, 자동으로 발급할 수 없는 기기입니다. 그 기기에서 Orca 를 열고 ${kind==="mobile"?"설정 → 모바일 페어링에서 QR 을 띄우거나":"설정에서 데스크톱 클라이언트용 액세스를 만들거나"} serve 를 ${kind==="mobile"?"--mobile-pairing 을 붙여 ":"--mobile-pairing 없이 "}다시 시작한 뒤 다시 불러오세요.</p>${fail}</div>`;
  }
  // 대기 코드가 없으면 게이트웨이가 가장 최근에 페어링된 기기의 코드를 준다(pending false)
  const reused=c.pending===false?html`<p class="orca-reused">이미 페어링된 코드 · ${c.name||c.device_id}</p>`:"";
  const body=html`${qr(c.qr,name)}<div class="orca-copy"><input readonly value="${c.url}" aria-label="${name} 페어링 코드"><button type="button" class="seg-action" data-orca-copy>복사</button></div>`;
  return html`<div class="orca-code" data-kind="${kind}"><h4 class="sub-h">${name}</h4>${body}${reused}</div>`;
 }
 const PHASE={manifest:"릴리스 확인",download:"패키지 받는 중",install:"설치·재시작",skills:"스킬 갱신"};
 function updateText(u){
  if(!u)return "";
  if(u.state==="running")return "업데이트 중 · "+(PHASE[u.phase]||u.phase)+(u.to_version?" → "+u.to_version:"");
  if(u.state==="failed")return "업데이트 실패"+(u.app==="updated"?" (패키지는 "+(u.to_version||"새 버전")+" 로 올라갔습니다)":"")+": "+(u.error||"원인 모름");
  if(u.state==="done"){
   const app=u.app==="current"?"Orca "+(u.to_version||"")+" 은 이미 최신":"Orca "+(u.from_version||"?")+" → "+(u.to_version||"?");
   return app+" · 스킬 "+(u.skills==="updated"?"갱신함":u.skills||"")+(u.sessions?.state==="preserved"?" · 기존 터미널 "+u.sessions.terminals+"개 유지 확인":"");
  }
  return "";
 }
 function upd(o){
  const line=updateText(o.update);
  if(!line)return "";
  const fail=o.update&&o.update.state==="failed";
  return html`<p class="${fail?"dev-err":"orca-state"}">${line}</p>`;
 }
 function orca(o){
  if(o.error&&!o.supported)return html`<p class="orca-none">${o.error}</p>${upd(o)}`;
  if(!o.installed)return html`<p class="orca-none">이 기기에는 Orca 가 없습니다.</p>${upd(o)}`;
  return html`${upd(o)}<p class="orca-state"><span class="badge" data-tone="${o.running?"ok":"warn"}">${o.running?"Orca 실행 중":"Orca 꺼짐"}</span> <span>${o.endpoint||"tailnet 주소를 모릅니다"}</span></p><div class="orca-codes">${code("desktop",o.codes.desktop,o)}${code("mobile",o.codes.mobile,o)}</div>`;
 }
 function head(d,now){
  const [tone,state]=!d.reachable?["crit",d.ok_at?"응답 없음":"접속 불가"]:[d.status||"ok",{ok:"정상",warn:"주의",crit:"심각"}[d.status]||"정상"];
  const al=d.alerts||{};
  // 제목은 기기가 보고한 호스트명(수집 전이면 설정 id)
  return html`<header class="tile-head"><h3 class="tile-title">${d.host||d.id}</h3><span class="badge" data-tone="${tone}">${state}</span><span class="tile-aside">${d.model||""}</span></header>
<p class="dev-stats">CPU ${fmt.pct(d.cpu_pct)} · 메모리 ${fmt.pct(d.mem_pct)} · 디스크 ${fmt.pct(d.disk_pct)} · 경보 ${al.crit??"–"}/${al.warn??"–"} · ${d.ok_at?fmt.ago(d.ok_at,now)+" 수집":"수집 전"}${d.version?html` · <span title="${d.commit?"커밋 "+d.commit:""}">jarvis v${d.version}</span>`:d.commit?" · jarvis "+d.commit:""}${d.error?" · "+d.error:""}</p>`;
 }
 function tile(d){
  const el=document.createElement("article");el.className="tile span-6 m-6 dev-tile";el.dataset.dev=d.id;
  P.render(el,html`<div class="dev-head"></div><p class="dev-acts"><button type="button" class="seg-action" data-orca="${d.id}">페어링</button><button type="button" class="seg-action" data-orca-check="${d.id}">Orca 업데이트 확인</button><button type="button" class="seg-action" data-orca-update="${d.id}">Orca 업데이트</button><button type="button" class="seg-action" data-jarvis-update="${d.id}">jarvis 업데이트</button><a class="seg-action" href="${"/?device="+encodeURIComponent(d.id)}">상세 보기</a><span class="dev-note" role="status"></span></p><div class="dev-orca"></div>`);
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
  for(const b of [$("#devUpdateAll"),$("#devJarvisAll")])if(b)b.hidden=false;
 if(first&&location.hash==="#devices")sec.scrollIntoView(); // 첫 렌더의 앵커 이동은 섹션이 숨어 있을 때 지나갔다
  for(const x of d.devices){
   if(!tiles.has(x.id)){const t=tile(x);tiles.set(x.id,t);list.append(t);}
   P.render(tiles.get(x.id).querySelector(".dev-head"),head(x,d.generated_at));
  }
  setTimeout(load,10000);
 }
 list.addEventListener("click",async e=>{
  const c=e.target.closest("[data-orca-copy]");
  if(c){
   const i=c.previousElementSibling;i.select();
   const done=()=>{c.textContent="복사됨";setTimeout(()=>{c.textContent="복사";},1500);};
   // http(비보안 컨텍스트)에서는 clipboard API 가 없다
   if(navigator.clipboard)navigator.clipboard.writeText(i.value).then(done,()=>{document.execCommand("copy");done();});
   else{document.execCommand("copy");done();}
   return;
  }
  const is=e.target.closest("[data-orca-issue]");
  if(is){
   const id=is.closest(".dev-tile").dataset.dev,kind=is.dataset.orcaIssue,t=tiles.get(id),note=t.querySelector(".dev-note");
   if(!confirm(`${id} 의 ${NAME[kind]} 코드를 발급합니다. 그 기기 Orca 가 1~2분 동안 재시작되고, 그 Orca 에서 진행 중인 작업(에이전트·터미널)이 끊깁니다. 계속할까요?`))return;
   is.disabled=true;note.textContent="발급 요청 중…";note.classList.remove("dev-err");
   try{
    const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/orca/issue?scope="+kind,{method:"POST",headers:{"X-Jarvis-Action":"orca-issue"},cache:"no-store"}),o=await r.json();
    if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
    // 발급이 끝날 때까지 5초마다 다시 불러온다(최대 4분)
    for(let n=0;n<48;n++){
     const x=await loadOrca(id);
     if(!x||!x.issue||x.issue.scope!==kind||x.issue.state!=="running")break;
     await new Promise(res=>setTimeout(res,5000));
    }
   }catch(err){note.textContent=err.message||String(err);note.classList.add("dev-err");is.disabled=false;}
   return;
  }
  const ck=e.target.closest("[data-orca-check]");
  if(ck){
   ck.disabled=true;
   try{await checkUpdate(ck.dataset.orcaCheck);}
   catch(err){const note=tiles.get(ck.dataset.orcaCheck).querySelector(".dev-note");note.textContent=err.message||String(err);note.classList.add("dev-err");}
   finally{ck.disabled=false;}
   return;
  }
  const up=e.target.closest("[data-orca-update]");
  if(up){
   const id=up.dataset.orcaUpdate;
   if(!confirm(`${id} 의 Orca 를 공식 릴리스로 올리고 스킬을 갱신합니다. 서버 연결이 잠시 끊길 수 있습니다. 기존 터미널의 보존 가능성을 확인하며, 네이티브 채팅 작업이나 확인 불가능한 세션이 있으면 보류합니다. 계속할까요?`))return;
   up.disabled=true;
   let keep=false;
   try{keep=await runUpdate("/api/devices/"+encodeURIComponent(id)+"/orca/update", id);}
   catch(err){const note=tiles.get(id).querySelector(".dev-note");note.textContent=err.message||String(err);note.classList.add("dev-err");}
   finally{if(!keep)up.disabled=false;}
   return;
  }
  const ju=e.target.closest("[data-jarvis-update]");
  if(ju){
   const id=ju.dataset.jarvisUpdate,note=tiles.get(id).querySelector(".dev-note");
   if(!confirm(`${id} 에서 jarvis 를 GitHub main 으로 받습니다. 코드가 바뀌면 그 기기 jarvis 가 몇 초 동안 재시작됩니다. 계속할까요?`))return;
   ju.disabled=true;note.textContent="받는 중…";note.classList.remove("dev-err");
   try{note.textContent=jarvisText(await jarvisUpdate("/api/devices/"+encodeURIComponent(id)+"/jarvis/update"));}
   catch(err){note.textContent=err.message||String(err);note.classList.add("dev-err");}
   finally{ju.disabled=false;}
   return;
  }
  const b=e.target.closest("[data-orca]");if(!b)return;
  b.disabled=true;
  await loadOrca(b.dataset.orca);
  b.disabled=false;
 });
 async function loadOrca(id){
  const t=tiles.get(id),note=t.querySelector(".dev-note"),b=t.querySelector("[data-orca]");
  note.textContent="불러오는 중…";note.classList.remove("dev-err");
  try{
   const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/orca",{cache:"no-store"}),o=await r.json();
   if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
   P.render(t.querySelector(".dev-orca"),orca(o));
   note.textContent=new Date().toLocaleTimeString("ko-KR")+" 기준";b.textContent="다시 불러오기";
   return o;
  }catch(err){P.render(t.querySelector(".dev-orca"),"");note.textContent=err.message||String(err);note.classList.add("dev-err");return null;}
 }
 async function jarvisUpdate(url){
  const r=await fetch(url,{method:"POST",headers:{"X-Jarvis-Action":"jarvis-update"},cache:"no-store"}),o=await r.json().catch(()=>({})); // 재시작 중인 core 앞의 Caddy 는 HTML 502 를 준다
  if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
  return o;
 }
 const jarvisText=o=>o.restart?`jarvis v${o.running||"?"} → v${o.to} · 재시작합니다`:`jarvis v${o.to} 최신`;
 const jAll=$("#devJarvisAll");
 if(jAll)jAll.addEventListener("click",async()=>{
  const note=$("#devUpdateNote");
  if(!confirm("목록의 모든 기기에서 jarvis 를 GitHub main 으로 받고, 코드가 바뀐 기기는 jarvis 를 재시작합니다. 이 core 는 마지막에 재시작합니다. 계속할까요?"))return;
  jAll.disabled=true;note.textContent="모든 기기에서 받는 중…";note.classList.remove("dev-err");
  try{
   const o=await jarvisUpdate("/api/devices/jarvis/update"),rows=o.devices||[];
   for(const x of rows){
    const n=tiles.get(x.id)?.querySelector(".dev-note");
    if(n){n.textContent=x.status===200?jarvisText(x):(x.error||("HTTP "+x.status));n.classList.toggle("dev-err",x.status!==200);}
   }
   const bad=rows.filter(x=>x.status!==200);
   note.textContent=`jarvis: ${rows.length-bad.length}대 완료`+(bad.length?" · 실패 "+bad.map(x=>x.id).join(", "):"");
   note.classList.toggle("dev-err",bad.length>0);
  }catch(err){note.textContent=err.message||String(err);note.classList.add("dev-err");}
  jAll.disabled=false;
 });
 const allBtn=$("#devUpdateAll"), allNote=$("#devUpdateNote");
 if(allBtn)allBtn.addEventListener("click",async()=>{
  if(!confirm("목록의 모든 기기 Orca 와 스킬을 갱신합니다. 기존 터미널의 보존 가능성을 확인하며, 네이티브 채팅 작업이나 확인 불가능한 세션이 있는 기기는 보류합니다. 계속할까요?"))return;
  allBtn.disabled=true;allNote.textContent="업데이트를 요청하는 중…";allNote.classList.remove("dev-err");
  try{
   const r=await fetch("/api/devices/orca/update",{method:"POST",headers:{"X-Jarvis-Action":"orca-update"},cache:"no-store"}),o=await r.json();
   if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
   const ids=(o.devices||[]).filter(x=>x.status===202).map(x=>x.id);
   const bad=(o.devices||[]).filter(x=>x.status!==202).map(x=>x.id+": "+(x.error||("HTTP "+x.status)));
   allNote.textContent=ids.length+"대 시작"+(bad.length?" · "+bad.join(" · "):"");
   if(bad.length)allNote.classList.add("dev-err");
   await Promise.all(ids.map(id=>watchUpdate(id)));
   allNote.textContent="모두 끝났습니다. 각 기기의 결과를 확인하세요."+(bad.length?" 시작 못 한 기기: "+bad.join(" · "):"");
  }catch(err){allNote.textContent=err.message||String(err);allNote.classList.add("dev-err");}
  allBtn.disabled=false;
 });
 async function checkUpdate(id){
  const t=tiles.get(id),note=t.querySelector(".dev-note"),up=t.querySelector("[data-orca-update]");
  note.textContent="릴리스와 비교하는 중…";note.classList.remove("dev-err");
  const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/orca/update",{cache:"no-store"}),o=await r.json();
  if(!r.ok){note.textContent=o.error||("HTTP "+r.status);note.classList.add("dev-err");return;}
  if(o.error){note.textContent=o.error;note.classList.add("dev-err");return;}
  const version=o.current?(o.installed||"")+" 최신":(o.installed||"?")+" → "+(o.latest||"?");
  if(o.supported===false){
   up.disabled=true;up.title=o.reason||"이 기기는 여기서 업데이트할 수 없습니다";
   note.textContent=version+" · "+up.title;note.classList.add("dev-err");
   return;
  }
  if(o.current){
   up.disabled=true;up.title="이미 최신입니다";
   note.textContent=version;
  }else{
   up.disabled=false;up.title="";
   note.textContent=version;
  }
 }
 function showUpdate(id,o){
  // 진행은 타일 메모에만 적는다. 페어링 패널(.dev-orca)은 여기서 열지 않는다.
  const t=tiles.get(id),note=t.querySelector(".dev-note"),u=o.update,line=updateText(u);
  if(!line)return;
  note.textContent=line;note.classList.toggle("dev-err",u.state==="failed");
  if(u.state==="done"&&(u.app==="current"||u.app==="updated")){
   const up=t.querySelector("[data-orca-update]");
   up.disabled=true;up.title="이미 최신입니다";
  }
 }
 async function fetchOrca(id){
  const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/orca",{cache:"no-store"}),o=await r.json();
  if(!r.ok)throw new Error(o.error||("HTTP "+r.status));
  return o;
 }
 async function runUpdate(url,id){
  const t=tiles.get(id),note=t.querySelector(".dev-note");
  note.textContent="업데이트 요청 중…";note.classList.remove("dev-err");
  const r=await fetch(url,{method:"POST",headers:{"X-Jarvis-Action":"orca-update"},cache:"no-store"}),o=await r.json();
  if(!r.ok){note.textContent=o.error||("HTTP "+r.status);note.classList.add("dev-err");return false;}
  const x=await watchUpdate(id);
  const u=x&&x.update;
  return !!(u&&u.state==="done"&&(u.app==="current"||u.app==="updated"));
 }
 async function watchUpdate(id){
  // deb 받기·설치·스킬까지 수십 분이 걸릴 수 있다. 페어링 QR 은 그리지 않는다.
  for(let n=0;n<480;n++){
   let x;
   try{x=await fetchOrca(id);}
   catch(err){
    const note=tiles.get(id).querySelector(".dev-note");
    note.textContent=err.message||String(err);note.classList.add("dev-err");
    return null;
   }
   showUpdate(id,x);
   if(!x.update||x.update.state!=="running")return x;
   await new Promise(res=>setTimeout(res,5000));
  }
  const t=tiles.get(id),note=t&&t.querySelector(".dev-note");
  if(note){note.textContent="40분이 지나도 업데이트가 끝나지 않았습니다";note.classList.add("dev-err");}
  return null;
 }
 load();
})();
