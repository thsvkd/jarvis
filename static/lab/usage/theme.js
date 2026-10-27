/* 랩 04 테마: 갤러리와 미리보기가 같은 세션 키를 쓴다. 운영 키 jarvis.theme 는 바꾸지 않는다.
 * window.JarvisLab 이 있으면 공용 shell.js 도 이 키에 저장한다. 미리보기는 storage 이벤트로 갤러리의 변경을 따라간다. */
(function(){
 "use strict";
 var K="jarvis.lab04.",d=document.documentElement;
 window.JarvisLab={
  getPref:function(k,def){try{var v=sessionStorage.getItem(K+k);if(v!==null)return v;}catch(e){}return def;},
  setPref:function(k,v){try{sessionStorage.setItem(K+k,String(v));}catch(e){}}
 };
 function apply(t){if(t==="light"||t==="dark")d.setAttribute("data-theme",t);else d.removeAttribute("data-theme");}
 apply(window.JarvisLab.getPref("theme","system"));
 window.addEventListener("storage",function(e){if(e.key===K+"theme")apply(e.newValue);});
 window.JarvisLabTheme=function(t){window.JarvisLab.setPref("theme",t);apply(t);};
})();
