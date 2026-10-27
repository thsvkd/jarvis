/* Jarvis · 홈 — 네트워크 맵 미리보기(작은 토폴로지).
 *
 *   JarvisHomeMap.render(boxEl, snap, view)   boxEl 안에 SVG 를 그린다(폭이 바뀌면 스스로 다시 그림)
 *                                           view: "conceptual"(서비스 노출 경로, 기본) | "physical"(기기 구성)
 *   JarvisHomeMap.stats(snap)           { public, lan, tailnet, local, deg, down, prob } — 지도와 같은 정의의 빠른 숫자
 *   JarvisHomeMap.physStats(snap)       { ifaces, nets, ctrs, procs, online, total, lanPeers, direct, relay } | null — 물리 관점 숫자
 *
 * snap.topology 를 "영역(Internet·Tailnet·LAN) → Cloudflare 엣지 → cloudflared → Caddy → 서비스 묶음 → 내부 백엔드"
 * 로 접는다. 서비스는 카테고리(snap.categories 순서)별 한 덩어리로 묶고 구성원 상태를 작은 모양(● ▲ ◆ ○)으로 보여 준다.
 * 그래서 서비스가 50개를 넘어도 덩어리는 최대 7개다. 선 색은 길의 종류(공개·Tailnet·LAN·의존).
 *
 * 폭에 맞춰 세 단계로 그린다: 0 자세히(알약 + 보조 글자 + 상태 점) · 1 간단히 · 2 좁게(점 + 이름, 휴대폰).
 * 데이터 문자열은 JARVIS.html 로만 들어간다. 색·굵기는 CSS(.mm-*)가 토큰으로 칠하므로 테마가 바뀌어도 다시 그릴 필요가 없다.
 * 같은 데이터면 같은 문자열이 나와 P.render 가 DOM 을 건드리지 않는다(3초 갱신에도 깜빡임 없음).
 */
