/* 랩 04 미리보기: ?v=a|b|c. 같은 샘플 데이터(data.js)로 세 가지 배치를 그린다.
 * 여정: 훑어보기 → 프로바이더·주의 필터 → 계정 상세(펼치기·대화상자) → 새로고침(성공·실패) → 빈 결과. */
(function(){
 "use strict";
 const P=window.JARVIS,{html}=P,L=window.USAGE_LAB,$=s=>document.querySelector(s);
 const V={a:"기기 타일 안에",b:"계정 보드",c:"한도 레이더"};
 const SUB={a:"기기 타일마다 프로바이더별 사용 중 계정의 한도를 보여 주고, 다른 계정은 펼쳐서 봅니다.",
  b:"같은 구독 계정은 한 번만 보여 주고, 그 계정을 쓰는 기기를 함께 표시합니다.",
  c:"한도에 가까운 계정과 다음 리셋을 먼저 보여 주고, 기기 × 프로바이더 표로 나머지를 찾습니다."};
 const vid=V[new URLSearchParams(location.search).get("v")]?new URLSearchParams(location.search).get("v"):"a";
 const K="jarvis.lab04."+vid+".";
 const get=(k,d)=>{try{const v=sessionStorage.getItem(K+k);return v===null?d:JSON.parse(v);}catch(e){return d;}};
 const put=(k,v)=>{try{sessionStorage.setItem(K+k,JSON.stringify(v));}catch(e){}};
 const S={prov:get("prov","all"),hot:get("hot",false),open:new Set(get("open",[])),busy:new Set(),err:{},dlg:null};
 const PN=Object.fromEntries(L.PROVIDERS.map(p=>[p.id,p]));
 const DEV=Object.fromEntries(L.DEVICES.map(d=>[d.id,d]));
 const STATE={ok:["ok","Orca 실행 중"],off:["warn","Orca 꺼짐"],none:["muted","Orca 없음"],old:["muted","미지원"],down:["crit","접속 불가"]};

 const isHot=a=>L.usage(a).status==="error"||(L.peak(a)??0)>=75;
 const filtering=()=>S.prov!=="all"||S.hot;
 const shown=d=>d.accounts.filter(a=>(S.prov==="all"||a.provider===S.prov)&&(!S.hot||isHot(a)));
 const groups=list=>L.PROVIDERS.map(p=>({p,list:list.filter(a=>a.provider===p.id)})).filter(g=>g.list.length);
 const pct=n=>n==null?"–":Math.round(n)+"%";
 const meter=u=>html`<div class="meter" data-tone="${L.tone(u)}"><i style="--v:${u/100}"></i></div>`;
 const dot=p=>html`<i class="ul-dot" data-p="${p}" aria-hidden="true"></i>`;

 function win(w){
  return html`<div class="ul-win"><span class="ul-wl">${L.WIN[w.key]||w.key}</span>${meter(w.used)}<b class="ul-wp" data-tone="${L.tone(w.used)}">${pct(w.used)}</b><span class="ul-wr" title="${L.at(w.resets_at)} 리셋">${L.left(w.resets_at)} 리셋</span></div>`;
 }
 function body(a){
  const u=L.usage(a);
  if(u.status==="error")return html`<p class="ul-err">${u.error}</p>`;
  return html`<div class="ul-wins">${u.windows.map(win)}</div>${u.buckets?html`<p class="ul-extra">모델별 · ${u.buckets.map((b,i)=>html`${i?" · ":""}${b.name} <b>${pct(b.used)}</b>`)}</p>`:""}${u.extra?html`<p class="ul-extra">추가 사용량 $${u.extra.spent.toFixed(2)} / $${u.extra.limit} · ${u.extra.enabled?"켜짐":"꺼짐("+u.extra.reason+")"}</p>`:""}${u.credits!=null?html`<p class="ul-extra">한도 리셋 크레딧 ${u.credits}개</p>`:""}`;
 }
 function head(a,withProv){
  const u=L.usage(a);
  return html`<div class="ul-ah">${withProv?html`${dot(a.provider)}<span class="ul-pn">${PN[a.provider].name}</span>`:""}<b class="ul-email">${a.label}</b>${u.plan?html`<span class="ul-plan">${u.plan}</span>`:""}${a.active?html`<span class="badge" data-tone="info">사용 중</span>`:""}${u.status==="error"?html`<span class="badge" data-tone="crit">확인 실패</span>`:""}</div>`;
 }
 const acct=(a,withProv)=>html`<div class="ul-acct">${head(a,withProv)}${body(a)}</div>`;
 function devMeta(d){
  const busy=S.busy.has(d.id),err=S.err[d.id];
  if(d.state==="none")return "";
  return html`<p class="ul-meta"><span>${L.ago(d.fetched_at)} 수집</span><button type="button" class="seg-action ul-ref" data-refresh="${d.id}" ${busy?"disabled":""}>${busy?"갱신 중…":"새로고침"}</button>${err?html`<span class="dev-err">${err}</span>`:""}</p>`;
 }
 const badge=d=>html`<span class="badge" data-tone="${STATE[d.state][0]}">${STATE[d.state][1]}</span>`;
 const empty=()=>html`<div class="tile ul-none"><p>조건에 맞는 계정이 없습니다.</p><button type="button" class="seg-action" data-clear>필터 해제</button></div>`;
 // 필터 중에는 Orca 를 못 읽은 기기도 '전체' 프로바이더일 때만 보인다(어느 프로바이더인지 모른다)
 const visible=d=>d.state==="ok"?(!filtering()||shown(d).length>0):S.prov==="all";

 // ---------------------------------------------------------------- A: 기기 타일 안에
 function A(){
  const devs=L.DEVICES.filter(visible);
  if(!devs.length)return empty();
  return html`<div class="bento">${devs.map(d=>html`<article class="tile span-6 m-6 ul-dev" aria-busy="${S.busy.has(d.id)}"><header class="tile-head"><h3 class="tile-title">${d.host}</h3>${badge(d)}<span class="tile-aside">${d.model}</span></header>${devMeta(d)}${d.state!=="ok"?html`<p class="ul-muted">${L.STATE[d.state]}</p>`:groups(shown(d)).map(({p,list})=>{
   const act=list.find(a=>a.active)||list[0],rest=list.filter(a=>a!==act),key=d.id+":"+p.id,open=S.open.has(key);
   return html`<div class="ul-prov"><div class="ul-ph">${dot(p.id)}<b>${p.name}</b><span class="ul-who">${act.label}</span>${L.usage(act).plan?html`<span class="ul-plan">${L.usage(act).plan}</span>`:""}<span class="ul-peak" data-tone="${L.tone(L.peak(act))}">${L.usage(act).status==="error"?"확인 실패":"최대 "+pct(L.peak(act))}</span></div>${body(act)}${rest.length?html`<button type="button" class="ul-more" data-open="${key}" aria-expanded="${open}">${open?"다른 계정 접기":"다른 계정 "+rest.length+"개"}</button>${open?html`<div class="ul-others">${rest.map(a=>acct(a))}</div>`:""}`:""}</div>`;
  })}</article>`)}</div>`;
 }

 // ---------------------------------------------------------------- B: 계정 보드
 function uniq(){
  const m=new Map();
  for(const d of L.DEVICES)for(const a of shown(d)){let x=m.get(a.key);if(!x)m.set(a.key,x={a,devs:[]});x.devs.push({d,active:a.active});}
  return [...m.values()].sort((x,y)=>(L.peak(y.a)??101)-(L.peak(x.a)??101));
 }
 function B(){
  const all=uniq(),bad=S.prov==="all"?L.DEVICES.filter(d=>d.state!=="ok"):[];
  if(!all.length&&!bad.length)return empty();
  const gs=L.PROVIDERS.map(p=>({p,xs:all.filter(x=>x.a.provider===p.id)})).filter(g=>g.xs.length);
  return html`${gs.map(({p,xs})=>html`<h3 class="ul-gh">${dot(p.id)}${p.name}<span>계정 ${xs.length}개</span></h3><div class="bento ul-board">${xs.map(({a,devs})=>html`<article class="tile span-4 m-6 ul-card">${head(a)}${body(a)}<div class="ul-devs"><span class="ul-wl">사용 기기</span>${devs.map(({d,active})=>html`<button type="button" class="ul-chip" data-dev="${d.id}" aria-busy="${S.busy.has(d.id)}">${d.host}${active?html`<i aria-label="사용 중"> ●</i>`:""}</button>`)}</div></article>`)}</div>`)}${bad.length?html`<h3 class="ul-gh">사용량을 읽지 못한 기기<span>${bad.length}대</span></h3><div class="ul-fail">${bad.map(d=>html`<div class="ul-frow"><b>${d.host}</b>${badge(d)}<span class="ul-muted">${L.STATE[d.state]}</span>${devMeta(d)}</div>`)}</div>`:""}`;
 }

 // ---------------------------------------------------------------- C: 한도 레이더
 function C(){
  const all=uniq(),hot=all.filter(x=>isHot(x.a)),bad=L.DEVICES.filter(d=>d.state!=="ok");
  const wins=all.flatMap(x=>L.usage(x.a).status==="error"?[]:L.usage(x.a).windows.filter(w=>w.used>=50).map(w=>({x,w}))).sort((p,q)=>p.w.resets_at-q.w.resets_at).slice(0,6);
  const next=wins.find(o=>o.w.used>=75);
  const provs=L.PROVIDERS.filter(p=>S.prov==="all"||p.id===S.prov);
  const rows=L.DEVICES.filter(visible);
  const kpi=html`<div class="bento"><div class="tile span-4 m-3 ul-kpi" data-tone="${hot.length?"warn":"ok"}"><span class="ul-wl">주의 필요 계정</span><b>${hot.length}</b><span class="ul-muted">75% 이상 또는 확인 실패</span></div><div class="tile span-4 m-3 ul-kpi"><span class="ul-wl">다음 리셋(주의 계정)</span><b class="ul-kpi-s">${next?L.left(next.w.resets_at):"–"}</b><span class="ul-muted">${next?next.x.a.label+" · "+PN[next.x.a.provider].name+" "+L.WIN[next.w.key]:"주의 계정 없음"}</span></div><div class="tile span-4 m-6 ul-kpi" data-tone="${bad.length?"crit":"ok"}"><span class="ul-wl">읽지 못한 기기</span><b>${bad.length}</b><span class="ul-muted">${bad.map(d=>d.host).join(" · ")||"없음"}</span></div></div>`;
  const soon=wins.length?html`<div class="tile ul-soon"><h3 class="sub-h">곧 풀리는 한도 (50% 이상)</h3><ol>${wins.map(({x,w})=>html`<li><span class="ul-when">${L.left(w.resets_at)}</span>${dot(x.a.provider)}<span class="ul-email">${x.a.label}</span><span class="ul-wl">${PN[x.a.provider].name} ${L.WIN[w.key]}</span>${meter(w.used)}<b class="ul-wp" data-tone="${L.tone(w.used)}">${pct(w.used)}</b></li>`)}</ol></div>`:"";
  if(!rows.length)return html`${kpi}${empty()}`;
  const cell=(d,p)=>{
   const list=shown(d).filter(a=>a.provider===p.id);
   if(d.state!=="ok"||!list.length)return html`<td data-label="${p.name}" class="ul-nil">–</td>`;
   const err=list.some(a=>L.usage(a).status==="error"),pk=Math.max(0,...list.map(a=>L.peak(a)??0));
   return html`<td data-label="${p.name}"><button type="button" class="ul-cell" data-cell="${d.id+":"+p.id}" data-tone="${err?"crit":L.tone(pk)}" aria-busy="${S.busy.has(d.id)}"><b>${err&&!pk?"실패":pct(pk)}</b><span>계정 ${list.length}${err?" · 실패 포함":""}</span></button></td>`;
  };
  return html`${kpi}${soon}<div class="tile ul-mx"><table><thead><tr><th scope="col">기기</th>${provs.map(p=>html`<th scope="col">${dot(p.id)}${p.name}</th>`)}</tr></thead><tbody>${rows.map(d=>html`<tr><th scope="row"><b>${d.host}</b> ${badge(d)}${devMeta(d)}${d.state!=="ok"?html`<span class="ul-muted">${L.STATE[d.state]}</span>`:""}</th>${provs.map(p=>cell(d,p))}</tr>`)}</tbody></table></div>`;
 }

 // ---------------------------------------------------------------- 대화상자·이벤트
 const dlg=$("#ulDlg");
 function dlgDraw(){
  if(!S.dlg)return;
  const d=DEV[S.dlg.dev],list=d.accounts.filter(a=>!S.dlg.prov||a.provider===S.dlg.prov);
  $("#ulDlgT").textContent=d.host+(S.dlg.prov?" · "+PN[S.dlg.prov].name:" · 모든 프로바이더");
  P.render($("#ulDlgB"),html`${devMeta(d)}${d.state!=="ok"?html`<p class="ul-muted">${L.STATE[d.state]}</p>`:groups(list).map(({p,list})=>html`<div class="ul-prov">${S.dlg.prov?"":html`<div class="ul-ph">${dot(p.id)}<b>${p.name}</b></div>`}${list.map(a=>acct(a))}</div>`)}`);
 }
 function open(dev,prov){S.dlg={dev,prov};dlgDraw();if(!dlg.open)dlg.showModal();}
 dlg.addEventListener("close",()=>{S.dlg=null;});
 const R={a:A,b:B,c:C};
 function draw(){
  P.render($("#ulProv"),html`${[{id:"all",name:"전체"},...L.PROVIDERS].map(p=>html`<button type="button" data-prov="${p.id}" aria-pressed="${S.prov===p.id}">${p.name}</button>`)}`);
  const hb=$("#ulHot");hb.setAttribute("aria-pressed",String(S.hot));hb.textContent=S.hot?"✓ 주의 필요만":"주의 필요만";
  P.render($("#ulBody"),R[vid]());
  dlgDraw();
  put("prov",S.prov);put("hot",S.hot);put("open",[...S.open]);
 }
 function note(t,bad){const n=$("#ulNote");n.textContent=t;n.classList.toggle("dev-err",!!bad);}
 async function refresh(ids){
  ids.forEach(i=>S.busy.add(i));note("새로고침 중…");draw();
  const r=await Promise.allSettled(ids.map(i=>L.refresh(i).then(()=>{delete S.err[i];},e=>{S.err[i]=e.message;throw e;}).finally(()=>S.busy.delete(i))));
  const bad=r.filter(x=>x.status==="rejected").length;
  note(bad?(ids.length-bad)+"대 갱신 · "+bad+"대 실패(시뮬레이션)":ids.length+"대 갱신 완료(시뮬레이션)",bad);
  draw();
 }
 document.addEventListener("click",e=>{
  const t=e.target.closest("button,a");if(!t)return;
  if(t.dataset.themeSet){window.JarvisLabTheme(t.dataset.themeSet);marks();return;}
  if(t.dataset.prov){S.prov=t.dataset.prov;draw();return;}
  if(t.id==="ulHot"){S.hot=!S.hot;draw();return;}
  if(t.id==="ulAll"){refresh(L.DEVICES.filter(d=>d.state!=="none").map(d=>d.id));return;}
  if(t.dataset.refresh){refresh([t.dataset.refresh]);return;}
  if(t.dataset.open){S.open.has(t.dataset.open)?S.open.delete(t.dataset.open):S.open.add(t.dataset.open);draw();return;}
  if(t.dataset.dev){open(t.dataset.dev,null);return;}
  if(t.dataset.cell){const [d,p]=t.dataset.cell.split(":");open(d,p);return;}
  if(t.hasAttribute("data-close")){dlg.close();return;}
  if(t.hasAttribute("data-clear")){S.prov="all";S.hot=false;draw();}
 });
 function marks(){const cur=window.JarvisLab.getPref("theme","system");document.querySelectorAll("[data-theme-set]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.themeSet===cur)));}
 window.addEventListener("storage",marks);
 $("#ulName").textContent="시안 "+vid.toUpperCase()+" · "+V[vid];$("#ulSub").textContent=SUB[vid];
 document.title=V[vid]+" · AI 구독 사용량 시안";
 marks();draw();
 setInterval(draw,30000); // 남은 시간·수집 시각 표시를 갱신한다
})();
