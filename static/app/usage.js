/* core 홈의 AI 구독 사용량(설정 devices 가 있는 jarvis 에서만 보인다). 디자인 랩 04 의 C(한도 레이더) + 자세히 모드.
 * /api/devices/usage 가 기기마다 게이트웨이 /api/orca/usage(Orca accounts.list 정규화)를 모아 준다. 모아 두지 않고 열 때마다 묻는다.
 * 간략히: 기기 × 프로바이더 칸에 계정별 최대 사용률의 평균, 누르면 계정별 상세. 자세히: 칸 안에 계정마다 한도 창을 모두 펼친다(행이 길어진다).
 * 보기 방식은 localStorage jarvis.usage.mode 에 둔다. */
(function(){
 "use strict";
 const P=window.JARVIS,{html}=P,$=s=>document.querySelector(s);
 const sec=$("#usage");if(!sec||P.device)return; // 다른 기기를 보는 중이면 숨긴 채로 둔다
 const showNav=on=>document.querySelectorAll('[data-sh-sec="usage"],[data-lsec="usage"]').forEach(a=>a.toggleAttribute("data-core",on));
 const MODE_KEY="jarvis.usage.mode";
 const S={devs:null,prov:"all",hot:false,busy:new Set(),dlg:null,login:null,
  mode:(()=>{try{return localStorage.getItem(MODE_KEY)==="full"?"full":"brief";}catch(e){return "brief";}})()};
 const WIN={session:"5시간",weekly:"주간",fable:"Fable 주간",monthly:"월간"};
 const REASON={out_of_credits:"크레딧 소진",disabled:"사용 안 함"};
 const LOGIN={codex:"Codex",grok:"Grok"}; // 게이트웨이가 device-code 로그인을 띄울 수 있는 CLI

 // ---------------------------------------------------------------- 값
 const pct=n=>n==null?"–":Math.round(n)+"%";
 const tone=p=>p==null?"muted":p>=90?"crit":p>=75?"warn":"ok";
 const left=ts=>{
  if(!ts)return "리셋 시각 모름";
  const m=Math.max(0,Math.round((ts*1000-Date.now())/60000));
  return (m<60?m+"분":m<1440?Math.floor(m/60)+"시간 "+m%60+"분":Math.floor(m/1440)+"일 "+Math.floor(m%1440/60)+"시간")+" 후";
 };
 const at=ts=>ts?new Date(ts*1000).toLocaleString("ko-KR",{month:"numeric",day:"numeric",weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false})+" 리셋":"";
 const isErr=a=>a.status==="error";
 // 확인 실패의 종류. 원문(프로바이더 응답·스택)은 접어 두고 종류와 할 일만 보인다
 const KINDS=[
  ["auth","로그인 만료","로그인이 만료되었거나 풀렸습니다. 다시 로그인하세요.",/delegated-refresh|auth|401|unauthori[sz]ed|sign[- ]?in|log ?in|token|credential/i],
  ["rate","요청 제한","프로바이더가 잠시 요청을 막았습니다. 잠시 뒤 새로고침하세요.",/429|rate.?limit|too many/i],
  ["perm","권한 문제","jarvis 가 Orca 에 접근할 권한이 없습니다.",/관리자 권한|permission|denied|403/i],
  ["net","네트워크 오류","프로바이더에 닿지 못했습니다. 잠시 뒤 새로고침하세요.",/time.?out|timed out|network|connect|ENOTFOUND|EAI_|DNS|fetch failed|socket/i],
  ["server","프로바이더 오류","프로바이더 서버가 오류를 냈습니다. 잠시 뒤 새로고침하세요.",/\b5\d\d\b|server error|unavailable|bad gateway/i],
 ];
 const kind=a=>{const t=(a.failure||"")+" "+(a.error||"");return KINDS.find(k=>k[3].test(t))||["unknown","알 수 없는 오류","사용량을 읽지 못했습니다.",null];};
 const peak=a=>{const v=[...a.windows,...a.buckets].map(w=>w.used);return isErr(a)||!v.length?null:Math.max(...v);};
 const isHot=a=>isErr(a)||a.attention===true||(peak(a)??0)>=75; // attention: 재로그인 등 사람이 손봐야 다시 읽힌다
 // 같은 구독 계정(이메일·조직)은 어느 기기에서 보든 한도가 같다. 이메일이 없는 '기본 계정'은 기기마다 따로 센다.
 const keyOf=(d,p,a)=>p.id+":"+(a.label.includes("@")?a.label.toLowerCase()+"\n"+(a.org||""):d.id+":"+a.label);
 // 여러 기기에 같은 계정이 있으면 사용량을 읽은 쪽, 그중 최근 것을 쓴다(사용 중이 아닌 기기는 아직 안 읽었을 수 있다)
 const rank=a=>[a.windows.length||a.buckets.length?1:0,a.updated_at||0];
 const better=(x,y)=>{const [a,b]=[rank(x),rank(y)];return a[0]!==b[0]?a[0]>b[0]:a[1]>b[1];};

 function state(d){
  const some=(d.providers||[]).length>0; // Orca 가 없거나 실패해도 cswap(Claude)·caam(Codex) 계정은 있을 수 있다
  if(d.status===200&&d.installed===false)return some?["ok","Orca 없음",""]:["muted","Orca 없음","이 기기에는 Orca 가 없습니다."];
  if(d.status===200&&d.error&&some)return ["warn","Orca 응답 없음",d.error+" · 보이는 계정은 cswap·caam 값입니다."];
  if(d.status===200&&d.error)return ["warn","Orca 응답 없음",d.error];
  if(d.status===200)return ["ok","Orca 실행 중",""];
  if(d.status===501)return ["muted","미지원",d.error];
  if(d.status===403)return ["crit","권한 없음",d.error];
  return ["crit","접속 불가",d.error||("HTTP "+d.status)];
 }
 const ok=d=>d.status===200&&((d.providers||[]).length>0||(d.installed!==false&&!d.error));
 const failed=d=>!ok(d)&&!(d.status===200&&d.installed===false)&&d.status!==501; // Orca 없음·갱신 전 jarvis 는 실패가 아니다
 const provs=d=>(ok(d)?d.providers||[]:[]).filter(p=>S.prov==="all"||p.id===S.prov);
 const shown=(d,p)=>p.accounts.filter(a=>!S.hot||isHot(a));
 const filtering=()=>S.prov!=="all"||S.hot;
 // 필터 중에는 사용량을 못 읽은 기기도 '전체'일 때만 보인다(어느 프로바이더인지 모른다)
 const visible=d=>ok(d)?(!filtering()||provs(d).some(p=>shown(d,p).length)):S.prov==="all";
 function uniq(){
  const m=new Map();
  for(const d of S.devs)for(const p of provs(d))for(const a of shown(d,p)){
   const k=keyOf(d,p,a),o=m.get(k);if(!o||better(a,o.a))m.set(k,{a,p});
  }
  return [...m.values()];
 }
 function columns(hotOnly){
  const seen=new Map();
  for(const d of S.devs)for(const p of d.status===200?d.providers||[]:[])
   if(!seen.has(p.id)&&(!hotOnly||p.accounts.some(isHot)))seen.set(p.id,p.name); // 표는 주의 필터 중 빈 열을 뺀다
  return [...seen].map(([id,name])=>({id,name}));
 }

 // ---------------------------------------------------------------- 조각
 const dot=id=>html`<i class="ul-dot" data-p="${id}" aria-hidden="true"></i>`;
 const meter=u=>html`<div class="meter" data-tone="${tone(u)}"><i style="${"--v:"+u/100}"></i></div>`;
 const win=w=>html`<div class="ul-win"><span class="ul-wl">${WIN[w.key]||w.key}</span>${meter(w.used)}<b class="ul-wp" data-tone="${tone(w.used)}">${pct(w.used)}</b><span class="ul-wr" title="${at(w.resets_at)}">${left(w.resets_at)}</span></div>`;
 function extra(e){
  if(!e)return "";
  if(e.unit==="currency")return html`<p class="ul-extra">추가 사용량 ${e.currency==="USD"?"$":""}${e.spent??"–"}${e.limit!=null?" / "+(e.currency==="USD"?"$":"")+e.limit:""}${e.currency&&e.currency!=="USD"?" "+e.currency:""} · ${e.enabled?"켜짐":"꺼짐"+(e.reason?"("+(REASON[e.reason]||e.reason)+")":"")}</p>`;
  if(e.unit==="credits")return html`<p class="ul-extra">추가 크레딧 ${e.unlimited?"무제한":e.balance??"–"}${e.enabled?"":" · 꺼짐"}</p>`;
  return "";
 }
 function body(a){
  if(isErr(a)){const k=kind(a);return html`<p class="ul-err">${k[2]}</p>${a.error?html`<details class="ul-raw"><summary>원문 보기</summary><pre>${a.error}</pre></details>`:""}`;}
  const note=a.note?html`<p class="${a.attention?"ul-err":"ul-note"}">${a.note}</p>`:""; // 마지막으로 읽은 값에 붙는 이유
  if(!a.windows.length&&!a.buckets.length)return note||html`<p class="ul-muted">${a.status==="fetching"?"사용량을 읽는 중입니다.":"아직 사용량을 읽지 않았습니다."}</p>`;
  return html`${note}<div class="ul-wins">${a.windows.map(win)}</div>${a.buckets.length?html`<p class="ul-extra">모델별 · ${a.buckets.map((b,i)=>html`${i?" · ":""}${b.name} <b>${pct(b.used)}</b>`)}</p>`:""}${extra(a.extra)}${a.credits?html`<p class="ul-extra">한도 리셋 크레딧 ${a.credits}개</p>`:""}`;
 }
 const head=a=>html`<div class="ul-ah"><b class="ul-email">${a.label}</b>${a.plan?html`<span class="ul-plan">${a.plan}</span>`:""}${a.org?html`<span class="ul-plan">${a.org}</span>`:""}${a.active?html`<span class="badge" data-tone="info">사용 중</span>`:""}${isErr(a)||a.attention?html`<span class="badge" data-tone="crit">${isErr(a)?kind(a)[1]:"조치 필요"}</span>`:""}</div>`;
 const relog=(d,id,again=true)=>html`<button type="button" class="seg-action" data-login="${d.id}" data-lp="${id}">${LOGIN[id]} ${again?"다시 로그인":"로그인"}</button>`;
 const acct=(a,d,p)=>html`<div class="ul-acct">${head(a)}${body(a)}${(isErr(a)&&kind(a)[0]==="auth"||a.attention)&&(d.logins||[]).includes(p.id)?relog(d,p.id):""}${a.updated_at?html`<p class="ul-age">${P.fmt.ago(a.updated_at)} 확인</p>`:""}</div>`;
 const badge=d=>{const [t,l]=state(d);return html`<span class="badge" data-tone="${t}">${l}</span>`;};
 function devMeta(d){
  const busy=S.busy.has(d.id);
  if((d.status===200&&d.installed===false&&!(d.providers||[]).length)||d.status===501)return ""; // Orca·cswap 모두 없거나 갱신 전 jarvis 는 다시 물어도 같다
  return html`<p class="ul-meta">${d.fetched_at?html`<span>${P.fmt.ago(d.fetched_at)} 수집</span>`:""}<button type="button" class="seg-action ul-ref" data-refresh="${d.id}" ${busy?"disabled":""}>${busy?"읽는 중…":"새로고침"}</button>${(d.logins||[]).filter(id=>!(d.providers||[]).some(p=>p.id===id)).map(id=>relog(d,id,false))}</p>`;
 }

 // ---------------------------------------------------------------- 화면
 function view(){
  const all=uniq(),hot=all.filter(x=>isHot(x.a));
  const bad=S.devs.filter(failed);
  const wins=all.flatMap(({a,p})=>isErr(a)?[]:a.windows.filter(w=>w.used>=50&&w.resets_at).map(w=>({a,p,w}))).sort((x,y)=>x.w.resets_at-y.w.resets_at);
  const next=wins.find(o=>o.w.used>=75);
  const kpi=html`<div class="bento"><div class="tile span-4 m-3 ul-kpi" data-tone="${hot.length?"warn":"ok"}"><span class="ul-wl">주의 필요 계정</span><b>${hot.length}</b><span class="ul-muted">75% 이상 또는 확인 실패 · 같은 계정은 한 번</span></div><div class="tile span-4 m-3 ul-kpi"><span class="ul-wl">다음 리셋(주의 계정)</span><b class="ul-kpi-s">${next?left(next.w.resets_at):"–"}</b><span class="ul-muted">${next?next.a.label+" · "+next.p.name+" "+(WIN[next.w.key]||next.w.key):"주의 계정 없음"}</span></div><div class="tile span-4 m-6 ul-kpi" data-tone="${bad.length?"crit":"ok"}"><span class="ul-wl">읽지 못한 기기</span><b>${bad.length}</b><span class="ul-muted">${bad.map(d=>d.host||d.id).join(" · ")||"없음"}</span></div></div>`;
  const soon=wins.length?html`<div class="tile ul-soon"><h3 class="sub-h">곧 풀리는 한도 (50% 이상)</h3><ol>${wins.slice(0,6).map(({a,p,w})=>html`<li><span class="ul-when" title="${at(w.resets_at)}">${left(w.resets_at)}</span>${dot(p.id)}<span class="ul-email">${a.label}</span><span class="ul-wl">${p.name} ${WIN[w.key]||w.key}</span>${meter(w.used)}<b class="ul-wp" data-tone="${tone(w.used)}">${pct(w.used)}</b></li>`)}</ol></div>`:"";
  const rows=S.devs.filter(visible),cols=columns(S.hot).filter(c=>S.prov==="all"||c.id===S.prov);
  if(!rows.length)return html`${kpi}${soon}<div class="tile ul-none"><p>${filtering()?"조건에 맞는 계정이 없습니다.":"사용량을 보여 줄 기기가 없습니다."}</p>${filtering()?html`<button type="button" class="seg-action" data-clear>필터 해제</button>`:""}</div>`;
  const cell=(d,c)=>{
   const p=provs(d).find(p=>p.id===c.id),list=p?shown(d,p):[];
   if(!list.length)return html`<td data-label="${c.name}" class="ul-nil">–</td>`;
   if(S.mode==="full")return html`<td data-label="${c.name}" class="ul-full">${list.map(a=>acct(a,d,p))}</td>`;
   // 칸 값은 계정마다 가장 찬 한도의 평균이다(읽지 못한 계정은 빼고 센다)
   const err=list.some(isErr),act=list.some(a=>a.attention),pk=list.map(peak).filter(v=>v!=null),avg=pk.length?pk.reduce((x,y)=>x+y,0)/pk.length:null;
   return html`<td data-label="${c.name}"><button type="button" class="ul-cell" data-cell="${d.id+":"+c.id}" data-tone="${err||act?"crit":tone(avg)}"><b>${err&&avg==null?kind(list.find(isErr))[1]:pct(avg)}</b><span>${pk.length>1?"평균 · ":""}계정 ${list.length}${err&&avg!=null?" · "+kind(list.find(isErr))[1]+" 포함":""}${act?" · 조치 필요":""}</span></button></td>`;
  };
  return html`${kpi}${soon}<div class="tile ul-mx" data-mode="${S.mode}"><table style="${"--n:"+cols.length}"><thead><tr><th scope="col">기기</th>${cols.map(c=>html`<th scope="col">${dot(c.id)}${c.name}</th>`)}</tr></thead><tbody>${rows.map(d=>html`<tr aria-busy="${String(S.busy.has(d.id))}"><th scope="row"><b>${d.host||d.id}</b> ${badge(d)}${devMeta(d)}${state(d)[2]?html`<span class="ul-muted">${state(d)[2]}</span>`:ok(d)&&!(d.providers||[]).length?html`<span class="ul-muted">로그인한 구독 계정이 없습니다.</span>`:""}${[["cswap",d.cswap_error],["caam",d.caam_error]].filter(x=>x[1]).map(([n,e])=>html`<span class="ul-muted">${e.includes(n)?"":n+": "}${e}</span>`)}</th>${cols.map(c=>cell(d,c))}</tr>`)}</tbody></table></div>`;
 }
 const dlg=$("#ulDlg");
 function dlgDraw(){
  if(S.login){
   const L=S.login,d=S.devs.find(x=>x.id===L.dev);
   $("#ulDlgT").textContent=(d?d.host||d.id:L.dev)+" · "+LOGIN[L.prov]+" 로그인";
   P.render($("#ulDlgB"),L.err?html`<p class="ul-err">${L.err}</p>`:!L.url?html`<p class="ul-muted">로그인 URL 을 받는 중입니다…</p>`
    :html`<div class="ul-login"><p>아래 주소를 열어 로그인하고 일회용 코드를 넣으세요. 코드는 ${new Date(L.expires_at*1000).toLocaleTimeString("ko-KR",{hour:"2-digit",minute:"2-digit",hour12:false})}까지 유효합니다.</p><p><a href="${L.url}" target="_blank" rel="noopener noreferrer">${L.url}</a></p><p class="ul-code">${L.code}</p><p class="ul-muted">로그인을 마치면 이 창이 저절로 닫히고 사용량을 다시 읽습니다.</p></div>`);
   return;
  }
  if(!S.dlg)return;
  const d=S.devs.find(x=>x.id===S.dlg.dev),p=d&&provs(d).find(x=>x.id===S.dlg.prov);
  if(!p){dlg.close();return;}
  $("#ulDlgT").textContent=(d.host||d.id)+" · "+p.name;
  P.render($("#ulDlgB"),html`${devMeta(d)}${shown(d,p).map(a=>acct(a,d,p))}`);
 }
 dlg.addEventListener("close",()=>{S.dlg=null;S.login=null;});
 function draw(){
  const ids=columns(false).map(c=>c.id);
  if(S.prov!=="all"&&!ids.includes(S.prov))S.prov="all";
  P.render($("#ulProv"),html`${[{id:"all",name:"전체"},...columns(false)].map(p=>html`<button type="button" data-prov="${p.id}" aria-pressed="${String(S.prov===p.id)}">${p.name}</button>`)}`);
  const hb=$("#ulHot");hb.setAttribute("aria-pressed",String(S.hot));hb.textContent=S.hot?"✓ 주의 필요만":"주의 필요만";
  document.querySelectorAll("#ulMode [data-mode]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.mode===S.mode)));
  P.render($("#ulBody"),view());
  dlgDraw();
 }
 function note(t,bad){const n=$("#ulNote");n.textContent=t;n.classList.toggle("dev-err",!!bad);}

 // ---------------------------------------------------------------- 불러오기
 let timer=null;
 async function load(force){
  clearTimeout(timer);
  const mine=force&&S.devs?S.devs.map(d=>d.id).filter(i=>!S.busy.has(i)):[];
  if(force){mine.forEach(i=>S.busy.add(i));$("#ulAll").disabled=true;note("프로바이더에서 다시 읽는 중…");if(S.devs)draw();}
  try{
   const r=await fetch("/api/devices/usage"+(force?"?refresh=1":""),{cache:"no-store",headers:force?{"X-Jarvis-Action":"usage-refresh"}:{}});
   if(r.status===404){sec.hidden=true;showNav(false);return;} // core 가 아닌 jarvis(게이트웨이)
   if(!r.ok)throw new Error("HTTP "+r.status);
   const o=await r.json();
   S.devs=o.devices;
   const first=sec.hidden;sec.hidden=false;showNav(true);
   if(first&&location.hash==="#usage")sec.scrollIntoView();
   const n=S.devs.filter(failed).length;
   const fresh=S.devs.some(d=>d.refreshed); // 30초 안에 다시 누르면 게이트웨이는 Orca 보관값을 준다
   note(force?(n?n+"대는 읽지 못했습니다":fresh?"모두 다시 읽었습니다":"방금 읽은 값입니다(30초 뒤 다시 읽을 수 있습니다)"):"",force&&n);
  }catch(e){note("불러오지 못했습니다: "+(e.message||e),true);}
  finally{
   if(force){mine.forEach(i=>S.busy.delete(i));$("#ulAll").disabled=false;}
   if(S.devs)draw();
   timer=setTimeout(()=>{if(!document.hidden)load(false);},60000); // 숨은 탭은 멈춘다. 보이면 visibilitychange 가 다시 시작한다
  }
 }
 async function refreshOne(id){
  S.busy.add(id);draw();
  try{
   const r=await fetch("/api/devices/"+encodeURIComponent(id)+"/usage?refresh=1",{cache:"no-store",headers:{"X-Jarvis-Action":"usage-refresh"}}),o=await r.json();
   const i=S.devs.findIndex(d=>d.id===id);
   S.devs[i]={...o,id,host:S.devs[i].host,status:r.status};
   const good=r.ok&&(!o.error||(o.providers||[]).length>0); // Orca 가 실패해도 cswap 값은 읽혔을 수 있다
   note(good?"":(S.devs[i].host||id)+": "+(o.error||"HTTP "+r.status),!good);
  }catch(e){note((e.message||String(e)),true);}
  S.busy.delete(id);draw();
 }
 async function login(dev,prov){
  const L=S.login={dev,prov};dlgDraw();if(!dlg.open)dlg.showModal();
  try{
   const r=await fetch("/api/devices/"+encodeURIComponent(dev)+"/login?provider="+prov,{method:"POST",cache:"no-store",headers:{"X-Jarvis-Action":"usage-login"}}),o=await r.json();
   if(r.ok)Object.assign(L,{url:o.url,code:o.code,expires_at:o.expires_at});else L.err=o.error||"HTTP "+r.status;
  }catch(e){L.err=e.message||String(e);}
  if(S.login!==L)return;
  dlgDraw();
  // 로그인을 마치면(CLI 가 자격을 저장하고 끝나면) 창을 닫고 그 기기를 다시 읽는다
  while(!L.err&&S.login===L){
   await new Promise(r=>setTimeout(r,3000));
   if(S.login!==L)return;
   try{
    const r=await fetch("/api/devices/"+encodeURIComponent(dev)+"/login?provider="+prov,{cache:"no-store"}),o=await r.json();
    if(o.state==="done"){dlg.close();note(LOGIN[prov]+" 로그인을 확인했습니다");refreshOne(dev);return;}
    if(o.state==="failed"||o.state==="none"){L.err="로그인이 끝나지 않았습니다(코드가 만료되었거나 취소되었습니다). 다시 시도하세요.";dlgDraw();}
   }catch(e){} // 일시적인 실패는 다음 차례에 다시 묻는다
  }
 }
 sec.addEventListener("click",e=>{
  const t=e.target.closest("button");if(!t)return;
  if(t.dataset.prov){S.prov=t.dataset.prov;draw();return;}
  if(t.id==="ulHot"){S.hot=!S.hot;draw();return;}
  if(t.dataset.mode){S.mode=t.dataset.mode;try{localStorage.setItem(MODE_KEY,S.mode);}catch(err){}draw();return;}
  if(t.id==="ulAll"){load(true);return;}
  if(t.dataset.login){login(t.dataset.login,t.dataset.lp);return;}
  if(t.dataset.refresh){if(S.login)dlg.close();refreshOne(t.dataset.refresh);return;}
  if(t.dataset.cell){const [dev,prov]=t.dataset.cell.split(":");S.dlg={dev,prov};dlgDraw();if(!dlg.open)dlg.showModal();return;}
  if(t.hasAttribute("data-close")){dlg.close();return;}
  if(t.hasAttribute("data-clear")){S.prov="all";S.hot=false;draw();}
 });
 document.addEventListener("visibilitychange",()=>{if(!document.hidden)load(false);});
 load(false);
})();
