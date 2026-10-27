/* Ledger — Jarvis 상태 보고
 * 신문 지면처럼 조판한 Jarvis 시안. 데이터는 /shared/jarvis.js 가 3초마다 가져온다.
 * 규칙: 데이터로 HTML 을 만들 때는 반드시 JARVIS.html`` (자동 이스케이프), URL 은 JARVIS.safeUrl.
 *       DOM 은 영역별로 문자열 비교 후 바뀐 곳만 갈아 끼우고, 포커스·펼침·스크롤을 보존한다.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  if (!P) return;
  const { html, fmt } = P;
  const L = P.labels;
  const doc = document;
  const $ = (s) => doc.querySelector(s);
  const byId = (id) => doc.getElementById(id);
  const DASH = "–";
  const has = (v) => v != null && !(typeof v === "number" && !isFinite(v));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const sum = (a) => a.reduce((x, y) => x + (+y || 0), 0);
  const tone = P.tone;
  const now = () => Date.now() / 1000;

  if (P.embed) doc.documentElement.classList.add("embed");

  // ================================================================ DOM 갱신 (상태 보존)
  const tpl = doc.createElement("template");
  const q = (attr, key) => `[${attr}="${CSS.escape(key)}"]`;

  function capture(root) {
    const st = { fk: null, dk: null, open: null, scroll: null };
    const a = doc.activeElement;
    if (a && a !== doc.body && root.contains(a)) {
      st.fk = a.getAttribute("data-fk");
      // 펼침 요약(<summary>)에 포커스가 있으면 그 details 의 키로 기억한다
      if (!st.fk && a.tagName === "SUMMARY" && a.parentElement && a.parentElement.hasAttribute("data-dk")) st.dk = a.parentElement.getAttribute("data-dk");
    }
    const ds = root.querySelectorAll("details[data-dk]");
    if (ds.length) { st.open = []; ds.forEach((d) => st.open.push([d.getAttribute("data-dk"), d.open])); }
    const ss = root.querySelectorAll("[data-sk]");
    if (ss.length) {
      st.scroll = [];
      ss.forEach((e) => { if (e.scrollTop || e.scrollLeft) st.scroll.push([e.getAttribute("data-sk"), e.scrollTop, e.scrollLeft]); });
    }
    return st;
  }
  function restore(root, st) {
    if (st.open) for (const [k, o] of st.open) { const d = root.querySelector("details" + q("data-dk", k)); if (d && d.open !== o) d.open = o; }
    if (st.scroll) for (const [k, t, l] of st.scroll) { const e = root.querySelector(q("data-sk", k)); if (e) { e.scrollTop = t; e.scrollLeft = l; } }
    if (st.fk) {
      const e = root.querySelector(q("data-fk", st.fk));
      if (e && doc.activeElement !== e) e.focus({ preventScroll: true });
    } else if (st.dk) {
      const e = root.querySelector("details" + q("data-dk", st.dk) + " > summary");
      if (e && doc.activeElement !== e) e.focus({ preventScroll: true });
    }
  }
  /** 영역 하나를 통째로 갈아 끼운다(내용이 같으면 아무것도 안 함). */
  function put(el, content) {
    if (typeof el === "string") el = byId(el);
    if (!el) return false;
    const s = content == null ? "" : String(content);
    if (el.__h === s) return false;
    const st = capture(el);
    el.innerHTML = s;
    el.__h = s;
    restore(el, st);
    return true;
  }
  function make(s) {
    tpl.innerHTML = s.trim();
    const el = tpl.content.firstElementChild;
    if (!el) return null;
    el.__h = s;
    for (const c of el.children) c.__h = c.outerHTML;
    return el;
  }
  const bareText = (el) => { let t = ""; for (const n of el.childNodes) if (n.nodeType === 3) t += n.data; return t; };
  /** 항목 el 을 새 마크업 n 으로 맞춘다. 구조가 같으면 바뀐 칸(자식)만 갈아 끼워서
   *  링크·펼침 같은 상호작용 요소가 3초마다 새로 태어나지 않게 한다. 반환: 최종 요소. */
  function patch(el, n) {
    if (el.tagName !== n.tagName || el.children.length !== n.children.length || bareText(el).trim() !== bareText(n).trim()) {
      el.replaceWith(n);
      return n;
    }
    for (const at of Array.from(el.attributes)) if (!n.hasAttribute(at.name)) el.removeAttribute(at.name);
    for (const at of Array.from(n.attributes)) if (el.getAttribute(at.name) !== at.value) el.setAttribute(at.name, at.value);
    const olds = Array.from(el.children); const news = Array.from(n.children);
    for (let i = 0; i < olds.length; i++) {
      if (olds[i].__h !== news[i].__h) olds[i].replaceWith(news[i]);
    }
    el.__h = n.__h;
    return el;
  }
  /** 키가 있는 목록: 바뀐 항목만 교체하고 순서를 맞춘다. items = [{k, h}] (h 는 요소 하나). */
  function list(container, items) {
    if (typeof container === "string") container = byId(container);
    if (!container) return;
    const st = capture(container);
    const old = new Map();
    for (const el of Array.from(container.children)) {
      const k = el.getAttribute("data-key");
      if (k != null && !old.has(k)) old.set(k, el); else el.remove();
    }
    let prev = null; let changed = false;
    for (const it of items) {
      const s = String(it.h);
      let el = old.get(it.k);
      if (el) {
        old.delete(it.k);
        if (el.__h !== s) { const n = make(s); if (n) { el = patch(el, n); changed = true; } }
      } else {
        el = make(s);
        if (!el) continue;
        changed = true;
      }
      const want = prev ? prev.nextElementSibling : container.firstElementChild;
      if (want !== el) { container.insertBefore(el, want); changed = true; }
      prev = el;
    }
    for (const el of old.values()) { el.remove(); changed = true; }
    if (changed) restore(container, st);
    container.__h = null;   // put() 과 섞여 쓰여도 다음 put() 이 반드시 다시 그리게
  }
  const setText = (el, t) => { if (typeof el === "string") el = byId(el); if (el && el.textContent !== t) el.textContent = t; };

  // ================================================================ 포맷·표기
  const slug = (s) => String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "x";
  const shortUrl = (u) => String(u || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");
  const statusLabel = (s) => L.status[s] || L.overall[s] || L.level[s] || "확인 불가";
  function upt(s) {
    if (!has(s)) return DASH;
    s = Math.max(0, Math.floor(s));
    const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600); const m = Math.floor((s % 3600) / 60);
    if (d) return h ? `${d}일 ${h}시간` : `${d}일`;
    if (h) return m ? `${h}시간 ${m}분` : `${h}시간`;
    if (m) return `${m}분`;
    return "1분 미만";
  }
  const pct = (v, d = 0) => fmt.pct(v, d);
  const n0 = (v) => fmt.num(v, 0);
  const ms = (v) => fmt.ms(v);
  const clock = (ts) => fmt.time(ts, true);
  const dateLong = (ts) => new Date(ts * 1000).toLocaleDateString("ko-KR", { year: "numeric", month: "long", day: "numeric", weekday: "long" });
  const dateShort = (ts) => (ts ? new Date(ts * 1000).toLocaleDateString("ko-KR", { month: "long", day: "numeric" }) : DASH);
  const ago = (ts) => fmt.ago(ts);
  /** 상태 표지: 모양 + 글자 */
  function mk(status, text = true) {
    const t = tone(status);
    const lab = statusLabel(status);
    if (!text) return html`<span class="mk ${t}" role="img" aria-label="${lab}"></span>`;
    return html`<span class="mk ${t}" aria-hidden="true"></span><span class="mk-t ${t}">${lab}</span>`;
  }
  const bare = (t) => html`<span class="mk ${t}" aria-hidden="true"></span>`;
  /** 바깥 링크. safeUrl 을 통과하지 못하면 링크 대신 글자로만 적는다(새 탭에 대시보드가 다시 열리지 않게). */
  function ext(url, inner, fk, cls) {
    const u = P.safeUrl(url);
    if (u === "#") return html`<span class="mono nolink ${cls || ""}">${inner}</span>`;
    return html`<a href="${u}" class="${cls || ""}" target="_blank" rel="noopener noreferrer" data-fk="${fk}">${inner}</a>`;
  }
  /** 주소: IPv6 는 콜론 뒤에서만 줄을 바꿀 수 있게 <wbr> 을 넣는다. */
  const addr = (a) => {
    const s = String(a == null ? "" : a);
    if (s.indexOf(":") < 0) return s;
    return s.split(":").map((p, i) => (i ? html`:<wbr>${p}` : html`${p}`));
  };
  const addrs = (list, sep = ", ") => (list || []).map((a, i) => html`${i ? sep : ""}<span class="nw-addr">${addr(a)}</span>`);
  /** 전체 상태(ok/warn/crit)를 세 갈래로. 값이 없으면 "확인 불가" — 절대 정상으로 기울지 않는다. */
  function overallOf(status) {
    if (status === "ok" || status === "warn" || status === "crit") {
      return { s: status, t: tone(status), label: L.overall[status], wx: status === "crit" ? "storm" : status === "warn" ? "cloud" : "sun", wxT: status === "crit" ? "폭풍우" : status === "warn" ? "구름 조금" : "맑음" };
    }
    return { s: null, t: "muted", label: "확인 불가", wx: "fog", wxT: "관측 불가" };
  }
  const SRC = {
    system: "시스템", container: "컨테이너", systemd: "systemd", route: "라우트", exposure: "노출", dns: "DNS", cert: "인증서",
    tunnel: "터널", caddy: "Caddy", traffic: "트래픽", tailscale: "Tailscale", hermes: "Hermes", monitor: "모니터",
  };
  const PROBE_LABEL = { internal: "내부 :443", tunnel: "터널 :80", public: "공개 엣지" };
  const DNS_LABEL = { cloudflare: "Cloudflare", tailnet: "Tailnet IP", none: "레코드 없음", other: "기타 주소", error: "조회 실패" };

  // ================================================================ 상태
  const S = {
    snap: null, st: null, lastOk: null,
    hist: { range: "1h", data: null, at: 0, loading: false, hoverT: null, t0: 0, t1: 0 },
    map: { sel: null, width: 0, model: null },
  };

  // ================================================================ 마스트헤드
  const ICON = {
    sun: '<svg class="wx" viewBox="0 0 44 44" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><circle cx="22" cy="22" r="7.5"/><path d="M22 6.5v5M22 32.5v5M6.5 22h5M32.5 22h5M11 11l3.5 3.5M29.5 29.5L33 33M11 33l3.5-3.5M29.5 14.5L33 11"/></svg>',
    cloud: '<svg class="wx" viewBox="0 0 44 44" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="28" cy="15" r="5.5"/><path d="M28 4.5v2.5M37.5 15H40M34.8 8.2l1.8-1.8M21.2 8.2l-1.8-1.8"/><path d="M11.5 35h19a6.5 6.5 0 0 0 .6-13 9 9 0 0 0-17.1 2.6A5.3 5.3 0 0 0 11.5 35z" fill="var(--paper)"/></svg>',
    storm: '<svg class="wx" viewBox="0 0 44 44" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M11.5 28h19a6.5 6.5 0 0 0 .6-13 9 9 0 0 0-17.1 2.6A5.3 5.3 0 0 0 11.5 28z"/><path d="M22.5 29.5l-3.5 6h5l-3 6.5" stroke-width="1.8"/></svg>',
    fog: '<svg class="wx" viewBox="0 0 44 44" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M8 16h28M5 22h34M8 28h28M12 34h20" stroke-dasharray="3 3"/></svg>',
  };
  const STATUS_SIGN = { ok: "●", warn: "▲", crit: "◆", muted: "○" };

  function renderMast(snap, st) {
    const meta = snap.meta || {}; const sm = snap.summary || {}; const sys = snap.system || {};
    const host = sys.host || {};
    const ov = overallOf(sm.status);
    const al = sm.alerts;
    const gen = meta.generated_at || now();

    // 러닝 헤드 + 대표 상태
    put("rh-status", html`<span class="rh-ov ${ov.t}">${bare(ov.t)}${ov.label}</span> · ${clock(gen)}`);

    // 왼쪽 귀: 날씨처럼 읽는 한 줄 판정 — 첫 줄은 상태 색으로 분명히
    const alertBits = al ? [al.crit ? `심각 ${al.crit}` : null, al.warn ? `경고 ${al.warn}` : null, al.info ? `정보 ${al.info}` : null].filter(Boolean) : null;
    const temp = sys.thermal?.cpu_temp_c ?? sys.cpu?.temp_c;
    const fan = sys.thermal?.fan?.rpm;
    const ear = byId("ear-weather");
    if (ear && ear.className !== "ear ear-l ov-" + ov.t) ear.className = "ear ear-l ov-" + ov.t;
    put("ear-weather", html`<span class="k">오늘의 판정</span>${P.raw(ICON[ov.wx])}
      <span><span class="wx-o t-${ov.t}">${STATUS_SIGN[ov.t]} ${ov.label}</span><br><span class="wx-t">${ov.wxT}</span> <span class="dim">${alertBits == null ? "· 경보 집계 없음" : alertBits.length ? "· " + alertBits.join(" · ") : "· 경보 없음"}</span></span>
      <span class="wx-s">기온 ${fmt.temp(temp)} <span class="dim">(CPU)</span> · 바람 ${has(fan) ? n0(fan) + " rpm" : DASH} <span class="dim">(팬)</span></span>`);

    // 오른쪽 귀: 판(= 가동 일수)
    const up = sys.uptime_s;
    const ed = has(up) ? Math.floor(up / 86400) + 1 : null;
    put("ear-edition", html`<span class="k">판 · 가동</span>
      <div class="ed">제 <span class="n">${has(ed) ? n0(ed) : DASH}</span> 판</div>
      <div class="ed-s">가동 ${upt(up)} · 부팅 ${dateShort(sys.boot_time)}</div>
      <div class="ed-s dim">${host.os || ""}${host.kernel ? " · " + host.kernel.split("+")[0] : ""}</div>`);

    // 날짜선
    put("dateline", html`<span><b>${dateLong(gen)}</b> · ${clock(gen)} 발행${st.stale ? html` <span class="t-crit">(지연)</span>` : ""}</span>
      <span>${[meta.host || host.hostname || "host", host.model].filter(Boolean).join(" · ")}</span>
      <span class="dl-status ${ov.t}">전체 상태 ${bare(ov.t)}<b>${ov.label}</b></span>`);

    // 접속 주소
    const acc = (meta.access || []).filter(Boolean);
    put("access", html`<span class="k">접속</span>${acc.length ? acc.map((a, i) => ext(a.url, html`${shortUrl(a.url)}<span class="aud">${L.audience[a.audience] || a.audience || ""}</span>`, "acc:" + i)) : html`<span>${DASH}</span>`}`);
  }

  function renderLive() {
    const st = S.st;
    const live = byId("live"); const rl = byId("rh-live");
    if (!live) return;
    let text; let cls;
    if (!st) { text = S.failed ? "연결 실패 — 다시 시도하는 중" : "연결 중…"; cls = S.failed ? "is-stale" : ""; }
    else if (st.stale) {
      // 스크린리더가 매초 읽지 않도록 절대 시각으로 적는다
      text = `연결 끊김 — ${S.lastOk ? clock(S.lastOk) : "이전"} 자료`;
      cls = "is-stale";
    } else { text = `실시간 · ${P.embed ? 10 : 3}초마다 갱신`; cls = "is-live"; }
    live.className = "live " + cls;
    setText(live.querySelector(".live-t"), text);
    if (rl) {
      rl.className = "rh-live " + cls;
      const t2 = st && !st.stale ? "실시간" : text;
      if (rl.__t !== t2) { rl.innerHTML = '<span class="live-dot" aria-hidden="true"></span><span></span>'; rl.lastChild.textContent = t2; rl.__t = t2; }
    }
    doc.body.classList.toggle("is-stale", !!(st && st.stale));
    const sb = byId("stale-banner");
    if (sb) {
      const on = !!(st && st.stale);
      if (sb.hidden === on) sb.hidden = !on;
      if (on) setText(sb, `연결이 끊겼다 — 화면은 ${S.lastOk ? clock(S.lastOk) : "이전"} 에 받은 자료다. 자동으로 다시 시도하는 중${st.error ? " (" + st.error + ")" : ""}.`);
    }
  }

  // 새 스냅샷을 받을 때마다 한 번만 번지는 고리(합성 전용 transform·opacity, 끝나면 멈춘다).
  // 애니메이션 이름을 번갈아 바꿔 강제 리플로 없이 다시 재생한다.
  let pulseFlip = false;
  function pulseLive() {
    if (P.embed || !S.st || S.st.stale) return;
    pulseFlip = !pulseFlip;
    doc.querySelectorAll(".live-dot").forEach((d) => { d.classList.toggle("pa", pulseFlip); d.classList.toggle("pb", !pulseFlip); });
  }

  // ================================================================ 01 요약
  function renderFront(snap) {
    const sm = snap.summary || {}; const sys = snap.system || {};
    const sv = sm.services || {};
    const svcs = (snap.services || []).filter(Boolean);
    const bad = svcs.filter((s) => s.status === "down" || s.status === "degraded");
    const downs = bad.filter((s) => s.status === "down");
    const degr = bad.filter((s) => s.status === "degraded");
    const alerts = (snap.alerts || []).filter(Boolean);
    const serious = alerts.filter((a) => a.level === "crit" || a.level === "warn").sort((a, b) => (a.level === "crit" ? 0 : 1) - (b.level === "crit" ? 0 : 1));
    const ing = snap.ingress || {}; const tr = ing.traffic || {};
    const t24 = tr.last_24h || {}; const t1 = tr.last_1h || {};
    const ts = snap.tailscale || {};
    const caddy = ing.caddy || {};
    const ov = overallOf(sm.status);
    const al = sm.alerts || null;

    // 헤드라인 — 서비스 상태와 전체 판정이 서로 어긋나지 않게
    const total = sv.total ?? svcs.length;
    const upN = sv.up ?? svcs.filter((s) => s.status === "up").length;
    const names = (arr) => arr.map((s) => s.name).join("·");
    let hed; let deckLead = null;
    if (!total) hed = "확인된 서비스가 없다";
    else if (bad.length) {
      const parts = [];
      if (downs.length) parts.push(`${names(downs.slice(0, 2))}${downs.length > 2 ? ` 외 ${downs.length - 2}곳` : ""} 다운`);
      if (degr.length) parts.push(downs.length ? `저하 ${degr.length}곳` : `${names(degr.slice(0, 2))}${degr.length > 2 ? ` 외 ${degr.length - 2}곳` : ""} 저하`);
      hed = `서비스 ${n0(total)}곳 중 ${n0(upN)}곳 정상 — ${parts.join(" · ")}`;
    } else if (ov.s && ov.s !== "ok" && serious.length) {
      hed = `${ov.s === "crit" ? "장애" : "주의"} — ${serious[0].message}${serious.length > 1 ? ` 외 ${serious.length - 1}건` : ""}`;
      deckLead = `서비스 ${n0(total)}곳은 모두 정상 가동 중이다.`;
    } else if (!ov.s) {
      hed = `서비스 ${n0(total)}곳 중 ${n0(upN)}곳 정상 — 전체 판정은 확인 불가`;
    } else if (ov.s !== "ok") {
      hed = `서비스 ${n0(total)}곳 정상 · 전체 판정 ${ov.label}`;
    } else hed = `서비스 ${n0(total)}곳 모두 정상 가동`;
    const restSerious = deckLead ? serious.slice(1) : serious;
    const deckAlerts = restSerious.length
      ? restSerious.slice(0, 2).map((a) => a.message).join(" · ") + (restSerious.length > 2 ? ` 외 ${restSerious.length - 2}건` : "")
      : null;
    const infoN = alerts.length - serious.length;
    const deck = [deckLead, deckAlerts, !deckAlerts && !deckLead ? (alerts.length ? `경고 없이 참고 사항 ${alerts.length}건만 남았다.` : "특기할 사항 없이 모든 지표가 평온하다.") : deckLead && infoN ? `참고 사항 ${infoN}건은 오른쪽 난에.` : null].filter(Boolean).join(" ");
    const kicker = ov.s === "ok" ? html`<span class="kick-mark ok">${STATUS_SIGN.ok}</span>오늘의 머리기사 · 모두 정상`
      : ov.s === "warn" ? html`<span class="kick-mark warn">${STATUS_SIGN.warn}</span>주의 필요`
      : ov.s === "crit" ? html`<span class="kick-mark crit">${STATUS_SIGN.crit}</span>장애`
      : html`<span class="kick-mark muted">${STATUS_SIGN.muted}</span>전체 상태 확인 불가`;

    // 리드 문단
    const cat = {}; for (const c of (snap.categories || []).filter(Boolean)) cat[c.id] = c;
    const unknown = (t24.hosts || []).filter((h) => h && h.known === false);
    const tunnelReq = (t24.by_via || {}).tunnel;
    const thr = sys.thermal?.throttle;
    const mem = sys.memory || {};
    const days = has(sys.uptime_s) ? Math.floor(sys.uptime_s / 86400) : null;
    const p1 = html`<p>이 기기는 부팅 후 ${has(days) ? html`<b>${n0(days)}일째</b>` : "한동안"} 가동 중이다. 공개 서비스 <b>${n0(cat.public?.count ?? sm.public)}곳</b>과 Tailnet 전용 <b>${n0(cat.tailnet?.count ?? sm.tailnet)}곳</b>이 Caddy ${caddy.version || ""} 뒤에서 응답하고, 호스트 서버 <b>${n0(cat.host?.count ?? sm.host)}곳</b>이 직접 포트를 열어 두었다.${cat.hermes?.count ? html` Hermes 는 게이트웨이와 이 기기 안의 컨테이너·프로세스를 합쳐 <b>${n0(cat.hermes.count)}곳</b>이다.` : ""}</p>`;
    // 미등록 호스트: 정말 모두 거절됐는지 상태 코드로 확인한다
    let unkLine = "";
    if (unknown.length) {
      const hn = (h) => (h.host ? h.host : "(호스트 헤더 없음)");
      const tot = sum(unknown.map((h) => h.requests));
      const rej = sum(unknown.map((h) => (h.errors_4xx || 0) + (h.errors_5xx || 0)));
      const served = unknown.filter((h) => (h.requests || 0) > (h.errors_4xx || 0) + (h.errors_5xx || 0));
      const top2 = unknown.slice().sort((a, b) => (b.requests || 0) - (a.requests || 0)).slice(0, 2).map(hn).join(", ");
      const shown = html`<b>${top2}</b>${unknown.length > 2 ? ` 등 ${unknown.length}곳으로` : "로"}`;
      unkLine = rej >= tot
        ? html` 등록되지 않은 호스트 ${shown} ${n0(tot)}건이 들어와 모두 거절됐다.`
        : html` 등록되지 않은 호스트 ${shown} ${n0(tot)}건이 들어왔고, 그중 <b class="t-warn">${n0(tot - rej)}건은 거절되지 않고 응답을 받았다</b>(${served.slice(0, 4).map(hn).join(", ")}${served.length > 4 ? ` 외 ${served.length - 4}곳` : ""}).`;
    }
    const p2 = html`<p>지난 24시간 동안 Caddy는 요청 <b>${n0(t24.requests ?? sm.requests_24h)}건</b>(최근 1시간 ${n0(t1.requests ?? sm.requests_1h)}건)을 처리했고, 그중 ${n0(tunnelReq)}건이 Cloudflare 터널로 들어왔다.${unkLine} Tailnet 피어는 ${n0(ts.peers_online)}/${n0(ts.peers_total)}대가 온라인이다.</p>`;
    const thrNow = thr?.now || []; const thrBoot = thr?.since_boot || [];
    const thrLine = !thr ? "스로틀링 기록은 확인할 수 없다."
      : thrNow.length ? html`<b class="t-crit">지금 스로틀링 중이다(${thrNow.join(", ")}).</b>`
      : thrBoot.length ? html`<span class="t-warn">부팅 후 스로틀링 기록: ${thrBoot.join(", ")}.</span>`
      : thr.ok === false ? `스로틀 상태 ${thr.raw || DASH}.`
      : "부팅 후 스로틀링은 한 번도 없었다.";
    const p3 = html`<p>하드웨어는 CPU <b>${pct(sys.cpu?.usage_pct)}</b>(${fmt.temp(sys.thermal?.cpu_temp_c ?? sys.cpu?.temp_c)}), 메모리 <b>${pct(mem.usage_pct)}</b>, 스왑 ${pct(mem.swap_pct, 1)}, 보드 전력 ${fmt.watts(sys.power?.total_w)}다. ${thrLine}</p>`;

    // 핵심 숫자
    const ct = sm.containers || {}; const ls = sm.listeners || {};
    const peers = ts.peers || [];
    const direct = peers.filter((p) => p.connection === "direct").length;
    const relay = peers.filter((p) => p.connection === "relay").length;
    const kf = (k, v, s, key) => html`<div class="kf" data-kf="${key}"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`;
    // "이름 값" 짝은 한 덩어리로 — 줄은 · 에서만 바뀐다
    const pairs = (arr) => arr.map((p, i) => html`${i ? html`<span class="sep"> · </span>` : ""}<span class="pair">${p}</span>`);
    const figs = html`<div class="keyfigs">
      ${kf("서비스 정상", html`${n0(upN)}<small>/${n0(total)}</small>`, pairs([`저하 ${n0(sv.degraded)}`, `다운 ${n0(sv.down)}`]), "svc")}
      ${kf("요청 · 24시간", n0(t24.requests ?? sm.requests_24h), pairs([`최근 1시간 ${n0(t1.requests ?? sm.requests_1h)}건`]), "req")}
      ${kf("컨테이너 가동", html`${n0(ct.running)}<small>/${n0(ct.total)}</small>`, pairs([`healthy ${n0(ct.healthy)}`, `이상 ${n0(ct.unhealthy)}`]), "ctr")}
      ${kf("열린 TCP 포트", n0(ls.total), pairs([`LAN ${n0(ls.lan)}`, `Tailnet ${n0(ls.tailnet_only)}`, `로컬 ${n0(ls.loopback)}`]), "ports")}
      ${kf("Tailnet 피어", html`${n0(ts.peers_online)}<small>/${n0(ts.peers_total)}</small>`, pairs([`직접 ${n0(direct)}`, `릴레이 ${n0(relay)}`]), "ts")}
      ${kf("경보", al ? html`<span class="${al.crit ? "t-crit" : al.warn ? "t-warn" : ""}">${n0((al.crit || 0) + (al.warn || 0))}</span>` : DASH, al ? pairs([`심각 ${n0(al.crit)}`, `경고 ${n0(al.warn)}`, `정보 ${n0(al.info)}`]) : "집계 없음", "al")}
    </div>`;

    // 박스 스코어(분류별)
    const cats = (snap.categories || []).filter(Boolean);
    const z = (v) => (v ? n0(v) : html`<span class="zero">0</span>`);
    const box = html`<div class="boxscore"><table>
      <caption>분류별 현황<span>누르면 해당 지면으로</span></caption>
      <thead><tr><th scope="col">분류</th><th scope="col" class="num">계</th><th scope="col" class="num">정상</th><th scope="col" class="num">저하</th><th scope="col" class="num">다운</th><th scope="col" class="num">불명</th></tr></thead>
      <tbody>${cats.map((c) => html`<tr><th scope="row"><a href="${"#cat-" + slug(c.id)}" data-fk="box:${c.id}">${c.label || L.category[c.id] || c.id}</a><span class="cat-desc">${c.desc || ""}</span></th>
        <td class="num">${n0(c.count)}</td><td class="num">${z(c.up)}</td><td class="num ${c.degraded ? "t-warn" : ""}">${z(c.degraded)}</td><td class="num ${c.down ? "t-crit" : ""}">${z(c.down)}</td><td class="num">${z(c.unknown)}</td></tr>`)}</tbody>
      <tfoot><tr><th scope="row">합계</th><td class="num">${n0(sum(cats.map((c) => c.count)))}</td><td class="num">${n0(sum(cats.map((c) => c.up)))}</td><td class="num">${n0(sum(cats.map((c) => c.degraded)))}</td><td class="num">${n0(sum(cats.map((c) => c.down)))}</td><td class="num">${n0(sum(cats.map((c) => c.unknown)))}</td></tr></tfoot>
    </table></div>`;

    put("front-hed", html`<p class="kicker ${ov.t}">${kicker}</p><h3 class="hed ${ov.s && ov.s !== "ok" ? "hed-" + ov.t : ""}">${hed}</h3>${deck ? html`<p class="deck">${deck}</p>` : ""}`);
    put("front-lede", html`${p1}${p2}${p3}`);
    put("front-figs", figs);
    put("front-box", box);
    setText("dek-summary", `1면 · ${clock(snap.meta?.generated_at)} 기준`);

    // 주요 사항(경보)
    setText("notes-count", alerts.length ? `${alerts.length}건` : "");
    if (!alerts.length) {
      put("alerts", html`<p class="calm"><span class="calm-mk" aria-hidden="true">✓</span>특기할 사항 없음.<span>모든 서비스와 하드웨어 지표가 평온하다. 새 경보가 생기면 이 난에 가장 먼저 실린다.</span></p>`);
    } else {
      put("alerts", html`<ol class="notes-list">${alerts.map((a) => html`<li class="note ${tone(a.level)}">
        <p class="note-h">${bare(tone(a.level))}<span class="lvl">${L.level[a.level] || a.level}</span><span class="src">${SRC[a.source] || a.source || ""}${a.target ? " · " + a.target : ""}</span></p>
        <p class="note-m">${a.message}</p></li>`)}</ol>`);
    }
  }

  // ================================================================ 02 하드웨어
  function sparkSvg(vals, opts = {}) {
    const W = 200; const H = 30;
    const v = (vals || []).map((x) => (has(x) ? +x : null));
    const fin = v.filter((x) => x != null);
    if (!fin.length) return html`<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"></svg>`;
    const o = { min: opts.min, max: opts.max };
    if (o.min == null) o.min = Math.min(...fin);
    if (o.max == null) o.max = Math.max(...fin);
    if (o.max - o.min < (opts.minSpan || 0)) o.max = o.min + opts.minSpan;
    const line = P.sparkPath(v, W, H, o);
    const area = P.sparkPath(v, W, H, { ...o, area: true });
    let second = "";
    if (opts.second) second = P.sparkPath(opts.second.map((x) => (has(x) ? +x : null)), W, H, o);
    return html`<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><path class="a" d="${area}"/><path class="l" d="${line}"/>${second ? html`<path class="l2 mult-l2" d="${second}" style="fill:none;stroke:var(--ink-2);stroke-width:1.1;stroke-dasharray:3 2.5;vector-effect:non-scaling-stroke"/>` : ""}</svg>`;
  }
  const niceMax = (v) => {
    if (!(v > 0)) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    const m = v / e;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * e;
  };

  function renderHardware(snap) {
    const sys = snap.system || {}; const sp = snap.spark || {};
    const cpu = sys.cpu || {}; const gpu = sys.gpu || {}; const mem = sys.memory || {};
    const th = sys.thermal || {}; const pw = sys.power || {}; const net = sys.network || {};
    const host = sys.host || {}; const dio = sys.disk_io || {};
    const disks = sys.disks || [];
    const root = disks.find((d) => d.mount === "/") || disks[0] || {};
    const thr = th.throttle || {};

    setText("dek-hw", `${host.model || "호스트"} · ${n0(host.cores || cpu.cores)}코어 ${fmt.mhz(host.cpu_max_mhz || cpu.max_mhz)} · ${host.gpu_name || gpu.name || "GPU"}`);

    const sparkCap = (lab) => html`<div class="spark-cap"><span>${lab}</span><span>지금</span></div>`;
    const fig = (key, label, tag, value, unit, cls, sub, extra, spark) => ({ k: key, h: html`<div class="fig" data-key="${key}">
      <div class="k"><span>${label}</span><span class="ftag">${tag}</span></div>
      <div class="v ${cls || ""}">${value}${unit ? html`<span class="u">${unit}</span>` : ""}</div>
      <div class="s">${sub}</div>${extra || ""}${spark || ""}</div>` });

    const pcs = cpu.per_core_pct || [];
    const cores = pcs.map((v, i) => html`<div class="core"><div class="bar"><i class="${P.loadTone(v) === "ok" ? "" : P.loadTone(v)}" style="height:${clamp(+v || 0, 0, 100).toFixed(1)}%"></i></div><span class="cv"><span class="ci">${i}</span>${has(v) ? fmt.num(v, 0) + "%" : DASH}</span></div>`);
    const coresLabel = "코어별 사용률: " + (pcs.map((v, i) => `${i}번 ${pct(v)}`).join(", ") || DASH);
    const qs = gpu.queues || {};
    const QN = [["bin", "bin"], ["render", "rdr"], ["tfu", "tfu"], ["csd", "csd"], ["cache_clean", "cc"], ["cpu", "cpu"]];
    const qbars = QN.map(([k, lab]) => html`<div class="qbar" title="${k} ${pct(qs[k], 1)}"><div class="bar"><i style="height:${clamp(+qs[k] || 0, 0, 100).toFixed(1)}%"></i></div>${lab}</div>`);
    const qLabel = "V3D 대기열별 점유: " + QN.map(([k]) => `${k} ${pct(qs[k], 1)}`).join(", ");
    const memU = mem.total ? (mem.used / mem.total) * 100 : 0;
    const memC = mem.total ? ((mem.cached || 0) / mem.total) * 100 : 0;
    const memB = mem.total ? ((mem.buffers || 0) / mem.total) * 100 : 0;
    const swapAlert = (snap.alerts || []).filter(Boolean).find((a) => a.key === "system:swap" || a.target === "swap");
    const swapT = swapAlert ? (swapAlert.level === "info" ? "ok" : tone(swapAlert.level)) : has(mem.swap_pct) && mem.swap_pct >= 90 && mem.usage_pct >= 85 ? "warn" : "ok";
    const tempC = th.cpu_temp_c ?? cpu.temp_c;
    const tempT = P.loadTone(tempC, 70, 80);
    const lan = (net.interfaces || []).filter((i) => i && (i.kind === "wired" || i.kind === "wifi"));
    const wifi = lan.find((i) => i.kind === "wifi" && i.wifi);
    const netTotal = has(net.rx_bps) || has(net.tx_bps) ? (net.rx_bps || 0) + (net.tx_bps || 0) : null;
    const netStr = fmt.bps(netTotal).split(" ");
    const thrKnown = !!th.throttle;
    const thrNow = thr.now || []; const thrBoot = thr.since_boot || [];
    const thrTag = !thrKnown ? DASH : thrNow.length ? "스로틀 발생" : thrBoot.length ? "부팅 후 스로틀 기록" : thr.raw != null || thr.ok === true ? "스로틀 없음" : DASH;
    const upD = has(sys.uptime_s) ? Math.floor(sys.uptime_s / 86400) : null;
    const upH = has(sys.uptime_s) ? Math.floor((sys.uptime_s % 86400) / 3600) : null;
    const procs = sys.processes || {};

    list("hw-figs", [
      fig("cpu", "CPU", `${n0(cpu.cores || host.cores)}코어 · ${cpu.governor || ""}`, fmt.num(cpu.usage_pct, 0), "%", P.loadTone(cpu.usage_pct, 85, 95) === "ok" ? "" : P.loadTone(cpu.usage_pct, 85, 95),
        html`<b>${fmt.mhz(cpu.freq_mhz)}</b> · 부하 ${(cpu.load || []).map((x) => fmt.num(x, 2)).join(" / ") || DASH}`,
        html`<div class="cores" role="img" aria-label="${coresLabel}">${cores}</div>`, html`${sparkSvg(sp.cpu, { min: 0, max: 100 })}${sparkCap("5분")}`),
      fig("gpu", "GPU", "V3D", fmt.num(gpu.usage_pct, 0), "%", "",
        html`<b>${fmt.mhz(gpu.freq_mhz ?? sys.clocks_mhz?.v3d)}</b> · 작업 ${fmt.num(gpu.jobs_per_s, 1)}/s${gpu.available === false ? " · 수집 불가" : ""}`,
        html`<div class="qbars" role="img" aria-label="${qLabel}">${qbars}</div>`, html`${sparkSvg(sp.gpu, { min: 0, max: 100 })}${sparkCap("5분")}`),
      fig("mem", "메모리", fmt.bytes(mem.total), fmt.num(mem.usage_pct, 0), "%", P.loadTone(mem.usage_pct, 80, 92) === "ok" ? "" : P.loadTone(mem.usage_pct, 80, 92),
        html`<b>${fmt.bytes(mem.used)}</b> 사용 · 캐시 ${fmt.bytes(mem.cached)}<br>스왑 <b class="${swapT === "ok" ? "" : "t-" + swapT}">${pct(mem.swap_pct, 1)}</b> <span class="dim">(${fmt.bytes(mem.swap_used)} / ${fmt.bytes(mem.swap_total)})</span>`,
        html`<div class="membar" role="img" aria-label="사용 ${pct(memU)}, 캐시 ${pct(memC)}, 버퍼 ${pct(memB)}"><i class="u" style="width:${memU.toFixed(1)}%"></i><i class="c" style="width:${memC.toFixed(1)}%"></i><i class="b" style="width:${memB.toFixed(1)}%"></i></div>`,
        html`${sparkSvg(sp.mem, { min: 0, max: 100 })}${sparkCap("5분 · 메모리")}`),
      fig("disk", "저장장치", root.mount ? `${root.mount} · ${root.fs || ""}` : "", fmt.num(root.usage_pct, 0), "%", P.loadTone(root.usage_pct, 80, 92) === "ok" ? "" : P.loadTone(root.usage_pct, 80, 92),
        html`<b>${fmt.bytes(root.used)}</b> / ${fmt.bytes(root.total)} · 여유 ${fmt.bytes(root.free)}<br>읽기 ${fmt.bps(dio.read_bps)} · 쓰기 ${fmt.bps(dio.write_bps)}`,
        html`<div class="meter"><i class="${P.loadTone(root.usage_pct, 80, 92)}" style="width:${clamp(+root.usage_pct || 0, 0, 100)}%"></i></div>`,
        html`${sparkSvg(sp.dw, { min: 0, second: sp.dr, minSpan: 1024 })}${sparkCap("5분 · 쓰기/읽기")}`),
      fig("net", "네트워크", "LAN 합계", netStr[0], netStr.slice(1).join(" "), "",
        html`수신 <b>${fmt.bps(net.rx_bps)}</b> · 송신 <b>${fmt.bps(net.tx_bps)}</b><br>Tailnet ↓${fmt.bps(net.tailnet_rx_bps)} ↑${fmt.bps(net.tailnet_tx_bps)}${wifi ? html` · <span class="nw">Wi-Fi ${fmt.num(wifi.wifi.signal_dbm, 0)} dBm</span>` : ""}`,
        "", html`${sparkSvg(sp.rx, { min: 0, second: sp.tx, minSpan: 1024 })}${sparkCap("5분 · 수신/송신")}`),
      fig("temp", "온도", html`<span class="${thrNow.length ? "t-crit" : thrBoot.length ? "t-warn" : ""}">${thrTag}</span>`, fmt.num(tempC, 1), "°C", tempT === "ok" ? "" : tempT,
        html`PMIC ${fmt.temp(th.pmic_temp_c)} · 팬 <b>${has(th.fan?.rpm) ? n0(th.fan.rpm) + " rpm" : DASH}</b> (${pct(th.fan?.pct)})`,
        "", html`${sparkSvg(sp.temp, { minSpan: 4 })}${sparkCap("5분")}`),
      fig("pw", "전력", "PMIC 추정", fmt.num(pw.total_w, 2), "W", "",
        html`5V 입력 <b>${has(pw.input_v) ? fmt.num(pw.input_v, 2) + " V" : DASH}</b> · 코어 ${has(pw.core_volts) ? fmt.num(pw.core_volts, 3) + " V" : DASH}`,
        "", html`${sparkSvg(sp.pw, { minSpan: 0.5 })}${sparkCap("5분")}`),
      fig("up", "가동", `부팅 ${dateShort(sys.boot_time)}`, has(upD) ? n0(upD) : DASH, has(upH) ? `일 ${upH}시간` : "", "",
        html`프로세스 <b>${n0(procs.count)}</b> · 스레드 ${n0(procs.threads ?? cpu.threads_total)}<br>iowait ${pct(cpu.iowait_pct, 1)} · 실행 대기 ${n0(cpu.procs_running)}`,
        "", html`${sparkSvg(sp.load, { min: 0, minSpan: 1 })}${sparkCap("5분 · 부하")}`),
    ]);

    // ---- 세부 1: 프로세서·그래픽
    const pcf = cpu.per_core_freq_mhz || [];
    put("hw-cpu", html`<h3 class="sub-h">프로세서 · 그래픽</h3>
      <table class="mini"><thead><tr><th scope="col">코어</th><th scope="col" class="bar-cell">사용률</th><th scope="col" class="num">%</th><th scope="col" class="num">클럭</th></tr></thead>
      <tbody>${(cpu.per_core_pct || []).map((v, i) => html`<tr><td>${i}</td><td class="bar-cell"><div class="hbar"><i class="${P.loadTone(v) === "ok" ? "" : P.loadTone(v)}" style="width:${clamp(+v || 0, 0, 100).toFixed(1)}%"></i></div></td><td class="num">${fmt.num(v, 1)}</td><td class="num">${fmt.mhz(pcf[i])}</td></tr>`)}</tbody></table>
      <dl class="kv" style="margin-top:10px">
        <dt>거버너</dt><dd>${cpu.governor || DASH}</dd>
        <dt>현재 / 최대 클럭</dt><dd>${fmt.mhz(cpu.freq_mhz)} / ${fmt.mhz(cpu.max_mhz || host.cpu_max_mhz)}</dd>
        <dt>부하 1 · 5 · 15분</dt><dd>${(cpu.load || []).map((x) => fmt.num(x, 2)).join(" · ") || DASH}</dd>
        <dt>iowait</dt><dd>${pct(cpu.iowait_pct, 1)}</dd>
        <dt>실행 중 / 스레드</dt><dd>${n0(cpu.procs_running)} / ${n0(cpu.threads_total)}</dd>
        <dt>GPU</dt><dd>${gpu.name || host.gpu_name || DASH} <span class="dim">· ${n0(gpu.mem_mb ?? host.gpu_mem_mb)} MB</span></dd>
        <dt>V3D 사용률 · 클럭</dt><dd>${pct(gpu.usage_pct, 1)} · ${fmt.mhz(gpu.freq_mhz)}</dd>
        <dt>V3D 작업</dt><dd>${fmt.num(gpu.jobs_per_s, 1)} /s</dd>
      </dl>
      <table class="mini"><thead><tr><th scope="col">V3D 대기열</th><th scope="col" class="bar-cell">점유</th><th scope="col" class="num">%</th></tr></thead>
      <tbody>${QN.map(([k]) => html`<tr><td class="mono">${k}</td><td class="bar-cell"><div class="hbar"><i style="width:${clamp(+qs[k] || 0, 0, 100).toFixed(1)}%"></i></div></td><td class="num">${fmt.num(qs[k], 1)}</td></tr>`)}</tbody></table>
      <dl class="kv" style="margin-top:10px">${Object.entries(sys.clocks_mhz || {}).map(([k, v]) => html`<dt>클럭 · ${k}</dt><dd>${fmt.mhz(v)}</dd>`)}</dl>`);

    // ---- 세부 2: 메모리·저장장치·전원 (세 단의 길이를 맞추려고 전원·열을 가운데로)
    const devs = Object.entries(dio.devices || {}).filter(([, v]) => v);
    const rails = (pw.rails || []).filter(Boolean).slice().sort((a, b) => (b.watts || 0) - (a.watts || 0));
    const railRow = (r) => html`<tr><td class="mono">${r.name}</td><td class="num">${fmt.num(r.volts, 3)}</td><td class="num">${fmt.num(r.amps, 3)}</td><td class="num">${fmt.num(r.watts, 3)}</td></tr>`;
    const thrCell = !thrKnown ? html`<span class="dim">${DASH} 확인 불가</span>`
      : thrNow.length || thrBoot.length ? html`<span class="flags">${thrNow.map((f) => html`<span class="flag crit">지금 · ${f}</span>`)}${thrBoot.map((f) => html`<span class="flag warn">부팅 후 · ${f}</span>`)}</span>`
      : thr.ok === false ? html`<span class="t-warn">이상 신호(${thr.raw || DASH})</span>`
      : html`<span class="t-ok">없음 — 저전압·과열 기록 없음</span>`;
    put("hw-mem", html`<h3 class="sub-h">메모리 · 저장장치</h3>
      <dl class="kv">
        <dt>전체</dt><dd>${fmt.bytes(mem.total)}</dd>
        <dt>사용</dt><dd>${fmt.bytes(mem.used)} <span class="dim">(${pct(mem.usage_pct, 1)})</span></dd>
        <dt>사용 가능</dt><dd>${fmt.bytes(mem.available)}</dd>
        <dt>여유(free)</dt><dd>${fmt.bytes(mem.free)}</dd>
        <dt>캐시 · 버퍼</dt><dd>${fmt.bytes(mem.cached)} · ${fmt.bytes(mem.buffers)}</dd>
        <dt>공유</dt><dd>${fmt.bytes(mem.shared)}</dd>
        <dt>스왑</dt><dd><span class="${swapT === "ok" ? "" : "t-" + swapT}">${fmt.bytes(mem.swap_used)} / ${fmt.bytes(mem.swap_total)} (${pct(mem.swap_pct, 1)})</span></dd>
      </dl>
      <table class="mini"><thead><tr><th scope="col">마운트</th><th scope="col" class="bar-cell">사용</th><th scope="col" class="num">사용 / 전체</th></tr></thead>
      <tbody>${disks.filter(Boolean).map((d) => html`<tr><td><span class="mono">${d.mount}</span><br><span class="dim mono brk" style="font-size:11px">${d.device || ""} · ${d.fs || ""}</span></td>
        <td class="bar-cell"><div class="hbar"><i class="${P.loadTone(d.usage_pct, 80, 92) === "ok" ? "" : P.loadTone(d.usage_pct, 80, 92)}" style="width:${clamp(+d.usage_pct || 0, 0, 100)}%"></i></div><span class="dim" style="font-size:11px">${pct(d.usage_pct, 1)}</span></td>
        <td class="num">${fmt.bytes(d.used)}<br><span class="dim">/ ${fmt.bytes(d.total)}</span></td></tr>`)}</tbody></table>
      <table class="mini"><thead><tr><th scope="col">디스크 I/O</th><th scope="col" class="num">읽기</th><th scope="col" class="num">쓰기</th></tr></thead>
      <tbody><tr><td>합계</td><td class="num">${fmt.bps(dio.read_bps)}</td><td class="num">${fmt.bps(dio.write_bps)}</td></tr>
      ${devs.map(([k, v]) => html`<tr><td class="mono">${k}</td><td class="num">${fmt.bps(v?.read_bps)}</td><td class="num">${fmt.bps(v?.write_bps)}</td></tr>`)}</tbody></table>
      <h3 class="sub-h sub-h-2">전원 · 열</h3>
      <dl class="kv">
        <dt>보드 전력</dt><dd>${fmt.watts(pw.total_w)}</dd>
        <dt>5V 입력</dt><dd>${has(pw.input_v) ? fmt.num(pw.input_v, 3) + " V" : DASH}</dd>
        <dt>코어 전압</dt><dd>${has(pw.core_volts) ? fmt.num(pw.core_volts, 4) + " V" : DASH}</dd>
        <dt>CPU · PMIC 온도</dt><dd>${fmt.temp(tempC)} · ${fmt.temp(th.pmic_temp_c)}</dd>
        <dt>팬</dt><dd>${has(th.fan?.rpm) ? n0(th.fan.rpm) + " rpm" : DASH} · ${pct(th.fan?.pct)} <span class="dim">(PWM ${n0(th.fan?.pwm)})</span></dd>
        <dt>스로틀${thrKnown && thr.raw ? html` <span class="mono">(${thr.raw})</span>` : ""}</dt><dd>${thrCell}</dd>
      </dl>
`);

    // ---- 세부 3: 네트워크·시스템
    const ifs = (net.interfaces || []).filter(Boolean);
    const mainIf = ifs.filter((i) => i.kind !== "docker");
    const dockIf = ifs.filter((i) => i.kind === "docker");
    const IFK = { wired: "유선", wifi: "Wi-Fi", tailnet: "Tailnet", docker: "Docker", other: "기타" };
    const ifRow = (i) => html`<tr><td><b class="mono">${i.name}</b><br><span class="dim" style="font-size:11px">${IFK[i.kind] || i.kind}${i.up === false ? " · 내려감" : ""}</span></td>
      <td><span class="mono" style="font-size:11.5px">${(i.ipv4 || []).length ? addrs(i.ipv4) : DASH}</span>${i.wifi ? html`<br><span class="dim" style="font-size:11px">신호 ${fmt.num(i.wifi.signal_dbm, 0)} dBm · 품질 ${pct(i.wifi.quality)}</span>` : i.speed_mbps ? html`<br><span class="dim" style="font-size:11px">${n0(i.speed_mbps)} Mbps</span>` : ""}</td>
      <td class="num">↓${fmt.bps(i.rx_bps)}<br>↑${fmt.bps(i.tx_bps)}</td></tr>`;
    put("hw-net", html`<h3 class="sub-h">네트워크 · 시스템</h3>
      <dl class="kv">
        <dt>LAN 수신 · 송신</dt><dd>${fmt.bps(net.rx_bps)} · ${fmt.bps(net.tx_bps)}</dd>
        <dt>Tailnet 수신 · 송신</dt><dd>${fmt.bps(net.tailnet_rx_bps)} · ${fmt.bps(net.tailnet_tx_bps)}</dd>
        <dt>Docker 수신 · 송신</dt><dd>${fmt.bps(net.docker_rx_bps)} · ${fmt.bps(net.docker_tx_bps)}</dd>
        <dt>LAN IP</dt><dd class="mono">${(net.lan_ips || []).length ? addrs(net.lan_ips) : DASH}</dd>
        <dt>Tailnet IP</dt><dd class="mono">${net.tailnet_ip ? addr(net.tailnet_ip) : DASH}</dd>
      </dl>
      <table class="mini"><thead><tr><th scope="col">인터페이스</th><th scope="col">주소</th><th scope="col" class="num">수신 / 송신</th></tr></thead>
      <tbody>${mainIf.map(ifRow)}</tbody></table>
      ${dockIf.length ? html`<details class="fold" data-dk="ifs-docker"><summary data-fk="sum:ifs-docker">Docker 브리지 <span class="fold-n">${dockIf.length}개 · 켜짐 ${dockIf.filter((i) => i.up).length}</span></summary>
        <table class="mini"><tbody>${dockIf.map(ifRow)}</tbody></table></details>` : ""}
      <dl class="kv" style="margin-top:14px">
        <dt>모델</dt><dd>${host.model || DASH}</dd>
        <dt>OS · 커널</dt><dd>${host.os || DASH}<br><span class="dim mono brk">${host.kernel || ""} · ${host.arch || ""}</span></dd>
        <dt>부팅 · 가동</dt><dd>${fmt.datetime(sys.boot_time)} · ${upt(sys.uptime_s)}</dd>
      </dl>
      ${rails.length ? html`<table class="mini rails"><thead><tr><th scope="col">전원 레일 <span class="dim">(PMIC)</span></th><th scope="col" class="num">V</th><th scope="col" class="num">A</th><th scope="col" class="num">W</th></tr></thead>
        <tbody>${rails.slice(0, 5).map(railRow)}</tbody></table>
        ${rails.length > 5 ? html`<details class="fold" data-dk="rails"><summary data-fk="sum:rails">나머지 레일 <span class="fold-n">${rails.length - 5}개</span></summary><table class="mini"><tbody>${rails.slice(5).map(railRow)}</tbody></table></details>` : ""}` : ""}`);

    // ---- 상위 프로세스
    const pr = sys.processes || {};
    const OWN = { container: "컨테이너", unit: "유닛", "user-unit": "사용자 유닛", scope: "스코프", unknown: "?" };
    const ptable = (rows, title, by, key) => html`<div class="scroll" data-sk="${key}"><table class="mini procs"><caption class="sr">${title}</caption>
      <colgroup><col class="c-n"><col class="c-p"><col class="c-c"><col class="c-r"><col class="c-o"><col class="c-m"></colgroup>
      <thead><tr><th scope="col">${title}</th><th scope="col" class="num">PID</th><th scope="col" class="num">CPU</th><th scope="col" class="num">RSS</th><th scope="col">소유</th><th scope="col">명령</th></tr></thead>
      <tbody>${(rows || []).slice(0, 8).map((p) => html`<tr><td class="pn" title="${p.name} (${p.user || ""})"><b>${p.name}</b> <span class="dim" style="font-size:11px">${p.user || ""}</span></td><td class="num dim">${p.pid}</td><td class="num ${by === "cpu" ? "" : "dim"}">${pct(p.cpu_pct, 1)}</td><td class="num ${by === "mem" ? "" : "dim"}">${fmt.bytes(p.rss)}</td><td class="own" title="${p.owner || ""}"><span class="dim">${OWN[p.owner_kind] || ""}</span> <span class="mono">${p.owner || ""}</span></td><td class="cmd" title="${p.cmd || ""}">${p.cmd || ""}</td></tr>`)}</tbody></table></div>`;
    put("hw-procs", html`<h3 class="sub-h">상위 프로세스 <span class="sub-note">전체 ${n0(pr.count)}개 · 스레드 ${n0(pr.threads)} · CPU 는 코어 하나 기준(최대 ${n0((cpu.cores || 4) * 100)}%)</span></h3>
      <div class="proc-grid">${ptable(pr.top_cpu, "CPU 상위", "cpu", "procs-cpu")}${ptable(pr.top_mem, "메모리 상위", "mem", "procs-mem")}</div>`);
  }

  // ================================================================ 02 시계열 (작은 그림들)
  const fB = (v) => fmt.bps(v);
  const yPct = (v) => (v ? Math.round(v) + "%" : "0");
  const yB = (v) => (!v ? "0" : v >= 1048576 ? +(v / 1048576).toFixed(v >= 10485760 ? 0 : 1) + "M" : +(v / 1024).toFixed(v >= 10240 ? 0 : 1) + "K");
  const sysOf = (snap) => snap?.system || {};
  const PANELS = [
    { id: "cpu", label: "CPU", sub: "사용률", s: [["cpu", ""]], min: 0, max: 100, f: (v) => fmt.pct(v, 1), y: yPct, live: (x) => [sysOf(x).cpu?.usage_pct] },
    { id: "gpu", label: "GPU", sub: "V3D 사용률", s: [["gpu", ""]], min: 0, max: 100, f: (v) => fmt.pct(v, 1), y: yPct, live: (x) => [sysOf(x).gpu?.usage_pct] },
    { id: "mem", label: "메모리", sub: "사용률", s: [["mem", ""]], min: 0, max: 100, f: (v) => fmt.pct(v, 1), y: yPct, live: (x) => [sysOf(x).memory?.usage_pct] },
    { id: "swap", label: "스왑", sub: "사용률", s: [["swap", ""]], min: 0, max: 100, f: (v) => fmt.pct(v, 1), y: yPct, live: (x) => [sysOf(x).memory?.swap_pct] },
    { id: "temp", label: "온도", sub: "CPU · °C", s: [["temp", ""]], auto: "temp", f: (v) => fmt.temp(v), y: (v) => Math.round(v) + "°", live: (x) => [sysOf(x).thermal?.cpu_temp_c ?? sysOf(x).cpu?.temp_c] },
    { id: "load", label: "부하", sub: "1분 평균", s: [["load", ""]], min: 0, floorMax: 4, f: (v) => fmt.num(v, 2), y: (v) => String(+(+v).toFixed(1)), live: (x) => [(sysOf(x).cpu?.load || [])[0]] },
    { id: "mhz", label: "CPU 클럭", sub: "평균 · GHz", s: [["mhz", ""]], min: 0, max: 2400, f: (v) => fmt.mhz(v), y: (v) => (v ? (v / 1000).toFixed(1) : "0"), live: (x) => [sysOf(x).cpu?.freq_mhz] },
    { id: "fan", label: "팬", sub: "회전수 · rpm", s: [["fan", ""]], min: 0, floorMax: 1000, f: (v) => (has(v) ? n0(v) + " rpm" : DASH), y: (v) => (v >= 1000 ? +(v / 1000).toFixed(1) + "k" : String(Math.round(v))), live: (x) => [sysOf(x).thermal?.fan?.rpm] },
    { id: "pw", label: "보드 전력", sub: "PMIC 추정 · W", s: [["pw", ""]], min: 0, floorMax: 5, f: (v) => fmt.watts(v), y: (v) => String(+(+v).toFixed(1)), live: (x) => [sysOf(x).power?.total_w] },
    { id: "lan", label: "LAN", sub: "— 수신 ┄ 송신", s: [["rx", "↓"], ["tx", "↑"]], min: 0, floorMax: 10240, unit: 1024, f: fB, y: yB, peak: true, live: (x) => [sysOf(x).network?.rx_bps, sysOf(x).network?.tx_bps] },
    { id: "tn", label: "Tailnet", sub: "— 수신 ┄ 송신", s: [["trx", "↓"], ["ttx", "↑"]], min: 0, floorMax: 10240, unit: 1024, f: fB, y: yB, peak: true, live: (x) => [sysOf(x).network?.tailnet_rx_bps, sysOf(x).network?.tailnet_tx_bps] },
    { id: "disk", label: "디스크", sub: "— 쓰기 ┄ 읽기", s: [["dw", "쓰기"], ["dr", "읽기"]], min: 0, floorMax: 10240, unit: 1024, f: fB, y: yB, peak: true, live: (x) => [sysOf(x).disk_io?.write_bps, sysOf(x).disk_io?.read_bps] },
  ];
  const VW = 600; const VH = 100;

  function buildPanels() {
    const grid = byId("hist-grid");
    if (!grid || grid.__built) return;
    grid.__built = true;
    // 정적 설정 문자열만으로 뼈대를 만든다(데이터 없음).
    grid.innerHTML = PANELS.map((p) => `<figure class="mult" data-p="${p.id}">
      <figcaption class="cap"><span class="lab">${p.label}<small>${p.sub}</small></span><span class="val" data-v></span></figcaption>
      <div class="plot"><span class="ylab top" data-yt aria-hidden="true"></span><span class="ylab bot" data-yb aria-hidden="true"></span>
        <div class="pa"><svg viewBox="0 0 ${VW} ${VH}" preserveAspectRatio="none" aria-hidden="true">
          <g class="grid"><line x1="0" y1="0" x2="${VW}" y2="0"/><line x1="0" y1="${VH / 2}" x2="${VW}" y2="${VH / 2}"/><line class="base" x1="0" y1="${VH}" x2="${VW}" y2="${VH}"/></g>
          <g data-g></g><line class="cur" x1="0" y1="0" x2="0" y2="${VH}"/></svg>
        <span class="empty" data-e hidden>자료 없음</span></div></div>
      <div class="xax"><span data-x0></span><span data-x1></span></div><span class="sr" data-sum></span></figure>`).join("");
    for (const p of PANELS) {
      const f = grid.querySelector(`[data-p="${p.id}"]`);
      p.el = { fig: f, v: f.querySelector("[data-v]"), g: f.querySelector("[data-g]"), cur: f.querySelector(".cur"), yt: f.querySelector("[data-yt]"), yb: f.querySelector("[data-yb]"), e: f.querySelector("[data-e]"), x0: f.querySelector("[data-x0]"), x1: f.querySelector("[data-x1]"), sum: f.querySelector("[data-sum]"), svg: f.querySelector("svg") };
    }
    let raf = 0;
    const move = (ev) => {
      const plot = ev.target.closest && ev.target.closest(".mult");
      if (!plot || !S.hist.data) return;
      const svg = plot.querySelector("svg");
      const r = svg.getBoundingClientRect();
      if (!r.width) return;
      const f = clamp((ev.clientX - r.left) / r.width, 0, 1);
      S.hist.hoverT = S.hist.t0 + f * (S.hist.t1 - S.hist.t0);
      if (!raf) raf = requestAnimationFrame(() => { raf = 0; readouts(); });
    };
    grid.addEventListener("pointermove", move);
    grid.addEventListener("pointerdown", move);
    grid.addEventListener("pointerleave", () => { S.hist.hoverT = null; if (!raf) raf = requestAnimationFrame(() => { raf = 0; readouts(); }); });
    // 폭이 바뀌면 묶음(bucket) 수가 달라지므로 다시 그린다
    if ("ResizeObserver" in window) {
      let rt = 0;
      new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => drawHist(), 180); }).observe(grid);
    }

    doc.querySelectorAll(".seg button[data-range]").forEach((b) => b.addEventListener("click", () => {
      const r = b.getAttribute("data-range");
      if (r === S.hist.range) return;
      S.hist.range = r;
      doc.querySelectorAll(".seg button[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
      try { localStorage.setItem("ledger.range", r); } catch (e) { /* 저장 불가 무시 */ }
      S.hist.data = null; S.hist.at = 0;
      setText("hist-note", "불러오는 중…");
      loadHist(true);
    }));
  }

  /** 시계열을 화면 2px 마다 한 묶음으로 줄인다: 묶음마다 최소·최대·평균. 빈 묶음은 선을 끊는다. */
  function bucketize(ts, vs, t0, t1, nb) {
    const span = t1 - t0 || 1;
    const B = new Array(nb).fill(null);
    if (!vs) return B;
    for (let i = 0; i < ts.length; i++) {
      const t = ts[i]; const v = vs[i];
      if (t == null || v == null || !isFinite(v) || t < t0 - 1 || t > t1 + 1) continue;
      const k = clamp(Math.floor(((t - t0) / span) * nb), 0, nb - 1);
      const b = B[k];
      if (!b) B[k] = { mn: +v, mx: +v, s: +v, c: 1 };
      else { if (v < b.mn) b.mn = +v; if (v > b.mx) b.mx = +v; b.s += +v; b.c++; }
    }
    return B;
  }
  function runsOf(B) {
    const out = []; let cur = null;
    B.forEach((b, k) => { if (!b) { cur = null; return; } if (!cur) { cur = []; out.push(cur); } cur.push([k, b]); });
    return out;
  }

  function drawHist(force) {
    const h = S.hist.data;
    if (!h) return;
    const first = PANELS[0].el;
    const pxW = first && first.svg ? first.svg.getBoundingClientRect().width : 0;
    const ts = h.t || [];
    const span = S.hist.range === "24h" ? 86400 : 3600;
    const iv = h.interval_s || (span === 3600 ? 5 : 60);
    // 묶음 하나가 표본 간격의 두 배보다 짧으면 빈 묶음이 생겨 선이 끊기므로 상한을 둔다
    const nb = Math.max(24, Math.min(Math.floor((pxW || 240) / 2), Math.floor(span / (iv * 2))));
    const key = `${S.hist.at}|${S.hist.range}|${nb}`;
    if (!force && S.hist.drawn === key) return;
    S.hist.drawn = key;
    // 끝점은 마지막 기록 시각에 고정한다 — 스냅샷마다 축이 밀려 다시 그리지 않게
    const t1 = ts.length ? ts[ts.length - 1] : (S.snap?.meta?.generated_at || now());
    const t0 = t1 - span;
    S.hist.t0 = t0; S.hist.t1 = t1;
    const x0 = S.hist.range === "24h" ? fmt.datetime(t0) : fmt.time(t0, false);
    const firstT = ts.length ? ts[0] : t1;
    const ndW = clamp(((firstT - t0) / span) * VW, 0, VW);
    const nd = ndW > 6 ? `<rect class="nd" x="0" y="0" width="${ndW.toFixed(1)}" height="${VH}"/>` : "";
    const bx = (k) => ((k + 0.5) / nb) * VW;
    const half = (0.5 / nb) * VW;
    for (const p of PANELS) {
      const e = p.el; if (!e) continue;
      const vals = [];
      for (const [k] of p.s) for (const v of h[k] || []) if (has(v)) vals.push(+v);
      let lo = p.min; let hi = p.max;
      if (p.auto === "temp") {
        lo = vals.length ? Math.floor((Math.min(...vals) - 3) / 5) * 5 : 30;
        hi = vals.length ? Math.ceil((Math.max(...vals) + 3) / 5) * 5 : 80;
      } else if (hi == null) {
        const u = p.unit || 1;
        hi = niceMax((Math.max(p.floorMax || 1, ...(vals.length ? vals : [0])) * 1.08) / u) * u;
      }
      if (lo == null) lo = 0;
      p.lo = lo; p.hi = hi;
      const rng = hi - lo || 1;
      const yv = (v) => (VH - ((clamp(v, lo, hi) - lo) / rng) * VH).toFixed(1);
      let g = nd;
      p.s.forEach(([k], i) => {
        const runs = runsOf(bucketize(ts, h[k], t0, t1, nb));
        if (p.peak) {
          // 송수신·디스크: 묶음의 최대값(순간 폭증이 평균에 묻히지 않게)
          const line = runs.map((r) => (r.length === 1 ? `M${(bx(r[0][0]) - half).toFixed(1)} ${yv(r[0][1].mx)}h${(half * 2).toFixed(1)}` : r.map(([kk, b], j) => (j ? "L" : "M") + bx(kk).toFixed(1) + " " + yv(b.mx)).join(""))).join("");
          if (i === 0) {
            const area = runs.filter((r) => r.length > 1).map((r) => `M${bx(r[0][0]).toFixed(1)} ${VH}` + r.map(([kk, b]) => "L" + bx(kk).toFixed(1) + " " + yv(b.mx)).join("") + `L${bx(r[r.length - 1][0]).toFixed(1)} ${VH}Z`).join("");
            g += `<path class="a" d="${area}"/><path class="l" d="${line}"/>`;
          } else g += `<path class="l2" d="${line}"/>`;
        } else {
          // 한 계열: 최소–최대 띠 + 평균선
          const band = runs.map((r) => {
            const top = r.map(([kk, b], j) => (j ? "L" : "M") + (r.length === 1 ? (bx(kk) - half).toFixed(1) : bx(kk).toFixed(1)) + " " + yv(b.mx)).join("") + (r.length === 1 ? `L${(bx(r[0][0]) + half).toFixed(1)} ${yv(r[0][1].mx)}` : "");
            const bot = r.slice().reverse().map(([kk, b]) => "L" + (r.length === 1 ? (bx(kk) + half).toFixed(1) : bx(kk).toFixed(1)) + " " + yv(b.mn)).join("") + (r.length === 1 ? `L${(bx(r[0][0]) - half).toFixed(1)} ${yv(r[0][1].mn)}` : "");
            return top + bot + "Z";
          }).join("");
          const mean = runs.map((r) => (r.length === 1 ? `M${(bx(r[0][0]) - half).toFixed(1)} ${yv(r[0][1].s / r[0][1].c)}h${(half * 2).toFixed(1)}` : r.map(([kk, b], j) => (j ? "L" : "M") + bx(kk).toFixed(1) + " " + yv(b.s / b.c)).join(""))).join("");
          g += `<path class="band" d="${band}"/><path class="l" d="${mean}"/>`;
        }
      });
      if (e.g.__h !== g) { e.g.innerHTML = g; e.g.__h = g; }
      setText(e.yt, p.y(hi));
      setText(e.yb, p.y(lo));
      e.e.hidden = vals.length > 0;
      setText(e.x0, x0);
      setText(e.x1, "지금");
      if (vals.length) setText(e.sum, `${p.label} ${p.sub}: 기간 최소 ${p.f(Math.min(...vals))}, 최대 ${p.f(Math.max(...vals))}`);
    }
    // 자료가 얼마나 쌓였는지
    const cov = ts.length ? (t1 - ts[0]) / span : 0;
    const rangeLab = S.hist.range === "24h" ? "24시간 · 1분 간격" : "1시간 · 5초 간격";
    let note = `${rangeLab} · ${n0(ts.length)}점 · 띠는 구간 최소–최대, 선은 평균`;
    if (!ts.length) note = `${rangeLab} · 아직 쌓인 자료가 없다`;
    else if (cov < 0.9) note += ` — Jarvis 가 ${fmt.dur(t1 - ts[0], 2)} 분량만 기록했다(그 앞은 빈칸)`;
    S.hist.note = note;
    readouts();
  }

  function nearestIdx(ts, t) {
    let lo = 0; let hi = ts.length - 1;
    if (hi < 0) return -1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (ts[mid] < t) lo = mid; else hi = mid; }
    return Math.abs(ts[lo] - t) <= Math.abs(ts[hi] - t) ? lo : hi;
  }

  /** 제목 옆 값: 평소엔 지금 스냅샷 값(위 큰 숫자와 같다), 그림 위에 올리면 그 시각의 기록. */
  function readouts() {
    const h = S.hist.data;
    const grid = byId("hist-grid");
    if (!grid || !PANELS[0].el) return;
    const ts = h ? h.t || [] : [];
    const hov = h ? S.hist.hoverT : null;
    let idx = -1;
    if (hov != null) {
      idx = nearestIdx(ts, hov);
      if (idx >= 0 && Math.abs(ts[idx] - hov) > (h.interval_s || 5) * 4) idx = -1;
    }
    grid.classList.toggle("hovering", hov != null);
    const span = S.hist.t1 - S.hist.t0 || 1;
    const cx = hov != null ? (((hov - S.hist.t0) / span) * VW).toFixed(1) : "0";
    for (const p of PANELS) {
      const e = p.el; if (!e) continue;
      e.cur.setAttribute("x1", cx); e.cur.setAttribute("x2", cx);
      const live = hov == null ? p.live(S.snap) : null;
      const parts = p.s.map(([k, lab], i) => {
        const v = live ? live[i] : idx >= 0 ? (h[k] || [])[idx] : null;
        return { lab, txt: p.f(has(v) ? +v : null), i };
      });
      const s = parts.length === 1 ? html`${parts[0].txt}` : html`${parts[0].lab} ${parts[0].txt}<small>${parts[1].lab} ${parts[1].txt}</small>`;
      put(e.v, s);
    }
    const note = byId("hist-note");
    if (note) {
      const t = hov != null ? (idx >= 0 ? `${S.hist.range === "24h" ? fmt.datetime(ts[idx]) : fmt.time(ts[idx])} 시점의 기록` : "이 시각엔 기록이 없다") : S.hist.note || "";
      setText(note, t);
    }
  }

  function loadHist(force) {
    const H = S.hist;
    if (H.loading) return;
    const ttl = H.range === "24h" ? 60000 : 15000;
    if (!force && H.data && Date.now() - H.at < ttl) return;
    H.loading = true;
    const range = H.range;
    P.history(range).then((d) => {
      if (range !== H.range) return;
      H.data = d; H.at = Date.now();
      drawHist();
    }).catch((e) => {
      if (range === H.range) setText("hist-note", "시계열을 불러오지 못했다 — " + (e && e.message ? e.message : "오류"));
    }).finally(() => { H.loading = false; if (range !== H.range) loadHist(true); });
  }

  // ================================================================ 03 서비스
  function exposureBadges(x) {
    const b = [];
    if (x.public) b.push(html`<span class="xp pub">공개</span>`);
    if (x.tailnet) b.push(html`<span class="xp tn">Tailnet</span>`);
    if (x.lan) b.push(html`<span class="xp lan">LAN</span>`);
    if (x.local_only || !b.length) b.push(html`<span class="xp loc">로컬</span>`);
    return b;
  }
  function exposure(x) {
    if (!x) return DASH;
    const via = (x.via || []).map((v) => L.via[v] || v).join(" · ");
    return html`<span class="xps">${exposureBadges(x)}</span>${via ? html`<span class="via">${via}</span>` : ""}`;
  }
  function runtimeCell(r, s) {
    if (!r) return html`<span class="dim">${DASH}</span>`;
    if (r.type === "remote") {
      return html`<span class="rt-t">원격</span><span class="rt-n">${r.name || ""}</span><span class="rt-m">${r.ip || ""} · ${r.online ? "온라인" : "오프라인"}</span>`;
    }
    const type = L.runtime[r.type] || r.type;
    const rst = r.restart_count;
    const health = r.health ? (r.health === "healthy" ? "헬스 정상" : r.health === "unhealthy" ? "헬스 이상" : r.health === "starting" ? "헬스 확인 중" : r.health) : null;
    const hc = r.health === "unhealthy" ? "crit" : r.health === "starting" ? "warn" : "";
    return html`<span class="rt-t">${type}</span><span class="rt-n">${r.name || ""}</span>
      <span class="rt-m">${r.state && r.state !== "running" && r.state !== "active" ? html`<span class="${s.status === "down" ? "crit" : "warn"}">${r.state}${r.sub && r.sub !== r.state ? "/" + r.sub : ""}</span> · ` : ""}가동 ${upt(r.uptime_s)}${has(rst) ? html` · ${rst ? html`<span class="warn">재시작 ${n0(rst)}회</span>` : "재시작 0"}` : ""}</span>
      <span class="rt-m"><span title="코어 하나 기준(최대 400%)">CPU ${pct(r.cpu_pct, 1)}</span> · ${fmt.bytes(r.mem_bytes)}${health ? html` · <span class="${hc}">${health}</span>` : ""}</span>`;
  }
  function daysTone(d) { return !has(d) ? "muted" : d < 7 ? "crit" : d < 21 ? "warn" : "ok"; }
  function probeCell(s) {
    const pr = s.probes || {};
    const keys = ["internal", "tunnel", "public"].filter((k) => pr[k]);
    const rows = keys.map((k) => {
      const p = pr[k];
      const privateTunnel = k === "tunnel" && s.category !== "public";
      const cls = p.ok ? "ok" : "crit";
      const code = has(p.code) ? String(p.code) : p.error ? "실패" : DASH;
      const note = privateTunnel ? (p.ok ? "터널 경로 차단 확인" : "터널로 새는지 확인 필요") : p.error ? p.error : null;
      return html`<span class="pk">${PROBE_LABEL[k]}</span><span class="pc ${cls}">${code}${p.ok ? "" : html` <span class="sr">실패</span>`}</span><span class="pm">${ms(p.ms)}</span>${note ? html`<span class="pn">${note}</span>` : ""}`;
    });
    const facts = [];
    if (s.cert && has(s.cert.days_left)) facts.push(html`<span class="${daysTone(s.cert.days_left) === "ok" ? "" : daysTone(s.cert.days_left)}">인증서 ${n0(Math.floor(s.cert.days_left))}일</span>`);
    if (s.edge_cert && has(s.edge_cert.days_left)) facts.push(html`<span class="${daysTone(s.edge_cert.days_left) === "ok" ? "" : daysTone(s.edge_cert.days_left)}">엣지 인증서 ${n0(Math.floor(s.edge_cert.days_left))}일</span>`);
    if (s.dns && s.dns.points_to) {
      const pt = s.dns.points_to;
      const bad = pt === "error" || pt === "other" || (s.category === "public" && pt !== "cloudflare") || (s.category === "tailnet" && pt === "cloudflare");
      facts.push(html`<span class="${bad ? "warn" : ""}">DNS → ${DNS_LABEL[pt] || pt}</span>`);
    }
    const lst = (s.listeners || []).filter((l) => l && has(l.port));
    const ports = !keys.length && lst.length ? html`<div class="ports-inline">${lst.slice(0, 4).map((l, i) => html`${i ? " · " : ""}<span class="p">:${l.port}</span>${l.proto === "udp" ? "/udp" : ""} ${L.scope[l.scope] || l.scope}`)}${lst.length > 4 ? ` 외 ${lst.length - 4}` : ""}</div>` : "";
    const ex = s.extra || {};
    const extra = [];
    if (ex.version) extra.push(`버전 ${ex.version}`);
    if (has(ex.routes)) extra.push(`라우트 ${ex.routes}`);
    if (has(ex.connections)) extra.push(`연결 ${ex.connections}`);
    if (ex.edge_locations && ex.edge_locations.length) extra.push(`엣지 ${ex.edge_locations.join("·")}`);
    if (has(ex.peers_online)) extra.push(`피어 온라인 ${ex.peers_online}`);
    if (ex.backend_state) extra.push(ex.backend_state);
    if (ex.config_ok === false) extra.push("설정 오류");
    if (!rows.length && !facts.length && !ports && !extra.length) return html`<span class="dim">${DASH}</span>`;
    return html`${rows.length ? html`<div class="pr">${rows}</div>` : ""}${ports}${extra.length ? html`<div class="facts">${extra.map((x) => html`<span>${x}</span>`)}</div>` : ""}${facts.length ? html`<div class="facts">${facts}</div>` : ""}`;
  }
  function trafficCell(t) {
    if (!t) return html`<span class="dim">${DASH}</span>`;
    return html`<div class="tr-v">${n0(t.requests_1h)}<span class="sl">/</span>${n0(t.requests_24h)}</div>
      <div class="tr-s">${t.errors_5xx_24h ? html`<span class="crit">5xx ${n0(t.errors_5xx_24h)}</span> · ` : ""}${t.errors_4xx_24h ? html`<span class="warn">4xx ${n0(t.errors_4xx_24h)}</span>` : "오류 없음"}</div>
      ${has(t.p50_ms_24h) ? html`<div class="tr-s">p50 ${ms(t.p50_ms_24h)} · p95 ${ms(t.p95_ms_24h)}</div>` : ""}`;
  }
  function urlCell(s, only) {
    const urls = (s.urls || []).filter((u) => u && u.url);
    if (!urls.length) return html`<span class="dim">${s.hostnames && s.hostnames.length ? s.hostnames.join(", ") : "주소 없음"}</span>`;
    return (only ? urls.slice(0, only) : urls).map((u, i) => ext(u.url, html`${shortUrl(u.url)}<span class="aud">${u.label || L.audience[u.audience] || u.audience || ""}</span>`, `u:${s.id}:${i}`, "u"));
  }
  /** 점검 결과 한 줄: "200 · 200 · 200 · 75 ms" */
  function probeLine(s) {
    const pr = s.probes || {};
    const keys = ["internal", "tunnel", "public"].filter((k) => pr[k]);
    if (!keys.length) return null;
    const msv = pr.internal?.ms ?? pr[keys[0]]?.ms;
    return html`${keys.map((k, i) => { const p = pr[k]; return html`${i ? html`<span class="sep"> · </span>` : ""}<span class="pc ${p.ok ? "ok" : "crit"}" title="${PROBE_LABEL[k]}">${has(p.code) ? String(p.code) : p.error ? "실패" : DASH}${p.ok ? "" : html`<span class="sr"> 실패</span>`}</span>`; })}${has(msv) ? html`<span class="sep"> · </span><span class="pm">${ms(msv)}</span>` : ""}`;
  }
  /** 좁은 화면용 서비스 행: 두 줄 요약 + 펼치면 점검·인증서·DNS·실행. */
  function svcRowM(s) {
    const t = tone(s.status);
    const x = s.exposure || null;
    const tr = s.traffic;
    const r = s.runtime;
    const urls = (s.urls || []).filter((u) => u && u.url);
    const pl = probeLine(s);
    const rtShort = !r ? null : r.type === "remote" ? `원격 · ${r.online ? "온라인" : "오프라인"}` : `${L.runtime[r.type] || r.type || ""} · 가동 ${upt(r.uptime_s)}`;
    const lsn = (s.listeners || []).filter((l) => l && has(l.port));
    const extP = lsn.filter((l) => l.scope !== "loopback");
    const ports = extP.length ? extP.slice(0, 3).map((l) => ":" + l.port + (l.proto === "udp" ? "/udp" : "")).join(" ") + (extP.length > 3 ? ` 외 ${extP.length - 3}` : "")
      : lsn.length ? lsn.slice(0, 3).map((l) => ":" + l.port).join(" ") + " · 로컬만" : "";
    return html`<tr class="svc svc-m s-${t}" id="svc-${slug(s.id)}" data-key="${s.id}">
      <td class="m-main">
        <div class="m-l1">${mk(s.status, false)}<b class="nm">${s.name}</b>${s.status !== "up" ? html`<span class="mk-t ${t}">${statusLabel(s.status)}</span>` : ""}<span class="m-xp">${x ? exposureBadges(x) : ""}</span>${tr ? html`<span class="m-tr" title="요청 1시간 / 24시간">${n0(tr.requests_1h)}<span class="sl">/</span>${n0(tr.requests_24h)}</span>` : ""}</div>
        ${s.status_reason && s.status !== "up" ? html`<em class="why ${t}">${s.status_reason}</em>` : ""}
        <div class="m-l2">${urls.length ? html`${urlCell(s, 1)}${urls.length > 1 ? html`<span class="dim m-more-n">외 ${urls.length - 1}</span>` : ""}` : html`<span class="dim">${ports || (s.hostnames || []).join(", ") || "주소 없음"}</span>`}</div>
      </td>
      <td class="m-fold"><details class="fold svc-more" data-dk="svc:${s.id}"><summary data-fk="sum:svc:${s.id}"><span class="sm-k">${pl ? "점검" : "실행"}</span><span class="sm-v">${pl || rtShort || DASH}</span></summary>
        <div class="m-det">
          ${s.title && s.title !== s.name ? html`<p class="m-ti">${s.title}${s.kind ? html` <span class="kind">${L.kind[s.kind] || s.kind}</span>` : ""}</p>` : ""}
          ${s.description ? html`<p class="desc">${s.description}</p>` : ""}
          ${urls.length > 1 ? html`<div class="m-sec"><p class="m-k">주소</p><div class="c-url">${urlCell(s)}</div></div>` : ""}
          <div class="m-sec"><p class="m-k">노출</p>${exposure(x)}</div>
          <div class="m-sec"><p class="m-k">점검 · 인증서 · DNS</p>${probeCell(s)}</div>
          <div class="m-sec"><p class="m-k">실행</p>${runtimeCell(r, s)}</div>
          ${tr ? html`<div class="m-sec"><p class="m-k">요청 1시간 / 24시간</p>${trafficCell(tr)}</div>` : ""}
        </div></details></td>
    </tr>`;
  }
  function svcRow(s) {
    const t = tone(s.status);
    const showTitle = s.title && s.title !== s.name;
    return html`<tr class="svc s-${t}" id="svc-${slug(s.id)}" data-key="${s.id}">
      <td class="c-st" data-l="상태">${mk(s.status)}</td>
      <td class="c-name" data-l="서비스"><b class="nm">${s.name}</b>${showTitle ? html`<span class="ti">${s.title}</span>` : ""}<span class="kind">${L.kind[s.kind] || s.kind || ""}</span>
        ${s.status_reason && s.status !== "up" ? html`<em class="why ${t}">${s.status_reason}</em>` : ""}
        ${s.description ? html`<span class="desc">${s.description}</span>` : ""}</td>
      <td class="c-url" data-l="주소">${urlCell(s)}</td>
      <td class="c-xp" data-l="노출">${exposure(s.exposure)}</td>
      <td class="c-rt" data-l="실행">${runtimeCell(s.runtime, s)}</td>
      <td class="c-pr" data-l="점검 · 인증서 · DNS">${probeCell(s)}</td>
      <td class="c-tr num" data-l="요청 1시간 / 24시간">${s.traffic ? trafficCell(s.traffic) : html`<span class="dim">${DASH}</span>`}</td>
    </tr>`;
  }
  const MQ_M = window.matchMedia ? matchMedia("(max-width: 760px)") : { matches: false };
  function renderServices(snap) {
    const compact = MQ_M.matches;
    const groups = P.pick.byCategory(snap);
    const cats = (snap.categories || []).filter(Boolean);
    for (const id of Object.keys(groups)) if (!cats.find((c) => c.id === id)) cats.push({ id, label: L.category[id] || id });
    const items = [];
    for (const c of cats) {
      const svcs = (groups[c.id] || {}).services || [];
      const cnt = { up: c.up ?? svcs.filter((s) => s.status === "up").length, degraded: c.degraded ?? 0, down: c.down ?? 0, unknown: c.unknown ?? 0 };
      const count = c.count ?? svcs.length;
      items.push({ k: "rh:" + c.id, h: html`<tr class="rh" id="cat-${slug(c.id)}" data-key="${"rh:" + c.id}"><th colspan="7"><div class="rh-line">
        <span class="rh-name">${c.label || L.category[c.id] || c.id}</span><span class="rh-desc">${c.desc || ""}</span>
        <span class="rh-count">${n0(count)}곳${cnt.up ? html`${bare("ok")}정상 ${n0(cnt.up)}` : ""}${cnt.degraded ? html`${bare("warn")}저하 ${n0(cnt.degraded)}` : ""}${cnt.down ? html`${bare("crit")}다운 ${n0(cnt.down)}` : ""}${cnt.unknown ? html`${bare("muted")}불명 ${n0(cnt.unknown)}` : ""}</span></div></th></tr>` });
      if (!svcs.length) items.push({ k: "none:" + c.id, h: html`<tr data-key="${"none:" + c.id}"><td colspan="7" class="empty-note">이 분류에 떠 있는 것이 없다.</td></tr>` });
      for (const s of svcs.filter(Boolean)) items.push({ k: s.id, h: compact ? svcRowM(s) : svcRow(s) });
    }
    list("svc-body", items);
    const sv = snap.summary?.services || {};
    setText("dek-svc", `${n0(sv.total)}곳 · 정상 ${n0(sv.up)} · 저하 ${n0(sv.degraded)} · 다운 ${n0(sv.down)}`);
  }

  // ================================================================ 04 노출 경로(지도)
  const cv = doc.createElement("canvas").getContext("2d");
  const FONTS = {
    lab: '12.5px "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", "WenQuanYi Zen Hei", sans-serif',
    bold: '700 12.5px "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", "WenQuanYi Zen Hei", sans-serif',
    zone: '700 16px "Noto Serif KR", "Newsreader", serif',
    det: '11px "Pretendard Variable", Pretendard, -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif',
    elab: 'italic 12px "Newsreader", "Noto Serif KR", serif',
  };
  const tw = (t, f) => { cv.font = FONTS[f]; return cv.measureText(String(t || "")).width; };
  /** 폭 안에 들어가도록 끝을 줄인다 */
  const fit = (t, f, max) => {
    t = String(t || "");
    if (max <= 0) return "";
    if (tw(t, f) <= max) return t;
    let lo = 0; let hi = t.length;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (tw(t.slice(0, mid) + "…", f) <= max) lo = mid; else hi = mid - 1; }
    return lo ? t.slice(0, lo) + "…" : "";
  };
  const COARSE = window.matchMedia ? matchMedia("(pointer: coarse)") : { matches: false };

  /** 지도 공통: 토폴로지 노드에 표시 글자·폭·무리(group)를 붙인다. */
  function prepNodes(snap) {
    const topo = snap.topology || {};
    const svcById = new Map((snap.services || []).filter(Boolean).map((s) => [s.id, s]));
    const nodes = (topo.nodes || []).filter((n) => n && n.id).map((n) => ({ ...n, layer: clamp(n.layer | 0, 0, 5) }));
    const nb = new Map(nodes.map((n) => [n.id, n]));
    const edges = (topo.edges || []).filter((e) => e && nb.has(e.from) && nb.has(e.to) && e.from !== e.to).map((e) => ({ ...e }));
    const out = (id) => edges.filter((e) => e.from === id);
    const inc = (id) => edges.filter((e) => e.to === id);
    // 직접 포트를 여는 인프라(SSH 등)는 서비스 열로 옮겨 "직접 포트" 무리에 둔다.
    for (const n of nodes) {
      const ins = inc(n.id);
      if (n.layer === 3 && !out(n.id).length && ins.length && ins.every((e) => nb.get(e.from).layer === 0)) { n.layer = 4; n.group = "direct"; }
    }
    for (const n of nodes) {
      if (n.layer !== 4 || n.group) continue;
      n.group = n.category === "public" ? "public" : n.category === "tailnet" ? "tailnet" : n.type === "host" || n.category === "host" ? "direct" : "other";
    }
    const ts = snap.tailscale || {}; const net = snap.system?.network || {};
    const tunnel = snap.ingress?.tunnel || {};
    const caddySvc = svcById.get("infra:caddy");
    for (const n of nodes) {
      const s = n.service_id ? svcById.get(n.service_id) : null;
      n.svc = s;
      if (n.id === "zone:internet") { n.text = "인터넷"; n.det = "누구나"; }
      else if (n.id === "zone:tailnet") { n.text = "Tailnet"; n.det = has(ts.peers_online) ? `피어 ${ts.peers_online}/${ts.peers_total} 온라인` : n.detail || ""; }
      else if (n.id === "zone:lan") { n.text = "LAN"; n.det = (net.lan_ips || []).join(" · ") || n.detail || ""; }
      else if (n.type === "edge") { n.text = n.id === "edge:cloudflare" ? "Cloudflare 엣지" : n.label; n.det = (tunnel.edge_locations || []).join(" · ") || n.detail || ""; }
      else if (n.id === "infra:cloudflared") { n.text = "cloudflared"; n.det = has(tunnel.ready_connections) ? `연결 ${tunnel.ready_connections}/${tunnel.connections}` : n.detail || ""; }
      else if (n.id === "infra:caddy") { n.text = "Caddy"; n.det = caddySvc?.extra ? `${caddySvc.extra.version || ""} · 라우트 ${caddySvc.extra.routes ?? ""}` : n.detail || ""; }
      else if (n.id === "infra:ssh") { n.text = "SSH"; n.det = ":22"; }
      else if (n.type === "host") { n.text = n.label; n.det = n.detail ? ":" + String(n.detail).split(/,\s*/).join(" :") : ""; }
      else if (n.type === "internal") { n.text = n.label; n.det = ""; }
      else { n.text = n.label; n.det = ""; }
      n.text = String(n.text || n.id);
      n.box = n.type === "infra" || n.type === "edge";
      if (n.layer === 4 && n.group === "direct" && n.type === "infra") n.box = false;
      const lw = n.type === "zone" ? tw(n.text, "zone") : tw(n.text, n.box ? "bold" : "lab");
      const dw = n.det ? tw(n.det, "det") : 0;
      n.lw = lw;
      if (n.type === "zone") n.w = Math.max(lw, dw);
      else if (n.box) n.w = Math.max(lw + 12, dw) + 12;   // 표지 + 글자, 아래 줄 설명
      else n.w = 11 + lw + (dw ? 7 + dw : 0);
      n.edgeW = n.type === "zone" ? lw : n.w;
    }
    return { nodes, nb, edges, out, inc };
  }

  // ---- 가로 지도(넓은 화면): 영역 → 엣지 → 터널 → Caddy → 서비스 → 백엔드
  function mapModel(snap, width, coarse) {
    const { nodes, nb, edges, out, inc } = prepNodes(snap);
    let W = Math.max(Math.round(width), 920);
    const colF = [0, 0.135, 0.285, 0.43, 0.575, 0.83];
    const colX = colF.map((f, i) => (i === 0 ? 4 : Math.round(f * W)));
    // 칸 사이가 좁아 선 이름(QUIC 등)이 상자에 겹치면 뒤 칸들을 민다
    const vlab = (e) => L.via[e.label] || e.label;
    for (const Lr of [1, 2]) {
      const src = nodes.filter((n) => n.layer === Lr && out(n.id).some((e) => nb.get(e.to).layer === Lr + 1));
      if (!src.length) continue;
      const wMax = Math.max(...src.map((n) => (n.box ? n.w - 6 : n.edgeW + 8)));
      const labW = Math.max(0, ...edges.filter((e) => e.label && src.some((n) => n.id === e.from)).map((e) => tw(vlab(e), "elab")));
      const need = colX[Lr] + wMax + (labW ? labW + 28 : 24) + 6;
      if (colX[Lr + 1] < need) { const d = need - colX[Lr + 1]; for (let k = Lr + 1; k <= 5; k++) colX[k] += d; }
    }
    const w5 = Math.max(0, ...nodes.filter((n) => n.layer === 5).map((n) => n.w));
    W = Math.max(W, Math.round(colX[5] + w5 + 16));
    // 서비스 열 배치
    const GORDER = ["public", "tailnet", "direct", "other"];
    const GLAB = { public: "공개 서비스", tailnet: "Tailnet 전용", direct: "직접 포트", other: "기타" };
    const ROW = coarse ? 36 : 24; let y = 50; const caps = [];
    for (const g of GORDER) {
      const ns = nodes.filter((n) => n.layer === 4 && n.group === g);
      if (!ns.length) continue;
      if (g === "direct") ns.sort((a, b) => (a.type === "infra" ? -1 : 0) - (b.type === "infra" ? -1 : 0));
      caps.push({ x: colX[4], y: y - 20, t: GLAB[g] });
      for (const n of ns) { n.y = y; y += ROW; }
      y += 30;
    }
    const bottom = y - 30;
    // 내부 백엔드: 의존하는 서비스 높이에. 어디에도 이어지지 않은 것은 따로 모은다.
    const l5 = nodes.filter((m) => m.layer === 5);
    for (const n of l5) {
      const ys = inc(n.id).map((e) => nb.get(e.from).y).filter((v) => v != null);
      n.orphan = !inc(n.id).length && !out(n.id).length;
      n.y = ys.length ? sum(ys) / ys.length : null;
    }
    // 같은 종류의 선을 따라 닿는 서비스들의 평균 높이
    const reachY = (n) => {
      const ys = []; const seen = new Set([n.id]);
      const qq = out(n.id).filter((e) => e.kind !== "depends").map((e) => [e.to, e.kind]);
      while (qq.length) {
        const [id, k] = qq.shift();
        if (seen.has(id)) continue; seen.add(id);
        const m = nb.get(id);
        if (m.layer >= 4 && m.y != null) ys.push(m.y);
        for (const e of out(id)) if (e.kind === k) qq.push([e.to, k]);
      }
      return ys.length ? sum(ys) / ys.length : null;
    };
    for (const L0 of [3, 2, 1, 0]) for (const n of nodes.filter((m) => m.layer === L0)) n.y = reachY(n);
    for (let pass = 0; pass < 3; pass++) {
      for (const n of nodes) {
        if (n.y != null || n.orphan) continue;
        const ys = [...inc(n.id).map((e) => nb.get(e.from).y), ...out(n.id).map((e) => nb.get(e.to).y)].filter((v) => v != null);
        if (ys.length) n.y = sum(ys) / ys.length;
      }
    }
    for (const n of nodes) if (n.y == null && !n.orphan) n.y = bottom;
    // 층마다 겹침 풀기
    const GAPS = [58, 48, 46, 46, ROW, coarse ? 36 : 26];
    for (let li = 0; li <= 5; li++) {
      if (li === 4) continue;
      const ns = nodes.filter((n) => n.layer === li && !n.orphan).sort((a, b) => a.y - b.y);
      const gap = GAPS[li];
      for (let i = 1; i < ns.length; i++) if (ns[i].y < ns[i - 1].y + gap) ns[i].y = ns[i - 1].y + gap;
      if (ns.length && ns[0].y < 36) { const sh = 36 - ns[0].y; for (const n of ns) n.y += sh; }
    }
    // 연결 미상 백엔드: 다른 백엔드 바로 아래에 이름표를 달고 쌓는다
    const orphans = l5.filter((n) => n.orphan);
    if (orphans.length) {
      const placed = l5.filter((n) => !n.orphan);
      let oy = (placed.length ? Math.max(...placed.map((n) => n.y)) : 40) + (placed.length ? 46 : 20);
      caps.push({ x: colX[5], y: oy - 20, t: "연결 미상" });
      for (const n of orphans) { n.y = oy; oy += GAPS[5]; }
    }
    for (const n of nodes) {
      n.x = colX[n.layer];
      if (n.type === "zone") n.x = colX[0];
    }
    const maxY = Math.max(bottom, ...nodes.map((n) => n.y + (n.det && (n.box || n.type === "zone") ? 24 : 10)));
    const H = Math.round(maxY + 26);
    // 선: 오른쪽 끝 → 왼쪽 끝
    const sx = (n) => (n.box ? n.x + n.w - 6 : n.x + n.edgeW + 8);
    const lx = (n) => (n.box ? n.x - 6 : n.type === "zone" ? n.x - 6 : n.x - 7);
    const labeled = new Set();
    for (const e of edges) {
      const a = nb.get(e.from); const b = nb.get(e.to);
      const x1 = sx(a); const y1 = a.y;
      let d; let lxp = null; let lyp = null;
      if (e.kind === "depends") {
        if (b.layer <= a.layer) {
          // 같은 열의 서비스끼리: 오른쪽으로 꺾쇠를 치고 화살표로 가리킨다
          const bx = Math.max(x1, sx(b)) + 16;
          d = `M${x1} ${y1}H${bx}V${b.y}H${sx(b) + 3}`;
          lxp = bx + 5; lyp = (y1 + b.y) / 2; e.anchor = "start"; e.small = true; e.text = "의존";
        } else {
          const x2 = lx(b); const bend = Math.min(60, (x2 - x1) * 0.5);
          d = `M${x1} ${y1}L${x2 - bend} ${y1}C${x2 - bend / 2} ${y1} ${x2 - bend / 2} ${b.y} ${x2 - 2} ${b.y}`;
        }
      } else {
        const x2 = lx(b);
        const flat = Math.abs(b.y - y1) < 0.5;
        let runA; let runB; let runY;
        if (a.layer === 0 && b.layer >= 2) {
          // 버스: 영역의 높이로 곧게 달리다가 목적지 앞에서 갈라진다
          const bend = Math.min(72, Math.max(24, (x2 - x1) * 0.3));
          d = flat ? `M${x1} ${y1}L${x2} ${b.y}` : `M${x1} ${y1}L${x2 - bend} ${y1}C${x2 - bend / 2} ${y1} ${x2 - bend / 2} ${b.y} ${x2} ${b.y}`;
          runA = x1; runB = flat ? x2 : x2 - bend; runY = y1;
        } else {
          // 부채: 출발점 바로 뒤에서 갈라져 목적지 높이로 곧게 들어간다
          const bend = Math.min(84, Math.max(24, (x2 - x1) * 0.42));
          d = flat ? `M${x1} ${y1}L${x2} ${b.y}` : `M${x1} ${y1}C${x1 + bend / 2} ${y1} ${x1 + bend / 2} ${b.y} ${x1 + bend} ${b.y}L${x2} ${b.y}`;
          runA = flat ? x1 : x1 + bend; runB = x2; runY = b.y;
        }
        const lk = e.from + "|" + (e.label || "");
        if (e.label && b.layer <= 3 && !labeled.has(lk)) {
          labeled.add(lk);
          const lw = tw(vlab(e), "elab");
          if (runB - runA < lw + 24) { lxp = Math.max(x2 - 8, x1 + lw + 4); lyp = b.y - 7; e.anchor = "end"; }
          else { lxp = clamp((runA + runB) / 2, runA + lw / 2 + 6, runB - lw / 2 - 6); lyp = runY - 6; }
        }
      }
      e.d = d; e.lx = lxp; e.ly = lyp;
    }
    const hostX = colX[2] - 22;
    return { W, H, nodes, edges, caps, colX, hostX, nb, coarse };
  }

  const svgMark = (t, x, y, r = 3.6) => (t === "crit"
    ? html`<rect class="m-crit" x="${(x - r * 0.9).toFixed(1)}" y="${(y - r * 0.9).toFixed(1)}" width="${(r * 1.8).toFixed(1)}" height="${(r * 1.8).toFixed(1)}" transform="rotate(45 ${x} ${y})"/>`
    : t === "warn" ? html`<path class="m-warn" d="M${x} ${(y - r * 1.25).toFixed(1)}L${(x + r * 1.3).toFixed(1)} ${(y + r).toFixed(1)}L${(x - r * 1.3).toFixed(1)} ${(y + r).toFixed(1)}Z"/>`
    : html`<circle class="m-${t}" cx="${x}" cy="${y}" r="${r}"/>`);
  const nodeLabel = (n, extra) => `${n.text}${n.status && n.type !== "zone" ? " — " + statusLabel(n.status) : ""}${n.det ? ", " + n.det : ""}${extra ? ", " + extra : ""}`;
  const ARROW_DEF = html`<defs><marker id="ldg-arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" markerUnits="userSpaceOnUse" orient="auto"><path class="arr-h" d="M0 0.5L8 4L0 7.5z"/></marker></defs>`;
  function mapSvg(m) {
    const edgeEls = m.edges.map((e, i) => html`<path class="edge e-${e.kind}" d="${e.d}" data-i="${i}" data-from="${e.from}" data-to="${e.to}"${e.kind === "depends" ? P.raw(' marker-end="url(#ldg-arr)"') : ""}/>`);
    const labels = m.edges.filter((e) => e.lx != null).map((e) => html`<text class="elab${e.small ? " elab-s" : ""}" x="${e.lx.toFixed(1)}" y="${e.ly.toFixed(1)}" text-anchor="${e.anchor || "middle"}"${e.small ? P.raw(' dominant-baseline="central"') : ""}>${e.text || L.via[e.label] || e.label}</text>`);
    const hh = m.coarse ? 34 : 22;
    const nodeEls = m.nodes.map((n) => {
      const t = tone(n.status);
      let body;
      if (n.type === "zone") {
        body = html`<rect class="hit" x="${n.x - 6}" y="${n.y - 14}" width="${n.w + 14}" height="${n.det ? 42 : 28}" rx="2"/>
          <text class="zone-t" x="${n.x}" y="${n.y}" dominant-baseline="central">${n.text}</text>
          ${n.det ? html`<text class="det" x="${n.x}" y="${n.y + 18}" dominant-baseline="central">${n.det}</text>` : ""}`;
      } else if (n.box) {
        body = html`<rect class="hit" x="${n.x - 10}" y="${n.y - 15}" width="${n.w + 8}" height="${n.det ? 44 : 30}" rx="2"/>
          <rect class="box" x="${n.x - 6}" y="${n.y - 11}" width="${n.w}" height="22" rx="1.5"/>
          ${svgMark(t, n.x + 2, n.y, 3.2)}
          <text class="infra-t" x="${n.x + 10}" y="${n.y}" dominant-baseline="central">${n.text}</text>
          ${n.det ? html`<text class="det" x="${n.x - 6}" y="${n.y + 22}" dominant-baseline="central">${n.det}</text>` : ""}`;
      } else {
        body = html`<rect class="hit" x="${n.x - 8}" y="${n.y - hh / 2}" width="${n.w + 14}" height="${hh}" rx="2"/>
          ${svgMark(t, n.x, n.y)}
          <text x="${n.x + 11}" y="${n.y}" dominant-baseline="central" class="${n.type === "internal" ? "det" : ""}" style="${n.type === "internal" ? "font-size:12px" : ""}">${n.text}</text>
          ${n.det ? html`<text class="det" x="${n.x + 11 + n.lw + 7}" y="${n.y}" dominant-baseline="central">${n.det}</text>` : ""}`;
      }
      return html`<g class="node" tabindex="0" role="button" aria-label="${nodeLabel(n)}" data-id="${n.id}" data-fk="map:${n.id}">${body}</g>`;
    });
    const caps = m.caps.map((c) => html`<text class="grp" x="${c.x - 4}" y="${c.y}">${c.t}</text>`);
    const l5 = m.nodes.filter((n) => n.layer === 5 && !n.orphan);
    const minY5 = l5.length ? Math.min(...l5.map((n) => n.y)) : 0;
    return html`<svg width="${m.W}" height="${m.H}" viewBox="0 0 ${m.W} ${m.H}" role="group" aria-label="노출 경로 지도: 영역에서 이 기기 서비스까지">${ARROW_DEF}
      <rect class="hostbox" x="${m.hostX}" y="6" width="${m.W - m.hostX - 2}" height="${m.H - 12}" rx="3"/>
      <text class="hostbox-t" x="${m.hostX + 10}" y="22">이 기기 안</text>
      ${l5.length ? html`<text class="grp" x="${m.colX[5] - 4}" y="${Math.min(30, minY5 - 20)}">내부 백엔드</text>` : ""}
      ${caps}<g class="edges">${edgeEls}</g>${labels}<g class="nodes">${nodeEls}</g></svg>`;
  }

  // ---- 세로 지도(좁은 화면): 무리마다 들어오는 길을 세로 등뼈로, 서비스는 가지로
  function mapModelV(snap, width) {
    const base = prepNodes(snap);
    const { nodes, nb, edges, inc } = base;
    const W = Math.max(280, Math.round(width));
    const ROW = 40; const SX = 12;
    const items = []; let y = 0;
    /** x 까지 같은 종류(kind)의 선만 거슬러 올라간 길들 — 각 길은 영역부터 x 직전까지의 마디 [{id, label, kind}] */
    const back = (x, kind, seen = new Set([x])) => {
      const ins = inc(x).filter((e) => e.kind === kind && !seen.has(e.from));
      if (!ins.length) return [[]];
      const res = [];
      for (const e of ins) {
        const s2 = new Set(seen); s2.add(e.from);
        for (const c of back(e.from, kind, s2)) res.push([...c, { id: e.from, label: e.label, kind: e.kind }]);
      }
      return res;
    };
    /** 서비스 n 으로 들어오는 길: 들어오는 선마다 그 선과 같은 종류로만 거슬러 올라간다
     *  (Tailnet 전용 서비스가 Caddy 를 거쳐 인터넷에서 오는 것처럼 보이지 않게). */
    const chainsTo = (id) => {
      const res = [];
      for (const e of inc(id).filter((x) => x.kind !== "depends")) for (const c of back(e.from, e.kind)) res.push([...c, { id: e.from, label: e.label, kind: e.kind }]);
      return res;
    };
    // 인터넷 → LAN → Tailnet: 직접 포트 무리에서 LAN 이 등뼈가 되어야 "Tailnet만" 이 드러난다
    const zoneRank = (c) => ({ "zone:internet": 0, "zone:lan": 1, "zone:tailnet": 2 }[c[0]?.id] ?? 3);
    const groups = [
      { id: "public", t: "공개 서비스", ns: nodes.filter((n) => n.layer === 4 && n.group === "public") },
      { id: "tailnet", t: "Tailnet 전용", ns: nodes.filter((n) => n.layer === 4 && n.group === "tailnet") },
      { id: "direct", t: "직접 포트", ns: nodes.filter((n) => n.layer === 4 && n.group === "direct").sort((a, b) => (a.type === "infra" ? -1 : 0) - (b.type === "infra" ? -1 : 0)) },
      { id: "other", t: "기타", ns: nodes.filter((n) => n.layer === 4 && n.group === "other") },
    ];
    const vlab = (l) => (l ? L.via[l] || l : "");
    for (const g of groups) {
      if (!g.ns.length) continue;
      // 무리의 서비스들이 쓰는 길을 모아, 인터넷 → LAN → tailnet 순, 많이 쓰이는 순으로
      const cmap = new Map();
      for (const n of g.ns) for (const c of chainsTo(n.id)) {
        const k = c.map((h) => h.id).join(">");
        if (!cmap.has(k)) cmap.set(k, { c, n: 0 });
        cmap.get(k).n++;
      }
      const chains = [...cmap.values()].sort((a, b) => zoneRank(a.c) - zoneRank(b.c) || b.n - a.n || b.c.length - a.c.length).map((x) => x.c);
      items.push({ k: "cap", y: y + 16, t: `${g.t} · ${g.ns.length}곳` });
      y += 30;
      if (!chains.length) {
        for (const n of g.ns) { y += ROW / 2; items.push({ k: "leaf", n, y, x: SX, det: n.det }); y += ROW / 2; }
        y += 18; continue;
      }
      const prim = chains[0];
      const alts = chains.slice(1).map((c) => ({ c, tail: null }));
      // 공개 서비스는 Caddy 에 닿는 다른 길(Tailnet :443)로도 닿는다 — 마지막 마디로 들어오는 다른 종류의 선
      if (g.id === "public") {
        const last0 = prim[prim.length - 1];
        for (const e of inc(last0.id).filter((x) => x.kind !== "depends" && x.kind !== last0.kind)) {
          for (const c of back(e.from, e.kind)) alts.push({ c: [...c, { id: e.from, label: e.label, kind: e.kind }], tail: last0.id });
        }
      }
      // 등뼈: 길의 마디들
      prim.forEach((h, j) => {
        const cy = y + ROW / 2;
        if (j) {
          const prevY = cy - ROW;
          const ph = prim[j - 1];
          items.push({ k: "edge", d: `M${SX} ${prevY + 9}V${cy - 9}`, kind: ph.kind, from: ph.id, to: h.id });
          if (ph.label) items.push({ k: "elab", x: SX + 12, y: cy - ROW / 2, t: vlab(ph.label) });
        }
        items.push({ k: "hop", n: nb.get(h.id), y: cy });
        y += ROW;
      });
      const last = prim[prim.length - 1];
      const lastY = y - ROW / 2;
      // 서비스 가지 — 모두 같은 선 이름이면 한 번만 적는다(WireGuard 등)
      const lastLabs = [...new Set(g.ns.map((n) => { const e = inc(n.id).find((x) => x.from === last.id && x.kind !== "depends"); return e ? e.label || "" : "?"; }))];
      if (lastLabs.length === 1 && lastLabs[0] && lastLabs[0] !== "?") items.push({ k: "elab", x: SX + 12, y: lastY + ROW / 2, t: vlab(lastLabs[0]) });
      for (const n of g.ns) {
        const cy = y + ROW / 2;
        let e = inc(n.id).find((x) => x.from === last.id && x.kind !== "depends");
        let via = "";
        if (!e) {
          e = inc(n.id).find((x) => x.kind !== "depends");
          if (e) via = `${nb.get(e.from).text}만`;
        }
        items.push({ k: "edge", d: `M${SX} ${lastY + 9}V${cy}H${SX + 14}`, kind: e ? e.kind : g.id === "public" ? "public" : "tailnet", from: e ? e.from : last.id, to: n.id });
        items.push({ k: "leaf", n, y: cy, x: SX + 22, det: [n.det, via].filter(Boolean).join(" · ") });
        y += ROW;
      }
      // 다른 길은 가지 아래에 한 줄씩: "또는 Tailnet → Caddy :443"
      for (const a of alts) {
        const c = a.tail ? [...a.c, { id: a.tail, label: null }] : a.c;
        items.push({ k: "alt", y: y + ROW / 2, c });
        y += ROW;
      }
      y += 18;
    }
    // 내부 백엔드: 밖에서 닿는 길이 없다 — 누가 기대는지만 적는다
    const l5 = nodes.filter((n) => n.layer === 5);
    if (l5.length) {
      items.push({ k: "cap", y: y + 16, t: `내부 백엔드 · ${l5.length}곳 — 외부 노출 없음` });
      y += 30;
      for (const n of l5) {
        const deps = inc(n.id).filter((e) => e.kind === "depends").map((e) => nb.get(e.from).text);
        const cy = y + ROW / 2;
        items.push({ k: "leaf", n, y: cy, x: SX, det: deps.length ? "← " + deps.join(", ") : "연결 미상" });
        y += ROW;
      }
    }
    return { W, H: Math.round(y + 8), items, nodes, nb, edges, vertical: true };
  }
  function mapSvgV(m) {
    const W = m.W; const SX = 12;
    let fi = 0;
    const g = (n, body, extra) => html`<g class="node" tabindex="0" role="button" aria-label="${nodeLabel(n, extra)}" data-id="${n.id}" data-fk="map:${n.id}:${fi++}">${body}</g>`;
    const els = m.items.map((it) => {
      if (it.k === "cap") return html`<text class="grp" x="0" y="${it.y}">${it.t}</text>`;
      if (it.k === "edge") return html`<path class="edge e-${it.kind}" d="${it.d}" data-from="${it.from}" data-to="${it.to}"/>`;
      if (it.k === "elab") return html`<text class="elab elab-s" x="${it.x}" y="${it.y}" dominant-baseline="central">${it.t}</text>`;
      if (it.k === "hop") {
        const n = it.n; const t = tone(n.status);
        const isZone = n.type === "zone";
        const lab = fit(n.text, isZone ? "zone" : "bold", W - SX - 20);
        const lw = tw(lab, isZone ? "zone" : "bold");
        const det = n.det ? fit(n.det, "det", W - SX - 14 - lw - 10) : "";
        return g(n, html`<rect class="hit" x="0" y="${it.y - 20}" width="${W}" height="40"/>
          ${isZone ? html`<rect class="zone-dot" x="${SX - 4}" y="${it.y - 4}" width="8" height="8"/>` : svgMark(t, SX, it.y, 3.8)}
          <text class="${isZone ? "zone-t" : "infra-t"}" x="${SX + 14}" y="${it.y}" dominant-baseline="central">${lab}</text>
          ${det ? html`<text class="det" x="${SX + 14 + lw + 8}" y="${it.y}" dominant-baseline="central">${det}</text>` : ""}`);
      }
      if (it.k === "leaf") {
        const n = it.n; const t = tone(n.status);
        const f = n.type === "internal" ? "lab" : n.box ? "bold" : "lab";
        const lab = fit(n.text, f, W - it.x - 20);
        const lw = tw(lab, f);
        const det = it.det ? fit(it.det, "det", W - it.x - 12 - lw - 10) : "";
        return g(n, html`<rect class="hit" x="0" y="${it.y - 20}" width="${W}" height="40"/>
          ${svgMark(t, it.x, it.y)}
          <text class="${n.box ? "infra-t" : ""}" x="${it.x + 11}" y="${it.y}" dominant-baseline="central">${lab}</text>
          ${det ? html`<text class="det" x="${it.x + 11 + lw + 7}" y="${it.y}" dominant-baseline="central">${det}</text>` : ""}`);
      }
      if (it.k === "alt") {
        // 한 줄에 마디를 늘어놓는다 — 폭이 모자라면 뒤 마디를 줄인다
        const parts = [html`<text class="alt-k" x="0" y="${it.y}" dominant-baseline="central">또는</text>`];
        let x = tw("또는", "elab") + 10;
        it.c.forEach((h, j) => {
          const n = m.nb.get(h.id);
          const prev = j ? it.c[j - 1] : null;
          if (prev) {
            parts.push(html`<text class="det" x="${x}" y="${it.y}" dominant-baseline="central" aria-hidden="true">→</text>`);
            x += 16;
          }
          const lab = fit(n.text + (prev && prev.label ? " " + vlabOf(prev.label) : ""), "bold", W - x - 4);
          const lw = tw(lab, "bold");
          parts.push(g(n, html`<rect class="hit" x="${x - 4}" y="${it.y - 20}" width="${lw + 8}" height="40"/>
            <text class="infra-t" x="${x}" y="${it.y}" dominant-baseline="central">${lab}</text>`, "다른 길"));
          x += lw + 8;
        });
        return html`<g class="alt">${parts}</g>`;
      }
      return "";
    });
    return html`<svg width="${W}" height="${m.H}" viewBox="0 0 ${W} ${m.H}" role="group" aria-label="노출 경로 지도(세로): 무리마다 들어오는 길과 서비스">${els}</svg>`;
  }
  const vlabOf = (l) => (l ? L.via[l] || l : "");

  function related(m, id) {
    const set = new Set([id]);
    const up = [id];
    while (up.length) { const x = up.pop(); for (const e of m.edges) if (e.to === x && !set.has(e.from)) { set.add(e.from); up.push(e.from); } }
    const down = [id]; const seen = new Set([id]);
    while (down.length) { const x = down.pop(); for (const e of m.edges) if (e.from === x && !seen.has(e.to)) { seen.add(e.to); set.add(e.to); down.push(e.to); } }
    return set;
  }
  function pathsTo(m, id) {
    // 영역(zone)에서 id 까지 경로(의존 제외)
    const res = [];
    const walk = (x, acc) => {
      const ins = m.edges.filter((e) => e.to === x && e.kind !== "depends");
      if (!ins.length) { if (acc.length > 1) res.push(acc.slice().reverse()); return; }
      for (const e of ins) { if (acc.some((a) => a.id === e.from)) continue; acc.push({ id: e.from, label: e.label }); walk(e.from, acc); acc.pop(); }
    };
    walk(id, [{ id, label: null }]);
    return res.slice(0, 4);
  }
  function applySel() {
    const box = byId("map"); const m = S.map.model;
    if (!box || !m) return;
    const sel = S.map.sel && m.nb.has(S.map.sel) ? S.map.sel : null;
    if (!sel) S.map.sel = null;
    box.classList.toggle("has-sel", !!sel);
    const set = sel ? related(m, sel) : null;
    box.querySelectorAll(".node").forEach((g) => {
      const id = g.getAttribute("data-id");
      g.classList.toggle("off", !!set && !set.has(id));
      g.classList.toggle("sel", id === sel);
      g.setAttribute("aria-pressed", String(id === sel));
    });
    box.querySelectorAll(".edge").forEach((p) => {
      const on = set && set.has(p.getAttribute("data-from")) && set.has(p.getAttribute("data-to"));
      p.classList.toggle("off", !!set && !on);
    });
    const cap = byId("map-sel");
    if (!sel) { put(cap, html`노드를 누르거나 Tab 으로 골라 Enter — 그 노드로 들어오는 경로와 뒤따르는 백엔드만 남긴다.`); return; }
    const n = m.nb.get(sel);
    const paths = pathsTo(m, sel);
    const deps = m.edges.filter((e) => e.from === sel && e.kind === "depends").map((e) => m.nb.get(e.to).text);
    const name = (id) => m.nb.get(id)?.text || id;
    const svcLink = n.service_id ? html` <a href="#svc-${slug(n.service_id)}" data-fk="map-go" data-go="${slug(n.service_id)}">표에서 보기</a>` : "";
    put(cap, html`<b>${n.text}</b> ${n.status ? html`(${statusLabel(n.status)})` : ""}
      ${paths.length ? html` — ${paths.map((p, i) => html`${i ? " / " : ""}${p.map((h, j) => html`${j ? html` → ${p[j - 1].label ? html`<i>${L.via[p[j - 1].label] || p[j - 1].label}</i> ` : ""}` : ""}${name(h.id)}`)}`)}` : ""}
      ${deps.length ? html` · 백엔드 ${deps.join(", ")}` : ""}${svcLink}<button type="button" data-fk="map-clear" data-clear>선택 해제</button>`);
  }
  function renderMap(snap, force) {
    const scroller = byId("map-scroll"); const box = byId("map");
    if (!scroller || !box) return;
    const width = scroller.clientWidth || 1200;
    const vertical = width < 700;
    const coarse = !!COARSE.matches;
    const topoKey = JSON.stringify([(snap.topology?.nodes || []).filter(Boolean).map((n) => [n.id, n.status, n.detail, n.layer]), (snap.topology?.edges || []).filter(Boolean).map((e) => [e.from, e.to, e.kind, e.label]), snap.tailscale?.peers_online, snap.ingress?.tunnel?.ready_connections, snap.ingress?.tunnel?.edge_locations, Math.round(width / 8), vertical, coarse]);
    if (!force && S.map.key === topoKey) return;
    S.map.key = topoKey;
    const m = vertical ? mapModelV(snap, width) : mapModel(snap, width, coarse);
    S.map.model = m;
    const fig = scroller.closest(".map");
    if (fig) fig.classList.toggle("is-v", vertical);
    const lab = vertical ? "네트워크 지도" : "네트워크 지도 (가로로 스크롤)";
    if (scroller.getAttribute("aria-label") !== lab) scroller.setAttribute("aria-label", lab);
    // 세로 지도는 넘치지 않으므로 스크롤 영역에 따로 초점을 줄 필요가 없다
    if (vertical) scroller.removeAttribute("tabindex"); else if (!scroller.hasAttribute("tabindex")) scroller.setAttribute("tabindex", "0");
    put(box, vertical ? mapSvgV(m) : mapSvg(m));
    applySel();
  }
  function bindMap() {
    const box = byId("map"); const cap = byId("map-sel");
    if (!box) return;
    const pick = (id) => { S.map.sel = S.map.sel === id ? null : id; applySel(); };
    box.addEventListener("click", (ev) => {
      const g = ev.target.closest(".node");
      if (g) pick(g.getAttribute("data-id"));
      else if (S.map.sel) { S.map.sel = null; applySel(); }
    });
    box.addEventListener("keydown", (ev) => {
      const g = ev.target.closest && ev.target.closest(".node");
      if (g && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); pick(g.getAttribute("data-id")); }
      if (ev.key === "Escape" && S.map.sel) { S.map.sel = null; applySel(); }
    });
    cap.addEventListener("click", (ev) => {
      if (ev.target.closest("[data-clear]")) { S.map.sel = null; applySel(); const g = box.querySelector(".node"); if (g) g.focus({ preventScroll: true }); return; }
      const a = ev.target.closest("a[data-go]");
      if (a) {
        const row = byId("svc-" + a.getAttribute("data-go"));
        if (row) {
          ev.preventDefault();
          row.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
          row.classList.add("flash");
          setTimeout(() => row.classList.remove("flash"), 1600);
        }
      }
    });
    let t = 0;
    if ("ResizeObserver" in window) {
      new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => { if (S.snap) renderMap(S.snap); }, 160); }).observe(byId("map-scroll"));
    }
    if (doc.fonts && doc.fonts.ready) doc.fonts.ready.then(() => { if (S.snap) renderMap(S.snap, true); });
  }

  // 경로 요약(모든 화면 폭에서 읽히는 문장형 지도)
  function renderRoutes(snap) {
    const svcs = (snap.services || []).filter(Boolean);
    const by = (c) => svcs.filter((s) => s.category === c);
    const tunnel = snap.ingress?.tunnel || {};
    const net = snap.system?.network || {};
    const domain = snap.meta?.domain || snap.ingress?.domain || "";
    // 화살표는 뒤따르는 마디에 붙여서 줄 끝에 홀로 남지 않게 한다
    const hop = (t, port) => html`<span class="hop">${t}${port ? html` <span class="port">${port}</span>` : ""}</span>`;
    const nx = (t, port) => html` <span class="hop"><span class="arr" aria-hidden="true">→</span><span class="sr">다음</span>${t}${port ? html` <span class="port">${port}</span>` : ""}</span>`;
    const pn = (t) => html` <span class="pnote">${t}</span>`;
    const tgt = (s, extra) => html`<span class="t">${mk(s.status, false)}${s.name}${extra ? html`&nbsp;<span class="dim">${extra}</span>` : ""}</span>`;
    const ports = (s) => (s.listeners || []).filter((l) => l && l.scope !== "loopback" && has(l.port)).map((l) => ":" + l.port + (l.proto === "udp" ? "/udp" : "")).slice(0, 3).join(" ");
    const pub = by("public"); const tn = by("tailnet"); const host = by("host"); const intl = by("internal");
    const ssh = svcs.find((s) => s.id === "infra:ssh");
    const deps = new Map();
    for (const s of svcs) for (const d of s.depends_on || []) { if (!deps.has(d)) deps.set(d, []); deps.get(d).push(s.name); }
    const route = (kind, sw, title, chain, targets, note) => html`<div class="route"><p class="rk"><span class="sw ${sw}" aria-hidden="true"></span>${title}</p>
      <p class="chain">${chain}</p><p class="targets">${targets}</p>${note ? html`<p class="note-line">${note}</p>` : ""}</div>`;
    put("routes", html`
      ${route("public", "public", `공개 경로 · ${pub.length}곳`, html`${hop("인터넷")}${nx("Cloudflare 엣지", (tunnel.edge_locations || []).join("·"))}${nx("cloudflared", `${tunnel.ready_connections ?? DASH}/${tunnel.connections ?? DASH}`)}${nx("Caddy", ":80")}`, pub.length ? pub.map((s) => tgt(s)) : html`<span class="dim">없음</span>`, null)}
      ${route("tailnet", "", `Tailnet 경로 · 전용 ${tn.length}곳`, html`${hop("Tailnet")}${nx("*." + domain, net.tailnet_ip || "")}${nx("Caddy", ":443")}`, tn.length ? tn.map((s) => tgt(s)) : html`<span class="dim">없음</span>`, pub.length ? `공개 서비스 ${pub.length}곳도 이 경로로 닿는다. 비공개 서비스는 터널 경로(:80)에서 404 로 막힌다.` : null)}
      ${route("lan", "lan", `직접 포트 · ${host.length + (ssh ? 1 : 0)}곳`, html`${hop("LAN")} · ${hop("Tailnet")}${nx("이 기기 포트")}${pn("Caddy 를 거치지 않는다")}`, html`${ssh ? tgt(ssh, ":22") : ""}${host.map((s) => tgt(s, ports(s) + (s.exposure && !s.exposure.lan ? " · Tailnet만" : "")))}`, (snap.alerts || []).filter(Boolean).find((a) => a.key === "exposure:lan-open")?.message || null)}
      ${route("depends", "depends", `내부 백엔드 · ${intl.length}곳`, html`${hop("외부 노출 없음")}${pn("compose 네트워크·루프백 안에서만 닿는다")}`, intl.length ? intl.map((s) => tgt(s, deps.has(s.id) ? "← " + deps.get(s.id).join(", ") : "")) : html`<span class="dim">없음</span>`, null)}
    `);
  }

  // ================================================================ 05 인그레스
  function stackBar(parts, cls, total) {
    const t = total || sum(parts.map((p) => p[1])) || 0;
    if (!t) return html`<div class="stack" aria-hidden="true"></div>`;
    return html`<div class="stack" role="img" aria-label="${parts.map((p) => `${p[2]} ${p[1]}건`).join(", ")}">${parts.filter((p) => p[1] > 0).map((p) => html`<i class="${cls}${p[0]}" style="width:${((p[1] / t) * 100).toFixed(2)}%" title="${p[2]} ${n0(p[1])}"></i>`)}</div>`;
  }
  function legend(parts, cls, total) {
    const t = total || sum(parts.map((p) => p[1])) || 1;
    return html`<div class="stack-leg">${parts.map((p) => html`<span><span class="sw ${cls}${p[0]}"></span>${p[2]} ${n0(p[1])} <span class="dim">(${fmt.pct((p[1] / t) * 100, 0)})</span></span>`)}</div>`;
  }
  function renderIngress(snap) {
    const ing = snap.ingress || {}; const cd = ing.caddy || {}; const tu = ing.tunnel || {}; const tr = ing.traffic || {};
    const t1 = tr.last_1h || {}; const t24 = tr.last_24h || {};
    const routes = cd.routes || [];
    setText("dek-ing", `Caddy ${cd.version || ""} · 라우트 ${routes.length} · 터널 ${tu.ready == null ? "확인 불가" : tu.ready ? "준비됨" : "문제"} · 24시간 ${n0(t24.requests)}건`);

    // Caddy
    const hostLine = (h) => html`<span class="h">${h}</span>`;
    put("ing-caddy", html`<h3>Caddy <small>리버스 프록시 · ${cd.version || DASH}</small></h3>
      <dl class="kv" style="margin-top:6px">
        <dt>설정</dt><dd>${cd.config_ok === true ? html`${bare("ok")}정상 로드` : cd.config_ok === false ? html`<span class="t-crit">${bare("crit")}오류</span>` : html`<span class="dim">${bare("muted")}확인 불가</span>`}</dd>
        <dt>라우트</dt><dd>${n0(routes.length)}개 <span class="dim">· 공개 ${n0((cd.public_hosts || []).length)} · 비공개 ${n0((cd.private_hosts || []).length)}</span></dd>
        <dt>Caddyfile 수정</dt><dd>${cd.caddyfile_mtime ? `${fmt.datetime(cd.caddyfile_mtime)} (${ago(cd.caddyfile_mtime)})` : DASH}</dd>
        <dt>컨테이너</dt><dd class="mono">${cd.container || DASH}</dd>
      </dl>
      ${cd.error ? html`<p class="notice crit">${cd.error}</p>` : ""}
      <div class="hl-block"><p class="k"><span class="xp pub">공개</span>터널 경로(:80)로도 열림 · ${n0((cd.public_hosts || []).length)}</p><p class="hosts-list">${(cd.public_hosts || []).map((h, i) => html`${i ? html`<span class="dim"> · </span>` : ""}${hostLine(h)}`)}</p></div>
      <div class="hl-block"><p class="k"><span class="xp tn">Tailnet</span>:443 만 · ${n0((cd.private_hosts || []).length)}</p><p class="hosts-list">${(cd.private_hosts || []).map((h, i) => html`${i ? html`<span class="dim"> · </span>` : ""}${hostLine(h)}`)}</p></div>`);

    // Tunnel
    const codes = tu.responses_by_code || {};
    const cls = { "2": 0, "3": 0, "4": 0, "5": 0 };
    for (const [k, v] of Object.entries(codes)) { const c = String(k)[0]; if (c in cls) cls[c] += +v || 0; }
    const cparts = [["2xx", cls["2"], "2xx"], ["3xx", cls["3"], "3xx"], ["4xx", cls["4"], "4xx"], ["5xx", cls["5"], "5xx"]];
    const rtt = (tu.rtt_ms || []).filter(has);
    put("ing-tunnel", html`<h3>Cloudflare Tunnel <small>${tu.name || ""}</small></h3>
      <dl class="kv" style="margin-top:6px">
        <dt>상태</dt><dd>${tu.ready == null ? html`<span class="dim">${bare("muted")}확인 불가</span>` : tu.ready ? html`${bare("ok")}준비됨` : html`<span class="t-crit">${bare("crit")}준비 안 됨</span>`} · 연결 ${n0(tu.ready_connections)}/${n0(tu.connections)}</dd>
        <dt>엣지</dt><dd class="mono">${(tu.edge_locations || []).join(" · ") || DASH}</dd>
        <dt>RTT</dt><dd>${rtt.length ? rtt.map((v) => fmt.num(v, 0)).join(" · ") + " ms" : DASH}</dd>
        <dt>누적 요청 · 오류</dt><dd>${n0(tu.total_requests)} · <span class="${tu.request_errors ? "t-warn" : ""}">${n0(tu.request_errors)}</span> <span class="dim">(${tu.process_start ? upt(now() - tu.process_start) + " 동안" : ""})</span></dd>
        <dt>분당 · 동시 요청</dt><dd>${fmt.num(tu.req_per_min, 1)} · ${n0(tu.concurrent_requests)}</dd>
        <dt>버전</dt><dd>${tu.version || DASH}${tu.outdated ? html` <span class="t-warn">→ ${tu.outdated.latest}</span>` : ""}</dd>
        <dt>ingress 설정</dt><dd>v${tu.ingress_version ?? DASH}${tu.ingress_updated ? html` <span class="dim">· ${ago(tu.ingress_updated)} 갱신</span>` : ""}</dd>
        <dt>공개 호스트</dt><dd>${n0((tu.hostnames || []).length)}개</dd>
      </dl>
      ${tu.outdated ? html`<p class="notice">cloudflared <b>${tu.outdated.current}</b> → <b>${tu.outdated.latest}</b> 업데이트가 나와 있다.</p>` : ""}
      <p class="subk">누적 응답 코드</p>${stackBar(cparts, "s")}${legend(cparts, "s")}`);

    // 트래픽 요약
    const sp = (t) => [["2xx", t.by_status?.["2xx"] || 0, "2xx"], ["3xx", t.by_status?.["3xx"] || 0, "3xx"], ["4xx", t.by_status?.["4xx"] || 0, "4xx"], ["5xx", t.by_status?.["5xx"] || 0, "5xx"]];
    const VIA = ["tunnel", "tailnet", "lan", "docker", "local"];
    const vp = (t) => {
      const bv = t.by_via || {};
      const keys = [...VIA.filter((k) => k in bv), ...Object.keys(bv).filter((k) => !VIA.includes(k))];
      return keys.map((k) => [VIA.includes(k) ? k : "other", bv[k] || 0, L.trafficVia[k] || k]);
    };
    put("ing-traffic", html`<h3>트래픽 <small>Caddy 접근 로그</small></h3>
      <div class="bigpair"><div><div class="k">최근 1시간</div><div class="v">${n0(t1.requests)}</div><div class="dim" style="font-size:12px">p50 ${ms(t1.p50_ms)} · p95 ${ms(t1.p95_ms)}</div></div>
        <div><div class="k">24시간</div><div class="v">${n0(t24.requests)}</div><div class="dim" style="font-size:12px">p50 ${ms(t24.p50_ms)} · p95 ${ms(t24.p95_ms)}</div></div></div>
      <p class="subk">상태 코드 · 24시간</p>${stackBar(sp(t24), "s")}${legend(sp(t24), "s")}
      <p class="subk">들어온 길 · 24시간</p>${stackBar(vp(t24), "v")}${legend(vp(t24), "v")}
      <p class="subk">들어온 길 · 1시간</p>${stackBar(vp(t1), "v")}${legend(vp(t1), "v")}`);

    // 막대 차트
    const pm = tr.per_minute || []; const pm5 = tr.per_minute_5xx || []; const ph = tr.per_hour || [];
    const barSvg = (vals, over, W, H, nowCls) => {
      const mx = niceMax(Math.max(1, ...vals.map((v) => +v || 0)));
      const bs = P.bars(vals, W, H, { max: mx, gap: vals.length > 40 ? 2 : 4 });
      const os = over ? P.bars(over, W, H, { max: mx, gap: vals.length > 40 ? 2 : 4 }) : [];
      return { mx, svg: html`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true"><line class="g" x1="0" y1="0" x2="${W}" y2="0"/><line class="g" x1="0" y1="${H / 2}" x2="${W}" y2="${H / 2}"/>
        ${bs.map((b, i) => (i === bs.length - 1 && nowCls ? html`<rect class="bnow" x="${(b.x + 0.5).toFixed(2)}" y="${b.y.toFixed(2)}" width="${Math.max(0.5, b.w - 1).toFixed(2)}" height="${b.h.toFixed(2)}"/>` : html`<rect class="b" x="${b.x.toFixed(2)}" y="${b.y.toFixed(2)}" width="${b.w.toFixed(2)}" height="${b.h.toFixed(2)}"/>`))}
        ${os.filter((b) => b.v > 0).map((b) => html`<rect class="b5" x="${b.x.toFixed(2)}" y="${b.y.toFixed(2)}" width="${b.w.toFixed(2)}" height="${b.h.toFixed(2)}"/>`)}
        <line class="base" x1="0" y1="${H}" x2="${W}" y2="${H}"/></svg>` };
    };
    const a = barSvg(pm, pm5, 600, 100, true);
    const b = barSvg(ph, null, 300, 100, true);
    const peakM = Math.max(0, ...pm.map((v) => +v || 0));
    put("traffic-charts", html`<div class="tchart"><h4>분당 요청 · 최근 60분 <span>최고 ${n0(peakM)}건/분 · 5xx ${n0(sum(pm5))}건 <span class="sr">(5xx 는 붉은 막대)</span></span></h4>
        <div class="plot" role="img" aria-label="최근 60분 분당 요청 막대그래프, 최고 ${n0(peakM)}건">${a.svg}<span class="ylab">${n0(a.mx)}</span></div><div class="xax"><span>60분 전</span><span>30분 전</span><span>지금 <span class="dim">(빈 막대 = 진행 중)</span></span></div></div>
      <div class="tchart"><h4>시간당 요청 · 24시간 <span>합계 ${n0(sum(ph))}건</span></h4>
        <div class="plot" role="img" aria-label="최근 24시간 시간당 요청 막대그래프">${b.svg}<span class="ylab">${n0(b.mx)}</span></div><div class="xax"><span>24시간 전</span><span>12시간 전</span><span>지금</span></div></div>`);

    // 호스트별 작은 그림
    const hmap = new Map();
    for (const h of (t24.hosts || []).filter(Boolean)) hmap.set(h.host, { host: h.host, d: h, h: null });
    for (const h of (t1.hosts || []).filter(Boolean)) { const x = hmap.get(h.host) || { host: h.host, d: null, h: null }; x.h = h; hmap.set(h.host, x); }
    const hosts = [...hmap.values()].sort((x, y) => (y.d?.requests || 0) - (x.d?.requests || 0) || (y.h?.requests || 0) - (x.h?.requests || 0));
    const maxReq = Math.max(1, ...hosts.map((x) => x.d?.requests || x.h?.requests || 0));
    const LMIN = Math.log10(0.1); const LMAX = Math.log10(2000);
    const lx = (v, W) => (clamp((Math.log10(Math.max(0.1, v)) - LMIN) / (LMAX - LMIN), 0, 1) * W);
    const W = 200;
    const HOST_TOP = 12;
    const hostCard = (x) => {
      const d = x.d || x.h || {}; const known = (x.d || x.h || {}).known !== false;
      const req = d.requests || 0;
      const e5 = d.errors_5xx || 0; const e4 = d.errors_4xx || 0; const okc = Math.max(0, req - e4 - e5);
      const sw = (req / maxReq) * W;
      const seg = (v) => (req ? (v / req) * W : 0);
      const p50 = d.p50_ms; const p95 = d.p95_ms;
      return { k: "h:" + x.host, h: html`<div class="host" data-key="${"h:" + x.host}">
        <span class="hn" title="${x.host}">${x.host || "(호스트 헤더 없음)"}</span>${known ? html`<span class="known">Caddy 등록</span>` : html`<span class="unk">미등록 호스트</span>`}
        <div class="hv"><span class="v">${n0(x.d ? x.d.requests : null)}</span><span class="s">1시간 ${n0(x.h ? x.h.requests : 0)}<br>p50 ${ms(p50)} · p95 ${ms(p95)}</span></div>
        <svg viewBox="0 0 ${W} 34" preserveAspectRatio="none" role="img" aria-label="${x.host}: 24시간 ${req}건, 4xx ${e4}, 5xx ${e5}">
          <rect class="share-bg" x="0" y="0" width="${W}" height="4"/><rect class="share" x="0" y="0" width="${sw.toFixed(1)}" height="4"/>
          <rect class="st2" x="0" y="10" width="${seg(okc).toFixed(1)}" height="5"/><rect class="st4" x="${seg(okc).toFixed(1)}" y="10" width="${seg(e4).toFixed(1)}" height="5"/><rect class="st5" x="${(seg(okc) + seg(e4)).toFixed(1)}" y="10" width="${seg(e5).toFixed(1)}" height="5"/>
          <line class="lat-ax" x1="0" y1="27" x2="${W}" y2="27"/>${[1, 10, 100, 1000].map((t) => html`<line class="lat-tk" x1="${lx(t, W).toFixed(1)}" y1="24" x2="${lx(t, W).toFixed(1)}" y2="30"/>`)}
          ${has(p50) && has(p95) ? html`<line class="lat" x1="${lx(p50, W).toFixed(1)}" y1="27" x2="${lx(p95, W).toFixed(1)}" y2="27"/><circle class="p50" cx="${lx(p50, W).toFixed(1)}" cy="27" r="2.6"/><circle class="p95" cx="${lx(p95, W).toFixed(1)}" cy="27" r="2.6"/>` : ""}
        </svg>
        <div class="hf"><span>${e5 ? html`<span class="crit">5xx ${n0(e5)}</span> · ` : ""}${e4 ? html`<span class="warn">4xx ${n0(e4)}</span>` : "오류 없음"}</span><span>${d.last_ts ? ago(d.last_ts) : ""}</span></div>
      </div>` };
    };
    if (!hosts.length) put("hosts", html`<p class="empty-note">기록된 요청이 없다.</p>`);
    else list("hosts", hosts.slice(0, HOST_TOP).map(hostCard));
    const restH = hosts.slice(HOST_TOP);
    const more = byId("hosts-more");
    if (more) {
      more.hidden = !restH.length;
      setText("hosts-more-n", restH.length ? `${restH.length}곳 · ${n0(sum(restH.map((x) => x.d?.requests || 0)))}건` : "");
      list("hosts-rest", restH.map(hostCard));
    }

    // 최근 요청
    const rec = (tr.recent || []).filter(Boolean);
    const knownHosts = new Set([...(cd.public_hosts || []), ...(cd.private_hosts || [])]);
    put("recent", rec.length ? html`<table class="ledger recent"><caption class="sr">최근 요청</caption><thead><tr><th scope="col">시각</th><th scope="col">들어온 길</th><th scope="col">상태</th><th scope="col">메서드</th><th scope="col">호스트</th><th scope="col">경로</th><th scope="col" class="num">응답</th><th scope="col" class="num">크기</th><th scope="col">클라이언트</th></tr></thead>
      <tbody>${rec.map((r) => { const sc = String(r.status || "")[0]; return html`<tr class="${knownHosts.size && r.host && !knownHosts.has(r.host) ? "unk" : ""}"><td class="nowrap">${fmt.time(r.ts)}</td><td class="viac ${r.via || ""}">${L.trafficVia[r.via] || r.via || ""}</td><td class="st s${sc}">${r.status}</td><td>${r.method || ""}</td><td class="hostc">${r.host || ""}</td><td class="path" title="${r.path || ""}">${r.path || ""}</td><td class="num">${ms(r.ms)}</td><td class="num">${fmt.bytes(r.bytes)}</td><td class="cl">${r.client || ""}${r.country ? " · " + r.country : ""}</td></tr>`; })}</tbody></table>` : html`<p class="empty-note">최근 요청 없음.</p>`);

    // 오류
    const errs = (tr.errors || []).filter(Boolean);
    put("caddy-errors", errs.length ? html`<ul class="errlist">${errs.slice(0, 8).map((e) => html`<li><p class="eh"><span class="lv ${e.level === "error" ? "error" : "warn"}">${e.level}</span><span>${fmt.datetime(e.ts)}</span><span class="mono">${e.logger || ""}</span></p><p class="em">${e.msg || ""}</p>${e.error ? html`<p class="ee">${e.error}</p>` : ""}</li>`)}</ul>${errs.length > 8 ? html`<p class="note-line">외 ${errs.length - 8}건 — 24시간 창</p>` : ""}` : html`<p class="empty-note">24시간 동안 Caddy 경고·오류 없음.</p>`);
    const te = (tu.recent_errors || []).filter(Boolean).slice().reverse();
    setText("tunnel-errors-n", te.length ? `${te.length}건 · 마지막 ${ago(te[0].ts)}` : "없음");
    put("tunnel-errors", te.length ? html`<ul class="errlist">${te.map((e) => html`<li><p class="eh"><span class="lv ${e.level === "error" ? "error" : "warn"}">${e.level}</span><span>${fmt.datetime(e.ts)}</span></p><p class="ee">${e.msg}</p></li>`)}</ul>` : html`<p class="empty-note">최근 cloudflared 오류 없음.</p>`);
  }

  // ================================================================ 06 Tailscale
  function renderTailscale(snap) {
    const ts = snap.tailscale || {}; const self = ts.self || {}; const peers = (ts.peers || []).filter(Boolean);
    const direct = peers.filter((p) => p.connection === "direct").length;
    const relay = peers.filter((p) => p.connection === "relay").length;
    const idle = peers.filter((p) => p.online && !p.connection).length;
    const running = ts.backend_state === "Running";
    setText("dek-ts", `${ts.backend_state || DASH} · 피어 ${n0(ts.peers_online)}/${n0(ts.peers_total)} 온라인 · v${ts.version || DASH}`);
    put("ts-top", html`<div class="ts-figs">
        <div class="kf"><div class="k">상태</div><div class="v" style="font-size:26px">${running ? html`${bare("ok")}Running` : html`<span class="t-crit">${bare("crit")}${ts.backend_state || DASH}</span>`}</div><div class="s">tailscaled ${ts.version || ""}</div></div>
        <div class="kf"><div class="k">피어 온라인</div><div class="v">${n0(ts.peers_online)}<small>/${n0(ts.peers_total)}</small></div><div class="s">오프라인 ${n0((ts.peers_total || 0) - (ts.peers_online || 0))}</div></div>
        <div class="kf"><div class="k">연결 방식</div><div class="v">${n0(direct)}<small> 직접</small></div><div class="s">릴레이 ${n0(relay)} · 유휴 ${n0(idle)}</div></div>
      </div>
      <div class="ts-self"><dl class="kv">
        <dt>이 기기</dt><dd><b>${self.hostname || DASH}</b> <span class="dim mono brk">${self.dns_name || ""}</span></dd>
        <dt>Tailnet IP</dt><dd class="mono">${(self.ips || []).length ? addrs(self.ips, " · ") : DASH}</dd>
        <dt>tailnet · MagicDNS</dt><dd class="brk">${ts.tailnet || DASH} · <span class="mono">${ts.magic_dns_suffix || DASH}</span></dd>
        <dt>DERP 릴레이</dt><dd>${self.relay || DASH}</dd>
        <dt>키 만료</dt><dd>${self.key_expiry ? fmt.datetime(self.key_expiry) : "만료 없음"}</dd>
        <dt>Serve · Funnel</dt><dd>${ts.serve ? "Serve 사용" : "Serve 없음"} · ${ts.funnel ? html`<span class="t-warn">Funnel 켜짐(인터넷 공개)</span>` : "Funnel 꺼짐"}</dd>
        <dt>상태 경고</dt><dd>${(ts.health || []).length ? (ts.health || []).filter(Boolean).map((h) => html`<div class="t-warn">${typeof h === "string" ? h : h.message || h.text || ""}</div>`) : "없음"}</dd>
      </dl></div>`);
    const CONN = { direct: "직접", relay: "릴레이" };
    list("peers", peers.map((p, i) => {
      const on = !!p.online;
      const conn = p.connection ? CONN[p.connection] || p.connection : on ? "유휴" : DASH;
      const key = "p:" + (p.id || p.dns_name || p.hostname + ":" + i);
      return { k: key, h: html`<tr class="${on ? "" : "off"}" data-key="${key}">
        <td>${on ? mk("up", false) : html`<span class="mk muted" role="img" aria-label="오프라인"></span>`}<span class="${on ? "" : "dim"}" style="font-size:12px">${on ? "온라인" : "오프라인"}</span></td>
        <td><span class="pn">${p.hostname || DASH}</span><span class="pd">${p.dns_name || ""}</span></td>
        <td>${p.os || DASH}</td><td class="ip">${p.ip || DASH}</td>
        <td class="conn ${p.connection || ""}">${conn}${p.active ? html` <span class="dim">· 활동</span>` : ""}</td>
        <td class="mono" style="font-size:11px">${p.cur_addr ? p.cur_addr : p.relay ? html`<span class="dim">홈 DERP ${p.relay}</span>` : DASH}</td>
        <td class="num">${p.rx_bytes || p.tx_bytes ? html`${fmt.bytes(p.rx_bytes)} / ${fmt.bytes(p.tx_bytes)}` : html`<span class="dim">${DASH}</span>`}</td>
        <td class="nowrap">${on ? html`<span class="dim">지금</span>` : p.last_seen_ago_s != null ? fmt.dur(p.last_seen_ago_s, 1) + " 전" : p.last_seen ? ago(p.last_seen) : DASH}</td>
        <td>${(p.tags || []).map((t) => html`<span class="tag">${t}</span>`)}${p.exit_node ? html`<span class="tag">exit</span>` : ""}</td></tr>` };
    }));
  }

  // ================================================================ 07 열린 포트
  function renderPorts(snap) {
    const ls = (snap.listeners || []).filter(Boolean); const sm = snap.summary?.listeners || {};
    const ORDER = ["all", "lan", "tailnet", "docker", "loopback", "other"];
    const GL = { all: "모든 인터페이스", lan: "LAN 주소", tailnet: "Tailnet 주소", docker: "Docker 브리지", loopback: "로컬(loopback)", other: "기타" };
    const GD = { all: "0.0.0.0 · :: — LAN·Tailnet·Docker·로컬 어디서나 닿는다", lan: "사설망에서 닿는다", tailnet: "tailnet 피어만", docker: "컨테이너에서만", loopback: "이 기기 안에서만", other: "" };
    const udp = ls.filter((l) => l.proto === "udp").length;
    const lanWide = ls.filter((l) => l.scope === "all" || l.scope === "lan");
    const tcpLan = lanWide.filter((l) => l.proto !== "udp").length;
    const udpLan = lanWide.length - tcpLan;
    const kf = (k, v, s2) => html`<div class="kf"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s2}</div></div>`;
    put("ports-sum", html`<div class="ps-grid">${kf("TCP 리스너", n0(sm.total ?? ls.filter((l) => l.proto !== "udp").length), "summary 기준")}${kf("LAN 노출 · TCP", n0(sm.lan ?? tcpLan), "모든 인터페이스·LAN 바인딩")}${kf("Tailnet 전용", n0(sm.tailnet_only), "100.x · fd7a:")}${kf("로컬 전용", n0(sm.loopback), "127.0.0.1 · ::1")}${kf("UDP", n0(udp), html`LAN 노출 ${n0(udpLan)} · mDNS · WireGuard 등`)}</div>`);
    setText("dek-ports", `LAN 에서 닿는 포트 — TCP ${n0(sm.lan ?? tcpLan)} · UDP ${n0(udpLan)} · 굵은 막대로 표시`);
    const OWN = { container: "컨테이너", unit: "유닛", "user-unit": "사용자 유닛", scope: "스코프", unknown: "알 수 없음" };
    const REACH = ["lan", "tailnet", "docker", "local"];
    const RL = { lan: "LAN", tailnet: "Tailnet", docker: "Docker", local: "로컬" };
    const items = [];
    for (const g of ORDER) {
      const rows = ls.filter((l) => (ORDER.includes(l.scope) ? l.scope : "other") === g).sort((a, b) => (a.proto === b.proto ? (a.port || 0) - (b.port || 0) : a.proto === "tcp" ? -1 : 1));
      if (!rows.length) continue;
      items.push({ k: "g:" + g, h: html`<tr class="grp" data-key="${"g:" + g}"><th colspan="6">${GL[g]}<span>${rows.length}개 · ${GD[g]}</span></th></tr>` });
      for (const l of rows) {
        const key = `${l.proto}:${l.port}:${(l.addrs || []).join(",")}:${l.pid || ""}`;
        const wide = g === "all" || g === "lan";
        const reach = REACH.filter((r) => (l.reach || []).includes(r)).concat((l.reach || []).filter((r) => !REACH.includes(r))).map((r) => RL[r] || r).join(" · ");
        // "모든 인터페이스" 무리는 머리줄이 이미 도달 범위를 말하므로 배지만 남긴다
        const scopeTxt = g === "all" ? "" : reach || DASH;
        items.push({ k: key, h: html`<tr class="prow ${wide ? "lanwide" : ""} ${g === "loopback" ? "loop" : ""}" data-key="${key}">
          <td class="num c-pt"><span class="pt">${has(l.port) ? l.port : DASH}</span></td><td class="c-pr">${String(l.proto || "").toUpperCase()}</td>
          <td class="scope">${scopeTxt ? html`<span class="sc-t">${scopeTxt}</span>` : ""}${wide ? html`<span class="scope-all">LAN 노출</span>` : ""}</td>
          <td class="addr">${addrs(l.addrs)}</td>
          <td class="proc" title="${l.process || ""}${l.pid ? " " + l.pid : ""}"><span class="pname">${l.process || DASH}${l.pid ? html` <span class="dim">${l.pid}</span>` : ""}</span></td>
          <td class="own"><span class="ok">${OWN[l.owner_kind] || l.owner_kind || ""}</span><span class="on">${l.owner || DASH}</span>${l.cwd ? html`<span class="dim mono cwd" title="${l.cwd}">${l.cwd}</span>` : ""}</td></tr>` });
      }
    }
    list("ports", items);
  }

  // ================================================================ 08 컨테이너·유닛
  function renderRuntime(snap) {
    const ctrs = (snap.containers || []).filter(Boolean); const projs = (snap.projects || []).filter(Boolean); const units = (snap.units || []).filter(Boolean);
    const hostUp = snap.system?.uptime_s;
    // 시계 동기화 전(fake-hwclock) 시각으로 찍힌 가동 시간은 호스트 가동 시간을 넘을 수 없다
    const upSince = (v) => (has(v) && has(hostUp) && v >= hostUp - 5 ? html`<span class="dim">부팅 시부터</span>` : upt(v));
    const ct = snap.summary?.containers || {};
    const failed = units.filter((u) => u.active === "failed");
    setText("dek-rt", `컨테이너 ${n0(ct.running)}/${n0(ct.total)} 가동 · compose ${n0(projs.length)} · 유닛 ${n0(units.length)} (실패 ${n0(failed.length)})`);
    const PST = { running: "up", partial: "degraded", stopped: "down" };
    const PSL = { running: "모두 가동", partial: "일부 가동", stopped: "정지" };
    list("projects", projs.map((p) => ({ k: p.name, h: html`<div class="proj" data-key="${p.name}"><span class="pc">${n0(p.running)}/${n0(p.total)}</span>
      <p class="pn">${mk(PST[p.status] || "unknown", false)}${p.name}</p><p class="ps">${PSL[p.status] || p.status || ""} · ${(p.containers || []).join(", ")}</p><p class="pd" title="${p.dir || ""}">${p.dir || ""}</p></div>` })));
    const cst = (c) => (c.state !== "running" ? (c.state === "restarting" ? "degraded" : "down") : c.health === "unhealthy" ? "down" : (c.restart_count || 0) >= 3 && (c.uptime_s || 0) < 3600 ? "degraded" : "up");
    const HL = { healthy: "정상", unhealthy: "이상", starting: "확인 중" };
    const sorted = ctrs.slice().sort((a, b) => { const r = { down: 0, degraded: 1, up: 2 }; return r[cst(a)] - r[cst(b)] || String(a.project || "").localeCompare(String(b.project || "")) || String(a.name || "").localeCompare(String(b.name || "")); });
    list("ctrs", sorted.map((c) => ({ k: c.id || c.name, h: html`<tr data-key="${c.id || c.name}">
      <td>${mk(cst(c), false)}<span style="font-size:12px">${c.state === "running" ? "실행" : c.state}</span>${c.exit_code && c.state !== "running" ? html` <span class="t-crit">(${c.exit_code})</span>` : ""}${c.oom_killed ? html` <span class="t-crit">OOM</span>` : ""}</td>
      <td><span class="cn">${c.name}</span><span class="cp">${c.project ? c.project + " / " + (c.service || "") : "compose 아님"}</span></td>
      <td class="img" title="${c.image || ""}">${c.image || ""}</td>
      <td class="hl ${c.health || ""}">${c.health ? HL[c.health] || c.health : html`<span class="dim">검사 없음</span>`}</td>
      <td class="num">${upSince(c.uptime_s)}</td>
      <td class="num rs ${c.restart_count ? "warn" : ""}">${n0(c.restart_count)}</td>
      <td class="num">${pct(c.cpu_pct, 1)}</td><td class="num">${fmt.bytes(c.mem_bytes)}</td>
      <td class="pp">${(c.ports || []).slice().sort((a, b) => (a.public == null) - (b.public == null)).map((p, i) => html`${i ? html`<br>` : ""}${p.public != null ? html`${p.ip ? p.ip + ":" : ""}${p.public}→${p.private}/${p.proto}` : html`<span class="dim">${p.private}/${p.proto} 내부</span>`}`)}${!(c.ports || []).length ? html`<span class="dim">${c.network_mode === "host" ? "host 네트워크" : "노출 없음"}</span>` : ""}</td></tr>` })));
    const SUB = { running: "실행 중", exited: "완료", waiting: "대기", listening: "대기(소켓)", mounted: "마운트", plugged: "연결됨" };
    const UC = { app: "앱", server: "서버", infra: "인프라", system: "시스템", desktop: "데스크톱" };
    const main = units.filter((u) => u.active === "failed" || ["app", "server", "infra"].includes(u.category));
    const rest = units.filter((u) => !main.includes(u));
    const ust = (u) => (u.active === "failed" ? "down" : u.active === "active" ? "up" : "unknown");
    const urow = (u) => { const key = u.scope + ":" + u.name; return { k: key, h: html`<tr class="${u.active === "failed" ? "failed" : ""}" data-key="${key}">
      <td>${mk(ust(u), false)}<span style="font-size:12px">${u.active === "failed" ? "실패" : u.active === "active" ? SUB[u.sub] || u.sub || "활성" : u.active}</span></td>
      <td><span class="un">${u.name}</span><span class="ud">${u.description || ""}</span></td>
      <td>${u.scope === "user" ? "사용자" : "시스템"}</td><td>${UC[u.category] || u.category || ""}</td>
      <td class="num">${u.active === "failed" ? html`<span class="dim">${u.since ? ago(u.since) : DASH}</span>` : upSince(u.uptime_s)}</td>
      <td class="num ${u.restarts ? "t-warn" : ""}">${n0(u.restarts)}</td><td class="num">${pct(u.cpu_pct, 1)}</td><td class="num">${fmt.bytes(u.mem_bytes)}</td></tr>` }; };
    list("units", main.map(urow));
    list("units-rest", rest.map(urow));
    setText("units-note", `실패 ${failed.length}개 먼저 · 앱·서버·인프라 ${main.length - failed.length}개 · 나머지는 접어 둠`);
    setText("units-more-n", `${rest.length}개`);
  }

  // ================================================================ 판권(colophon)
  function renderColophon(snap) {
    const meta = snap.meta || {};
    const ca = meta.collected_at || {};
    const gen = meta.generated_at || now();
    const errs = Object.entries(meta.errors || {});
    const SRCN = { system_fast: "시스템(빠름)", system: "시스템", processes: "프로세스", docker: "Docker", systemd: "systemd", listeners: "리스너", tailscale: "Tailscale", tunnel: "터널", caddy_log: "Caddy 로그", caddy: "Caddy 설정", probes: "프로브", hermes: "Hermes", dns: "DNS", http_ports: "HTTP 포트" };
    const rel = (ts) => { const d = Math.max(0, gen - ts); return d < 60 ? `${Math.round(d)}초 전` : d < 3600 ? `${Math.floor(d / 60)}분 전` : `${Math.floor(d / 3600)}시간 전`; };
    const lim = (k) => (k === "dns" || k === "http_ports" ? 900 : 120);
    put("colophon", html`<div class="cb wide"><h4>취재원 · 마지막 수집</h4>
        <div class="src-list">${Object.entries(ca).map(([k, v]) => html`<div><span>${SRCN[k] || k}</span><span class="${gen - v > lim(k) ? "old" : "dim"}">${rel(v)}</span></div>`)}</div></div>
      <div class="cb"><h4>수집 오류</h4>${errs.length ? html`<ul class="errs">${errs.map(([k, v]) => html`<li><b>${k}</b> — ${v}</li>`)}</ul>` : html`<p>없음 — 모든 취재원이 응답했다.</p>`}
        <h4 style="margin-top:12px">발행</h4><p>Jarvis ${meta.version || ""} · Jarvis 가동 ${upt(gen - (meta.started_at || gen))}<br>스냅샷 ${fmt.datetime(gen)} · ${P.embed ? 10 : 3}초마다 새로 찍는다<br>읽기 전용 — LAN·tailnet 에서만 열린다</p></div>
      <p class="sig">— Ledger 판 · 인쇄해도 그대로 읽히도록 조판했다 —</p>`);
  }

  // ================================================================ 메인 루프
  /** 지면 하나를 새로 그리지 못했으면 그 지면에 표시한다 — 멈춘 숫자가 실시간처럼 보이지 않게. */
  function markSection(id, err, snap) {
    const sec = id && byId(id);
    if (!sec) return;
    const bad = !!err;
    if (sec.classList.contains("sec-failed") !== bad) sec.classList.toggle("sec-failed", bad);
    let note = sec.querySelector(":scope > .sec-err");
    if (!bad) { if (note && !note.hidden) note.hidden = true; return; }
    if (!note) {
      note = doc.createElement("p");
      note.className = "sec-err";
      note.setAttribute("role", "status");
      const head = sec.querySelector(":scope > .sec-head");
      if (head) head.after(note); else sec.prepend(note);
    }
    note.hidden = false;
    if (!sec.__failAt) sec.__failAt = S.lastGood?.[id] || null;
    setText(note, `이 지면을 새로 그리지 못했다 — ${sec.__failAt ? clock(sec.__failAt) : "이전"} 자료가 남아 있다.`);
  }
  function renderAll(snap, st) {
    const steps = [
      [null, () => renderMast(snap, st)], ["s-summary", () => renderFront(snap)], ["s-hw", () => renderHardware(snap)], ["s-svc", () => renderServices(snap)],
      ["s-map", () => { renderMap(snap); renderRoutes(snap); }], ["s-ing", () => renderIngress(snap)],
      ["s-ts", () => renderTailscale(snap)], ["s-ports", () => renderPorts(snap)], ["s-rt", () => renderRuntime(snap)], [null, () => renderColophon(snap)],
    ];
    S.lastGood = S.lastGood || {};
    const gen = snap.meta?.generated_at || now();
    for (const [id, f] of steps) {
      try {
        f();
        if (id) { S.lastGood[id] = gen; const sec = byId(id); if (sec) sec.__failAt = null; markSection(id, null, snap); }
      } catch (e) {
        console.warn("[ledger] 렌더 실패", id || "", e);
        markSection(id, e, snap);
      }
    }
  }

  function onSnap(snap, st) {
    S.snap = snap; S.st = st;
    if (!st.stale) S.lastOk = st.fetchedAt || now();
    renderLive();
    renderAll(snap, st);
    loadHist(false);
    if (S.hist.data) drawHist();   // 기록이 바뀌었거나 폭이 달라졌을 때만 실제로 다시 그린다
    readouts();                    // 제목 옆 값은 매 스냅샷의 실시간 값
    pulseLive();
  }

  function init() {
    try { const r = localStorage.getItem("ledger.range"); if (r === "24h" || r === "1h") S.hist.range = r; } catch (e) { /* 무시 */ }
    doc.querySelectorAll(".seg button[data-range]").forEach((x) => x.setAttribute("aria-pressed", String(x.getAttribute("data-range") === S.hist.range)));
    buildPanels();
    bindMap();
    // 좁은 화면 ↔ 넓은 화면을 넘나들면 서비스 행 모양을 바꾼다
    const onMq = () => { if (S.snap) { try { renderServices(S.snap); } catch (e) { console.warn("[ledger] 렌더 실패", e); } } };
    if (MQ_M.addEventListener) MQ_M.addEventListener("change", onMq);
    if (COARSE.addEventListener) COARSE.addEventListener("change", () => { if (S.snap) renderMap(S.snap, true); });
    // 러닝 헤드: 마스트헤드가 화면을 벗어나면 나타난다
    const rh = byId("runhead"); const mast = $(".masthead");
    if (rh && mast && "IntersectionObserver" in window) {
      new IntersectionObserver((ents) => { for (const e of ents) rh.classList.toggle("on", !e.isIntersecting); }, { threshold: 0 }).observe(mast);
    }
    // 인쇄할 때는 접힌 것을 모두 펼친다
    const opened = [];
    window.addEventListener("beforeprint", () => { doc.querySelectorAll("details:not([open])").forEach((d) => { d.open = true; opened.push(d); }); });
    window.addEventListener("afterprint", () => { while (opened.length) opened.pop().open = false; });

    P.subscribe(onSnap, {
      onError: (e) => {
        S.failed = true;
        renderLive();
        if (!S.snap) put("front-hed", html`<p class="placeholder">스냅샷을 받지 못했다 — ${e && e.message ? e.message : "연결 오류"}. 잠시 뒤 다시 시도한다.</p>`);
      },
    });
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", init); else init();
})();
