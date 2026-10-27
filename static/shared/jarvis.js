/* Jarvis 공용 클라이언트. 모든 디자인이 같은 데이터·포맷·안전 규칙을 쓰게 한다.
 *
 *   <script src="/shared/jarvis.js"></script>
 *   JARVIS.subscribe((snap, state) => { ... }, { interval: 3000 });
 *   JARVIS.history('1h' | '24h').then(h => ...);
 *   el.innerHTML = JARVIS.html`<b>${untrusted}</b>`;   // 보간값은 자동 이스케이프
 *
 * 스냅샷 스키마는 /shared/API.md 참고.
 */
(function (global) {
  "use strict";

  // ------------------------------------------------------------------ 안전한 HTML
  class Raw {
    constructor(s) { this.s = s; }
    toString() { return this.s; }
  }
  const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
  const esc = (v) => (v == null ? "" : String(v).replace(/[&<>"'`]/g, (c) => ESC[c]));
  const toHtml = (v) => {
    if (v == null || v === false) return "";
    if (v instanceof Raw) return v.s;
    if (Array.isArray(v)) return v.map(toHtml).join("");
    return esc(v);
  };
  /** URL 속성용: http(s) 절대 주소와 같은 출처 상대 경로(/x, #x, ?x)만 통과시킨다. */
  const safeUrl = (u) => {
    const s = String(u == null ? "" : u).trim();
    return /^https?:\/\//i.test(s) || /^\/(?![\/\\])/.test(s) || /^[#?]/.test(s) ? s : "#";
  };
  // 보간 위치의 문맥. 이스케이프로 막을 수 없는 자리(이벤트 핸들러 속성은 엔티티가 JS 실행 전에 풀린다,
  // 따옴표 없는 속성, <script>/<style> 본문)에는 값을 넣지 않고 콘솔 오류로 알린다.
  // 태그 안팎은 새로 붙는 조각만 훑어 상태(ts)로 이어 간다(큰 템플릿에서도 싸게). 따옴표 값 속의 < > 는 태그 경계가 아니고,
  // 주석과 <script>·<style> 본문은 끝 표지(ts.raw)까지 통째로 건너뛴다. 본문의 '<' 는 뒤에 영문자·/·!·? 가 올 때만 태그 시작이다.
  function scan(ts, s) {
    let from = 0; // 이번 조각에서 ts.tag 에 아직 붙이지 않은 시작 위치
    for (let i = 0; i < s.length; i++) {
      if (ts.raw) {
        const re = new RegExp(ts.raw, "ig");
        re.lastIndex = i;
        const m = re.exec(s);
        if (!m) return;
        i = ts.raw === "-->" ? m.index + 2 : m.index - 1;
        ts.raw = "";
        continue;
      }
      const c = s[i];
      if (ts.q) { if (c === ts.q) ts.q = ""; }
      else if (ts.tag === null) {
        if (c !== "<") continue;
        if (s.startsWith("<!--", i)) { ts.raw = "-->"; i += 3; }
        else if (i + 1 === s.length || /[A-Za-z\/!?]/.test(s[i + 1])) { ts.tag = ""; from = i; }
      } else if (c === ">") {
        const m = /^<(script|style)(?=[\s>\/]|$)/i.exec(ts.tag + s.slice(from, i));
        ts.tag = null;
        if (m) ts.raw = "</" + m[1];
      } else if (c === '"' || c === "'") ts.q = c;
    }
    if (ts.tag !== null) ts.tag += s.slice(from);
  }
  function ctxOf(ts, out) {
    if (ts.tag !== null) {
      const tag = ts.tag;
      if (/\son[a-z]+\s*=\s*(?:"[^"]*|'[^']*|[^\s>"']*)$/i.test(tag) || (!ts.q && /=\s*$/.test(tag))) return "unsafe";
      if (/\s(?:href|src|action|formaction|xlink:href|poster)\s*=\s*["']$/i.test(tag)) return "url";
      return "attr";
    }
    return ts.raw && ts.raw !== "-->" ? "unsafe" : "text";
  }
  /** 태그드 템플릿: 보간값을 이스케이프한다. 중첩 html`` 결과와 배열은 그대로 이어 붙인다. */
  function html(strings, ...vals) {
    let out = strings[0];
    const ts = { tag: null, q: "", raw: "" };
    scan(ts, out);
    for (let i = 0; i < vals.length; i++) {
      let v = vals[i];
      const ctx = ctxOf(ts, out);
      if (ctx === "unsafe") {
        console.error("JARVIS.html: 이 위치에는 값을 넣을 수 없습니다(이벤트 핸들러·따옴표 없는 속성·script/style).",
          out.slice(-60));
        v = "";
      } else if (ctx === "url" && !(v instanceof Raw)) {
        v = safeUrl(v);
      }
      const add = toHtml(v) + strings[i + 1];
      out += add;
      scan(ts, add);
    }
    return new Raw(out);
  }
  /** 이미 안전한 문자열(직접 만든 SVG 등)을 그대로 넣을 때만 쓴다. 외부 데이터에는 절대 쓰지 않는다. */
  const raw = (s) => new Raw(String(s));
  function render(el, content) {
    if (typeof el === "string") el = document.querySelector(el);
    if (!el) return;
    const s = toHtml(content);
    if (el.__jarvisHtml !== s) { el.innerHTML = s; el.__jarvisHtml = s; }
  }

  // ------------------------------------------------------------------ 포맷
  const nf = (d) => new Intl.NumberFormat("ko-KR", { maximumFractionDigits: d, minimumFractionDigits: d });
  const nfs = [0, 1, 2, 3].map(nf);
  const fmt = {
    num(n, d = 0) { return n == null || isNaN(n) ? "–" : nfs[Math.min(d, 3)].format(n); },
    pct(n, d = 0) { return n == null || isNaN(n) ? "–" : nfs[Math.min(d, 3)].format(n) + "%"; },
    bytes(n, d = 1) {
      if (n == null || isNaN(n)) return "–";
      const u = ["B", "KB", "MB", "GB", "TB"];
      let i = 0; let v = Math.abs(n);
      while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
      return (n < 0 ? "-" : "") + (i === 0 ? Math.round(v) : v.toFixed(v >= 100 ? 0 : d)) + " " + u[i];
    },
    /** 초당 바이트 → "1.2 MB/s" */
    bps(n, d = 1) { return n == null || isNaN(n) ? "–" : fmt.bytes(n, d) + "/s"; },
    /** 초당 바이트 → 비트 "9.6 Mbps" */
    bits(n, d = 1) {
      if (n == null || isNaN(n)) return "–";
      const b = n * 8; const u = ["bps", "Kbps", "Mbps", "Gbps"];
      let i = 0; let v = b;
      while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
      return (i === 0 ? Math.round(v) : v.toFixed(d)) + " " + u[i];
    },
    /** 초 → "3일 4시간" / "12분 3초" */
    dur(s, parts = 2) {
      if (s == null || isNaN(s)) return "–";
      s = Math.max(0, Math.floor(s));
      const units = [["일", 86400], ["시간", 3600], ["분", 60], ["초", 1]];
      const out = [];
      for (const [label, size] of units) {
        const v = Math.floor(s / size);
        if (v || (out.length && out.length < parts)) { if (v) out.push(v + label); s -= v * size; }
        if (out.length >= parts) break;
      }
      return out.length ? out.join(" ") : "0초";
    },
    /** 짧은 지속시간 "3d 4h" 형식 (좁은 칸용) */
    durShort(s) {
      if (s == null || isNaN(s)) return "–";
      s = Math.floor(s);
      if (s < 60) return s + "s";
      if (s < 3600) return Math.floor(s / 60) + "m";
      if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m";
      return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h";
    },
    /** epoch 초 → "12초 전" */
    ago(ts, now) {
      if (!ts) return "–";
      const d = (now || Date.now() / 1000) - ts;
      if (d < 0) return "방금";
      if (d < 60) return Math.floor(d) + "초 전";
      if (d < 3600) return Math.floor(d / 60) + "분 전";
      if (d < 86400) return Math.floor(d / 3600) + "시간 전";
      return Math.floor(d / 86400) + "일 전";
    },
    time(ts, withSec = true) {
      if (!ts) return "–";
      return new Date(ts * 1000).toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit", ...(withSec ? { second: "2-digit" } : {}) });
    },
    date(ts) {
      if (!ts) return "–";
      return new Date(ts * 1000).toLocaleDateString("ko-KR", { month: "short", day: "numeric" });
    },
    datetime(ts) {
      if (!ts) return "–";
      return new Date(ts * 1000).toLocaleString("ko-KR", { hour12: false, month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
    },
    ms(n) { return n == null || isNaN(n) ? "–" : n < 10 ? n.toFixed(1) + " ms" : n < 1000 ? Math.round(n) + " ms" : (n / 1000).toFixed(2) + " s"; },
    temp(n) { return n == null || isNaN(n) ? "–" : n.toFixed(1) + "°C"; },
    watts(n) { return n == null || isNaN(n) ? "–" : n.toFixed(2) + " W"; },
    mhz(n) { return n == null || isNaN(n) ? "–" : n >= 1000 ? (n / 1000).toFixed(2) + " GHz" : Math.round(n) + " MHz"; },
  };

  // ------------------------------------------------------------------ 라벨
  const labels = {
    status: { up: "정상", degraded: "저하", down: "다운", unknown: "확인 불가" },
    overall: { ok: "모두 정상", warn: "주의 필요", crit: "장애" },
    level: { crit: "심각", warn: "경고", info: "정보" },
    category: {
      public: "공개 서비스", tailnet: "Tailnet 전용", hermes: "Hermes", host: "호스트 서버",
      internal: "내부 백엔드", infra: "인프라", system: "시스템 데몬",
    },
    scope: { all: "모든 인터페이스", lan: "LAN", tailnet: "Tailnet", docker: "Docker 브리지", loopback: "로컬(loopback)", other: "기타" },
    audience: { public: "공개", tailnet: "Tailnet", lan: "LAN", local: "로컬" },
    via: {
      "cloudflare-tunnel": "Cloudflare Tunnel", "caddy-https": "Caddy HTTPS", "direct-lan": "LAN 직접",
      "direct-tailnet": "Tailnet 직접", "tailnet-node": "Tailnet 노드",
    },
    kind: {
      web: "웹", database: "DB", cache: "캐시", app: "앱", proxy: "리버스 프록시", tunnel: "터널", vpn: "VPN",
      runtime: "런타임", "remote-access": "원격 접속", server: "서버", local: "로컬", monitor: "모니터", "process-manager": "프로세스 관리자", "agent-gateway": "에이전트 게이트웨이",
    },
    runtime: { container: "컨테이너", systemd: "systemd", pm2: "pm2", process: "프로세스", remote: "원격" },
    trafficVia: { tunnel: "터널(공개)", tailnet: "Tailnet", lan: "LAN", docker: "Docker", local: "로컬", public: "공개", unknown: "?" },
  };
  /** 상태 → CSS 변수 이름 접미사. 디자인마다 --ok/--warn/--crit/--muted 를 정의해 쓴다. */
  const tone = (status) => ({ up: "ok", ok: "ok", degraded: "warn", warn: "warn", down: "crit", crit: "crit", info: "info" }[status] || "muted");

  // ------------------------------------------------------------------ 차트 헬퍼
  /** 값 배열 → SVG path d. null 은 선을 끊는다. opts: {min, max, area:true 면 닫힌 면} */
  function sparkPath(values, w, h, opts = {}) {
    const vs = (values || []).map((v) => (v == null || isNaN(v) ? null : +v));
    const finite = vs.filter((v) => v != null);
    if (!finite.length) return "";
    const min = opts.min != null ? opts.min : Math.min(...finite);
    let max = opts.max != null ? opts.max : Math.max(...finite);
    if (max === min) max = min + 1;
    const n = vs.length;
    const x = (i) => (n === 1 ? w : (i / (n - 1)) * w);
    const y = (v) => h - ((Math.min(max, Math.max(min, v)) - min) / (max - min)) * h;
    let d = ""; let pen = false; let first = null; let last = null;
    vs.forEach((v, i) => {
      if (v == null) { pen = false; return; }
      const px = x(i).toFixed(1); const py = y(v).toFixed(1);
      d += (pen ? "L" : "M") + px + " " + py;
      if (first == null) first = px;
      last = px; pen = true;
    });
    if (opts.area && first != null) d += `L${last} ${h}L${first} ${h}Z`;
    return d;
  }
  /** 막대 배열 → [{x,y,w,h,v}] */
  function bars(values, w, h, opts = {}) {
    const vs = (values || []).map((v) => +v || 0);
    const max = opts.max != null ? opts.max : Math.max(1, ...vs);
    const gap = opts.gap != null ? opts.gap : 1;
    const bw = vs.length ? (w - gap * (vs.length - 1)) / vs.length : 0;
    return vs.map((v, i) => {
      const bh = (v / max) * h;
      return { x: i * (bw + gap), y: h - bh, w: Math.max(0.5, bw), h: bh, v };
    });
  }
  /** 0~100 값 → 원호 게이지 path(각도 start~end, 도 단위, 12시=0) */
  function arc(cx, cy, r, startDeg, endDeg) {
    const rad = (d) => ((d - 90) * Math.PI) / 180;
    const sx = cx + r * Math.cos(rad(startDeg)); const sy = cy + r * Math.sin(rad(startDeg));
    const ex = cx + r * Math.cos(rad(endDeg)); const ey = cy + r * Math.sin(rad(endDeg));
    const large = endDeg - startDeg > 180 ? 1 : 0;
    return `M${sx.toFixed(2)} ${sy.toFixed(2)}A${r} ${r} 0 ${large} 1 ${ex.toFixed(2)} ${ey.toFixed(2)}`;
  }
  /** 사용률(%)에 따른 톤: <60 ok, <85 warn, 그 이상 crit (온도는 thresholds 로 바꿔 쓴다) */
  const loadTone = (v, warn = 60, crit = 85) => (v == null ? "muted" : v >= crit ? "crit" : v >= warn ? "warn" : "ok");

  // ------------------------------------------------------------------ 데이터 접근
  const pick = {
    byCategory(snap) {
      const out = {};
      for (const c of snap.categories || []) out[c.id] = { ...c, services: [] };
      for (const s of snap.services || []) (out[s.category] || (out[s.category] = { id: s.category, services: [] })).services.push(s);
      return out;
    },
    service(snap, id) { return (snap.services || []).find((s) => s.id === id); },
    /** 서비스의 대표 지연시간(ms): Caddy 내부 프로브 → 공개 프로브 순 */
    latency(s) { return s?.probes?.internal?.ms ?? s?.probes?.public?.ms ?? null; },
    primaryUrl(s) { return (s?.urls || [])[0]?.url || null; },
  };

  // ------------------------------------------------------------------ 폴링
  const params = new URLSearchParams(location.search);
  const embed = params.has("embed");
  // core 에서 다른 기기를 볼 때(?device=<id>): 같은 화면이 /api/devices/<id>/… 를 읽고, 같은 사이트 링크도 그 기기를 유지한다.
  const device = /^[A-Za-z0-9._-]{1,64}$/.test(params.get("device") || "") ? params.get("device") : null;
  const API = device ? "/api/devices/" + encodeURIComponent(device) : "/api";
  // 누르기 직전(mousedown: 가운데 클릭·우클릭 링크 복사 포함, click: 키보드)에 href 를 고친다
  if (device) for (const ev of ["mousedown", "click"]) document.addEventListener(ev, (e) => {
    const a = e.target.closest && e.target.closest("a[href]");
    if (!a || a.hasAttribute("data-all-devices")) return;
    const u = new URL(a.href, location.href);
    if (u.origin !== location.origin || u.pathname.startsWith("/api/") || u.searchParams.has("device")) return;
    u.searchParams.set("device", device);
    a.href = u.href;
  }, true);
  function subscribe(cb, opts = {}) {
    const interval = opts.interval || (embed ? 10000 : 3000);
    let last = null; let timer = null; let inflight = false; let failures = 0;
    async function tick() {
      if (inflight) return;
      inflight = true;
      try {
        const r = await fetch(API + "/snapshot", { cache: "no-store" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        last = await r.json();
        failures = 0;
        cb(last, { stale: false, error: null, fetchedAt: Date.now() / 1000, embed });
      } catch (e) {
        failures++;
        if (last) cb(last, { stale: true, error: String(e.message || e), fetchedAt: null, embed });
        else if (opts.onError) opts.onError(e);
      } finally {
        inflight = false;
        clearTimeout(timer);
        timer = setTimeout(tick, document.hidden ? interval * 5 : failures ? Math.min(30000, interval * (failures + 1)) : interval);
      }
    }
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { clearTimeout(timer); tick(); } });
    tick();
    return { refresh: () => { clearTimeout(timer); tick(); }, get last() { return last; } };
  }
  const _hcache = {};
  async function history(range = "1h") {
    const c = _hcache[range];
    const ttl = range === "1h" ? 5000 : 60000;
    if (c && Date.now() - c.t < ttl) return c.v;
    const r = await fetch(API + "/history?range=" + encodeURIComponent(range), { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const v = await r.json();
    _hcache[range] = { t: Date.now(), v };
    return v;
  }

  // ------------------------------------------------------------------ 보는 사람 브라우저의 편의 설정
  /** localStorage "jarvis.<k>" 를 읽는다. 없으면 이름을 바꾸기 전 키 "pit5.<k>" 를 이어 읽는다(쓰기는 늘 jarvis.*). 막히면 null. */
  function pref(k) {
    try {
      const v = localStorage.getItem("jarvis." + k);
      return v != null ? v : localStorage.getItem("pit5." + k);
    } catch (e) { return null; }
  }

  global.JARVIS = { pref, html, raw, esc, safeUrl, render, fmt, labels, tone, loadTone, sparkPath, bars, arc, pick, subscribe, history, embed, device, Raw };
})(window);
