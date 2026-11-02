// covers: SETUP-36, SETUP-37 — 기기 서랍에서 보존 상태가 보인다.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const elements = new Map(), listeners = {};
function element(id) {
  if (!elements.has(id)) elements.set(id, { id, dataset: {}, addEventListener() {}, showModal() { this.open = true; } });
  return elements.get(id);
}
const document = { activeElement: null, hidden: false, querySelector: s => element(s.slice(1)),
  addEventListener: (name, fn) => { listeners[name] = fn; } };
const html = (parts, ...values) => parts.reduce((s, p, i) => s + p + (values[i] == null ? "" : [values[i]].flat(Infinity).join("")), "");
const data = { full: true, generated_at: Date.now() / 1000, devices: [{ id: "one", status: 200, modules: {
  setup: { installed: true, owned: true, health: "ok", checked_at: Date.now() / 1000,
    details: { harnesses: ["claude", "codex"], recorded: {}, pending: [], session_retention: {
      claude: { state: "user_configured", value: 7 }, codex: { state: "unknown" } } } } } }] };
const P = { html, render: (el, value) => { el.innerHTML = value; }, subscribe() {} };
const ctx = { window: { JARVIS: P }, document, console, Date, setTimeout() {}, clearTimeout() {}, setInterval() {},
  fetch: async () => ({ status: 200, json: async () => data }) };
vm.runInNewContext(readFileSync(new URL("../static/setup/setup.js", import.meta.url), "utf8"), ctx);
await new Promise(resolve => setImmediate(resolve));
listeners.click({ target: { closest: () => ({ dataset: { act: "open", id: "one" }, hasAttribute: () => false }) } });
const rendered = element("suSheetBody").innerHTML;
assert.match(rendered, /Claude[^<]*.*7일/s);
assert.match(rendered, /기존.*보존/s);
assert.match(rendered, /Codex[^<]*.*확인 불가/s);
console.log("setup retention UI ok");
