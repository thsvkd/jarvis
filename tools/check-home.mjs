#!/usr/bin/env node
// 같은 Chromium 세션에서 재접속 후 운영 설정 복원까지 확인한다.
import {execFileSync} from 'node:child_process';
import {readFileSync,writeFileSync,mkdirSync} from 'node:fs';
import {dirname,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const origin=new URL(process.argv[2]||'http://127.0.0.1:9393'),out=resolve(process.argv[3]||'/tmp/jarvis-home-check');mkdirSync(out,{recursive:true});
const evalCode=readFileSync(resolve(root,'tools/check-home-eval.js'),'utf8');
const raw=execFileSync(process.execPath,[resolve(root,'tools/shot.mjs'),new URL('/',origin).href,resolve(out,'home'),'--sizes','1440x900,390x844','--wait','2500','--eval',evalCode],{cwd:root,encoding:'utf8',timeout:120000,maxBuffer:4*1024*1024});
const r=JSON.parse(raw);writeFileSync(resolve(out,'report.json'),JSON.stringify(r,null,2)+'\n');
for(const run of r.runs){if(run.hOverflow||(run.console||[]).length||(run.exceptions||[]).length||(run.failedRequests||[]).length||!run.evalResult?.pass)throw Error(JSON.stringify(run));}
console.log('Production Focus PASS: desktop, mobile, settings restored across reload. '+out);
