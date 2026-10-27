/* 운영 Focus 배치와 영구 설정. 데이터·차트·상세 렌더링은 기존 Bento가 맡는다. */
(function(){
 "use strict";
 if(window.JarvisLab)return;
 const P=window.JARVIS,{html}=P,$=s=>document.querySelector(s);
 const L=window.JarvisFocus={last:null,
  // 고정 서비스는 기기마다 다르다: core 에서 다른 기기(?device)를 볼 때 그 기기 몫으로 따로 둔다
  key(k){return "jarvis.home.focus."+(k==="pins"&&P.device?P.device+".":"")+k;},
  getPref(k,d){try{return localStorage.getItem(L.key(k))??d;}catch(e){return d;}},
  setPref(k,v){try{localStorage.setItem(L.key(k),String(v));}catch(e){}}
 };
 const folds=new Map();let showAll=L.getPref("showAll","false")==="true";
 function fold(node,key,label){
  const d=document.createElement("details");d.className="focus-fold";d.dataset.fold=key;d.open=L.getPref("fold."+key,"false")==="true";
  P.render(d,html`<summary><span>${label}</span><small data-fold-count="${key}"></small><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5"/></svg></summary>`);
  node.before(d);d.append(node);folds.set(key,d);
  d.addEventListener("toggle",()=>L.setPref("fold."+key,String(d.open)));
 }
 fold($("#mapTile"),"map","네트워크 맵 · 구성과 노출 경로");
 for(const id of ["hardware","network","runtime"]){const s=$("#"+id);fold(s,id,s.querySelector(".sec-head h2").textContent);}
 const more=document.createElement("button");more.type="button";more.id="focus-more-services";more.className="focus-more seg-action";more.setAttribute("aria-expanded",String(showAll));$("#svcGroups").after(more);
 function reveal(target){for(const d of folds.values())if(d.contains(target))d.open=true;}
 function hashTarget(hash){try{return document.getElementById(decodeURIComponent(hash.replace(/^#/,"")));}catch(e){return null;}}
 document.addEventListener("click",e=>{
  const a=e.target.closest('a[href^="#"],[data-go],[data-cat]');if(!a)return;
  const hash=a.getAttribute("href"),[kind,arg]=(a.dataset.go||"").split(":");
  const id={units:"t-units",ports:"t-ports",traffic:"t-traffic",svc:"services"}[kind]||(kind==="tile"?arg:a.hasAttribute("data-cat")?"services":null);
  const target=hash?hashTarget(hash):document.getElementById(id);if(target)reveal(target);
 },true);
 window.addEventListener("hashchange",()=>{const target=hashTarget(location.hash);if(target)reveal(target);});
 const initial=hashTarget(location.hash);if(initial)reveal(initial);
 document.addEventListener("click",e=>{
  if(e.target.closest("#focus-more-services")){showAll=!showAll;L.setPref("showAll",String(showAll));L.update(L.last);}
  if(e.target.closest("#svcFilter,#svcView,[data-go],[data-cat]"))queueMicrotask(()=>L.update(L.last));
 });
 $("#svcSearch").addEventListener("input",()=>queueMicrotask(()=>L.update(L.last)));
 L.update=function(s){
  if(!s)return;
  const all=showAll||$("#svcSearch").value.trim()||$("#svcFilter [aria-pressed=true]")?.dataset.v!=="all";
  for(const g of document.querySelectorAll("#svcGroups>.svc-group")){
   g.querySelectorAll(".focus-overflow").forEach(n=>n.classList.remove("focus-overflow"));g.classList.remove("focus-group-empty");
   if(g.hidden)continue;
   const grid=g.querySelector(".svc-grid"),box=grid.hidden?g.querySelector(".svc-rows"):grid;let visible=0;
   for(const n of box.children){
    if(n.hidden)continue;
    const id=n.querySelector("[data-sid]")?.dataset.sid;
    if(!all&&(n._local||!L.selectedServiceIds?.has(id))){n.classList.add("focus-overflow");continue;}visible++;
   }
   if(!visible)g.classList.add("focus-group-empty");
  }
  more.setAttribute("aria-expanded",String(showAll));
 };
 const subscribe=P.subscribe;
 P.subscribe=(cb,opts)=>subscribe((s,st)=>{L.last=s;cb(s,st);L.update(s,st);},opts);
})();
