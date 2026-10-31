// covers: USAGE-1. 실행: node tests/test_usage_summary.mjs
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
const now = Math.floor(Date.now() / 1000);
const account = (label, used, updated, extra = {}) => ({ label, org: null, status: "ok", active: false,
  windows: used == null ? [] : [{ key: "session", used, resets_at: null }, { key: "weekly", used: 42, resets_at: null }],
  buckets: [], updated_at: now - updated, ...extra });
const provider = (id, name, accounts) => ({ id, name, accounts });
const device = (id, providers) => ({ id, host: id, status: 200, mode: "independent", unavailable: [], providers });
const devices = [
  device("first", [provider("claude", "Claude", [account("a@example.com", 95, 100), account("org@example.com", 10, 10, { dedupe: false }), account("기본 계정", 20, 10), account("기본 계정", 25, 10)])]),
  device("second", [provider("claude", "Claude", [account("a@example.com", 80, 20), account("org@example.com", 12, 10, { dedupe: false }), account("기본 계정", 30, 10)]),
    provider("codex", "Codex", [account("b@example.com", null, 10, { status: "error", error: "network" })])]),
  device("empty", []),
];
devices[0].unavailable = ["gemini"];
devices[2].unavailable = ["grok"];
const urls = [];
const ctx = { window: {}, document, location: { search: "", hash: "" }, URLSearchParams, console,
  localStorage: { getItem() { return null; }, setItem() {} }, setTimeout() {}, clearTimeout() {},
  fetch: async url => { urls.push(url); return { ok: true, status: 200, json: async () => ({ devices }) }; } };
vm.runInNewContext(readFileSync(new URL("../static/shared/jarvis.js", import.meta.url), "utf8"), ctx);
vm.runInNewContext(readFileSync(new URL("../static/app/usage.js", import.meta.url), "utf8"), ctx);
await new Promise(resolve => setImmediate(resolve));
assert.equal(urls[0], "/api/devices/usage?independent=1");
const rendered = () => element("ulBody").innerHTML;
const summary = () => rendered().match(/<div class="ul-summary"[\s\S]*?<\/section><\/div>/)?.[0] || "";
const initial = summary();
assert.match(initial, /프로바이더별 계정 사용량/);
assert.match(initial, /Claude/);
assert.match(initial, /Codex/);
assert.equal((initial.match(/a@example\.com/g) || []).length, 1);
assert.equal((initial.match(/org@example\.com/g) || []).length, 2);
assert.match(initial, /80%/);
assert.doesNotMatch(initial, /95%/);
assert.match(initial, /42%/);
assert.equal((initial.match(/기본 계정/g) || []).length, 3);
assert.match(initial, /네트워크 오류|프로바이더에 닿지 못했습니다/);
assert.match(initial, /second/);
assert.match(initial, /확인/);
assert.match(rendered(), /독립 조회/);
assert.match(rendered(), /확인 불가: Gemini/);
assert.match(rendered(), /조회 결과 없음/);
assert.match(rendered(), /읽지 못한 기기<\/span><b>1<\/b>/);
assert.doesNotMatch(rendered(), /Orca 실행 중/);

const click = (dataset, id = "") => element("usage").click({ target: { closest: () => ({ dataset, id, getAttribute: () => null }) } });
click({ prov: "codex" });
assert.match(summary(), /Codex/);
assert.doesNotMatch(summary(), /Claude/);
click({ prov: "all" });
devices[1].providers[0].accounts[0].windows[0].used = 10;
click({}, "ulHot");
assert.doesNotMatch(summary(), /a@example\.com/);
assert.match(summary(), /b@example\.com/);
assert.doesNotMatch(summary(), /기본 계정/);
assert.match(rendered(), /주의 필요 계정<\/span><b>1<\/b>/);
console.log("usage summary ok");
