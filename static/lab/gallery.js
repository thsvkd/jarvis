(function(){
  "use strict";
  const P=window.JARVIS, C=window.JARVIS_LAB, {html}=P;
  const $=s=>document.querySelector(s);
  let last=null, state=null, chosen=null;
  try{chosen=localStorage.getItem("jarvis.lab03.favorite");}catch(e){}
  P.render($("#cards"), C.map(c=>html`<article class="card" id="${c.id}">
    <div class="preview"><div class="iframe-shell"><iframe src="/lab/proposal/?concept=${c.id}&embed=1" title="${c.name} 실시간 미리보기" tabindex="-1"></iframe></div><span class="preview-caption">LIVE PREVIEW / ${c.n}</span></div>
    <div class="card-body"><div class="card-id">${c.n} / ${c.en}<span>${c.fit}</span></div><h3>${c.name}<small>${c.en}</small></h3><p class="question">${c.question}</p><p class="sequence">${c.sequence}</p><p class="technique">${c.technique}</p><p class="tradeoff">고려할 점 · ${c.tradeoff}</p><div class="actions"><a class="primary" href="/lab/proposal/?concept=${c.id}">직접 써보기 ↗</a><button class="save" data-save="${c.id}" aria-pressed="false" type="button">후보 저장</button></div></div></article>`));
  const ro=new ResizeObserver(entries=>entries.forEach(e=>{
    const frame=e.target.querySelector(".iframe-shell");
    if(frame)frame.style.transform="scale("+(e.contentRect.width/1440)+")";
  }));
  document.querySelectorAll(".preview").forEach(e=>ro.observe(e));
  function saved(){
    document.querySelectorAll("[data-save]").forEach(b=>{const on=b.dataset.save===chosen;b.setAttribute("aria-pressed",String(on));b.textContent=on?"✓ 저장한 후보":"후보 저장";});
    const c=C.find(c=>c.id===chosen);$("#favorite").textContent=c?"저장한 후보: "+c.name:"후보를 저장해 두고 비교하세요.";
  }
  saved();
  document.addEventListener("click",e=>{
    const b=e.target.closest("[data-save]");if(!b)return;
    chosen=chosen===b.dataset.save?null:b.dataset.save;
    try{chosen?localStorage.setItem("jarvis.lab03.favorite",chosen):localStorage.removeItem("jarvis.lab03.favorite");}catch(err){}
    saved();
  });
  for(const id of ["left","right"])P.render($("#"+id),C.map(c=>html`<option value="${c.id}">${c.n} ${c.name}</option>`));
  $("#right").value="focus-plus";
  function comparison(){
    P.render($("#compare-frames"),["left","right"].map(id=>{
      const c=C.find(c=>c.id===$("#"+id).value);
      return html`<iframe src="/lab/proposal/?concept=${c.id}&embed=1" title="${c.name} 비교 화면"></iframe>`;
    }));
  }
  $("#left").addEventListener("change",comparison);$("#right").addEventListener("change",comparison);
  $("#compare-toggle").setAttribute("aria-expanded","false");
  $("#compare-toggle").addEventListener("click",()=>{
    const open=$("#comparison").hidden;$("#comparison").hidden=!open;
    $("#compare-toggle").textContent=open?"비교 화면 접기":"비교 화면 열기";
    $("#compare-toggle").setAttribute("aria-expanded",String(open));
    if(open)comparison();else P.render($("#compare-frames"),html``);
  });
  function send(w){if(last)w.postMessage({type:"jarvis-lab-snapshot",snapshot:last,state},location.origin);}
  const historyPending={};
  window.addEventListener("message",e=>{
    if(e.origin!==location.origin||![...document.querySelectorAll("iframe")].some(f=>f.contentWindow===e.source))return;
    if(e.data?.type==="jarvis-lab-ready")send(e.source);
    if(e.data?.type==="jarvis-lab-history"&&["1h","24h"].includes(e.data.range)){
      const range=e.data.range;
      const p=historyPending[range]||(historyPending[range]=P.history(range).finally(()=>{delete historyPending[range];}));
      p.then(h=>e.source.postMessage({type:"jarvis-lab-history-result",id:e.data.id,history:h},location.origin),()=>e.source.postMessage({type:"jarvis-lab-history-result",id:e.data.id,error:"서버 이력을 가져오지 못했습니다"},location.origin));
    }
  });
  P.subscribe((s,st)=>{
    last=s;state=st;
    $("#live").textContent=st.stale?"연결 지연 · 마지막 수신 데이터 표시":s.meta.host+" · 실시간 · "+new Date(s.meta.generated_at*1000).toLocaleTimeString("ko-KR");
    document.querySelectorAll("iframe").forEach(f=>send(f.contentWindow));
  },{interval:5000,onError:()=>{$("#live").textContent="서버 연결 실패 · 연결을 재시도합니다";}});
})();
