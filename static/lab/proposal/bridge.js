/* 부모와 공유한 실시간 스냅샷을 기존 셸·Bento 렌더러에 그대로 전달한다. */
(function(){
  "use strict";
  const P=window.JARVIS;
  const concept=window.JARVIS_LAB.find(c=>c.id===new URLSearchParams(location.search).get("concept"))||window.JARVIS_LAB[0];
  const embedded=P.embed&&parent!==window;
  const L=Object.assign(window.JarvisLab,{concept,embedded,last:null,update:null,
    after(s,st){L.last=s;document.body.dataset.snapshotAt=String(s.meta.generated_at);if(L.update)L.update(s,st);document.title=concept.name+" · Jarvis 콘텐츠 랩";}
  });
  // 실제 1h/24h 이력도 부모가 같은 요청을 합쳐 전달한다. 임의의 값이나 기간은 만들지 않는다.
  const pending=new Map();let seq=0;
  if(embedded){
    P.history=(range="1h")=>new Promise((resolve,reject)=>{
      if(!["1h","24h"].includes(range)){reject(new Error("Invalid history range"));return;}
      const id=++seq;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error("History request timed out"));},10000);
      pending.set(id,{resolve,reject,timer});
      parent.postMessage({type:"jarvis-lab-history",range,id},location.origin);
    });
    window.addEventListener("message",e=>{
      if(e.origin!==location.origin||e.source!==parent||e.data?.type!=="jarvis-lab-history-result")return;
      const p=pending.get(e.data.id);if(!p)return;clearTimeout(p.timer);pending.delete(e.data.id);
      e.data.error?p.reject(new Error(e.data.error)):p.resolve(e.data.history);
    });
  }
  const original=P.subscribe;
  P.subscribe=function(cb,opts){
    const deliver=(s,st)=>{L.last=s;cb(s,st);L.after(s,st);};
    if(!embedded)return original(deliver,opts);
    let last=null;
    window.addEventListener("message",e=>{
      if(e.origin!==location.origin||e.source!==parent||e.data?.type!=="jarvis-lab-snapshot"||!e.data.snapshot?.meta)return;
      last=e.data.snapshot;deliver(last,e.data.state||{});
    });
    const refresh=()=>parent.postMessage({type:"jarvis-lab-ready"},location.origin);
    refresh();
    return {refresh,get last(){return last;}};
  };
})();