(function () {
  "use strict";
  const P = window.JARVIS;
  if (!P) return;
  const { html, raw, fmt, labels } = P;
  const SH = window.JarvisShell || {};
  /** 영역·엣지 이름은 지도와 한 벌(인터넷 · Tailnet · LAN · Cloudflare 엣지) */
  const nameOf = (n) => (SH.nodeName ? SH.nodeName(n) : n.label);
  const isNum = (v) => typeof v === "number" && isFinite(v);
  const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
  const r1 = (v) => Math.round(v * 10) / 10;

  const RANK = { up: 0, unknown: 1, degraded: 2, down: 3 };
  const worstOf = (sts) => sts.reduce((w, s) => ((RANK[s] || 0) > (RANK[w] || 0) ? s : w), "up");
  const TYPE_CAT = { internal: "internal", host: "host", service: "public", infra: "infra" };
  const ZONE_ORDER = { "zone:internet": 0, "zone:tailnet": 1, "zone:lan": 2 };
  const ZONE_IC = {
    "zone:internet": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.7 3.8 5.7 3.8 9s-1.3 6.3-3.8 9c-2.5-2.7-3.8-5.7-3.8-9S9.5 5.7 12 3z"/>',
    "zone:tailnet": '<g stroke="none" fill="currentColor"><circle cx="5" cy="12" r="2.6"/><circle cx="12" cy="12" r="2.6"/><circle cx="19" cy="12" r="2.6"/><circle cx="12" cy="19" r="2.6"/><circle cx="5" cy="5" r="2.6" opacity=".35"/><circle cx="12" cy="5" r="2.6" opacity=".35"/><circle cx="19" cy="5" r="2.6" opacity=".35"/><circle cx="5" cy="19" r="2.6" opacity=".35"/><circle cx="19" cy="19" r="2.6" opacity=".35"/></g>',
    "zone:lan": '<rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 16.5h.01M10.5 16.5h.01M8 13V9.5M16 13V6"/>',
  };

  // ------------------------------------------------------------------ 글자 폭(캔버스로 잰다 — 웹폰트가 늦게 와도 다시 잰다)
  let cx = null; let fam = null; let monoFam = null;
  function tw(s, size, weight, mono) {
    s = String(s == null ? "" : s);
    if (cx === null) { try { cx = document.createElement("canvas").getContext("2d") || false; } catch (e) { cx = false; } }
    if (!fam) {
      const cs = getComputedStyle(document.body || document.documentElement);
      fam = cs.fontFamily || "sans-serif";
      monoFam = (cs.getPropertyValue("--mono") || "").trim() || "monospace";
    }
    if (!cx) return s.length * size * (mono ? 0.62 : 0.6);
    cx.font = (weight || 600) + " " + size + "px " + (mono ? monoFam : fam);
    return cx.measureText(s).width;
  }

  // ------------------------------------------------------------------ 모형: 토폴로지 → 노드(영역·홉·묶음) + 합친 엣지
  function detailOf(n, s) {
    const net = (s.system && s.system.network) || {};
    const tun = (s.ingress && s.ingress.tunnel) || {};
    if (n.id === "zone:tailnet") {
      const t = s.tailscale || {};
      return isNum(t.peers_online) ? fmt.num(t.peers_online) + "/" + fmt.num(t.peers_total) : null;
    }
    if (n.id === "zone:lan") return (net.lan_ips || [])[0] || (n.detail ? String(n.detail).split(/,\s*/)[0] : null);
    if (n.id === "zone:internet") return null;
    if (n.type === "edge") return (tun.edge_locations || []).join("·") || n.detail || null;
    if (n.id === "infra:cloudflared" || /cloudflared/.test(n.id)) {
      const c = tun.ready_connections != null ? tun.ready_connections : tun.connections;
      return isNum(c) ? "연결 " + c : null;
    }
    if (n.id === "infra:caddy") {
      const r = ((s.ingress && s.ingress.caddy && s.ingress.caddy.routes) || []).length;
      return r ? "라우트 " + r : null;
    }
    return n.detail || null;
  }

  function build(s) {
    const T = s.topology || {};
    const nodes = (T.nodes || []).filter((n) => n && n.id);
    if (!nodes.length) return null;
    const N = new Map(nodes.map((n) => [n.id, n]));
    const edges = (T.edges || []).filter((e) => e && N.has(e.from) && N.has(e.to) && e.from !== e.to);
    const svc = new Map((s.services || []).map((x) => [x.id, x]));
    const hasOut = new Set(edges.filter((e) => e.kind !== "depends").map((e) => e.from));
    const hopish = (n) => n.type === "zone" || n.type === "edge" || (n.type === "infra" && hasOut.has(n.id));
    const catOf = (n) => {
      if (n.category) return n.category;
      const x = n.service_id && svc.get(n.service_id);
      return (x && x.category) || TYPE_CAT[n.type] || "other";
    };
    const gid = (n) => (hopish(n) ? n.id : "g:" + catOf(n));
    const catLabel = {}; const catIdx = {};
    (s.categories || []).forEach((c, i) => { catLabel[c.id] = c.label; catIdx[c.id] = i; });

    const V = new Map();
    for (const n of nodes) {
      const id = gid(n);
      let v = V.get(id);
      if (!v) {
        const hop = hopish(n);
        v = { id, kind: n.type === "zone" ? "zone" : hop ? "hop" : "group", node: hop ? n : null, cat: hop ? null : catOf(n), members: [], ins: [], outs: [] };
        V.set(id, v);
      }
      v.members.push(n);
    }
    const E = new Map();
    for (const e of edges) {
      const a = gid(N.get(e.from)); const b = gid(N.get(e.to));
      if (a === b) continue;
      const k = a + ">" + b + ">" + e.kind;
      let x = E.get(k);
      if (!x) { x = { key: k, a, b, kind: e.kind, n: 0, labels: new Set() }; E.set(k, x); V.get(a).outs.push(x); V.get(b).ins.push(x); }
      x.n++;
      if (e.label) x.labels.add(e.label);
    }
    for (const v of V.values()) {
      const sts = v.members.map((m) => m.status || "unknown");
      v.status = worstOf(sts);
      v.tone = v.kind === "zone" && v.status === "up" ? "ok" : P.tone(v.status);
      if (v.kind === "group") {
        v.n = v.members.length;
        v.label = v.n === 1 ? v.members[0].label : catLabel[v.cat] || labels.category[v.cat] || v.cat;
        v.order = catIdx[v.cat] != null ? catIdx[v.cat] : 99;
        v.bad = v.members.filter((m) => m.status === "degraded" || m.status === "down").length;
        // 나쁜 상태가 앞에 오게(점 줄에서 먼저 보이게)
        v.dots = v.members.map((m) => m.status || "unknown").sort((x, y) => (RANK[y] || 0) - (RANK[x] || 0));
      } else {
        v.label = nameOf(v.node);
        v.full = v.node.label;
        v.detail = detailOf(v.node, s);
      }
    }
    return { V, E: Array.from(E.values()) };
  }

  // ------------------------------------------------------------------ 배치: 열(깊이) · 행(무게중심 + 겹침 풀기)
  function place(M, compact) {
    const vs = Array.from(M.V.values());
    const depth = new Map();
    vs.forEach((v) => { if (v.kind === "zone") depth.set(v.id, 0); });
    for (let it = 0; it < 12; it++) {
      let changed = false;
      for (const e of M.E) {
        if (e.kind === "depends" || M.V.get(e.b).kind !== "hop" || !depth.has(e.a)) continue;
        const d = Math.min(8, depth.get(e.a) + 1);
        if (!depth.has(e.b) || depth.get(e.b) < d) { depth.set(e.b, d); changed = true; }
      }
      if (!changed) break;
    }
    let maxHop = 0;
    vs.forEach((v) => {
      if (v.kind === "hop") { if (!depth.has(v.id)) depth.set(v.id, 1); maxHop = Math.max(maxHop, depth.get(v.id)); }
    });
    const destCol = maxHop + 1;
    let hasBack = false;
    vs.forEach((v) => {
      if (v.kind !== "group") return;
      v.backend = v.ins.length > 0 && v.ins.every((e) => e.kind === "depends");
      if (v.backend && !compact) hasBack = true;
      depth.set(v.id, v.backend && !compact ? destCol + 1 : destCol);
    });
    const ncol = destCol + (hasBack ? 2 : 1);

    const Y = new Map();
    const dest = vs.filter((v) => v.kind === "group" && depth.get(v.id) === destCol)
      .sort((a, b) => (compact ? (a.backend ? 1 : 0) - (b.backend ? 1 : 0) : 0) || a.order - b.order || a.label.localeCompare(b.label));
    dest.forEach((g, i) => Y.set(g.id, i));
    const spread = (list) => {
      if (!list.length) return;
      list.sort((a, b) => a.y - b.y || a.o - b.o);
      const before = list.reduce((t, x) => t + x.y, 0);
      for (let i = 1; i < list.length; i++) if (list[i].y < list[i - 1].y + 1) list[i].y = list[i - 1].y + 1;
      const shift = (before - list.reduce((t, x) => t + x.y, 0)) / list.length;
      list.forEach((x) => Y.set(x.v.id, x.y + shift));
    };
    const back = vs.filter((v) => depth.get(v.id) === destCol + 1);
    spread(back.map((v) => ({ v, o: v.order, y: mean(v.ins.map((e) => Y.get(e.a)).filter(isNum)) ?? 0 })));
    const toward = (v) => {
      const ys = v.outs.filter((e) => e.kind !== "depends").map((e) => Y.get(e.b)).filter(isNum);
      return ys.length ? mean(ys) : null;
    };
    for (let c = maxHop; c >= 0; c--) {
      const col = vs.filter((v) => depth.get(v.id) === c && v.kind !== "group");
      spread(col.map((v) => ({ v, o: ZONE_ORDER[v.id] != null ? ZONE_ORDER[v.id] : 5, y: toward(v) ?? (dest.length - 1) / 2 })));
    }
    let ymin = Infinity; let ymax = -Infinity;
    Y.forEach((y) => { ymin = Math.min(ymin, y); ymax = Math.max(ymax, y); });
    if (!isFinite(ymin)) { ymin = 0; ymax = 0; }
    return { depth, ncol, destCol, maxHop, Y, ymin, ymax };
  }

  // ------------------------------------------------------------------ 모양
  function shape(tone, x, y, r) {
    x = r1(x); y = r1(y);
    if (tone === "warn") return html`<path class="mm-sh" data-tone="warn" d="${"M" + x + " " + r1(y - r) + "L" + r1(x + r) + " " + r1(y + r * 0.8) + "L" + r1(x - r) + " " + r1(y + r * 0.8) + "Z"}"/>`;
    if (tone === "crit") return html`<path class="mm-sh" data-tone="crit" d="${"M" + x + " " + r1(y - r) + "L" + r1(x + r) + " " + y + "L" + x + " " + r1(y + r) + "L" + r1(x - r) + " " + y + "Z"}"/>`;
    if (tone === "muted" || tone === "info") return html`<circle class="mm-sh ring" data-tone="${tone}" cx="${x}" cy="${y}" r="${r1(r - 0.6)}"/>`;
    return html`<circle class="mm-sh" data-tone="ok" cx="${x}" cy="${y}" r="${r}"/>`;
  }
  const curve = (x1, y1, x2, y2) => {
    const dx = Math.max(12, (x2 - x1) * 0.5);
    return "M" + r1(x1) + " " + r1(y1) + "C" + r1(x1 + dx) + " " + r1(y1) + " " + r1(x2 - dx) + " " + r1(y2) + " " + r1(x2) + " " + r1(y2);
  };
  /** 한 노드에 여러 선이 모이면 세로로 조금씩 벌린다 */
  function ports(list, key, h, Ypx) {
    const out = new Map();
    const sorted = list.slice().sort((a, b) => (Ypx(a[key]) || 0) - (Ypx(b[key]) || 0));
    const n = sorted.length;
    const step = n < 2 ? 0 : Math.min(6, (h - 12) / (n - 1));
    sorted.forEach((e, i) => out.set(e.key, (i - (n - 1) / 2) * step));
    return out;
  }

  // ------------------------------------------------------------------ 넓은 배치(알약)
  const WIDE = { h: 30, slot: 44, gapMin: 34, gapMax: 150, padY: 8 };
  function wideW(v, lv, drop) {
    const label = tw(v.label, 12.5, 650);
    if (v.kind === "group") {
      const count = v.n > 1 ? 6 + tw(String(v.n), 12, 600) : 0;
      const maxDots = lv === 0 ? 12 : 6;
      const nd = Math.min(v.dots.length, maxDots);
      const more = v.dots.length > maxDots ? 4 + tw("+" + (v.dots.length - maxDots + 1), 11, 600) : 0;
      return 16 + label + count + 10 + nd * 9 + more + 10;
    }
    const lead = v.kind === "zone" ? 14 + 6 : 8 + 7;
    const det = lv === 0 && v.detail && !(drop && drop.has(v.id)) ? 7 + tw(v.detail, 11, 500) : 0;
    return 11 + lead + label + det + 12;
  }
  function wideLayout(M, W, lv, drop) {
    const pl = place(M, false);
    const colW = new Array(pl.ncol).fill(0);
    const w = new Map();
    M.V.forEach((v) => {
      const ww = Math.ceil(wideW(v, lv, drop));
      w.set(v.id, ww);
      const c = pl.depth.get(v.id);
      colW[c] = Math.max(colW[c], ww);
    });
    const sumW = colW.reduce((a, b) => a + b, 0);
    const gaps = Math.max(1, pl.ncol - 1);
    if (sumW + gaps * WIDE.gapMin > W) return null;
    const gap = Math.min(WIDE.gapMax, (W - sumW) / gaps);
    const total = sumW + gap * gaps;
    const x0 = Math.max(0, (W - total) / 2);
    const colX = []; let acc = x0;
    colW.forEach((cw, i) => { colX[i] = acc; acc += cw + gap; });
    const pos = new Map();
    M.V.forEach((v) => {
      const c = pl.depth.get(v.id);
      const ww = w.get(v.id);
      const x = v.kind === "zone" ? colX[c] + colW[c] - ww : colX[c];
      const y = WIDE.padY + (pl.Y.get(v.id) - pl.ymin) * WIDE.slot;
      pos.set(v.id, { x: r1(x), y: r1(y), w: ww, h: WIDE.h });
    });
    const H = Math.ceil(WIDE.padY * 2 + (pl.ymax - pl.ymin) * WIDE.slot + WIDE.h);
    return { lv, W, H, pos, gap, drop: drop || new Set(), depth: pl.depth, colX, colW };
  }
  function drawWide(M, L) {
    const { pos } = L;
    const cy = (id) => pos.get(id).y + pos.get(id).h / 2;
    const outP = new Map(); const inP = new Map();
    M.V.forEach((v) => {
      ports(v.outs, "b", WIDE.h, cy).forEach((o, k) => outP.set(k, o));
      ports(v.ins, "a", WIDE.h, cy).forEach((o, k) => inP.set(k, o));
    });
    const edges = []; const eps = []; const elabels = [];
    for (const e of M.E) {
      const a = pos.get(e.a); const b = pos.get(e.b);
      const x1 = a.x + a.w; const y1 = cy(e.a) + (outP.get(e.key) || 0);
      const x2 = b.x; const y2 = cy(e.b) + (inP.get(e.key) || 0);
      const da = L.depth.get(e.a); const db = L.depth.get(e.b);
      // 열을 건너뛰는 선(예: Tailnet → Caddy :443)은 중간 열의 노드를 지나가지 않게 가로 차선으로 돌아서
      // 도착 열 바로 앞에서 휘어 들어간다(지도와 같은 길 모양). 차선이 중간 노드와 겹치면 그 아래로 내린다.
      let d; let lx; let ly; let segW;
      if (db - da > 1) {
        let lane = y1;
        for (let c = da + 1; c < db; c++) {
          pos.forEach((p, id) => { if (L.depth.get(id) === c && lane > p.y - 5 && lane < p.y + p.h + 5) lane = p.y + p.h + 9; });
        }
        const xk = L.colX[db - 1] + L.colW[db - 1] + L.gap * 0.35;
        d = "M" + r1(x1) + " " + r1(y1);
        if (lane !== y1) { const xs = x1 + Math.min(L.gap * 0.8, 40); d += "C" + r1(x1 + 14) + " " + r1(y1) + " " + r1(xs - 14) + " " + r1(lane) + " " + r1(xs) + " " + r1(lane); }
        d += "H" + r1(xk);
        const dx = Math.max(10, (x2 - xk) * 0.55);
        d += "C" + r1(xk + dx) + " " + r1(lane) + " " + r1(x2 - dx) + " " + r1(y2) + " " + r1(x2) + " " + r1(y2);
        segW = x2 - xk; lx = (xk + x2) / 2; ly = (lane + y2) / 2;
        if (segW < 44) { segW = xk - x1 - 20; lx = xk - 30; ly = lane; }   // 마지막 굽이가 짧으면 라벨은 차선 끝에
      } else {
        d = curve(x1, y1, x2, y2);
        segW = x2 - x1; lx = (x1 + x2) / 2; ly = (y1 + y2) / 2;
      }
      edges.push(html`<path class="mm-e" data-k="${e.kind}" d="${d}"/>`);
      eps.push(html`<circle class="mm-ep" data-k="${e.kind}" cx="${r1(x2)}" cy="${r1(y2)}" r="2.2"/>`);
      const tb = M.V.get(e.b);
      if (L.lv === 0 && e.labels.size === 1 && tb.kind === "hop") {
        const t = Array.from(e.labels)[0];
        const lw = tw(t, 10, 500, true) + 10;
        if (segW >= lw + 10) {
          elabels.push(html`<g class="mm-el" data-k="${e.kind}" transform="${"translate(" + r1(lx) + " " + r1(ly) + ")"}"><rect x="${r1(-lw / 2)}" y="-8" width="${r1(lw)}" height="16" rx="8"/><text y="0.5" text-anchor="middle" dominant-baseline="central">${t}</text></g>`);
        }
      }
    }
    const nodes = [];
    M.V.forEach((v) => {
      const p = pos.get(v.id);
      const mid = p.h / 2;
      const tr = "translate(" + p.x + " " + p.y + ")";
      if (v.kind === "group") {
        const lx = 14;
        const lw = tw(v.label, 12.5, 650);
        let x = lx + lw;
        let count = "";
        if (v.n > 1) { x += 6; count = html`<text class="mm-c" x="${r1(x)}" y="${mid}" dominant-baseline="central">${fmt.num(v.n)}</text>`; x += tw(String(v.n), 12, 600); }
        x += 10;
        const maxDots = L.lv === 0 ? 12 : 6;
        const over = v.dots.length > maxDots;
        const shown = over ? v.dots.slice(0, maxDots - 1) : v.dots;
        const dots = shown.map((st, i) => shape(P.tone(st), x + 3.5 + i * 9, mid, 3.4));
        const more = over ? html`<text class="mm-more" x="${r1(x + shown.length * 9 + 2)}" y="${mid}" dominant-baseline="central">${"+" + (v.dots.length - shown.length)}</text>` : "";
        nodes.push(html`<g class="mm-n mm-grp" data-tone="${v.tone}" data-cat="${v.cat}" transform="${tr}"><rect class="mm-box" width="${p.w}" height="${p.h}" rx="9"/><rect class="mm-acc" x="5" y="8" width="3" height="${p.h - 16}" rx="1.5"/><text class="mm-l" x="${lx}" y="${mid}" dominant-baseline="central">${v.label}</text>${count}${dots}${more}</g>`);
      } else if (v.kind === "zone") {
        const lw = tw(v.label, 12.5, 650);
        const det = L.lv === 0 && v.detail && !L.drop.has(v.id) ? html`<text class="mm-d" x="${r1(31 + lw + 7)}" y="${mid}" dominant-baseline="central">${v.detail}</text>` : "";
        nodes.push(html`<g class="mm-n mm-zone" data-tone="${v.tone}" transform="${tr}"><rect class="mm-box" width="${p.w}" height="${p.h}" rx="${p.h / 2}"/><g class="mm-ic" transform="${"translate(11 " + r1(mid - 7) + ") scale(.5833)"}">${raw(ZONE_IC[v.id] || ZONE_IC["zone:internet"])}</g><text class="mm-l" x="31" y="${mid}" dominant-baseline="central">${v.label}</text>${det}</g>`);
      } else {
        const lw = tw(v.label, 12.5, 650);
        const det = L.lv === 0 && v.detail && !L.drop.has(v.id) ? html`<text class="mm-d" x="${r1(26 + lw + 7)}" y="${mid}" dominant-baseline="central">${v.detail}</text>` : "";
        nodes.push(html`<g class="mm-n mm-hop" data-tone="${v.tone}" transform="${tr}"><rect class="mm-box" width="${p.w}" height="${p.h}" rx="9"/>${shape(v.tone, 15, mid, 4)}<text class="mm-l" x="26" y="${mid}" dominant-baseline="central">${v.label}</text>${det}</g>`);
      }
    });
    return html`<svg class="mm" data-lv="${L.lv}" viewBox="${"0 0 " + L.W + " " + L.H}" width="${L.W}" height="${L.H}" aria-hidden="true" focusable="false"><g class="mm-edges">${edges}</g><g class="mm-eps">${eps}</g><g class="mm-els">${elabels}</g><g class="mm-nodes">${nodes}</g></svg>`;
  }

  // ------------------------------------------------------------------ 좁은 배치(점 + 이름) — 휴대폰
  const NARROW = { slot: 34, r: 5, padT: 24, padB: 20, padL: 8 };
  function narrowLayout(M, W) {
    const pl = place(M, true);
    const groups = Array.from(M.V.values()).filter((v) => v.kind === "group");
    const gLabel = (v) => v.label + (v.n > 1 ? " " + v.n : "");
    const gw = groups.reduce((m, v) => Math.max(m, tw(gLabel(v), 12, 650) + (v.bad ? 16 : 0)), 40);
    const gx = Math.max(NARROW.padL + 150, W - gw - 22);
    const xs = [];
    for (let c = 0; c <= pl.maxHop; c++) xs[c] = NARROW.padL + 4 + (c * (gx - NARROW.padL - 4)) / (pl.maxHop + 1);
    xs[pl.destCol] = gx;
    const pos = new Map();
    M.V.forEach((v) => {
      const c = pl.depth.get(v.id);
      pos.set(v.id, { x: r1(xs[c]), y: r1(NARROW.padT + (pl.Y.get(v.id) - pl.ymin) * NARROW.slot), c });
    });
    const H = Math.ceil(NARROW.padT + (pl.ymax - pl.ymin) * NARROW.slot + NARROW.padB + 8);
    return { W, H, pos, gx, xs, pl, gLabel };
  }
  function drawNarrow(M, L) {
    const { pos } = L;
    const R = NARROW.r;
    const edges = [];
    for (const e of M.E) {
      const a = pos.get(e.a); const b = pos.get(e.b);
      if (e.kind === "depends" && a.c === b.c) {
        // 같은 열의 의존(서비스 묶음 → 내부 백엔드)은 왼쪽으로 휘는 점선 호
        const bx = a.x - R - 16;
        edges.push(html`<path class="mm-e" data-k="depends" d="${"M" + r1(a.x - R) + " " + a.y + "C" + r1(bx) + " " + a.y + " " + r1(bx) + " " + b.y + " " + r1(b.x - R) + " " + b.y}"/>`);
        continue;
      }
      if (b.c - a.c > 1 && L.xs[b.c - 1] != null) {
        // 열을 건너뛰면 출발 높이로 가다가 도착 열 바로 앞에서 휜다(중간 정거장 이름을 가로지르지 않게)
        const xk = L.xs[b.c - 1] + (b.x - L.xs[b.c - 1]) * 0.35;
        const x2 = b.x - R - 1;
        const dx = Math.max(8, (x2 - xk) * 0.55);
        edges.push(html`<path class="mm-e" data-k="${e.kind}" d="${"M" + r1(a.x + R) + " " + a.y + "H" + r1(xk) + "C" + r1(xk + dx) + " " + a.y + " " + r1(x2 - dx) + " " + b.y + " " + r1(x2) + " " + b.y}"/>`);
        continue;
      }
      edges.push(html`<path class="mm-e" data-k="${e.kind}" d="${curve(a.x + R, a.y, b.x - R - 1, b.y)}"/>`);
    }
    // 이름: 영역은 점 아래, 홉은 열마다 위·아래를 번갈아(한 줄에 나란히 있어도 겹치지 않게)
    const nodes = [];
    const hopCols = new Map();
    M.V.forEach((v) => { if (v.kind === "hop") hopCols.set(v.id, pos.get(v.id).c); });
    M.V.forEach((v) => {
      const p = pos.get(v.id);
      if (v.kind === "group") {
        const label = L.gLabel(v);
        const bad = v.bad ? html`${shape(v.tone, p.x + 14 + tw(label, 12, 650) + 8, p.y, 3.6)}` : "";
        nodes.push(html`<g class="mm-n mm-grp mm-dot" data-tone="${v.tone}" data-cat="${v.cat}"><rect class="mm-acc" x="${r1(p.x - R)}" y="${r1(p.y - R)}" width="${R * 2}" height="${R * 2}" rx="3"/><text class="mm-l" x="${r1(p.x + 11)}" y="${p.y}" dominant-baseline="central">${label}</text>${bad}</g>`);
      } else if (v.kind === "zone") {
        nodes.push(html`<g class="mm-n mm-zone mm-dot" data-tone="${v.tone}"><circle class="mm-ring" cx="${p.x}" cy="${p.y}" r="${R}"/><text class="mm-l" x="${r1(p.x - R)}" y="${r1(p.y + 9)}" dominant-baseline="hanging">${v.label}</text></g>`);
      } else {
        const below = p.c % 2 === 0;
        const lw = tw(v.label, 12, 650);
        const lx = Math.max(p.x - lw / 2, 2);
        nodes.push(html`<g class="mm-n mm-hop mm-dot" data-tone="${v.tone}">${shape(v.tone === "ok" ? "ok" : v.tone, p.x, p.y, R)}<text class="mm-l" x="${r1(lx)}" y="${r1(below ? p.y + 9 : p.y - 10)}"${below ? raw(' dominant-baseline="hanging"') : ""}>${v.label}</text></g>`);
      }
    });
    return html`<svg class="mm mm-narrow" viewBox="${"0 0 " + L.W + " " + L.H}" width="${L.W}" height="${L.H}" aria-hidden="true" focusable="false"><g class="mm-edges">${edges}</g><g class="mm-nodes">${nodes}</g></svg>`;
  }

  // ================================================================== 물리 관점(기기 구성) 미리보기
  // snap.topology_physical 을 전체 물리 지도와 같은 뼈대로 위에서 아래로 접는다:
  //   인터넷 → Cloudflare 엣지 · LAN(+ 같은 LAN 기기) · 이 기기(위 테두리 eth0·wlan0, 안에 Docker 네트워크 칩 · 호스트 프로세스,
  //   아래 테두리 tailscale0) · Tailnet(+ 기기 수 · 경로). 상자는 "포함", 선은 "실제 연결". 터널은 컨테이너에서 Cloudflare 로.
  const IF_ORDER = { wired: 0, wifi: 1, tailnet: 2 };
  const PIC = {
    lan: ZONE_IC["zone:lan"],
    tailnet: ZONE_IC["zone:tailnet"],
    internet: ZONE_IC["zone:internet"],
    peers: '<rect x="3" y="5" width="12" height="9" rx="1.8"/><path d="M6 17.5h6"/><rect x="15.5" y="9" width="5.5" height="10" rx="1.4"/>',
    chip: '<rect x="5" y="5" width="14" height="14" rx="2.4"/><path d="M9 2.5v2.5M15 2.5v2.5M9 19v2.5M15 19v2.5M2.5 9h2.5M2.5 15h2.5M19 9h2.5M19 15h2.5"/>',
    docker: '<path d="M20.5 7.8 12 3 3.5 7.8v8.4L12 21l8.5-4.8V7.8z"/><path d="M3.5 7.8 12 12.6l8.5-4.8M12 12.6V21"/>',
  };
  /** compose 기본 네트워크 이름 "<프로젝트>_default" 는 프로젝트 이름만 */
  const netLabel = (l) => String(l || "").replace(/_default$/, "");

  function physModel(s) {
    const T = s.topology_physical || {};
    const nodes = (T.nodes || []).filter((n) => n && n.id);
    if (!nodes.length) return null;
    const N = new Map(nodes.map((n) => [n.id, n]));
    const edges = (T.edges || []).filter((e) => e && N.has(e.from) && N.has(e.to) && e.from !== e.to);
    const kids = new Map();
    for (const n of nodes) if (n.parent && N.has(n.parent)) { if (!kids.has(n.parent)) kids.set(n.parent, []); kids.get(n.parent).push(n); }
    const of = (id, type) => (kids.get(id) || []).filter((n) => !type || n.type === type);
    const dev = nodes.find((n) => n.type === "device" && n.self) || nodes.find((n) => n.type === "device");
    if (!dev) return null;
    const segOf = new Map();          // 인터페이스 → 그 인터페이스가 닿는 구간 엣지
    for (const e of edges) if (N.get(e.from).type === "iface" && N.get(e.to).type === "segment") segOf.set(e.from, e);
    const ifaces = of(dev.id, "iface").sort((a, b) => (IF_ORDER[a.kind] != null ? IF_ORDER[a.kind] : 5) - (IF_ORDER[b.kind] != null ? IF_ORDER[b.kind] : 5) || String(a.label).localeCompare(String(b.label)));
    const segs = nodes.filter((n) => n.type === "segment");
    const segLan = N.get("seg:lan") || segs.find((n) => /lan/i.test(n.id)) || null;
    const segTn = N.get("seg:tailnet") || segs.find((n) => /tailnet/i.test(n.id)) || null;
    const ports = ifaces.map((i) => {
      const e = segOf.get(i.id);
      const seg = e ? e.to : null;
      return { id: i.id, label: i.label, detail: (i.ips || [])[0] || i.detail || null, kind: i.kind, seg, ek: e ? e.kind : null, el: e && e.label ? String(e.label) : null, status: i.status || "unknown" };
    });
    // Docker: 네트워크마다 붙은 컨테이너 수(주 네트워크 + 다리로 이어진 추가 네트워크). host 네트워크 컨테이너는 한 칩.
    const engine = of(dev.id, "engine")[0] || null;
    const nets = engine ? of(engine.id, "network") : [];
    const att = new Map(nets.map((n) => [n.id, new Set()]));
    const ctrs = nodes.filter((n) => n.type === "container");
    for (const c of ctrs) if (att.has(c.parent)) att.get(c.parent).add(c.id);
    for (const e of edges) if (e.kind === "bridge" && att.has(e.to) && N.get(e.from).type === "container") att.get(e.to).add(e.from);
    const tunnelE = edges.find((e) => e.kind === "tunnel") || null;
    const tunnelCtr = tunnelE ? N.get(tunnelE.from) : null;
    // 칩 숫자는 지도와 같은 말: "본래 구성원 + 다른 망에서 붙은 수"(예: edge 7+2)
    const chips = nets.map((n) => {
      const ids = Array.from(att.get(n.id));
      const own = ids.filter((id) => N.get(id).parent === n.id).length;
      const br = ids.length - own;
      return { id: n.id, label: netLabel(n.label), n: ids.length, nt: own + (br ? "+" + br : ""), status: worstOf(ids.map((id) => N.get(id).status || "unknown")), tunnel: !!(tunnelCtr && tunnelCtr.parent === n.id) };
    }).sort((a, b) => b.n - a.n || a.label.localeCompare(b.label));
    const hostCtrs = engine ? of(engine.id, "container") : [];
    if (hostCtrs.length) chips.push({ id: "host", label: "host", n: hostCtrs.length, status: worstOf(hostCtrs.map((c) => c.status || "unknown")), tunnel: !!(tunnelCtr && tunnelCtr.parent === engine.id), host: true });
    // 터널이 나가는 칩은 맨 뒤에(오른쪽 통로로 빠져나가 위의 Cloudflare 로 — 전체 지도와 같은 방향)
    const ti = chips.findIndex((c) => c.tunnel);
    if (ti >= 0 && ti < chips.length - 1) chips.push(chips.splice(ti, 1)[0]);
    const procs = of(dev.id, "process");
    // 다른 기기(tailnet 피어): 온라인 수 · 같은 LAN 기기 수(공용 판정 — 공유기 NAT 경유는 빼고, 한 번 튀는 변화는 누른다) · 경로(직결·중계·유휴)
    const peers = nodes.filter((n) => n.type === "peer");
    const LP = SH.lanPeers ? SH.lanPeers(s) : null;
    // 같은 LAN 기기 수는 공용 정의 하나(JarvisShell.lanPeers.count — 지도 요약 칸·열 제목과 같은 수)
    const lanPeers = LP ? { size: LP.count } : new Set(edges.filter((e) => e.kind === "lan" && N.get(e.from).type === "peer" && !N.get(e.from).parent).map((e) => e.from));
    const mix = SH.tailnetMix ? SH.tailnetMix(s) : { direct: 0, relay: 0, idle: 0, text: "" };
    const direct = mix.direct; const relay = mix.relay; const idle = mix.idle;
    const ts = s.tailscale || {};
    const online = isNum(ts.peers_online) ? ts.peers_online : peers.filter((p) => p.online).length;
    const total = isNum(ts.peers_total) ? ts.peers_total : peers.length;
    const uplink = segLan ? edges.find((e) => e.kind === "uplink" && e.from === segLan.id) : null;
    const inet = N.get("ext:internet") || nodes.find((n) => n.type === "external" && /internet/.test(n.id)) || null;
    const cf = N.get("ext:cloudflare") || nodes.find((n) => n.type === "external" && /cloudflare/.test(n.id)) || null;
    const host = (s.system && s.system.host) || {};
    const memT = isNum(dev.mem_total) ? dev.mem_total : (s.system && s.system.memory && s.system.memory.total);
    return {
      dev: { label: dev.label || host.hostname || "host", status: dev.status || "unknown", detail: [String(dev.detail || host.model || "").replace(/\s+Model\s+\S+.*$/i, "").replace(/\s+Rev\s+.*$/i, "").trim() || null, isNum(dev.cores) ? dev.cores + "코어" : null, isNum(memT) ? fmt.bytes(memT, 1) : null].filter(Boolean).join(" · ") },
      ports, chips, procs: procs.map((p) => p.status || "unknown"), nProc: procs.length, nCtr: ctrs.length, nNet: nets.length, nBridged: edges.filter((e) => e.kind === "bridge").length,
      lan: segLan ? { label: "LAN", detail: segLan.cidr || null, status: segLan.status || "up" } : null,
      tn: segTn ? { label: "Tailnet", status: segTn.status || "up" } : null,
      lanPeers: lanPeers.size, online, total, direct, relay, idle, mixText: mix.text, brief: SH.alertBrief ? SH.alertBrief(s) : null,
      inet: inet ? { label: nameOf({ id: "zone:internet", type: "zone", label: inet.label }) } : null,
      cf: cf ? { label: nameOf({ id: "edge:cf", type: "edge", label: cf.label }), detail: cf.detail ? String(cf.detail).replace(/,\s*/g, "·") : null, status: cf.status || "up" } : null,
      uplink: uplink ? (uplink.label ? String(uplink.label) : "NAT") : null,
      tunnel: tunnelE ? { label: tunnelE.label ? "터널 " + String(tunnelE.label) : "터널" } : null,
      hasTunnelChip: chips.some((c) => c.tunnel),
    };
  }
  /** 물리 관점 빠른 숫자(개요 타일 옆 칸) */
  function physStats(s) {
    let M = null;
    try { M = physModel(s); } catch (e) { M = null; }
    if (!M) return null;
    return { ifaces: M.ports.length, nets: M.nNet, ctrs: M.nCtr, procs: M.nProc, online: M.online, total: M.total, lanPeers: M.lanPeers, direct: M.direct, relay: M.relay, idle: M.idle, mix: M.mixText, bridged: M.nBridged };
  }

  // 알약(아이콘 + 이름 + 보조 글자) 폭
  const pillW = (label, detail, icon, small, mono) => Math.ceil((small ? 9 : 11) + (icon ? (small ? 17 : 20) : 13) + (mono ? tw(label, 11.5, 600, true) : tw(label, small ? 12 : 12.5, 650)) + (detail ? 7 + tw(detail, 11, 500) : 0) + (small ? 10 : 12));
  function pillSvg(o) {
    // o: {x, y, w, h, label, detail, icon, tone, cls, k}
    const mid = o.h / 2;
    const ix = o.small ? 9 : 11;
    const lead = o.icon
      ? html`<g class="mm-ic" transform="${"translate(" + ix + " " + r1(mid - 7) + ") scale(.5833)"}">${raw(o.icon)}</g>`
      : shape(o.tone || "ok", ix + 4, mid, 3.6);
    const lx = ix + (o.icon ? (o.small ? 17 : 20) : 13);
    const lw = o.mono ? tw(o.label, 11.5, 600, true) : tw(o.label, o.small ? 12 : 12.5, 650);
    return html`<g class="${"mm-n " + (o.cls || "mm-hop")}" data-tone="${o.tone || "ok"}"${o.k ? html` data-k="${o.k}"` : ""} transform="${"translate(" + r1(o.x) + " " + r1(o.y) + ")"}"><rect class="mm-box" width="${o.w}" height="${o.h}" rx="${o.h / 2}"/>${lead}<text class="mm-l" x="${r1(lx)}" y="${r1(mid)}" dominant-baseline="central">${o.label}</text>${o.detail ? html`<text class="mm-d" x="${r1(lx + lw + 7)}" y="${r1(mid)}" dominant-baseline="central">${o.detail}</text>` : ""}</g>`;
  }
  const vcurve = (x1, y1, x2, y2) => {
    const dy = (y2 - y1) * 0.5;
    return "M" + r1(x1) + " " + r1(y1) + "C" + r1(x1) + " " + r1(y1 + dy) + " " + r1(x2) + " " + r1(y2 - dy) + " " + r1(x2) + " " + r1(y2);
  };
  function edgeLabelSvg(t, x, y, k, anchor) {
    const lw = tw(t, 10, 500, true) + 10;
    const x0 = anchor === "start" ? x : x - lw / 2;
    return html`<g class="mm-el" data-k="${k}" transform="${"translate(" + r1(x0 + lw / 2) + " " + r1(y) + ")"}"><rect x="${r1(-lw / 2)}" y="-8" width="${r1(lw)}" height="16" rx="8"/><text y="0.5" text-anchor="middle" dominant-baseline="central">${t}</text></g>`;
  }

  /** lv 0 자세히 · 1 보조 글자 줄임 · 2 좁게(피어 묶음을 구간 알약에 합침, 칩 개수 제한). 안 맞으면 null
   *  뼈대는 전체 물리 지도와 같다(눌러 들어가도 다시 익히지 않게):
   *    맨 위 인터넷 → Cloudflare 엣지 · LAN 버스(위 테두리의 eth0·wlan0 이 붙음) · 기기 상자 · 아래 테두리의 tailscale0 → Tailnet */
  function physLayout(M, W, lv) {
    const small = lv >= 2;
    const PH = small ? 28 : 30;          // 알약 높이
    const PORT_H = 26;
    const GAP_Y = small ? 34 : 38;
    const boxX = 0;
    const boxW = W - 1;
    const merged = lv >= 2;
    // ---- 맨 위: 인터넷 → Cloudflare 엣지
    const inet = M.inet ? { label: M.inet.label, icon: PIC.internet } : null;
    if (inet) inet.w = pillW(inet.label, null, true, small);
    const cf = M.cf ? { label: M.cf.label, detail: lv === 0 ? M.cf.detail : null, tone: P.tone(M.cf.status) } : null;
    if (cf) cf.w = pillW(cf.label, cf.detail, null, small);
    // ---- 둘째 줄: LAN 구간 + 같은 LAN 기기
    const lan = M.lan ? { label: M.lan.label, detail: merged ? (M.lanPeers ? "기기 " + M.lanPeers : null) : lv === 0 ? M.lan.detail : null, icon: PIC.lan, tone: P.tone(M.lan.status), k: "lan" } : null;
    if (lan) lan.w = pillW(lan.label, lan.detail, true, small);
    const lanP = lan && !merged && M.lanPeers ? { label: "같은 LAN 기기 " + M.lanPeers, icon: PIC.peers, small: true } : null;
    if (lanP) lanP.w = pillW(lanP.label, null, true, true);
    // ---- 맨 아래: Tailnet 구간 + 기기 수 · 경로
    const tn = M.tn ? { label: M.tn.label, detail: merged ? fmt.num(M.online) + "/" + fmt.num(M.total) : null, icon: PIC.tailnet, tone: P.tone(M.tn.status), k: "tailnet" } : null;
    if (tn) tn.w = pillW(tn.label, tn.detail, true, small);
    const tnP = tn && !merged ? { label: "기기 " + fmt.num(M.online) + "/" + fmt.num(M.total) + " 온라인", detail: lv === 0 && M.mixText ? M.mixText : null, icon: PIC.peers, small: true } : null;
    if (tnP) tnP.w = pillW(tnP.label, tnP.detail, true, true);
    // ---- 포트(인터페이스): LAN 쪽은 위 테두리, Tailnet 쪽은 아래 테두리
    const pw = (p) => pillW(p.label, lv === 0 ? p.detail : null, null, true, true);
    const topPorts = M.ports.filter((p) => !(tn && p.seg && /tailnet/i.test(p.seg)));
    const botPorts = M.ports.filter((p) => tn && p.seg && /tailnet/i.test(p.seg));
    const PG = 8;
    const rowW = (list) => list.reduce((a, p) => a + pw(p), 0) + Math.max(0, list.length - 1) * PG;
    const SAT = small ? 16 : 22;
    // 폭 검사: 윗줄(인터넷 → Cloudflare), 둘째 줄(LAN + 기기), 위 포트, 아래 줄(Tailnet + 기기)
    if (inet && cf && inet.w + 30 + cf.w > W) return null;
    if (lan && lan.w + (lanP ? SAT + lanP.w : 0) > W) return null;
    if (tn && tn.w + (tnP ? SAT + tnP.w : 0) > W) return null;
    if (rowW(topPorts) + 32 > W || rowW(botPorts) + 32 > W) return null;

    // ---- 세로 위치
    const y1 = 4;
    const y2 = y1 + PH + GAP_Y;
    const boxTop = y2 + PH + GAP_Y + 2;
    // ---- 기기 상자 안: 머리 · Docker(네트워크 칩) · 호스트 프로세스
    const headY = boxTop + 28;
    const inTop = boxTop + 46;
    const IN_PAD = 12;
    const procW = Math.min(250, Math.max(small ? 0 : 170, 26 + M.nProc * 10));
    const stackProcs = small || boxW < 560;
    const dockX = boxX + IN_PAD;
    const dockW = stackProcs ? boxW - IN_PAD * 2 : boxW - IN_PAD * 3 - procW;
    const CH = 24; const CG = 6;
    const maxChips = small ? 5 : 99;
    let chips = M.chips.slice();
    if (chips.length > maxChips) {
      // 줄여도 터널 칩(맨 뒤)은 남긴다
      const tun = chips.filter((c) => c.tunnel); const rest0 = chips.filter((c) => !c.tunnel);
      const keep = rest0.slice(0, maxChips - 1 - tun.length); const rest = rest0.slice(keep.length);
      chips = keep.concat(rest.length ? [{ id: "more", label: "+" + rest.length, n: null, status: worstOf(rest.map((c) => c.status)), more: true }] : [], tun);
    }
    const chipTxt = (c) => (c.nt != null ? String(c.nt) : c.n != null ? String(c.n) : null);
    const chipW = (c) => Math.ceil(10 + 12 + tw(c.label, 12, 600) + (chipTxt(c) != null ? 6 + tw(chipTxt(c), 11.5, 700) : 0) + 10);
    // 터널 통로: Docker 상자 오른쪽 틈(프로세스가 옆에 있으면 그 사이, 아래로 쌓였으면 상자 안 오른쪽 끝)
    const tunnel = !!(M.tunnel && M.cf && M.hasTunnelChip);
    const laneX = stackProcs ? boxX + boxW - 12 : dockX + dockW + IN_PAD / 2;
    let cx = dockX + 12; let cy = inTop + 30; let rows = 1;
    const chipLimit = dockX + dockW - 12 - (tunnel && stackProcs ? 14 : 0);
    const chipPos = chips.map((c) => {
      const w = Math.min(chipW(c), dockW - 24);
      if (cx + w > chipLimit && cx > dockX + 12) { cx = dockX + 12; cy += CH + CG; rows++; }
      const o = { c, x: cx, y: cy, w };
      cx += w + CG;
      return o;
    });
    const dockH = chips.length ? 30 + rows * CH + (rows - 1) * CG + 12 : 40;
    const procX = stackProcs ? dockX : dockX + dockW + IN_PAD;
    const procTop = stackProcs ? inTop + dockH + 10 : inTop;
    const procBW = stackProcs ? dockW : procW;
    const perRow = Math.max(1, Math.floor((procBW - 24) / 10));
    const procRows = Math.max(1, Math.ceil(M.nProc / perRow));
    const procH = 30 + procRows * 10 + 10;
    const boxH = (stackProcs ? procTop + procH : inTop + Math.max(dockH, procH)) - boxTop + 12;
    const boxBot = boxTop + boxH;
    const y3 = boxBot + GAP_Y;                         // Tailnet 줄
    const H = Math.ceil((tn ? y3 + PH : boxBot) + 4);

    // ---- 가로 위치
    // 둘째 줄: LAN 은 왼쪽, 같은 LAN 기기는 그 오른쪽 · 위 포트는 LAN 아래(터널 통로를 비켜서)
    if (lan) { lan.x = 0; lan.y = y2; }
    if (lanP) { lanP.x = lan.x + lan.w + SAT; lanP.y = y2 + (PH - 26) / 2; }
    let px = 16;
    const topPos = topPorts.map((p) => { const w = pw(p); const o = { p, x: px, w }; px += w + PG; return o; });
    // 윗줄: 인터넷은 LAN 위, Cloudflare 는 그 오른쪽(터널 통로 위로 맞춘다)
    if (inet) { inet.x = lan ? Math.max(0, lan.x + Math.min(lan.w, 160) / 2 - inet.w / 2) : 0; inet.y = y1; }
    if (cf) {
      const minX = inet ? inet.x + inet.w + 30 : 0;
      const want = tunnel ? laneX - cf.w / 2 : minX;
      cf.x = Math.max(minX, Math.min(W - cf.w - 1, want)); cf.y = y1;
    }
    const laneTop = cf ? cf.y + PH : y1;
    // 위 포트가 터널 통로에 걸리면 통로 왼쪽으로 몰아넣는다(안 되면 이 단계는 포기)
    if (tunnel && topPos.length) {
      const last = topPos[topPos.length - 1];
      if (last.x + last.w > laneX - 10 && topPos[0].x < laneX + 10) return null;
    }
    // 아래: tailscale0 은 아래 테두리 오른쪽, Tailnet 알약은 그 아래(가운데 맞춤)
    let bx = boxX + boxW - 24 - rowW(botPorts);
    const botPos = botPorts.map((p) => { const w = pw(p); const o = { p, x: bx, w }; bx += w + PG; return o; });
    if (tn) {
      const pc = botPos.length ? botPos[0].x + botPos[0].w / 2 : boxX + boxW / 2;
      const blockW = tn.w + (tnP ? SAT + tnP.w : 0);
      tn.x = Math.max(0, Math.min(W - blockW - 1, pc - tn.w / 2));
      if (tnP && tn.x + tn.w + SAT + tnP.w > W - 1) tn.x = Math.max(0, W - 1 - blockW);
      tn.y = y3;
    }
    if (tnP) { tnP.x = tn.x + tn.w + SAT; tnP.y = y3 + (PH - 26) / 2; }

    // ---- 그리기
    const edges = []; const labels = []; const nodes = [];
    const cyOf = (p, h) => p.y + (h || PH) / 2;
    if (cf && inet) edges.push(html`<path class="mm-e" data-k="public" d="${"M" + r1(inet.x + inet.w) + " " + r1(cyOf(inet)) + "H" + r1(cf.x)}"/>`);
    if (lan && inet) {
      edges.push(html`<path class="mm-e" data-k="uplink" d="${vcurve(inet.x + inet.w / 2, y1 + PH, lan.x + Math.min(lan.w, 160) / 2, y2)}"/>`);
      if (M.uplink && lv < 2) labels.push(edgeLabelSvg(M.uplink, inet.x + inet.w / 2 + 8, (y1 + PH + y2) / 2, "uplink", "start"));
    }
    if (lan && lanP) edges.push(html`<path class="mm-e" data-k="peer-lan" d="${"M" + r1(lan.x + lan.w) + " " + r1(cyOf(lan)) + "H" + r1(lanP.x)}"/>`);
    if (tn && tnP) edges.push(html`<path class="mm-e" data-k="peer-tailnet" d="${"M" + r1(tn.x + tn.w) + " " + r1(cyOf(tn)) + "H" + r1(tnP.x)}"/>`);
    const portTopY = boxTop - PORT_H / 2;
    const portBotY = boxBot - PORT_H / 2;
    topPos.forEach((o) => {
      if (!lan) return;
      const k = o.p.ek === "wifi" ? "wifi" : "lan";
      const x1 = o.x + o.w / 2;
      edges.push(html`<path class="mm-e" data-k="${k}" d="${vcurve(x1, portTopY, Math.max(lan.x + 20, Math.min(lan.x + lan.w - 20, x1)), y2 + PH)}"/>`);
    });
    botPos.forEach((o) => {
      if (!tn) return;
      const x1 = o.x + o.w / 2;
      edges.push(html`<path class="mm-e" data-k="tailnet" d="${vcurve(x1, portBotY + PORT_H, Math.max(tn.x + 20, Math.min(tn.x + tn.w - 20, x1)), y3)}"/>`);
    });
    // 터널: 맨 뒤 칩(터널 컨테이너)에서 오른쪽 통로로 나가 곧게 위의 Cloudflare 엣지로
    if (tunnel && chipPos.length) {
      const c0 = chipPos.find((o) => o.c.tunnel) || chipPos[chipPos.length - 1];
      const ty = c0.y + CH / 2;
      const q = 8;
      edges.push(html`<path class="mm-e" data-k="tunnel" d="${"M" + r1(c0.x + c0.w) + " " + r1(ty) + "H" + r1(laneX - q) + "Q" + r1(laneX) + " " + r1(ty) + " " + r1(laneX) + " " + r1(ty - q) + "V" + r1(laneTop)}"/>`);
      if (lv < 2) {
        // 오른쪽 끝에 닿으면 통로 왼쪽에 붙인다(잘리지 않게)
        const lw = tw(M.tunnel.label, 10, 500, true) + 10;
        labels.push(edgeLabelSvg(M.tunnel.label, laneX + 6 + lw > W - 2 ? laneX - 6 - lw : laneX + 6, (y1 + PH + y2) / 2, "tunnel", "start"));
      }
    }
    // 노드: 바깥 · 구간 · 기기 묶음
    if (inet) nodes.push(pillSvg({ x: inet.x, y: inet.y, w: inet.w, h: PH, label: inet.label, icon: inet.icon, tone: "ok", cls: "mm-zone", small }));
    if (cf) nodes.push(pillSvg({ x: cf.x, y: cf.y, w: cf.w, h: PH, label: cf.label, detail: cf.detail, tone: cf.tone, cls: "mm-hop", small }));
    if (lan) nodes.push(pillSvg({ x: lan.x, y: lan.y, w: lan.w, h: PH, label: lan.label, detail: lan.detail, icon: lan.icon, tone: lan.tone, cls: "mm-seg", k: "lan", small }));
    if (tn) nodes.push(pillSvg({ x: tn.x, y: tn.y, w: tn.w, h: PH, label: tn.label, detail: tn.detail, icon: tn.icon, tone: tn.tone, cls: "mm-seg", k: "tailnet", small }));
    if (lanP) nodes.push(pillSvg({ x: lanP.x, y: lanP.y, w: lanP.w, h: 26, label: lanP.label, icon: lanP.icon, cls: "mm-peer", small: true }));
    if (tnP) nodes.push(pillSvg({ x: tnP.x, y: tnP.y, w: tnP.w, h: 26, label: tnP.label, detail: tnP.detail, icon: tnP.icon, cls: "mm-peer", small: true }));
    // 기기 상자(실선 · 중립색 이름표). 상태는 이름 뒤 기호 + 말(헤더와 같은 어휘) + 까닭
    const devTone = P.tone(M.dev.status);
    const box = [];
    box.push(html`<rect class="mm-dev" x="${boxX}" y="${boxTop}" width="${r1(boxW)}" height="${r1(boxH)}" rx="16"/>`);
    const hx = boxX + 14;
    box.push(html`<g class="mm-ic mm-dev-ic" transform="${"translate(" + hx + " " + r1(headY - 7) + ") scale(.5833)"}">${raw(PIC.chip)}</g>`);
    box.push(html`<text class="mm-l mm-dev-l" x="${hx + 20}" y="${headY}" dominant-baseline="central">${M.dev.label}</text>`);
    let hx2 = hx + 20 + tw(M.dev.label, 14, 750) + 9;
    if (devTone !== "ok" && devTone !== "muted") {
      box.push(shape(devTone, hx2, headY, 4.2));
      const word = ((SH.DEV_STATUS || {})[M.dev.status] || P.labels.status[M.dev.status] || "") + (M.brief && M.brief.text && lv < 2 ? " · " + M.brief.text : "");
      box.push(html`<text class="mm-stw" data-tone="${devTone}" x="${r1(hx2 + 8)}" y="${headY}" dominant-baseline="central">${word}</text>`);
      hx2 += 8 + tw(word, 12, 700) + 10;
    }
    if (M.dev.detail && lv < 1 && hx2 + tw(M.dev.detail, 11, 500) < boxX + boxW - 12) box.push(html`<text class="mm-d" x="${r1(hx2)}" y="${headY}" dominant-baseline="central">${M.dev.detail}</text>`);
    // Docker 상자 + 네트워크 칩
    box.push(html`<rect class="mm-in" x="${dockX}" y="${inTop}" width="${r1(dockW)}" height="${r1(dockH)}" rx="11"/>`);
    box.push(html`<g class="mm-ic" transform="${"translate(" + (dockX + 11) + " " + (inTop + 9) + ") scale(.5)"}">${raw(PIC.docker)}</g>`);
    box.push(html`<text class="mm-l mm-in-l" x="${dockX + 30}" y="${inTop + 15}" dominant-baseline="central">Docker</text>`);
    const dkw = tw("Docker", 12, 700);
    box.push(html`<text class="mm-d" x="${r1(dockX + 30 + dkw + 7)}" y="${inTop + 15}" dominant-baseline="central">${"컨테이너 " + fmt.num(M.nCtr) + " · 도커 네트워크 " + fmt.num(M.nNet)}</text>`);
    chipPos.forEach((o) => {
      const c = o.c;
      const t = P.tone(c.status);
      const lw = tw(c.label, 12, 600);
      const ct = chipTxt(c);
      box.push(html`<g class="${"mm-chip" + (c.tunnel ? " tun" : "") + (c.more ? " more" : "")}" data-tone="${t}" transform="${"translate(" + r1(o.x) + " " + r1(o.y) + ")"}"><rect width="${o.w}" height="${CH}" rx="7"/>${shape(t, 11, CH / 2, 3.2)}<text class="mm-cl" x="20" y="${CH / 2}" dominant-baseline="central">${c.label}</text>${ct != null ? html`<text class="mm-c" x="${r1(20 + lw + 6)}" y="${CH / 2}" dominant-baseline="central">${ct}</text>` : ""}</g>`);
    });
    // 호스트 프로세스(점 = 상태)
    box.push(html`<rect class="mm-in mm-procs" x="${procX}" y="${procTop}" width="${r1(procBW)}" height="${r1(procH)}" rx="11"/>`);
    box.push(html`<text class="mm-l mm-in-l" x="${procX + 12}" y="${procTop + 15}" dominant-baseline="central">호스트 프로세스</text>`);
    const pl = tw("호스트 프로세스", 12, 700);
    box.push(html`<text class="mm-c" x="${r1(procX + 12 + pl + 6)}" y="${procTop + 15}" dominant-baseline="central">${fmt.num(M.nProc)}</text>`);
    M.procs.slice().sort((a, b) => (RANK[b] || 0) - (RANK[a] || 0)).forEach((st, i) => {
      const r = Math.floor(i / perRow); const c = i % perRow;
      box.push(shape(P.tone(st), procX + 16 + c * 10, procTop + 32 + r * 10, 3.2));
    });
    // 포트(위·아래 테두리에 걸침)
    const ports = [];
    topPos.forEach((o) => ports.push(pillSvg({ x: o.x, y: portTopY, w: o.w, h: PORT_H, label: o.p.label, detail: lv === 0 ? o.p.detail : null, tone: P.tone(o.p.status), cls: "mm-port", k: o.p.seg ? (o.p.ek === "wifi" ? "wifi" : "lan") : "other", small: true, mono: true })));
    botPos.forEach((o) => ports.push(pillSvg({ x: o.x, y: portBotY, w: o.w, h: PORT_H, label: o.p.label, detail: lv === 0 ? o.p.detail : null, tone: P.tone(o.p.status), cls: "mm-port", k: "tailnet", small: true, mono: true })));
    return { W, H, lv, svg: html`<svg class="mm mm-phys" data-lv="${lv}" viewBox="${"0 0 " + W + " " + H}" width="${W}" height="${H}" aria-hidden="true" focusable="false"><g class="mm-box-g" data-tone="${devTone}">${box}</g><g class="mm-edges">${edges}</g><g class="mm-nodes">${nodes}${ports}</g><g class="mm-els">${labels}</g></svg>` };
  }
  function drawPhys(box, s) {
    const W = Math.floor(box.clientWidth);
    if (!W) return;
    let M = null;
    try { M = physModel(s); } catch (e) { console.error("[home-map] 물리 모형 실패:", e); }
    if (!M) { P.render(box, html`<p class="mm-empty">물리 구성 데이터를 기다리는 중이에요.</p>`); return; }
    let L = null;
    for (let lv = 0; lv <= 2 && !L; lv++) L = physLayout(M, W, lv);
    if (!L) L = physLayout(M, Math.max(W, 300), 2);
    P.render(box, L ? L.svg : html`<p class="mm-empty">화면이 좁아 물리 구성을 그리지 못했어요.</p>`);
  }

  // ------------------------------------------------------------------ 그리기 + 크기 변화 추적
  function draw(box) {
    const s = box._jarvisSnap;
    const W = Math.floor(box.clientWidth);
    if (!s || !W) return;
    if (box._jarvisView === "physical") { drawPhys(box, s); return; }
    let M = null;
    try { M = build(s); } catch (e) { console.error("[home-map] 모형 실패:", e); }
    if (!M) { P.render(box, html`<p class="mm-empty">지도 데이터를 기다리는 중이에요.</p>`); return; }
    // 0단계에서 안 맞으면 보조 글자를 긴 것부터 하나씩 뺀다 → 그래도 안 되면 1단계 → 좁은 배치
    let L = wideLayout(M, W, 0);
    if (!L) {
      const drop = new Set();
      const withDet = Array.from(M.V.values()).filter((v) => v.detail).sort((a, b) => tw(b.detail, 11, 500) - tw(a.detail, 11, 500));
      for (const v of withDet) { drop.add(v.id); L = wideLayout(M, W, 0, drop); if (L) break; }
    }
    if (!L) L = wideLayout(M, W, 1);
    P.render(box, L ? drawWide(M, L) : drawNarrow(M, narrowLayout(M, W)));
  }
  function render(box, snap, view) {
    if (!box || !snap) return;
    box._jarvisSnap = snap;
    box._jarvisView = view === "physical" ? "physical" : "conceptual";
    draw(box);
    if (box._jarvisRo || !window.ResizeObserver) return;
    let rq = 0; let lastW = box.clientWidth;
    box._jarvisRo = new ResizeObserver(() => {
      if (rq || box.clientWidth === lastW) return;
      rq = requestAnimationFrame(() => { rq = 0; lastW = box.clientWidth; draw(box); });
    });
    box._jarvisRo.observe(box);
    // 웹폰트가 늦게 오면 글자 폭이 바뀐다 → 한 번 다시 잰다
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { fam = null; draw(box); }).catch(() => {});
  }

  /** 빠른 숫자 — 지도와 같은 정의(가장 넓게 열린 범위). 문제 = 저하 + 다운 서비스 */
  function stats(s) {
    const svcs = s.services || [];
    const c = SH.expCounts ? SH.expCounts(s) : { public: 0, lan: 0, tailnet: 0, local: 0 };
    const deg = svcs.filter((x) => x.status === "degraded").length;
    const down = svcs.filter((x) => x.status === "down").length;
    return Object.assign({}, c, { deg, down, prob: deg + down });
  }

  window.JarvisHomeMap = { render, stats, physStats };
})();
