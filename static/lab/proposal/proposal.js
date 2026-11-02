/* 운영 화면의 DOM과 렌더러는 그대로 유지한다. 배치·공개 범위만 조정한다. */
(function(){
 "use strict";
 const P=window.JARVIS,L=window.JarvisLab,c=L.concept,{html}=P;
 const $=s=>document.querySelector(s);
 document.body.dataset.lab=c.id;
 $("#lab-name").textContent=c.n+" · "+c.name;
 $("#lab-intent").textContent=c.intent;
 const isFocus=c.id==="focus";
 const main=$("#main");
 const folds=new Map();
 let showAll=false,showHealthy=false;
 function fold(node,key,label,opened=false){
  const d=document.createElement("details");d.className="lab-fold";d.dataset.fold=key;d.open=opened;
  P.render(d,html`<summary><span>${label}</span><small data-fold-count="${key}"></small><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary>`);
  node.before(d);d.append(node);folds.set(key,d);return d;
 }
 function foldMap(){fold($("#mapTile"),"map","네트워크 맵 · 구성과 노출 경로");}
 function foldSections(ids){ids.forEach(id=>{const s=$("#"+id),label=s.querySelector(".sec-head h2")?.textContent||"운영 개요";fold(s,id,label);});}
 if(c.id==="library"){
  main.insertBefore($("#services"),$("#overview"));
  foldSections(["overview","hardware","network","runtime"]);
  $("#overview").querySelector(".bento").prepend($("#mapTile"));
 }else{
  foldMap();
  foldSections(["hardware","network","runtime"]);
 }
 if(c.id==="rooms"){
  const group=document.createElement("section");group.id="lab-operations";group.className="sec";
  P.render(group,html`<div class="sec-head"><h2>경보·구성 변경</h2><p class="sec-sub">현재 주의할 일과 최근의 변화</p></div><div class="bento"></div>`);
  main.insertBefore(group,$("#overview"));
  const alert=$("#alerts"),changes=$("#t-changes").closest(".tile");
  alert.classList.remove("span-4");alert.classList.add("span-4");
  changes.classList.remove("span-12");changes.classList.add("span-8","t-changes");
  group.querySelector(".bento").append(alert,changes);
  $("#overview .hero").classList.remove("span-8");$("#overview .hero").classList.add("span-12");
  const ctrl=document.createElement("div");ctrl.className="lab-list-control";
  P.render(ctrl,html`<span id="lab-problem-count"></span><button type="button" class="seg-action" id="lab-healthy">전체 서비스 보기</button>`);
  $("#svcGroups").before(ctrl);
 }else if(isFocus||c.id==="library"){
  const b=document.createElement("button");b.type="button";b.id="lab-more-services";b.className="lab-more seg-action";$("#svcGroups").after(b);
 }
 function reveal(target){
  for(const d of folds.values())if(d.contains(target))d.open=true;
 }
 document.addEventListener("click",e=>{
  const a=e.target.closest('a[href^="#"],[data-go],[data-cat]');
  if(a){
   const hash=a.getAttribute("href"),[kind,arg]=(a.dataset.go||"").split(":");
   const id=hash?hash.slice(1):({units:"t-units",ports:"t-ports",traffic:"t-traffic",svc:"services"}[kind]||(kind==="tile"?arg:null));
   const target=id?document.getElementById(id):a.hasAttribute("data-cat")?$("#services"):null;
   if(target)reveal(target);
  }
 },true);
 window.addEventListener("hashchange",()=>{let id;try{id=decodeURIComponent(location.hash.slice(1));}catch(e){return;}const target=document.getElementById(id);if(target)reveal(target);});
 document.addEventListener("click",e=>{
  const more=e.target.closest("#lab-more-services");if(more){showAll=!showAll;L.update(L.last);}
  if(e.target.closest("#lab-healthy")){showHealthy=!showHealthy;L.update(L.last);}
  if(e.target.closest("#svcFilter,#svcView,[data-go],[data-cat]"))queueMicrotask(()=>L.update(L.last));
 });
 $("#svcSearch").addEventListener("input",()=>queueMicrotask(()=>L.update(L.last)));
 function listGroups(s){
  const q=$("#svcSearch").value.trim();const filter=$("#svcFilter [aria-pressed=true]")?.dataset.v||"all";
  const all=showAll||q||filter!=="all"&&c.id!=="library";let available=0,shown=0;
  document.querySelectorAll("#svcGroups>.svc-group").forEach(g=>{
   const grid=g.querySelector(".svc-grid"),rows=g.querySelector(".svc-rows"),box=grid.hidden?rows:grid;
   g.querySelectorAll(".lab-overflow,.lab-healthy-hidden").forEach(n=>n.classList.remove("lab-overflow","lab-healthy-hidden"));
   g.classList.remove("lab-group-empty");
   if(!g.hidden){
    let visible=0;
    [...box.children].forEach(n=>{
     if(n.hidden)return;
     if(n._local&&(isFocus||c.id==="library")){if(!all){n.classList.add("lab-overflow");return;}visible++;return;}
     if(c.id==="rooms"&&!showHealthy&&!q){const id=n.querySelector("[data-sid]")?.dataset.sid;const sv=s.services.find(x=>x.id===id);if(!sv||!["down","degraded","unknown"].includes(sv.status)){n.classList.add("lab-healthy-hidden");return;}}
     if(isFocus||c.id==="library"){available++;
      if(!all&&shown>=6){n.classList.add("lab-overflow");return;}shown++;}
     visible++;
    });
    if(!visible&&(isFocus||c.id==="library"||c.id==="rooms"))g.classList.add("lab-group-empty");
   }
  });
  const b=$("#lab-more-services");if(b){b.hidden=available<=6||!!q||filter!=="all"&&isFocus;b.textContent=showAll?"6개만 요약해서 보기":"나머지 "+Math.max(0,available-6)+"개 서비스 펼치기";b.setAttribute("aria-expanded",String(showAll));}
  if(c.id==="rooms"){
   const n=s.services.filter(x=>["down","degraded","unknown"].includes(x.status)).length;
   $("#lab-problem-count").textContent=n?"저하·다운·확인 불가 "+n+"개":"현재 저하·다운·확인 불가 서비스 없음";
   $("#lab-healthy").textContent=showHealthy?"문제 서비스만 보기":"전체 서비스 보기";
  }
 }
 function update(s){
  if(!s)return;
  listGroups(s);
  const counts={overview:s.summary.services.total+"개 서비스 관측",hardware:"CPU·GPU·메모리·온도·디스크",network:(s.ingress.caddy.routes||[]).length+"개 경로 · "+(s.tailscale.peers||[]).length+"개 피어",runtime:s.containers.length+"개 컨테이너 · "+s.units.length+"개 유닛",map:(s.ingress.caddy.routes||[]).length+"개 경로"};
  document.querySelectorAll("[data-fold-count]").forEach(el=>{el.textContent=counts[el.dataset.foldCount]||"";});
 }
 L.update=update;
 const initial=document.getElementById(location.hash.slice(1));if(initial)reveal(initial);
})();
