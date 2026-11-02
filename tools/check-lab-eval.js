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
  $('#left').value='rooms';$('#left').dispatchEvent(new Event('change',{bubbles:true}));
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
  if(c==='library'){
   assert([...$('#main').children].indexOf($('#services'))<[...$('#main').children].indexOf($('[data-fold=overview]')),'services first');
   assert($('#svcFilter [data-v=public]').getAttribute('aria-pressed')==='true','public category default');results.push('services first','public default');
  }
  if(c==='rooms'){
   assert($('#lab-operations').contains($('#alerts'))&&$('#lab-operations').contains($('#t-changes')),'original alerts and changes first');click('#lab-healthy');results.push('alerts and changes first','all services accessible');
  }
  if(c==='focus'){assert(cards().length<=6,'six initial services');if(!$('#lab-more-services').hidden)click('#lab-more-services');results.push('six services then expand');}
  click('#svcFilter [data-v=all]');await wait(30);
  $('#svcSearch').focus();$('#svcSearch').value='__NO_MATCH__';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(30);
  assert(cards().length===0,'search filters');assert(document.activeElement.id==='svcSearch','search focus preserved');
  $('#svcSearch').value='';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(30);
  const card=cards()[0];assert(card,'service available');card.click();assert($('#sheet').open,'original service detail');click('#sheetClose');assert(!$('#sheet').open,'detail closes');
  results.push('search','focus preserved','original service detail');
  const map=$('[data-fold=map]')||$('[data-fold=overview]');map.open=true;click('#mapView [data-v=physical]');
  assert($('#mapView [data-v=physical]').getAttribute('aria-checked')==='true','original map switches');
  for(const [action,id] of [['units:failed','t-units'],['ports:exposed','t-ports'],['traffic:hosts','t-traffic'],['svc:all','services']]){
   const b=document.createElement('button');b.dataset.go=action;document.body.append(b);b.click();b.remove();await wait(30);
   assert(visible($('#'+id)),'alert reveals '+action);
  }
  assert(prefs()===before,'production preferences untouched');results.push('native map','alert destination mapping','preference isolation');
  const fold=$('[data-fold=hardware]');fold.open=true;await wait(3500);assert(fold.open,'fold persists through polling');fold.open=false;results.push('fold state across polling');
  for(const d of document.querySelectorAll('.lab-fold'))d.open=false;
  click('#mapView [data-v=concept]');
  click('#svcFilter [data-v='+ (c==='library'?'public':'all')+']');
  if(c==='rooms')click('#lab-healthy');
  if(c==='focus'&&$('#lab-more-services').getAttribute('aria-expanded')==='true')click('#lab-more-services');
  window.scrollTo(0,0);window.dispatchEvent(new Event("scroll"));await wait(300);
  assert([...document.querySelectorAll("[data-sh-sec][aria-current=page]")].every(a=>visible(document.getElementById(a.dataset.shSec))),"hidden sections never marked current");
 }
 assert(prefs()===before,'production preferences untouched');
 return {pass:true,results};
})().catch(e=>({pass:false,error:e.message,stack:e.stack}))
