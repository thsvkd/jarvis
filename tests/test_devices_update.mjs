// covers: UI-4. 실행: node tests/test_devices_update.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

// devices.js 가 쓰는 만큼의 DOM: 요소는 선택자마다 하위 요소를 하나씩 만든다
function el(name) {
  const kids = new Map();
  return { name, hidden: name === "#devices", dataset: {}, innerHTML: "", textContent: "", disabled: false,
    classList: { add() {}, remove() {}, toggle() {} }, toggleAttribute() {}, append() {}, scrollIntoView() {},
    listeners: [], addEventListener(type, fn) { this.listeners.push(fn); },
    querySelector(s) { if (!kids.has(s)) kids.set(s, el(s)); return kids.get(s); } };
}
const doc = el("document");
doc.querySelectorAll = () => [];
const made = [];  // 기기 타일
doc.createElement = () => { const t = el("tile"); made.push(t); return t; };
let device = { id: "m1", host: "m1", version: "1.11.0", reachable: true, status: "ok" };
const progress = [];  // GET /api/devices/m1/jarvis/update 응답을 차례로(없으면 연결 실패)
let post = null;      // POST 응답을 기다리게 한다
const timers = [];
const reply = (status, body) => ({ ok: status < 300, status, json: async () => body });
const ctx = { window: {}, document: doc, location: { search: "", hash: "", href: "" }, URLSearchParams, console,
  getSelection: () => "", confirm: () => true,
  setTimeout(fn, ms) { timers.push({ fn, ms }); return timers.length; }, clearTimeout() {},
  fetch: async (url, opt = {}) => {
    if (url === "/api/devices") return reply(200, { generated_at: 0, devices: [device] });
    if (url === "/api/devices/m1/jarvis/update" && opt.method === "POST") return new Promise(r => { post = r; });
    if (url === "/api/devices/m1/jarvis/update") {
      const o = progress.shift();
      if (!o) throw new Error("connection refused");
      return reply(200, o);
    }
    throw new Error("unexpected " + url);
  } };
vm.runInNewContext(readFileSync(new URL("../static/shared/jarvis.js", import.meta.url), "utf8"), ctx);
vm.runInNewContext(readFileSync(new URL("../static/app/devices.js", import.meta.url), "utf8"), ctx);
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
const tick = async () => { const t = timers.filter(t => t.ms === 500); timers.length = 0; t.forEach(x => x.fn()); await flush(); };
await flush();
const list = doc.querySelector("#devList"), tile = made[0];
assert.ok(tile, "기기 타일이 있어야 한다");
const button = { dataset: { jarvisUpdate: "m1" }, disabled: false };
const target = { closest: s => (s === "[data-jarvis-update]" ? button : null) };
const prog = () => tile.querySelector(".dev-prog").innerHTML, note = () => tile.querySelector(".dev-note").textContent;

// 1) 누르면 스피너
progress.push({ busy: true, version: "1.11.0", stage: "check" });
list.listeners.forEach(fn => fn({ target }));
await flush();
assert.match(prog(), /dev-spin/);
// 2) 받는 중이면 진행 막대(받은 바이트/전체)
progress.push({ busy: true, version: "1.11.0", stage: "download", got: 5, total: 10 });
await tick();
assert.match(prog(), /dev-meter[\s\S]*aria-valuenow="50"[\s\S]*width:50%/);
assert.match(note(), /받는 중 50%/);
// 3) POST 가 재시작을 알리면 다시 스피너, 재시작하는 동안 응답이 없어도 기다린다
post(reply(200, { running: "1.11.0", to: "1.12.0", restart: true }));
await flush();
assert.match(prog(), /dev-spin/);
assert.match(note(), /v1\.11\.0 → v1\.12\.0 재시작 중/);
await tick();
assert.match(prog(), /dev-spin/);
// 4) 새 버전이 응답하면 진행 표시를 거두고 바뀐 버전을 보인다
progress.push({ busy: false, version: "1.12.0" });
await tick();
assert.equal(prog(), "");
assert.equal(note(), "jarvis v1.11.0 → v1.12.0");

// 이미 최신이면 스피너를 거두고 그렇게 알린다(진행 경로가 없는 옛 게이트웨이도 같다)
device = { ...device, version: "1.12.0" };
timers.filter(t => t.ms === 10000).forEach(t => t.fn());
await flush();
list.listeners.forEach(fn => fn({ target }));
await flush();
assert.match(prog(), /dev-spin/);
post(reply(200, { running: "1.12.0", to: "1.12.0", restart: false }));
await flush();
assert.equal(prog(), "");
assert.equal(note(), "jarvis v1.12.0 최신");
console.log("devices update ok");
