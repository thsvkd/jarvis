// covers: USAGE-1, USAGE-3, USAGE-4, USAGE-5, USAGE-7. 실행: node tests/test_usage_summary.mjs
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
assert.equal(urls[0], "/api/devices/usage");
const rendered = () => element("ulBody").innerHTML;
const summary = () => rendered().match(/<div class="ul-summary"[\s\S]*?<\/section><\/div>/)?.[0] || "";
const initial = summary();
assert.match(initial, /프로바이더별 계정 사용량/);
assert.match(initial, /Claude/);
assert.match(initial, /Codex/);
assert.equal((initial.match(/a@example\.com/g) || []).length, 1);
// 같은 이메일·조직은 기기가 달라도 한 번, 조직이 다르면 따로 센다. 이메일의 대소문자와 앞뒤 공백은 보지 않는다(서버의 신원 규칙과 같다)
{
  devices[1].providers[0].accounts.push(account(" ORG@example.com ", 11, 500, { org: "Org A" })); // 더 오래된 값이라 고르지 않는다
  element("usage").click({ target: { closest: () => ({ dataset: { prov: "all" }, id: "", getAttribute: () => null }) } });
  assert.doesNotMatch(summary(), /ORG@example\.com/);
  devices[1].providers[0].accounts.pop();
  element("usage").click({ target: { closest: () => ({ dataset: { prov: "all" }, id: "", getAttribute: () => null }) } });
}
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

// 리셋 시각이 지난 창·세부 한도는 지난 기간의 값이다: 지금 값처럼 보이지 않고 주의·곧 풀리는 한도에 세지 않는다
const hotBefore = element("ulHotN").textContent;
devices[0].providers[0].accounts.push(account("stale@example.com", null, 86400, { source: "cswap-cache",
  windows: [{ key: "weekly", used: 100, resets_at: now - 3600 }], buckets: [{ name: "모델별 한도 1", used: 100, resets_at: now - 3600 }] }));
click({ prov: "all" });
const stale = summary().match(/stale@example\.com[\s\S]*?ul-age/)?.[0] || "";
assert.match(stale, /리셋됨/);
assert.doesNotMatch(stale, /100%/);
assert.doesNotMatch(rendered().match(/<div class="tile ul-soon">[\s\S]*?<\/ol>/)?.[0] || "", /stale@example\.com/);
assert.equal(element("ulHotN").textContent, hotBefore);

// 기기 행에 게이트웨이가 확인한 hswap 버전을 보인다. 고정 버전과 다르거나 올리기에 실패하면 그 사실을 붙인다
devices[0].hswap = { version: "0.2.1", pinned: "0.2.1", note: null };
devices[1].hswap = { version: "0.2.0", pinned: "0.2.1", note: "hswap 설치 실패(종료 코드 2): <in use>" };
click({ prov: "all" });
assert.match(rendered(), /<span class="ul-muted">hswap 0\.2\.1<\/span>/);
assert.match(rendered(), /hswap 0\.2\.0 \(고정 0\.2\.1\) · hswap 설치 실패\(종료 코드 2\): &lt;in use&gt;/);
assert.equal((rendered().match(/>hswap /g) || []).length, 2); // 확인 전인 기기(hswap 없음)에는 적지 않는다
delete devices[0].hswap; delete devices[1].hswap;

