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

console.log("ok");
