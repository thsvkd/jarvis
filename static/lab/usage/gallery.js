/* 랩 04 갤러리: 시안 카드(실제 미리보기 iframe) · 선택 · 시안별 의견. 저장은 이 브라우저 localStorage 에만. */
(function(){
 "use strict";
 const P=window.JARVIS,{html}=P,$=s=>document.querySelector(s);
 const C=[
  {id:"a",n:"A",name:"기기 타일 안에",fit:"지금 화면에 가장 가까움",
   question:"이 기기에서 지금 어떤 계정을 얼마나 쓰고 있나?",
   sequence:"기기 타일 → 프로바이더별 사용 중 계정의 5시간·주간 막대 → 다른 계정 펼치기",
   technique:"기존 기기 타일 아래에 프로바이더 줄을 붙입니다. 사용 중 계정만 먼저 보이고, 같은 프로바이더의 다른 계정은 펼쳐서 봅니다. 페어링·업데이트 버튼과 한자리에 있습니다.",
   tradeoff:"같은 계정이 여러 기기에 반복됩니다. 기기가 많으면 세로로 길어집니다."},
  {id:"b",n:"B",name:"계정 보드",fit:"계정이 여러 개일 때",
   question:"각 구독 계정의 한도가 얼마나 남았나?",
   sequence:"프로바이더 묶음 → 계정 카드(높은 사용률 순) → 사용 기기 칩 → 그 기기 상세",
   technique:"구독 한도는 계정 단위라서 같은 계정을 한 번만 보여 줍니다. 카드 아래에 그 계정을 쓰는 기기(● 사용 중)를 칩으로 달고, 칩을 누르면 기기 상세가 열립니다.",
   tradeoff:"‘이 기기에 무엇이 로그인돼 있나’는 칩이나 상세 창을 거쳐야 보입니다."},
  {id:"c",n:"C",name:"한도 레이더",fit:"한도 관리가 목적일 때",
   question:"곧 막히는 계정이 있나, 언제 풀리나?",
   sequence:"주의 계정 수·다음 리셋 → 곧 풀리는 한도 목록 → 기기 × 프로바이더 표 → 칸 상세",
   technique:"주의가 필요한 계정과 리셋 시각을 맨 위에 둡니다. 아래 표의 칸은 그 기기·프로바이더의 최대 사용률이며, 누르면 계정별 상세가 열립니다. 모바일에서는 표가 기기 카드로 바뀝니다.",
   tradeoff:"정보가 가장 압축되어 처음 보는 사람에게는 표 읽는 법이 필요합니다."}
 ];
 const K="jarvis.lab04.";
 const load=k=>{try{return localStorage.getItem(K+k)||"";}catch(e){return "";}};
 const save=(k,v)=>{try{v?localStorage.setItem(K+k,v):localStorage.removeItem(K+k);}catch(e){}};
 let pick=load("pick");
 P.render($("#cards"),C.map(c=>html`<article class="card" id="v-${c.id}">
  <div class="preview"><div class="iframe-shell"><iframe src="${"/lab/usage/preview.html?v="+c.id+"&embed=1"}" title="${"시안 "+c.n+" "+c.name+" 미리보기"}" tabindex="-1" loading="lazy"></iframe></div><span class="preview-caption">PREVIEW / ${c.n}</span></div>
  <div class="card-body"><div class="card-id">시안 ${c.n}<span>${c.fit}</span></div><h3>${c.name}</h3><p class="question">${c.question}</p><p class="sequence">${c.sequence}</p><p class="technique">${c.technique}</p><p class="tradeoff">고려할 점 · ${c.tradeoff}</p>
  <label class="ul-fbl">이 시안에 대한 의견<textarea class="ul-fb" rows="3" data-fb="${c.id}" placeholder="좋은 점, 바꿀 점"></textarea></label>
  <div class="actions"><a class="primary" href="${"/lab/usage/preview.html?v="+c.id}">직접 써보기 ↗</a><button class="save" data-pick="${c.id}" aria-pressed="false" type="button">이 시안 선택</button></div></div></article>`));
 document.querySelectorAll("[data-fb]").forEach(t=>{t.value=load("fb."+t.dataset.fb);t.addEventListener("input",()=>{save("fb."+t.dataset.fb,t.value);summary();});});
 const ro=new ResizeObserver(es=>es.forEach(e=>{const f=e.target.querySelector(".iframe-shell");if(f)f.style.transform="scale("+(e.contentRect.width/1440)+")";}));
 document.querySelectorAll(".preview").forEach(e=>ro.observe(e));
 function summary(){
  document.querySelectorAll("[data-pick]").forEach(b=>{const on=b.dataset.pick===pick;b.setAttribute("aria-pressed",String(on));b.textContent=on?"✓ 선택한 시안":"이 시안 선택";});
  const c=C.find(c=>c.id===pick);
  $("#favorite").textContent=c?"선택: 시안 "+c.n+" "+c.name:"시안을 하나 골라 주세요.";
  const lines=["[랩 04 AI 구독 사용량] 선택: "+(c?"시안 "+c.n+" "+c.name:"없음")];
  C.forEach(x=>{const f=load("fb."+x.id).trim();if(f)lines.push("- 시안 "+x.n+" "+x.name+": "+f.replace(/\s*\n\s*/g," / "));});
  $("#fbOut").value=lines.join("\n");
 }
 document.addEventListener("click",e=>{
  const b=e.target.closest("[data-pick]");
  if(b){pick=pick===b.dataset.pick?"":b.dataset.pick;save("pick",pick);summary();return;}
  if(e.target.id==="fbCopy"){
   const t=$("#fbOut"),n=$("#fbNote");t.select();
   const ok=()=>{n.textContent="복사했습니다.";setTimeout(()=>{n.textContent="";},2000);};
   if(navigator.clipboard)navigator.clipboard.writeText(t.value).then(ok,()=>{document.execCommand("copy");ok();});
   else{document.execCommand("copy");ok();}
   return;
  }
  if(e.target.id==="fbReset"&&confirm("선택과 의견을 모두 지울까요?")){
   pick="";save("pick","");C.forEach(c=>save("fb."+c.id,""));document.querySelectorAll("[data-fb]").forEach(t=>{t.value="";});summary();
  }
 });
 summary();
})();
