// covers: USAGE-14. 실행: node tests/test_usage_rotation.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, { id, hidden: id === "usage", dataset: {}, textContent: "", classList: { toggle() {} }, open: false,
    showModal() { this.open = true; }, querySelector: () => null,
    setAttribute() {}, toggleAttribute() {}, addEventListener(type, fn) { this[type] = fn; } });
  return elements.get(id);
}
const document = { hidden: false, querySelector: s => element(s.slice(1)), querySelectorAll: () => [], addEventListener() {} };
const acct = (slot, label, active = false) => ({ slot, label, active, org: null, windows: [], buckets: [], status: "ok" });
const dev = { id: "m1", host: "m1", status: 200, unavailable: [],
  providers: [{ id: "claude", name: "Claude", accounts: [acct("1", "a@x.com", true), acct("2", "b@x.com"), acct("3", "c@x.com")] }] };
const calls = [];
let rotation = { on: false, ordered: false, order: ["3"], threshold: 90, service: null };
const reply = (status, body) => ({ ok: status < 300, status, json: async () => body });
const ctx = { window: {}, document, location: { search: "", hash: "" }, URLSearchParams, console,
  localStorage: { getItem() { return "full"; }, setItem() {} }, setTimeout() { return 1; }, clearTimeout() {}, confirm: () => true,
  fetch: async (url, opt = {}) => {
    calls.push({ url, opt });
    if (url === "/api/devices/usage") return reply(200, { devices: [dev] });
    if (url === "/api/devices/m1/rotation") return reply(200, rotation);
    if (url.startsWith("/api/devices/m1/rotation?")) return reply(200, { ...rotation, on: true });
    return reply(404, {});
  } };
const flush = async () => { for (let i = 0; i < 10; i++) await new Promise(r => setImmediate(r)); };
const click = dataset => element("usage").click({ target: { closest: () => ({ dataset, id: "", getAttribute: () => null }) } });
const dlgBody = () => element("ulDlgB").innerHTML;
vm.runInNewContext(readFileSync(new URL("../static/shared/jarvis.js", import.meta.url), "utf8"), ctx);
vm.runInNewContext(readFileSync(new URL("../static/app/usage.js", import.meta.url), "utf8"), ctx);
await flush();

// 슬롯이 둘 이상인 Claude 칸에 전환 순서 버튼이 있다
assert.match(element("ulBody").innerHTML, /data-rot="m1"/);

// 열면 저장된 순서(3)를 앞에, 나머지를 슬롯 순으로 뒤에 두고 사용률 90 을 남은 10% 로 보인다
click({ rot: "m1" });
await flush();
const order = () => [...dlgBody().matchAll(/<b class="ul-email">([^<]+)<\/b>/g)].map(m => m[1]);
assert.deepEqual(order(), ["c@x.com", "a@x.com", "b@x.com"]);
assert.match(dlgBody(), /value="10"/);
assert.match(dlgBody(), /aria-label="c@x.com 위로" title="위로" aria-disabled="true"/); // 첫 계정은 더 올라가지 않는다

// 위/아래로 옮기면 순위가 바뀌고 알림 영역에 새 순위를 알린다
click({ mv: "1", i: "0" });
assert.deepEqual(order(), ["a@x.com", "c@x.com", "b@x.com"]);
assert.match(element("ulDlgLive").textContent, /c@x.com 을\(를\) 2순위로/);

// 잘못된 남은 한도는 보내지 않는다
const form = (on, left) => ({ dataset: { rotform: "1" }, elements: { on: { checked: on }, left: { value: left } } });
element("ulDlg").submit({ target: form(true, "0"), preventDefault() {} });
await flush();
assert.ok(!calls.some(c => c.url.includes("rotation?")));
assert.match(dlgBody(), /1 이상 50 이하/);

// 저장: 지금 순서와 사용률(100 - 남은 양)을 viewers 동작 헤더로 보낸다
element("ulDlg").submit({ target: form(true, "15"), preventDefault() {} });
await flush();
const save = calls.find(c => c.url.includes("rotation?"));
assert.equal(save.url, "/api/devices/m1/rotation?on=1&order=1%2C3%2C2&threshold=85");
assert.equal(save.opt.method, "POST");
assert.equal(save.opt.headers["X-Jarvis-Action"], "usage-account");
assert.match(element("ulNote").textContent, /켰습니다 · 남은 15%/);
console.log("ok");
