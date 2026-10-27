#!/usr/bin/env node
// 헤드리스 chromium 으로 페이지를 열어 스크린샷·콘솔 오류·가로 넘침을 한 번에 본다.
// 의존성 없음(Node 22+ 내장 WebSocket/fetch 로 CDP 직접 호출).
//
//   node tools/shot.mjs <url> <out-prefix> [--sizes 1440x900,390x844] [--wait 5000] [--full] [--dark] [--light]
//                       [--click "<css selector>"] [--eval "<js expr>"]
//
// 결과: <out-prefix>-<w>x<h>[-dark].png 파일들과, stdout 으로 JSON 보고서
//   { url, runs: [{ size, scheme, file, scrollWidth, innerWidth, hOverflow, overflowing: [...], title,
//                   textLength, console: [...], exceptions: [...], failedRequests: [...], evalResult }] }
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";

const args = process.argv.slice(2);
if (args.length < 2) {
  console.error("usage: node shot.mjs <url> <out-prefix> [--sizes WxH,...] [--wait ms] [--full] [--dark] [--light] [--click sel] [--eval js]");
  process.exit(2);
}
const [url, prefix] = args;
const opt = (name, def) => {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : def;
};
const flag = (name) => args.includes(name);
const sizes = opt("--sizes", "1440x900,390x844").split(",").map((s) => s.split("x").map(Number));
const waitMs = Number(opt("--wait", "5000"));
const full = flag("--full");
const schemes = [];
if (flag("--light") || !flag("--dark")) schemes.push("light");
if (flag("--dark")) schemes.push("dark");
const clickSel = opt("--click", null);
const evalExpr = opt("--eval", null);
mkdirSync(dirname(prefix) || ".", { recursive: true });

const profile = mkdtempSync(join(tmpdir(), "pit5shot-"));
const chrome = spawn(
  "chromium",
  [
    "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "--no-first-run",
    "--no-default-browser-check", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
    "--disable-extensions", "--disable-background-networking", "--lang=ko-KR", "about:blank",
  ],
  { stdio: ["ignore", "ignore", "pipe"] },
);
const cleanup = () => {
  try { chrome.kill("SIGKILL"); } catch {}
  try { rmSync(profile, { recursive: true, force: true }); } catch {}
};
process.on("exit", cleanup);
const timer = setTimeout(() => { console.error("timeout"); cleanup(); process.exit(3); }, 120000 + waitMs * sizes.length * 2);

const wsUrl = await new Promise((resolve, reject) => {
  let buf = "";
  chrome.stderr.on("data", (d) => {
    buf += d.toString();
    const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
    if (m) resolve(m[1]);
  });
  chrome.on("exit", (c) => reject(new Error("chromium exited " + c + "\n" + buf.slice(-800))));
});
const port = new URL(wsUrl).port;
let pageWs;
for (let i = 0; i < 50 && !pageWs; i++) {
  const list = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  pageWs = list.find((t) => t.type === "page")?.webSocketDebuggerUrl;
  if (!pageWs) await new Promise((r) => setTimeout(r, 100));
}
const ws = new WebSocket(pageWs);
await new Promise((r, j) => { ws.onopen = r; ws.onerror = j; });
let seq = 0;
const pending = new Map();
const listeners = [];
ws.onmessage = (ev) => {
  const msg = JSON.parse(ev.data);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  } else if (msg.method) listeners.forEach((l) => l(msg));
};
const send = (method, params = {}) =>
  new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

await send("Page.enable");
await send("Runtime.enable");
await send("Log.enable");
await send("Network.enable");

