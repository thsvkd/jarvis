/* Jarvis — 네트워크 맵의 물리 보기(기기·네트워크). /map/?view=physical
 * "어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가"를 스냅샷의 topology_physical 로 그린다.
 *
 *   맨 위      인터넷 ─ Cloudflare 엣지                (업링크 NAT 로 LAN 에, 터널은 이 기기 안의 cloudflared 에서)
 *   LAN 버스   ════════════════════════                (eth0·wlan0 이 위 테두리에서 내려와 붙는다)
 *   가운데     [이 기기 상자: 테두리에 인터페이스, 안에 Docker 엔진 ⊃ 도커 네트워크 ⊃ 컨테이너 · 호스트 프로세스]
 *              오른쪽에 같은 LAN 기기(빗살 배선 — 왼쪽은 LAN, 오른쪽은 Tailnet). 같은 기기 안의 다른 노드는 그 카드 안에.
 *   Tailnet 버스 ════════════════════════               (tailscale0 이 아래 테두리에서 내려와 붙는다)
 *   맨 아래    Tailnet 에만 있는 기기 · 오프라인 N (접힌 묶음, 펼칠 수 있다)
 *
 * 이 파일은 모형·배치·SVG·검사기 조각·모바일 나무만 만든다. 이벤트·확대/이동·선택·주소·3초 갱신은 atlas.js 가
 * 개념 보기와 같은 틀로 맡는다(atlas.js 가 JarvisMapPhys.init(도구) 로 공용 조각을 넘긴다).
 * 배치는 결정적이다 — 구조(노드·부모·엣지 종류)와 이름만으로 정해져, 상태·숫자가 바뀌어도 자리가 흔들리지 않는다.
 * 안전: 데이터로 만드는 HTML·SVG 는 JARVIS.html(자동 이스케이프)만 쓴다. 아이콘 path 는 이 파일의 상수뿐이다.
 */
