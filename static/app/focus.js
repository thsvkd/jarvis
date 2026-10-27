/* Focus의 기존 컴포넌트를 유지하며 요약·선택·모바일 공개 범위만 다듬는다. */
(function(){
 "use strict";
 const L=window.JarvisLab||window.JarvisFocus;if(!L||(window.JarvisLab&&L.concept.id!=="focus-plus"))return;
 document.body.dataset.focusLayout="refined";
 const P=window.JARVIS,{html,fmt,labels}=P,$=s=>document.querySelector(s);
 let ids=null,last=null,lastState={};
 try{const v=JSON.parse(L.getPref("pins","null"));if(Array.isArray(v)&&v.length)ids=[...new Set(v.filter(x=>typeof x==="string"))];}catch(e){}
 if(ids&&!ids.length)ids=null;
 const more=$("#focus-more-services")||$("#lab-more-services");
 const controls=document.createElement("div");controls.className="focus-controls";
 P.render(controls,html`<p id="focus-selection-note"></p><button type="button" class="seg-action" id="focus-select" aria-haspopup="dialog">고정 서비스 선택</button>`);
 $("#svcGroups").before(controls);
 const attention=document.createElement("div");attention.id="focus-attention";attention.className="focus-attention";attention.hidden=true;controls.before(attention);
 const empty=document.createElement("p");empty.id="focus-pin-empty";empty.className="focus-note";empty.hidden=true;controls.after(empty);
 const dialog=document.createElement("dialog");dialog.id="focus-pin-dialog";dialog.className="sheet";dialog.setAttribute("aria-labelledby","focus-pin-title");
 P.render(dialog,html`<div class="sheet-panel"><header class="sheet-head"><div class="sheet-head-main"><h2 id="focus-pin-title">고정 서비스 선택</h2><p>첫 화면에 둘 서비스를 개수 제한 없이 선택하세요.</p></div><button type="button" class="icon-btn sheet-close" id="focus-pin-close" aria-label="선택 창 닫기">×</button></header><form id="focus-pin-form" class="sheet-body"><p class="focus-note">처음에는 분류별 대표 서비스를 선택합니다. 이 목록을 자주 쓰는 서비스로 바꿔 보세요.</p><div id="focus-pin-options"></div><p id="focus-pin-count" role="status"></p><div class="focus-pin-actions"><button type="submit" class="seg-action">선택 적용</button><button type="button" class="seg-action" id="focus-pin-cancel">취소</button></div></form></div>`);
 document.body.append(dialog);
 function options(){
  if(!last)return;
  P.render($("#focus-pin-options"),last.services.map(s=>html`<label class="focus-pin-option"><input type="checkbox" name="pin" value="${s.id}"><span><b>${s.name||s.id}</b><small>${labels.category[s.category]||s.category}</small></span><span class="badge" data-tone="${P.tone(s.status)}">${labels.status[s.status]||"확인 불가"}</span></label>`));
  dialog.querySelectorAll('input[name=pin]').forEach(n=>{n.checked=(ids||[]).includes(n.value);});countOptions();
 }
 function countOptions(){const n=dialog.querySelectorAll('input[name=pin]:checked').length;$("#focus-pin-count").textContent=n+"개 선택";}
 $("#focus-select").addEventListener("click",()=>{if(!last)return;options();dialog.showModal();});
 $("#focus-pin-form").addEventListener("change",countOptions);
 $("#focus-pin-form").addEventListener("submit",e=>{
  e.preventDefault();const chosen=[...dialog.querySelectorAll('input[name=pin]:checked')].map(n=>n.value);
  if(!chosen.length){$("#focus-pin-count").textContent="서비스를 하나 이상 선택해 주세요.";return;}
  ids=chosen;L.setPref("pins",JSON.stringify(ids));
  $("#svcSearch").value="";$("#svcSearch").dispatchEvent(new Event("input",{bubbles:true}));
  $("#svcFilter [data-v=all]").click();if(more.getAttribute("aria-expanded")==="true")more.click();
  dialog.close();L.update(last);$("#focus-select").focus();
 });
 for(const id of ["focus-pin-close","focus-pin-cancel"])$("#"+id).addEventListener("click",()=>dialog.close());
 // 모바일에서만 접는다. 화면 폭을 오가도 같은 원본 노드와 상세 링크를 사용한다.
 const extra=document.createElement("details");extra.id="focus-hero-extra";
 P.render(extra,html`<summary>분류별 상태·운영 통계 <span aria-hidden="true">⌄</span></summary>`);
 $("#heroCats").before(extra);extra.append($("#heroCats"),$(".hero-stats"));
 const mobile=matchMedia("(max-width:699px)");
 const syncExtra=()=>{extra.open=!mobile.matches||L.getPref("mobileStatsOpen","false")==="true";};syncExtra();
 mobile.addEventListener("change",syncExtra);
 if(!window.JarvisLab)extra.addEventListener("toggle",()=>{if(mobile.matches)L.setPref("mobileStatsOpen",String(extra.open));});
 const alertToggle=document.createElement("button");alertToggle.type="button";alertToggle.id="focus-alert-toggle";alertToggle.className="seg-action";alertToggle.setAttribute("aria-expanded","false");alertToggle.setAttribute("aria-controls","alertList");alertToggle.textContent="경보 상세 펼치기";$("#alertMore").after(alertToggle);
 if(!window.JarvisLab&&L.getPref("alertsOpen","false")==="true")$("#alerts").classList.add("focus-alerts-open");
 function syncAlerts(){const on=$("#alerts").classList.contains("focus-alerts-open");alertToggle.setAttribute("aria-expanded",String(on));alertToggle.textContent=on?"경보 간단히 보기":"경보 상세 펼치기";}syncAlerts();
 alertToggle.addEventListener("click",()=>{const on=$("#alerts").classList.toggle("focus-alerts-open");alertToggle.setAttribute("aria-expanded",String(on));alertToggle.textContent=on?"경보 간단히 보기":"경보 상세 펼치기";if(!window.JarvisLab)L.setPref("alertsOpen",String(on));});
 function defaults(s){
  const groups=[...new Set(s.services.map(x=>x.category))],pick=[];
  for(const cat of groups){const sv=s.services.find(x=>x.category===cat);if(sv)pick.push(sv.id);if(pick.length===6)break;}
  for(const sv of s.services){if(pick.length>=6)break;if(!pick.includes(sv.id))pick.push(sv.id);}return pick;
 }
 const pct=v=>Number.isFinite(v)?fmt.pct(v,0):"확인 불가";
 const previous=L.update;
 L.update=function(s,st){
  if(!s)return;last=s;if(st)lastState=st;
  if(ids===null&&s.services.length){ids=defaults(s);L.setPref("pins",JSON.stringify(ids));}
  const selected=ids||[];L.selectedServiceIds=new Set(selected);previous(s,st);
  const live=selected.filter(id=>s.services.some(x=>x.id===id)),missing=selected.length-live.length;
  $("#focus-selection-note").textContent="고정 "+live.length+"개 / 전체 "+s.services.length+"개"+(missing?" · 미관측 "+missing+"개":"");
  empty.hidden=live.length>0||!selected.length;empty.textContent="고정한 서비스를 현재 관측하지 못했습니다. 고정 서비스 선택 또는 전체 목록에서 확인해 주세요.";
  const all=more.getAttribute("aria-expanded")==="true";
  const browsing=all||$("#svcSearch").value.trim()||$("#svcFilter [aria-pressed=true]")?.dataset.v!=="all";
  $("#services").classList.toggle("focus-pinned-mode",!browsing);
  $("#services").classList.toggle("focus-list-mode",$("#svcView [aria-pressed=true]")?.dataset.v==="list");
  $("#h-svc").textContent=browsing?"서비스":"고정 서비스";
  more.hidden=!!$("#svcSearch").value.trim()||$("#svcFilter [aria-pressed=true]")?.dataset.v!=="all"||(!all&&s.services.length<=live.length);
  more.textContent=all?"고정 서비스만 보기":"전체 "+s.services.length+"개 서비스 보기";
  $("[data-f='svc.sub']").textContent="고정 서비스부터 · 검색과 카테고리 선택은 전체 목록에서 찾습니다";
  const bad=s.services.filter(x=>["down","degraded","unknown"].includes(x.status));
  const outside=bad.filter(x=>!selected.includes(x.id));attention.hidden=!outside.length;
  P.render(attention,html`<span>고정 목록 밖의 문제·확인 불가 ${outside.length}개</span>${outside.map(x=>html`<button type="button" class="issue" data-tone="${P.tone(x.status)}" data-sid="${x.id}"><i class="shape"></i><b>${x.name||x.id}</b><span>${labels.status[x.status]||"확인 불가"}</span></button>`)}`);
  const sys=s.system||{},ts=s.tailscale||{},ctr=s.summary.containers||{},errors=s.meta.errors||{};
  const now=s.meta.generated_at,recent=(s.changes||[]).filter(x=>Number.isFinite(x.t)&&x.t<=now&&now-x.t<=3600);
  const errorFor=names=>names.some(n=>errors[n]);
  const staleFor=names=>(s.monitor?.collectors||[]).some(c=>names.includes(c.name)&&c.stale);
  const summaries={
   hardware:"CPU "+pct(sys.cpu?.usage_pct)+" · 메모리 "+pct(sys.memory?.usage_pct)+" · 디스크 "+pct(sys.disks?.find(d=>d.mount==="/")?.usage_pct),
   network:(ts.peers_online??"–")+" / "+(ts.peers_total??"–")+" 피어 온라인 · "+(s.ingress?.caddy?.routes||[]).length+"개 경로",
   runtime:"컨테이너 "+(ctr.running??"–")+" / "+(ctr.total??"–")+" 실행 · 실패 유닛 "+s.units.filter(x=>x.active==="failed").length+" · 최근 1시간 변경 "+recent.length+"건",
   map:(s.ingress?.caddy?.routes||[]).length+"개 경로 · 문제·확인 불가 "+bad.length+"개"
  };
  const sources={hardware:["system_fast","system"],network:["tailscale","caddy","tunnel","dns","probes","listeners","neighbors","caddy_log"],runtime:["docker","systemd","inventory","pm2","processes","listeners","hermes"],map:["caddy","tailscale","tunnel","dns","probes","docker"]};
  for(const [key,value] of Object.entries(summaries)){
   const n=document.querySelector('[data-fold-count="'+key+'"]');if(n){const prefix=lastState.stale?"연결 지연":errorFor(sources[key])?"수집 오류":staleFor(sources[key])?"수집 지연":"";n.textContent=(prefix?prefix+" · ":"")+value;}
  }
  // 원문 전체는 기존 상세 창에 있다. 카드에는 상태·이름·대표 주소만 남긴다.
  document.querySelectorAll("#svcGroups .svc").forEach(card=>{
   const link=card.querySelector(".svc-links .svc-link");if(link)link.setAttribute("data-primary","true");
   const reason=card.querySelector(".svc-reason");if(reason)reason.title=reason.textContent;
  });
  alertToggle.hidden=!(s.alerts||[]).length;
 };
})();