const report = { url, runs: [] };
for (const scheme of schemes) {
  for (const [w, h] of sizes) {
    const run = { size: `${w}x${h}`, scheme, console: [], exceptions: [], failedRequests: [] };
    const onMsg = (m) => {
      if (m.method === "Runtime.consoleAPICalled" && ["error", "warning", "assert"].includes(m.params.type)) {
        run.console.push({ type: m.params.type, text: m.params.args.map((a) => a.value ?? a.description ?? "").join(" ").slice(0, 300) });
      } else if (m.method === "Runtime.exceptionThrown") {
        const d = m.params.exceptionDetails;
        run.exceptions.push(((d.exception && d.exception.description) || d.text || "").slice(0, 400) + ` @${d.url || ""}:${d.lineNumber}`);
      } else if (m.method === "Log.entryAdded" && ["error", "warning"].includes(m.params.entry.level)) {
        run.console.push({ type: "log-" + m.params.entry.level, text: (m.params.entry.text + " " + (m.params.entry.url || "")).slice(0, 300) });
      } else if (m.method === "Network.loadingFailed" && !m.params.canceled) {
        run.failedRequests.push(m.params.errorText + " " + (m.params.type || ""));
      } else if (m.method === "Network.responseReceived" && m.params.response.status >= 400) {
        run.failedRequests.push(`${m.params.response.status} ${m.params.response.url}`);
      }
    };
    listeners.push(onMsg);
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: w < 700 });
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
    const loaded = new Promise((r) => {
      const l = (m) => { if (m.method === "Page.loadEventFired") { listeners.splice(listeners.indexOf(l), 1); r(); } };
      listeners.push(l);
    });
    await send("Page.navigate", { url });
    await Promise.race([loaded, sleep(20000)]);
    await sleep(waitMs);
    if (clickSel) {
      await send("Runtime.evaluate", { expression: `document.querySelector(${JSON.stringify(clickSel)})?.click()` });
      await sleep(1200);
    }
    const metrics = await send("Runtime.evaluate", {
      returnByValue: true,
      expression: `(() => {
        const de = document.documentElement, iw = ${w};  // 에뮬레이션 폭 기준(모바일은 넘치면 뷰포트가 늘어난다)
        const over = [];
        for (const el of document.querySelectorAll('body *')) {
          const r = el.getBoundingClientRect();
          if (r.width && r.right > iw + 1 && getComputedStyle(el).position !== 'fixed') {
            let p = el.parentElement, clipped = false;
            while (p && p !== document.body) { const o = getComputedStyle(p).overflowX; if (o === 'auto' || o === 'hidden' || o === 'scroll' || o === 'clip') { clipped = true; break; } p = p.parentElement; }
            if (!clipped) over.push((el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (el.className && typeof el.className === 'string' ? '.' + el.className.trim().split(/\\s+/).slice(0,2).join('.') : '')) + ' right=' + Math.round(r.right));
          }
          if (over.length >= 12) break;
        }
        return { scrollWidth: de.scrollWidth, innerWidth: iw, scrollHeight: de.scrollHeight, title: document.title,
                 textLength: (document.body.innerText || '').length, overflowing: over };
      })()`,
    });
    Object.assign(run, metrics.result.value);
    run.hOverflow = run.scrollWidth > w + 1 || run.innerWidth > w + 1;
    if (evalExpr) {
      try {
        const r = await send("Runtime.evaluate", { expression: evalExpr, returnByValue: true, awaitPromise: true });
        run.evalResult = r.result.value ?? r.result.description;
      } catch (e) { run.evalResult = "eval error: " + e.message; }
    }
    const shotParams = { format: "png" };
    if (full) {
      const hh = Math.min(run.scrollHeight || h, 7000);
      shotParams.captureBeyondViewport = true;
      shotParams.clip = { x: 0, y: 0, width: w, height: hh, scale: 1 };
    }
    const shot = await send("Page.captureScreenshot", shotParams);
    run.file = `${prefix}-${w}x${h}${scheme === "dark" ? "-dark" : ""}.png`;
    writeFileSync(run.file, Buffer.from(shot.data, "base64"));
    listeners.splice(listeners.indexOf(onMsg), 1);
    report.runs.push(run);
  }
}
clearTimeout(timer);
console.log(JSON.stringify(report, null, 1));
ws.close();
cleanup();
process.exit(0);