// covers: USAGE-7. Claude 칸의 기기 로그인 상태: 정상은 작게, 이상은 주의 색과 안내 버튼. 이상만 '주의 필요'로 센다
const claudeOf = d => d.providers.find(p => p.id === "claude");
const hotNow = () => Number(element("ulHotN").textContent);
const hotBase = hotNow();
claudeOf(devices[0]).login = { state: "ok", text: "로그인됨, 자동 갱신 5분 전", note: "접근 토큰 3시간 뒤 만료(자동 갱신)", attention: false, slot: "2" };
click({ prov: "all" });
assert.match(rendered(), /<p class="ul-age">로그인됨, 자동 갱신 5분 전 · 접근 토큰 3시간 뒤 만료\(자동 갱신\)<\/p>/);
assert.doesNotMatch(rendered(), /다시 로그인 방법/);
assert.equal(hotNow(), hotBase);
for (const [state, text] of [["relogin", "재로그인 필요"], ["expiring", "로그인 만료 임박(2일)"], ["keychain", "키체인 접근 불가"], ["stalled", "자동 갱신 멈춤"], ["failed", "자동 갱신 실패"]]) {
  claudeOf(devices[0]).login = { state, text, note: null, attention: true, slot: "2" };
  click({ prov: "all" });
  assert.ok(rendered().includes(`<p class="ul-err">${text}</p><button type="button" class="seg-action" data-guide="first">다시 로그인 방법</button>`), state);
  assert.equal(hotNow(), hotBase + 1); // 기기 하나로 센다
  assert.match(rendered(), new RegExp(`주의 필요 계정</span><b>${hotBase + 1}</b>`));
}
// 사용 중 계정이 이미 주의면 같은 기기의 로그인 상태를 또 세지 않는다
// 같은 계정을 다른 기기의 더 새 값으로 세었으면 이 기기의 로그인은 따로 센다
for (const [i, what] of [[1, "이 기기에만 있는 계정"], [0, "다른 기기 값으로 세는 계정"]]) {
  const row = claudeOf(devices[0]).accounts[i];
  Object.assign(row, { active: true, attention: true });
  click({ prov: "all" });
  assert.equal(hotNow(), hotBase + 1, what);
  Object.assign(row, { active: false, attention: undefined });
}
click({ prov: "all" });
// 주의 필터 중에도 로그인 이상 기기는 남고, 정상으로 돌아오면 빠진다. 다른 프로바이더 필터에서는 세지 않는다
const guideDialog = element("ulDlg");
guideDialog.showModal = () => { guideDialog.open = true; };
click({}, "ulHot");
assert.match(rendered(), /data-guide="first"/);
// covers: USAGE-10. 게이트웨이가 logins 에 claude 를 알리면 로그인 이상 기기 줄에 Claude 로그인 버튼이 안내 버튼과 함께 나온다
assert.doesNotMatch(rendered(), /data-lp="claude"/);
devices[0].logins = ["claude"];
click({ prov: "all" });
assert.match(rendered(), /data-login="first" data-lp="claude">Claude 다시 로그인/);
delete devices[0].logins;
click({ prov: "all" });
click({}, "ulHot");
// 이름 없는 OpenCode Go 구독은 월간 창의 리셋 시각이 같으면 기기가 달라도 한 계정으로 센다
const go = (reset, name = "이름 없는 계정") => provider("opencodeGo", "OpenCode Go", [{ label: name, org: null, status: "ok", active: true, attention: false,
  windows: [{ key: "session", used: 1, resets_at: reset - 9e5 }, { key: "monthly", used: 6, resets_at: reset }], buckets: [], updated_at: now - 5 }]);
