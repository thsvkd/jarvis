// static/shared/jarvis.js 의 HTML 이스케이프 회귀 테스트. 실행: node tests/test_jarvis_js.mjs [jarvis.js 경로]
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const path = process.argv[2] || new URL("../static/shared/jarvis.js", import.meta.url);
const errs = []; // JARVIS.html 이 값을 거부하며 남기는 콘솔 오류
const ctx = { window: {}, location: { search: "" }, URLSearchParams, console: { error: (...a) => errs.push(a) } };
vm.runInNewContext(readFileSync(path, "utf8"), ctx);
const J = ctx.window.JARVIS;
const h = (...a) => String(J.html(...a));

// 본문 값은 이스케이프한다
assert.equal(h`<b>${"<i>&"}</b>`, "<b>&lt;i&gt;&amp;</b>");
// href 는 안전한 URL 만 통과한다
assert.equal(h`<a href="${"javascript:alert(1)"}">`, '<a href="#">');
// 88eb43b 회귀: 따옴표 값 안의 '=' 바로 뒤 보간이 따옴표 없는 속성으로 오인돼 버려지면 안 된다
assert.equal(h`<a title="k=${"v"}">`, '<a title="k=v">');
assert.equal(h`<a href="/x?a=${"v"}">`, '<a href="/x?a=v">');
// 따옴표 없는 속성·이벤트 핸들러 자리에는 값을 넣지 않는다
assert.equal(h`<a class=${"x"}>`, "<a class=>");
assert.equal(h`<a onclick="${"alert(1)"}">`, '<a onclick="">');

// 따옴표 속성값 안의 '>' 를 태그 끝으로 착각하면 뒤따르는 URL 정화·따옴표 없는 속성 차단이 꺼진다
assert.equal(h`<a title="a>b" href="${"javascript:alert(1)"}">`, '<a title="a>b" href="#">');
assert.equal(h`<a title='a>b' class=${"x onmouseover=alert(1)"}>`, "<a title='a>b' class=>");
assert.equal(h`<a title="a>b" onclick="${"alert(1)"}">`, '<a title="a>b" onclick="">');
// 속성값 안의 '<' 도 태그 시작으로 오인하지 않는다: 태그가 닫힌 뒤의 본문 값은 이스케이프만 한다
assert.equal(h`<a title="a<b">${"<x>"}</a>`, '<a title="a<b">&lt;x&gt;</a>');
// 태그를 여러 개 지난 뒤에도 본문·속성 문맥이 어긋나지 않는다
assert.equal(h`<p class="a>b">${"1"}</p><a href="${"/ok"}">${"<"}</a>`, '<p class="a>b">1</p><a href="/ok">&lt;</a>');

// 닫힌 따옴표 값 바로 뒤의 보간은 정상 자리다(값이 버려지거나 콘솔 오류가 나면 안 된다)
errs.length = 0;
assert.equal(h`<a href="/" data-x="y"${J.raw(' aria-current="page"')}>x</a>`, '<a href="/" data-x="y" aria-current="page">x</a>');
assert.equal(errs.length, 0);
// 주석·본문·script/style 본문의 따옴표·꺾쇠는 태그 상태를 흐트러뜨리지 않는다
assert.equal(h`<!-- it's --><a class=${"x onmouseover=alert(1)"}>`, "<!-- it's --><a class=>");
assert.equal(h`<!-- 5" pipe --><a class=${"x"}>`, '<!-- 5" pipe --><a class=>');
assert.equal(h`<!-- a > b --><a href="${"javascript:1"}">`, '<!-- a > b --><a href="#">');
assert.equal(h`<p>a < b, it's</p><a class=${"x"}>`, "<p>a < b, it's</p><a class=>");
assert.equal(h`<!-- it's --><script>var a=${"1"}</script>`, "<!-- it's --><script>var a=</script>");
assert.equal(h`<script>var s="it's"; if (a<b) {}</script><a class=${"x"}>`, `<script>var s="it's"; if (a<b) {}</script><a class=>`);
assert.equal(h`<style>a::after{content:"'"}</style>${"<"}`, `<style>a::after{content:"'"}</style>&lt;`);

