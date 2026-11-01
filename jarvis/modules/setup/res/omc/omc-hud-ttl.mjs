#!/usr/bin/env node
// OMC HUD 앞단 래퍼: 세션 경과 시간(session:NNm) 자리에 프롬프트 캐시 TTL 잔여 시간을 띄운다.
//
//   node omc-hud-ttl.mjs <원래 statusLine 명령 argv...>
//
// Claude Code 가 statusLine stdin 으로 넘기는 prompt_cache.expires_at(epoch 초)을 읽어
// 매 렌더마다 지금 시각 기준으로 계산한다. OMC HUD 출력은 한 프레임 늦게 캐시되므로
// 거기에 기대지 않는다. 설치는 res/omc/apply.py 가 한다.
//
// OMC HUD 의 session 요소가 꺼진 프리셋이면 바꿀 자리가 없어 아무것도 표시되지 않는다.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";

const GREEN = "\x1b[32m", YELLOW = "\x1b[33m", RED = "\x1b[31m", RESET = "\x1b[0m";
const SESSION_RE = /session:(?:\x1b\[[0-9;]*m)?\d+m(?:\x1b\[0m)?/;

function renderCache(cache, nowSec) {
  const left = Math.floor(cache.expires_at - nowSec);
  if (left <= 0 || cache.warm === false) return `cache:${RED}cold${RESET}`;
  const text = left >= 60 ? `${Math.floor(left / 60)}m` : `${left}s`;
  return `cache:${left <= 120 ? YELLOW : GREEN}${text}${RESET}`;
}

let input = "";
try { input = readFileSync(0, "utf8"); } catch { /* stdin 없음 */ }
let cache;
try { cache = JSON.parse(input).prompt_cache; } catch { /* 파싱 불가면 원래 출력 유지 */ }

const [cmd, ...args] = process.argv.slice(2);
if (!cmd) process.exit(0);
const r = spawnSync(cmd, args, { input, encoding: "utf8", timeout: 10000 });
// HUD 가 사라졌을 때 원인을 찾을 수 있도록 안쪽 명령의 실패는 stderr 로 전달한다. 종료 코드는
// OMC find-node.sh 처럼 항상 0 — statusLine 이 0 이 아닌 코드로 끝나 표시가 버려지지 않게.
if (r.error) process.stderr.write(`[omc-hud-ttl] ${r.error.message}\n`);
if (r.stderr) process.stderr.write(r.stderr);

let out = r.stdout || "";
// 캐시 정보가 없으면(구버전 Claude Code 등) 쓸모 있는 session 표시를 그대로 둔다.
if (cache && typeof cache.expires_at === "number") out = out.replace(SESSION_RE, renderCache(cache, Date.now() / 1000));
process.stdout.write(out);
