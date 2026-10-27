/* 실험 화면의 UI 설정은 시안별 세션에만 저장한다. */
(function(){
 "use strict";
 const id=new URLSearchParams(location.search).get("concept"),concept=window.JARVIS_LAB.find(c=>c.id===id);
 const key="jarvis.lab03."+(concept?.id||"gallery")+".";
 window.JarvisLab={
  getPref(k,d){try{const v=sessionStorage.getItem(key+k);if(v!==null)return v;}catch(e){}return k==="svcFilter"&&concept?.id==="library"?"public":d;},
  setPref(k,v){try{sessionStorage.setItem(key+k,String(v));}catch(e){}}
 };
})();