// covers: UI-2. 탭 전환 회귀: 헤더(shell.js)는 페이지마다 새로 그려진다. 지난 화면에서 받은 호스트가 저장돼 있으면 "호스트 확인 중…" 으로 되돌리지 않는다
{
  // 어떤 속성이든 받는 가짜 DOM 노드. 헤더 템플릿의 data-sh 노드만 쓰는 값(innerHTML)을 가진다.
  const node = (own = {}) => new Proxy(function () {}, {
    get: (t, k) => (k in own ? own[k] : k === "length" ? 0 : k === Symbol.toPrimitive ? () => "" : node()),
    set: (t, k, v) => { own[k] = v; return true; },
    apply: () => node(),
  });
  const mountShell = (store) => {
    const host = node({ getAttribute: (n) => (n === "data-sh" ? "host" : null), innerHTML: "호스트 확인 중…" });
    const names = ["live", "ovFull", "ovShort", "ovN", "ring", "liveT", "clock", "accN", "acc", "sr"];
    const header = node({ querySelectorAll: (q) => (q === "[data-sh]" ? [host, ...names.map((n) => node({ getAttribute: () => n }))] : []) });
    const document = node({ getElementById: () => header, querySelectorAll: () => [], title: "개요 · Jarvis" });
    const c = { document, location: { search: "", pathname: "/" }, URLSearchParams, console, Intl, Date, setTimeout, clearTimeout, setInterval, addEventListener() {},
      localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } }, matchMedia: () => ({ matches: false }) };
    c.window = c;
    vm.createContext(c);
    for (const f of [path, new URL("../static/app/shell.js", import.meta.url)]) vm.runInContext(readFileSync(f, "utf8"), c);
    return host;
  };
  assert.equal(mountShell({ "jarvis.shellHost": JSON.stringify({ host: "s2", model: "Pi 5", title: "t" }) }).innerHTML, "<b>s2</b> · Pi 5");
  assert.equal(mountShell({}).innerHTML, "호스트 확인 중…"); // 처음 불러올 때만 자리표시 문구
}

