/* 랩 05 미리보기: ?v=a|b|c. 같은 샘플(data.js)로 세 가지 배치를 그린다.
 * 여정: 상태 확인 → 계획 미리 보기(위험 등급·막힌 단계·plan_id) → 실행(승인 필요 단계는 승인 대기로) → 결과(done·current·deferred·stale·failed). */
(function(){
 "use strict";
 const P=window.JARVIS,{html}=P,L=window.SETUP_LAB,$=s=>document.querySelector(s);
 const V={a:"기기 × 모듈 행렬",b:"모듈별 탭",c:"할 일 먼저 + 기기 서랍"};
 const SUB={a:"기기 9대 × 모듈 7개를 한 표로 봅니다. 칸을 누르면 그 줄 아래에서 계획·실행·결과가 이어집니다.",
  b:"모듈을 하나 고르고 그 모듈의 기기 목록에서 작업합니다. doctor 는 발견 항목 종류별로 묶습니다.",
  c:"승인 대기와 할 일을 먼저 보여 주고, 기기 상세는 옆(모바일은 아래)에서 여는 서랍으로 봅니다."};
 const qs=new URLSearchParams(location.search);
 const vid=V[qs.get("v")]?qs.get("v"):"a";
 if(qs.has("embed"))document.documentElement.classList.add("embed");
 const K="jarvis.lab05."+vid+".";
 const get=(k,d)=>{try{const v=sessionStorage.getItem(K+k);return v===null?d:JSON.parse(v);}catch(e){return d;}};
 const put=(k,v)=>{try{sessionStorage.setItem(K+k,JSON.stringify(v));}catch(e){}};
 const S={sel:get("sel",null),tab:get("tab","orca"),drawer:null};
 const LEGACY=L.MODULES.filter(m=>!m.migrated);
 const HEALTH={ok:["ok","정상"],degraded:["warn","저하"],down:["crit","중단"]};

 // ---------------------------------------------------------------- 작은 조각
 const pill=(t,label)=>html`<span class="pill" data-tone="${t}"><i class="shape"></i>${label}</span>`;
 const risk=r=>html`<span class="badge su-risk" data-tone="${L.RISK[r][0]}">${L.RISK[r][1]}</span>`;
 const need=r=>L.AUTO.has(r)?html`<span class="su-auto">자동</span>`:html`<span class="su-need">승인 필요</span>`;
 const act=(a,id,label,cls,extra)=>html`<button type="button" class="${"su-btn "+(cls||"")}" data-act="${a}" data-id="${id||""}" ${extra||""}>${label}</button>`;
 const ver=v=>v?html`<b class="mono">${v}</b>`:html`<b class="su-none">없음</b>`;
 const dev=id=>L.DEV[id];
 const osLine=d=>html`<span class="su-os">${L.OS[d.os]} · ${d.model}</span>`;
 const reach=d=>d.reach?"":html`<span class="su-off">연결 안 됨 · 마지막 응답 ${L.ago(d.seen)}</span>`;
 const worst=xs=>["crit","warn","info","muted"].find(t=>xs.some(x=>L.KIND[x.kind].tone===t))||"ok";

 // 진행 단계 표시: 상태 → 계획 → 승인 → 실행 → 결과
 function flow(j){
  const idx=!j?0:{planning:1,plan:1,pending:2,running:3,result:4}[j.phase];
  const auto=j&&j.plan&&!L.needs(j.plan).length;
  return html`<ol class="su-flow" aria-label="진행 단계">${["상태","계획","승인","실행","결과"].map((n,i)=>html`<li data-s="${i<idx?"past":i===idx?"now":"next"}" ${i===idx?'aria-current="step"':""}>${i===2&&auto&&idx>=1?"승인 없음":n}</li>`)}</ol>`;
 }
 function status(d){
  const o=d.orca,st=L.orcaState(d),h=HEALTH[o.health],x=o.details;
  const facts=[];
  if(x.server)facts.push(html`<span>서버 <b>${x.server}</b>${x.adopted?" (수용)":""}</span>`);
  if(o.installed&&d.reach)facts.push(html`<span>열린 터미널 <b>${x.terminals==null?"셀 수 없음":x.terminals+"개"}</b></span>`);
  if(o.installed)facts.push(html`<span>소유 <b>${o.owned?"jarvis 설치":"수용·기록만"}</b></span>`);
  return html`<div class="su-status"><div class="su-ver"><span class="su-k">설치본</span>${ver(o.version)}${o.installed&&o.version!==L.LATEST?html`<span class="su-to">→ ${L.LATEST}</span>`:""}</div>${pill(st.tone,st.label)}${d.reach&&o.installed?html`<span class="badge" data-tone="${h[0]}">상태 ${h[1]}</span>`:""}${facts.length?html`<p class="su-facts">${facts}</p>`:""}${x.note?html`<p class="su-note" data-tone="${o.health==="degraded"?"warn":"muted"}">${x.note}</p>`:""}</div>`;
 }
 const RTONE=r=>L.STEP[r][0];
 function steps(j){
  const p=j.plan,got=Object.fromEntries((j.phase==="result"?j.result.steps:j.partial||[]).map(s=>[s.id,s]));
  const live=j.phase==="running";
  return html`<ol class="su-steps">${p.steps.map((s,i)=>{
   const b=L.blockedOf(p,s.id),r=got[s.id],now=live&&i===j.cursor&&!r;
   return html`<li class="su-step" data-blocked="${!!b}" ${r?html`data-r="${r.result}"`:""}>
    <span class="su-i" ${r?html`data-tone="${RTONE(r.result)}"`:""} aria-hidden="true">${r?L.STEP[r.result][1]:now?"…":i+1}</span>
    <div class="su-sm"><b>${s.summary}</b><code>${s.argv.join(" ")}</code>${b?html`<p class="su-block"><b>막힘</b> ${b.reason}</p>`:""}${r?html`<p class="su-rmsg" data-tone="${RTONE(r.result)}"><span class="sh-sr">${L.STEP[r.result][2]}: </span>${r.message}</p>`:now?html`<p class="su-rmsg su-live">실행 중…</p>`:""}</div>
    <div class="su-rk">${risk(s.risk)}${need(s.risk)}</div></li>`;})}</ol>`;
 }
 function planSum(p){
  const n=L.needs(p),risks=[...new Set(n.map(s=>L.RISK[s.risk][1]))];
  return html`<p class="su-psum"><span>단계 <b>${p.steps.length}</b></span><span>자동 <b>${p.steps.length-n.length}</b></span><span data-tone="${n.length?"warn":"ok"}">승인 필요 <b>${n.length}</b>${risks.length?" · "+risks.join("·"):""}</span>${p.blocked.length?html`<span data-tone="crit">막힘 <b>${p.blocked.length}</b></span>`:""}</p>`;
 }
 function planHead(j,d){
  const p=j.plan;
  const what=L.ACTION[p.action]+(p.action==="update"&&p.steps.length>1?" "+p.from+" → "+p.to:p.action==="install"?" "+p.to:"");
  return html`<header class="su-ph"><div class="su-pt"><span class="su-k">계획 · ${d.host}</span><b>Orca ${what}</b></div><code class="su-pid" title="plan_id(샘플). 앞 12자만 보인다">plan_id ${L.short(p.plan_id)}…</code></header>`;
 }
 function result(j){
  const r=j.result,m=L.RESULT[r.status],extra=r.steps.find(s=>!j.plan.steps.some(x=>x.id===s.id));
  return html`<div class="su-result" data-tone="${m.tone}" role="status"><i class="shape" aria-hidden="true"></i><div><b>${m.label}</b> <span class="su-code">${r.status}</span><p>${extra?extra.message:m.desc}</p></div></div>`;
 }
 function acts(j,id){
  const p=j.plan;
  if(j.phase==="plan"){
   const n=L.needs(p).length;
   return html`${p.blocked.length?html`<p class="su-warn">막힌 단계가 있어 실행하면 <b>failed</b> 로 끝납니다. 막힌 이유를 먼저 풀어야 합니다.</p>`:""}<div class="su-acts">${n?act("request",id,"승인 요청 보내기","primary"):act("request",id,p.steps.length===1?"확인 실행":"바로 실행","primary")}${act("close",id,"닫기")}<span class="su-hint">${n?"승인 대기 목록에 올리고 휴대폰으로 알립니다":"읽기·갱신 단계뿐이라 승인 없이 실행합니다"}</span></div>`;
  }
  if(j.phase==="pending")return html`<div class="su-acts">${act("approve",id,"승인하고 실행","primary")}${act("reject",id,"거절")}<span class="su-hint">요청 ${L.ago(j.asked)} · 휴대폰 알림 보냄 · 계획과 같을 때만 실행</span></div>`;
  if(j.phase==="running")return html`<p class="su-hint su-run" role="status">실행 중 · ${j.cursor||0}/${j.plan.steps.length}단계</p>`;
  const s=j.result.status;
  return html`<div class="su-acts">${s==="deferred"?act("run",id,"지금 다시 시도","primary"):""}${s==="stale"||s==="failed"?act("plan",id,"계획 다시 세우기","primary"):""}${act("close",id,"닫기")}</div>`;
 }
 // Orca 한 기기의 전체 흐름(세 시안 공용)
 function orca(d,opt){
  const j=L.job(d.id),o=opt||{};
  if(!d.reach)return html`<div class="su-orca">${o.noStatus?"":status(d)}<p class="su-muted">기기에 닿지 않아 계획을 세울 수 없습니다. 위 값은 ${L.ago(d.seen)}에 마지막으로 본 상태입니다.</p></div>`;
  const label=!d.orca.installed?"설치 계획 미리 보기":"계획 미리 보기";
  return html`<div class="su-orca" data-flow="${d.id}" tabindex="-1">${o.noStatus?"":status(d)}${flow(j)}${!j?html`<div class="su-acts">${act("plan",d.id,label,"primary")}<span class="su-hint">계획은 아무것도 바꾸지 않습니다(dry-run)</span></div>`:j.phase==="planning"?html`<p class="su-hint su-run" role="status">계획 세우는 중…</p>`:html`<section class="su-plan" aria-label="실행 계획">${planHead(j,d)}${j.phase==="result"?html`${result(j)}${acts(j,d.id)}`:""}${steps(j)}${planSum(j.plan)}${j.phase==="result"?"":acts(j,d.id)}</section>`}</div>`;
 }
 function fbody(x){
  return {
   secret:()=>html`<code>${x.file}:${x.line}</code> · 키 <code>${x.key}</code> · <span class="su-hide">값 숨김</span>`,
   drift:()=>html`<code>${x.file}</code> 에 은퇴한 훅 <code>${x.hook}</code> 이 남아 있음`,
   pathdup:()=>html`<code>${x.name}</code> 두 벌 · 우선 <code>${x.first}</code> · 가려짐 ${x.also.map((a,i)=>html`${i?", ":""}<code>${a}</code>`)}`,
   missing:()=>html`<code>${x.name}</code> 없음 · 설치 명령 <code>${x.cmd}</code>`,
   skill:()=>html`<code>~/.agents/skills/${x.name}</code>${x.link?" (링크)":""} · lock 에 없음, 그대로 둠`}[x.kind]();
 }
 const finding=x=>html`<li class="su-find" data-tone="${L.KIND[x.kind].tone}"><i class="shape" aria-hidden="true"></i><div><b>${L.KIND[x.kind].label}</b><p>${fbody(x)}</p></div></li>`;
 function doctor(d){
  if(!d.reach)return html`<p class="su-muted">연결되지 않아 점검하지 못했습니다.</p>`;
  const busy=L.busy.has(d.id),xs=d.findings;
  return html`<div class="su-doc" aria-busy="${busy}"><p class="su-meta"><span class="badge">읽기 전용</span><span>${busy?"점검 중…":L.ago(d.checked)+" 점검"}</span>${act("recheck",d.id,busy?"점검 중…":"다시 점검","",busy?"disabled":"")}</p>${xs.length?html`<ul class="su-finds">${xs.map(finding)}</ul>`:html`<p class="su-clean" data-tone="ok"><i class="shape" aria-hidden="true"></i>발견 항목 없음</p>`}</div>`;
 }
 const legacyNote=()=>html`<p class="su-muted">아직 jarvis 로 옮기지 않았습니다. 지금은 ai-setup 이 관리하며 여기서는 실행할 수 없습니다. 이전 순서: ${L.ORDER}</p>`;
 function legacyList(d){
  return html`<ul class="su-legacy">${LEGACY.map(m=>html`<li><b>${m.name}</b><span>${d.reach?"ai-setup 관리 중":"–"}</span></li>`)}</ul>`;
 }
 function counts(){
  const r=L.DEVICES.filter(d=>d.reach);
  return {pending:L.pending().length,upd:r.filter(d=>["갱신 가능","설치 안 됨","재구성 필요"].includes(L.orcaState(d).label)).length,
   finds:L.findings().length,secrets:L.findings().filter(f=>f.x.kind==="secret").length,off:L.DEVICES.length-r.length};
 }
 function chips(){
  const c=counts();
  return html`<div class="su-chips"><span data-tone="${c.pending?"warn":"ok"}"><i class="shape"></i>승인 대기 <b>${c.pending}</b></span><span data-tone="${c.upd?"info":"ok"}"><i class="shape"></i>Orca 할 일 <b>${c.upd}</b></span><span data-tone="${c.secrets?"crit":c.finds?"warn":"ok"}"><i class="shape"></i>doctor 발견 <b>${c.finds}</b></span><span data-tone="${c.off?"muted":"ok"}"><i class="shape"></i>연결 안 됨 <b>${c.off}</b></span></div>`;
 }
 function approvals(compact){
  const xs=L.pending();
  if(!xs.length)return "";
  return html`<div class="tile su-appr"><header class="tile-head"><h3 class="tile-title">승인 대기</h3><span class="badge" data-tone="warn">${xs.length}건</span><span class="tile-aside">휴대폰에도 같은 알림이 갔습니다. 어느 쪽에서 눌러도 결과는 같습니다.</span></header><ul>${xs.map(j=>{const d=dev(j.plan.device),n=L.needs(j.plan);
   return html`<li class="su-ar"><div class="su-ar-t"><b>${d.host}</b><span>Orca ${L.ACTION[j.plan.action]} ${j.plan.from} → ${j.plan.to}</span><code class="su-pid">${L.short(j.plan.plan_id)}…</code></div><div class="su-ar-n">${n.map(s=>html`<span class="su-ar-s">${risk(s.risk)}${s.summary}</span>`)}</div><div class="su-acts">${act("approve",d.id,"승인하고 실행","primary")}${act("reject",d.id,"거절")}${compact?act(compact,d.id,"계획 전체 보기"):""}<span class="su-hint">${L.ago(j.asked)} 요청</span></div></li>`;})}</ul></div>`;
 }

 // ---------------------------------------------------------------- A: 기기 × 모듈 행렬
 function A(){
  const sel=S.sel;
  const cellO=d=>{const st=L.orcaState(d),on=sel&&sel.dev===d.id&&sel.mod==="orca";
   return html`<td data-label="Orca"><button type="button" class="su-cell" data-act="cell" data-id="${d.id}" data-mod="orca" data-tone="${st.tone}" aria-expanded="${on}">${ver(d.orca.version)}<span data-tone="${st.tone}"><i class="shape" aria-hidden="true"></i>${st.label}</span></button></td>`;};
  const cellD=d=>{const on=sel&&sel.dev===d.id&&sel.mod==="doctor";
   if(!d.reach)return html`<td data-label="doctor"><button type="button" class="su-cell" data-act="cell" data-id="${d.id}" data-mod="doctor" data-tone="muted" aria-expanded="${on}"><b>–</b><span data-tone="muted"><i class="shape" aria-hidden="true"></i>점검 못 함</span></button></td>`;
   const xs=d.findings,t=worst(xs);
   return html`<td data-label="doctor"><button type="button" class="su-cell" data-act="cell" data-id="${d.id}" data-mod="doctor" data-tone="${t}" aria-expanded="${on}"><b>${xs.length?"발견 "+xs.length:"이상 없음"}</b><span>${xs.length?[...new Set(xs.map(x=>L.KIND[x.kind].label))].join(" · "):L.ago(d.checked)+" 점검"}</span></button></td>`;};
  return html`${chips()}${approvals("selorca")}<div class="tile su-mx"><table><thead><tr><th scope="col" rowspan="2">기기</th><th scope="colgroup" colspan="2" class="su-gh">jarvis 모듈</th><th scope="colgroup" colspan="${LEGACY.length}" class="su-gh su-gh-l">아직 ai-setup 이 관리</th></tr><tr><th scope="col">Orca <span class="su-k">최신 ${L.LATEST}</span></th><th scope="col">doctor</th>${LEGACY.map(m=>html`<th scope="col" class="su-lgh">${m.name}</th>`)}</tr></thead><tbody>${L.DEVICES.map(d=>{
   const open=sel&&sel.dev===d.id;
   return html`<tr data-off="${!d.reach}" ${open?'class="su-open"':""}><th scope="row"><b>${d.host}</b>${osLine(d)}${reach(d)}<span class="su-lg-m">훅·하네스·스킬·플러그인·MCP — ${d.reach?"ai-setup 관리 중":"–"}</span></th>${cellO(d)}${cellD(d)}${LEGACY.map(m=>html`<td class="su-lg" data-label="${m.name}">${d.reach?"ai-setup":"–"}</td>`)}</tr>${open?html`<tr class="su-xrow"><td colspan="${2+1+LEGACY.length}"><div class="su-x"><div class="su-x-h"><h3>${d.host} · ${sel.mod==="orca"?"Orca":"doctor"}</h3>${act("unsel","","접기")}</div>${sel.mod==="orca"?orca(d):doctor(d)}</div></td></tr>`:""}`;})}</tbody></table></div><p class="su-foot">${legacyNote()}</p>`;
 }

 // ---------------------------------------------------------------- B: 모듈별 탭
 function B(){
  const c=counts(),tab=S.tab;
  const tabs=html`<div class="seg seg-lg su-tabs" role="tablist" aria-label="모듈">${L.MODULES.map(m=>{const n=m.id==="orca"?c.pending+c.upd:m.id==="doctor"?c.finds:0;
   return html`<button type="button" role="tab" data-act="tab" data-id="${m.id}" aria-selected="${tab===m.id}" aria-pressed="${tab===m.id}">${m.name}${n?html`<span class="su-tn" data-tone="${m.id==="orca"&&c.pending?"warn":m.id==="doctor"&&c.secrets?"crit":"info"}">${n}</span>`:""}${m.migrated?"":html`<span class="su-tl">ai-setup</span>`}</button>`;})}</div>`;
  let body;
  if(tab==="orca"){
   const RANK=["최신","갱신 가능","재구성 필요","승인 대기","계획 세우는 중","실행 중","보류","계획 바뀜","실패","설치 안 됨","연결 안 됨"],by={};
   L.DEVICES.map(d=>L.orcaState(d)).sort((a,b)=>RANK.indexOf(a.label)-RANK.indexOf(b.label)).forEach(l=>{(by[l.label]=by[l.label]||{t:l.tone,n:0}).n++;});
   const todo=L.DEVICES.filter(d=>d.reach&&!L.job(d.id)&&["갱신 가능","설치 안 됨","재구성 필요"].includes(L.orcaState(d).label));
   body=html`<div class="tile su-mod"><header class="tile-head wrap-head"><h3 class="tile-title">Orca</h3><span class="badge">최신 ${L.LATEST}</span><span class="tile-aside">jarvis 모듈 · 시범</span></header><div class="su-dist" role="img" aria-label="${Object.entries(by).map(([k,v])=>k+" "+v.n+"대").join(", ")}">${Object.entries(by).map(([k,v])=>html`<i data-tone="${v.t}" style="${"flex:"+v.n}"></i>`)}</div><p class="su-legend">${Object.entries(by).map(([k,v])=>html`<span data-tone="${v.t}"><i class="shape"></i>${k} <b>${v.n}</b></span>`)}</p>${todo.length?html`<div class="su-acts">${act("planall","","할 일이 있는 "+todo.length+"대 계획 세우기","primary")}<span class="su-hint">${todo.map(d=>d.host).join(" · ")}</span></div>`:""}</div>
   <ul class="su-rows">${L.DEVICES.map(d=>{const j=L.job(d.id),st=L.orcaState(d);
    return html`<li class="tile su-row" data-off="${!d.reach}"><div class="su-rh"><div class="su-rn"><b>${d.host}</b>${osLine(d)}${reach(d)}</div><div class="su-ver">${ver(d.orca.version)}${d.orca.installed&&d.orca.version!==L.LATEST?html`<span class="su-to">→ ${L.LATEST}</span>`:""}</div>${pill(st.tone,st.label)}<div class="su-rb">${!d.reach?"":j?"":act("plan",d.id,d.orca.installed?"계획 미리 보기":"설치 계획","")}</div></div>${j?orca(d,{noStatus:true}):d.reach&&(d.orca.details.note||d.orca.health!=="ok")?html`<p class="su-note" data-tone="${d.orca.health==="degraded"?"warn":"muted"}">${d.orca.details.note||""}</p>`:""}</li>`;})}</ul>`;
  }else if(tab==="doctor"){
   const all=L.findings(),reach=L.DEVICES.filter(d=>d.reach);
   body=html`<div class="tile su-mod"><header class="tile-head wrap-head"><h3 class="tile-title">doctor</h3><span class="badge">읽기 전용</span><span class="tile-aside">아무것도 바꾸지 않습니다 · 시크릿 값은 표시하지 않습니다</span></header><p class="su-legend">${L.KIND_ORDER.map(k=>{const n=all.filter(f=>f.x.kind===k).length;return html`<span data-tone="${n?L.KIND[k].tone:"ok"}"><i class="shape"></i>${L.KIND[k].label} <b>${n}</b></span>`;})}</p><div class="su-acts">${act("recheckall","","연결된 "+reach.length+"대 다시 점검")}<span class="su-hint">이상 없음: ${reach.filter(d=>!d.findings.length).map(d=>d.host).join(" · ")||"없음"}</span></div></div>
   ${L.KIND_ORDER.map(k=>{const xs=all.filter(f=>f.x.kind===k);if(!xs.length)return "";return html`<section class="su-kind"><h3 class="su-gh2"><span data-tone="${L.KIND[k].tone}"><i class="shape"></i></span>${L.KIND[k].label}<span>${xs.length}건</span></h3><p class="su-muted">${L.KIND[k].hint}</p><ul class="su-finds tile">${xs.map(({d,x})=>html`<li class="su-find" data-tone="${L.KIND[k].tone}"><b class="su-fd" aria-busy="${L.busy.has(d.id)}">${d.host}</b><div><p>${fbody(x)}</p></div></li>`)}</ul></section>`;})}
   <section class="su-kind"><h3 class="su-gh2">점검하지 못한 기기<span>${L.DEVICES.length-reach.length}대</span></h3><ul class="su-finds tile">${L.DEVICES.filter(d=>!d.reach).map(d=>html`<li class="su-find" data-tone="muted"><b class="su-fd">${d.host}</b><div><p>연결 안 됨 · 마지막 응답 ${L.ago(d.seen)}</p></div></li>`)}</ul></section>`;
  }else{
   const m=L.MODULES.find(x=>x.id===tab);
   body=html`<div class="tile su-mod su-lmod"><header class="tile-head wrap-head"><h3 class="tile-title">${m.name}</h3><span class="badge" data-tone="muted">ai-setup 관리 중</span><span class="tile-aside">${m.what}</span></header>${legacyNote()}<ol class="su-order">${["Orca","doctor","훅","하네스","스킬·플러그인·MCP"].map(x=>html`<li data-s="${x==="Orca"||x==="doctor"?"past":x.includes(m.name)?"now":"next"}">${x}</li>`)}</ol></div><ul class="su-grid">${L.DEVICES.map(d=>html`<li data-off="${!d.reach}"><b>${d.host}</b><span>${d.reach?"ai-setup 관리 중":"연결 안 됨"}</span></li>`)}</ul>`;
  }
  return html`${tabs}<div class="su-tabp" role="tabpanel">${body}</div>`;
 }

 // ---------------------------------------------------------------- C: 할 일 먼저 + 기기 서랍
 function C(){
  const c=counts();
  const live=L.DEVICES.map(d=>L.job(d.id)).filter(j=>j&&(j.phase==="running"||j.phase==="result"));
  const todo=[];
  L.DEVICES.forEach(d=>{const st=L.orcaState(d);if(d.reach&&!L.job(d.id)&&["갱신 가능","설치 안 됨","재구성 필요"].includes(st.label))todo.push({t:st.tone,d,what:html`<b>Orca ${st.label}</b> <span>${d.orca.installed?d.orca.version+(d.orca.version!==L.LATEST?" → "+L.LATEST:""):"→ "+L.LATEST}</span>`,a:act("planopen",d.id,st.label==="설치 안 됨"?"설치 계획":"계획 보기")});});
  L.findings().filter(f=>f.x.kind==="secret"||f.x.kind==="drift").forEach(({d,x})=>todo.push({t:L.KIND[x.kind].tone,d,what:html`<b>${L.KIND[x.kind].label}</b> <span>${x.kind==="secret"?x.file+":"+x.line+" · 키 "+x.key:x.hook+" ← "+x.file}</span>`,a:act("open",d.id,"자세히")}));
  L.DEVICES.filter(d=>!d.reach).forEach(d=>todo.push({t:"muted",d,what:html`<b>연결 안 됨</b> <span>마지막 응답 ${L.ago(d.seen)}</span>`,a:act("open",d.id,"기기 보기")}));
  const rest=L.findings().filter(f=>f.x.kind!=="secret"&&f.x.kind!=="drift").length;
  const kpi=html`<div class="bento su-kpis"><div class="tile span-3 m-3 su-kpi" data-tone="${c.pending?"warn":"ok"}"><span class="su-k">승인 대기</span><b>${c.pending}</b><span class="su-muted">휴대폰 알림과 같은 목록</span></div><div class="tile span-3 m-3 su-kpi" data-tone="${c.upd?"info":"ok"}"><span class="su-k">Orca 할 일</span><b>${c.upd}</b><span class="su-muted">갱신·설치·재구성</span></div><div class="tile span-3 m-3 su-kpi" data-tone="${c.secrets?"crit":c.finds?"warn":"ok"}"><span class="su-k">doctor 발견</span><b>${c.finds}</b><span class="su-muted">평문 시크릿 ${c.secrets}건 포함</span></div><div class="tile span-3 m-3 su-kpi" data-tone="${c.off?"muted":"ok"}"><span class="su-k">연결 안 됨</span><b>${c.off}</b><span class="su-muted">${L.DEVICES.filter(d=>!d.reach).map(d=>d.host).join(" · ")||"없음"}</span></div></div>`;
  const liveT=live.length?html`<div class="tile su-live-t"><header class="tile-head"><h3 class="tile-title">방금 실행한 작업</h3></header>${live.map(j=>{const d=dev(j.dev);return html`<div class="su-lj"><div class="su-x-h"><h4>${d.host} · Orca ${L.ACTION[j.plan.action]}</h4>${act("open",d.id,"서랍에서 보기")}</div>${j.phase==="result"?result(j):""}${j.phase==="running"?html`<div class="su-prog"><i style="${"--v:"+((j.cursor||0)/Math.max(1,j.plan.steps.length))}"></i></div><p class="su-hint su-run">실행 중 ${j.cursor||0}/${j.plan.steps.length}</p>`:acts(j,d.id)}</div>`;})}</div>`:"";
  const todoT=html`<div class="tile su-todo"><header class="tile-head"><h3 class="tile-title">할 일</h3><span class="badge">${todo.length}</span></header>${todo.length?html`<ul>${todo.map(t=>html`<li data-tone="${t.t}"><i class="shape" aria-hidden="true"></i><div class="su-tw"><span class="su-th">${t.d.host}</span><p>${t.what}</p></div>${t.a}</li>`)}</ul>`:html`<p class="su-muted">할 일이 없습니다.</p>`}${rest?html`<p class="su-muted">그 밖의 doctor 발견 ${rest}건(PATH 중복·미설치 하네스·lock 밖 스킬)은 기기 서랍에서 봅니다.</p>`:""}</div>`;
  const hist=html`<div class="tile su-hist"><header class="tile-head"><h3 class="tile-title">최근 결과</h3></header><ol>${L.history.slice(0,7).map(h=>{const m=h.status==="rejected"?{tone:"muted",label:"거절"}:L.RESULT[h.status];return html`<li data-tone="${m.tone}"><i class="shape" aria-hidden="true"></i><div><b>${h.dev}</b> · Orca ${h.text}<span>${m.label} · ${L.ago(h.at)}</span></div></li>`;})}</ol></div>`;
  const devs=html`<div class="sec-head su-sh"><h3>기기</h3><span class="sec-sub">누르면 서랍에서 모듈별 상세를 봅니다</span></div><ul class="su-devs">${L.DEVICES.map(d=>{const st=L.orcaState(d),xs=d.findings||[];return html`<li><button type="button" class="tile su-dev" data-act="open" data-id="${d.id}" data-off="${!d.reach}"><span class="su-dh"><b>${d.host}</b><span class="su-os">${L.OS[d.os]}</span></span><span class="su-dl"><span class="su-k">Orca</span>${ver(d.orca.version)}${pill(st.tone,st.label)}</span><span class="su-dl"><span class="su-k">doctor</span>${d.reach?html`<span data-tone="${worst(xs)}" class="su-dn"><i class="shape" aria-hidden="true"></i>${xs.length?"발견 "+xs.length:"이상 없음"}</span>`:html`<span class="su-muted">점검 못 함</span>`}</span></button></li>`;})}</ul>`;
  return html`${kpi}<div class="bento su-cgrid"><div class="span-7 su-col">${approvals("open")}${liveT}${todoT}</div><div class="span-5 su-col">${hist}</div></div>${devs}`;
 }
 const sheet=$("#suSheet");
 function drawer(){
  if(!S.drawer||!sheet)return;
  const d=dev(S.drawer);
  P.render($("#suSheetHead"),html`<div class="sht-top">${L.OS[d.os]} · ${d.model}</div><h2 id="suSheetTitle">${d.host}</h2><div class="sht-row">${d.reach?pill("ok","연결됨"):pill("muted","연결 안 됨 · "+L.ago(d.seen))}</div>`);
  P.render($("#suSheetBody"),html`<div><h3 class="grp-title">Orca</h3><div class="grp su-grp">${orca(d)}</div></div><div><h3 class="grp-title">doctor</h3><div class="grp su-grp">${doctor(d)}</div></div><div><h3 class="grp-title">아직 ai-setup 이 관리</h3><div class="grp su-grp">${legacyList(d)}${legacyNote()}</div></div>`);
 }
 function openDrawer(id){S.drawer=id;drawer();if(!sheet.open)sheet.showModal();}
 if(sheet)sheet.addEventListener("close",()=>{S.drawer=null;});

 // ---------------------------------------------------------------- 그리기·이벤트
 const R={a:A,b:B,c:C};
 function draw(){
  const a=document.activeElement,sel=a&&a.dataset&&a.dataset.act?'[data-act="'+a.dataset.act+'"][data-id="'+(a.dataset.id||"")+'"]'+(a.dataset.mod?'[data-mod="'+a.dataset.mod+'"]':""):null;
  const fid=a&&a.closest&&a.closest("[data-flow]")?a.closest("[data-flow]").dataset.flow:null;
  P.render($("#suBody"),R[vid]());
  drawer();
  if(sel&&(!document.activeElement||document.activeElement===document.body)){
   const t=document.querySelector(sel)||(fid&&document.querySelector('[data-flow="'+fid+'"]'));
   if(t)t.focus({preventScroll:true});
  }
  put("sel",S.sel);put("tab",S.tab);
 }
 L.on(draw);
 document.addEventListener("click",e=>{
  const t=e.target.closest("button,a");if(!t)return;
  if(t.dataset.themeSet){window.JarvisLabTheme(t.dataset.themeSet);marks();return;}
  if(t.hasAttribute("data-close")){sheet.close();return;}
  const a=t.dataset.act,id=t.dataset.id;if(!a)return;
  ({cell:()=>{S.sel=S.sel&&S.sel.dev===id&&S.sel.mod===t.dataset.mod?null:{dev:id,mod:t.dataset.mod};draw();},
   unsel:()=>{S.sel=null;draw();},
   selorca:()=>{S.sel={dev:id,mod:"orca"};draw();const f=document.querySelector('[data-flow="'+id+'"]');if(f){f.focus({preventScroll:true});f.scrollIntoView({block:"center",behavior:"smooth"});}},
   tab:()=>{S.tab=id;draw();},
   plan:()=>L.plan(id),request:()=>L.request(id),approve:()=>L.approve(id),reject:()=>L.reject(id),run:()=>L.run(id),close:()=>L.close(id),
   recheck:()=>L.recheck(id),
   recheckall:()=>L.DEVICES.filter(d=>d.reach).forEach(d=>L.recheck(d.id)),
   planall:()=>L.DEVICES.filter(d=>d.reach&&!L.job(d.id)&&["갱신 가능","설치 안 됨","재구성 필요"].includes(L.orcaState(d).label)).forEach(d=>L.plan(d.id)),
   open:()=>openDrawer(id),
   planopen:()=>{openDrawer(id);L.plan(id);}
  })[a]?.();
 });
 function marks(){const cur=window.JarvisLab.getPref("theme","system");document.querySelectorAll("[data-theme-set]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.themeSet===cur)));}
 window.addEventListener("storage",marks);
 $("#suName").textContent="시안 "+vid.toUpperCase()+" · "+V[vid];$("#suSub").textContent=SUB[vid];
 document.title=V[vid]+" · AI 셋업 시안";
 marks();draw();
 setInterval(draw,30000); // 'n분 전' 표시를 갱신한다
})();
