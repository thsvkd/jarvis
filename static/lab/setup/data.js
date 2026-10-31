/* 랩 05 공용 샘플 데이터와 시뮬레이션. 세 시안이 같은 기기·모듈·계획·결과를 쓴다. 실제 서버 값이 아니다.
 * 모양은 docs/SPEC.md "모듈 계약"을 따른다: status {module, installed, owned, version, latest, health, details},
 * plan {module, device, plan_id, action, steps:[{id, summary, risk, argv}], blocked:[{step, reason}]},
 * apply {status: done/current/deferred/stale/failed, steps:[{id, result, message}]}.
 * 실행은 시뮬레이션이다. 기기마다 결과를 정해 두었다(README.md 의 '결과 정해 둔 기기'). */
(function(){
 "use strict";
 const now=Date.now(),M=60e3,H=60*M,D=24*H;
 const LATEST="1.4.245";
 const OS={linux:"Linux",macos:"macOS",windows:"Windows"};
 // read·update 는 자동, 나머지는 승인(SPEC 기본 정책)
 const AUTO=new Set(["read","update"]);
 const RISK={read:["muted","읽기"],update:["info","갱신"],install:["warn","설치"],config:["warn","설정 변경"],
  external:["warn","외부 접속"],sudo:["crit","관리자 권한"],remove:["crit","삭제"]};
 const ACTION={install:"설치",update:"갱신",reconfigure:"재구성",uninstall:"제거"};
 const RESULT={
  done:{tone:"ok",label:"완료",desc:"계획한 단계를 모두 실행했습니다."},
  current:{tone:"info",label:"이미 최신",desc:"바꿀 것이 없어 아무것도 실행하지 않았습니다."},
  deferred:{tone:"warn",label:"보류",desc:"기다리면 스스로 풀리는 보류입니다. 조건이 풀리면 같은 계획으로 다시 시도합니다."},
  stale:{tone:"muted",label:"계획 바뀜",desc:"승인한 뒤 계획이 바뀌어 아무것도 실행하지 않았습니다. 계획을 다시 세워 확인하세요."},
  failed:{tone:"crit",label:"실패",desc:"실행하지 못한 단계가 있습니다."}};
 const STEP={ok:["ok","✓","완료"],skipped:["muted","–","건너뜀"],deferred:["warn","Ⅱ","보류"],failed:["crit","✕","실패"]};
 const KIND={
  secret:{tone:"crit",label:"평문 시크릿",hint:"값은 표시하지 않습니다. 환경 변수로 옮기세요."},
  drift:{tone:"warn",label:"은퇴한 훅이 남은 설정",hint:"ai-setup 이 은퇴시킨 훅을 아직 부르고 있습니다(드리프트)."},
  pathdup:{tone:"warn",label:"PATH 중복 설치",hint:"PATH 에서 먼저 나오는 쪽이 실행됩니다."},
  missing:{tone:"info",label:"설치되지 않은 하네스",hint:"설치는 하네스 모듈이 옮겨 오면 여기서 합니다. 지금은 안내만 합니다."},
  skill:{tone:"muted",label:"lock 밖의 스킬",hint:"~/.agents/.skill-lock.json 에 없는 스킬입니다. 그대로 둡니다."}};
 const KIND_ORDER=["secret","drift","pathdup","missing","skill"];
 // 아직 옮기지 않은 모듈(D12 순서). ai-setup 이 관리한다.
 const MODULES=[
  {id:"orca",name:"Orca",migrated:true},{id:"doctor",name:"doctor",migrated:true},
  {id:"hooks",name:"훅",what:"danger_guard 외 훅"},{id:"harness",name:"하네스",what:"설치·갱신(update-ai)"},
  {id:"skills",name:"스킬",what:"lock 기준 스킬"},{id:"plugins",name:"플러그인",what:"하네스 플러그인"},{id:"mcp",name:"MCP",what:"MCP 서버 등록"}];
 const ORDER="Orca(시범) → doctor → 훅 → 하네스 → 스킬·플러그인·MCP";

 const st=(o)=>Object.assign({module:"orca",installed:true,owned:true,version:LATEST,latest:LATEST,health:"ok",details:{}},o);
 const f=(kind,o)=>Object.assign({kind},o);
 const DEVICES=[
  {id:"s2",host:"s2",os:"linux",model:"Raspberry Pi 5 · core",reach:true,
   orca:st({owned:false,details:{server:"orca-serve.service",adopted:true,terminals:3}}),findings:[]},
  {id:"pit5",host:"pit5",os:"linux",model:"Raspberry Pi 5",reach:true,
   orca:st({owned:false,version:"1.4.243",details:{server:"orca-serve.service",adopted:true,terminals:0}}),
   findings:[f("drift",{hook:"stop_notify.py",file:"~/.claude/settings.json"})]},
  {id:"pi5-s3",host:"pi5-s3",os:"linux",model:"Raspberry Pi 5",reach:true,
   orca:st({owned:false,version:"1.4.240",health:"degraded",details:{server:"orca-serve.service",adopted:true,terminals:0,linger:false,note:"linger 가 꺼져 있어 로그아웃하면 서버가 멈춥니다."}}),
   findings:[f("missing",{name:"codex",cmd:"curl -fsSL https://chatgpt.com/codex/install.sh | sh"}),f("skill",{name:"grill-board"})]},
  {id:"cm5-1",host:"cm5-1",os:"linux",model:"Raspberry Pi CM5",reach:true,
   orca:st({installed:false,owned:false,version:null,health:"down",details:{note:"Orca 가 설치되어 있지 않습니다."}}),
   findings:[f("secret",{file:"~/.codex/config.toml",line:14,key:"OPENAI_API_KEY"}),f("missing",{name:"gemini",cmd:"python3 res/tools/npm_release.py install gemini"})]},
  {id:"thsvkd-ubuntu-1",host:"thsvkd-ubuntu-1",os:"linux",model:"Ubuntu 24.04 · x86_64",reach:true,
   orca:st({owned:false,version:"1.4.241",details:{server:"orca-serve.service",adopted:true,terminals:2}}),
   findings:[f("secret",{file:"~/.claude.json",line:88,key:"GITHUB_PERSONAL_ACCESS_TOKEN"}),f("pathdup",{name:"claude",first:"~/.local/bin/claude",also:["/usr/local/bin/claude"]})]},
  {id:"thsvkd-mac",host:"thsvkd-mac",os:"macos",model:"MacBook Pro",reach:false,seen:now-2*D-3*H,
   orca:st({version:"1.4.236",health:"down",details:{note:"마지막으로 본 상태입니다."}}),findings:null},
  {id:"thsvkd-kgolf-macmini",host:"thsvkd-kgolf-macmini",os:"macos",model:"Mac mini",reach:true,
   orca:st({health:"degraded",details:{server:"LaunchAgent",terminals:1,note:"LaunchAgent 의 PATH 에 ~/.local/bin 이 없어 Orca 터미널이 하네스를 찾지 못합니다."}}),
   findings:[f("pathdup",{name:"codex",first:"/opt/homebrew/bin/codex",also:["~/.npm-global/bin/codex"]}),f("skill",{name:"old-review",link:true})]},
  {id:"thsvkd-win",host:"thsvkd-win",os:"windows",model:"Windows 11",reach:false,seen:now-5*H-20*M,
   orca:st({version:"1.4.238",health:"down",details:{note:"마지막으로 본 상태입니다."}}),findings:null},
  {id:"kgolf",host:"kgolf",os:"windows",model:"Windows 11 · S4U",reach:true,
   orca:st({version:"1.4.238",health:"degraded",details:{server:"S4U 예약 작업",terminals:null,note:"관리자 권한 서버라 일반 권한으로는 터미널 수를 셀 수 없습니다."}}),
   findings:[f("drift",{hook:"session_banner.py",file:"~/.codex/hooks.json"}),f("missing",{name:"grok",cmd:"irm https://x.ai/cli/install.ps1 | iex"})]}
 ];
 const DEV=Object.fromEntries(DEVICES.map(d=>[d.id,d]));
 DEVICES.forEach((d,i)=>{d.checked=d.reach?now-(7+i*5)*M:null;});

 // ---------------------------------------------------------------- 계획(샘플)
 const S=(id,summary,risk,argv)=>({id,summary,risk,argv});
 const manifest=()=>S("manifest","공식 매니페스트에서 최신 버전 확인",  "read",["GET","<Orca 매니페스트>"]);
 function steps(d){
  const o=d.orca;
  if(d.id==="thsvkd-kgolf-macmini")return {action:"reconfigure",steps:[
   S("launchagent","LaunchAgent 에 로그인 셸의 PATH·SHELL 넣기","config",["write","~/Library/LaunchAgents/<Orca 서버>.plist"]),
   S("reload","LaunchAgent 다시 읽기(서버 재시작)","config",["launchctl","kickstart","-k","gui/<uid>/<Orca 서버>"]),
   S("pairing","데스크톱 클라이언트 페어링","config",["<임시 서버로 데스크톱 페어링>"])],
   blocked:[{step:"pairing",reason:"실행 중인 서버를 멈춰야 합니다. 데스크톱 프로필에 이 서버의 환경이 없어 임시 서버가 필요합니다."}]};
  if(!o.installed)return {action:"install",steps:[manifest(),
   S("download","github.com 에서 deb 내려받고 크기·SHA-512 검증","external",["GET","<Orca_"+LATEST+"_arm64.deb>"]),
   S("package","패키지 설치(/opt/Orca)","sudo",["sudo","-n","apt-get","install","-y","<검증한 deb>"]),
   S("unit","사용자 유닛 orca-serve.service 쓰기","install",["write","~/.config/systemd/user/orca-serve.service"]),
   S("enable","daemon-reload 뒤 서버 켜기","config",["systemctl","--user","enable","--now","orca-serve.service"]),
   S("skills","Orca 스킬 설치·기록","install",["<영수증: tool_installs 스킬 다이제스트>"])],blocked:[]};
  if(o.version===LATEST)return {action:"update",steps:[manifest()],blocked:[]};
  if(d.os==="windows")return {action:"update",steps:[manifest(),
   S("terminals","열린 터미널 확인(셀 수 없으면 러너 전환을 기다림)","read",["<Orca 런타임: 터미널 목록>"]),
   S("download","설치기 내려받고 크기·SHA-512 검증","update",["GET","<Orca-Setup-"+LATEST+".exe>"]),
   S("installer","설치기 조용히 실행","update",["<검증한 설치기>","/S","--updated"]),
   S("switch","러너의 전환 기다림(최대 300초)","read",["<러너 전환 대기>","300s"])],blocked:[]};
  const xs=[manifest(),
   S("terminals","열린 Orca 터미널 확인","read",["<Orca 런타임: 터미널 목록>"]),
   S("download","deb 내려받고 크기·SHA-512 검증","update",["GET","<Orca_"+LATEST+"_arm64.deb>"]),
   S("package","패키지 갱신(/opt/Orca)","sudo",["sudo","-n","apt-get","install","-y","<검증한 deb>"])];
  if(o.details.linger===false)xs.push(S("linger","linger 켜기(로그아웃해도 서버 유지)","config",["loginctl","enable-linger","<사용자>"]));
  xs.push(S("restart","orca-serve.service 재시작","update",["systemctl","--user","restart","orca-serve.service"]),
   S("skills","소유 스킬 다이제스트 다시 기록","update",["<영수증: tool_installs 스킬 다이제스트>"]));
  return {action:"update",steps:xs,blocked:[]};
 }
 // 샘플 해시(FNV-1a 네 벌을 이어 64자). 진짜 sha256 이 아니다 — 모양만 보인다.
 function hash(s){let out="";for(let k=0;k<4;k++){let h=0x811c9dc5^k*0x9e3779b9;for(let i=0;i<s.length;i++){h^=s.charCodeAt(i);h=Math.imul(h,0x01000193)>>>0;}out+=h.toString(16).padStart(8,"0")+(h^0xa5a5a5a5).toString(16).padStart(8,"0");}return out;}
 function makePlan(d){
  const p=Object.assign({module:"orca",device:d.host},steps(d));
  p.plan_id=hash(JSON.stringify(p)+d.orca.version);
  p.from=d.orca.version;p.to=LATEST;
  return p;
 }
 const needs=p=>p.steps.filter(s=>!AUTO.has(s.risk));
 const blockedOf=(p,id)=>p.blocked.find(b=>b.step===id);

 // ---------------------------------------------------------------- 결과(정해 둔 시뮬레이션)
 const OKMSG={manifest:LATEST+" 확인",terminals:"열린 터미널 없음",download:"크기·SHA-512 일치",package:"설치함",installer:"설치기 종료 코드 0",
  switch:"러너가 "+LATEST+" 로 전환함",linger:"켬",restart:"다시 시작함 · 응답 확인",skills:"3개 기록",unit:"썼음 · 다이제스트 기록",enable:"켜짐",
  launchagent:"PATH·SHELL 넣음",reload:"다시 읽음",pairing:"페어링함"};
 function outcome(d,p,j){
  const R=(id,result,message)=>({id,result,message});
  const sk=(from,msg)=>p.steps.slice(from).map(s=>R(s.id,"skipped",msg));
  if(p.blocked.length)return {status:"failed",steps:p.steps.map(s=>blockedOf(p,s.id)?R(s.id,"failed","막힘: "+blockedOf(p,s.id).reason):R(s.id,"skipped","막힌 단계가 있어 실행하지 않음"))};
  if(p.steps.length===1)return {status:"current",steps:[R("manifest","skipped","이미 "+LATEST)]};
  if(d.id==="kgolf"&&!j.tries){d.orca.version=LATEST;return {status:"stale",steps:[R("replan","skipped","실행 직전에 다시 세운 계획의 plan_id 가 다릅니다. 그사이 데스크톱 앱이 스스로 "+LATEST+" 로 갱신했습니다.")].concat(sk(0,"실행하지 않음"))};}
  if(d.id==="thsvkd-ubuntu-1"&&!j.tries)return {status:"deferred",steps:[R("manifest","ok",OKMSG.manifest),R("terminals","deferred","열린 터미널 2개 — 닫히면 스스로 다시 시도합니다")].concat(sk(2,"보류로 실행하지 않음"))};
  return {status:"done",steps:p.steps.map(s=>R(s.id,"ok",OKMSG[s.id]||"완료"))};
 }

 // ---------------------------------------------------------------- 작업 상태(기기×모듈)
 // phase: planning → plan → (pending: 승인 대기) → running → result. 키는 "기기:orca".
 const J={},subs=[];
 const history=[
  {dev:"s2",status:"done",at:now-26*H,text:"1.4.243 → "+LATEST+" 갱신"},
  {dev:"thsvkd-win",status:"deferred",at:now-6*H,text:"데스크톱 앱이 열려 있어 보류 — 그 뒤 연결이 끊김"}];
 const emit=()=>subs.forEach(fn=>fn());
 const key=d=>d+":orca";
 function pend(id,ago){const d=DEV[id];J[key(id)]={dev:id,phase:"pending",plan:makePlan(d),asked:now-ago,notified:now-ago+20e3,tries:0};}
 pend("pit5",4*M);pend("pi5-s3",11*M);
 const job=id=>J[key(id)]||null;
 const wait=ms=>new Promise(r=>setTimeout(r,ms));
 async function plan(id){
  const d=DEV[id];if(!d.reach)return;
  J[key(id)]={dev:id,phase:"planning",tries:0};emit();
  await wait(650);
  Object.assign(J[key(id)],{phase:"plan",plan:makePlan(d),at:Date.now()});emit();
 }
 function request(id){
  const j=job(id);if(!j||j.phase!=="plan")return;
  if(needs(j.plan).length){Object.assign(j,{phase:"pending",asked:Date.now(),notified:Date.now()});emit();}
  else run(id);
 }
 async function run(id){
  const j=job(id),d=DEV[id];if(!j||!j.plan)return;
  Object.assign(j,{phase:"running",cursor:0,approved:j.phase==="pending"?Date.now():j.approved});emit();
  const r=outcome(d,j.plan,j);
  // 단계가 하나씩 끝나는 것처럼 보인다(결과 줄 수만큼)
  for(let i=1;i<=r.steps.length;i++){await wait(r.steps[i-1].result==="ok"?380:200);j.cursor=i;j.partial=r.steps.slice(0,i);emit();}
  await wait(250);
  Object.assign(j,{phase:"result",result:r,at:Date.now(),tries:j.tries+1});
  if(r.status==="done"){d.orca.version=LATEST;d.orca.installed=true;d.orca.health="ok";if(d.orca.details.linger===false)d.orca.details.linger=true;delete d.orca.details.note;
   if(j.plan.action==="install")Object.assign(d.orca,{owned:true,details:{server:"orca-serve.service",terminals:0}});}
  history.unshift({dev:id,status:r.status,at:Date.now(),text:ACTION[j.plan.action]+(j.plan.action==="update"&&r.status!=="current"?" "+j.plan.from+" → "+LATEST:"")});
  emit();
 }
 const approve=id=>run(id);
 function reject(id){const j=job(id);if(!j)return;history.unshift({dev:id,status:"rejected",at:Date.now(),text:"승인 거절 · "+ACTION[j.plan.action]});delete J[key(id)];emit();}
 function close(id){delete J[key(id)];emit();}
 const busy=new Set();
 async function recheck(id){if(!DEV[id].reach)return;busy.add(id);emit();await wait(800);DEV[id].checked=Date.now();busy.delete(id);emit();}

 // ---------------------------------------------------------------- 파생 값
 function orcaState(d){
  const o=d.orca,j=job(d.id);
  if(!d.reach)return {tone:"muted",label:"연결 안 됨"};
  if(j&&j.phase==="pending")return {tone:"warn",label:"승인 대기"};
  if(j&&(j.phase==="running"||j.phase==="planning"))return {tone:"info",label:j.phase==="running"?"실행 중":"계획 세우는 중"};
  if(j&&j.phase==="result"&&j.result.status!=="done"&&j.result.status!=="current")return {tone:RESULT[j.result.status].tone,label:RESULT[j.result.status].label};
  if(!o.installed)return {tone:"muted",label:"설치 안 됨"};
  if(o.health==="degraded"&&o.version===LATEST)return {tone:"warn",label:"재구성 필요"};
  if(o.version!==LATEST)return {tone:"info",label:"갱신 가능"};
  return {tone:"ok",label:"최신"};
 }
 const pending=()=>DEVICES.map(d=>job(d.id)).filter(j=>j&&j.phase==="pending").sort((a,b)=>a.asked-b.asked);
 const findings=()=>DEVICES.flatMap(d=>(d.findings||[]).map(x=>({d,x}))).sort((a,b)=>KIND_ORDER.indexOf(a.x.kind)-KIND_ORDER.indexOf(b.x.kind));
 const ago=ts=>{const s=(Date.now()-ts)/1000;return s<45?"방금":s<3600?Math.max(1,Math.round(s/60))+"분 전":s<86400?Math.floor(s/3600)+"시간 전":Math.floor(s/86400)+"일 전";};
 const short=id=>id.slice(0,12);

 window.SETUP_LAB={DEVICES,DEV,MODULES,ORDER,LATEST,OS,AUTO,RISK,ACTION,RESULT,STEP,KIND,KIND_ORDER,history,busy,
  job,plan,request,approve,reject,run,close,recheck,needs,blockedOf,orcaState,pending,findings,ago,short,
  on:fn=>subs.push(fn)};
})();
