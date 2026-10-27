#!/usr/bin/env node
// 실시간 디자인 랩의 가독성·탐색·동일 스냅샷 비교를 브라우저에서 검증한다.
// node tools/check-lab.mjs http://100.64.0.2:9300 /tmp/jarvis-lab-check
import {execFileSync} from "node:child_process";
import {mkdirSync,writeFileSync,readFileSync} from "node:fs";
import {resolve,dirname} from "node:path";
import {fileURLToPath} from "node:url";
const root=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const origin=new URL(process.argv[2]||"http://127.0.0.1:9393");
const out=resolve(process.argv[3]||"/tmp/jarvis-lab-check");
mkdirSync(out,{recursive:true});
const evaluation=readFileSync(resolve(root,"tools/check-lab-eval.js"),"utf8");
const reports=[];
const concepts=["gallery","focus","inbox","library","rooms","pulse","focus-plus"];
const selected=process.argv[4]?process.argv[4].split(","):concepts;
if(selected.some(c=>!concepts.includes(c)))throw Error("Unknown concept");
for(const concept of selected){
 const url=new URL(concept==="gallery"?"/lab/":"/lab/proposal/?concept="+concept,origin);
 const raw=execFileSync(process.execPath,[resolve(root,"tools/shot.mjs"),url.href,resolve(out,concept),"--sizes","1440x900,390x844","--full","--wait","2500","--eval",evaluation],{cwd:root,encoding:"utf8",timeout:120000,maxBuffer:4*1024*1024});
 const r=JSON.parse(raw);
 writeFileSync(resolve(out,concept+".json"),JSON.stringify(r,null,2)+"\n");
 for(const run of r.runs){
  const problems=[...(run.console||[]),...(run.exceptions||[]),...(run.failedRequests||[])];
  if(run.hOverflow||problems.length||run.evalError||!run.evalResult?.pass)throw Error(concept+" "+run.size+" failed: "+JSON.stringify(run));
 }
 reports.push({concept,runs:r.runs.map(r=>({size:r.size,pass:r.evalResult.pass,checks:r.evalResult.results}))});
 process.stdout.write(concept+" PASS (desktop + mobile)\n");
}
writeFileSync(resolve(out,"report.json"),JSON.stringify(reports,null,2)+"\n");
process.stdout.write("All selected pages passed. Reports and screenshots: "+out+"\n");
