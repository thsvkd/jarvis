/* 랩 05 갤러리: 시안 카드(실제 미리보기 iframe) · 선택 · 시안별 의견. 저장은 이 브라우저 localStorage 에만. */
(function(){
 "use strict";
 const P=window.JARVIS,{html}=P,$=s=>document.querySelector(s);
 const C=[
  {id:"a",n:"A",name:"기기 × 모듈 행렬",fit:"전체를 한눈에",
   question:"어느 기기의 어느 모듈이 어떤 상태인가?",
   sequence:"요약 칩·승인 대기 → 기기 9행 × 모듈 7열 → 칸 누르기 → 그 줄 아래에서 계획·실행·결과",
   technique:"옮긴 모듈(Orca·doctor)과 아직 ai-setup 이 관리하는 모듈 5개를 열 묶음으로 나눕니다. 칸을 누르면 그 행 바로 아래가 펼쳐져 계획을 보고 실행합니다. 모바일에서는 행이 기기 카드가 됩니다.",
   tradeoff:"모듈이 늘수록 열이 넓어집니다. 아직 옮기지 않은 열은 지금은 정보가 거의 없습니다."},
  {id:"b",n:"B",name:"모듈별 탭",fit:"모듈 하나씩 옮길 때",
   question:"이 모듈을 어느 기기에 적용해야 하나?",
   sequence:"모듈 탭(할 일 수) → 모듈 요약·분포 막대 → 기기 목록 → 계획 미리 보기 → 실행 · doctor 는 발견 종류별 묶음",
   technique:"모듈마다 맞는 모양을 씁니다. Orca 는 기기 목록과 '할 일 있는 기기 모두 계획 세우기', doctor 는 평문 시크릿부터 종류별로 묶고, 옮기지 않은 모듈은 이전 순서를 보여 줍니다.",
   tradeoff:"여러 모듈에 걸친 한 기기의 상태는 탭을 오가야 보입니다. 승인 대기는 Orca 탭의 숫자로만 드러납니다."},
  {id:"c",n:"C",name:"할 일 먼저 + 기기 서랍",fit:"운영 홈에 붙일 때",
   question:"지금 내가 눌러야 할 것이 있나?",
   sequence:"KPI 4개 → 승인 대기(승인·거절) → 할 일 → 최근 결과 → 기기 타일 → 서랍에서 모듈별 상세",
   technique:"승인 대기와 할 일(갱신 가능·평문 시크릿·드리프트·연결 안 됨)을 맨 위에 둡니다. 기기 상세는 운영 화면의 서비스 상세와 같은 서랍(모바일은 아래 시트)으로 엽니다. 휴대폰 알림의 목적지로 쓰기 좋습니다.",
   tradeoff:"모든 기기의 상태를 한 번에 비교하는 표가 없습니다. 할 일이 없으면 첫 화면이 비어 보입니다."}
 ];
 const K="jarvis.lab05.";
 const load=k=>{try{return localStorage.getItem(K+k)||"";}catch(e){return "";}};
 const save=(k,v)=>{try{v?localStorage.setItem(K+k,v):localStorage.removeItem(K+k);}catch(e){}};
 let pick=load("pick");
 P.render($("#cards"),C.map(c=>html`<article class="card" id="v-${c.id}">
  <div class="preview"><div class="iframe-shell"><iframe src="${"/lab/setup/preview.html?v="+c.id+"&embed=1"}" title="${"시안 "+c.n+" "+c.name+" 미리보기"}" tabindex="-1" loading="lazy"></iframe></div><span class="preview-caption">PREVIEW / ${c.n}</span></div>
  <div class="card-body"><div class="card-id">시안 ${c.n}<span>${c.fit}</span></div><h3>${c.name}</h3><p class="question">${c.question}</p><p class="sequence">${c.sequence}</p><p class="technique">${c.technique}</p><p class="tradeoff">고려할 점 · ${c.tradeoff}</p>
  <label class="su-fbl">이 시안에 대한 의견<textarea class="su-fb" rows="3" data-fb="${c.id}" placeholder="좋은 점, 바꿀 점"></textarea></label>
  <div class="actions"><a class="primary" href="${"/lab/setup/preview.html?v="+c.id}">직접 써보기 ↗</a><button class="save" data-pick="${c.id}" aria-pressed="false" type="button">이 시안 선택</button></div></div></article>`));
 document.querySelectorAll("[data-fb]").forEach(t=>{t.value=load("fb."+t.dataset.fb);t.addEventListener("input",()=>{save("fb."+t.dataset.fb,t.value);summary();});});
 const ro=new ResizeObserver(es=>es.forEach(e=>{const f=e.target.querySelector(".iframe-shell");if(f)f.style.transform="scale("+(e.contentRect.width/1440)+")";}));
 document.querySelectorAll(".preview").forEach(e=>ro.observe(e));
 function summary(){
  document.querySelectorAll("[data-pick]").forEach(b=>{const on=b.dataset.pick===pick;b.setAttribute("aria-pressed",String(on));b.textContent=on?"✓ 선택한 시안":"이 시안 선택";});
  const c=C.find(c=>c.id===pick);
  $("#favorite").textContent=c?"선택: 시안 "+c.n+" "+c.name:"시안을 하나 골라 주세요.";
  const lines=["[랩 05 AI 셋업] 선택: "+(c?"시안 "+c.n+" "+c.name:"없음")];
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
