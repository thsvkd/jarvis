/* Jarvis — 첫 페인트 전에 테마를 정한다. <head> 안, 스타일시트보다 먼저 동기(<script src>)로 불러온다.
 * CSP(script-src 'self') 때문에 인라인 스크립트 대신 이 파일을 쓴다.
 *
 *  - 테마: localStorage "jarvis.theme" = "light" | "dark" | "system"(또는 없음) → <html data-theme="light|dark">.
 *          없으면 옛 키("pit5.theme" → 예전 Bento 의 "pit5.bento.theme")를 이어 읽는다. 저장소 접근은 막힐 수 있어 감싼다.
 *  - ?embed 로 열리면 <html class="embed"> (움직임을 끈다).
 *  - 웹폰트(Pretendard · JetBrains Mono)는 스크립트로 붙여 렌더를 막지 않는다. CDN 이 막혀도 시스템 글꼴로 바로 그린다.
 * 바꾸기(토글·저장·이벤트)는 /app/shell.js 가 맡는다.
 */
(function () {
  var d = document.documentElement;
  var t = null;
  try {
    t = localStorage.getItem("jarvis.theme");
    if (t == null) t = localStorage.getItem("pit5.theme");
    if (t == null) t = localStorage.getItem("pit5.bento.theme");
  } catch (e) { /* 저장소 없음(사생활 모드 등): 시스템 설정을 따른다 */ }
  if (t === "light" || t === "dark") d.setAttribute("data-theme", t);
  if (/[?&]embed(=|&|$)/.test(location.search)) d.classList.add("embed");

  function sheet(href, cors) {
    var l = document.createElement("link");
    l.rel = "stylesheet";
    l.href = href;
    if (cors) l.crossOrigin = "anonymous";
    document.head.appendChild(l);
  }
  sheet("https://cdn.jsdelivr.net/npm/pretendard@1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css", true);
  sheet("https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap", false);
})();
