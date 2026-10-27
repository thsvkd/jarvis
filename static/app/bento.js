/* Jarvis · 홈(개요) 렌더러 — "/".
 *
 * 순서: 개요(건강 · 경보 · 네트워크 맵 미리보기) → 서비스 → 하드웨어 → 네트워크(인그레스·트래픽·포트·Tailscale) → 런타임.
 * 공용 헤더(/app/shell.js)가 JARVIS.subscribe 를 감싸 헤더를 먼저 갱신하고, 여기 cb 가 나머지를 그린다.
 * 네트워크 맵 미리보기는 /app/home-map.js(JarvisHomeMap)가 그린다.
 *
 * 맵과 오가기
 *  - 서비스 카드·목록 행·경보·상세 시트의 "맵에서 보기" → /map/?view=concept#focus=<서비스 id>(노출 경로)
 *    런타임·하드웨어 쪽("어디서 도나") → /map/?view=physical#focus=<서비스 id | 기기 id>
 *  - /#svc=<서비스 id> 로 열리면(맵의 인스펙터 링크) 그 서비스의 상세 시트를 연다. hashchange 도 따른다.
 *    시트를 닫으면 주소의 #svc= 를 지운다. 갱신(3초)은 해시를 건드리지 않는다.
 *
 * 원칙
 *  - 데이터 문자열은 전부 JARVIS.html(자동 이스케이프) 또는 textContent 로만 DOM 에 들어간다.
 *  - 상호작용하는 요소(카드 버튼·세그먼트·탭·검색창·시트)는 한 번만 만들고 값만 바꾼다(keyed).
 *    → 3초마다 갱신돼도 포커스·스크롤·열린 패널이 유지된다.
 *  - 애니메이션은 숫자 트윈(rAF, 끝나면 멈춤)과 CSS 전환뿐. 임베드·모션 줄이기면 끈다.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  if (!P) {
    const be = document.getElementById("bootError");
    if (be) be.hidden = false;
    return;
  }
  const { html, raw, fmt, labels } = P;
  // 공용 제품 도구(/app/shell.js) — 두 페이지가 같은 판정·같은 이름·같은 숫자를 쓰게. 셸이 없으면 최소한으로 대신한다.
  const SH = window.JarvisShell || {};
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const docEl = document.documentElement;
  if (P.embed) docEl.classList.add("embed");
  const mqReduce = window.matchMedia("(prefers-reduced-motion: reduce)");
  const calm = () => P.embed || mqReduce.matches;
  const nowS = () => Date.now() / 1000;
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const uniq = (a) => Array.from(new Set(a));
  const n0 = (x) => fmt.num(x, 0);

  const store = {
    get(k, d) {
      if (window.JarvisLab) return window.JarvisLab.getPref(k, d);
      try {
        let v = P.pref("home." + k);
        if (v == null) v = P.pref("bento." + k);
        return v == null ? d : v;
      } catch (e) { return d; }
    },
    set(k, v) {
      if (window.JarvisLab) { window.JarvisLab.setPref(k, v); return; }
      try { localStorage.setItem("jarvis.home." + k, v); } catch (e) { /* 저장 불가(사생활 모드 등) — 무시 */ }
    },
  };

  // ------------------------------------------------------------------ 아이콘(상수 SVG — 데이터 없음)
  const ICON = {
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
    tailnet: '<g stroke="none" fill="currentColor"><circle cx="5" cy="12" r="2.4"/><circle cx="12" cy="12" r="2.4"/><circle cx="19" cy="12" r="2.4"/><circle cx="12" cy="19" r="2.4"/><circle cx="5" cy="5" r="2.4" opacity=".35"/><circle cx="12" cy="5" r="2.4" opacity=".35"/><circle cx="19" cy="5" r="2.4" opacity=".35"/><circle cx="5" cy="19" r="2.4" opacity=".35"/><circle cx="19" cy="19" r="2.4" opacity=".35"/></g>',
    server: '<rect x="3.5" y="4" width="17" height="7" rx="2"/><rect x="3.5" y="13" width="17" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
    db: '<ellipse cx="12" cy="6" rx="7.5" ry="3"/><path d="M4.5 6v12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3V6M4.5 12c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3"/>',
    layers: '<path d="M12 3.5 21 8l-9 4.5L3 8z"/><path d="m3 12.2 9 4.5 9-4.5"/><path d="m3 16.4 9 4.5 9-4.5"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.6M12 18.6v2.6M4.2 7.4l2.3 1.3M17.5 15.3l2.3 1.3M4.2 16.6l2.3-1.3M17.5 8.7l2.3-1.3"/><circle cx="12" cy="12" r="7.2"/>',
    lan: '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 16.5h.01M10.5 16.5h.01M8 13V9.5M16 13V6"/>',
    lock: '<rect x="5" y="10.5" width="14" height="10" rx="2.2"/><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5"/>',
    ext: '<path d="M7 17 17 7M9 7h8v8"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    x: '<path d="M17 7 7 17M7 7l10 10"/>',
    warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    crit: '<path d="M7.9 2.5h8.2l5.4 5.4v8.2l-5.4 5.4H7.9l-5.4-5.4V7.9z"/><path d="M12 8v4.5M12 16h.01"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4.5M12 8h.01"/>',
    okc: '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9"/>',
    route: '<circle cx="5.5" cy="6" r="2.5"/><circle cx="18.5" cy="18" r="2.5"/><path d="M8 6h7.5a3 3 0 0 1 0 6h-7a3 3 0 0 0 0 6H16"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    map: '<circle cx="5.5" cy="6" r="2.3"/><circle cx="5.5" cy="18" r="2.3"/><circle cx="18.5" cy="12" r="2.3"/><path d="M7.8 6h2.7a3 3 0 0 1 3 3v0a3 3 0 0 0 3 3M7.8 18h2.7a3 3 0 0 0 3-3v0a3 3 0 0 1 3-3"/>',
  };
  const ic = (n, cls) =>
    raw('<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (ICON[n] || "") + "</svg>");

  const CAT_ICON = { public: "globe", tailnet: "tailnet", hermes: "route", host: "server", internal: "db", infra: "layers", system: "gear" };
  const AUD_CLS = { public: "b-public", tailnet: "b-tailnet", lan: "b-lan", local: "b-local" };
  const AUD_IC = { public: "globe", tailnet: "tailnet", lan: "lan", local: "lock" };
  const SRC = {
    system: "시스템", container: "컨테이너", systemd: "systemd", route: "라우트", exposure: "노출", dns: "DNS", cert: "인증서",
    tunnel: "터널", caddy: "Caddy", traffic: "트래픽", tailscale: "Tailscale", hermes: "Hermes", monitor: "모니터",
  };
  const PROBE_ORDER = ["internal", "tunnel", "public"];
  const PROBE_L = { internal: "Tailnet 경로 · Caddy :443", tunnel: "터널 경로 · Caddy :80", public: "공개 경로 · Cloudflare 엣지" };
  /** 프로브 판정(공용): { tone, chip, value, text, notes, title, blocked, retry } */
  const verdict = (p, k, sv) => (SH.probeVerdict ? SH.probeVerdict(p, k, sv) : p ? { tone: p.ok ? "ok" : "crit", chip: k, value: p.code != null ? String(p.code) : "실패", text: String(p.code), notes: [], title: k } : null);
  const DNS_L = { cloudflare: "Cloudflare", tailnet: "Tailnet", none: "없음", other: "기타", error: "오류" };
  const OWNER_L = { container: "컨테이너", unit: "systemd", "user-unit": "사용자 유닛", scope: "앱·세션 스코프", unknown: "알 수 없음" };
  const REACH_L = { lan: "LAN", tailnet: "TN", docker: "Docker", local: "로컬" };
  const STATE_L = SH.STATE_L || { running: "실행 중", exited: "종료됨", restarting: "재시작 중", paused: "일시정지", created: "생성됨", dead: "중단", removing: "제거 중" };
  const HEALTH_L = SH.HEALTH_L || { healthy: "헬스 정상", unhealthy: "헬스 비정상", starting: "헬스 확인 중" };
  const PROJ_L = { running: "실행 중", partial: "일부 실행", stopped: "중지" };
  const VIA_ORDER = ["tunnel", "tailnet", "lan", "docker", "local"];

  // ------------------------------------------------------------------ DOM 도우미
  const F = Object.create(null);
  $$("[data-f]").forEach((el) => { F[el.getAttribute("data-f")] = el; });
  const elOf = (k) => (typeof k === "string" ? F[k] : k);
  function txt(k, s) {
    const el = elOf(k);
    if (!el) return;
    s = s == null ? "–" : String(s);
    if (el.textContent !== s) el.textContent = s;
  }
  function tone(el, t) { if (el && el.getAttribute("data-tone") !== t) el.setAttribute("data-tone", t); }
  function attr(el, k, v) { if (el && el.getAttribute(k) !== v) el.setAttribute(k, v); }
  function show(el, on) { if (el && el.hidden === !!on) el.hidden = !on; }
  function cssVar(el, name, v) {
    if (!el) return;
    const key = "_css" + name;
    if (el[key] === v) return;
    el[key] = v;
    el.style.setProperty(name, v);
  }
  function mk(tag, cls, content) {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (content != null) P.render(el, content);
    return el;
  }
  /** 키가 같은 요소는 재사용하고 순서만 맞춘다. 사라진 키는 지운다. */
  function keyed(box, items, keyOf, create, update) {
    const map = box._keyed || (box._keyed = new Map());
    const seen = new Set();
    let prev = null;
    for (const it of items) {
      const k = keyOf(it);
      if (seen.has(k)) continue;
      seen.add(k);
      let el = map.get(k);
      if (!el) { el = create(it); map.set(k, el); }
      update(el, it);
      const want = prev ? prev.nextElementSibling : box.firstElementChild;
      if (want !== el) box.insertBefore(el, want);
      prev = el;
    }
    map.forEach((el, k) => { if (!seen.has(k)) { el.remove(); map.delete(k); } });
  }

  // ------------------------------------------------------------------ 숫자 트윈
  const tweens = new Map();
  let rafId = 0;
  function num(k, v, f) {
    const el = elOf(k);
    if (!el) return;
    f = f || n0;
    if (!isNum(v)) { tweens.delete(el); el._v = null; txt(el, f(null)); return; }
    const from = el._v;
    el._v = v;
    if (!isNum(from) || from === v || calm() || document.hidden) { tweens.delete(el); txt(el, f(v)); return; }
    tweens.set(el, { from, to: v, t0: performance.now(), f });
    if (!rafId) rafId = requestAnimationFrame(step);
  }
  // 글자가 바뀌면 그 칸의 레이아웃이 다시 돈다 — 매 프레임이 아니라 90ms 마다만 쓴다(끝값은 늘 바로)
  let tweenW = 0;
  function step(t) {
    rafId = 0;
    const write = t - tweenW >= 90;
    if (write) tweenW = t;
    tweens.forEach((tw, el) => {
      const k = clamp((t - tw.t0) / 650, 0, 1);
      if (k >= 1) { txt(el, tw.f(tw.to)); tweens.delete(el); return; }
      if (write) txt(el, tw.f(tw.from + (tw.to - tw.from) * (1 - Math.pow(1 - k, 3))));
    });
    if (tweens.size) rafId = requestAnimationFrame(step);
  }

  // ------------------------------------------------------------------ 작은 조각
  const pill = (st, text) =>
    html`<span class="pill" data-tone="${P.tone(st)}"><i class="shape"></i>${text != null ? text : labels.status[st] || "확인 불가"}</span>`;
  const audBadge = (a) => html`<span class="badge ${AUD_CLS[a] || ""}">${ic(AUD_IC[a] || "globe")}${labels.audience[a] || a || "?"}</span>`;
  const shortUrl = (u) => String(u || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");
  /** 새 창 외부 링크. 안전하지 않은 주소(javascript: 등)는 링크를 걸지 않고 글자로만 보인다. */
  const extLink = (url, content, cls) => {
    const u = P.safeUrl(url);
    if (u === "#") return html`<span class="${(cls || "") + " is-invalid"}" title="안전하지 않은 주소라 링크를 걸지 않았어요">${content}</span>`;
    return html`<a class="${cls || ""}" href="${u}" target="_blank" rel="noopener noreferrer">${content}<span class="sr"> (새 창)</span></a>`;
  };
  function expBadges(sv) {
    const e = sv.exposure || {};
    const out = [];
    if (e.public) out.push(audBadge("public"));
    if (e.tailnet) out.push(audBadge("tailnet"));
    if (e.lan) out.push(audBadge("lan"));
    if (e.local_only) out.push(audBadge("local"));
    return out;
  }
  function stackBar(parts, cls) {
    // style 속성에 들어가는 값은 반드시 숫자로 강제한다(문자열이 CSS 로 섞여 들지 않게).
    const nums = parts.map((p) => [isNum(+p[0]) ? Math.max(0, +p[0]) : 0, p[1], p[2]]);
    const tot = nums.reduce((a, p) => a + p[0], 0);
    return html`<div class="stack ${cls || ""}">${tot > 0 ? nums.filter((p) => p[0] > 0).map((p) => html`<i class="${p[1]}" style="flex-grow:${p[0].toFixed(3)}" title="${p[2] || ""}${p[2] ? " " : ""}${fmt.num(p[0])}"></i>`) : ""}</div>`;
  }
  function niceCeil(v) {
    if (!(v > 0)) return 1;
    const e = Math.pow(10, Math.floor(Math.log10(v)));
    const f = v / e;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * e;
  }
  /** 스파크라인/영역 차트 SVG. 경로에는 숫자만 들어간다. */
  function areaSvg(series, o) {
    const w = o.w, h = o.h, min = o.min || 0;
    let max = o.max;
    if (max == null) {
      let m = 0;
      series.forEach((s) => (s.v || []).forEach((v) => { if (isNum(v) && v > m) m = v; }));
      max = niceCeil(m * 1.15) || 1;
    }
    let g = "";
    (o.grid || []).forEach((gv) => {
      const y = (h - ((gv - min) / (max - min)) * h).toFixed(1);
      g += '<line class="grid-ln" x1="0" x2="' + w + '" y1="' + y + '" y2="' + y + '"/>';
    });
    let p = "";
    series.forEach((s, i) => {
      const d = P.sparkPath(s.v, w, h, { min, max });
      if (!d) return;
      if (i === 0 && o.area !== false) p += '<path class="ar ' + s.cls + '" d="' + P.sparkPath(s.v, w, h, { min, max, area: true }) + '"/>';
      p += '<path class="ln ' + s.cls + (o.thin ? " thin" : "") + '" d="' + d + '"/>';
    });
    return raw('<svg viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" aria-hidden="true" focusable="false">' + g + p + "</svg>");
  }
  /** 네트워크 맵에서 이 서비스(노드)를 골라 보여 주는 주소. view: "concept"(어떻게 노출되나 — 기본) | "physical"(어디서 도나) */
  const mapHref = (id, view) => (SH.mapView ? SH.mapView.href(view || "concept", id) : "/map/?view=" + (view || "concept") + "#focus=" + encodeURIComponent(String(id)));
  const mapLink = (id, name, cls, text, view) =>
    html`<a class="${cls}" href="${mapHref(id, view)}" aria-label="${(name || id) + " — 네트워크 맵(" + (view === "physical" ? "물리" : "개념") + ")에서 보기"}" title="${view === "physical" ? "물리 지도에서 어디서 도는지 보기" : "개념 지도에서 노출 경로 보기"}">${ic("map")}${text === false ? "" : html`<span>${text || "맵에서 보기"}</span>`}</a>`;
  const codeTone = (c) => (!isNum(c) ? "muted" : c >= 500 ? "crit" : c >= 400 ? "warn" : c >= 300 ? "info" : "ok");

  // 테마 토글·전체 상태·실시간 표시·접속 주소는 공용 헤더(/app/shell.js)가 맡는다.

  // ------------------------------------------------------------------ 상태
  let S = null;
  let ST = { stale: false };
  let lastOk = 0;
  let firstDone = false;
  // 보기 방식: 고른 적이 없으면 휴대폰(700px 미만)은 촘촘한 목록, 그 밖은 카드
  const savedView = store.get("svcView", null);
  const SV = { filter: String(store.get("svcFilter", "all")).replace(/^openclaw$/, "hermes"), q: "", view: savedView === "list" || savedView === "cards" ? savedView : window.matchMedia("(max-width: 699px)").matches ? "list" : "cards" };
  const TR = { range: store.get("trRange", "1h"), tab: store.get("trTab", "hosts") };
  const PF = { f: store.get("portFilter", "all") };
  const UFL = { f: store.get("unitFilter", "all") };
  const H = { range: store.get("hRange", "1h"), metric: store.get("hMetric", "cpu"), data: null, at: 0, loading: false, err: null, hoverX: null, geom: null };
  // 지도 관점은 지도와 한 벌로 기억한다(JarvisShell.mapView — 개요에서 고르면 헤더의 '네트워크 맵'도 그 관점으로 열린다)
  const MV = { v: SH.mapView ? SH.mapView.get(store.get("mapView", "concept")) : store.get("mapView", "concept") === "physical" ? "physical" : "concept" };

  // ------------------------------------------------------------------ 페이지 메타(제목·하드웨어 머리말)
  function renderMeta(s) {
    const m = s.meta || {};
    const host = (s.system && s.system.host) || {};
    const name = m.host || host.hostname || "이 기기";
    txt("pageTitle", name + " 개요");
    const bits = [host.model, host.os, host.kernel ? "커널 " + host.kernel : null, host.arch, host.cores ? host.cores + "코어" : null, host.gpu_name].filter(Boolean);
    txt("hw.sub", bits.join(" · ") || "CPU · GPU · 메모리 · 온도 · 네트워크 · 저장소");
    txt("lnav.svc", fmt.num((s.services || []).length));
    updateLive();
  }
  // 안내 띠: API 응답 없음(심각) · 응답 지연(경고) · API 는 정상인데 일부 수집기가 늦음(경고, 자기 진단 링크)
  function updateLive() {
    if (!S) return;
    const gen = S.meta && S.meta.generated_at;
    const age = nowS() - lastOk;
    const late = !ST.stale && age > 20;
    const off = ST.stale || late;
    const Hh = !off && SH.monitor ? SH.monitor.health(S) : null;
    const partial = !!(Hh && Hh.known && !Hh.ok);
    const banner = $("#staleBanner");
    const show = off || partial;
    if (banner.hidden === show) banner.hidden = !show;
    const go = $("#staleGo");
    if (off) {
      tone(banner, ST.stale ? "crit" : "warn");
      txt("stale.head", ST.stale ? "Jarvis API 가 응답하지 않아요." : "Jarvis 응답이 늦어지고 있어요.");
      txt("stale.msg", "마지막 데이터 " + fmt.time(gen) + " (" + fmt.ago(lastOk) + ")를 보여 주는 중이에요. 자동으로 다시 연결하고 있어요.");
      if (!go.hidden) go.hidden = true;
    } else if (partial) {
      tone(banner, "warn");
      txt("stale.head", "일부 수집이 늦어지고 있어요.");
      txt("stale.msg", Hh.problems.slice(0, 4).map((c) => SH.monitor.collectorName(c.name)).join(" · ") + " 값은 오래된 것일 수 있어요. API 는 정상이에요.");
      const sv = SH.monitor.svc(S);
      if (go.hidden !== !sv) go.hidden = !sv;
      if (sv) attr(go, "href", "#svc=" + encodeURIComponent(sv.id));
    }
    docEl.classList.toggle("is-stale", off);
  }

  // ------------------------------------------------------------------ 히어로
  function renderHero(s) {
    const sum = s.summary || {};
    const a = sum.alerts || {};
    const sv = sum.services || {};
    const t = P.tone(sum.status);
    tone($(".hero"), t === "muted" ? "ok" : t);
    txt("hero.eyebrow", "지금 " + ((s.meta && s.meta.host) || "이 기기") + "는");
    let sent;
    if (sum.status === "crit") sent = html`심각한 문제가 <em>${fmt.num(a.crit)}건</em> 있어요.`;
    else if (sum.status === "warn") sent = html`살펴볼 경고가 <em>${fmt.num(a.warn)}건</em> 있어요.`;
    else if (sum.status === "ok") sent = html`모든 것이 <em>순조로워요.</em>`;
    else sent = html`상태를 <em>확인하고 있어요.</em>`;
    P.render($("#heroSentence"), sent);

    const total = sv.total || 0, up = sv.up || 0, deg = sv.degraded || 0, down = sv.down || 0;
    const unk = Math.max(0, total - up - deg - down);
    let line;
    if (!total) line = "서비스 요약을 아직 받지 못했어요.";
    else if (up === total) line = `서비스 ${fmt.num(total)}개가 모두 정상이에요.`;
    else {
      const rest = [deg ? `${deg}개는 저하` : null, down ? `${down}개는 다운` : null, unk ? `${unk}개는 확인 불가` : null].filter(Boolean);
      line = `서비스 ${fmt.num(total)}개 중 ${fmt.num(up)}개가 정상이고, ${rest.join(", ")} 상태예요.`;
    }
    const extra = [a.warn ? `경고 ${a.warn}` : null, a.info ? `정보 ${a.info}` : null, a.crit ? `심각 ${a.crit}` : null].filter(Boolean);
    const bad = (s.services || []).filter((x) => x.status && x.status !== "up");
    P.render($("#heroDetail"), html`<p>${line}${extra.length ? " 경보는 " + extra.join(" · ") + "건이에요." : ""}</p>${
      bad.length
        ? html`<div class="hero-issues">${bad.slice(0, 6).map((b) => html`<button type="button" class="issue" data-tone="${P.tone(b.status)}" data-sid="${b.id}"><i class="shape"></i><b>${b.name}</b><span>${b.status_reason || labels.status[b.status] || ""}</span></button>`)}</div>`
        : ""
    }`);

    const sys = s.system || {};
    txt("hero.uptime", fmt.dur(sys.uptime_s, 2));
    txt("hero.boot", sys.boot_time ? "부팅 " + fmt.datetime(sys.boot_time) : "–");
    const c = sum.containers || {};
    num("hero.ctr", c.running);
    txt("hero.ctrTotal", "/" + fmt.num(c.total));
    txt("hero.ctrSub", "헬스 정상 " + fmt.num(c.healthy) + (c.unhealthy ? " · 비정상 " + c.unhealthy : ""));
    num("hero.req", sum.requests_24h);
    txt("hero.reqSub", "최근 1시간 " + fmt.num(sum.requests_1h));
    const ts = s.tailscale || {};
    num("hero.peers", ts.peers_online);
    txt("hero.peersTotal", "/" + fmt.num(ts.peers_total));
    // 열린 포트: 아래 '열린 포트' 타일과 같은 정의(TCP·UDP 전부, LAN 노출 = 모든 인터페이스·LAN 바인딩)
    const LS = s.listeners || [];
    num("hero.ports", LS.length);
    txt("hero.portsSub", "LAN 노출 " + fmt.num(LS.filter(PORT_MATCH.exposed).length) + " · 로컬 " + fmt.num(LS.filter(PORT_MATCH.loopback).length));

    num("hero.up", up);
    txt("hero.total", "/ " + fmt.num(total) + " 정상");
    drawDonut([up, deg, down, unk], total);
    P.render($("#heroLegend"), [["ok", "정상", up], ["warn", "저하", deg], ["crit", "다운", down], ["muted", "확인 불가", unk]]
      .filter((x, i) => i === 0 || x[2] > 0)
      .map((x) => html`<li data-tone="${x[0]}"><i class="shape"></i>${x[1]} <b>${fmt.num(x[2])}</b></li>`));

    P.render($("#heroCats"), (s.categories || []).map((c2) => {
      const bits = [];
      if (c2.degraded) bits.push(html`<b>저하 ${c2.degraded}</b>`);
      if (c2.down) bits.push(html`<b class="crit">다운 ${c2.down}</b>`);
      if (c2.unknown) bits.push(html`<span>확인 불가 ${c2.unknown}</span>`);
      return html`<button type="button" class="catbar" data-cat="${c2.id}" aria-label="${c2.label + " " + c2.count + "개"} — 목록으로 이동"><span class="cat-${c2.id}">${ic(CAT_ICON[c2.id] || "layers")}</span><span class="cb-l">${c2.label}</span><span class="cb-n">${fmt.num(c2.count)}개${bits.length ? html` · ${bits}` : ""}</span>${stackBar([[c2.up, "t-ok", "정상"], [c2.degraded, "t-warn", "저하"], [c2.down, "t-crit", "다운"], [c2.unknown, "t-muted", "확인 불가"]])}</button>`;
    }));
  }
  function drawDonut(vals, total) {
    const box = $("#heroDonut");
    const R = 70, C = 2 * Math.PI * R;
    if (!box._segs) {
      box.innerHTML = '<svg viewBox="0 0 168 168" aria-hidden="true" focusable="false"><circle class="d-track" cx="84" cy="84" r="70"/>' +
        '<circle class="t-ok" cx="84" cy="84" r="70"/><circle class="t-warn" cx="84" cy="84" r="70"/><circle class="t-crit" cx="84" cy="84" r="70"/><circle class="t-muted" cx="84" cy="84" r="70"/></svg>';
      box._segs = $$("circle", box).slice(1);
      box._segs.forEach((c) => { c.style.strokeDasharray = "0 " + C; });
    }
    const tot = Math.max(1, total || vals.reduce((a, b) => a + b, 0));
    const gap = vals.filter((v) => v > 0).length > 1 ? 3 : 0;
    let off = 0;
    vals.forEach((v, i) => {
      const len = (v / tot) * C;
      const seg = v > 0 ? Math.max(1.5, len - gap) : 0;
      box._segs[i].style.strokeDasharray = seg.toFixed(2) + " " + C.toFixed(2);
      box._segs[i].style.strokeDashoffset = (-off).toFixed(2);
      off += len;
    });
    attr(box, "aria-label", `서비스 상태 분포: 정상 ${vals[0]}, 저하 ${vals[1]}, 다운 ${vals[2]}, 확인 불가 ${vals[3]}`);
  }

  // ------------------------------------------------------------------ 경보
  function findSvc(target) {
    if (!target || !S) return null;
    return (S.services || []).find((x) => x.id === target || x.name === target || (x.runtime && x.runtime.name === target) || (x.hostnames || []).indexOf(target) >= 0) || null;
  }
  function alertAction(a) {
    const sv = findSvc(a.target);
    if (sv) return { sid: sv.id, name: sv.name, label: sv.name + " 상세 보기" };
    switch (a.source) {
      case "systemd": return { go: "units:failed", label: "실패한 유닛 보기" };
      case "exposure": return { go: "ports:exposed", label: "LAN 노출 포트 보기" };
      case "traffic": return { go: "traffic:hosts", label: "상위 호스트 보기" };
      case "tunnel": case "caddy": return { go: "tile:t-ingress", label: "인그레스 보기" };
      case "tailscale": return { go: "tile:t-tail", label: "Tailscale 보기" };
      case "container": return { go: "tile:t-ctr", label: "컨테이너 보기" };
      case "hermes": return { go: "svc:hermes", label: "Hermes 보기" };
      case "system": {
        const map = { swap: "t-mem", memory: "t-mem", mem: "t-mem", temp: "t-therm", throttle: "t-therm", power: "t-therm", fan: "t-therm", disk: "t-disk", cpu: "t-cpu", gpu: "t-gpu" };
        const key = String(a.target || "").split(/[:/]/)[0];
        return { go: "tile:" + (map[key] || "t-cpu"), label: "하드웨어 보기" };
      }
      default: return null;
    }
  }
  function renderAlerts(s) {
    const list = s.alerts || [];
    const a = (s.summary || {}).alerts || {};
    P.render($("#alertCounts"), html`${a.crit ? html`<span class="badge" data-tone="crit">심각 ${a.crit}</span> ` : ""}${a.warn ? html`<span class="badge" data-tone="warn">경고 ${a.warn}</span> ` : ""}${a.info ? html`<span class="badge" data-tone="info">정보 ${a.info}</span>` : ""}`);
    const box = $("#alertList");
    box.hidden = !list.length;
    $("#alertEmpty").hidden = list.length > 0;
    // 줄 뼈대는 한 번만 만들고 칸별로 갱신한다 — "…부터" 시간이 바뀌어도 버튼 포커스가 유지된다.
    const gen = (s.meta && s.meta.generated_at) || nowS();
    keyed(box, list, (x) => x.key || x.message, () => {
      const el = mk("li", "alert", html`<span class="al-ic"></span><div class="al-body"><div class="al-meta"><span class="al-lv"></span><span class="src"></span><span class="age num"></span></div><p class="al-msg"></p><div class="al-go-box"></div></div>`);
      el._ic = $(".al-ic", el); el._lv = $(".al-lv", el); el._src = $(".src", el); el._age = $(".age", el); el._msg = $(".al-msg", el); el._go = $(".al-go-box", el);
      return el;
    }, (el, x) => {
      tone(el, P.tone(x.level));
      const go = alertAction(x);
      P.render(el._ic, ic(x.level === "crit" ? "crit" : x.level === "warn" ? "warn" : "info"));
      txt(el._lv, labels.level[x.level] || x.level);
      txt(el._src, "· " + (SRC[x.source] || x.source || "–"));
      txt(el._age, x.since ? "· " + fmt.ago(x.since, gen) + "부터" : "");
      attr(el._age, "title", x.since ? fmt.datetime(x.since) + "부터 계속" : "");
      txt(el._msg, x.message);
      P.render(el._go, go ? (go.sid
        ? html`<div class="al-acts"><button type="button" class="al-go" data-sid="${go.sid}">${go.label} →</button>${mapLink(go.sid, go.name, "al-go al-map")}</div>`
        : html`<button type="button" class="al-go" data-go="${go.go}">${go.label} →</button>`) : "");
    });
    requestAnimationFrame(updateAlertMore);
    const errs = Object.entries((s.meta && s.meta.errors) || {});
    const ce = $("#collectorErrors");
    ce.hidden = !errs.length;
    if (errs.length) P.render(ce, html`<strong>수집 오류</strong> ${errs.map(([k, v]) => html`<div><b>${k}</b> — ${v}</div>`)}`);
  }

  /** 경보가 타일보다 많으면 "아래 N건 더 보기"(목록을 내린다) */
  function updateAlertMore() {
    const box = $("#alertList"); const btn = $("#alertMore");
    if (!box || !btn) return;
    let hidden = 0;
    if (!box.hidden) {
      const bottom = box.getBoundingClientRect().bottom;
      for (const li of box.children) if (li.getBoundingClientRect().bottom > bottom + 2) hidden++;
    }
    if (btn.hidden !== !hidden) btn.hidden = !hidden;
    if (hidden) txt("alert.more", "아래 " + hidden + "건 더 보기");
  }
  $("#alertMore").addEventListener("click", () => {
    const box = $("#alertList");
    const bottom = box.getBoundingClientRect().bottom;
    const next = Array.from(box.children).find((li) => li.getBoundingClientRect().bottom > bottom + 2);
    if (next) box.scrollTo({ top: box.scrollTop + next.getBoundingClientRect().top - box.getBoundingClientRect().top - 4, behavior: calm() ? "auto" : "smooth" });
  });
  $("#alertList").addEventListener("scroll", () => { requestAnimationFrame(updateAlertMore); }, { passive: true });
  window.addEventListener("resize", () => requestAnimationFrame(updateAlertMore), { passive: true });

  // ------------------------------------------------------------------ 하드웨어 한눈에(지도 도크와 같은 부품)
  function renderHw(s) {
    if (SH.hw) SH.hw.render($("#hwStrip"), s, { base: "", more: "아래 하드웨어 섹션에서 자세히" });
  }

  // ------------------------------------------------------------------ CPU
  function renderCpu(s) {
    const sys = s.system || {};
    const c = sys.cpu || {};
    const h = sys.host || {};
    num("cpu.pct", c.usage_pct);
    tone(F["cpu.pct"].parentNode, P.loadTone(c.usage_pct, 80, 95) === "ok" ? "muted" : P.loadTone(c.usage_pct, 80, 95));
    txt("cpu.aside", fmt.num(c.cores || h.cores) + "코어 · 최대 " + fmt.mhz(c.max_mhz || h.cpu_max_mhz));
    txt("cpu.freq", fmt.mhz(c.freq_mhz));
    txt("cpu.gov", c.governor ? " · " + c.governor : "");
    txt("cpu.load", (c.load || []).map((v) => fmt.num(v, 2)).join(" · ") || "–");
    txt("cpu.temp", fmt.temp(c.temp_c));
    tone(F["cpu.temp"], P.loadTone(c.temp_c, 70, 80));
    txt("cpu.iowait", fmt.pct(c.iowait_pct, 1));
    const pr = sys.processes || {};
    txt("cpu.foot", (isNum(c.procs_running) ? "실행 대기 " + fmt.num(c.procs_running) + " · 스레드 " + fmt.num(c.threads_total) + " · " : "") + "프로세스 " + fmt.num(pr.count));
    const sp = s.spark || {};
    P.render($("#cpuChart .chart-svg"), areaSvg([{ v: sp.cpu, cls: "s1" }], { w: 600, h: 96, max: 100, grid: [25, 50, 75, 100] }));
    const cap = sp.cpu && sp.cpu.length ? "최근 " + fmt.dur(Math.round((sp.cpu.length * (sp.interval_s || 5)) / 60) * 60, 1) + " 사용률 · 최고 " + fmt.pct(Math.max.apply(null, sp.cpu.filter(isNum).concat([0])), 0) : "최근 5분 사용률";
    txt("cpu.chartcap", cap);
    const pc = c.per_core_pct || [];
    const pf = c.per_core_freq_mhz || [];
    keyed($("#cpuCores"), pc.map((_, i) => i), (i) => i, (i) => {
      const el = mk("div", "core", html`<div class="core-top"><span>코어 ${i}</span><span class="core-v"><span class="cv num">–</span>%</span></div><div class="meter" data-tone="info"><i></i></div><div class="core-f">–</div>`);
      el._v = el.querySelector(".cv"); el._m = el.querySelector(".meter"); el._f = el.querySelector(".core-f");
      return el;
    }, (el, i) => {
      const v = pc[i];
      num(el._v, v);
      const t = P.loadTone(v, 80, 95);
      tone(el._m, t === "ok" ? "info" : t);
      cssVar(el._m.firstElementChild, "--v", String(isNum(v) ? clamp(v / 100, 0, 1) : 0));
      txt(el._f, fmt.mhz(pf[i]));
    });
  }

  // ------------------------------------------------------------------ GPU
  const QUEUES = [["bin", "비닝", "Binning(지오메트리)"], ["render", "렌더", "Render(픽셀)"], ["tfu", "TFU", "텍스처 포맷 유닛"], ["csd", "CSD", "컴퓨트 셰이더 디스패치"], ["cache_clean", "캐시", "캐시 정리"], ["cpu", "CPU", "CPU 작업 큐"]];
  function renderGpu(s) {
    const g = (s.system || {}).gpu || {};
    const clk = (s.system || {}).clocks_mhz || {};
    const v3d = Object.keys(g.queues || {}).length > 0;
    show($("#hw-gpu"), g.available !== false || isNum(g.usage_pct)); // GPU 사용률을 모르는 기기에서는 타일째 숨긴다
    txt("gpu.name", g.name || "–");
    num("gpu.pct", g.usage_pct);
    txt("gpu.clkl", v3d ? "V3D" : "클럭");
    txt("gpu.mhz", fmt.mhz(isNum(g.freq_mhz) ? g.freq_mhz : isNum(clk.v3d) ? clk.v3d : clk.gpu));
    txt("gpu.jobs", isNum(g.jobs_per_s) ? " · 작업 " + fmt.num(g.jobs_per_s, 1) + "/초" : "");
    P.render($("#gpuSpark"), areaSvg([{ v: (s.spark || {}).gpu, cls: "s1" }], { w: 300, h: 88, max: 100, grid: [25, 50, 75, 100] }));
    const box = $("#gpuQueues");
    if (g.available === false) {
      if (box._keyed) { box._keyed.clear(); }
      P.render(box, html`<p class="gpu-na">V3D 사용률을 읽을 수 없어요(debugfs 권한 필요).</p>`);
      return;
    }
    if (box.__jarvisHtml) { box.innerHTML = ""; box.__jarvisHtml = null; }
    show(box, v3d);
    const q = g.queues || {};
    keyed(box, QUEUES, (x) => x[0], (x) => {
      const el = mk("div", "q", html`<span class="q-l" title="${x[2]}">${x[1]}</span><div class="meter"><i></i></div><span class="q-v num">–</span>`);
      el._m = el.querySelector(".meter > i"); el._v = el.querySelector(".q-v");
      return el;
    }, (el, x) => {
      const v = q[x[0]];
      cssVar(el._m, "--v", String(isNum(v) ? clamp(v / 100, 0, 1) : 0));
      txt(el._v, fmt.pct(v, 0));
    });
  }

  // ------------------------------------------------------------------ 메모리
  function renderMem(s) {
    const m = (s.system || {}).memory || {};
    txt("mem.total", fmt.bytes(m.total, 1));
    num("mem.pct", m.usage_pct);
    const lt = P.loadTone(m.usage_pct, 85, 95);
    tone(F["mem.pct"].parentNode, lt === "ok" ? "muted" : lt);
    txt("mem.used", fmt.bytes(m.used));
    txt("mem.avail", fmt.bytes(m.available));
    const reclaim = isNum(m.available) && isNum(m.free) ? Math.max(0, m.available - m.free) : null;
    const box = $("#memStack");
    if (!box._segs) {
      box.innerHTML = '<i class="s1"></i><i class="s2"></i><i class="t-muted"></i>';
      box._segs = $$("i", box);
    }
    const parts = [m.used, reclaim, m.free];
    // 크기가 바뀌는 값은 움직이지 않는다(레이아웃) — 값이 바뀔 때만 한 번 쓴다
    parts.forEach((v, i) => { const g = String(isNum(v) ? Math.max(0, v) : 0); if (box._segs[i]._g !== g) { box._segs[i]._g = g; box._segs[i].style.flexGrow = g; } });
    attr(box, "aria-label", `메모리 구성: 사용 ${fmt.bytes(m.used)}, 회수 가능 캐시 ${fmt.bytes(reclaim)}, 여유 ${fmt.bytes(m.free)}`);
    P.render($("#memLegend"), html`<li><i class="sw s1"></i>사용<span class="lv">${fmt.bytes(m.used)}</span></li><li><i class="sw s2"></i>캐시·버퍼 (회수 가능)<span class="lv">${fmt.bytes(reclaim)}</span></li><li><i class="sw t-muted"></i>여유<span class="lv">${fmt.bytes(m.free)}</span></li><li class="legend-sub">페이지 캐시 ${fmt.bytes(m.cached)} · 버퍼 ${fmt.bytes(m.buffers)} · 공유 ${fmt.bytes(m.shared)}</li>`);
    P.render($("#memSpark"), areaSvg([{ v: (s.spark || {}).mem, cls: "s1" }], { w: 300, h: 44, max: 100, grid: [50] }));
    // 스왑이 찬 것만으로는 문제가 아니다 — 백엔드 경보와 같은 기준: 스왑 인이 있거나 가용 메모리가 15% 미만일 때만 압박.
    const sw = $("#memSwap");
    const swapIn = isNum(m.swap_in_ps) ? m.swap_in_ps : null;
    const lowAvail = isNum(m.available) && isNum(m.total) && m.total > 0 && m.available / m.total < 0.15;
    const pressure = (swapIn != null && swapIn > 0) || lowAvail;
    const full = isNum(m.swap_pct) && m.swap_pct >= 90;
    tone(sw, !isNum(m.swap_pct) ? "muted" : pressure ? "warn" : "ok");
    const meter = $(".meter", sw);
    tone(meter, pressure ? "warn" : isNum(m.swap_pct) ? "info" : "muted");
    cssVar(meter.firstElementChild, "--v", String(isNum(m.swap_pct) ? clamp(m.swap_pct / 100, 0, 1) : 0));
    txt("mem.swap", fmt.pct(m.swap_pct, 1));
    txt("mem.swapSub", fmt.bytes(m.swap_used) + " / " + fmt.bytes(m.swap_total) + " · 스왑 인 " + (swapIn != null ? fmt.num(swapIn, 1) + "/초" : "–"));
    txt("mem.swapNote", pressure ? (swapIn > 0 ? "스왑 인이 일어나고 있어요 — 메모리 압박" : "가용 메모리가 15% 미만이에요 — 메모리 압박")
      : full ? "가득 찼지만 스왑 인 없음 — 압박 아님" : "스왑 인 없음 — 압박 아님");
    tone(F["mem.swapNote"], pressure ? "warn" : "muted");
  }

  // ------------------------------------------------------------------ 온도·팬·전력
  function renderTherm(s) {
    const sys = s.system || {};
    const th = sys.thermal || {};
    const pw = sys.power || {};
    const clk = sys.clocks_mhz || {};
    const t = isNum(th.cpu_temp_c) ? th.cpu_temp_c : (sys.cpu || {}).temp_c;
    num("therm.temp", t, (x) => fmt.num(x, 1));
    const gt = P.loadTone(t, 70, 80);
    const g = $("#tempGauge");
    tone(g, gt === "ok" ? "info" : gt);
    const gs = $(".gauge-svg", g);
    if (!gs._arc) {
      const d = P.arc(75, 72, 58, -125, 125);
      gs.innerHTML = '<svg viewBox="0 0 150 118" aria-hidden="true" focusable="false"><path class="g-track" d="' + d + '"/><path class="g-val" d="' + d + '"/></svg>';
      gs._arc = $(".g-val", gs);
      gs._len = 58 * (250 * Math.PI / 180);
      gs._arc.style.strokeDasharray = gs._len.toFixed(2);
      gs._arc.style.strokeDashoffset = gs._len.toFixed(2);
    }
    // 호는 0.5°C 단위로만 움직인다 — 0.1°C 떨림마다 0.8초 전환(프레임마다 다시 그리기)이 돌지 않게. 값이 바뀔 때만 쓴다
    const frac = isNum(t) ? Math.round(clamp((t - 20) / (90 - 20), 0, 1) * 140) / 140 : 0;
    const goff = (gs._len * (1 - frac)).toFixed(2);
    if (gs._off !== goff) { gs._off = goff; gs._arc.style.strokeDashoffset = goff; }
    attr(g, "aria-label", "CPU 온도 " + fmt.temp(t));
    // 이 기기에 없는 계기(PMIC·팬·보드 전력·스로틀·클럭·전원 레일)는 숨긴다. 온도까지 없으면 타일째.
    show($("#hw-therm"), isNum(t) || !!th.fan || isNum(pw.total_w) || !!th.throttle);
    show($("#tempGauge"), isNum(t));
    show(F["therm.pmic"], isNum(th.pmic_temp_c));
    show($("#fanStat"), !!th.fan);
    show($("#powerStat"), isNum(pw.total_w));
    show(F["therm.invw"], isNum(pw.input_v));
    txt("therm.pwl", (pw.rails || []).length ? "보드 전력" : "시스템 전력");
    show($("#throttle"), !!th.throttle);
    txt("therm.pmic", "PMIC " + fmt.temp(th.pmic_temp_c));
    const fan = th.fan || {};
    num("therm.rpm", fan.rpm);
    txt("therm.fanpct", fmt.pct(fan.pct, 0));
    const fi = $("#fanIcon");
    const spinning = isNum(fan.rpm) && fan.rpm > 0;
    const bucket = spinning ? Math.round(clamp(isNum(fan.pct) ? fan.pct : 50, 5, 100) / 10) : 0;
    cssVar(fi, "--spin", spinning ? (2.6 - bucket * 0.2).toFixed(2) + "s" : "0s");
    cssVar(fi, "--spin-state", spinning ? "running" : "paused");
    num("therm.watts", pw.total_w, (x) => fmt.num(x, 2));
    txt("therm.inv", isNum(pw.input_v) ? fmt.num(pw.input_v, 2) + " V" : "–");
    tone(F["therm.inv"], isNum(pw.input_v) && pw.input_v < 4.85 ? "warn" : "ok");

    const tr = th.throttle || {};
    const now = tr.now || [], since = tr.since_boot || [];
    const tb = $("#throttle");
    if (now.length) {
      tone(tb, "crit");
      P.render(tb, html`${ic("crit")}<div><b>지금 제한 중</b> — ${now.join(", ")}${since.length ? html`<div class="th-sub">부팅 후: ${since.join(", ")}</div>` : ""}</div>`);
    } else if (since.length) {
      tone(tb, "warn");
      P.render(tb, html`${ic("warn")}<div><b>부팅 후 제한 발생</b> — ${since.join(", ")} <span class="th-sub">(현재는 정상 · ${tr.raw || ""})</span></div>`);
    } else if (tr.raw != null || tr.ok) {
      tone(tb, "ok");
      P.render(tb, html`${ic("okc")}<div><b>스로틀 없음</b> <span class="th-sub">저전압·과열로 인한 클럭 제한 기록이 없어요 · ${tr.raw || "0x0"}</span></div>`);
    } else {
      tone(tb, "muted");
      P.render(tb, html`${ic("info")}<div><b>스로틀 정보 없음</b></div>`);
    }
    const CL = [["arm", "ARM"], ["core", "Core"], ["v3d", "V3D"], ["isp", "ISP"], ["hevc", "HEVC"], ["gpu", "GPU"]].filter(([k]) => k in clk);
    show($("#clocks"), CL.length > 0);
    P.render($("#clocks"), CL.map(([k, l]) => html`<div><dt>${l}</dt><dd>${isNum(clk[k]) ? fmt.num(clk[k], 0) : "–"}</dd></div>`));
    attr($("#clocks"), "aria-label", "클럭(MHz)");
    const rails = pw.rails || [];
    show($("#rails"), rails.length > 0);
    txt("therm.rails", rails.length + "개 · 코어 " + (isNum(pw.core_volts) ? fmt.num(pw.core_volts, 3) + " V" : "–"));
    const rb = $("#railsBody");
    if ($("#rails").open || !rb.__jarvisHtml) {
      P.render(rb, rails.map((r) => html`<div class="rail"><span>${r.name}</span><span>${fmt.num(r.volts, 3)} V · ${fmt.num(r.watts, 2)} W</span></div>`));
    }
  }

  // ------------------------------------------------------------------ 네트워크
  function ifaceRow(i) {
    const kindL = { wired: "유선", wifi: "Wi-Fi", tailnet: "Tailnet", docker: "Docker", other: "기타" }[i.kind] || i.kind;
    let kd = kindL;
    if (i.speed_mbps && i.kind !== "docker") kd += " · " + (i.speed_mbps >= 1000 ? fmt.num(i.speed_mbps / 1000, 0) + " Gbps" : i.speed_mbps + " Mbps");
    let wifi = "";
    if (i.wifi) {
      const q = i.wifi.quality;
      const lv = isNum(q) ? Math.ceil(q / 25) : 0;
      wifi = html` <span class="wifi-bars" role="img" aria-label="신호 ${fmt.pct(q, 0)}">${[1, 2, 3, 4].map((b) => html`<i class="${b <= lv ? "on" : ""}" style="height:${b * 25}%"></i>`)}</span> 신호 ${fmt.num(i.wifi.signal_dbm, 0)} dBm · ${fmt.pct(q, 0)}`;
    }
    const ips = (i.ipv4 || []).concat(i.ipv6 || []);
    return html`<li class="iface" data-tone="${i.up ? "ok" : "muted"}"><span class="if-n"><span class="mono">${i.name}</span><span class="if-k">${kd}${wifi}${i.up ? "" : " · 꺼짐"}</span></span><span class="if-r">↓ ${fmt.bps(i.rx_bps)} ↑ ${fmt.bps(i.tx_bps)}</span>${ips.length ? html`<span class="if-ip">${ips.join(" · ")}</span>` : ""}</li>`;
  }
  function renderNet(s) {
    const n = (s.system || {}).network || {};
    num("net.rx", n.rx_bps, (x) => fmt.bps(x));
    num("net.tx", n.tx_bps, (x) => fmt.bps(x));
    num("net.trx", n.tailnet_rx_bps, (x) => fmt.bps(x));
    num("net.ttx", n.tailnet_tx_bps, (x) => fmt.bps(x));
    const ifs = n.interfaces || [];
    const main = ifs.filter((i) => i.kind !== "docker");
    const br = ifs.filter((i) => i.kind === "docker");
    txt("net.aside", "인터페이스 " + ifs.filter((i) => i.up).length + "/" + ifs.length + " 활성");
    const sp = s.spark || {};
    P.render($("#netSpark"), areaSvg([{ v: sp.rx, cls: "s1" }, { v: sp.tx, cls: "s2" }, { v: sp.trx, cls: "s3" }, { v: sp.ttx, cls: "s4" }], { w: 300, h: 64, thin: true }));
    P.render($("#ifaces"), main.map(ifaceRow));
    txt("net.bridges", br.length + "개 · " + br.filter((i) => i.up).length + "개 활성 · ↓ " + fmt.bps(n.docker_rx_bps) + " ↑ " + fmt.bps(n.docker_tx_bps));
    $("#bridges").hidden = !br.length;
    if ($("#bridges").open || !$("#bridgesBody").__jarvisHtml) P.render($("#bridgesBody"), br.map(ifaceRow));
  }

  // ------------------------------------------------------------------ 저장소
  function renderDisk(s) {
    const sys = s.system || {};
    const disks = sys.disks || [];
    const io = sys.disk_io || {};
    const tot = disks.filter((d) => d.fs !== "tmpfs").reduce((a, d) => a + (d.total || 0), 0);
    txt("disk.aside", Object.keys(io.devices || {}).join(" · ") + (tot ? " · 총 " + fmt.bytes(tot, 0) : ""));
    keyed($("#disks"), disks, (d) => d.mount, (d) => {
      const el = mk("li", "disk", html`<div class="disk-top"><span class="disk-m">${d.mount}</span><span class="disk-p num">–</span></div><div class="meter" data-tone="info"><i></i></div><div class="disk-s"><span class="ds-a">–</span><span class="ds-b">–</span></div>`);
      el._p = $(".disk-p", el); el._m = $(".meter", el); el._a = $(".ds-a", el); el._b = $(".ds-b", el);
      return el;
    }, (el, d) => {
      num(el._p, d.usage_pct, (x) => fmt.pct(x, 1));
      const t = P.loadTone(d.usage_pct, 80, 92);
      tone(el._m, t === "ok" ? "info" : t);
      tone(el._p, t);
      cssVar(el._m.firstElementChild, "--v", String(isNum(d.usage_pct) ? clamp(d.usage_pct / 100, 0, 1) : 0));
      txt(el._a, fmt.bytes(d.used) + " / " + fmt.bytes(d.total) + " · 여유 " + fmt.bytes(d.free));
      txt(el._b, [d.device, d.fs].filter(Boolean).join(" · "));
    });
    num("disk.r", io.read_bps, (x) => fmt.bps(x));
    num("disk.w", io.write_bps, (x) => fmt.bps(x));
    const sp = s.spark || {};
    P.render($("#diskSpark"), areaSvg([{ v: sp.dr, cls: "s1" }, { v: sp.dw, cls: "s2" }], { w: 300, h: 44, thin: true }));
  }

  // ------------------------------------------------------------------ 추이(히스토리)
  const VF = {
    pct: (v) => fmt.pct(v, 1), temp: (v) => fmt.temp(v), rpm: (v) => (isNum(v) ? fmt.num(v, 0) + " rpm" : "–"),
    bps: (v) => fmt.bps(v), w: (v) => fmt.watts(v), load: (v) => fmt.num(v, 2),
  };
  const AXF = {
    pct: (v) => fmt.num(v, 0) + "%", temp: (v) => fmt.num(v, 0) + "°", rpm: (v) => fmt.num(v, 0),
    bps: (v) => (v === 0 ? "0" : fmt.bytes(v, 0)), w: (v) => fmt.num(v, v < 10 ? 1 : 0) + " W", load: (v) => fmt.num(v, v < 10 ? 1 : 0),
  };
  const METRICS = {
    cpu: { series: [["cpu", "CPU"], ["gpu", "GPU"]], unit: "pct", title: "CPU·GPU 사용률" },
    mem: { series: [["mem", "메모리"], ["swap", "스왑"]], unit: "pct", title: "메모리·스왑 사용률" },
    temp: { series: [["temp", "CPU 온도"]], unit: "temp", title: "CPU 온도" },
    fan: { series: [["fan", "팬 속도"]], unit: "rpm", title: "팬 속도" },
    net: { series: [["rx", "LAN 수신"], ["tx", "LAN 송신"], ["trx", "Tailnet 수신"], ["ttx", "Tailnet 송신"]], unit: "bps", title: "네트워크 처리량" },
    disk: { series: [["dr", "읽기"], ["dw", "쓰기"]], unit: "bps", title: "디스크 I/O" },
    power: { series: [["pw", "보드 전력"]], unit: "w", title: "보드 전력" },
    load: { series: [["load", "부하 평균 1분"]], unit: "load", title: "부하 평균" },
  };
  function maybeHistory() {
    const ttl = H.range === "1h" ? 15 : 60;
    if (!H.loading && (nowS() - H.at > ttl || !H.data || H.data.range !== H.range)) loadHistory();
  }
  async function loadHistory() {
    H.loading = true;
    const want = H.range;
    try {
      const d = await P.history(want);
      if (want === H.range) { H.data = d; H.err = null; }
    } catch (e) {
      H.err = e;
    } finally {
      H.at = nowS();
      H.loading = false;
      drawHistory();
    }
  }
  function seriesStats(arr) {
    const v = (arr || []).filter(isNum);
    if (!v.length) return { last: null, avg: null, max: null };
    let last = null;
    for (let i = arr.length - 1; i >= 0; i--) if (isNum(arr[i])) { last = arr[i]; break; }
    return { last, avg: v.reduce((a, b) => a + b, 0) / v.length, max: Math.max.apply(null, v) };
  }
  function drawHistory() {
    const box = $("#histChart");
    const svgBox = $(".hist-svg", box);
    const M = METRICS[H.metric] || METRICS.cpu;
    const d = H.data && H.data.range === H.range ? H.data : null;
    const f = VF[M.unit];
    P.render($("#histLegend"), M.series.map(([k, label], i) => {
      const st = seriesStats(d && d[k]);
      return html`<div class="hl"><span class="hl-n"><i class="sw s${i + 1}"></i>${label}</span><span class="hl-v">${f(st.last)}</span><span class="hl-s">평균 ${f(st.avg)} · 최고 ${f(st.max)}</span></div>`;
    }));
    const w = box.clientWidth, h = box.clientHeight;
    const n = d && d.t ? d.t.length : 0;
    const note = $("#histNote");
    if (!d || n < 2 || !w || !h) {
      H.geom = null;
      $("#histTip").hidden = true;
      P.render(svgBox, html`<div class="hist-empty">${H.err ? "기록을 불러오지 못했어요. 잠시 후 다시 시도해요." : d ? "아직 기록이 충분하지 않아요 — Jarvis 가 막 시작됐어요. 곧 채워져요." : "기록을 불러오는 중…"}</div>`);
      txt(note, d ? "기록 지점 " + n + "개" : "–");
      return;
    }
    const ts = d.t;
    const t0 = ts[0], t1 = ts[n - 1];
    const narrow = w < 520;
    const pad = { l: narrow ? 40 : 52, r: 12, t: 10, b: 24 };
    const iw = Math.max(10, w - pad.l - pad.r), ih = Math.max(10, h - pad.t - pad.b);
    const vals = [];
    M.series.forEach(([k]) => (d[k] || []).forEach((v) => { if (isNum(v)) vals.push(v); }));
    let lo = 0, hi;
    if (M.unit === "pct") hi = 100;
    else if (M.unit === "temp" && vals.length) {
      lo = Math.floor((Math.min.apply(null, vals) - 3) / 5) * 5;
      hi = Math.ceil((Math.max.apply(null, vals) + 3) / 5) * 5;
      if (hi - lo < 10) hi = lo + 10;
    } else hi = niceCeil((vals.length ? Math.max.apply(null, vals) : 1) * 1.08);
    if (!(hi > lo)) hi = lo + 1;
    const X = (t) => pad.l + (t1 === t0 ? iw : ((t - t0) / (t1 - t0)) * iw);
    const Y = (v) => pad.t + ih - ((clamp(v, lo, hi) - lo) / (hi - lo)) * ih;
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
      const y = Y(v).toFixed(1);
      g += '<line class="grid-ln" x1="' + pad.l + '" x2="' + (w - pad.r) + '" y1="' + y + '" y2="' + y + '"/>';
      g += '<text class="ax" x="' + (pad.l - 8) + '" y="' + y + '" text-anchor="end" dominant-baseline="middle">' + P.esc(AXF[M.unit](v)) + "</text>";
    }
    const xt = narrow ? 3 : 5;
    for (let i = 0; i < xt; i++) {
      const t = t0 + ((t1 - t0) * i) / (xt - 1);
      const anchor = i === 0 ? "start" : i === xt - 1 ? "end" : "middle";
      g += '<text class="ax" x="' + X(t).toFixed(1) + '" y="' + (h - 6) + '" text-anchor="' + anchor + '">' + P.esc(fmt.time(t, false)) + "</text>";
    }
    const gapS = (d.interval_s || 5) * 3.5;
    let paths = "";
    M.series.forEach(([k], si) => {
      const arr = d[k] || [];
      let line = "", area = "", seg = [];
      const flush = () => {
        if (seg.length > 1) {
          line += "M" + seg.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L");
          if (si === 0) area += "M" + seg[0][0].toFixed(1) + " " + (pad.t + ih) + "L" + seg.map((p) => p[0].toFixed(1) + " " + p[1].toFixed(1)).join("L") + "L" + seg[seg.length - 1][0].toFixed(1) + " " + (pad.t + ih) + "Z";
        } else if (seg.length === 1) {
          line += "M" + (seg[0][0] - 1).toFixed(1) + " " + seg[0][1].toFixed(1) + "L" + (seg[0][0] + 1).toFixed(1) + " " + seg[0][1].toFixed(1);
        }
        seg = [];
      };
      for (let i = 0; i < n; i++) {
        const v = arr[i];
        if (!isNum(v) || (i > 0 && ts[i] - ts[i - 1] > gapS)) flush();
        if (isNum(v)) seg.push([X(ts[i]), Y(v)]);
      }
      flush();
      if (area) paths += '<path class="ar s' + (si + 1) + '" d="' + area + '"/>';
      if (line) paths += '<path class="ln s' + (si + 1) + '" d="' + line + '"/>';
    });
    let hov = '<g class="hov" visibility="hidden"><line class="cross" y1="' + pad.t + '" y2="' + (pad.t + ih) + '"/>';
    M.series.forEach((_, si) => { hov += '<circle class="dotm s' + (si + 1) + '" r="4.5"/>'; });
    hov += "</g>";
    const label = M.title + " — " + (H.range === "24h" ? "24시간" : "1시간") + " 추이";
    P.render(svgBox, raw('<svg viewBox="0 0 ' + w + " " + h + '" width="' + w + '" height="' + h + '" role="img" aria-label="' + P.esc(label) + '">' + g + paths + hov + "</svg>"));
    H.geom = { X, Y, ts, d, M, pad, w, h, iw, t0, t1 };
    const span = t1 - t0;
    let gaps = 0;
    for (let i = 1; i < n; i++) if (ts[i] - ts[i - 1] > gapS) gaps++;
    const want = H.range === "24h" ? 86400 : 3600;
    const step = H.range === "24h" ? "1분" : "5초";
    const base = span < want * 0.9
      ? "기록이 아직 " + fmt.dur(span, 2) + "치예요 (" + step + " 간격 " + n + "개 지점). 시간이 지나면 " + (H.range === "24h" ? "24시간" : "1시간") + "까지 채워져요."
      : (H.range === "24h" ? "최근 24시간 · 1분 간격" : "최근 1시간 · 5초 간격") + " · 차트 위에 올리면 시점별 값을 보여줘요.";
    txt(note, base + (gaps ? " 끊긴 구간은 수집이 멈췄던 때예요(재시작 등)." : ""));
    showHover();
  }
  function nearestIdx(arr, t) {
    let lo = 0, hi = arr.length - 1;
    while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (arr[mid] < t) lo = mid; else hi = mid; }
    return Math.abs(arr[lo] - t) <= Math.abs(arr[hi] - t) ? lo : hi;
  }
  function showHover() {
    const G = H.geom;
    const tip = $("#histTip");
    const svg = $("#histChart svg");
    const hov = svg && svg.querySelector(".hov");
    if (!G || H.hoverX == null || !hov) { if (hov) hov.setAttribute("visibility", "hidden"); tip.hidden = true; return; }
    const x = clamp(H.hoverX, G.pad.l, G.w - G.pad.r);
    const t = G.t0 + ((x - G.pad.l) / G.iw) * (G.t1 - G.t0);
    const i = nearestIdx(G.ts, t);
    const tx = G.X(G.ts[i]);
    const line = hov.querySelector(".cross");
    line.setAttribute("x1", tx.toFixed(1));
    line.setAttribute("x2", tx.toFixed(1));
    const dots = hov.querySelectorAll(".dotm");
    const f = VF[G.M.unit];
    G.M.series.forEach(([k], si) => {
      const v = (G.d[k] || [])[i];
      const c = dots[si];
      if (isNum(v)) { c.setAttribute("cx", tx.toFixed(1)); c.setAttribute("cy", G.Y(v).toFixed(1)); c.setAttribute("visibility", "visible"); }
      else c.setAttribute("visibility", "hidden");
    });
    hov.setAttribute("visibility", "visible");
    P.render(tip, html`<div class="tt-t">${fmt.datetime(G.ts[i])}:${String(new Date(G.ts[i] * 1000).getSeconds()).padStart(2, "0")}</div>${G.M.series.map(([k, label], si) => html`<div class="tt-r"><i class="sw s${si + 1}"></i>${label}<b>${f((G.d[k] || [])[i])}</b></div>`)}`);
    tip.hidden = false;
    const tw = tip.offsetWidth || 160;
    const left = tx + 14 + tw > G.w ? tx - 14 - tw : tx + 14;
    tip.style.transform = "translateX(" + Math.max(0, left).toFixed(0) + "px)";
  }
  (function bindHistory() {
    const box = $("#histChart");
    box.addEventListener("pointermove", (e) => {
      const r = box.getBoundingClientRect();
      H.hoverX = e.clientX - r.left;
      showHover();
    });
    box.addEventListener("pointerleave", () => { H.hoverX = null; showHover(); });
    let rq = 0;
    if (window.ResizeObserver) {
      const ro = new ResizeObserver(() => {
        if (rq) return;
        rq = requestAnimationFrame(() => { rq = 0; if (H.data) drawHistory(); });
      });
      ro.observe(box);
    }
  })();

  // ------------------------------------------------------------------ 서비스
  function viaLine(sv) {
    const e = sv.exposure || {};
    const via = e.via || [];
    if (!via.length) return html`<p class="svc-via">${ic("lock")}외부 노출 없음 · 로컬 전용</p>`;
    return html`<p class="svc-via">${ic("route")}${via.map((v, i) => html`${i ? html`<span class="arr">·</span>` : ""}<span>${labels.via[v] || v}</span>`)}</p>`;
  }
  function subParts(sv, s) {
    const rt = sv.runtime || {};
    const out = [];
    if (sv.title && sv.title !== sv.name) out.push(sv.title);
    const kl = SH.kindLabel ? SH.kindLabel(sv.kind) : labels.kind[sv.kind] || sv.kind;
    if (kl && !(sv.title && String(sv.title).includes(kl))) out.push(kl);
    if (rt.type) out.push(labels.runtime[rt.type] || rt.type);
    if (isNum(rt.restart_count) && rt.restart_count > 0) out.push("재시작 " + rt.restart_count + "회");
    return out;
  }
  // 칩 이름은 길 이름: Tailnet(Caddy :443) · 터널(Caddy :80) · 공개(Cloudflare 엣지). 비공개 라우트의 터널 404 는 "공개 차단 ✓".
  function probeChips(sv) {
    const pr = sv.probes || {};
    const keys = PROBE_ORDER.filter((k) => pr[k]);
    const chips = keys.map((k) => {
      const v = verdict(pr[k], k, sv);
      if (v.blocked) return html`<span class="probe blocked" data-tone="ok" title="${v.title}">${ic("lock")}공개 차단 <b aria-hidden="true">✓</b><span class="sr"> 확인됨</span></span>`;
      return html`<span class="probe" data-tone="${v.tone}" title="${v.title}"><i class="shape"></i>${v.chip} <b>${v.value}</b></span>`;
    });
    if (sv.dns && sv.dns.points_to) {
      const pt = sv.dns.points_to;
      const bad = (sv.category === "public" && pt !== "cloudflare") || (sv.category === "tailnet" && pt !== "tailnet") || pt === "error";
      chips.push(html`<span class="probe" data-tone="${bad ? "warn" : "muted"}" title="공개 DNS 응답: ${(sv.dns.answers || []).join(", ")}">DNS <b>${DNS_L[pt] || pt}</b></span>`);
    }
    return chips.length ? html`<div class="probes">${chips}</div>` : "";
  }
  function cardLinks(sv) {
    const urls = sv.urls || [];
    if (!urls.length) return "";
    const shown = urls.slice(0, 3);
    return html`${shown.map((u) => extLink(u.url, html`<span>${shortUrl(u.url)}</span>${ic("ext")}<small class="aud">${labels.audience[u.audience] || ""}</small>`, "svc-link"))}${urls.length > 3 ? html`<small class="aud">외 ${urls.length - 3}개</small>` : ""}`;
  }
  function cardMetrics(sv, s) {
    const rt = sv.runtime || {};
    const tr = sv.traffic;
    const cells = [];
    if (tr || (sv.probes && (sv.probes.internal || sv.probes.public))) {
      // 어느 프로브의 지연인지 라벨에 쓴다(지도 검사기의 공개 지연과 헷갈리지 않게)
      const viaTn = !!(sv.probes && sv.probes.internal);
      const src = viaTn ? "Tailnet 경로(Caddy :443)" : "공개 경로(Cloudflare 엣지)";
      cells.push([viaTn ? "Tailnet 응답" : "공개 응답", fmt.ms(P.pick.latency(sv)), "", src + " 프로브 응답 시간"]);
      cells.push(["요청 1시간", fmt.num(tr && tr.requests_1h)]);
      cells.push(["요청 24시간", fmt.num(tr && tr.requests_24h)]);
      const dl = sv.cert && sv.cert.days_left;
      cells.push(["인증서", isNum(dl) ? Math.floor(dl) + "일" : "–", isNum(dl) && dl < 14 ? "c" : isNum(dl) && dl < 30 ? "w" : ""]);
    } else {
      const ports = uniq((sv.listeners || []).map((l) => l.port)).sort((a, b) => a - b);
      cells.push(["포트", ports.length ? ports.slice(0, 2).join(", ") + (ports.length > 2 ? "…" : "") : "–"]);
      cells.push(["가동", fmt.durShort(rt.uptime_s)]);
      cells.push(["CPU", isNum(rt.cpu_pct) ? fmt.pct(rt.cpu_pct, 1) : "–"]);
      cells.push(["메모리", fmt.bytes(rt.mem_bytes, 0)]);
    }
    return cells.map((c) => html`<div title="${c[3] || ""}"><dt>${c[0]}</dt><dd class="${c[2] || ""}">${c[1]}</dd></div>`);
  }
  function searchKey(sv) {
    const rt = sv.runtime || {};
    return [sv.name, sv.title, sv.id, sv.upstream, sv.description, rt.name, rt.image, rt.project, labels.category[sv.category]]
      .concat(sv.hostnames || [], (sv.listeners || []).map((l) => String(l.port)), (sv.urls || []).map((u) => u.url))
      .filter(Boolean).join(" ").toLowerCase();
  }
  function createCard(sv) {
    const el = mk("article", "svc", html`<div class="svc-head"></div><div class="svc-title"><h4 class="svc-name"><button type="button" class="svc-open" aria-haspopup="dialog"></button></h4><a class="svc-map icon" href="/map/" title="네트워크 맵에서 보기">${ic("map")}</a></div><div class="svc-mid"></div><div class="svc-links"></div><div class="svc-extra"></div><dl class="svc-metrics"></dl>`);
    el._head = el.children[0];
    el._btn = $(".svc-open", el);
    el._map = $(".svc-map", el);
    el._mid = el.children[2];
    el._links = el.children[3];
    el._extra = el.children[4];
    el._met = el.children[5];
    return el;
  }
  function updateCard(el, sv, s) {
    tone(el, P.tone(sv.status));
    txt(el._btn, sv.name || sv.id);
    attr(el._btn, "data-sid", sv.id);
    attr(el._btn, "aria-label", (sv.name || sv.id) + " — " + (labels.status[sv.status] || "확인 불가") + ", 상세 정보 열기");
    attr(el._map, "href", P.safeUrl(mapHref(sv.id)));
    attr(el._map, "aria-label", (sv.name || sv.id) + " — 네트워크 맵에서 보기");
    el._search = searchKey(sv);
    P.render(el._head, html`${pill(sv.status)}<span class="badges">${expBadges(sv)}</span>`);
    const sub = subParts(sv, s);
    P.render(el._mid, html`<p class="svc-sub">${sub.map((p, i) => html`<span class="${i ? "dotsep" : ""}">${p}</span>`)}</p>${sv.status !== "up" && sv.status_reason ? html`<p class="svc-reason">${sv.status_reason}</p>` : ""}${viaLine(sv)}`);
    P.render(el._links, cardLinks(sv));
    P.render(el._extra, html`${probeChips(sv)}${selfLine(sv, s)}`);
    P.render(el._met, cardMetrics(sv, s));
  }
  // 목록 보기 — 서비스가 많을 때(50개+) 한 줄에 하나씩 촘촘하게
  function rowMetrics(sv, s) {
    const rt = sv.runtime || {};
    const tr = sv.traffic;
    const cell = (k, v, c) => html`<span class="${"rm" + (c ? " " + c : "")}"><small>${k}</small>${v}</span>`;
    if (tr || (sv.probes && (sv.probes.internal || sv.probes.public))) {
      // 한 번 튄 실패(fail_streak 1)는 "재확인 중" — 실패로 세지 않는다(경보도 안 뜬다)
      const ps = PROBE_ORDER.map((k) => sv.probes && sv.probes[k]).filter(Boolean);
      const failing = ps.filter((p) => !p.ok && p.fail_streak !== 1).length;
      const retry = ps.filter((p) => !p.ok && p.fail_streak === 1).length;
      const first = cell(sv.probes && sv.probes.internal ? "Tailnet 응답" : "공개 응답", fmt.ms(P.pick.latency(sv)), failing ? "c" : retry ? "w" : "");
      // 이 모니터 자신: 요청 수 대신 자기 진단(수집기 몇 개가 제때인가)
      if (MON && isMonitor(sv)) { const Hh = MON.health(s); return [first, cell("수집기", Hh.known ? Hh.fresh + "/" + Hh.total : "–", Hh.ok === false ? "w" : "")]; }
      return [first, cell("요청 24시간", fmt.num(tr && tr.requests_24h))];
    }
    const ports = uniq((sv.listeners || []).map((l) => l.port)).sort((a, b) => a - b);
    return [cell("포트", ports.length ? ports.slice(0, 2).join(", ") + (ports.length > 2 ? "…" : "") : "–"), cell("가동", fmt.durShort(rt.uptime_s))];
  }
  function createRow(sv) {
    const el = mk("li", "srow", html`<span class="sr-st"></span><div class="sr-main"><div class="sr-nm"></div><p class="sr-sub"></p></div><div class="sr-exp"></div><div class="sr-url"></div><div class="sr-met"></div><a class="svc-map sr-map icon" href="/map/" title="네트워크 맵에서 보기">${ic("map")}</a>`);
    el._st = el.children[0]; el._nm = $(".sr-nm", el); el._sub = $(".sr-sub", el);
    el._exp = el.children[2]; el._url = el.children[3]; el._met = el.children[4]; el._map = el.children[5];
    P.render(el._nm, html`<button type="button" class="svc-open" aria-haspopup="dialog"></button>`);
    el._btn = el._nm.firstElementChild;
    return el;
  }
  function updateRow(el, sv, s) {
    tone(el, P.tone(sv.status));
    txt(el._btn, sv.name || sv.id);
    attr(el._btn, "data-sid", sv.id);
    attr(el._btn, "aria-label", (sv.name || sv.id) + " — " + (labels.status[sv.status] || "확인 불가") + ", 상세 정보 열기");
    el._search = searchKey(sv);
    P.render(el._st, pill(sv.status));
    const sub = subParts(sv, s);
    P.render(el._sub, html`${sv.status !== "up" && sv.status_reason ? html`<b class="sr-why">${sv.status_reason}</b>` : sub.join(" · ")}`);
    const eb = expBadges(sv);
    P.render(el._exp, eb.length ? eb : html`<span class="badge b-local">${ic("lock")}로컬</span>`);
    const u = (sv.urls || [])[0];
    const via = ((sv.exposure || {}).via || []).map((v) => labels.via[v] || v).join(" · ");
    P.render(el._url, u ? extLink(u.url, html`<span>${shortUrl(u.url)}</span>${ic("ext")}`, "sr-link") : html`<span class="sr-via">${via || "외부 노출 없음"}</span>`);
    P.render(el._met, rowMetrics(sv, s));
    attr(el._map, "href", P.safeUrl(mapHref(sv.id)));
    attr(el._map, "aria-label", (sv.name || sv.id) + " — 네트워크 맵에서 보기");
  }

  function renderServices(s) {
    const cats = (s.categories || []).slice();
    const byCat = P.pick.byCategory(s);
    Object.keys(byCat).forEach((id) => {
      if (!cats.find((c) => c.id === id)) cats.push({ id, label: labels.category[id] || id, desc: "", count: byCat[id].services.length });
    });
    const total = (s.services || []).length;
    txt("seg.all", fmt.num(total));
    cats.forEach((c) => { if (F["seg." + c.id]) txt("seg." + c.id, fmt.num(c.count)); });
    txt("svc.sub", "이 기기에서 떠 있는 모든 것 — " + fmt.num(total) + "개 · 누르면 상세 정보, 맵에서 보기로 경로 확인");
    keyed($("#svcGroups"), cats, (c) => c.id, (c) => {
      const g = mk("section", "svc-group", html`<header class="grp-head"><span class="grp-ic cat-${c.id}">${ic(CAT_ICON[c.id] || "layers")}</span><div class="grp-t"><h3 id="g-${c.id}"></h3><p class="grp-desc"></p></div><div class="grp-counts"></div></header><div class="svc-grid"></div><ul class="svc-rows" hidden></ul>`);
      g.setAttribute("data-group", c.id);
      g.setAttribute("aria-labelledby", "g-" + c.id);
      g._title = $("h3", g); g._desc = $(".grp-desc", g); g._counts = $(".grp-counts", g); g._grid = $(".svc-grid", g); g._list = $(".svc-rows", g);
      return g;
    }, (g, c) => {
      const list = byCat[c.id] ? byCat[c.id].services : [];
      P.render(g._title, c.label || labels.category[c.id] || c.id);
      txt(g._desc, c.desc || "");
      const parts = [html`<span class="badge">${fmt.num(c.count != null ? c.count : list.length) + "개"}</span>`];
      if (c.up) parts.push(html`<span class="badge" data-tone="ok">정상 ${c.up}</span>`);
      if (c.degraded) parts.push(html`<span class="badge" data-tone="warn">저하 ${c.degraded}</span>`);
      if (c.down) parts.push(html`<span class="badge" data-tone="crit">다운 ${c.down}</span>`);
      if (c.unknown) parts.push(html`<span class="badge" data-tone="muted">확인 불가 ${c.unknown}</span>`);
      P.render(g._counts, parts);
      const asList = SV.view === "list";
      if (g._grid.hidden !== asList) g._grid.hidden = asList;
      if (g._list.hidden === asList) g._list.hidden = !asList;
      if (asList) keyed(g._list, list, (x) => x.id, createRow, (el, sv) => updateRow(el, sv, s));
      else keyed(g._grid, list, (x) => x.id, createCard, (el, sv) => updateCard(el, sv, s));
    });
    applySvcFilter();
  }
  function applySvcFilter() {
    const q = SV.q.trim().toLowerCase();
    let any = false;
    $$("#svcGroups > .svc-group").forEach((g) => {
      const catOk = SV.filter === "all" || g.getAttribute("data-group") === SV.filter;
      let vis = 0;
      const box = SV.view === "list" ? g._list : g._grid;
      Array.from(box ? box.children : []).forEach((card) => {
        const m = !q || (card._search || "").indexOf(q) >= 0;
        if (card.hidden === m) card.hidden = !m;
        if (m) vis++;
      });
      const show = catOk && vis > 0;
      if (g.hidden === show) g.hidden = !show;
      if (show) any = true;
    });
    $("#svcEmpty").hidden = any || !S;
    $$("#svcFilter button").forEach((b) => attr(b, "aria-pressed", String(b.getAttribute("data-v") === SV.filter)));
  }
  function setSvcFilter(v) {
    SV.filter = v;
    store.set("svcFilter", v);
    applySvcFilter();
  }

  // ------------------------------------------------------------------ 네트워크 맵 미리보기
  // 작은 토폴로지 SVG 는 /app/home-map.js 가 그린다. 타일 전체가 /map/ 으로 가는 링크다.
  // 공개 상태 숫자는 지도의 요약과 같은 정의(가장 넓게 열린 범위, JarvisShell.expCounts)다.
  // "경고"라는 말은 경보 등급에만 쓴다 → 저하·다운 서비스 수는 "문제 서비스".
  //   관점 두 가지(사용자 선택, 이 브라우저에 기억): 개념 = 서비스가 어떤 길로 노출되는가(snap.topology)
  //                                               물리 = 어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가(snap.topology_physical)
  //   타일 링크는 같은 관점으로 전체 지도를 연다: /map/?view=concept | /map/?view=physical
  const MAP_VIEW = {
    concept: { name: "개념", desc: "인터넷 · Tailnet · LAN 에서 이 기기의 서비스까지 이어지는 길이에요. 선 색이 들어오는 길의 종류예요." },
    physical: { name: "물리", desc: "이 기기가 어떤 선으로 LAN · Tailnet · 인터넷과 이어지고, 안에 무엇이 들어 있는지예요. 상자는 포함, 선은 실제 연결이에요." },
  };
  function renderMap(s) {
    const view = MV.v;
    const tileEl = $("#mapTile");
    attr(tileEl, "data-view", view);
    attr($("#mapOpen"), "href", SH.mapView ? SH.mapView.href(view) : "/map/?view=" + view);
    txt("map.openSr", " — " + MAP_VIEW[view].name + " 관점으로 전체 지도 열기");
    txt("map.desc", MAP_VIEW[view].desc);
    txt("map.q", SH.MAP_MODES ? SH.MAP_MODES[view].q : "");
    syncMapView();
    // 하드웨어·런타임 섹션의 물리 지도 링크: 이 기기·Docker 엔진의 실제 id 로
    const tpn = (s.topology_physical && s.topology_physical.nodes) || [];
    const devN = tpn.find((n) => n && n.type === "device" && n.self) || tpn.find((n) => n && n.type === "device");
    const engN = tpn.find((n) => n && n.type === "engine");
    $$("a[data-physfocus]").forEach((a) => { const n = a.getAttribute("data-physfocus") === "device" ? devN : engN; if (n) attr(a, "href", mapHref(n.id, "physical")); });
    if (SH.LEVELS && SH.expCounts) {
      const c = SH.expCounts(s);
      P.render($("#mapLevels"), SH.LEVELS.map((L) => html`<div class="ml" data-k="${L.k}" title="${L.long + " — " + L.sub}"><dt>${L.label}</dt><dd class="num">${fmt.num(c[L.id])}</dd></div>`));
    }
    const HM = window.JarvisHomeMap;
    const ps = HM && HM.physStats ? HM.physStats(s) : null;
    const pc = (k, label, v, title, sub) => html`<div class="ml" data-k="${k}" title="${title}"><dt>${label}</dt><dd class="num">${v}${sub ? html`<small>${sub}</small>` : ""}</dd></div>`;
    P.render($("#mapPhys"), ps ? [
      pc("lan", "인터페이스", fmt.num(ps.ifaces), "이 기기의 네트워크 인터페이스(유선·Wi-Fi·Tailnet)"),
      pc("docker", "도커 네트워크", fmt.num(ps.nets), "도커 네트워크 수(host 네트워크 제외)"),
      pc("docker", "컨테이너", fmt.num(ps.ctrs), "도커 컨테이너 수"),
      pc("local", "호스트 프로세스", fmt.num(ps.procs), "기기에서 바로 도는 서버·데몬(systemd 유닛·pm2 앱·일반 프로세스)"),
      pc("tailnet", "Tailnet 기기", fmt.num(ps.online), "Tailnet 기기 온라인 " + ps.online + " / 전체 " + ps.total + (ps.mix ? " · " + ps.mix : ""), "/" + fmt.num(ps.total)),
      pc("lan", "같은 LAN 기기", fmt.num(ps.lanPeers), "같은 LAN 에서도 닿는 다른 tailnet 기기(공유기 NAT 경유는 뺌)"),
    ] : html`<div class="ml"><dt>물리 구성</dt><dd>–</dd></div>`);
    const svcs = s.services || [];
    const deg = svcs.filter((x) => x.status === "degraded").length;
    const down = svcs.filter((x) => x.status === "down").length;
    num("map.prob", deg + down);
    tone($("#mapProb"), down ? "crit" : deg ? "warn" : "ok");
    txt("map.probSub", deg + down ? [deg ? "저하 " + deg : null, down ? "다운 " + down : null].filter(Boolean).join(" · ") : "저하·다운 없음");
    if (HM) HM.render($("#miniMap"), s, view);
  }

  // ------------------------------------------------------------------ 인그레스
  function renderIngress(s) {
    const ing = s.ingress || {};
    const c = ing.caddy || {};
    const routes = c.routes || [];
    const pub = c.public_hosts || routes.filter((r) => r.public).map((r) => r.host);
    const priv = c.private_hosts || routes.filter((r) => !r.public).map((r) => r.host);
    const cfgOk = c.config_ok !== false && !c.error;
    P.render($("#caddyBox"), html`<div class="ing-h"><h4>Caddy</h4><span class="ver">${c.version || "–"}</span>${pill(cfgOk ? "up" : "down", cfgOk ? "구성 정상" : "구성 오류")}</div>
      ${c.error ? html`<div class="notice" data-tone="crit">${ic("crit")}<div>${c.error}</div></div>` : ""}
      <dl class="ing-stats"><div><dt>라우트</dt><dd>${fmt.num(routes.length)}</dd></div><div><dt>공개 호스트</dt><dd>${fmt.num(pub.length)}</dd></div><div><dt>비공개 호스트</dt><dd>${fmt.num(priv.length)}</dd></div></dl>
      <div class="hostcloud" aria-label="Caddy 호스트">${pub.map((h) => html`<span class="pub" title="공개(:80·:443)">${h.replace("." + (ing.domain || ""), "")}</span>`)}${priv.map((h) => html`<span title="tailnet 전용(:443)">${h.replace("." + (ing.domain || ""), "")}</span>`)}</div>
      <p class="tile-foot">${c.container ? "컨테이너 " + c.container + " · " : ""}Caddyfile 수정 ${fmt.ago(c.caddyfile_mtime)} · 보라색은 공개 호스트</p>`);

    const t = ing.tunnel || {};
    const rtt = (t.rtt_ms || []).filter(isNum);
    const avg = rtt.length ? rtt.reduce((a, b) => a + b, 0) / rtt.length : null;
    const byCode = t.responses_by_code || {};
    const cls = { "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0 };
    Object.keys(byCode).forEach((k) => { const d = String(k)[0]; if (cls[d + "xx"] != null) cls[d + "xx"] += byCode[k] || 0; });
    P.render($("#tunnelBox"), html`<div class="ing-h"><h4>Cloudflare Tunnel</h4><span class="ver">${t.name || ""} · ${t.version || "–"}</span>${pill(t.ready ? "up" : "down", t.ready ? "준비됨" : "준비 안 됨")}</div>
      <dl class="ing-stats">
        <div><dt>연결</dt><dd>${fmt.num(t.ready_connections != null ? t.ready_connections : t.connections)}<small>/${fmt.num(t.connections)}</small></dd></div>
        <div><dt>엣지</dt><dd title="${(t.edge_locations || []).join(", ")}">${(t.edge_locations || []).join("·") || "–"}</dd></div>
        <div><dt>RTT 평균</dt><dd>${fmt.ms(avg)}</dd></div>
        <div><dt>누적 요청</dt><dd>${fmt.num(t.total_requests)}</dd></div>
        <div><dt>요청 오류</dt><dd class="${t.request_errors ? "w" : ""}">${fmt.num(t.request_errors)}</dd></div>
        <div><dt>분당 · 동시</dt><dd>${fmt.num(t.req_per_min, 0)}<small> · ${fmt.num(t.concurrent_requests)}</small></dd></div>
      </dl>
      <div class="split"><span class="split-l">응답 코드</span>${stackBar([[cls["2xx"], "t-ok", "2xx"], [cls["3xx"], "t-info", "3xx"], [cls["4xx"], "t-warn", "4xx"], [cls["5xx"], "t-crit", "5xx"]], "thin")}<ul class="legend inline"><li><i class="sw t-ok"></i>2xx <span class="lv">${fmt.num(cls["2xx"])}</span></li><li><i class="sw t-info"></i>3xx <span class="lv">${fmt.num(cls["3xx"])}</span></li><li><i class="sw t-warn"></i>4xx <span class="lv">${fmt.num(cls["4xx"])}</span></li><li><i class="sw t-crit"></i>5xx <span class="lv">${fmt.num(cls["5xx"])}</span></li></ul></div>
      ${t.outdated ? html`<div class="notice" data-tone="info">${ic("info")}<div>cloudflared <b>${t.outdated.current}</b> → <b>${t.outdated.latest}</b> 업데이트가 있어요.</div></div>` : ""}
      <p class="tile-foot">터널 호스트 ${fmt.num((t.hostnames || []).length)}개 · 인그레스 v${t.ingress_version != null ? t.ingress_version : "–"} (${fmt.ago(t.ingress_updated)} 갱신)${t.process_start ? " · 프로세스 가동 " + fmt.dur(nowS() - t.process_start, 1) : ""}</p>`);
    const errs = (t.recent_errors || []).slice().reverse();
    txt("tun.errs", errs.length + "건");
    $("#tunErrors").hidden = !errs.length;
    if ($("#tunErrors").open || !$("#tunErrorsBody").__jarvisHtml) {
      P.render($("#tunErrorsBody"), errs.map((e) => html`<li data-tone="${e.level === "error" ? "crit" : "warn"}"><span class="lg-t">${fmt.datetime(e.ts)}</span><span class="lg-l">${e.level}</span><span class="mono">${e.msg}</span></li>`));
    }
  }

  // ------------------------------------------------------------------ 트래픽
  function renderTraffic(s) {
    const t = (s.ingress && s.ingress.traffic) || {};
    const w = (TR.range === "24h" ? t.last_24h : t.last_1h) || {};
    num("tr.req", w.requests);
    txt("tr.p50", fmt.ms(w.p50_ms));
    txt("tr.p95", fmt.ms(w.p95_ms));
    txt("tr.other", TR.range === "24h" ? "최근 1시간 " + fmt.num((t.last_1h || {}).requests) + "건" : "24시간 " + fmt.num((t.last_24h || {}).requests) + "건");
    const bs = w.by_status || {};
    const ST_K = [["2xx", "t-ok"], ["3xx", "t-info"], ["4xx", "t-warn"], ["5xx", "t-crit"]];
    P.render($("#trStatus"), stackBar(ST_K.map(([k, c]) => [bs[k] || 0, c, k]), "thin"));
    P.render($("#trStatusLegend"), ST_K.map(([k, c]) => html`<li><i class="sw ${c}"></i>${k} <span class="lv">${fmt.num(bs[k] || 0)}</span></li>`));
    const bv = w.by_via || {};
    const keys = VIA_ORDER.filter((k) => bv[k] != null).concat(Object.keys(bv).filter((k) => VIA_ORDER.indexOf(k) < 0));
    const vcls = (k) => ({ tunnel: "s1", tailnet: "s2", lan: "s3", docker: "s4" }[k] || "t-muted");
    P.render($("#trVia"), stackBar(keys.map((k) => [bv[k] || 0, vcls(k), labels.trafficVia[k] || k]), "thin"));
    P.render($("#trViaLegend"), keys.map((k) => html`<li><i class="sw ${vcls(k)}"></i>${labels.trafficVia[k] || k} <span class="lv">${fmt.num(bv[k] || 0)}</span></li>`));

    const is24 = TR.range === "24h";
    const per = (is24 ? t.per_hour : t.per_minute) || [];
    const per5 = is24 ? [] : t.per_minute_5xx || [];
    const unit = is24 ? "시간" : "분";
    const max = Math.max.apply(null, [1].concat(per.map((v) => v || 0)));
    txt("tr.barsL", per.length + unit + " 전");
    txt("tr.barsC", is24 ? "시간당 요청 · 최대 " + fmt.num(max) + "건" : "분당 요청 · 최대 " + fmt.num(max) + "건 · 빨강은 5xx");
    const bars = $("#trBars");
    attr(bars, "aria-label", (is24 ? "최근 24시간 시간당" : "최근 60분 분당") + " 요청 수, 최대 " + max + "건");
    keyed(bars, per.map((_, i) => i), (i) => i, () => {
      const b = mk("div", "b");
      b.innerHTML = "<i></i><i class=\"e\"></i>";
      return b;
    }, (b, i) => {
      const v = per[i] || 0, e = per5[i] || 0;
      const ago = per.length - 1 - i;
      // 막대 높이는 transform(scaleY)로 — 레이아웃 없이 합성만. 값이 바뀐 막대만 움직인다(cssVar 가 같은 값은 건너뛴다)
      cssVar(b.firstElementChild, "--h", Math.max(v / max, 0.012).toFixed(3));
      cssVar(b.lastElementChild, "--h", (e / max).toFixed(3));
      const tt = (ago === 0 ? "지금(이번 " + unit + ")" : ago + unit + " 전") + " · " + fmt.num(v) + "건" + (e ? " · 5xx " + e + "건" : "");
      if (b.title !== tt) b.title = tt;
      b.classList.toggle("now", ago === 0);
    });

    // 로그 스트림이 끊기면 위 숫자가 멈춘다 — "요청 없음"으로 오해하지 않게 늘 보여 준다
    const stv = t.stream;
    const se = $("#trStream");
    const gen = (s.meta && s.meta.generated_at) || nowS();
    if (!stv) { tone(se, "muted"); txt("tr.stream", "로그 스트림 정보 없음"); }
    else if (stv.connected) { tone(se, "ok"); txt("tr.stream", "Caddy 접근 로그 스트림 연결됨" + (stv.since ? " · " + fmt.ago(stv.since, gen) + "부터" : "")); }
    else { tone(se, "crit"); txt("tr.stream", "로그 스트림 끊김" + (stv.error ? " — " + stv.error : "") + " · 위 수치가 멈춰 있을 수 있어요"); }
    const errs = t.errors || [];
    txt("tr.errCount", errs.length ? String(errs.length) : "");
    syncTabs();
    const panel = $("#panel-" + TR.tab);
    const recentTable = (rec) => html`<table class="tbl"><thead><tr><th scope="col">시각</th><th scope="col">요청</th><th scope="col">상태</th><th scope="col" class="r">시간</th><th scope="col">경로</th><th scope="col">클라이언트</th></tr></thead><tbody>${rec.map((r) => html`<tr><td class="nw num">${fmt.time(r.ts)}</td><td><span class="host">${r.host}</span><div class="mono path-l" title="${r.path}">${r.method} ${r.path}</div></td><td><span class="code" data-tone="${codeTone(r.status)}">${r.status}</span></td><td class="r">${fmt.ms(r.ms)}</td><td class="nw">${labels.trafficVia[r.via] || r.via || "–"}</td><td class="nw mono">${r.client || "–"}${r.country ? " · " + r.country : ""}</td></tr>`)}</tbody></table>`;
    if (TR.tab === "hosts") {
      const hosts = w.hosts || [];
      // 호스트가 몇 개뿐이면 남는 자리에 최근 요청을 미리 보여 준다(타일이 옆 인그레스 높이만큼 늘어나 비지 않게)
      const rec = (t.recent || []).slice(0, Math.max(0, 7 - hosts.length));
      const preview = hosts.length < 6 && rec.length ? html`<p class="tr-sub">최근 요청 <button type="button" class="linkish" data-trtab="recent">전체 보기 →</button></p>${recentTable(rec)}` : "";
      P.render(panel, hosts.length ? html`<table class="tbl"><thead><tr><th scope="col">호스트</th><th scope="col" class="r">요청</th><th scope="col" class="r">4xx</th><th scope="col" class="r">5xx</th><th scope="col" class="r">p50</th><th scope="col" class="r">p95</th><th scope="col" class="r">마지막</th></tr></thead><tbody>${hosts.map((h) => html`<tr class="${h.known === false ? "unk-row" : ""}"><td class="host">${h.host}${h.known === false ? html`<span class="badge unk" data-tone="warn">미등록</span>` : ""}</td><td class="r">${fmt.num(h.requests)}</td><td class="r">${fmt.num(h.errors_4xx)}</td><td class="r ${h.errors_5xx ? "c" : ""}">${fmt.num(h.errors_5xx)}</td><td class="r">${fmt.ms(h.p50_ms)}</td><td class="r">${fmt.ms(h.p95_ms)}</td><td class="r">${fmt.ago(h.last_ts)}</td></tr>`)}</tbody></table>${hosts.some((h) => h.known === false) ? html`<p class="dest-note" style="padding:8px 10px">미등록 = Caddy 에 없는 호스트로 들어온 요청(예: 지운 서비스를 계속 두드리는 클라이언트).</p>` : ""}${preview}` : html`<p class="empty">이 기간에 요청이 없어요.</p>${preview}`);
    } else if (TR.tab === "recent") {
      const rec = t.recent || [];
      P.render(panel, rec.length ? recentTable(rec) : html`<p class="empty">최근 요청이 없어요.</p>`);
    } else {
      P.render(panel, errs.length ? html`<ul class="loglist" style="max-height:none;padding-top:8px">${errs.map((e) => html`<li data-tone="${e.level === "error" ? "crit" : "warn"}"><span class="lg-t">${fmt.datetime(e.ts)}</span><span class="lg-l">${e.level}</span><b>${e.msg}</b> <span class="mono">${e.logger || ""}${e.error ? " — " + e.error : ""}</span></li>`)}</ul>` : html`<p class="empty">Caddy 오류·경고가 없어요.</p>`);
    }
  }
  function syncTabs() {
    $$("#trTabs [role=tab]").forEach((b) => {
      const on = b.getAttribute("data-v") === TR.tab;
      attr(b, "aria-selected", String(on));
      attr(b, "tabindex", on ? "0" : "-1");
      const p = document.getElementById(b.getAttribute("aria-controls"));
      if (p && p.hidden === on) p.hidden = !on;
    });
  }

  // ------------------------------------------------------------------ Tailscale
  function renderTail(s) {
    const t = s.tailscale || {};
    const self = t.self || {};
    P.render($("#tailState"), pill(t.backend_state === "Running" ? "up" : t.backend_state ? "down" : "unknown", t.backend_state || "알 수 없음"));
    num("tail.on", t.peers_online);
    txt("tail.total", fmt.num(t.peers_total));
    const peers = t.peers || [];
    const direct = peers.filter((p) => p.online && p.connection === "direct").length;
    const relay = peers.filter((p) => p.online && p.connection === "relay").length;
    const idle = peers.filter((p) => p.online && !p.connection).length;
    txt("tail.mix", SH.tailnetMix ? SH.tailnetMix(s).text : "직결 " + direct + " · 중계 " + relay + " · 유휴 " + idle);
    const ring = $("#tailRing");
    const C = 2 * Math.PI * 30;
    if (!ring._c) {
      ring.innerHTML = '<svg viewBox="0 0 76 76" aria-hidden="true" focusable="false"><circle class="tr-bg" cx="38" cy="38" r="30"/><circle class="tr-on" cx="38" cy="38" r="30" style="stroke-dasharray:0 ' + C.toFixed(1) + '"/></svg>';
      ring._c = $(".tr-on", ring);
    }
    const frac = t.peers_total ? (t.peers_online || 0) / t.peers_total : 0;
    ring._c.style.strokeDasharray = (frac * C).toFixed(1) + " " + C.toFixed(1);
    P.render($("#tailSelf"), html`<div><dt>이 기기</dt><dd class="mono">${self.dns_name ? self.dns_name.replace(/\.$/, "") : self.hostname || "–"}</dd></div><div><dt>IP</dt><dd class="mono">${(self.ips || []).join(" · ") || "–"}</dd></div><div><dt>DERP · 버전</dt><dd>${self.relay || "–"} · v${t.version || "–"}</dd></div><div><dt>Serve · Funnel</dt><dd>${t.serve ? "Serve 켜짐" : "Serve 꺼짐"} · ${t.funnel ? "Funnel 켜짐" : "Funnel 꺼짐"}</dd></div>`);
    const hl = t.health || [];
    $("#tailHealth").hidden = !hl.length;
    if (hl.length) P.render($("#tailHealth"), hl.map((x) => html`<div>${typeof x === "string" ? x : JSON.stringify(x)}</div>`));
    P.render($("#peers"), peers.map((p) => {
      const on = !!p.online;
      const conn = !on ? html`<span class="badge">오프라인</span>` : p.connection === "direct" ? html`<span class="badge" data-tone="ok">직결</span>` : p.connection === "relay" ? html`<span class="badge" data-tone="warn">중계${p.relay ? " · DERP " + p.relay : ""}</span>` : html`<span class="badge" title="${"온라인이지만 지금 오가는 연결이 없어요" + (p.relay ? " · 홈 DERP " + p.relay : "")}">유휴</span>`;
      const seen = !on ? (p.last_seen ? "마지막 접속 " + fmt.ago(p.last_seen) : isNum(p.last_seen_ago_s) ? "마지막 접속 " + fmt.dur(p.last_seen_ago_s, 1) + " 전" : "마지막 접속 기록 없음") : p.active ? "활성 연결" + (p.cur_addr ? " · " + p.cur_addr : "") : "대기 중";
      return html`<li class="peer ${on ? "" : "off"}" data-tone="${on ? "ok" : "muted"}"><i class="shape"></i><span class="pe-n"><span>${p.hostname}</span>${p.exit_node ? html`<span class="badge">출구 노드</span>` : ""}</span>${conn}<span class="pe-s">${[p.os, p.ip, seen].filter(Boolean).join(" · ")}</span></li>`;
    }));
  }

  // ------------------------------------------------------------------ 포트
  const PORT_MATCH = {
    all: () => true,
    exposed: (l) => l.scope === "all" || l.scope === "lan",
    tailnet: (l) => l.scope === "tailnet",
    docker: (l) => l.scope === "docker",
    loopback: (l) => l.scope === "loopback",
  };
  const SCOPE_TONE = { all: "warn", lan: "warn", tailnet: "info", docker: "muted", loopback: "ok" };
  function renderPorts(s) {
    const L = s.listeners || [];
    Object.keys(PORT_MATCH).forEach((k) => txt("pf." + k, fmt.num(L.filter(PORT_MATCH[k]).length)));
    $$("#portFilter button").forEach((b) => attr(b, "aria-pressed", String(b.getAttribute("data-v") === PF.f)));
    const exp = L.filter(PORT_MATCH.exposed);
    P.render($("#portsNote"), exp.length
      ? html`LAN 전체(모든 인터페이스)에 열린 포트가 <b>${exp.length}개</b> 있어요 — 같은 네트워크의 누구나 닿을 수 있어요. 노란 줄로 표시했어요.`
      : html`LAN 전체에 열린 포트가 없어요.`);
    const rows = L.filter(PORT_MATCH[PF.f] || PORT_MATCH.all);
    P.render($("#portsBody"), rows.length ? rows.map((l) => {
      const wide = l.scope === "all" || l.scope === "lan";
      const reachT = (l.reach || []).map((r) => ({ lan: "LAN", tailnet: "Tailnet", docker: "Docker", local: "로컬" }[r] || r)).join(" · ");
      return html`<tr class="${wide ? "lanwide" : ""}"><td class="nw"><span class="port-n">${l.port}</span><span class="own">${String(l.proto || "").toUpperCase()}</span></td><td class="nw" title="${reachT ? "닿는 곳: " + reachT : ""}"><span class="scope" data-tone="${SCOPE_TONE[l.scope] || "muted"}"><i class="shape"></i>${labels.scope[l.scope] || l.scope}</span>${l.scope === "all" ? "" : html`<span class="reach">${(l.reach || []).map((r) => html`<span>${REACH_L[r] || r}</span>`)}</span>`}</td><td class="nw">${l.process || "–"}${l.pid ? html`<span class="own">pid ${l.pid}</span>` : ""}</td><td>${OWNER_L[l.owner_kind] || l.owner_kind || "–"}${l.owner ? html`<span class="own">${l.owner}</span>` : ""}</td><td class="mono nw">${(l.addrs || []).join(", ")}</td></tr>`;
    }) : html`<tr><td colspan="5" class="empty">해당하는 포트가 없어요.</td></tr>`);
  }

  // ------------------------------------------------------------------ 컨테이너
  function renderContainers(s) {
    const cs = s.containers || [];
    const projs = s.projects || [];
    const sum = (s.summary || {}).containers || {};
    txt("ctr.aside", "실행 " + fmt.num(sum.running) + "/" + fmt.num(sum.total) + " · 프로젝트 " + projs.length);
    const byProj = new Map();
    cs.forEach((c) => { const k = c.project || ""; if (!byProj.has(k)) byProj.set(k, []); byProj.get(k).push(c); });
    const ctrRow = (c) => {
      const st = c.state === "running" ? "up" : c.state === "restarting" ? "degraded" : "down";
      const hl = c.health ? html`<span class="badge" data-tone="${c.health === "healthy" ? "ok" : c.health === "unhealthy" ? "crit" : "warn"}">${HEALTH_L[c.health] || c.health}</span>` : "";
      const extra = c.state !== "running" && c.exit_code != null ? " (코드 " + c.exit_code + (c.oom_killed ? ", OOM" : "") + ")" : "";
      return html`<tr><td><span class="ctr-n">${c.name}</span><span class="ctr-img" title="${c.image}">${c.image}</span></td><td class="nw">${pill(st, (STATE_L[c.state] || c.state) + extra)} ${hl}</td><td class="r">${fmt.durShort(c.uptime_s)}</td><td class="r ${c.restart_count ? "w" : ""}">${fmt.num(c.restart_count)}</td><td class="r">${fmt.pct(c.cpu_pct, 1)}</td><td class="r">${fmt.bytes(c.mem_bytes)}</td></tr>`;
    };
    const rows = [];
    const pRow = (p) => html`<tr class="proj"><td colspan="6"><b>${p.name}</b>${pill(p.status === "running" ? "up" : p.status === "partial" ? "degraded" : p.status ? "down" : "unknown", (PROJ_L[p.status] || "compose 밖") + (p.total != null ? " " + p.running + "/" + p.total : ""))}${p.dir ? html`<span class="mono">${p.dir}</span>` : ""}</td></tr>`;
    projs.forEach((p) => {
      rows.push(pRow(p));
      (byProj.get(p.name) || []).forEach((c) => rows.push(ctrRow(c)));
      byProj.delete(p.name);
    });
    byProj.forEach((list, k) => {
      rows.push(pRow({ name: k || "프로젝트 없음", status: null, dir: null }));
      list.forEach((c) => rows.push(ctrRow(c)));
    });
    P.render($("#ctrBody"), rows.length ? rows : html`<tr><td colspan="6" class="empty">컨테이너가 없어요.</td></tr>`);
  }

  // ------------------------------------------------------------------ systemd
  const UNIT_MATCH = {
    all: () => true,
    failed: (u) => u.active === "failed",
    app: (u) => u.category === "app" || u.category === "server",
    infra: (u) => u.category === "infra",
    system: (u) => u.category === "system" || u.category === "desktop",
  };
  function renderUnits(s) {
    const us = s.units || [];
    Object.keys(UNIT_MATCH).forEach((k) => txt("uf." + k, fmt.num(us.filter(UNIT_MATCH[k]).length)));
    $$("#unitFilter button").forEach((b) => attr(b, "aria-pressed", String(b.getAttribute("data-v") === UFL.f)));
    const rows = us.filter(UNIT_MATCH[UFL.f] || UNIT_MATCH.all);
    P.render($("#units"), rows.length ? rows.map((u) => {
      const t = u.active === "active" ? "ok" : u.active === "failed" ? "crit" : u.active === "activating" || u.active === "reloading" || u.active === "deactivating" ? "warn" : "muted";
      const right = u.active === "failed"
        ? "실패" + (u.restarts ? " · 재시작 " + u.restarts + "회" : "")
        : [u.active !== "active" ? u.active : fmt.durShort(u.uptime_s), isNum(u.mem_bytes) ? fmt.bytes(u.mem_bytes, 0) : null].filter(Boolean).join(" · ");
      return html`<li class="unit ${u.active === "failed" ? "failed" : ""}" data-tone="${t}"><i class="shape"></i><span class="un-n" title="${u.name}">${u.name}</span><span class="un-r">${right}</span><span class="un-d">${[u.description, u.scope === "user" ? "사용자" : "시스템", u.enabled].filter(Boolean).join(" · ")}</span></li>`;
    }) : html`<li class="empty">해당하는 유닛이 없어요.</li>`);
  }

  // ------------------------------------------------------------------ 프로세스
  function renderProcs(s) {
    const p = (s.system || {}).processes || {};
    txt("proc.aside", "프로세스 " + fmt.num(p.count) + " · 스레드 " + fmt.num(p.threads));
    const own = (x) => {
      if (!x.owner) return "";
      const o = /^[0-9a-f]{40,}$/.test(x.owner) ? x.owner.slice(0, 12) : x.owner;
      return (OWNER_L[x.owner_kind] || x.owner_kind || "") + " " + o;
    };
    const row = (x, byCpu) => html`<li class="proc" title="${x.cmd || x.name}"><span class="pr-n">${x.name}<small>pid ${x.pid} · ${x.user || "?"}</small></span><span class="pr-v">${byCpu ? fmt.pct(x.cpu_pct, 1) : fmt.bytes(x.rss)}<small>${byCpu ? fmt.bytes(x.rss) : fmt.pct(x.cpu_pct, 1)}</small></span><span class="pr-c">${[own(x), x.cmd].filter(Boolean).join(" — ")}</span></li>`;
    P.render($("#procCpu"), (p.top_cpu || []).slice(0, 6).map((x) => row(x, true)));
    P.render($("#procMem"), (p.top_mem || []).slice(0, 6).map((x) => row(x, false)));
  }

  // ------------------------------------------------------------------ 모니터 자기 진단(이 웹 서비스 자신도 감시 대상)
  //   판정·이름은 공용(JarvisShell.monitor) — 헤더의 연결 팝오버와 같은 말을 쓴다.
  const MON = SH.monitor || null;
  const isMonitor = (sv) => !!sv && (sv.kind === "monitor" || sv.id === "infra:monitor");
  const secs = (v) => (MON ? MON.secs(v) : isNum(v) ? fmt.num(v, 1) + "초" : "–");
  /** 카드·목록 행에 붙는 한 줄: "자기 진단 정상 · 수집기 15/15 · 스냅샷 18 ms" */
  function selfLine(sv, s) {
    if (!MON || !isMonitor(sv)) return "";
    const Hh = MON.health(s);
    const m = Hh.m || {};
    const bits = [Hh.known ? "수집기 " + Hh.fresh + "/" + Hh.total + " 제때" : null, isNum(m.snapshot_ms) ? "스냅샷 " + fmt.ms(m.snapshot_ms) : null, isNum(m.rss_bytes) ? fmt.bytes(m.rss_bytes) : null].filter(Boolean);
    return html`<p class="svc-self" data-tone="${Hh.tone}" title="${Hh.text}"><i class="shape"></i><b>자기 진단 ${Hh.short}</b>${bits.length ? html`<span>${bits.join(" · ")}</span>` : ""}</p>`;
  }
  /** 상세 시트의 "자기 진단" 본문 */
  function selfBody(sv, s) {
    const m = MON ? MON.of(s, sv) : (sv.extra && sv.extra.monitor) || s.monitor;
    if (!m || !MON) return html`<p class="reach-none">자기 진단 정보를 받지 못했어요 — Jarvis 백엔드가 이 정보를 아직 보내지 않아요.</p>`;
    const Hh = MON.health(s);
    const cs = (Array.isArray(m.collectors) ? m.collectors : []).filter((c) => c && c.name);
    const bad = (c) => !!(c.stale || c.error || (m.problems || []).indexOf(c.name) >= 0);
    const ordered = cs.filter(bad).concat(cs.filter((c) => !bad(c)));
    const commit = m.commit ? (SH.commitInfo ? SH.commitInfo(m.commit) : { text: String(m.commit).slice(0, 7), title: String(m.commit) }) : null;
    const cell = (k, v, sub) => html`<div><dt>${k}</dt><dd>${v}${sub ? html`<small>${sub}</small>` : ""}</dd></div>`;
    const loops = m.loops || {};
    const loopKeys = MON.LOOP_ORDER.filter((k) => loops[k]).concat(Object.keys(loops).filter((k) => MON.LOOP_ORDER.indexOf(k) < 0));
    const gen = (s.meta && s.meta.generated_at) || nowS();
    return html`<div class="self-sum" data-tone="${Hh.tone}"><i class="shape"></i><div><b>${Hh.short}</b> — ${Hh.detail}.${Hh.ok ? "" : html` <span class="self-sub">이 수집기의 값은 화면에서 오래된 것일 수 있어요.</span>`}</div></div>
      <dl class="self-grid">
        ${cell("버전", m.version ? "v" + m.version : "–", commit ? html`<span class="mono" title="${commit.title}">${commit.text}</span>` : "")}
        ${cell("가동", isNum(m.uptime_s) ? fmt.dur(m.uptime_s, 2) : "–", isNum(m.started_at) ? fmt.datetime(m.started_at) + " 시작" : "")}
        ${cell("메모리 (RSS)", fmt.bytes(m.rss_bytes), isNum(m.threads) ? "스레드 " + fmt.num(m.threads) : "")}
        ${cell("스냅샷 생성", fmt.ms(m.snapshot_ms), "요청마다 만드는 데 걸린 시간")}
        ${cell("스냅샷 크기", fmt.bytes(m.snapshot_bytes), isNum(m.snapshot_gz_bytes) ? "gzip " + fmt.bytes(m.snapshot_gz_bytes) : "")}
        ${cell("수신", m.bind ? html`<span class="mono">${m.bind}</span>` : "–", m.pid ? "PID " + m.pid : "")}
      </dl>
      <p class="self-h">수집기 최신성 <small>마지막 수집 뒤 지난 시간 / 한도 · ${fmt.num(cs.length)}개</small></p>
      <ul class="self-cols">${ordered.map((c) => {
        const t = c.error ? "crit" : bad(c) ? "warn" : "ok";
        const frac = isNum(c.age_s) && isNum(c.limit_s) && c.limit_s > 0 ? clamp(c.age_s / c.limit_s, 0, 1) : 0;
        const state = c.error ? "오류" : bad(c) ? "지연" : "제때";
        return html`<li data-tone="${t}" title="${c.name + " — " + state}"><span class="sc-n"><i class="shape"></i><span>${MON.collectorName(c.name)}</span><small class="mono">${c.name}</small></span><span class="sc-m" aria-hidden="true"><i style="${"--v:" + frac.toFixed(3)}"></i></span><span class="sc-v">${secs(c.age_s)}<small> / ${secs(c.limit_s)}</small><span class="sr"> — ${state}</span></span>${c.error ? html`<span class="sc-e">${String(c.error).slice(0, 200)}</span>` : ""}</li>`;
      })}</ul>
      ${loopKeys.length ? html`<p class="self-h">주기 작업 <small>한 번 도는 데 걸린 시간 · 마지막 실행</small></p>
      <ul class="self-loops">${loopKeys.map((k) => {
        const L = loops[k] || {};
        const over = isNum(L.last_ms) && isNum(L.interval_s) && L.last_ms > L.interval_s * 1000 * 0.8;
        return html`<li data-tone="${over ? "warn" : "muted"}"><span>${MON.LOOP_L[k] || k}${isNum(L.interval_s) ? html`<small>${fmt.num(L.interval_s)}초마다</small>` : ""}</span><span class="sc-v">${fmt.ms(L.last_ms)}<small> · ${L.last_at ? fmt.ago(L.last_at, gen) : "–"}</small></span></li>`;
      })}</ul>` : ""}`;
  }

  function renderChanges(s) {
    const el = $("#changesBody");
    if (!el) return;
    const names = { containers: "컨테이너", routes: "라우트", devices: "기기", units: "유닛", pm2: "PM2", lan: "LAN 기기" };
    const kinds = { added: "추가", removed: "관측 목록에서 제거", changed: "변경" };
    P.render(el, (s.changes || []).length ? s.changes.map((e) => {
      const before = e.before || {}; const after = e.after || {};
      const fields = [...new Set([...Object.keys(before), ...Object.keys(after)])].filter((k) => JSON.stringify(before[k]) !== JSON.stringify(after[k]));
      const text = fields.map((k) => {
        const val = (v) => v == null ? "–" : Array.isArray(v) ? v.join(", ") : String(v);
        const label = { full_id: "컨테이너 ID", image: "이미지", image_id: "이미지 ID", revision: "커밋", state: "상태", health: "헬스", started_at: "기동", upstreams: "배포 대상", listen: "수신 포트", online: "온라인", active: "상태", ips: "주소", hostname: "기기 이름", since: "기동", main_pid: "PID" }[k] || k;
        return label + ": " + val(before[k]) + " → " + val(after[k]);
      }).join(" · ");
      return html`<tr><td>${fmt.datetime(e.t)}</td><td>${names[e.source] || e.source}<br><span class="mono">${e.id}</span></td><td>${kinds[e.kind] || e.kind}</td><td style="max-width:40rem;overflow-wrap:anywhere;white-space:normal">${text}</td></tr>`;
    }) : html`<tr><td colspan="4" class="empty">기준 목록 수집 이후 관측한 변경이 없어요.</td></tr>`);
  }

  function renderFoot(s) {
    const m = s.meta || {};
    const up = isNum(m.generated_at) && isNum(m.started_at) ? fmt.dur(m.generated_at - m.started_at, 2) : "–";
    txt("foot", "Jarvis " + (m.version ? "v" + m.version : "") + " · Jarvis 가동 " + up + " · 스냅샷 " + fmt.time(m.generated_at) + " · " + (P.embed ? "10초" : "3초") + "마다 갱신");
    // 이 모니터 자신도 감시 대상 — 자기 진단으로 가는 작은 칩
    const chip = $("#footSelf");
    if (!chip || !MON) return;
    const sv = MON.svc(s);
    const Hh = MON.health(s);
    chip.hidden = !sv;
    if (!sv) return;
    attr(chip, "href", "#svc=" + encodeURIComponent(sv.id));
    tone(chip, Hh.tone);
    txt("foot.self", "모니터 자기 진단 · " + Hh.short);
    attr(chip, "title", Hh.text);
  }

  // ------------------------------------------------------------------ 상세 시트
  const dlg = $("#sheet");
  let sheetId = null;
  const row = (k, v, sub) => html`<div class="row"><dt>${k}</dt><dd>${v == null || v === "" ? "–" : v}${sub ? html`<span class="sub">${sub}</span>` : ""}</dd></div>`;
  const grp = (title, body, tag) => html`<h3 class="grp-title">${title}</h3>${tag === "div" ? html`<div class="grp">${body}</div>` : html`<dl class="grp">${body}</dl>`}`;
  const yn = (b) => (b ? "예" : "아니요");
  /** "어떻게 닿는가" — 지도와 같은 길(JarvisShell.graphOf + chains)을 단계로 보여 준다.
   *  넓으면 한 줄 노선(링크 라벨과 다음 칸이 붙어서 줄바꿈), 520px 아래에서는 지도 검사기처럼 번호 매긴 세로 단계. */
  const KT = SH.KIND_TITLE || { public: "공개 경로 — 인터넷에서", tailnet: "Tailnet 경로 — 내 기기에서", lan: "LAN 직접 — 같은 공유기에서", depends: "내부 의존" };
  const KIND_SHORT = { public: "공개", tailnet: "Tailnet", lan: "LAN" };
  function reachHtml(sv, s) {
    const G = SH.graphOf ? SH.graphOf(s) : null;
    const nid = G && G.svcNode.get(sv.id);
    const openPorts = (sv.listeners || []).filter((l) => l.scope !== "loopback" && l.scope !== "docker");
    const none = openPorts.length
      ? html`<p class="reach-none">지도에 그린 길은 없지만 이 포트로 바로 닿아요: ${openPorts.map((l) => html`<b class="mono">${l.port}/${l.proto}</b> <span class="sub-i">${labels.scope[l.scope] || l.scope}</span> `)}</p>`
      // 로컬 전용은 위 한 줄(공개 상태의 설명)이 이미 같은 말을 한다 — 두 번 쓰지 않는다
      : SH.expLevel && SH.expLevel(sv) === "local" ? "" : html`<p class="reach-none">밖에서 닿는 길이 없어요 — 이 기기 안에서만 쓰여요.</p>`;
    if (!G || !nid) return none;
    const nm = (id) => (SH.nodeName ? SH.nodeName(G.nodes.get(id) || id) : id);
    const tun = (s.ingress && s.ingress.tunnel) || {};
    const nw = (s.system && s.system.network) || {};
    // 간접(의존) 길은 그 앱마다 하나만 — 앱의 길은 앱 상세에서 본다
    const seenApp = new Set();
    const cs = SH.chains(G, nid, 6).filter((c) => {
      if (!SH.chainIndirect(c)) return true;
      const app = c.nodes[c.nodes.length - 2];
      if (seenApp.has(app)) return false;
      seenApp.add(app);
      return true;
    });
    if (!cs.length) return none;
    const detail = (id, i, c) => {
      const n = G.nodes.get(id);
      const next = c.nodes[i + 1] ? G.nodes.get(c.nodes[i + 1]) : null;
      if (i === c.nodes.length - 1) return SH.chainIndirect(c) ? nm(c.nodes[i - 1]) + " 이(가) 안에서 사용" : sv.upstream || "";
      if (!n) return "";
      if (n.type === "edge") return (tun.edge_locations || []).join("·");
      if (id === "zone:tailnet" && next && next.svc && next.svc.kind === "proxy") return (sv.hostnames || [])[0] ? "DNS → " + (nw.tailnet_ip || "tailnet IP") : "";
      if (id === "zone:lan") return (nw.lan_ips || [])[0] || "";
      return "";
    };
    return cs.map((c) => {
      const kind = SH.chainKind(c);
      const ind = SH.chainIndirect(c);
      const title = ind ? "간접 — " + nm(c.nodes[c.nodes.length - 2]) + " 를 거쳐 · " + (KIND_SHORT[kind] || "") + " 요청 경로" : KT[kind] || kind;
      return html`<div class="way" data-k="${kind}" data-indirect="${ind ? "1" : "0"}"><p class="way-h"><i aria-hidden="true"></i>${title}</p><ol class="path">${c.nodes.map((id, i) => {
        const e = i ? c.edges[i - 1] : null;
        const lb = e ? SH.edgeLabel(G, e) : "";
        const d = detail(id, i, c);
        return html`<li class="${"hop" + (i === c.nodes.length - 1 ? " dest" : "")}">${e ? html`<span class="link" data-k="${e.kind}"><span>${lb}</span></span>` : ""}<span class="node"><b>${nm(id)}</b>${d ? html`<small>${d}</small>` : ""}</span></li>`;
      })}</ol></div>`;
    });
  }
  function sheetSections(sv, s) {
    const out = [];
    const rt = sv.runtime || {};
    const e = sv.exposure || {};
    if (sv.status !== "up") {
      out.push({ k: "status", h: html`<div class="notice" data-tone="${P.tone(sv.status)}">${ic(sv.status === "down" ? "crit" : sv.status === "degraded" ? "warn" : "info")}<div><b>${labels.status[sv.status] || "확인 불가"}</b>${sv.status_reason ? html` — ${sv.status_reason}` : ""}</div></div>` });
    }
    if (sv.description) out.push({ k: "desc", h: html`<p class="sheet-desc">${sv.description}</p>` });
    // 이 모니터 자신이면 자기 진단을 맨 위에(주소·경로보다 먼저)
    if (isMonitor(sv)) out.push({ k: "self", h: grp(html`자기 진단 <span class="grp-note">이 웹 서비스 자신</span>`, selfBody(sv, s), "div") });
    const urls = sv.urls || [];
    if (urls.length) {
      out.push({ k: "urls", h: grp("주소", urls.map((u) => html`<div class="url-row">${extLink(u.url, html`<span>${shortUrl(u.url)}</span>${ic("ext")}`)}${u.label ? html`<small class="aud">${u.label}</small>` : ""}${u.code != null || u.error ? html`<span class="code" data-tone="${u.error ? "crit" : codeTone(u.code)}">${u.code != null ? u.code : "실패"}</span>` : ""}${audBadge(u.audience)}</div>`), "div") });
    }
    const flags = [["공개", e.public], ["Tailnet", e.tailnet], ["LAN", e.lan], ["로컬 전용", e.local_only]];
    const lv = SH.LEVELS && SH.expLevel ? SH.LEVELS.find((L) => L.id === SH.expLevel(sv)) : null;
    out.push({ k: "exposure", h: grp("어떻게 닿는가", html`${lv ? html`<p class="reach-sum" data-k="${lv.k}">${lv.reach}</p>` : ""}<div class="flags">${flags.map(([l, on]) => html`<span class="flag ${on ? "on" : ""}">${ic(on ? "check" : "x")}${l}</span>`)}</div>${reachHtml(sv, s)}<p class="path-more">${mapLink(sv.id, sv.name, "path-map", "개념 지도에서 이 길 보기", "concept")}</p>`, "div") });
    // 프로브 판정은 지도 검사기와 같은 공용 규칙(재확인 중·연속 실패·TLS·공개 차단)
    const pr = sv.probes || {};
    const pk = PROBE_ORDER.filter((k) => pr[k]).concat(Object.keys(pr).filter((k) => PROBE_ORDER.indexOf(k) < 0));
    if (pk.length) {
      out.push({ k: "probes", h: grp("프로브", pk.map((k) => {
        const p = pr[k] || {};
        const v = verdict(p, k, sv);
        const errNote = (n) => /^TLS:/.test(n) || (p.error && n === String(p.error).slice(0, 160));
        return html`<div class="probe-row" data-tone="${v.tone}"><span class="pl"><i class="shape"></i>${PROBE_L[k] || k}</span><span class="code" data-tone="${v.tone}">${v.blocked ? html`${ic("lock")}공개 차단` : v.value}</span><span class="ms">${fmt.ms(p.ms)}</span>${v.notes.map((n) => html`<span class="${"note" + (errNote(n) ? " err" : "")}">${n}</span>`)}${p.cf_ray ? html`<span class="note">CF-Ray ${p.cf_ray}</span>` : ""}</div>`;
      }), "div") });
    }
    const cert = sv.cert, ec = sv.edge_cert, dns = sv.dns;
    if (cert || ec || dns) {
      const dtone = (d) => (isNum(d) ? (d < 14 ? "crit" : d < 30 ? "warn" : "ok") : "muted");
      out.push({ k: "cert", h: grp("인증서 · DNS", html`${cert ? row("오리진 인증서", html`<span class="code" data-tone="${dtone(cert.days_left)}">${isNum(cert.days_left) ? Math.floor(cert.days_left) + "일 남음" : "–"}</span>`, [cert.issuer, cert.subject, cert.not_after ? "만료 " + fmt.date(cert.not_after) : null].filter(Boolean).join(" · ")) : ""}${ec ? row("Cloudflare 엣지 인증서", html`<span class="code" data-tone="${dtone(ec.days_left)}">${isNum(ec.days_left) ? Math.floor(ec.days_left) + "일 남음" : "–"}</span>`, ec.issuer) : ""}${dns ? row("공개 DNS", DNS_L[dns.points_to] || dns.points_to || "–", (dns.answers || []).join(", ") + (dns.t ? " · " + fmt.ago(dns.t) + " 확인" : "")) : ""}`) });
    }
    const tr = sv.traffic;
    if (tr) {
      out.push({ k: "traffic", h: grp("트래픽 (Caddy 접근 로그)", html`${row("요청 1시간 · 24시간", fmt.num(tr.requests_1h) + " · " + fmt.num(tr.requests_24h))}${row("4xx · 5xx (24시간)", fmt.num(tr.errors_4xx_24h) + " · " + fmt.num(tr.errors_5xx_24h))}${row("지연 p50 · p95 (24시간)", fmt.ms(tr.p50_ms_24h) + " · " + fmt.ms(tr.p95_ms_24h))}${row("마지막 요청", fmt.ago(tr.last_ts), tr.last_ts ? fmt.datetime(tr.last_ts) : null)}`) });
    }
    if (sv.runtime) {
      const rows = [row("유형", labels.runtime[rt.type] || rt.type)];
      if (rt.name) rows.push(row("이름", html`<span class="mono">${rt.name}</span>`));
      if (rt.project || rt.service) rows.push(row("compose", [rt.project, rt.service].filter(Boolean).join(" / ")));
      if (rt.image) rows.push(row("이미지", html`<span class="mono">${rt.image}</span>`));
      if (rt.state || rt.sub) rows.push(row("상태", SH.runtimeState ? SH.runtimeState(rt) : [STATE_L[rt.state] || rt.state, rt.sub, rt.health ? HEALTH_L[rt.health] || rt.health : null].filter(Boolean).join(" · ")));
      if (rt.online != null) rows.push(row("온라인", yn(rt.online)));
      if (rt.uptime_s != null) rows.push(row("가동 시간", fmt.dur(rt.uptime_s, 2), rt.started_at ? fmt.datetime(rt.started_at) + " 시작" : null));
      if (rt.restart_count != null) rows.push(row("재시작", html`<span class="${rt.restart_count ? "code" : ""}" data-tone="warn">${fmt.num(rt.restart_count)}회</span>`));
      if (rt.cpu_pct != null || rt.mem_bytes != null) rows.push(row("CPU · 메모리", fmt.pct(rt.cpu_pct, 1) + " · " + fmt.bytes(rt.mem_bytes)));
      if (rt.pid) rows.push(row("PID", String(rt.pid)));
      if (rt.cwd) rows.push(row("작업 디렉터리", html`<span class="mono">${rt.cwd}</span>`));
      if (rt.scope_unit) rows.push(row("스코프", html`<span class="mono">${rt.scope_unit}</span>`));
      if (rt.scope) rows.push(row("systemd 범위", rt.scope === "user" ? "사용자" : "시스템"));
      if (rt.enabled) rows.push(row("부팅 시 시작", rt.enabled));
      if (rt.description && rt.description !== sv.description) rows.push(row("설명", rt.description));
      if (rt.network_mode) rows.push(row("네트워크", html`<span class="mono">${rt.network_mode.length > 40 ? rt.network_mode.slice(0, 12) + "…" : rt.network_mode}</span>`));
      if (rt.ip) rows.push(row("IP", html`<span class="mono">${rt.ip}</span>`));
      if (rt.os) rows.push(row("OS", rt.os));
      out.push({ k: "runtime", h: html`${grp("런타임", rows)}<p class="path-more">${mapLink(sv.id, sv.name, "path-map", "물리 지도에서 어디서 도는지 보기", "physical")}</p>` });
    } else if (sv.kind === "static") {
      // 정적 라우트: 실행 주체가 따로 없다 — Caddy 가 직접 응답한다(오류가 아니다). 물리 지도에서는 그 Caddy 를 보여 준다
      const cad = (s.services || []).find((x) => x.kind === "proxy");
      const rows = [row("유형", "Caddy 직접 응답", "뒤에 앱·컨테이너가 없는 정적 라우트 — 상태는 프로브로만 정해요")];
      if (cad) rows.push(row("응답하는 것", html`<button type="button" class="dep-btn" data-sid="${cad.id}" data-tone="${P.tone(cad.status)}"><i class="shape"></i>${cad.name}</button>`, cad.runtime && cad.runtime.name ? cad.runtime.name : null));
      out.push({ k: "runtime", h: html`${grp("런타임", rows)}<p class="path-more">${mapLink(sv.id, sv.name, "path-map", "물리 지도에서 응답하는 Caddy 보기", "physical")}</p>` });
    }
    const ls = sv.listeners || [];
    if (ls.length) {
      out.push({ k: "listeners", h: grp("리스너", ls.map((l) => row(html`<span class="port-n">${l.port}</span> ${String(l.proto || "").toUpperCase()}`, html`<span class="scope" data-tone="${SCOPE_TONE[l.scope] || "muted"}"><i class="shape"></i>${labels.scope[l.scope] || l.scope}</span>`, [(l.addrs || []).join(", "), l.process].filter(Boolean).join(" · ")))) });
    }
    const deps = sv.depends_on || [];
    const rdeps = (s.services || []).filter((x) => (x.depends_on || []).indexOf(sv.id) >= 0);
    if (deps.length || rdeps.length) {
      const btn = (id) => { const d = P.pick.service(s, id); return html`<button type="button" class="dep-btn" data-sid="${id}" data-tone="${P.tone(d ? d.status : "unknown")}"><i class="shape"></i>${d ? d.name : id}</button>`; };
      out.push({ k: "deps", h: grp("의존 관계", html`${deps.length ? html`<div class="row stack-row"><dt>이 서비스가 쓰는 것</dt><dd class="deps">${deps.map(btn)}</dd></div>` : ""}${rdeps.length ? html`<div class="row stack-row"><dt>이 서비스를 쓰는 것</dt><dd class="deps">${rdeps.map((x) => btn(x.id))}</dd></div>` : ""}`) });
    }
    const ex = sv.extra || {};
    // pm2 데몬: 앱 표(이름 · 상태 · 재시작 · 불안정 재시작) — 앱이 연 포트의 서비스로 이어 준다
    if (sv.kind === "process-manager" && Array.isArray(ex.apps)) {
      const full = new Map(((s.pm2 && s.pm2.apps) || []).map((a) => [a.name, a]));
      const stT = (st) => (st === "online" ? "ok" : st === "errored" ? "crit" : st === "stopped" || st === "stopping" ? "muted" : "warn");
      out.push({ k: "pm2apps", h: grp("pm2 앱", ex.apps.length ? ex.apps.map((a) => {
        const f = full.get(a.name) || {}; const st = a.status || f.status;
        const own = (s.services || []).find((x) => x.runtime && x.runtime.type === "pm2" && x.runtime.name === a.name);
        const unstable = f.unstable_restarts != null ? f.unstable_restarts : a.unstable_restarts;
        return row(own ? html`<button type="button" class="dep-btn" data-sid="${own.id}" data-tone="${P.tone(own.status)}"><i class="shape"></i>${a.name}</button>` : a.name,
          html`<span class="code" data-tone="${stT(st)}">${STATE_L[st] || st || "–"}</span>`,
          "재시작 " + fmt.num(a.restarts != null ? a.restarts : f.restarts) + "회" + (unstable ? " · 불안정 재시작 " + fmt.num(unstable) + "회" : ""));
      }) : row("앱", "없음")) });
    }
    // extra.monitor 는 위 "자기 진단"이, extra.apps(pm2)는 위 표가 풀어서 보여 준다
    const exK = Object.keys(ex).filter((k) => !(k === "monitor" && isMonitor(sv)) && !(k === "apps" && sv.kind === "process-manager"));
    if (exK.length) {
      const EXL = { version: "버전", routes: "라우트", config_ok: "구성 정상", connections: "연결", edge_locations: "엣지", tunnel: "터널", backend_state: "상태", peers_online: "온라인 피어", ips: "IP", online: "온라인 앱", total: "전체 앱", daemon_pid: "데몬 PID", apps: "앱" };
      const val = (v) => (Array.isArray(v) ? v.map((o) => (o && typeof o === "object" ? o.name || o.id || JSON.stringify(o) : o)).join(", ") : typeof v === "boolean" ? yn(v) : v == null ? "–" : typeof v === "object" ? JSON.stringify(v) : String(v));
      out.push({ k: "extra", h: grp("추가 정보", exK.map((k) => row(EXL[k] || k, val(ex[k])))) });
    }
    const idRows = [row("ID", html`<span class="mono">${sv.id}</span>`)];
    if (sv.upstream) idRows.push(row("업스트림", sv.kind === "static" ? sv.upstream : html`<span class="mono">${sv.upstream}</span>`));
    if ((sv.hostnames || []).length) idRows.push(row("호스트 이름", html`<span class="mono">${sv.hostnames.join(", ")}</span>`));
    if (sv.snippet) idRows.push(row("Caddy 스니펫", sv.snippet === "public" ? "public (터널 + tailnet)" : "private (tailnet 전용)"));
    if (sv.in_tunnel != null) idRows.push(row("터널 인그레스에 등록", yn(sv.in_tunnel)));
    if (sv.header_inject) idRows.push(row("관리자 헤더 주입", "예"));
    out.push({ k: "ident", h: grp("식별 정보", idRows) });
    return out;
  }
  function renderSheet(s) {
    if (!sheetId || !s) return;
    const sv = P.pick.service(s, sheetId);
    if (!sv) {
      P.render($("#sheetHead"), html`<h2 id="sheetTitle">찾을 수 없어요</h2>`);
      keyed($("#sheetBody"), [{ k: "gone" }], (x) => x.k, () => mk("section", "sheet-sec"), (el) => P.render(el, html`<p class="gone">이 서비스가 지금 목록에 없어요. 중지됐거나 이름이 바뀌었을 수 있어요.</p>`));
      return;
    }
    const cat = sv.category;
    // 머리 모양은 지도 검사기와 같다: 분류 · 이름 · 보조 이름 · [상태 + 노출 배지 … 동작(열기 · 다른 페이지로)]
    const primary = (sv.urls || []).find((u) => P.safeUrl(u.url) !== "#");
    P.render($("#sheetHead"), html`<p class="sht-top"><span class="cat-${cat}">${ic(CAT_ICON[cat] || "layers")}</span>${labels.category[cat] || cat}${sv.kind ? " · " + (SH.kindLabel ? SH.kindLabel(sv.kind) : labels.kind[sv.kind] || sv.kind) : ""}</p><h2 id="sheetTitle">${sv.name}</h2>${sv.title && sv.title !== sv.name ? html`<p class="sht-title">${sv.title}</p>` : ""}<div class="sht-row">${pill(sv.status)}<span class="badges">${expBadges(sv)}</span><span class="sht-acts">${primary ? html`<a class="sh-act" href="${P.safeUrl(primary.url)}" target="_blank" rel="noopener noreferrer">${ic("ext")}<span>열기</span><span class="sr"> (새 창)</span></a>` : ""}<a class="sh-act" href="${mapHref(sv.id, "physical")}" aria-label="${sv.name + " — 물리 지도에서 어디서 도는지 보기"}" title="어느 기기·인터페이스·도커 네트워크 안에서 도는가">${ic("server")}<span>어디서 도나 <small class="sa-s">물리</small></span></a><a class="sh-act primary" href="${mapHref(sv.id, "concept")}" aria-label="${sv.name + " — 개념 지도에서 노출 경로 보기"}" title="무엇이 어떤 경로로 공개되는가">${ic("route")}<span>노출 경로 <small class="sa-s">개념</small></span></a></span></div>`);
    // 제자리 갱신(JarvisShell.morph): 가동 시간 같은 숫자만 바뀌면 그 글자만 고친다 → 시트 안에서 글자를 골라 복사할 수 있다
    keyed($("#sheetBody"), sheetSections(sv, s), (x) => x.k, () => mk("section", "sheet-sec"), (el, x) => (SH.morph ? SH.morph(el, x.h) : P.render(el, x.h)));
  }
  /** 주소의 #svc=<id> → id (없으면 null) */
  function svcFromHash() {
    const m = /^#svc=(.+)$/.exec(location.hash || "");
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
  }
  let pendingSvc = svcFromHash();
  function openSheet(id, fromHash) {
    if (!id) return;
    if (!S) { pendingSvc = id; return; }
    const changed = sheetId !== id;
    sheetId = id;
    renderSheet(S);
    if (!dlg.open) {
      try { dlg.showModal(); } catch (e) { dlg.setAttribute("open", ""); }
    }
    if (changed) $("#sheetBody").scrollTop = 0;
    // 해시는 여는 순간에만 쓴다(3초 갱신은 건드리지 않음). 기록을 쌓지 않도록 replaceState.
    const want = "#svc=" + encodeURIComponent(id);
    if (!fromHash && location.hash !== want) {
      try { history.replaceState(null, "", location.pathname + location.search + want); } catch (e) { /* 무시 */ }
    }
  }
  dlg.addEventListener("close", () => {
    sheetId = null;
    if (/^#svc=/.test(location.hash || "")) {
      try { history.replaceState(null, "", location.pathname + location.search); } catch (e) { /* 무시 */ }
    }
  });
  // 뒤로/앞으로·주소 직접 입력·다른 페이지(맵)의 /#svc= 링크
  window.addEventListener("hashchange", () => {
    const id = svcFromHash();
    if (id) { if (!dlg.open || sheetId !== id) openSheet(id, true); }
    else if (dlg.open) dlg.close();
  });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) dlg.close(); });
  $("#sheetClose").addEventListener("click", () => dlg.close());

  // ------------------------------------------------------------------ 이동·강조
  function flash(tile) {
    if (!tile) return;
    tile.scrollIntoView({ behavior: calm() ? "auto" : "smooth", block: "start" });
    const h = tile.querySelector("h2, h3");
    if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
    if (!calm()) {
      tile.classList.remove("flash");
      void tile.offsetWidth;
      tile.classList.add("flash");
      setTimeout(() => tile.classList.remove("flash"), 1600);
    }
  }
  function go(action) {
    const [kind, arg] = String(action).split(":");
    if (kind === "units") { UFL.f = arg || "all"; store.set("unitFilter", UFL.f); if (S) renderUnits(S); flash($("#t-units").closest(".tile")); }
    else if (kind === "ports") { PF.f = arg || "all"; store.set("portFilter", PF.f); if (S) renderPorts(S); flash($("#t-ports").closest(".tile")); }
    else if (kind === "traffic") { TR.tab = arg || "hosts"; if (S) renderTraffic(S); flash($("#t-traffic").closest(".tile")); }
    else if (kind === "svc") { setSvcFilter(arg || "all"); flash($("#services")); }
    else if (kind === "tile") { const h = document.getElementById(arg); if (h) flash(h.closest(".tile")); }
  }

  // ------------------------------------------------------------------ 이벤트
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-sid],[data-cat],[data-go]");
    if (!t || e.target.closest("a")) return;
    if (t.hasAttribute("data-sid")) openSheet(t.getAttribute("data-sid"));
    else if (t.hasAttribute("data-cat")) { setSvcFilter(t.getAttribute("data-cat")); $("#svcSearch").value = ""; SV.q = ""; applySvcFilter(); flash($("#services")); }
    else if (t.hasAttribute("data-go")) go(t.getAttribute("data-go"));
  });
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-trtab]");
    if (!b) return;
    TR.tab = b.getAttribute("data-trtab"); store.set("trTab", TR.tab);
    if (S) renderTraffic(S);
    const tb = $('#trTabs [data-v="' + TR.tab + '"]'); if (tb) tb.focus();
  });
  function bindSeg(sel, get, set) {
    const el = $(sel);
    const sync = () => $$("button[data-v]", el).forEach((b) => attr(b, "aria-pressed", String(b.getAttribute("data-v") === get())));
    el.addEventListener("click", (e) => {
      const b = e.target.closest("button[data-v]");
      if (!b) return;
      set(b.getAttribute("data-v"));
      sync();
      fadeEls.forEach(updateFade);
    });
    sync();
  }
  bindSeg("#svcFilter", () => SV.filter, (v) => setSvcFilter(v));
  bindSeg("#svcView", () => SV.view, (v) => { if (SV.view === v) return; SV.view = v === "list" ? "list" : "cards"; store.set("svcView", SV.view); if (S) renderServices(S); });
  bindSeg("#histMetric", () => H.metric, (v) => { H.metric = v; store.set("hMetric", v); drawHistory(); });
  bindSeg("#histRange", () => H.range, (v) => { if (H.range === v) return; H.range = v; store.set("hRange", v); H.at = 0; drawHistory(); loadHistory(); });
  bindSeg("#trRange", () => TR.range, (v) => { TR.range = v; store.set("trRange", v); if (S) renderTraffic(S); });
  bindSeg("#portFilter", () => PF.f, (v) => { PF.f = v; store.set("portFilter", v); if (S) renderPorts(S); });
  bindSeg("#unitFilter", () => UFL.f, (v) => { UFL.f = v; store.set("unitFilter", v); if (S) renderUnits(S); });
  // 지도 관점 고르기: 지도와 같은 radiogroup(화살표로 옮기면 바로 고른다)
  function syncMapView() {
    $$("#mapView [role=radio]").forEach((b) => { const on = b.getAttribute("data-v") === MV.v; attr(b, "aria-checked", String(on)); attr(b, "tabindex", on ? "0" : "-1"); });
  }
  function pickMapView(v, focus) {
    const nv = v === "physical" ? "physical" : "concept";
    if (MV.v !== nv) {
      MV.v = nv;
      if (SH.mapView) SH.mapView.set(nv); else store.set("mapView", nv);
      if (S) renderMap(S); else syncMapView();
    }
    if (focus) { const b = $('#mapView [data-v="' + nv + '"]'); if (b) b.focus(); }
  }
  $("#mapView").addEventListener("click", (e) => { const b = e.target.closest("[role=radio]"); if (b) pickMapView(b.getAttribute("data-v")); });
  $("#mapView").addEventListener("keydown", (e) => {
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
    e.preventDefault();
    const list = ["concept", "physical"];
    const i = list.indexOf(MV.v);
    const j = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1 : (i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + list.length) % list.length;
    pickMapView(list[j], true);
  });
  syncMapView();
  $("#svcSearch").addEventListener("input", (e) => { SV.q = e.target.value || ""; applySvcFilter(); });
  (function bindTabs() {
    const tabs = $("#trTabs");
    const pick = (v, focus) => {
      TR.tab = v;
      store.set("trTab", v);
      if (S) renderTraffic(S); else syncTabs();
      fadeEls.forEach(updateFade);
      if (focus) { const b = $('[data-v="' + v + '"]', tabs); if (b) b.focus(); }
    };
    tabs.addEventListener("click", (e) => { const b = e.target.closest("[role=tab]"); if (b) pick(b.getAttribute("data-v")); });
    tabs.addEventListener("keydown", (e) => {
      const list = $$("[role=tab]", tabs).map((b) => b.getAttribute("data-v"));
      const i = list.indexOf(TR.tab);
      if (e.key === "ArrowRight") { e.preventDefault(); pick(list[(i + 1) % list.length], true); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); pick(list[(i - 1 + list.length) % list.length], true); }
      else if (e.key === "Home") { e.preventDefault(); pick(list[0], true); }
      else if (e.key === "End") { e.preventDefault(); pick(list[list.length - 1], true); }
    });
    syncTabs();
  })();
  // 접었다 편 상세(전원 레일·브리지·터널 로그)는 열 때 바로 최신 값으로 채운다.
  ["#rails", "#bridges", "#tunErrors"].forEach((sel) => $(sel).addEventListener("toggle", () => { if (S) { renderTherm(S); renderNet(S); renderIngress(S); } }));

  // ------------------------------------------------------------------ 섹션 바로가기(페이지 안)
  // 1320px 이상에서는 공용 헤더의 섹션 링크(data-sections)가 같은 일을 한다 → 여기 막대는 CSS 로 숨고 계산도 쉰다.
  (function bindLnav() {
    const nav = $("#lnav");
    if (!nav) return;
    const sc = $(".lnav-in", nav);
    const links = $$("a[data-lsec]", nav);
    const mq = window.matchMedia("(max-width: 1319px)");
    let rq = 0; let cur = null;
    const spy = () => {
      rq = 0;
      if (!mq.matches || window.JarvisLab?.concept?.id === "inbox") return;
      const line = nav.getBoundingClientRect().bottom + 40;
      let c = links[0].getAttribute("data-lsec");
      for (const a of links) {
        const t = document.getElementById(a.getAttribute("data-lsec"));
        if (t && t.getClientRects().length && t.getBoundingClientRect().top <= line) c = a.getAttribute("data-lsec");
      }
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) {
        const shown = links.filter(a => document.getElementById(a.dataset.lsec)?.getClientRects().length);
        if (shown.length) c = shown[shown.length - 1].getAttribute("data-lsec");
      }
      if (c === cur) return;
      cur = c;
      links.forEach((a) => {
        if (a.getAttribute("data-lsec") === c) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current");
      });
      const a = links.find((x) => x.getAttribute("data-lsec") === c);
      if (a && sc.scrollWidth > sc.clientWidth + 1) {
        const ar = a.getBoundingClientRect(); const sr = sc.getBoundingClientRect();
        if (ar.left < sr.left + 12) sc.scrollLeft += ar.left - sr.left - 12;
        else if (ar.right > sr.right - 12) sc.scrollLeft += ar.right - sr.right + 12;
      }
    };
    const req = () => { if (!rq) rq = requestAnimationFrame(spy); };
    window.addEventListener("scroll", req, { passive: true });
    window.addEventListener("resize", req, { passive: true });
    if (mq.addEventListener) mq.addEventListener("change", req); else if (mq.addListener) mq.addListener(req);
    req();
  })();

  // ------------------------------------------------------------------ 구독
  const RENDER = [renderMeta, renderHero, renderAlerts, renderHw, renderMap, renderServices, renderCpu, renderGpu, renderMem, renderTherm, renderNet, renderDisk, renderIngress, renderTraffic, renderTail, renderPorts, renderContainers, renderUnits, renderChanges, renderProcs, renderFoot, renderSheet, maybeHistory];
  const FADE_SEL = ["#alertList", "#peers", "#units", "#portsWrap", ".t-ctr .table-wrap", "#panel-hosts", "#panel-recent", "#panel-errors"];
  const fadeEls = [];
  function updateFade(el) {
    const more = el.scrollHeight - el.clientHeight - el.scrollTop > 4;
    if (el.classList.contains("more-below") !== more) el.classList.toggle("more-below", more);
  }
  FADE_SEL.forEach((sel) => {
    const el = $(sel);
    if (!el) return;
    fadeEls.push(el);
    el.addEventListener("scroll", () => updateFade(el), { passive: true });
  });
  function renderAll() {
    for (const fn of RENDER) {
      try { fn(S); } catch (err) { console.error("[home] " + (fn.name || "render") + " 실패:", err); }
    }
    fadeEls.forEach(updateFade);
  }
  // 다른 페이지(지도)에서 /#hardware · /#hw-cpu · /#network 로 오면: 브라우저는 첫 데이터 전에 이미 점프한다.
  // 서비스 목록이 그려지며 대상이 수천 px 밀려나므로, 첫 렌더 뒤(와 웹폰트 도착 뒤) 한 번 더 맞춘다 — 사용자가 먼저 움직였으면 건드리지 않는다.
  let userMoved = false;
  const markMoved = () => { userMoved = true; };
  ["wheel", "touchstart", "keydown", "pointerdown"].forEach((ev) => window.addEventListener(ev, markMoved, { passive: true, once: true }));
  function reapplyAnchor() {
    if (userMoved || svcFromHash()) return;
    const h = location.hash || "";
    if (!/^#[A-Za-z][\w-]*$/.test(h)) return;
    const t = document.getElementById(h.slice(1));
    if (t) t.scrollIntoView({ block: "start", behavior: "instant" });
  }
  // 헤더 경보 목록: 서비스가 대상인 경보는 그 서비스 상세로 바로
  if (window.JarvisShell) JarvisShell.alertLink = (a) => { const sv = findSvc(a.target); return sv ? { href: "#svc=" + encodeURIComponent(sv.id), label: sv.name + " 상세 보기" } : null; };

  P.subscribe((snap, state) => {
    S = snap;
    ST = state || { stale: false };
    if (!ST.stale) lastOk = ST.fetchedAt || nowS();
    $("#bootError").hidden = true;
    docEl.classList.add("has-data");
    renderAll();
    if (!firstDone) {
      firstDone = true;
      const id = pendingSvc || svcFromHash();
      pendingSvc = null;
      if (id) openSheet(id, true);
      requestAnimationFrame(() => requestAnimationFrame(reapplyAnchor));
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => requestAnimationFrame(reapplyAnchor)).catch(() => {});
    }
  }, {
    onError: (e) => {
      $("#bootError").hidden = false;
      txt("boot.msg", "잠시 후 자동으로 다시 시도해요. (" + ((e && e.message) || e) + ")");
    },
  });
  setInterval(() => { if (!document.hidden) updateLive(); }, 1000);
})();
