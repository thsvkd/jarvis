/* Jarvis — 네트워크 맵(/map/). 옛 시안 이름은 Atlas.
 * 두 관점: 개념(서비스 노출 경로 — "무엇이 어떤 경로로 공개되는가", snap.topology) ·
 *          물리(기기·네트워크 — "어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가", snap.topology_physical, /app/map-physical.js).
 *   주소 /map/?view=physical (없으면 개념) + 브라우저에 기억. 확대·이동·선택·검사기·목록·주소(#focus)는 두 관점이 같은 틀을 쓰고,
 *   관점을 바꿔도 같은 서비스가 있으면 선택이 따라간다(#focus=<서비스 id> 는 물리에서 그 서비스를 돌리는 컨테이너·프로세스로).
 * 토폴로지가 곧 화면이다. 인터넷·Tailnet·LAN 에서 각 서비스까지 이어지는 길을 층(layer)별로
 * 왼쪽→오른쪽에 결정적으로 배치한 SVG 지도로 그린다. 노드를 고르면 검사기(데스크톱 오른쪽 판, 모바일 아래 시트)에
 * 서비스의 모든 필드와 "어떻게 닿는가" 단계 설명이 열린다. 왼쪽 판은 공개 상태 요약 + 목록(서비스·노출·경보),
 * 아래는 하드웨어 계기. 하드웨어·런타임·트래픽의 전체 상세는 개요(/)로 링크한다. 모바일(<700px)은 세로 노선도.
 *
 * 주소: /map/#focus=<서비스 id(URL 인코딩)> → 그 노드를 골라 가운데로 확대하고 검사기를 연다(hashchange 도 따름).
 *       노드를 고르면 주소가 #focus=… 로 바뀐다(replaceState — 방문 기록은 늘지 않는다).
 *       검사기의 "개요에서 자세히" → /#svc=<서비스 id>.
 *
 * 안전: 데이터로 HTML 을 만들 때는 JARVIS.html(자동 이스케이프)만 쓴다. URL 은 JARVIS.safeUrl 로만, 외부 링크는 새 탭 + noopener.
 * 갱신: 그래프 구조는 토폴로지 서명이 바뀔 때만 다시 만들고, 3초마다는 속성·글자만 제자리에서 바꾼다.
 *       선택·확대/이동·레이어·목록 탭·검색어·스크롤은 갱신돼도 유지된다. 공용 헤더(/app/shell.js)가 연결 상태를 알려 준다.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  if (!P) {
    const p = document.createElement("p");
    p.className = "fatal";
    p.textContent = "공용 스크립트(/shared/jarvis.js)를 불러오지 못했습니다.";
    document.body.appendChild(p);
    return;
  }
  const { html, raw, fmt, labels } = P;
  // 공용 제품 도구(/app/shell.js): 같은 토폴로지 모형·공개 상태 정의·프로브 판정·이름·제자리 갱신을 개요와 함께 쓴다.
  const SH = window.JarvisShell || {};
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));

  // ------------------------------------------------------------------ 기본 요소
  const app = $("#app");
  const graph = $("#graph");
  const vp = $("#vp");
  const stage = $("#map");
  const flowEl = $("#flow");
  const side = $("#side");
  const ssBody = $("#ss-body");
  const sideTabs = $("#side-tabs");
  const sidePanel = $("#side-panel");
  const sideTools = $("#side-tools");
  const sideBody = $("#side-body");
  const sideToggle = $("#side-toggle");
  const inspector = $("#inspector");
  const inspBody = $("#insp-body");
  const inspActions = $("#insp-actions");
  const inspH = $("#insp-h");
  const toolbar = $("#toolbar");
  const caption = $("#caption");
  const legend = $("#legend");
  const legendBtn = $("#legend-btn");
  const ribbon = $("#stale-ribbon");
  const toastEl = $("#toast");
  const empty = $("#stage-empty");
  const backdrop = $("#insp-backdrop");
  const modesEl = $("#modes");
  const legendH = $("#legend-h");
  // 물리 보기 모듈(/app/map-physical.js). 없으면 개념 보기만.
  const PHYS = window.JarvisMapPhys || null;

  const EMBED = !!P.embed;
  const reduceMq = matchMedia("(prefers-reduced-motion: reduce)");
  // 세로 노선도: 휴대폰 폭, 그리고 세로로 긴 태블릿(폭 1100 미만 · 높이 ≥ 폭×1.1) — 가로 지도를 폭에 맞추면 글자가 너무 작아진다.
  // atlas.css 의 같은 미디어 쿼리와 짝이다(둘 중 하나만 바꾸지 말 것).
  const FLOW_MQ = "(max-width: 699px), (max-width: 1099px) and (max-aspect-ratio: 10/11)";
  const mobileMq = matchMedia(FLOW_MQ);
  let mobile = mobileMq.matches;
  app.dataset.mode = mobile ? "flow" : "map";

  const nowS = () => Date.now() / 1000;
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const mean = (a) => (a.length ? a.reduce((s, v) => s + v, 0) / a.length : null);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const tone = (s) => P.tone(s);
  const cssEsc = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/["\\]/g, "\\$&"));
  const calm = () => EMBED || reduceMq.matches;

  // 보는 사람 브라우저에만 남는 편의 설정(레이어·목록 열림·탭). 막혀 있어도 화면은 그대로 동작한다.
  const store = {
    get(k, d) { try { const v = P.pref("map." + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("jarvis.map." + k, JSON.stringify(v)); } catch (e) { /* 저장소 없음: 무시 */ } },
  };

  // ------------------------------------------------------------------ 화면 상태(갱신돼도 유지)
  const TABS = ["services", "exposure", "alerts"];
  const TAB_LABEL = { services: "서비스 목록", exposure: "노출 — 어디까지 열려 있나", alerts: "경보" };
  const savedLayers = store.get("layers", {});
  const ui = {
    side: !EMBED && store.get("side", false) === true,
    tab: TABS.includes(store.get("tab", "")) ? store.get("tab", "") : "services",
    selected: null,
    hover: null,
    layers: { public: true, tailnet: true, lan: true, depends: true },
    legend: false,
    q: "",
    problems: false,
    // 서비스가 많은 카테고리(12개 초과)는 척추에 10개(문제 있는 것 먼저 포함)만 두고 "+N 더 보기" — 펼친 카테고리를 기억한다
    expanded: (() => { const v = store.get("expanded", {}); return v && typeof v === "object" ? v : {}; })(),
    // 관점: 주소(?view=) > 기억 > 개념
    view: "concept",
    offOpen: store.get("offOpen", false) === true,   // 물리: 오프라인 기기 묶음 펼침
    tree: null,                                        // 물리(모바일): 펼친 나무 칸(Set) — 첫 스냅샷에 기본값
  };
  // 관점 이름 규약은 공용(JarvisShell.mapView): ?view=concept|conceptual|physical|phys. 주소로 고른 관점은 기억한다(개요 타일과 같은 값).
  const MVh = SH.mapView || {
    norm: (v) => { const x = String(v == null ? "" : v).replace(/"/g, "").toLowerCase(); return x === "physical" || x === "phys" ? "physical" : x === "concept" || x === "conceptual" ? "concept" : null; },
    get: (d) => (store.get("view", d || "concept") === "physical" ? "physical" : "concept"),
    set: (v) => store.set("view", v),
  };
  const viewFromUrl = () => { try { return MVh.norm(new URLSearchParams(location.search).get("view")); } catch (e) { return null; } };
  {
    const qv = viewFromUrl();
    ui.view = PHYS ? qv || MVh.get("concept") : "concept";
    if (qv && PHYS) MVh.set(qv);
  }
  const isPhys = () => ui.view === "physical" && !!PHYS;
  if (savedLayers && typeof savedLayers === "object") for (const k in ui.layers) if (savedLayers[k] === false) ui.layers[k] = false;
  const tabNow = () => (mobile && ui.tab === "services" ? "exposure" : ui.tab);

  let S = null; // 마지막 스냅샷
  let D = null; // 파생 데이터(개념 — 목록·검사기의 서비스 길은 관점과 상관없이 이것을 쓴다)
  let PD = null; // 파생 데이터(물리)
  const G = { sig: null, layout: null, nodeEls: new Map(), metaEls: new Map(), edgeEls: new Map(), labelEls: [], hostIp: null };
  const view = { k: 1, tx: 0, ty: 0, auto: true, fitAll: false, floored: false };
  // 기본 맞춤의 최저 배율: 이보다 작으면 노드 글자(12.5px)가 10px 아래로 떨어져 읽기 어렵다 → 왼쪽 위(영역·관문·척추 윗부분)를 이 배율로 담고 나머지는 끌어서 본다.
  const FLOOR = 0.8;
  // 물리 보기는 글자를 한 단계 크게 그린다(칩 이름 13px · 보조 줄 11.5px). 지도 전체가 이 배율(이름 ≥ 11.4px · 보조 ≥ 10px)로
  // 안 들어가면 이 기기 상자(+ 위의 인터넷·LAN)를 읽기 좋은 크기로 담고 나머지는 가장자리로(→/↓ 안내 칩) — fitView 참고
  const PHYS_FLOOR = 0.88;
  const floorK = () => (isPhys() ? PHYS_FLOOR : FLOOR);
  let lastInvoker = null;
  let pendingFocus = null;
  let liveState = "wait";
  let animTimer = 0;
  /** 사용자가 판을 여닫을 때만 움직임을 켠다(창 크기 변화 등에는 바로 바뀐다). */
  function panelAnim() {
    if (calm() || mobile) return;
    app.classList.add("panel-anim");
    clearTimeout(animTimer);
    animTimer = setTimeout(() => app.classList.remove("panel-anim"), 480);
  }

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
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    home: '<rect x="3.5" y="3.5" width="7.5" height="7.5" rx="2"/><rect x="13" y="3.5" width="7.5" height="4.5" rx="1.6"/><rect x="3.5" y="13" width="7.5" height="7.5" rx="2"/><rect x="13" y="10" width="7.5" height="10.5" rx="2"/>',
    search: '<circle cx="11" cy="11" r="6.5"/><path d="m20 20-4.2-4.2"/>',
    route: '<circle cx="5.5" cy="6" r="2.5"/><circle cx="18.5" cy="18" r="2.5"/><path d="M8 6h7.5a3 3 0 0 1 0 6h-7a3 3 0 0 0 0 6H16"/>',
    okc: '<circle cx="12" cy="12" r="9"/><path d="m8 12.3 2.8 2.8L16.2 9"/>',
    shield: '<path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6l8-3z"/>',
    cpu: '<rect x="6" y="6" width="12" height="12" rx="2.2"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/><path d="M9 2.5v3M15 2.5v3M9 18.5v3M15 18.5v3M2.5 9h3M2.5 15h3M18.5 9h3M18.5 15h3"/>',
    gpu: '<rect x="2.5" y="6" width="19" height="12" rx="2.4"/><circle cx="9" cy="12" r="3"/><path d="M15.5 10h3M15.5 14h3M6 18v2.5M18 18v2.5"/>',
    mem: '<rect x="2.5" y="6.5" width="19" height="9" rx="1.8"/><path d="M6.5 9.5v3M10 9.5v3M14 9.5v3M17.5 9.5v3M5 15.5v3M9 15.5v3M15 15.5v3M19 15.5v3"/>',
    temp: '<path d="M14 14.5V5a2 2 0 1 0-4 0v9.5a4 4 0 1 0 4 0z"/><path d="M12 9.5v7"/>',
    bolt: '<path d="M13 2.5 5 13.5h6l-1 8 8-11h-6z"/>',
    net: '<path d="M8 3.5v17M8 20.5l-4-4M8 20.5l4-4M16 20.5v-17M16 3.5l-4 4M16 3.5l4 4"/>',
    disk: '<rect x="3" y="4" width="18" height="16" rx="2.6"/><path d="M3 13.5h18"/><path d="M16.5 16.8h.01M13.5 16.8h.01"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  };
  const ic = (n, cls) => raw('<svg class="ic' + (cls ? " " + cls : "") + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' + (ICON[n] || "") + "</svg>");
  const CAT_ICON = { public: "globe", tailnet: "tailnet", hermes: "route", host: "server", internal: "db", infra: "layers", system: "gear" };

  // ------------------------------------------------------------------ 작은 조각들
  const statusText = (st) => labels.status[st] || "확인 불가";
  const shape = (t) => html`<i class="shape" data-tone="${t}" aria-hidden="true"></i>`;
  const pill = (st, text) => { const t = tone(st); return html`<span class="pill" data-tone="${t}">${shape(t)}${text || statusText(st)}</span>`; };
  const levelPill = (lv) => { const t = tone(lv); return html`<span class="pill" data-tone="${t}">${shape(t)}${labels.level[lv] || lv}</span>`; };
  const SOURCE = { system: "시스템", container: "컨테이너", systemd: "systemd", route: "라우트", exposure: "노출", dns: "DNS", cert: "인증서", tunnel: "터널", caddy: "Caddy", traffic: "트래픽", tailscale: "Tailscale", hermes: "Hermes", monitor: "모니터" };
  const DNS_L = { cloudflare: "Cloudflare", tailnet: "Tailnet IP", none: "레코드 없음", other: "기타", error: "조회 실패" };
  const PROBE_L = { internal: ["Caddy :443", "tailnet 사용자 경로"], tunnel: ["Caddy :80", "터널 경로"], public: ["공개 DNS → Cloudflare 엣지", "인터넷 사용자 경로"] };
  const OWNER_L = { container: "컨테이너", unit: "systemd", "user-unit": "사용자 유닛", scope: "앱·세션", unknown: "?" };
  const AUD = { public: ["공개", "globe"], tailnet: ["Tailnet", "tailnet"], lan: ["LAN", "lan"], local: ["로컬", "lock"] };

  /** 노출 배지(개요와 같은 모양) */
  function xbadges(ex, withVia) {
    if (!ex) return "";
    const b = [];
    for (const k of ["public", "tailnet", "lan"]) if (ex[k]) b.push(html`<span class="xb" data-k="${k}">${ic(AUD[k][1])}${AUD[k][0]}</span>`);
    if (ex.local_only || !b.length) b.push(html`<span class="xb" data-k="local">${ic("lock")}로컬</span>`);
    const via = withVia && (ex.via || []).length ? html`<span class="xvia">경유 ${ex.via.map((v) => labels.via[v] || v).join(" · ")}</span>` : "";
    return html`<span class="xbs">${b}${via}</span>`;
  }
  const shortErr = (e) => (SH.shortErr ? SH.shortErr(e) : String(e || "").slice(0, 60));
  /** 키-값 줄 목록(개요의 상세 시트와 같은 모양) */
  const rows = (list) => html`<dl class="rows">${list.filter(Boolean).map(([k, v]) => html`<div class="row"><dt>${k}</dt><dd>${v == null || v === "" ? "–" : v}</dd></div>`)}</dl>`;
  const ext = (url, text, cls) => {
    const u = P.safeUrl(url);
    if (u === "#") return html`<span class="${(cls || "ext") + " is-invalid"}" title="안전하지 않은 주소라 링크를 걸지 않았어요"><span>${text || url}</span></span>`;
    return html`<a class="${cls || "ext"}" href="${u}" target="_blank" rel="noopener noreferrer" data-fk="${"a:" + url}"><span>${text || url}</span>${ic("ext")}</a>`;
  };
  const homeLink = (href, text, fk) => html`<a class="home-link" href="${href}" data-fk="${fk || "h:" + href}">${text}${ic("arrow")}</a>`;
  const ago = (ts) => fmt.ago(ts, S && S.meta && S.meta.generated_at);
  const codeTone = (c) => (c == null ? "muted" : c >= 500 ? "crit" : c >= 400 ? "warn" : "ok");
  const shortHost = (h) => { const d = (S && S.meta && S.meta.domain) || ""; const s = String(h || ""); return d && s.endsWith("." + d) ? s.slice(0, -d.length - 1) : s; };
  const when = (ts) => (!ts ? "–" : nowS() - ts < 86400 ? fmt.time(ts, false) : fmt.datetime(ts));
  const bareUrl = (u) => String(u || "").replace(/^https?:\/\//i, "").replace(/\/$/, "");

  /** 다시 그려도 포커스·입력 커서·내부 스크롤을 잃지 않는 render. */
  function renderKeep(el, content) {
    if (!el) return false;
    const str = content == null ? "" : String(content);
    if (el.__jarvisHtml === str) return false;
    const act = document.activeElement;
    const fk = act && act !== el && el.contains(act) ? act.getAttribute("data-fk") : null;
    const selRange = fk && act.tagName === "INPUT" ? [act.selectionStart, act.selectionEnd] : null;
    const scrolls = [];
    el.querySelectorAll("[data-sk]").forEach((n) => { if (n.scrollTop || n.scrollLeft) scrolls.push([n.getAttribute("data-sk"), n.scrollTop, n.scrollLeft]); });
    // 제자리 갱신: 구조가 같으면 바뀐 숫자·글자만 고친다(선택한 글자·누르던 버튼·hover 가 3초마다 사라지지 않게)
    if (SH.morph) { if (!SH.morph(el, content)) return false; } else P.render(el, content);
    for (const [k, t, l] of scrolls) {
      const n = el.querySelector(`[data-sk="${cssEsc(k)}"]`);
      if (n) { n.scrollTop = t; n.scrollLeft = l; }
    }
    if (fk && !el.contains(document.activeElement)) {
      const n = el.querySelector(`[data-fk="${cssEsc(fk)}"]`);
      if (n) {
        n.focus({ preventScroll: true });
        if (selRange && n.setSelectionRange) try { n.setSelectionRange(selRange[0], selRange[1]); } catch (e) { /* 무시 */ }
      }
    }
    return true;
  }
  const setText = (el, s) => { if (el && el.textContent !== s) el.textContent = s; };
  const setAttr = (el, k, v) => { if (el && el.getAttribute(k) !== v) el.setAttribute(k, v); };

  let toastT = 0;
  function toast(msg) {
    setText(toastEl, msg);
    toastEl.hidden = !msg;
    clearTimeout(toastT);
    if (msg) toastT = setTimeout(() => { toastEl.hidden = true; }, 5200);
  }

  // ------------------------------------------------------------------ 파생 데이터
  const ZONE_NAME = { "zone:internet": "인터넷", "zone:tailnet": "Tailnet", "zone:lan": "LAN" };
  const SERVICE_LIKE = SH.SERVICE_LIKE || new Set(["service", "host", "internal", "extra"]);
  const nodeName = (id) => { const n = D && D.nodes.get(id); return SH.nodeName ? SH.nodeName(n || id) : ZONE_NAME[id] || (n ? n.label : id); };

  /** 공개 상태: 가장 넓게 열린 범위. 공개 > LAN > Tailnet 전용 > 로컬. */
  //   이름·설명·세는 법은 개요와 한 벌(JarvisShell.LEVELS · expLevel). 여기서는 지도용 아이콘·영역·길 종류만 더한다.
  const LV_MAP = {
    public: { icon: "globe", zone: "zone:internet", kinds: ["public"] },
    lan: { icon: "lan", zone: "zone:lan", kinds: ["lan"] },
    tailnet: { icon: "tailnet", zone: "zone:tailnet", kinds: ["tailnet"] },
    local: { icon: "lock", kinds: [] },
  };
  const LEVELS = (SH.LEVELS || [
    { id: "public", label: "공개", long: "공개", sub: "인터넷 누구나" }, { id: "lan", label: "LAN", long: "LAN", sub: "같은 공유기 안 누구나" },
    { id: "tailnet", label: "Tailnet", long: "Tailnet 전용", sub: "내 tailnet 기기만" }, { id: "local", label: "로컬", long: "로컬 전용", sub: "이 기기 안에서만" },
  ]).map((l) => Object.assign({}, l, LV_MAP[l.id]));
  const LEVEL = Object.fromEntries(LEVELS.map((l) => [l.id, l]));
  function expLevel(sv) {
    if (SH.expLevel) return SH.expLevel(sv);
    if (!sv) return "local";
    const e = sv.exposure || {};
    return e.public ? "public" : e.lan ? "lan" : e.tailnet ? "tailnet" : "local";
  }

  function derive(s) {
    // 토폴로지 모형은 공용(JarvisShell.graphOf): 길 없는 서비스('extra') · LAN 직접(합성) 엣지까지 포함
    const g = SH.graphOf(s);
    const catLabel = {};
    for (const c of s.categories || []) catLabel[c.id] = c.label;
    const d = Object.assign({}, g, { catLabel });
    // 경보 → 지도 노드
    d.alertNode = new Map();
    for (const a of s.alerts || []) { const nid = alertTarget(d, a); if (nid) d.alertNode.set(a.key || a.message, nid); }
    return d;
  }
  const catName = (c) => (D && D.catLabel[c]) || labels.category[c] || c || "기타";
  const kindName = (k) => (SH.kindLabel ? SH.kindLabel(k) : labels.kind[k] || k || "");
  const isStatic = (sv) => !!sv && sv.kind === "static";

  // ------------------------------------------------------------------ 두 관점 사이의 다리
  // 개념의 출발 영역·Cloudflare 엣지 ↔ 물리의 외부·구간. 그 밖은 서비스 id 로 잇는다(물리 노드의 service_ids).
  function aliasP(id) {
    if (!PD || !id) return null;
    if (id === "zone:lan") return PD.segLan ? PD.segLan.id : null;
    if (id === "zone:tailnet") return PD.segTn ? PD.segTn.id : null;
    if (id === "zone:internet" || /^edge:/.test(id)) {
      const re = id === "zone:internet" ? /internet/i : /cloudflare/i;
      for (const n of PD.nodes.values()) if (n.type === "external" && re.test(n.id)) return n.id;
    }
    return null;
  }
  function aliasC(id) {
    const n = PD && PD.nodes.get(id);
    if (!n || !D) return null;
    if (n === PD.segLan) return D.nodes.has("zone:lan") ? "zone:lan" : null;
    if (n === PD.segTn) return D.nodes.has("zone:tailnet") ? "zone:tailnet" : null;
    if (n.type === "external") {
      if (/internet/i.test(id)) return D.nodes.has("zone:internet") ? "zone:internet" : null;
      for (const c of D.nodes.values()) if (c.type === "edge") return c.id;
    }
    return null;
  }
  /** 개념 노드 id · 서비스 id · 물리 id → 물리 노드 id (없으면 null) */
  function toPhys(id) {
    if (!PD || !id) return null;
    if (PD.nodes.has(id)) return id;
    const a = aliasP(id);
    if (a) return a;
    const cn = D && D.nodes.get(id);
    const sid = cn && cn.service_id ? cn.service_id : id;
    const l = PD.bySvc.get(sid);
    if (l && l.length) return l[0];
    // 정적 라우트(Caddy 가 직접 응답 — 뒤에 앱이 없다)는 그 응답을 만드는 Caddy 로
    const sv = S && (S.services || []).find((x) => x.id === sid);
    if (isStatic(sv)) { for (const n of PD.nodes.values()) if ((n.svcs || []).some((x) => x.kind === "proxy")) return n.id; }
    return null;
  }
  /** 물리 노드 id · 서비스 id → 개념 노드 id (없으면 null) */
  function toConcept(id) {
    if (!D || !id) return null;
    const a = aliasC(id);
    if (a) return a;
    const n = PD && PD.nodes.get(id);
    if (n) { for (const sid of n.service_ids || []) { const c = D.svcNode.get(sid); if (c) return c; } return D.nodes.has(id) ? id : null; }
    if (D.nodes.has(id)) return id;
    return D.svcNode.get(id) || null;
  }
  /** 목록·검사기·경보가 쓰는 id(개념 노드 id 나 서비스 id) → 지금 관점의 노드 */
  const resolveAny = (id) => (isPhys() ? toPhys(id) : D && D.nodes.has(id) ? id : toConcept(id));
  const curHas = (id) => !!id && (isPhys() ? !!(PD && PD.nodes.has(id)) : !!(D && D.nodes.has(id)));
  const curName = (id) => (isPhys() && PD && PD.nodes.has(id) ? (PD.nodes.get(id).type === "group" ? PHYS.groupTitle(PD, PD.nodes.get(id)) : PHYS.name(PD, id)) : nodeName(id));
  /** 지금 고른 것이 돌리는 서비스 id 들(목록의 '고름' 표시용) */
  function selSvcs() {
    const out = new Set(); const id = ui.selected;
    if (!id) return out;
    if (isPhys()) { const n = PD && PD.nodes.get(id); if (n) (n.service_ids || []).forEach((x) => out.add(x)); }
    else { const n = D && D.nodes.get(id); if (n && n.service_id) out.add(n.service_id); }
    return out;
  }
  /** 개념 id(관문 카드 등)가 지금 고른 것인가 */
  const isSelC = (cid) => !!cid && !!ui.selected && (isPhys() ? toPhys(cid) === ui.selected : cid === ui.selected);

  /** 경보의 target(서비스 id·호스트명·유닛 …)을 지도 노드로. 못 찾으면 null. */
  function alertTarget(d, a) {
    const t = a && a.target ? String(a.target) : "";
    const byKey = (k) => (d.nodes.has(k) ? k : d.svcNode.get(k) || null);
    if (t) {
      for (const k of [t, "route:" + t, "host:" + t, "ctr:" + t, "infra:" + t]) { const r = byKey(k); if (r) return r; }
      for (const sv of d.s.services || []) {
        if (sv.name === t || (sv.hostnames || []).includes(t) || (sv.runtime && sv.runtime.name === t)) { const r = d.svcNode.get(sv.id); if (r) return r; }
      }
    }
    const SRC_NODE = { tunnel: "infra:cloudflared", caddy: "infra:caddy", traffic: "infra:caddy", tailscale: "zone:tailnet" };
    if (a.source === "exposure" && /lan/.test(t)) return d.nodes.has("zone:lan") ? "zone:lan" : null;
    const f = SRC_NODE[a.source];
    return f && d.nodes.has(f) ? f : null;
  }
  /** 물리 지도에서 경보 대상: 컨테이너·프로세스·기기 이름으로, 시스템 경보는 이 기기로 */
  function physAlertNode(a) {
    if (!PD || !a) return null;
    const t = String(a.target || "");
    if (t) for (const k of ["ctr:" + t, "proc:" + t, "peer:" + t, "iface:" + t, "net:" + t]) if (PD.nodes.has(k)) return k;
    if (a.source === "tailscale" && PD.segTn) return PD.segTn.id;
    // 그 밖(systemd 유닛·시스템·모니터·DNS·인증서 …)은 이 기기의 일 — 기기 상태("주의")의 까닭이므로 기기로
    return PD.self ? PD.self.id : null;
  }
  /** 경보 → 지금 관점의 지도 노드(없으면 null) */
  function alertNodeNow(a) {
    const nid = D && D.alertNode.get(a.key || a.message);
    if (!isPhys()) return nid || null;
    return (nid && toPhys(nid)) || physAlertNode(a);
  }
  /** 지도에 없는 경보 대상은 개요의 알맞은 섹션으로 */
  const alertHome = (a) => ({ system: "/#hardware", container: "/#runtime", systemd: "/#runtime", tailscale: "/#network", traffic: "/#network", exposure: "/#network", tunnel: "/#network", caddy: "/#network" }[a.source] || "/#alerts");

  const kindsOf = (id) => SH.kindsOf(D, id);
  const passes = (allowed, kind) => !allowed || allowed.has(kind);

  /** 노드를 가리켰을 때 밝힐 노드·엣지 집합 — "이 서비스는 어떻게 닿는가". only 가 있으면 그 종류의 길만.
   *  합성 LAN 엣지(terminal)는 그 노드 자체의 포트다 — 시작 노드일 때만 따라 올라가고, 그 너머로 내려가지 않는다.
   *  관문(Caddy 등)에서 시작해 내려갈 때는 길 종류가 맞아야 한다(LAN 칸을 가리켰는데 Caddy → Tailnet 앱이 밝아지지 않게). */
  function pathSet(id, only) {
    const N = new Set([id]); const E = new Set();
    const up = (nid, allowed, depth) => {
      if (depth > 14) return;
      for (const e of D.inE.get(nid) || []) {
        if (e.terminal && nid !== id) continue;
        let next = allowed;
        if (e.kind === "depends") next = only || kindsOf(e.from);
        else if (!passes(allowed, e.kind)) continue;
        if (E.has(e.key)) continue;
        E.add(e.key); N.add(e.from);
        up(e.from, next, depth + 1);
      }
    };
    const down = (nid, allowed, depth, strict) => {
      if (depth > 14) return;
      for (const e of D.outE.get(nid) || []) {
        if (E.has(e.key)) continue;
        const t = D.nodes.get(e.to);
        if (e.kind !== "depends") {
          if (strict && !passes(allowed, e.kind)) continue;
          if (t && SERVICE_LIKE.has(t.type)) {
            const tk = kindsOf(e.to);
            if (allowed && tk && ![...tk].some((k) => allowed.has(k))) continue;
          } else if (!passes(allowed, e.kind)) continue;
        }
        E.add(e.key); N.add(e.to);
        if (!e.terminal) down(e.to, allowed, depth + 1, false);
      }
    };
    const n = D.nodes.get(id);
    const k = only || kindsOf(id);
    up(id, k, 0);
    down(id, n && SERVICE_LIKE.has(n.type) ? new Set() : k, 0, !!(n && !SERVICE_LIKE.has(n.type) && n.type !== "zone"));
    return { N, E };
  }
  /** 공개 상태 한 칸(공개·LAN·…)에 속한 노드와 그 길 */
  function levelSet(lv) {
    const L = LEVEL[lv]; const N = new Set(); const E = new Set();
    if (!L || !D) return { N, E };
    const only = L.kinds.length ? new Set(L.kinds) : new Set();
    for (const sv of S.services || []) {
      if (expLevel(sv) !== lv) continue;
      const nid = D.svcNode.get(sv.id);
      if (!nid) continue;
      const ps = pathSet(nid, only);
      ps.N.forEach((x) => N.add(x)); ps.E.forEach((x) => E.add(x));
    }
    return { N, E };
  }

  const chains = (id, max) => SH.chains(D, id, max);
  const edgeLabel = (e) => SH.edgeLabel(D, e);
  function chainHtml(c) {
    return html`<span class="chain">${c.nodes.map((nid, i) => {
      const e = i > 0 ? c.edges[i - 1] : null;
      const lb = edgeLabel(e);
      return html`${e ? html`<span class="hop-e" data-k="${e.kind}"><svg viewBox="0 0 14 8" aria-hidden="true"><path d="M0 4h12M9 1l3 3-3 3"/></svg>${lb ? html`<i>${lb}</i>` : ""}</span>` : ""}<span class="hop">${nodeName(nid)}</span>`;
    })}</span>`;
  }

  // ------------------------------------------------------------------ 레이아웃 (결정적)
  const COLW = [164, 176, 156, 170, 256, 206];
  const GAP = [60, 54, 56, 92, 74];
  const COLX = [];
  { let x = 0; for (let i = 0; i < 6; i++) { COLX[i] = x; x += COLW[i] + (GAP[i] || 0); } }
  // 척추(서비스 열)는 촘촘하게 — 기본 맞춤에서 글자가 읽히는 배율이 나오도록(행 27px, 머리 24px)
  const H1 = 23, PITCH = 27, H2 = 46, GH = 24, GG = 18, RP = 10;
  const BUS = { public: 7, tailnet: 7, lan: 14, depends: 7 };   // 척추 왼쪽 버스선의 자리(영역 테두리에서 떨어진 거리)

  const SPINE_LIMIT = 12, SPINE_SHOW = 10;
  function sigOf(d) {
    const ns = [...d.nodes.values()].map((n) => [n.id, n.type, n.layer, n.category || "", n.label].join("|")).sort().join(";");
    const es = d.edges.map((e) => e.key + "|" + (e.label || "")).sort().join(";");
    // 접힌 큰 카테고리는 무엇을 보이는지(문제 있는 노드)가 배치에 들어간다
    const big = {};
    for (const n of d.nodes.values()) if (n.layer === 4 && n.type !== "extra") (big[n.category] = big[n.category] || []).push(n);
    const vis = Object.entries(big).filter(([c, l]) => l.length > SPINE_LIMIT && !ui.expanded[c])
      .map(([c, l]) => c + ":" + l.filter((n) => n.status && n.status !== "up").map((n) => n.id).sort().join(",")).join(";");
    return ns + "#" + es + "#" + Object.keys(ui.expanded).filter((k) => ui.expanded[k]).sort().join(",") + "#" + vis;
  }
  /** 접힌 카테고리 안에 숨은 노드를 고르면(목록·#focus) 그 카테고리를 편다 */
  function ensureVisible(id) {
    if (isPhys()) {
      // 물리: 접힌 오프라인 묶음 안의 기기를 고르면 묶음을 편다
      if (!G.layout || G.layout.pos.has(id) || !PD) return false;
      const pn = PD.nodes.get(id);
      if (pn && pn.parent === "grp:offline" && !ui.offOpen) { ui.offOpen = true; store.set("offOpen", true); G.sig = null; return true; }
      return false;
    }
    if (!G.layout || G.layout.pos.has(id) || !D) return false;
    const n = D.nodes.get(id);
    if (!n || !n.category || ui.expanded[n.category]) return false;
    ui.expanded[n.category] = true;
    store.set("expanded", ui.expanded);
    G.sig = null;
    return true;
  }
  function setExpanded(cat, on) {
    if (isPhys() && cat === "offline") {
      ui.offOpen = !!on;
      store.set("offOpen", ui.offOpen);
      G.sig = null;
      renderAll();
      requestAnimationFrame(() => { const b = vp.querySelector('[data-more="offline"]'); if (b) b.focus({ preventScroll: true }); if (on) revealRect(G.layout && G.layout.pos.get("grp:offline")); });
      return;
    }
    if (on) ui.expanded[cat] = true; else delete ui.expanded[cat];
    store.set("expanded", ui.expanded);
    G.sig = null;
    renderAll();
    requestAnimationFrame(() => { const b = vp.querySelector(`[data-more="${cssEsc(cat)}"]`); if (b) b.focus({ preventScroll: true }); });
  }
  function layout(d) {
    return layoutPass(d);
  }
  /** 길이 있는(합성 LAN 포함) '경로 밖' 서비스 → 3열 "포트 직접"(SSH 옆), 없으면 5열 "경로 밖" */
  const directExtra = (d, n) => n.type === "extra" && (d.inE.get(n.id) || []).some((e) => e.kind !== "depends");
  function layoutPass(d) {
    const pos = new Map(); const regions = [];
    const spineReg = new Map();
    const nodes = [...d.nodes.values()];
    const colOf = (n) => (n.type === "extra" ? (directExtra(d, n) ? 3 : 5) : clamp(n.layer | 0, 0, 5));
    const cats = (d.s.categories || []).map((c) => c.id);
    const catRank = (c) => { const i = cats.indexOf(c); return i < 0 ? 99 : i; };
    const byName = (a, b) => catRank(a.category) - catRank(b.category) || String(a.label).localeCompare(String(b.label));
    const place = (n, col, y, h) => pos.set(n.id, { x: COLX[col], y, w: COLW[col], h, col });
    const cy = (id) => { const p = pos.get(id); return p ? p.y + p.h / 2 : null; };
    function resolve(items, col, gap) {
      gap = gap == null ? 16 : gap;
      items.sort((a, b) => (a.want - b.want) || String(a.n.label).localeCompare(String(b.n.label)));
      let bottom = -Infinity;
      for (const it of items) {
        let top = isFinite(it.want) ? it.want - it.h / 2 : bottom + gap;
        if (!isFinite(top)) top = 0;
        if (top < bottom + gap) top = bottom + gap;
        place(it.n, col, top, it.h);
        bottom = top + it.h;
      }
      return bottom;
    }
    // 1) 서비스 척추(4열) — 카테고리 순, 이름 순. 상태로 정렬하지 않는다(상태가 바뀌어도 자리가 그대로).
    const spine = nodes.filter((n) => colOf(n) === 4).sort(byName);
    const center = {}; const regOf = {};
    let y = 0;
    const groupCats = [...new Set(spine.map((n) => n.category || "other"))].sort((a, b) => catRank(a) - catRank(b));
    const moreRows = [];
    for (const cat of groupCats) {
      const all = spine.filter((n) => (n.category || "other") === cat);
      let mem = all; let hidden = 0;
      if (all.length > SPINE_LIMIT && !ui.expanded[cat]) {
        const keep = new Set(all.filter((n) => n.status && n.status !== "up").map((n) => n.id));
        for (const n of all) { if (keep.size >= SPINE_SHOW) break; keep.add(n.id); }
        mem = all.filter((n) => keep.has(n.id));
        hidden = all.length - mem.length;
      }
      const gy = y; y += GH;
      const cs = [];
      for (const n of mem) { place(n, 4, y, H1); cs.push(y + H1 / 2); y += PITCH; }
      if (all.length > SPINE_LIMIT) { moreRows.push({ cat, x: COLX[4], y, w: COLW[4], h: H1, hidden, total: all.length }); y += PITCH; }
      const gh = y - PITCH + H1 + RP - gy;
      const reg = { cat, x: COLX[4] - RP, y: gy, w: COLW[4] + RP * 2, h: gh, n: all.length, first: cs[0], last: cs[cs.length - 1] };
      regions.push(reg); regOf[cat] = reg;
      for (const n of mem) spineReg.set(n.id, reg);
      center[cat] = mean(cs);
      y = gy + gh + GG;
    }
    const spineBottom = Math.max(0, y - GG);
    const A = { public: center.public != null ? center.public : 23 };
    A.tailnet = center.tailnet != null ? center.tailnet : A.public + 170;
    A.lan = center.host != null ? center.host : Math.max(A.tailnet + 150, spineBottom + 30);
    // 2) 영역(0열): 각자의 '고속도로' 높이에
    const zoneWant = { "zone:internet": A.public, "zone:tailnet": A.tailnet, "zone:lan": A.lan };
    const succMean = (n) => mean((d.outE.get(n.id) || []).map((e) => cy(e.to)).filter((v) => v != null));
    resolve(nodes.filter((n) => colOf(n) === 0).map((n) => ({ n, want: zoneWant[n.id] != null ? zoneWant[n.id] : (succMean(n) != null ? succMean(n) : A.lan + 90), h: H2 })), 0, 18);
    // 3) 1~3열: 서비스로 이어지면 그 무게중심, 아니면 앞 노드의 높이
    let col3Bottom = -Infinity;
    for (const L of [1, 2, 3]) {
      const items = [];
      for (const n of nodes) {
        if (colOf(n) !== L || n.type === "extra") continue;
        // 합성(LAN 직접) 엣지는 자리 잡기에 쓰지 않는다 — cloudflared 가 LAN 쪽으로 끌려 내려가지 않게
        const succ = (d.outE.get(n.id) || []).filter((e) => !e.synthetic).map((e) => pos.get(e.to)).filter((p) => p && p.col >= 4);
        let want = succ.length ? mean(succ.map((p) => p.y + p.h / 2)) : null;
        if (want == null) {
          const pr = (d.inE.get(n.id) || []).filter((e) => !e.synthetic).map((e) => cy(e.from)).filter((v) => v != null);
          want = pr.length ? mean(pr) : spineBottom;
        }
        items.push({ n, want, h: H2 });
      }
      const b = resolve(items, L, 20);
      if (L === 3) col3Bottom = b;
    }
    // 3b) LAN 에 포트를 바로 연 인프라·시스템 데몬(tailscale·avahi …) — SSH 처럼 3열, LAN 차선 바로 아래 "포트 직접" 묶음
    const direct = nodes.filter((n) => colOf(n) === 3 && n.type === "extra").sort(byName);
    if (direct.length) {
      const lanY = cy("zone:lan") != null ? cy("zone:lan") : A.lan;
      const gy = Math.max(lanY + 16, isFinite(col3Bottom) ? col3Bottom + 18 : -Infinity);
      let yy = gy + GH;
      for (const n of direct) { place(n, 3, yy, H1); yy += PITCH; }
      regions.push({ cat: "direct", x: COLX[3] - RP, y: gy, w: COLW[3] + RP * 2, h: yy - PITCH + H1 + RP - gy, n: direct.length, direct: true });
    }
    // 5) 내부 백엔드(5열): 자신을 쓰는 앱 옆에
    const internals = nodes.filter((n) => colOf(n) === 5 && n.type !== "extra");
    let c5Bottom = resolve(internals.map((n) => {
      const pr = (d.inE.get(n.id) || []).map((e) => cy(e.from)).filter((v) => v != null);
      return { n, want: pr.length ? mean(pr) : Infinity, h: H1 };
    }), 5, 6);
    if (internals.length) {
      const ps = internals.map((n) => pos.get(n.id));
      const top = Math.min(...ps.map((p) => p.y)) - GH; const bot = Math.max(...ps.map((p) => p.y + p.h)) + RP;
      regions.push({ cat: "internal", x: COLX[5] - RP, y: top, w: COLW[5] + RP * 2, h: bot - top, n: internals.length, loose: true });
      c5Bottom = bot;
    } else c5Bottom = -Infinity;
    // 6) 5열 아래: 길이 없는 인프라·시스템 데몬('경로 밖') — 카테고리별 세로 묶음
    const hostReg = regOf.host;
    let sy = Math.max(isFinite(c5Bottom) ? c5Bottom + GG + 6 : -Infinity, hostReg ? hostReg.y : spineBottom - 120);
    if (!isFinite(sy)) sy = spineBottom - 120;
    const stack = (list, reg) => {
      if (!list.length) return;
      let yy = sy + GH;
      for (const n of list) { place(n, 5, yy, H1); yy += PITCH; }
      regions.push(Object.assign({ x: COLX[5] - RP, y: sy, w: COLW[5] + RP * 2, h: yy - PITCH + H1 + RP - sy, n: list.length }, reg));
      sy = yy - PITCH + H1 + RP + GG;
    };
    const off = nodes.filter((n) => colOf(n) === 5 && n.type === "extra").sort(byName);
    for (const cat of [...new Set(off.map((n) => n.category || "other"))]) stack(off.filter((n) => (n.category || "other") === cat), { cat, extra: true });
    // 7) 기기 경계(2~5열)
    const inside = [...pos.values()].filter((p) => p.col >= 2);
    const regIn = regions.filter((r) => r.x >= COLX[2] - RP - 1);
    let box = null;
    if (inside.length) {
      const minY = Math.min(...inside.map((p) => p.y), ...regIn.map((r) => r.y));
      const maxY = Math.max(...inside.map((p) => p.y + p.h), ...regIn.map((r) => r.y + r.h));
      box = { x: COLX[2] - 30, y: minY - 40, w: COLX[5] + COLW[5] + 28 - (COLX[2] - 30), h: maxY - minY + 40 + 32 };
    }
    // 8) 엣지 경로
    const edges = [];
    for (const e of d.edges) {
      const a = pos.get(e.from); const b = pos.get(e.to);
      if (!a || !b) continue;
      edges.push(Object.assign({}, e, edgeGeom(a, b, e, spineReg.get(e.to))));
    }
    // 9) 경계
    const allP = [...pos.values()];
    let minY = Math.min(0, ...allP.map((p) => p.y), ...regions.map((r) => r.y));
    let maxY = Math.max(10, ...allP.map((p) => p.y + p.h), ...regions.map((r) => r.y + r.h));
    if (box) { minY = Math.min(minY, box.y); maxY = Math.max(maxY, box.y + box.h); }
    const maxX = Math.max(COLX[5] + COLW[5] + 30, box ? box.x + box.w + 8 : 0);
    const bounds = { x: -8, y: minY - 8, w: maxX + 16, h: maxY + 8 - (minY - 8) };
    // 영역·관문 열(0~3)의 윗부분 — 선택이 없고 전체가 너무 작아질 때 이것만 읽히게 담는다
    return { pos, regions, edges, box, bounds, moreRows };
  }
  const f1 = (v) => Math.round(v * 10) / 10;
  /** 엣지 한 줄의 경로. 척추(4열) 서비스로 들어가는 길은 묶는다: 출발점 → (영역 쪽 끝 높이까지) 한 줄기 → 척추 왼쪽 버스선 → 짧은 가지. */
  function edgeGeom(a, b, e, reg) {
    const y1 = a.y + a.h / 2; const y2 = b.y + b.h / 2;
    const x1 = a.x + a.w; const x2 = b.x;
    if (b.col === 4 && a.col < 4 && reg && e.kind !== "depends") {
      const xb = COLX[4] - RP - (BUS[e.kind] || 7);
      const sx = a.col < 3 ? COLX[3] + COLW[3] + 4 : x1;
      const yE = clamp(y1, reg.first, reg.last);
      const r = Math.min(6, Math.abs(y2 - yE) / 2);
      let d = `M${f1(x1)} ${f1(y1)}`;
      if (sx > x1 + 1) d += `H${f1(sx)}`;
      const xe = r > 0.5 ? xb - r : xb;
      const dx = Math.max(8, (xe - sx) * 0.5);
      d += `C${f1(sx + dx)} ${f1(y1)} ${f1(xe - dx)} ${f1(yE)} ${f1(xe)} ${f1(yE)}`;
      const lc = [sx, y1, sx + dx, y1, xe - dx, yE, xe, yE];
      if (r > 0.5) {
        const sg = y2 > yE ? 1 : -1;
        d += `Q${f1(xb)} ${f1(yE)} ${f1(xb)} ${f1(yE + sg * r)}V${f1(y2 - sg * r)}Q${f1(xb)} ${f1(y2)} ${f1(xb + r)} ${f1(y2)}`;
      }
      d += `H${f1(x2)}`;
      return { d, lx: (sx + xb) / 2, ly: (y1 + yE) / 2, ex: x2, ey: y2, lc };
    }
    if (b.col === a.col) {
      const x = a.x + a.w; const bulge = 24 + Math.min(34, Math.abs(y2 - y1) * 0.35);
      return { d: `M${x} ${y1}C${x + bulge} ${y1} ${x + bulge} ${y2} ${x} ${y2}`, lx: x + bulge * 0.75, ly: (y1 + y2) / 2, ex: x, ey: y2, lc: [x, y1, x + bulge, y1, x + bulge, y2, x, y2] };
    }
    if (b.col < a.col) {
      const xa = a.x; const xz = b.x + b.w; const dx = (xa - xz) / 2;
      return { d: `M${xa} ${y1}C${xa - dx} ${y1} ${xz + dx} ${y2} ${xz} ${y2}`, lx: (xa + xz) / 2, ly: (y1 + y2) / 2, ex: xz, ey: y2, lc: [xa, y1, xa - dx, y1, xz + dx, y2, xz, y2] };
    }
    const xk = COLX[b.col] - GAP[b.col - 1];
    let d = `M${x1} ${y1}`; let sx = x1;
    if (xk > x1 + 1) { d += `H${xk}`; sx = xk; }
    const dx = (x2 - sx) * 0.55;
    d += `C${(sx + dx).toFixed(1)} ${y1} ${(x2 - dx).toFixed(1)} ${y2} ${x2} ${y2}`;
    return { d, lx: (sx + x2) / 2, ly: (y1 + y2) / 2, ex: x2, ey: y2, lc: [sx, y1, sx + dx, y1, x2 - dx, y2, x2, y2] };
  }

  // ------------------------------------------------------------------ 그래프 그리기
  const NICON = {
    globe: '<circle cx="9" cy="9" r="7"/><path d="M2 9h14M9 2c2.6 2.3 2.6 11.7 0 14M9 2c-2.6 2.3-2.6 11.7 0 14"/>',
    mesh: '<circle cx="4" cy="4" r="1.7"/><circle cx="14" cy="4" r="1.7"/><circle cx="9" cy="9" r="1.7"/><circle cx="4" cy="14" r="1.7"/><circle cx="14" cy="14" r="1.7"/><path d="M5.3 5.3l2.4 2.4M12.7 5.3l-2.4 2.4M5.3 12.7l2.4-2.4M12.7 12.7l-2.4-2.4"/>',
    router: '<rect x="2" y="9.5" width="14" height="6" rx="1.6"/><path d="M5.2 12.5h.01M8.2 12.5h.01M6 9.5 4.6 4M12 9.5 13.4 4"/>',
    cloud: '<path d="M5 14.5h8.3a3 3 0 00.4-6A4.6 4.6 0 005.1 7.3 3.6 3.6 0 005 14.5z"/>',
    tunnel: '<path d="M2 15.5V9a7 7 0 0114 0v6.5"/><path d="M5.5 15.5V9.6a3.5 3.5 0 017 0v5.9"/><path d="M1 15.5h16"/>',
    gate: '<path d="M1.5 9h5M6.5 9l4-4.6h6M6.5 9l4 4.6h6M6.5 9h10"/>',
    term: '<rect x="1.5" y="3" width="15" height="12" rx="2"/><path d="M4.6 7l2.4 2-2.4 2M9 11.4h4.2"/>',
    box: '<path d="M9 2 15.5 5.6v6.8L9 16 2.5 12.4V5.6z"/><path d="M2.5 5.6 9 9.2l6.5-3.6M9 9.2V16"/>',
  };
  function nodeIcon(n) {
    if (n.id === "zone:internet") return "globe";
    if (n.id === "zone:tailnet") return "mesh";
    if (n.id === "zone:lan") return "router";
    if (n.type === "edge") return "cloud";
    const k = n.svc && n.svc.kind;
    if (k === "tunnel") return "tunnel";
    if (k === "proxy") return "gate";
    if (k === "remote-access") return "term";
    return "box";
  }
  const estW = (s, px) => { let w = 0; for (const ch of String(s)) w += ch.charCodeAt(0) > 0x2e80 ? px : px * 0.6; return w; };
  // 노드 이름 폭은 실제 글꼴로 잰다(웹폰트가 늦게 오면 한 번 다시 그린다). 캔버스가 없으면 어림값.
  const mctx = (() => { try { return document.createElement("canvas").getContext("2d"); } catch (e) { return null; } })();
  const FONT = (getComputedStyle(document.documentElement).getPropertyValue("--font") || "sans-serif").trim();
  const textW = (s, px, wt) => { if (!mctx) return estW(s, px); mctx.font = (wt || 650) + " " + px + "px " + FONT; return mctx.measureText(String(s)).width; };
  function fitLabel(s, maxW, px, wt) {
    s = String(s || "");
    if (textW(s, px, wt) <= maxW) return s;
    let out = "";
    for (const ch of s) { if (textW(out + ch + "…", px, wt) > maxW) break; out += ch; }
    return out + "…";
  }
  // 상태 기호: 정상 ● · 저하 ▲ · 다운 ◆ · 확인 불가 ○ (색만으로 말하지 않는다)
  const SG = raw(`<circle class="sg-ok" r="4.2"/><path class="sg-warn" d="M0 -4.9 4.8 3.6H-4.8z"/><rect class="sg-crit" x="-3.6" y="-3.6" width="7.2" height="7.2" rx="1.2" transform="rotate(45)"/><circle class="sg-muted" r="3.8"/>`);

  function nodeSvg(n, p) {
    const two = p.h >= 40;
    const name = SH.nodeName ? SH.nodeName(n) : n.id in ZONE_NAME ? ZONE_NAME[n.id] : n.label;
    const cls = "n n-" + n.type + (n.type === "zone" ? " n-zone-" + n.id.split(":")[1] : "");
    if (two) {
      const zone = n.type === "zone";
      const rx = zone ? p.h / 2 : 12;
      const ix = zone ? 15 : 13;
      return html`<g class="${cls}" data-id="${n.id}" data-tone="muted" transform="translate(${p.x} ${p.y})" tabindex="0" role="button" aria-label="${name}">
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="${rx + 4}"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="${rx}"/>
<g class="n-ic" transform="translate(${ix} ${p.h / 2 - 9})">${raw(NICON[nodeIcon(n)])}</g>
<text class="n-name" x="${zone ? 42 : 40}" y="19.5">${fitLabel(name, p.w - 62, 13, 700)}</text>
<text class="n-meta" x="${zone ? 42 : 40}" y="35"></text>
<g class="n-sg" transform="translate(${p.w - (zone ? 19 : 14)} ${zone ? 15 : 14})">${SG}</g>
</g>`;
    }
    const metaW = n.type === "extra" ? (p.col === 3 ? 46 : 58) : n.type === "internal" ? 54 : n.type === "host" ? 66 : 92;
    return html`<g class="${cls}" data-id="${n.id}" data-tone="muted" transform="translate(${p.x} ${p.y})" tabindex="0" role="button" aria-label="${name}">
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="12"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="8"/>
<g class="n-sg" transform="translate(13 ${p.h / 2})">${SG}</g>
<text class="n-name" x="25" y="${p.h / 2 + 4.5}">${fitLabel(name, p.w - 34 - metaW, 12.5, n.type === "extra" ? 550 : 650)}</text>
<text class="n-meta" x="${p.w - 10}" y="${p.h / 2 + 4}" text-anchor="end"></text>
</g>`;
  }
  function regionLabel(r) {
    if (r.direct) return "포트 직접 · LAN";
    if (r.extra) return catName(r.cat) + " · 경로 밖";
    return catName(r.cat);
  }
  function buildGraph(L) {
    const regs = L.regions.map((r) => {
      const icon = r.direct ? "lan" : CAT_ICON[r.cat] || "layers";
      return html`<g class="${"reg" + (r.loose ? " reg-loose" : "") + (r.extra ? " reg-shelf" : "") + (r.direct ? " reg-direct" : "")}" data-cat="${r.cat}">
<rect class="reg-bg" x="${r.x}" y="${r.y}" width="${r.w}" height="${r.h}" rx="14"/>
<g class="reg-ic" transform="${"translate(" + (r.x + 10) + " " + (r.y + 8) + ") scale(.5)"}">${raw(ICON[icon])}</g>
<text class="reg-l" x="${r.x + 27}" y="${r.y + 18}">${regionLabel(r)}</text>
<text class="reg-n" x="${r.x + r.w - 12}" y="${r.y + 18}" text-anchor="end">${r.n}</text>
</g>`;
    });
    const box = L.box ? html`<g class="hostbox"><rect x="${L.box.x}" y="${L.box.y}" width="${L.box.w}" height="${L.box.h}" rx="24"/>
<text class="hb-l" x="${L.box.x + 20}" y="${L.box.y + 24}">이 기기 안</text>
<text class="hb-ip" x="${L.box.x + L.box.w - 20}" y="${L.box.y + L.box.h - 12}" text-anchor="end"></text></g>` : "";
    // 선 색은 늘 길의 종류. 저하·다운인 곳으로 가는 선은 끝에 상태색 점만 붙인다(점선은 '의존' 전용).
    // e-flow = 움직임을 줄인 환경의 멈춘 점선 · e-fx = 트래픽 점(renderFlows 가 요청 많은 길에만 채운다)
    const edges = L.edges.map((e) => html`<g class="${"e" + (e.synthetic ? " e-syn" : "")}" data-kind="${e.kind}" data-key="${e.key}" data-flow="0"><path class="e-base" d="${e.d}"/><path class="e-flow" d="${e.d}"/><g class="e-fx" aria-hidden="true"></g><circle class="e-end" cx="${e.ex}" cy="${e.ey}" r="3"/></g>`);
    // 같은 출발점·같은 글자의 라벨은 하나로 합친다(예: WireGuard ×2).
    const lab = new Map();
    for (const e of L.edges) {
      if (!e.label) continue;
      // 포트 직접 묶음(tailscale·avahi …)은 노드의 보조 줄이 포트를 보여 주므로 선 라벨을 달지 않는다(겹침 방지)
      if (e.synthetic && (D.nodes.get(e.to) || {}).type === "extra") continue;
      const k = e.from + "|" + e.label;
      if (!lab.has(k)) lab.set(k, { label: e.label, kind: e.kind, xs: [], ys: [], keys: [] });
      const o = lab.get(k); o.xs.push(e.lx); o.ys.push(e.ly); o.keys.push(e.key); o.lc = e.lc; o.syn = !!e.synthetic;
    }
    // 라벨끼리 겹치면 같은 곡선 위의 다른 자리(가운데 → 조금 앞·뒤)로 옮긴다. 토폴로지 라벨이 합성(LAN 직접) 라벨보다 먼저 자리를 잡는다.
    const bez = (c, t) => { const u = 1 - t; return [u * u * u * c[0] + 3 * u * u * t * c[2] + 3 * u * t * t * c[4] + t * t * t * c[6], u * u * u * c[1] + 3 * u * u * t * c[3] + 3 * u * t * t * c[5] + t * t * t * c[7]]; };
    const placed = [];
    const hit = (x, y, w) => placed.some((q) => Math.abs(q.x - x) < (q.w + w) / 2 + 3 && Math.abs(q.y - y) < 19);
    const labs = [...lab.values()].sort((a, b) => a.syn - b.syn).map((o) => {
      const w = Math.max(28, estW(o.label, 10) + 14);
      let x = mean(o.xs); let y = mean(o.ys);
      if (o.xs.length === 1 && o.lc) {
        for (const t of [0.5, 0.36, 0.64, 0.24, 0.76, 0.14, 0.86]) { const [px, py] = bez(o.lc, t); if (!hit(px, py, w)) { x = px; y = py; break; } }
      }
      placed.push({ x, y, w });
      return html`<g class="el" data-kind="${o.kind}" data-keys="${o.keys.join(" ")}" transform="translate(${x.toFixed(1)} ${y.toFixed(1)})"><rect x="${(-w / 2).toFixed(1)}" y="-8.5" width="${w.toFixed(1)}" height="17" rx="8.5"/><text y="3.5" text-anchor="middle">${o.label}</text></g>`;
    });
    // 키보드 Tab 순서 = 눈으로 읽는 순서(왼쪽 열부터, 위에서 아래로)
    const nodes = [...L.pos.entries()].sort((a, b) => a[1].col - b[1].col || a[1].y - b[1].y || a[1].x - b[1].x).map(([id, p]) => nodeSvg(D.nodes.get(id), p));
    const more = (L.moreRows || []).map((m) => html`<g class="n-more" data-more="${m.cat}" data-open="${m.hidden ? "0" : "1"}" tabindex="0" role="button" aria-label="${m.hidden ? catName(m.cat) + " 나머지 " + m.hidden + "개 더 보기" : catName(m.cat) + " 접기 — 10개만 보기"}" transform="translate(${m.x} ${m.y})"><rect width="${m.w}" height="${m.h}" rx="8"/><text x="${m.w / 2}" y="${m.h / 2 + 4}" text-anchor="middle">${m.hidden ? "+" + m.hidden + "개 더 보기" : "접기 · " + m.total + "개 중 10개만"}</text></g>`);
    flowClear();
    P.render(vp, html`${box}<g class="regs">${regs}</g><g class="edges">${edges}</g><g class="elabels">${labs}</g><g class="nodes">${nodes}${more}</g>`);
    G.nodeEls = new Map(); G.metaEls = new Map(); G.auxEls = new Map(); G.edgeEls = new Map(); G.metaMax = new Map(); G.refit = true;
    for (const el of $$(".n", vp)) {
      const id = el.getAttribute("data-id"); const p = L.pos.get(id);
      G.nodeEls.set(id, el); G.metaEls.set(id, el.querySelector(".n-meta"));
      // 보조 줄(지연·인증서·포트 …)이 쓸 수 있는 폭: 두 줄 노드는 이름 아래 줄 전체, 한 줄 노드는 이름 오른쪽 남는 폭
      if (p) {
        if (p.h >= 40) G.metaMax.set(id, p.w - (id.startsWith("zone:") ? 42 : 40) - 12);
        else { const nm = el.querySelector(".n-name"); let nw = 0; try { nw = nm.getComputedTextLength(); } catch (e) { nw = 0; } G.metaMax.set(id, nw ? p.w - 10 - (25 + nw + 10) : 0); }
      }
    }
    for (const el of $$(".e", vp)) G.edgeEls.set(el.getAttribute("data-key"), el);
    G.labelEls = $$(".el", vp).map((el) => { el.__keys = (el.getAttribute("data-keys") || "").split(" "); return el; });
    G.hostIp = $(".hb-ip", vp);
  }
  /** 물리 보기 그리기(모양은 map-physical.js). 보조 줄이 쓸 수 있는 폭은 data-mx(고정 폭) 또는 이름 오른쪽 남는 폭. */
  function buildPhys(L) {
    flowClear();
    P.render(vp, PHYS.svg(PD, L));
    G.nodeEls = new Map(); G.metaEls = new Map(); G.auxEls = new Map(); G.edgeEls = new Map(); G.metaMax = new Map(); G.refit = true;
    for (const el of $$(".n", vp)) {
      const id = el.getAttribute("data-id");
      G.nodeEls.set(id, el);
      const m = el.querySelector(".n-meta");
      G.metaEls.set(id, m);
      const ax = el.querySelector(".n-aux");
      if (ax) G.auxEls.set(id, ax);
      if (!m) continue;
      const mx = m.getAttribute("data-mx");
      if (mx === "name") {
        const nm = el.querySelector(".n-name"); let nw = 0;
        try { nw = nm.getComputedTextLength(); } catch (e) { nw = 0; }
        const right = +m.getAttribute("x") || 0; const x0 = +nm.getAttribute("x") || 0;
        G.metaMax.set(id, nw ? right - (x0 + nw + 10) : 0);
      } else if (mx) G.metaMax.set(id, +mx);
    }
    for (const el of $$(".e", vp)) G.edgeEls.set(el.getAttribute("data-key"), el);
    G.labelEls = $$(".el", vp).map((el) => { el.__keys = (el.getAttribute("data-keys") || "").split(" "); return el; });
    G.hostIp = null;
    G.virtEl = $(".p-virt", vp);
    G.ghostEls = $$(".p-ghost", vp);
    G.capEls = $$(".p-cap[data-cap]", vp).filter((el) => el.getAttribute("data-cap"));
  }
  function updatePhys() {
    const refit = [];
    for (const [id, el] of G.nodeEls) {
      const n = PD.nodes.get(id);
      if (!n) continue;
      const m = PHYS.info(PD, n);
      setAttr(el, "data-tone", m.tone);
      setAttr(el, "aria-label", m.aria);
      if (n.type === "group" && n.role === "offline") setText(el.querySelector(".n-name"), PHYS.groupTitle(PD, n));
      const t = G.metaEls.get(id);
      if (t) {
        const before = t.__jarvisHtml;
        P.render(t, m.meta);
        if (G.refit || t.__jarvisHtml !== before) refit.push([id, t]);
      }
      const ax = G.auxEls.get(id);
      if (ax) setText(ax, m.aux || "");
      if (n.type === "device") { const sw = el.querySelector(".n-stw"); if (sw) setText(sw, m.stw || ""); }
    }
    G.refit = false;
    for (const [id, t] of refit) fitMeta(id, t);
    // 열 제목(같은 LAN 기기 N · 잠시 안 보임 M)은 배치 없이 바뀐다 — 요약 칸과 같은 수
    for (const el of G.capEls || []) { const t = PHYS.capText ? PHYS.capText(PD, el.getAttribute("data-cap")) : null; if (t != null) setText(el, t); }
    // 선: Tailnet 경로(직결·중계·유휴)·LAN 잠시 안 보임은 제자리에서 모양만 바꾼다(다시 배치하지 않는다)
    for (const [key, el] of G.edgeEls) {
      const e = PD.byKey.get(key);
      if (!e) continue;
      setAttr(el, "data-kind", e.kind);
      setAttr(el, "data-stale", e.stale ? "1" : "0");
    }
    // 선 라벨(QUIC 연결 수·경로 …)은 자리는 그대로 두고 글자·폭만
    for (const el of G.labelEls) {
      const e = PD.byKey.get(el.__keys[0]);
      if (!e) continue;
      setAttr(el, "data-kind", e.kind);
      const tx = el.querySelector("text"); const lb = String(e.label || "");
      if (tx && tx.textContent !== lb) {
        tx.textContent = lb;
        const w = PHYS.labelW ? PHYS.labelW(lb) : Math.max(28, estW(lb, 11) + 14); const r = el.querySelector("rect");
        if (r) { r.setAttribute("x", (-w / 2).toFixed(1)); r.setAttribute("width", w.toFixed(1)); }
      }
    }
    applyLayers();
    applyHighlight();
  }

  // 노드별 짧은 계기(오른쪽 숫자) — 3초마다 제자리 갱신
  function nodeInfo(n) {
    const s = S; const sv = n.svc;
    const st = n.type === "extra" && sv ? sv.status : n.status;
    let t = tone(st);
    let meta = ""; let aria = nodeName(n.id) + ", " + statusText(st);
    const T = (cls, v) => html`<tspan class="${cls}">${v}</tspan>`;
    const sep = T("m-sep", " · ");
    const bad = st && st !== "up" ? T("m-st", statusText(st)) : "";
    switch (n.type) {
      case "zone": {
        if (n.id === "zone:internet") {
          const pub = (s.services || []).filter((x) => x.exposure && x.exposure.public).length;
          const req = s.ingress && s.ingress.traffic && s.ingress.traffic.last_1h && s.ingress.traffic.last_1h.by_via ? s.ingress.traffic.last_1h.by_via.tunnel : null;
          meta = html`${T("m-v", "공개 " + pub)}${sep}${T("m-v", "1h " + fmt.num(req || 0) + "건")}`;
          aria = "인터넷 영역 — 공개 서비스 " + pub + "개, 최근 1시간 공개 요청 " + fmt.num(req || 0) + "건";
        } else if (n.id === "zone:tailnet") {
          const ts = s.tailscale || {};
          meta = html`${T("m-v", fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total))}${T("m-u", " 기기 온라인")}`;
          aria = "Tailnet 영역 — 기기 " + fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total) + " 온라인";
        } else if (n.id === "zone:lan") {
          const ips = (s.system && s.system.network && s.system.network.lan_ips) || [];
          meta = html`${T("m-v", ips[0] || n.detail || "–")}${ips.length > 1 ? T("m-u", " 외 " + (ips.length - 1)) : ""}`;
          aria = "LAN 영역 — 이 기기 " + (ips.join(", ") || "");
        } else meta = T("m-v", n.detail || "");
        t = n.status && n.status !== "up" ? t : "ok";
        break;
      }
      case "edge": {
        const tn = (s.ingress && s.ingress.tunnel) || {};
        const rtt = mean((tn.rtt_ms || []).filter(isNum));
        meta = html`${T("m-v", (tn.edge_locations || []).join(" · ") || n.detail || "–")}${rtt != null ? html`${sep}${T("m-v", Math.round(rtt) + "ms")}` : ""}`;
        break;
      }
      case "infra": {
        const x = (sv && sv.extra) || {};
        if (sv && sv.kind === "tunnel") {
          const tn = (s.ingress && s.ingress.tunnel) || {};
          meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-u", "연결 ")}${T("m-v", fmt.num(tn.ready_connections != null ? tn.ready_connections : x.connections) + "/" + fmt.num(tn.connections != null ? tn.connections : x.connections))}${tn.outdated ? html`${sep}${T("m-info", "업데이트")}` : ""}`;
        } else if (sv && sv.kind === "proxy") {
          meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", x.version || "–")}${sep}${T("m-u", "라우트 ")}${T("m-v", fmt.num(x.routes))}`;
        } else {
          const ports = sv ? [...new Set((sv.listeners || []).filter((l) => l.proto === "tcp").map((l) => ":" + l.port))].slice(0, 2).join(" ") : "";
          meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", ports || n.detail || "–")}${sv && sv.runtime ? html`${sep}${T("m-u", "가동 " + fmt.durShort(sv.runtime.uptime_s))}` : ""}`;
        }
        break;
      }
      case "service": {
        const lat = P.pick.latency(sv);
        const cd = sv && sv.cert ? sv.cert.days_left : null;
        const certCls = cd != null && cd < 14 ? "m-crit" : cd != null && cd < 30 ? "m-warn" : "m-u";
        meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", fmt.ms(lat))}${cd != null ? html`${sep}${T(certCls, Math.floor(cd) + "일")}` : ""}`;
        aria += ", " + (sv && sv.probes && sv.probes.internal ? "Tailnet 경로 응답 " : "공개 경로 응답 ") + fmt.ms(lat) + (cd != null ? ", 인증서 " + Math.floor(cd) + "일 남음" : "");
        break;
      }
      case "host": {
        const ports = sv ? [...new Set((sv.listeners || []).filter((l) => l.scope !== "loopback").map((l) => ":" + l.port))] : [];
        meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", ports[0] || (n.detail ? ":" + String(n.detail).split(",")[0] : "–"))}${ports.length > 1 ? T("m-u", " +" + (ports.length - 1)) : ""}`;
        aria += ", 포트 " + (ports.join(" ") || n.detail || "");
        break;
      }
      case "internal": {
        const rt = sv && sv.runtime;
        meta = bad || T("m-v", rt && rt.mem_bytes != null ? fmt.bytes(rt.mem_bytes, 0) : "");
        if (rt && rt.service) aria += ", " + rt.service;
        break;
      }
      default: {
        const open = sv ? [...new Set((sv.listeners || []).filter((l) => l.scope === "all" || l.scope === "lan").map((l) => ":" + l.port))] : [];
        meta = bad || (open.length ? html`${T("m-v", open[0])}${open.length > 1 ? T("m-u", " +" + (open.length - 1)) : ""}` : T("m-u", sv ? kindName(sv.kind) : ""));
        if (open.length) aria += ", LAN 에 열린 포트 " + open.join(" ");
        const rt = sv && sv.runtime;
        if (rt && rt.mem_bytes != null) aria += ", 메모리 " + fmt.bytes(rt.mem_bytes);
      }
    }
    if (sv && sv.status_reason && st !== "up") aria += ". " + sv.status_reason;
    return { tone: t, meta, aria };
  }

  function edgeWeight(e) {
    const tr = (S.ingress && S.ingress.traffic && S.ingress.traffic.last_1h) || {};
    const via = tr.by_via || {};
    const to = D.nodes.get(e.to); const from = D.nodes.get(e.from);
    if (e.kind === "depends") return (from && from.svc && from.svc.traffic && from.svc.traffic.requests_1h) || 0;
    if (to && to.svc && to.svc.traffic && SERVICE_LIKE.has(to.type)) return to.svc.traffic.requests_1h || 0;
    if (e.kind === "public") return via.tunnel || 0;
    if (e.kind === "tailnet" && to && to.svc && to.svc.kind === "proxy") return via.tailnet || 0;
    if (e.kind === "lan" && to && to.svc && to.svc.kind === "proxy") return via.lan || 0;
    return 0;
  }
  const flowBucket = (w) => (w <= 0 ? 0 : w < 5 ? 1 : w < 30 ? 2 : w < 150 ? 3 : 4);

  /** 보조 줄이 노드 밖으로 넘치면 뒤쪽 조각(· 로 나뉜 묶음)부터 빼고, 그래도 넘치면 글자를 줄인다. 전체는 툴팁으로. */
  function fitMeta(id, t) {
    const max = G.metaMax.get(id);
    if (!t || !max || !t.getComputedTextLength) return;
    let len = 0;
    try { len = t.getComputedTextLength(); } catch (e) { return; }
    if (!len || len <= max) return;
    const full = t.textContent;
    let spans = [...t.children].filter((x) => x.tagName === "tspan");
    while (len > max) {
      const si = spans.map((x) => x.classList.contains("m-sep")).lastIndexOf(true);
      if (si <= 0) break;
      spans.slice(si).forEach((x) => x.remove());
      spans = spans.slice(0, si);
      len = t.getComputedTextLength();
    }
    if (len > max) {
      const last = spans.length ? spans[spans.length - 1] : t;
      let txt = last.textContent;
      while (txt.length > 1 && t.getComputedTextLength() > max) { txt = txt.slice(0, -1); last.textContent = txt + "…"; }
    }
    const ti = document.createElementNS("http://www.w3.org/2000/svg", "title");
    ti.textContent = full;
    t.appendChild(ti);
  }
  function updateGraph() {
    const refit = [];
    for (const [id, el] of G.nodeEls) {
      const n = D.nodes.get(id);
      if (!n) continue;
      const m = nodeInfo(n);
      setAttr(el, "data-tone", m.tone);
      setAttr(el, "aria-label", m.aria);
      const t = G.metaEls.get(id);
      const before = t ? t.__jarvisHtml : null;
      P.render(t, m.meta);
      if (t && (G.refit || t.__jarvisHtml !== before)) refit.push([id, t]);
    }
    G.refit = false;
    for (const [id, t] of refit) fitMeta(id, t);   // 쓰기를 다 한 뒤 한 번에 잰다(레이아웃 한 번)
    for (const e of G.layout.edges) {
      const el = G.edgeEls.get(e.key);
      if (!el) continue;
      setAttr(el, "data-flow", String(flowBucket(edgeWeight(e))));
      const to = D.nodes.get(e.to);
      const tt = to ? tone(to.type === "extra" && to.svc ? to.svc.status : to.status) : "ok";
      setAttr(el, "data-tone", tt === "warn" || tt === "crit" ? tt : "ok");
    }
    if (G.hostIp) {
      const nw = (S.system && S.system.network) || {};
      setText(G.hostIp, [...(nw.lan_ips || []), nw.tailnet_ip].filter(Boolean).join(" · "));
    }
    renderFlows();
    applyLayers();
    applyHighlight();
  }

  // ------------------------------------------------------------------ 트래픽 흐름(싸게)
  // 최근 1시간 요청이 있는 길(requests_1h > 0 · 터널/Tailnet 입구 요청 수)만, 많은 순으로 FLOW_MAX 개까지 작은 점이 길을 따라 흐른다.
  // 점의 움직임은 transform 키프레임(Web Animations — 길 위의 점을 미리 뽑아 둔다)이라 합성 단계에서 돌고 메인 스레드를 거의 쓰지 않는다
  // (예전의 stroke-dashoffset 점선은 프레임마다 선 전체를 다시 그렸다). 요청이 많을수록 점이 많고 빠르다.
  // 탭이 숨었거나, FLOW_IDLE_MS 동안 마우스·키·터치가 없거나, 연결이 끊겼으면 멈춘다(점은 제자리). 손대면 다시 흐른다.
  // 움직임 줄이기·임베드: 점 없이 멈춘 점선(.e-flow)으로만 보인다.
  const FLOW_MAX = 6;
  const FLOW_IDLE_MS = 45000;
  const FLOW_SPEED = [0, 34, 50, 72, 104];   // px/초
  const FLOW_DOTS = [0, 1, 2, 2, 3];
  const flowClock = { idle: false, last: 0, t: 0, paused: false };
  G.fx = new Map();   // 엣지 key → { b: 빠르기 칸, anims: [Animation] }
  /** 흐름 점을 모두 지운다(다시 그리기·관점 바꾸기 전에 — 떨어져 나간 요소의 애니메이션이 남지 않게) */
  function flowClear() {
    for (const [key, x] of G.fx) {
      for (const a of x.anims) { try { a.cancel(); } catch (e) { /* 무시 */ } }
      const el = G.edgeEls.get(key); const fx = el && el.querySelector(".e-fx");
      if (fx) P.render(fx, "");
    }
    G.fx = new Map(); G.fxSig = null;
  }
  /** 길 위의 점(월드 좌표) — 길이에 비례해 8~40개 */
  function flowPts(el) {
    if (el.__pts) return el.__pts;
    const path = el.querySelector(".e-base");
    let len = 0;
    try { len = path.getTotalLength(); } catch (e) { len = 0; }
    if (!len) return null;
    const n = clamp(Math.round(len / 18), 8, 40);
    const pts = [];
    for (let i = 0; i <= n; i++) { const q = path.getPointAtLength((len * i) / n); pts.push({ transform: "translate(" + q.x.toFixed(1) + "px, " + q.y.toFixed(1) + "px)" }); }
    el.__len = len; el.__pts = pts;
    return pts;
  }
  function renderFlows() {
    if (!G.layout || isPhys() || mobile) return;
    const list = [];
    if (!calm() && document.body.animate) for (const e of G.layout.edges) { const w = edgeWeight(e); if (w > 0 && G.edgeEls.has(e.key)) list.push({ e, w }); }
    list.sort((a, b) => b.w - a.w || (a.e.key < b.e.key ? -1 : a.e.key > b.e.key ? 1 : 0));
    const want = new Map(list.slice(0, FLOW_MAX).map((x) => [x.e.key, flowBucket(x.w)]));
    const sig = [...want].map(([k, b]) => k + ":" + b).sort().join("|");
    if (sig === G.fxSig) return;
    G.fxSig = sig;
    // 빠진 길의 점은 지운다
    for (const [key, x] of G.fx) {
      if (want.get(key) === x.b) continue;
      for (const a of x.anims) { try { a.cancel(); } catch (e) { /* 무시 */ } }
      const el = G.edgeEls.get(key); const fx = el && el.querySelector(".e-fx");
      if (fx) P.render(fx, "");
      G.fx.delete(key);
    }
    // 새로 흐를 길(또는 빠르기가 바뀐 길)에 점을 단다
    for (const [key, b] of want) {
      if (G.fx.has(key)) continue;
      const el = G.edgeEls.get(key); const fx = el && el.querySelector(".e-fx");
      const pts = el && fx ? flowPts(el) : null;
      if (!pts) continue;
      const n = FLOW_DOTS[b];
      const dur = clamp(el.__len / FLOW_SPEED[b], 1.6, 9) * 1000;
      const dots = [];
      for (let i = 0; i < n; i++) dots.push(html`<circle class="e-dotf" r="${b >= 4 ? 3.6 : 3}"/>`);
      P.render(fx, html`${dots}`);
      const anims = [...fx.children].map((c, i) => {
        const a = c.animate(pts, { duration: dur, iterations: Infinity, delay: -(dur * i) / n, easing: "linear" });
        if (flowClock.paused) a.pause();
        return a;
      });
      G.fx.set(key, { b, anims });
    }
    syncFlowClock();
  }
  /** 멈출 때인가(탭 숨김 · 오래 손대지 않음 · 연결 끊김 · 물리/모바일) → 점 애니메이션을 한꺼번에 멈추거나 돌린다 */
  function syncFlowClock() {
    const stop = document.hidden || flowClock.idle || liveState === "stale" || isPhys() || mobile || calm();
    if (stop === flowClock.paused) return;
    flowClock.paused = stop;
    for (const x of G.fx.values()) for (const a of x.anims) { try { if (stop) a.pause(); else a.play(); } catch (e) { /* 무시 */ } }
  }
  function flowWake() {
    flowClock.last = Date.now();
    if (flowClock.idle) { flowClock.idle = false; syncFlowClock(); }
    if (!flowClock.t) flowClock.t = setTimeout(flowIdleCheck, FLOW_IDLE_MS);
  }
  function flowIdleCheck() {
    flowClock.t = 0;
    const left = FLOW_IDLE_MS - (Date.now() - flowClock.last);
    if (left > 200) { flowClock.t = setTimeout(flowIdleCheck, left); return; }
    flowClock.idle = true;
    syncFlowClock();
  }
  for (const t of ["pointermove", "pointerdown", "wheel", "keydown", "touchstart"]) window.addEventListener(t, flowWake, { passive: true, capture: true });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) flowWake(); syncFlowClock(); });
  reduceMq.addEventListener("change", () => { flowClear(); renderFlows(); syncFlowClock(); });
  flowWake();

  // ------------------------------------------------------------------ 레이어·강조
  function applyLayers() {
    const off = Object.keys(ui.layers).filter((k) => !ui.layers[k]);
    setAttr(graph, "data-off", off.join(" "));
    for (const b of $$(".layer", toolbar)) setAttr(b, "aria-pressed", ui.layers[b.dataset.kind] ? "true" : "false");
    if (isPhys()) { setAttr(graph, "data-off", ""); for (const [, el] of G.nodeEls) el.classList.remove("off"); return; }
    if (!D) return;
    for (const [id, el] of G.nodeEls) {
      const n = D.nodes.get(id);
      let dim = false;
      if (n && off.length) {
        const hasDep = (D.inE.get(id) || []).some((e) => e.kind === "depends");
        const ks = kindsOf(id);
        if (ks && ks.size) dim = [...ks].every((k) => !ui.layers[k]);
        else if (hasDep) dim = !ui.layers.depends;
      }
      el.classList.toggle("off", dim);
    }
  }
  function applyHighlight() {
    if (mobile || !(isPhys() ? PD : D)) return;
    const id = ui.hover || ui.selected;
    let on = null;
    if (isPhys()) {
      if (id && id.startsWith("lvl:")) on = PHYS.cellSet(PD, id.slice(4));
      else if (id && PD.nodes.has(id)) on = PHYS.hl(PD, id);
    } else if (id && id.startsWith("lvl:")) on = levelSet(id.slice(4));
    else if (id && D.nodes.has(id)) on = pathSet(id);
    graph.classList.toggle("has-hl", !!on);
    for (const [nid, el] of G.nodeEls) {
      el.classList.toggle("on", !!on && on.N.has(nid));
      el.classList.toggle("sel", nid === ui.selected);
    }
    for (const [key, el] of G.edgeEls) el.classList.toggle("on", !!on && on.E.has(key));
    for (const el of G.labelEls) el.classList.toggle("on", !!on && el.__keys.some((k) => on.E.has(k)));
    if (isPhys()) {
      // 열린 포트 → 인터페이스(실제 선이 없는 연결)는 가리킬 때만 옅게
      if (G.virtEl) P.render(G.virtEl, on && on.V && on.V.length ? html`${PHYS.virt(PD, G.layout, on).map((v) => html`<path class="pv" data-k="${v.kind}" d="${v.d}"/>`)}` : "");
      for (const el of G.ghostEls || []) el.classList.toggle("on", !!on && on.N.has(el.getAttribute("data-ghost")));
    }
    renderCaption(id);
  }
  function renderCaption(id) {
    if (mobile) return;
    caption.classList.toggle("is-hint", !id);
    // 고른 노드의 길은 검사기의 "어떻게 닿는가"가 보여 주니, 올린 것이 없으면 설명 띠를 숨긴다.
    const quiet = !ui.hover && ui.selected && app.dataset.insp === "open";
    if (caption.hidden !== !!quiet) caption.hidden = !!quiet;
    if (quiet) return;
    if (isPhys()) {
      if (id && id.startsWith("lvl:") && PD) {
        const c = PHYS.summary(PD).find((x) => x.id === id.slice(4));
        renderKeep(caption, html`<p class="cap-title"><b>${c ? c.label + " " + c.val : ""}</b><span>${c ? c.sub : ""} — 지도에서 밝혔습니다</span></p>`);
      } else if (id && PD && PD.nodes.has(id)) renderKeep(caption, PHYS.caption(PD, id));
      else renderKeep(caption, html`<p class="cap-hint">${ic("server")}<span><b>물리</b> 어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가 · 올리면 포함 관계와 연결이, 누르면 상세가 열립니다 · 끌어서 이동 · Ctrl+휠 확대</span></p>`);
      return;
    }
    if (id && id.startsWith("lvl:")) {
      const L = LEVEL[id.slice(4)];
      renderKeep(caption, html`<p class="cap-title"><b>${L ? L.long || L.label : ""}</b><span>${L ? L.sub : ""} — 해당 서비스와 그 길을 밝혔습니다</span></p>`);
      return;
    }
    if (!id || !D || !D.nodes.has(id)) {
      renderKeep(caption, html`<p class="cap-hint">${ic("route")}<span><b>개념</b> 무엇이 어떤 경로로 공개되는가 · 노드에 올리면 그곳까지 가는 길이 밝아지고, 누르면 상세가 열립니다 · 끌어서 이동 · Ctrl+휠 확대</span></p>`);
      return;
    }
    const cs = chains(id, 3);
    const n = D.nodes.get(id);
    const body = cs.length
      ? html`<ol class="cap-chains">${cs.map((c) => html`<li>${chainHtml(c)}</li>`)}</ol>`
      : html`<p class="cap-none">${n && n.type === "zone" ? "출발 영역 — 여기서 오른쪽으로 길이 나갑니다" : n && n.type === "extra" ? "밖에서 이어진 길이 없습니다 — 이 기기 안에서만 확인됩니다" : "밖에서 닿는 길이 없습니다(내부 전용)"}</p>`;
    renderKeep(caption, html`<p class="cap-title"><b>${nodeName(id)}</b><span>${cs.length ? "까지 가는 길" : ""}</span></p>${body}`);
  }
  function setHover(id) {
    if (id === ui.hover) return;
    ui.hover = id;
    applyHighlight();
  }

  // ------------------------------------------------------------------ 이동·확대
  const zoomVal = $("#zoom-val");
  function applyView(animate) {
    vp.classList.toggle("anim", !!animate && !calm());
    vp.style.transform = `translate(${view.tx.toFixed(1)}px, ${view.ty.toFixed(1)}px) scale(${view.k.toFixed(4)})`;
    setText(zoomVal, Math.round(view.k * 100) + "%");
    afterView(animate);
  }

  // ------------------------------------------------------------------ 화면 밖 안내 · 설명 띠 접기
  // 기본 맞춤이 최저 배율에 걸려 지도 일부가 화면 밖이면 "↓ Tailnet 기기 6 · 오프라인 10대"처럼 알려 주고, 누르면 그쪽으로 옮긴다.
  // 설명 띠(안내 문구)가 노드를 가리면 안내 문구만 접는다(노드에 올리면 다시 보인다).
  const offsEl = document.createElement("div");
  offsEl.className = "offs"; offsEl.id = "offs"; offsEl.hidden = true;
  offsEl.setAttribute("role", "group"); offsEl.setAttribute("aria-label", "화면 밖에 있는 것");
  app.appendChild(offsEl);
  let viewT = 0; let offsInfo = null;
  function afterView(animate) {
    if (viewT) return;
    const run = () => { viewT = 0; updateCapFold(); updateOffscreen(); };
    // 움직임이 끝난 자리로 재야 한다(애니메이션 중이면 조금 뒤에)
    viewT = animate && !calm() ? setTimeout(run, 480) : requestAnimationFrame(run);
  }
  const scr = (p) => ({ x1: view.tx + p.x * view.k, y1: view.ty + p.y * view.k, x2: view.tx + (p.x + p.w) * view.k, y2: view.ty + (p.y + p.h) * view.k });
  function updateCapFold() {
    if (mobile || !G.layout) { if (app.dataset.capfold) delete app.dataset.capfold; return; }
    const hint = caption.classList.contains("is-hint");
    if (!hint) return; // 가리키는 중에는 늘 보인다
    const gr = graph.getBoundingClientRect(); const cr = caption.getBoundingClientRect();
    if (!cr.width) return;
    const c = { x1: cr.left - gr.left - 6, y1: cr.top - gr.top - 6, x2: cr.right - gr.left + 6, y2: cr.bottom - gr.top + 6 };
    let hit = false;
    for (const p of G.layout.pos.values()) {
      const q = scr(p);
      if (q.x2 > c.x1 && q.x1 < c.x2 && q.y2 > c.y1 && q.y1 < c.y2) { hit = true; break; }
    }
    setAttr(app, "data-capfold", hit ? "1" : "0");
  }
  function updateOffscreen() {
    if (mobile || !G.layout || !S) { offsEl.hidden = true; offsInfo = null; return; }
    const gr = graph.getBoundingClientRect();
    const capShown = !caption.hidden && getComputedStyle(caption).visibility !== "hidden" && getComputedStyle(caption).display !== "none";
    const cr = capShown ? caption.getBoundingClientRect() : null;
    const bottom = cr && cr.height ? cr.top - gr.top - 4 : gr.height - 8;
    const right = gr.width - (app.dataset.insp === "open" ? inspector.offsetWidth + 24 : 8);
    app.style.setProperty("--m-cap-h", (cr && cr.height ? Math.round(gr.bottom - cr.top) : 0) + "px");
    const down = []; const side = [];
    const zones = G.layout.zones;
    if (zones && zones.length) {
      for (const z of zones) {
        const rs = z.ids.map((id) => G.layout.pos.get(id)).filter(Boolean);
        // 30% 넘게 가려지면 "화면 밖"
        const offB = rs.filter((p) => scr(p).y1 + p.h * view.k * 0.7 > bottom);
        const offR = rs.filter((p) => scr(p).x1 + p.w * view.k * 0.7 > right);
        // 묶음 전체가 밖이면 요약 칸과 같은 수(오프라인 N대 · 같은 LAN 기기 N — 잠시 안 보임은 빼고), 일부만이면 밖에 있는 수
        const cnt = (off) => (z.id === "off" || (z.count != null && off.length === rs.length) ? z.count : off.length);
        if (offB.length) down.push({ t: z.label + " " + cnt(offB) + (z.unit || ""), rs: offB });
        else if (offR.length) side.push({ t: z.label + " " + cnt(offR) + (z.unit || ""), rs: offR });
      }
    } else {
      const svcN = [...G.layout.pos.entries()].filter(([id]) => { const n = D && D.nodes.get(id); return n && SERVICE_LIKE.has(n.type); }).map(([, p]) => p);
      const offB = svcN.filter((p) => scr(p).y1 + p.h * view.k * 0.7 > bottom);
      const offR = svcN.filter((p) => scr(p).x1 + p.w * view.k * 0.7 > right);
      if (offB.length) down.push({ t: "서비스 " + offB.length + "개", rs: offB });
      if (offR.length) side.push({ t: "서비스 " + offR.length + "개", rs: offR });
    }
    const union = (list) => { const rs = list.flatMap((x) => x.rs); const x1 = Math.min(...rs.map((p) => p.x)); const y1 = Math.min(...rs.map((p) => p.y)); return { x: x1, y: y1, w: Math.max(...rs.map((p) => p.x + p.w)) - x1, h: Math.max(...rs.map((p) => p.y + p.h)) - y1 }; };
    offsInfo = { down: down.length ? union(down) : null, right: side.length ? union(side) : null };
    const btn = (dir, list, arrow, what) => html`<button type="button" class="${"offs-b offs-" + dir}" data-offs="${dir}" data-fk="${"offs:" + dir}" aria-label="${what + " 화면 밖: " + list.map((x) => x.t).join(", ") + " — 누르면 그쪽으로 옮겨요"}" title="${list.map((x) => x.t).join(" · ")}"><span class="offs-a" aria-hidden="true">${arrow}</span><span class="offs-t">${list.map((x) => x.t).join(" · ")}</span></button>`;
    renderKeep(offsEl, html`${down.length ? btn("down", down, "↓", "아래") : ""}${side.length ? btn("right", side, "→", "오른쪽") : ""}`);
    offsEl.hidden = !down.length && !side.length;
    offsBounds = { bottom, right };
    placeOffs();
  }
  /** 화면 밖 칩이 고른 노드나 자기가 가리키는 노드를 덮지 않게: 오른쪽 칩은 위아래로, 아래 칩은 좌우로 비켜 선다.
   *  비킬 자리가 없으면 화살표만 남긴다(이름은 aria-label·title 에 그대로). */
  let offsBounds = null;
  function placeOffs() {
    if (offsEl.hidden || !G.layout || !offsInfo || !offsBounds) return;
    const gr = graph.getBoundingClientRect();
    const pad = 10;
    const visR = (q) => ({ x1: Math.max(0, q.x1), y1: Math.max(0, q.y1), x2: Math.min(gr.width, q.x2), y2: Math.min(gr.height, q.y2) });
    const selP = ui.selected && G.layout.pos.get(ui.selected);
    const selR = selP ? [visR(scr(selP))] : [];
    const hitsAny = (r, list) => list.some((a) => a.x2 > a.x1 && a.y2 > a.y1 && r.x2 + pad > a.x1 && r.x1 - pad < a.x2 && r.y2 + pad > a.y1 && r.y1 - pad < a.y2);
    const leftEdge = sideShown() && !mobile ? Math.max(8, side.getBoundingClientRect().right - gr.left + 8) : 8;
    // 화면에 보이는 작은 노드(상자·묶음처럼 큰 것은 빼고)
    const others = [...G.layout.pos.values()].map((p) => visR(scr(p))).filter((q) => q.x2 > q.x1 && q.y2 > q.y1 && q.x2 - q.x1 < 420 && q.y2 - q.y1 < 160);
    for (const b of offsEl.querySelectorAll(".offs-b")) {
      const dir = b.getAttribute("data-offs");
      // 이 칩이 가리키는 노드 중 화면에 걸쳐 보이는 것
      const tgt = dir === "right" ? offsInfo.right : offsInfo.down;
      const tgtR = tgt ? [...G.layout.pos.values()].filter((p) => p.x + p.w > tgt.x - 1 && p.x < tgt.x + tgt.w + 1 && p.y + p.h > tgt.y - 1 && p.y < tgt.y + tgt.h + 1).map((p) => visR(scr(p))) : [];
      const prop = dir === "right" ? "--offs-dy" : "--offs-dx";
      const tryAt = (mini, avoid) => {
        if (mini) b.setAttribute("data-mini", "1"); else b.removeAttribute("data-mini");
        b.style.setProperty(prop, "0px");
        const r0 = b.getBoundingClientRect();
        const base = { x1: r0.left - gr.left, y1: r0.top - gr.top, x2: r0.right - gr.left, y2: r0.bottom - gr.top };
        const lo = dir === "right" ? 64 - base.y1 : leftEdge - base.x1;
        const hi = dir === "right" ? offsBounds.bottom - 8 - base.y2 : offsBounds.right - base.x2;
        for (let i = 0; i <= 24; i++) {
          for (const sg of i ? [1, -1] : [1]) {
            const d = sg * i * 24;
            if (d < lo || d > hi) continue;
            const r = dir === "right" ? { x1: base.x1, x2: base.x2, y1: base.y1 + d, y2: base.y2 + d } : { x1: base.x1 + d, x2: base.x2 + d, y1: base.y1, y2: base.y2 };
            if (!hitsAny(r, avoid)) { b.style.setProperty(prop, d + "px"); return true; }
          }
        }
        return false;
      };
      // 고른 노드는 반드시 비킨다 · 가리키는 노드는 되도록 · 다른 작은 노드도 되면 비킨다. 안 되면 숨긴다(고른 노드를 가리지 않는 것이 먼저)
      const both = selR.concat(tgtR);
      const ok = tryAt(false, both.concat(others)) || tryAt(false, both) || tryAt(true, both) || tryAt(false, selR) || tryAt(true, selR);
      if (!ok) b.style.setProperty(prop, "0px");
      if (b.hidden === ok) b.hidden = !ok;
    }
  }
  offsEl.addEventListener("click", (e) => {
    const b = e.target.closest("[data-offs]");
    if (!b || !offsInfo) return;
    const r = offsInfo[b.getAttribute("data-offs")];
    if (r) revealRect({ x: r.x - 16, y: r.y - 16, w: r.w + 32, h: r.h + 32 });
  });
  /** 왼쪽 판·검사기·도구 막대가 지도를 얼마나 가리는지에 따라 틀의 모드를 정한다. */
  function updateChrome() {
    if (mobile) { app.dataset.stack = "0"; app.dataset.crowd = "0"; app.dataset.strip = "0"; return; }
    const W = stage.clientWidth;
    const insOpen = app.dataset.insp === "open";
    const stack = ui.side && insOpen && W < 1180;
    setAttr(app, "data-stack", stack ? "1" : "0");
    const sideOpen = ui.side && !stack;
    // 목록이 접혀 있으면 왼쪽 위 요약은 한 줄 띠(관점 · 요약 칸 + 목록 탭) — 지도 위를 덜 가린다
    setAttr(app, "data-strip", sideOpen ? "0" : "1");
    const free = W - (sideOpen ? side.offsetWidth + 12 : 0) - (insOpen ? inspector.offsetWidth + 12 : 0);
    setAttr(app, "data-crowd", free < 760 ? "2" : free < 1240 ? "1" : "0");
    const tb = toolbar.offsetHeight;
    if (tb) app.style.setProperty("--m-tb-h", tb + "px");
    const tw = toolbar.offsetWidth;
    if (tw) app.style.setProperty("--m-tb-w", tw + "px");
    // 띠가 두 줄로 접히면 관점 버튼의 부제·칸 제목을 빼서 한 줄로(재 보고 정한다 — 오가며 흔들리지 않게 매번 넓은 상태부터)
    //   그래도 접히면(좁은 태블릿) 첫 줄 = 관점 버튼 + 목록 펼치기, 둘째 줄 = 요약 칸
    if (!sideOpen) {
      setAttr(app, "data-tight", "0");
      if (side.offsetHeight > 60) { setAttr(app, "data-tight", "1"); if (side.offsetHeight > 60) setAttr(app, "data-tight", "2"); }
    } else setAttr(app, "data-tight", "0");
  }
  const sideShown = () => ui.side && !mobile && app.dataset.stack !== "1";
  function insets(r) {
    let l = 16;
    if (sideShown()) l = side.offsetWidth + 12 + 16;
    let right = 18;
    if (app.dataset.insp === "open" && !mobile) right += inspector.offsetWidth + 12;
    let top = toolbar.getBoundingClientRect().bottom - r.top + 14;
    // 접힌 요약 띠도 위를 가린다
    if (!sideShown()) { const sb = side.getBoundingClientRect(); if (sb.height) top = Math.max(top, sb.bottom - r.top + 14); }
    return { l, r: right, t: Math.max(56, top), b: 60 };
  }
  /** 지도 위에 떠 있는 판들(도구 막대·접힌 왼쪽 판·설명 띠)의 자리 — 무대 좌표. 가로로 겹치는 노드만 비켜 간다. */
  function obstacles(r, withSide) {
    const out = [];
    const add = (el, where) => {
      if (!el || el.hidden) return;
      const a = el.getBoundingClientRect();
      if (!a.width || !a.height) return;
      out.push({ left: a.left - r.left, right: a.right - r.left, top: a.top - r.top, bottom: a.bottom - r.top, where });
    };
    add(toolbar, "top");
    if (withSide) add(side, "top");
    if (getComputedStyle(caption).display !== "none") add(caption, "bottom");
    return out;
  }
  /** 판에 가리면 안 되는 것들: 노드·영역 + 개념의 '이 기기 안' 상자 테두리(위·아래 선 전체와 제목) */
  function fitItems() {
    const items = [...G.layout.pos.values()].concat(G.layout.regions);
    if (G.layout.box) { const bx = G.layout.box; items.push({ x: bx.x, y: bx.y, w: 200, h: 34 }, { x: bx.x, y: bx.y, w: bx.w, h: 4 }, { x: bx.x, y: bx.y + bx.h - 4, w: bx.w, h: 4 }); }
    return items;
  }
  /** 왼쪽 여백 L0 에서, 판들을 비켜 가며 담을 수 있는 가장 큰 배율과 위치. target(월드 사각형)을 주면 그것만 담는다(나머지는 넘쳐도 된다). */
  function solveFit(r, L0, R0, obs, target) {
    const T0 = 14; const B0 = 14;
    const b = target || G.layout.bounds;
    const aw = r.width - L0 - R0; const ah = r.height - T0 - B0;
    if (aw < 60 || ah < 60) return null;
    let k = clamp(Math.min(aw / b.w, ah / b.h), 0.3, r.width > 2200 ? 1.4 : 1.15);
    const items = fitItems().filter((p) => !target || (p.x < b.x + b.w && p.x + p.w > b.x && p.y < b.y + b.h && p.y + p.h > b.y));
    let tx = 0; let ty = 0;
    for (let i = 0; i < 18; i++) {
      tx = L0 + (aw - b.w * k) / 2 - b.x * k;
      let lo = T0 - b.y * k; let hi = r.height - B0 - (b.y + b.h) * k;
      for (const o of obs) {
        for (const p of items) {
          const x1 = tx + p.x * k; const x2 = tx + (p.x + p.w) * k;
          if (x2 < o.left - 8 || x1 > o.right + 8) continue;
          if (o.where === "top") lo = Math.max(lo, o.bottom + 10 - p.y * k);
          else hi = Math.min(hi, o.top - 8 - (p.y + p.h) * k);
        }
      }
      if (lo <= hi || k <= 0.32) { ty = lo <= hi ? (lo + hi) / 2 : lo; break; }
      k *= 0.96;
    }
    return { k, tx, ty, aw, L0, obs };
  }
  /** 배율 k·가로 위치 tx 에서, 위에 떠 있는 판(도구 막대·요약 띠)과 가로로 겹치는 것들을 그 아래로 내리는 가장 위 ty */
  function topClear(k, tx, obs) {
    const b = G.layout.bounds;
    const items = fitItems();
    let lo = 14 - b.y * k;
    for (const o of obs) {
      if (o.where !== "top") continue;
      for (const p of items) {
        const x1 = tx + p.x * k; const x2 = tx + (p.x + p.w) * k;
        if (x2 < o.left - 8 || x1 > o.right + 8) continue;
        lo = Math.max(lo, o.bottom + 10 - p.y * k);
      }
    }
    return lo;
  }
  function syncFitBtn(kAll) {
    const b = $("#zoom-fit");
    const toRead = view.auto && view.fitAll && kAll < floorK();
    const t = toRead ? "읽기 좋은 크기로 (0)" : "지도 전체 보기 (0)";
    setAttr(b, "title", t);
    setAttr(b, "aria-label", t.replace(/ \(0\)$/, ""));
    setAttr(b, "data-mode", toRead ? "read" : "all");
  }
  function fitView(animate) {
    if (!G.layout || mobile) return;
    updateChrome();
    const r = graph.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const R0 = 18 + (app.dataset.insp === "open" ? inspector.offsetWidth + 12 : 0);
    const L0 = sideShown() ? side.offsetWidth + 12 + 16 : 16;
    const obs = obstacles(r, !sideShown());
    const best = solveFit(r, L0, R0, obs);
    if (!best) return;
    let { k, tx, ty } = best;
    const b = G.layout.bounds;
    view.floored = false;
    if (k < floorK()) {
      // 전체를 담으면 글자를 못 읽는다: 선택이 있으면 그 길만, 아니면 왼쪽 위를 읽을 수 있는 배율로(전체 보기는 맞춤 버튼·0 키)
      if (ui.selected && G.layout.pos.has(ui.selected)) { focusView(ui.selected, animate, true); syncFitBtn(best.k); return; }
      if (!view.fitAll) {
        const aw = r.width - L0 - R0;
        const pr = isPhys() ? G.layout.prio : null;
        if (pr) {
          // 물리: 이 기기 상자(+ 위의 인터넷·Cloudflare·LAN)를 담을 수 있는 가장 큰 배율(최저 배율 ~ 100%) —
          // 같은 LAN 기기 열·Tailnet 버스·아래 줄은 가장자리로 넘치고 →/↓ 안내 칩이 알려 준다
          const pf = solveFit(r, L0, R0, obs, pr);
          k = clamp(pf ? pf.k : floorK(), floorK(), 1);
          tx = b.w * k <= aw ? L0 + (aw - b.w * k) / 2 - b.x * k : L0 - Math.min(b.x, pr.x) * k;
        } else {
          k = floorK();
          tx = b.w * k <= aw ? L0 + (aw - b.w * k) / 2 - b.x * k : L0 - b.x * k;
        }
        ty = topClear(k, tx, obs);
        view.floored = true;
      }
    }
    view.k = k; view.tx = tx; view.ty = ty;
    view.auto = true;
    applyView(animate);
    syncFitBtn(best.k);
  }
  /** 맞춤 버튼·0 키: 읽기 크기 ↔ 지도 전체. 손으로 옮긴 뒤라면 지금 설정대로 다시 맞춘다. */
  function fitToggle() {
    if (view.auto && view.floored) view.fitAll = true;
    else if (view.auto && view.fitAll) view.fitAll = false;
    fitView(true);
  }
  /** 노드 하나(와 그곳까지의 길)를 읽을 수 있는 크기로 가운데에. keepAuto 면 이후 갱신·창 크기 변화에도 다시 맞춘다. */
  function focusView(id, animate, keepAuto) {
    if (!G.layout || mobile) return;
    const sel = G.layout.pos.get(id);
    if (!sel) return;
    updateChrome();
    const r = graph.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const ins = insets(r);
    const aw = r.width - ins.l - ins.r; const ah = r.height - ins.t - ins.b;
    if (aw < 60 || ah < 60) return;
    // 물리: 노드 + 강조되는 것(조상 상자 머리·테두리 인터페이스·구간·인터넷)을 담되 100% 를 넘기지 않는다.
    //       다 담으면 최저 배율 아래로 떨어지면 노드 + 기기 제목·인터페이스 줄만 담고 옮긴다(확대하지 않는다).
    let ps; let kMax = 1.15;
    if (isPhys() && PD) {
      const fr = PHYS.focusRects(PD, G.layout, id);
      const fits = (list) => { const w = Math.max(...list.map((p) => p.x + p.w)) - Math.min(...list.map((p) => p.x)) + 48; const hh = Math.max(...list.map((p) => p.y + p.h)) - Math.min(...list.map((p) => p.y)) + 48; return Math.min(aw / w, ah / hh); };
      ps = fr.all.length && fits(fr.all) >= floorK() ? fr.all : fr.near;
      kMax = 1;
    } else ps = [...pathSet(id).N].map((x) => G.layout.pos.get(x)).filter(Boolean);
    if (!ps.length) return;
    const x1 = Math.min(...ps.map((p) => p.x)) - 24; const x2 = Math.max(...ps.map((p) => p.x + p.w)) + 24;
    const y1 = Math.min(...ps.map((p) => p.y)) - 24; const y2 = Math.max(...ps.map((p) => p.y + p.h)) + 24;
    let k = clamp(Math.min(aw / (x2 - x1), ah / (y2 - y1)), isPhys() ? floorK() : 0.68, kMax);
    const fitsX = (x2 - x1) * k <= aw; const fitsY = (y2 - y1) * k <= ah;
    const cxw = fitsX ? (x1 + x2) / 2 : null; const cyw = fitsY ? (y1 + y2) / 2 : sel.y + sel.h / 2;
    // 길이 가로로 다 안 들어가면: 선택 노드를 오른쪽 끝 쪽에 두고, 들어오는 길을 왼쪽으로 최대한 보인다.
    view.tx = cxw != null ? ins.l + aw / 2 - cxw * k : ins.l + aw - Math.max(x2, sel.x + sel.w + 24) * k;
    view.ty = ins.t + ah / 2 - cyw * k;
    view.k = k;
    view.auto = !!keepAuto;
    clampView();
    applyView(animate);
  }
  function clampView() {
    if (!G.layout) return;
    const r = graph.getBoundingClientRect(); const b = G.layout.bounds; const m = 120;
    const minTx = m - (b.x + b.w) * view.k; const maxTx = r.width - m - b.x * view.k;
    const minTy = m - (b.y + b.h) * view.k; const maxTy = r.height - m - b.y * view.k;
    view.tx = clamp(view.tx, Math.min(minTx, maxTx), Math.max(minTx, maxTx));
    view.ty = clamp(view.ty, Math.min(minTy, maxTy), Math.max(minTy, maxTy));
  }
  function zoomAt(cx, cy, f, animate) {
    const r = graph.getBoundingClientRect();
    const px = cx - r.left; const py = cy - r.top;
    const k2 = clamp(view.k * f, 0.3, 2.4);
    view.tx = px - (px - view.tx) * (k2 / view.k);
    view.ty = py - (py - view.ty) * (k2 / view.k);
    view.k = k2; view.auto = false;
    clampView(); applyView(animate);
  }
  function zoomCenter(f) {
    const r = graph.getBoundingClientRect(); const ins = insets(r);
    zoomAt(r.left + ins.l + (r.width - ins.l - ins.r) / 2, r.top + ins.t + (r.height - ins.t - ins.b) / 2, f, true);
  }
  /** 노드가 판 뒤에 가려져 있으면 보이는 곳으로 옮긴다(크기는 그대로). */
  function revealNode(id) {
    if (mobile || !G.layout) return;
    const p = G.layout.pos.get(id);
    if (!p) return;
    const r = graph.getBoundingClientRect(); const ins = insets(r);
    const sx = view.tx + (p.x + p.w / 2) * view.k; const sy = view.ty + (p.y + p.h / 2) * view.k;
    const inside = sx > ins.l + 20 && sx < r.width - ins.r - 20 && sy > ins.t + 10 && sy < r.height - ins.b - 10;
    if (inside) return;
    // 맞춤이 지도 전체를 담고 있을 때만 다시 맞춘다(읽기 크기로 일부만 담은 맞춤이면 그 노드 쪽으로 옮긴다)
    if (view.auto && !ui.selected && !view.floored) { fitView(true); return; }
    view.tx = ins.l + (r.width - ins.l - ins.r) / 2 - (p.x + p.w / 2) * view.k;
    view.ty = ins.t + (r.height - ins.t - ins.b) / 2 - (p.y + p.h / 2) * view.k;
    view.auto = false; clampView(); applyView(true);
  }

  /** 사각형(월드 좌표)이 판들에 가리지 않게 필요한 만큼만 옮긴다(크기는 그대로) */
  function revealRect(p) {
    if (mobile || !p || !G.layout) return;
    const r = graph.getBoundingClientRect(); const ins = insets(r);
    const x1 = view.tx + p.x * view.k; const x2 = view.tx + (p.x + p.w) * view.k;
    const y1 = view.ty + p.y * view.k; const y2 = view.ty + (p.y + p.h) * view.k;
    let dx = 0; let dy = 0;
    if (y2 > r.height - ins.b) dy = r.height - ins.b - y2;
    if (y1 + dy < ins.t) dy = ins.t - y1;
    if (x2 > r.width - ins.r) dx = r.width - ins.r - x2;
    if (x1 + dx < ins.l) dx = ins.l - x1;
    if (!dx && !dy) return;
    view.tx += dx; view.ty += dy; view.auto = false;
    clampView(); applyView(true);
  }

  // 끌기(이동) · 두 손가락(확대) · 휠
  const ptrs = new Map();
  let drag = null; let pinch = null; let suppressClick = false;
  graph.addEventListener("pointerdown", (e) => {
    if (mobile || (e.pointerType === "mouse" && e.button !== 0)) return;
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (ptrs.size === 2) {
      const [a, b] = [...ptrs.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, k: view.k, mx: (a.x + b.x) / 2, my: (a.y + b.y) / 2, tx: view.tx, ty: view.ty };
      drag = null;
      return;
    }
    drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty, moved: false, id: e.pointerId };
  });
  graph.addEventListener("pointermove", (e) => {
    if (ptrs.has(e.pointerId)) ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pinch && ptrs.size >= 2) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const r = graph.getBoundingClientRect();
      const k2 = clamp(pinch.k * (d / pinch.d), 0.3, 2.4);
      const mx = (a.x + b.x) / 2 - r.left; const my = (a.y + b.y) / 2 - r.top;
      const ox = pinch.mx - r.left; const oy = pinch.my - r.top;
      view.tx = mx - (ox - pinch.tx) * (k2 / pinch.k);
      view.ty = my - (oy - pinch.ty) * (k2 / pinch.k);
      view.k = k2; view.auto = false; suppressClick = true;
      clampView(); applyView(false);
      return;
    }
    if (drag && drag.id === e.pointerId) {
      const dx = e.clientX - drag.x; const dy = e.clientY - drag.y;
      if (!drag.moved && Math.hypot(dx, dy) > 5) { drag.moved = true; try { graph.setPointerCapture(e.pointerId); } catch (er) { /* 무시 */ } graph.classList.add("panning"); }
      if (drag.moved) { view.tx = drag.tx + dx; view.ty = drag.ty + dy; view.auto = false; clampView(); applyView(false); }
      return;
    }
    if (e.pointerType === "touch") return;
    const nEl = e.target.closest ? e.target.closest(".n") : null;
    const gEl = !nEl && e.target.closest ? e.target.closest(".p-ghost") : null;
    setHover(nEl ? nEl.getAttribute("data-id") : gEl ? gEl.getAttribute("data-ghost") : null);
  });
  const endPtr = (e) => {
    ptrs.delete(e.pointerId);
    if (ptrs.size < 2) pinch = null;
    if (drag && drag.id === e.pointerId) {
      if (drag.moved) { suppressClick = true; }
      drag = null; graph.classList.remove("panning");
    }
    if (suppressClick) setTimeout(() => { suppressClick = false; }, 0);
  };
  graph.addEventListener("pointerup", endPtr);
  graph.addEventListener("pointercancel", endPtr);
  graph.addEventListener("pointerleave", () => { if (!drag) setHover(null); });
  graph.addEventListener("click", (e) => {
    if (suppressClick) return;
    const mEl = e.target.closest(".n-more");
    if (mEl) { setExpanded(mEl.getAttribute("data-more"), mEl.getAttribute("data-open") !== "1"); return; }
    const gEl = e.target.closest(".p-ghost");
    if (gEl) { select(gEl.getAttribute("data-ghost"), { reveal: true }); return; }
    const nEl = e.target.closest(".n");
    if (nEl) select(nEl.getAttribute("data-id"), { invoker: nEl, reveal: true });
    else if (ui.selected) select(null);
  });
  graph.addEventListener("keydown", (e) => {
    const mEl = e.target.closest && e.target.closest(".n-more");
    if (mEl && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setExpanded(mEl.getAttribute("data-more"), mEl.getAttribute("data-open") !== "1"); return; }
    const nEl = e.target.closest && e.target.closest(".n");
    if (nEl && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); select(nEl.getAttribute("data-id"), { invoker: nEl, focus: true, reveal: true }); }
  });
  graph.addEventListener("focusin", (e) => { const nEl = e.target.closest && e.target.closest(".n"); if (nEl) { setHover(nEl.getAttribute("data-id")); revealNode(nEl.getAttribute("data-id")); } });
  graph.addEventListener("focusout", () => setHover(null));
  graph.addEventListener("wheel", (e) => {
    if (mobile) return;
    e.preventDefault();
    if (e.ctrlKey || e.metaKey) zoomAt(e.clientX, e.clientY, Math.exp(-e.deltaY * 0.0025), false);
    else { view.tx -= e.deltaX; view.ty -= e.deltaY; view.auto = false; clampView(); applyView(false); }
  }, { passive: false });
  $("#zoom-in").addEventListener("click", () => zoomCenter(1.2));
  $("#zoom-out").addEventListener("click", () => zoomCenter(1 / 1.2));
  $("#zoom-fit").addEventListener("click", fitToggle);
  let roT = 0;
  if ("ResizeObserver" in window) {
    // 무대 크기뿐 아니라 떠 있는 판(왼쪽 위 띠·도구 막대)의 크기가 바뀌어도(3초 갱신으로 띠가 늘어나는 등) 다시 맞춘다.
    // 설명 띠는 올릴 때마다 커지므로 보지 않는다(가리킬 때마다 지도가 움직이면 안 된다).
    const ro = new ResizeObserver(() => {
      if (roT) return;
      roT = requestAnimationFrame(() => { roT = 0; if (mobile) return; if (view.auto) fitView(false); else { updateChrome(); clampView(); applyView(false); } });
    });
    ro.observe(stage); ro.observe(side); ro.observe(toolbar);
  }

  for (const b of $$(".layer", toolbar)) {
    b.addEventListener("click", () => {
      const k = b.dataset.kind;
      ui.layers[k] = !ui.layers[k];
      store.set("layers", ui.layers);
      applyLayers();
    });
  }
  function setLegend(open) {
    ui.legend = !!open;
    legend.hidden = !ui.legend || mobile;
    setAttr(legendBtn, "aria-expanded", ui.legend ? "true" : "false");
  }
  legendBtn.addEventListener("click", () => setLegend(!ui.legend));
  document.addEventListener("pointerdown", (e) => {
    if (ui.legend && !legend.contains(e.target) && !legendBtn.contains(e.target)) setLegend(false);
  });

  // ------------------------------------------------------------------ 선택·검사기·주소(#focus)
  /** 개념 노드 → 주소 키(서비스 id, 없으면 노드 id) */
  const conceptKey = (nid) => { const n = D && D.nodes.get(nid); return (n && n.service_id) || nid; };
  /** 지금 관점의 노드 → 주소 키. 서비스를 돌리는 물리 노드는 그 서비스 id 로 — 두 관점이 같은 #focus 를 쓴다. */
  function focusKey(nid) {
    if (!isPhys()) return conceptKey(nid);
    const n = PD && PD.nodes.get(nid);
    if (n && (n.service_ids || []).length) return n.service_ids[0];
    return aliasC(nid) || nid;
  }
  function resolveKey(key) {
    if (!key) return null;
    if (isPhys()) return toPhys(key);
    if (!D) return null;
    if (D.nodes.has(key)) return key;
    if (D.svcNode.has(key)) return D.svcNode.get(key);
    return toConcept(key);
  }
  function syncHash(id) {
    const want = id ? "#focus=" + encodeURIComponent(focusKey(id)) : "";
    if (location.hash === want) return;
    if (!id && !/^#focus=/.test(location.hash)) return;
    try { history.replaceState(history.state, "", location.pathname + location.search + want); } catch (e) { /* 무시 */ }
  }
  function readFocusHash() {
    const m = /^#focus=(.+)$/.exec(location.hash);
    if (!m) return null;
    try { return decodeURIComponent(m[1]); } catch (e) { return m[1]; }
  }
  /** #focus=<id> 적용. 데이터가 아직 없으면 첫 스냅샷 뒤로 미룬다. */
  function applyFocus(key) {
    if (!D || (isPhys() && !PD)) { pendingFocus = key; return; }
    const nid = resolveKey(key);
    if (!nid) {
      const k = String(key);
      const q = "(“" + (k.length > 48 ? k.slice(0, 48) + "…" : k) + "”)";
      // 다른 관점에만 있는 항목이면 그렇게 알려 준다
      const other = isPhys() ? (D && (D.nodes.has(key) || D.svcNode.has(key))) : (PD && PD.nodes.has(key));
      toast(other ? (isPhys() ? "물리 지도에는 따로 그리지 않는 항목이에요 — 개념 보기에서 볼 수 있어요 " : "개념 지도에는 없는 항목이에요 — 물리 보기에서 볼 수 있어요 ") + q : "지도에서 찾을 수 없는 항목이에요 " + q);
      syncHash(ui.selected);
      return;
    }
    // 정적 라우트는 물리 지도에 따로 칩이 없다 — 그 응답을 만드는 Caddy 를 골랐다고 알린다
    const ssv = isPhys() && S ? (S.services || []).find((x) => x.id === key) : null;
    toast(isStatic(ssv) ? ssv.name + " — Caddy 가 직접 응답하는 정적 라우트라 응답하는 Caddy 를 보여 줘요" : "");
    select(nid, { focus: true, fromHash: true });
    if (mobile) {
      requestAnimationFrame(() => {
        const leaf = flowEl.querySelector(`[data-select="${cssEsc(nid)}"]`);
        if (leaf) leaf.scrollIntoView({ block: "center", behavior: "auto" });
      });
    } else requestAnimationFrame(() => focusView(nid, !calm()));
  }
  // 뒤로·주소 직접 수정·지도 안 링크: #focus 가 있으면 그곳으로, 없어지면 검사기를 닫는다(개요의 #svc 시트와 같은 규칙)
  window.addEventListener("hashchange", () => { const k = readFocusHash(); if (k) applyFocus(k); else if (ui.selected) select(null); });
  // 뒤로·앞으로: 주소의 관점(?view)이 다르면 관점부터 맞추고, #focus 를 따른다(쿼리가 다른 기록 사이에서는 hashchange 가 안 온다)
  window.addEventListener("popstate", () => {
    const v = viewFromUrl();
    const k = readFocusHash();
    if (v && PHYS && v !== ui.view) { setView(v, { select: k || null, fromHistory: true }); if (!k && ui.selected) select(null); return; }
    if (k) applyFocus(k); else if (ui.selected) select(null);
  });

  function select(id, opts) {
    opts = opts || {};
    if (id && !curHas(id)) return;
    const was = ui.selected;
    ui.selected = id;
    ui.selKey = id ? focusKey(id) : null;
    if (id && !mobile && ensureVisible(id)) renderAll();
    if (id && mobile && isPhys()) treeReveal(id);
    if (id) {
      if (opts.invoker) lastInvoker = opts.invoker;
      if (app.dataset.insp !== "open") panelAnim();
      app.dataset.insp = "open";
      if (!mobile) updateChrome();
      renderInspector();
      if (mobile) {
        backdrop.hidden = false;
        document.body.classList.add("sheet-open");
        inspector.setAttribute("role", "dialog");
        inspector.setAttribute("aria-modal", "true");
        inspH.focus({ preventScroll: true });
      } else if (opts.focus) inspH.focus({ preventScroll: true });
      if (!opts.fromHash && !mobile) {
        if (view.auto) requestAnimationFrame(() => fitView(true));
        else if (opts.reveal) requestAnimationFrame(() => revealNode(id));
      }
      if (was !== id) inspBody.scrollTop = 0;
      syncHash(id);
    } else {
      closeInspector();
      return;
    }
    applyHighlight();
    renderSide();
    if (mobile) renderFlow();
    placeOffs();
  }
  function closeInspector() {
    const had = app.dataset.insp === "open";
    const focusInside = inspector.contains(document.activeElement);
    if (had) panelAnim();
    ui.selected = null;
    app.dataset.insp = "closed";
    backdrop.hidden = true;
    document.body.classList.remove("sheet-open");
    inspector.removeAttribute("role");
    inspector.removeAttribute("aria-modal");
    inspector.style.transform = "";
    updateChrome();
    syncHash(null);
    if (had && view.auto) requestAnimationFrame(() => fitView(true));
    if (focusInside && lastInvoker && document.contains(lastInvoker)) lastInvoker.focus({ preventScroll: true });
    applyHighlight();
    renderSide();
    if (mobile) renderFlow();
  }
  $("#insp-close").addEventListener("click", () => select(null));
  backdrop.addEventListener("click", () => select(null));
  // 모바일 시트: 손잡이를 아래로 끌면 닫힌다. 열린 동안 Tab 은 시트 안에서만 돈다.
  {
    const head = $("#insp-head");
    let sy = null; let dy = 0;
    head.addEventListener("pointerdown", (e) => { if (!mobile || e.target.closest("button")) return; sy = e.clientY; dy = 0; try { head.setPointerCapture(e.pointerId); } catch (er) { /* 무시 */ } });
    head.addEventListener("pointermove", (e) => { if (sy == null) return; dy = Math.max(0, e.clientY - sy); inspector.style.transform = dy ? `translateY(${dy}px)` : ""; });
    const end = () => { if (sy == null) return; sy = null; if (dy > 90) select(null); else inspector.style.transform = ""; dy = 0; };
    head.addEventListener("pointerup", end);
    head.addEventListener("pointercancel", end);
    inspector.addEventListener("keydown", (e) => {
      if (e.key !== "Tab" || !mobile || app.dataset.insp !== "open") return;
      const f = $$('a[href], button:not([disabled]), input, [tabindex]:not([tabindex="-1"]), h2[tabindex]', inspector).filter((x) => x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0]; const last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); } else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
  }

  // ------------------------------------------------------------------ 왼쪽 판(상태 요약 + 목록)
  function setSide(open, tab) {
    if (tab && TABS.includes(tab) && tab !== ui.tab) {
      ui.tab = tab; store.set("tab", tab);
      sidePanel.scrollTop = 0; sideBody.scrollTop = 0;
    }
    if (ui.side !== !!open) panelAnim();
    ui.side = !!open;
    if (!EMBED) store.set("side", ui.side);
    app.dataset.side = ui.side ? "open" : "closed";
    syncTabs();
    renderTools();
    if (!mobile) updateChrome();
    renderSide();
    if (!mobile && view.auto) requestAnimationFrame(() => fitView(true));
  }
  function syncTabs() {
    const cur = tabNow();
    const expanded = mobile || ui.side;
    for (const b of $$(".stab", sideTabs)) setAttr(b, "aria-pressed", expanded && b.dataset.tab === cur ? "true" : "false");
    setAttr(sideToggle, "aria-expanded", ui.side ? "true" : "false");
    setAttr(sideToggle, "aria-label", ui.side ? "목록 접기" : "목록 펼치기");
    setAttr(sidePanel, "aria-label", TAB_LABEL[cur]);
  }
  sideTabs.addEventListener("click", (e) => {
    const b = e.target.closest(".stab");
    if (b) {
      const t = b.dataset.tab;
      if (!mobile && ui.side && ui.tab === t) setSide(false);
      else setSide(true, t);
      return;
    }
    if (e.target.closest("#side-toggle")) setSide(!ui.side);
  });

  function renderTools() {
    const t = tabNow();
    if (t === "services") {
      renderKeep(sideTools, html`<div class="tools-row">
<label class="search">${ic("search")}<span class="sr-only">서비스 찾기</span><input type="search" id="svc-q" data-fk="svc-q" placeholder="이름·주소·프로젝트로 찾기 ( / )" value="${ui.q}" autocomplete="off" spellcheck="false"></label>
<button type="button" class="chip" data-act="problems" data-fk="problems" aria-pressed="${ui.problems ? "true" : "false"}">문제만</button>
</div>`);
    } else renderKeep(sideTools, "");
    sideTools.hidden = !(t === "services" && (ui.side || mobile));
  }
  sideTools.addEventListener("input", (e) => { if (e.target.id === "svc-q") { ui.q = e.target.value; renderSide(); } });

  /** 공개 상태 요약(늘 보인다) */
  function renderStatus() {
    const sm = S.summary || {}; const sv = sm.services || {};
    const t = tone(sm.status);
    const cnt = {}; for (const L of LEVELS) cnt[L.id] = 0;
    for (const x of S.services || []) cnt[expLevel(x)]++;
    const probs = (S.services || []).filter((x) => x.status !== "up");
    // 개념: 공개 상태 다섯 칸 · 물리: 기기·네트워크 구성 여섯 칸(가리키면 지도에서 밝히고, 누르면 상세)
    //   (관점 단추는 접힌 띠에서 늘 같은 크기라, 칸 수가 달라도 누른 단추는 흔들리지 않는다)
    const physSet = PHYS && PD ? html`<p class="ss-cap">기기·네트워크 <span>이 기기와 tailnet 기준</span></p>
<ul class="ss-levels ss-phys">${PHYS.summary(PD).map((c) => html`<li><button type="button" class="lvl" data-lvl="${c.id}" data-k="${c.k}" data-tone="${c.tone}" data-fk="${"pc:" + c.id}" aria-label="${c.label + " " + c.val + (c.tone === "warn" || c.tone === "crit" ? " — " + statusText(c.tone === "crit" ? "down" : "degraded") : "") + " — " + c.sub + ". 누르면 상세"}"><b>${c.val}</b><span>${c.label}</span>${c.tone === "warn" || c.tone === "crit" ? shape(c.tone) : ""}</button></li>`)}</ul>` : "";
    const conceptSet = html`<p class="ss-cap">공개 상태 <span>가장 넓게 열린 범위 기준</span></p>
<ul class="ss-levels">${LEVELS.map((L) => html`<li><button type="button" class="lvl" data-lvl="${L.id}" data-k="${L.id}" data-fk="${"lvl:" + L.id}" aria-label="${(L.long || L.label) + " " + cnt[L.id] + "개 — " + L.sub + ". 노출 목록에서 보기"}"><b>${fmt.num(cnt[L.id])}</b><span>${L.label}</span></button></li>`)}</ul>`;
    const ph = isPhys() && !!physSet;
    const cells = html`<div class="ss-set" data-set="${ph ? "physical" : "concept"}">${ph ? physSet : conceptSet}</div>`;
    const worstT = probs.some((x) => x.status === "down") ? "crit" : probs.length ? "warn" : "ok";
    // 띠(목록 접힘)에서는 문제를 알약 하나로(띠 높이가 3초 갱신에 따라 늘었다 줄었다 하지 않게) — 누르면 목록의 '문제만'
    const probPill = probs.length ? html`<button type="button" class="ss-probpill" data-act="probs" data-fk="probpill" data-tone="${worstT}" aria-label="${"확인 필요 서비스 " + probs.length + "개 — 목록에서 보기"}">${shape(worstT)}확인 필요 <b>${probs.length}</b></button>` : "";
    renderKeep(ssBody, html`<div class="ss-top"><p class="ss-state" data-tone="${t}">${shape(t)}<b>${labels.overall[sm.status] || "확인 중"}</b></p><p class="ss-svc">서비스 <b>${fmt.num(sv.up)}</b><span>/${fmt.num(sv.total)}</span> 정상</p></div>
${cells}${probPill}
${probs.length ? html`<p class="ss-probs"><span class="ss-probs-l">확인 필요</span>${probs.slice(0, 5).map((x) => { const nid = D.svcNode.get(x.id); const tt = tone(x.status); return nid ? html`<button type="button" class="ss-prob" data-select="${nid}" data-fk="${"prob:" + x.id}" data-tone="${tt}">${shape(tt)}${x.name}<span class="sr-only"> — ${statusText(x.status)}</span></button>` : ""; })}${probs.length > 5 ? html`<span class="ss-more">외 ${probs.length - 5}</span>` : ""}</p>` : ""}`);
    // 탭 숫자
    const al = sm.alerts || {};
    const n = (al.crit || 0) + (al.warn || 0);
    const setN = (k, v, tn) => { const b = $(`.stab-n[data-n="${k}"]`, sideTabs); if (!b) return; setText(b, v ? String(v) : ""); setAttr(b, "data-tone", tn || "muted"); };
    setN("services", (S.services || []).length, "muted");
    setN("exposure", cnt.public || 0, "muted");
    setN("alerts", n || (al.info || 0), al.crit ? "crit" : al.warn ? "warn" : "info");
    // (도구 막대의 레이어 버튼은 켜고 끄는 스위치라 숫자를 달지 않는다 — 서비스 수는 위 공개 상태 칸 하나로만 말한다)
  }
  // 공개 상태 칸: 올리면 지도에서 밝히고, 누르면 노출 목록의 그 칸으로
  ssBody.addEventListener("pointerover", (e) => { if (mobile || e.pointerType === "touch") return; const b = e.target.closest(".lvl"); if (b) setHover("lvl:" + b.dataset.lvl); });
  ssBody.addEventListener("pointerout", (e) => { const b = e.target.closest(".lvl"); const to = e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest(".lvl"); if (b && b !== to) setHover(to ? "lvl:" + to.dataset.lvl : null); });
  ssBody.addEventListener("focusin", (e) => { const b = e.target.closest(".lvl"); if (b && !mobile) setHover("lvl:" + b.dataset.lvl); });
  ssBody.addEventListener("focusout", (e) => { if (e.target.closest(".lvl")) setHover(null); });
  ssBody.addEventListener("click", (e) => {
    const b = e.target.closest(".lvl");
    if (!b) return;
    const lv = b.dataset.lvl;
    if (isPhys()) {
      const c = PD && PHYS.summary(PD).find((x) => x.id === lv);
      if (c && c.sel && curHas(c.sel)) select(c.sel, { invoker: b, reveal: true, focus: mobile });
      return;
    }
    setSide(true, "exposure");
    requestAnimationFrame(() => {
      const sec = sideBody.querySelector(`[data-sec="${cssEsc(lv)}"]`);
      if (sec) sec.scrollIntoView({ block: "start", behavior: calm() ? "auto" : "smooth" });
    });
  });

  let SEL = new Set(); // 지금 고른 것이 돌리는 서비스 id(목록 표시)
  function renderSide() {
    if (!S) return;
    SEL = selSvcs();
    if (!mobile && !sideShown()) return; // 접힌 목록은 그리지 않는다(열 때 바로 그린다)
    let body;
    switch (tabNow()) {
      case "services": body = panelServices(); break;
      case "exposure": body = panelExposure(); break;
      case "alerts": body = panelAlerts(); break;
      default: body = "";
    }
    renderKeep(sideBody, body);
  }

  function runtimeLine(rt) {
    if (!rt) return "";
    const bits = [labels.runtime[rt.type] || rt.type];
    if (rt.type === "remote") { if (rt.ip) bits.push(rt.ip); return bits.join(" · "); }
    if (rt.uptime_s != null) bits.push("가동 " + fmt.durShort(rt.uptime_s));
    if (rt.restart_count) bits.push("재시작 " + rt.restart_count);
    if (rt.cpu_pct != null) bits.push("CPU " + fmt.pct(rt.cpu_pct, 1));
    if (rt.mem_bytes != null) bits.push(fmt.bytes(rt.mem_bytes));
    return bits.join(" · ");
  }
  const svcRight = (sv) => {
    const lat = P.pick.latency(sv);
    if (lat != null) return fmt.ms(lat);
    return (sv.listeners || []).filter((l) => l.scope !== "loopback").map((l) => ":" + l.port).slice(0, 2).join(" ");
  };

  function svcRow(sv, fkp) {
    const nid = D.svcNode.get(sv.id);
    const t = tone(sv.status);
    const tr = sv.traffic;
    const sel = SEL.has(sv.id);
    return html`<li class="${"svc" + (sel ? " is-sel" : "")}" data-tone="${t}">
<button type="button" class="svc-btn" data-select="${nid || ""}" data-fk="${(fkp || "svc:") + sv.id}" aria-current="${sel ? "true" : "false"}">
<span class="svc-l1">${shape(t)}<span class="svc-name">${sv.name}</span>${sv.title && sv.title !== sv.name ? html`<span class="svc-title">${sv.title}</span>` : ""}<span class="svc-num">${svcRight(sv)}</span></span>
<span class="svc-l2">${sv.status !== "up" ? html`<span class="svc-why">${statusText(sv.status)}${sv.status_reason ? " — " + sv.status_reason : ""}</span>` : html`${xbadges(sv.exposure)}${tr && tr.requests_24h ? html`<span class="svc-rt">요청 24h <b>${fmt.num(tr.requests_24h)}</b>${tr.errors_5xx_24h ? html` · <span class="t-crit">5xx ${fmt.num(tr.errors_5xx_24h)}</span>` : ""}</span>` : sv.runtime ? html`<span class="svc-rt">${labels.runtime[sv.runtime.type] || sv.runtime.type}${sv.runtime.mem_bytes != null ? " · " + fmt.bytes(sv.runtime.mem_bytes) : ""}</span>` : ""}`}</span>
</button>
</li>`;
  }

  function panelServices() {
    const q = ui.q.trim().toLowerCase();
    const match = (sv) => {
      if (ui.problems && sv.status === "up") return false;
      if (!q) return true;
      return [sv.id, sv.name, sv.title, sv.description, sv.upstream, ...(sv.hostnames || []), sv.runtime && sv.runtime.project, sv.runtime && sv.runtime.name].filter(Boolean).some((x) => String(x).toLowerCase().includes(q));
    };
    const cats = (S.categories || []).map((c) => c.id);
    for (const sv of S.services || []) if (!cats.includes(sv.category)) cats.push(sv.category);
    const total = (S.services || []).filter(match).length;
    const groups = cats.map((cid) => {
      const c = (S.categories || []).find((x) => x.id === cid) || { id: cid, label: catName(cid) };
      const list = (S.services || []).filter((sv) => sv.category === cid && match(sv)).sort((a, b) => String(a.name).localeCompare(String(b.name)));
      const all = (S.services || []).filter((sv) => sv.category === cid);
      if (!list.length) return "";
      const up = all.filter((x) => x.status === "up").length;
      return html`<section class="sgroup" data-cat="${cid}">
<header class="sgroup-h"><h3><span class="cat-ic">${ic(CAT_ICON[cid] || "layers")}</span>${c.label || catName(cid)}</h3><span class="sg-count">${up}/${all.length} 정상</span></header>
${c.desc ? html`<p class="sgroup-d">${c.desc}</p>` : ""}
<ul class="svc-list">${list.map((sv) => svcRow(sv))}</ul>
</section>`;
    });
    return html`<p class="panel-note">${q || ui.problems ? html`조건에 맞는 서비스 <b>${total}</b>개` : html`이 기기의 서비스 <b>${(S.services || []).length}</b>개 · 카테고리 ${cats.length}개 — 누르면 지도에서 찾아 줍니다`}</p>${total || (!q && !ui.problems) ? groups : html`<p class="calm">${ic("okc")}<span>${ui.problems ? "문제가 있는 서비스가 없어요." : "찾는 서비스가 없어요."}</span></p>`}`;
  }

  /** 노출 목록 한 줄 */
  function xrow(sv) {
    const nid = D.svcNode.get(sv.id);
    const t = tone(sv.status);
    const lv = expLevel(sv);
    let sub = "";
    if (lv === "public" || lv === "tailnet") sub = (sv.hostnames || [])[0] || "";
    const ports = (sv.listeners || []).filter((l) => l.scope !== "loopback").map((l) => ":" + l.port);
    if (!sub) sub = ports.slice(0, 3).join(" ");
    if (!sub && sv.runtime) sub = labels.runtime[sv.runtime.type] || "";
    let right = "";
    // 어느 프로브의 숫자인지 붙인다: 공개 = Cloudflare 엣지 경유, Tailnet = Caddy :443 경유(지도 노드·서비스 목록의 숫자)
    if (lv === "public" && sv.probes && sv.probes.public) { const p = sv.probes.public; right = p.ok ? "공개 " + fmt.ms(p.ms) : p.fail_streak === 1 ? "재확인 중" : "공개 실패"; }
    else if (sv.probes && sv.probes.internal) { const p = sv.probes.internal; right = p.ok ? "Tailnet " + fmt.ms(p.ms) : p.fail_streak === 1 ? "재확인 중" : "Tailnet 실패"; }
    const sel = SEL.has(sv.id);
    return html`<li class="${"xrow" + (sel ? " is-sel" : "")}" data-tone="${t}"><button type="button" data-select="${nid || ""}" data-fk="${"x:" + sv.id}">${shape(t)}<span class="xr-n">${sv.name}</span><span class="xr-s">${sub}</span><span class="xr-r">${right}</span></button></li>`;
  }
  function gateCard(nid, title, icon, st, lines, note) {
    const t = tone(st);
    return html`<button type="button" class="${"gate" + (isSelC(nid) ? " is-sel" : "")}" data-select="${nid}" data-fk="${"gate:" + nid}" data-tone="${t}">
<span class="gate-h"><span class="gate-ic">${ic(icon)}</span><b>${title}</b>${shape(t)}</span>
<span class="gate-l">${lines}</span>${note || ""}</button>`;
  }
  function panelExposure() {
    const svcs = S.services || [];
    const by = {}; for (const L of LEVELS) by[L.id] = [];
    for (const sv of svcs) by[expLevel(sv)].push(sv);
    for (const k in by) by[k].sort((a, b) => (a.status === "up") - (b.status === "up") || String(a.name).localeCompare(String(b.name)));
    const ing = S.ingress || {}; const tn = ing.tunnel || {}; const cad = ing.caddy || {}; const ts = S.tailscale || {};
    const rtt = mean((tn.rtt_ms || []).filter(isNum));
    const mix = SH.tailnetMix ? SH.tailnetMix(S) : { text: "" };
    const gates = [];
    if (D.nodes.has("infra:cloudflared")) {
      gates.push(gateCard("infra:cloudflared", "Cloudflare Tunnel", "globe", tn.ready === false ? "down" : (D.nodes.get("infra:cloudflared").status || "up"),
        html`${tn.ready ? "준비됨" : tn.ready === false ? "준비 안 됨" : "–"} · 연결 <b>${fmt.num(tn.ready_connections)}/${fmt.num(tn.connections)}</b> · 엣지 ${(tn.edge_locations || []).join(" ") || "–"}${rtt != null ? html` · RTT <b>${Math.round(rtt)} ms</b>` : ""} · 분당 <b>${fmt.num(tn.req_per_min, 1)}</b>`,
        tn.outdated ? html`<span class="gate-note" data-tone="info">${shape("info")}cloudflared ${tn.outdated.current} → ${tn.outdated.latest} 업데이트 가능</span>` : ""));
    }
    if (D.nodes.has("infra:caddy")) {
      gates.push(gateCard("infra:caddy", "Caddy", "route", cad.config_ok === false ? "down" : (D.nodes.get("infra:caddy").status || "up"),
        html`${cad.version || "–"} · 라우트 <b>${fmt.num((cad.routes || []).length)}</b> (공개 ${(cad.public_hosts || []).length} · 비공개 ${(cad.private_hosts || []).length}) · ${cad.config_ok === false ? "설정 오류" : "설정 정상"}`,
        cad.config_ok === false && cad.error ? html`<span class="gate-note" data-tone="crit">${shape("crit")}${cad.error}</span>` : ""));
    }
    if (D.nodes.has("zone:tailnet")) {
      gates.push(gateCard("zone:tailnet", "Tailscale", "tailnet", ts.backend_state === "Running" ? "up" : ts.backend_state ? "down" : "unknown",
        html`${ts.backend_state || "–"} · 기기 <b>${fmt.num(ts.peers_online)}/${fmt.num(ts.peers_total)}</b> 온라인 · ${mix.text}`));
    }
    const tr = ing.traffic || {}; const w1 = tr.last_1h || {};
    const vias = Object.entries(w1.by_via || {}).filter(([, n]) => n).sort((a, b) => b[1] - a[1]);
    const VK = { tunnel: "public", tailnet: "tailnet", lan: "lan", docker: "local", local: "local" };
    const traffic = html`<div class="xtraffic"><p class="xt-h">최근 1시간 요청 <b>${fmt.num(w1.requests)}</b>${(w1.by_status || {})["5xx"] ? html` · <span class="t-crit">5xx ${fmt.num(w1.by_status["5xx"])}</span>` : ""}${tr.stream && tr.stream.connected === false ? html` · <span class="t-warn">로그 스트림 끊김</span>` : ""}</p>
${vias.length ? html`<span class="split" aria-hidden="true">${vias.map(([k, n]) => html`<i data-k="${VK[k] || "local"}" style="${"flex:" + n}"></i>`)}</span><p class="split-l">${vias.map(([k, n]) => html`<span data-k="${VK[k] || "local"}"><i></i>${labels.trafficVia[k] || k} <b>${fmt.num(n)}</b></span>`)}</p>` : ""}</div>`;

    const lanPorts = (S.listeners || []).filter((l) => l.scope === "all" || l.scope === "lan").sort((a, b) => a.port - b.port || String(a.proto).localeCompare(b.proto));
    const tsPorts = (S.listeners || []).filter((l) => l.scope === "tailnet").sort((a, b) => a.port - b.port);
    const portRows = (list, key) => html`<ul class="ports" data-sk="${key}">${list.map((l) => html`<li><b class="mono">${l.port}</b><span class="mono pt-p">${l.proto}</span><span class="pt-o">${l.process || "?"}${l.owner ? html` <span class="pt-w">· ${OWNER_L[l.owner_kind] || l.owner_kind || ""} ${l.owner}</span>` : ""}</span></li>`)}</ul>`;
    const sec = (L, extra) => {
      const list = by[L.id];
      if (!list.length && !extra) return "";
      return html`<section class="xsec" data-sec="${L.id}" data-k="${L.id}">
<header class="xsec-h"><h3><span class="lvl-ic">${ic(L.icon)}</span>${L.long || L.label}<span class="xsec-n">${list.length}</span></h3>${L.zone && D.nodes.has(L.zone) ? html`<button type="button" class="linkish" data-select="${L.zone}" data-fk="${"zsel:" + L.zone}">영역 보기</button>` : ""}</header>
<p class="xsec-d">${L.sub}${L.id === "public" ? " — 입구는 Cloudflare Tunnel 하나예요(공유기 포트포워딩은 Jarvis 가 확인하지 못해요)" : L.id === "lan" ? " — Caddy 를 거치지 않고 포트로 바로 닿아요" : L.id === "tailnet" ? " — *." + ((S && S.meta && S.meta.domain) || "<도메인>") + " 이 tailnet IP 를 가리켜요. 터널로 오면 404" : L.id === "local" ? " — 밖에서는 닿지 않아요" : ""}</p>
${list.length ? html`<ul class="xrows">${list.map(xrow)}</ul>` : ""}${extra || ""}
</section>`;
    };
    return html`<section class="xsec" data-sec="gates"><header class="xsec-h"><h3>들어오는 관문</h3></header><div class="gates">${gates}</div>${traffic}</section>
${sec(LEVEL.public)}
${sec(LEVEL.lan, lanPorts.length ? html`<p class="mini-h">LAN 전체에 열린 포트 <span>${lanPorts.length}</span></p>${portRows(lanPorts, "lanp")}` : "")}
${sec(LEVEL.tailnet, tsPorts.length ? html`<p class="mini-h">tailnet 에만 열린 포트 <span>${tsPorts.length}</span></p>${portRows(tsPorts, "tsp")}` : "")}
${sec(LEVEL.local)}
<p class="x-more">${homeLink("/#network", "인그레스·트래픽·열린 포트 전체는 개요에서", "more:network")}</p>`;
  }

  const ALERT_HOME_L = { systemd: "개요에서 실패한 유닛 보기", container: "개요에서 컨테이너 보기", system: "개요에서 하드웨어 보기", monitor: "개요에서 자기 진단 보기" };
  /** 경보 목록. opts.self = 지금 보고 있는 노드(그 노드로 가는 "지도에서 보기" 대신 개요의 알맞은 곳으로) */
  function alertItems(list, opts) {
    opts = opts || {};
    if (!list.length) return html`<p class="calm">${ic("okc")}<span>지금은 확인할 경보가 없어요.</span></p>`;
    return html`<ul class="alist">${list.map((a) => {
      const tid0 = alertNodeNow(a);
      const tid = tid0 && tid0 !== opts.self ? tid0 : null;
      const home = a.source === "monitor" && SH.monitor && SH.monitor.href && S ? SH.monitor.href(S) || alertHome(a) : alertHome(a);
      return html`<li data-tone="${tone(a.level)}"><p class="a-top">${levelPill(a.level)}<span class="a-src">${SOURCE[a.source] || a.source || "–"}${a.since ? " · " + ago(a.since) + "부터" : ""}</span></p><p class="a-msg">${a.message}</p><p class="a-act">${tid ? html`<button type="button" class="linkish" data-select="${tid}" data-fk="${"al:" + (a.key || a.message)}">지도에서 보기 · ${curName(tid)}</button>` : homeLink(home, ALERT_HOME_L[a.source] || "개요에서 보기", "alh:" + (a.key || a.message))}</p></li>`;
    })}</ul>`;
  }
  function panelAlerts() {
    const al = S.alerts || [];
    const errs = Object.entries((S.meta && S.meta.errors) || {});
    return html`<p class="panel-note">경보 <b>${al.length}</b>건 · 심각도순. 지도에 있는 대상은 누르면 찾아 줍니다.</p>${alertItems(al)}
${errs.length ? html`<section class="xsec"><header class="xsec-h"><h3>수집 오류 <span class="xsec-n">${errs.length}</span></h3></header><ul class="errs">${errs.map(([k, v]) => html`<li><b class="mono">${k}</b>${v}</li>`)}</ul></section>` : ""}
<p class="x-more">${homeLink("/#alerts", "개요에서 경보 보기", "more:alerts")}</p>`;
  }

  // ------------------------------------------------------------------ 전역 클릭 위임(목록·검사기)
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-act],[data-select],[data-tree]");
    if (!t || t.closest("#graph")) return;
    if (t.hasAttribute("data-tree")) { toggleTree(t.getAttribute("data-tree")); return; }
    if (t.hasAttribute("data-select")) {
      const raw0 = t.getAttribute("data-select");
      const id = resolveAny(raw0);
      if (id && curHas(id)) select(id, { invoker: t, reveal: true, focus: mobile });
      else if (raw0) toast(isPhys() ? "물리 지도에는 따로 그리지 않는 항목이에요 — 개념 보기에서 볼 수 있어요" : "개념 지도에는 없는 항목이에요 — 물리 보기에서 볼 수 있어요");
      return;
    }
    const act = t.getAttribute("data-act");
    if (act === "problems") { ui.problems = !ui.problems; renderTools(); renderSide(); }
    else if (act === "probs") { ui.problems = true; setSide(true, "services"); renderTools(); renderSide(); }
    else if (act === "view") setView(t.getAttribute("data-view"), { select: t.getAttribute("data-target") || null, invoker: t });
    else if (act === "tree-all" || act === "tree-none") {
      ui.tree = new Set(act === "tree-all" && PD ? PHYS.treeIds(PD) : []);
      store.set("tree", [...ui.tree]);
      renderFlow();
    }
  });
  // 목록·검사기의 항목에 올리면 지도에서 그 길을 밝힌다.
  for (const host of [side, inspector]) {
    host.addEventListener("pointerover", (e) => {
      if (mobile || e.pointerType === "touch") return;
      const t = e.target.closest && e.target.closest("[data-select]");
      const id = t && resolveAny(t.getAttribute("data-select"));
      if (id && curHas(id)) setHover(id);
    });
    host.addEventListener("pointerout", (e) => {
      if (mobile) return;
      const from = e.target.closest && e.target.closest("[data-select]");
      const to = e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest("[data-select]");
      if (from && from !== to) { const tid = to ? resolveAny(to.getAttribute("data-select")) : null; setHover(tid && curHas(tid) ? tid : null); }
    });
  }

  // 키보드: Esc(검사기 → 범례 → 목록 순으로 닫기) · + − 0 확대 · / 찾기
  document.addEventListener("keydown", (e) => {
    const typing = e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable);
    if (e.key === "Escape") {
      if (typing && e.target.id === "svc-q" && ui.q) { ui.q = ""; e.target.value = ""; renderSide(); return; }
      if (app.dataset.insp === "open") { select(null); return; }
      if (ui.legend) { setLegend(false); legendBtn.focus(); return; }
      if (ui.side && !mobile) { setSide(false); const b = $(`.stab[data-tab="${tabNow()}"]`, sideTabs); if (b) b.focus(); }
      return;
    }
    if (typing || e.altKey || e.ctrlKey || e.metaKey || mobile) return;
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomCenter(1.2); }
    else if (e.key === "-" || e.key === "_") { e.preventDefault(); zoomCenter(1 / 1.2); }
    else if (e.key === "0") { e.preventDefault(); fitToggle(); }
    else if (e.key === "/") {
      e.preventDefault();
      setSide(true, "services");
      requestAnimationFrame(() => { const i = $("#svc-q"); if (i) i.focus(); });
    }
  });

  // ------------------------------------------------------------------ 검사기 내용
  const iSec = (title, content, cls) => (content ? html`<section class="${"isec" + (cls ? " " + cls : "")}"><h3 class="isec-h">${title}</h3>${content}</section>` : "");
  const grp = (content) => html`<div class="grp">${content}</div>`;

  /** 프로브 한 칸의 판정(공용 JarvisShell.probeVerdict — 개요 칩·상세와 같은 말) → { t, text, notes, blocked } */
  function probeVerdict(p, key, sv) {
    const v = SH.probeVerdict ? SH.probeVerdict(p, key, sv) : null;
    return v ? Object.assign({ t: v.tone }, v) : null;
  }
  function probeRows(sv) {
    const pr = sv.probes || {};
    const ks = Object.keys(pr);
    if (!ks.length) return "";
    const order = ["public", "tunnel", "internal"].filter((k) => pr[k]).concat(ks.filter((k) => !PROBE_L[k]));
    return grp(html`<ul class="probes">${order.map((k) => {
      const p = pr[k] || {};
      const v = probeVerdict(p, k, sv) || { t: "muted", text: "–", notes: [] };
      const [a, b] = PROBE_L[k] || [k, "엔드포인트"];
      return html`<li data-tone="${v.t}">${shape(v.t)}<span class="pb-l"><b>${a}</b><span class="sub">${b}${p.t ? " · " + ago(p.t) : ""}</span></span><span class="pb-r">${v.text}${p.cf_ray ? html`<span class="sub mono">${p.cf_ray}</span>` : ""}</span>${(v.notes || []).length ? html`<span class="pb-note">${v.notes.join(" · ")}</span>` : ""}</li>`;
    })}</ul>`);
  }

  const KIND_TITLE = SH.KIND_TITLE || { public: "공개 경로 — 인터넷에서", tailnet: "Tailnet 경로 — 내 기기에서", lan: "LAN 직접 — 같은 공유기에서", depends: "내부 의존" };
  const KIND_SHORT = { public: "공개", tailnet: "Tailnet", lan: "LAN" };
  // 길의 종류는 출발 영역에서 나가는 첫 간선이 정한다(Caddy → 서비스 간선은 서비스 카테고리의 종류라 기준이 될 수 없다).
  const chainKind = (c) => (SH.chainKind ? SH.chainKind(c) : "depends");
  const chainIndirect = (c) => c.edges.length > 0 && c.edges[c.edges.length - 1].kind === "depends";
  /** "어떻게 닿는가" 한 단계의 설명 */
  function stepText(nid, i, c, target, tsv) {
    const n = D.nodes.get(nid);
    const inE = i > 0 ? c.edges[i - 1] : null;
    const outE = c.edges[i] || null;
    const kind = chainKind(c);
    const sv = n && n.svc;
    const nw = (S.system && S.system.network) || {};
    const tn = (S.ingress && S.ingress.tunnel) || {};
    if (nid === "zone:internet") return tsv && tsv.dns ? "누구나 접속 · 공개 DNS 가 " + (DNS_L[tsv.dns.points_to] || tsv.dns.points_to) + " 를 가리킴" : "인터넷의 누구나";
    if (nid === "zone:tailnet") {
      const ts = S.tailscale || {};
      const toProxy = outE && D.nodes.get(outE.to) && D.nodes.get(outE.to).svc && D.nodes.get(outE.to).svc.kind === "proxy";
      const host = tsv && (tsv.hostnames || [])[0];
      return "내 tailnet 기기만 (" + fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total) + " 온라인)" + (toProxy && host ? " · " + host + " → " + (nw.tailnet_ip || "tailnet IP") : "");
    }
    if (nid === "zone:lan") return "같은 LAN의 기기 · 이 기기 " + ((nw.lan_ips || [])[0] || "");
    if (!n) return "";
    // 합성 LAN 엣지: 관문(Caddy·cloudflared …) 자체의 포트가 LAN 에 바로 열려 있다
    if (nid === target && inE && inE.terminal) return "LAN 에 바로 열린 포트 " + (inE.label || "") + " — " + nodeName(nid) + " 자체로 닿음(프록시 경로 아님)";
    if (n.type === "edge") return "TLS 종료 · 엣지 " + ((tn.edge_locations || []).join(" ") || "–") + (tsv && tsv.edge_cert ? " · 엣지 인증서 " + Math.floor(tsv.edge_cert.days_left) + "일" : "");
    if (sv && sv.kind === "tunnel") return "QUIC 터널로 이 기기 안까지 · 연결 " + fmt.num(tn.ready_connections) + "/" + fmt.num(tn.connections);
    if (sv && sv.kind === "proxy") {
      const port = inE && inE.label ? inE.label : kind === "public" ? ":80" : ":443";
      const base = kind === "public" ? "Caddy " + port + " 평문 — Host 헤더로 라우팅" : "Caddy " + port + " HTTPS" + (tsv && tsv.cert ? " · 인증서 " + Math.floor(tsv.cert.days_left) + "일" : "");
      const nextIsTarget = outE && outE.to === target;
      if (nextIsTarget && isStatic(tsv)) return base + " → 앱 없이 직접 응답";
      const up = nextIsTarget && tsv && tsv.upstream ? " → " + tsv.upstream : "";
      return base + up;
    }
    if (sv && sv.kind === "remote-access") return "SSH " + ((inE && inE.label) || ":22");
    if (nid === target) {
      if (inE && D.nodes.get(inE.from) && D.nodes.get(inE.from).type === "zone") return "포트 " + (edgeLabel(inE) || "?") + " 로 바로 — Caddy 를 거치지 않음";
      if (inE && inE.kind === "depends") return nodeName(inE.from) + " 이(가) 안에서 사용 · 밖으로는 열리지 않음";
      if (isStatic(tsv)) return "앱 없이 Caddy 가 직접 응답(정적 라우트)";
      return runtimeLine(tsv && tsv.runtime) || "도착";
    }
    return sv ? runtimeLine(sv.runtime) || kindName(sv.kind) : n.detail || "";
  }
  function chainVerdict(c, tsv) {
    if (!tsv) return null;
    const pr = tsv.probes || {};
    const kind = chainKind(c);
    const lastHop = D.nodes.get(c.nodes[c.nodes.length - 2]);
    const viaProxy = lastHop && lastHop.svc && lastHop.svc.kind === "proxy";
    if (c.nodes[c.nodes.length - 1] !== D.svcNode.get(tsv.id)) return null;
    if (kind === "public" && pr.public) return Object.assign({ label: "공개 프로브" }, probeVerdict(pr.public, "public", tsv));
    if (kind === "tailnet" && viaProxy && pr.internal) return Object.assign({ label: "Caddy :443 프로브" }, probeVerdict(pr.internal, "internal", tsv));
    return null;
  }
  function reachSection(n, sv) {
    // 간접(의존) 길은 그 백엔드를 쓰는 앱마다 하나만 — 앱의 길 전체는 앱의 검사기에서 본다
    const seenApp = new Set();
    const cs = chains(n.id, 8).filter((c) => {
      if (!chainIndirect(c)) return true;
      const app = c.nodes[c.nodes.length - 2];
      if (seenApp.has(app)) return false;
      seenApp.add(app);
      return true;
    });
    const lv = expLevel(sv);
    const SUM = {};
    for (const L of LEVELS) SUM[L.id] = L.reach || L.sub;
    const t0 = tone(sv.status);
    // 관문(Caddy·cloudflared·SSH 등)은 "누가 닿나"보다 "어떤 길이 지나가나"가 요점이다.
    const gateway = !SERVICE_LIKE.has(n.type);
    const KN = { public: "공개", tailnet: "Tailnet", lan: "LAN 직접" };
    const through = gateway ? [...new Set(cs.map(chainKind))].map((k) => KN[k] || k) : [];
    const sumText = gateway
      ? (through.length ? through.join(" · ") + " 경로가 지나는 관문이에요." : "지나는 경로가 없어요.") + (sv.exposure && sv.exposure.lan ? " 포트는 LAN에도 바로 열려 있어요." : "")
      : SUM[lv];
    const blocks = cs.map((c, ci) => {
      const kind = chainKind(c);
      const ind = chainIndirect(c);
      const app = ind ? c.nodes[c.nodes.length - 2] : null;
      const v = ind ? null : chainVerdict(c, sv);
      const title = ind ? "간접 — " + nodeName(app) + " 를 거쳐 · " + (KIND_SHORT[kind] || "") + " 요청 경로" : KIND_TITLE[kind] || kind;
      return html`<div class="rt" data-k="${kind}" data-indirect="${ind ? "1" : "0"}">
<p class="rt-h"><span class="rt-k"><i aria-hidden="true"></i>${title}</span>${v ? html`<span class="rt-v" data-tone="${v.t}">${shape(v.t)}${v.label} ${v.text}</span>` : ""}${ind ? html`<button type="button" class="linkish" data-select="${app}" data-fk="${"rtapp:" + app}">${nodeName(app)} 에서 보기</button>` : ""}</p>
<ol class="rt-steps">${c.nodes.map((nid, i) => {
        const e = i > 0 ? c.edges[i - 1] : null;
        const lb = edgeLabel(e);
        const last = i === c.nodes.length - 1;
        const nn = D.nodes.get(nid);
        const st = nn ? (nn.type === "extra" && nn.svc ? nn.svc.status : nn.status) : "unknown";
        return html`${e ? html`<li class="rt-e" data-k="${e.kind}" aria-hidden="${lb ? "false" : "true"}">${lb ? html`<span class="mono">${lb}</span>` : ""}</li>` : ""}<li class="${"rt-s" + (last ? " last" : "")}" data-tone="${tone(st)}"><span class="rt-n">${i + 1}</span><span class="rt-b"><button type="button" class="rt-node" data-select="${nid}" data-fk="${"rt" + ci + ":" + nid}">${nodeName(nid)}</button><span class="rt-d">${stepText(nid, i, c, n.id, sv)}</span></span></li>`;
      })}</ol></div>`;
    });
    const bv = sv.probes && sv.probes.tunnel ? probeVerdict(sv.probes.tunnel, "tunnel", sv) : null;
    const blocked = bv && (bv.blocked || sv.snippet === "private") ? bv : null;
    return grp(html`<p class="reach-sum" data-tone="${t0}">${sumText}</p><p class="reach-x">${xbadges(sv.exposure, true)}</p>
${blocks.length ? blocks : !gateway && lv === "local" && !(sv.listeners || []).some((l) => l.scope !== "loopback" && l.scope !== "docker") ? "" : noPath(sv)}
${blocked ? html`<p class="reach-blk" data-tone="${blocked.t}">${shape(blocked.t)}<span>터널(공개) 경로 · Caddy :80 — ${blocked.text}</span></p>` : ""}`);
  }

  /** 지도에 길이 그려지지 않은 서비스: 열린 포트가 있으면 그것으로 설명한다. */
  function noPath(sv) {
    const open = (sv.listeners || []).filter((l) => l.scope !== "loopback" && l.scope !== "docker");
    if (!open.length) return html`<p class="reach-none">밖에서 닿는 길이 없어요 — 이 기기 안에서만 쓰여요.</p>`;
    return html`<p class="reach-none">지도에 따로 그린 길은 없지만, 이 포트로 바로 닿아요: ${open.map((l) => html`<b class="mono">${l.port}/${l.proto}</b> <span class="sub-i">${labels.scope[l.scope] || l.scope}</span> `)}</p>`;
  }
  function inspectService(n, sv, opts) {
    opts = opts || {};
    const usedBy = D.edges.filter((e) => e.kind === "depends" && e.to === n.id).map((e) => e.from);
    const deps = (sv.depends_on || []).map((id) => D.svcNode.get(id) || id);
    const rt = sv.runtime;
    const tr = sv.traffic;
    const urls = sv.urls || [];
    const desc = sv.description;
    const status = sv.status !== "up" || sv.status_reason || desc ? html`<div class="istatus" data-tone="${tone(sv.status)}">${sv.status_reason ? html`<p class="why">${sv.status_reason}</p>` : ""}${desc ? html`<p class="desc">${desc}</p>` : ""}</div>` : "";
    // 주소 줄은 개요 상세와 같은 모양: 글꼴 그대로의 링크 ↗ · (라벨) · 오른쪽에 응답 코드와 노출 배지
    const urlSec = urls.length ? grp(html`<ul class="iurls">${urls.map((u) => html`<li>${ext(u.url, bareUrl(u.url), "url")}${u.label ? html`<span class="sub">${u.label}</span>` : ""}<span class="iu-r">${u.code != null || u.error ? html`<span class="${"u-st t-" + (u.error ? "crit" : codeTone(u.code))}">${u.code != null ? u.code + " · " + fmt.ms(u.ms) : shortErr(u.error)}</span>` : ""}<span class="xb" data-k="${u.audience}">${ic((AUD[u.audience] || AUD.local)[1])}${labels.audience[u.audience] || u.audience}</span></span></li>`)}</ul>`) : "";
    const certDns = sv.cert || sv.dns || sv.edge_cert || sv.in_tunnel != null || (sv.hostnames || []).length ? grp(rows([
      sv.cert ? ["오리진 인증서", html`<span class="${"t-" + (sv.cert.days_left < 14 ? "crit" : sv.cert.days_left < 30 ? "warn" : "none")}">${fmt.num(Math.floor(sv.cert.days_left))}일 남음</span><span class="sub">${sv.cert.issuer || ""}${sv.cert.not_after ? " · " + fmt.date(sv.cert.not_after) + " 만료" : ""}${sv.cert.subject ? " · " + sv.cert.subject : ""}</span>`] : null,
      sv.edge_cert ? ["엣지 인증서", html`${fmt.num(Math.floor(sv.edge_cert.days_left))}일 남음<span class="sub">${sv.edge_cert.issuer || ""}</span>`] : null,
      sv.dns ? ["공개 DNS", html`<span class="${sv.dns.points_to === "error" ? "t-crit" : ""}">${DNS_L[sv.dns.points_to] || sv.dns.points_to}</span>${(sv.dns.answers || []).length ? html`<span class="sub mono">${sv.dns.answers.join(", ")}</span>` : ""}`] : null,
      (sv.hostnames || []).length ? ["호스트", html`<span class="mono">${sv.hostnames.join(", ")}</span>`] : null,
      sv.in_tunnel != null ? ["터널 등록", sv.in_tunnel ? "예" : "아니오"] : null,
      sv.header_inject ? ["헤더 주입", "예 (Caddy 가 헤더를 덧붙임)"] : null,
      sv.snippet ? ["Caddy 스니펫", sv.snippet === "public" ? "public (터널 허용)" : "private (터널 404)"] : null,
    ])) : "";
    const trSec = tr ? grp(html`<div class="tstats"><p><b>${fmt.num(tr.requests_1h)}</b><span>1시간</span></p><p><b>${fmt.num(tr.requests_24h)}</b><span>24시간</span></p><p><b class="${tr.errors_4xx_24h ? "t-warn" : ""}">${fmt.num(tr.errors_4xx_24h)}</b><span>4xx·24h</span></p><p><b class="${tr.errors_5xx_24h ? "t-crit" : ""}">${fmt.num(tr.errors_5xx_24h)}</b><span>5xx·24h</span></p></div>${rows([["지연 p50 / p95", fmt.ms(tr.p50_ms_24h) + " / " + fmt.ms(tr.p95_ms_24h)], ["마지막 요청", tr.last_ts ? ago(tr.last_ts) : "없음"]])}`) : "";
    const rtSec = rt ? grp(rows([
      ["종류", labels.runtime[rt.type] || rt.type],
      ["이름", html`<span class="mono">${rt.name || "–"}</span>`],
      rt.project ? ["프로젝트", rt.project + (rt.service ? " / " + rt.service : "")] : null,
      rt.image ? ["이미지", html`<span class="mono">${rt.image}</span>`] : null,
      rt.state || rt.sub ? ["상태", SH.runtimeState ? SH.runtimeState(rt) : (rt.state || "") + (rt.sub ? "/" + rt.sub : "") + (rt.health ? " · " + rt.health : "")] : null,
      rt.type === "remote" ? ["주소", html`<span class="mono">${rt.ip || "–"}</span> · ${rt.online ? "온라인" : "오프라인"}`] : null,
      rt.uptime_s != null ? ["가동", fmt.dur(rt.uptime_s, 2)] : null,
      rt.restart_count != null ? ["재시작", html`<span class="${rt.restart_count > 5 ? "t-warn" : ""}">${fmt.num(rt.restart_count)}회</span>`] : null,
      rt.cpu_pct != null || rt.mem_bytes != null ? ["CPU · 메모리", fmt.pct(rt.cpu_pct, 1) + " · " + fmt.bytes(rt.mem_bytes)] : null,
      rt.pid ? ["PID", html`<span class="mono">${rt.pid}</span>`] : null,
      rt.network_mode ? ["네트워크", html`<span class="mono">${rt.network_mode}</span>`] : null,
    ])) : "";
    // 정적 라우트: 실행 주체가 따로 없다 — Caddy 가 직접 응답한다(오류가 아니다)
    const caddyN = isStatic(sv) && !rt ? [...D.nodes.values()].find((x) => x.svc && x.svc.kind === "proxy") : null;
    const rtStatic = isStatic(sv) && !rt ? grp(html`<p class="reach-none">Caddy 가 직접 응답해요 — 뒤에 앱·컨테이너가 없는 정적 라우트라 상태는 프로브로만 정해요.</p>${caddyN ? html`<p class="deps"><button type="button" class="dep" data-select="${caddyN.id}" data-fk="${"rt-caddy:" + sv.id}" data-tone="${tone(caddyN.status)}">${shape(tone(caddyN.status))}${nodeName(caddyN.id)}</button></p>` : ""}`) : "";
    const lsSec = (sv.listeners || []).length ? grp(html`<ul class="ports">${sv.listeners.map((l) => html`<li><b class="mono">${l.port}</b><span class="mono pt-p">${l.proto}</span><span class="pt-o"><span class="${"scope s-" + l.scope}">${labels.scope[l.scope] || l.scope}</span> <span class="mono pt-w">${(l.addrs || []).join(", ")}</span>${l.process ? html` <span class="pt-w">· ${l.process}</span>` : ""}</span></li>`)}</ul>`) : "";
    const depSec = deps.length || usedBy.length ? grp(html`${deps.length ? html`<p class="dep-l">이 서비스가 쓰는 것</p><p class="deps">${deps.map((id) => (D.nodes.has(id) ? html`<button type="button" class="dep" data-select="${id}" data-fk="${"dep:" + id}" data-tone="${tone(D.nodes.get(id).status)}">${shape(tone(D.nodes.get(id).status))}${nodeName(id)}</button>` : html`<span class="dep">${id}</span>`))}</p>` : ""}${usedBy.length ? html`<p class="dep-l">이것을 쓰는 서비스</p><p class="deps">${usedBy.map((id) => html`<button type="button" class="dep" data-select="${id}" data-fk="${"used:" + id}" data-tone="${tone(D.nodes.get(id).status)}">${shape(tone(D.nodes.get(id).status))}${nodeName(id)}</button>`)}</p>` : ""}`) : "";
    const x = Object.assign({}, sv.extra || {});
    delete x.monitor; // 모니터 자기 진단은 전용 칸으로
    if (sv.kind === "process-manager") { delete x.apps; delete x.daemon_pid; delete x.online; delete x.total; } // pm2 는 전용 표가 보여 준다
    const extraSec = Object.keys(x).length ? grp(rows(Object.entries(x).map(([k, v]) => [XL[k] || k, extraVal(v)]))) : "";
    let special = ""; let first = "";
    if (sv.kind === "proxy") special = iSec("Caddy", caddyBlock());
    else if (sv.kind === "tunnel") special = iSec("Cloudflare Tunnel", tunnelBlock());
    else if (sv.kind === "process-manager") special = iSec("pm2 앱", pm2Block(sv));
    // 이 모니터 자신이면 자기 진단이 머리기사 — 설명 바로 뒤, 주소·경로보다 먼저(개요 상세와 같은 순서)
    else if (sv.kind === "monitor" && !opts.noMonitor) first = iSec("모니터 자기 진단", monitorBlock(sv));
    return html`${status}${first}${iSec("주소", urlSec)}${iSec("어떻게 닿는가", reachSection(n, sv), "reach")}${special}${iSec("프로브", probeRows(sv))}${iSec("인증서 · DNS", certDns)}${iSec("트래픽", trSec)}${iSec("실행 주체", rtSec || rtStatic)}${iSec("열린 포트", lsSec)}${iSec("의존 관계", depSec)}${iSec("추가 정보", extraSec)}
<p class="ifoot">${sv.upstream && !isStatic(sv) ? html`업스트림 <span class="mono">${sv.upstream}</span> · ` : ""}ID <span class="mono">${sv.id}</span></p>`;
  }
  const XL = { version: "버전", routes: "라우트", config_ok: "설정", connections: "연결", edge_locations: "엣지", tunnel: "터널", peers_online: "온라인 피어", backend_state: "상태", ips: "주소", online: "온라인 앱", total: "전체 앱", daemon_pid: "데몬 PID", apps: "앱" };
  /** 추가 정보 값 한 칸: 객체 배열은 이름만, 객체는 JSON, 참·거짓은 예·아니오 */
  const extraVal = (v) => (Array.isArray(v) ? v.map((o) => (o && typeof o === "object" ? o.name || o.id || JSON.stringify(o) : o)).join(", ") : typeof v === "boolean" ? (v ? "예" : "아니오") : v && typeof v === "object" ? JSON.stringify(v) : v == null ? "–" : String(v));
  /** pm2 데몬: 앱 표(이름 · 상태 · 재시작 · 불안정 재시작) — 앱이 연 포트의 서비스로 이어 준다 */
  function pm2Block(sv) {
    const ex = sv.extra || {};
    const apps = Array.isArray(ex.apps) ? ex.apps : [];
    const full = new Map((((S && S.pm2 && S.pm2.apps) || [])).map((a) => [a.name, a]));
    const svcOf = (name) => (S.services || []).find((x) => x.runtime && x.runtime.type === "pm2" && x.runtime.name === name) || null;
    const stT = (st) => (st === "online" ? "ok" : st === "errored" ? "crit" : st === "stopped" || st === "stopping" ? "muted" : "warn");
    return grp(html`${rows([["데몬 PID", ex.daemon_pid ? html`<span class="mono">${ex.daemon_pid}</span>` : null], ["앱", ex.total != null ? fmt.num(ex.online) + " 실행 / " + fmt.num(ex.total) : null]])}
${apps.length ? html`<ul class="pm2-apps">${apps.map((a) => {
      const f = full.get(a.name) || {}; const st = a.status || f.status; const t = stT(st);
      const own = svcOf(a.name); const nid = own ? D.svcNode.get(own.id) : null;
      const unstable = f.unstable_restarts != null ? f.unstable_restarts : a.unstable_restarts;
      return html`<li data-tone="${t}">${shape(t)}<span class="pa-n">${nid ? html`<button type="button" class="linkish" data-select="${nid}" data-fk="${"pm2:" + a.name}">${a.name}</button>` : html`<b>${a.name}</b>`}</span><span class="pa-s">${(SH.STATE_L && SH.STATE_L[st]) || st || "–"}</span><span class="pa-r">재시작 ${fmt.num(a.restarts != null ? a.restarts : f.restarts)}회${unstable ? html` · <span class="t-warn">불안정 ${fmt.num(unstable)}회</span>` : ""}</span></li>`;
    })}</ul>` : html`<p class="sub">pm2 가 관리하는 앱이 없어요.</p>`}`);
  }
  /** 이 웹 서비스(Jarvis) 자신의 상태: 판정 · 버전 · 자원 · 스냅샷 · 수집 주기 · 수집기별 최신성 */
  function monitorBlock(sv) {
    const M = SH.monitor || null;
    const m = M && M.of ? M.of(S, sv) : (S && S.monitor) || (sv.extra && sv.extra.monitor) || null;
    if (!m || typeof m !== "object") return "";
    const hh = M && M.health ? M.health(S) : null;
    const secs = M && M.secs ? M.secs : (v) => (isNum(v) ? fmt.num(v, v < 10 ? 1 : 0) + "초" : "–");
    const cname = M && M.collectorName ? M.collectorName : (nm) => nm;
    const LL = (M && M.LOOP_L) || {};
    const loops = m.loops && typeof m.loops === "object" ? m.loops : {};
    const ORDER = ((M && M.LOOP_ORDER) || Object.keys(loops)).filter((k) => loops[k]);
    const cs = Array.isArray(m.collectors) ? m.collectors.filter((c) => c && c.name) : [];
    return grp(html`${hh ? html`<p class="notice" data-tone="${hh.tone}">${shape(hh.tone)}<span>${hh.text}</span></p>` : ""}
${rows([
      ["버전", html`${m.version || "–"}${m.commit ? html` · <span class="mono" title="${SH.commitInfo ? SH.commitInfo(m.commit).title : m.commit}">${SH.commitInfo ? SH.commitInfo(m.commit).hash : String(m.commit).slice(0, 10)}</span>${SH.commitInfo && SH.commitInfo(m.commit).dirty ? " · 미커밋 수정" : ""}` : ""}`],
      ["가동", isNum(m.uptime_s) ? fmt.dur(m.uptime_s, 2) : null],
      ["메모리(RSS) · 스레드", fmt.bytes(m.rss_bytes) + " · " + fmt.num(m.threads)],
      ["스냅샷 만들기", fmt.ms(m.snapshot_ms) + " · " + fmt.bytes(m.snapshot_bytes) + (m.snapshot_gz_bytes ? " (gzip " + fmt.bytes(m.snapshot_gz_bytes) + ")" : "")],
      m.bind ? ["수신 주소", html`<span class="mono">${m.bind}</span>`] : null,
      m.pid ? ["PID", html`<span class="mono">${m.pid}</span>`] : null,
    ])}
${ORDER.length ? html`<p class="mini-h">수집 주기 <span>마지막 한 바퀴에 걸린 시간</span></p><ul class="mon-rows">${ORDER.map((k) => { const l = loops[k] || {}; return html`<li><span class="mr-n">${LL[k] || k}</span><span class="mr-v">${fmt.ms(l.last_ms)}</span><span class="mr-s">${l.interval_s ? l.interval_s + "초마다" : ""}${l.last_at ? " · " + ago(l.last_at) : ""}</span></li>`; })}</ul>` : ""}
${cs.length ? html`<p class="mini-h">수집기 <span>${cs.length}개 · 마지막 갱신 / 허용 한도</span></p><ul class="mon-rows">${cs.map((c) => { const t = c.error ? "crit" : c.stale ? "warn" : "ok"; return html`<li data-tone="${t}">${shape(t)}<span class="mr-n">${cname(c.name)}</span><span class="mr-v">${secs(c.age_s)} / ${secs(c.limit_s)}</span>${c.error ? html`<span class="mr-e">${c.error}</span>` : c.stale ? html`<span class="mr-e">늦어짐</span>` : ""}</li>`; })}</ul>` : ""}`);
  }
  function caddyBlock() {
    const cad = (S.ingress && S.ingress.caddy) || {};
    const tr = (S.ingress && S.ingress.traffic) || {}; const w1 = tr.last_1h || {}; const w24 = tr.last_24h || {};
    const hostChip = (h, k) => { const nid = D.svcNode.get("route:" + h); return nid ? html`<button type="button" class="hchip" data-k="${k}" data-select="${nid}" data-fk="${"hc:" + h}">${shortHost(h)}</button>` : html`<span class="hchip" data-k="${k}">${shortHost(h)}</span>`; };
    return grp(html`${rows([["설정", cad.config_ok === false ? html`<span class="t-crit">오류 ${cad.error || ""}</span>` : "정상"], ["버전", cad.version], ["컨테이너", html`<span class="mono">${cad.container || "–"}</span>`], ["Caddyfile 수정", cad.caddyfile_mtime ? fmt.datetime(cad.caddyfile_mtime) + " (" + ago(cad.caddyfile_mtime) + ")" : null], ["요청 1h · 24h", fmt.num(w1.requests) + " · " + fmt.num(w24.requests)], ["로그 스트림", tr.stream ? (tr.stream.connected ? "연결됨" : html`<span class="t-crit">끊김 ${tr.stream.error || ""}</span>`) : null]])}
<p class="mini-h">터널로 열린 호스트 <span>${(cad.public_hosts || []).length}</span></p><p class="hosts">${(cad.public_hosts || []).map((h) => hostChip(h, "public"))}</p>
<p class="mini-h">tailnet 전용 호스트 <span>${(cad.private_hosts || []).length}</span></p><p class="hosts">${(cad.private_hosts || []).map((h) => hostChip(h, "tailnet"))}</p>`);
  }
  function tunnelBlock() {
    const tn = (S.ingress && S.ingress.tunnel) || {};
    const rtt = (tn.rtt_ms || []).filter(isNum);
    return grp(html`${tn.outdated ? html`<p class="notice" data-tone="info">${shape("info")}<span>cloudflared <span class="mono">${tn.outdated.current}</span> → <b class="mono">${tn.outdated.latest}</b> 업데이트가 나왔어요.</span></p>` : ""}
${rows([["상태", tn.ready ? "준비됨" : html`<span class="t-crit">준비 안 됨</span>`], ["터널", html`<span class="mono">${tn.name || "–"}</span>`], ["연결", fmt.num(tn.ready_connections) + " / " + fmt.num(tn.connections)], ["엣지", (tn.edge_locations || []).join(", ")], ["RTT", rtt.length ? rtt.map((v) => Math.round(v)).join(" · ") + " ms" : null], ["분당 요청 · 동시", fmt.num(tn.req_per_min, 1) + " · " + fmt.num(tn.concurrent_requests)], ["누적 요청 · 오류", fmt.num(tn.total_requests) + " · " + fmt.num(tn.request_errors)], ["버전", tn.version]])}
${(tn.recent_errors || []).length ? html`<p class="mini-h">최근 터널 오류 <span>${tn.recent_errors.length}</span></p><ul class="loglist" data-sk="tn-err">${tn.recent_errors.slice().reverse().slice(0, 6).map((e) => html`<li data-tone="${e.level === "error" ? "crit" : "warn"}"><span class="lg-t mono">${when(e.ts)}</span><span class="lg-m">${e.msg}</span></li>`)}</ul>` : ""}`);
  }
  function reachChips(id) {
    const reach = [...pathSet(id).N].filter((x) => { const n = D.nodes.get(x); return n && SERVICE_LIKE.has(n.type); });
    return reach.length ? html`<p class="deps">${reach.sort((a, b) => nodeName(a).localeCompare(nodeName(b))).map((x) => { const t = tone(D.nodes.get(x).status); return html`<button type="button" class="dep" data-select="${x}" data-fk="${"z:" + x}" data-tone="${t}">${shape(t)}${nodeName(x)}</button>`; })}</p>` : html`<p class="sub">이어진 서비스가 없습니다.</p>`;
  }
  function inspectZone(n) {
    const out = [];
    if (n.id === "zone:internet") {
      const tr = (S.ingress && S.ingress.traffic) || {};
      const v1 = (tr.last_1h && tr.last_1h.by_via) || {}; const v24 = (tr.last_24h && tr.last_24h.by_via) || {};
      const pubs = (S.services || []).filter((x) => x.exposure && x.exposure.public);
      out.push(html`<div class="istatus"><p class="desc">인터넷의 누구나 들어오는 이 기기의 공개 입구는 Cloudflare Tunnel 하나예요(공유기 포트포워딩은 Jarvis 가 확인하지 못해요).</p></div>`);
      out.push(iSec("공개 요청", grp(rows([["최근 1시간", fmt.num(v1.tunnel || 0) + "건"], ["최근 24시간", fmt.num(v24.tunnel || 0) + "건"], ["공개 DNS → Cloudflare", pubs.filter((x) => x.dns && x.dns.points_to === "cloudflare").length + " / " + pubs.length], ["터널", (S.ingress && S.ingress.tunnel && S.ingress.tunnel.ready) ? "준비됨" : "확인 필요"]]))));
    } else if (n.id === "zone:tailnet") {
      const ts = S.tailscale || {}; const self = ts.self || {};
      const peers = (ts.peers || []);
      const on = peers.filter((p) => p.online);
      const mix = SH.tailnetMix ? SH.tailnetMix(S) : { text: "" };
      const CL = SH.CONN_L || { direct: "직결", relay: "중계", idle: "유휴" };
      out.push(iSec("Tailscale", grp(rows([["상태", ts.backend_state === "Running" ? "Running" : html`<span class="t-crit">${ts.backend_state || "–"}</span>`], ["버전", ts.version], ["이 기기", html`<span class="mono">${self.dns_name || self.hostname || "–"}</span>`], ["주소", html`<span class="mono">${(self.ips || []).join(" · ") || "–"}</span>`], ["MagicDNS", ts.magic_dns_suffix], ["도메인", html`<span class="mono">*.${(S.meta && S.meta.domain) || ""} → ${(S.system && S.system.network && S.system.network.tailnet_ip) || ""}</span>`], ["기기", fmt.num(ts.peers_online) + " / " + fmt.num(ts.peers_total) + " 온라인 · " + mix.text], ["Serve / Funnel", (ts.serve ? "Serve 켜짐" : "Serve 없음") + " · " + (ts.funnel ? "Funnel 켜짐" : "Funnel 꺼짐")]]))));
      if ((ts.health || []).length) out.push(html`<ul class="notices">${ts.health.map((h) => html`<li class="notice" data-tone="warn">${shape("warn")}<span>${h}</span></li>`)}</ul>`);
      out.push(iSec("온라인 기기", grp(html`<ul class="peers">${on.slice(0, 14).map((p) => html`<li><span class="pe-n mono">${p.hostname}</span><span class="pe-ip mono">${p.ip || ""}</span><span class="${"conn c-" + (p.connection || "idle")}">${p.connection === "direct" ? CL.direct : p.connection === "relay" ? CL.relay + (p.relay ? " · DERP " + p.relay : "") : CL.idle}</span></li>`)}</ul>${on.length > 14 ? html`<p class="sub">외 ${on.length - 14}대</p>` : ""}<p class="sub">오프라인 ${peers.length - on.length}대는 개요에서 볼 수 있어요.</p>`)));
    } else if (n.id === "zone:lan") {
      const nw = (S.system && S.system.network) || {};
      const open = (S.listeners || []).filter((l) => l.scope === "all" || l.scope === "lan").sort((a, b) => a.port - b.port);
      out.push(html`<div class="istatus"><p class="desc">같은 공유기에 붙은 기기는 '모든 인터페이스'에 열린 포트를 Caddy 없이 바로 두드릴 수 있어요.</p></div>`);
      out.push(iSec("LAN", grp(rows([["이 기기 주소", html`<span class="mono">${(nw.lan_ips || []).join(" · ") || "–"}</span>`], ["LAN 에 열린 포트", open.length + "개"]]))));
      if (open.length) out.push(iSec("열린 포트", grp(html`<ul class="ports">${open.map((l) => html`<li><b class="mono">${l.port}</b><span class="mono pt-p">${l.proto}</span><span class="pt-o">${l.process || "?"}${l.owner ? html` <span class="pt-w">· ${l.owner}</span>` : ""}</span></li>`)}</ul>`)));
    }
    out.push(iSec("여기서 닿는 곳", grp(reachChips(n.id))));
    return html`${out}`;
  }
  function inspectEdge(n) {
    return html`<div class="istatus"><p class="desc">공개 요청은 Cloudflare 엣지에서 TLS 가 끝나고, QUIC 터널로 이 기기의 cloudflared 까지 들어옵니다.</p></div>${iSec("터널", tunnelBlock())}${iSec("여기서 닿는 곳", grp(reachChips(n.id)))}`;
  }
  const ZONE_HOME = { "zone:internet": "/#network", "zone:tailnet": "/#network", "zone:lan": "/#network", "edge:cloudflare": "/#network" };

  function renderInspector() {
    if (!ui.selected || !S) return;
    if (isPhys()) { renderInspectorPhys(); return; }
    const n = D.nodes.get(ui.selected);
    const kick = $("#insp-kicker"); const sub = $("#insp-sub");
    if (!n) {
      P.render(kick, "");
      setText(inspH, "사라진 항목");
      sub.hidden = true;
      renderKeep(inspActions, "");
      renderKeep(inspBody, html`<p class="calm">${shape("muted")}<span>이 항목이 최신 스냅샷에 없어요. 멈췄거나 이름이 바뀌었을 수 있어요.</span></p>`);
      return;
    }
    const sv = n.svc;
    const TYPE = { zone: "출발 영역", edge: "Cloudflare 엣지", infra: "인프라", service: "서비스", host: "호스트 서버", internal: "내부 백엔드", extra: "서비스" };
    const cat = n.category || (sv && sv.category) || null;
    const ZK = { "zone:internet": " · 인터넷 누구나", "zone:lan": " · 같은 공유기", "zone:tailnet": " · 내 tailnet 기기" };
    const kt = (cat ? catName(cat) : TYPE[n.type] || n.type) + (sv && sv.kind ? " · " + kindName(sv.kind) : n.type === "zone" ? ZK[n.id] || "" : "");
    renderKeep(kick, html`<span class="${"cat-ic c-" + (cat || "zone")}">${ic(cat ? CAT_ICON[cat] || "layers" : n.id === "zone:internet" ? "globe" : n.id === "zone:tailnet" ? "tailnet" : n.id === "zone:lan" ? "lan" : "globe")}</span>${kt}`);
    setText(inspH, nodeName(n.id));
    const same = (a, b) => String(a || "").toLowerCase() === String(b || "").toLowerCase();
    const head = nodeName(n.id);
    let subT = "";
    if (sv && sv.title && !same(sv.title, sv.name) && !same(sv.title, head)) subT = sv.title;
    else if (sv && (sv.hostnames || [])[0] && !same(sv.hostnames[0], head)) subT = sv.hostnames[0];
    else if (sv && !same(sv.name, head)) subT = sv.name;
    setText(sub, subT);
    sub.hidden = !subT;
    sub.classList.toggle("prose", /[가-힣]/.test(subT));
    const st = n.type === "extra" && sv ? sv.status : n.status;
    const t = tone(st);
    setAttr(inspector, "data-tone", t);
    const primary = sv && (sv.urls || []).find((u) => /^https?:/i.test(u.url || ""));
    const homeHref = sv ? "/#svc=" + encodeURIComponent(sv.id) : ZONE_HOME[n.id] || "/";
    const pid = PHYS && PD ? toPhys(n.id) : null;
    renderKeep(inspActions, html`${n.type === "zone" ? html`<span class="pill" data-tone="ok">${shape("ok")}출발 영역</span>` : pill(st)}${sv && sv.exposure ? xbadges(sv.exposure) : ""}${sv && sv.status_source ? html`<span class="ia-src">근거: ${sv.status_source === "probe" || !sv.runtime ? "프로브" : "런타임"}</span>` : ""}
<span class="ia-btns">${pid ? html`<button type="button" class="sh-act" data-act="view" data-view="physical" data-target="${pid}" data-fk="ia-view" title="물리 지도에서 이것이 어느 기기·네트워크 안에서 도는지 보기">${ic("server")}<span>어디서 도나 <small class="sa-s">물리</small></span></button>` : ""}${primary && P.safeUrl(primary.url) !== "#" ? html`<a class="sh-act" href="${P.safeUrl(primary.url)}" target="_blank" rel="noopener noreferrer" data-fk="ia-open">${ic("ext")}<span>열기</span><span class="sr-only"> (새 창)</span></a>` : ""}<a class="sh-act primary" href="${homeHref}" data-fk="ia-home">${ic("home")}<span>${sv ? "개요에서 자세히" : "개요에서 보기"}</span></a></span>`);
    let body;
    if (n.type === "zone") body = inspectZone(n);
    else if (n.type === "edge") body = inspectEdge(n);
    else if (sv) body = inspectService(n, sv);
    else body = grp(rows([["상태", statusText(n.status)], ["설명", n.detail]]));
    renderKeep(inspBody, body);
  }

  // 물리 검사기: 노드 종류에 맞춘 칸(map-physical.js) + 그 노드가 돌리는 서비스의 전체 상세(개념 검사기와 같은 조각)
  const PK = { device: "cpu", iface: "net", engine: "layers", network: "route", container: "layers", process: "server", peer: "tailnet", segment: "lan", external: "globe", group: "layers" };
  const TST = { ok: "up", warn: "degraded", crit: "down", muted: "unknown", info: "unknown" };
  const psvOpen = { sel: null, set: new Set() };
  function renderInspectorPhys() {
    const kick = $("#insp-kicker"); const sub = $("#insp-sub");
    // 고른 노드가 사라졌으면(컨테이너를 다시 만들어 이름이 바뀐 경우 등 — 프로세스 id 는 재시작해도 그대로다) 같은 서비스를 돌리는 노드로 다시 찾는다
    if (PD && ui.selected && !PD.nodes.has(ui.selected) && ui.selKey) {
      const again = toPhys(ui.selKey);
      if (again && PD.nodes.has(again)) { ui.selected = again; syncHash(again); requestAnimationFrame(applyHighlight); }
    }
    const n = PD && PD.nodes.get(ui.selected);
    const r = n ? PHYS.inspect(PD, ui.selected) : null;
    if (!r) {
      P.render(kick, "");
      setText(inspH, "사라진 항목");
      sub.hidden = true;
      renderKeep(inspActions, "");
      renderKeep(inspBody, html`<p class="calm">${shape("muted")}<span>이 항목이 최신 스냅샷에 없어요. 멈췄거나 이름이 바뀌었을 수 있어요.</span></p>`);
      return;
    }
    if (psvOpen.sel !== ui.selected) { psvOpen.sel = ui.selected; psvOpen.set = new Set(r.svcs.length ? [r.svcs[0].id] : []); }
    const icon = n.type === "segment" && PD.segTn === n ? "tailnet" : PK[n.type] || "layers";
    renderKeep(kick, html`<span class="cat-ic c-zone">${ic(icon)}</span>${r.kicker}`);
    setText(inspH, r.title);
    setText(sub, r.sub || "");
    sub.hidden = !r.sub;
    setAttr(inspector, "data-tone", r.tone);
    const sv = r.svcs[0] || null;
    const primary = sv && (sv.urls || []).find((u) => /^https?:/i.test(u.url || ""));
    const cid = toConcept(ui.selected);
    const off = (n.type === "peer" && n.online === false) || (n.type === "group" && n.role === "offline");
    const stPill = off ? pill("unknown", "오프라인") : n.type === "device" ? pill(n.status, (SH.DEV_STATUS || {})[n.status] || statusText(n.status)) : pill(n.type === "group" || n.type === "network" ? TST[r.tone] || "unknown" : n.status);
    renderKeep(inspActions, html`${stPill}${sv && sv.exposure ? xbadges(sv.exposure) : ""}
<span class="ia-btns">${cid ? html`<button type="button" class="sh-act" data-act="view" data-view="concept" data-target="${cid}" data-fk="ia-view" title="개념 지도에서 이것이 어떤 경로로 공개되는지 보기">${ic("route")}<span>노출 경로 <small class="sa-s">개념</small></span></button>` : ""}${primary && P.safeUrl(primary.url) !== "#" ? html`<a class="sh-act" href="${P.safeUrl(primary.url)}" target="_blank" rel="noopener noreferrer" data-fk="ia-open">${ic("ext")}<span>열기</span><span class="sr-only"> (새 창)</span></a>` : ""}<a class="sh-act primary" href="${r.homeHref}" data-fk="ia-home">${ic("home")}<span>${r.homeLabel}</span></a></span>`);
    const multi = r.svcs.length > 1;
    const svcSec = r.svcs.length ? html`<section class="isec psvs"><h3 class="isec-h">${(multi ? "이것이 돌리는 서비스 " + r.svcs.length + "개" : "이것이 돌리는 서비스")}</h3>${r.svcs.map((x) => svcPart(x, multi))}</section>` : "";
    // 모니터 자신을 돌리는 노드: 자기 진단이 머리기사(개념 검사기·개요 상세와 같은 순서)
    const mon = r.svcs.find((x) => x.kind === "monitor");
    renderKeep(inspBody, html`${mon ? iSec("모니터 자기 진단", monitorBlock(mon)) : ""}${r.body}${svcSec}`);
  }
  function svcPart(sv, multi) {
    const cid = D && D.svcNode.get(sv.id); const cn = cid && D.nodes.get(cid);
    const t = tone(sv.status);
    const head = html`${shape(t)}<b class="psv-n">${sv.name}</b><span class="psv-k">${catName(sv.category)}${sv.kind ? " · " + kindName(sv.kind) : ""}</span><span class="psv-st">${statusText(sv.status)}</span>`;
    const acts = html`<p class="psv-acts">${xbadges(sv.exposure)}<span class="psv-l">${cn ? html`<button type="button" class="linkish" data-act="view" data-view="concept" data-target="${cn.id}" data-fk="${"psv-c:" + sv.id}">개념 지도에서</button>` : ""}<a class="home-link" href="${"/#svc=" + encodeURIComponent(sv.id)}" data-fk="${"psv-h:" + sv.id}">개요에서 자세히${ic("arrow")}</a></span></p>`;
    const body = cn ? inspectService(cn, sv, { noMonitor: true }) : grp(rows([["상태", statusText(sv.status)], ["설명", sv.description]]));
    if (!multi) return html`<div class="psv" data-tone="${t}"><div class="psv-h">${head}</div>${acts}<div class="psv-b">${body}</div></div>`;
    return html`<details class="psv" data-tone="${t}" data-svc="${sv.id}"${raw(psvOpen.set.has(sv.id) ? " open" : "")}><summary class="psv-h">${head}</summary>${acts}<div class="psv-b">${body}</div></details>`;
  }
  inspBody.addEventListener("toggle", (e) => {
    const d = e.target;
    if (!d || !d.classList || !d.classList.contains("psv")) return;
    const id = d.getAttribute("data-svc");
    if (d.open) psvOpen.set.add(id); else psvOpen.set.delete(id);
  }, true);

  // ------------------------------------------------------------------ 도크(하드웨어 계기) — 개요 첫 화면과 같은 공용 부품(JarvisShell.hw)
  const dockRow = $("#dock-row");
  function renderDock() {
    if (SH.hw && dockRow) SH.hw.render(dockRow, S, { base: "/", more: "개요의 하드웨어에서 자세히 보기" });
  }

  // ------------------------------------------------------------------ 모바일: 세로 노선도
  function renderFlow() {
    if (!mobile || !S) return;
    if (isPhys()) { renderTree(); return; }
    const cats = (S.categories || []).map((c) => c.id);
    const services = S.services || [];
    const KC = { public: "public", tailnet: "tailnet", host: "lan", hermes: "tailnet", internal: "depends", infra: "local", system: "local" };
    const sections = cats.map((cid) => {
      const c = (S.categories || []).find((x) => x.id === cid) || {};
      const mem = services.filter((s) => s.category === cid).sort((a, b) => String(a.name).localeCompare(String(b.name)));
      if (!mem.length) return "";
      const k = KC[cid] || "local";
      // 가장 흔한 길을 노선으로
      const count = new Map();
      if (["public", "tailnet", "hermes", "host"].includes(cid)) {
        for (const s of mem) {
          const nid = D.svcNode.get(s.id);
          if (!nid) continue;
          for (const ch of chains(nid, 4)) {
            const key = ch.nodes.slice(0, -1).join(">");
            if (!count.has(key)) count.set(key, { n: 0, ch });
            count.get(key).n++;
          }
        }
      }
      const viaInfra = (ch) => ch.nodes.slice(0, -1).every((id) => { const x = D.nodes.get(id); return x && !SERVICE_LIKE.has(x.type); });
      const lines = [...count.values()].filter((l) => viaInfra(l.ch)).sort((a, b) => b.n - a.n).slice(0, 3);
      const main = lines[0];
      const stops = main ? main.ch.nodes.slice(0, -1).map((nid, i, arr) => {
        const e = main.ch.edges[i];
        // 마지막 정거장 → 서비스 구간은 서비스마다 다르므로(포트 등) 토폴로지 라벨만 쓴다.
        const lb = i < arr.length - 1 ? edgeLabel(e) : (e && e.label) || "";
        return html`<li><button type="button" class="stop" data-select="${nid}" data-fk="${"stop:" + cid + nid}"><span class="st-n">${nodeName(nid)}</span>${lb ? html`<span class="st-e">${lb}</span>` : ""}</button></li>`;
      }) : "";
      const alt = lines.slice(1).map((l) => html`<li>${chainHtml({ nodes: l.ch.nodes.slice(0, -1), edges: l.ch.edges.slice(0, -1) })}<span class="alt-n">${l.n}곳</span></li>`);
      const up = mem.filter((s) => s.status === "up").length;
      const leaf = (s) => {
        const nid = D.svcNode.get(s.id); const t = tone(s.status);
        const n = nid ? D.nodes.get(nid) : null; const info = n ? nodeInfo(n) : null;
        const lat = P.pick.latency(s);
        const right = s.category === "host" ? (s.listeners || []).filter((l) => l.scope !== "loopback").map((l) => ":" + l.port).slice(0, 2).join(" ") : lat != null ? fmt.ms(lat) : s.runtime ? fmt.bytes(s.runtime.mem_bytes, 0) : "";
        const usedBy = nid ? D.edges.filter((e) => e.kind === "depends" && e.to === nid).map((e) => nodeName(e.from)) : [];
        return html`<li data-tone="${t}"><button type="button" class="${"leaf" + (nid === ui.selected ? " is-sel" : "")}" data-select="${nid || ""}" data-fk="${"leaf:" + s.id}" aria-label="${info ? info.aria : s.name}">${shape(t)}<span class="lf-main"><span class="lf-name">${s.name}</span><span class="lf-sub">${s.status !== "up" ? html`<span class="lf-why">${statusText(s.status)}${s.status_reason ? " — " + s.status_reason : ""}</span>` : usedBy.length ? "← " + usedBy.join(", ") + " 가 사용" : ["host", "public", "tailnet"].includes(s.category) ? xbadges(s.exposure) : runtimeLine(s.runtime)}</span></span><span class="lf-r">${right}</span></button></li>`;
      };
      // 노선 색 = 보여 주는 주 노선의 길 종류(카테고리 기본색이 아니라)
      const kk = main ? chainKind(main.ch) : k;
      return html`<section class="route" data-cat="${cid}" data-k="${kk}">
<header class="route-h"><h3><span class="cat-ic">${ic(CAT_ICON[cid] || "layers")}</span>${c.label || catName(cid)}</h3><span class="route-n">${up}/${mem.length} 정상</span></header>
${c.desc ? html`<p class="route-d">${c.desc}</p>` : ""}
${main ? html`<ol class="stops">${stops}</ol>` : ""}
${alt.length ? html`<ul class="alts"><li class="alts-h">다른 길</li>${alt}</ul>` : ""}
<ul class="${"leaves" + (main ? " on-line" : "")}">${mem.map(leaf)}</ul>
</section>`;
    });
    renderKeep(flowEl, html`<header class="flow-h"><h2>노출 경로</h2><p>위에서 아래로: 들어오는 영역 → 거치는 관문 → 서비스. 누르면 상세가 열립니다.</p>
<ul class="flow-legend"><li data-k="public"><i></i>공개</li><li data-k="tailnet"><i></i>Tailnet</li><li data-k="lan"><i></i>LAN 직접</li><li data-k="depends"><i></i>내부</li></ul></header>${sections}`);
  }

  // 물리(모바일): 접히는 기기 나무. 펼친 칸은 이 브라우저에 기억한다.
  function renderTree() {
    if (!PD) return;
    if (!ui.tree) {
      const st = store.get("tree", null);
      ui.tree = new Set(Array.isArray(st) ? st.filter((x) => typeof x === "string")
        : [PD.self && PD.self.id, ...[...PD.nodes.values()].filter((n) => n.type === "engine").map((n) => n.id), PD.segLan && PD.segLan.id, PD.segTn && PD.segTn.id].filter(Boolean));
    }
    renderKeep(flowEl, PHYS.tree(PD, ui.tree, ui.selected));
  }
  function toggleTree(id) {
    if (!ui.tree || !id) return;
    if (ui.tree.has(id)) ui.tree.delete(id); else ui.tree.add(id);
    store.set("tree", [...ui.tree]);
    renderFlow();
  }
  /** 나무에서 고른 것이 보이게 조상(과 그것이 붙은 구간)을 편다 */
  function treeReveal(id) {
    if (!PD || !ui.tree) return;
    const chain = PD.anc(id);
    chain.forEach((a) => ui.tree.add(a));
    const top = chain.length ? chain[chain.length - 1] : id;
    for (const e of PD.edgesOf(top)) { const o = e.from === top ? e.to : e.from; const on = PD.nodes.get(o); if (on && on.type === "segment") ui.tree.add(o); }
  }

  // ------------------------------------------------------------------ 연결 상태(공용 헤더가 판정) → 지도 흐림·안내 띠
  let livePartial = false;
  function applyLive(state, partial) {
    liveState = state || "wait";
    if (partial != null) livePartial = !!partial;
    app.dataset.stale = liveState === "stale" ? "yes" : "no";
    const gen = S && S.meta && S.meta.generated_at;
    const txt = liveState === "stale" && S ? "Jarvis 와 연결이 끊겼어요 — " + fmt.time(gen) + " 에 받은 마지막 상태를 보여 주는 중이에요. 자동으로 다시 시도합니다."
      : liveState === "late" && S ? "데이터 갱신이 늦어지고 있어요 — 마지막 데이터 " + fmt.time(gen) : "";
    // API 는 정상인데 일부 수집기가 늦으면(헤더의 '일부 수집 지연') 부드러운 경고 한 줄 + 자기 진단 링크
    const mh = !txt && livePartial && S && SH.monitor ? SH.monitor : null;
    const href = mh && mh.href ? mh.href(S) : null;
    if (txt) renderKeep(ribbon, html`${txt}`);
    else if (mh) renderKeep(ribbon, html`${mh.health ? mh.health(S).text : "일부 수집이 늦어지고 있어요"}${href ? html` — <a class="rb-link" href="${href}" data-fk="rb-mon">자기 진단 보기</a>` : ""}`);
    else renderKeep(ribbon, "");
    ribbon.hidden = !txt && !mh;
    setAttr(ribbon, "data-tone", liveState === "stale" ? "crit" : "warn");
    syncFlowClock();
  }
  if (window.JarvisShell && JarvisShell.on) JarvisShell.on("live", (d) => applyLive(d && d.state, d ? d.partial : null));
  // 헤더 경보 목록: 지도에 있는 대상은 그 노드로(#focus=…)
  if (window.JarvisShell) JarvisShell.alertLink = (a) => {
    const tid = alertNodeNow(a);
    return tid ? { href: "#focus=" + encodeURIComponent(focusKey(tid)), label: "지도에서 보기 · " + curName(tid) } : null;
  };

  // ------------------------------------------------------------------ 한 번의 갱신
  // 한 단계의 렌더 오류가 다른 단계(와 공용 헤더의 연결 상태)를 망치지 않게 단계마다 격리한다
  const step = (name, fn) => { try { fn(); } catch (e) { console.error("[map] " + name + " 실패:", e); } };
  function renderAll() {
    if (!S || !D) return;
    step("renderStatus", renderStatus);
    step("renderDock", renderDock);
    if (mobile) {
      step("renderFlow", renderFlow);
    } else {
      const ph = isPhys() && !!PD;
      step("graph", () => {
        const sig = ph ? PHYS.sig(PD, { offOpen: ui.offOpen }) : sigOf(D);
        if (sig !== G.sig) {
          G.sig = sig;
          // 다시 그리면 SVG 가 통째로 바뀐다 — 키보드 초점이 지도 안에 있었으면 같은 노드(또는 "더 보기" 단추)로 되돌린다
          const ae = document.activeElement;
          const keep = ae && ae !== vp && vp.contains(ae) ? (ae.hasAttribute("data-id") ? '[data-id="' + cssEsc(ae.getAttribute("data-id")) + '"]' : ae.hasAttribute("data-more") ? '[data-more="' + cssEsc(ae.getAttribute("data-more")) + '"]' : null) : null;
          if (ph) { G.layout = PHYS.layout(PD, { offOpen: ui.offOpen }); buildPhys(G.layout); }
          else { G.layout = layout(D); buildGraph(G.layout); }
          if (view.auto) fitView(false); else { clampView(); applyView(false); }
          if (keep) { const el = vp.querySelector(keep); if (el) { try { el.focus({ preventScroll: true }); } catch (e) { el.focus(); } } }
        }
      });
      step("updateGraph", ph ? updatePhys : updateGraph);
    }
    step("renderSide", renderSide);
    if (ui.selected) step("renderInspector", renderInspector);
    syncFlowClock();
    const noPhys = isPhys() && PD && !PD.nodes.size;
    empty.hidden = !noPhys;
    if (noPhys) P.render(empty, html`<p class="empty-in">${shape("muted")}물리 구성 정보(topology_physical)를 아직 받지 못했어요 — 개념 보기로 볼 수 있어요.</p>`);
  }
  function onSnap(snap) {
    S = snap;
    try { D = derive(S); } catch (e) { console.error("[map] derive 실패:", e); if (!D) return; }
    if (PHYS) { try { PD = PHYS.derive(S); } catch (e) { console.error("[map] 물리 derive 실패:", e); } }
    renderAll();
    if (window.JarvisShell && JarvisShell.live) applyLive(JarvisShell.live(), JarvisShell.view ? JarvisShell.view() === "partial" : null);
    if (pendingFocus) { const k = pendingFocus; pendingFocus = null; applyFocus(k); }
  }

  mobileMq.addEventListener("change", () => {
    mobile = mobileMq.matches;
    app.dataset.mode = mobile ? "flow" : "map";
    setLegend(false);
    if (!mobile) {
      backdrop.hidden = true; document.body.classList.remove("sheet-open");
      inspector.removeAttribute("role"); inspector.removeAttribute("aria-modal"); inspector.style.transform = "";
      G.sig = null; view.auto = true; renderKeep(flowEl, "");
    } else {
      flowClear(); P.render(vp, ""); G.sig = null; G.nodeEls = new Map(); G.edgeEls = new Map(); G.labelEls = [];
      if (app.dataset.insp === "open") { backdrop.hidden = false; document.body.classList.add("sheet-open"); inspector.setAttribute("role", "dialog"); inspector.setAttribute("aria-modal", "true"); }
    }
    syncTabs();
    renderTools();
    renderAll();
  });

  // ------------------------------------------------------------------ 관점 바꾸기(개념 ↔ 물리)
  const MODE_Q = SH.MAP_MODES ? { concept: SH.MAP_MODES.concept.q, physical: SH.MAP_MODES.physical.q } : { concept: "무엇이 어떤 경로로 공개되는가", physical: "어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가" };
  function syncModes() {
    app.dataset.view = ui.view;
    if (modesEl) for (const b of $$(".mode", modesEl)) {
      const on = b.dataset.view === ui.view;
      setAttr(b, "aria-checked", on ? "true" : "false");
      setAttr(b, "tabindex", on ? "0" : "-1");
    }
    setText($("#modes-q"), MODE_Q[ui.view] || "");
    setText(legendH, isPhys() ? "범례 · 물리 구성을 읽는 법" : "범례 · 공개 상태를 읽는 법");
    setAttr(graph, "aria-label", (isPhys() ? "물리 지도 — 기기·인터페이스·도커 네트워크·원격 기기. " : "개념 지도 — 인터넷·Tailnet·LAN 에서 서비스까지의 노출 경로. ") + "노드를 고르면 상세가 열립니다. 더하기·빼기 키로 확대·축소, 0 키로 맞춤.");
    setText($("#map-h"), isPhys() ? "네트워크 맵 · 물리 — 어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가" : "네트워크 맵 · 개념 — 무엇이 어떤 경로로 공개되는가");
  }
  /** 주소에 관점을 늘 적어 둔다(?view=concept|physical — 복사한 주소가 누구에게나 같은 관점으로 열리게). 방문 기록은 늘리지 않는다. */
  function syncUrlView() {
    try {
      const u = new URL(location.href);
      u.searchParams.set("view", ui.view === "physical" ? "physical" : "concept");
      const qs = u.searchParams.toString();
      const next = u.pathname + (qs ? "?" + qs : "") + u.hash;
      if (next !== location.pathname + location.search + location.hash) history.replaceState(history.state, "", next);
    } catch (e) { /* 무시 */ }
  }
  /** 관점을 바꾼다. 고른 것은 같은 서비스(또는 같은 구간)가 있으면 따라가고, 없으면 검사기를 닫는다. opts.select 가 있으면 그것을 고른다. */
  function setView(v, opts) {
    opts = opts || {};
    if (!PHYS || (v !== "concept" && v !== "physical")) return;
    if (v === ui.view) {
      if (opts.select) { const id = resolveAny(opts.select); if (id && curHas(id)) select(id, { invoker: opts.invoker, reveal: true }); }
      return;
    }
    const carry = opts.select || ui.selected;
    ui.view = v;
    MVh.set(v);
    syncModes();
    syncUrlView();
    ui.hover = null;
    setLegend(ui.legend);
    // 그림을 새로: 새 관점은 전체 맞춤에서 시작한다
    flowClear();
    P.render(vp, "");
    G.sig = null; G.layout = null; G.nodeEls = new Map(); G.edgeEls = new Map(); G.labelEls = []; G.metaEls = new Map(); G.auxEls = new Map();
    view.auto = true; view.fitAll = false;
    const next = carry && S ? resolveAny(carry) : null;
    if (next && curHas(next)) {
      ui.selected = next;
      ui.selKey = focusKey(next);
      if (mobile && isPhys()) treeReveal(next);
    } else if (ui.selected) closeInspector();
    renderTools();
    renderAll();
    if (ui.selected) {
      syncHash(ui.selected);
      if (!mobile) requestAnimationFrame(() => focusView(ui.selected, false, true));
    }
    updateChrome();
    if (carry && !ui.selected) toast("고른 항목은 " + (isPhys() ? "물리" : "개념") + " 지도에 따로 없어요 — 상세를 닫았어요");
  }
  if (modesEl) {
    if (!PHYS) modesEl.hidden = true;
    modesEl.addEventListener("click", (e) => { const b = e.target.closest(".mode"); if (b) setView(b.dataset.view); });
    modesEl.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      const bs = $$(".mode", modesEl);
      const i = Math.max(0, bs.findIndex((b) => b.dataset.view === ui.view));
      const j = e.key === "Home" ? 0 : e.key === "End" ? bs.length - 1 : (i + (e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 1) + bs.length) % bs.length;
      setView(bs[j].dataset.view);
      bs[j].focus();
    });
  }
  if (PHYS) PHYS.init({ html, raw, fmt, tone, statusText, shape, pill, rows, grp, iSec, estW, mean, clamp, fitLabel, SG, NICON, isNum, ago, SH, tunnelBlock, alertItems });

  // 처음 상태
  syncModes();
  syncUrlView();
  app.dataset.side = ui.side ? "open" : "closed";
  setLegend(false);
  syncTabs();
  renderTools();
  applyLayers();
  updateChrome();
  pendingFocus = readFocusHash();
  empty.hidden = false;
  P.render(empty, html`<p class="empty-in"><span class="empty-spin" aria-hidden="true"></span>지도를 그리는 중…</p>`);
  // 웹폰트가 도착하면 노드 이름 폭을 다시 잰다(한 번).
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { if (S && !mobile) { G.sig = null; renderAll(); } }).catch(() => {});
  P.subscribe(onSnap, {
    onError: (e) => {
      if (S) return;
      empty.hidden = false;
      P.render(empty, html`<p class="empty-in" data-tone="crit">${shape("crit")}Jarvis API 에 연결하지 못했어요 — ${String((e && e.message) || e)}. 자동으로 다시 시도합니다.</p>`);
    },
  });
})();
