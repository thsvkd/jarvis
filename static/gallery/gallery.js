/* Jarvis — 시안 갤러리 스크립트
 *
 * 하는 일
 *  1) 미리보기: 각 시안을 1440×900 으로 띄운 iframe 을 카드 폭에 맞춰 축소(ResizeObserver).
 *     로드 결과를 같은 출처 문서로 확인해 실패하면 자리표시자를 보여 주고, 404 는 주기적으로 다시 확인한다.
 *  2) 헤더: 전체 상태·갱신 시각·끊김 표시·서비스/경보 개수·CPU·GPU·메모리·온도·전력·네트워크.
 *  3) 접속 주소 칩: 누르면 복사(보안 컨텍스트가 아니면 execCommand 대체).
 *  4) 푸터: 모니터링 범위와 갱신 주기를 실제 값으로.
 *
 * 안전: 데이터는 전부 JARVIS.html(자동 이스케이프) 또는 textContent 로만 넣는다. 가져오는 곳은 /api/* 뿐이다.
 */
(function () {
  "use strict";

  const P = window.JARVIS;
  if (!P) return;
  const { html, raw, fmt, labels, render } = P;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.from((r || document).querySelectorAll(s));
  const motionQuery = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const motionOk = () => !motionQuery.matches && !P.embed;

  const DESIGN_W = 1440;
  const PAGE_INTERVAL_S = 3;
  const EMBED_INTERVAL_S = 10;

  const store = {
    get(k) { try { return window.localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* 저장소 없음: 무시 */ } },
  };

  // ------------------------------------------------------------------ 아이콘 (고정 문자열)
  const ICON = {
    ok: raw('<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="8.2" fill="currentColor" opacity=".16"/><path d="M6.3 10.4l2.4 2.4 5-5.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>'),
    warn: raw('<svg viewBox="0 0 20 20"><path d="M10 2.8 18 16.6H2z" fill="currentColor" opacity=".16" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 8v3.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><circle cx="10" cy="14.2" r="1.1" fill="currentColor"/></svg>'),
    crit: raw('<svg viewBox="0 0 20 20"><path d="M7 2.5h6L17.5 7v6L13 17.5H7L2.5 13V7z" fill="currentColor" opacity=".16" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M7.6 7.6l4.8 4.8M12.4 7.6l-4.8 4.8" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'),
    stale: raw('<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="3 2.4"/><path d="M8 7.2v5.6M12 7.2v5.6" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>'),
    muted: raw('<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="7.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="3 2.4"/></svg>'),
  };
  const GLYPH = {
    ok: raw('<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.2" fill="currentColor"/></svg>'),
    warn: raw('<svg viewBox="0 0 12 12"><path d="M6 1.4 11 10.4H1z" fill="currentColor"/></svg>'),
    crit: raw('<svg viewBox="0 0 12 12"><rect x="1.8" y="1.8" width="8.4" height="8.4" rx="1.2" fill="currentColor"/></svg>'),
    info: raw('<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4.2" fill="none" stroke="currentColor" stroke-width="1.6"/></svg>'),
    muted: raw('<svg viewBox="0 0 12 12"><circle cx="6" cy="6" r="4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="2 1.6"/></svg>'),
  };
  const COPY = raw('<svg class="ci-copy" viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8"/><path d="M10.5 3.2V3a.9.9 0 0 0-.9-.9H3.4a.9.9 0 0 0-.9.9v6.2c0 .5.4.9.9.9h.3"/></svg>');
  const CHECK = raw('<svg class="ci-done" viewBox="0 0 16 16"><path d="M3.5 8.4l2.9 2.9 6.1-6.4"/></svg>');
  const LOCK = raw('<svg class="lock" viewBox="0 0 12 12" aria-hidden="true"><rect x="2.4" y="5.2" width="7.2" height="5.2" rx="1.2"/><path d="M4 5.2V3.9a2 2 0 0 1 4 0v1.3"/></svg>');
  const CLOCK = raw('<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6"/><path d="M8 4.8V8l2.2 1.4"/></svg>');
  const LAYERS = raw('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2.2 14 5.4 8 8.6 2 5.4z"/><path d="M2 8.4l6 3.2 6-3.2"/><path d="M2 11.2l6 3.2 6-3.2" opacity=".6"/></svg>');
  const PULSE = raw('<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.5 8.5h3l1.8-4.5 3 9 2-6 1 1.5h2.2"/></svg>');

  // ------------------------------------------------------------------ 배치(그리드 / 나란히)
  const grid = $("#grid");
  const LAYOUT_KEY = "jarvis.gallery.layout";
  function setLayout(mode, save) {
    mode = mode === "two" ? "two" : "grid";
    if (grid && grid.dataset.layout !== mode) grid.dataset.layout = mode;
    $$("button[data-layout]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.layout === mode)));
    if (save) store.set(LAYOUT_KEY, mode);
  }
  setLayout(P.pref("gallery.layout"), false);

  // ------------------------------------------------------------------ 미리보기 축소
  function setScale(view, w) {
    if (!(w > 0)) return;
    const s = (w / DESIGN_W).toFixed(5);
    if (view.__s !== s) { view.__s = s; view.style.setProperty("--s", s); }
  }
  const views = $$(".view");
  views.forEach((v) => setScale(v, v.clientWidth));
  if ("ResizeObserver" in window) {
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        const box = e.contentBoxSize && (e.contentBoxSize[0] || e.contentBoxSize);
        setScale(e.target, box && box.inlineSize ? box.inlineSize : e.contentRect.width);
      }
    });
    views.forEach((v) => ro.observe(v));
  } else {
    let raf = 0;
    window.addEventListener("resize", () => {
      if (raf) return;
      raf = requestAnimationFrame(() => { raf = 0; views.forEach((v) => setScale(v, v.clientWidth)); });
    });
  }

  // ------------------------------------------------------------------ 미리보기 수명주기
  const LOAD_TIMEOUT_MS = 30000;
  const RETRY_MS = 45000;
  const TAG = { loading: "불러오는 중", ready: "실시간", missing: "준비 중", timeout: "응답 없음", error: "불러오기 실패" };
  const PH = {
    loading: ["미리보기를 불러오는 중", ""],
    missing: ["아직 준비 중인 시안이에요", "페이지를 찾지 못했어요. 잠시 뒤 자동으로 다시 확인합니다."],
    timeout: ["미리보기 응답이 늦어요", "기기가 바쁘거나 네트워크가 느릴 수 있어요. 열기로 바로 확인할 수 있습니다."],
    error: ["미리보기를 불러오지 못했어요", "시안 대신 다른 응답이 돌아왔어요. 잠시 뒤 다시 확인합니다."],
  };

  /** 같은 출처 iframe 의 문서를 보고 결과를 판정한다. */
  function inspect(ifr) {
    let doc = null;
    try { doc = ifr.contentDocument; } catch (e) { return "ready"; }
    if (!doc) return "ready";
    let href = "";
    try { href = String(doc.location && doc.location.href); } catch (e) { /* 무시 */ }
    if (!href || href === "about:blank") return "blank";
    const ct = String(doc.contentType || "text/html").toLowerCase();
    if (ct.indexOf("html") < 0) return "missing";
    if (!/Jarvis/i.test(doc.title || "")) return "error";
    return "ready";
  }

  const reloaders = new WeakMap();
  const io = "IntersectionObserver" in window
    ? new IntersectionObserver((entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue;
          io.unobserve(e.target);
          const p = reloaders.get(e.target);
          if (p) p.seen();
        }
      }, { rootMargin: "200px 0px" })
    : null;

  function initPreview(card) {
    const ifr = $("iframe", card);
    if (!ifr) return;
    const base = ifr.getAttribute("src");
    const tag = $(".ptag", card);
    const phT = $(".ph-title", card);
    const phS = $(".ph-sub", card);
    let timer = 0;
    let visible = !io;

    const set = (state) => {
      if (card.dataset.state !== state) card.dataset.state = state;
      tag.textContent = TAG[state] || "";
      const t = PH[state] || ["", ""];
      if (phT.textContent !== t[0]) phT.textContent = t[0];
      if (phS.textContent !== t[1]) phS.textContent = t[1];
    };
    const clear = () => { if (timer) { clearTimeout(timer); timer = 0; } };
    const armTimeout = () => {
      clear();
      timer = setTimeout(() => { timer = 0; if (card.dataset.state === "loading") set("timeout"); }, LOAD_TIMEOUT_MS);
    };
    const armRetry = () => {
      clear();
      timer = setTimeout(function again() {
        timer = 0;
        if (document.hidden) { timer = setTimeout(again, RETRY_MS); return; }
        reload();
      }, RETRY_MS);
    };
    const reload = () => {
      set("loading");
      ifr.src = base + (base.indexOf("?") >= 0 ? "&" : "?") + "r=" + Date.now().toString(36);
      if (visible) armTimeout();
    };

    ifr.addEventListener("load", () => {
      const v = inspect(ifr);
      if (v === "blank") return;
      clear();
      set(v);
      if (v === "missing" || v === "error") armRetry();
    });

    reloaders.set(card, {
      reload,
      seen() {
        visible = true;
        if (card.dataset.state === "loading" && !timer) armTimeout();
      },
    });
    set(card.dataset.state === "ready" ? "ready" : "loading");
    // 스크립트보다 먼저 로드가 끝났을 수 있다.
    const early = inspect(ifr);
    let doneEarly = false;
    try { doneEarly = !!(ifr.contentDocument && ifr.contentDocument.readyState === "complete"); } catch (e) { doneEarly = false; }
    if (early !== "blank" && doneEarly) {
      set(early);
      if (early === "missing" || early === "error") armRetry();
    }
    if (io) io.observe(card); else armTimeout();
  }
  $$(".card[data-slug]").forEach(initPreview);

  // ------------------------------------------------------------------ 클릭 위임
  const srLive = $("#sr-live");
  const copyTimers = new WeakMap();

  async function copyText(text) {
    try {
      if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(text); return true; }
    } catch (e) { /* 대체 경로로 */ }
    const active = document.activeElement;
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none";
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    ta.remove();
    if (active && active.focus) active.focus({ preventScroll: true });
    return ok;
  }

  async function onCopy(btn) {
    const text = btn.dataset.copy || "";
    if (!text) return;
    const ok = await copyText(text);
    clearTimeout(copyTimers.get(btn));
    btn.removeAttribute("data-copied");
    btn.removeAttribute("data-failed");
    btn.setAttribute(ok ? "data-copied" : "data-failed", "");
    if (srLive) srLive.textContent = ok ? "주소를 복사했습니다: " + text : "복사하지 못했습니다. 주소를 직접 선택해 주세요: " + text;
    if (!ok) {
      const u = $(".u", btn);
      if (u && window.getSelection) {
        const r = document.createRange();
        r.selectNodeContents(u);
        const sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(r);
      }
    }
    copyTimers.set(btn, setTimeout(() => { btn.removeAttribute("data-copied"); btn.removeAttribute("data-failed"); }, 1800));
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    if (!(t instanceof Element)) return;
    const copyBtn = t.closest("button[data-copy]");
    if (copyBtn) { onCopy(copyBtn); return; }
    const retry = t.closest('[data-act="retry"]');
    if (retry) {
      const card = retry.closest(".card");
      const p = card && reloaders.get(card);
      if (p) p.reload();
      return;
    }
    const lay = t.closest("button[data-layout]");
    if (lay) setLayout(lay.dataset.layout, true);
  });

  // ------------------------------------------------------------------ 작은 그림
  function rangeOf(values, minSpan, floor) {
    const f = (values || []).filter((v) => v != null && !isNaN(v));
    if (!f.length) return {};
    let lo = Math.min(...f);
    let hi = Math.max(...f);
    if (hi - lo < minSpan) { const mid = (hi + lo) / 2; lo = mid - minSpan / 2; hi = mid + minSpan / 2; }
    if (floor != null && lo < floor) { hi += floor - lo; lo = floor; }
    return { min: lo, max: hi };
  }
  function spark(values, opts) {
    const W = 100;
    const H = 24;
    const vs = (values || []).slice(-60);
    const line = P.sparkPath(vs, W, H, opts || {});
    if (!line) {
      return html`<svg viewBox="0 0 ${W} 26" preserveAspectRatio="none" aria-hidden="true"><line class="spark-base" x1="0" y1="24" x2="${W}" y2="24"/></svg>`;
    }
    const area = P.sparkPath(vs, W, H, Object.assign({}, opts || {}, { area: true }));
    return html`<svg viewBox="0 -1 ${W} 26" preserveAspectRatio="none" aria-hidden="true"><path class="spark-area" d="${area}"/><path class="spark-line" d="${line}"/></svg>`;
  }
  /** 비율 막대: parts = [{v, c}] (c: ok|warn|crit|info|none) */
  function hbar(parts) {
    const segs = parts.filter((p) => p.v > 0);
    if (!segs.length) return html`<span class="hbar" aria-hidden="true"><i class="s-none" style="flex-grow:1"></i></span>`;
    return html`<span class="hbar" aria-hidden="true">${segs.map((p) => html`<i class="s-${p.c}" style="flex-grow:${Number(p.v) || 0}"></i>`)}</span>`;
  }
  function unitSplit(s) {
    const m = /^(-?[\d.,]+)\s*(.*)$/.exec(String(s));
    return m ? [m[1], m[2]] : [String(s), ""];
  }
  const valUnit = (text) => {
    const [n, u] = unitSplit(text);
    return u ? html`${n}<small>${u}</small>` : html`${n}`;
  };
  /** 값 안에서는 줄이 안 바뀌게(nbsp), 줄바꿈은 " · " 사이에서만 */
  const nb = (v) => String(v).replace(/ /g, "\u00a0");
  const parts = (...xs) => xs.filter((x) => x != null && x !== "").map(nb).join(" · ");
  const toneTag = (t) => (t === "crit" ? "매우 높음" : t === "warn" ? "높음" : "");

  // ------------------------------------------------------------------ 헤더 요소
  const el = {
    hostline: $("#hostline"),
    pill: $("#pill"),
    updated: $("#updated"),
    updatedText: $("#updated-text"),
    beat: $("#beat"),
    why: $("#why"),
    access: $("#access"),
    scope: $("#scope"),
    cadence: $("#cadence"),
    colophon: $("#colophon"),
  };
  const cells = {};
  $$(".v[data-k]").forEach((c) => {
    cells[c.dataset.k] = { root: c, val: $('[data-f="val"]', c), sub: $('[data-f="sub"]', c), gfx: $('[data-f="gfx"]', c), tag: $('[data-f="tag"]', c) };
  });
  function setCell(k, o) {
    const c = cells[k];
    if (!c) return;
    const tone = o.tone || "muted";
    if (c.root.dataset.tone !== tone) c.root.dataset.tone = tone;
    render(c.val, o.val == null ? "–" : o.val);
    render(c.sub, o.sub == null || o.sub === "" ? raw("&nbsp;") : o.sub);
    render(c.gfx, o.gfx || "");
    render(c.tag, o.tag || "");
    if (o.title != null && c.root.title !== o.title) c.root.title = o.title;
  }

  function setPill(tone, text) {
    if (el.pill.dataset.tone !== tone) el.pill.dataset.tone = tone;
    render(el.pill, html`<span class="pill-ico" aria-hidden="true">${ICON[tone] || ICON.muted}</span><span class="sr-only">전체 상태: </span><span class="pill-text">${text}</span>`);
  }

  function beat() {
    if (!motionOk() || !el.beat.animate) return;
    try {
      el.beat.animate(
        [{ transform: "scale(1.9)", opacity: 0.25 }, { transform: "scale(1)", opacity: 1 }],
        { duration: 700, easing: "cubic-bezier(.2,.7,.3,1)" },
      );
    } catch (e) { /* 무시 */ }
  }

  // ------------------------------------------------------------------ 스냅샷 → 화면
  function renderHost(snap) {
    const sys = snap.system || {};
    const host = sys.host || {};
    const os = String(host.os || "").replace("GNU/Linux ", "");
    const parts = [
      html`<span class="hl-host">${host.hostname || (snap.meta || {}).host || "host"}</span>`,
      host.model ? html`<span>${host.model}</span>` : "",
      os ? html`<span>${os}</span>` : "",
      host.arch ? html`<span>${host.arch}</span>` : "",
      html`<span class="tnum">가동 ${fmt.dur(sys.uptime_s)}</span>`,
    ];
    render(el.hostline, parts);
  }

  function renderStatus(snap, state) {
    const sum = snap.summary || {};
    const meta = snap.meta || {};
    const gen = meta.generated_at;
    if (state.stale) {
      setPill("stale", "연결 끊김");
      el.updated.setAttribute("data-stale", "");
      el.updated.removeAttribute("data-idle");
      el.updatedText.textContent = "마지막 데이터 " + fmt.time(gen) + " · " + fmt.ago(gen) + " — 다시 연결 중";
      document.body.setAttribute("data-stale", "");
    } else {
      const st = sum.status;
      if (st && labels.overall[st]) setPill(P.tone(st), labels.overall[st]);
      else setPill("muted", "상태 확인 불가");
      el.updated.removeAttribute("data-stale");
      el.updated.removeAttribute("data-idle");
      el.updatedText.textContent = fmt.time(gen) + " 갱신 · " + PAGE_INTERVAL_S + "초마다";
      document.body.removeAttribute("data-stale");
      beat();
    }
    const alerts = snap.alerts || [];
    const serious = alerts.filter((a) => a.level === "crit" || a.level === "warn");
    const info = alerts.length - serious.length;
    let whyHtml;
    let whyTone = "muted";
    let whyTitle = "";
    if (serious.length) {
      const top = serious[0];
      whyTone = P.tone(top.level);
      whyTitle = serious.map((a) => (labels.level[a.level] || a.level) + " · " + a.message).join("\n");
      whyHtml = html`<b>${labels.level[top.level] || top.level}</b> ${top.message}${serious.length > 1 ? " 외 " + (serious.length - 1) + "건" : ""}`;
    } else {
      whyHtml = info ? html`경보 없음 · 참고 정보 ${info}건` : html`경보 없음`;
    }
    if (el.why.dataset.tone !== whyTone) el.why.dataset.tone = whyTone;
    if (el.why.title !== whyTitle) el.why.title = whyTitle;
    render(el.why, whyHtml);
  }

  function renderVitals(snap) {
    const sum = snap.summary || {};
    const sys = snap.system || {};
    const cpu = sys.cpu || {};
    const gpu = sys.gpu || {};
    const mem = sys.memory || {};
    const th = sys.thermal || {};
    const pw = sys.power || {};
    const net = sys.network || {};
    const sp = snap.spark || {};
    const clocks = sys.clocks_mhz || {};

    // 서비스
    const sv = sum.services || {};
    const unk = Math.max(0, (sv.total || 0) - (sv.up || 0) - (sv.degraded || 0) - (sv.down || 0));
    const svTone = sv.down ? "crit" : sv.degraded ? "warn" : sv.total ? "ok" : "muted";
    const nCat = (snap.categories || []).length;
    setCell("svc", {
      tone: svTone,
      val: sv.total == null ? null : html`${fmt.num(sv.up)}<small>/ ${fmt.num(sv.total)}</small>`,
      gfx: hbar([{ v: sv.up, c: "ok" }, { v: sv.degraded, c: "warn" }, { v: sv.down, c: "crit" }, { v: unk, c: "none" }]),
      sub: sv.down || sv.degraded
        ? parts("다운 " + fmt.num(sv.down || 0), "저하 " + fmt.num(sv.degraded || 0), unk ? "확인 불가 " + unk : "")
        : parts("모두 정상", nCat + "개 분류", unk ? "확인 불가 " + unk : ""),
      tag: sv.down ? "다운" : sv.degraded ? "저하" : "",
    });

    // 경보
    const al = sum.alerts || {};
    const serious = (al.crit || 0) + (al.warn || 0);
    const alTone = !sum.alerts ? "muted" : al.crit ? "crit" : al.warn ? "warn" : "ok";
    setCell("alerts", {
      tone: alTone,
      val: sum.alerts ? html`${fmt.num(serious)}<small>건</small>` : null,
      gfx: hbar([{ v: al.crit, c: "crit" }, { v: al.warn, c: "warn" }, { v: al.info, c: "info" }]),
      sub: parts("심각 " + fmt.num(al.crit || 0), "경고 " + fmt.num(al.warn || 0), "정보 " + fmt.num(al.info || 0)),
      tag: al.crit ? "심각" : al.warn ? "경고" : "",
    });

    // CPU
    const cu = cpu.usage_pct;
    const cuTone = P.loadTone(cu, 75, 90);
    const load = cpu.load || [];
    setCell("cpu", {
      tone: cu == null ? "muted" : cuTone,
      val: cu == null ? null : html`${fmt.num(cu)}<small>%</small>`,
      gfx: spark(sp.cpu, { min: 0, max: 100 }),
      sub: parts(fmt.mhz(cpu.freq_mhz), "부하 " + fmt.num(load[0], 2), fmt.num(cpu.cores || (sys.host || {}).cores) + "코어"),
      tag: toneTag(cuTone),
      title: (cpu.per_core_pct || []).length ? "코어별 " + cpu.per_core_pct.map((v) => fmt.pct(v)).join(" · ") : "",
    });

    // GPU
    const gu = gpu.available === false ? null : gpu.usage_pct;
    const guTone = P.loadTone(gu, 75, 90);
    setCell("gpu", {
      tone: gu == null ? "muted" : guTone,
      val: gu == null ? null : html`${fmt.num(gu)}<small>%</small>`,
      gfx: spark(sp.gpu, { min: 0, max: 100 }),
      sub: gpu.available === false ? "V3D 정보 없음" : parts("V3D " + fmt.mhz(gpu.freq_mhz != null ? gpu.freq_mhz : clocks.v3d), gpu.jobs_per_s ? fmt.num(gpu.jobs_per_s, 1) + " 작업/s" : ""),
      tag: toneTag(guTone),
    });

    // 메모리
    const mu = mem.usage_pct;
    const muTone = P.loadTone(mu, 80, 92);
    setCell("mem", {
      tone: mu == null ? "muted" : muTone,
      val: mu == null ? null : html`${fmt.num(mu)}<small>%</small>`,
      gfx: spark(sp.mem, { min: 0, max: 100 }),
      sub: parts(fmt.bytes(mem.used) + " / " + fmt.bytes(mem.total), "스왑 " + fmt.pct(mem.swap_pct)),
      tag: toneTag(muTone),
    });

    // 온도
    const tc = th.cpu_temp_c != null ? th.cpu_temp_c : cpu.temp_c;
    const tcTone = P.loadTone(tc, 70, 80);
    const fan = th.fan || {};
    setCell("temp", {
      tone: tc == null ? "muted" : tcTone,
      val: tc == null ? null : html`${fmt.num(tc, 1)}<small>°C</small>`,
      gfx: spark(sp.temp, rangeOf(sp.temp, 8)),
      sub: fan.rpm != null ? parts("팬 " + fmt.num(fan.rpm) + " rpm", fan.pct != null ? fmt.pct(fan.pct) : "") : parts("PMIC " + fmt.temp(th.pmic_temp_c)),
      tag: tcTone === "crit" ? "과열" : tcTone === "warn" ? "뜨거움" : "",
    });

    // 전력 + 스로틀
    const thr = th.throttle || {};
    const now = thr.now || [];
    const past = thr.since_boot || [];
    const thrText = now.length ? "스로틀 " + now.join("·") : past.length ? "부팅 후 " + past.join("·") : thr.ok === false ? "스로틀 감지" : "스로틀 없음";
    const pwTone = now.length || thr.ok === false ? "warn" : pw.total_w == null ? "muted" : "ok";
    setCell("power", {
      tone: pwTone,
      val: pw.total_w == null ? null : valUnit(fmt.watts(pw.total_w)),
      gfx: spark(sp.pw, rangeOf(sp.pw, 1.5, 0)),
      sub: parts("입력 " + (pw.input_v == null ? "–" : fmt.num(pw.input_v, 2) + " V"), thrText),
      tag: now.length ? "스로틀" : "",
    });

    // 네트워크
    const wifi = (net.interfaces || []).find((i) => i.kind === "wifi" && i.up && i.wifi);
    const [rxN, rxU] = unitSplit(fmt.bps(net.rx_bps, 0));
    setCell("net", {
      tone: net.rx_bps == null ? "muted" : "ok",
      val: net.rx_bps == null ? null : html`<span class="arrow" aria-hidden="true">↓</span><span class="sr-only">수신 </span>${rxN}<small>${rxU}</small>`,
      gfx: spark(sp.rx, rangeOf(sp.rx, 1024, 0)),
      sub: nb("↑ " + fmt.bps(net.tx_bps)) + " · Tailnet " + nb("↓" + fmt.bps(net.tailnet_rx_bps)) + " " + nb("↑" + fmt.bps(net.tailnet_tx_bps)),
      tag: "",
      title: wifi && wifi.wifi && wifi.wifi.signal_dbm != null ? `Wi-Fi ${wifi.name} 신호 ${wifi.wifi.signal_dbm} dBm` : "",
    });
  }

  function renderAccess(snap) {
    const access = ((snap.meta || {}).access || []).filter((a) => a && P.safeUrl(a.url) !== "#");
    if (!access.length) { render(el.access, html`<li class="access-empty">등록된 접속 주소가 없습니다</li>`); return; }
    const here = (u) => { try { return new URL(u).origin === location.origin; } catch (e) { return false; } };
    const items = access.map((a) => {
      const url = String(a.url);
      const aud = ["public", "tailnet", "lan", "local"].indexOf(a.audience) >= 0 ? a.audience : "other";
      const audLabel = labels.audience[aud] || a.audience || "기타";
      const disp = url.replace(/^https?:\/\//i, "").replace(/\/$/, "");
      const secure = /^https:/i.test(url);
      const cur = here(url);
      return html`<li><button type="button" class="chip-copy" data-copy="${url}" title="눌러서 복사 — ${url}" aria-label="${audLabel} 접속 주소 ${url} 복사${cur ? " (지금 이 주소로 보는 중)" : ""}"><span class="aud aud-${aud}">${audLabel}</span>${secure ? LOCK : ""}<span class="u">${disp}</span>${cur ? html`<span class="here">지금 접속</span>` : ""}<span class="ci" aria-hidden="true">${COPY}${CHECK}</span></button></li>`;
    });
    render(el.access, items);
  }

  function row(tone, label, value, opts) {
    const o = opts || {};
    return html`<li class="${o.hl ? "hl" : ""}" data-tone="${tone}"><span class="glyph" aria-hidden="true">${GLYPH[tone] || GLYPH.muted}</span><span class="lbl">${label}${o.tag ? html`<span class="sep-tag">${o.tag}</span>` : ""}</span><span class="val">${value}</span></li>`;
  }
  const catTone = (c) => (c.down ? "crit" : c.degraded ? "warn" : c.unknown && !c.up ? "muted" : c.count ? "ok" : "muted");
  const toneWord = { ok: "정상", warn: "주의", crit: "문제", muted: "확인 불가", info: "정보" };

  function renderScope(snap) {
    const sys = snap.system || {};
    const host = sys.host || {};
    const cpu = sys.cpu || {};
    const mem = sys.memory || {};
    const gpu = sys.gpu || {};
    const disks = sys.disks || [];
    const ifs = ((sys.network || {}).interfaces || []).filter((i) => i.up && i.kind !== "docker");
    const sum = snap.summary || {};
    const cats = snap.categories || [];
    const ing = snap.ingress || {};
    const caddy = ing.caddy || {};
    const tun = ing.tunnel || {};
    const tr = ing.traffic || {};
    const ts = snap.tailscale || {};
    const L = sum.listeners || {};
    const C = sum.containers || {};
    const units = snap.units || [];
    const failed = units.filter((u) => u.active === "failed").length;
    const projects = snap.projects || [];
    const projPartial = projects.filter((p) => p.status !== "running").length;
    const h24 = tr.last_24h || {};
    const unknownHosts = (h24.hosts || []).filter((h) => h.known === false).length;
    const s5 = (h24.by_status || {})["5xx"] || 0;
    const sv = sum.services || {};

    const hw = html`<section class="sc">
      <h3>하드웨어 <span class="sc-count">${host.model || "–"}</span></h3>
      <ul class="sc-rows">
        ${row("ok", "CPU", html`<b>${fmt.num(cpu.cores || host.cores)}코어</b> · 최대 ${fmt.mhz(cpu.max_mhz || host.cpu_max_mhz)}`)}
        ${row(gpu.available === false ? "muted" : "ok", "GPU", html`<b>${gpu.name || host.gpu_name || "–"}</b>`)}
        ${row("ok", "메모리", html`<b>${fmt.bytes(mem.total)}</b> · 스왑 ${fmt.bytes(mem.swap_total)}`)}
        ${row("ok", "저장소 · 네트워크", html`디스크 <b>${disks.length}</b> · 인터페이스 <b>${ifs.length}</b>`)}
      </ul>
      <p class="sc-desc">사용률·코어별 부하·클럭·온도·팬·보드 전력·스로틀·디스크 I/O·네트워크 처리량과 Wi-Fi 신호</p>
    </section>`;

    const svc = html`<section class="sc">
      <h3>서비스 <span class="sc-count">${fmt.num(sv.total)}개 · ${cats.length}개 분류</span></h3>
      <ul class="sc-rows">
        ${cats.map((c) => row(catTone(c), c.label || labels.category[c.id] || c.id,
          html`<b>${fmt.num(c.up)}</b> / ${fmt.num(c.count)} ${toneWord[catTone(c)]}`))}
      </ul>
    </section>`;

    const tunTone = tun.ready ? "ok" : tun.ready === false ? "crit" : "muted";
    const tsTone = ts.backend_state === "Running" ? "ok" : ts.backend_state ? "warn" : "muted";
    const exp = html`<section class="sc">
      <h3>노출 경로</h3>
      <ul class="sc-rows">
        ${row(caddy.config_ok === false ? "crit" : caddy.version ? "ok" : "muted", "Caddy", html`${caddy.version || "–"} · 라우트 <b>${fmt.num((caddy.routes || []).length)}</b>`)}
        ${row(tunTone, "Cloudflare Tunnel", html`연결 <b>${fmt.num(tun.connections)}</b>${(tun.edge_locations || []).length ? " · " + tun.edge_locations.join("·") : ""}`)}
        ${row(tsTone, "Tailscale", html`피어 <b>${fmt.num(ts.peers_online)}</b> / ${fmt.num(ts.peers_total)} 온라인`)}
        ${row("info", "공개 · Tailnet 호스트", html`<b>${fmt.num((caddy.public_hosts || []).length)}</b> · <b>${fmt.num((caddy.private_hosts || []).length)}</b>`)}
      </ul>
      <p class="sc-desc">인터넷 → Cloudflare → cloudflared → Caddy :80, tailnet → Caddy :443, 직접 연 포트까지 네트워크 맵으로</p>
    </section>`;

    const ports = html`<section class="sc">
      <h3>열린 포트 <span class="sc-count">TCP ${fmt.num(L.total)}개</span></h3>
      <ul class="sc-rows">
        ${row(L.lan ? "warn" : "ok", "LAN 전체에 열림", html`<b>${fmt.num(L.lan)}</b>`, { hl: !!L.lan })}
        ${row("info", "Tailnet 전용", html`<b>${fmt.num(L.tailnet_only)}</b>`)}
        ${row("ok", "로컬(loopback)", html`<b>${fmt.num(L.loopback)}</b>`)}
      </ul>
      <p class="sc-desc">포트·프로토콜·바인딩 범위(LAN·Tailnet·Docker·로컬)·소유 프로세스</p>
    </section>`;

    const rt = html`<section class="sc">
      <h3>컨테이너 · 유닛</h3>
      <ul class="sc-rows">
        ${row(C.unhealthy ? "crit" : C.running < C.total ? "warn" : C.total ? "ok" : "muted", "Docker 컨테이너", html`<b>${fmt.num(C.running)}</b> / ${fmt.num(C.total)} 실행 · healthy ${fmt.num(C.healthy)}`)}
        ${row(projPartial ? "warn" : projects.length ? "ok" : "muted", "compose 프로젝트", html`<b>${fmt.num(projects.length)}</b>${projPartial ? " · 일부 중지 " + projPartial : ""}`)}
        ${row(failed ? "warn" : "ok", "systemd 유닛", html`<b>${fmt.num(units.length)}</b>${failed ? " · 실패 " + failed : ""}`, { hl: !!failed })}
      </ul>
      <p class="sc-desc">상태·health·가동 시간·재시작 횟수·CPU·메모리, 실패한 유닛은 맨 앞에</p>
    </section>`;

    const traffic = html`<section class="sc">
      <h3>트래픽 <span class="sc-count">Caddy 접근 로그</span></h3>
      <ul class="sc-rows">
        ${row("ok", "최근 1시간", html`<b>${fmt.num(sum.requests_1h)}</b>건`)}
        ${row("ok", "최근 24시간", html`<b>${fmt.num(sum.requests_24h)}</b>건`)}
        ${row(s5 ? "warn" : "ok", "5xx 응답 (24시간)", html`<b>${fmt.num(s5)}</b>건`)}
        ${row(unknownHosts ? "warn" : "ok", "미등록 호스트", html`<b>${fmt.num(unknownHosts)}</b>개`)}
      </ul>
      <p class="sc-desc">상태 코드·유입 경로(터널·Tailnet·LAN)·상위 호스트·최근 요청·Caddy 오류</p>
    </section>`;

    render(el.scope, [hw, svc, exp, ports, rt, traffic]);
  }

  function renderCadence(snap) {
    const meta = snap.meta || {};
    const iv = meta.intervals_s || {};
    const errs = Object.entries(meta.errors || {});
    render(el.cadence, html`
      <span class="cd-item">${CLOCK}<span>이 페이지 <b>${PAGE_INTERVAL_S}초</b>마다</span></span>
      <span class="cd-item">${LAYERS}<span>미리보기 <b>${EMBED_INTERVAL_S}초</b>마다</span></span>
      <span class="cd-item">${PULSE}<span>수집 주기 <b>${fmt.num(iv.fast)}</b> · <b>${fmt.num(iv.medium)}</b> · <b>${fmt.num(iv.slow)}</b> · DNS <b>${fmt.num(iv.dns)}</b>초</span></span>
      <span class="cd-lead">탭이 가려지면 더 느리게 받고, 다시 보이면 바로 새로 고칩니다.</span>
      ${errs.length ? html`<ul class="errors" aria-label="수집 오류">${errs.map(([k, v]) => html`<li>수집 오류 · ${k}: ${v}</li>`)}</ul>` : ""}
    `);
    const now = Date.now() / 1000;
    render(el.colophon, html`
      <span>jarvis ${meta.version ? "v" + meta.version : ""}</span>
      <span>Jarvis 가동 ${fmt.dur(meta.started_at ? now - meta.started_at : null)}</span>
      <span>읽기 전용</span>
      <span>LAN·Tailnet 에서만 열림</span>
      ${meta.domain ? html`<span>${meta.domain}</span>` : ""}
    `);
  }

  function onSnapshot(snap, state) {
    try { renderStatus(snap, state); } catch (e) { report(e); }
    try { renderHost(snap); } catch (e) { report(e); }
    try { renderVitals(snap); } catch (e) { report(e); }
    try { renderAccess(snap); } catch (e) { report(e); }
    try { renderScope(snap); } catch (e) { report(e); }
    try { renderCadence(snap); } catch (e) { report(e); }
  }
  // 한 구역이 깨져도 나머지는 계속 그린다. 같은 오류는 한 번만 알린다.
  const reported = new Set();
  function report(e) {
    const k = String(e && e.message);
    if (reported.has(k)) return;
    reported.add(k);
    setTimeout(() => { throw e; });
  }

  P.subscribe(onSnapshot, {
    interval: PAGE_INTERVAL_S * 1000,
    onError(e) {
      setPill("crit", "연결 실패");
      el.updated.setAttribute("data-idle", "");
      el.updatedText.textContent = "Jarvis API 에 연결하지 못했어요 — 다시 시도 중";
    },
  });
})();