// covers: UI-1. 탭 바 하나: 개요 화면은 주소 해시로 섹션 하나만 보인다(shell.js 가 data-tab-on, bento.css 가 나머지를 숨긴다).
// 기기·AI 사용량 탭은 core 에서만(shell.css 가 data-core 없는 탭을 숨긴다. 어느 화면이든 개요에서 기억한 값으로 먼저 그린다).
{
  const node = (own = {}) => new Proxy(function () {}, {
    get: (t, k) => (k in own ? own[k] : k === "length" ? 0 : k === Symbol.toPrimitive ? () => "" : node()),
    set: (t, k, v) => { own[k] = v; return true; },
    apply: () => node(),
  });
  const scrolled = [];
  class El {
    constructor(attrs = {}, parent = null, cls = "") { this.a = new Map(Object.entries(attrs)); this.parentElement = parent; this.cls = cls; }
    get id() { return this.a.get("id") || ""; }
    getAttribute(n) { return this.a.has(n) ? this.a.get(n) : null; }
    setAttribute(n, v) { this.a.set(n, String(v)); }
    removeAttribute(n) { this.a.delete(n); }
    hasAttribute(n) { return this.a.has(n); }
    toggleAttribute(n, on) { if (on) this.a.set(n, ""); else this.a.delete(n); return on; }
    closest(sel) { for (let e = this; e; e = e.parentElement) if (sel === ".sec" && e.cls === "sec") return e; return null; }
    scrollIntoView() { scrolled.push(this.id); }
    addEventListener() {}
  }
  const mount = ({ page, hash = "", store = {}, sections = {} }) => {
    const nav = new El();
    nav.scrollWidth = nav.clientWidth = 0;
    let links = [];
    const names = ["host", "live", "ovFull", "ovShort", "ovN", "ring", "liveT", "clock", "accN", "acc", "sr"];
    const header = node({
      getAttribute: (n) => (n === "data-page" ? page : null),
      set innerHTML(s) { // 셸이 그린 탭 링크를 그대로 읽는다
        links = [...s.matchAll(/<a href="([^"]*)" data-sh-nav="([^"]*)"(?: data-sh-sec="([^"]*)")?( aria-current="page")?/g)]
          .map(([, href, id, sec, cur]) => new El({ href, "data-sh-nav": id, ...(sec ? { "data-sh-sec": sec } : {}), ...(cur ? { "aria-current": "page" } : {}) }, nav));
      },
      querySelector: (q) => (q === ".sh-nav" ? nav : node()),
      querySelectorAll: (q) => (q === "[data-sh]" ? names.map((n) => node({ getAttribute: () => n })) : q === "[data-sh-nav]" ? links : []),
    });
    const main = new El({ id: "main" });
    const els = {};
    for (const [id, kids] of Object.entries(sections)) {
      els[id] = new El({ id }, main, "sec");
      for (const k of kids) els[k] = new El({ id: k }, els[id]);
    }
    const document = node({ getElementById: (id) => (id === "jarvis-shell" ? header : els[id] || node()), querySelectorAll: () => [], title: "개요 · Jarvis", readyState: "complete" });
    const on = {};
    const location = { search: "", pathname: page === "home" ? "/" : "/" + page + "/", hash };
    const c = { document, location, URLSearchParams, console, Intl, Date, setTimeout, clearTimeout, setInterval, matchMedia: () => ({ matches: false }),
      addEventListener: (t, fn) => { (on[t] = on[t] || []).push(fn); },
      history: { pushState: (s, t, url) => { location.hash = url.slice(url.indexOf("#")); } },
      scrollY: 0, scrollTo: (x, y) => { const top = typeof x === "object" ? x.top : y; scrolled.push(top ? "y" + top : "top"); },
      localStorage: { getItem: (k) => store[k] ?? null, setItem: (k, v) => { store[k] = v; } } };
    c.window = c;
    vm.createContext(c);
    for (const f of [path, new URL("../static/app/shell.js", import.meta.url)]) vm.runInContext(readFileSync(f, "utf8"), c);
    const go = (h) => { location.hash = h; (on.hashchange || []).forEach((fn) => fn()); };
    const shown = () => Object.values(els).filter((e) => e.hasAttribute("data-tab-on")).map((e) => e.id);
    const current = () => links.filter((a) => a.getAttribute("aria-current") === "page").map((a) => a.getAttribute("data-sh-nav"));
    const tab = (id) => links.find((a) => a.getAttribute("data-sh-nav") === id);
    return { go, shown, current, tab, links, els, location, win: c, shell: c.JarvisShell };
  };
  const SECS = { overview: ["alerts"], devices: [], usage: [], services: ["svcGroups"], hardware: ["hw-cpu"], network: [], runtime: [] };

  // 탭 바는 하나: 개요 화면의 섹션 다음에 다른 화면
  const home = mount({ page: "home", hash: "#usage", sections: SECS });
  assert.deepEqual(home.links.map((a) => a.getAttribute("data-sh-nav")), ["home", "devices", "usage", "services", "hardware", "network", "runtime", "map", "setup"]);
  // 딥 링크(/#usage): 그 섹션 하나만 보이고 그 탭이 지금 탭
  assert.deepEqual(home.shown(), ["usage"]);
  assert.deepEqual(home.current(), ["usage"]);
  // 해시 전환(뒤로 가기 포함): 섹션 안 요소면 그 섹션 탭으로 바꾸고 그 요소로 스크롤
  scrolled.length = 0;
  home.go("#hw-cpu");
  assert.deepEqual(home.shown(), ["hardware"]);
  assert.deepEqual(home.current(), ["hardware"]);
  assert.deepEqual(scrolled, ["hw-cpu"]);
  // 섹션 밖 해시(서비스 상세 시트 · 본문 건너뛰기)는 지금 탭을 그대로 둔다
  home.go("#svc=caddy");
  home.go("#main");
  assert.deepEqual(home.shown(), ["hardware"]);
  // 해시가 없으면 개요, 탭을 옮기면 맨 위에서 시작
  scrolled.length = 0;
  home.go("");
  assert.deepEqual(home.shown(), ["overview"]);
  assert.deepEqual(home.current(), ["home"]);
  assert.deepEqual(scrolled, ["top"]);
  // 개요의 타일 · 경보에서 다른 섹션으로 가면(bento.js flash) 그 탭으로 바뀌고 기록에 남는다
  home.shell.reveal(home.els.svcGroups);
  assert.deepEqual(home.shown(), ["services"]);
  assert.equal(home.location.hash, "#services");
  // 뒤로·앞으로 가기(클릭 없는 hashchange)로 돌아온 탭은 떠날 때의 스크롤 위치로 돌아간다
  home.win.scrollY = 640;
  home.go("#hw-cpu");
  home.win.scrollY = 0;
  scrolled.length = 0;
  home.go("#services");
  assert.deepEqual(scrolled, ["y640"]);

  // core 가 아니면(아무도 data-core 를 붙이지 않음) 기기 · AI 사용량 탭은 CSS 로 숨는다
  assert.equal(home.tab("devices").hasAttribute("data-core"), false);
  assert.equal(home.tab("usage").hasAttribute("data-core"), false);
  const shellCss = readFileSync(new URL("../static/app/shell.css", import.meta.url), "utf8");
  assert.match(shellCss, /\.sh-nav \[data-sh-sec="devices"\]:not\(\[data-core\]\), \.sh-nav \[data-sh-sec="usage"\]:not\(\[data-core\]\) \{ display: none; \}/);
  // 고른 섹션 하나만 보인다(나머지 .sec 는 숨김, 고른 섹션이 이 기기에 없으면 개요)
  const bentoCss = readFileSync(new URL("../static/app/bento.css", import.meta.url), "utf8");
  assert.match(bentoCss, /#main\[data-tabbed\] > \.sec:not\(#overview\):not\(\[data-tab-on\]\) \{ display: none; \}/);
assert.doesNotMatch(bentoCss, /(^|\n)#main > \.sec/); // 셋업 페이지(data-tabbed 없음)의 섹션은 숨기지 않는다

  // 지도 · 셋업: 섹션을 건드리지 않고 그 화면 탭이 지금 탭. core 전용 탭은 개요에서 기억한 값을 따른다
  const map = mount({ page: "map", store: { "jarvis.shellCore": '["usage"]' }, sections: SECS });
  assert.deepEqual(map.shown(), []);
  assert.deepEqual(map.current(), ["map"]);
  assert.equal(map.tab("usage").hasAttribute("data-core"), true);
  assert.equal(map.tab("devices").hasAttribute("data-core"), false);
  const setup = mount({ page: "setup", sections: SECS });
  assert.deepEqual(setup.current(), ["setup"]);
  assert.equal(setup.tab("usage").hasAttribute("data-core"), false); // 기억한 값이 없으면(게이트웨이 · 첫 방문) 숨긴 채
  // 개요도 기억한 값으로 먼저 그린다(지도에서 넘어올 때 탭이 사라졌다 나타나지 않게). 확인은 devices.js · usage.js 가 /api/devices 로 한다
  const back = mount({ page: "home", hash: "#devices", store: { "jarvis.shellCore": '["devices","usage"]' }, sections: SECS });
  assert.equal(back.tab("devices").hasAttribute("data-core"), true);
  assert.equal(back.tab("usage").hasAttribute("data-core"), true);
  assert.deepEqual(back.current(), ["devices"]);
}

console.log("ok");