const goCount = () => summary().match(/OpenCode Go <span class="ul-muted">(\d+)개 계정/)?.[1];
devices[0].providers.push(go(now + 9e5)); devices[1].providers.push(go(now + 9e5));
click({ prov: "all" });
assert.equal(goCount(), "1");
devices[1].providers.pop(); devices[1].providers.push(go(now + 8e5)); // 다른 구독이면 따로 센다
click({ prov: "all" });
assert.equal(goCount(), "2");
devices[0].providers.pop(); devices[1].providers.pop();
click({ prov: "all" });
click({ prov: "codex" });
assert.doesNotMatch(rendered(), /data-guide/);
click({ prov: "all" });
// 안내 대화상자: GUI 터미널 /login → hswap claude add 순서. keychain 이면 로그인 세션 안내가 앞에 온다. 알면 슬롯을 넣는다
click({ guide: "first" });
let guide = element("ulDlgB").innerHTML;
assert.equal(guideDialog.open, true);
assert.equal(element("ulDlgT").textContent, "first · Claude 다시 로그인");
assert.ok(guide.indexOf("/login") < guide.indexOf("hswap claude add --slot 2"));
assert.doesNotMatch(guide, /로그인 세션에서 실행해야/);
guideDialog.close(); // 이 가짜 DOM 에서는 close 이벤트 처리기다. 닫으면 다시 그려도 안내가 돌아오지 않는다
element("ulDlgB").innerHTML = "";
click({ prov: "all" });
assert.equal(element("ulDlgB").innerHTML, "");
claudeOf(devices[0]).login = { state: "keychain", text: "키체인 접근 불가", note: null, attention: true, slot: null };
click({ guide: "first" });
guide = element("ulDlgB").innerHTML;
assert.ok(guide.indexOf("로그인 세션에서 실행해야") < guide.indexOf("/login"));
assert.match(guide, /<code>hswap claude add<\/code> 로 그 계정을 다시 저장합니다\. 슬롯 번호를 알면 --slot N/);
// 파일 저장소: SSH → /login → add 순서이고 화면 터미널은 피한다. 기기 이름은 일반 표기다. 슬롯을 알면 넣는다
const guideOf = login => {
  guideDialog.close();
  claudeOf(devices[0]).login = { note: null, attention: true, slot: null, ...login };
  click({ guide: "first" });
  return element("ulDlgB").innerHTML;
};
const fileStore = { state: "failed", text: "자동 갱신 실패", store: "file", slot: "2" };
guide = guideOf(fileStore);
assert.ok(guide.indexOf("<code>ssh &lt;그 기기&gt;</code>") < guide.indexOf("/login"));
assert.ok(guide.indexOf("/login") < guide.indexOf("hswap claude add --slot 2"));
assert.match(guide, /화면 터미널은 피합니다: 키체인이 열린 곳에서 <code>claude<\/code> 를 실행하면 로그인이 키체인으로 옮겨집니다/);
assert.doesNotMatch(guide, /GUI|migrate-store|unlock-keychain|first/);
// 이전 계정의 기록이면 파일 저장소에서도 add 만 안내한다
guide = guideOf({ ...fileStore, state: "other_account", slot: null, other: true });
assert.match(guide, /<code>ssh &lt;그 기기&gt;<\/code>/);
assert.match(guide, /<p>2\. <code>hswap claude add<\/code> 로 지금 로그인을 새 슬롯에 저장합니다/);
assert.doesNotMatch(guide, /\/login/);
// 키체인 항목이 있거나 확인하지 못했으면 복구 명령과 그 조건, 항목이 없을 때 볼 로그가 재로그인보다 먼저 온다
guide = guideOf({ ...fileStore, state: "keychain_item_present" });
assert.ok(guide.indexOf("hswap claude migrate-store --to file --keep keychain") < guide.indexOf("ssh &lt;그 기기&gt;"));
assert.match(guide, /<code>hswap claude migrate-store --to file --keep keychain<\/code>은 항목이 있을 때 씁니다\. 항목이 없는데도 이 표시가 계속되면 <code>~\/Library\/Logs\/hswap-keepalive\.log<\/code>를 확인합니다\./);
assert.match(guide, /<code>security unlock-keychain<\/code> 을 먼저 실행합니다/);
guide = guideOf({ state: "keychain_item_present", text: "멈춤", store: null }); // store 가 없어도 이 오류는 파일 저장소의 것이다. GUI 안내는 파일을 지울 수 있다
assert.match(guide, /migrate-store/);
assert.doesNotMatch(guide, /GUI/);
// 설정 충돌은 설정만 고친다. 다시 로그인을 권하지 않는다
guide = guideOf({ state: "config_conflict", text: "hswap 설정 충돌", store: "file" });
assert.match(guide, /credentials\.store.*credentials\.requireKeychain/);
assert.doesNotMatch(guide, /\/login|ssh &lt;|hswap claude add/);
// store 가 없는 옛 형식과 auto·keychain 은 기존 GUI 안내 그대로다
for (const store of [undefined, "auto", "keychain"]) {
  guide = guideOf({ state: "failed", text: "자동 갱신 실패", store, slot: "2" });
  assert.match(guide, /로그인된 화면\(GUI\) 터미널에서 <code>claude<\/code> 를 실행해 <code>\/login<\/code> 합니다/, String(store));
  assert.doesNotMatch(guide, /ssh &lt;|migrate-store/, String(store));
}
// 자동 갱신 기록이 이전 계정의 것이면 옛 슬롯을 권하지 않고 새 슬롯으로 add 만 안내한다
guideDialog.close();
claudeOf(devices[0]).login = { state: "other_account", text: "자동 갱신 기록이 이전 계정의 것입니다", note: null, attention: true, slot: null, other: true };
click({ guide: "first" });
guide = element("ulDlgB").innerHTML;
assert.match(guide, /<code>hswap claude add<\/code> 로 지금 로그인을 새 슬롯에 저장합니다/);
assert.doesNotMatch(guide, /--slot|\/login/);
guideDialog.close();
// 다른 상태라도 기록이 이전 계정의 것이면 슬롯 안내 없이 새 슬롯으로 저장한다(재로그인 절차는 남는다)
claudeOf(devices[0]).login = { state: "stalled", text: "자동 갱신 멈춤", note: null, attention: true, slot: null, other: true };
click({ guide: "first" });
guide = element("ulDlgB").innerHTML;
assert.match(guide, /\/login/);
assert.match(guide, /새 슬롯에 저장합니다/);
assert.doesNotMatch(guide, /--slot/);
guideDialog.close();
// covers: USAGE-7. 자격 부재는 add 만으로 고칠 수 없다: 슬롯을 모르든, 기록이 다른 슬롯이든 /login 단계로 시작한다
for (const other of [false, true]) {
  claudeOf(devices[0]).login = { state: "failed", text: "자동 갱신 실패(자격 증명 없음)", note: null, attention: true, slot: null, other };
  click({ guide: "first" });
  guide = element("ulDlgB").innerHTML;
  assert.match(guide, /^<div class="ul-login"><p>1\. [^<]*<code>claude<\/code>[^<]*<code>\/login<\/code>/, String(other));
  assert.ok(guide.indexOf("/login") < guide.indexOf("<code>hswap claude add</code>"));
  assert.doesNotMatch(guide, /--slot \d/);
  guideDialog.close();
}
// covers: USAGE-7. organizationUuid 가 null 인 사용 중 계정의 재로그인(서버가 slot 을 준다): /login 으로 시작하고 그 슬롯에 저장한다
claudeOf(devices[0]).login = { state: "relogin", text: "재로그인 필요", note: null, attention: true, slot: "1", other: false };
click({ guide: "first" });
guide = element("ulDlgB").innerHTML;
assert.ok(guide.includes("<code>/login</code>"));
assert.ok(guide.indexOf("<code>/login</code>") < guide.indexOf("<code>hswap claude add --slot 1</code>"));
guideDialog.close();
delete claudeOf(devices[0]).login;
click({ prov: "all" });
assert.equal(hotNow(), hotBase); // 상태가 없으면(keepalive 를 쓰지 않는 기기) 세지 않는다

