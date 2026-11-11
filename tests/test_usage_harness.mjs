// covers: USAGE-13. 실행: node tests/test_usage_harness.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { id, hidden: id === "usage", dataset: {}, classList: { toggle() {} },
    setAttribute() {}, toggleAttribute() {}, addEventListener(type, fn) { this[type] = fn; } });
  return elements.get(id);
}
const document = { hidden: false, querySelector: s => element(s.slice(1)), querySelectorAll: () => [], addEventListener() {} };
const PLAN_ID = "a".repeat(64);
const tools = [{ id: "claude", state: "outdated", version: "2.1.0", latest: "2.2.0" }, { id: "codex", state: "current", version: "0.9.0", latest: "0.9.0" }];
let modules = { id: "m1", host: "m1", status: 200, modules: { harness: { health: "degraded", ok: true, details: { tools } } }, job: null };
let steps = [{ id: "claude", summary: "claude 2.1.0 → 2.2.0 (npm)", risk: "update" }];
const calls = [], timers = [], asked = [];
let answer = true;
const reply = (status, body) => ({ ok: status < 300, status, json: async () => body });
const ctx = { window: {}, document, location: { search: "", hash: "" }, URLSearchParams, console,
  localStorage: { getItem() { return null; }, setItem() {} },
  setTimeout(fn) { timers.push(fn); return timers.length; }, clearTimeout() {},
  confirm(text) { asked.push(text); return answer; },
  fetch: async (url, opt = {}) => {
    calls.push({ url, opt });
    if (url === "/api/devices/usage") return reply(200, { devices: [{ id: "m1", host: "m1", status: 200, unavailable: [], providers: [] }] });
    if (url === "/api/devices/modules") return reply(200, { devices: [modules] });
    if (url.endsWith("/modules/harness/plan")) return reply(200, { plan_id: PLAN_ID, steps, blocked: [] });
    if (url.includes("/modules/harness/apply")) return reply(202, { module: "harness", state: "running" });
    return reply(200, {});
  } };
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
const click = dataset => element("usage").click({ target: { closest: () => ({ dataset, id: "", getAttribute: () => null }) } });
const body = () => element("ulBody").innerHTML;
vm.runInNewContext(readFileSync(new URL("../static/shared/jarvis.js", import.meta.url), "utf8"), ctx);
vm.runInNewContext(readFileSync(new URL("../static/app/usage.js", import.meta.url), "utf8"), ctx);
await flush();

// 갱신 가능한 하네스만 기기 행에 대상·판과 버튼으로 보인다
assert.ok(calls.some(c => c.url === "/api/devices/modules"));
assert.match(body(), /하네스 갱신 가능: claude 2\.1\.0 → 2\.2\.0/);
assert.doesNotMatch(body(), /codex 0\.9\.0/);
assert.match(body(), /data-hup="m1"/);

// 확인을 거절하면 apply 를 보내지 않는다
answer = false;
click({ hup: "m1" });
await flush();
assert.match(asked[0], /claude 2\.1\.0 → 2\.2\.0 \(npm\)/);
assert.ok(!calls.some(c => c.url.includes("/apply")));

// 데몬 재시작 단계가 있으면 확인 창이 따로 알리고, 승인하면 계획의 plan_id 로 apply 한다
answer = true;
steps = [...steps, { id: "codex-daemon", summary: "codex 관리형 데몬 재시작", risk: "config" }];
click({ hup: "m1" });
await flush();
assert.match(asked[1], /Codex 작업을 끊을 수 있습니다/);
const apply = calls.find(c => c.url.includes("/apply"));
assert.equal(apply.url, "/api/devices/m1/modules/harness/apply?plan_id=" + PLAN_ID);
assert.equal(apply.opt.method, "POST");
assert.equal(apply.opt.headers["X-Jarvis-Action"], "module-apply");
assert.match(body(), /하네스 업데이트 중/);

// 작업이 끝나면 결과를 알림 줄에 보이고 버튼을 거둔다
modules = { ...modules, modules: { harness: { health: "ok", ok: true, details: { tools: [] } } },
  job: { module: "harness", state: "finished", status: "done" } };
timers.at(-1)();
await flush();
assert.match(element("ulNote").textContent, /m1: 하네스를 갱신했습니다/);
assert.doesNotMatch(body(), /data-hup/);

// viewers 가 아니면 details 가 없어 대상 이름 없이 알린다
modules = { id: "m1", host: "m1", status: 200, modules: { harness: { health: "degraded", ok: true } }, job: null };
timers.at(-1)();
await flush();
assert.match(body(), /하네스 갱신 가능<\/span>/);
console.log("usage harness ok");
