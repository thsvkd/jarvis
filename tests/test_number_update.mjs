// bento.js 의 num(): 숫자 텍스트는 보간 없이 바로 최종 값이 된다. 실행: node tests/test_number_update.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const src = readFileSync(new URL("../static/app/bento.js", import.meta.url), "utf8");
// IIFE 안의 num() 만 떼어 가짜 txt/isNum/n0 와 함께 돌린다(실제 DOM 이 필요 없다)
const m = src.match(/\n  function num\(k, v, f\) \{[\s\S]*?\n  \}\n/);
assert.ok(m, "bento.js 에서 num() 을 찾지 못했다");
// num() 안에 숫자 트윈(rAF·시간 보간)이 다시 생기지 않게 한다
assert.doesNotMatch(m[0], /requestAnimationFrame|performance\.now\(\)|tween/i);
const frames = []; // requestAnimationFrame 으로 예약된 콜백 — 있으면 안 된다
const written = []; // txt 에 쓰인 값 전부
const ctx = {
  requestAnimationFrame: (fn) => { frames.push(fn); return frames.length; },
  isNum: (v) => typeof v === "number" && isFinite(v),
  n0: (x) => (x == null ? "–" : String(Math.round(x))),
  txt: (k, s) => written.push([k, s]),
};
vm.createContext(ctx);
vm.runInContext(m[0] + "\nthis.num = num;", ctx);

ctx.num("cpu.pct", 0);
ctx.num("cpu.pct", 50); // 0 → 50 이 중간 값 없이 한 번에
assert.deepEqual(written, [["cpu.pct", "0"], ["cpu.pct", "50"]]);
assert.equal(frames.length, 0);

// 포맷터는 최종 값에만 적용된다
written.length = 0;
ctx.num("net.rx", 1234, (x) => x + " bps");
assert.deepEqual(written, [["net.rx", "1234 bps"]]);

// 값이 없으면 포맷터에 null 이 가서 자리표시로 바뀐다
written.length = 0;
ctx.num("cpu.pct", undefined);
ctx.num("net.rx", NaN, (x) => (x == null ? "–" : "x"));
assert.deepEqual(written, [["cpu.pct", "–"], ["net.rx", "–"]]);

console.log("ok");
