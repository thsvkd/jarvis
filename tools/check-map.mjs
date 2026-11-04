#!/usr/bin/env node
// 네트워크 맵(개념·물리)의 선·묶음 규칙을 실제 브라우저에서 검사한다(MAP-2·3·4·6·7).
//   node tools/check-map.mjs <origin> <out-dir> [기기 id,...]
//   origin 이 core 면 ?device=<id> 로 각 기기 지도를 연다. 기기를 안 주면 그 origin 의 자기 지도 하나.
// 콘솔 오류·요청 실패·가로 넘침·규칙 위반이 있으면 종료 코드 1.
import { execFileSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const origin = process.argv[2] || "http://127.0.0.1:9300";
const out = resolve(process.argv[3] || "/tmp/jarvis-map-check");
const devs = process.argv[4] ? process.argv[4].split(",") : [null];
mkdirSync(out, { recursive: true });

// 개념 지도: 선이 끝점이 아닌 노드 상자를 지나지 않는가(MAP-2), 묶음 안 행으로 들어가는 가지가 12px 이상 보이는가(MAP-3),
// '이 기기 안에서만' 묶음의 행에는 선이 없는가(MAP-4), 다른 기기 입구(zone:peer:*)에서 나간 선이 있으면 서비스 행에 닿는가(MAP-6)
const concept = `(async () => {
  const vp = document.querySelector("#vp");
  const rects = new Map();
  for (const g of vp.querySelectorAll(".n[data-id]")) {
    const m = /translate\\(([-\\d.]+)[ ,]+([-\\d.]+)\\)/.exec(g.getAttribute("transform") || "");
    const bg = g.querySelector(".n-bg");
    if (!m || !bg) continue;
    rects.set(g.dataset.id, { x: +m[1], y: +m[2], w: +bg.getAttribute("width"), h: +bg.getAttribute("height") });
  }
  const bad = [], branch = [], local = [], peer = [];
  const inLocal = new Set();
  for (const r of vp.querySelectorAll('.reg[data-cat="local"] .reg-bg')) {
    const b = { x: +r.getAttribute("x"), y: +r.getAttribute("y"), w: +r.getAttribute("width"), h: +r.getAttribute("height") };
    for (const [id, n] of rects) if (n.x >= b.x && n.y >= b.y && n.x + n.w <= b.x + b.w && n.y + n.h <= b.y + b.h) inLocal.add(id);
  }
  let edges = 0;
  for (const e of vp.querySelectorAll(".e[data-key]")) {
    const key = e.dataset.key; const [from, rest] = key.split("→"); const to = rest.split("·")[0];
    const p = e.querySelector(".e-base"); const L = p.getTotalLength(); edges++;
    if (inLocal.has(to)) local.push(key);
    for (let s = 6; s < L - 6; s += 5) {
      const q = p.getPointAtLength(s);
      for (const [id, r] of rects) {
        if (id === from || id === to) continue;
        if (q.x > r.x + 2 && q.x < r.x + r.w - 2 && q.y > r.y + 2 && q.y < r.y + r.h - 2) { bad.push(key + " ⨯ " + id); s = L; break; }
      }
    }
    const t = rects.get(to);
    if (t && t.h < 40 && e.dataset.kind !== "depends") {
      const end = p.getPointAtLength(L), back = p.getPointAtLength(Math.max(0, L - 12));
      if (Math.abs(end.y - back.y) > 0.5) branch.push(key);
    }
    if (from.startsWith("zone:peer:") && !rects.has(to)) peer.push(key);
  }
  const peers = [...rects.keys()].filter((id) => id.startsWith("zone:peer:"));
  return { pass: !bad.length && !branch.length && !local.length && !peer.length && edges > 0,
           edges, peers, crossings: bad.slice(0, 20), shortBranches: branch.slice(0, 20), localWithEdges: local, peerDangling: peer };
})()`;

// 물리 지도: IP·트래픽 없는 인터페이스가 'N개 더' 한 칸으로 접히고, 누르면 펼쳐지는가(MAP-7)
const physical = `(async () => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const more = document.querySelector('#vp [data-more="ifaces"]');
  const shown = () => document.querySelectorAll('#vp .n[data-id^="iface:"]').length;
  if (!more) return { pass: true, folded: 0, note: "접을 인터페이스 없음" };
  const before = shown();
  more.dispatchEvent(new MouseEvent("click", { bubbles: true })); await sleep(700);
  const after = shown();
  const again = document.querySelector('#vp [data-more="ifaces"]');
  if (again) { again.dispatchEvent(new MouseEvent("click", { bubbles: true })); await sleep(700); }
  return { pass: after > before && shown() === before, before, after };
})()`;

let fail = false;
const report = [];
for (const d of devs) {
  for (const [view, ev] of [["concept", concept], ["physical", physical]]) {
    const url = new URL("/map/", origin);
    url.searchParams.set("view", view);
    if (d) url.searchParams.set("device", d);
    const prefix = resolve(out, (d || "self") + "-" + view);
    const raw = execFileSync(process.execPath, [resolve(root, "tools/shot.mjs"), url.href, prefix, "--sizes", "1440x900",
      "--wait", "6000", "--eval", ev], { cwd: root, encoding: "utf8", timeout: 120000, maxBuffer: 1 << 26 });
    const r = JSON.parse(raw).runs[0];
    const probs = [...(r.console || []), ...(r.exceptions || []), ...(r.failedRequests || [])];
    const ok = !r.hOverflow && !probs.length && r.evalResult && r.evalResult.pass;
    if (!ok) fail = true;
    report.push({ device: d, view, ok, eval: r.evalResult, problems: probs, file: r.file });
    process.stdout.write(`${ok ? "PASS" : "FAIL"} ${d || "self"} ${view} ${JSON.stringify(r.evalResult)}${probs.length ? " " + JSON.stringify(probs) : ""}\n`);
  }
}
writeFileSync(resolve(out, "report.json"), JSON.stringify(report, null, 2) + "\n");
process.exit(fail ? 1 : 0);