(function () {
  "use strict";
  let h = null; // atlas.js 가 넘기는 공용 도구

  // ------------------------------------------------------------------ 치수(월드 좌표, px)
  // 글자: 개념 지도보다 한 단계 크게. 기본 맞춤은 0.88 아래로 내려가지 않는다(atlas.js — 넘치면 기기 상자를 먼저 담는다) → 이름 ≥ 11.4px · 보조 ≥ 10px
  const FS = { chip: 13, meta: 11.5, label: 11 };
  const CW = 212, CH = 26, CP = 30;                 // 칩(컨테이너·프로세스)
  const GH = 20, GP = 24;                            // 다른 망에서 붙은 컨테이너(유령 줄)
  const NBW = CW + 16, NBH = 40, NBP = 8, NBG = 12;  // 도커 네트워크·묶음 상자(머리 두 줄)
  const EH = 30, EP = 12, CG = 16, CGB = 44;         // 엔진 머리·여백·열 간격(선이 지나는 곳은 넓게)
  const DX = 120, DP = 18, DTOP = 38, DBOT = 38;     // 기기 상자
  const PW = 178, PH = 40, TW = 256, TH = 40;        // 테두리의 인터페이스 · 기기 제목 칩
  const CARDW = 204, CARDH = 46, KIDH = 42;          // 원격 기기 카드 · 같은 기기 안의 노드
  const SEGW = 250, SEGH = 42, EXTW = 206, EXTH = 44; // 구간 알약 · 외부(인터넷 보조 줄 "공개 요청 1시간 99,999건"이 잘리지 않는 폭)
  const ROWH = 24, ROWP = 28;                         // 오프라인 묶음의 줄
  const GAPX = 28;
  const PROC_2COL = 7;                                // 호스트 프로세스가 이보다 많으면 두 칸
  const TN_KINDS = new Set(["overlay", "tailnet-direct", "tailnet-relay", "tailnet-idle", "offline"]);
  const TN_PEER = new Set(["tailnet-direct", "tailnet-relay", "tailnet-idle"]);
  const IF_RANK = { wired: 0, wifi: 1, tailnet: 2, docker: 3, other: 4 };
  const f1 = (v) => Math.round(v * 10) / 10;

  const TYPE_L = {
    external: "외부", segment: "네트워크 구간", device: "기기", iface: "인터페이스", engine: "Docker 엔진", network: "도커 네트워크",
    container: "컨테이너", process: "호스트 프로세스", peer: "Tailnet 기기", group: "묶음",
  };
  const ROLE_L = { hostnet: "host 네트워크", nonet: "네트워크 없음", procs: "호스트 프로세스", offline: "오프라인 기기" };
  const KIND_L = {
    wired: "유선", wifi: "무선(Wi-Fi)", overlay: "오버레이", uplink: "업링크(NAT)", edge: "엣지", tunnel: "터널", bridge: "다른 망에도 붙음",
    bind: "바인딩", "tailnet-direct": "Tailnet 직결", "tailnet-relay": "Tailnet 중계(DERP)", "tailnet-idle": "Tailnet 유휴", offline: "오프라인", lan: "같은 LAN",
  };
  const IFK_L = { wired: "유선", wifi: "무선", tailnet: "Tailnet", docker: "Docker", other: "기타" };

  // 18×18 아이콘(상수 SVG 조각 — 데이터 없음)
  const PI = {
    linux: '<rect x="2.5" y="2.5" width="13" height="5.5" rx="1.4"/><rect x="2.5" y="10" width="13" height="5.5" rx="1.4"/><path d="M5.2 5.25h.01M5.2 12.75h.01"/>',
    mac: '<rect x="3.5" y="3.5" width="11" height="8" rx="1.3"/><path d="M1.5 14.5h15"/>',
    win: '<rect x="2" y="2.5" width="14" height="9.5" rx="1.4"/><path d="M6.5 15.5h5M9 12v3.5"/>',
    phone: '<rect x="5" y="1.8" width="8" height="14.4" rx="2"/><path d="M8 13.6h2"/>',
    tablet: '<rect x="3" y="2" width="12" height="14" rx="2"/><path d="M8 13.6h2"/>',
    eth: '<rect x="2.5" y="3.5" width="13" height="11" rx="1.8"/><path d="M6 14.5v-3.2h6v3.2M6.2 7h.01M9 7h.01M11.8 7h.01"/>',
    wifi: '<path d="M1.8 7.2a10.2 10.2 0 0 1 14.4 0M4.4 9.8a6.4 6.4 0 0 1 9.2 0M7 12.4a2.8 2.8 0 0 1 4 0"/><circle cx="9" cy="14.9" r=".8"/>',
    board: '<rect x="3.5" y="3.5" width="11" height="11" rx="2"/><rect x="6.5" y="6.5" width="5" height="5" rx="1"/><path d="M6.5 1.5v2M11.5 1.5v2M6.5 14.5v2M11.5 14.5v2M1.5 6.5h2M1.5 11.5h2M14.5 6.5h2M14.5 11.5h2"/>',
    engine: '<rect x="2" y="9.5" width="4.2" height="4.2" rx=".6"/><rect x="6.9" y="9.5" width="4.2" height="4.2" rx=".6"/><rect x="11.8" y="9.5" width="4.2" height="4.2" rx=".6"/><rect x="4.4" y="4.3" width="4.2" height="4.2" rx=".6"/><rect x="9.3" y="4.3" width="4.2" height="4.2" rx=".6"/><path d="M1.5 16h15"/>',
    net: '<circle cx="9" cy="3.8" r="2"/><circle cx="3.8" cy="14" r="2"/><circle cx="14.2" cy="14" r="2"/><path d="M9 5.8v3.4M9 9.2 5.2 12.4M9 9.2l3.8 3.2"/>',
    procs: '<rect x="1.5" y="3" width="15" height="12" rx="2"/><path d="M4.6 7l2.4 2-2.4 2M9 11.4h4.2"/>',
    hostnet: '<rect x="1.5" y="2.5" width="15" height="13" rx="2"/><path d="M1.5 7h15M5 11h.01M8 11h.01M11 11h2.5"/>',
    off: '<circle cx="9" cy="9" r="6.6" stroke-dasharray="2.2 2.2"/><path d="M6.6 6.6l4.8 4.8M11.4 6.6l-4.8 4.8"/>',
  };
  const osIcon = (n) => {
    const o = String(n.os || "").toLowerCase(); const lb = String(n.label || "").toLowerCase();
    if (/ipad/.test(lb) || (/ios/.test(o) && /tab|pad/.test(lb))) return PI.tablet;
    if (/android|ios|iphone/.test(o)) return PI.phone;
    if (/mac|darwin/.test(o)) return PI.mac;
    if (/windows/.test(o)) return PI.win;
    return PI.linux;
  };
  const ifIcon = (n) => (n.kind === "wifi" ? PI.wifi : n.kind === "tailnet" ? h.NICON.mesh : PI.eth);

  // ------------------------------------------------------------------ 모형
  /** topology_physical → { nodes, edges, kids, inE, outE, bySvc, self, segLan, segTn, ... }.
   *  묶음(합성, type "group"): 엔진 바로 아래 host 네트워크 컨테이너 → grp:hostnet · 기기의 프로세스 → grp:procs ·
   *  맨 위의 오프라인 피어 → grp:offline(엣지도 하나로 합친다). 원래 부모는 parent0 에 남는다. */
  function derive(s) {
    const tp = (s && s.topology_physical) || {};
    const svcById = new Map(((s && s.services) || []).map((x) => [x.id, x]));
    const nodes = new Map();
    for (const n0 of tp.nodes || []) {
      if (!n0 || !n0.id || nodes.has(n0.id)) continue;
      const n = Object.assign({}, n0);
      n.service_ids = Array.isArray(n.service_ids) ? n.service_ids.filter((x) => typeof x === "string") : [];
      n.svcs = n.service_ids.map((id) => svcById.get(id)).filter(Boolean);
      n.parent0 = n.parent || null;
      nodes.set(n.id, n);
    }
    for (const n of nodes.values()) if (n.parent && (!nodes.has(n.parent) || n.parent === n.id)) n.parent = null;
    for (const n of nodes.values()) { // 부모 고리 끊기(방어)
      let p = n.parent; let guard = 0;
      while (p && guard++ < 16) { if (p === n.id) { n.parent = null; break; } const q = nodes.get(p); p = q ? q.parent : null; }
    }
    const all = () => [...nodes.values()];
    const self = all().find((n) => n.type === "device" && n.self) || all().find((n) => n.type === "device" && !n.parent) || null;
    const mkGroup = (id, label, parent, role, extra) => {
      const g = Object.assign({ id, label, type: "group", parent, status: "up", role, service_ids: [], svcs: [], synthetic: true }, extra || {});
      nodes.set(id, g);
      return g;
    };
    const engines = all().filter((n) => n.type === "engine");
    for (const eng of engines) {
      const direct = all().filter((n) => n.parent === eng.id && n.type === "container");
      const suf = engines.length > 1 ? ":" + eng.id : "";
      const hostC = direct.filter((n) => n.host_network);
      const other = direct.filter((n) => !n.host_network);
      if (hostC.length) { const g = mkGroup("grp:hostnet" + suf, "host 네트워크", eng.id, "hostnet"); hostC.forEach((n) => { n.parent = g.id; }); }
      if (other.length) { const g = mkGroup("grp:nonet" + suf, "네트워크 없음", eng.id, "nonet"); other.forEach((n) => { n.parent = g.id; }); }
    }
    if (self) {
      const hk = all().filter((n) => n.parent === self.id && !["iface", "engine", "group"].includes(n.type));
      if (hk.length) { const g = mkGroup("grp:procs", "호스트 프로세스", self.id, "procs"); hk.forEach((n) => { n.parent = g.id; }); }
    }
    const hasKids = new Set(all().map((n) => n.parent).filter(Boolean));
    const off = all().filter((n) => n.type === "peer" && !n.parent && n.online === false && !hasKids.has(n.id));
    if (off.length) { const g = mkGroup("grp:offline", "오프라인 기기", null, "offline", { status: "unknown" }); off.forEach((n) => { n.parent = g.id; }); }
    const offIds = new Set(off.map((n) => n.id));

    const edges = []; const byKey = new Map();
    // Tailnet 피어 선은 종류(직결·중계·유휴)가 3초마다 바뀔 수 있어 키에서 종류를 뺀다 → 바뀌어도 다시 배치하지 않고 제자리에서 모양만 바꾼다
    const add = (e) => {
      const key = e.from + "→" + e.to + "·" + (TN_PEER.has(e.kind) ? "tn" : e.kind);
      const x = byKey.get(key);
      if (x) { if (e.label && !String(x.label || "").split(" ").includes(e.label)) x.label = x.label ? x.label + " " + e.label : e.label; return; }
      const ne = Object.assign({}, e, { key });
      byKey.set(key, ne); edges.push(ne);
    };
    // 같은 LAN 기기: 공용 판정(JarvisShell.lanPeers — 백엔드의 on_lan·via_gateway 를 쓰고, 한 번 튀는 변화만 누른다).
    // 맨 위 기기만 판정한다 — 같은 기기 안의 노드(same_device)는 백엔드 값을 그대로 둔다.
    const LP = SHs().lanPeers && s ? SHs().lanPeers(s) : null;
    for (const n of nodes.values()) {
      if (n.type !== "peer") continue;
      n.via_gw = !!n.via_gateway || !!(LP && LP.viaGw.has(n.id));
      const kid = !!(n.parent && nodes.has(n.parent) && nodes.get(n.parent).type === "peer");
      if (!LP || kid) continue;
      const x = LP.peers.get(n.id);
      n.on_lan = !!x;
      n.lan_stale = !!(x && x.stale);
      n.lan_last = x ? x.last : null;
      if (x && x.ip) n.lan_ip = x.ip;
    }
    // 피어 → Tailnet 선의 종류는 백엔드가 정한다(tailnet-direct · tailnet-relay · tailnet-idle · offline).
    // 종류가 없는 옛 스냅샷만 피어의 connection 으로 정한다(peer.relay 는 홈 DERP 지역일 뿐 지금 중계 중이라는 뜻이 아니다).
    const tnEdge = (p, e) => {
      let k = TN_PEER.has(e.kind) || e.kind === "offline" ? e.kind : null;
      if (!k) { const c = SHs().peerConn ? SHs().peerConn(p) : p.online === false ? "offline" : p.connection || "idle"; k = c === "offline" ? "offline" : "tailnet-" + c; }
      if (k === "tailnet-direct") return { kind: k, label: "직결" };
      if (k === "tailnet-relay") return { kind: k, label: "중계" + (p.relay ? " · DERP " + p.relay : "") };
      if (k === "offline") return { kind: k, label: e.label || null };
      return { kind: "tailnet-idle", label: "유휴" };
    };
    let offEdge = null;
    for (const e of tp.edges || []) {
      if (!e || !nodes.has(e.from) || !nodes.has(e.to) || e.from === e.to) continue;
      let kind = String(e.kind || "link");
      let label = e.label || null;
      if (offIds.has(e.from) || offIds.has(e.to)) {
        if (kind === "offline" || TN_KINDS.has(kind)) {
          if (!offEdge) offEdge = { from: "grp:offline", to: offIds.has(e.from) ? e.to : e.from, kind: "offline", label: null, n: 0 };
          offEdge.n++;
        }
        continue;
      }
      if (kind === "lan" && LP) continue; // 아래에서 안정된 목록으로 다시 만든다
      if (TN_PEER.has(kind)) {
        const p = nodes.get(e.from).type === "peer" ? nodes.get(e.from) : nodes.get(e.to).type === "peer" ? nodes.get(e.to) : null;
        if (p) { const t = tnEdge(p, e); kind = t.kind; label = t.label; }
      }
      add({ from: e.from, to: e.to, kind, label });
    }
    if (LP) {
      for (const [pid, x] of LP.peers) {
        const seg = x.seg && nodes.has(x.seg) ? x.seg : null;
        if (!nodes.has(pid) || !seg || offIds.has(pid)) continue;
        add({ from: pid, to: seg, kind: "lan", label: x.ip || null, stale: !!x.stale });
      }
    }
    if (offEdge) add({ from: offEdge.from, to: offEdge.to, kind: "offline", label: offEdge.n + "대", synthetic: true });

    const kidMap = new Map();
    for (const n of nodes.values()) if (n.parent) { if (!kidMap.has(n.parent)) kidMap.set(n.parent, []); kidMap.get(n.parent).push(n.id); }
    const inE = new Map(); const outE = new Map();
    for (const e of edges) {
      if (!inE.has(e.to)) inE.set(e.to, []);
      if (!outE.has(e.from)) outE.set(e.from, []);
      inE.get(e.to).push(e); outE.get(e.from).push(e);
    }
    const RANK = { container: 0, process: 0, peer: 1, engine: 2, device: 3 };
    const bySvc = new Map();
    for (const n of nodes.values()) for (const sid of n.service_ids) { if (!bySvc.has(sid)) bySvc.set(sid, []); bySvc.get(sid).push(n.id); }
    for (const l of bySvc.values()) l.sort((a, b) => (RANK[nodes.get(a).type] ?? 5) - (RANK[nodes.get(b).type] ?? 5) || a.localeCompare(b));
    const segs = all().filter((n) => n.type === "segment");
    const touches = (sg, pred) => edges.some((e) => (e.to === sg.id || e.from === sg.id) && pred(e));
    const segTn = segs.find((sg) => /tailnet/i.test(sg.id) || touches(sg, (e) => TN_KINDS.has(e.kind))) || null;
    const segLan = segs.find((sg) => sg !== segTn && (/lan/i.test(sg.id) || touches(sg, (e) => ["wired", "wifi", "lan", "uplink"].includes(e.kind)))) || segs.find((sg) => sg !== segTn) || null;
    const cats = ((s && s.categories) || []).map((c) => c.id);
    const PD = {
      s, nodes, edges, byKey, inE, outE, bySvc, self, segLan, segTn, cats,
      kidIds: (id) => kidMap.get(id) || [],
      kids: (id) => (kidMap.get(id) || []).map((k) => nodes.get(k)).filter(Boolean),
    };
    PD.anc = (id) => { const out = []; let n = nodes.get(id); let g = 0; while (n && n.parent && g++ < 16) { out.push(n.parent); n = nodes.get(n.parent); } return out; };
    PD.desc = (id) => { const out = []; const st = [id]; let g = 0; while (st.length && g++ < 2000) { const x = st.pop(); for (const k of PD.kidIds(x)) { out.push(k); st.push(k); } } return out; };
    PD.edgesOf = (id) => (inE.get(id) || []).concat(outE.get(id) || []);
    // 같은 LAN 기기 수(한 가지 정의 — JarvisShell.lanPeers.count 와 같다): 맨 위 기기의 lan 선 중 지금 보이는 것 · 잠시 안 보임
    PD.lan = { n: 0, held: 0, ids: [] };
    for (const e of edges) {
      if (e.kind !== "lan") continue;
      const p = nodes.get(e.from).type === "peer" ? nodes.get(e.from) : nodes.get(e.to);
      if (!p || p.type !== "peer" || (p.parent && nodes.get(p.parent) && nodes.get(p.parent).type === "peer")) continue;
      if (e.stale) PD.lan.held++; else PD.lan.n++;
      PD.lan.ids.push(p.id);
    }
    return PD;
  }
  /** "같은 LAN 기기 2" (+ " · 잠시 안 보임 1") — 요약 칸·열 제목·LAN 구간이 같은 글자 */
  const lanText = (PD, short) => "같은 LAN 기기 " + PD.lan.n + (PD.lan.held ? (short ? " (+" + PD.lan.held + " 잠시 안 보임)" : " · 잠시 안 보임 " + PD.lan.held) : "");

  /** 화면 이름(노드 라벨 그대로 — 인터넷·Cloudflare 엣지·LAN 등은 백엔드가 한국어로 준다) */
  function name(PD, id) {
    const n = PD && PD.nodes.get(id);
    if (!n) return String(id || "");
    if (n.type === "group" && n.role === "offline") return "오프라인 기기";
    if (n.type === "segment" && PD.segTn === n) return "Tailnet";
    if (n.type === "engine" && !/엔진/.test(n.label || "")) return (n.label || "Docker") + " 엔진";
    return n.label || n.id;
  }
  const typeLabel = (n) => (n.source === "neighbor-cache" ? "LAN 기기" : n.type === "group" ? ROLE_L[n.role] || "묶음" : n.type === "device" && n.self ? "기기 · 이 기기" : TYPE_L[n.type] || n.type || "");
  const catOf = (n) => (n && n.svcs && n.svcs[0] ? n.svcs[0].category : "");

  // ------------------------------------------------------------------ 짧은 표기
  const speedT = (mb) => (mb == null || !isFinite(mb) ? "" : mb >= 1000 ? (mb % 1000 ? (mb / 1000).toFixed(1) : mb / 1000) + " Gbps" : mb + " Mbps");
  /** 칩 보조 줄의 포트 조각: [":443", ":80", "+2"] · ["lo:8080"] · host 네트워크면 서비스가 여는 포트 */
  function portList(PD, n) {
    if (n.type === "process") { const ps = procPorts(n); return ps.slice(0, 2).concat(ps.length > 2 ? ["+" + (ps.length - 2)] : []); }
    if (n.type !== "container") return [];
    const all = []; const lo = [];
    for (const p of n.ports || []) {
      const m = /^(.*):(\d+)\s*(?:→|->)\s*(\d+)/.exec(String(p));
      if (!m) continue;
      const ip = m[1]; const hp = m[2];
      if (/^127\.|^::1$|^\[::1\]$/.test(ip)) { if (!lo.includes(hp)) lo.push(hp); } else if (!all.includes(hp)) all.push(hp);
    }
    // 포트는 번호순(스냅샷마다 순서가 바뀌어 글자가 흔들리지 않게)
    const num = (a, b) => +a - +b;
    const cut = (l, pre) => { l = l.slice().sort(num); return l.slice(0, 2).map((p) => pre + p).concat(l.length > 2 ? ["+" + (l.length - 2)] : []); };
    if (all.length) return cut(all, ":");
    if (lo.length) return cut(lo, "lo:");
    if (n.host_network) {
      const ps = [];
      for (const sv of n.svcs || []) for (const l of sv.listeners || []) if (l.scope !== "loopback" && !ps.includes(l.port)) ps.push(l.port);
      return cut(ps, ":");
    }
    return [];
  }
  const procPorts = (n) => String(n.detail || "").split(/\s+/).filter((x) => /^:\d+$/.test(x)).sort((a, b) => +a.slice(1) - +b.slice(1));
  /** 피어의 지금 경로: 직결 · 중계 · DERP x · 유휴(오가는 연결 없음) · 오프라인 */
  const connText = (n) => {
    const c = SHs().peerConn ? SHs().peerConn(n) : n.online === false ? "offline" : n.connection || "idle";
    return c === "offline" ? "오프라인" : c === "direct" ? "직결" : c === "relay" ? "중계" + (n.relay ? " · DERP " + n.relay : "") : "유휴";
  };
  /** 이 칩(컨테이너·프로세스)이 기기 밖으로 연 포트와 그 포트가 걸린 인터페이스 → { ifs: Set(iface id), ports: ["80","443"] }
   *  0.0.0.0/:: → 모든 인터페이스 · 특정 IP → 그 IP 의 인터페이스 · 루프백·도커 대역은 뺀다. host 네트워크·프로세스는 서비스 리스너의 도달 범위로. */
  function published(PD, n) {
    const ifs = PD.self ? PD.kids(PD.self.id).filter((k) => k.type === "iface") : [];
    const out = new Set(); const ports = [];
    const addP = (p) => { p = String(p); if (!ports.includes(p)) ports.push(p); };
    const addIf = (list) => list.forEach((i) => out.add(i.id));
    if (!n) return { ifs: out, ports };
    if (n.type === "container" && !n.host_network) {
      for (const p of n.ports || []) {
        const m = /^(.*):(\d+)\s*(?:→|->)/.exec(String(p));
        if (!m) continue;
        const ip = m[1].replace(/^\[|\]$/g, "");
        if (/^127\.|^::1$|^localhost$/i.test(ip)) continue;
        if (!ip || ip === "0.0.0.0" || ip === "::") { addIf(ifs); addP(m[2]); continue; }
        const hit = ifs.filter((i) => (i.ips || []).includes(ip));
        if (hit.length) { addIf(hit); addP(m[2]); }
      }
    } else if (n.type === "container" || n.type === "process") {
      for (const sv of n.svcs || []) for (const l of sv.listeners || []) {
        if (l.scope === "all") { addIf(ifs); addP(l.port); }
        else if (l.scope === "lan") { addIf(ifs.filter((i) => i.kind === "wired" || i.kind === "wifi" || (l.addrs || []).some((a) => (i.ips || []).includes(a)))); addP(l.port); }
        else if (l.scope === "tailnet") { addIf(ifs.filter((i) => i.kind === "tailnet")); addP(l.port); }
      }
    }
    ports.sort((a, b) => +a - +b);
    return { ifs: out, ports };
  }
  /** 이 노드(Caddy 를 돌리는 칩)가 뒤에 앱 없이 직접 응답하는 정적 라우트(kind "static", runtime 없음) */
  function staticFor(PD, n) {
    if (!n || !(n.svcs || []).some((x) => x.kind === "proxy")) return [];
    return ((PD.s && PD.s.services) || []).filter((x) => x && x.kind === "static");
  }
  const ifaceOf = (PD, n) => (((PD.s && PD.s.system && PD.s.system.network && PD.s.system.network.interfaces) || []).find((i) => i.name === n.label) || null);
  const shortModel = (m) => String(m || "").replace(/\s+Model\s+\S+.*$/i, "").replace(/\s+Rev\s+.*$/i, "").replace(/^Raspberry Pi/i, "Pi").trim();

  // ------------------------------------------------------------------ 배치(결정적)
  /** opt: { offOpen } → { pos, order, boxes, caps, edges, labels, more, ghosts, zones, bounds, box:null, regions:[] } */
  function layout(PD, opt) {
    opt = opt || {};
    const { estW, mean } = h;
    const pos = new Map(); const order = []; const boxes = []; const caps = []; const more = []; const ghosts = [];
    const byLabel = (a, b) => String(a.label).localeCompare(String(b.label));
    const cx = (r) => r.x + r.w / 2; const cy = (r) => r.y + r.h / 2;
    const place = (id, r) => { r.id = id; pos.set(id, r); return r; };
    const self = PD.self; const segLan = PD.segLan; const segTn = PD.segTn;
    const catRank = (n) => { const c = catOf(n); const i = PD.cats.indexOf(c); return c ? (i < 0 ? 90 : i) : 99; };
    const LAN_Y = EXTH + 44 + SEGH / 2;
    const metaW = (t) => (t ? estW(t, FS.meta) + 6 : 0);
    const isTunnel = (id) => PD.edges.some((e) => e.kind === "tunnel" && e.from === id);
    const bridgesTo = (id) => PD.edges.filter((e) => e.kind === "bridge" && e.to === id && PD.nodes.has(e.from));
    // 칩 이름 폭: 보조 줄(포트)의 첫 조각만큼만 비워 둔다(나머지는 fitMeta 가 넘치면 뺀다)
    const chip = (n, x, y) => {
      const first = (portList(PD, n)[0] || "");
      return place(n.id, { x, y, w: CW, h: CH, role: "chip", nameMax: CW - 31 - Math.min(72, metaW(first)) });
    };

    // ---------- 기기 상자 (없으면 건너뛴다)
    let dev = null; let lane = null; let procBox = null; let tunnelMaxX = null;
    const devY = LAN_Y + 46;
    const topPorts = []; const botPorts = [];
    if (self) {
      const kids = PD.kids(self.id);
      const ports = kids.filter((n) => n.type === "iface").sort((a, b) => (IF_RANK[a.kind] ?? 5) - (IF_RANK[b.kind] ?? 5) || byLabel(a, b));
      for (const p of ports) {
        const toTn = PD.edgesOf(p.id).some((e) => TN_KINDS.has(e.kind) || (segTn && (e.to === segTn.id || e.from === segTn.id)));
        (p.kind === "tailnet" || toTn ? botPorts : topPorts).push(p);
      }
      const engines = kids.filter((n) => n.type === "engine").sort(byLabel);
      const procG = kids.find((n) => n.type === "group" && n.role === "procs");
      // 제목 칩(위 테두리 왼쪽)
      const tab = { x: DX + DP, y: devY - TH / 2, w: TW, h: TH };
      order.push(self.id);
      const devIdx = order.length;
      let bx = DX + DP; const by = devY + DTOP; let bodyH = 0;
      const bodyOrder = [];
      for (const eng of engines) {
        const r = layEngine(eng, bx, by, bodyOrder);
        bx += r.w + CG; bodyH = Math.max(bodyH, r.h);
      }
      if (procG) {
        // 바인딩 선이 있는 칩은 오른쪽 칸 끝으로(선이 다른 칩을 가로지르지 않고 바로 통로로 나가게)
        const bound = (n) => PD.edges.some((e) => e.kind === "bind" && e.from === n.id);
        const list0 = PD.kids(procG.id).sort((a, b) => catRank(a) - catRank(b) || byLabel(a, b));
        const list = list0.filter((n) => !bound(n)).concat(list0.filter(bound));
        const two = list.length > PROC_2COL;
        const rows = two ? Math.ceil(list.length / 2) : list.length;
        const bw = two ? CW * 2 + 24 : NBW;
        const hgt = list.length ? NBH + rows * CP - (CP - CH) + NBP : NBH + NBP + 4;
        procBox = place(procG.id, { x: bx, y: by, w: bw, h: hgt, role: "box", hh: NBH });
        boxes.push({ id: procG.id, cls: "procs", x: bx, y: by, w: bw, h: hgt, rx: 12 });
        bodyOrder.push(procG.id);
        list.forEach((n, i) => {
          const c = two && i >= rows ? 1 : 0; const r = c ? i - rows : i;
          chip(n, bx + 8 + c * (CW + 8), by + NBH + r * CP); bodyOrder.push(n.id);
        });
        bx += bw; bodyH = Math.max(bodyH, hgt);
        if (PD.edges.some((e) => e.kind === "bind" && list.some((n) => n.id === e.from))) lane = bx + 14;
      } else bx -= CG;
      const right = bx + (lane ? 56 : DP);
      // 위 테두리 인터페이스: 제목 칩과 터널 통로(있으면)를 비켜서
      let px = Math.max(tab.x + tab.w + 28, tunnelMaxX != null ? tunnelMaxX + 34 : -Infinity);
      for (const p of topPorts) { place(p.id, { x: px, y: devY - PH / 2, w: PW, h: PH, role: "port", side: "top" }); px += PW + 18; }
      const w = Math.max(right, px - 18 + 24) - DX;
      const hgt = DTOP + Math.max(bodyH, 60) + DBOT;
      dev = place(self.id, { x: DX, y: devY, w, h: hgt, role: "dev", tab });
      boxes.unshift({ id: self.id, cls: "device", x: DX, y: devY, w, h: hgt, rx: 24 });
      // 아래 테두리 인터페이스: 바인딩 통로가 있으면 그 밑에(통로가 인터페이스 위로 곧게 떨어지게)
      let bx2 = lane != null ? lane + 24 - PW : (procBox ? procBox.x + procBox.w - PW + 10 : DX + w - PW - 24);
      for (const p of botPorts) { place(p.id, { x: bx2, y: devY + hgt - PH / 2, w: PW, h: PH, role: "port", side: "bottom" }); bx2 -= PW + 18; }
      // 읽는 순서: 제목 → 위 인터페이스 → 안(엔진·칩·프로세스) → 아래 인터페이스
      order.splice(devIdx, 0, ...topPorts.map((p) => p.id), ...bodyOrder);
      order.push(...botPorts.map((p) => p.id));
    }

    function layEngine(eng, x0, y0, ord) {
      const kids = PD.kids(eng.id);
      const nets = kids.filter((n) => n.type === "network");
      const groups = kids.filter((n) => n.type === "group").sort((a, b) => (a.role === "hostnet" ? 0 : 1) - (b.role === "hostnet" ? 0 : 1) || byLabel(a, b));
      const cnt = (n) => PD.kidIds(n.id).length;
      const hub = nets.slice().sort((a, b) => cnt(b) - cnt(a) || byLabel(a, b))[0] || null;
      const par = (id) => { const n = PD.nodes.get(id); return n ? n.parent : null; };
      // 허브(가장 큰 네트워크)의 칩이 붙는 망은 오른쪽 열, 그 밖(허브로 붙는 망 포함)은 왼쪽 열 — 두 열 높이가 비슷해지고 선이 짧다
      const fromHub = (net) => !!hub && PD.edges.some((e) => e.kind === "bridge" && e.to === net.id && par(e.from) === hub.id);
      const intoHub = (net) => !!hub && PD.edges.some((e) => e.kind === "bridge" && e.to === hub.id && par(e.from) === net.id);
      const rest = nets.filter((n) => n !== hub);
      const right = rest.filter(fromHub);
      const left = groups.concat(rest.filter((n) => !right.includes(n)).sort((a, b) => intoHub(b) - intoHub(a) || cnt(b) - cnt(a) || byLabel(a, b)));
      const cols = [];
      if (left.length) cols.push({ list: left, left: true });
      if (hub) cols.push({ list: [hub], hub: true });
      if (right.length) cols.push({ list: right, right: true });
      const tunnelIn = (list) => list.some((b) => PD.kids(b.id).some((c) => isTunnel(c.id)));
      const gapAfter = (ci) => {
        const a = cols[ci]; const b = cols[ci + 1];
        if (!b) return 0;
        if (b.right) return CGB;
        if (a.left && (left.some(intoHub) || tunnelIn(left))) return CGB;
        return tunnelIn(a.list) ? CGB : CG;
      };
      ord.push(eng.id);
      let x = x0 + EP; const top = y0 + EH; let maxH = 0;
      cols.forEach((col, ci) => {
        let list = col.list;
        if (col.right) {
          // 허브의 어느 칩과 이어지는지에 맞춰 위에서 아래로 — 선이 짧고 덜 꼬이게
          const want = (net) => {
            const ys = PD.edges.filter((e) => e.kind === "bridge" && e.to === net.id && par(e.from) === hub.id).map((e) => pos.get(e.from)).filter(Boolean).map(cy);
            const hb = pos.get(hub.id);
            return ys.length ? mean(ys) : hb ? hb.y + hb.h : 1e9;
          };
          list = list.map((n) => ({ n, w: want(n) })).sort((a, b) => a.w - b.w || byLabel(a.n, b.n)).map((o) => o.n);
        }
        let y = top;
        for (const box of list) { y += layBox(box, x, y, ord) + NBG; }
        maxH = Math.max(maxH, y - NBG - top);
        x += NBW + gapAfter(ci);
      });
      const w = Math.max(x - x0 + EP, 240); const hgt = EH + Math.max(maxH, 24) + EP;
      place(eng.id, { x: x0, y: y0, w, h: hgt, role: "box", hh: EH });
      boxes.push({ id: eng.id, cls: "engine", x: x0, y: y0, w, h: hgt, rx: 16 });
      // 터널 통로: 터널 컨테이너가 든 상자 오른쪽 틈으로 나가 위로(여럿이면 7px 씩 옆 통로)
      let ti = 0;
      for (const [id, r] of pos) {
        if (r.role !== "chip" || !isTunnel(id)) continue;
        const box = pos.get((PD.nodes.get(id) || {}).parent);
        const bx = box ? box.x + box.w : r.x + r.w + 8;
        r.tunnelX = bx + 12 + ti * 7; ti++;
        tunnelMaxX = Math.max(tunnelMaxX == null ? -Infinity : tunnelMaxX, r.tunnelX);
      }
      return { w, h: hgt };
    }
    function layBox(net, x, y, ord) {
      const tunnelFirst = (a, b) => (isTunnel(b.id) ? 1 : 0) - (isTunnel(a.id) ? 1 : 0);
      const ch = PD.kids(net.id).sort((a, b) => tunnelFirst(a, b) || byLabel(a, b));
      // 다른 망에서 이 망에도 붙은 컨테이너: 점선 유령 줄(이름 ↗ 본래 망) — 선을 따라가지 않아도 구성원을 읽을 수 있게
      const gh = net.type === "network" ? bridgesTo(net.id).map((e) => PD.nodes.get(e.from)).filter((n) => n && n.parent !== net.id).sort(byLabel) : [];
      const realH = ch.length ? ch.length * CP - (CP - CH) : 0;
      const ghostH = gh.length ? (ch.length ? 6 : 0) + gh.length * GP - (GP - GH) : 0;
      const hgt = ch.length || gh.length ? NBH + realH + ghostH + NBP : NBH + NBP + 4;
      place(net.id, { x, y, w: NBW, h: hgt, role: "box", hh: NBH });
      boxes.push({ id: net.id, cls: net.type === "group" ? net.role || "group" : "network", x, y, w: NBW, h: hgt, rx: 12 });
      ord.push(net.id);
      ch.forEach((c, i) => { chip(c, x + 8, y + NBH + i * CP); ord.push(c.id); });
      const gy = y + NBH + realH + (ch.length ? 6 : 0);
      gh.forEach((c, j) => {
        const home = c.parent ? PD.nodes.get(c.parent) : null;
        // 본래 망 이름: compose 기본 망 "<프로젝트>_default" 는 프로젝트 이름만(좁은 줄에서 잘리지 않게, 전체는 툴팁에)
        const homeFull = home ? (home.type === "group" ? home.label : name(PD, home.id)) : "";
        ghosts.push({ of: c.id, net: net.id, x: x + 8, y: gy + j * GP, w: CW, h: GH, name: name(PD, c.id), home: homeFull.replace(/_default$/, ""), homeFull });
      });
      return hgt;
    }

    // ---------- 카드(원격 기기) 크기
    const cardOf = (n, x, y) => {
      const kids = PD.kids(n.id).filter((k) => k.type !== "group");
      const hh = CARDH + (kids.length ? kids.length * (KIDH + 8) + 4 : 0);
      const r = place(n.id, { x, y, w: CARDW + (kids.length ? 16 : 0), h: hh, role: "card", nest: kids.length > 0 });
      order.push(n.id);
      kids.sort(byLabel).forEach((k, i) => {
        place(k.id, { x: x + 10, y: y + CARDH + i * (KIDH + 8), w: r.w - 20, h: KIDH, role: "card", kid: true });
        order.push(k.id);
      });
      return r;
    };
    const cardSize = (n) => { const k = PD.kids(n.id).filter((x) => x.type !== "group").length; return { w: CARDW + (k ? 16 : 0), h: CARDH + (k ? k * (KIDH + 8) + 4 : 0) }; };

    // ---------- 맨 위의 카드들을 LAN 에도 있는 것 / Tailnet 에만 있는 것으로
    const tops = [...PD.nodes.values()].filter((n) => !n.parent && n !== self && !["external", "segment"].includes(n.type) && !(n.type === "group" && n.role === "offline"));
    const onLan = (n) => !!segLan && PD.edgesOf(n.id).some((e) => (e.to === segLan.id || e.from === segLan.id));
    const online = (n) => n.online !== false;
    const lanCards = tops.filter(onLan).sort((a, b) => online(b) - online(a) || byLabel(a, b));
    const tnCards = tops.filter((n) => !onLan(n)).sort((a, b) => online(b) - online(a) || byLabel(a, b));
    const offG = PD.nodes.get("grp:offline") || null;

    // ---------- 같은 LAN 기기: 기기 오른쪽 한 열 + 빗살 배선(왼쪽 = LAN 통로, 오른쪽 = Tailnet 통로)
    const devBottom = dev ? dev.y + dev.h : LAN_Y + 60;
    const n = lanCards.length;
    const lanLaneW = n ? 14 + (n - 1) * 9 : 0;
    const colX = (dev ? dev.x + dev.w : SEGW + 40) + 34 + lanLaneW;
    const colH = lanCards.reduce((a, c) => a + cardSize(c).h, 0) + Math.max(0, n - 1) * 18;
    const bandTop = LAN_Y + 46;
    const bandBot = Math.max(devBottom, bandTop + colH + 40);
    let yy = bandTop + Math.max(18, (bandBot - bandTop - colH) / 2);
    const lanRects = [];
    if (n) caps.push({ id: "lan", x: colX, y: yy - 10, t: lanText(PD) });
    for (const c of lanCards) { lanRects.push(cardOf(c, colX, yy)); yy += cardSize(c).h + 18; }
    const colW = lanRects.length ? Math.max(...lanRects.map((r) => r.w)) : 0;
    // LAN 통로: 아래 카드일수록 바깥(왼쪽) · Tailnet 통로: 위 카드일수록 바깥(오른쪽) — 서로 건너지 않는다
    lanRects.forEach((r, i) => { r.lanLane = colX - 14 - i * 9; r.lanY = r.y + (r.nest ? CARDH / 2 : r.h / 2); });
    const tnStubs = [];
    for (const r of lanRects) {
      const id = r.id;
      tnStubs.push({ id, r, y: r.y + (r.nest ? CARDH / 2 - 8 : r.h / 2) });
      for (const k of PD.kids(id)) { const kr = pos.get(k.id); if (kr) tnStubs.push({ id: k.id, r: kr, y: kr.y + kr.h / 2, kid: true }); }
    }
    tnStubs.sort((a, b) => b.y - a.y).forEach((s, i) => { s.r.tnLane = colX + colW + 14 + i * 9; s.r.tnY = s.y; });

    // ---------- 버스 높이
    const botPortBottom = dev && botPorts.length ? dev.y + dev.h + PH / 2 : -Infinity;
    const TN_Y = Math.max(bandBot, botPortBottom) + 40;

    // ---------- Tailnet 에만 있는 기기(버스 아래 한 줄, 넘치면 반 칸 엇갈린 두 줄) + 오프라인 묶음
    const rowX0 = SEGW + 40;
    const items = tnCards.map((c) => ({ n: c, ...cardSize(c) }));
    if (offG) {
      const m = PD.kids(offG.id).sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0) || byLabel(a, b));
      const open = !!opt.offOpen;
      // 펼치면 줄 목록 — 6대가 넘으면 두 칸으로(지도가 아래로 길어지지 않게)
      const oc = open && m.length > 6 ? 2 : 1;
      items.push({ n: offG, w: oc === 2 ? CARDW * 2 + 8 : CARDW, h: CARDH + (open ? Math.ceil(m.length / oc) * ROWP + 6 : 0), off: true, members: m, open, oc });
    }
    const aboveW = Math.max(colX + colW + 40 + tnStubs.length * 9, dev ? dev.x + dev.w : 0);
    const P = CARDW + GAPX;
    // 한 줄이 기본(8개까지는 늘 한 줄) — 넘치면 반 칸 엇갈린 두 줄(아래 줄의 선이 윗줄 카드 사이로 지나간다)
    let perRow = items.length <= 8 ? Math.max(1, items.length) : Math.max(8, Math.floor((aboveW - rowX0 + GAPX) / P));
    if (items.length > perRow * 2) perRow = Math.ceil(items.length / 2);
    const rowY = TN_Y + 46;
    const tnRects = [];
    items.forEach((it, i) => {
      const row = i < perRow ? 0 : 1; const j = row ? i - perRow : i;
      const x = rowX0 + j * P + (row ? P / 2 : 0);
      const y = rowY + (row ? Math.max(...items.slice(0, perRow).map((q) => q.h)) + 34 : 0);
      if (it.off) {
        const r = place(it.n.id, { x, y, w: it.w, h: it.h, role: "card", off: true, row });
        order.push(it.n.id);
        more.push({ id: it.n.id, x: x + it.w - 66, y: y + 12, w: 56, h: 22, open: it.open, count: it.members.length });
        if (it.open) {
          const cw = (it.w - 16 - (it.oc - 1) * 8) / it.oc;
          it.members.forEach((m, k) => {
            const c = k % it.oc; const rr = Math.floor(k / it.oc);
            place(m.id, { x: x + 8 + c * (cw + 8), y: y + CARDH + rr * ROWP, w: cw, h: ROWH, role: "row" });
            order.push(m.id);
          });
        }
        tnRects.push(r);
      } else {
        const r = cardOf(it.n, x, y); r.row = row; tnRects.push(r);
      }
    });

    // ---------- 구간 알약(버스 왼쪽 끝) · 외부(맨 위)
    if (segLan) place(segLan.id, { x: 0, y: LAN_Y - SEGH / 2, w: SEGW, h: SEGH, role: "pill", busY: LAN_Y, bus: "lan" });
    if (segTn) place(segTn.id, { x: 0, y: TN_Y - SEGH / 2, w: SEGW, h: SEGH, role: "pill", busY: TN_Y, bus: "tailnet" });
    const exts = [...PD.nodes.values()].filter((n) => n.type === "external" && !n.parent);
    const inet = exts.find((n) => /internet/i.test(n.id)) || exts.find((n) => PD.edgesOf(n.id).some((e) => e.kind === "uplink")) || exts[0] || null;
    let ex = Math.max(0, SEGW / 2 - EXTW / 2);
    if (inet) { place(inet.id, { x: ex, y: 0, w: EXTW, h: EXTH, role: "ext" }); ex += EXTW + 44; }
    for (const e of exts.filter((x) => x !== inet).sort(byLabel)) {
      // 터널이 닿는 외부(Cloudflare)는 터널 통로들 가운데 위에
      const lanes = PD.edges.filter((q) => q.kind === "tunnel" && q.to === e.id).map((q) => pos.get(q.from)).filter((r) => r && r.tunnelX != null).map((r) => r.tunnelX);
      const x = lanes.length ? Math.max(ex, mean(lanes) - EXTW / 2) : ex;
      place(e.id, { x, y: 0, w: EXTW, h: EXTH, role: "ext" });
      ex = x + EXTW + 44;
    }
    // 그 밖의 구간(드묾): 맨 위 줄 오른쪽에 알약만
    for (const sg of [...PD.nodes.values()].filter((x) => x.type === "segment" && x !== segLan && x !== segTn)) {
      place(sg.id, { x: ex, y: 0, w: SEGW, h: SEGH, role: "pill" }); ex += SEGW + 30;
    }
    // 읽는 순서: 맨 위 → LAN → 기기(이미 들어감) → LAN 기기 → Tailnet → 아래 줄
    const headIds = exts.slice().sort((a, b) => pos.get(a.id).x - pos.get(b.id).x).map((x) => x.id).concat(segLan ? [segLan.id] : []);
    order.unshift(...headIds);
    const tnIdx = order.indexOf(tnRects.length ? tnRects[0].id : "");
    if (segTn) { if (tnIdx >= 0) order.splice(tnIdx, 0, segTn.id); else order.push(segTn.id); }
    for (const sg of [...PD.nodes.values()].filter((x) => x.type === "segment" && x !== segLan && x !== segTn)) order.push(sg.id);
    // 어디에도 못 놓인 노드(모르는 종류가 상자 안에 있는 등) — 떨어뜨리지 않는다: 맨 아래 줄 끝에
    let lx = rowX0 + Math.min(items.length, perRow) * P;
    for (const nn of PD.nodes.values()) {
      if (pos.has(nn.id)) continue;
      if (nn.parent === "grp:offline" && !opt.offOpen) continue;
      if (nn.parent && pos.has(nn.parent) && pos.get(nn.parent).role === "card") continue;
      place(nn.id, { x: lx, y: rowY, w: CARDW, h: CARDH, role: "card" }); order.push(nn.id); lx += P;
    }

    // ---------- 브리지 통로: 상자 사이 틈마다, 도착 망(상자)별로 한 통로(7px 간격) — 선은 늘 도착 망의 머리(이름 줄)로 들어간다
    const bridgeLane = new Map();
    {
      const gut = new Map();
      for (const e of PD.edges) {
        if (e.kind !== "bridge") continue;
        const a = pos.get(e.from); const b = pos.get(e.to);
        if (!a || !b || a.role !== "chip" || b.role !== "box") continue;
        const toRight = b.x >= a.x + a.w + 4; const toLeft = b.x + b.w <= a.x - 4;
        if (!toRight && !toLeft) continue;
        const g1 = toRight ? a.x + a.w + 8 : b.x + b.w; const g2 = toRight ? b.x : a.x - 8;
        const k = Math.round(g1) + ":" + Math.round(g2);
        if (!gut.has(k)) gut.set(k, { g1, g2, toRight, t: new Map() });
        const G = gut.get(k);
        if (!G.t.has(e.to)) G.t.set(e.to, b.y);
      }
      for (const G of gut.values()) {
        // 위쪽 망일수록 도착 쪽에 가까운 통로(가로선과 세로선이 덜 건넌다)
        [...G.t.entries()].sort((p, q) => p[1] - q[1]).forEach(([to], i) => {
          const x = G.toRight ? G.g2 - 12 - i * 7 : G.g1 + 12 + i * 7;
          bridgeLane.set(to + "|" + (G.toRight ? "r" : "l") + "|" + Math.round(G.g1), x);
        });
      }
    }

    // ---------- 엣지 모양
    const attach = { lan: [], tailnet: [] };
    const busOf = (id) => { const r = pos.get(id); return r && r.role === "pill" && r.bus ? r : null; };
    const lc = (x1, y1, x2, y2) => [x1, y1, x1 + (x2 - x1) / 3, y1 + (y2 - y1) / 3, x1 + 2 * (x2 - x1) / 3, y1 + 2 * (y2 - y1) / 3, x2, y2];
    const vcurve = (x1, y1, x2, y2) => {
      if (Math.abs(x1 - x2) < 0.5) return { d: `M${f1(x1)} ${f1(y1)}V${f1(y2)}`, lc: lc(x1, y1, x2, y2) };
      const my = (y1 + y2) / 2;
      return { d: `M${f1(x1)} ${f1(y1)}C${f1(x1)} ${f1(my)} ${f1(x2)} ${f1(my)} ${f1(x2)} ${f1(y2)}`, lc: [x1, y1, x1, my, x2, my, x2, y2] };
    };
    const R = 6;
    /** 가로 → 세로 → 가로 꺾은선(모서리 둥글게) */
    const hvh = (x1, y1, L, y2, x2) => {
      const dy = y2 - y1; const s1 = x1 < L ? 1 : -1; const s2 = x2 > L ? 1 : -1;
      if (Math.abs(dy) < 2 * R) return `M${f1(x1)} ${f1(y1)}H${f1(L)}V${f1(y2)}H${f1(x2)}`;
      const sy = dy > 0 ? 1 : -1;
      return `M${f1(x1)} ${f1(y1)}H${f1(L - s1 * R)}Q${f1(L)} ${f1(y1)} ${f1(L)} ${f1(y1 + sy * R)}V${f1(y2 - sy * R)}Q${f1(L)} ${f1(y2)} ${f1(L + s2 * R)} ${f1(y2)}H${f1(x2)}`;
    };
    function geom(e) {
      const a = pos.get(e.from); const b = pos.get(e.to);
      if (!a || !b) return null;
      const na = PD.nodes.get(e.from); const nb = PD.nodes.get(e.to);
      // 구간(버스)으로: LAN 빗살 · Tailnet 빗살 · 그 밖은 수직
      const busB = busOf(e.to); const busA = busOf(e.from);
      if ((busB && !busA && na && na.type !== "external") || (busA && !busB && nb && nb.type !== "external")) {
        const bus = busB || busA; const r = busB ? a : b;
        const by = bus.busY;
        if (bus.bus === "lan" && r.lanLane != null) {
          const x = r.lanLane; const y = r.lanY; const sx = r.x;
          attach.lan.push(x);
          return { d: `M${f1(sx)} ${f1(y)}H${f1(x + R)}Q${f1(x)} ${f1(y)} ${f1(x)} ${f1(y - R)}V${f1(by)}`, lc: lc(x, y, x, by), plain: true };
        }
        if (bus.bus === "tailnet" && r.tnLane != null) {
          const x = r.tnLane; const y = r.tnY; const sx = r.x + r.w;
          attach.tailnet.push(x);
          return { d: `M${f1(sx)} ${f1(y)}H${f1(x - R)}Q${f1(x)} ${f1(y)} ${f1(x)} ${f1(y + R)}V${f1(by)}`, lc: lc(x, y, x, by), plain: true };
        }
        const x = r.role === "card" && r.nest ? r.x + 34 : cx(r);
        const above = cy(r) < by;
        const y1 = above ? r.y + r.h : r.y;
        attach[bus.bus].push(x);
        return { d: `M${f1(x)} ${f1(y1)}V${f1(by)}`, lc: lc(x, y1, x, by) };
      }
      // 구간 → 외부(업링크 NAT): 알약 위에서 위로
      if (na && nb && (na.type === "segment" || nb.type === "segment") && (na.type === "external" || nb.type === "external")) {
        const sg = na.type === "segment" ? a : b; const xt = na.type === "external" ? a : b;
        const g = vcurve(cx(sg), sg.y, cx(xt), xt.y + xt.h);
        g.arrow = [cx(xt), xt.y + xt.h + 1]; // 업링크: 바깥(인터넷)으로 나가는 방향
        return g;
      }
      // 외부 ↔ 외부: 가로
      if (na && nb && na.type === "external" && nb.type === "external") {
        const [l, r] = a.x <= b.x ? [a, b] : [b, a];
        const x1 = l.x + l.w; const x2 = r.x; const y = cy(l);
        return { d: `M${f1(x1)} ${f1(y)}H${f1(x2)}`, lc: lc(x1, y, x2, y) };
      }
      // 기기 안 칩 → 외부(터널): 칩 오른쪽에서 상자 옆 통로로 나가 곧게 위로
      if (a.role === "chip" && nb && nb.type === "external") {
        const top = b.y + b.h;
        if (a.tunnelX == null) return vcurve(a.x + a.w - 28, a.y, cx(b), top);
        const L = a.tunnelX; const x1 = a.x + a.w; const y1 = cy(a);
        let d = `M${f1(x1)} ${f1(y1)}H${f1(L - R)}Q${f1(L)} ${f1(y1)} ${f1(L)} ${f1(y1 - R)}`;
        if (L > b.x + 10 && L < b.x + b.w - 10) d += `V${f1(top)}`;
        else { const tx = Math.max(b.x + 14, Math.min(b.x + b.w - 14, L)); d += `V${f1(top + 30)}C${f1(L)} ${f1(top + 12)} ${f1(tx)} ${f1(top + 18)} ${f1(tx)} ${f1(top)}`; }
        return { d, lc: lc(L, y1, L, top), plain: false };
      }
      // 브리지: 칩 → (상자 사이 통로) → 다른 망 상자의 머리(이름 줄). 끝에 작은 점.
      if (a.role === "chip" && b.role === "box") {
        const y1 = cy(a); const y2 = b.y + 20;
        const toRight = b.x >= a.x + a.w + 4; const toLeft = b.x + b.w <= a.x - 4;
        if (toRight || toLeft) {
          const g1 = toRight ? a.x + a.w + 8 : b.x + b.w;
          const L = bridgeLane.get(e.to + "|" + (toRight ? "r" : "l") + "|" + Math.round(g1)) ?? (toRight ? b.x - 12 : b.x + b.w + 12);
          const x1 = toRight ? a.x + a.w : a.x; const x2 = toRight ? b.x : b.x + b.w;
          return { d: hvh(x1, y1, L, y2, x2), lc: lc(L, y1, L, y2), dot: [x2, y2] };
        }
        // 같은 열(드묾): 오른쪽으로 둘러서 머리로
        const x1 = a.x + a.w; const x2 = b.x + b.w; const bx = Math.max(x1, x2) + 18;
        return { d: `M${f1(x1)} ${f1(y1)}C${f1(bx)} ${f1(y1)} ${f1(bx)} ${f1(y2)} ${f1(x2)} ${f1(y2)}`, lc: [x1, y1, bx, y1, bx, y2, x2, y2], dot: [x2, y2] };
      }
      // 바인딩: 칩 → (오른쪽 통로) → 테두리의 인터페이스
      if (a.role === "chip" && b.role === "port") {
        const x1 = a.x + a.w; const y1 = cy(a);
        const L = lane != null ? lane : x1 + 12;
        const inPort = L > b.x + 8 && L < b.x + b.w - 8;
        const yEnd = b.side === "bottom" ? b.y : b.y + b.h;
        const dir = yEnd > y1 ? 1 : -1;
        let d = `M${f1(x1)} ${f1(y1)}H${f1(L - R)}Q${f1(L)} ${f1(y1)} ${f1(L)} ${f1(y1 + dir * R)}`;
        if (inPort) d += `V${f1(yEnd)}`;
        else { const ym = cy(b); const xs = L > cx(b) ? b.x + b.w : b.x; d += `V${f1(ym - dir * R)}Q${f1(L)} ${f1(ym)} ${f1(L + (xs > L ? R : -R))} ${f1(ym)}H${f1(xs)}`; }
        return { d, lc: lc(L, y1, L, yEnd), plain: true };
      }
      // 그 밖: 가까운 옆끼리 곡선
      const ax = cx(a) < cx(b) ? a.x + a.w : a.x; const bx = cx(a) < cx(b) ? b.x : b.x + b.w;
      const dx = (bx - ax) / 2;
      return { d: `M${f1(ax)} ${f1(cy(a))}C${f1(ax + dx)} ${f1(cy(a))} ${f1(bx - dx)} ${f1(cy(b))} ${f1(bx)} ${f1(cy(b))}`, lc: [ax, cy(a), ax + dx, cy(a), bx - dx, cy(b), bx, cy(b)] };
    }
    const edges = [];
    for (const e of PD.edges) { const g = geom(e); if (g) edges.push(Object.assign({}, e, g)); }
    // 버스 길이: 알약 오른쪽 끝부터 가장 먼 연결점 너머까지
    for (const sg of [segLan, segTn]) {
      const r = sg && pos.get(sg.id);
      if (!r) continue;
      const xs = attach[r.bus];
      r.busEnd = Math.max(r.x + r.w + 40, ...(xs.length ? xs.map((x) => x + 26) : [0]));
    }

    // ---------- 선 라벨: 칩·카드·알약·상자 머리·버스·다른 라벨을 비켜 곡선 위의 빈자리에
    //   유선·무선·오버레이 값(속도·dBm·IP)은 인터페이스 칩이 이미 보여 주므로 선에는 달지 않는다(한 가지 사실은 한 번만)
    const NO_LABEL = new Set(["wired", "wifi", "overlay", "bridge", "bind", "lan"]);
    const obst = [];
    for (const [, r] of pos) {
      if (r.role === "box") obst.push({ x: r.x, y: r.y, w: r.w, h: r.hh || 28 });
      else if (r.role === "dev") obst.push(r.tab);
      else obst.push(r);
      if (r.role === "pill" && r.busEnd) obst.push({ x: r.x + r.w, y: r.busY - 3, w: r.busEnd - r.x - r.w, h: 6 });
    }
    const bez = (c, t) => { const u = 1 - t; return [u * u * u * c[0] + 3 * u * u * t * c[2] + 3 * u * t * t * c[4] + t * t * t * c[6], u * u * u * c[1] + 3 * u * u * t * c[3] + 3 * u * t * t * c[5] + t * t * t * c[7]]; };
    const placed = [];
    const hitR = (x, y, w) => obst.some((o) => x + w / 2 > o.x - 2 && x - w / 2 < o.x + o.w + 2 && y + 10 > o.y - 1 && y - 10 < o.y + o.h + 1);
    const hitL = (x, y, w) => placed.some((q) => Math.abs(q.x - x) < (q.w + w) / 2 + 4 && Math.abs(q.y - y) < 21);
    const labels = [];
    const PRI = { tunnel: 0, uplink: 1, "tailnet-relay": 3, "tailnet-direct": 3, "tailnet-idle": 3, offline: 4 };
    for (const e of edges.slice().sort((a, b) => (PRI[a.kind] ?? 5) - (PRI[b.kind] ?? 5) || a.key.localeCompare(b.key))) {
      if (!e.label || e.plain || NO_LABEL.has(e.kind)) continue;
      const w = labelW(e.label);
      let best = null;
      for (const t of [0.5, 0.38, 0.62, 0.28, 0.72, 0.18, 0.82]) { const [x, y] = bez(e.lc, t); if (!hitL(x, y, w) && !hitR(x, y, w)) { best = [x, y]; break; } }
      if (!best) for (const t of [0.5, 0.35, 0.65, 0.2, 0.8]) { const [x, y] = bez(e.lc, t); if (!hitL(x, y, w)) { best = [x, y]; break; } }
      if (!best) best = bez(e.lc, 0.5);
      placed.push({ x: best[0], y: best[1], w });
      labels.push({ key: e.key, kind: e.kind, x: best[0], y: best[1], w, text: e.label });
    }

    // ---------- 먼저 읽을 곳(기본 맞춤): 맨 위 외부 · LAN 알약 · 이 기기 상자(테두리 인터페이스 포함).
    //   지도 전체가 읽을 크기로 안 들어가면 atlas.js 가 이것을 읽기 좋은 크기로 담고, 같은 LAN 기기 열·Tailnet 줄은 가장자리로 보낸다(→/↓ 안내 칩)
    let prio = null;
    if (dev) {
      const pr = [dev, dev.tab].concat([...pos.values()].filter((r) => r.role === "ext" || r.role === "port" || (segLan && r.id === segLan.id)));
      const x1 = Math.min(...pr.map((r) => r.x)); const y1 = Math.min(...pr.map((r) => r.y));
      prio = { x: x1 - 12, y: y1 - 12, w: Math.max(...pr.map((r) => r.x + r.w)) - x1 + 24, h: Math.max(...pr.map((r) => r.y + r.h)) - y1 + 24 };
    }

    // ---------- 경계
    const rs = [...pos.values()].concat(boxes, [...pos.values()].filter((r) => r.tab).map((r) => r.tab));
    let minX = Math.min(...rs.map((r) => r.x)); let minY = Math.min(...rs.map((r) => r.y));
    let maxX = Math.max(...rs.map((r) => r.x + r.w)); let maxY = Math.max(...rs.map((r) => r.y + r.h));
    for (const r of pos.values()) if (r.busEnd) maxX = Math.max(maxX, r.busEnd + 4);
    for (const l of labels) { minX = Math.min(minX, l.x - l.w / 2); maxX = Math.max(maxX, l.x + l.w / 2); }
    for (const e of edges) for (let i = 0; i < 8; i += 2) { maxX = Math.max(maxX, e.lc[i]); minX = Math.min(minX, e.lc[i]); }
    if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 400; maxY = 300; }
    const bounds = { x: minX - 12, y: minY - 12, w: maxX - minX + 24, h: maxY - minY + 24 };
    const uniq = []; const seenO = new Set();
    for (const id of order) if (pos.has(id) && !seenO.has(id)) { seenO.add(id); uniq.push(id); }
    // 화면 밖 안내(atlas.js): 아래 줄의 Tailnet 기기 · 오프라인 묶음 · 오른쪽 열의 같은 LAN 기기
    const zones = [];
    const tnOnly = tnRects.filter((r) => !r.off);
    if (tnOnly.length) zones.push({ id: "tn", label: "Tailnet 기기", ids: tnOnly.map((r) => r.id) });
    if (offG && pos.has(offG.id)) zones.push({ id: "off", label: "오프라인", count: PD.kidIds(offG.id).length, ids: [offG.id], unit: "대" });
    if (lanRects.length) zones.push({ id: "lan", label: "같은 LAN 기기", count: PD.lan.n, ids: lanRects.map((r) => r.id) });
    return { pos, order: uniq, boxes, caps, edges, labels, more, ghosts, zones, bounds, prio, box: null, regions: [], lane, LAN_Y, TN_Y };
  }
  /** 선 라벨 알약 폭(글자 크기 FS.label) */
  const labelW = (t) => Math.max(28, h.estW(String(t || ""), FS.label) + 14);

  /** 구조 서명: 바뀌면 배치를 다시 한다(상태·숫자·선 라벨은 넣지 않는다 — 제자리 갱신) */
  function sig(PD, opt) {
    if (!PD) return "";
    const ns = [...PD.nodes.values()].map((n) => [n.id, n.type, n.parent || "", n.label, n.role || "", n.kind || "", n.online === false ? 0 : 1].join("|")).sort().join(";");
    const es = PD.edges.map((e) => e.key).sort().join(";");
    return "P#" + ns + "#" + es + "#" + (opt && opt.offOpen ? 1 : 0);
  }

  // ------------------------------------------------------------------ SVG
  function svg(PD, L) {
    const { html, raw, fitLabel, SG } = h;
    const boxes = L.boxes.map((b) => html`<rect class="${"pbox pbox-" + b.cls}" data-box="${b.id}" x="${f1(b.x)}" y="${f1(b.y)}" width="${f1(b.w)}" height="${f1(b.h)}" rx="${b.rx}"/>`);
    const caps = L.caps.map((c) => html`<text class="p-cap" data-cap="${c.id || ""}" x="${f1(c.x)}" y="${f1(c.y)}">${c.t}</text>`);
    // 무늬: 업링크 = 겹선 + 화살촉(↑ 인터넷) · 오버레이 = 굵은 선 + 가운데 ◈ · 브리지 = 끝 점(도착 망의 머리)
    const bz = (c, t) => { const u = 1 - t; return [u * u * u * c[0] + 3 * u * u * t * c[2] + 3 * u * t * t * c[4] + t * t * t * c[6], u * u * u * c[1] + 3 * u * u * t * c[3] + 3 * u * t * t * c[5] + t * t * t * c[7]]; };
    const deco = (e) => {
      if (e.kind === "uplink") return html`<path class="e-gap" d="${e.d}"/>${e.arrow ? html`<path class="e-arrow" d="${"M" + f1(e.arrow[0] - 5.5) + " " + f1(e.arrow[1] + 8) + "L" + f1(e.arrow[0]) + " " + f1(e.arrow[1]) + "L" + f1(e.arrow[0] + 5.5) + " " + f1(e.arrow[1] + 8)}"/>` : ""}`;
      if (e.kind === "overlay" && e.lc) { const [x, y] = bz(e.lc, 0.5); return html`<g class="e-glyph" transform="${"translate(" + f1(x) + " " + f1(y) + ")"}"><rect x="-7" y="-7" width="14" height="14" rx="3" transform="rotate(45)"/><rect x="-2.6" y="-2.6" width="5.2" height="5.2" rx="1" transform="rotate(45)"/></g>`; }
      return "";
    };
    const edges = L.edges.map((e) => html`<g class="${"e pe" + (e.synthetic ? " e-syn" : "")}" data-kind="${e.kind}" data-key="${e.key}" data-flow="0" data-stale="${e.stale ? "1" : "0"}"><path class="e-base" d="${e.d}"/>${deco(e)}${e.dot ? html`<circle class="e-dot" cx="${f1(e.dot[0])}" cy="${f1(e.dot[1])}" r="3.2"/>` : ""}</g>`);
    const labels = L.labels.map((l) => html`<g class="el pel" data-kind="${l.kind}" data-keys="${l.key}" transform="${"translate(" + l.x.toFixed(1) + " " + l.y.toFixed(1) + ")"}"><rect x="${(-l.w / 2).toFixed(1)}" y="-9.5" width="${l.w.toFixed(1)}" height="19" rx="9.5"/><text y="3.8" text-anchor="middle">${l.text}</text></g>`);
    // 다른 망에서 붙은 컨테이너(유령 줄): 누르면 그 컨테이너를 고른다. 키보드는 본래 칩으로 닿는다(중복 초점 없음).
    const ghosts = (L.ghosts || []).map((g) => {
      const nmT = fitLabel(g.name, g.w - 24 - Math.min(96, h.estW("↗ " + g.home, 11) + 8), 12, 550);
      return html`<g class="p-ghost" data-ghost="${g.of}" transform="${"translate(" + f1(g.x) + " " + f1(g.y) + ")"}" aria-hidden="true"><title>${g.name + " — 본래 " + (g.homeFull || g.home) + " 에 있고 이 망에도 붙어 있어요"}</title><rect class="pg-bg" width="${g.w}" height="${g.h}" rx="5"/><text class="pg-n" x="10" y="${f1(g.h / 2 + 4)}">${nmT}</text><text class="pg-h" x="${g.w - 8}" y="${f1(g.h / 2 + 3.6)}" text-anchor="end">${fitLabel("↗ " + g.home, 96, 11, 500)}</text></g>`;
    });
    const nodes = [];
    for (const id of L.order) {
      const n = PD.nodes.get(id); const p = L.pos.get(id);
      if (!n || !p) continue;
      const nm = name(PD, id);
      const T = (x, y) => "translate(" + f1(x) + " " + f1(y) + ")";
      if (p.role === "chip" || p.role === "row") {
        const cat = p.role === "chip" ? catOf(n) : "";
        const bound = PD.edges.some((e) => e.kind === "bind" && e.from === id);
        const nmT = fitLabel(nm, p.nameMax || p.w - 90, p.role === "row" ? 12 : FS.chip, 600);
        nodes.push(html`<g class="${"n n-chip n-" + n.type + (p.role === "row" ? " n-row" : "") + (bound ? " is-bound" : "")}" data-id="${id}" data-tone="muted" data-cat="${cat}" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="-3" y="-3" width="${p.w + 6}" height="${p.h + 6}" rx="9"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="6"/>${cat ? html`<rect class="n-cat" x="1.5" y="4" width="3" height="${p.h - 8}" rx="1.5"/>` : ""}
<g class="n-sg" transform="${T(13, p.h / 2)}">${SG}</g>
<text class="n-name" x="23" y="${f1(p.h / 2 + 4.5)}">${nmT}</text>
<text class="n-meta" x="${p.w - 8}" y="${f1(p.h / 2 + 4)}" text-anchor="end" data-mx="name"></text>${nmT !== nm ? html`<title>${nm}</title>` : ""}
</g>`);
      } else if (p.role === "port") {
        nodes.push(html`<g class="${"n n-port n-port-" + (n.kind || "other")}" data-id="${id}" data-tone="muted" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="14"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="10"/>
<g class="n-ic" transform="${T(10, p.h / 2 - 9)}">${raw(ifIcon(n))}</g>
<text class="n-name" x="35" y="17">${fitLabel(nm, 80, 13, 700)}</text>
<text class="n-aux" x="${p.w - 11}" y="17" text-anchor="end"></text>
<text class="n-meta" x="35" y="32" data-mx="${p.w - 35 - 10}"></text>
</g>`);
      } else if (p.role === "dev") {
        const t = p.tab;
        nodes.push(html`<g class="n n-dev" data-id="${id}" data-tone="muted" transform="${T(t.x, t.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="${f1(p.x - t.x - 4)}" y="${f1(p.y - t.y - 4)}" width="${f1(p.w + 8)}" height="${f1(p.h + 8)}" rx="28"/>
<rect class="n-bg" width="${t.w}" height="${t.h}" rx="12"/>
<g class="n-ic" transform="${T(11, t.h / 2 - 9)}">${raw(PI.board)}</g>
<text class="n-name" x="36" y="17">${fitLabel(nm, 96, 14, 750)}</text>
<text class="n-stw" x="${t.w - 24}" y="17" text-anchor="end"></text>
<text class="n-meta" x="36" y="32" data-mx="${t.w - 36 - 12}"></text>
<g class="n-sg" transform="${T(t.w - 14, 13)}">${SG}</g>
</g>`);
      } else if (p.role === "box") {
        const two = p.hh >= 36;
        const icon = n.type === "engine" ? PI.engine : n.role === "procs" ? PI.procs : n.role === "hostnet" ? PI.hostnet : PI.net;
        const rx = n.type === "engine" ? 16 : 12;
        nodes.push(html`<g class="${"n n-box n-" + n.type + (n.role ? " n-" + n.role : "")}" data-id="${id}" data-tone="muted" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="-3" y="-3" width="${f1(p.w + 6)}" height="${f1(p.h + 6)}" rx="${rx + 3}"/>
<rect class="n-hit" width="${f1(p.w)}" height="${p.hh}" rx="${rx}"/>
<g class="n-ic" transform="${T(9, two ? 6 : p.hh / 2 - 8) + " scale(.9)"}">${raw(icon)}</g>
<text class="n-name" x="30" y="${two ? 18 : f1(p.hh / 2 + 4.5)}">${fitLabel(nm, two ? p.w - 56 : 130, 13, 700)}</text>
${two ? html`<text class="n-meta" x="30" y="32" data-mx="${f1(p.w - 30 - 12)}"></text>` : html`<text class="n-meta" x="${f1(p.w - 28)}" y="${f1(p.hh / 2 + 4)}" text-anchor="end" data-mx="name"></text>`}
<g class="n-sg" transform="${T(p.w - 14, two ? 15 : p.hh / 2)}">${SG}</g>
</g>`);
      } else if (p.role === "pill" || p.role === "ext") {
        const isTn = PD.segTn === n; const isInet = p.role === "ext" && /internet/i.test(id);
        const cls = p.role === "pill" ? "n n-seg n-zone " + (isTn ? "n-zone-tailnet" : "n-zone-lan") : isInet ? "n n-ext n-zone n-zone-internet" : "n n-ext n-edge";
        const icon = p.role === "pill" ? (isTn ? h.NICON.mesh : h.NICON.router) : isInet ? h.NICON.globe : h.NICON.cloud;
        const bus = p.busEnd ? html`<line class="p-bus-hit" x1="${p.w}" y1="${p.h / 2}" x2="${f1(p.busEnd - p.x)}" y2="${p.h / 2}"/><line class="p-bus" x1="${p.w - 4}" y1="${p.h / 2}" x2="${f1(p.busEnd - p.x)}" y2="${p.h / 2}"/>` : "";
        nodes.push(html`<g class="${cls}" data-id="${id}" data-tone="muted" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">${bus}
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="${p.h / 2 + 4}"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="${p.role === "pill" ? p.h / 2 : 12}"/>
<g class="n-ic" transform="${T(p.role === "pill" ? 15 : 13, p.h / 2 - 9)}">${raw(icon)}</g>
<text class="n-name" x="42" y="19.5">${fitLabel(nm, p.w - 66, 13, 700)}</text>
<text class="n-meta" x="42" y="34.5" data-mx="${p.w - 42 - 14}"></text>
<g class="n-sg" transform="${T(p.w - (p.role === "pill" ? 19 : 14), 14)}">${SG}</g>
</g>`);
      } else if (p.role === "card") {
        if (p.off) {
          nodes.push(html`<g class="n n-card n-offgrp" data-id="${id}" data-tone="muted" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="15"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="12"/>
<g class="n-ic" transform="${T(12, CARDH / 2 - 9)}">${raw(PI.off)}</g>
<text class="n-name" x="38" y="19.5"></text>
<text class="n-meta" x="38" y="35" data-mx="${p.w - 38 - 72}"></text>
</g>`);
        } else {
          const hh = p.kid ? KIDH : CARDH;
          nodes.push(html`<g class="${"n n-card n-peer" + (p.kid ? " n-kid" : "") + (p.nest ? " n-nest" : "") + (n.online === false ? " is-off" : "")}" data-id="${id}" data-tone="muted" transform="${T(p.x, p.y)}" tabindex="0" role="button" aria-label="${nm}">
<rect class="n-halo" x="-4" y="-4" width="${p.w + 8}" height="${p.h + 8}" rx="15"/>
<rect class="n-bg" width="${p.w}" height="${p.h}" rx="${p.kid ? 9 : 12}"/>
<g class="n-ic" transform="${T(12, hh / 2 - 9)}">${raw(n.type === "peer" || n.type === "device" ? osIcon(n) : PI.linux)}</g>
<text class="n-name" x="38" y="${p.kid ? 18 : 19.5}">${fitLabel(nm, p.w - 38 - 26, 13, 700)}</text>
<text class="n-meta" x="38" y="${p.kid ? 32.5 : 35}" data-mx="${p.w - 38 - 12}"></text>
<g class="n-sg" transform="${T(p.w - 14, 14)}">${SG}</g>
</g>`);
        }
      }
    }
    const tog = L.more.map((m) => html`<g class="n-more p-more" data-more="offline" data-open="${m.open ? "1" : "0"}" tabindex="0" role="button" aria-expanded="${m.open ? "true" : "false"}" aria-label="${m.open ? "오프라인 기기 목록 접기" : "오프라인 기기 " + m.count + "대 펼치기"}" transform="${"translate(" + f1(m.x) + " " + f1(m.y) + ")"}"><rect width="${m.w}" height="${m.h}" rx="7"/><text x="${m.w / 2}" y="${m.h / 2 + 4}" text-anchor="middle">${m.open ? "접기" : "펼치기"}</text></g>`);
    return html`<g class="pboxes">${boxes}${caps}</g><g class="pghosts">${ghosts}</g><g class="edges">${edges}</g><g class="p-virt" aria-hidden="true"></g><g class="elabels">${labels}</g><g class="nodes">${nodes}${tog}</g>`;
  }

  // ------------------------------------------------------------------ 3초마다 바뀌는 글자(보조 줄·상태)
  function worst(list) {
    const R = { crit: 3, warn: 2, muted: 1, ok: 0 };
    let t = "ok";
    for (const x of list) { const tt = h.tone(x.status); if ((R[tt] ?? 1) > (R[t] ?? 0)) t = tt; }
    return list.length ? t : "muted";
  }
  function info(PD, n) {
    const { html, fmt, statusText } = h;
    const S = PD.s || {};
    const st = n.status;
    let t = h.tone(st);
    const T = (cls, v) => html`<tspan class="${cls}">${v}</tspan>`;
    const sep = T("m-sep", " · ");
    const bad = st && st !== "up" && n.type !== "external" && n.type !== "group" ? T("m-st", statusText(st)) : "";
    let meta = ""; let aux = ""; let stw = "";
    const nm = name(PD, n.id);
    let aria = nm + ", " + typeLabel(n) + (n.type !== "group" ? ", " + statusText(st) : "");
    switch (n.type) {
      case "external": {
        if (/internet/i.test(n.id)) {
          const tr = (S.ingress && S.ingress.traffic && S.ingress.traffic.last_1h && S.ingress.traffic.last_1h.by_via) || {};
          meta = html`${T("m-u", "공개 요청 1시간 ")}${T("m-v", fmt.num(tr.tunnel || 0) + "건")}`;
          aria = nm + " — 최근 1시간 공개 요청 " + fmt.num(tr.tunnel || 0) + "건";
        } else {
          const tn = (S.ingress && S.ingress.tunnel) || {};
          const rtt = h.mean((tn.rtt_ms || []).filter(h.isNum));
          // 지연을 먼저, 엣지 위치는 하나씩(넘치면 뒤 위치부터 빠지고 전체는 툴팁으로)
          const locs = tn.edge_locations || [];
          const parts = (rtt != null ? [Math.round(rtt) + "ms"] : []).concat(locs.length ? locs : [n.detail || "–"]);
          meta = html`${parts.map((x, i) => html`${i ? sep : ""}${T("m-v", x)}`)}`;
          aria = nm + " — 엣지 " + ((tn.edge_locations || []).join(", ") || n.detail || "");
        }
        t = n.status && n.status !== "up" ? t : "ok";
        break;
      }
      case "segment": {
        if (PD.segTn === n) {
          const ts = S.tailscale || {};
          meta = ts.peers_total != null ? html`${T("m-v", fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total))}${T("m-u", " 기기 온라인")}` : T("m-v", n.detail || "");
          aria = "Tailnet 구간 — " + (ts.peers_total != null ? fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total) + " 기기 온라인" : n.detail || "");
        } else {
          const lan = PD.segLan === n ? PD.lan : { n: 0, held: 0 };
          meta = html`${T("m-u", "게이트웨이 ")}${T("m-v", n.gateway || "–")}${lan.n || lan.held ? html`${sep}${T("m-u", lanText(PD, true))}` : ""}`;
          aria = (n.label || "LAN") + " 구간 — 게이트웨이 " + (n.gateway || "알 수 없음") + ", 같은 LAN 기기 " + lan.n + "대" + (lan.held ? ", 잠시 안 보임 " + lan.held + "대" : "");
        }
        t = n.status && n.status !== "up" ? t : "ok";
        break;
      }
      case "device": {
        const sy = S.system || {}; const cpu = sy.cpu || {};
        const temp = (sy.thermal && sy.thermal.cpu_temp_c) != null ? sy.thermal.cpu_temp_c : cpu.temp_c;
        // 제목 칩은 중립색: 이름 + 모델·CPU·온도. 상태는 따로 기호 + 말(헤더와 같은 말: 주의 · 장애) + 까닭
        meta = html`${T("m-v", shortModel(n.detail || (sy.host && sy.host.model)) || "이 기기")}${cpu.usage_pct != null ? html`${sep}${T("m-u", "CPU ")}${T("m-v", fmt.pct(cpu.usage_pct))}` : ""}${temp != null ? html`${sep}${T("m-v", fmt.temp(temp))}` : ""}`;
        const br = st && st !== "up" && SHs().alertBrief ? SHs().alertBrief(S) : null;
        stw = st && st !== "up" ? devWord(st) + (br && br.text ? " · " + br.text : "") : "";
        aria = nm + " — 이 기기, " + (n.detail || "") + ", " + (stw || statusText(st)) + (cpu.usage_pct != null ? ", CPU " + fmt.pct(cpu.usage_pct) : "") + (temp != null ? ", " + fmt.temp(temp) : "");
        break;
      }
      case "iface": {
        const ips = (n.ips || []).filter(Boolean);
        meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", ips[0] || n.detail || "–")}${h.isNum(n.rx_bps) ? html`${sep}${T("m-u", "↓")}${T("m-v", fmt.bps(n.rx_bps, 0))}${T("m-u", " ↑")}${T("m-v", fmt.bps(n.tx_bps, 0))}` : ""}`;
        aux = n.kind === "wifi" && n.signal_dbm != null ? Math.round(n.signal_dbm) + " dBm" : speedT(n.speed_mbps) || IFK_L[n.kind] || "";
        aria = nm + " 인터페이스(" + (IFK_L[n.kind] || n.kind || "") + "), " + (ips.join(", ") || "주소 없음") + (aux ? ", " + aux : "") + ", " + statusText(st);
        break;
      }
      case "engine": {
        const cs = S.containers || [];
        const run = cs.filter((c) => c.state === "running").length;
        const nets = PD.kids(n.id).filter((k) => k.type === "network").length;
        meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-u", "컨테이너 ")}${T("m-v", run + "/" + cs.length)}${sep}${T("m-u", "네트워크 ")}${T("m-v", String(nets))}`;
        aria = "Docker 엔진, " + statusText(st) + ", 컨테이너 " + run + "/" + cs.length + " 실행, 도커 네트워크 " + nets + "개";
        break;
      }
      case "network": {
        const k = PD.kids(n.id);
        const inb = PD.edges.filter((e) => e.kind === "bridge" && e.to === n.id).length;
        meta = html`${T("m-v", n.subnet || n.detail || "–")}${n.driver && n.driver !== "bridge" ? html`${sep}${T("m-u", n.driver)}` : ""}${sep}${T("m-u", k.length + "개" + (inb ? " + 다른 망에서 " + inb : ""))}`;
        t = k.length ? worst(k) : h.tone(st);
        aria = "도커 네트워크 " + nm + ", 서브넷 " + (n.subnet || "알 수 없음") + ", 컨테이너 " + k.length + "개" + (inb ? ", 다른 망에서 이 망에도 붙은 컨테이너 " + inb + "개" : "");
        break;
      }
      case "group": {
        const k = PD.kids(n.id);
        if (n.role === "offline") {
          const last = Math.max(0, ...k.map((x) => x.last_seen || 0));
          meta = html`${T("m-u", "최근 ")}${T("m-v", last ? h.ago(last) : "–")}`;
          t = "muted";
          aria = "오프라인 기기 " + k.length + "대 — 가장 최근 접속 " + (last ? h.ago(last) : "알 수 없음");
        } else {
          const note = n.role === "hostnet" ? "기기 인터페이스 공유" : n.role === "procs" ? "systemd·pm2·일반" : n.role === "nonet" ? "격리" : "";
          meta = html`${note ? html`${T("m-u", note)}${sep}` : ""}${T("m-v", k.length + "개")}`;
          t = worst(k);
          aria = nm + " — " + (note ? note + ", " : "") + k.length + "개";
        }
        break;
      }
      case "container": case "process": {
        const bound = PD.edges.filter((e) => e.kind === "bind" && e.from === n.id);
        const pl = portList(PD, n);
        const ports = pl.join(" ");
        meta = bad || html`${pl.map((x, i) => html`${i ? T("m-sep", " ") : ""}${T(/^\+/.test(x) ? "m-u" : bound.length ? "m-bind" : "m-v", x)}`)}`;
        const sv = n.svcs[0];
        aria += (ports ? ", 포트 " + ports : "") + (bound.length ? ", " + bound.map((e) => name(PD, e.to)).join("·") + " 에만 바인딩" : "") + (sv ? ", 서비스 " + n.svcs.map((x) => x.name).join(", ") : "") + (n.host_network ? ", host 네트워크" : "");
        if (sv && sv.status_reason && st !== "up") aria += ". " + sv.status_reason;
        break;
      }
      case "peer": {
        if (n.source === "neighbor-cache") {
          meta = T("m-v", n.online === true ? "LAN 응답 관측" : "LAN 주소 관측 · 현재 상태 미확인");
          aria = nm + ", LAN 이웃 테이블, " + (n.online === true ? "응답 관측" : "현재 온라인 여부 미확인");
          break;
        }
        if (n.online === false) {
          meta = html`${T("m-u", "마지막 ")}${T("m-v", n.last_seen ? h.ago(n.last_seen) : "–")}`;
          t = "muted";
          aria = nm + ", 오프라인, 마지막 접속 " + (n.last_seen ? h.ago(n.last_seen) : "알 수 없음");
        } else {
          // 한 가지 사실은 한 번만: 선에 경로 알약(직결·중계·유휴)이 붙는 카드는 IP·OS 만,
          // 빗살 배선(같은 LAN 기기)·같은 기기 안의 노드는 선에 알약이 없으니 경로를 여기에
          const lanE = PD.segLan && PD.edgesOf(n.id).find((e) => e.kind === "lan");
          const kid = pAnc(PD, n);
          let first; let second; let strong = false;
          if (kid) { first = n.ip || ""; second = "같은 기기"; strong = true; }
          else if (lanE) { first = "LAN " + (n.lan_ip || lanE.label || ""); second = n.lan_stale ? "잠시 안 보임" : connText(n); strong = !n.lan_stale && n.connection === "direct"; }
          else { first = n.ip || ""; second = n.via_gw ? "공유기 NAT 경유" : n.os || ""; }
          meta = html`${bad ? html`${bad}${sep}` : ""}${T("m-v", first || "–")}${second ? html`${sep}${T(strong ? "m-v" : "m-u", second)}` : ""}`;
          aria = nm + " (" + (n.os || "") + "), " + statusText(st) + ", Tailnet " + (n.ip || "") + (n.lan_ip && n.on_lan ? ", LAN " + n.lan_ip : "") + ", " + connText(n) + (n.via_gw ? ", 공유기 NAT 경유" : "") + (n.same_device ? ", " + name(PD, n.parent) + " 과 같은 기기" : "");
        }
        break;
      }
      default: {
        meta = bad || T("m-v", n.detail || "");
      }
    }
    return { tone: t, meta, aux, aria, stw };
  }
  /** 기기 상태의 말(헤더와 같은 어휘: 주의 · 장애) */
  const devWord = (st) => ((SHs().DEV_STATUS || {})[st] || (st === "degraded" ? "주의" : st === "down" ? "장애" : h.statusText(st)));
  const pAnc = (PD, n) => !!(n.parent && PD.nodes.get(n.parent) && PD.nodes.get(n.parent).type === "peer");
  /** 오프라인 묶음 이름(개수 포함) */
  const groupTitle = (PD, n) => (n.role === "offline" ? "오프라인 " + PD.kidIds(n.id).length + "대" : name(PD, n.id));

  // ------------------------------------------------------------------ 강조: 올린 노드의 포함 관계 + 실제 연결(+ 인터넷까지)
  /** 칩이 돌리는 서비스가 지나는 관문: 공개(Cloudflare 터널 → cloudflared) · Tailnet HTTPS(Caddy) */
  function gates(PD, n) {
    const via = new Set();
    for (const sv of (n && n.svcs) || []) for (const v of (sv.exposure && sv.exposure.via) || []) via.add(v);
    const out = [];
    if (via.has("cloudflare-tunnel")) for (const e of PD.edges) if (e.kind === "tunnel" && e.from !== n.id && PD.nodes.has(e.from)) out.push({ id: e.from, why: "tunnel" });
    if (via.has("caddy-https") || via.has("cloudflare-tunnel")) {
      for (const c of PD.nodes.values()) if (c !== n && (c.type === "container" || c.type === "process") && (c.svcs || []).some((x) => x.kind === "proxy")) out.push({ id: c.id, why: "proxy" });
    }
    return out;
  }
  /** → { N: 노드 id, E: 엣지 key, V: [{from: 칩, to: 인터페이스}] 가상 연결(열린 포트), pub: {ports, ifs}, via: [관문] } */
  function hl(PD, id) {
    const N = new Set(); const E = new Set(); const V = []; let pub = null; let via = [];
    const n = PD && PD.nodes.get(id);
    if (!n) return { N, E, V, pub, via };
    const core = [id].concat(PD.desc(id));
    PD.anc(id).forEach((a) => N.add(a));
    core.forEach((x) => N.add(x));
    const frontier = [];
    const take = (e, from) => {
      if (E.has(e.key)) return;
      E.add(e.key);
      const o = e.from === from ? e.to : e.from;
      if (!N.has(o)) { N.add(o); frontier.push(o); }
    };
    const reach = (x) => { if (!N.has(x)) { N.add(x); frontier.push(x); } };
    for (const d of core) for (const e of PD.edgesOf(d)) take(e, d);
    // 열린 포트: 컨테이너·프로세스가 기기 밖으로 연 포트 → 그 포트가 걸린 인터페이스 → 구간 → 인터넷(실제 선은 없어 가상 연결로)
    const chipLike = (x) => x && (x.type === "container" || x.type === "process");
    for (const d of core) {
      const dn = PD.nodes.get(d);
      if (!chipLike(dn)) continue;
      const p = published(PD, dn);
      if (d === id) pub = p;
      for (const i of p.ifs) { reach(i); if (d === id) V.push({ from: d, to: i }); }
    }
    // 서비스가 지나는 관문(공개 터널·Caddy)도 두 번째 길로
    if (chipLike(n)) {
      via = gates(PD, n);
      for (const g of via) {
        const gn = PD.nodes.get(g.id);
        reach(g.id); PD.anc(g.id).forEach((a) => N.add(a));
        if (g.why === "tunnel") for (const e of PD.edgesOf(g.id)) if (e.kind === "tunnel") take(e, g.id);
        if (g.why === "proxy") for (const i of published(PD, gn).ifs) reach(i);
      }
    }
    // 오프라인 묶음 안의 기기: 묶음의 선
    for (const a of PD.anc(id)) { const an = PD.nodes.get(a); if (an && an.type === "group" && an.role === "offline") for (const e of PD.edgesOf(a)) take(e, a); }
    let g = 0;
    while (frontier.length && g++ < 80) {
      const o = frontier.shift(); const on = PD.nodes.get(o);
      if (!on) continue;
      PD.anc(o).forEach((a) => N.add(a));
      if (on.type === "segment" || on.type === "external") {
        // 바깥쪽으로만: LAN → (업링크) → 인터넷, Cloudflare → (엣지) → 인터넷. 인터넷에서 되돌아오지 않는다
        for (const e of PD.outE.get(o) || []) if (e.kind === "uplink" || e.kind === "edge") take(e, o);
      } else if (on.type === "iface") {
        for (const e of PD.edgesOf(o)) if (["wired", "wifi", "overlay"].includes(e.kind)) take(e, o);
      }
    }
    return { N, E, V, pub, via };
  }
  /** 가상 연결(열린 포트)의 모양: 칩 → 테두리의 인터페이스. 올렸을 때만 옅게 그린다(배치는 그대로). */
  function virt(PD, L, on) {
    const out = [];
    for (const v of (on && on.V) || []) {
      const a = L.pos.get(v.from); const b = L.pos.get(v.to);
      if (!a || !b) continue;
      const bx = b.x + b.w / 2; const top = b.side !== "bottom";
      const by = top ? b.y + b.h : b.y;
      const ax = a.x + a.w / 2; const ay = top ? a.y : a.y + a.h;
      const k = (by - ay) * 0.45;
      out.push({ d: `M${f1(ax)} ${f1(ay)}C${f1(ax)} ${f1(ay + k)} ${f1(bx)} ${f1(by - k)} ${f1(bx)} ${f1(by)}`, kind: (PD.nodes.get(v.to) || {}).kind || "other" });
    }
    return out;
  }
  /** 요약 칸(인터페이스·컨테이너 …)을 가리킬 때 밝힐 것 */
  function cellSet(PD, cell) {
    const N = new Set(); const E = new Set();
    if (!PD) return { N, E };
    const addHl = (id) => { const r = hl(PD, id); r.N.forEach((x) => N.add(x)); r.E.forEach((x) => E.add(x)); };
    const all = [...PD.nodes.values()];
    if (cell === "iface") all.filter((n) => n.type === "iface").forEach((n) => addHl(n.id));
    else if (cell === "ctr") all.filter((n) => n.type === "container").forEach((n) => { N.add(n.id); PD.anc(n.id).forEach((a) => N.add(a)); });
    else if (cell === "net") all.filter((n) => n.type === "network").forEach((n) => { N.add(n.id); PD.anc(n.id).forEach((a) => N.add(a)); PD.edgesOf(n.id).forEach((e) => { E.add(e.key); N.add(e.from); }); });
    else if (cell === "proc") all.filter((n) => n.type === "process").forEach((n) => { N.add(n.id); PD.anc(n.id).forEach((a) => N.add(a)); PD.edgesOf(n.id).forEach((e) => { E.add(e.key); N.add(e.to); }); });
    else if (cell === "lan" && PD.segLan) addHl(PD.segLan.id);
    else if (cell === "tn" && PD.segTn) addHl(PD.segTn.id);
    return { N, E };
  }
  /** 고른 노드를 읽을 수 있게 담을 사각형들 → { all, near }
   *  all  = 노드 + 강조되는 것(조상 상자의 머리 줄 · 테두리의 인터페이스 · 구간 알약 · 인터넷·Cloudflare · 이어진 카드)
   *  near = 너무 넓으면 대신 쓸 것: 노드 + 기기 제목·테두리 인터페이스 */
  function focusRects(PD, L, id) {
    const p = L.pos.get(id);
    if (!p) return { all: [], near: [] };
    const on = hl(PD, id);
    const head = (r) => (r.role === "box" ? { x: r.x, y: r.y, w: r.w, h: r.hh || 28 } : r.role === "dev" ? r.tab : r);
    const all = [p]; const near = [p];
    for (const x of on.N) {
      if (x === id) continue;
      const r = L.pos.get(x);
      if (!r) continue;
      all.push(head(r));
      if (r.role === "port" || r.role === "dev") near.push(head(r));
    }
    return { all, near };
  }

  // ------------------------------------------------------------------ 설명 띠(가리킨 것)
  function crumbs(PD, id) { return PD.anc(id).reverse().concat([id]).map((x) => name(PD, x)); }
  function caption(PD, id) {
    const { html } = h;
    const n = PD.nodes.get(id);
    if (!n) return "";
    const es = PD.edgesOf(id);
    const MAXL = 4;
    const links = es.slice(0, MAXL).map((e) => {
      const o = e.from === id ? e.to : e.from;
      return html`<li><span class="hop-e" data-k="${e.kind}"><svg viewBox="0 0 14 8" aria-hidden="true"><path d="M0 4h12M9 1l3 3-3 3"/></svg></span><span class="hop">${KIND_L[e.kind] || e.kind}</span> <span class="cap-o">${PD.nodes.get(o) && PD.nodes.get(o).type === "group" ? groupTitle(PD, PD.nodes.get(o)) : name(PD, o)}</span>${e.label && !NO_CAP_LABEL.has(e.kind) ? html` <i class="cap-l">${e.label}</i>` : ""}</li>`;
    });
    if (es.length > MAXL) links.push(html`<li class="cap-more">외 ${es.length - MAXL}개</li>`);
    // 열린 포트 → 인터페이스 → 구간: "이 칩은 어느 인터페이스로 닿는가"
    const on = n.type === "container" || n.type === "process" ? hl(PD, id) : null;
    let pubLi = "";
    if (on && on.pub && on.pub.ifs.size) {
      const ifs = [...on.pub.ifs].map((x) => PD.nodes.get(x)).filter(Boolean).sort((a, b) => (IF_RANK[a.kind] ?? 5) - (IF_RANK[b.kind] ?? 5));
      const segs = [];
      for (const i of ifs) for (const e of PD.edgesOf(i.id)) { const o = PD.nodes.get(e.from === i.id ? e.to : e.from); if (o && o.type === "segment" && !segs.includes(o)) segs.push(o); }
      pubLi = html`<li class="cap-pub"><span class="hop">열린 포트</span> <span class="cap-o mono">${on.pub.ports.map((x) => ":" + x).join(" ")}</span> → <span class="cap-o">${ifs.map((i) => i.label).join(" · ")}</span>${segs.length ? html` → <span class="cap-o">${segs.map((x) => name(PD, x.id).replace(/\s.*$/, "")).join(" · ")}</span>` : ""}</li>`;
    } else if (on && (n.type === "container" || n.type === "process")) {
      pubLi = html`<li class="cap-pub"><span class="hop">열린 포트</span> <span class="cap-o">기기 밖으로 연 포트 없음</span></li>`;
    }
    const viaLi = on && on.via.length ? html`<li class="cap-pub"><span class="hop">거치는 관문</span> <span class="cap-o">${[...new Set(on.via.map((g) => (g.why === "tunnel" ? "Cloudflare 터널(" + name(PD, g.id) + ")" : "Caddy(" + name(PD, g.id) + ")")))].join(" · ")}</span></li>` : "";
    const svcs = n.svcs.length ? html`<li class="cap-svc">서비스 ${n.svcs.map((s) => s.name).join(" · ")}</li>` : "";
    return html`<p class="cap-title"><b>${n.type === "group" ? groupTitle(PD, n) : name(PD, id)}</b><span>${typeLabel(n)}</span></p>${PD.anc(id).length ? html`<p class="cap-crumb">${crumbs(PD, id).join(" › ")}</p>` : ""}${links.length || svcs || pubLi ? html`<ul class="cap-links">${pubLi}${viaLi}${links}${svcs}</ul>` : ""}`;
  }
  const NO_CAP_LABEL = new Set([]);

  // ------------------------------------------------------------------ 검사기
  /** → { kicker, kickIcon, title, sub, tone, statusPill, homeHref, homeLabel, openUrl, svcs, body } */
  function inspect(PD, id) {
    const { html, raw, fmt, rows, grp, iSec, pill, shape, statusText } = h;
    const n = PD.nodes.get(id);
    if (!n) return null;
    const S = PD.s || {};
    const t = info(PD, n).tone;
    const nm = n.type === "group" ? groupTitle(PD, n) : name(PD, id);
    const sel = (x, label, tone) => html`<button type="button" class="dep" data-select="${x}" data-fk="${"p:" + id + ":" + x}" data-tone="${tone || h.tone((PD.nodes.get(x) || {}).status)}">${shape(tone || h.tone((PD.nodes.get(x) || {}).status))}${label || name(PD, x)}</button>`;
    const where = PD.anc(id).length ? html`<nav class="p-where" aria-label="위치">${PD.anc(id).reverse().map((a, i) => html`${i ? html`<span class="p-sep" aria-hidden="true">›</span>` : ""}<button type="button" class="linkish" data-select="${a}" data-fk="${"w:" + a}">${PD.nodes.get(a).type === "group" ? groupTitle(PD, PD.nodes.get(a)) : name(PD, a)}</button>`)}<span class="p-sep" aria-hidden="true">›</span><b>${nm}</b></nav>` : "";
    const linkList = () => {
      const es = PD.edgesOf(id);
      // 컨테이너·프로세스: 기기 밖으로 연 포트가 걸린 인터페이스(실제 선은 없지만 "어디로 닿는가"의 답)
      const pb = n.type === "container" || n.type === "process" ? published(PD, n) : null;
      const pubLi = pb && pb.ifs.size ? html`<li data-k="published"><i class="ln" aria-hidden="true"></i><span class="pl-k">열린 포트</span><span class="pl-ifs">${[...pb.ifs].map((x) => PD.nodes.get(x)).filter(Boolean).sort((a, b) => (IF_RANK[a.kind] ?? 5) - (IF_RANK[b.kind] ?? 5)).map((i, k) => html`${k ? " · " : ""}<button type="button" class="linkish" data-select="${i.id}" data-fk="${"pub:" + i.id}">${i.label}</button>`)}</span><span class="pl-l mono">${pb.ports.map((x) => ":" + x).join(" ")}</span></li>` : "";
      if (!es.length && !pubLi) return "";
      return grp(html`<ul class="p-links">${pubLi}${es.map((e) => {
        const o = e.from === id ? e.to : e.from;
        return html`<li data-k="${e.kind}"><i class="ln" aria-hidden="true"></i><span class="pl-k">${KIND_L[e.kind] || e.kind}</span><button type="button" class="linkish" data-select="${o}" data-fk="${"l:" + e.key}">${PD.nodes.get(o) && PD.nodes.get(o).type === "group" ? groupTitle(PD, PD.nodes.get(o)) : name(PD, o)}</button>${e.label ? html`<span class="pl-l mono">${e.label}</span>` : ""}</li>`;
      })}</ul>`);
    };
    const members = (list, title) => (list.length ? iSec(title, grp(html`<p class="deps">${list.map((k) => sel(k.id, k.type === "group" ? groupTitle(PD, k) : null, k.type === "group" ? info(PD, k).tone : null))}</p>`)) : "");
    const blocks = [];
    let desc = ""; let topBlock = "";
    // Caddy 가 직접 응답하는 정적 라우트(뒤에 앱·컨테이너가 없어 물리 지도에는 따로 칩이 없다) — 개념 지도의 그 라우트로
    const staticSec = () => {
      const st = staticFor(PD, n);
      if (!st.length) return;
      blocks.push(iSec("직접 응답하는 라우트 " + st.length, grp(html`<p class="p-why">뒤에 앱·컨테이너 없이 이 Caddy 가 바로 응답하는 라우트예요 — 누르면 개념 지도에서 그 길을 봐요.</p><p class="deps">${st.map((x) => html`<button type="button" class="dep" data-act="view" data-view="concept" data-target="${x.id}" data-fk="${"st:" + x.id}" data-tone="${h.tone(x.status)}">${shape(h.tone(x.status))}${x.name}</button>`)}</p>`)));
    };
    let homeHref = "/#network"; let homeLabel = "개요에서 보기";
    const sv0 = n.svcs[0] || null;
    switch (n.type) {
      case "device": {
        const sy = S.system || {}; const host = sy.host || {}; const mem = sy.memory || {}; const cpu = sy.cpu || {};
        desc = n.self ? "Jarvis 가 도는 이 기기예요. 테두리의 칩이 네트워크 인터페이스, 안의 상자가 Docker 엔진과 호스트 프로세스예요." : "";
        // 왜 주의·장애인가: 기기 상태는 경보 중 가장 높은 등급 — 그 경보들을 맨 위에(각 경보는 지도의 대상이나 개요로)
        if (n.status && n.status !== "up") {
          const br = SHs().alertBrief ? SHs().alertBrief(S) : { list: [] };
          const word = devWord(n.status);
          topBlock = iSec("왜 " + word + "인가", grp(html`<p class="p-why">이 기기의 상태는 경보 중 가장 높은 등급으로 정해져요 — 지금 ${br.list.length ? "경고 이상 경보 " + br.list.length + "건" : "경고 이상 경보가 없어 곧 풀려요"}.</p>${br.list.length && h.alertItems ? h.alertItems(br.list, { self: id }) : ""}`), "p-whysec");
        }
        blocks.push(iSec("기기", grp(rows([
          ["모델", host.model || n.detail], ["OS", host.os || n.os], ["커널 · 아키텍처", [host.kernel, host.arch].filter(Boolean).join(" · ") || null],
          ["코어", fmt.num(host.cores || n.cores) + "개" + (host.cpu_max_mhz ? " · 최대 " + fmt.mhz(host.cpu_max_mhz) : "")],
          ["메모리", mem.total != null ? fmt.bytes(mem.used) + " / " + fmt.bytes(mem.total) + " (" + fmt.pct(mem.usage_pct) + ")" : n.mem_total ? fmt.bytes(n.mem_total) : null],
          ["CPU", cpu.usage_pct != null ? fmt.pct(cpu.usage_pct) + (cpu.temp_c != null ? " · " + fmt.temp(cpu.temp_c) : "") : null],
          ["가동", sy.uptime_s != null ? fmt.dur(sy.uptime_s, 2) : null],
        ]))));
        const kids = PD.kids(id);
        blocks.push(members(kids.filter((k) => k.type === "iface"), "인터페이스"));
        blocks.push(members(kids.filter((k) => k.type !== "iface"), "안에 있는 것"));
        homeHref = "/#hardware"; homeLabel = "하드웨어에서 자세히";
        break;
      }
      case "iface": {
        const ifc = ifaceOf(PD, n) || {};
        const wifi = ifc.wifi || null;
        desc = n.kind === "tailnet" ? "Tailscale 의 가상 인터페이스예요. 이 주소로 오는 것은 tailnet 안의 내 기기들뿐이에요." : n.kind === "wifi" ? "무선 LAN 인터페이스예요." : "유선 LAN 인터페이스예요.";
        blocks.push(iSec("인터페이스", grp(rows([
          ["종류", IFK_L[n.kind] || n.kind], ["상태", ifc.up === false ? html`<span class="t-crit">내려감</span>` : "올라옴"],
          ["IPv4", html`<span class="mono">${(ifc.ipv4 || n.ips || []).join(", ") || "–"}</span>`],
          (ifc.ipv6 || []).length ? ["IPv6", html`<span class="mono">${ifc.ipv6.join(", ")}</span>`] : null,
          ifc.mac ? ["MAC", html`<span class="mono">${ifc.mac}</span>`] : null,
          n.speed_mbps != null ? ["링크 속도", speedT(n.speed_mbps)] : null,
          wifi || n.signal_dbm != null ? ["신호", (n.signal_dbm != null ? Math.round(n.signal_dbm) + " dBm" : "–") + (wifi && wifi.quality != null ? " · 품질 " + fmt.pct(wifi.quality) : "")] : null,
          ["받기 · 보내기", fmt.bps(n.rx_bps) + " · " + fmt.bps(n.tx_bps)],
          ifc.rx_total != null ? ["누적", "받음 " + fmt.bytes(ifc.rx_total) + " · 보냄 " + fmt.bytes(ifc.tx_total)] : null,
        ]))));
        const bound = PD.edges.filter((e) => e.kind === "bind" && e.to === id);
        if (bound.length) blocks.push(iSec("이 인터페이스에만 연 포트", grp(html`<ul class="p-links">${bound.map((e) => html`<li data-k="bind"><i class="ln" aria-hidden="true"></i><button type="button" class="linkish" data-select="${e.from}" data-fk="${"b:" + e.key}">${name(PD, e.from)}</button><span class="pl-l mono">${e.label || ""}</span></li>`)}</ul>`)));
        homeHref = "/#hw-net"; homeLabel = "네트워크 계기에서 자세히";
        break;
      }
      case "engine": {
        const cs = S.containers || []; const pj = S.projects || [];
        desc = "Docker 가 컨테이너를 돌리는 곳이에요. 안의 상자가 도커 네트워크(서브넷), 그 안의 칩이 컨테이너예요.";
        blocks.push(iSec("Docker", grp(rows([
          ["컨테이너", cs.filter((c) => c.state === "running").length + " 실행 / " + cs.length],
          ["헬스", (S.summary && S.summary.containers) ? fmt.num(S.summary.containers.healthy) + " 정상 · " + fmt.num(S.summary.containers.unhealthy) + " 비정상" : null],
          ["도커 네트워크", PD.kids(id).filter((k) => k.type === "network").length + "개"],
          ["compose 프로젝트", pj.length ? pj.length + "개" : null],
        ]))));
        blocks.push(members(PD.kids(id), "네트워크 · 묶음"));
        homeHref = "/#runtime"; homeLabel = "런타임에서 자세히";
        break;
      }
      case "network": {
        const inb = PD.edges.filter((e) => e.kind === "bridge" && e.to === id).map((e) => PD.nodes.get(e.from)).filter(Boolean);
        desc = "도커 브리지 네트워크예요. 같은 네트워크의 컨테이너끼리는 이름으로 서로 닿아요.";
        blocks.push(iSec("네트워크", grp(rows([["서브넷", html`<span class="mono">${n.subnet || n.detail || "–"}</span>`], ["드라이버", n.driver || "–"], ["컨테이너", PD.kids(id).length + "개"], inb.length ? ["다른 망에서 붙은 것", inb.length + "개"] : null]))));
        blocks.push(members(PD.kids(id), "이 네트워크의 컨테이너"));
        blocks.push(members(inb, "다른 망에 있으면서 이 망에도 붙은 컨테이너"));
        homeHref = "/#runtime"; homeLabel = "런타임에서 자세히";
        break;
      }
      case "group": {
        const k = PD.kids(id);
        if (n.role === "hostnet") desc = "host 네트워크 모드 컨테이너예요. 도커 네트워크 없이 기기의 인터페이스(eth0·wlan0·tailscale0)를 그대로 써서, 포트를 기기에 바로 열어요.";
        else if (n.role === "procs") desc = "컨테이너가 아니라 기기에서 바로 도는 systemd 유닛·pm2 앱·일반 프로세스예요. 칩의 포트가 청록색이면 tailscale0 에만 열린 포트예요.";
        else if (n.role === "offline") desc = "지금 오프라인인 tailnet 기기예요. 마지막으로 접속한 순서예요.";
        else if (n.role === "nonet") desc = "네트워크에 붙지 않은(격리된) 컨테이너예요.";
        if (n.role === "offline") blocks.push(iSec("오프라인 기기 " + k.length + "대", grp(html`<ul class="p-offl">${k.slice().sort((a, b) => (b.last_seen || 0) - (a.last_seen || 0)).map((p) => html`<li><button type="button" class="linkish" data-select="${p.id}" data-fk="${"off:" + p.id}">${name(PD, p.id)}</button><span class="sub-i">${p.os || ""}</span><span class="pl-l">${p.last_seen ? h.ago(p.last_seen) : "–"}</span></li>`)}</ul>`)));
        else blocks.push(members(k, "구성원 " + k.length));
        homeHref = n.role === "offline" ? "/#network" : "/#runtime";
        break;
      }
      case "container": {
        const c = (S.containers || []).find((x) => x.name === n.label) || {};
        const nets = (n.networks || c.networks || []);
        desc = n.host_network ? "host 네트워크 모드 — 기기의 인터페이스를 그대로 써요." : "";
        blocks.push(iSec("컨테이너", grp(rows([
          ["이미지", html`<span class="mono">${n.detail || c.image || "–"}</span>`],
          n.project || c.project ? ["프로젝트", (n.project || c.project) + (c.service ? " / " + c.service : "")] : null,
          ["상태", (SHs().runtimeState ? SHs().runtimeState({ state: c.state, health: c.health }) : c.state) || statusText(n.status)],
          ["네트워크", html`${nets.map((x, i) => { const nid = "net:" + x; return html`${i ? ", " : ""}${PD.nodes.has(nid) ? html`<button type="button" class="linkish" data-select="${nid}" data-fk="${"cn:" + nid}">${x}</button>` : html`<span class="mono">${x}</span>`}`; })}`],
          (n.ports || []).length ? ["게시한 포트", html`<span class="mono">${n.ports.join(", ")}</span>`] : null,
          c.uptime_s != null ? ["가동", fmt.dur(c.uptime_s, 2)] : null,
          c.restart_count != null ? ["재시작", fmt.num(c.restart_count) + "회"] : null,
          c.cpu_pct != null || c.mem_bytes != null ? ["CPU · 메모리", fmt.pct(c.cpu_pct, 1) + " · " + fmt.bytes(c.mem_bytes)] : null,
        ]))));
        staticSec();
        break;
      }
      case "process": {
        const bound = PD.edges.filter((e) => e.kind === "bind" && e.from === id);
        const rt = (sv0 && sv0.runtime) || {};
        blocks.push(iSec("프로세스", grp(rows([
          n.unit ? ["유닛", html`<span class="mono">${n.unit}</span>`] : n.manager === "pm2" ? ["관리", html`pm2 앱 <span class="mono">${rt.name || n.label}</span>`] : ["종류", "일반 프로세스"],
          n.manager === "pm2" && rt.state ? ["상태", (SHs().runtimeState ? SHs().runtimeState(rt) : rt.state)] : null,
          n.manager === "pm2" && rt.restart_count != null ? ["재시작", fmt.num(rt.restart_count) + "회" + (rt.unstable_restarts ? " · 불안정 재시작 " + fmt.num(rt.unstable_restarts) + "회" : "")] : null,
          ["포트", html`<span class="mono">${procPorts(n).join(" ") || "–"}</span>`],
          bound.length ? ["바인딩", html`${bound.map((e, i) => html`${i ? ", " : ""}<button type="button" class="linkish" data-select="${e.to}" data-fk="${"pb:" + e.key}">${name(PD, e.to)}</button> <span class="mono">${e.label || ""}</span>`)} 에만`] : null,
        ]))));
        staticSec();
        break;
      }
      case "peer": {
        if (n.source === "neighbor-cache") {
          blocks.push(iSec("LAN 기기", grp(rows([["주소", n.ip], ["MAC", n.mac],
            ["상태", n.online === true ? "LAN 응답 관측" : "주소 관측 · 현재 온라인 여부 미확인"],
            ["근거", "서버의 LAN 이웃 테이블"], ["관측", h.ago(n.observed_at)]]))));
          desc = "이 서버가 통신하며 관측한 LAN 기기예요. 이웃 캐시에 주소가 남아 있어도 현재 온라인이라는 뜻은 아니에요.";
          break;
        }
        const par = n.parent && PD.nodes.get(n.parent);
        desc = n.same_device && par ? name(PD, par.id) + " 과 같은 LAN 주소를 쓰는 노드예요 — 같은 기기 안(예: Tailscale 사이드카 컨테이너)에서 도는 것으로 보여 그 안에 그렸어요." : "";
        blocks.push(iSec("Tailnet 기기", grp(rows([
          ["상태", n.online === false ? html`<span class="t-muted">오프라인</span>` : "온라인"],
          ["OS", n.os], n.hostname && n.hostname !== n.label ? ["호스트 이름", n.hostname] : null,
          ["Tailnet 주소", html`<span class="mono">${n.ip || "–"}</span>`],
          n.lan_ip ? ["LAN 주소", html`<span class="mono">${n.lan_ip}</span>${n.on_lan ? (n.lan_stale ? " · 같은 LAN(잠시 안 보임)" : " · 같은 LAN") : ""}${n.via_gw || (PD.segLan && PD.segLan.gateway && n.lan_ip === PD.segLan.gateway) ? html`<span class="sub">공유기(게이트웨이) 주소예요 — 같은 LAN 이 아니라 공유기 NAT 를 거쳐 닿는 연결로 보여 같은 LAN 기기에 넣지 않았어요.</span>` : n.lan_stale && n.lan_last ? html`<span class="sub">LAN 에서 마지막으로 본 지 ${h.ago(n.lan_last)} — 잠깐 사라진 것이면 그대로 두어요.</span>` : ""}`] : null,
          ["연결", n.online === false ? "–" : n.connection === "direct" ? "직결(WireGuard P2P)" : n.connection === "relay" ? "중계 · DERP " + (n.relay || "–") : "유휴(지금 오가는 연결 없음)" + (n.relay ? " · 홈 DERP " + n.relay : "")],
          // Tailscale 은 온라인 피어의 LastSeen 을 갱신하지 않는다 — 오프라인일 때만 뜻이 있다
          n.online === false && n.last_seen ? ["마지막 접속", h.ago(n.last_seen)] : null,
          (n.tags || []).length ? ["태그", html`<span class="mono">${n.tags.join(", ")}</span>`] : null,
        ]))));
        const kids = PD.kids(id);
        if (kids.length) blocks.push(members(kids, "같은 기기 안의 노드"));
        break;
      }
      case "segment": {
        if (PD.segTn === n) {
          const ts = S.tailscale || {}; const self = ts.self || {};
          desc = "Tailscale 로 묶인 내 기기들의 가상 네트워크예요. 직결(P2P)이 안 되면 DERP 중계 서버를 거쳐요. 유휴 = 온라인이지만 지금 오가는 연결이 없는 기기예요.";
          const mix = SHs().tailnetMix ? SHs().tailnetMix(S) : null;
          blocks.push(iSec("Tailscale", grp(rows([["상태", ts.backend_state], ["이 기기", html`<span class="mono">${self.dns_name || self.hostname || "–"}</span>`], ["주소", html`<span class="mono">${(self.ips || []).join(" · ") || "–"}</span>`], ["기기", fmt.num(ts.peers_online) + " / " + fmt.num(ts.peers_total) + " 온라인"], mix ? ["경로", mix.text] : null, ["버전", ts.version]]))));
          const on = PD.edgesOf(id).map((e) => (e.from === id ? e.to : e.from)).map((x) => PD.nodes.get(x)).filter((x) => x && x.type === "peer");
          blocks.push(members(on, "붙어 있는 기기"));
        } else {
          const open = (S.listeners || []).filter((l) => l.scope === "all" || l.scope === "lan").sort((a, b) => a.port - b.port);
          const peers = PD.edgesOf(id).filter((e) => e.kind === "lan").map((e) => PD.nodes.get(e.from === id ? e.to : e.from)).filter(Boolean);
          desc = "공유기 아래의 집 LAN 이에요. 인터넷으로는 공유기 NAT 로 나가요. 이 기기의 공개 입구는 Cloudflare Tunnel 하나예요(공유기 포트포워딩은 Jarvis 가 확인하지 못해요).";
          blocks.push(iSec("LAN", grp(rows([["대역", html`<span class="mono">${n.cidr || "–"}</span>`], ["게이트웨이", html`<span class="mono">${n.gateway || "–"}</span>`], ["이 기기 주소", html`<span class="mono">${((S.system && S.system.network && S.system.network.lan_ips) || []).join(" · ") || "–"}</span>`], ["LAN 에 열린 포트", open.length + "개"]]))));
          blocks.push(members(peers, "같은 LAN 기기"));
          if (open.length) blocks.push(iSec("LAN 에 열린 포트", grp(html`<ul class="ports">${open.map((l) => html`<li><b class="mono">${l.port}</b><span class="mono pt-p">${l.proto}</span><span class="pt-o">${l.process || "?"}${l.owner ? html` <span class="pt-w">· ${l.owner}</span>` : ""}</span></li>`)}</ul>`)));
        }
        break;
      }
      case "external": {
        if (/internet/i.test(id)) {
          desc = "이 기기의 공개 서비스로 들어오는 입구는 Cloudflare Tunnel 하나예요(공유기 포트포워딩은 Jarvis 가 확인하지 못해요).";
        } else {
          desc = "공개 요청은 Cloudflare 엣지에서 TLS 가 끝나고, 이 기기의 cloudflared 가 밖으로 연 QUIC 터널로 들어와요.";
          if (h.tunnelBlock) blocks.push(iSec("터널", h.tunnelBlock()));
        }
        break;
      }
      default: break;
    }
    const links = linkList();
    const body = html`${topBlock}${desc ? html`<div class="istatus"><p class="desc">${desc}</p></div>` : ""}${where ? html`<div class="p-wrap">${where}</div>` : ""}${iSec("연결", links)}${blocks}`;
    if (sv0) { homeHref = "/#svc=" + encodeURIComponent(sv0.id); homeLabel = "개요에서 자세히"; }
    const kick = typeLabel(n) + (n.type === "container" && n.host_network ? " · host 네트워크" : "") + (n.type === "process" && n.manager === "pm2" ? " · pm2" : "");
    const sub = n.type === "container" ? n.detail : n.type === "iface" ? (n.ips || []).join(" · ") : n.type === "network" ? n.subnet : n.type === "peer" ? n.ip : n.type === "process" ? n.unit || (n.manager ? n.manager + " 앱" : "") : n.type === "device" ? n.detail : n.type === "segment" ? (n.cidr || "") : "";
    return { kicker: kick, title: nm, sub: sub && sub !== nm ? sub : "", tone: t, svcs: n.svcs, homeHref, homeLabel, body };
  }
  const SHs = () => (h && h.SH) || {};

  // ------------------------------------------------------------------ 모바일: 접히는 기기 나무
  /** open: Set(열린 나무 칸 id). 기본으로 기기·엔진·구간이 열려 있다(atlas.js 가 정한다).
   *  순서: 바깥(인터넷 ⇄ Cloudflare) → 이 기기 → LAN → Tailnet. 한 가지 사실은 한 번만(오른쪽 = 경로, 아래 줄 = 주소). */
  function tree(PD, open, selected) {
    const { html, raw, shape } = h;
    const isOpen = (id) => open.has(id);
    const nw = (v) => html`<span class="nw">${v}</span>`;
    const row = (n, opts) => {
      opts = opts || {};
      const m = info(PD, n);
      const kids = opts.kids || [];
      const tid = "pt-" + String(n.id).replace(/[^A-Za-z0-9_-]/g, "_");
      const o = kids.length && isOpen(n.id);
      const metaTxt = P0(m.meta);
      const nm = n.type === "group" ? groupTitle(PD, n) : name(PD, n.id);
      const selBtn = html`<button type="button" class="${"pt-sel" + (n.id === selected ? " is-sel" : "")}" data-select="${n.id}" data-fk="${"pt:" + n.id}" aria-current="${n.id === selected ? "true" : "false"}">${shape(m.tone)}<span class="pt-main"><span class="pt-name">${nm}</span><span class="pt-sub">${opts.sub != null ? opts.sub : metaTxt}</span></span>${opts.right ? html`<span class="pt-r">${opts.right}</span>` : ""}</button>`;
      return html`<li class="${"pt-li pt-" + n.type + (n.role ? " pt-" + n.role : "")}" data-tone="${m.tone}">
<div class="pt-row">${kids.length ? html`<button type="button" class="pt-tog" data-tree="${n.id}" data-fk="${"tog:" + n.id}" aria-expanded="${o ? "true" : "false"}" aria-controls="${tid}"><svg class="ic" viewBox="0 0 24 24" aria-hidden="true"><path d="m9 6 6 6-6 6"/></svg><span class="sr-only">${(o ? "접기: " : "펼치기: ") + nm}</span></button>` : html`<span class="pt-tog-sp" aria-hidden="true"></span>`}${selBtn}</div>
${kids.length ? html`<ul class="pt-kids" id="${tid}"${raw(o ? "" : " hidden")}>${kids}</ul>` : ""}
</li>`;
    };
    // 다른 망에서 이 망에도 붙은 컨테이너(흐린 줄, 개수에는 넣지 않는다)
    const ghostRows = (netId) => PD.edges.filter((e) => e.kind === "bridge" && e.to === netId && PD.nodes.has(e.from) && PD.nodes.get(e.from).parent !== netId).map((e) => {
      const c = PD.nodes.get(e.from); const home = c.parent ? PD.nodes.get(c.parent) : null;
      return html`<li class="pt-li pt-ghost"><div class="pt-row"><span class="pt-tog-sp" aria-hidden="true"></span><button type="button" class="pt-sel" data-select="${c.id}" data-fk="${"ptg:" + netId + ":" + c.id}"><span class="pt-gl" aria-hidden="true">↔</span><span class="pt-main"><span class="pt-name">${name(PD, c.id)}</span><span class="pt-sub">다른 망에서 붙음 · 본래 ${home ? (home.type === "group" ? home.label : name(PD, home.id)) : "–"}</span></span></button></div></li>`;
    });
    const chipKids = (id) => PD.kids(id).sort((a, b) => String(a.label).localeCompare(String(b.label))).map((k) => row(k, { kids: PD.kids(k.id).length ? chipKids(k.id) : [] })).concat(ghostRows(id));
    const secs = [];
    const shown = new Set();
    // 바깥: 인터넷 ⇄ Cloudflare 엣지(+ 공유기 NAT 로 LAN 과 이어짐)
    const exts = [...PD.nodes.values()].filter((n) => n.type === "external").sort((a, b) => (/internet/i.test(b.id) ? 1 : 0) - (/internet/i.test(a.id) ? 1 : 0));
    if (exts.length) {
      exts.forEach((x) => shown.add(x.id));
      const tun = PD.edges.filter((e) => e.kind === "tunnel");
      const up = PD.edges.find((e) => e.kind === "uplink");
      secs.push(html`<ul class="ptree pt-out"><li class="pt-cap">바깥</li>${exts.map((x) => { const t = tun.find((e) => e.to === x.id); return row(x, { right: t ? "터널 " + (t.label || "") : /internet/i.test(x.id) && exts.length > 1 ? "⇄ Cloudflare" : "" }); })}${up ? html`<li class="pt-note pt-link">${up.label || "NAT"} ↔ LAN</li>` : ""}</ul>`);
    }
    const self = PD.self;
    if (self) {
      const kids = PD.kids(self.id);
      const ifs = kids.filter((k) => k.type === "iface").sort((a, b) => (IF_RANK[a.kind] ?? 5) - (IF_RANK[b.kind] ?? 5) || String(a.label).localeCompare(String(b.label))).map((k) => {
        const seg = PD.edgesOf(k.id).map((e) => (e.from === k.id ? e.to : e.from)).map((x) => PD.nodes.get(x)).find((x) => x && x.type === "segment");
        const im = info(PD, k);
        return row(k, { right: seg ? "→ " + name(PD, seg.id).replace(/\s.*$/, "") : "", sub: html`${P0(im.meta)}${im.aux && k.kind !== "tailnet" ? html` · ${nw(im.aux)}` : ""}` });
      });
      // 엔진 안: 가장 큰 네트워크부터(지도의 허브), 같으면 이름순 · host 네트워크 묶음은 맨 끝
      const cnt = (x) => PD.kidIds(x.id).length;
      const engKids = (id) => PD.kids(id).sort((a, b) => (a.type === "group") - (b.type === "group") || cnt(b) - cnt(a) || String(a.label).localeCompare(String(b.label)));
      const eng = kids.filter((k) => k.type === "engine").map((k) => row(k, { kids: engKids(k.id).map((nb) => row(nb, { kids: chipKids(nb.id) })) }));
      const rest = kids.filter((k) => k.type === "group").map((k) => row(k, { kids: chipKids(k.id) }));
      const dm = info(PD, self);
      secs.push(html`<ul class="ptree">${row(self, { kids: [].concat(ifs, eng, rest), sub: html`${dm.stw ? html`<b class="pt-stw">${dm.stw}</b> · ` : ""}${P0(dm.meta)}` })}</ul>`);
    }
    const segRow = (sg) => {
      const isTn = sg === PD.segTn;
      const all = PD.edgesOf(sg.id).map((e) => ({ e, o: PD.nodes.get(e.from === sg.id ? e.to : e.from) })).filter((x) => x.o && x.o.type !== "iface" && x.o.type !== "external");
      const conn = all.filter((x) => !shown.has(x.o.id));
      const again = all.filter((x) => shown.has(x.o.id));
      const againTop = again.filter((x) => !pAnc(PD, x.o)).length; const againKid = again.length - againTop;
      conn.forEach((x) => { shown.add(x.o.id); PD.kidIds(x.o.id).forEach((k) => shown.add(k)); });
      const peers = conn.sort((a, b) => (a.o.type === "group") - (b.o.type === "group") || String(a.o.label).localeCompare(String(b.o.label)))
        .map((x) => {
          const p = x.o;
          if (p.type !== "peer") return row(p, { kids: PD.kids(p.id).map((k) => row(k, {})), right: p.type === "group" ? "" : x.e.label || "" });
          // Tailnet: 오른쪽 = 경로(직결·중계·유휴), 아래 줄 = 주소·OS · LAN: 아래 줄이 LAN 주소와 경로
          const sub0 = isTn ? [p.ip, p.via_gw ? "공유기 NAT 경유" : p.os].filter(Boolean).join(" · ") : null;
          return row(p, { kids: PD.kids(p.id).map((k) => row(k, { sub: [k.ip, "같은 기기"].filter(Boolean).join(" · ") })), right: isTn ? connText(p) : "", sub: sub0 });
        });
      const note = againTop ? html`<li class="pt-note">위 같은 LAN 기기 ${againTop}대${againKid ? "(+" + againKid + " 같은 기기)" : ""}도 이 구간에 붙어 있어요</li>` : "";
      return html`<ul class="ptree">${row(sg, { kids: [].concat(peers, note ? [note] : []) })}</ul>`;
    };
    if (PD.segLan) secs.push(segRow(PD.segLan));
    if (PD.segTn) secs.push(segRow(PD.segTn));
    return html`<header class="flow-h"><h2>기기·네트워크</h2><p>어느 기기·인터페이스·도커 네트워크 안에 무엇이 있는가 — ▸ 로 펼치고, 이름을 누르면 상세가 열립니다.</p>
<p class="pt-tools"><button type="button" class="chip" data-act="tree-all" data-fk="tree-all">모두 펼치기</button><button type="button" class="chip" data-act="tree-none" data-fk="tree-none">모두 접기</button></p></header>
<div class="ptrees">${secs}</div>`;
  }
  /** JARVIS.html 결과(tspan 포함)를 글자만으로 */
  function P0(v) { return String(v == null ? "" : v).replace(/<[^>]*>/g, "").replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&#96;/g, "`").replace(/&amp;/g, "&"); }
  /** 나무에서 펼칠 수 있는 칸 전부 */
  function treeIds(PD) {
    const out = [];
    for (const n of PD.nodes.values()) if (PD.kidIds(n.id).length || n.type === "segment" || (n.type === "network" && PD.edges.some((e) => e.kind === "bridge" && e.to === n.id))) out.push(n.id);
    return out;
  }

  // ------------------------------------------------------------------ 요약 칸(인터페이스·컨테이너·네트워크·프로세스·LAN·Tailnet)
  function summary(PD) {
    const all = [...PD.nodes.values()];
    const S = PD.s || {};
    const fmt = h.fmt;
    const ifs = all.filter((n) => n.type === "iface");
    const ctr = all.filter((n) => n.type === "container");
    const ctrUp = ctr.filter((n) => n.status === "up").length;
    const nets = all.filter((n) => n.type === "network");
    const procs = all.filter((n) => n.type === "process");
    const ts = S.tailscale || {};
    const eng = all.find((n) => n.type === "engine");
    const segTone = (sg) => (sg ? h.tone(sg.status || "up") : "muted");
    return [
      { id: "iface", label: "인터페이스", val: String(ifs.length), sub: ifs.map((n) => n.label).join(" · "), k: "neutral", sel: PD.self ? PD.self.id : null, tone: worst(ifs) },
      { id: "ctr", label: "컨테이너", val: ctr.length === ctrUp ? String(ctr.length) : ctrUp + "/" + ctr.length, sub: "실행 " + ctrUp + " / " + ctr.length, k: "neutral", sel: eng ? eng.id : null, tone: worst(ctr) },
      { id: "net", label: "도커 네트워크", val: String(nets.length), sub: "도커 네트워크 " + nets.length + "개", k: "neutral", sel: eng ? eng.id : null, tone: nets.length ? worst(nets) : "muted" },
      { id: "proc", label: "호스트 프로세스", val: String(procs.length), sub: "기기에서 바로 도는 서버·데몬(systemd 유닛·pm2 앱·일반 프로세스)", k: "neutral", sel: PD.nodes.has("grp:procs") ? "grp:procs" : null, tone: worst(procs) },
      { id: "lan", label: "같은 LAN 기기", val: String(PD.lan.n), sub: "같은 LAN 에서도 닿는 다른 tailnet 기기(공유기 NAT 경유는 뺌)" + (PD.lan.held ? " · 잠시 안 보임 " + PD.lan.held : ""), k: "lan", sel: PD.segLan ? PD.segLan.id : null, tone: segTone(PD.segLan) },
      { id: "tn", label: "Tailnet 기기", val: ts.peers_total != null ? fmt.num(ts.peers_online) + "/" + fmt.num(ts.peers_total) : "–", sub: "온라인 / 전체 tailnet 기기", k: "tailnet", sel: PD.segTn ? PD.segTn.id : null, tone: segTone(PD.segTn) },
    ];
  }

  window.JarvisMapPhys = {
    init(helpers) { h = helpers; },
    derive, name, typeLabel, layout, sig, svg, info, hl, virt, published, cellSet, focusRects, caption, inspect, tree, treeIds, summary, groupTitle, labelW,
    capText: (PD, id) => (id === "lan" ? lanText(PD) : null), staticFor,
    TYPE_L, KIND_L, FS,
  };
})();
