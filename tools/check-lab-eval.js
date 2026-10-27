(async()=>{
 const assert=(v,m)=>{if(!v)throw Error(m);},wait=ms=>new Promise(r=>setTimeout(r,ms));
 const until=async(fn,m)=>{for(let i=0;i<80;i++){if(fn())return;await wait(100);}assert(false,m);};
 const $=s=>document.querySelector(s),results=[];
 const prefs=()=>JSON.stringify(Object.keys(localStorage).filter(k=>!k.startsWith('jarvis.lab')).sort().map(k=>[k,localStorage.getItem(k)]));
 const before=prefs();
 $('[data-sh-theme=dark]').click();assert(document.documentElement.dataset.theme==='dark','original theme control');
 $('[data-sh-theme=light]').click();
 if($('#cards')){
  assert(document.querySelectorAll('.card').length===window.JARVIS_LAB.length,'all concepts');
  $('[data-save]').click();assert($('[data-save]').getAttribute('aria-pressed')==='true','favorite saved');$('[data-save]').click();
  $('#compare-toggle').click();assert(!$('#comparison').hidden,'comparison visible');
  $('#left').value='pulse';$('#left').dispatchEvent(new Event('change',{bubbles:true}));
  const frames=()=>[...document.querySelectorAll('iframe')];
  await until(()=>frames().length===window.JARVIS_LAB.length+2&&frames().every(f=>f.contentDocument?.body?.dataset.snapshotAt),'all embedded renders');
  await until(()=>new Set(frames().map(f=>f.contentDocument.body.dataset.snapshotAt)).size===1,'identical shared snapshot');
  assert(frames().every(f=>f.contentWindow.performance.getEntriesByType('resource').filter(r=>r.name.includes('/api/snapshot')).length===0),'embedded zero API polling');
  const child=frames()[0].contentDocument;child.querySelector('[data-fold=map]').open=true;child.querySelector('#mapView [data-v=physical]').click();
  assert(prefs()===before,'gallery and embedded production preferences untouched');
  $('#compare-toggle').click();results.push('all previews','favorite','comparison','shared snapshot','embedded zero polling','embedded preference isolation');
 }else{
  const c=new URLSearchParams(location.search).get('concept');
  await until(()=>document.documentElement.classList.contains('has-data'),'native Bento rendered');
  assert($('#jarvis-shell')&&$('#svcGroups .svc-group')&&$('#t-cpu'),'original components');
  const visible=n=>!!n.getClientRects().length&&getComputedStyle(n).display!=='none';
  const cards=()=>[...document.querySelectorAll('.svc-open[data-sid]')].filter(visible);
  const click=s=>{const n=$(s);assert(n,'missing '+s);n.click();};
  if(c==='focus-plus'){
   const selected=()=>cards().map(n=>n.dataset.sid);
   const initial=selected();assert(initial.length===6,'six representative pinned services');
   assert(new Set(initial.map(id=>window.JarvisLab.last.services.find(s=>s.id===id).category)).size>1,'selection spans categories');
   if(innerWidth<700){assert(!$('#focus-hero-extra').open,'mobile stats folded');click('#focus-hero-extra summary');assert($('#focus-hero-extra').open,'mobile stats accessible');click('#focus-hero-extra summary');click('#focus-alert-toggle');assert($('#focus-alert-toggle').getAttribute('aria-expanded')==='true','full alerts accessible');click('#focus-alert-toggle');}
   else assert($('#focus-hero-extra').open,'desktop original stats visible');
   click('#focus-select');assert($('#focus-pin-dialog').open,'pin picker opens');
   let boxes=[...document.querySelectorAll('#focus-pin-options input')];assert(boxes.filter(b=>b.checked).length===6,'selected pins in picker');
   assert(boxes.length>6&&boxes.every(b=>!b.disabled),'no selection limit');
   boxes.filter(b=>!b.checked).forEach(b=>b.click());$('#focus-pin-form').requestSubmit();await wait(60);assert(!$('#focus-pin-dialog').open&&selected().length===boxes.length,'entire inventory pinned');assert(JSON.parse(window.JarvisLab.getPref('pins')).length===boxes.length,'all lab pins saved');
   click('#focus-select');boxes=[...document.querySelectorAll('#focus-pin-options input')];assert(boxes.every(b=>b.checked),'all pins retained in picker');
   for(const b of boxes){b.checked=false;}$('#focus-pin-form').dispatchEvent(new Event('change',{bubbles:true}));
   const alternative=boxes.filter(b=>!initial.includes(b.value)).slice(0,2);assert(alternative.length===2,'alternate services exist');alternative.forEach(b=>b.click());
   $('#focus-pin-form').requestSubmit();await wait(60);assert(!$('#focus-pin-dialog').open,'pin selection applied');
   assert(selected().length===2&&selected().every(id=>alternative.some(b=>b.value===id)),'chosen services replace default pins');
   const expected=selected().join();await wait(3500);assert(selected().join()===expected,'pin order persists across polling');
   const bad=window.JarvisLab.last.services.filter(x=>['down','degraded','unknown'].includes(x.status)&&!alternative.some(b=>b.value===x.id));
   assert($('#focus-attention').querySelectorAll('[data-sid]').length===bad.length,'all unpinned problem services accessible');
   if(bad.length){click('#focus-attention [data-sid]');assert($('#sheet').open,'unpinned problem service details');click('#sheetClose');}
   click('#lab-more-services');assert(cards().length===window.JarvisLab.last.services.length,'full service list accessible');click('#lab-more-services');assert(cards().length===2,'returns to selected pins');
   const saved=window.JarvisLab.getPref('pins');assert(JSON.parse(saved).length===2,'pins stored in lab session');
   const snapshot=window.JarvisLab.last;
   const changed=structuredClone(snapshot);changed.system.cpu.usage_pct=23;changed.system.memory.usage_pct=null;
   changed.meta.errors={...changed.meta.errors,systemd:'test collection failure'};
   changed.changes=[{t:changed.meta.generated_at-10},{t:changed.meta.generated_at-3601}];
   window.JarvisLab.update(changed);
   assert(!$('[data-fold-count=hardware]').textContent.includes('수집 오류'),'systemd error does not mark hardware');
   assert($('[data-fold-count=hardware]').textContent.includes('CPU 23%')&&$('[data-fold-count=hardware]').textContent.includes('메모리 확인 불가'),'actual metrics and missing values');
   assert($('[data-fold-count=runtime]').textContent.includes('수집 오류')&&$('[data-fold-count=runtime]').textContent.includes('최근 1시간 변경 1건'),'collection error and recent change window');
   const small=structuredClone(snapshot);small.services=snapshot.services.filter(x=>alternative.some(b=>b.value===x.id)).concat(snapshot.services.filter(x=>!alternative.some(b=>b.value===x.id)).slice(0,4));
   window.JarvisLab.update(small);assert(!$('#lab-more-services').hidden,'full list accessible when total six and only two pinned');
   const docker=structuredClone(snapshot);docker.meta.errors={docker:'test docker collection failure'};window.JarvisLab.update(docker);assert($('[data-fold-count=runtime]').textContent.startsWith('수집 오류'),'real docker key');
   const stale=structuredClone(snapshot);stale.meta.errors={};stale.monitor.collectors=[{name:'system_fast',stale:true}];window.JarvisLab.update(stale);assert($('[data-fold-count=hardware]').textContent.startsWith('수집 지연'),'stale collector label');
   window.JarvisLab.update(snapshot);
   click('#focus-select');for(const b of document.querySelectorAll('#focus-pin-options input'))b.checked=false;$('#focus-pin-form').dispatchEvent(new Event('change',{bubbles:true}));for(const b of document.querySelectorAll('#focus-pin-options input'))if(initial.includes(b.value))b.click();$('#focus-pin-form').requestSubmit();await wait(30);
   results.push('six representative pins','unlimited pin picker','stable selection across polling','unpinned problem details','full list','live fold summaries','missing values and collection errors','recent changes','mobile compact controls');
  }
  if(c==='inbox'){
   assert(visible($('.lnav')),'desktop and mobile tabs visible');
   assert(!visible($('.sh-sections')),'no duplicate section menu');
   click('.lnav [data-lsec=services]');assert(!$('#services').classList.contains('lab-inactive'),'services tab');
   $('.lnav [data-lsec=services]').dispatchEvent(new KeyboardEvent('keydown',{key:'Home',bubbles:true}));
   assert(!$('#overview').classList.contains('lab-inactive'),'keyboard return to overview');click('.lnav [data-lsec=services]');
   results.push('five tabs','keyboard navigation');
  }
  if(c==='library'){
   assert([...$('#main').children].indexOf($('#services'))<[...$('#main').children].indexOf($('[data-fold=overview]')),'services first');
   assert($('#svcFilter [data-v=public]').getAttribute('aria-pressed')==='true','public category default');results.push('services first','public default');
  }
  if(c==='rooms'){
   assert($('#lab-operations').contains($('#alerts'))&&$('#lab-operations').contains($('#t-changes')),'original alerts and changes first');click('#lab-healthy');results.push('alerts and changes first','all services accessible');
  }
  if(c==='pulse'){
   assert([...document.querySelectorAll('#svcGroups>.svc-group')].every(g=>g.classList.contains('lab-group-collapsed')),'group bodies initially folded');
   click('[data-group-toggle]');assert($('[data-group-toggle]').getAttribute('aria-expanded')==='true','category opens');results.push('category summaries','category expansion');
  }
  if(c==='focus'){assert(cards().length<=6,'six initial services');if(!$('#lab-more-services').hidden)click('#lab-more-services');results.push('six services then expand');}
  click('#svcFilter [data-v=all]');await wait(30);
  $('#svcSearch').focus();$('#svcSearch').value='__NO_MATCH__';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(30);
  assert(cards().length===0,'search filters');assert(document.activeElement.id==='svcSearch','search focus preserved');
  $('#svcSearch').value='';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(30);
  if(c==='pulse'&&!cards().length)click('[data-group-toggle]');
  const card=cards()[0];assert(card,'service available');card.click();assert($('#sheet').open,'original service detail');click('#sheetClose');assert(!$('#sheet').open,'detail closes');
  results.push('search','focus preserved','original service detail');
  if(c==='inbox')click('.lnav [data-lsec=overview]');
  const map=$('[data-fold=map]')||$('[data-fold=overview]');map.open=true;click('#mapView [data-v=physical]');
  assert($('#mapView [data-v=physical]').getAttribute('aria-checked')==='true','original map switches');
  for(const [action,id,section] of [['units:failed','t-units','runtime'],['ports:exposed','t-ports','network'],['traffic:hosts','t-traffic','network'],['svc:all','services','services']]){
   const b=document.createElement('button');b.dataset.go=action;document.body.append(b);b.click();b.remove();await wait(30);
   assert(visible($('#'+id)),'alert reveals '+action);if(c==='inbox')assert(!$('#'+section).classList.contains('lab-inactive'),'alert selects tab');
  }
  assert(prefs()===before,'production preferences untouched');results.push('native map','alert destination mapping','preference isolation');
  if(c==='inbox')click('.lnav [data-lsec=overview]');
  else{const fold=$('[data-fold=hardware]');fold.open=true;await wait(3500);assert(fold.open,'fold persists through polling');fold.open=false;results.push('fold state across polling');}
  for(const d of document.querySelectorAll('.lab-fold'))d.open=false;
  click('#mapView [data-v=concept]');
  click('#svcFilter [data-v='+ (c==='library'?'public':'all')+']');
  if(c==='rooms')click('#lab-healthy');
  if(c==='focus'&&$('#lab-more-services').getAttribute('aria-expanded')==='true')click('#lab-more-services');
  if(c==='pulse')for(const b of document.querySelectorAll('[data-group-toggle][aria-expanded=true]'))b.click();
  window.scrollTo(0,0);window.dispatchEvent(new Event("scroll"));await wait(300);
  assert([...document.querySelectorAll("[data-sh-sec][aria-current=true]")].every(a=>visible(document.getElementById(a.dataset.shSec))),"hidden sections never marked current");
 }
 assert(prefs()===before,'production preferences untouched');
 return {pass:true,results};
})().catch(e=>({pass:false,error:e.message,stack:e.stack}))