// covers: USAGE-7. 계정이 하나도 없어도 로그인 상태가 있으면 Claude 칸에 그리고, 세고, 안내한다
{
  const noAccounts = { id: "claude", name: "Claude", accounts: [], login: { state: "relogin", text: "재로그인 필요", note: null, attention: true, slot: null, other: false } };
  devices[2].providers.push(noAccounts);
  const before = hotBase + 1;
  click({ prov: "all" });
  assert.equal(hotNow(), before);
  const cellOf = () => rendered().match(/<tr aria-busy="false"><th scope="row"><b>empty<\/b>[\s\S]*?<\/tr>/)?.[0] || "";
  assert.match(cellOf(), /<td data-label="Claude" class="ul-full"><p class="ul-err">재로그인 필요<\/p><button type="button" class="seg-action" data-guide="empty">다시 로그인 방법<\/button>/);
  click({ guide: "empty" });
  assert.match(element("ulDlgB").innerHTML, /\/login/);
  element("ulDlg").close();
  click({}, "ulHot"); // 주의 필터 중에도 그 기기와 칸이 남는다
  assert.match(cellOf(), /data-guide="empty"/);
  click({}, "ulHot");
  // Claude 필터에서는 정상 상태만 있어도 행이 남는다(계정이 없어 숨으면 상태를 볼 곳이 없다)
  noAccounts.login = { state: "ok", text: "로그인됨, 자동 갱신 5분 전", note: null, attention: false, slot: null, other: false };
  click({ prov: "claude" });
  assert.match(cellOf(), /<p class="ul-age">로그인됨, 자동 갱신 5분 전<\/p>/);
  assert.equal(hotNow() < before, true);
  click({ prov: "all" });
  devices[2].providers.pop();
  click({ prov: "all" });
}

// 탭과 섹션은 사용량 모으기(기기마다 프로바이더를 불러 수십 초)를 기다리지 않고 core 확인(/api/devices)만으로 뜬다.
// 셸이 core 로 기억한 탭이면(지도에서 넘어올 때) 어떤 응답보다 먼저 섹션을 연다
for (const cached of [false, true]) {
  const els = new Map();
  const el = id => els.get(id) || els.set(id, { id, hidden: id === "usage", dataset: {}, classList: { toggle() {} },
    setAttribute() {}, toggleAttribute() {}, addEventListener() {} }).get(id);
  const tab = { "data-core": cached || undefined, toggleAttribute(name, on) { this[name] = on; } };
  const slow = { ...ctx, window: {}, document: { hidden: false, querySelector: s => s[0] === "#" ? el(s.slice(1)) : cached ? tab : null, querySelectorAll: () => [tab], addEventListener() {} },
    fetch: async url => url === "/api/devices" && !cached ? { ok: true, status: 200 } : new Promise(() => {}) }; // 사용량 응답은 오지 않는다(기억한 값이면 아무 응답도)
  vm.runInNewContext(readFileSync(new URL("../static/shared/jarvis.js", import.meta.url), "utf8"), slow);
  vm.runInNewContext(readFileSync(new URL("../static/app/usage.js", import.meta.url), "utf8"), slow);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(el("usage").hidden, false);
  assert.equal(tab["data-core"], true);
}
console.log("usage summary ok");
