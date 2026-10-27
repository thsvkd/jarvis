(async()=>{
 const assert=(v,m)=>{if(!v)throw Error(m);},wait=ms=>new Promise(r=>setTimeout(r,ms));
 const $=s=>document.querySelector(s),visible=n=>!!n&&!!n.getClientRects().length&&getComputedStyle(n).display!=='none';
 const click=s=>{const n=$(s);assert(n,'missing '+s);n.click();};
 for(let i=0;i<80&&!document.documentElement.classList.contains('has-data');i++)await wait(100);
 const L=window.JarvisFocus;assert(L&&!window.JarvisLab,'production Focus without lab context');
 assert(!performance.getEntriesByType('resource').some(r=>r.name.includes('/lab/')),'no lab dependencies');assert(!$('.lab-note'),'no lab banner');
 const results=[],cards=()=>[...document.querySelectorAll('#svcGroups .svc-open[data-sid]')].filter(visible),ids=()=>cards().map(n=>n.dataset.sid);
 const old=localStorage.getItem('jarvis.home.focus.test.expected');
 if(old){assert(ids().join()===old,'pins restored across page reload');assert($('[data-fold=network]').open,'fold restored across page reload');results.push('pin and fold restoration after reload');}
 else{assert(cards().length===6,'six initial representative services');assert([...document.querySelectorAll('[data-fold]')].every(d=>!d.open),'details start folded');}
 $('[data-sh-theme=dark]').click();assert(localStorage.getItem('jarvis.theme')==='dark'&&document.documentElement.dataset.theme==='dark','production theme persists');$('[data-sh-theme=light]').click();
 if(innerWidth<700){assert(!$('#focus-hero-extra').open,'mobile summary compact');click('#focus-hero-extra summary');await wait(50);assert(localStorage.getItem('jarvis.home.focus.mobileStatsOpen')==='true','mobile summary persists');click('#focus-hero-extra summary');await wait(50);click('#focus-alert-toggle');assert(localStorage.getItem('jarvis.home.focus.alertsOpen')==='true','full alerts persist');click('#focus-alert-toggle');}
 else assert($('#focus-hero-extra').open,'desktop original summary retained');
 click('#focus-select');assert($('#focus-pin-dialog').open,'pin selection dialog');
 let boxes=[...document.querySelectorAll('#focus-pin-options input')];assert(boxes.length>6,'inventory exercises former pin limit');assert(boxes.every(b=>!b.disabled),'all pin choices enabled');
 boxes.forEach(b=>{b.checked=false;});$('#focus-pin-form').dispatchEvent(new Event('change',{bubbles:true}));
 $('#focus-pin-form').requestSubmit();assert($('#focus-pin-dialog').open&&$('#focus-pin-count').textContent.includes('하나 이상'),'empty selection explained');
 boxes.forEach(b=>b.click());assert($('#focus-pin-count').textContent===boxes.length+'개 선택','unlimited selection count');$('#focus-pin-form').requestSubmit();await wait(80);
 assert(!$('#focus-pin-dialog').open&&cards().length===L.last.services.length,'entire inventory pinnable');assert(JSON.parse(localStorage.getItem('jarvis.home.focus.pins')).length===boxes.length,'all pins saved');assert($('#focus-more-services').hidden,'full-list button hidden when everything pinned');
 click('#focus-select');boxes=[...document.querySelectorAll('#focus-pin-options input')];assert(boxes.every(b=>b.checked&&!b.disabled),'all pins retained in picker');boxes.forEach(b=>{b.checked=false;});$('#focus-pin-form').dispatchEvent(new Event('change',{bubbles:true}));
 boxes.slice(0,2).forEach(b=>b.click());$('#focus-pin-form').requestSubmit();await wait(80);
 assert(!$('#focus-pin-dialog').open&&cards().length===2,'chosen pins only');
 const chosen=ids().join();assert(JSON.parse(localStorage.getItem('jarvis.home.focus.pins')).length===2,'production pins persist');await wait(3500);assert(ids().join()===chosen,'stable pins and order across polling');
 const unpinned=L.last.services.filter(x=>['down','degraded','unknown'].includes(x.status)&&!ids().includes(x.id));assert($('#focus-attention').querySelectorAll('[data-sid]').length===unpinned.length,'unpinned problems accessible');
 if(unpinned.length){click('#focus-attention [data-sid]');assert($('#sheet').open,'problem service detail');click('#sheetClose');}
 click('#focus-more-services');assert(cards().length===L.last.services.length,'full list accessible');assert(localStorage.getItem('jarvis.home.focus.showAll')==='true','full view persists');click('#focus-more-services');assert(cards().length===2,'return to pins');
 $('#svcSearch').focus();$('#svcSearch').value='__NO_MATCH__';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(50);assert(cards().length===0&&document.activeElement.id==='svcSearch','search focus and filtering');
 $('#svcSearch').value=L.last.services.find(s=>!chosen.split(',').includes(s.id)).name;$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(50);assert(cards().length>0,'search across entire inventory');
 $('#svcSearch').value='';$('#svcSearch').dispatchEvent(new Event('input',{bubbles:true}));await wait(50);assert(cards().length===2,'clear search restores pins');
 click('#svcFilter [data-v=public]');await wait(50);assert(cards().length===L.last.services.filter(s=>s.category==='public').length,'category bypasses pins');click('#svcFilter [data-v=all]');await wait(50);assert(cards().length===2,'all category restores pins');
 cards()[0].click();assert($('#sheet').open,'native detail sheet');click('#sheetClose');
 $('[data-fold=map]').open=true;click('#mapView [data-v=physical]');assert(localStorage.getItem('jarvis.map.view')===JSON.stringify('physical'),'native map preference persists');click('#mapView [data-v=concept]');
 for(const [action,id] of [['units:failed','t-units'],['ports:exposed','t-ports'],['traffic:hosts','t-traffic'],['svc:all','services']]){
  const b=document.createElement('button');b.dataset.go=action;document.body.append(b);b.click();b.remove();await wait(50);assert(visible($('#'+id)),'alert reveals '+action);
 }
 const snap=L.last,small=structuredClone(snap);small.services=snap.services.slice(0,6);L.update(small);assert(!$('#focus-more-services').hidden,'six total with two pins full list available');
 const failed=structuredClone(snap);failed.meta.errors={systemd:'test systemd error'};failed.system.memory.usage_pct=null;L.update(failed);
 assert($('[data-fold-count=runtime]').textContent.startsWith('수집 오류'),'real systemd collector error');assert(!$('[data-fold-count=hardware]').textContent.includes('수집 오류'),'no systemd hardware false error');assert($('[data-fold-count=hardware]').textContent.includes('확인 불가'),'missing metric never zero');
 L.update(snap,{stale:true});assert($('[data-fold-count=runtime]').textContent.startsWith('연결 지연'),'disconnected snapshot labeled');L.update(snap,{stale:false});
 for(const d of document.querySelectorAll('[data-fold]'))d.open=false;
 $('[data-fold=network]').open=true;await wait(100);assert(localStorage.getItem('jarvis.home.focus.fold.network')==='true','fold interaction persists');
 click('#focus-select');boxes=[...document.querySelectorAll('#focus-pin-options input')];boxes.forEach(b=>{b.checked=false;});$('#focus-pin-form').dispatchEvent(new Event('change',{bubbles:true}));boxes.slice(0,8).forEach(b=>b.click());$('#focus-pin-form').requestSubmit();await wait(80);assert(cards().length===8,'eight pins ready for reload');
 localStorage.setItem('jarvis.home.focus.test.expected',ids().join());
 window.scrollTo(0,0);window.dispatchEvent(new Event('scroll'));await wait(300);assert([...document.querySelectorAll('[data-sh-sec][aria-current=true]')].every(a=>visible(document.getElementById(a.dataset.shSec))),'current nav excludes closed sections');
 results.push('production isolation','theme','compact mobile controls','unlimited pins and empty selection','eight pins survive reload','persistent pins','stable polling','problem shortcuts','full list','search across inventory','category filter','detail sheet','native map','alert navigation','six-service inventory','collector errors and missing values','stale snapshot','persistent folds');
 return {pass:true,results,serviceY:$('#services').offsetTop};
})().catch(e=>({pass:false,error:e.message,stack:e.stack}))
