/* Jarvis · 터미널 — btop/k9s 스타일 대시보드.
 *
 * 원칙
 *  - 데이터 문자열은 전부 JARVIS.html(자동 이스케이프) 또는 textContent 로만 DOM 에 들어간다.
 *    raw() 는 이 파일에서 숫자·상수만으로 만든 SVG 에만 쓴다.
 *  - 3초마다 다시 그려도 상태가 유지되도록, 상호작용 컨테이너(서비스 행·필터·버튼)는 한 번 만들고
 *    안쪽 값만 JARVIS.render 로 갱신한다. 포커스는 data-fk 로 복원한다.
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  if (!P) return;
  const { html, raw, fmt, labels } = P;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const byId = (id) => document.getElementById(id);
  const root = document.documentElement;
  const DASH = "–";
  const EMBED = !!P.embed;
  const mqReduce = matchMedia("(prefers-reduced-motion: reduce)");
  if (EMBED) root.classList.add("embed");
  const smooth = () => (mqReduce.matches || EMBED ? "auto" : "smooth");

  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 저장 불가: 무시 */ } },
  };

  // ───────────────────────────────────────────── 상태
  const S = {
    snap: null, st: null, active: 2, sel: null, open: new Set(),
    filter: "", cat: "all", range: P.pref("term.range") === "24h" ? "24h" : "1h",
    hist: { "1h": null, "24h": null }, histAt: { "1h": 0, "24h": 0 }, histBusy: false,
    proc: "cpu", unitsAll: false, tunErrs: false, cerrAll: false,
    more: {},                       // "더 보기"로 펼친 목록 (recent·peers·ports·hosts·alerts)
    lastOk: null, stale: false, down: false, spin: 0,
    genVal: null, genAt: null,      // generated_at 이 마지막으로 바뀐 시각(클라이언트 시계) — 시계 차이와 무관한 스테일 판정
    visAt: 0,
    alMinH: 0, alShrinkAt: 0, alW: 0,  // 경보 영역 높이 예약(늘 때는 즉시, 줄 때는 60초 뒤)
    spy: null,
  };
  /** 휴대폰·태블릿 폭: 목록을 중첩 스크롤 대신 "더 보기"로 접는다. */
  const MQ_M = matchMedia("(max-width: 899px)");

  // ───────────────────────────────────────────── 테마
  const THEMES = [["tokyo", "Tokyo Night"], ["latte", "Catppuccin Latte"], ["gruvbox", "Gruvbox"]];
  function applyTheme(id, save) {
    const t = THEMES.find((x) => x[0] === id) || THEMES[0];
    root.setAttribute("data-theme", t[0]);
    byId("theme-name").textContent = t[1];
    byId("theme-chip").setAttribute("aria-label", "테마 바꾸기 — 현재 " + t[1]);
    if (save) store.set("jarvis.term.theme", t[0]);
  }
  function cycleTheme() {
    const i = THEMES.findIndex((x) => x[0] === root.getAttribute("data-theme"));
    applyTheme(THEMES[(i + 1) % THEMES.length][0], true);
  }
  applyTheme(root.getAttribute("data-theme"), false);

  // ───────────────────────────────────────────── 렌더 도우미
  /** JARVIS.render + 포커스 복원(data-fk). */
  function put(el, content) {
    if (typeof el === "string") el = byId(el);
    if (!el) return;
    const a = document.activeElement;
    const fk = a && a !== el && el.contains(a) ? a.getAttribute("data-fk") : null;
    P.render(el, content);
    if (fk && !el.contains(a)) {
      const n = el.querySelector('[data-fk="' + CSS.escape(fk) + '"]');
      if (n) n.focus({ preventScroll: true });
    }
  }
  const GLYPH = { up: "●", degraded: "▲", down: "✖", unknown: "◌" };
  const LV = { crit: "✖", warn: "▲", info: "●" };
  const stLabel = (s) => labels.status[s] || labels.status.unknown;
  /** 상태 글리프(모양+색) + 스크린리더용 글자. text=true 면 글자도 보인다. */
  function st(status, text) {
    const t = P.tone(status);
    const l = stLabel(status);
    return html`<span class="st t-${t}" title="${l}"><b aria-hidden="true">${GLYPH[status] || "◌"}</b>${text ? html`<span>${l}</span>` : html`<span class="sr">${l}</span>`}</span>`;
  }
  const okGlyph = (ok) => (ok ? html`<span class="t-ok" title="정상"><span aria-hidden="true">✓</span><span class="sr">정상</span></span>` : html`<span class="t-crit" title="실패"><span aria-hidden="true">✗</span><span class="sr">실패</span></span>`);
  function pctf(v, d) {
    if (v == null || isNaN(v)) return DASH;
    return fmt.pct(v, d != null ? d : v > 0 && v < 10 ? 1 : 0);
  }
  const num = (v) => fmt.num(v);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function urlText(u) {
    try { const x = new URL(u); return x.host + (x.pathname && x.pathname !== "/" ? x.pathname : ""); } catch (e) { return String(u || ""); }
  }
  function extLink(u, text, fk, cls) {
    const href = P.safeUrl(u);
    if (href === "#") return html`<span>${text}</span>`;
    return html`<a class="ext ${cls || ""}" href="${href}" target="_blank" rel="noopener noreferrer" data-fk="${fk}">${text}</a>`;
  }
  const shortHost = (h, dom) => (dom && h && h.endsWith("." + dom) ? html`${h.slice(0, -dom.length - 1)}<span class="dm">.${dom}</span>` : h);
  /** 미터 ■■■□□ (숫자는 항상 옆에 따로 표시하므로 aria-hidden) */
  function meter(v, cls) {
    const x = v == null || isNaN(v) ? 0 : clamp(+v, 0, 100);
    return html`<span class="meter ${cls || ""}" style="--v:${x.toFixed(1)}" aria-hidden="true"><i></i></span>`;
  }
  /** 폭이 제한된 칸: 넘치면 말줄임, 전체 값은 title 로. w 는 ch 단위 최대 폭. */
  function cl(content, w, title, cls) {
    return html`<span class="cl ${cls || ""}" style="--mw:${w || 24}ch" title="${title == null ? "" : title}">${content}</span>`;
  }
  /** 목록 자르기: 좁은 화면(또는 always)에서 n 개만, 펼치면 전부. */
  function limited(list, key, n, always) {
    const cut = !S.more[key] && (always || MQ_M.matches) && list.length > n;
    return { items: cut ? list.slice(0, n) : list, hidden: cut ? list.length - n : 0, open: !!S.more[key], total: list.length, n };
  }
  function moreBtn(key, lim, label) {
    if (!lim.hidden && !lim.open) return "";
    if (lim.open && lim.total <= lim.n) return "";
    return html`<button type="button" class="toggle-btn" data-act="more" data-k="${key}" aria-expanded="${lim.open ? "true" : "false"}" data-fk="more:${key}">${lim.open ? "▾ 접기" : html`▸ ${label || "더 보기"} <span class="dim">(+${lim.hidden})</span>`}</button>`;
  }
  /** 스왑은 채움 비율이 아니라 실제 압박(스왑 인·가용 메모리 15% 미만)으로 색을 정한다 — 백엔드 경보 규칙과 같다. */
  function swapTone(m) {
    if (!m || m.swap_pct == null) return "muted";
    const avail = m.total ? (m.available / m.total) * 100 : null;
    const pressure = (m.swap_in_ps || 0) > 0 || (avail != null && avail < 15);
    if (!pressure) return "info";
    return avail != null && avail < 15 && m.swap_pct > 80 ? "crit" : "warn";
  }
  /** 스왑 %: 소수 1자리, 99.96% 가 100% 로 반올림되어 보이지 않게 내림. */
  function swapPct(v) {
    if (v == null || isNaN(v)) return DASH;
    return v >= 100 ? fmt.pct(100, 0) : fmt.pct(Math.floor(v * 10) / 10, 1);
  }
  function msTone(ms) { return ms == null ? "muted" : ms >= 1500 ? "warn" : "ok"; }
  function daysTone(d) { return d == null ? "muted" : d < 7 ? "crit" : d < 21 ? "warn" : "ok"; }
  function codeTone(c) { return c == null ? "muted" : c >= 500 ? "crit" : c >= 400 ? "warn" : c >= 300 ? "info" : "ok"; }
  const niceCeil = (v) => {
    if (!(v > 0)) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    const m = v / p;
    return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
  };

  // ───────────────────────────────────────────── 블록 스파크라인 ▁▂▃▄▅▆▇█
  function resample(vals, n, agg) {
    const src = vals || [];
    const out = new Array(n).fill(null);
    if (!src.length) return out;
    if (src.length <= n) {
      const off = n - src.length;
      src.forEach((v, i) => { out[off + i] = v == null || isNaN(v) ? null : +v; });
      return out;
    }
    const per = src.length / n;
    for (let k = 0; k < n; k++) {
      const a = Math.floor(k * per), b = Math.max(a + 1, Math.floor((k + 1) * per));
      let s = 0, c = 0, m = null;
      for (let i = a; i < b && i < src.length; i++) {
        const v = src[i];
        if (v == null || isNaN(v)) continue;
        s += +v; c++; m = m == null ? +v : Math.max(m, +v);
      }
      out[k] = c ? (agg === "max" ? m : s / c) : null;
    }
    return out;
  }
  const SP_COLORS = { acc: "acc-f", ok: "ok-f", warn: "warn-f", crit: "crit-f", mag: "mag-f", teal: "teal-f", info: "info-f", org: "org-f", calm: "calm" };
  function spark(vals, o) {
    o = o || {};
    const cells = o.cells || 20;
    const W = cells * 8, H = 16;
    const vs = resample(vals, cells, o.agg);
    const fin = vs.filter((v) => v != null);
    const min = o.min != null ? o.min : 0;
    let max = o.max != null ? o.max : Math.max(0, ...fin);
    if (!(max > min)) max = min + 1;
    let r = "";
    vs.forEach((v, i) => {
      if (v == null) return;
      let lv = Math.round(((clamp(v, min, max) - min) / (max - min)) * 8);
      if (v > min && lv === 0) lv = 1;
      if (!lv) { r += `<rect class="base" x="${i * 8 + 1}" y="${H - 1}" width="6" height="1"/>`; return; }
      r += `<rect x="${i * 8 + 1}" y="${H - lv * 2}" width="6" height="${lv * 2}"/>`;
    });
    const col = SP_COLORS[o.color] || "acc-f";
    return raw(`<svg class="spark" style="--cells:${cells}ch;--sp:var(--${col})" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${r}</svg>`);
  }

  // ───────────────────────────────────────────── 점자(braille) 그래프 — SVG 점 패턴
  const DOT = 4;
  function brailleSVG(id, W, H, series, extra) {
    let defs = `<pattern id="${id}-p" width="${DOT}" height="${DOT}" patternUnits="userSpaceOnUse"><circle cx="${DOT / 2}" cy="${DOT / 2}" r="1.3" fill="#fff"/></pattern>`
      + `<pattern id="${id}-o" width="${DOT}" height="${DOT}" patternUnits="userSpaceOnUse"><circle cx="${DOT / 2}" cy="${DOT / 2}" r="0.85" style="fill:var(--dot-off)"/></pattern>`;
    let body = `<rect width="${W}" height="${H}" fill="url(#${id}-o)"/>`;
    series.forEach((s, si) => {
      let d = "";
      const span = s.max - s.min || 1;
      s.cols.forEach((v, i) => {
        if (v == null) return;
        let lv = Math.round(((clamp(v, s.min, s.max) - s.min) / span) * s.rows);
        if (v > s.min && lv < 1) lv = 1;
        if (lv <= 0) return;
        const x = i * DOT;
        d += s.dir === "down"
          ? `M${x} ${s.y0 * DOT}h${DOT}v${lv * DOT}h-${DOT}z`
          : `M${x} ${(s.y0 - lv) * DOT}h${DOT}v${lv * DOT}h-${DOT}z`;
      });
      const y1 = s.y0 * DOT, y2 = s.dir === "down" ? (s.y0 + s.rows) * DOT : (s.y0 - s.rows) * DOT;
      defs += `<linearGradient id="${id}-g${si}" gradientUnits="userSpaceOnUse" x1="0" y1="${y1}" x2="0" y2="${y2}">`
        + s.grad.map((g) => `<stop offset="${g[0]}" style="stop-color:var(--${g[1]})"/>`).join("") + `</linearGradient>`
        + `<clipPath id="${id}-c${si}"><path d="${d || "M0 0z"}"/></clipPath>`
        + `<mask id="${id}-m${si}" maskUnits="userSpaceOnUse" x="0" y="0" width="${W}" height="${H}"><rect width="${W}" height="${H}" fill="url(#${id}-p)" clip-path="url(#${id}-c${si})"/></mask>`;
      body += `<rect width="${W}" height="${H}" fill="url(#${id}-g${si})" mask="url(#${id}-m${si})"/>`;
    });
    return `<svg class="gsvg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true"><defs>${defs}</defs>${body}${extra || ""}</svg>`;
  }

  /** 현재 범위의 시계열: 1h 는 기록 + 최근 5분 spark 를 이어 붙여 실시간으로. */
  function series(key) {
    const h = S.hist[S.range];
    const sp = S.snap && S.snap.spark;
    let t = [], v = [];
    if (h && Array.isArray(h.t)) { t = h.t.slice(); v = (h[key] || []).slice(0, t.length); }
    if (S.range === "1h" && sp && Array.isArray(sp.t) && sp.t.length) {
      let cut = t.length;
      while (cut > 0 && t[cut - 1] >= sp.t[0]) cut--;
      t = t.slice(0, cut).concat(sp.t);
      v = v.slice(0, cut).concat((sp[key] || []).slice(0, sp.t.length));
    }
    return { t, v };
  }
  function bucket(ser, t0, dt, n, agg) {
    const out = new Array(n).fill(null), cnt = new Array(n).fill(0);
    for (let i = 0; i < ser.t.length; i++) {
      const v = ser.v[i];
      if (v == null || isNaN(v)) continue;
      const k = Math.floor((ser.t[i] - t0) / dt);
      if (k < 0 || k >= n) continue;
      if (agg === "max") out[k] = out[k] == null ? +v : Math.max(out[k], +v);
      else { out[k] = (out[k] || 0) + +v; cnt[k]++; }
    }
    if (agg !== "max") for (let k = 0; k < n; k++) if (cnt[k]) out[k] /= cnt[k];
    return out;
  }

  const GRAD = {
    cpu: [[0, "ok-f"], [0.5, "ok-f"], [0.75, "warn-f"], [1, "crit-f"]],
    gpu: [[0, "mag-f"], [0.6, "acc-f"], [1, "crit-f"]],
    mem: [[0, "teal-f"], [0.55, "info-f"], [1, "mag-f"]],
    temp: [[0, "info-f"], [0.4, "ok-f"], [0.7, "warn-f"], [1, "crit-f"]],
    rx: [[0, "teal-f"], [1, "info-f"]],
    tx: [[0, "mag-f"], [1, "org-f"]],
  };
  const gsize = new Map();
  let graphRaf = 0;
  function schedGraphs() {
    if (graphRaf) return;
    graphRaf = requestAnimationFrame(() => { graphRaf = 0; renderGraphs(); });
  }
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver((entries) => {
    let changed = false;
    for (const e of entries) {
      const w = Math.floor(e.contentRect.width), h = Math.floor(e.contentRect.height);
      const o = gsize.get(e.target);
      if (!o || o.w !== w || o.h !== h) { gsize.set(e.target, { w, h }); changed = true; }
    }
    if (changed) schedGraphs();
  }) : null;
  $$(".graph").forEach((el) => {
    if (ro) ro.observe(el);
    else gsize.set(el, { w: el.clientWidth, h: el.clientHeight });
  });

  function renderGraphs() {
    const snap = S.snap;
    if (!snap) return;
    const sys = snap.system || {};
    const now = (snap.meta && snap.meta.generated_at) || Date.now() / 1000;
    const span = S.range === "24h" ? 86400 : 3600;
    let firstT = null;
    for (const el of $$(".graph")) {
      const key = el.dataset.graph;
      const sz = gsize.get(el);
      if (!sz || sz.w < 24 || sz.h < 12) continue;
      const nx = Math.floor(sz.w / DOT), ny = Math.floor(sz.h / DOT);
      const W = nx * DOT, H = ny * DOT;
      const dt = span / nx;
      const t1 = Math.ceil(now / dt) * dt, t0 = t1 - span;
      let svg = "", label = null, right = "";
      if (key === "net") {
        const rx = series("rx"), tx = series("tx");
        const crx = bucket(rx, t0, dt, nx, "avg"), ctx = bucket(tx, t0, dt, nx, "avg");
        const mx = niceCeil(Math.max(1024, ...crx.filter((v) => v != null), ...ctx.filter((v) => v != null)));
        const half = Math.floor(ny / 2);
        svg = brailleSVG("g-" + key, W, H, [
          { cols: crx, min: 0, max: mx, y0: half, rows: half, dir: "up", grad: GRAD.rx },
          { cols: ctx, min: 0, max: mx, y0: half, rows: ny - half, dir: "down", grad: GRAD.tx },
        ], `<line x1="0" x2="${W}" y1="${half * DOT}" y2="${half * DOT}" style="stroke:var(--line-2)" stroke-width="1"/>`);
        const n = sys.network || {};
        const bpsS = (v) => { const a = fmt.bps(v, 1); return a.length > 9 ? fmt.bps(v, 0) : a; };
        label = html`<span>NET</span><span class="t-teal">↓${bpsS(n.rx_bps)}</span><span class="t-mag">↑${bpsS(n.tx_bps)}</span>`;
        right = "최대 " + fmt.bps(mx, 0);
      } else {
        const m = sys.memory || {};
        const spec = {
          cpu: { k: "cpu", min: 0, max: 100, g: GRAD.cpu, name: "CPU", val: pctf(sys.cpu && sys.cpu.usage_pct) },
          gpu: { k: "gpu", min: 0, max: 100, g: GRAD.gpu, name: "GPU", val: pctf(sys.gpu && sys.gpu.usage_pct), scale: "0–100%" },
          mem: { k: "mem", min: 0, max: 100, g: GRAD.mem, name: "MEM", val: pctf(m.usage_pct), scale: "0–100%" },
          temp: { k: "temp", min: 30, max: 90, g: GRAD.temp, name: "온도", val: fmt.temp(sys.cpu && sys.cpu.temp_c != null ? sys.cpu.temp_c : sys.thermal && sys.thermal.cpu_temp_c), scale: "30–90°C" },
        }[key];
        if (!spec) continue;
        const ser = series(spec.k);
        if (key === "cpu" && ser.t.length) firstT = ser.t.find((t, i) => t >= t0 && ser.v[i] != null) || null;
        const cols = bucket(ser, t0, dt, nx, "avg");
        svg = brailleSVG("g-" + key, W, H, [{ cols, min: spec.min, max: spec.max, y0: ny, rows: ny, dir: "up", grad: spec.g }]);
        const tone = key === "temp" ? P.loadTone(sys.cpu && sys.cpu.temp_c, 65, 80) : P.loadTone(key === "cpu" ? sys.cpu && sys.cpu.usage_pct : key === "gpu" ? sys.gpu && sys.gpu.usage_pct : m.usage_pct);
        label = html`<span>${spec.name}</span><b class="t-${tone}">${spec.val}</b>`;
        if (key === "mem" && m.swap_pct != null) label = html`${label}<span class="dim">스왑</span><span class="t-${swapTone(m)}">${swapPct(m.swap_pct)}</span>`;
        right = spec.scale || "";
      }
      // 작은 그래프는 라벨을 그래프 위 한 줄로(최근 데이터를 가리지 않게), 큰 CPU 그래프만 왼쪽 위(가장 오래된 쪽)에 겹친다
      const gh = el.previousElementSibling && el.previousElementSibling.classList.contains("gh") ? el.previousElementSibling : null;
      if (gh) {
        put(gh, html`<span class="gl2">${label}</span><span class="gs">${right}</span>`);
        put(el, raw(svg));
      } else {
        put(el, html`${raw(svg)}<div class="gl">${label}</div>`);
      }
    }
    // 시간축·데이터 누적 안내
    const axis = S.range === "24h" ? ["-24시간", "-18시간", "-12시간", "-6시간", "지금"] : ["-60분", "-45분", "-30분", "-15분", "지금"];
    put("hist-axis", html`${axis.map((a) => html`<span>${a}</span>`)}`);
    const h = S.hist[S.range];
    let note = "";
    if (firstT != null) {
      const cover = now - firstT;
      if (cover < span * 0.9) note = html` · <span class="t-warn" title="Jarvis 가 재시작된 뒤 계속 쌓이는 중">◌ 기록 ${fmt.dur(cover)}</span>`;
    } else if (!h) note = html` · <span class="dim">불러오는 중…</span>`;
    put("hist-meta", html`${S.range === "24h" ? "24시간 · 1분 간격" : "1시간 · 5초 간격"}${note}`);
  }

  function maybeFetchHist(force) {
    const r = S.range;
    const ttl = r === "1h" ? 30000 : 60000;
    if (S.histBusy || (!force && Date.now() - S.histAt[r] < ttl)) return;
    S.histBusy = true;
    P.history(r)
      .then((h) => { S.hist[r] = h; S.histAt[r] = Date.now(); schedGraphs(); })
      .catch(() => { S.histAt[r] = Date.now() - ttl + 10000; })
      .finally(() => { S.histBusy = false; if (S.range !== r) maybeFetchHist(true); });
  }
  function setRange(r) {
    if (r !== "1h" && r !== "24h") return;
    S.range = r;
    store.set("jarvis.term.range", r);
    $$('[data-act="range"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.range === r)));
    maybeFetchHist(!S.hist[r]);
    schedGraphs();
  }

  // ───────────────────────────────────────────── 헤더 · 상태줄
  function renderHeader(s) {
    const sys = s.system || {};
    const h = sys.host || {};
    put("hd-host", html`<b>${(s.meta && s.meta.host) || h.hostname || "host"}</b>`);
    put("hd-model", html`${h.model || ""}${h.os ? html` · ${h.os}` : ""}`);
    renderOverall(s);
    const acc = (s.meta && s.meta.access) || [];
    put("hd-access", html`<span class="lbl">접속</span>${acc.map((a, i) => html`<a href="${P.safeUrl(a.url)}" target="_blank" rel="noopener noreferrer" data-fk="acc:${i}"><span>${urlText(a.url)}</span><span class="aud">${labels.audience[a.audience] || a.audience || ""}</span></a>`)}`);
    byId("sl-host").textContent = (s.meta && s.meta.host) || "host";
  }
  /** 전체 상태 알약. 연결이 끊기면 회색으로 낮추고 "마지막:"을 붙여 옛 값임을 밝힌다. */
  function renderOverall(s) {
    s = s || S.snap;
    if (!s) return;
    const sum = s.summary || {};
    const svc = sum.services || {};
    const g = { ok: "●", warn: "▲", crit: "✖" }[sum.status] || "◌";
    const el = byId("hd-overall");
    const cls = "overall t-" + P.tone(sum.status) + (S.stale ? " is-old" : "");
    if (el.className !== cls) el.className = cls;
    put(el, html`${S.stale ? html`<span class="old">마지막:</span>` : ""}<span aria-hidden="true">${g}</span><span>${labels.overall[sum.status] || "확인 불가"}</span><span class="osub">서비스 ${svc.up != null ? svc.up : DASH}/${svc.total != null ? svc.total : DASH} 정상</span>`);
  }

  const WINS = [[0, "경보"], [1, "시스템"], [2, "서비스"], [3, "맵"], [4, "하드웨어"], [5, "인그레스"], [6, "tailnet"], [7, "포트"], [8, "도커"]];
  const MAXKEY = WINS.length - 1;
  function buildWins() {
    const nav = byId("sl-wins");
    nav.textContent = "";
    for (const [k, name] of WINS) {
      const b = document.createElement("button");
      b.type = "button"; b.dataset.act = "panel"; b.dataset.key = String(k);
      const n = document.createElement("span"); n.className = "n"; n.textContent = String(k);
      b.append(n, document.createTextNode(name));
      b.setAttribute("aria-label", name + " 패널로 이동 (" + k + ")");
      nav.appendChild(b);
    }
  }
  buildWins();

  /** 스크롤 위치 표시: 화면 위쪽 40% 선에 걸친 패널을 상태줄 탭에 "현재"로 표시하고, 탭 줄이 넘치면 그 탭이 보이게 민다. */
  function setSpy(key) {
    if (key == null || S.spy === key) return;
    S.spy = key;
    const strip = byId("sl-wins");
    let cur = null;
    for (const b of $$("button", strip)) {
      if (b.dataset.key === String(key)) { b.setAttribute("aria-current", "true"); cur = b; }
      else b.removeAttribute("aria-current");
    }
    if (cur && strip.scrollWidth > strip.clientWidth + 2) {
      const want = cur.offsetLeft - (strip.clientWidth - cur.offsetWidth) / 2;
      strip.scrollTo({ left: Math.max(0, want), behavior: smooth() });
    }
    edgeFade(strip);
  }
  const spyOn = new Set();
  const panelsInOrder = $$("main > .panel");
  if (typeof IntersectionObserver === "function") {
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) { if (e.isIntersecting) spyOn.add(e.target); else spyOn.delete(e.target); }
      const first = panelsInOrder.find((p) => spyOn.has(p));
      if (first) setSpy(+first.dataset.key);
    }, { rootMargin: "-38% 0px -58% 0px" });
    panelsInOrder.forEach((p) => io.observe(p));
  }
  /** 가로로 넘치는 줄(디자인 링크·상태줄 탭)에 가장자리 페이드를 걸어 더 있음을 알린다. */
  function edgeFade(el) {
    if (!el) return;
    const over = el.scrollWidth > el.clientWidth + 2;
    const l = over && el.scrollLeft > 2;
    const r = over && el.scrollLeft + el.clientWidth < el.scrollWidth - 2;
    el.classList.toggle("fade-l", l);
    el.classList.toggle("fade-r", r);
  }
  for (const id of ["designs", "sl-wins"]) {
    const el = byId(id);
    el.addEventListener("scroll", () => edgeFade(el), { passive: true });
  }
  function fitStrips() {
    const d = byId("designs");
    const cur = $(".cur", d);
    if (cur && d.scrollWidth > d.clientWidth + 2) d.scrollLeft = cur.offsetLeft + cur.offsetWidth - d.clientWidth + 4;
    edgeFade(d);
    edgeFade(byId("sl-wins"));
  }
  window.addEventListener("resize", () => { fitStrips(); }, { passive: true });

  function setMode() {
    const m = byId("sl-mode");
    let mode = "NORMAL", cls = "";
    if (S.down) { mode = "OFFLINE"; cls = "m-stale"; }
    else if (byId("help").open) { mode = "HELP"; cls = "m-help"; }
    else if (document.activeElement === byId("svc-filter")) { mode = "FILTER"; cls = "m-filter"; }
    else if (S.stale) { mode = "STALE"; cls = "m-stale"; }
    m.textContent = mode;
    m.className = "sl-mode " + cls;
  }

  const SPIN = "⣾⣽⣻⢿⡿⣟⣯⣷";
  function tick() {
    if (document.hidden) return;
    const now = Date.now() / 1000;
    const d = new Date();
    byId("sl-clock").textContent = d.toLocaleTimeString("ko-KR", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const s = S.snap;
    const gen = s && s.meta && s.meta.generated_at;
    // 나이는 클라이언트 시계로만 잰다: 마지막 성공 수신, 그리고 generated_at 이 마지막으로 바뀐 때부터.
    // (서버 시계와 비교하지 않으므로 기기 시계가 틀려도 오판하지 않는다)
    const limit = Math.max(30, (EMBED ? 10 : 3) * 4);
    const settling = now - S.visAt < 6;   // 탭이 다시 보인 직후 첫 수신 전에는 판정 보류
    const ageOk = S.lastOk ? now - S.lastOk : null;
    const ageGen = S.genAt ? now - S.genAt : null;
    const staleNow = !!(S.st && S.st.stale) || (!settling && ((ageOk != null && ageOk > limit) || (ageGen != null && ageGen > limit)));
    if (staleNow !== S.stale) { S.stale = staleNow; document.body.classList.toggle("is-stale", staleNow); setMode(); renderBanner(); renderOverall(); }
    const hu = byId("hd-upd"), su = byId("sl-upd");
    if (!s) { hu.textContent = S.down ? "✖ 연결 안 됨" : "연결 중…"; su.textContent = ""; return; }
    const t = fmt.time(S.lastOk || gen);
    const ago = fmt.ago(S.lastOk || gen, now);
    hu.classList.toggle("stale", S.stale);
    su.classList.toggle("stale", S.stale);
    if (S.stale) {
      put(hu, html`<span class="live" aria-hidden="true">✖</span><span>연결 끊김 · 마지막 ${t} (${ago})</span>`);
      put(su, html`<span class="spin" aria-hidden="true">✖</span><span class="lbl">오프라인 · </span><span>${t} (${ago})</span>`);
    } else {
      const still = mqReduce.matches || EMBED;
      const sp = still ? "●" : SPIN[(S.spin = (S.spin + 1) % SPIN.length)];
      put(hu, html`<span class="live" aria-hidden="true">●</span><span>실시간 · ${t} 갱신</span>`);
      put(su, html`<span class="spin" aria-hidden="true">${sp}</span><span class="lbl">갱신 </span><span>${t} · ${ago}</span>`);
    }
    if (S.open.size || S.sel) { /* 상대시간은 상세에서 스냅샷마다 갱신된다 */ }
  }
  function renderBanner() {
    const b = byId("banner");
    if (S.down && !S.snap) put(b, html`✖ Jarvis API(/api/snapshot)에 연결할 수 없습니다 — 자동으로 다시 시도하는 중입니다.`);
    else if (S.stale) put(b, html`✖ 연결이 끊겼거나 데이터가 오래되었습니다. 마지막 수신 ${fmt.time(S.lastOk || (S.snap && S.snap.meta && S.snap.meta.generated_at))} — 화면의 값은 그 시점 기준입니다.${S.st && S.st.error ? html` <span class="dim">(${S.st.error})</span>` : ""}`);
    else put(b, "");
  }
  function renderStatusAlerts(s) {
    const a = (s.summary && s.summary.alerts) || {};
    put("sl-al", html`<span class="t-crit" title="심각 ${a.crit || 0}건"><span aria-hidden="true">✖</span>${a.crit || 0}<span class="sr">건 심각</span></span><span class="t-warn" title="경고 ${a.warn || 0}건"><span aria-hidden="true">▲</span>${a.warn || 0}<span class="sr">건 경고</span></span><span class="t-info" title="정보 ${a.info || 0}건"><span aria-hidden="true">●</span>${a.info || 0}<span class="sr">건 정보</span></span>`);
  }

  // ───────────────────────────────────────────── 0 · 경보
  function findSvcFor(target) {
    if (!target || !S.snap) return null;
    const list = S.snap.services || [];
    return list.find((x) => x.id === target) || list.find((x) => x.runtime && x.runtime.name === target) || list.find((x) => x.name === target) || null;
  }
  function renderAlerts(s) {
    const al = s.alerts || [];
    const errs = Object.entries((s.meta && s.meta.errors) || {});
    const sum = (s.summary && s.summary.alerts) || {};
    put("alerts-meta", html`<span class="t-crit">심각 ${sum.crit || 0}</span> · <span class="t-warn">경고 ${sum.warn || 0}</span> · <span class="t-info">정보 ${sum.info || 0}</span>`);
    const body = byId("alerts-body");
    if (!al.length && !errs.length) {
      put(body, html`<div class="alerts-empty"><span class="ok" aria-hidden="true">✓</span><span>경보 없음 — 모든 점검을 통과했습니다. <span class="dim">(${fmt.time(s.meta && s.meta.generated_at)} 기준)</span></span></div>`);
      reserveAlerts(body);
      return;
    }
    const erows = errs.map(([k, m]) => html`<li class="alert"><span class="t-crit" aria-hidden="true">✖</span><span class="lv t-crit">수집</span><span class="src">${k}</span><span class="msg">수집 오류: ${m}</span></li>`);
    // 좁은 화면: 심각·경고는 모두, 정보는 합쳐 3줄까지만 — 나머지는 "더 보기"
    const serious = al.filter((a) => a.level !== "info");
    const lim = limited(al, "alerts", Math.max(3, serious.length));
    const rows = lim.items.map((a) => {
      const t = P.tone(a.level);
      const sv = findSvcFor(a.target);
      return html`<li class="alert"><span class="t-${t}" aria-hidden="true">${LV[a.level] || "●"}</span><span class="lv t-${t}">${labels.level[a.level] || a.level}</span><span class="src">${a.source}</span><span class="msg">${a.message}${sv ? html` <button type="button" class="inline-btn" data-act="goto" data-id="${sv.id}" data-fk="al:${a.key}" aria-label="${sv.name} 서비스 보기">→ ${sv.name}</button>` : ""}</span></li>`;
    });
    put(body, html`<ul class="alerts" aria-label="경보 목록">${erows}${rows}</ul>${moreBtn("alerts", lim, "정보 경보 더 보기")}`);
    reserveAlerts(body);
  }
  /** 경보 수·길이가 폴링마다 바뀌어도 아래 패널이 들썩이지 않게: 높이는 늘 때 즉시, 줄 때는 60초 뒤에 따라간다. */
  function reserveAlerts(body) {
    const w = body.clientWidth;
    if (w !== S.alW) { S.alW = w; S.alMinH = 0; S.alShrinkAt = 0; }
    body.style.minHeight = "";
    const h = body.offsetHeight;
    const now = Date.now();
    if (h >= S.alMinH) { S.alMinH = h; S.alShrinkAt = 0; }
    else if (!S.alShrinkAt) S.alShrinkAt = now;
    else if (now - S.alShrinkAt > 60000) { S.alMinH = h; S.alShrinkAt = 0; }
    if (S.alMinH > h) body.style.minHeight = S.alMinH + "px";
  }

  // ───────────────────────────────────────────── 1 · 시스템 (현재) · 4 · 하드웨어 상세
  function line(l, r, cls) { return html`<div class="line ${cls || ""}"><span class="l">${l}</span><span class="r">${r}</span></div>`; }
  const tempPct = (v) => (v == null ? null : ((v - 30) / 60) * 100);
  const SW_METER = { info: "m-sw-info", warn: "m-sw-warn", crit: "m-sw-crit", muted: "m-sw-info" };
  const SW_SPARK = { info: "calm", warn: "warn", crit: "crit", muted: "calm" };

  /** 1 · 시스템: btop 머리 — 핵심 수치를 미터 + 최근 5분 블록 스파크로 한눈에. */
  function renderNow(s) {
    const sys = s.system || {};
    const h = sys.host || {};
    const c = sys.cpu || {};
    const g = sys.gpu || {};
    const m = sys.memory || {};
    const th = sys.thermal || {};
    const pw = sys.power || {};
    const clk = sys.clocks_mhz || {};
    const n = sys.network || {};
    const io = sys.disk_io || {};
    const sp = s.spark || {};
    const fan = th.fan || {};
    const t = th.throttle || {};
    const root = (sys.disks || []).find((d) => d.mount === "/") || (sys.disks || [])[0] || {};
    const cores4 = c.cores || h.cores || 4;
    const l1 = (c.load || [])[0];
    const cpuT = c.temp_c != null ? c.temp_c : th.cpu_temp_c;
    const swT = swapTone(m);
    const ioSum = (sp.dr || []).map((v, i) => (v == null && (sp.dw || [])[i] == null ? null : (v || 0) + ((sp.dw || [])[i] || 0)));

    put("sys-meta", html`<b>${h.hostname || "host"}</b> · ${h.kernel || ""} · ${h.arch || ""} · 가동 <b>${fmt.dur(sys.uptime_s)}</b>`);
    put("now-meta", html`<span class="dim">최근 5분</span>`);
    const row = (lbl, mtr, val, sk, x, title) => html`<div class="nr" title="${title || ""}"><span class="l">${lbl}</span>${mtr}<span class="v">${val}</span>${sk}<span class="x">${x}</span></div>`;
    const throttleBad = t.ok === false || (t.now || []).length;
    put("now-body", html`<div class="nrows">
        ${row("CPU", meter(c.usage_pct), html`<b class="t-${P.loadTone(c.usage_pct)}">${pctf(c.usage_pct)}</b>`, spark(sp.cpu, { cells: 20, max: 100, color: "ok" }),
          html`${fmt.mhz(c.freq_mhz)} · 부하 <span class="t-${P.loadTone(l1 != null ? (l1 / cores4) * 100 : null, 75, 100)}">${l1 != null ? fmt.num(l1, 2) : DASH}</span>`)}
        ${row("GPU", meter(g.usage_pct, "m-gpu"), html`<b class="t-${P.loadTone(g.usage_pct)}">${pctf(g.usage_pct)}</b>`, spark(sp.gpu, { cells: 20, max: 100, color: "mag" }),
          html`${fmt.mhz(clk.v3d != null ? clk.v3d : g.freq_mhz)} · 작업 ${fmt.num(g.jobs_per_s, 1)}/s`)}
        ${row("MEM", meter(m.usage_pct, "m-mem"), html`<b class="t-${P.loadTone(m.usage_pct, 80, 92)}">${pctf(m.usage_pct)}</b>`, spark(sp.mem, { cells: 20, max: 100, color: "teal" }),
          html`${fmt.bytes(m.used)} / ${fmt.bytes(m.total)}`)}
        ${row("SWAP", meter(m.swap_pct, SW_METER[swT]), html`<b class="t-${swT}">${swapPct(m.swap_pct)}</b>`, spark(sp.swap, { cells: 20, max: 100, color: SW_SPARK[swT] }),
          html`스왑 인 ${fmt.num(m.swap_in_ps, 1)}/s${swT === "info" ? html` <span class="faint">·</span> 정상` : ""}`,
          swT === "info" ? "스왑이 차 있어도 스왑 인이 없고 가용 메모리가 충분하면 문제가 아닙니다" : "")}
        ${row("TEMP", meter(tempPct(cpuT), "m-temp"), html`<b class="t-${P.loadTone(cpuT, 65, 80)}">${fmt.temp(cpuT)}</b>`, spark(sp.temp, { cells: 20, min: 30, max: 90, color: "warn" }),
          html`팬 ${fan.rpm != null ? fmt.num(fan.rpm) + " rpm" : DASH}${fan.pct != null ? html` <span class="dim">${pctf(fan.pct)}</span>` : ""}`, "PMIC " + fmt.temp(th.pmic_temp_c))}
        ${row("DISK", meter(root.usage_pct, "m-disk"), html`<b class="t-${P.loadTone(root.usage_pct, 80, 92)}">${pctf(root.usage_pct)}</b>`, spark(ioSum, { cells: 20, color: "org" }),
          html`↓${fmt.bps(io.read_bps)} ↑${fmt.bps(io.write_bps)}`, "미터: " + (root.mount || "/") + " 사용률 · 스파크·수치: 디스크 I/O 읽기↓ 쓰기↑")}
      </div>
      <div class="nfoot">
        <div class="nf"><span class="l">NET</span><span class="r">LAN <span class="t-teal">↓${fmt.bps(n.rx_bps)}</span> <span class="t-mag">↑${fmt.bps(n.tx_bps)}</span> <span class="faint">·</span> Tailnet <span class="t-acc">↓${fmt.bps(n.tailnet_rx_bps)} ↑${fmt.bps(n.tailnet_tx_bps)}</span></span></div>
        <div class="nf"><span class="l">PWR</span><span class="r">${fmt.watts(pw.total_w)} <span class="faint">·</span> 입력 ${pw.input_v != null ? fmt.num(pw.input_v, 2) + " V" : DASH} <span class="faint">·</span> 스로틀 ${throttleBad ? html`<span class="t-crit">✖ ${(t.now || []).join(", ") || t.raw || "있음"}</span>` : html`<span class="t-ok">✓ 정상</span>`}</span></div>
      </div>`);
  }

  /** 4 · 하드웨어 상세: 코어별·큐별·마운트별·인터페이스별·센서·프로세스. */
  function renderHw(s) {
    const sys = s.system || {};
    const h = sys.host || {};
    const c = sys.cpu || {};
    const g = sys.gpu || {};
    const m = sys.memory || {};
    const th = sys.thermal || {};
    const pw = sys.power || {};
    const clk = sys.clocks_mhz || {};
    const sp = s.spark || {};

    put("hw-meta", html`${h.model || ""}${h.gpu_name ? html` · ${h.gpu_name}` : ""}`);

    // cpu
    put("cpu-meta", html`<b>${fmt.mhz(c.freq_mhz)}</b> · ${c.governor || DASH} · ${c.cores || h.cores || DASH}코어`);
    const cores = (c.per_core_pct || []).map((v, i) => html`<div class="core"><span class="lbl">C${i}</span>${meter(v)}<span class="v t-${P.loadTone(v)}">${pctf(v)}</span><span class="f">${fmt.mhz((c.per_core_freq_mhz || [])[i])}</span></div>`);
    const cores4 = c.cores || h.cores || 4;
    const l1 = (c.load || [])[0];
    put("cpu-body", html`<div class="cores">
        <div class="core total"><span class="lbl">ALL</span>${meter(c.usage_pct)}<span class="v t-${P.loadTone(c.usage_pct)}">${pctf(c.usage_pct)}</span><span class="f t-${P.loadTone(c.temp_c, 65, 80)}">${fmt.temp(c.temp_c)}</span></div>
        ${cores}
      </div>
      <div class="stats">
        <div class="stat"><span>부하</span><span class="t-${P.loadTone(l1 != null ? (l1 / cores4) * 100 : null, 75, 100)}">${(c.load || []).map((x) => fmt.num(x, 2)).join(" ") || DASH}</span></div>
        <div class="stat"><span>iowait</span><span>${pctf(c.iowait_pct, 1)}</span></div>
        <div class="stat"><span>프로세스</span><span>${num(sys.processes && sys.processes.count)}</span></div>
        <div class="stat"><span>스레드</span><span>${num(c.threads_total)}</span></div>
        <div class="stat"><span>실행 중</span><span>${num(c.procs_running)}</span></div>
        <div class="stat"><span>최대 클럭</span><span>${fmt.mhz(c.max_mhz || h.cpu_max_mhz)}</span></div>
      </div>
      <div class="io" style="margin-top:10px"><span class="lbl">부하</span>${spark(sp.load, { cells: 24, color: "warn" })}<span class="num">${l1 != null ? fmt.num(l1, 2) : DASH}</span></div>
      <div class="io"><span class="lbl">클럭</span>${spark(sp.mhz, { cells: 24, min: 0, max: c.max_mhz || h.cpu_max_mhz || 2400, color: "info" })}<span class="num">${fmt.mhz(c.freq_mhz)}</span></div>`);

    // gpu
    put("gpu-meta", html`${g.name || h.gpu_name || "V3D"}`);
    const q = g.queues || {};
    const qn = { bin: "bin", render: "render", tfu: "tfu", csd: "csd", cache_clean: "cache", cpu: "cpu" };
    put("gpu-body", g.available === false ? html`<p class="dim">GPU 사용률을 읽을 수 없습니다.</p>` : html`
      <div class="mrow"><span class="lbl">V3D</span>${meter(g.usage_pct, "m-gpu")}<span class="v t-${P.loadTone(g.usage_pct)}">${pctf(g.usage_pct)}</span></div>
      <div class="lines">
        ${line("v3d 클럭", fmt.mhz(clk.v3d != null ? clk.v3d : g.freq_mhz))}
        ${line("작업", html`${fmt.num(g.jobs_per_s, 1)}/s`)}
        ${line("GPU 메모리", g.mem_mb != null || h.gpu_mem_mb != null ? (g.mem_mb != null ? g.mem_mb : h.gpu_mem_mb) + " MB" : DASH)}
      </div>
      <p class="minihd">큐별 사용률 %</p>
      <div class="q">${Object.keys(qn).map((k) => html`<div class="qi"><span>${qn[k]}</span>${meter(q[k], "thin m-gpu")}<span>${q[k] != null ? Math.round(q[k]) : DASH}</span></div>`)}</div>`);

    // mem
    put("mem-meta", html`<b>${fmt.bytes(m.used)}</b> / ${fmt.bytes(m.total)}`);
    const pc = (a) => (a != null && m.total ? (a / m.total) * 100 : null);
    const swT = swapTone(m);
    put("mem-body", html`
      <div class="mrow"><span class="lbl">사용</span>${meter(m.usage_pct, "m-mem")}<span class="v">${pctf(m.usage_pct)}</span></div>
      <div class="mrow"><span class="lbl">캐시</span>${meter(pc(m.cached), "m-acc")}<span class="v">${fmt.bytes(m.cached)}</span></div>
      <div class="mrow"><span class="lbl">가용</span>${meter(pc(m.available), "m-acc")}<span class="v">${fmt.bytes(m.available)}</span></div>
      <div class="mrow"><span class="lbl">스왑</span>${meter(m.swap_pct, SW_METER[swT])}<span class="v t-${swT}">${swapPct(m.swap_pct)}</span></div>
      <div class="lines">
        ${line("스왑 사용", html`${fmt.bytes(m.swap_used)} / ${fmt.bytes(m.swap_total)}`)}
        ${line("스왑 인", html`<span class="${(m.swap_in_ps || 0) > 0 ? "t-warn" : ""}">${fmt.num(m.swap_in_ps, 1)}/s</span>${swT === "info" ? html` <span class="dim">· 압박 아님</span>` : ""}`)}
        ${line("여유", fmt.bytes(m.free))}
        ${line("버퍼 · 공유", html`${fmt.bytes(m.buffers)} · ${fmt.bytes(m.shared)}`)}
      </div>`);

    // disk
    const disks = sys.disks || [];
    const io = sys.disk_io || {};
    put("dsk-meta", html`↓${fmt.bps(io.read_bps)} ↑${fmt.bps(io.write_bps)}`);
    const devs = Object.entries(io.devices || {});
    put("dsk-body", html`${disks.map((d) => html`<div class="disk">
        <div class="dh"><span class="m">${d.mount}</span><span class="d">${d.device} · ${d.fs}</span></div>
        ${meter(d.usage_pct, "m-disk")}
        <div class="dh"><span class="t-${P.loadTone(d.usage_pct, 80, 92)}">${pctf(d.usage_pct)}</span><span class="dim">${fmt.bytes(d.used)} / ${fmt.bytes(d.total)} · 여유 ${fmt.bytes(d.free)}</span></div>
      </div>`)}
      <div class="io"><span class="lbl">읽기</span>${spark(sp.dr, { cells: 16, color: "info" })}<span class="num">${fmt.bps(io.read_bps)}</span></div>
      <div class="io"><span class="lbl">쓰기</span>${spark(sp.dw, { cells: 16, color: "org" })}<span class="num">${fmt.bps(io.write_bps)}</span></div>
      ${devs.length > 1 ? html`<div class="lines" style="margin-top:6px">${devs.map(([k, v]) => line(k, html`↓${fmt.bps(v && v.read_bps)} ↑${fmt.bps(v && v.write_bps)}`))}</div>` : ""}`);

    // net
    const n = sys.network || {};
    const ifs = n.interfaces || [];
    const main = ifs.filter((i) => i.kind !== "docker");
    const dock = ifs.filter((i) => i.kind === "docker");
    const kindL = { wired: "유선", wifi: "Wi-Fi", tailnet: "tailnet", docker: "docker", other: "기타" };
    put("net-meta", html`LAN ↓${fmt.bps(n.rx_bps)} ↑${fmt.bps(n.tx_bps)}`);
    const wifiBars = (w) => {
      const qv = w && w.quality != null ? w.quality : null;
      const on = qv == null ? 0 : Math.ceil(qv / 25);
      return html`<span class="wifi" aria-hidden="true">${[1, 2, 3, 4].map((i) => html`<i class="${i <= on ? "on" : ""}" style="height:${i * 25}%"></i>`)}</span>`;
    };
    put("net-body", html`
      <div class="io"><span class="lbl">LAN↓</span>${spark(sp.rx, { cells: 16, color: "teal" })}<span class="num">${fmt.bps(n.rx_bps)}</span></div>
      <div class="io"><span class="lbl">LAN↑</span>${spark(sp.tx, { cells: 16, color: "mag" })}<span class="num">${fmt.bps(n.tx_bps)}</span></div>
      <div class="io"><span class="lbl">TS↓</span>${spark(sp.trx, { cells: 16, color: "acc" })}<span class="num">${fmt.bps(n.tailnet_rx_bps)}</span></div>
      <div class="io"><span class="lbl">TS↑</span>${spark(sp.ttx, { cells: 16, color: "acc" })}<span class="num">${fmt.bps(n.tailnet_tx_bps)}</span></div>
      <div class="ifs">
        ${main.map((i) => html`<div class="if ${i.up ? "" : "down"}">
          <span class="n">${i.name} <span class="k">${kindL[i.kind] || i.kind}${i.up ? "" : " · 꺼짐"}${i.speed_mbps ? html` · ${i.speed_mbps >= 1000 ? i.speed_mbps / 1000 + "G" : i.speed_mbps + "M"}` : ""}</span></span>
          <span class="rt">↓${fmt.bps(i.rx_bps)} ↑${fmt.bps(i.tx_bps)}</span>
          <span class="ip" title="${(i.ipv4 || []).concat(i.ipv6 || []).join(", ")}">${(i.ipv4 || []).join(", ") || DASH}${(i.ipv6 || []).length ? html` <span class="dim">· ${i.ipv6[0]}</span>` : ""}</span>
          ${i.wifi ? html`<span class="wf"><span class="dim">신호</span> ${wifiBars(i.wifi)} ${pctf(i.wifi.quality)}${i.wifi.signal_dbm != null ? html` <span class="dim">· ${i.wifi.signal_dbm} dBm</span>` : ""}</span>` : ""}
        </div>`)}
        ${dock.length ? html`<div class="if"><span class="n">docker <span class="k">브리지 ${dock.length}개 · 활성 ${dock.filter((i) => i.up).length}</span></span><span class="rt">↓${fmt.bps(n.docker_rx_bps)} ↑${fmt.bps(n.docker_tx_bps)}</span></div>` : ""}
      </div>`);

    // sensors
    const t = th.throttle || {};
    const fan = th.fan || {};
    put("sen-meta", html`<b>${fmt.temp(th.cpu_temp_c)}</b> · ${fmt.watts(pw.total_w)}`);
    const allRails = (pw.rails || []).filter((r) => r && typeof r === "object");
    const rails = allRails.slice().sort((a, b) => (b.watts || 0) - (a.watts || 0)).filter((r) => (r.watts || 0) >= 0.01).slice(0, 6);
    const railTitle = allRails.map((r) => (r.name || "?") + " " + fmt.num(r.watts, 3) + " W").join(", ");
    const lowV = pw.input_v != null && pw.input_v < 4.85;
    const clkNames = ["arm", "core", "v3d", "isp", "hevc"].filter((k) => clk[k] != null);
    put("sen-body", html`
      <div class="mrow"><span class="lbl">CPU</span>${meter(tempPct(th.cpu_temp_c), "m-temp")}<span class="v t-${P.loadTone(th.cpu_temp_c, 65, 80)}">${fmt.temp(th.cpu_temp_c)}</span></div>
      <div class="mrow"><span class="lbl">PMIC</span>${meter(tempPct(th.pmic_temp_c), "m-temp")}<span class="v t-${P.loadTone(th.pmic_temp_c, 65, 80)}">${fmt.temp(th.pmic_temp_c)}</span></div>
      <div class="mrow"><span class="lbl">팬</span>${meter(fan.pct, "m-acc")}<span class="v">${fan.rpm != null ? fmt.num(fan.rpm) + " rpm" : DASH} <span class="dim">${pctf(fan.pct)}</span></span></div>
      <div class="io" style="margin-top:6px"><span class="lbl">팬</span>${spark(sp.fan, { cells: 22, min: 0, color: "acc" })}<span class="num">${fan.rpm != null ? fmt.num(fan.rpm) : DASH}</span></div>
      <div class="io"><span class="lbl">전력</span>${spark(sp.pw, { cells: 22, min: 0, color: "org" })}<span class="num">${fmt.watts(pw.total_w)}</span></div>
      <div class="lines" style="margin-top:6px">
        ${line("5V 입력", html`<span class="${lowV ? "t-crit" : ""}">${pw.input_v != null ? fmt.num(pw.input_v, 2) + " V" : DASH}${lowV ? " · 저전압" : ""}</span>`)}
        ${line("코어 전압", pw.core_volts != null ? fmt.num(pw.core_volts, 3) + " V" : DASH)}
        ${line("스로틀", t.ok === false || (t.now || []).length || (t.since_boot || []).length
          ? html`<span class="flags">${(t.now || []).map((f) => html`<span class="flag t-crit">지금 ${f}</span>`)}${(t.since_boot || []).map((f) => html`<span class="flag t-warn">부팅 후 ${f}</span>`)}<span class="dim">${t.raw || ""}</span></span>`
          : html`<span class="t-ok">✓ 정상 <span class="dim">(${t.raw || "0x0"})</span></span>`)}
        ${line("가동", html`${fmt.dur(sys.uptime_s, 3)} <span class="dim">· 부팅 ${fmt.datetime(sys.boot_time)}</span>`)}
      </div>
      ${rails.length ? html`<p class="minihd" title="${railTitle}">전원 레일 <span class="dim">(상위 ${rails.length}/${allRails.length})</span></p>
        <div class="kgrid">${rails.map((r) => html`<span><span class="dim">${r.name || "?"}</span> ${fmt.num(r.watts, 2)} W</span>`)}</div>` : ""}
      ${clkNames.length ? html`<p class="minihd">클럭</p><div class="kgrid">${clkNames.map((k) => html`<span><span class="dim">${k}</span> ${fmt.mhz(clk[k])}</span>`)}</div>` : ""}`);

    renderProc(s);
  }
  function renderProc(s) {
    s = s || S.snap;
    if (!s) return;
    const pr = (s.system && s.system.processes) || {};
    const all = (S.proc === "mem" ? pr.top_mem : pr.top_cpu) || [];
    const pl = limited(all, "procs", 6);
    const list = pl.items;
    $$('[data-act="proc"]').forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.proc === S.proc)));
    put("prc-meta", html`${num(pr.count)} 프로세스 · ${num(pr.threads)} 스레드 · CPU%는 코어 1개 기준`);
    const ok = { unit: "unit", "user-unit": "user", container: "ctr", scope: "scope", unknown: "?" };
    // 고정 열 폭(table-layout: fixed): 내용이 바뀌어도 열이 흔들리지 않고 가로 스크롤 위치도 유지된다
    put("prc-body", list.length ? html`<table class="tbl fixed stk t-proc"><colgroup><col class="w-pid"><col class="w-name"><col class="w-user"><col class="w-cpu"><col class="w-mem"><col class="w-thr"><col class="w-st"><col class="w-own"><col></colgroup>
      <thead><tr><th class="r">PID</th><th>NAME</th><th>USER</th><th class="r">CPU%</th><th class="r">MEM</th><th class="r">THR</th><th>ST</th><th>OWNER</th><th>CMD</th></tr></thead><tbody>
      ${list.map((p) => html`<tr><td class="r dim s" data-l="pid">${p.pid}</td><td class="b p ell" title="${p.name}">${p.name}</td><td class="dim s ell" data-l="user">${p.user}</td><td class="r k t-${P.loadTone(p.cpu_pct, 50, 90)}">${fmt.num(p.cpu_pct, 1)}<span class="m-only">%</span></td><td class="r s" data-l="mem">${fmt.bytes(p.rss)}</td><td class="r dim s" data-l="thr">${p.threads}</td><td class="dim x">${p.state}</td><td class="dim s ell" title="${p.owner || ""}"><span class="t-mag">${ok[p.owner_kind] || p.owner_kind || ""}</span> ${p.owner || ""}</td><td class="dim s ell cmd" title="${p.cmd}">${p.cmd}</td></tr>`)}
    </tbody></table>` : html`<p class="dim">프로세스 정보 없음</p>`);
    put("prc-more", moreBtn("procs", pl, "프로세스 더 보기"));
  }

  // ───────────────────────────────────────────── 2 · 서비스 (k9s)
  const CAT_ICON = { public: "◆", tailnet: "◇", hermes: "◈", host: "▣", internal: "▤", infra: "▥", system: "▦" };
  const svcList = byId("svc-list");
  const filterInput = byId("svc-filter");
  const groupEls = new Map();
  const rowEls = new Map();
  let uid = 0;
  let catSig = "";
  const emptyEl = document.createElement("p");
  emptyEl.className = "svc-empty";
  emptyEl.hidden = true;

  function buildCats(s) {
    const cats = (s.categories || []).filter((c) => c && c.id != null);
    const sig = cats.map((c) => c.id).join(",");
    const box = byId("svc-cats");
    if (sig !== catSig) {
      catSig = sig;
      box.textContent = "";
      const mk = (id, name) => {
        const b = document.createElement("button");
        b.type = "button"; b.dataset.act = "cat"; b.dataset.cat = id;
        const l = document.createElement("span"); l.textContent = name;
        const c = document.createElement("span"); c.className = "c";
        b.append(l, c);
        box.appendChild(b);
      };
      mk("all", "전체");
      for (const c of cats) mk(c.id, c.label || labels.category[c.id] || c.id);
    }
    const total = (s.services || []).length;
    for (const b of $$("button", box)) {
      const id = b.dataset.cat;
      const c = id === "all" ? total : ((cats.find((x) => x.id === id) || {}).count || 0);
      const el = $(".c", b);
      if (el.textContent !== String(c)) el.textContent = String(c);
      b.setAttribute("aria-pressed", String(S.cat === id));
    }
  }

  /** 카테고리 id 정규화: null·빈 값은 "other" 로 모은다(한 항목이 깨져도 목록 전체가 멈추지 않게). */
  const catKey = (v) => (v == null || v === "" ? "other" : String(v));
  function byCategory(s) {
    const out = {};
    for (const c of s.categories || []) { if (c && c.id != null) out[catKey(c.id)] = Object.assign({}, c, { id: catKey(c.id), services: [] }); }
    for (const sv of s.services || []) {
      if (!sv || sv.id == null) continue;
      const k = catKey(sv.category);
      (out[k] || (out[k] = { id: k, services: [] })).services.push(sv);
    }
    return out;
  }
  function ensureGroup(cat) {
    let g = groupEls.get(cat.id);
    if (!g) {
      const el = document.createElement("section");
      el.className = "grp"; el.dataset.cat = cat.id;
      const hid = "grp-" + String(cat.id).replace(/[^a-z0-9_-]/gi, "_");
      el.setAttribute("aria-labelledby", hid);
      const head = document.createElement("h3"); head.className = "grp-h"; head.id = hid;
      const ul = document.createElement("ul"); ul.className = "grp-ul";
      el.append(head, ul);
      g = { el, head, ul };
      groupEls.set(cat.id, g);
    }
    return g;
  }
  function makeRow(id) {
    const li = document.createElement("li");
    li.className = "svc"; li.dataset.id = id;
    const lineEl = document.createElement("div"); lineEl.className = "svc-line";
    const tog = document.createElement("button");
    tog.type = "button"; tog.className = "svc-tog"; tog.dataset.act = "svc";
    tog.setAttribute("aria-expanded", "false");
    tog.setAttribute("data-fk", "svc:" + id);
    const detId = "svc-det-" + ++uid;
    tog.setAttribute("aria-controls", detId);
    lineEl.appendChild(tog);
    const mk = (cls) => { const e = document.createElement("span"); e.className = cls; lineEl.appendChild(e); return e; };
    const r = { li, line: lineEl, tog, url: mk("c-url"), exp: mk("c-exp"), rt: mk("c-rt"), lat: mk("c-lat num"), cpu: mk("c-cpu num"), mem: mk("c-mem num"), req: mk("c-req num") };
    const det = document.createElement("div");
    det.className = "svc-detail"; det.id = detId; det.hidden = true;
    det.setAttribute("role", "region");
    r.det = det;
    li.append(lineEl, det);
    return r;
  }
  function expBadges(e) {
    e = e || {};
    const b = [];
    if (e.public) b.push(html`<span class="bdg pub" title="인터넷 공개">공개</span>`);
    if (e.tailnet) b.push(html`<span class="bdg ts" title="tailnet에서 접근">Tailnet</span>`);
    if (e.lan) b.push(html`<span class="bdg lan" title="LAN에서 직접 접근">LAN</span>`);
    if (e.local_only || !b.length) b.push(html`<span class="bdg loc" title="이 기기 안에서만">로컬</span>`);
    return b;
  }
  /** 실행 주체 칸: 재시작 횟수는 말줄임되지 않는 별도 경고 배지로 맨 앞에. */
  function rtCell(sv) {
    const r = sv.runtime;
    if (!r) return html`<span class="rtx">${DASH}</span>`;
    if (r.type === "remote") return html`<span class="rtx ${r.online === false ? "t-crit" : ""}">원격 · ${r.online === false ? "오프라인" : "온라인"}</span>`;
    const t = { container: "ctr", systemd: "sysd", process: "proc" }[r.type] || r.type || "?";
    const rc = r.restart_count;
    return html`${rc ? html`<span class="rs t-warn" title="재시작 ${rc}회">재시작 ${rc}</span>` : ""}<span class="rtx" title="${(labels.runtime[r.type] || r.type || "") + " · 가동 " + fmt.dur(r.uptime_s)}">${t} · ${fmt.durShort(r.uptime_s)}</span>`;
  }
  function probeSummary(sv) {
    const pr = sv.probes || {};
    const vals = Object.values(pr).filter((x) => x && typeof x === "object");
    return { n: vals.length, ok: vals.filter((x) => x.ok).length };
  }
  /** 텍스트를 선택 중이면(복사하려는 중) 상세를 다시 그리지 않는다. */
  function selectingIn(el) {
    const sel = window.getSelection && window.getSelection();
    return !!(sel && !sel.isCollapsed && sel.anchorNode && el.contains(sel.anchorNode));
  }
  function updRow(r, sv) {
    const ps = probeSummary(sv);
    const reason = sv.status !== "up" && sv.status_reason;
    const sub = sv.title && sv.title !== sv.name ? sv.title : "";
    put(r.tog, html`<span class="car" aria-hidden="true">▸</span>${st(sv.status)}<span class="nm">${sv.name}</span><span class="tt ${reason ? "reason" : ""}" title="${reason || sub}">${reason || sub}</span>`);
    r.li.dataset.st = sv.status || "unknown";
    const urls = sv.urls || [];
    const lsn = (sv.listeners || []).filter((l) => l && l.port != null);
    let urlHtml;
    if (urls.length) {
      urlHtml = html`${extLink(urls[0].url, urlText(urls[0].url), "u:" + sv.id + ":0")}${urls.length > 1 ? html` <span class="dim">+${urls.length - 1}</span>` : ""}`;
    } else if (lsn.length) {
      urlHtml = html`<span class="dim">${lsn.map((l) => ":" + l.port + "/" + (l.proto || "tcp")).join(" ")}</span>`;
    } else if (sv.upstream) {
      urlHtml = html`<span class="dim">→ ${sv.upstream}</span>`;
    } else urlHtml = html`<span class="none">${DASH}</span>`;
    put(r.url, urlHtml);
    put(r.exp, expBadges(sv.exposure));
    put(r.rt, rtCell(sv));
    const tr = sv.traffic;
    const ms = P.pick.latency(sv);
    const title = (ps.n ? "프로브 " + ps.ok + "/" + ps.n + " 정상" : "프로브 없음") + (tr && tr.requests_24h != null ? " · 24h 요청 " + fmt.num(tr.requests_24h) : "");
    put(r.lat, ps.n ? html`<span class="t-${ps.ok < ps.n ? "crit" : msTone(ms)}" title="${title}">${ps.ok < ps.n ? "✗ " : ""}${fmt.ms(ms)}</span>` : html`<span class="dim" title="${title}">${DASH}</span>`);
    const noLat = !ps.n;
    if (noLat !== (r.li.dataset.nolat === "1")) r.li.dataset.nolat = noLat ? "1" : "0";
    const rt = sv.runtime || {};
    put(r.cpu, rt.cpu_pct != null ? pctf(rt.cpu_pct, 1) : DASH);
    put(r.mem, rt.mem_bytes != null ? fmt.bytes(rt.mem_bytes, 0) : DASH);
    put(r.req, tr && tr.requests_24h != null ? num(tr.requests_24h) : DASH);
    if (S.open.has(sv.id) && !selectingIn(r.det)) put(r.det, svcDetail(sv));
  }

  function kvRow(k, v) { return v == null || v === "" ? "" : html`<dt>${k}</dt><dd>${v}</dd>`; }
  function probeLine(name, p) {
    if (!p) return "";
    return html`<span class="probe"><span class="pl">${name}</span>${okGlyph(p.ok)}<span class="t-${codeTone(p.code)}">${p.code != null ? p.code : DASH}</span><span class="t-${msTone(p.ms)}">${fmt.ms(p.ms)}</span>${p.caddy_fallback ? html`<span class="t-warn">Caddy 기본응답</span>` : ""}${p.error ? html`<span class="t-crit">${p.error}</span>` : ""}</span>`;
  }
  function svcDetail(sv) {
    const snap = S.snap || {};
    const now = (snap.meta && snap.meta.generated_at) || Date.now() / 1000;
    const dom = snap.meta && snap.meta.domain;
    const r = sv.runtime;
    const e = sv.exposure || {};
    const urls = sv.urls || [];
    const links = urls.length ? html`<span class="links">${urls.map((u, i) => html`<span>${extLink(u.url, u.url, "d:" + sv.id + ":" + i)} <span class="dim">${labels.audience[u.audience] || u.audience || ""}${u.label ? " · " + u.label : ""}</span>${u.code != null || u.error ? html` <span class="t-${u.error ? "crit" : codeTone(u.code)}">${u.code != null ? u.code : "✗"}</span>` : ""}${u.ms != null ? html` <span class="dim">${fmt.ms(u.ms)}</span>` : ""}</span>`)}</span>` : null;
    let rtHtml = null;
    if (r) {
      const parts = [];
      parts.push(html`<b>${labels.runtime[r.type] || r.type}</b> ${r.name || ""}`);
      if (r.project) parts.push(html`<span class="dim">${r.project}${r.service ? "/" + r.service : ""}</span>`);
      if (r.image) parts.push(html`<span class="dim">${r.image}</span>`);
      if (r.state) parts.push(html`${r.state}${r.sub && r.sub !== r.state ? "/" + r.sub : ""}`);
      if (r.health) parts.push(html`<span class="t-${r.health === "healthy" ? "ok" : r.health === "unhealthy" ? "crit" : "warn"}">${r.health}</span>`);
      if (r.uptime_s != null) parts.push(html`가동 ${fmt.dur(r.uptime_s)}`);
      if (r.restart_count != null && r.type !== "remote") parts.push(html`<span class="${r.restart_count ? "t-warn" : ""}">재시작 ${r.restart_count}</span>`);
      if (r.cpu_pct != null) parts.push(html`CPU ${pctf(r.cpu_pct, 1)}`);
      if (r.mem_bytes != null) parts.push(html`MEM ${fmt.bytes(r.mem_bytes)}`);
      if (r.pid) parts.push(html`<span class="dim">pid ${r.pid}</span>`);
      if (r.ip) parts.push(html`<span class="dim">${r.ip}</span>`);
      if (r.os) parts.push(html`<span class="dim">${r.os}</span>`);
      if (r.cwd) parts.push(html`<span class="dim">${r.cwd}</span>`);
      rtHtml = parts.map((p, i) => html`${i ? " · " : ""}${p}`);
    }
    const pr = sv.probes || {};
    let probes = null;
    if (Object.keys(pr).length) {
      const nm = { internal: "내부 :443", tunnel: "터널 :80", public: "공개 엣지" };
      probes = html`<span class="plist">${Object.keys(pr).map((k) => probeLine(nm[k] || k, pr[k]))}</span>`;
    }
    const cert = sv.cert ? html`<span class="t-${daysTone(sv.cert.days_left)}">${sv.cert.days_left != null ? Math.floor(sv.cert.days_left) + "일 남음" : DASH}</span> <span class="dim">${sv.cert.subject || ""} · ${sv.cert.issuer || ""}</span>${sv.edge_cert ? html` · 엣지 <span class="t-${daysTone(sv.edge_cert.days_left)}">${Math.floor(sv.edge_cert.days_left)}일</span> <span class="dim">${sv.edge_cert.issuer || ""}</span>` : ""}` : null;
    const dns = sv.dns ? html`<span class="t-${sv.dns.points_to === "error" || sv.dns.points_to === "none" ? "warn" : "ok"}">→ ${sv.dns.points_to}</span> <span class="dim">${(sv.dns.answers || []).join(", ")}</span>` : null;
    const tr = sv.traffic;
    const trHtml = tr ? html`1h <b>${num(tr.requests_1h)}</b> · 24h <b>${num(tr.requests_24h)}</b> · <span class="${tr.errors_4xx_24h ? "t-warn" : "dim"}">4xx ${num(tr.errors_4xx_24h)}</span> · <span class="${tr.errors_5xx_24h ? "t-crit" : "dim"}">5xx ${num(tr.errors_5xx_24h)}</span> · p50 ${fmt.ms(tr.p50_ms_24h)} · p95 ${fmt.ms(tr.p95_ms_24h)}${tr.last_ts ? html` <span class="dim">· 마지막 ${fmt.ago(tr.last_ts, now)}</span>` : ""}` : null;
    const lsn = (sv.listeners || []).filter((l) => l && l.port != null);
    const lst = lsn.length ? html`${lsn.map((l, i) => html`${i ? " · " : ""}${l.proto || "tcp"} <b>${l.port}</b> <span class="dim">${(l.addrs || []).join(",")}</span> ${scopeBadge(l.scope)}`)}` : null;
    const deps = (sv.depends_on || []).length ? html`${sv.depends_on.map((d) => { const x = P.pick.service(snap, d); return html`<button type="button" class="inline-btn" data-act="goto" data-id="${d}" data-fk="dep:${sv.id}:${d}">${x ? st(x.status) : ""} ${x ? x.name : d}</button>`; })}` : null;
    // via 는 서로 다른 접근 경로 목록(순서 있는 체인이 아님) — 경로마다 칩 하나
    const via = (e.via || []).map((v) => html`<span class="via">${labels.via[v] || v}</span>`);
    const extra = sv.extra && typeof sv.extra === "object" ? Object.entries(sv.extra).filter(([, v]) => v != null && typeof v !== "object").map(([k, v]) => html`<span class="nowrap">${k} <b>${String(v)}</b></span>`) : [];
    const extraArr = sv.extra && typeof sv.extra === "object" ? Object.entries(sv.extra).filter(([, v]) => Array.isArray(v)).map(([k, v]) => html`<span class="nowrap">${k} <b>${v.join(", ")}</b></span>`) : [];
    const ex = extra.concat(extraArr);
    return html`<dl class="kv">
      ${kvRow("상태", html`${st(sv.status, true)}${sv.status_reason ? html` <span class="t-warn">— ${sv.status_reason}</span>` : ""} <span class="dim">· ${labels.category[sv.category] || sv.category} · ${labels.kind[sv.kind] || sv.kind || ""}</span>`)}
      ${kvRow("설명", sv.description)}
      ${kvRow("주소", links)}
      ${kvRow("노출", html`${expBadges(e)}${via.length ? html` <span class="dim">경로</span> ${via}` : ""}${sv.in_tunnel != null && sv.snippet ? html` <span class="dim">· 터널 ${sv.in_tunnel ? "포함" : "제외"} · ${sv.snippet}</span>` : ""}`)}
      ${kvRow("업스트림", sv.upstream)}
      ${kvRow("실행", rtHtml)}
      ${kvRow("프로브", probes)}
      ${kvRow("인증서", cert)}
      ${kvRow("DNS", dns)}
      ${kvRow("트래픽", trHtml)}
      ${kvRow("리스너", lst)}
      ${kvRow("의존", deps)}
      ${kvRow("부가", ex.length ? ex.map((x, i) => html`${i ? " · " : ""}${x}`) : null)}
      ${kvRow("ID", html`<span class="dim">${sv.id}</span>`)}
    </dl>`;
  }

  function renderSvc(s) {
    const af = document.activeElement;
    buildCats(s);
    const cats = (s.categories || []).filter((c) => c && c.id != null).map((c) => Object.assign({}, c, { id: catKey(c.id) }));
    const by = byCategory(s);
    for (const id of Object.keys(by)) if (!cats.find((c) => c.id === id)) cats.push({ id, label: labels.category[id] || (id === "other" ? "기타" : id) });
    const sum = s.summary && s.summary.services || {};
    put("svc-meta", html`<b>${num(sum.total)}</b> · <span class="t-ok">정상 ${num(sum.up)}</span>${sum.degraded ? html` · <span class="t-warn">저하 ${sum.degraded}</span>` : ""}${sum.down ? html` · <span class="t-crit">다운 ${sum.down}</span>` : ""}`);
    const seen = new Set();
    let prevG = null;
    for (const cat of cats) {
      const grp = by[cat.id];
      const list = (grp && grp.services) || [];
      if (!list.length) { const g0 = groupEls.get(cat.id); if (g0) g0.el.remove(); continue; }
      const g = ensureGroup(cat);
      // 순서 유지
      const want = prevG ? prevG.el.nextSibling : svcList.firstChild;
      if (g.el !== want) svcList.insertBefore(g.el, want);
      prevG = g;
      // 개수는 실제 목록에서 센다(카테고리 요약이 없거나 어긋나도 맞게)
      const c = { up: 0, degraded: 0, down: 0, unknown: 0 };
      for (const sv of list) c[sv.status in c ? sv.status : "unknown"]++;
      const counts = [];
      if (c.up) counts.push(html`<span class="t-ok" title="정상 ${c.up}">●${c.up}</span>`);
      if (c.degraded) counts.push(html`<span class="t-warn" title="저하 ${c.degraded}">▲${c.degraded}</span>`);
      if (c.down) counts.push(html`<span class="t-crit" title="다운 ${c.down}">✖${c.down}</span>`);
      if (c.unknown) counts.push(html`<span class="dim" title="확인 불가 ${c.unknown}">◌${c.unknown}</span>`);
      put(g.head, html`<span class="gi" aria-hidden="true">${CAT_ICON[cat.id] || "▪"}</span><span>${cat.label || labels.category[cat.id] || cat.id}</span><span class="gd">${cat.desc || ""}</span><span class="rule" aria-hidden="true"></span><span class="gc">${list.length}개 ${counts.map((x) => html` ${x}`)}</span>`);
      let prev = null;
      for (const sv of list) {
        seen.add(sv.id);
        let r = rowEls.get(sv.id);
        if (!r) { r = makeRow(sv.id); rowEls.set(sv.id, r); }
        const want2 = prev ? prev.li.nextSibling : g.ul.firstChild;
        if (r.li.parentNode !== g.ul || r.li !== want2) g.ul.insertBefore(r.li, want2);
        prev = r;
        r.cat = catKey(sv.category);
        r.sv = sv;
        updRow(r, sv);
      }
    }
    for (const [id, r] of rowEls) if (!seen.has(id)) { r.li.remove(); rowEls.delete(id); S.open.delete(id); }
    if (!emptyEl.parentNode) svcList.appendChild(emptyEl);
    else if (svcList.lastChild !== emptyEl) svcList.appendChild(emptyEl);
    applyFilter();
    if (S.sel && !rowEls.has(S.sel)) S.sel = null;
    // 상태 변화로 행이 재정렬되며 DOM 이동 중 잃은 포커스를 되돌린다
    if (af && af !== document.body && af.isConnected && document.activeElement !== af && svcList.contains(af)) af.focus({ preventScroll: true });
  }

  function svcMatch(sv, q) {
    if (!q) return true;
    const hay = [sv.id, sv.name, sv.title, sv.kind, labels.kind[sv.kind], sv.upstream, sv.description, labels.category[sv.category], stLabel(sv.status), sv.status]
      .concat(sv.hostnames || [], (sv.urls || []).map((u) => u.url), (sv.listeners || []).map((l) => ":" + l.port + " " + l.port))
      .concat(sv.runtime ? [sv.runtime.name, sv.runtime.image, sv.runtime.project, sv.runtime.ip] : [])
      .filter(Boolean).join(" ").toLowerCase();
    return q.toLowerCase().split(/\s+/).filter(Boolean).every((t) => hay.includes(t));
  }
  function applyFilter() {
    let shown = 0;
    for (const [, r] of rowEls) {
      const vis = (S.cat === "all" || r.cat === S.cat) && svcMatch(r.sv || {}, S.filter);
      if (r.li.hidden === vis) r.li.hidden = !vis;
      if (vis) shown++;
    }
    for (const [id, g] of groupEls) {
      const any = $$(".svc", g.ul).some((li) => !li.hidden);
      if (g.el.hidden === any) g.el.hidden = !any;
    }
    emptyEl.hidden = shown > 0;
    if (!shown) emptyEl.textContent = "일치하는 서비스가 없습니다" + (S.filter ? " — 검색어: " + S.filter : "") + ". Esc로 필터를 지웁니다.";
    $('[data-act="clear-filter"]').hidden = !S.filter;
  }
  function setFilter(v) {
    S.filter = v;
    if (filterInput.value !== v) filterInput.value = v;
    applyFilter();
  }
  function setCat(c) {
    S.cat = c || "all";
    $$("#svc-cats button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.cat === S.cat)));
    applyFilter();
  }
  function cycleCat() {
    const ids = ["all"].concat(((S.snap && S.snap.categories) || []).filter((c) => c && c.id != null).map((c) => catKey(c.id)));
    const i = ids.indexOf(S.cat);
    setCat(ids[(i + 1) % ids.length]);
    setActive(2);
  }
  function visibleRows() { return $$(".svc", svcList).filter((li) => !li.hidden && !li.closest(".grp").hidden); }
  function select(id, o) {
    o = o || {};
    S.sel = id;
    for (const [k, r] of rowEls) r.li.classList.toggle("sel", k === id);
    const r = rowEls.get(id);
    if (!r) return;
    if (o.focus !== false) r.tog.focus({ preventScroll: true });
    if (o.scroll !== false) r.li.scrollIntoView({ block: o.center ? "center" : "nearest", behavior: smooth() });
  }
  function move(d) {
    const rows = visibleRows();
    if (!rows.length) return;
    let i = rows.findIndex((li) => li.dataset.id === S.sel);
    i = i < 0 ? (d > 0 ? 0 : rows.length - 1) : clamp(i + d, 0, rows.length - 1);
    setActive(2);
    select(rows[i].dataset.id);
  }
  function toggleOpen(id, force) {
    const open = force != null ? force : !S.open.has(id);
    if (open) S.open.add(id); else S.open.delete(id);
    const r = rowEls.get(id);
    if (!r) return;
    r.tog.setAttribute("aria-expanded", String(open));
    r.det.hidden = !open;
    if (open && r.sv) put(r.det, svcDetail(r.sv));
    if (!open) P.render(r.det, "");
  }
  function gotoSvc(id) {
    const r = rowEls.get(id);
    if (!r) return;
    if (r.li.hidden) { setCat("all"); setFilter(""); }
    setActive(2);
    toggleOpen(id, true);
    select(id, { center: true });
  }

  // ───────────────────────────────────────────── 3 · 네트워크 맵
  const LANE_ORDER = { public: 0, tailnet: 1, lan: 2, "tailnet-d": 3, internal: 5 };
  function renderMap(s) {
    const topo = s.topology || { nodes: [], edges: [] };
    const N = new Map((topo.nodes || []).map((n) => [n.id, n]));
    const inE = new Map(), dep = new Map(), rdep = new Map(), outCnt = new Map();
    for (const e of topo.edges || []) {
      if (e.kind === "depends") {
        if (!dep.has(e.from)) dep.set(e.from, []);
        dep.get(e.from).push(e.to);
        if (!rdep.has(e.to)) rdep.set(e.to, []);
        rdep.get(e.to).push(e.from);
        continue;
      }
      if (!inE.has(e.to)) inE.set(e.to, []);
      inE.get(e.to).push(e);
      outCnt.set(e.from, (outCnt.get(e.from) || 0) + 1);
    }
    const lanes = new Map();
    for (const n of topo.nodes || []) {
      if (n.type === "zone" || outCnt.get(n.id)) continue;
      const ins = inE.get(n.id) || [];
      for (const e of ins) {
        const hops = [];
        const seen = new Set([n.id]);
        let node = N.get(e.from), out = null;
        while (node && !seen.has(node.id)) {
          seen.add(node.id);
          hops.unshift({ node, out });
          if (node.type === "zone") break;
          const prev = inE.get(node.id) || [];
          const p = prev.find((x) => x.kind === e.kind) || prev[0];
          if (!p) break;
          out = p.label; node = N.get(p.from);
        }
        if (!hops.length) continue;
        const direct = hops.length === 1;
        const k = e.kind;
        const key = hops.map((h) => h.node.id).join(">") + "|" + k;
        let lane = lanes.get(key);
        if (!lane) {
          let title, desc, order;
          const z = hops[0].node;
          if (z.id === "zone:internet") { title = "공개 경로"; desc = "인터넷 누구나 — Cloudflare Tunnel 경유"; order = LANE_ORDER.public; }
          else if (e.kind === "tailnet" && !direct) { title = "Tailnet 경로"; desc = "tailnet 기기만 — *." + ((s.meta && s.meta.domain) || "") + " → Caddy :443"; order = LANE_ORDER.tailnet; }
          else if (e.kind === "lan") { title = "LAN 직접"; desc = "같은 LAN의 기기에서 포트로 직접"; order = LANE_ORDER.lan; }
          else if (e.kind === "tailnet") { title = "Tailnet 직접"; desc = "tailnet 기기에서 포트로 직접"; order = LANE_ORDER["tailnet-d"]; }
          else { title = hops.map((h) => h.node.label).join(" → "); desc = ""; order = 9; }
          lane = { key, k, title, desc, order, hops, targets: [] };
          lanes.set(key, lane);
        }
        lane.targets.push({ node: n, label: e.label });
      }
    }
    // 한 레인의 대상이 모두 같은 라벨(예: WireGuard)이면 선 위에 한 번만 표시
    for (const l of lanes.values()) {
      const u = new Set(l.targets.map((t) => t.label || ""));
      if (u.size === 1 && l.targets[0].label) {
        l.hops[l.hops.length - 1] = Object.assign({}, l.hops[l.hops.length - 1], { out: l.targets[0].label });
        l.targets.forEach((t) => { t.label = null; });
      }
    }
    // 내부 전용: 들어오는 경로 없이 의존으로만 연결되거나 이 기기 안에서만 열린 노드
    const internals = (topo.nodes || []).filter((n) => n.type !== "zone" && !(inE.get(n.id) || []).length && !outCnt.get(n.id));
    const all = Array.from(lanes.values()).sort((a, b) => a.order - b.order);
    const tgtBtn = (n, lbl) => {
      const sid = n.service_id;
      // 목록과 같은 판정을 보이도록 서비스 상태를 우선 쓴다
      const sv = sid ? P.pick.service(s, sid) : null;
      const status = sv ? sv.status : n.status;
      const inner = html`${st(status)}<span class="tn">${n.label}</span>`;
      return sid ? html`<button type="button" data-act="goto" data-id="${sid}" data-fk="map:${n.id}:${lbl || ""}" aria-label="${n.label} 서비스 상세 보기">${inner}</button>` : html`<span class="b">${inner}</span>`;
    };
    const detailOf = (n) => {
      if (n.type === "host") return n.detail ? String(n.detail).split(/,\s*/).map((p) => ":" + p).join(" ") : "";
      if (n.type === "service") return n.detail && n.label && String(n.detail).startsWith(n.label + ".") ? "" : n.detail || "";
      return n.detail || "";
    };
    const wire = (lbl) => html`<span class="wire" aria-hidden="true"><s></s>${lbl ? html`<i>${lbl}</i><s></s>` : ""}<b>▶</b></span>`;
    const hopBox = (n) => html`<span class="hop ${n.type === "zone" ? "zone" : ""} s-${n.status || "unknown"}"><span>${n.type !== "zone" ? html`${st(n.status)} ` : ""}${n.label}</span>${n.detail ? html`<span class="hd">${n.detail}</span>` : ""}</span>`;
    const depsOf = (n) => (dep.get(n.id) || []).map((d) => N.get(d)).filter(Boolean);
    const laneHtml = all.map((l) => html`<div class="lane" data-k="${l.k}">
      <div class="lane-h"><span class="lt">${l.title} <span class="dim">· ${l.targets.length}</span></span><span class="ld">${l.desc}</span></div>
      <div class="chain">${l.hops.map((h, i) => html`<span class="cu">${hopBox(h.node)}${i < l.hops.length - 1 ? wire(h.out) : h.out ? wire(h.out) : ""}</span>`)}<span class="sr">에서 아래 대상으로 연결</span></div>
      <ul class="tree">${l.targets.map((t) => {
        const ds = depsOf(t.node);
        const d = detailOf(t.node);
        return html`<li class="tgt">${tgtBtn(t.node, l.key)}${d ? html`<span class="td">${d}</span>` : ""}${t.label ? html`<span class="tl">${t.label}</span>` : ""}${ds.length ? html`<span class="dep">→ ${ds.map((x, i) => html`${i ? ", " : ""}<b>${x.label}</b>`)}</span>` : ""}</li>`;
      })}</ul>
    </div>`);
    const intHtml = internals.length ? html`<div class="lane" data-k="internal">
      <div class="lane-h"><span class="lt">내부 전용 <span class="dim">· ${internals.length}</span></span><span class="ld">외부 노출 없음 — Docker 네트워크·loopback 안에서만</span></div>
      <ul class="tree">${internals.map((n) => {
        const by = (rdep.get(n.id) || []).map((d) => N.get(d)).filter(Boolean);
        return html`<li class="tgt">${tgtBtn(n, "int")}<span class="td">${n.detail || ""}</span>${by.length ? html`<span class="dep">← ${by.map((x, i) => html`${i ? ", " : ""}<b>${x.label}</b>`)}</span>` : ""}</li>`;
      })}</ul>
    </div>` : "";
    const sum = s.summary || {};
    put("map-meta", html`공개 <b>${num(sum.public)}</b> · Tailnet <b>${num(sum.tailnet)}</b> · 호스트 <b>${num(sum.host)}</b> · Hermes <b>${num(sum.hermes)}</b>`);
    put("map-body", html`<div class="lanes">${laneHtml}${intHtml}</div>
      <div class="maplegend" aria-label="범례"><span style="--c:var(--org)">공개(인터넷)</span><span style="--c:var(--accent)">Tailnet</span><span style="--c:var(--teal)">LAN</span><span style="--c:var(--dim)">내부 · 의존(→)</span></div>`);
  }

  // ───────────────────────────────────────────── 5 · 인그레스
  function stack(parts) {
    const tot = parts.reduce((a, p) => a + (p.v || 0), 0);
    return html`<div class="stack" aria-hidden="true">${tot ? parts.filter((p) => p.v).map((p) => html`<i style="width:${((p.v / tot) * 100).toFixed(2)}%;background:var(--${p.c})"></i>`) : ""}</div>
      <div class="legend">${parts.map((p) => html`<span class="${p.v ? "" : "zero"}" style="--c:var(--${p.c})">${p.l} <b>${num(p.v || 0)}</b>${tot && p.v ? html` <span class="dim">${Math.round((p.v / tot) * 100)}%</span>` : ""}</span>`)}</div>`;
  }
  function barsSVG(vals, errs, o) {
    o = o || {};
    const n = (vals || []).length;
    if (!n) return raw(`<svg class="bars" aria-hidden="true"></svg>`);
    const W = n * 10, H = 40;
    const max = Math.max(1, ...vals.map((v) => +v || 0));
    let r = "";
    vals.forEach((v, i) => {
      const h = v ? Math.max(1.5, (v / max) * (H - 2)) : 0;
      if (h) r += `<rect class="${i === n - 1 && o.cur ? "cur" : "b"}" x="${i * 10 + 1}" y="${(H - h).toFixed(1)}" width="8" height="${h.toFixed(1)}"/>`;
      const e = errs && +errs[i];
      if (e) { const eh = Math.max(1.5, (e / max) * (H - 2)); r += `<rect class="e" x="${i * 10 + 1}" y="${(H - eh).toFixed(1)}" width="8" height="${eh.toFixed(1)}"/>`; }
    });
    r += `<line class="base" x1="0" x2="${W}" y1="${H - 0.5}" y2="${H - 0.5}"/>`;
    return raw(`<svg class="bars" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">${r}</svg>`);
  }
  function scopeBadge(sc) {
    const m = { all: ["all", "LAN 전체"], lan: ["lan", "LAN"], tailnet: ["ts", "Tailnet"], docker: ["dock", "Docker"], loopback: ["loc", "로컬"] }[sc] || ["loc", sc || "?"];
    return html`<span class="bdg ${m[0]}" title="${labels.scope[sc] || sc || ""}">${m[1]}</span>`;
  }
  function renderIngress(s) {
    const ing = s.ingress || {};
    const cd = ing.caddy || {};
    const tn = ing.tunnel || {};
    const tr = ing.traffic || {};
    const dom = ing.domain || (s.meta && s.meta.domain);
    const now = (s.meta && s.meta.generated_at) || Date.now() / 1000;
    const routes = cd.routes || [];
    put("ing-meta", html`Caddy <b>${cd.version || DASH}</b> · 라우트 <b>${routes.length}</b> · 터널 <b class="t-${tn.ready ? "ok" : "crit"}">${tn.ready_connections != null ? tn.ready_connections : DASH}/${tn.connections != null ? tn.connections : DASH}</b> · 요청 1h <b>${num(tr.last_1h && tr.last_1h.requests)}</b>`);

    // caddy
    const svcOf = (h) => P.pick.service(s, "route:" + h);
    const hostChip = (h, pub) => { const x = svcOf(h); return html`<span class="host ${pub ? "pub" : ""}">${x ? st(x.status) : ""} ${shortHost(h, dom)}</span>`; };
    put("caddy-meta", html`${cd.container || ""}`);
    put("caddy-body", html`<div class="lines">
        ${line("버전", html`<b>${cd.version || DASH}</b>`)}
        ${line("설정", cd.config_ok === false ? html`<span class="t-crit">✖ 오류 — ${cd.error || "알 수 없음"}</span>` : cd.config_ok ? html`<span class="t-ok">✓ 정상</span>` : DASH)}
        ${line("라우트", html`${routes.length} <span class="dim">(공개 ${(cd.public_hosts || []).length} · 비공개 ${(cd.private_hosts || []).length})</span>`)}
        ${line("Caddyfile", cd.caddyfile_mtime ? html`${fmt.ago(cd.caddyfile_mtime, now)} 수정 <span class="dim">${fmt.datetime(cd.caddyfile_mtime)}</span>` : DASH)}
      </div>
      <div style="margin-top:8px"><span class="dim">공개 호스트 — 터널 :80 + tailnet :443</span><div class="hosts">${(cd.public_hosts || []).map((h) => hostChip(h, true))}</div></div>
      <div style="margin-top:8px"><span class="dim">Tailnet 전용 — :443만, 터널 경로는 404</span><div class="hosts">${(cd.private_hosts || []).map((h) => hostChip(h, false))}</div></div>`);

    // tunnel
    const codes = tn.responses_by_code || {};
    const cls = { "2xx": 0, "3xx": 0, "4xx": 0, "5xx": 0 };
    for (const [k, v] of Object.entries(codes)) { const c = String(k)[0] + "xx"; if (c in cls) cls[c] += v; }
    const errs = tn.recent_errors || [];
    put("tun-meta", html`<span class="t-${tn.ready ? "ok" : "crit"}">${tn.ready ? "● 준비됨" : "✖ 준비 안 됨"}</span>`);
    put("tun-body", html`<div class="lines">
        ${line("터널", html`<b>${tn.name || DASH}</b> <span class="dim">${tn.container || ""}</span>`)}
        ${line("연결", html`<span class="t-${tn.ready_connections && tn.ready_connections === tn.connections ? "ok" : "warn"}">${tn.ready_connections != null ? tn.ready_connections : DASH}/${tn.connections != null ? tn.connections : DASH}</span> <span class="dim">준비된 연결</span>`)}
        ${line("엣지", (tn.edge_locations || []).join(" · ") || DASH)}
        ${line("RTT", (tn.rtt_ms || []).length ? html`${tn.rtt_ms.map((x) => fmt.num(x, 0)).join(" · ")} ms` : DASH)}
        ${line("요청", html`${num(tn.total_requests)} 누적 · <span class="${tn.request_errors ? "t-warn" : "dim"}">오류 ${num(tn.request_errors)}</span> · 분당 ${fmt.num(tn.req_per_min, 1)} · 동시 ${num(tn.concurrent_requests)}`)}
        ${line("인그레스", html`v${tn.ingress_version != null ? tn.ingress_version : DASH} · 호스트 ${(tn.hostnames || []).length}${tn.ingress_updated ? html` <span class="dim">· ${fmt.ago(tn.ingress_updated, now)} 갱신</span>` : ""}`)}
        ${line("버전", html`${tn.version || DASH}${tn.process_start ? html` <span class="dim">· 가동 ${fmt.durShort(now - tn.process_start)}</span>` : ""}`)}
      </div>
      ${tn.outdated ? html`<p class="notice"><b>↑ 업데이트</b> cloudflared ${tn.outdated.current} → <b>${tn.outdated.latest}</b> 사용 가능</p>` : ""}
      <div style="margin-top:10px"><span class="dim">응답 코드 (누적)</span>${stack([{ l: "2xx", v: cls["2xx"], c: "ok-f" }, { l: "3xx", v: cls["3xx"], c: "info-f" }, { l: "4xx", v: cls["4xx"], c: "warn-f" }, { l: "5xx", v: cls["5xx"], c: "crit-f" }])}</div>
      ${errs.length ? html`<div style="margin-top:8px"><button type="button" class="toggle-btn" data-act="tun-errs" aria-expanded="${S.tunErrs ? "true" : "false"}" data-fk="tunerr">${S.tunErrs ? "▾" : "▸"} 최근 터널 오류 ${errs.length}건</button>
        ${S.tunErrs ? html`<ul class="errlist" style="margin-top:6px">${errs.slice().reverse().map((x) => html`<li><span class="t-${x.level === "error" ? "crit" : "warn"}">${fmt.datetime(x.ts)}</span><span class="m">${x.msg}</span></li>`)}</ul>` : ""}</div>` : ""}`);

    // traffic
    const h1 = tr.last_1h || {}, h24 = tr.last_24h || {};
    put("trf-meta", html`1h <b>${num(h1.requests)}</b> · 24h <b>${num(h24.requests)}</b>`);
    const st1 = h1.by_status || {}, st24 = h24.by_status || {};
    const viaC = { tunnel: "org-f", tailnet: "acc-f", lan: "teal-f", docker: "mag-f", local: "faint", public: "org-f", unknown: "dim" };
    const viaParts = (bv) => Object.entries(bv || {}).sort((a, b) => b[1] - a[1]).map(([k, v]) => ({ l: labels.trafficVia[k] || k, v, c: viaC[k] || "dim" }));
    const pm = tr.per_minute || [];
    const pm5 = tr.per_minute_5xx || [];
    put("trf-body", html`<div class="lines">
        ${line("지연 1h", html`p50 ${fmt.ms(h1.p50_ms)} · p95 ${fmt.ms(h1.p95_ms)}`)}
        ${line("지연 24h", html`p50 ${fmt.ms(h24.p50_ms)} · p95 ${fmt.ms(h24.p95_ms)}`)}
      </div>
      <div style="margin-top:10px"><div class="line"><span class="l">분당 요청 · 최근 60분</span><span class="r dim">최대 ${num(Math.max(0, ...pm))}/분${pm5.some((x) => x) ? html` · <span class="t-crit">■ 5xx</span>` : ""}</span></div>${barsSVG(pm, pm5, { cur: true })}<div class="taxis"><span>-60분</span><span>-30분</span><span>지금</span></div></div>
      <div style="margin-top:8px"><div class="line"><span class="l">시간당 요청 · 최근 24시간</span><span class="r dim">최대 ${num(Math.max(0, ...(tr.per_hour || [0])))}/시</span></div>${barsSVG(tr.per_hour || [], null, { cur: true })}<div class="taxis"><span>-24시간</span><span>-12시간</span><span>지금</span></div></div>
      <div style="margin-top:10px"><span class="dim">상태 코드 · 1h</span>${stack([{ l: "2xx", v: st1["2xx"], c: "ok-f" }, { l: "3xx", v: st1["3xx"], c: "info-f" }, { l: "4xx", v: st1["4xx"], c: "warn-f" }, { l: "5xx", v: st1["5xx"], c: "crit-f" }])}</div>
      <div style="margin-top:8px"><span class="dim">상태 코드 · 24h</span>${stack([{ l: "2xx", v: st24["2xx"], c: "ok-f" }, { l: "3xx", v: st24["3xx"], c: "info-f" }, { l: "4xx", v: st24["4xx"], c: "warn-f" }, { l: "5xx", v: st24["5xx"], c: "crit-f" }])}</div>
      <div style="margin-top:10px"><span class="dim">유입 경로 · 1h</span>${stack(viaParts(h1.by_via))}</div>
      <div style="margin-top:8px"><span class="dim">유입 경로 · 24h</span>${stack(viaParts(h24.by_via))}</div>`);

    // top hosts
    const allHosts = h24.hosts || [];
    const hl = limited(allHosts, "hosts", MQ_M.matches ? 8 : 15, true);
    const h1map = new Map((h1.hosts || []).map((x) => [x.host, x]));
    const unknownN = allHosts.filter((x) => x.known === false).reduce((a, x) => a + (x.folded ? x.folded : 1), 0);
    put("hosts-meta", html`24h 기준 · ${allHosts.length}개${unknownN ? html` · <span class="t-warn">미등록 ${unknownN}</span>` : ""}`);
    const hostCell = (x) => {
      const name = x.host === "" || x.host == null ? html`<span class="dim">(Host 헤더 없음)</span>` : shortHost(x.host, dom);
      return html`${x.known === false ? html`<span class="bdg unk" title="Caddy에 없는 호스트">미등록</span> ` : ""}${cl(name, 28, x.host || "")}${x.folded ? html` <span class="dim" title="이름을 접은 미등록 호스트 수">묶음 ${x.folded}</span>` : ""}`;
    };
    put("hosts-body", allHosts.length ? html`<table class="tbl stk t-hosts"><thead><tr><th>HOST</th><th class="r">24h</th><th class="r">1h</th><th class="r">4xx</th><th class="r">5xx</th><th class="r">p95</th><th class="r">마지막</th></tr></thead><tbody>
      ${hl.items.map((x) => html`<tr class="${x.known === false ? "hl" : ""}"><td class="p fx hcell">${hostCell(x)}</td><td class="r b k">${num(x.requests)}<span class="m-only dim"> 요청</span></td><td class="r s" data-l="1h">${num((h1map.get(x.host) || {}).requests || 0)}</td><td class="r s ${x.errors_4xx ? "t-warn" : "dim"}" data-l="4xx">${num(x.errors_4xx)}</td><td class="r s ${x.errors_5xx ? "t-crit" : "dim"}" data-l="5xx">${num(x.errors_5xx)}</td><td class="r s" data-l="p95">${fmt.ms(x.p95_ms)}</td><td class="r dim s" data-l="마지막">${fmt.ago(x.last_ts, now)}</td></tr>`)}
    </tbody></table>` : html`<p class="dim">최근 24시간 요청 없음</p>`);
    put("hosts-more", moreBtn("hosts", hl, "호스트 더 보기"));

    // recent — 고정 열 폭: 폴링마다 열이 흔들리지 않고, PATH 가 남는 폭을 모두 쓴다
    const rec = tr.recent || [];
    const rl = limited(rec, "recent", 10);
    const viaShort = { tunnel: "터널", tailnet: "Tailnet", lan: "LAN", docker: "Docker", local: "로컬" };
    put("recent-meta", html`최신 ${rec.length}건`);
    put("recent-body", rec.length ? html`<table class="tbl fixed stk t-recent"><colgroup><col class="w-time"><col class="w-stm"><col class="w-host"><col class="w-via"><col class="w-cli"><col class="w-ms"><col class="w-size"><col></colgroup>
      <thead><tr><th>TIME</th><th>ST</th><th>HOST</th><th>VIA</th><th>CLIENT</th><th class="r">MS</th><th class="r">SIZE</th><th>PATH</th></tr></thead><tbody>
      ${rl.items.map((q) => html`<tr><td class="dim">${fmt.time(q.ts)}</td><td class="nowrap"><span class="t-${codeTone(q.status)}">${q.status != null ? q.status : DASH}</span> <span class="dim">${q.method || ""}</span></td><td class="p ell" title="${q.host || ""}">${shortHost(q.host, dom)}</td><td class="s"><span class="t-${q.via === "tunnel" ? "org" : q.via === "tailnet" ? "acc" : "muted"}" title="${labels.trafficVia[q.via] || q.via || ""}">${viaShort[q.via] || q.via || "?"}</span></td><td class="dim s ell" title="${(q.client || "") + (q.country ? " · " + q.country : "")}">${q.client || ""}${q.country ? " · " + q.country : ""}</td><td class="r k">${fmt.ms(q.ms)}</td><td class="r dim s">${fmt.bytes(q.bytes, 0)}</td><td class="s ell path" title="${q.path || ""}">${q.path || ""}</td></tr>`)}
    </tbody></table>` : html`<p class="dim">최근 요청 없음</p>`);
    put("recent-more", moreBtn("recent", rl, "요청 더 보기"));

    // caddy errors
    const ce = tr.errors || [];
    put("cerr-meta", html`${ce.length}건`);
    const ceShow = S.cerrAll ? ce : ce.slice(0, 5);
    put("cerr-body", ce.length ? html`<ul class="errlist">${ceShow.map((x) => html`<li><span class="t-${x.level === "error" ? "crit" : "warn"} nowrap">${fmt.time(x.ts)} ${x.level}</span><span class="m">${x.msg} <span class="dim">${x.logger || ""}</span></span>${x.error ? html`<span class="e">${x.error}</span>` : ""}</li>`)}</ul>
      ${ce.length > 5 ? html`<button type="button" class="toggle-btn" data-act="cerr-all" aria-expanded="${S.cerrAll ? "true" : "false"}" data-fk="cerrall">${S.cerrAll ? "▾ 접기" : "▸ 모두 보기 (" + ce.length + ")"}</button>` : ""}` : html`<p class="alerts-empty"><span class="ok">✓</span> Caddy 오류 없음</p>`);
  }

  // ───────────────────────────────────────────── 6 · Tailscale
  function renderTs(s) {
    const ts = s.tailscale || {};
    const self = ts.self || {};
    const peers = ts.peers || [];
    const now = (s.meta && s.meta.generated_at) || Date.now() / 1000;
    const on = peers.filter((p) => p.online);
    const direct = on.filter((p) => p.connection === "direct").length;
    const relay = on.filter((p) => p.connection === "relay").length;
    const running = ts.backend_state === "Running";
    put("ts-meta", html`<span class="t-${running ? "ok" : "crit"}">${running ? "●" : "✖"} ${ts.backend_state || DASH}</span> · 피어 <b>${num(ts.peers_online)}</b>/${num(ts.peers_total)}`);
    put("ts-head", html`<div class="sumline">
        <span>온라인 <b class="t-ok">${num(ts.peers_online)}</b>/${num(ts.peers_total)}</span>
        <span>직접 <b>${direct}</b></span>
        <span>릴레이 <b class="${relay ? "t-warn" : ""}">${relay}</b></span>
        <span>대기 <b>${on.length - direct - relay}</b></span>
        <span class="dim">v${ts.version || DASH}</span>
      </div>
      <dl class="kv" style="margin-bottom:12px">
        ${kvRow("이 기기", html`<b>${self.hostname || DASH}</b> <span class="dim">${self.dns_name || ""}</span>`)}
        ${kvRow("IP", (self.ips || []).join(" · ") || DASH)}
        ${kvRow("tailnet", html`${ts.tailnet || DASH} <span class="dim">· MagicDNS ${ts.magic_dns_suffix || DASH}</span>`)}
        ${kvRow("DERP", html`${self.relay || DASH}${self.key_expiry ? html` <span class="dim">· 키 만료 ${fmt.datetime(self.key_expiry)}</span>` : ""}`)}
        ${kvRow("Serve", html`${ts.serve ? "켜짐" : html`<span class="dim">꺼짐</span>`} · Funnel ${ts.funnel ? html`<span class="t-warn">켜짐 (공개)</span>` : html`<span class="dim">꺼짐</span>`}`)}
        ${kvRow("점검", (ts.health || []).length ? html`${ts.health.map((x) => html`<div class="t-warn">▲ ${x}</div>`)}` : html`<span class="t-ok">✓ 이상 없음</span>`)}
      </dl>`);
    const conn = (p) => {
      if (!p.online) return html`<span class="dim">${DASH}</span>`;
      if (p.connection === "direct") return html`<span class="t-ok">직접</span>`;
      if (p.connection === "relay") return html`<span class="t-warn">릴레이 ${p.relay || ""}</span>`;
      return html`<span class="dim">대기</span>`;
    };
    const pl = limited(peers, "peers", 10);
    const seen = (p) => (p.online ? (p.active ? "지금 활성" : "온라인") : p.last_seen ? fmt.ago(p.last_seen, now) : DASH);
    put("ts-peers", peers.length ? html`<table class="tbl stk t-peers"><thead><tr><th>HOST</th><th>연결</th><th>마지막 접속</th><th>IP</th><th class="r c-rx">RX/TX</th></tr></thead><tbody>
      ${pl.items.map((p) => html`<tr class="${p.online ? "" : "off"}"><td class="p fx" title="${(p.dns_name || "") + (p.tags && p.tags.length ? " · " + p.tags.join(" ") : "")}"><span class="hn">${st(p.online ? "up" : "unknown")} ${cl(p.hostname, 22, p.hostname, p.online ? "b" : "")} <span class="dim c-os">${p.os || ""}</span>${p.exit_node ? html` <span class="bdg ts">exit</span>` : ""}</span></td><td class="s" title="${p.cur_addr || ""}">${conn(p)}</td><td class="k ${p.online ? "dim" : ""}">${seen(p)}</td><td class="dim s">${p.ip || ""}</td><td class="r dim s c-rx" data-l="RX/TX">${p.rx_bytes || p.tx_bytes ? fmt.bytes(p.rx_bytes, 0) + " / " + fmt.bytes(p.tx_bytes, 0) : DASH}</td></tr>`)}
    </tbody></table>` : html`<p class="dim">피어 없음</p>`);
    put("ts-more", moreBtn("peers", pl, "피어 더 보기"));
  }

  // ───────────────────────────────────────────── 7 · 포트
  const SCOPE_RANK = { all: 0, lan: 1, tailnet: 2, docker: 3, loopback: 4 };
  function renderPorts(s) {
    const ls = (s.listeners || []).filter((l) => l && l.port != null).sort((a, b) => (SCOPE_RANK[a.scope] ?? 5) - (SCOPE_RANK[b.scope] ?? 5) || (String(a.proto) > String(b.proto) ? 1 : String(a.proto) < String(b.proto) ? -1 : 0) || a.port - b.port);
    const sum = (s.summary && s.summary.listeners) || {};
    const udp = ls.filter((l) => l.proto === "udp").length;
    const wide = (l) => l.scope === "all" || l.scope === "lan";
    const lanWide = ls.filter(wide);
    const lanT = lanWide.filter((l) => l.proto !== "udp").length, lanU = lanWide.length - lanT;
    put("port-meta", html`TCP <b>${num(sum.total)}</b> · UDP <b>${udp}</b>`);
    // 요약 수와 표의 ▲ 줄 수가 같도록, LAN 노출은 표에서 직접 센다(TCP·UDP 나눠 표시)
    put("port-head", html`<div class="sumline">
        <span class="t-warn">▲ LAN 노출 <b>${lanWide.length}</b> <span class="dim">(TCP ${lanT} · UDP ${lanU})</span></span>
        <span>Tailnet 전용 <b>${num(sum.tailnet_only)}</b></span>
        <span>loopback <b>${num(sum.loopback)}</b></span>
      </div>
      ${lanWide.length ? html`<p class="dim note">▲ 표시한 줄은 모든 인터페이스(0.0.0.0)나 LAN에 바인딩되어 같은 네트워크의 모든 기기에서 접근할 수 있습니다.</p>` : ""}`);
    const ownL = { container: "ctr", unit: "unit", "user-unit": "user", scope: "scope", unknown: "?" };
    const lim = limited(ls, "ports", 12);
    put("port-body", ls.length ? html`<table class="tbl stk t-ports"><thead><tr><th class="mk"><span class="sr">LAN 노출</span></th><th class="r">PORT</th><th>범위</th><th>프로세스</th><th>소유</th><th>주소</th></tr></thead><tbody>
      ${lim.items.map((l) => html`<tr class="${wide(l) ? "hl" : ""}"><td class="mk">${wide(l) ? html`<span class="t-warn" title="LAN 전체에 노출">▲</span>` : ""}</td><td class="r b pt">${l.port}<span class="dim" style="font-weight:400">/${l.proto || "tcp"}</span></td><td class="k">${scopeBadge(l.scope)}</td><td class="p fx">${cl(html`${l.process || DASH}${l.pid ? html` <span class="dim">${l.pid}</span>` : ""}`, 22, (l.process || "") + (l.pid ? " (pid " + l.pid + ")" : ""))}</td><td class="s">${cl(html`<span class="t-mag">${ownL[l.owner_kind] || l.owner_kind || ""}</span> <span class="dim">${l.owner || ""}</span>`, 28, l.owner || "")}</td><td class="dim s">${cl((l.addrs || []).join(", "), 26, (l.addrs || []).join(", "))}</td></tr>`)}
    </tbody></table>` : html`<p class="dim">열린 포트 정보 없음</p>`);
    put("port-more", moreBtn("ports", lim, "포트 더 보기"));
  }

  // ───────────────────────────────────────────── 8 · 도커 · systemd
  /** 컨테이너 포트: 게시된 매핑만 "바인드:공개→내부"로(IPv4/IPv6 중복은 하나로), 게시 안 된 노출 포트는 "+N 내부"로 접는다. */
  function ctrPorts(c) {
    const pub = new Map();
    const inner = [];
    for (const p of c.ports || []) {
      if (!p || p.private == null) continue;
      const proto = p.proto || "tcp";
      const pp = proto === "udp" ? "/udp" : "";
      if (p.public == null) { const k = p.private + pp; if (!inner.includes(k)) inner.push(k); continue; }
      const ip = p.ip || "";
      const bind = !ip || ip === "0.0.0.0" || ip === "::" ? "*:" : /^127\./.test(ip) || ip === "::1" ? "lo:" : (ip.includes(":") ? "[" + ip + "]" : ip) + ":";
      const key = bind + p.public + "/" + p.private + "/" + proto;
      if (!pub.has(key)) pub.set(key, bind + p.public + "→" + p.private + pp);
    }
    const pubs = Array.from(pub.values());
    if (!pubs.length && !inner.length) return { text: c.network_mode === "host" ? "host 네트워크" : DASH, inner: [], title: "" };
    const title = (pubs.length ? "게시: " + pubs.join(", ") : "게시 없음") + (inner.length ? " · 내부 전용: " + inner.join(", ") : "") + " (*: 모든 인터페이스, lo: loopback)";
    return { text: pubs.join(" "), inner, title };
  }
  function renderDock(s) {
    const cs = (s.containers || []).filter((c) => c && typeof c === "object");
    const prj = s.projects || [];
    const units = s.units || [];
    const sum = (s.summary && s.summary.containers) || {};
    const bad = (c) => (c.state !== "running" ? 0 : c.health === "unhealthy" ? 1 : c.restart_count ? 2 : 3);
    cs.sort((a, b) => bad(a) - bad(b) || (String(a.name) > String(b.name) ? 1 : -1));
    const failed = units.filter((u) => u.active === "failed");
    put("dock-meta", html`컨테이너 <b>${num(sum.running)}</b>/${num(sum.total)} · healthy <b>${num(sum.healthy)}</b>${sum.unhealthy ? html` · <span class="t-crit">unhealthy ${sum.unhealthy}</span>` : ""} · 유닛 실패 <b class="${failed.length ? "t-crit" : ""}">${failed.length}</b>`);
    put("proj-meta", html`${prj.length}개 프로젝트`);
    const pst = { running: "up", partial: "degraded", stopped: "down" };
    put("proj-body", html`<div class="projs">${prj.map((p) => html`<span class="proj" title="${p.dir || ""}">${st(pst[p.status] || "unknown")} ${p.name} <span class="c">${p.running}/${p.total}</span></span>`)}</div>`);
    put("ctr-meta", html`${num(sum.running)}/${num(sum.total)} 실행`);
    const cst = (c) => (c.state !== "running" ? (c.state === "restarting" ? "degraded" : "down") : c.health === "unhealthy" ? "down" : c.health === "starting" ? "degraded" : "up");
    const cl8 = limited(cs, "ctrs", 8);
    put("ctr-body", html`<table class="tbl stk t-ctr"><thead><tr><th>NAME</th><th>HEALTH</th><th class="r">UP</th><th class="r">재시작</th><th class="r">CPU</th><th class="r">MEM</th><th class="r">PIDS</th><th>PROJECT</th><th>IMAGE</th><th>PORTS</th></tr></thead><tbody>
      ${cl8.items.map((c) => {
        const pt = ctrPorts(c);
        return html`<tr class="${cst(c) === "down" ? "bad" : c.restart_count ? "hl" : ""}"><td class="p fx">${st(cst(c))} ${cl(c.name, 30, c.name, "b")}${c.state !== "running" ? html` <span class="t-crit">${c.state}${c.exit_code ? " (" + c.exit_code + ")" : ""}${c.oom_killed ? " OOM" : ""}</span>` : ""}</td><td class="${c.health ? "s" : "x"} ${c.health === "healthy" ? "t-ok" : c.health === "unhealthy" ? "t-crit" : c.health ? "t-warn" : "dim"}">${c.health || DASH}</td><td class="r k">${fmt.durShort(c.uptime_s)}</td><td class="r s ${c.restart_count ? "t-warn" : "dim"}" data-l="재시작">${c.restart_count || 0}</td><td class="r s" data-l="CPU">${pctf(c.cpu_pct, 1)}</td><td class="r s" data-l="MEM">${fmt.bytes(c.mem_bytes, 0)}</td><td class="r dim x" data-l="PIDS">${c.pids != null ? c.pids : DASH}</td><td class="dim s">${cl(html`${c.project || DASH}${c.service ? "/" + c.service : ""}`, 24, (c.project || "") + (c.service ? "/" + c.service : ""))}</td><td class="s">${cl(c.image, 26, c.image, "dim")}</td><td class="dim s">${cl(pt.text, 28, pt.title)}${pt.inner.length ? html` <span class="dim" title="${pt.title}">+${pt.inner.length} 내부</span>` : ""}</td></tr>`;
      })}
    </tbody></table>`);
    put("ctr-more", moreBtn("ctrs", cl8, "컨테이너 더 보기"));
    const shown = S.unitsAll ? units : units.filter((u) => u.active === "failed" || ["app", "server", "infra"].includes(u.category));
    put("unit-meta", html`${S.unitsAll ? "전체" : "실패 + 앱·서버·인프라"} ${shown.length}/${units.length}`);
    const tgl = byId("units-toggle");
    tgl.setAttribute("aria-pressed", String(S.unitsAll));
    tgl.textContent = S.unitsAll ? "주요 유닛만 보기" : "전체 유닛 보기 (" + units.length + ")";
    const ust = (u) => (u.active === "failed" ? "down" : u.active === "active" ? "up" : u.active === "activating" || u.active === "reloading" ? "degraded" : "unknown");
    put("unit-body", html`<table class="tbl stk t-units"><thead><tr><th>UNIT</th><th>상태</th><th class="r">가동</th><th class="r">재시작</th><th class="r">CPU</th><th class="r">MEM</th><th>범위</th><th>분류</th></tr></thead><tbody>
      ${shown.map((u) => html`<tr class="${u.active === "failed" ? "bad" : ""}"><td class="p fx" title="${u.description || ""}">${st(ust(u))} ${cl(u.name, 36, u.name, u.active === "failed" ? "t-crit b" : "b")}</td><td class="k ${u.active === "failed" ? "t-crit" : "dim"}">${u.active}/${u.sub}</td><td class="r s" data-l="가동">${fmt.durShort(u.uptime_s)}</td><td class="r s ${u.restarts ? "t-warn" : "dim"}" data-l="재시작">${u.restarts || 0}</td><td class="r s" data-l="CPU">${u.cpu_pct != null ? pctf(u.cpu_pct, 1) : DASH}</td><td class="r s" data-l="MEM">${u.mem_bytes != null ? fmt.bytes(u.mem_bytes, 0) : DASH}</td><td class="dim s">${u.scope}</td><td class="dim s">${u.category || ""}</td></tr>`)}
    </tbody></table>`);
  }

  // ───────────────────────────────────────────── 패널 포커스
  function setActive(n) {
    if (S.active === n) return;
    S.active = n;
    for (const p of $$(".panel")) p.classList.toggle("active", p.dataset.key === String(n));
  }
  function focusPanel(n) {
    const el = $('.panel[data-key="' + n + '"]');
    if (!el) return;
    setActive(n);
    setSpy(n);
    if (n === 2 && S.sel && rowEls.get(S.sel) && !rowEls.get(S.sel).li.hidden) rowEls.get(S.sel).tog.focus({ preventScroll: true });
    else el.focus({ preventScroll: true });
    el.scrollIntoView({ block: "start", behavior: smooth() });
  }

  // ───────────────────────────────────────────── 이벤트 (위임 — 한 번만 등록)
  const help = byId("help");
  function openHelp() { if (!help.open) { help.showModal(); setMode(); } }
  function closeHelp() { if (help.open) help.close(); }
  help.addEventListener("close", setMode);

  document.addEventListener("click", (e) => {
    const a = e.target.closest("[data-act]");
    if (!a) {
      const ln = e.target.closest(".svc-line");
      if (ln && !e.target.closest("a")) {
        const id = ln.parentElement.dataset.id;
        setActive(2);
        select(id, { focus: false, scroll: false });
        toggleOpen(id);
      }
      return;
    }
    switch (a.dataset.act) {
      case "svc": { const id = a.closest(".svc").dataset.id; setActive(2); select(id, { focus: false, scroll: false }); toggleOpen(id); break; }
      case "cat": setCat(a.dataset.cat); break;
      case "range": setRange(a.dataset.range); break;
      case "proc": S.proc = a.dataset.proc; renderProc(); break;
      case "theme": cycleTheme(); break;
      case "help": openHelp(); break;
      case "help-close": closeHelp(); break;
      case "panel": focusPanel(+a.dataset.key); break;
      case "goto": gotoSvc(a.dataset.id); break;
      case "clear-filter": setFilter(""); filterInput.focus(); break;
      case "units-all": S.unitsAll = !S.unitsAll; if (S.snap) renderDock(S.snap); break;
      case "tun-errs": S.tunErrs = !S.tunErrs; if (S.snap) renderIngress(S.snap); break;
      case "cerr-all": S.cerrAll = !S.cerrAll; if (S.snap) renderIngress(S.snap); break;
      case "more": {
        const k = a.dataset.k;
        S.more[k] = !S.more[k];
        if (k === "alerts") { S.alMinH = 0; S.alShrinkAt = 0; }
        if (S.snap) renderMore(k, S.snap);
        break;
      }
      default: break;
    }
  });
  document.addEventListener("pointerdown", (e) => {
    const p = e.target.closest && e.target.closest(".panel");
    if (p) setActive(+p.dataset.key);
  }, { passive: true });
  document.addEventListener("focusin", (e) => {
    const p = e.target.closest && e.target.closest(".panel");
    if (p) setActive(+p.dataset.key);
    setMode();
  });
  document.addEventListener("focusout", () => setTimeout(setMode, 0));

  filterInput.addEventListener("input", () => { S.filter = filterInput.value; applyFilter(); });
  filterInput.addEventListener("focus", setMode);
  filterInput.addEventListener("blur", () => setTimeout(setMode, 0));
  filterInput.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      e.preventDefault(); e.stopPropagation();
      if (filterInput.value) setFilter("");
      else { filterInput.blur(); byId("p-svc").focus({ preventScroll: true }); }
    } else if ((e.key === "Enter" || e.key === "ArrowDown") && !e.isComposing) {
      e.preventDefault();
      const rows = visibleRows();
      if (rows.length) select(rows[0].dataset.id);
    }
  });

  const CODE_KEY = { KeyJ: "j", KeyK: "k", KeyC: "c", KeyT: "t", KeyR: "r" };
  document.addEventListener("keydown", (e) => {
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target;
    const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
    if (typing) return;
    if (help.open) { if (e.key === "?") { e.preventDefault(); closeHelp(); } return; }
    // 한글 자판(ㅓ·ㅏ·ㅊ·ㅅ·ㄱ)에서도 같은 키가 되도록 물리 키로 보정
    let k = e.key;
    if (k && k.length === 1 && !/[ -~]/.test(k) && CODE_KEY[e.code]) k = CODE_KEY[e.code];
    if (k && k.length === 1) k = k.toLowerCase();
    const inSvc = t && t.closest && t.closest("#svc-list");
    switch (k) {
      case "0": case "1": case "2": case "3": case "4": case "5": case "6": case "7": case "8":
        if (+k > MAXKEY) break;
        e.preventDefault(); focusPanel(+k); break;
      case "j": e.preventDefault(); move(1); break;
      case "k": e.preventDefault(); move(-1); break;
      case "ArrowDown": case "ArrowUp":
        if (inSvc || (S.active === 2 && (t === document.body || t === byId("p-svc")))) { e.preventDefault(); move(k === "ArrowDown" ? 1 : -1); }
        break;
      case "Enter":
        if (S.sel && (t === document.body || (t.classList && t.classList.contains("panel")))) { e.preventDefault(); toggleOpen(S.sel); }
        break;
      case "/": e.preventDefault(); setActive(2); filterInput.focus(); filterInput.select(); setMode(); byId("p-svc").scrollIntoView({ block: "start", behavior: smooth() }); break;
      case "c": e.preventDefault(); cycleCat(); break;
      case "t": e.preventDefault(); cycleTheme(); break;
      case "r": e.preventDefault(); setRange(S.range === "1h" ? "24h" : "1h"); break;
      case "?": e.preventDefault(); openHelp(); break;
      case "Escape":
        if (S.sel && S.open.has(S.sel)) { toggleOpen(S.sel, false); const r = rowEls.get(S.sel); if (r) r.tog.focus({ preventScroll: true }); }
        else if (S.filter || S.cat !== "all") { setFilter(""); setCat("all"); }
        break;
      default: break;
    }
  });

  // ───────────────────────────────────────────── 구독
  function safe(fn, s) {
    try { fn(s); } catch (err) { console.error("[terminal] 렌더 실패:", fn.name, err); }
  }
  /** "더 보기" 토글 뒤 해당 목록만 다시 그린다. */
  function renderMore(k, snap) {
    const fn = { alerts: renderAlerts, recent: renderIngress, hosts: renderIngress, peers: renderTs, ports: renderPorts, ctrs: renderDock, procs: renderProc }[k];
    if (fn) safe(fn, snap);
  }
  function renderAll(snap) {
    safe(renderHeader, snap);
    safe(renderStatusAlerts, snap);
    safe(renderAlerts, snap);
    safe(renderNow, snap);
    safe(renderSvc, snap);
    safe(renderMap, snap);
    safe(renderHw, snap);
    safe(renderIngress, snap);
    safe(renderTs, snap);
    safe(renderPorts, snap);
    safe(renderDock, snap);
  }
  function onSnap(snap, st) {
    const first = !S.snap;
    S.snap = snap;
    S.st = st;
    S.down = false;
    document.body.classList.remove("is-down");
    const nowC = Date.now() / 1000;
    if (!st.stale) S.lastOk = st.fetchedAt || nowC;
    const gen = snap && snap.meta && snap.meta.generated_at;
    if (gen !== S.genVal) { S.genVal = gen; S.genAt = nowC; }
    renderAll(snap);
    schedGraphs();
    maybeFetchHist(first);
    tick();
    renderBanner();
    setMode();
    if (first) requestAnimationFrame(fitStrips);
  }
  // 휴대폰 ↔ 데스크톱 폭이 바뀌면 "더 보기" 접힘 규칙이 달라지므로 다시 그린다
  const onMq = () => { S.alMinH = 0; if (S.snap) renderAll(S.snap); fitStrips(); };
  if (MQ_M.addEventListener) MQ_M.addEventListener("change", onMq); else if (MQ_M.addListener) MQ_M.addListener(onMq);
  function onBootError() {
    S.down = true;
    document.body.classList.add("is-down");
    renderBanner();
    setMode();
    tick();
  }

  setActive(-1);
  setActive(2);
  setRange(S.range);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { S.visAt = Date.now() / 1000; tick(); } });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(schedGraphs).catch(() => {});
  setInterval(tick, EMBED ? 10000 : 1000);
  tick();
  P.subscribe(onSnap, { onError: onBootError });
})();
