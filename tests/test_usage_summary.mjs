// covers: USAGE-1, USAGE-4. 실행: node tests/test_usage_summary.mjs
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
  device("first", [provider("claude", "Claude", [account("a@example.com", 95, 100), account("u5@example.com", 5, 10, { source: "cswap-cache", windows: [{ key: "weekly", used: 5, resets_at: null }] }), account("org@example.com", 10, 10, { org: "Org A" }), account("기본 계정", 20, 10), account("기본 계정", 25, 10)])]),
  device("second", [provider("claude", "Claude", [account("a@example.com", 80, 20), account("org@example.com", 12, 10, { org: "Org A" }), account("org@example.com", 5, 10, { org: "Org B" }), account("기본 계정", 30, 10)]),
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
// 같은 이메일·조직은 기기가 달라도 한 번, 조직이 다르면 따로 센다
assert.equal((initial.match(/org@example\.com/g) || []).length, 2);
assert.equal((initial.match(/Org A/g) || []).length, 1);
assert.match(initial, /80%/);
// 5시간 창이 없는 Claude(cswap) 계정은 읽지 못한 것과 구분해 적는다(5시간 창이 있는 계정에는 적지 않는다)
assert.equal((initial.match(/5시간 한도 없음/g) || []).length, 1);
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

// 요약 카드의 인증 실패 계정에는 메시지와 함께 다시 로그인 버튼이 나온다(상세 acct 와 같다)
devices[1].providers[1].accounts[0] = { ...devices[1].providers[1].accounts[0], status: "error",
  failure: "delegated-refresh-required", error: "401 unauthorized", windows: [], buckets: [] };
click({ prov: "all" });
assert.match(summary(), /로그인이 만료되었거나 풀렸습니다/);
assert.doesNotMatch(summary(), /data-login/); // 이 기기는 로그인할 CLI 목록을 아직 안 줬다
devices[1].logins = ["codex", "grok"];
click({ prov: "all" });
assert.match(summary(), /data-login="second" data-lp="codex"/);
assert.match(summary(), /Codex 다시 로그인/);

// 기기 행의 '로그인' 버튼은 저장한 자격이 없다고 확인된 프로바이더에만 나온다
devices[0].fresh_logins = ["codex"];
click({ prov: "all" });
assert.match(rendered(), /data-login="first" data-lp="codex"/);
assert.match(rendered(), /Codex 로그인/);
devices[0].fresh_logins = ["claude"]; // claude 는 행이 있어 남겨둔다
click({ prov: "all" });
assert.doesNotMatch(rendered(), /data-login="first"/);
devices[0].fresh_logins = [];
click({ prov: "all" });
assert.doesNotMatch(rendered(), /data-login="first"/); // 자격 없음이 확인되지 않으면 버튼을 내지 않는다

// 자격 없음이 확인됐고 행이 없는 프로바이더는 기기 머리가 아니라 그 프로바이더 칸에 로그인 카드를 낸다(확인 불가에서는 뺀다)
if (element("ulHot").textContent !== "주의 필요만") click({}, "ulHot");
devices[2].fresh_logins = ["grok"];
click({ prov: "all" });
assert.match(rendered(), /data-p="grok" aria-hidden="true"><\/i>Grok<\/th>/); // 계정이 없어도 Grok 열이 생긴다
assert.match(rendered(), /<td data-label="Grok" class="ul-full"><div class="ul-acct"><p class="ul-muted">로그인하지 않았습니다\.<\/p><button type="button" class="seg-action" data-login="empty" data-lp="grok">Grok 로그인<\/button>/);
assert.doesNotMatch(rendered(), /확인 불가: Grok/);
assert.equal((rendered().match(/data-lp="grok"/g) || []).length, 1); // 기기 머리에는 더 이상 내지 않는다
click({ prov: "grok" });
assert.match(rendered(), /data-login="empty" data-lp="grok"/); // Grok 필터에서도 로그인할 기기가 보인다
click({}, "ulHot");
assert.doesNotMatch(rendered(), /data-lp="grok"/); // 주의 필터는 로그인 카드를 세지 않는다
click({}, "ulHot");
click({ prov: "all" });
console.log("usage summary ok");
