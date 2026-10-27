/* Jarvis — 공용 제품 셸(헤더). 모든 페이지가 같은 머리띠를 쓴다:
 *   브랜드(Jarvis + 호스트·모델) · 주 탐색(개요 / · 네트워크 맵 /map/) · [섹션 바로가기] ·
 *   전체 상태 알약(누르면 경보 목록) · 실시간/지연/오프라인 표시 + 데이터 시각 · 접속 주소 메뉴(복사) · 테마(시스템/라이트/다크)
 *
 * ── 페이지에 붙이는 법 ───────────────────────────────────────────────────────────
 *   <head>
 *     <script src="/app/theme-init.js"></script>        동기. 첫 페인트 전에 테마 적용 + 웹폰트 비동기 로드
 *     <link rel="stylesheet" href="/app/shell.css">      토큰 + 헤더 스타일. 페이지 CSS 보다 먼저
 *     <link rel="stylesheet" href="/app/<page>.css">
 *   </head>
 *   <body>
 *     <a class="sh-skip" href="#main">본문으로 건너뛰기</a>
 *     <header id="jarvis-shell" data-page="home|map" [data-layout="full"] [data-sections="id:라벨,id:라벨"]></header>
 *     <script src="/shared/jarvis.js"></script>
 *     <script src="/app/shell.js"></script>              jarvis.js 바로 뒤, 페이지 스크립트보다 먼저(동기)
 *     … 본문 … <script src="/app/<page>.js"></script>   (또는 <head> 의 defer)
 *
 *   data-page      지금 페이지. 주 탐색의 aria-current 와 "경보 모두 보기" 링크를 정한다(없으면 경로로 추측)
 *   data-layout    "full" 이면 헤더 내용을 화면 폭 끝까지(지도처럼 꽉 찬 페이지)
 *   data-sections  넓은 화면(1320px↑)에서 주 탐색 옆에 보이는 섹션 바로가기. 스크롤 위치에 따라 aria-current="true"
 *
 * ── 데이터: 폴링은 페이지당 한 번 ────────────────────────────────────────────────
 *   이 파일은 JARVIS.subscribe 를 감싼다. 페이지는 지금처럼 JARVIS.subscribe(cb, opts) 를 한 번만 부르면 되고,
 *   같은 응답이 먼저 헤더로, 그다음 페이지 cb 로 간다(opts.onError 도 헤더가 먼저 본다).
 *   셸은 스스로 /api 를 부르지 않는다 → 페이지가 subscribe 를 부르지 않으면 헤더는 "연결 중"에 머문다.
 *
 * ── window.JarvisShell ───────────────────────────────────────────────────────────
 *   update(snap, state)  헤더 갱신(감싼 subscribe 가 부른다. 보통 직접 부를 일 없음)
 *   live()               "wait" | "live" | "late" | "stale"  지금 연결 상태(대기·실시간·지연·끊김)
 *   view()               헤더에 보이는 자세한 상태 = live() + "partial"(API 정상·일부 수집 지연) · "frozen"(응답은 오나 스냅샷이 안 바뀜)
 *   monitor              모니터 자기 진단 도구: of(snap) · svc(snap) · health(snap) · href(snap) · collectorName(name)
 *                        (헤더의 "실시간" 표시를 누르면 연결·자기 진단 팝오버 → 개요의 모니터 상세 시트로)
 *   theme()              { pref: "system"|"light"|"dark", effective: "light"|"dark" }
 *   setTheme(pref)       테마 바꾸기(저장 포함)
 *   on(type, fn)         "theme" | "live" 변화 구독 → 해제 함수 반환. 같은 내용이 document 의
 *                        CustomEvent "jarvis:theme" / "jarvis:live" (detail 같음)로도 나간다.
 *                        live detail = { state: live(), view: view(), partial: bool }
 *   el                   <header id="jarvis-shell">
 *
 * ── 페이지와의 약속 ─────────────────────────────────────────────────────────────
 *   - 헤더 안 클래스·속성은 sh- / data-sh- 접두어. 페이지 CSS·JS 는 이것을 건드리지 않는다.
 *   - 토큰(:root 의 --bg, --text, --ok …)은 shell.css 가 정한다. 페이지는 :root 에서 같은 이름을 다른 뜻으로 재정의하지 않는다.
 *   - 홈은 경보 목록 요소에 id="alerts" 를 둔다 — 헤더 경보 팝오버의 "모두 보기" 링크 대상(/#alerts).
 *   - 테마 저장 키 localStorage "jarvis.theme"(옛 "pit5.theme" 도 읽는다) = "light"|"dark"|"system". <html data-theme> 로 적용된다.
 *
 * 안전: 데이터로 만드는 HTML 은 JARVIS.html(자동 이스케이프·URL 검사)만 쓴다. 외부 링크는 새 탭 + noopener noreferrer.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  const doc = document;
  const root = doc.documentElement;
  if (!P) { console.error("[shell] /shared/jarvis.js 가 먼저 로드되어야 합니다."); return; }
  const { html, raw, fmt, labels } = P;

  // ------------------------------------------------------------------ 헤더 자리
  let el = doc.getElementById("jarvis-shell");
  if (!el) {
    el = doc.createElement("header");
    el.id = "jarvis-shell";
    (doc.body || root).insertBefore(el, doc.body ? doc.body.firstChild : null);
  }
  const PAGE = el.getAttribute("data-page") || (/^\/map(\/|$)/.test(location.pathname) ? "map" : "home");
  const SECTIONS = (el.getAttribute("data-sections") || "")
    .split(",").map((s) => s.trim()).filter(Boolean)
    .map((s) => { const i = s.indexOf(":"); return i > 0 ? { id: s.slice(0, i).trim(), label: s.slice(i + 1).trim() } : null; })
    .filter((s) => s && /^[A-Za-z][\w-]*$/.test(s.id));

  // ------------------------------------------------------------------ 아이콘(상수 SVG — 데이터 없음)
  const ICON = {
    home: '<rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2"/><rect x="13" y="3.5" width="7.5" height="4.5" rx="1.6"/><rect x="3.5" y="13" width="7.5" height="7.5" rx="2"/><rect x="13" y="10" width="7.5" height="10.5" rx="2"/>',
    map: '<circle cx="5.5" cy="6" r="2.3"/><circle cx="5.5" cy="18" r="2.3"/><circle cx="18.5" cy="12" r="2.3"/><path d="M7.8 6h2.7a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M7.8 18h2.7a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3"/>',
    link: '<path d="M10 14a4.2 4.2 0 0 0 6 .2l2.8-2.8a4.2 4.2 0 0 0-6-6l-1.2 1.2"/><path d="M14 10a4.2 4.2 0 0 0-6-.2l-2.8 2.8a4.2 4.2 0 0 0 6 6l1.2-1.2"/>',
    copy: '<rect x="8.5" y="8.5" width="11.5" height="11.5" rx="2.4"/><path d="M15.5 8.5V6.4A2.4 2.4 0 0 0 13.1 4H6.4A2.4 2.4 0 0 0 4 6.4v6.7a2.4 2.4 0 0 0 2.4 2.4h2.1"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    caret: '<path d="m7 10 5 5 5-5"/>',
    system: '<rect x="3" y="4.5" width="18" height="12" rx="2.2"/><path d="M8.5 20h7M12 16.5V20"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2.8v2M12 19.2v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2.8 12h2M19.2 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    moon: '<path d="M20.2 14.3A8.4 8.4 0 0 1 9.7 3.8a8.4 8.4 0 1 0 10.5 10.5z"/>',
    calm: '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    // 하드웨어 계기
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2.2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    gpu: '<rect x="2.5" y="6" width="19" height="12" rx="2.4"/><circle cx="9" cy="12" r="3"/><path d="M15.5 10h3M15.5 14h3M6 18v2.5M18 18v2.5"/>',
    mem: '<rect x="2.5" y="6.5" width="19" height="9" rx="1.8"/><path d="M6.5 9.5v3M10 9.5v3M14 9.5v3M17.5 9.5v3M5 15.5v3M9 15.5v3M15 15.5v3M19 15.5v3"/>',
    temp: '<path d="M14 14.5V5a2 2 0 1 0-4 0v9.5a4 4 0 1 0 4 0z"/><path d="M12 9.5v7"/>',
    bolt: '<path d="M13 2.5 5 13.5h6l-1 8 8-11h-6z"/>',
    net: '<path d="M8 3.5v17M8 20.5l-4-4M8 20.5l4-4M16 20.5v-17M16 3.5l-4 4M16 3.5l4 4"/>',
    disk: '<rect x="3" y="4" width="18" height="16" rx="2.6"/><path d="M3 13.5h18"/><path d="M16.5 16.8h.01M13.5 16.8h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  };
  const ic = (n, cls) => raw('<svg class="sh-ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + ICON[n] + "</svg>");
  const MARK = raw('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="2.5" y="2.5" width="11" height="8" rx="2.4"/><rect x="15.5" y="2.5" width="6" height="8" rx="2.4"/><rect x="2.5" y="12.5" width="6" height="9" rx="2.4"/><rect x="10.5" y="12.5" width="11" height="9" rx="2.4"/></svg>');

  const NAV = [
    { id: "home", href: "/", label: "개요", icon: "home" },
    { id: "map", href: "/map/", label: "네트워크 맵", short: "맵", icon: "map" },
  ];
  const THEMES = [
    { v: "system", label: "시스템", aria: "시스템 설정 따르기", icon: "system" },
    { v: "light", label: "라이트", aria: "라이트 테마", icon: "sun" },
    { v: "dark", label: "다크", aria: "다크 테마", icon: "moon" },
  ];
  const SRC = {
    system: "시스템", container: "컨테이너", systemd: "systemd", route: "라우트", exposure: "노출", dns: "DNS", cert: "인증서",
    tunnel: "터널", caddy: "Caddy", traffic: "트래픽", tailscale: "Tailscale", hermes: "Hermes", monitor: "모니터",
  };
  const SHORT = { ok: "정상", warn: "주의", crit: "장애" };
  // 연결 상태(live())는 wait · live · late · stale 네 가지 — 페이지들이 이것으로 안내 띠를 정한다.
  // 헤더 표시는 더 자세히 나눈다: 응답은 오는데 수집기 일부가 늦으면 "일부 수집 지연", 응답은 오는데 스냅샷이 안 바뀌면 "수집 멈춤".
  const LIVE = {
    wait: { tone: "muted", text: "연결 중" },
    live: { tone: "ok", text: "실시간" },
    partial: { tone: "warn", text: "일부 수집 지연" },
    late: { tone: "warn", text: "응답 지연" },
    frozen: { tone: "warn", text: "수집 멈춤" },
    stale: { tone: "crit", text: "API 응답 없음" },
  };

  // ------------------------------------------------------------------ 뼈대(한 번만 그린다)
  const themeGroup = (wide) => html`<div class="${"sh-theme" + (wide ? " wide" : "")}" role="radiogroup" aria-label="화면 테마">${THEMES.map((t) =>
    html`<button type="button" role="radio" aria-checked="false" tabindex="-1" data-sh-theme="${t.v}" aria-label="${t.aria}" title="${t.aria}">${ic(t.icon)}<span class="sh-theme-l" aria-hidden="true">${t.label}</span></button>`)}</div>`;

  P.render(el, html`<div class="sh-in">
  <a class="sh-brand" href="/" aria-label="Jarvis — 개요로 가기">
    <span class="sh-mark" aria-hidden="true">${MARK}</span>
    <span class="sh-brand-text"><span class="sh-name"><b>Jarvis</b></span><span class="sh-host" data-sh="host">호스트 확인 중…</span></span>
  </a>
  <nav class="sh-nav" aria-label="화면 전환">${NAV.map((n) => html`<a href="${n.href}" data-sh-nav="${n.id}"${n.id === PAGE ? raw(' aria-current="page"') : ""}>${ic(n.icon)}<span class="${"sh-tab-l" + (n.short ? " has-short" : "")}">${n.label}</span>${n.short ? html`<span class="sh-tab-s" aria-hidden="true">${n.short}</span>` : ""}</a>`)}</nav>
  ${SECTIONS.length ? html`<nav class="sh-sections" aria-label="이 페이지의 섹션">${SECTIONS.map((s) => html`<a href="${"#" + s.id}" data-sh-sec="${s.id}">${s.label}</a>`)}</nav>` : ""}
  <div class="sh-right">
    <div class="sh-pop" data-sh-pop="alerts">
      <button type="button" class="sh-status" data-sh-tone="muted" aria-expanded="false" aria-controls="sh-alerts-pop" aria-label="전체 상태 확인 중 — 경보 목록">
        <i class="sh-shape" aria-hidden="true"></i>
        <span class="sh-status-l" aria-hidden="true"><span class="sh-l-full" data-sh="ovFull">확인 중</span><span class="sh-l-short" data-sh="ovShort">확인 중</span></span>
        <span class="sh-status-n" aria-hidden="true" data-sh="ovN"></span>
        ${ic("caret", "sh-caret")}
      </button>
      <div class="sh-popover" id="sh-alerts-pop" role="region" aria-label="경보" tabindex="-1" hidden></div>
    </div>
    <div class="sh-pop" data-sh-pop="monitor">
      <button type="button" class="sh-live" data-sh-tone="muted" data-sh="live" aria-expanded="false" aria-controls="sh-mon-pop" aria-label="연결 상태: 첫 데이터를 기다리는 중 — 모니터 상태 보기">
        <span class="sh-live-mark" aria-hidden="true"><i class="sh-live-dot"></i><i class="sh-live-ring" data-sh="ring"></i></span><span class="sh-live-t" data-sh="liveT">연결 중</span><span class="sh-clock" data-sh="clock"></span>
      </button>
      <div class="sh-popover" id="sh-mon-pop" role="region" aria-label="연결 · 모니터 상태" tabindex="-1" hidden></div>
    </div>
    <div class="sh-pop" data-sh-pop="access">
      <button type="button" class="sh-btn" aria-expanded="false" aria-controls="sh-access-pop" aria-label="접속 주소와 화면 설정" title="접속 주소">
        ${ic("link")}<span class="sh-acc-l">접속 주소</span><span class="sh-acc-n" data-sh="accN"></span>
      </button>
      <div class="sh-popover" id="sh-access-pop" role="region" aria-label="접속 주소와 화면 설정" tabindex="-1" hidden>
        <div class="sh-pop-head"><p class="sh-pop-h">Jarvis 에 접속하는 주소</p></div>
        <ul class="sh-acc" data-sh="acc"><li><span class="sh-aud">–</span><span class="sh-acc-u">불러오는 중…</span></li></ul>
        <div class="sh-pop-sec sh-pop-theme"><p class="sh-pop-h">화면 테마</p>${themeGroup(true)}</div>
      </div>
    </div>
    ${themeGroup(false)}
  </div>
</div>
<p class="sh-sr" role="status" aria-live="polite" data-sh="sr"></p>`);

  const Q = Object.create(null);
  el.querySelectorAll("[data-sh]").forEach((n) => { Q[n.getAttribute("data-sh")] = n; });
  const statusBtn = el.querySelector(".sh-status");
  const accBtn = el.querySelector('[aria-controls="sh-access-pop"]');
  const POPS = {
    alerts: { wrap: el.querySelector('[data-sh-pop="alerts"]'), btn: statusBtn, panel: doc.getElementById("sh-alerts-pop") },
    monitor: { wrap: el.querySelector('[data-sh-pop="monitor"]'), btn: Q.live, panel: doc.getElementById("sh-mon-pop") },
    access: { wrap: el.querySelector('[data-sh-pop="access"]'), btn: accBtn, panel: doc.getElementById("sh-access-pop") },
  };

  const setText = (n, s) => { if (n && n.textContent !== s) n.textContent = s; };
  const setAttr = (n, k, v) => { if (n && n.getAttribute(k) !== v) n.setAttribute(k, v); };
  const nowS = () => Date.now() / 1000;

  // ------------------------------------------------------------------ 알림(이벤트)
  const subs = { theme: new Set(), live: new Set() };
  function emit(type, detail) {
    subs[type].forEach((fn) => { try { fn(detail); } catch (e) { console.error("[shell] " + type + " 구독자 오류:", e); } });
    try { doc.dispatchEvent(new CustomEvent("jarvis:" + type, { detail })); } catch (e) { /* 오래된 브라우저 */ }
  }
  let srLast = "";
  function announce(s) {
    if (!s || s === srLast) return;
    srLast = s;
    setText(Q.sr, s);
  }

  // ------------------------------------------------------------------ 테마
  const THEME_KEY = "jarvis.theme";
  const mqDark = window.matchMedia ? matchMedia("(prefers-color-scheme: dark)") : { matches: false };
  const validPref = (v) => (v === "light" || v === "dark" ? v : "system");
  let pref = validPref(window.JarvisLab ? window.JarvisLab.getPref("theme", root.getAttribute("data-theme")) : root.getAttribute("data-theme"));
  const effective = () => (pref === "system" ? (mqDark.matches ? "dark" : "light") : pref);
  let lastEff = effective();
  const metaTheme = Array.from(doc.querySelectorAll('meta[name="theme-color"]')).map((m) => ({ m, orig: m.getAttribute("content") }));
  function syncThemeUI() {
    el.querySelectorAll("[data-sh-theme]").forEach((b) => {
      const on = b.getAttribute("data-sh-theme") === pref;
      setAttr(b, "aria-checked", on ? "true" : "false");
      setAttr(b, "tabindex", on ? "0" : "-1");
    });
    // 브라우저 UI 색: 시스템이면 원래 media 별 값, 직접 고르면 그 테마의 바탕색
    const bg = pref === "system" ? null : (getComputedStyle(root).getPropertyValue("--bg") || "").trim();
    metaTheme.forEach(({ m, orig }) => setAttr(m, "content", bg || orig || ""));
  }
  function applyTheme(next, save) {
    pref = validPref(next);
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    if (save) {
      if (window.JarvisLab) window.JarvisLab.setPref("theme", pref);
      else try { localStorage.setItem(THEME_KEY, pref); localStorage.removeItem("pit5.bento.theme"); } catch (e) { /* 저장 불가: 이번 방문에만 적용 */ }
    }
    syncThemeUI();
    const eff = effective();
    if (eff !== lastEff || save) { lastEff = eff; emit("theme", { pref, effective: eff }); }
  }
  el.addEventListener("click", (e) => {
    const b = e.target.closest("[data-sh-theme]");
    if (!b) return;
    applyTheme(b.getAttribute("data-sh-theme"), true);
    announce("화면 테마: " + (THEMES.find((t) => t.v === pref) || {}).label);
  });
  // 라디오 그룹 키보드: ←/→/↑/↓ 로 고르고, Home/End
  el.addEventListener("keydown", (e) => {
    const b = e.target.closest && e.target.closest("[data-sh-theme]");
    if (!b) return;
    const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1, Home: "first", End: "last" };
    if (!(e.key in keys)) return;
    e.preventDefault();
    const btns = Array.from(b.parentElement.querySelectorAll("[data-sh-theme]"));
    let i = btns.indexOf(b);
    const k = keys[e.key];
    i = k === "first" ? 0 : k === "last" ? btns.length - 1 : (i + k + btns.length) % btns.length;
    applyTheme(btns[i].getAttribute("data-sh-theme"), true);
    btns[i].focus();
  });
  const onSystemTheme = () => { if (pref === "system") applyTheme("system", false); };
  if (mqDark.addEventListener) mqDark.addEventListener("change", onSystemTheme);
  else if (mqDark.addListener) mqDark.addListener(onSystemTheme);
  // 다른 탭에서 바꾸면 따라간다
  window.addEventListener("storage", (e) => { if (!window.JarvisLab && e.key === THEME_KEY) applyTheme(e.newValue, false); });
  if (window.JarvisLab) applyTheme(pref, false);
  else syncThemeUI();

  // ------------------------------------------------------------------ 팝오버(경보 · 접속 주소)
  let openPop = null;
  function setPop(name, focusBtn) {
    if (name && !POPS[name]) name = null;
    const prev = openPop;
    openPop = name;
    for (const k in POPS) {
      const p = POPS[k];
      const on = k === name;
      setAttr(p.btn, "aria-expanded", on ? "true" : "false");
      if (p.panel.hidden === on) p.panel.hidden = !on;
    }
    if (name === "alerts") renderAlertsPop();
    if (name === "monitor") renderMonitorPop();
    if (!name && prev && focusBtn) POPS[prev].btn.focus();
  }
  statusBtn.addEventListener("click", () => setPop(openPop === "alerts" ? null : "alerts"));
  Q.live.addEventListener("click", () => setPop(openPop === "monitor" ? null : "monitor"));
  accBtn.addEventListener("click", () => setPop(openPop === "access" ? null : "access"));
  // 바깥을 누르면 닫는다
  doc.addEventListener("pointerdown", (e) => {
    if (openPop && !POPS[openPop].wrap.contains(e.target)) setPop(null);
  }, true);
  // Esc: 열린 팝오버만 닫고 페이지의 Esc 처리(검사기 닫기 등)로 흘려보내지 않는다
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !openPop) return;
    e.preventDefault();
    e.stopPropagation();
    setPop(null, true);
  }, true);
  // 키보드 포커스가 팝오버 밖으로 나가면 닫는다
  for (const k in POPS) {
    POPS[k].wrap.addEventListener("focusout", (e) => {
      if (openPop !== k || copying) return;
      const to = e.relatedTarget;
      if (to && !POPS[k].wrap.contains(to)) setPop(null);
    });
  }
  // 같은 페이지 안 링크(경보 "모두 보기" · 자기 진단 보기)를 누르면 닫는다
  POPS.alerts.panel.addEventListener("click", (e) => { if (e.target.closest("a")) setPop(null); });
  POPS.monitor.panel.addEventListener("click", (e) => { if (e.target.closest("a")) setPop(null); });

  // ------------------------------------------------------------------ 주소 복사
  let copying = false;       // 복사용 textarea 가 잠깐 포커스를 가져가도 팝오버를 닫지 않게
  async function copyText(t) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; }
    } catch (e) { /* 아래 방법으로 */ }
    const ta = doc.createElement("textarea");
    ta.value = t;
    ta.setAttribute("readonly", "");
    ta.setAttribute("aria-hidden", "true");
    ta.style.position = "fixed";
    ta.style.top = "0";
    ta.style.left = "0";
    ta.style.opacity = "0";
    ta.style.pointerEvents = "none";
    copying = true;
    doc.body.appendChild(ta);
    let ok = false;
    try { ta.select(); ok = doc.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    copying = false;
    return ok;
  }
  POPS.access.panel.addEventListener("click", async (e) => {
    const b = e.target.closest("[data-sh-copy]");
    if (!b) return;
    const url = b.getAttribute("data-sh-copy") || "";
    const ok = await copyText(url);
    b.focus();
    if (ok) {
      b.classList.add("is-copied");
      announce("주소를 복사했어요: " + url);
      clearTimeout(b._shT);
      b._shT = setTimeout(() => b.classList.remove("is-copied"), 1600);
    } else {
      announce("복사하지 못했어요. 주소를 길게 눌러 직접 복사해 주세요.");
    }
  });

  // ------------------------------------------------------------------ 섹션 바로가기(스크롤 위치 표시)
  if (SECTIONS.length) {
    const links = Array.from(el.querySelectorAll("[data-sh-sec]"));
    let rq = 0;
    let current = null;
    const spy = () => {
      rq = 0;
      const line = window.innerHeight * 0.35;
      let cur = null;
      for (const s of SECTIONS) {
        const t = doc.getElementById(s.id);
        if (t && t.getClientRects().length && t.getBoundingClientRect().top <= line) cur = s.id;
      }
      // 맨 아래까지 내렸으면 마지막 섹션(마지막 섹션이 화면보다 짧아도 표시되게)
      if (window.innerHeight + window.scrollY >= doc.documentElement.scrollHeight - 4) {
        const shown = SECTIONS.filter(s => doc.getElementById(s.id)?.getClientRects().length);
        if (shown.length) cur = shown[shown.length - 1].id;
      }
      if (cur === current) return;
      current = cur;
      links.forEach((a) => setAttr(a, "aria-current", String(a.getAttribute("data-sh-sec") === cur)));
    };
    const req = () => { if (!rq) rq = requestAnimationFrame(spy); };
    window.addEventListener("scroll", req, { passive: true });
    window.addEventListener("resize", req, { passive: true });
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", req); else req();
  }
  // 지금 페이지의 탭을 다시 누르면 새로 읽지 않고 맨 위로
  el.querySelector(".sh-nav").addEventListener("click", (e) => {
    const a = e.target.closest("a[aria-current='page']");
    if (!a || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button) return;
    e.preventDefault();
    const calm = P.embed || (window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches);
    window.scrollTo({ top: 0, behavior: calm ? "auto" : "smooth" });
  });

  // ------------------------------------------------------------------ 모니터 자기 진단(이 웹 서비스 자신)
  //   헤더 연결 팝오버 · 개요 상세 시트 · 서비스 카드가 같은 판정·같은 이름을 쓴다(JarvisShell.monitor).
  const COLLECTOR_L = {
    system_fast: "시스템 계기", system: "시스템", processes: "프로세스", docker: "Docker", systemd: "systemd 유닛",
    listeners: "열린 포트", tailscale: "Tailscale", tunnel: "Cloudflare 터널", caddy_log: "Caddy 접근 로그",
    caddy: "Caddy 구성", probes: "라우트 프로브", hermes: "Hermes 게이트웨이",
    dns: "공개 DNS", http_ports: "HTTP 포트 확인",
  };
  const LOOP_L = { fast: "빠른 주기", medium: "보통 주기", slow: "느린 주기", dns: "DNS 주기", persist: "기록 저장" };
  const LOOP_ORDER = ["fast", "medium", "slow", "dns", "persist"];
  const collectorName = (n) => COLLECTOR_L[n] || String(n || "?");
  /** 초 → "1.3초" · "42초" · "3분 5초" (경과 시간 표기) */
  const secs = (v) => (typeof v !== "number" || !isFinite(v) ? "–" : v < 10 ? fmt.num(v, 1) + "초" : v < 60 ? fmt.num(v, 0) + "초" : fmt.dur(v, 2));
  function monitorSvc(s) {
    const list = (s && s.services) || [];
    return list.find((x) => x && x.kind === "monitor") || list.find((x) => x && x.id === "infra:monitor") || null;
  }
  /** snap.monitor(없으면 모니터 서비스의 extra.monitor) */
  function monitorOf(s, sv) {
    if (!s) return null;
    if (s.monitor && typeof s.monitor === "object") return s.monitor;
    sv = sv || monitorSvc(s);
    return (sv && sv.extra && sv.extra.monitor) || null;
  }
  /** 자기 진단 상세 링크(개요 상세 시트). 개요 안에서는 #svc=, 다른 페이지에서는 /#svc= */
  function monitorHref(s) {
    const sv = monitorSvc(s);
    return sv ? (PAGE === "home" ? "" : "/") + "#svc=" + encodeURIComponent(sv.id) : null;
  }
  /** 자기 진단 요약 → { known, ok, tone, text, short, total, fresh, problems[], stale[], errors[] } */
  function monitorHealth(s) {
    const m = monitorOf(s);
    if (!m) return { known: false, ok: null, tone: "muted", text: "자기 진단 정보를 받지 못했어요", short: "정보 없음", detail: "자기 진단 정보를 받지 못했어요", names: "", total: 0, fresh: 0, problems: [], stale: [], errors: [] };
    const cs = (Array.isArray(m.collectors) ? m.collectors : []).filter((c) => c && c.name);
    const named = new Set(Array.isArray(m.problems) ? m.problems : []);
    const problems = cs.filter((c) => c.stale || c.error || named.has(c.name));
    named.forEach((n) => { if (!cs.some((c) => c.name === n)) problems.push({ name: n, stale: true }); });
    const stale = problems.filter((c) => c.stale || (!c.error && named.has(c.name)));
    const errors = problems.filter((c) => c.error);
    const ok = m.ok !== false && !problems.length;
    const names = problems.slice(0, 3).map((c) => collectorName(c.name)).join(" · ") + (problems.length > 3 ? " 외 " + (problems.length - 3) + "개" : "");
    // text = 한 줄 요약(툴팁·헤더) · short = 짧은 이름표 · detail = 이름표 뒤에 붙는 설명
    let text; let short; let detail;
    if (ok) { short = "정상"; detail = cs.length ? "수집기 " + cs.length + "개 모두 제때 갱신되고 있어요" : "모니터가 스스로 정상이라고 알려요"; text = detail; }
    else if (!problems.length) { short = "확인 필요"; detail = "모니터가 스스로 문제를 보고했어요"; text = detail; }
    else if (errors.length && !stale.length) { short = "수집 오류 " + errors.length + "개"; detail = names + " 수집에 오류가 났어요"; text = short + " — " + names; }
    else { short = "수집 지연 " + problems.length + "개"; detail = names + " 수집이 늦어지고 있어요"; text = short + " — " + names; }
    return { known: true, ok, tone: ok ? "ok" : "warn", text, short, detail, names, total: cs.length, fresh: cs.length - problems.filter((c) => cs.indexOf(c) >= 0).length, problems, stale, errors, m };
  }

  // ------------------------------------------------------------------ 데이터 → 헤더
  let S = null;
  let ST = { stale: false };
  let lastOkAt = 0;        // 마지막으로 응답을 받은 시각(이 브라우저 시계)
  let lastGen = null;      // 서버 generated_at — 서버 시계와 브라우저 시계를 비교하지 않으려고 "바뀐 때"만 본다
  let genChangedAt = 0;
  let failedBeforeData = false;
  let graceUntil = 0;      // 탭이 다시 보이면 곧 새로 받아오니 잠깐은 "지연"으로 보지 않는다
  let liveState = "wait";

  const shortModel = (m) => String(m || "").replace(/\s+Model\s+\S+.*$/i, "").replace(/\s+Rev\s+.*$/i, "").trim();
  const shortUrl = (u) => String(u || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");
  const isHere = (u) => { try { return new URL(u).origin === location.origin; } catch (e) { return false; } };

  const BASE_TITLE = document.title; // "개요 · Jarvis" → "개요 · <호스트> · Jarvis"
  function renderHost() {
    const m = S.meta || {};
    const h = (S.system && S.system.host) || {};
    const host = m.host || h.hostname || "host";
    document.title = BASE_TITLE.replace(/Jarvis$/, host + " · Jarvis");
    const model = shortModel(h.model);
    P.render(Q.host, html`<b>${host}</b>${model ? " · " + model : ""}`);
    setAttr(Q.host, "title", [h.model, h.os, h.kernel ? "커널 " + h.kernel : null, h.arch].filter(Boolean).join(" · ") || host);
  }

  function renderStatus() {
    const sum = S.summary || {};
    const a = sum.alerts || {};
    const t = P.tone(sum.status);
    const full = labels.overall[sum.status] || "확인 중";
    setAttr(statusBtn, "data-sh-tone", t);
    setText(Q.ovFull, full);
    setText(Q.ovShort, SHORT[sum.status] || "확인 중");
    const crit = a.crit || 0; const warn = a.warn || 0; const info = a.info || 0;
    const parts = [];
    if (crit) parts.push("심각 " + fmt.num(crit));
    if (warn) parts.push("경고 " + fmt.num(warn));
    const nFull = parts.join(" · ");
    const nShort = crit + warn ? fmt.num(crit + warn) : "";
    P.render(Q.ovN, nFull ? html`<span class="sh-n-full">${nFull}</span><span class="sh-n-short">${nShort}</span>` : "");
    const counts = "심각 " + crit + "건, 경고 " + warn + "건, 정보 " + info + "건";
    setAttr(statusBtn, "aria-label", "전체 상태: " + full + " — " + counts + " — 경보 목록");
    setAttr(statusBtn, "title", full + " · " + counts.replace(/건/g, ""));
    announce("전체 상태: " + full + (nFull ? " — " + nFull + "건" : ""));
  }

  function renderAlertsPop() {
    const panel = POPS.alerts.panel;
    if (!S) {
      P.render(panel, html`<p class="sh-calm">${failedBeforeData ? "Jarvis API 에 연결하지 못했어요. 자동으로 다시 시도하고 있어요." : "상태를 불러오는 중이에요…"}</p>`);
      return;
    }
    const list = S.alerts || [];
    const c = (S.summary && S.summary.alerts) || {};
    const errs = Object.entries((S.meta && S.meta.errors) || {});
    const now = nowS();
    const moreHref = PAGE === "home" ? "#alerts" : "/#alerts";
    P.render(panel, html`<div class="sh-pop-head"><p class="sh-pop-h">경보</p><p class="sh-pop-c">심각 <b>${fmt.num(c.crit || 0)}</b> · 경고 <b>${fmt.num(c.warn || 0)}</b> · 정보 <b>${fmt.num(c.info || 0)}</b></p></div>
${list.length ? html`<ul class="sh-al">${list.map((a) => {
      const t = P.tone(a.level);
      // 페이지가 경보마다 바로 가는 곳을 알려 줄 수 있다(지도: #focus=…, 개요: #svc=…)
      let go = null;
      if (typeof api.alertLink === "function") { try { go = api.alertLink(a); } catch (e) { go = null; } }
      return html`<li data-sh-tone="${t}"><i class="sh-shape" aria-hidden="true"></i><div><p class="sh-al-m">${a.message}</p><p class="sh-al-s"><b>${labels.level[a.level] || a.level}</b> · ${SRC[a.source] || a.source || "–"}${a.since ? " · " + fmt.ago(a.since, now) + "부터" : ""}</p>${go && go.href ? html`<a class="sh-al-go" href="${go.href}">${go.label || "보기"}${ic("arrow")}</a>` : ""}</div></li>`;
    })}</ul>` : html`<p class="sh-calm">${ic("calm")}<span>지금은 확인할 경보가 없어요.</span></p>`}
${errs.length ? html`<div class="sh-pop-sec"><p class="sh-pop-h">수집 오류 ${fmt.num(errs.length)}</p><ul class="sh-errs">${errs.map(([k, v]) => html`<li><b>${k}</b>${v}</li>`)}</ul></div>` : ""}
${PAGE !== "home" || doc.getElementById("alerts") ? html`<a class="sh-pop-link" href="${moreHref}">${PAGE === "home" ? "경보 타일로 이동" : "개요에서 경보 자세히 보기"}${ic("arrow")}</a>` : ""}`);
  }

  function renderAccess() {
    const acc = ((S.meta && S.meta.access) || []).filter((a) => a && a.url);
    setText(Q.accN, acc.length > 1 ? fmt.num(acc.length) : "");
    P.render(Q.acc, acc.length ? acc.map((a) => {
      const u = shortUrl(a.url);
      return html`<li><span class="sh-aud" data-aud="${a.audience}">${labels.audience[a.audience] || a.audience || "–"}</span><span class="sh-acc-u"><a href="${a.url}" target="_blank" rel="noopener noreferrer">${u}</a>${isHere(a.url) ? html`<span class="sh-here">지금 보고 있는 주소</span>` : ""}</span><button type="button" class="sh-copy" data-sh-copy="${a.url}" aria-label="${u + " 주소 복사"}" title="주소 복사">${ic("copy", "sh-ic-copy")}${ic("check", "sh-ic-ok")}</button></li>`;
    }) : html`<li><span class="sh-aud">–</span><span class="sh-acc-u">등록된 접속 주소가 없어요.</span></li>`);
  }

  function computeLive() {
    if (!S) return failedBeforeData ? "stale" : "wait";
    if (ST.stale) return "stale";
    const now = nowS();
    if (now < graceUntil) return "live";
    if (now - lastOkAt > 20) return "late";            // 3초 주기인데 20초 넘게 응답이 없다(홈의 안내 띠와 같은 기준)
    if (genChangedAt && now - genChangedAt > 30) return "late";  // 응답은 오는데 서버 수집이 멈췄다
    return "live";
  }
  /** 헤더에 보이는 자세한 상태: 연결 상태 + 수집기 상태 */
  function computeView(st) {
    if (st === "late") return nowS() - lastOkAt > 20 ? "late" : "frozen";
    if (st === "live" && S) { const h = monitorHealth(S); if (h.known && !h.ok) return "partial"; }
    return st;
  }
  let viewState = "wait";
  const mqCalm = window.matchMedia ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  /** 새 데이터를 받을 때마다 점 둘레에 한 번 퍼지는 고리(끊임없는 애니메이션 대신 — 약한 기기 배려) */
  function beat() {
    const r = Q.ring;
    if (!r || !r.animate || doc.hidden || P.embed || mqCalm.matches || viewState !== "live") return;
    try { r.animate([{ transform: "scale(.4)", opacity: 0.75 }, { transform: "scale(1.35)", opacity: 0 }], { duration: 1300, easing: "cubic-bezier(0.2, 0.8, 0.2, 1)" }); } catch (e) { /* 무시 */ }
  }
  function liveTitle(view) {
    const gen = S && S.meta && S.meta.generated_at;
    if (!S) return failedBeforeData ? "Jarvis API 가 응답하지 않아요 — 자동으로 다시 시도 중" : "첫 데이터를 기다리는 중";
    const base = " · 데이터 기준 " + fmt.time(gen) + " · 마지막 수신 " + fmt.ago(lastOkAt);
    if (view === "stale") return "API 응답 없음 — 마지막으로 받은 데이터를 보여 주는 중" + base;
    if (view === "late") return "응답 지연 — 20초 넘게 새 응답이 없어요" + base;
    if (view === "frozen") return "수집 멈춤 — 응답은 오지만 30초 넘게 스냅샷이 바뀌지 않았어요" + base;
    if (view === "partial") return "API 는 정상, " + monitorHealth(S).text + base;
    return LIVE[view].text + base;
  }
  function updateLive() {
    const st = computeLive();
    const view = computeView(st);
    const L = LIVE[view];
    setAttr(Q.live, "data-sh-tone", L.tone);
    setText(Q.liveT, !S && failedBeforeData ? "API 응답 없음" : L.text);
    const gen = S && S.meta && S.meta.generated_at;
    setText(Q.clock, gen ? fmt.time(gen) : "");
    const title = liveTitle(view);
    setAttr(Q.live, "title", title);
    setAttr(Q.live, "aria-label", "연결 상태: " + title + " — 연결·모니터 상태 보기");
    const viewChanged = view !== viewState;
    viewState = view;
    if (st !== liveState || viewChanged) {
      const prev = liveState;
      const changed = st !== liveState;
      liveState = st;
      setAttr(el, "data-sh-live", st);
      setAttr(el, "data-sh-view", view);
      if (changed && (prev !== "wait" || st !== "live")) announce(st === "live" ? "실시간 연결이 돌아왔어요" : st === "late" ? "데이터 갱신이 늦어지고 있어요" : st === "stale" ? "Jarvis API 가 응답하지 않아요" : "");
      else if (viewChanged && view === "partial") announce("일부 수집이 늦어지고 있어요");
      emit("live", { state: st, view, partial: view === "partial" });
    }
    if (openPop === "monitor") renderMonitorPop();
  }

  /** 헤더 연결 팝오버: 연결 상태 + 모니터 자기 진단 요약 + 개요의 자기 진단으로 가는 링크 */
  function renderMonitorPop() {
    const panel = POPS.monitor.panel;
    const st = computeLive();
    const view = computeView(st);
    const L = LIVE[view];
    const gen = S && S.meta && S.meta.generated_at;
    const connLine = !S
      ? (failedBeforeData ? "Jarvis API 가 응답하지 않아요. 자동으로 다시 시도하고 있어요." : "첫 데이터를 기다리는 중이에요…")
      : view === "stale" ? "API 가 응답하지 않아요 — 마지막으로 받은 데이터를 보여 주는 중이에요."
      : view === "late" ? "20초 넘게 새 응답이 없어요. 네트워크나 서버가 느릴 수 있어요."
      : view === "frozen" ? "응답은 오지만 30초 넘게 스냅샷이 바뀌지 않았어요 — 서버의 수집이 멈췄을 수 있어요."
      : (P.embed ? "10초" : "3초") + "마다 새 데이터를 받고 있어요.";
    const connTone = view === "partial" ? "ok" : L.tone;
    const H = S ? monitorHealth(S) : null;
    const oldData = view === "stale" || view === "late";
    const m = H && H.m;
    const href = S ? monitorHref(S) : null;
    const facts = m ? [
      m.version ? "v" + m.version + (m.commit ? " · " + commitInfo(m.commit).text : "") : null,
      typeof m.uptime_s === "number" ? "가동 " + fmt.dur(m.uptime_s, 2) : null,
      typeof m.rss_bytes === "number" ? "메모리 " + fmt.bytes(m.rss_bytes) : null,
      typeof m.snapshot_ms === "number" ? "스냅샷 " + fmt.ms(m.snapshot_ms) : null,
    ].filter(Boolean) : [];
    // 제자리 갱신(morph) — 2초마다 "마지막 수신 N초 전"이 바뀌어도 링크 포커스가 유지된다
    morph(panel, html`<div class="sh-pop-head"><p class="sh-pop-h">연결 · 모니터</p><p class="sh-pop-c">${gen ? "데이터 기준 " + fmt.time(gen) : ""}</p></div>
<div class="sh-mon-row" data-sh-tone="${connTone}"><i class="sh-shape" aria-hidden="true"></i><div><p class="sh-mon-t">${view === "partial" ? "API 연결 정상" : L.text}</p><p class="sh-mon-s">${connLine}${S && lastOkAt ? " 마지막 수신 " + (nowS() - lastOkAt < 2 ? "방금" : fmt.ago(lastOkAt)) + "." : ""}</p></div></div>
${H ? html`<div class="sh-mon-row" data-sh-tone="${oldData ? "muted" : H.tone}"><i class="sh-shape" aria-hidden="true"></i><div><p class="sh-mon-t">자기 진단 · ${H.short}${oldData ? html` <span class="sh-mon-old">마지막으로 받은 값</span>` : ""}</p><p class="sh-mon-s">${H.problems.length ? "API 는 정상이지만 아래 수집기의 값이 늦어요." : H.detail + "."}${facts.length ? html`<br><span class="sh-mon-f">${facts.map((f, i) => html`${i ? " · " : ""}<span>${f}</span>`)}</span>` : ""}</p>${H.problems.length ? html`<ul class="sh-mon-p">${H.problems.slice(0, 5).map((c) => html`<li><b>${collectorName(c.name)}</b>${c.error ? "오류 — " + shortErr(c.error) : "마지막 수집 " + secs(c.age_s) + " 전" + (typeof c.limit_s === "number" ? " (한도 " + secs(c.limit_s) + ")" : "")}</li>`)}</ul>` : ""}</div></div>` : ""}
${href ? html`<a class="sh-pop-link" href="${href}">자기 진단 자세히 보기${ic("arrow")}</a>` : ""}`);
  }

  function update(snap, state) {
    if (!snap) return;
    S = snap;
    ST = state || { stale: false };
    const now = nowS();
    if (!ST.stale) lastOkAt = ST.fetchedAt || now;
    const gen = S.meta && S.meta.generated_at;
    if (gen !== lastGen) { lastGen = gen; genChangedAt = now; }
    const steps = [renderHost, renderStatus, renderAccess, updateLive];
    if (openPop === "alerts") steps.push(renderAlertsPop);
    for (const fn of steps) {
      try { fn(); } catch (e) { console.error("[shell] " + fn.name + " 실패:", e); }
    }
    if (!ST.stale) beat();
  }
  function onError() {
    if (!S) failedBeforeData = true;
    updateLive();
    if (openPop === "alerts") renderAlertsPop();
  }

  // 지연 감지: 보일 때만 2초마다(숨으면 멈춘다)
  let tickT = 0;
  function tick() {
    tickT = 0;
    if (doc.hidden) return;
    updateLive();
    tickT = setTimeout(tick, 2000);
  }
  doc.addEventListener("visibilitychange", () => {
    clearTimeout(tickT);
    tickT = 0;
    if (!doc.hidden) { graceUntil = nowS() + 5; tick(); }
  });
  tick();

  // ------------------------------------------------------------------ JARVIS.subscribe 감싸기(폴링은 한 번)
  const origSubscribe = P.subscribe;
  P.subscribe = function (cb, opts) {
    const o = Object.assign({}, opts || {});
    const pageErr = o.onError;
    o.onError = (e) => {
      try { onError(e); } catch (x) { console.error("[shell] 오류 처리 실패:", x); }
      if (pageErr) pageErr(e);
    };
    return origSubscribe.call(P, (snap, state) => {
      try { update(snap, state); } catch (x) { console.error("[shell] 헤더 갱신 실패:", x); }
      // 페이지 렌더 오류가 jarvis.js 로 새면 "가져오기 실패"로 잡혀 오프라인·재시도 지연이 된다 → 여기서 막는다.
      if (cb) { try { cb(snap, state); } catch (x) { console.error("[shell] 페이지 렌더 실패:", x); } }
    }, o);
  };

  // ================================================================== 제품 공용 도구
  // 두 페이지(개요·네트워크 맵)가 같은 말·같은 숫자·같은 판정을 쓰도록 한곳에 둔다. 페이지 스크립트보다 먼저 실행된다.
  const isNum = (v) => typeof v === "number" && isFinite(v);

  // ---- 숫자 표기: 공용 jarvis.js 는 그대로 두고 제품 페이지에서만 다듬는다(두 페이지가 같은 표기를 쓰게)
  //   바이트: 지금 단위로 1000 을 넘으면 다음 단위(1004 KB → 1.0 MB). 1ms 미만(핑 0.0 ms 등)은 "<1 ms".
  {
    const U = ["B", "KB", "MB", "GB", "TB"];
    fmt.bytes = function (n, d) {
      if (d == null) d = 1;
      if (n == null || isNaN(n)) return "–";
      let i = 0; let v = Math.abs(n);
      while (v >= 999.5 && i < U.length - 1) { v /= 1024; i++; }
      return (n < 0 ? "-" : "") + (i === 0 ? Math.round(v) : v.toFixed(v >= 100 ? 0 : d)) + " " + U[i];
    };
    const ms0 = fmt.ms;
    fmt.ms = (n) => (isNum(n) && n >= 0 && n < 0.5 ? "<1 ms" : ms0(n));
  }

  // ---- 이름: 영역·엣지(홉) 이름은 한 벌만
  const HOP_NAME = { "zone:internet": "인터넷", "zone:tailnet": "Tailnet", "zone:lan": "LAN" };
  const EDGE_NAME = "Cloudflare 엣지";
  /** 토폴로지 노드(또는 id) → 화면 이름 */
  function nodeName(n) {
    if (!n) return "";
    if (typeof n === "string") return HOP_NAME[n] || (/^edge:/.test(n) ? EDGE_NAME : n);
    return HOP_NAME[n.id] || (n.type === "edge" ? EDGE_NAME : n.label || n.id);
  }

  // ---- 실행 상태 라벨(컨테이너·systemd)
  const STATE_L = {
    running: "실행 중", exited: "종료됨", restarting: "재시작 중", paused: "일시정지", created: "생성됨", dead: "중단", removing: "제거 중",
    active: "활성", inactive: "비활성", failed: "실패", activating: "시작 중", deactivating: "멈추는 중", reloading: "다시 읽는 중",
    waiting: "대기", listening: "연결 대기", start: "시작 중", "auto-restart": "자동 재시작 대기",
    // pm2 앱 상태
    online: "실행 중", stopped: "멈춤", stopping: "멈추는 중", errored: "오류로 멈춤", launching: "시작 중",
    "one-launch-status": "1회 실행", "waiting restart": "재시작 대기",
  };
  const HEALTH_L = { healthy: "헬스 정상", unhealthy: "헬스 비정상", starting: "헬스 확인 중" };
  /** 런타임 상태 한 줄: "실행 중 · 헬스 정상", "활성 · 실행 중" */
  function runtimeState(rt) {
    if (!rt) return "";
    const st = rt.state ? STATE_L[rt.state] || rt.state : null;
    const sub = rt.sub && rt.sub !== rt.state ? STATE_L[rt.sub] || rt.sub : null;
    const hl = rt.health ? HEALTH_L[rt.health] || rt.health : null;
    return [st, sub, hl].filter(Boolean).join(" · ");
  }

  // ---- 공개 상태(가장 넓게 열린 범위) — 개요 타일·지도 요약·노출 목록이 같은 정의·같은 숫자를 쓴다
  //   label 은 좁은 칸, long 은 제목, reach 는 상세의 "어떻게 닿는가" 첫 문장
  const LEVELS = [
    { id: "public", label: "공개", long: "공개", sub: "인터넷 누구나", k: "public", reach: "인터넷의 누구나 닿아요." },
    { id: "lan", label: "LAN", long: "LAN", sub: "같은 공유기 안 누구나", k: "lan", reach: "같은 LAN의 기기라면 누구나 닿아요(tailnet 기기도)." },
    { id: "tailnet", label: "Tailnet", long: "Tailnet 전용", sub: "내 tailnet 기기만", k: "tailnet", reach: "내 tailnet 기기에서만 닿아요. 인터넷에서는 닿지 않아요." },
    { id: "local", label: "로컬", long: "로컬 전용", sub: "이 기기 안에서만", k: "local", reach: "밖에서는 닿지 않아요 — 이 기기 안에서만 쓰여요." },
  ];
  function expLevel(sv) {
    if (!sv) return "local";
    const e = sv.exposure || {};
    return e.public ? "public" : e.lan ? "lan" : e.tailnet ? "tailnet" : "local";
  }
  function expCounts(s) {
    const c = { public: 0, lan: 0, tailnet: 0, local: 0 };
    for (const sv of (s && s.services) || []) c[expLevel(sv)]++;
    return c;
  }

  // ---- 네트워크 맵의 두 관점(개념 · 물리) — 개요 타일과 지도가 같은 이름·부제·질문·아이콘·기억(한 벌)을 쓴다
  //   주소 규약: /map/?view=concept|physical (+ #focus=<서비스 id | zone:* | 물리 노드 id>). ?view=conceptual · phys 도 받는다.
  const MAP_MODES = {
    concept: { id: "concept", title: "개념", sub: "서비스 노출 경로", q: "무엇이 어떤 경로로 공개되는가",
      icon: '<circle cx="5.5" cy="6" r="2.5"/><circle cx="18.5" cy="18" r="2.5"/><path d="M8 6h7.5a3 3 0 0 1 0 6h-7a3 3 0 0 0 0 6H16"/>' },
    physical: { id: "physical", title: "물리", sub: "기기·네트워크", q: "어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가",
      icon: '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>' },
  };
  const MV_KEY = "map.view";
  const mapView = {
    /** 주소·저장값 → "concept" | "physical" | null */
    norm(v) {
      const x = String(v == null ? "" : v).replace(/"/g, "").trim().toLowerCase();
      return x === "physical" || x === "phys" ? "physical" : x === "concept" || x === "conceptual" ? "concept" : null;
    },
    /** 이 브라우저에 기억된 관점(지도·개요가 같은 값) */
    get(d) {
      if (window.JarvisLab) return mapView.norm(window.JarvisLab.getPref("mapView", d)) || d || "concept";
      let v = null;
      try { v = mapView.norm(P.pref(MV_KEY)) || mapView.norm(P.pref("home.mapView")); } catch (e) { v = null; }
      return v || d || "concept";
    },
    set(v) { const n = mapView.norm(v); if (!n) return; if (window.JarvisLab) { window.JarvisLab.setPref("mapView", n); return; } try { localStorage.setItem("jarvis." + MV_KEY, JSON.stringify(n)); } catch (e) { /* 저장소 없음 */ } },
    /** /map/?view=<v>#focus=<id> */
    href(v, focus) { const n = mapView.norm(v); return "/map/" + (n ? "?view=" + n : "") + (focus ? "#focus=" + encodeURIComponent(String(focus)) : ""); },
  };

  // ---- Tailnet 연결 종류(피어의 connection 기준 — peer.relay 는 그 기기의 홈 DERP 지역일 뿐 지금 중계 중이라는 뜻이 아니다)
  const CONN_L = { direct: "직결", relay: "중계", idle: "유휴", offline: "오프라인" };
  const peerConn = (p) => (!p || p.online === false ? "offline" : p.connection === "direct" ? "direct" : p.connection === "relay" ? "relay" : "idle");
  /** 온라인 피어의 연결 종류 수 → { direct, relay, idle, text: "직결 4 · 중계 0 · 유휴 5" } */
  function tailnetMix(s) {
    const c = { direct: 0, relay: 0, idle: 0 };
    for (const p of (s && s.tailscale && s.tailscale.peers) || []) { const k = peerConn(p); if (k !== "offline") c[k]++; }
    c.text = CONN_L.direct + " " + c.direct + " · " + CONN_L.relay + " " + c.relay + " · " + CONN_L.idle + " " + c.idle;
    return c;
  }

  // ---- 같은 LAN 기기(물리) — 한 가지 정의를 모든 화면(개요 타일·지도 요약 칸·열 제목·LAN 구간)이 쓴다:
  //   맨 위 기기(같은 기기 안의 노드는 빼고) 가운데 백엔드가 on_lan 으로 준 온라인 피어. via_gateway(LAN 주소가 공유기 주소 =
  //   NAT 너머)는 백엔드가 이미 on_lan 에서 뺀다(옛 백엔드면 게이트웨이 주소와 비교해 뺀다).
  //   on_lan 은 Tailscale 의 CurAddr(직결 중에만 있다)로 정해져 3초마다 흔들릴 수 있어 여기서만 한 번 누른다:
  //   새로 보인 피어는 두 스냅샷 연속이어야 넣고, 사라진 피어는 LAN_HOLD_S 동안 "잠시 안 보임"(held)으로 자리를 지킨다
  //   → 지도가 3초마다 다시 배치되지 않는다. 첫 스냅샷은 그대로 믿는다.
  //   count = 지금 LAN 에 보이는 맨 위 기기 수(잠시 안 보임은 빼고) · held = 잠시 안 보임 수.
  const LAN_HOLD_S = 120;
  const lanMem = new Map();   // 피어 id → { ip, seg, hits, last, ok }
  let lanInit = false; let lanGen = null; let lanOut = null;
  /** → { peers: Map(피어 id → { ip, seg, stale, last }), viaGw: Set(피어 id), gateway, count, held } */
  function lanPeers(s) {
    const tp = (s && s.topology_physical) || {};
    const gen = (s && s.meta && s.meta.generated_at) || null;
    if (lanOut && gen != null && gen === lanGen) return lanOut;
    const nodes = new Map();
    for (const n of tp.nodes || []) if (n && n.id) nodes.set(n.id, n);
    const seg = [...nodes.values()].find((n) => n.type === "segment" && /lan/i.test(n.id)) || null;
    const gw = seg && seg.gateway ? String(seg.gateway) : null;
    // 피어 → lan 엣지(구간 id · 라벨 IP)
    const lanE = new Map();
    for (const e of tp.edges || []) {
      if (!e || e.kind !== "lan") continue;
      const a = nodes.get(e.from); const b = nodes.get(e.to);
      const p = a && a.type === "peer" ? a : b && b.type === "peer" ? b : null;
      if (p) lanE.set(p.id, { label: e.label, seg: p === a ? e.to : e.from });
    }
    const isTop = (p) => { const q = p.parent ? nodes.get(p.parent) : null; return !q || q.type !== "peer"; };
    const seen = new Map(); const viaGw = new Set();
    for (const p of nodes.values()) {
      if (p.type !== "peer" || p.online === false) continue;
      const le = lanE.get(p.id);
      const ip = String(p.lan_ip || (le && le.label) || "");
      const vg = p.via_gateway != null ? !!p.via_gateway : !!(gw && ip && ip === gw);
      if (vg) { viaGw.add(p.id); continue; }
      const on = p.on_lan != null ? !!p.on_lan : !!le;
      if (!on || !isTop(p)) continue;
      seen.set(p.id, { ip, seg: le ? le.seg : seg ? seg.id : null });
    }
    const t = typeof gen === "number" ? gen : Date.now() / 1000;
    for (const [id, x] of seen) {
      const m = lanMem.get(id);
      if (!m) lanMem.set(id, { ip: x.ip, seg: x.seg, hits: 1, last: t, ok: !lanInit });
      else { m.hits = m.present ? m.hits + 1 : 1; m.ip = x.ip; m.seg = x.seg; m.last = t; if (m.hits >= 2) m.ok = true; }
      lanMem.get(id).present = true;
    }
    const peers = new Map(); let count = 0;
    for (const [id, m] of lanMem) {
      if (!seen.has(id)) {
        m.present = false; m.hits = 0;
        const p = nodes.get(id);
        if (!m.ok || !p || p.online === false || viaGw.has(id) || !isTop(p) || t - m.last > LAN_HOLD_S) { lanMem.delete(id); continue; }
      }
      if (!m.ok) continue;
      const stale = !seen.has(id);
      peers.set(id, { ip: m.ip, seg: m.seg, stale, last: m.last });
      if (!stale) count++;
    }
    lanInit = true; lanGen = gen;
    lanOut = { peers, viaGw, gateway: gw, count, held: peers.size - count };
    return lanOut;
  }

  // ---- 서비스 종류 이름(공용 labels.kind 에 없는 새 종류도 한국어로)
  const KIND_X = { static: "정적 응답" };
  const kindLabel = (k) => (k ? (labels.kind && labels.kind[k]) || KIND_X[k] || String(k) : "");
  /** Caddy 가 직접 응답하는 라우트(뒤에 앱·컨테이너가 없다 — runtime null, 상태는 프로브로만) */
  const isStatic = (sv) => !!sv && sv.kind === "static";

  // ---- 커밋 표기: monitor.commit 은 "git describe --dirty"(예 "3e14dda0ab-dirty", "v1.0-3-g3e14dda-dirty")
  /** → { hash: "3e14dda", dirty: bool, text: "3e14dda · 미커밋 수정", title } | null */
  function commitInfo(c) {
    if (!c) return null;
    const raw0 = String(c).trim();
    const dirty = /-dirty$/.test(raw0);
    const body = raw0.replace(/-dirty$/, "");
    const m = /(?:^|-g)([0-9a-f]{7,40})$/i.exec(body);
    const hash = (m ? m[1] : body).slice(0, 7);
    return { hash, dirty, text: hash + (dirty ? " · 미커밋 수정" : ""), title: raw0 + (dirty ? " — 커밋하지 않은 수정이 있는 상태로 돌고 있어요" : "") };
  }

  // ---- 연결 오류를 한국어 한 줄로(원문은 '자세히'에)
  function errKo(e) {
    const t = String(e || "");
    if (!t) return "";
    if (/refused|Errno 111/i.test(t)) return "연결 거부 — 포트가 열려 있지 않아요";
    if (/timed? ?out|timeout|Errno 110/i.test(t)) return "시간 초과 — 응답이 없어요";
    if (/Name or service not known|getaddrinfo|NXDOMAIN|nodename|Errno -2/i.test(t)) return "이름을 찾지 못해요(DNS)";
    if (/unreachable|No route|Errno 113|Errno 101/i.test(t)) return "닿을 수 없어요 — 가는 길이 없어요";
    if (/reset by peer|Errno 104|RemoteDisconnected|closed/i.test(t)) return "연결이 도중에 끊겼어요";
    if (/SSL|TLS|certificate/i.test(t)) return "TLS 오류 — 인증서를 확인하세요";
    return shortErr(t);
  }
  // ---- 경보 요약(기기 상태의 "왜"): 기기 상태는 경보 중 가장 높은 등급이라, 그 경보들을 짧게 말한다
  const DEV_STATUS = { up: "정상", ok: "정상", degraded: "주의", warn: "주의", down: "장애", crit: "장애", unknown: "확인 불가" };
  const BRIEF_SRC = { systemd: "실패한 유닛", container: "컨테이너", route: "라우트", monitor: "수집 지연", tunnel: "터널", caddy: "Caddy", dns: "DNS", cert: "인증서", tailscale: "Tailscale", hermes: "Hermes", system: "시스템", exposure: "노출", traffic: "트래픽" };
  /** → { list: 경고·심각 경보[], n, text: "실패한 유닛 1" | "경고 3" | "" } */
  function alertBrief(s) {
    const list = ((s && s.alerts) || []).filter((a) => a && (a.level === "crit" || a.level === "warn"));
    if (!list.length) return { list, n: 0, text: "" };
    const srcs = [...new Set(list.map((a) => a.source))];
    const text = srcs.length === 1 && BRIEF_SRC[srcs[0]] ? BRIEF_SRC[srcs[0]] + " " + list.length : (list.some((a) => a.level === "crit") ? "심각 " : "경고 ") + list.length;
    return { list, n: list.length, text };
  }

  // ---- 라우트 프로브 판정(칩·상세·검사기가 같은 말)
  //   internal = Tailnet 경로(Caddy :443) · tunnel = 터널 경로(Caddy :80) · public = 공개(Cloudflare 엣지)
  const PROBE = {
    internal: { chip: "Tailnet", name: "Tailnet 경로", where: "Caddy :443", who: "tailnet 사용자 경로" },
    tunnel: { chip: "터널", name: "터널 경로", where: "Caddy :80", who: "터널(공개) 경로" },
    public: { chip: "공개", name: "공개 경로", where: "공개 DNS → Cloudflare 엣지", who: "인터넷 사용자 경로" },
  };
  function shortErr(e) {
    if (!e) return "";
    const s = String(e);
    if (/timed? ?out|TimeoutError/i.test(s)) return "시간 초과";
    if (/refused/i.test(s)) return "연결 거부";
    if (/not known|getaddrinfo|NXDOMAIN|resolve/i.test(s)) return "이름 조회 실패";
    if (/unreachable/i.test(s)) return "도달 불가";
    if (/SSL|certificate|TLS/i.test(s)) return "TLS 오류";
    if (/reset|broken pipe/i.test(s)) return "연결 끊김";
    return s.replace(/^[A-Za-z]+Error:\s*/, "").slice(0, 60);
  }
  /** 프로브 한 칸 → { tone, chip, value, text, notes[], title, blocked } */
  function probeVerdict(p, key, sv) {
    if (!p) return null;
    const def = PROBE[key] || { chip: key, name: key, where: key, who: "엔드포인트" };
    const ex = (sv && sv.exposure) || {};
    // 비공개(tailnet 전용) 라우트는 터널 경로에서 Caddy 기본 404 가 나와야 정상 = 공개 차단 확인
    const blocked = key === "tunnel" && p.code === 404 && !ex.public && !!(p.caddy_fallback || (sv && sv.snippet === "private"));
    const ms = isNum(p.ms) ? fmt.ms(p.ms) : null;
    const err = shortErr(p.error || p.tls_error);
    const notes = [];
    let tone; let value; let text;
    if (blocked) {
      tone = "ok"; value = "공개 차단"; text = "404 — 공개 차단 확인";
      notes.push("Caddy 기본 응답이에요 — 비공개 라우트라 터널(공개) 경로에서 404 가 정상이에요.");
    } else if (p.ok) {
      tone = isNum(p.code) && p.code >= 400 ? "muted" : "ok";
      value = p.code != null ? String(p.code) : "응답";
      text = (p.code != null ? p.code + " · " : "") + (ms || "–");
      if (tone === "muted") notes.push("응답은 오지만 첫 화면이 " + p.code + " 이에요 — 앱이 / 경로를 쓰지 않으면 정상이에요.");
    } else if (p.fail_streak === 1) {
      tone = "warn"; value = "재확인 중";
      text = "재확인 중 — " + (p.code != null ? p.code + " · " : "") + (err || "실패");
      notes.push("한 번 실패했어요 — 다음 확인에서도 실패하면 경보가 떠요.");
    } else {
      tone = "crit"; value = p.code != null ? String(p.code) : "실패";
      text = (p.code != null ? p.code + " · " : "") + (err || "실패") + (p.fail_streak > 1 ? " · 연속 " + p.fail_streak + "회" : "");
      if (p.fail_streak > 1) notes.push("연속 " + p.fail_streak + "회 실패했어요.");
    }
    if (p.tls_error) notes.push("TLS: " + p.tls_error);
    if (p.error && !p.ok) notes.push(String(p.error).slice(0, 160));
    const title = def.where + " — " + def.who + ": " + text + (blocked ? " (코드 " + p.code + ")" : "") + (p.tls_error ? " · TLS: " + p.tls_error : "");
    return { key, tone, chip: def.chip, name: def.name, where: def.where, who: def.who, value, text, notes, title, blocked, retry: !p.ok && p.fail_streak === 1 };
  }

  // ---- 토폴로지 모형(지도·상세 시트의 "어떻게 닿는가"가 같은 길을 쓴다)
  //   snap.topology + 길이 그려지지 않은 서비스(type "extra")
  //   + LAN 직접 엣지(합성): LAN(모든 인터페이스·LAN 바인딩)에 포트를 연 서비스인데 LAN 길이 없으면 LAN → 그 서비스.
  //     합성 엣지는 terminal — 그 노드 자체의 포트로 닿는다는 뜻이라 그 너머(Caddy → 앱)로 이어지지 않는다.
  const SERVICE_LIKE = new Set(["service", "host", "internal", "extra"]);
  function graphOf(s) {
    if (!s) return null;
    if (s.__jarvisGraph) return s.__jarvisGraph;
    const svcById = new Map((s.services || []).map((x) => [x.id, x]));
    const topo = s.topology || {};
    const nodes = new Map();
    for (const n of topo.nodes || []) {
      if (!n || !n.id) continue;
      nodes.set(n.id, Object.assign({}, n, { svc: n.service_id ? svcById.get(n.service_id) || null : null }));
    }
    const svcNode = new Map();
    for (const n of nodes.values()) if (n.service_id) svcNode.set(n.service_id, n.id);
    for (const sv of s.services || []) {
      if (svcNode.has(sv.id)) continue;
      if (nodes.has(sv.id)) { svcNode.set(sv.id, sv.id); continue; }
      nodes.set(sv.id, { id: sv.id, label: sv.name, type: "extra", layer: 5, status: sv.status, category: sv.category, service_id: sv.id, svc: sv, synthetic: true });
      svcNode.set(sv.id, sv.id);
    }
    const seen = new Set();
    const edges = [];
    const add = (e) => {
      const key = e.from + "→" + e.to + "·" + e.kind;
      if (seen.has(key)) return;
      seen.add(key);
      edges.push(Object.assign({}, e, { key }));
    };
    for (const e of topo.edges || []) if (e && nodes.has(e.from) && nodes.has(e.to) && e.from !== e.to) add(e);
    if (nodes.has("zone:lan")) {
      const lanIn = new Set(edges.filter((e) => e.from === "zone:lan").map((e) => e.to));
      for (const sv of s.services || []) {
        const nid = svcNode.get(sv.id);
        if (!nid || lanIn.has(nid) || !(sv.exposure && sv.exposure.lan)) continue;
        const ports = [...new Set((sv.listeners || []).filter((l) => l.scope === "all" || l.scope === "lan").map((l) => ":" + l.port))];
        add({ from: "zone:lan", to: nid, kind: "lan", label: ports.slice(0, 2).join(" ") + (ports.length > 2 ? " +" + (ports.length - 2) : ""), synthetic: true, terminal: true });
      }
    }
    const inE = new Map(); const outE = new Map();
    for (const e of edges) {
      if (!inE.has(e.to)) inE.set(e.to, []);
      if (!outE.has(e.from)) outE.set(e.from, []);
      inE.get(e.to).push(e); outE.get(e.from).push(e);
    }
    const G = { s, svcById, nodes, edges, inE, outE, svcNode };
    try { Object.defineProperty(s, "__jarvisGraph", { value: G, enumerable: false, configurable: true }); } catch (e) { /* 얼린 객체: 캐시 없이 */ }
    return G;
  }
  const passes = (allowed, kind) => !allowed || allowed.has(kind);
  /** 노드가 어떤 종류의 길로 닿는지: 서비스는 노출 정보, 영역은 나가는 길, 관문(인프라)은 들어오는 길 */
  function kindsOf(G, id) {
    const n = G.nodes.get(id);
    if (!n) return null;
    if (n.type === "zone") { const k = (G.outE.get(id) || []).filter((e) => e.kind !== "depends").map((e) => e.kind); return k.length ? new Set(k) : null; }
    if (!SERVICE_LIKE.has(n.type)) { const k = (G.inE.get(id) || []).filter((e) => e.kind !== "depends").map((e) => e.kind); return k.length ? new Set(k) : null; }
    const ex = n.svc && n.svc.exposure;
    if (!ex) return null;
    const k = new Set();
    if (ex.public) k.add("public");
    if (ex.tailnet) k.add("tailnet");
    if (ex.lan) k.add("lan");
    return k;
  }
  /** 영역에서 노드까지의 길 목록. 각 길 = { nodes: [id…], edges: [e…] } */
  function chains(G, id, max) {
    max = max || 6;
    const out = [];
    const walk = (nid, allowed, acc, depth) => {
      if (out.length >= max) return;
      const ins = (G.inE.get(nid) || []).filter((e) => (e.kind === "depends" || passes(allowed, e.kind)) && (!e.terminal || nid === id));
      if (!ins.length || depth > 10) { if (acc.length) out.push(acc.slice().reverse()); return; }
      for (const e of ins) {
        if (acc.some((a) => a.id === e.from)) continue;
        acc.push({ id: e.from, edge: e });
        walk(e.from, e.kind === "depends" ? kindsOf(G, e.from) : allowed, acc, depth + 1);
        acc.pop();
      }
    };
    walk(id, kindsOf(G, id), [], 0);
    return out.map((c) => ({ nodes: c.map((x) => x.id).concat(id), edges: c.map((x) => x.edge) }));
  }
  /** 길의 종류 = 출발 영역에서 나가는 첫 간선(의존만 있으면 depends) */
  const chainKind = (c) => { for (const e of c.edges) if (e.kind !== "depends") return e.kind; return "depends"; };
  /** 간접 길: 마지막 간선이 의존(앱이 안에서 쓰는 백엔드) */
  const chainIndirect = (c) => c.edges.length > 0 && c.edges[c.edges.length - 1].kind === "depends";
  /** 엣지 라벨. 영역 → 서비스(직접 포트)에 라벨이 없으면 그 길로 열린 포트로 채운다 */
  function edgeLabel(G, e) {
    if (!e) return "";
    if (e.label) return e.label;
    const t = G.nodes.get(e.to); const f = G.nodes.get(e.from);
    if (t && t.svc && e.kind !== "depends" && f && f.type === "zone") {
      const ports = [...new Set((t.svc.listeners || []).filter((l) => l.scope !== "loopback" && l.scope !== "docker" &&
        (e.kind !== "tailnet" || l.reach == null || l.reach.includes("tailnet")) && (e.kind !== "lan" || l.reach == null || l.reach.includes("lan"))).map((l) => ":" + l.port))];
      return ports.slice(0, 3).join(" ");
    }
    return "";
  }
  const KIND_TITLE = { public: "공개 경로 — 인터넷에서", tailnet: "Tailnet 경로 — 내 기기에서", lan: "LAN 직접 — 같은 공유기에서", depends: "내부 의존" };

  // ---- 제자리 갱신(morph): 새 HTML 과 구조가 같으면 바뀐 글자·속성만 고친다.
  //   3초 갱신이 숫자 하나 때문에 목록 전체를 갈아 끼우지 않게 → 글자 선택·포커스·hover·누르던 손가락이 유지된다.
  //   누르고 있거나 글자를 고르는 중이면 이번 갱신은 건너뛴다(다음 갱신에 반영).
  let ptrIn = null;
  doc.addEventListener("pointerdown", (e) => { ptrIn = e.target; }, true);
  const ptrOff = () => { ptrIn = null; };
  doc.addEventListener("pointerup", ptrOff, true);
  doc.addEventListener("pointercancel", ptrOff, true);
  const toStr = (v) => (v == null || v === false ? "" : v instanceof P.Raw ? String(v) : Array.isArray(v) ? v.map(toStr).join("") : P.esc(v));
  function busy(el) {
    if (ptrIn && el.contains(ptrIn)) return true;
    const sel = window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed && sel.rangeCount) {
      const r = sel.getRangeAt(0);
      if (el.contains(r.commonAncestorContainer) || r.intersectsNode(el)) return true;
    }
    return false;
  }
  function patchAttrs(a, b) {
    const ba = b.attributes;
    for (let i = 0; i < ba.length; i++) { const at = ba[i]; if (a.getAttribute(at.name) !== at.value) a.setAttribute(at.name, at.value); }
    const aa = a.attributes;
    for (let i = aa.length - 1; i >= 0; i--) {
      const nm = aa[i].name;
      if (!b.hasAttribute(nm) && !(nm === "open" && a.tagName === "DETAILS")) a.removeAttribute(nm);
    }
  }
  function patchKids(dst, src) {
    let a = dst.firstChild; let b = src.firstChild;
    while (b) {
      const nb = b.nextSibling;
      if (!a) { dst.appendChild(b); b = nb; continue; }
      if (a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName)) {
        if (a.nodeType === 1) { patchAttrs(a, b); patchKids(a, b); }
        else if (a.nodeValue !== b.nodeValue) a.nodeValue = b.nodeValue;
        a = a.nextSibling;
      } else {
        const na = a.nextSibling;
        dst.replaceChild(b, a);
        a = na;
      }
      b = nb;
    }
    while (a) { const na = a.nextSibling; dst.removeChild(a); a = na; }
  }
  /** P.render 와 같은 약속(같은 문자열이면 아무것도 안 함)에 제자리 갱신을 더한 것. 바꿨으면 true. */
  function morph(el, content, opts) {
    if (!el) return false;
    const s = toStr(content);
    if (el.__jarvisHtml === s) return false;
    if (el.__jarvisHtml != null && !(opts && opts.force) && busy(el)) return false;
    const tpl = doc.createElement("template");
    tpl.innerHTML = s;
    if (!el.firstChild) el.appendChild(tpl.content);
    else patchKids(el, tpl.content);
    el.__jarvisHtml = s;
    return true;
  }

  // ---- 하드웨어 계기(개요 첫 화면의 줄 · 지도 아래 도크가 같은 부품)
  //   JarvisShell.hw.render(rowEl, snap, { base: "" | "/", more: "링크 설명" })
  //   rowEl 안에 계기 8칸(a.hwd-t)을 한 번 만들고 값만 갱신한다. 각 칸은 개요의 해당 하드웨어 타일(#hw-…)로 간다.
  const HW = [
    ["cpu", "CPU", "cpu", "blue", "hw-cpu"], ["gpu", "GPU", "gpu", "violet", "hw-gpu"], ["mem", "메모리", "mem", "green", "hw-mem"],
    ["temp", "온도", "temp", "orange", "hw-therm"], ["power", "전력", "bolt", "red", "hw-therm"], ["net", "네트워크", "net", "teal", "hw-net"],
    ["disk", "저장소", "disk", "gray", "hw-disk"], ["up", "가동", "clock", "dark", "hardware"],
  ];
  function hwSpark(values, o) {
    o = o || {};
    const d = P.sparkPath(values, 100, 28, o);
    if (!d) return html`<svg class="hwd-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true" focusable="false"></svg>`;
    const area = P.sparkPath(values, 100, 28, Object.assign({}, o, { area: true }));
    const d2 = o.second ? P.sparkPath(o.second, 100, 28, o) : "";
    return html`<svg class="hwd-spark" viewBox="0 0 100 28" preserveAspectRatio="none" aria-hidden="true" focusable="false"><path class="sp-a" d="${area}"/><path class="sp-l" d="${d}"/>${d2 ? html`<path class="sp-l2" d="${d2}"/>` : ""}</svg>`;
  }
  const maxOf = (...arrs) => Math.max(1, ...arrs.flat().filter(isNum));
  const valTone = (v, w, c) => { const t = P.loadTone(v, w, c); return t === "ok" || t === "muted" ? "none" : t; };
  const V = (v, u) => html`${v}<small>${u}</small>`;
  /** "3.3 / 7.9 GB" — 단위가 같으면 한 번만 */
  const memPair = (a, b) => {
    const x = fmt.bytes(a); const y = fmt.bytes(b);
    const ux = x.split(" ")[1]; const uy = y.split(" ")[1];
    return ux && ux === uy ? x.split(" ")[0] + " / " + y : x + " / " + y;
  };
  function hwRender(row, s, opts) {
    if (!row || !s) return;
    opts = opts || {};
    const base = opts.base || "";
    if (!row.__hwd) {
      row.textContent = "";
      row.__hwd = {};
      for (const [k, , , , anchor] of HW) {
        const a = doc.createElement("a");
        a.className = "hwd-t";
        a.setAttribute("data-metric", k);
        a.setAttribute("href", base + "#" + anchor);
        row.appendChild(a);
        row.__hwd[k] = a;
      }
    }
    const sy = s.system || {}; const sp = s.spark || {};
    const cpu = sy.cpu || {}; const gpu = sy.gpu || {}; const mem = sy.memory || {}; const th = sy.thermal || {};
    const pw = sy.power || {}; const nw = sy.network || {}; const clk = sy.clocks_mhz || {};
    const tile = (k, o) => {
      const el = row.__hwd[k];
      const def = HW.find((x) => x[0] === k);
      morph(el, html`<span class="hwd-head"><span class="hwd-ib" data-ib="${def[3]}">${ic(def[2])}</span><span class="hwd-label">${def[1]}</span><span class="hwd-aux" data-lo="${o.lo ? String(o.lo) : "0"}">${o.aux || ""}</span></span>
<span class="hwd-body"><span class="hwd-val" data-tone="${o.tone || "none"}">${o.val}</span>${o.extra || ""}${o.spark || ""}</span>
<span class="hwd-foot">${o.foot || ""}</span>`, { force: true });
      const aux = el.querySelector(".hwd-aux"); const foot = el.querySelector(".hwd-foot");
      const tip = [def[1], aux ? aux.textContent : "", foot ? foot.textContent : ""].filter(Boolean).join(" · ");
      setAttr(el, "title", tip);
      setAttr(el, "aria-label", def[1] + " " + o.sr + (opts.more ? " — " + opts.more : ""));
    };
    const cores = cpu.per_core_pct || [];
    tile("cpu", {
      aux: fmt.mhz(cpu.freq_mhz), tone: valTone(cpu.usage_pct, 80, 95),
      sr: fmt.pct(cpu.usage_pct) + ", 부하 " + (cpu.load || []).map((v) => fmt.num(v, 2)).join(" "),
      val: V(fmt.num(cpu.usage_pct), "%"),
      extra: html`<span class="hwd-cores" aria-hidden="true">${cores.map((c) => html`<i data-tone="${P.loadTone(c, 80, 95)}"><b style="${"height:" + Math.min(100, Math.max(4, isNum(c) ? c : 0)).toFixed(0) + "%"}"></b></i>`)}</span>`,
      spark: hwSpark(sp.cpu, { min: 0, max: 100 }),
      foot: html`부하 <b>${(cpu.load || []).map((v) => fmt.num(v, 2)).join(" · ") || "–"}</b>${isNum(cpu.temp_c) ? html` · <b>${fmt.temp(cpu.temp_c)}</b>` : ""}`,
    });
    // 이 기기에 없는 계기(Pi 가 아닌 기기의 V3D·PMIC·팬·보드 전력 등)는 칸째 숨긴다
    const showT = (k, on) => { const el = row.__hwd[k]; if (el.hidden === on) el.hidden = !on; };
    const q = gpu.queues || {};
    const v3d = Object.keys(q).length > 0;
    showT("gpu", gpu.available !== false || isNum(gpu.usage_pct));
    tile("gpu", {
      aux: (v3d ? "V3D " : "") + fmt.mhz(isNum(gpu.freq_mhz) ? gpu.freq_mhz : isNum(clk.v3d) ? clk.v3d : clk.gpu), lo: 2, tone: gpu.available === false ? "none" : valTone(gpu.usage_pct, 80, 95),
      sr: gpu.available === false ? "사용률 없음" : fmt.pct(gpu.usage_pct),
      val: gpu.available === false ? V("–", "") : V(fmt.num(gpu.usage_pct), "%"),
      spark: hwSpark(sp.gpu, { min: 0, max: 100 }),
      foot: v3d ? html`렌더 <b>${fmt.pct(q.render)}</b> · 비닝 <b>${fmt.pct(q.bin)}</b><span data-lo="2"> · TFU <b>${fmt.pct(q.tfu)}</b></span>` : "",
    });
    const swapIn = isNum(mem.swap_in_ps) && mem.swap_in_ps > 0;
    tile("mem", {
      aux: memPair(mem.used, mem.total), tone: valTone(mem.usage_pct, 85, 95),
      sr: fmt.pct(mem.usage_pct) + ", 스왑 " + fmt.pct(mem.swap_pct) + (swapIn ? ", 스왑 인 " + fmt.num(mem.swap_in_ps, 1) + "/초" : ""),
      val: V(fmt.num(mem.usage_pct), "%"),
      spark: hwSpark(sp.mem, { min: 0, max: 100 }),
      foot: html`캐시 <b>${fmt.bytes(mem.cached)}</b> · 스왑 <b>${fmt.pct(mem.swap_pct)}</b>${swapIn ? html` · 스왑 인 <b class="hwd-warn">${fmt.num(mem.swap_in_ps, 1)}/s</b>` : ""}`,
    });
    const fan = th.fan || {};
    const tC = isNum(th.cpu_temp_c) ? th.cpu_temp_c : cpu.temp_c;
    showT("temp", isNum(tC) || !!th.fan);
    tile("temp", {
      aux: isNum(th.pmic_temp_c) ? "PMIC " + fmt.temp(th.pmic_temp_c) : "", lo: true, tone: valTone(tC, 70, 80),
      sr: fmt.temp(tC) + (th.fan ? ", 팬 " + fmt.num(fan.rpm) + " rpm" : ""),
      val: V(isNum(tC) ? tC.toFixed(1) : "–", "°C"),
      spark: hwSpark(sp.temp, { min: 30, max: 90 }),
      foot: th.fan ? html`팬 <b>${fmt.num(fan.rpm)}</b> rpm${isNum(fan.pct) ? html` · <b>${fmt.pct(fan.pct)}</b>` : ""}` : "",
    });
    const thr = th.throttle || {};
    const vin = pw.input_v;
    const thrNow = thr.ok === false || (thr.now || []).length > 0;
    const thrKnown = thr.ok === true || thr.raw === "0x0";
    const core = isNum(pw.core_volts) ? pw.core_volts.toFixed(3) + " V" : "–";
    showT("power", isNum(pw.total_w) || !!th.throttle);
    tile("power", {
      aux: isNum(vin) ? "입력 " + vin.toFixed(2) + " V" : "",
      tone: thrNow || (isNum(vin) && vin < 4.8) ? "crit" : isNum(vin) && vin < 4.95 ? "warn" : "none",
      sr: fmt.watts(pw.total_w) + (thrNow ? ", 스로틀 중" : thrKnown ? ", 스로틀 없음" : ", 스로틀 정보 없음"),
      val: V(isNum(pw.total_w) ? pw.total_w.toFixed(2) : "–", "W"),
      spark: hwSpark(sp.pw, { min: 0 }),
      foot: thrNow ? html`<span class="hwd-crit">스로틀 ${(thr.now || []).join(", ") || thr.raw}</span>`
        : (thr.since_boot || []).length ? html`<span class="hwd-warn">부팅 후 ${(thr.since_boot || []).join(", ")}</span>`
        : th.throttle ? html`코어 <b>${core}</b> · ${thrKnown ? "스로틀 없음" : "스로틀 정보 없음"}` : "",
    });
    tile("net", {
      aux: "LAN", sr: "받기 " + fmt.bps(nw.rx_bps) + ", 보내기 " + fmt.bps(nw.tx_bps),
      val: html`<span class="hwd-rxtx"><span><i aria-hidden="true">↓</i>${fmt.bps(nw.rx_bps)}</span><span><i aria-hidden="true">↑</i>${fmt.bps(nw.tx_bps)}</span></span>`,
      spark: hwSpark(sp.rx, { min: 0, max: maxOf(sp.rx || [], sp.tx || []), second: sp.tx }),
      foot: html`Tailnet ↓<b>${fmt.bps(nw.tailnet_rx_bps)}</b> ↑<b>${fmt.bps(nw.tailnet_tx_bps)}</b>`,
    });
    const disks = sy.disks || [];
    const root = disks.find((d) => d.mount === "/") || disks[0] || {};
    const io = sy.disk_io || {};
    tile("disk", {
      aux: fmt.bytes(root.free, 0) + " 남음", tone: valTone(root.usage_pct, 80, 92), sr: fmt.pct(root.usage_pct) + " 사용",
      val: V(fmt.num(root.usage_pct), "%"),
      spark: hwSpark(sp.dw, { min: 0, max: maxOf(sp.dw || [], sp.dr || []), second: sp.dr }),
      foot: html`읽기 <b>${fmt.bps(io.read_bps)}</b> · 쓰기 <b>${fmt.bps(io.write_bps)}</b>`,
    });
    const up = sy.uptime_s;
    const days = isNum(up) ? Math.floor(up / 86400) : null;
    tile("up", {
      aux: sy.boot_time ? "부팅 " + fmt.date(sy.boot_time) : "", lo: true, sr: fmt.dur(up, 2),
      val: !isNum(up) ? V("–", "") : days > 0 ? html`${days}<small>일</small><span class="hwd-more"> ${Math.floor((up % 86400) / 3600)}<small>시간</small></span>` : V(fmt.durShort(up), ""),
      foot: html`프로세스 <b>${fmt.num(sy.processes && sy.processes.count)}</b><span data-lo="2"> · 스레드 <b>${fmt.num(cpu.threads_total)}</b></span>`,
    });
  }

  const api = window.JarvisShell = {
    el,
    page: PAGE,
    update,
    live: () => liveState,
    /** 헤더에 보이는 자세한 상태: live 에 더해 "partial"(일부 수집 지연) · "frozen"(수집 멈춤) */
    view: () => viewState,
    theme: () => ({ pref, effective: effective() }),
    setTheme: (p) => applyTheme(p, true),
    on(type, fn) {
      if (!subs[type] || typeof fn !== "function") return () => {};
      subs[type].add(fn);
      return () => subs[type].delete(fn);
    },
    /** 페이지가 정하는 경보 바로가기: (alert) => { href, label } | null. 헤더 경보 목록 각 줄에 링크로 붙는다. */
    alertLink: null,
    // 공용 도구
    nodeName, runtimeState, STATE_L, HEALTH_L,
    LEVELS, expLevel, expCounts,
    MAP_MODES, mapView,
    CONN_L, peerConn, tailnetMix, lanPeers,
    kindLabel, isStatic, commitInfo,
    DEV_STATUS, alertBrief, errKo,
    PROBE, probeVerdict, shortErr,
    SERVICE_LIKE, graphOf, kindsOf, chains, chainKind, chainIndirect, edgeLabel, KIND_TITLE,
    morph,
    hw: { render: hwRender },
    /** 모니터 자기 진단: of(snap) → snap.monitor · svc(snap) → 모니터 서비스 · health(snap) → 요약 판정 · href(snap) → 개요 상세 링크 */
    monitor: { of: monitorOf, svc: monitorSvc, health: monitorHealth, href: monitorHref, collectorName, COLLECTOR_L, LOOP_L, LOOP_ORDER, secs },
  };
})();
