/* Jarvis · 관제실 (Control Room)
 * 규칙: 데이터는 전부 JARVIS.html(자동 이스케이프) 또는 textContent 로만 DOM 에 들어간다.
 *       URL 은 JARVIS.safeUrl 만 통과. 상호작용 요소(타일 버튼·탭·필터)는 한 번 만들고 값만 갱신한다.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  const { html, raw, fmt, labels } = P;
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  root.classList.remove("no-js");
  if (P.embed) root.classList.add("embed");
  const mqReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const syncMotion = () => root.classList.toggle("reduce", mqReduce.matches);
  syncMotion();
  mqReduce.addEventListener("change", () => { syncMotion(); tickerDirty = true; layoutTicker(); });
  const animOK = () => !mqReduce.matches && !P.embed;

  // ────────────────────────────────────────────── 환경설정(뷰어 편의용)
  const store = {
    get(k, d) { try { const v = P.pref("control." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("jarvis.control." + k, JSON.stringify(v)); } catch (e) { /* 무시 */ } },
  };
  const S = {
    snap: null, st: null,
    q: "",
    issues: store.get("issues", false) === true,
    trange: store.get("trange", "1h") === "24h" ? "24h" : "1h",
    tab: ["recent", "hosts", "errors"].includes(store.get("tab", "recent")) ? store.get("tab", "recent") : "recent",
    ports: store.get("ports", "all"),
    units: store.get("units", "all") === "failed" ? "failed" : "all",
    hrange: store.get("hrange", "1h") === "24h" ? "24h" : "1h",
    alertsOpen: false,          // 벽 모드에서는 겹쳐 뜨는 목록이라 저장하지 않는다
    sel: null,
    more: { peers: false, tabs: false, ports: false, units: false },   // 모바일 "더 보기"
    hist: null, histAt: 0, histRange: null, histBusy: false, histVisible: false,
    lastOk: 0,                  // 마지막으로 성공한 수신(클라이언트 시각)
    skew: null,                 // 클라이언트 시계 − 서버 시계 (초, 평활)
  };

  // ────────────────────────────────────────────── 작은 도우미
  /** JARVIS.render + 포커스 복원(data-k). 바뀌었으면 true */
  function put(el, content) {
    if (!el) return false;
    const before = el.__jarvisHtml;
    const ae = document.activeElement;
    const key = ae && ae !== el && el.contains(ae) ? ae.getAttribute("data-k") : null;
    P.render(el, content);
    const changed = el.__jarvisHtml !== before;
    if (changed && key) {
      for (const n of el.querySelectorAll("[data-k]")) if (n.getAttribute("data-k") === key) { n.focus({ preventScroll: true }); break; }
    }
    return changed;
  }
  const setAttr = (el, k, v) => { if (el.getAttribute(k) !== v) el.setAttribute(k, v); };
  const setText = (el, v) => { if (el && el.textContent !== v) el.textContent = v; };
  const setData = (el, k, v) => { if (el && el.dataset[k] !== v) el.dataset[k] = v; };
  const tn = (s) => P.tone(s);
  const led = (tone) => html`<i class="led led-${tone}" aria-hidden="true"></i>`;
  const stLabel = (s) => labels.status[s] || s || "–";
  const now = () => Date.now() / 1000;
  /** 서버 시계 기준 현재 시각 — 보는 기기의 시계가 틀려도 "N초 전"·수집기 나이·추세 창이 맞게 */
  const serverNow = () => now() - (S.skew || 0);
  const ago = (ts) => fmt.ago(ts, serverNow());
  const uniq = (a) => [...new Set(a)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const shortHost = (h) => {
    const d = S.snap && S.snap.meta && S.snap.meta.domain;
    return d && h && h.endsWith("." + d) ? h.slice(0, -(d.length + 1)) : h;
  };
  /** 이름을 구두점(- . _ @ /) 뒤에서만 줄바꿈되게 <wbr> 을 끼운다. 조각은 이스케이프된다.
   *  (정규식 lookbehind 는 구형 Safari 에서 구문 오류라 쓰지 않는다) */
  const wb = (str) => raw(String(str == null ? "" : str).replace(/([-._@/])/g, "$1\u0000").split("\u0000").map((x) => P.esc(x)).join("<wbr>"));
  /** 좁은 칸용 바이트: "69G", "512M" */
  const cbytes = (n) => (n == null ? "–" : fmt.bytes(n, 0).replace(/ ([KMGT])B$/, "$1").replace(/ B$/, "B"));
  const codeClass = (c) => (c == null ? "" : "c" + String(c)[0]);
  const LEVEL_TONE = { crit: "crit", warn: "warn", info: "info" };
  const SRC_L = { system: "시스템", container: "컨테이너", systemd: "systemd", route: "라우트", exposure: "노출", dns: "DNS", cert: "인증서", tunnel: "터널", caddy: "Caddy", traffic: "트래픽", tailscale: "Tailscale", hermes: "Hermes", monitor: "모니터" };
  const srcLabel = (s) => SRC_L[s] || s || "";
  const mqMobile = window.matchMedia("(max-width: 899px)");

  const IC = {
    public: raw('<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.7"/><path d="M1.3 6h9.4M6 1.3c1.7 1.5 1.7 7.9 0 9.4M6 1.3c-1.7 1.5-1.7 7.9 0 9.4"/></svg>'),
    tailnet: raw('<svg viewBox="0 0 12 12"><g fill="currentColor" stroke="none"><circle cx="2.2" cy="2.2" r="1.25"/><circle cx="6" cy="2.2" r="1.25"/><circle cx="9.8" cy="2.2" r="1.25"/><circle cx="2.2" cy="6" r="1.25"/><circle cx="6" cy="6" r="1.25"/><circle cx="9.8" cy="6" r="1.25"/><circle cx="6" cy="9.8" r="1.25"/></g></svg>'),
    lan: raw('<svg viewBox="0 0 12 12"><path d="M1.6 2.4h8.8v5.4H8.4v1.8H3.6V7.8h-2z"/><path d="M4 4.4v1.2M6 4.4v1.2M8 4.4v1.2"/></svg>'),
    local: raw('<svg viewBox="0 0 12 12"><rect x="2.3" y="5.3" width="7.4" height="5.1" rx="1"/><path d="M4 5.3V3.9a2 2 0 0 1 4 0v1.4"/></svg>'),
    ext: raw('<svg viewBox="0 0 14 14"><path d="M5.5 2.5h-3v9h9v-3M8 2.5h3.5V6M11.5 2.5 6.2 7.8"/></svg>'),
  };
  const XG_LABEL = { public: "공개", tailnet: "Tailnet", lan: "LAN", local: "로컬 전용" };
  function glyphs(s) {
    const e = s.exposure || {};
    const ks = [];
    if (e.public) ks.push("public");
    if (e.tailnet) ks.push("tailnet");
    if (e.lan) ks.push("lan");
    if (e.local_only || !ks.length) ks.push("local");
    return html`<span class="xgs">${ks.map((k) => html`<span class="xg xg-${k}" title="${XG_LABEL[k]}">${IC[k]}<span class="sr-only">${XG_LABEL[k]}</span></span>`)}</span>`;
  }
  function expChips(s) {
    const e = s.exposure || {};
    const out = [];
    if (e.public) out.push(html`<span class="chip chip-public">${IC.public ? html`<span class="xg xg-public" aria-hidden="true">${IC.public}</span>` : ""}공개</span>`);
    if (e.tailnet) out.push(html`<span class="chip chip-tailnet"><span class="xg xg-tailnet" aria-hidden="true">${IC.tailnet}</span>Tailnet</span>`);
    if (e.lan) out.push(html`<span class="chip chip-lan"><span class="xg xg-lan" aria-hidden="true">${IC.lan}</span>LAN</span>`);
    if (e.local_only || !out.length) out.push(html`<span class="chip chip-local"><span class="xg xg-local" aria-hidden="true">${IC.local}</span>로컬 전용</span>`);
    return out;
  }
  // 범례 글리프 채우기
  for (const el of document.querySelectorAll("[data-glyph]")) el.innerHTML = IC[el.dataset.glyph].s;

  // ────────────────────────────────────────────── 헤더
  function renderAccess(snap) {
    const acc = (snap.meta && snap.meta.access) || [];
    const item = (a, i, p) => html`<li><a data-k="${p}${i}" href="${P.safeUrl(a.url)}" target="_blank" rel="noopener noreferrer"><span class="aud aud-${a.audience === "lan" ? "lan" : a.audience === "public" ? "public" : "tailnet"}">${labels.audience[a.audience] || a.audience}</span>${String(a.url || "").replace(/^https?:\/\//, "").replace(/\/$/, "")}</a></li>`;
    put($("access"), acc.map((a, i) => item(a, i, "acc")));
    put($("access-m"), acc.map((a, i) => item(a, i, "accm")));
  }
  function renderHost(snap) {
    const h = (snap.system && snap.system.host) || {};
    put($("hostblk"), html`<span class="host-name">${h.hostname || (snap.meta && snap.meta.host) || "–"}</span><span class="host-model">${h.model || "–"}</span><span class="host-up">가동 ${fmt.dur(snap.system && snap.system.uptime_s)}</span>`);
  }
  function svcSummary(sv) {
    return `서비스 ${fmt.num(sv.up)}/${fmt.num(sv.total)} 정상${sv.degraded ? " · 저하 " + sv.degraded : ""}${sv.down ? " · 다운 " + sv.down : ""}`;
  }
  function renderOverall(snap) {
    const sm = snap.summary || {};
    const stale = !!(S.st && S.st.stale);
    const tone = sm.status ? tn(sm.status) : "muted";
    const el = $("overall");
    setData(el, "tone", stale ? "stale" : tone);
    const a = sm.alerts || {};
    const sv = sm.services || {};
    const nAlerts = (a.crit || 0) + (a.warn || 0) + (a.info || 0);
    // 전체 상태를 정한 경보(가장 높은 심각·경고)를 부제에 바로 적는다 — "왜 주황인가"가 한눈에
    const top = (snap.alerts || []).find((x) => x.level === "crit" || x.level === "warn");
    const gen = snap.meta && snap.meta.generated_at;
    let label = labels.overall[sm.status] || "확인 불가";
    let why; let cnt;
    if (stale) { label = "연결 끊김"; why = `마지막 상태: ${labels.overall[sm.status] || "–"} · ${fmt.time(gen)} 기준`; cnt = ""; }
    else if (top) { why = top.message; cnt = `경보 ${fmt.num(nAlerts)} · ${svcSummary(sv)}`; }
    else { why = svcSummary(sv); cnt = `경보 ${fmt.num(nAlerts)}`; }
    put(el, html`${led(stale ? tone : tone)}<div class="overall-txt"><b class="overall-label">${label}</b><span class="overall-sub"><span class="why" title="${why}">${why}</span>${cnt ? html`<span class="cnt">${cnt}</span>` : ""}</span></div>`);
    const c = (k, shape, label) => {
      const n = a[k] || 0;
      return html`<div class="ctr ${n ? "on-" + k : "zero"}"><b>${n}</b><span>${led(shape)}${label}</span></div>`;
    };
    put($("counters"), [c("crit", "crit", "심각"), c("warn", "warn", "경고"), c("info", "info", "정보")]);
  }
  /** #fresh 는 고정 마크업 — 값만 바꾼다(LIVE 점 애니메이션이 매초 새로 시작되지 않게) */
  function renderFresh() {
    const snap = S.snap; const st = S.st;
    if (!snap) return;
    const gen = snap.meta && snap.meta.generated_at;
    const stale = !!(st && st.stale);
    const since = S.lastOk ? now() - S.lastOk : null;   // 클라이언트 시계끼리만 비교
    let cls = "ok"; let txt = "LIVE";
    if (stale) { cls = "crit"; txt = "오프라인"; }
    else if (since != null && since > 15) { cls = "warn"; txt = "지연"; }
    const errs = Object.keys((snap.meta && snap.meta.errors) || {}).length;
    const ee = $("fresh-err");
    if (ee.hidden !== !errs) ee.hidden = !errs;
    if (errs) setText(ee, `수집 오류 ${errs}`);
    setText($("fresh-time"), fmt.time(S.lastOk || gen));
    setText($("fresh-ago"), ago(gen));
    const live = $("live"); const lc = "live live-" + cls;
    if (live.className !== lc) live.className = lc;
    setText($("live-l"), txt);
    root.classList.toggle("is-stale", stale);
    const note = $("stale-note");
    if (stale) {
      if (note.hidden) note.hidden = false;
      put(note, html`${led("crit")}<span class="t">API 연결 끊김 — 표시 값은 ${fmt.time(gen)} 기준 · 자동으로 다시 연결합니다 <span class="muted">(${st.error || "오류"})</span></span>`);
    } else if (!note.hidden) note.hidden = true;
  }
  function tickClock() {
    const d = new Date();
    setText($("clock"), d.toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    renderFresh();
  }

  // ────────────────────────────────────────────── 경보
  // 심각·경고는 띠 앞쪽에 고정(최대 2건 + "+N"), 정보만 흐른다.
  let tickerDirty = true;
  const lvlTone = (a) => LEVEL_TONE[a.level] || "muted";
  const sinceTxt = (ts) => (ts ? fmt.dur(Math.max(0, serverNow() - ts), 1) + "째" : "");
  function renderAlerts(snap) {
    const alerts = snap.alerts || [];
    const stale = !!(S.st && S.st.stale);
    const sec = $("alerts");
    setData(sec, "tone", alerts.length ? tn((snap.summary || {}).status) : "ok");
    setText($("alerts-n"), String(alerts.length));
    const btn = $("alerts-toggle");
    if (btn.hidden !== !alerts.length) btn.hidden = !alerts.length;
    if (!alerts.length && S.alertsOpen) S.alertsOpen = false;
    const hi = alerts.filter((a) => a.level === "crit" || a.level === "warn");
    const lo = alerts.filter((a) => a.level !== "crit" && a.level !== "warn");
    const pinned = $("alerts-pinned");
    const showPinned = !stale && !S.alertsOpen && hi.length > 0;
    if (pinned.hidden !== !showPinned) pinned.hidden = !showPinned;
    if (showPinned) {
      put(pinned, html`${hi.slice(0, 2).map((a) => html`<span class="pin" title="${a.message}">${led(lvlTone(a))}<span class="lvl t-${lvlTone(a)}">${labels.level[a.level] || a.level}</span><span class="src">${srcLabel(a.source)}</span><span class="msg">${a.message}</span></span>`)}${hi.length > 2 ? html`<span class="pin-more" title="심각·경고 ${hi.length}건 — 전체 보기에서 확인">+${hi.length - 2}</span>` : ""}`);
    }
    const tk = $("ticker");
    if (tk.hidden !== stale) tk.hidden = stale;
    const item = (a) => html`<span class="tick">${led(lvlTone(a))}<span class="lvl t-${lvlTone(a)}">${labels.level[a.level] || a.level}</span><span class="src">${srcLabel(a.source)}</span><span>${a.message}</span></span>`;
    let track;
    if (!alerts.length) {
      track = html`<span class="tick-calm">${led("ok")}현재 경보 없음 — 모든 점검 항목이 정상입니다.</span>`;
    } else if (S.alertsOpen) {
      const sm = (snap.summary || {}).alerts || {};
      track = html`<span class="tick-calm">심각 <b class="num">${sm.crit || 0}</b> · 경고 <b class="num">${sm.warn || 0}</b> · 정보 <b class="num">${sm.info || 0}</b> — 목록 참고</span>`;
    } else if (!lo.length) {
      track = "";
    } else {
      const items = lo.map(item);
      track = html`<span class="tick-list" role="list">${items.map((x) => html`<span role="listitem">${x}</span>`)}</span><span class="tick-list" aria-hidden="true">${items}</span>`;
    }
    if (put($("ticker-track"), track)) tickerDirty = true;
    layoutTicker();
    const list = $("alert-list");
    const open = S.alertsOpen && alerts.length > 0;
    if (list.hidden !== !open) list.hidden = !open;
    setAttr(btn, "aria-expanded", String(open));
    setText(btn, open ? "접기" : "전체 보기");
    if (open) {
      put(list, alerts.map((a) => html`<li class="alert-item" data-tone="${lvlTone(a)}">${led(lvlTone(a))}<span class="msg">${a.message}</span><span class="meta meta-items"><b class="t-${lvlTone(a)}">${labels.level[a.level] || a.level}</b><span>${srcLabel(a.source)}</span>${a.target ? html`<span class="mono">${a.target}</span>` : ""}${a.since ? html`<span title="${fmt.datetime(a.since)} 부터">${sinceTxt(a.since)}</span>` : ""}</span></li>`));
    }
  }
  function layoutTicker() {
    if (!tickerDirty) return;
    tickerDirty = false;
    const tk = $("ticker");
    const first = $("ticker-track").querySelector(".tick-list");
    const over = !!first && first.scrollWidth > tk.clientWidth + 4;
    if (!first || !animOK()) {
      tk.classList.remove("run");
      // 움직이지 않는 띠가 넘치면 키보드로도 가로 스크롤할 수 있게
      if (over) { if (tk.tabIndex !== 0) tk.tabIndex = 0; setAttr(tk, "aria-label", "정보 경보 (가로 스크롤)"); }
      else if (tk.hasAttribute("tabindex")) { tk.removeAttribute("tabindex"); tk.removeAttribute("aria-label"); }
      return;
    }
    if (tk.hasAttribute("tabindex")) { tk.removeAttribute("tabindex"); tk.removeAttribute("aria-label"); }
    if (over) {
      // 진행 중 애니메이션 길이를 자주 바꾸면 위치가 튄다 — 폭이 크게(15%↑) 바뀔 때만 갱신
      const dur = Math.max(20, Math.round(first.scrollWidth / 42));
      const cur = parseFloat(tk.style.getPropertyValue("--dur")) || 0;
      if (!cur || Math.abs(dur - cur) / cur > 0.15 || !tk.classList.contains("run")) tk.style.setProperty("--dur", dur + "s");
      tk.classList.add("run");
    } else tk.classList.remove("run");
  }
  if ("ResizeObserver" in window) new ResizeObserver(() => { tickerDirty = true; layoutTicker(); }).observe($("ticker"));
  function setAlertsOpen(open, focusToggle) {
    if (S.alertsOpen === open) return;
    S.alertsOpen = open;
    if (S.snap) renderAlerts(S.snap);
    if (focusToggle) $("alerts-toggle").focus();
  }
  $("alerts-toggle").addEventListener("click", () => setAlertsOpen(!S.alertsOpen));
  // 벽 모드에서 겹쳐 뜬 목록은 바깥을 누르면 닫힌다
  document.addEventListener("pointerdown", (e) => {
    if (S.alertsOpen && root.classList.contains("is-wall") && !$("alerts").contains(e.target)) setAlertsOpen(false);
  });

  // ────────────────────────────────────────────── A. 하드웨어
  const GAUGES = [
    { id: "cpu", name: "CPU", unit: "%", min: 0, max: 100, warn: 60, crit: 85, spark: "cpu" },
    { id: "gpu", name: "GPU", unit: "%", min: 0, max: 100, warn: 60, crit: 85, spark: "gpu" },
    { id: "mem", name: "메모리", unit: "%", min: 0, max: 100, warn: 75, crit: 90, spark: "mem" },
    { id: "temp", name: "온도", unit: "°C", min: 30, max: 90, warn: 70, crit: 80, spark: "temp" },
  ];
  const TONE_WORD = { ok: "정상", warn: "주의", crit: "높음", muted: "–" };
  (function buildGauges() {
    const box = $("gauges");
    const A0 = -135; const SW = 270;
    const track = P.arc(50, 50, 40, A0, A0 + SW);
    let out = "";
    for (const g of GAUGES) {
      const fw = (g.warn - g.min) / (g.max - g.min); const fc = (g.crit - g.min) / (g.max - g.min);
      let ticks = "";
      for (let i = 0; i <= 10; i++) {
        const a = ((A0 + SW * (i / 10) - 90) * Math.PI) / 180;
        const r1 = i % 5 === 0 ? 44.5 : 45.5; const r2 = 48.5;
        ticks += `<line class="g-tick" x1="${(50 + r1 * Math.cos(a)).toFixed(2)}" y1="${(50 + r1 * Math.sin(a)).toFixed(2)}" x2="${(50 + r2 * Math.cos(a)).toFixed(2)}" y2="${(50 + r2 * Math.sin(a)).toFixed(2)}"/>`;
      }
      out += `<div class="gauge" data-g="${g.id}" data-tone="muted">
        <div class="gauge-dial" role="img" aria-label="${g.name} –">
          <svg class="gauge-svg" viewBox="0 0 100 100" aria-hidden="true">
            ${ticks}
            <path class="g-band g-band-warn" d="${P.arc(50, 50, 52, A0 + SW * fw, A0 + SW * fc)}"/>
            <path class="g-band g-band-crit" d="${P.arc(50, 50, 52, A0 + SW * fc, A0 + SW)}"/>
            <path class="g-track" d="${track}" pathLength="100"/>
            <path class="g-val" d="${track}" pathLength="100" stroke-dasharray="0 100"/>
          </svg>
          <div class="gauge-read" aria-hidden="true"><b class="gauge-val">–</b><span class="gauge-unit">${g.unit}</span></div>
        </div>
        <div class="gauge-meta">
          <h3 class="gauge-name">${g.name} <span class="state"></span></h3>
          <div class="gauge-sub"></div>
          <svg class="spark" viewBox="0 0 120 26" preserveAspectRatio="none" aria-hidden="true"><line class="spark-base" x1="0" y1="26" x2="120" y2="26"/><path class="spark-area"/><path class="spark-line"/></svg>
        </div>
      </div>`;
    }
    box.innerHTML = out;
  })();
  function setSpark(svg, values, opts) {
    if (!svg) return;
    const vb = svg.viewBox.baseVal; const w = vb.width; const h = vb.height;
    const line = svg.querySelector(".spark-line"); const area = svg.querySelector(".spark-area");
    const vs = values || [];
    const d = P.sparkPath(vs, w, h - 1, opts);
    if (line && line.getAttribute("d") !== d) line.setAttribute("d", d);
    if (area) { const a = d ? P.sparkPath(vs, w, h - 1, { ...opts, area: true }) : ""; if (area.getAttribute("d") !== a) area.setAttribute("d", a); }
  }
  function updateGauge(id, v, sub, spark, sparkOpts) {
    const g = GAUGES.find((x) => x.id === id);
    const el = $("gauges").querySelector(`[data-g="${id}"]`);
    if (!g || !el) return;
    const tone = v == null || isNaN(v) ? "muted" : P.loadTone(v, g.warn, g.crit);
    setData(el, "tone", tone);
    const pct = v == null || isNaN(v) ? 0 : clamp(((v - g.min) / (g.max - g.min)) * 100, 0, 100);
    const val = el.querySelector(".g-val");
    const da = pct.toFixed(1) + " 100";
    if (val.getAttribute("stroke-dasharray") !== da) val.setAttribute("stroke-dasharray", da);
    // 원호 안에는 정수만(62°C) — 소수점 값은 아래 부제에 둔다. 폭이 원호 안쪽을 넘지 않게.
    const shown = v == null || isNaN(v) ? "–" : Math.round(v).toString();
    setText(el.querySelector(".gauge-val"), shown);
    setText(el.querySelector(".state"), TONE_WORD[tone]);
    setAttr(el.querySelector(".gauge-dial"), "aria-label", `${g.name} ${v == null || isNaN(v) ? "–" : id === "temp" ? v.toFixed(1) : shown}${g.unit} (${TONE_WORD[tone]})`);
    put(el.querySelector(".gauge-sub"), sub);
    setSpark(el.querySelector(".spark"), spark, sparkOpts);
  }

  function renderHW(snap) {
    const sys = snap.system || {};
    const cpu = sys.cpu || {}; const gpu = sys.gpu || {}; const mem = sys.memory || {};
    const th = sys.thermal || {}; const pw = sys.power || {}; const clk = sys.clocks_mhz || {};
    const sp = snap.spark || {};
    const host = sys.host || {};
    put($("hw-meta"), html`${host.arch || ""} · 커널 ${host.kernel ? host.kernel.split("+")[0] : "–"} · 부팅 ${fmt.datetime(sys.boot_time)}`);

    const load = cpu.load || [];
    updateGauge("cpu", cpu.usage_pct, html`<span><b>${fmt.mhz(cpu.freq_mhz)}</b></span> <span>· 부하 <b>${fmt.num(load[0], 2)}</b></span>`, sp.cpu, { min: 0, max: 100 });
    updateGauge("gpu", gpu.available === false ? null : gpu.usage_pct, gpu.available === false ? html`V3D 사용 불가` : html`<span>V3D <b>${fmt.mhz(gpu.freq_mhz != null ? gpu.freq_mhz : clk.v3d)}</b></span> <span>· <b>${fmt.num(gpu.jobs_per_s, 1)}</b> jobs/s</span>`, sp.gpu, { min: 0, max: 100 });
    updateGauge("mem", mem.usage_pct, html`<span><b>${fmt.bytes(mem.used)}</b> / ${fmt.bytes(mem.total)}</span> <span>· 가용 <b>${fmt.bytes(mem.available)}</b></span>`, sp.mem, { min: 0, max: 100 });
    const tv = th.cpu_temp_c != null ? th.cpu_temp_c : cpu.temp_c;
    const temps = (sp.temp || []).filter((x) => x != null);
    const tmin = temps.length ? Math.min(...temps) - 3 : 30; const tmax = temps.length ? Math.max(...temps) + 3 : 90;
    updateGauge("temp", tv, html`<span>CPU <b>${fmt.temp(tv)}</b></span> <span>· PMIC <b>${fmt.temp(th.pmic_temp_c)}</b></span>`, sp.temp, { min: tmin, max: tmax });

    // 코어
    const cores = cpu.per_core_pct || [];
    const freqs = cpu.per_core_freq_mhz || [];
    const coreBox = $("cores");
    if (coreBox.childElementCount !== cores.length) {
      coreBox.innerHTML = cores.map((_, i) => `<div class="core"><span class="core-id">C${i}</span><span class="meter seg20" data-tone="muted"><i></i></span><span class="core-v">–</span></div>`).join("");
    }
    cores.forEach((v, i) => {
      const row = coreBox.children[i];
      const m = row.children[1];
      setData(m, "tone", P.loadTone(v));
      const w = clamp(v || 0, 0, 100).toFixed(1) + "%";
      if (m.firstChild.style.width !== w) m.firstChild.style.width = w;
      setText(row.children[2], fmt.pct(v));
      const f = freqs[i];
      setAttr(row, "title", `코어 ${i}: ${fmt.pct(v, 1)} · ${fmt.mhz(f)}`);
    });
    put($("cpu-extra"), html`<span title="부하 평균 1분 · 5분 · 15분">부하 <span class="k">1m</span> <b>${fmt.num(load[0], 2)}</b> <span class="k">5m</span> <b>${fmt.num(load[1], 2)}</b> <span class="k">15m</span> <b>${fmt.num(load[2], 2)}</b></span>`);
    put($("cpu-io"), html`iowait <b>${fmt.pct(cpu.iowait_pct, 1)}</b>`);

    // GPU 큐
    const q = gpu.queues || {};
    const QN = { bin: "bin", render: "render", tfu: "tfu", csd: "csd", cache_clean: "cache", cpu: "cpu" };
    const qk = Object.keys(q).length ? Object.keys(q) : Object.keys(QN);
    put($("gpuq"), html`<div class="gpuq">${qk.map((k) => html`<div class="gq"><div class="row"><span>${QN[k] || k}</span><b>${fmt.pct(q[k])}</b></div><span class="meter thin" data-tone="${q[k] > 0 ? "info" : "muted"}"><i style="width:${clamp(q[k] || 0, 0, 100).toFixed(1)}%"></i></span></div>`)}</div>
      <div class="kv-row"><span>V3D <b>${fmt.mhz(clk.v3d)}</b></span><span>jobs/s <b>${fmt.num(gpu.jobs_per_s, 1)}</b></span></div>`);

    // 메모리
    const tot = mem.total || 0;
    const cache = mem.cached == null && mem.buffers == null ? null : (mem.cached || 0) + (mem.buffers || 0);
    const usedPct = tot ? ((mem.used || 0) / tot) * 100 : 0;
    const cachePct = tot && cache != null ? clamp((cache / tot) * 100, 0, 100 - usedPct) : 0;
    const memTone = P.loadTone(mem.usage_pct, 75, 90);
    // 스왑은 "찼는가"가 아니라 "압박인가"로 판정한다(백엔드 경보와 같은 규칙):
    // 스왑 인이 있거나 가용 메모리 < 15% 면 경고, 많이 찼지만 압박이 없으면 정보.
    const availFrac = tot && mem.available != null ? mem.available / tot : null;
    const pressure = (mem.swap_in_ps || 0) > 0 || (availFrac != null && availFrac < 0.15);
    const swapAlert = (snap.alerts || []).find((x) => x.key === "system:swap" || x.target === "swap");
    let swapTone = mem.swap_pct == null ? "muted" : pressure ? "warn" : mem.swap_pct >= 80 ? "info" : "ok";
    if (swapAlert && (swapAlert.level === "warn" || swapAlert.level === "crit")) swapTone = swapAlert.level;
    const swapNote = mem.swap_pct == null ? "" : (mem.swap_in_ps || 0) > 0 ? `스왑 인 ${fmt.num(mem.swap_in_ps, 1)}/s` : pressure ? "가용 메모리 부족" : "압박 없음";
    put($("mem"), html`<div class="stack" role="img" aria-label="메모리: 사용 ${fmt.bytes(mem.used)}, 캐시 ${fmt.bytes(cache)}, 여유 ${fmt.bytes(mem.free)}">
        <i style="width:${usedPct.toFixed(1)}%;background:var(--${memTone === "muted" ? "muted" : memTone})"></i><i style="width:${cachePct.toFixed(1)}%;background:#3d4c66"></i>
      </div>
      <div class="legend"><span><i class="sw" style="background:var(--${memTone === "muted" ? "muted" : memTone})"></i>사용 <b>${fmt.bytes(mem.used)}</b></span><span><i class="sw" style="background:#3d4c66"></i>캐시 <b>${fmt.bytes(cache)}</b></span><span><i class="sw" style="background:#182234;outline:1px solid #2a3548"></i>여유 <b>${fmt.bytes(mem.free)}</b></span></div>
      <div class="stat-line"><span>${led(swapTone)}스왑 <b class="${swapTone === "warn" || swapTone === "crit" ? "t-" + swapTone : ""}">${fmt.pct(mem.swap_pct, 1)}</b> <span class="mono">${cbytes(mem.swap_used)}/${cbytes(mem.swap_total)}</span></span><span class="note ${swapTone === "warn" || swapTone === "crit" ? "t-" + swapTone : ""}">${swapNote}</span></div>
      <span class="meter thin" data-tone="${swapTone}" title="스왑 ${fmt.bytes(mem.swap_used)} / ${fmt.bytes(mem.swap_total)}"><i style="width:${clamp(mem.swap_pct || 0, 0, 100).toFixed(1)}%"></i></span>`);

    // 전원 · 냉각
    const vin = pw.input_v;
    const vTone = vin == null ? "muted" : vin < 4.8 ? "crit" : vin < 5.0 ? "warn" : "ok";
    const fan = th.fan || {};
    const thr = th.throttle;
    const rawN = thr && thr.raw ? parseInt(thr.raw, 16) : null;
    const FLAGS = [["저전압", 0], ["ARM 클럭 제한", 1], ["스로틀링", 2], ["온도 소프트 리밋", 3]];
    const flagState = (label, bit) => {
      if (!thr) return "muted";
      const nowOn = rawN != null && !isNaN(rawN) ? (rawN >> bit) & 1 : (thr.now || []).includes(label);
      const past = rawN != null && !isNaN(rawN) ? (rawN >> (bit + 16)) & 1 : (thr.since_boot || []).includes(label);
      return nowOn ? "crit" : past ? "warn" : "ok";
    };
    const FS = { ok: "정상", warn: "부팅 후 발생", crit: "현재 발생", muted: "확인 불가" };
    put($("power"), html`<div class="pwr-top"><div><span class="big">${pw.total_w != null ? pw.total_w.toFixed(2) : "–"}<small>W</small></span><div class="cap">보드 전력 (PMIC 추정)</div></div>
        <svg class="spark" viewBox="0 0 90 26" preserveAspectRatio="none" style="width:45%;--tc:var(--info)" aria-hidden="true"><path class="spark-area" d="${P.sparkPath(sp.pw, 90, 25, { area: true, min: 0 })}"/><path class="spark-line" d="${P.sparkPath(sp.pw, 90, 25, { min: 0 })}"/></svg></div>
      <div class="stat-line"><span>${led(vTone)} 5V 입력 <b>${vin != null ? vin.toFixed(2) + " V" : "–"}</b></span><span>팬 <b>${fmt.num(fan.rpm)}</b> rpm</span></div>
      <span class="meter thin" data-tone="info" title="팬 ${fmt.pct(fan.pct)}"><i style="width:${clamp(fan.pct || 0, 0, 100)}%"></i></span>
      <div class="throttle" role="list" aria-label="스로틀 플래그 (raw ${thr ? thr.raw : "–"})" title="vcgencmd get_throttled = ${thr ? thr.raw : "–"}">${FLAGS.map(([l, b]) => { const s = flagState(l, b); return html`<span role="listitem" class="thr ${s === "warn" ? "past" : s === "crit" ? "now" : ""}" title="${l}: ${FS[s]}">${led(s)}${l}<span class="sr-only"> ${FS[s]}</span></span>`; })}</div>
      <div class="thr-key" aria-hidden="true"><span>${led("ok")}정상</span><span>${led("warn")}부팅 후</span><span>${led("crit")}현재</span></div>`);

    // 스토리지
    const disks = sys.disks || [];
    const io = sys.disk_io || {};
    put($("disk"), html`${disks.map((d) => html`<div class="disk-row" title="${d.device} · ${d.fs} · ${fmt.bytes(d.used)} / ${fmt.bytes(d.total)}"><div class="top-line"><span class="mono">${d.mount}</span><span>${fmt.pct(d.usage_pct)} · ${cbytes(d.used)}/${cbytes(d.total)}</span></div><span class="meter thin" data-tone="${P.loadTone(d.usage_pct, 80, 92)}"><i style="width:${clamp(d.usage_pct || 0, 0, 100)}%"></i></span></div>`)}
      <div class="io"><div class="io-cell">읽기<b>${fmt.bps(io.read_bps)}</b><svg class="spark" viewBox="0 0 80 18" preserveAspectRatio="none" style="--tc:#38bdf8" aria-hidden="true"><path class="spark-line" d="${P.sparkPath(sp.dr, 80, 17, { min: 0 })}"/></svg></div>
      <div class="io-cell">쓰기<b>${fmt.bps(io.write_bps)}</b><svg class="spark" viewBox="0 0 80 18" preserveAspectRatio="none" style="--tc:#fdba74" aria-hidden="true"><path class="spark-line" d="${P.sparkPath(sp.dw, 80, 17, { min: 0 })}"/></svg></div></div>`);

    // 네트워크
    const net = sys.network || {};
    const ifs = net.interfaces || [];
    const wifi = ifs.find((i) => i.kind === "wifi" && i.wifi);
    const sum = (a, b) => (a || []).map((v, i) => (v == null && (b || [])[i] == null ? null : (v || 0) + ((b || [])[i] || 0)));
    const q4 = wifi && wifi.wifi && wifi.wifi.quality != null ? Math.round(wifi.wifi.quality / 25) : 0;
    const lanIfs = ifs.filter((i) => (i.kind === "wired" || i.kind === "wifi") && (i.ipv4 || []).length);
    put($("net-hw"), html`<div class="net-row"><span class="lbl">LAN</span><span class="rates"><span class="dn">↓ ${fmt.bps(net.rx_bps)}</span><span class="up">↑ ${fmt.bps(net.tx_bps)}</span></span>
        <svg class="spark" viewBox="0 0 120 16" preserveAspectRatio="none" style="--tc:var(--k-public)" aria-hidden="true"><path class="spark-area" d="${P.sparkPath(sum(sp.rx, sp.tx), 120, 15, { area: true, min: 0 })}"/><path class="spark-line" d="${P.sparkPath(sum(sp.rx, sp.tx), 120, 15, { min: 0 })}"/></svg></div>
      <div class="net-row"><span class="lbl">Tailnet</span><span class="rates"><span class="dn">↓ ${fmt.bps(net.tailnet_rx_bps)}</span><span class="up">↑ ${fmt.bps(net.tailnet_tx_bps)}</span></span>
        <svg class="spark" viewBox="0 0 120 16" preserveAspectRatio="none" style="--tc:var(--k-tailnet)" aria-hidden="true"><path class="spark-area" d="${P.sparkPath(sum(sp.trx, sp.ttx), 120, 15, { area: true, min: 0 })}"/><path class="spark-line" d="${P.sparkPath(sum(sp.trx, sp.ttx), 120, 15, { min: 0 })}"/></svg></div>
      ${wifi ? html`<div class="stat-line"><span><span class="wifi" aria-hidden="true">${[1, 2, 3, 4].map((i) => html`<i class="${i <= q4 ? "on" : ""}" style="height:${i * 25}%"></i>`)}</span>Wi-Fi ${wifi.name}</span><span><b>${wifi.wifi.signal_dbm != null ? wifi.wifi.signal_dbm + " dBm" : "–"}</b> · ${fmt.pct(wifi.wifi.quality)}</span></div>` : ""}
`);
  }

  // ────────────────────────────────────────────── B. 서비스 매트릭스
  const lanesEl = $("lanes");
  function syncKeyed(container, items, keyOf, create, update) {
    const existing = new Map();
    for (const el of Array.from(container.children)) if (el.dataset.key != null) existing.set(el.dataset.key, el);
    const seen = new Set();
    let prev = null;
    for (const it of items) {
      const k = keyOf(it);
      if (seen.has(k)) continue;
      seen.add(k);
      let el = existing.get(k);
      if (!el) { el = create(it, k); el.dataset.key = k; }
      update(el, it);
      const want = prev ? prev.nextElementSibling : container.firstElementChild;
      if (want !== el) container.insertBefore(el, want);
      prev = el;
    }
    for (const [k, el] of existing) if (!seen.has(k)) el.remove();
  }
  function matches(s) {
    if (S.issues && s.status === "up") return false;
    const q = S.q.trim().toLowerCase();
    if (!q) return true;
    const r = s.runtime || {};
    return [s.name, s.title, s.id, s.upstream, r.name, r.project, ...(s.hostnames || [])].filter(Boolean).join(" ").toLowerCase().includes(q);
  }
  function portsOf(s, includeLoop) {
    return uniq((s.listeners || []).filter((l) => includeLoop || l.scope !== "loopback").map((l) => l.port)).sort((a, b) => a - b);
  }
  function tileMetric(s) {
    const r = s.runtime || {};
    const x = s.extra || {};
    const m = (inner, title) => html`<span class="m" title="${title || ""}">${inner}</span>`;
    if (s.category === "public" || s.category === "tailnet" || (s.probes && s.probes.internal)) {
      const t = s.traffic || {};
      return m(html`<b>${fmt.ms(P.pick.latency(s))}</b>`, `내부 프로브 지연 · 1시간 요청 ${fmt.num(t.requests_1h)}건`);
    }
    if (s.id === "infra:caddy") return m(html`<b>${fmt.num(x.routes)}</b> 라우트`, `Caddy ${x.version || ""}${x.config_ok === false ? " · 설정 오류" : ""}`);
    if (s.id === "infra:cloudflared") return m(html`<b>${fmt.num(x.connections)}</b> 연결`, `엣지 ${(x.edge_locations || []).join(", ")} · ${x.version || ""}`);
    if (s.id === "infra:tailscale") return m(html`피어 <b>${fmt.num(x.peers_online)}</b>`);
    if (s.category === "internal") return m(html`${labels.kind[s.kind] || s.kind || ""} <b>${fmt.bytes(r.mem_bytes)}</b>`);
    const ports = portsOf(s, s.category === "system");
    if (ports.length) return m(html`<b>:${ports.slice(0, 3).join(" :")}</b>${ports.length > 3 ? " …" : ""}`, ports.map((p) => ":" + p).join(" "));
    if (r.cpu_pct != null) return m(html`<b>${fmt.pct(r.cpu_pct, 1)}</b> · ${fmt.bytes(r.mem_bytes, 0)}`, "CPU · 메모리");
    return m(html`${labels.runtime[r.type] || ""} <b>${fmt.bytes(r.mem_bytes, 0)}</b>`);
  }
  function tileTip(s) {
    const r = s.runtime || {};
    const lines = [`${s.name}${s.title && s.title !== s.name ? " (" + s.title + ")" : ""} — ${stLabel(s.status)}`];
    if (s.status_reason) lines.push(s.status_reason);
    if ((s.hostnames || []).length) lines.push(s.hostnames.join(", "));
    if (s.upstream) lines.push("→ " + s.upstream);
    if (r.type) lines.push(`${labels.runtime[r.type] || r.type}: ${r.name || ""}${r.uptime_s != null ? " · " + fmt.durShort(r.uptime_s) : ""}`);
    return lines.join("\n");
  }
  function tileBody(s, snap) {
    const tone = tn(s.status);
    const reason = s.status !== "up" ? html`<span class="t3"><span class="st">${stLabel(s.status)}</span>${s.status_reason || ""}</span>` : "";
    return html`<span class="t1">${led(tone)}<span class="t-name">${s.name}</span></span><span class="t2">${glyphs(s)}${tileMetric(s)}</span>${reason}`;
  }
  function createTile(s) {
    const el = document.createElement("div");
    el.className = "tile";
    el.setAttribute("role", "listitem");
    const btn = document.createElement("button");
    btn.type = "button"; btn.className = "tile-btn";
    const body = document.createElement("span");
    body.className = "tile-body";
    btn.appendChild(body);
    const a = document.createElement("a");
    a.className = "tile-link"; a.target = "_blank"; a.rel = "noopener noreferrer";
    a.innerHTML = IC.ext.s;
    el.append(btn, a);
    el._t = { btn, body, a };
    return el;
  }
  function updateTile(el, s) {
    const t = el._t; const snap = S.snap;
    setData(el, "tone", tn(s.status));
    put(t.body, tileBody(s, snap));
    setAttr(t.btn, "aria-label", `${s.name} — ${stLabel(s.status)}${s.status_reason ? ", " + s.status_reason : ""}. 상세 보기`);
    setAttr(t.btn, "title", tileTip(s));
    const url = P.pick.primaryUrl(s);
    if (url) {
      setAttr(t.a, "href", P.safeUrl(url));
      setAttr(t.a, "aria-label", `${s.name} 열기 (새 창): ${url}`);
      setAttr(t.a, "title", url);
      if (t.a.hidden) t.a.hidden = false;
    } else if (!t.a.hidden) t.a.hidden = true;
    el.classList.toggle("selected", S.sel === s.id);
    const vis = matches(s);
    if (el.hidden === vis) el.hidden = !vis;
  }
  const LANE_SHORT = { public: "CF → Caddy :80", tailnet: "Caddy :443 전용", host: "직접 포트 (Caddy 우회)", hermes: "에이전트 게이트웨이", internal: "외부 노출 없음", infra: "인그레스·런타임", system: "OS 데몬" };
  function createLane(c) {
    const el = document.createElement("section");
    el.className = "lane";
    const hid = "lane-h-" + String(c.id).replace(/[^a-z0-9_-]/gi, "_");
    el.setAttribute("aria-labelledby", hid);
    const head = document.createElement("div");
    head.className = "lane-head";
    const tiles = document.createElement("div");
    tiles.className = "lane-tiles"; tiles.setAttribute("role", "list");
    el.append(head, tiles);
    el._l = { head, tiles, hid };
    return el;
  }
  function updateLane(el, c) {
    const { head, tiles, hid } = el._l;
    const snap = S.snap;
    const svcs = c.services || [];
    const cnt = (k) => (c[k] != null ? c[k] : svcs.filter((s) => s.status === k).length);
    // 개수는 제목에 한 번만. 이상이 있을 때만 그 개수를 모양+글자로 덧붙인다.
    const bad = [["degraded", "warn", "저하"], ["down", "crit", "다운"], ["unknown", "muted", "확인 불가"]].filter(([k]) => cnt(k) > 0);
    put(head, html`<h3 class="lane-title" id="${hid}">${c.label || labels.category[c.id] || c.id} <span class="num">${fmt.num(c.count != null ? c.count : svcs.length)}</span></h3>
      ${bad.length ? html`<div class="lane-counts">${bad.map(([k, t, l]) => html`<span class="t-${t}">${led(t)}${l} ${cnt(k)}</span>`)}</div>` : ""}
      <p class="lane-desc" title="${c.desc || ""}">${LANE_SHORT[c.id] || c.desc || ""}</p>`);
    syncKeyed(tiles, svcs, (s) => s.id, createTile, updateTile);
    const anyVisible = svcs.some(matches);
    let empty = tiles.querySelector(".lane-empty");
    if (!svcs.length && !empty) { /* 빈 카테고리 */ }
    const filtering = S.issues || S.q.trim();
    const hide = filtering && !anyVisible;
    if (el.hidden !== hide) el.hidden = hide;
  }
  function renderServices(snap) {
    const byCat = P.pick.byCategory(snap);
    const order = (snap.categories || []).map((c) => c.id);
    for (const k of Object.keys(byCat)) if (!order.includes(k)) order.push(k);
    const lanes = order.map((id) => byCat[id]).filter((c) => c && ((c.services || []).length || c.count));
    syncKeyed(lanesEl, lanes, (c) => c.id, createLane, updateLane);
    const sv = (snap.summary || {}).services || {};
    put($("svc-meta"), html`<span class="num">${fmt.num(sv.total)}</span>개 · 정상 <span class="num">${fmt.num(sv.up)}</span>${sv.degraded ? html` · <span class="t-warn">저하 <span class="num">${sv.degraded}</span></span>` : ""}${sv.down ? html` · <span class="t-crit">다운 <span class="num">${sv.down}</span></span>` : ""}`);
    // 걸러서 하나도 안 남으면 빈 레인 대신 가운데 정렬된 안내 — "이상만"에서 전부 정상이면 차분한 확인 메시지
    const anyShown = Array.from(lanesEl.children).some((l) => !l.hidden);
    if (lanesEl.hidden !== !anyShown) lanesEl.hidden = !anyShown;
    const empty = $("svc-empty");
    if (empty.hidden !== anyShown) empty.hidden = anyShown;
    if (!anyShown) {
      const q = S.q.trim();
      const msg = S.issues && !q
        ? html`${led("ok")}<span>이상 없음 — <span class="num">${fmt.num(sv.total)}</span>개 서비스 모두 정상</span>`
        : S.issues
          ? html`${led("muted")}<span>“${q}” 에 해당하는 이상 서비스가 없습니다 <span class="muted">· 나머지는 정상</span></span>`
          : html`${led("muted")}<span>“${q}” 검색 결과가 없습니다</span>`;
      put($("svc-empty-msg"), msg);
      setText($("svc-empty-reset"), S.issues ? "필터 해제" : "검색 지우기");
    }
    scheduleWallCheck();
  }
  $("svc-q").addEventListener("input", (e) => { S.q = e.target.value; if (S.snap) renderServices(S.snap); });
  const issuesBtn = $("svc-issues");
  issuesBtn.setAttribute("aria-pressed", String(S.issues));
  issuesBtn.addEventListener("click", () => {
    S.issues = !S.issues; store.set("issues", S.issues);
    issuesBtn.setAttribute("aria-pressed", String(S.issues));
    if (S.snap) renderServices(S.snap);
  });
  $("svc-empty-reset").addEventListener("click", () => {
    const hadIssues = S.issues;
    S.issues = false; store.set("issues", false); issuesBtn.setAttribute("aria-pressed", "false");
    S.q = ""; $("svc-q").value = "";
    if (S.snap) renderServices(S.snap);
    (hadIssues ? issuesBtn : $("svc-q")).focus();
  });
  lanesEl.addEventListener("click", (e) => {
    const b = e.target.closest(".tile-btn");
    if (!b) return;
    const tile = b.closest(".tile");
    if (tile) openInspector(tile.dataset.key, b);
  });

  // ────────────────────────────────────────────── C. 인그레스
  function stackBar(parts, label) {
    const total = parts.reduce((a, p) => a + (p.v || 0), 0);
    return html`<div class="stack" role="img" aria-label="${label}: ${parts.map((p) => p.label + " " + fmt.num(p.v)).join(", ")}">${total ? parts.filter((p) => p.v > 0).map((p) => html`<i style="width:${((p.v / total) * 100).toFixed(2)}%;background:${p.c}"></i>`) : ""}</div>
      <div class="legend">${parts.map((p) => html`<span><i class="sw" style="background:${p.c}"></i>${p.label} <b>${fmt.num(p.v)}</b></span>`)}</div>`;
  }
  const VIA_C = { tunnel: "var(--k-public)", tailnet: "var(--k-tailnet)", lan: "var(--k-lan)", docker: "var(--k-docker)", local: "var(--k-local)" };
  function renderIngress(snap) {
    const ig = snap.ingress || {};
    const cd = ig.caddy || {};
    const tu = ig.tunnel || {};
    const tr = ig.traffic || {};
    const svcBy = (id) => P.pick.service(snap, id) || {};
    const caddySvc = svcBy("infra:caddy");
    const cTone = cd.config_ok === false || cd.error ? "crit" : tn(caddySvc.status || "unknown");
    const pub = cd.public_hosts || []; const priv = cd.private_hosts || [];
    put($("caddy"), html`<div class="ing-line"><span class="ttl">${led(cTone)}Caddy <b>${cd.version || "–"}</b></span><span>라우트 <b>${fmt.num((cd.routes || []).length)}</b></span><span>${cd.config_ok === false ? html`<b class="t-crit">설정 오류</b>` : "설정 정상"}</span><span class="muted">Caddyfile ${ago(cd.caddyfile_mtime)} 수정</span></div>
      ${cd.error ? html`<div class="notice" style="color:var(--crit)">${led("crit")}${cd.error}</div>` : ""}
      <div class="hosts-line"><span title="${pub.join(", ")}"><span class="k" style="color:#9cc7fb">공개</span><b class="num">${pub.length}</b> <span class="muted">(:80·:443)</span></span><span title="${priv.join(", ")}"><span class="k" style="color:#c4b5fd">Tailnet 전용</span><b class="num">${priv.length}</b> <span class="muted">(:443)</span></span></div>`);

    const nConn = tu.connections || 0; const ready = tu.ready_connections != null ? tu.ready_connections : nConn;
    const slots = Math.max(4, nConn);
    const tTone = tu.ready === false ? "crit" : tu.ready ? (ready < nConn ? "warn" : "ok") : "muted";
    const rtt = (tu.rtt_ms || []).filter((x) => x != null);
    // 머리줄: 합계(아래 트래픽 블록과 중복) 대신 인그레스 전체 건강 + 지금 분당 요청
    const ingTone = [cTone, tTone].includes("crit") ? "crit" : [cTone, tTone].includes("warn") ? "warn" : cTone === "ok" && tTone === "ok" ? "ok" : "muted";
    const pm = tr.per_minute || [];
    put($("ing-meta"), html`${led(ingTone)}<span>${{ ok: "수신 정상", warn: "주의", crit: "장애", muted: "확인 중" }[ingTone]}</span><span class="muted">· 이번 분 <span class="num">${fmt.num(pm.length ? pm[pm.length - 1] : null)}</span>건</span>`);
    put($("tunnel"), html`<div class="ing-line"><span class="ttl">${led(tTone)}Cloudflare Tunnel</span><span class="mono">${tu.name || ""}</span><span>${tu.ready ? "준비됨" : tu.ready === false ? html`<b class="t-crit">준비 안 됨</b>` : "–"}</span>
        <span class="conns" role="img" aria-label="연결 ${ready}/${nConn}">${Array.from({ length: slots }, (_, i) => html`<i class="${i < ready ? "on" : ""}"></i>`)}</span><span><b>${ready}</b>/${nConn} 연결</span></div>
      <div class="tun-grid">
        <div class="cell" title="Cloudflare 엣지: ${(tu.edge_locations || []).join(", ")}"><span class="k">엣지</span>${(tu.edge_locations || []).length ? html`<span class="v chips">${tu.edge_locations.map((e) => html`<span class="chip">${e}</span>`)}</span>` : html`<span class="v">–</span>`}</div>
        <div class="cell"><span class="k">연결별 RTT</span><span class="v" title="${rtt.join(", ")} ms">${rtt.length ? rtt.map((x) => Math.round(x)).join("·") + " ms" : "–"}</span></div>
        <div class="cell"><span class="k">요청 · 오류</span><span class="v">${fmt.num(tu.total_requests)} · <span class="${tu.request_errors ? "t-warn" : ""}">${fmt.num(tu.request_errors)}</span></span></div>
      </div>
      <div class="tun-sub"><span>버전 <b>${tu.version || "–"}</b></span><span>분당 <b>${fmt.num(tu.req_per_min, 1)}</b></span><span>동시 <b>${fmt.num(tu.concurrent_requests)}</b></span><span>시작 ${ago(tu.process_start)}</span></div>
      ${tu.outdated ? html`<div class="notice">${led("warn")}cloudflared ${tu.outdated.current} → ${tu.outdated.latest} 업데이트 가능</div>` : ""}`);

    // 트래픽
    const win = S.trange === "24h" ? tr.last_24h || {} : tr.last_1h || {};
    const l1 = tr.last_1h || {}; const l24 = tr.last_24h || {};
    const bs = win.by_status || {};
    const bv = win.by_via || {};
    const is24 = S.trange === "24h";
    const series = is24 ? tr.per_hour || [] : tr.per_minute || [];
    const s5 = is24 ? [] : tr.per_minute_5xx || [];
    const W = 300; const H = 46;
    const max = Math.max(1, ...series.map((v) => +v || 0));
    const bars = P.bars(series, W, H, { gap: series.length > 40 ? 1 : 2, max });
    const bars5 = P.bars(s5, W, H, { gap: series.length > 40 ? 1 : 2, max });
    const peak = series.reduce((m, v) => Math.max(m, +v || 0), 0);
    const unit = is24 ? "시" : "분";
    put($("traffic"), html`<div class="trf-line"><span>1시간 <b>${fmt.num(l1.requests)}</b></span><span>24시간 <b>${fmt.num(l24.requests)}</b></span><span>p50 <b>${fmt.ms(win.p50_ms)}</b></span><span>p95 <b>${fmt.ms(win.p95_ms)}</b></span></div>
      <svg class="bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${is24 ? "최근 24시간 시간당" : "최근 60분 분당"} 요청 수, 최대 ${peak}, 현재 ${fmt.num(series[series.length - 1])}">
        ${bars.map((b, i) => html`<rect x="${b.x.toFixed(2)}" y="${b.y.toFixed(2)}" width="${b.w.toFixed(2)}" height="${Math.max(b.v ? 1 : 0, b.h).toFixed(2)}" class="${i === bars.length - 1 ? "cur" : ""}"><title>${is24 ? `${bars.length - 1 - i}시간 전` : `${bars.length - 1 - i}분 전`}: ${b.v}건${s5[i] ? ` (5xx ${s5[i]})` : ""}</title></rect>`)}
        ${bars5.filter((b) => b.v > 0).map((b) => html`<rect class="e5" x="${b.x.toFixed(2)}" y="${b.y.toFixed(2)}" width="${b.w.toFixed(2)}" height="${Math.max(1, b.h).toFixed(2)}"/>`)}
      </svg>
      <div class="bars-axis"><span>-${series.length}${unit}</span><span>${is24 ? "시간당" : "분당"} 최대 ${fmt.num(peak)}${s5.some((v) => v > 0) ? " · ■ 5xx" : ""}</span><span>지금</span></div>
      <div class="splits"><div class="split"><span class="k">상태 코드 · ${is24 ? "24시간" : "1시간"}</span>${stackBar([
        { label: "2xx", v: bs["2xx"] || 0, c: "var(--ok)" }, { label: "3xx", v: bs["3xx"] || 0, c: "var(--info)" },
        { label: "4xx", v: bs["4xx"] || 0, c: "var(--warn)" }, { label: "5xx", v: bs["5xx"] || 0, c: "var(--crit)" }], "상태 코드 분포")}</div>
      <div class="split"><span class="k">유입 경로</span>${stackBar(Object.keys(bv).sort((a, b) => bv[b] - bv[a]).map((k) => ({ label: labels.trafficVia[k] || k, v: bv[k], c: VIA_C[k] || "var(--muted)" })), "유입 경로 분포")}</div></div>`);

    // 탭: 최근 요청
    const rec = tr.recent || [];
    put($("tp-recent-c"), rec.length ? rec.map((r) => html`<div class="req"><span class="ts">${fmt.time(r.ts)}</span><span class="code ${codeClass(r.status)}">${r.status}</span><span class="what" title="${r.method} ${r.host}${r.path}"><span class="h">${shortHost(r.host)}</span>${r.path}</span><span class="ms">${fmt.ms(r.ms)}</span>
        <span class="sub"><span>${r.method}</span><span>${labels.trafficVia[r.via] || r.via || "?"}</span><span>${fmt.bytes(r.bytes)}</span><span class="mono" title="${r.client || ""}">${r.client || "–"}${r.country ? " " + r.country : ""}</span></span></div>`)
      : html`<p class="calm">${led("muted")}최근 요청이 없습니다.</p>`);
    // 탭: 상위 호스트
    const hosts = win.hosts || [];
    const hmax = Math.max(1, ...hosts.map((h) => h.requests || 0));
    put($("tp-hosts-c"), hosts.length ? hosts.map((h) => html`<div class="hostrow"><span class="h">${h.known === false ? led("warn") : led("ok")}${h.host ? html`<span title="${h.host}">${h.host}</span>` : html`<span class="blank" title="Host 헤더가 빈 요청">(빈 Host)</span>`}${h.folded ? html`<span class="chip">${fmt.num(h.folded)}개 묶음</span>` : ""}${h.known === false ? html`<span class="chip chip-warn">미등록</span>` : ""}</span><span class="n">${fmt.num(h.requests)}</span>
        <span class="meter thin" data-tone="${h.known === false ? "warn" : "info"}"><i style="width:${(((h.requests || 0) / hmax) * 100).toFixed(1)}%"></i></span>
        <span class="sub meta-items"><span>4xx ${fmt.num(h.errors_4xx)}</span><span>5xx <span class="${h.errors_5xx ? "t-crit" : ""}">${fmt.num(h.errors_5xx)}</span></span><span>p50 ${fmt.ms(h.p50_ms)}</span><span>p95 ${fmt.ms(h.p95_ms)}</span><span>${ago(h.last_ts)}</span></span></div>`)
      : html`<p class="calm">${led("muted")}이 기간에 요청이 없습니다.</p>`);
    // 탭: 오류
    const ce = tr.errors || []; const te = tu.recent_errors || [];
    setText($("err-n"), String(ce.length + te.length));
    const errRow = (e, src) => html`<div class="errrow"><div class="top-line">${led(e.level === "error" ? "crit" : e.level === "warn" ? "warn" : "info")}<span>${fmt.datetime(e.ts)}</span><span>${src}${e.logger ? " · " + e.logger : ""}</span></div><div class="msg">${e.msg}${e.error ? html` — ${e.error}` : ""}</div></div>`;
    put($("tp-errors-c"), ce.length || te.length ? html`${ce.length ? html`<h4 class="sub-h">Caddy ${ce.length}</h4>${ce.map((e) => errRow(e, "caddy"))}` : html`<p class="calm">${led("ok")}Caddy 오류 없음</p>`}
      ${te.length ? html`<h4 class="sub-h">cloudflared ${te.length}</h4>${te.slice().reverse().map((e) => errRow(e, "tunnel"))}` : ""}`
      : html`<p class="calm">${led("ok")}기록된 오류가 없습니다.</p>`);
    tabN.recent = rec.length; tabN.hosts = hosts.length;
    moreBtn("tabs", tabN[S.tab] || 0);
    cue($("tp-" + S.tab));
  }
  // 범위 토글 / 탭
  document.querySelectorAll("[data-range]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.range === S.trange));
    b.addEventListener("click", () => {
      S.trange = b.dataset.range; store.set("trange", S.trange);
      document.querySelectorAll("[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x.dataset.range === S.trange)));
      if (S.snap) renderIngress(S.snap);
    });
  });
  const tabs = Array.from(document.querySelectorAll('[role="tab"][data-tab]'));
  function selectTab(name, focus) {
    S.tab = name; store.set("tab", name);
    for (const t of tabs) {
      const on = t.dataset.tab === name;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
      if (on && focus) t.focus();
    }
    if (S.snap) { moreBtn("tabs", tabN[name] || 0); cue($("tp-" + name)); }
  }
  const tabN = { recent: 0, hosts: 0, errors: 0 };
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => selectTab(t.dataset.tab));
    t.addEventListener("keydown", (e) => {
      let j = null;
      if (e.key === "ArrowRight") j = (i + 1) % tabs.length;
      else if (e.key === "ArrowLeft") j = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === "Home") j = 0;
      else if (e.key === "End") j = tabs.length - 1;
      if (j != null) { e.preventDefault(); selectTab(tabs[j].dataset.tab, true); }
    });
  });
  selectTab(S.tab);

  // ────────────────────────────────────────────── D1. 네트워크 맵
  const mapEl = $("map"); const mapCols = $("map-cols"); const wiresEl = $("map-wires");
  let mapModel = null; let wireRaf = 0;
  const CAT_LABEL = (id) => labels.category[id] || id;
  function renderMap(snap) {
    const topo = snap.topology || {};
    const nodes = topo.nodes || [];
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const inc = new Map(); const out = new Map(); const deps = [];
    const push = (m, k, v) => { if (!m.has(k)) m.set(k, []); m.get(k).push(v); };
    for (const e of topo.edges || []) {
      if (!byId.has(e.from) || !byId.has(e.to)) continue;
      if (e.kind === "depends") { deps.push(e); continue; }
      push(inc, e.to, e); push(out, e.from, e);
    }
    const isZone = (n) => n.type === "zone" || (n.layer || 0) === 0;
    // 끝점에서 영역까지 거슬러 올라가며 경로를 모은다(같은 종류의 간선을 우선)
    const chains = (id, kind, depth) => {
      if (depth > 8) return [];
      const ins = inc.get(id) || [];
      const pref = kind ? ins.filter((e) => e.kind === kind) : ins;
      const res = [];
      for (const e of pref.length ? pref : ins) {
        const src = byId.get(e.from);
        if (isZone(src)) res.push({ zone: src, hops: [], labels: [e.label], kinds: [e.kind] });
        else for (const c of chains(src.id, e.kind, depth + 1)) res.push({ zone: c.zone, hops: [...c.hops, src], labels: [...c.labels, e.label], kinds: [...c.kinds, e.kind] });
      }
      return res;
    };
    const rows = new Map();
    for (const n of nodes) {
      if (isZone(n) || n.type === "internal" || (out.get(n.id) || []).length || !(inc.get(n.id) || []).length) continue;
      const bySeq = new Map();
      for (const p of chains(n.id, null, 0)) push(bySeq, p.hops.map((h) => h.id).join(">"), p);
      for (const [seq, ps] of bySeq) {
        const zones = uniq(ps.map((p) => p.zone.id));
        const key = seq + "|" + zones.slice().sort().join(",");
        if (!rows.has(key)) rows.set(key, { key, zones: zones.map((z) => byId.get(z)), hops: ps[0].hops, labels: ps[0].labels, kinds: ps.flatMap((p) => p.kinds), leaves: [], first: rows.size });
        rows.get(key).leaves.push(n);
      }
    }
    const rank = (r) => (r.kinds.includes("public") ? 0 : r.hops.length ? 1 : r.zones.some((z) => z.id === "zone:lan") ? 3 : 4);
    const list = [...rows.values()].sort((a, b) => rank(a) - rank(b) || a.first - b.first);
    const maxHops = Math.max(1, ...list.map((r) => r.hops.length));
    const internals = nodes.filter((n) => n.type === "internal");
    const kindOf = (r) => (r.kinds.includes("public") ? "public" : r.zones.every((z) => z.id === "zone:tailnet") ? "tailnet" : r.zones.some((z) => z.id === "zone:lan") ? "lan" : "tailnet");
    const zoneName = (z) => z.label || z.id;
    const rowLabel = (r) => {
      if (!r.hops.length) return "직접";
      const cats = r.leaves.map((l) => l.category).filter(Boolean);
      const top = cats.sort((a, b) => cats.filter((x) => x === b).length - cats.filter((x) => x === a).length)[0];
      return ({ public: "공개", tailnet: "Tailnet" })[top] || labels.category[top] || top || "기타";
    };
    const chip = (n, direct) => {
      const tone = tn(n.status);
      const sv = n.service_id ? P.pick.service(snap, n.service_id) : null;
      const ports = direct && sv ? portsOf(sv) : [];
      return html`<span class="mchip" data-node="${n.id}" data-tone="${tone}" title="${n.label}${n.detail ? " — " + n.detail : ""}: ${stLabel(n.status)}">${led(tone)}${n.label}${ports.length ? html`<span class="pt">:${ports.join(" :")}</span>` : ""}</span>`;
    };
    const hopBox = (n) => { const tone = tn(n.status); return html`<span class="hop" data-node="${n.id}" data-tone="${tone}" title="${n.label}${n.detail ? " — " + n.detail : ""}">${led(tone)}<span>${n.label}${n.detail ? html`<small>${n.detail}</small>` : ""}</span></span>`; };
    const ln = (label) => html`<span class="ln">${label ? html`<span class="lb">${label}</span>` : ""}</span>`;
    const rowsHtml = list.map((r) => {
      const k = kindOf(r);
      const slots = [];
      for (let j = 0; j < maxHops; j++) {
        const i = j - (maxHops - r.hops.length);
        if (i >= 0) slots.push(html`<div class="rs k-${k}">${ln(r.labels[i])}${hopBox(r.hops[i])}</div>`);
        else slots.push(html`<div class="rs k-${k}">${ln("")}</div>`);
      }
      const direct = !r.hops.length;
      const bad = r.leaves.filter((l) => l.status && l.status !== "up").length;
      return html`<div class="rz k-${k}"><span class="zone-pill">${r.zones.map(zoneName).join(" · ")}</span></div>${slots}
        <div class="rc k-${k}"><span class="ln arrow"></span><div class="rchips"><span class="rcap">${rowLabel(r)} <b>${r.leaves.length}</b>${bad ? html` <span class="t-warn">· 이상 ${bad}</span>` : ""}</span>${r.leaves.map((l) => chip(l, direct))}</div></div>`;
    });
    const intHtml = internals.length ? html`<div class="rint" style="grid-row: 1 / span ${Math.max(1, list.length)}"><div class="rcap">내부 백엔드 <b>${internals.length}</b> <span class="muted">· 외부 노출 없음</span></div><div class="rint-chips">${internals.map((n) => chip(n, false))}</div></div>` : "";
    mapModel = { edges: [], deps };   // 서비스→내부 백엔드 뿐 아니라 서비스→서비스 의존도 그린다
    mapCols.style.setProperty("--hops", String(maxHops));
    const changed = put(mapCols, html`${rowsHtml}${intHtml}`);
    if (changed || !wiresEl.__drawn) scheduleWires();
    renderPaths(snap, list, internals, (r) => {
      if (!r.hops.length) return "직접 포트";
      const l = rowLabel(r);
      return ({ 공개: "공개 서비스", Tailnet: "Tailnet 전용" })[l] || l;
    });
  }
  function scheduleWires() {
    if (wireRaf) return;
    wireRaf = requestAnimationFrame(() => { wireRaf = 0; drawWires(); });
  }
  function drawWires() {
    if (!mapModel) return;
    const box = mapEl.getBoundingClientRect();
    if (!box.width) return;
    const els = new Map();
    for (const n of mapCols.querySelectorAll("[data-node]")) els.set(n.getAttribute("data-node"), n);
    const R = (el) => { const r = el.getBoundingClientRect(); return { l: r.left - box.left, r: r.right - box.left, t: r.top - box.top, b: r.bottom - box.top, cy: (r.top + r.bottom) / 2 - box.top }; };
    const f = (v) => v.toFixed(1);
    const rint = mapCols.querySelector(".rint");
    const gutter = rint ? R(rint).l - 9 : null;
    const parts = []; const heads = [];
    let lane = 0;
    // 의존 선은 칩 사이 틈을 지나지 않게 직교로 그린다:
    //   출발 칩 아래 2.5px → (행 안쪽 틈을 따라) 가로 → 백엔드 상자 왼쪽 세로 통로 → 대상 칩 왼쪽 가운데
    //   같은 줄의 서비스→서비스는 두 칩 아래를 잇는 작은 꺾쇠
    for (const d of mapModel.deps) {
      const a = els.get(d.from); const b = els.get(d.to);
      if (!a || !b) continue;
      const ra = R(a); const rb = R(b);
      const x1 = (ra.l + ra.r) / 2; const yg = ra.b + 2.5;
      let path; let head;
      if (rint && rint.contains(b) && !rint.contains(a)) {
        const gx = gutter - (lane++ % 4) * 4;
        path = `M${f(x1)} ${f(ra.b)}V${f(yg)}H${f(gx)}V${f(rb.cy)}H${f(rb.l - 1)}`;
        head = `M${f(rb.l - 1)} ${f(rb.cy)}l-5 -3v6z`;
      } else {
        const x2 = (rb.l + rb.r) / 2;
        if (rb.t >= ra.b - 2) { path = `M${f(x1)} ${f(ra.b)}V${f(yg)}H${f(x2)}V${f(rb.t - 1)}`; head = `M${f(x2)} ${f(rb.t - 1)}l-3 -5h6z`; }
        else if (Math.abs(rb.t - ra.t) < 3) { path = `M${f(x1)} ${f(ra.b)}V${f(yg)}H${f(x2)}V${f(rb.b + 1)}`; head = `M${f(x2)} ${f(rb.b + 1)}l-3 5h6z`; }
        else { const yt = ra.t - 2.5; path = `M${f(x1)} ${f(ra.t)}V${f(yt)}H${f(x2)}V${f(rb.b + 1)}`; head = `M${f(x2)} ${f(rb.b + 1)}l-3 5h6z`; }
      }
      parts.push(html`<path class="w-depends" data-a="${d.from}" data-b="${d.to}" d="${path}"/>`);
      heads.push(html`<path class="w-dot" data-a="${d.from}" data-b="${d.to}" d="${head}"/>`);
    }
    put(wiresEl, [parts, heads]);
    wiresEl.__drawn = true;
    if (hlId) { const id = hlId; hlId = null; highlight(id); }
  }
  // 칩에 올리면 그 칩의 의존 선과 반대쪽 칩을 함께 강조
  let hlId = null;
  function highlight(id) {
    if (hlId === id) return;
    hlId = id;
    for (const el of mapEl.querySelectorAll(".hl")) el.classList.remove("hl");
    if (!id) return;
    const rel = new Set();
    for (const p of wiresEl.querySelectorAll("[data-a]")) {
      if (p.getAttribute("data-a") === id || p.getAttribute("data-b") === id) { p.classList.add("hl"); rel.add(p.getAttribute("data-a")); rel.add(p.getAttribute("data-b")); }
    }
    if (rel.size) for (const c of mapCols.querySelectorAll(".mchip[data-node]")) if (rel.has(c.getAttribute("data-node"))) c.classList.add("hl");
  }
  mapEl.addEventListener("pointerover", (e) => { const c = e.target.closest && e.target.closest(".mchip[data-node]"); highlight(c ? c.getAttribute("data-node") : null); });
  mapEl.addEventListener("pointerleave", () => highlight(null));
  if ("ResizeObserver" in window) new ResizeObserver(() => scheduleWires()).observe(mapEl);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { scheduleWires(); tickerDirty = true; layoutTicker(); });

  /** 모바일·스크린리더용 경로 목록 — 지도와 같은 행(토폴로지에서 도출)을 글로 푼다 */
  function renderPaths(snap, list, internals, rowLabelFull) {
    const svc = (n) => (n.service_id ? P.pick.service(snap, n.service_id) : null);
    const chipOf = (n, withPorts) => {
      const sv = svc(n); const ps = withPorts && sv ? portsOf(sv) : [];
      const tone = tn(n.status);
      return html`<span class="mchip" data-tone="${tone}">${led(tone)}${n.label}${ps.length ? html`<span class="pt">:${ps.join(" :")}</span>` : ""}<span class="sr-only"> ${stLabel(n.status)}</span></span>`;
    };
    const worst = (arr) => (arr.some((n) => n.status === "down") ? "crit" : arr.some((n) => n.status && n.status !== "up") ? "warn" : "ok");
    // 화살표는 다음 홉과 한 덩어리(줄바꿈이 → 뒤에서 생기지 않게)
    const route = (parts) => html`<span class="route">${parts.map((h, i) => html`<span>${i ? html`<span class="arrow">→</span> ` : ""}${h}</span>`)}</span>`;
    const rows = list.map((r) => {
      const direct = !r.hops.length;
      const parts = [r.zones.map((z) => z.label || z.id).join(" · "), ...r.hops.map((h, i) => { const port = r.labels[i] && /^:\d+$/.test(r.labels[i]) ? " " + r.labels[i] : ""; return h.detail ? `${h.label}${port} (${h.detail})` : h.label + port; })];
      if (direct) parts.push("직접 포트");
      const k = r.kinds.includes("public") ? "k-public" : r.zones.some((z) => z.id === "zone:lan") ? "k-lan" : "k-tailnet";
      return html`<li class="path-row ${k}"><span class="k">${led(worst(r.leaves))}${rowLabelFull(r)} <span class="num muted">${r.leaves.length}</span></span>${route(parts)}<span class="mchips">${r.leaves.map((n) => chipOf(n, direct))}</span></li>`;
    });
    if (internals.length) rows.push(html`<li class="path-row"><span class="k">${led(worst(internals))}내부 백엔드 <span class="num muted">${internals.length}</span></span>${route(["로컬 전용", "컨테이너 네트워크"])}<span class="mchips">${internals.map((n) => chipOf(n, false))}</span></li>`);
    const nodeLabel = (id) => { const n = ((snap.topology || {}).nodes || []).find((x) => x.id === id); return n ? n.label : id; };
    const deps = (mapModel && mapModel.deps) || [];
    if (deps.length) rows.push(html`<li class="path-row"><span class="k">의존 관계 <span class="num muted">${deps.length}</span></span><span class="route">${deps.map((d) => html`<span>${nodeLabel(d.from)} <span class="arrow">→</span> ${nodeLabel(d.to)}</span>`)}</span></li>`);
    put($("paths"), rows.length ? rows : html`<li class="calm">${led("muted")}토폴로지 정보가 없습니다.</li>`);
  }

  // ────────────────────────────────────────────── D2. Tailscale
  const OS_L = { linux: "Linux", windows: "Windows", macOS: "macOS", android: "Android", iOS: "iOS" };
  function renderTailscale(snap) {
    const ts = snap.tailscale || {};
    const self = ts.self || {};
    const peers = ts.peers || [];
    const direct = peers.filter((p) => p.online && p.connection === "direct").length;
    const relay = peers.filter((p) => p.online && p.connection === "relay").length;
    const bTone = ts.backend_state === "Running" ? "ok" : ts.backend_state ? "crit" : "muted";
    put($("ts-meta"), html`<span class="num">${fmt.num(ts.peers_online)}</span>/<span class="num">${fmt.num(ts.peers_total)}</span> 온라인 · 직접 <span class="num">${direct}</span> · 릴레이 <span class="num">${relay}</span>`);
    put($("ts-self"), html`<div class="ts-self">${led(bTone)}<span class="nm" title="${self.dns_name || ""}">${self.hostname || "–"} <span class="muted">이 호스트</span></span><span class="chip ${bTone === "ok" ? "chip-ok" : "chip-crit"}">${ts.backend_state || "–"}</span>
      <span class="sub meta-items">${(self.ips || []).map((ip) => html`<span class="mono" title="${ip}">${ip}</span>`)}<span>v${ts.version || "–"}</span>${self.relay ? html`<span>DERP ${self.relay}</span>` : ""}${self.dns_name ? html`<span class="mono" title="${self.dns_name}">${self.dns_name}</span>` : ""}</span></div>
      ${(ts.health || []).map((h) => html`<div class="notice">${led("warn")}${h}</div>`)}`);
    const seenAgo = (p) => (p.last_seen_ago_s != null ? fmt.ago(1, 1 + p.last_seen_ago_s) : ago(p.last_seen));   // 서버가 잰 경과가 있으면 그것을
    put($("peers"), peers.map((p) => {
      const tone = p.online ? "ok" : "muted";
      const conn = !p.online ? html`<span class="r" title="마지막 접속 ${fmt.datetime(p.last_seen)}">${seenAgo(p)}</span>` : p.connection === "direct" ? html`<span class="chip chip-ok">직접</span>` : p.connection === "relay" ? html`<span class="chip chip-warn">릴레이 ${p.relay || ""}</span>` : html`<span class="r muted">유휴</span>`;
      const tags = (p.tags || []).join(" ");
      return html`<li class="peer ${p.online ? "" : "off"}">${led(tone)}<span class="nm" title="${p.dns_name || p.hostname || ""}"><span>${p.hostname || "–"}</span></span>${conn}
        <span class="sub"><span class="mono">${p.ip || "–"}</span><span>${OS_L[p.os] || p.os || ""}</span>${p.online ? (p.rx_bytes || p.tx_bytes ? html`<span>↓${fmt.bytes(p.rx_bytes)} ↑${fmt.bytes(p.tx_bytes)}</span>` : "") : html`<span>오프라인</span>`}${tags ? html`<span title="${tags}">${tags}</span>` : ""}</span></li>`;
    }));
    moreBtn("peers", peers.length);
    cue($("peers-scroll"));
  }

  // ────────────────────────────────────────────── D3. 열린 포트
  const SCOPE_ORDER = { all: 0, lan: 1, tailnet: 2, docker: 3, loopback: 4 };
  // all = 0.0.0.0/:: → LAN·Tailnet·Docker·로컬 모두. 표에선 짧게 "모든 IF", 설명은 title 에.
  const SCOPE_L = { all: "모든 IF", lan: "LAN", tailnet: "Tailnet", docker: "Docker", loopback: "로컬" };
  const SCOPE_T = { all: "모든 인터페이스 (LAN·Tailnet·Docker·로컬)", lan: "LAN 주소에만", tailnet: "Tailnet 주소에만", docker: "Docker 브리지(컨테이너만)", loopback: "loopback(이 기기 안에서만)" };
  function renderPorts(snap) {
    const ls = (snap.listeners || []).slice().sort((a, b) => (SCOPE_ORDER[a.scope] ?? 9) - (SCOPE_ORDER[b.scope] ?? 9) || (a.port || 0) - (b.port || 0) || (a.proto > b.proto ? 1 : -1));
    const f = S.ports;
    const rows = ls.filter((l) => f === "all" || (f === "lan" ? l.scope === "all" || l.scope === "lan" : l.scope === f));
    const sl = (snap.summary || {}).listeners || {};
    put($("ports-meta"), html`TCP <span class="num">${fmt.num(sl.total)}</span> · <span class="t-warn">LAN <span class="num">${fmt.num(sl.lan)}</span></span> · Tailnet <span class="num">${fmt.num(sl.tailnet_only)}</span> · 로컬 <span class="num">${fmt.num(sl.loopback)}</span>`);
    put($("ports"), rows.length ? rows.map((l) => {
      const exp = l.scope === "all" || l.scope === "lan";
      const own = l.owner ? `${l.owner_kind ? l.owner_kind + ": " : ""}${l.owner}` : "";
      return html`<tr class="${exp ? "expose" : ""}"><td class="port">${l.port != null ? l.port : "–"}<span class="proto">${l.proto || ""}</span></td>
        <td><span class="scope scope-${l.scope}" title="${SCOPE_T[l.scope] || labels.scope[l.scope] || l.scope || ""}">${exp ? led("warn") : ""}${SCOPE_L[l.scope] || labels.scope[l.scope] || l.scope || "–"}</span></td>
        <td title="${(l.addrs || []).join(", ")}"><span class="cut">${l.process || "–"}${l.pid ? html` <span class="owner">${l.pid}</span>` : ""}</span><span class="cut own2" title="${own}">${l.owner || "–"}</span></td>
        <td class="c-owner owner" title="${own}"><span class="cut">${l.owner || "–"}</span></td></tr>`;
    }) : html`<tr><td colspan="4" class="muted">해당하는 포트가 없습니다.</td></tr>`);
    moreBtn("ports", rows.length);
    cue($("ports-scroll"));
  }
  const pf = $("ports-filter");
  pf.querySelectorAll("[data-f]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.f === S.ports)));
  pf.addEventListener("click", (e) => {
    const b = e.target.closest("[data-f]"); if (!b) return;
    S.ports = b.dataset.f; store.set("ports", S.ports);
    pf.querySelectorAll("[data-f]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    if (S.snap) renderPorts(S.snap);
  });

  // ────────────────────────────────────────────── E. 상세 데크
  function ctrTone(c) {
    if (c.state === "running") return c.health === "unhealthy" ? "crit" : c.health === "starting" || (c.restart_count || 0) >= 5 ? "warn" : "ok";
    if (c.state === "restarting") return "warn";
    if (c.state === "exited" || c.state === "dead") return c.exit_code === 0 ? "muted" : "crit";
    return "muted";
  }
  function ctrPorts(c) {
    if (c.network_mode === "host") return "host";
    const seen = new Set(); const out = [];
    for (const p of c.ports || []) {
      if (p.public == null) continue;
      const loop = p.ip === "127.0.0.1" || p.ip === "::1";
      const k = (loop ? "lo:" : "*:") + p.public + ">" + p.private + "/" + p.proto;
      if (seen.has(k)) continue;
      seen.add(k); out.push({ loop, pub: p.public, priv: p.private });
    }
    if (!out.length) return html`<span class="muted">–</span>`;
    return html`${out.slice(0, 3).map((p, i) => html`${i ? html`<br>` : ""}<span class="${p.loop ? "" : "t-warn"}" title="${p.loop ? "loopback 전용" : "모든 인터페이스"}">${p.loop ? "lo" : "*"}:${p.pub}→${p.priv}</span>`)}${out.length > 3 ? html`<br><span class="muted">+${out.length - 3}</span>` : ""}`;
  }
  function renderContainers(snap) {
    const cs = snap.containers || []; const ps = snap.projects || [];
    const sm = (snap.summary || {}).containers || {};
    put($("ctr-meta"), html`<span class="num">${fmt.num(sm.total)}</span>개 · 실행 <span class="num">${fmt.num(sm.running)}</span> · healthy <span class="num">${fmt.num(sm.healthy)}</span>${sm.unhealthy ? html` · <span class="t-crit">unhealthy ${sm.unhealthy}</span>` : ""} · 프로젝트 <span class="num">${ps.length}</span>`);
    const byName = new Map(cs.map((c) => [c.name, c]));
    const used = new Set();
    const row = (c) => {
      const tone = ctrTone(c);
      used.add(c.name);
      return html`<tr><td class="nowrap">${led(tone)} <span class="${tone === "ok" ? "dim" : "t-" + tone}">${c.state}${c.health ? " · " + c.health : ""}</span></td>
        <td class="nm"><b>${wb(c.name)}</b><div class="reg-sub ell" title="${c.image}">${c.image}</div></td>
        <td class="nowrap mono">${fmt.durShort(c.uptime_s)}</td>
        <td class="r ${(c.restart_count || 0) >= 5 ? "t-warn" : ""}">${fmt.num(c.restart_count)}</td>
        <td class="r">${fmt.pct(c.cpu_pct, 1)}</td><td class="r">${fmt.bytes(c.mem_bytes)}</td>
        <td class="mono nowrap">${ctrPorts(c)}</td></tr>`;
    };
    const out = [];
    for (const p of ps) {
      const pt = p.status === "running" ? "ok" : p.status === "partial" ? "warn" : "crit";
      out.push(html`<tr class="grp"><td colspan="7">${led(pt)} ${p.name} <span class="muted">· ${p.running}/${p.total} 실행 · <span class="mono">${p.dir}</span></span></td></tr>`);
      for (const n of p.containers || []) { const c = byName.get(n); if (c) out.push(row(c)); }
    }
    const rest = cs.filter((c) => !used.has(c.name));
    if (rest.length) { out.push(html`<tr class="grp"><td colspan="7">Compose 밖 컨테이너</td></tr>`); rest.forEach((c) => out.push(row(c))); }
    put($("ctr"), out.length ? out : html`<tr><td colspan="7" class="muted">컨테이너가 없습니다.</td></tr>`);
    cue($("ctr").closest(".scroll"));
  }
  function unitTone(u) { return u.active === "active" ? "ok" : u.active === "failed" ? "crit" : u.active === "activating" || u.active === "reloading" ? "warn" : "muted"; }
  function renderUnits(snap) {
    const us = snap.units || [];
    const failed = us.filter((u) => u.active === "failed");
    put($("units-meta"), html`<span class="num">${us.length}</span>개 · ${failed.length ? html`<span class="t-crit">실패 <span class="num">${failed.length}</span></span>` : "실패 없음"}`);
    const rows = S.units === "failed" ? failed : us;
    put($("units"), rows.length ? rows.map((u) => {
      const tone = unitTone(u);
      return html`<tr class="${tone === "crit" ? "failed" : ""}"><td class="nowrap">${led(tone)} <span class="${tone === "ok" ? "dim" : "t-" + tone}">${u.active}${u.sub && u.sub !== u.active ? "/" + u.sub : ""}</span></td>
        <td class="nm"><b class="mono" style="font-weight:600">${wb(u.name)}</b><div class="reg-sub">${u.description || ""}</div></td>
        <td class="nowrap">${u.scope}${u.category ? html`<div class="reg-sub">${u.category}</div>` : ""}</td>
        <td class="nowrap mono">${u.uptime_s != null ? fmt.durShort(u.uptime_s) : u.since ? fmt.datetime(u.since) : "–"}</td>
        <td class="r ${u.restarts ? "t-warn" : ""}">${fmt.num(u.restarts)}</td><td class="r">${fmt.pct(u.cpu_pct, 1)}</td><td class="r">${fmt.bytes(u.mem_bytes)}</td></tr>`;
    }) : html`<tr><td colspan="7" class="muted">실패한 유닛이 없습니다.</td></tr>`);
    moreBtn("units", rows.length);
    cue($("units").closest(".scroll"));
  }
  document.querySelectorAll("[data-units]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.units === S.units));
    b.addEventListener("click", () => {
      S.units = b.dataset.units; store.set("units", S.units);
      document.querySelectorAll("[data-units]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      if (S.snap) renderUnits(S.snap);
    });
  });

  const PROBE_L = { internal: "내부", tunnel: "터널", public: "공개" };
  // 프로브 결과는 세 상태: ok=true 성공 · false 실패 · null/없음 = 아직 모름(실패로 보이지 않게)
  const probeTone = (p) => (p.ok === true ? "ok" : p.ok === false ? "crit" : "muted");
  const probeVal = (p) => (p.code != null ? p.code : p.ok === true ? "OK" : p.ok === false ? "실패" : "–");
  const certTone = (c) => (!c || c.days_left == null ? "muted" : c.days_left < 14 ? "crit" : c.days_left < 30 ? "warn" : "ok");
  function probeCell(s) {
    const pr = s.probes || {};
    const ks = Object.keys(pr);
    if (!ks.length) return html`<span class="muted">–</span>`;
    return html`<div class="probe-mini">${ks.map((k) => { const p = pr[k] || {}; const tone = probeTone(p); return html`<span><span class="k">${PROBE_L[k] || k.replace(/\s*\(.*\)/, "")}</span>${led(tone)} ${probeVal(p)} · ${fmt.ms(p.ms)}${p.caddy_fallback && k === "tunnel" ? html` <span class="muted">차단</span>` : ""}</span>`; })}</div>`;
  }
  function runtimeLine(s) {
    const r = s.runtime;
    if (!r) return html`<span class="muted">–</span>`;
    return html`<div>${labels.runtime[r.type] || r.type} · <span class="mono">${r.name || "–"}</span></div><div class="reg-sub">${r.uptime_s != null ? "가동 " + fmt.durShort(r.uptime_s) : ""}${r.restart_count ? html` · <span class="t-warn">재시작 ${r.restart_count}</span>` : ""}${r.cpu_pct != null ? " · CPU " + fmt.pct(r.cpu_pct, 1) : ""}${r.mem_bytes != null ? " · " + fmt.bytes(r.mem_bytes) : ""}${r.ping_ms != null ? " · ping " + fmt.ms(r.ping_ms) : ""}</div>`;
  }
  const DNS_L = { cloudflare: "Cloudflare", tailnet: "tailnet IP", none: "없음", other: "기타", error: "조회 실패" };
  function renderRegister(snap) {
    const byCat = P.pick.byCategory(snap);
    const order = (snap.categories || []).map((c) => c.id);
    for (const k of Object.keys(byCat)) if (!order.includes(k)) order.push(k);
    const out = [];
    for (const cid of order) {
      const c = byCat[cid]; if (!c || !(c.services || []).length) continue;
      out.push(html`<tr class="grp"><td colspan="8">${c.label || CAT_LABEL(cid)} <span class="muted">· ${c.services.length} · ${c.desc || ""}</span></td></tr>`);
      for (const s of c.services) {
        const tone = tn(s.status);
        const cert = s.cert; const t = s.traffic;
        const urls = (s.urls || []).filter((u) => u && u.url);
        out.push(html`<tr><td class="nowrap">${led(tone)} <span class="${tone === "ok" ? "dim" : "t-" + tone}">${stLabel(s.status)}</span>${s.status_reason ? html`<div class="reg-sub" style="max-width:12rem;white-space:normal">${s.status_reason}</div>` : ""}</td>
          <td><div class="reg-name"><button type="button" class="reg-btn" data-open="${s.id}" data-k="reg:${s.id}">${wb(s.name)}</button></div>${s.title && s.title !== s.name ? html`<div class="reg-sub">${s.title}</div>` : ""}</td>
          <td><div class="urls">${urls.length ? urls.map((u, i) => html`<a href="${P.safeUrl(u.url)}" target="_blank" rel="noopener noreferrer" data-k="regu:${s.id}:${i}" title="${u.label || labels.audience[u.audience] || ""}: ${u.url}">${String(u.url).replace(/^https?:\/\//, "")}</a>`) : html`<span class="muted">–</span>`}</div></td>
          <td><div class="xp">${expChips(s)}</div><div class="via meta-items">${(s.exposure && s.exposure.via || []).map((v) => html`<span>${labels.via[v] || v}</span>`)}</div></td>
          <td>${runtimeLine(s)}</td>
          <td>${probeCell(s)}</td>
          <td class="nowrap">${cert ? html`<div>${led(certTone(cert))} ${cert.days_left == null ? "–" : fmt.num(cert.days_left) + "일"}</div>` : ""}${s.dns ? html`<div class="reg-sub">DNS → ${DNS_L[s.dns.points_to] || s.dns.points_to}</div>` : ""}${!cert && !s.dns ? html`<span class="muted">–</span>` : ""}</td>
          <td class="r">${t ? html`${fmt.num(t.requests_1h)} / ${fmt.num(t.requests_24h)}${t.errors_5xx_24h ? html`<div class="t-crit">5xx ${t.errors_5xx_24h}</div>` : ""}` : "–"}</td></tr>`);
      }
    }
    put($("reg"), out);
  }
  $("reg").addEventListener("click", (e) => { const b = e.target.closest("[data-open]"); if (b) openInspector(b.dataset.open, b); });

  const IF_ORDER = { wired: 0, wifi: 1, tailnet: 2, docker: 3, other: 4 };
  const IF_L = { wired: "유선", wifi: "Wi-Fi", tailnet: "Tailnet", docker: "Docker", other: "기타" };
  function renderIfaces(snap) {
    const ifs = (((snap.system || {}).network || {}).interfaces || []).slice().sort((a, b) => (IF_ORDER[a.kind] ?? 9) - (IF_ORDER[b.kind] ?? 9) || (b.up ? 1 : 0) - (a.up ? 1 : 0) || (a.name > b.name ? 1 : -1));
    put($("if-meta"), html`<span class="num">${ifs.length}</span>개 · 활성 <span class="num">${ifs.filter((i) => i.up).length}</span>`);
    put($("ifaces"), ifs.map((i) => html`<tr class="${i.up ? "" : "muted"}"><td class="nowrap">${led(i.up ? "ok" : "muted")} <b class="mono" style="font-weight:600">${i.name}</b> <span class="chip">${IF_L[i.kind] || i.kind}</span></td>
      <td class="mono">${(i.ipv4 || []).join(", ") || "–"}${(i.ipv6 || []).length ? html`<div class="reg-sub ell" title="${i.ipv6.join(", ")}">${i.ipv6[0]}</div>` : ""}</td>
      <td class="r">${fmt.bps(i.rx_bps)}</td><td class="r">${fmt.bps(i.tx_bps)}</td><td class="r">${fmt.bytes(i.rx_total)} / ${fmt.bytes(i.tx_total)}</td>
      <td class="nowrap">${i.wifi ? html`${i.wifi.signal_dbm != null ? i.wifi.signal_dbm + " dBm" : "–"} · ${fmt.pct(i.wifi.quality)}` : i.speed_mbps ? html`${fmt.num(i.speed_mbps)} Mbps` : i.up ? "up" : "down"}</td></tr>`));
    cue($("ifaces").closest(".scroll"));
  }
  function renderProcs(snap) {
    const pr = (snap.system || {}).processes || {};
    put($("proc-meta"), html`프로세스 <span class="num">${fmt.num(pr.count)}</span> · 스레드 <span class="num">${fmt.num(pr.threads)}</span>`);
    const row = (p) => html`<tr><td><b>${p.name}</b> <span class="owner mono">${p.pid}</span><div class="reg-sub ell" style="max-width:15rem" title="${p.cmd}">${p.cmd}</div></td>
      <td class="ell owner" style="max-width:10rem" title="${p.owner_kind}: ${p.owner}">${p.owner || p.user || "–"}</td><td class="r">${fmt.pct(p.cpu_pct, 1)}</td><td class="r">${fmt.bytes(p.rss)}</td></tr>`;
    const topCpu = (pr.top_cpu || []).slice(0, 8);
    const cpuPids = new Set(topCpu.map((p) => p.pid));
    const topMem = (pr.top_mem || []).filter((p) => !cpuPids.has(p.pid)).slice(0, 6);
    put($("procs"), [html`<tr class="grp"><td colspan="4">CPU 상위</td></tr>`, topCpu.map(row), topMem.length ? html`<tr class="grp"><td colspan="4">메모리 상위 (위 목록 제외)</td></tr>` : "", topMem.map(row)]);
    cue($("procs").closest(".scroll"));
  }
  function renderRails(snap) {
    const sys = snap.system || {}; const pw = sys.power || {}; const clk = sys.clocks_mhz || {};
    const rails = (pw.rails || []).slice().sort((a, b) => (b.watts || 0) - (a.watts || 0));
    const maxW = Math.max(0.001, ...rails.map((r) => r.watts || 0));
    put($("rails-meta"), html`합계 <span class="num">${fmt.watts(pw.total_w)}</span> · 입력 <span class="num">${pw.input_v != null ? pw.input_v.toFixed(2) + " V" : "–"}</span>`);
    put($("rails"), html`<div class="rails-cols"><div class="table-wrap scroll" style="max-height:20rem" tabindex="0" aria-label="전원 레일 표"><table class="tbl"><thead><tr><th scope="col">레일</th><th scope="col" class="r">V</th><th scope="col" class="r">A</th><th scope="col" class="r">W</th><th scope="col" style="width:30%"><span class="sr-only">비율</span></th></tr></thead><tbody>
      ${rails.map((r) => html`<tr><td class="mono nowrap">${r.name}</td><td class="r">${fmt.num(r.volts, 3)}</td><td class="r">${fmt.num(r.amps, 3)}</td><td class="r">${fmt.num(r.watts, 3)}</td><td><span class="meter thin" data-tone="info"><i style="width:${(((r.watts || 0) / maxW) * 100).toFixed(1)}%"></i></span></td></tr>`)}</tbody></table></div>
      <dl class="kv">${Object.keys(clk).map((k) => html`<dt>${k.toUpperCase()}</dt><dd class="mono">${fmt.mhz(clk[k])}</dd>`)}<dt>코어 전압</dt><dd class="mono">${pw.core_volts != null ? pw.core_volts.toFixed(3) + " V" : "–"}</dd><dt>PMIC 온도</dt><dd class="mono">${fmt.temp(((sys.thermal || {}).pmic_temp_c))}</dd><dt>governor</dt><dd class="mono">${(sys.cpu || {}).governor || "–"}</dd></dl></div>`);
  }
  function renderSys(snap) {
    const sys = snap.system || {}; const h = sys.host || {}; const m = snap.meta || {};
    const ca = m.collected_at || {}; const errs = m.errors || {};
    const t = serverNow();
    const collTone = (k, age) => { if (errs[k]) return "crit"; const lim = k === "dns" || k === "http_ports" ? 900 : 120; return age == null ? "muted" : age < lim ? "ok" : age < lim * 3 ? "warn" : "crit"; };
    put($("sys-meta"), html`${m.version ? "v" + m.version : ""} · Jarvis 가동 ${fmt.dur(m.started_at ? t - m.started_at : null)}`);
    put($("sysinfo"), html`<div class="sys-cols"><dl class="kv">
        <dt>모델</dt><dd>${h.model || "–"}</dd><dt>OS</dt><dd>${h.os || "–"}</dd><dt>커널</dt><dd class="mono">${h.kernel || "–"}</dd><dt>아키텍처</dt><dd class="mono">${h.arch || "–"} · ${fmt.num(h.cores)}코어 · 최대 ${fmt.mhz(h.cpu_max_mhz)}</dd>
        <dt>GPU</dt><dd>${h.gpu_name || "–"}${h.gpu_mem_mb != null ? " · " + h.gpu_mem_mb + " MB" : ""}</dd><dt>부팅</dt><dd class="mono">${fmt.datetime(sys.boot_time)}</dd><dt>가동</dt><dd>${fmt.dur(sys.uptime_s, 3)}</dd><dt>도메인</dt><dd class="mono">${m.domain || "–"}</dd></dl>
      <div><h3 class="bh">수집기 신선도</h3><div class="collectors">${Object.keys(ca).map((k) => { const age = ca[k] ? t - ca[k] : null; const tone = collTone(k, age); return html`<div class="coll" title="${k}: ${ago(ca[k])}${errs[k] ? " — " + errs[k] : ""}">${led(tone)}<span class="nm">${k}</span><span class="age">${age == null ? "–" : age < 60 ? Math.round(age) + "s" : fmt.durShort(age)}</span></div>`; })}</div>
        ${Object.keys(errs).length ? html`<h3 class="bh" style="margin-top:10px">수집 오류</h3>${Object.keys(errs).map((k) => html`<div class="notice" style="color:var(--crit)">${led("crit")}<span class="mono">${k}</span> ${errs[k]}</div>`)}` : html`<p class="calm">${led("ok")}수집 오류 없음</p>`}</div></div>`);
  }

  // ── 추세(작은 배수 차트)
  const S1 = "#38bdf8"; const S2 = "#fdba74";
  // wide: 두 계열(수신·송신 등) 차트는 2칸. 메모리·스왑은 1200–1799 에서만 2칸(CSS) — 어느 폭에서도 줄이 꽉 찬다.
  const HCH = [
    { id: "cpu", name: "CPU 사용률", s: [["cpu", "CPU"]], min: 0, max: 100, f: (v) => fmt.pct(v, 1) },
    { id: "gpu", name: "GPU V3D", s: [["gpu", "GPU"]], min: 0, max: 100, f: (v) => fmt.pct(v, 1) },
    { id: "temp", name: "CPU 온도", s: [["temp", "온도"]], auto: "temp", f: (v) => fmt.temp(v) },
    { id: "load", name: "부하 평균 (1분)", s: [["load", "부하"]], min: 0, f: (v) => fmt.num(v, 2) },
    { id: "mhz", name: "CPU 평균 클럭", s: [["mhz", "클럭"]], min: 0, f: (v) => fmt.mhz(v) },
    { id: "mem", name: "메모리 · 스왑", s: [["mem", "메모리"], ["swap", "스왑"]], min: 0, max: 100, f: (v) => fmt.pct(v, 1) },
    { id: "net", name: "LAN 트래픽", s: [["rx", "수신"], ["tx", "송신"]], min: 0, f: (v) => fmt.bps(v), wide: true },
    { id: "pw", name: "보드 전력", s: [["pw", "전력"]], min: 0, f: (v) => fmt.watts(v) },
    { id: "tnet", name: "Tailnet 트래픽", s: [["trx", "수신"], ["ttx", "송신"]], min: 0, f: (v) => fmt.bps(v), wide: true },
    { id: "disk", name: "디스크 I/O", s: [["dr", "읽기"], ["dw", "쓰기"]], min: 0, f: (v) => fmt.bps(v), wide: true },
    { id: "fan", name: "팬 속도", s: [["fan", "팬"]], min: 0, f: (v) => (v == null ? "–" : fmt.num(v) + " rpm"), fy: (v) => fmt.num(v) },
  ];
  const HW = 1000; const HH = 100;
  (function buildHist() {
    let out = "";
    for (const c of HCH) {
      out += `<figure class="hc${c.wide ? " wide" : ""}" data-h="${c.id}">
        <div class="hc-head"><figcaption class="hc-name">${c.name}</figcaption><span class="hc-now">–</span></div>
        <div class="hc-plot"><svg viewBox="0 0 ${HW} ${HH}" preserveAspectRatio="none" aria-hidden="true">
          <line class="grid" x1="0" y1="0" x2="${HW}" y2="0"/><line class="grid" x1="0" y1="50" x2="${HW}" y2="50"/><line class="grid" x1="0" y1="${HH}" x2="${HW}" y2="${HH}"/>
          ${c.s.map((x, i) => `<path class="ln" data-s="${i}" stroke="${i ? S2 : S1}"/>`).join("")}
        </svg><span class="hc-y top"></span><span class="hc-y bot"></span><span class="xhair"></span></div>
        <div class="hc-x"><span class="x0"></span><span class="x1">지금</span></div>
        <div class="hc-stats"></div>
      </figure>`;
    }
    $("hist-grid").innerHTML = out;
  })();
  function niceMax(v) {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }
  /** 시계열 → path. 그림 폭(px)만큼의 구간으로 나눠 구간마다 최소·최대 두 점만 남긴다(모양은 그대로, 점은 절반 이하). */
  function timePath(ts, vs, t0, t1, min, max, gap, buckets) {
    const span = t1 - t0 || 1; const rng = max - min || 1;
    const bw = span / Math.max(1, buckets);
    let d = ""; let pen = false; let lastT = null;
    let curB = null; let lo = null; let hi = null;
    const emit = (t, v) => {
      d += (pen ? "L" : "M") + (((t - t0) / span) * HW).toFixed(1) + " " + (HH - ((clamp(v, min, max) - min) / rng) * HH).toFixed(1);
      pen = true;
    };
    const flush = () => {
      if (!lo) return;
      if (lo === hi) emit(lo.t, lo.v);
      else if (lo.t <= hi.t) { emit(lo.t, lo.v); emit(hi.t, hi.v); } else { emit(hi.t, hi.v); emit(lo.t, lo.v); }
      lo = hi = null;
    };
    for (let i = 0; i < ts.length; i++) {
      const t = ts[i]; const v = vs ? vs[i] : null;
      if (t < t0 - gap) continue;
      if (v == null || isNaN(v)) { flush(); pen = false; continue; }
      if (lastT != null && t - lastT > gap) { flush(); pen = false; curB = null; }
      lastT = t;
      const b = Math.floor((t - t0) / bw);
      if (b !== curB) { flush(); curB = b; }
      if (!lo || v < lo.v) lo = { t, v };
      if (!hi || v > hi.v) hi = { t, v };
    }
    flush();
    return d;
  }
  function histWindow() {
    const span = S.hrange === "24h" ? 86400 : 3600;
    const h = S.hist;
    const sn = serverNow();
    const t1 = h && h.t && h.t.length ? Math.max(h.t[h.t.length - 1], sn - 5) : sn;
    return { t0: t1 - span, t1, span };
  }
  const finite = (v) => v != null && !isNaN(v);
  function renderHist() {
    const h = S.hist;
    const grid = $("hist-grid");
    if (!h || !h.t) { put($("hist-meta"), html`불러오는 중…`); return; }
    const { t0, t1 } = histWindow();
    const gap = (h.interval_s || 5) * 3.5;
    const n = h.t.length;
    const covered = n ? t1 - h.t[0] : 0;
    const full = S.hrange === "24h" ? 86400 : 3600;
    put($("hist-meta"), html`${S.hrange === "24h" ? "1분" : "5초"} 간격 · <span class="num">${fmt.num(n)}</span>점${covered < full * 0.9 ? html` · <span class="t-warn">기록 ${fmt.dur(covered)} 분량만 있음</span>` : ""}`);
    const last = (arr) => { for (let i = arr.length - 1; i >= 0; i--) if (finite(arr[i])) return arr[i]; return null; };
    for (const c of HCH) {
      const fig = grid.querySelector(`[data-h="${c.id}"]`);
      if (!fig) continue;
      const vals = c.s.map(([k]) => h[k] || []);
      const all = vals.flat().filter(finite);
      let min = c.min != null ? c.min : 0; let max = c.max;
      if (c.auto === "temp") { min = all.length ? Math.floor((Math.min(...all) - 2) / 5) * 5 : 30; max = all.length ? Math.ceil((Math.max(...all) + 2) / 5) * 5 : 90; }
      else if (max == null) max = niceMax(all.length ? Math.max(...all) * 1.08 : 1);
      fig._scale = { min, max };
      const px = Math.max(60, Math.round(fig.querySelector(".hc-plot").clientWidth || 300));
      c.s.forEach((_, i) => { const p = fig.querySelector(`[data-s="${i}"]`); const d = timePath(h.t, vals[i], t0, t1, min, max, gap, px); if (p.getAttribute("d") !== d) p.setAttribute("d", d); });
      const fy = c.fy || c.f;   // 눈금 라벨은 그림 옆 좁은 여백에 들어가도록 짧게
      setText(fig.querySelector(".hc-y.top"), fy(max));
      setText(fig.querySelector(".hc-y.bot"), fy(min));
      setText(fig.querySelector(".x0"), S.hrange === "24h" ? fmt.datetime(t0) : fmt.time(t0, false));
      const nowEl = fig.querySelector(".hc-now");
      if (!nowEl.classList.contains("cursor")) setText(nowEl, vals.map((v) => c.f(last(v))).join(" · "));
      // 계열마다 이름을 붙여 통계를 보인다(두 계열 차트에서 어느 쪽 통계인지 분명하게)
      const st = vals.map((arr) => { const v = arr.filter(finite); return v.length ? { min: Math.min(...v), avg: v.reduce((a, b) => a + b, 0) / v.length, max: Math.max(...v) } : null; });
      put(fig.querySelector(".hc-stats"), c.s.length === 1
        ? (st[0] ? html`<div><span>최소 <b>${c.f(st[0].min)}</b></span><span>평균 <b>${c.f(st[0].avg)}</b></span><span>최대 <b>${c.f(st[0].max)}</b></span></div>` : html`<div><span>데이터 없음</span></div>`)
        : c.s.map(([, name], i) => html`<div><span class="sn"><i style="border-color:${i ? S2 : S1}"></i>${name}</span>${st[i] ? html`<span>평균 <b>${c.f(st[i].avg)}</b></span><span>최대 <b>${c.f(st[i].max)}</b></span>` : html`<span>데이터 없음</span>`}</div>`));
    }
  }
  async function loadHist(force) {
    if (S.histBusy) return;
    const due = S.hrange === "24h" ? 60000 : 15000;
    if (!force && (S.hist ? !S.histVisible || document.hidden || Date.now() - S.histAt < due : !S.histVisible || Date.now() - S.histAt < 10000)) return;
    S.histBusy = true;
    const range = S.hrange;
    try {
      const h = await P.history(range);
      if (range === S.hrange) { S.hist = h; S.histAt = Date.now(); renderHist(); }
    } catch (e) {
      put($("hist-meta"), html`<span class="t-crit">추세를 불러오지 못했습니다 (${String(e.message || e)})</span>`);
      S.histAt = Date.now();
    } finally { S.histBusy = false; }
  }
  // 실제로 화면에 보일 때만 추세를 받아 그린다(여유 0px — 벽 아래 접힌 곳은 받지 않는다)
  if ("IntersectionObserver" in window) {
    new IntersectionObserver((ents) => { S.histVisible = ents.some((e) => e.isIntersecting); if (S.histVisible) loadHist(); }, { rootMargin: "0px" }).observe($("hist"));
  } else S.histVisible = true;
  document.querySelectorAll("[data-hrange]").forEach((b) => {
    b.setAttribute("aria-pressed", String(b.dataset.hrange === S.hrange));
    b.addEventListener("click", () => {
      if (S.hrange === b.dataset.hrange) return;
      S.hrange = b.dataset.hrange; store.set("hrange", S.hrange);
      document.querySelectorAll("[data-hrange]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      S.hist = null; renderHist(); loadHist(true);
    });
  });
  // 공유 십자선
  (function crosshair() {
    const grid = $("hist-grid");
    let raf = 0; let lastEv = null;
    const bsearch = (arr, t) => { let lo = 0; let hi = arr.length - 1; while (lo < hi) { const mid = (lo + hi) >> 1; if (arr[mid] < t) lo = mid + 1; else hi = mid; } if (lo > 0 && Math.abs(arr[lo - 1] - t) < Math.abs(arr[lo] - t)) lo--; return lo; };
    function apply() {
      raf = 0;
      const e = lastEv; const h = S.hist;
      if (!e || !h || !h.t || !h.t.length) return;
      const plot = e.target.closest && e.target.closest(".hc-plot");
      if (!plot) return;
      const r = plot.getBoundingClientRect();
      const f = clamp((e.clientX - r.left) / r.width, 0, 1);
      const { t0, span } = histWindow();
      const tc = t0 + f * span;
      const i = bsearch(h.t, tc);
      const ok = Math.abs(h.t[i] - tc) <= (h.interval_s || 5) * 3;
      const tf = S.hrange === "24h" ? fmt.datetime : fmt.time;   // 24시간 창은 이틀에 걸치므로 날짜까지
      grid.classList.add("hover");
      for (const c of HCH) {
        const fig = grid.querySelector(`[data-h="${c.id}"]`);
        fig.querySelector(".xhair").style.left = (f * 100).toFixed(2) + "%";
        const nowEl = fig.querySelector(".hc-now");
        nowEl.classList.add("cursor");
        setText(nowEl, ok ? tf(h.t[i]) + " · " + c.s.map(([k]) => c.f((h[k] || [])[i])).join(" · ") : tf(tc) + " · 데이터 없음");
      }
    }
    grid.addEventListener("pointermove", (e) => { lastEv = e; if (!raf) raf = requestAnimationFrame(apply); });
    grid.addEventListener("pointerleave", () => {
      lastEv = null; grid.classList.remove("hover");
      for (const el of grid.querySelectorAll(".hc-now.cursor")) el.classList.remove("cursor");
      renderHist();
    });
  })();

  // ────────────────────────────────────────────── 인스펙터
  const insp = $("inspector"); const scrim = $("scrim");
  let opener = null; let modal = false;
  // 좁은 화면에선 전체 화면 대화상자(모달): 뒤쪽은 inert, 본문 스크롤 잠금, 포커스는 안에서만 돈다
  function setModal(on) {
    modal = on;
    setAttr(insp, "aria-modal", on ? "true" : "false");
    for (const el of [$("screen"), $("deck"), $("foot")]) if (el && el.inert !== on) el.inert = on;
    document.body.classList.toggle("lock", on);
  }
  mqMobile.addEventListener("change", () => { if (!insp.hidden) setModal(mqMobile.matches); });
  function openInspector(id, from) {
    S.sel = id; opener = from || document.activeElement;
    try { history.replaceState(null, "", "#s=" + encodeURIComponent(id)); } catch (e) { /* 무시 */ }
    insp.hidden = false; scrim.hidden = false;
    setModal(mqMobile.matches);
    renderInspector();
    markSelected();
    $("insp-close").focus({ preventScroll: true });
  }
  function closeInspector() {
    const prev = S.sel;
    S.sel = null;
    insp.hidden = true; scrim.hidden = true;
    setModal(false);
    try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* 무시 */ }
    markSelected();
    let target = null;
    for (const t of lanesEl.querySelectorAll(".tile")) if (t.dataset.key === prev) target = t._t && t._t.btn;
    if (opener && opener.isConnected && opener !== document.body) target = opener;
    if (target) target.focus({ preventScroll: false });
    opener = null;
  }
  function markSelected() { for (const t of lanesEl.querySelectorAll(".tile")) t.classList.toggle("selected", t.dataset.key === S.sel); }
  $("insp-close").addEventListener("click", closeInspector);
  scrim.addEventListener("click", closeInspector);
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (S.alertsOpen) { e.preventDefault(); setAlertsOpen(false, true); return; }
    if (!insp.hidden) { e.preventDefault(); closeInspector(); }
  });
  insp.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || !modal) return;
    const f = Array.from(insp.querySelectorAll('a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])')).filter((x) => x.getClientRects().length);
    if (!f.length) return;
    const first = f[0]; const last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  insp.addEventListener("click", (e) => { const b = e.target.closest("[data-open]"); if (b) { const keep = opener; openInspector(b.dataset.open, keep); } });

  /** 노출 경로: 경로마다 한 줄(앞에 범위 배지), "→ 홉"은 한 덩어리로 줄바꿈 */
  function viaPath(s) {
    const via = (s.exposure || {}).via || [];
    const snap = S.snap || {};
    const ports = portsOf(s);
    const up = s.upstream ? [s.upstream] : [];
    const line = (hops) => html`<div class="pathline">${hops.map((h, i) => html`<span class="step">${i ? html`<span class="arrow">→</span>` : ""}<span class="hop">${h}</span></span>`)}</div>`;
    const row = (scope, label, hops) => html`<div class="pathrow"><span class="chip chip-${scope}">${label}</span>${line(hops)}</div>`;
    const rows = [];
    if (via.includes("cloudflare-tunnel")) rows.push(row("public", "공개", ["인터넷", "Cloudflare", "cloudflared", "Caddy :80", ...up]));
    if (via.includes("caddy-https")) rows.push(row("tailnet", "Tailnet", ["Tailnet", (s.hostnames || [])[0] || "*." + ((snap.meta || {}).domain || ""), "Caddy :443", ...up]));
    if (via.includes("direct-lan")) rows.push(row("lan", "LAN", ["LAN", ports.length ? ":" + ports.join(" :") : "직접"]));
    if (via.includes("direct-tailnet")) rows.push(row("tailnet", "Tailnet", ["Tailnet", ports.length ? ":" + ports.join(" :") : "직접"]));
    if (via.includes("tailnet-node")) rows.push(row("tailnet", "Tailnet", ["Tailnet", "WireGuard", (s.runtime || {}).ip || s.name]));
    if (!rows.length) rows.push(row("local", "로컬", ["로컬 전용", s.runtime && s.runtime.type === "container" ? "컨테이너 네트워크" : "loopback"]));
    return html`<div class="pathrows">${rows}</div>`;
  }
  const SRCSTAT_L = { runtime: "런타임 상태", probe: "HTTP 프로브" };
  function renderInspector() {
    if (insp.hidden || !S.sel || !S.snap) return;
    const snap = S.snap;
    const s = P.pick.service(snap, S.sel);
    if (!s) {
      put($("insp-head"), html`<div class="insp-title" id="insp-title">${S.sel}</div>`);
      put($("insp-body"), html`<p class="calm">${led("muted")}이 서비스는 현재 스냅샷에 없습니다.</p>`);
      return;
    }
    const tone = tn(s.status); const r = s.runtime || null;
    put($("insp-head"), html`<div class="insp-kicker"><span class="chip">${labels.category[s.category] || s.category}</span><span>${labels.kind[s.kind] || s.kind || ""}</span>${r ? html`<span>· ${labels.runtime[r.type] || r.type}</span>` : ""}</div>
      <h2 class="insp-title" id="insp-title">${led(tone)}${s.name}</h2>${s.title && s.title !== s.name ? html`<p class="insp-sub">${s.title}</p>` : ""}`);
    const sec = (title, body) => html`<section class="isec"><h3>${title}</h3>${body}</section>`;
    const kv = (pairs) => html`<dl class="kv">${pairs.filter((p) => p && p[1] != null && p[1] !== "").map(([k, v, mono]) => html`<dt>${k}</dt><dd class="${mono ? "mono" : ""}">${v}</dd>`)}</dl>`;
    const probes = s.probes || {};
    const pk = Object.keys(probes);
    const PL = { internal: "내부 (Caddy :443)", tunnel: "터널 경로 (Caddy :80)", public: "공개 (Cloudflare 엣지)" };
    const dependents = (snap.services || []).filter((x) => (x.depends_on || []).includes(s.id));
    const t = s.traffic;
    const cert = s.cert;
    const urls = (s.urls || []).filter((u) => u && u.url);
    const days = (d) => (d == null ? "–" : fmt.num(d, 0) + "일 남음");
    const body = [
      sec("상태", html`<div class="ing-line" style="font-size:.95rem">${led(tone)}<b style="font-family:var(--sans)" class="t-${tone === "ok" ? "ok" : tone}">${stLabel(s.status)}</b>${s.status_source ? html`<span class="muted">판정 근거: ${SRCSTAT_L[s.status_source] || s.status_source}</span>` : ""}</div>
        ${s.status_reason ? html`<p class="reason ${tone}" style="margin-top:8px">${s.status_reason}</p>` : ""}`),
      urls.length ? sec("접속", html`<div class="links">${urls.map((u, i) => html`<a href="${P.safeUrl(u.url)}" target="_blank" rel="noopener noreferrer" data-k="iu${i}"><span class="aud aud-${u.audience === "public" ? "public" : u.audience === "lan" ? "lan" : "tailnet"}">${u.label || labels.audience[u.audience] || u.audience || ""}</span><span class="u">${u.url}</span>${IC.ext}</a>`)}</div>`) : "",
      sec("노출", html`<div class="xp" style="margin-bottom:10px">${expChips(s)}</div>${viaPath(s)}${s.in_tunnel != null ? html`<p class="muted meta-items" style="font-size:.8rem;margin-top:8px"><span>터널 ingress ${s.in_tunnel ? "포함" : "미포함"}</span>${s.snippet ? html`<span>Caddy 스니펫 ${s.snippet}</span>` : ""}${s.header_inject ? html`<span>헤더 주입</span>` : ""}</p>` : ""}`),
      r ? sec("런타임", kv([
        ["유형", labels.runtime[r.type] || r.type], ["이름", r.name, true], ["프로젝트", r.project ? r.project + (r.service ? " / " + r.service : "") : null], ["이미지", r.image, true],
        ["상태", r.state ? r.state + (r.sub ? "/" + r.sub : "") + (r.health ? " · " + r.health : "") : null], ["가동", r.uptime_s != null ? fmt.dur(r.uptime_s) : null],
        ["재시작", r.restart_count != null ? html`<span class="${r.restart_count >= 5 ? "t-warn" : ""}">${fmt.num(r.restart_count)}회</span>` : null],
        ["CPU", r.cpu_pct != null ? fmt.pct(r.cpu_pct, 1) : null], ["메모리", r.mem_bytes != null ? fmt.bytes(r.mem_bytes) : null], ["PID", r.pid, true], ["IP", r.ip, true],
        ["작업 폴더", r.cwd, true], ["upstream", s.upstream, true],
      ])) : "",
      pk.length ? sec("프로브", html`<table class="probe-tbl"><tbody>${pk.map((k) => { const p = probes[k] || {}; const exp404 = k === "tunnel" && p.caddy_fallback; return html`<tr><th scope="row">${PL[k] || k}</th><td>${led(probeTone(p))} ${probeVal(p)}</td><td>${fmt.ms(p.ms)}</td></tr>${p.error || exp404 || p.cf_ray ? html`<tr><td colspan="3" class="err">${exp404 ? "Caddy 기본 응답 — 비공개 서비스는 터널 경로에서 막히는 것이 정상" : ""}${p.error || ""}${p.cf_ray ? html` <span class="mono">cf-ray ${p.cf_ray}</span>` : ""}</td></tr>` : ""}`; })}</tbody></table>`) : "",
      cert || s.edge_cert || s.dns ? sec("인증서 · DNS", kv([
        cert ? ["오리진 인증서", html`${led(certTone(cert))} ${days(cert.days_left)}${cert.issuer ? " · " + cert.issuer : ""}`] : null,
        cert ? ["주체", cert.subject, true] : null, cert && cert.not_after ? ["만료", fmt.datetime(cert.not_after)] : null,
        s.edge_cert ? ["엣지 인증서", `${days(s.edge_cert.days_left)}${s.edge_cert.issuer ? " · " + s.edge_cert.issuer : ""}`] : null,
        s.dns ? ["DNS", html`${DNS_L[s.dns.points_to] || s.dns.points_to || "–"}${(s.dns.answers || []).length ? html` <span class="muted mono">${s.dns.answers.join(", ")}</span>` : ""}`] : null,
      ])) : "",
      t ? sec("트래픽", kv([
        ["요청", html`<span class="mono">1h ${fmt.num(t.requests_1h)} · 24h ${fmt.num(t.requests_24h)}</span>`],
        ["오류 24h", html`<span class="mono">4xx ${fmt.num(t.errors_4xx_24h)} · <span class="${t.errors_5xx_24h ? "t-crit" : ""}">5xx ${fmt.num(t.errors_5xx_24h)}</span></span>`],
        ["지연 24h", html`<span class="mono">p50 ${fmt.ms(t.p50_ms_24h)} · p95 ${fmt.ms(t.p95_ms_24h)}</span>`], ["마지막 요청", ago(t.last_ts)],
      ])) : "",
      (s.listeners || []).length ? sec("리스너", html`<div class="xp">${s.listeners.map((l) => html`<span class="chip mono ${l.scope === "all" || l.scope === "lan" ? "chip-warn" : ""}" title="${SCOPE_T[l.scope] || ""} ${(l.addrs || []).join(", ")}">${l.proto || "?"}/${l.port != null ? l.port : "–"} · ${SCOPE_L[l.scope] || l.scope || "–"}</span>`)}</div>`) : "",
      (s.depends_on || []).length || dependents.length ? sec("의존 관계", html`${(s.depends_on || []).length ? html`<p class="muted" style="font-size:.8rem;margin-bottom:4px">이 서비스가 쓰는 것</p><div class="deps">${s.depends_on.map((d) => { const x = P.pick.service(snap, d); return html`<button type="button" class="dep-btn" data-open="${d}" data-k="dep:${d}">${led(x ? tn(x.status) : "muted")}${x ? x.name : d}</button>`; })}</div>` : ""}
        ${dependents.length ? html`<p class="muted" style="font-size:.8rem;margin:8px 0 4px">이 서비스를 쓰는 것</p><div class="deps">${dependents.map((x) => html`<button type="button" class="dep-btn" data-open="${x.id}" data-k="rdep:${x.id}">${led(tn(x.status))}${x.name}</button>`)}</div>` : ""}`) : "",
      s.description ? sec("설명", html`<p class="desc">${s.description}</p>`) : "",
      sec("식별자", html`<code class="muted">${s.id}</code>`),
    ];
    put($("insp-body"), body);
  }

  // ────────────────────────────────────────────── 스크롤 단서 · 모바일 "더 보기"
  // 안에서 스크롤되는 목록이 넘치면 아래쪽을 흐리고 "▼ N개 더" 를 띄운다(끝까지 내리면 사라짐).
  const cueRO = "ResizeObserver" in window ? new ResizeObserver((ents) => { for (const e of ents) cue(e.target); }) : null;
  function cue(el) {
    if (!el) return;
    if (!el._cue) {
      el._cue = { raf: 0 };
      el.addEventListener("scroll", () => cue(el), { passive: true });
      if (cueRO) cueRO.observe(el);
    }
    if (el._cue.raf) return;
    el._cue.raf = requestAnimationFrame(() => { el._cue.raf = 0; cueNow(el); });
  }
  function cueNow(el) {
    const over = el.scrollHeight > el.clientHeight + 2;
    const on = over && el.scrollTop + el.clientHeight < el.scrollHeight - 4;
    if (el.classList.contains("is-overflow") !== on) el.classList.toggle("is-overflow", on);
    if (!on) return;
    const hint = el.querySelector(":scope > .more-hint > span");
    if (!hint) return;
    const items = el.querySelectorAll(".req, .hostrow, .errrow, .peer, tbody > tr:not(.grp)");
    const bottom = el.getBoundingClientRect().bottom - 8;
    let n = 0;
    for (let i = items.length - 1; i >= 0; i--) { if (items[i].getBoundingClientRect().top >= bottom) n++; else break; }
    setText(hint, n ? `▼ ${n}개 더` : "▼ 더 있음");
  }
  const MORE_LIMIT = 8; const moreN = {};
  function moreBtn(name, n) {
    moreN[name] = n;
    const b = document.querySelector(`[data-more="${name}"]`);
    if (!b) return;
    const need = n > MORE_LIMIT;
    if (b.hidden !== !need) b.hidden = !need;
    const open = !!S.more[name];
    setAttr(b, "aria-expanded", String(open));
    setText(b, open ? "접기" : `${n - MORE_LIMIT}개 더 보기`);
    const targets = name === "tabs" ? [$("tp-recent-c"), $("tp-hosts-c")] : [$(name)];
    for (const t of targets) if (t && t.classList.contains("open") !== open) t.classList.toggle("open", open);
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("[data-more]");
    if (!b) return;
    const k = b.dataset.more;
    S.more[k] = !S.more[k];
    moreBtn(k, moreN[k] || 0);
    if (!S.more[k]) b.scrollIntoView({ block: "nearest" });
  });

  // ────────────────────────────────────────────── 벽 모드 — 들어갈 때만, 들어가는 한 가장 크게
  // 1500×820 이상에서 html.wall 을 켜 보고 A·B·C 열과 맵이 넘치지 않으면 유지한다.
  // 글자 크기(--wall-fs)는 14px 부터 올려 가며 들어가는 가장 큰 값을 고른다(2560×1440 같은 큰 화면에서 빈 공간 대신 글자를 키운다).
  const WALL_FS = [14, 15, 16, 17, 18, 19, 20];
  let wallSig = null; let wallQueued = false;
  function wallFits() {
    const els = [$("hw"), lanesEl, $("ing"), document.querySelector(".map-scroll")];
    for (const el of els) if (el && !el.hidden && el.scrollHeight > el.clientHeight + 2) return false;
    return true;
  }
  function wallSignature() {
    const snap = S.snap || {};
    const sv = snap.services || [];
    return [innerWidth, innerHeight, sv.length, sv.filter((s) => s.status !== "up").length, (snap.categories || []).length,
      ((snap.system || {}).disks || []).length, ((snap.topology || {}).nodes || []).length, fontsReady].join("|");
  }
  let fontsReady = 0;
  function setWall(on, fs) {
    root.classList.toggle("is-wall", on);
    if (on) root.style.setProperty("--wall-fs", fs + "px"); else root.style.removeProperty("--wall-fs");
  }
  function fitWall(fromFs) {
    const sx = window.scrollX; const sy = window.scrollY;
    const was = root.classList.contains("is-wall");
    const wasFs = was ? parseFloat(root.style.getPropertyValue("--wall-fs")) || 14 : 0;
    let best = 0;
    if (innerWidth >= 1500 && innerHeight >= 820) {
      const list = fromFs ? WALL_FS.filter((f) => f <= fromFs).reverse() : WALL_FS;
      for (const fs of list) {
        setWall(true, fs);
        const ok = wallFits();
        if (fromFs) { if (ok) { best = fs; break; } }
        else if (ok) best = fs; else break;
      }
    }
    setWall(!!best, best);
    if (window.scrollX !== sx || window.scrollY !== sy) window.scrollTo(sx, sy);
    if (was !== !!best || wasFs !== best) {
      tickerDirty = true; layoutTicker(); scheduleWires();
      document.querySelectorAll(".scroll").forEach((el) => { if (el._cue) cue(el); });
    }
  }
  function checkWall() {
    const sig = wallSignature();
    if (sig !== wallSig) { wallSig = sig; fitWall(0); return; }
    // 같은 조건인데 내용이 늘어 넘치면 한 단계씩 줄이고, 14px 로도 안 되면 일반 흐름으로
    if (root.classList.contains("is-wall") && !wallFits()) fitWall(parseFloat(root.style.getPropertyValue("--wall-fs")) - 1 || 13);
  }
  function scheduleWallCheck() {
    if (wallQueued) return;
    wallQueued = true;
    requestAnimationFrame(() => { wallQueued = false; if (S.snap) checkWall(); });
  }
  let resizeT = 0;
  window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(() => { wallSig = null; scheduleWallCheck(); }, 180); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { fontsReady = 1; wallSig = null; scheduleWallCheck(); });

  // ────────────────────────────────────────────── 모바일 바로가기: 지금 보고 있는 구역 표시
  if ("IntersectionObserver" in window) {
    const links = Array.from(document.querySelectorAll("[data-jump]"));
    const io = new IntersectionObserver((ents) => {
      for (const e of ents) if (e.isIntersecting) for (const a of links) a.classList.toggle("on", a.dataset.jump === e.target.id);
    }, { rootMargin: "-40% 0px -55% 0px" });
    for (const a of links) { const t = $(a.dataset.jump); if (t) io.observe(t); }
  }

  // ────────────────────────────────────────────── 렌더 루프
  const SECTIONS = [
    ["access", renderAccess], ["host", renderHost], ["overall", renderOverall], ["alerts", renderAlerts],
    ["hw", renderHW], ["services", renderServices], ["ingress", renderIngress], ["map", renderMap],
    ["tailscale", renderTailscale], ["ports", renderPorts], ["containers", renderContainers], ["units", renderUnits],
    ["register", renderRegister], ["ifaces", renderIfaces], ["procs", renderProcs], ["rails", renderRails], ["sys", renderSys],
  ];
  // 상세 데크 표는 화면에 보일 때만 다시 만든다(보이지 않는 표를 3초마다 다시 그리지 않는다)
  const DECK = new Set(["containers", "units", "register", "ifaces", "procs", "rails", "sys"]);
  const deckVis = new Set();
  const runSection = (name, fn) => { try { fn(S.snap); } catch (e) { console.error("[control] " + name + " 렌더 실패:", e); } };
  if ("IntersectionObserver" in window) {
    const io = new IntersectionObserver((ents) => {
      for (const e of ents) {
        const k = e.target.dataset.deck;
        if (e.isIntersecting) {
          if (!deckVis.has(k)) { deckVis.add(k); const sec = SECTIONS.find(([n]) => n === k); if (sec && S.snap) runSection(k, sec[1]); }
        } else deckVis.delete(k);
      }
    }, { rootMargin: "0px 0px 120px 0px" });
    document.querySelectorAll("[data-deck]").forEach((el) => io.observe(el));
  } else DECK.forEach((k) => deckVis.add(k));
  let firstSnap = true;
  function renderAll() {
    const snap = S.snap;
    for (const [name, fn] of SECTIONS) {
      // 첫 스냅샷에서는 전부 한 번 채운다(빈 표가 보이지 않게). 그 뒤로는 보이는 표만 갱신.
      if (!firstSnap && DECK.has(name) && !deckVis.has(name)) continue;
      runSection(name, fn);
    }
    try { renderFresh(); renderInspector(); } catch (e) { console.error("[control] 인스펙터 렌더 실패:", e); }
    setText($("foot-ver"), `Jarvis ${(snap.meta && snap.meta.version) || ""} · 관제실`);
    if (firstSnap) {
      firstSnap = false;
      const m = /^#s=(.+)$/.exec(location.hash);
      if (m) { let id = null; try { id = decodeURIComponent(m[1]); } catch (e) { /* 무시 */ } if (id && P.pick.service(snap, id)) openInspector(id, null); }
    }
    scheduleWallCheck();
    loadHist(false);
  }
  // 추세는 시작할 때 한 번 받아 둔다(접힌 곳이어도 빈 차트가 아니게). 이후 갱신은 보일 때만.
  setTimeout(() => { if (!S.hist) loadHist(true); }, 1500);
  P.subscribe((snap, st) => {
    if (!st.stale && st.fetchedAt && snap.meta && snap.meta.generated_at) {
      S.lastOk = st.fetchedAt;
      const d = st.fetchedAt - snap.meta.generated_at;   // 시계 차 + 지연(≤1초 캐시 포함)
      S.skew = S.skew == null || Math.abs(d - S.skew) > 30 ? d : S.skew * 0.8 + d * 0.2;
    }
    S.snap = snap; S.st = st; renderAll();
  }, {
    onError: (e) => {
      const el = $("overall");
      setData(el, "tone", "crit");
      put(el, html`${led("crit")}<div class="overall-txt"><b class="overall-label">연결 실패</b><span class="overall-sub"><span class="why">Jarvis API 에 연결할 수 없습니다 — 다시 시도 중 (${String((e && e.message) || e)})</span></span></div>`);
    },
  });
  tickClock();
  setInterval(() => { if (!document.hidden) tickClock(); }, 1000);
})();
