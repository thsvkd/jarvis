/* 랩 04 공용 샘플 데이터. 세 시안이 같은 기기·계정·사용률을 쓴다. 실제 서버 값이 아니다.
 * 모양은 게이트웨이 /api/orca/usage 로 낼 정규화 형식(기기 → 프로바이더 → 계정 → 창)을 따른다.
 * 같은 구독 계정은 어느 기기에서 보든 사용률이 같다(구독 단위 한도). */
(function(){
 "use strict";
 const now=Date.now(),H=3600e3,D=24*H;
 const win=(key,used,resetIn)=>({key,used,resets_at:now+resetIn});
 // provider:email → 사용량. 같은 계정은 여러 기기에서 같은 객체를 가리킨다.
 const U={
  "claude:me@example.com":{plan:"Max 20x",windows:[win("session",64,2.2*H),win("weekly",41,3.4*D),win("fable",12,3.4*D)],
   extra:{spent:5.13,limit:100,currency:"USD",enabled:false,reason:"크레딧 소진"}},
  "claude:work@example.com":{plan:"Max 5x",windows:[win("session",93,0.6*H),win("weekly",82,1.2*D),win("fable",30,1.2*D)]},
  "claude:lab@example.com":{plan:"Pro",status:"error",error:"로그인이 만료되었습니다. 그 기기에서 claude 로 다시 로그인하세요.",windows:[]},
  "codex:me@example.com":{plan:"Pro",windows:[win("session",18,3.9*H),win("weekly",12,5.5*D)],credits:1},
  "codex:team@example.com":{plan:"Team · Acme",windows:[win("session",47,1.5*H),win("weekly",88,0.9*D)]},
  "grok:me@example.com":{plan:"SuperGrok",windows:[win("monthly",33,17*D)]},
  "grok:expired":{status:"error",error:"Grok 로그인이 만료되었습니다. Orca 가 도는 기기에서 grok 을 한 번 실행하세요.",windows:[]},
  "antigravity:me@example.com":{windows:[win("weekly",22,6.1*D)],
   buckets:[{name:"Gemini 모델",used:4,resets_at:now+6.1*D},{name:"Claude·GPT 모델",used:22,resets_at:now+6.1*D}]}
 };
 const acc=(p,email,active)=>({key:p+":"+email,provider:p,label:email==="expired"?"기본 계정":email,active:!!active});
 const DEVICES=[
  {id:"s2",host:"pi5-server2",model:"Raspberry Pi 5",state:"ok",accounts:[acc("claude","me@example.com",1),acc("claude","work@example.com"),acc("codex","me@example.com",1),acc("codex","team@example.com"),acc("grok","expired",1),acc("antigravity","me@example.com",1)]},
  {id:"pit5",host:"pit5",model:"Raspberry Pi 5",state:"ok",accounts:[acc("claude","me@example.com",1),acc("codex","me@example.com",1)]},
  {id:"mac-1",host:"macbookpro",model:"MacBook Pro",state:"ok",accounts:[acc("claude","work@example.com",1),acc("claude","me@example.com"),acc("codex","team@example.com",1),acc("grok","me@example.com",1)]},
  {id:"mac-2",host:"macstudio",model:"Mac Studio",state:"ok",accounts:[acc("claude","me@example.com",1),acc("grok","me@example.com",1),acc("antigravity","me@example.com",1)]},
  {id:"ubuntu-1",host:"ubuntu-1",model:"Ubuntu 24.04",state:"ok",accounts:[acc("codex","me@example.com",1),acc("claude","lab@example.com",1)]},
  {id:"win-1",host:"win-1",model:"Windows 11",state:"off",accounts:[]},
  {id:"win-2",host:"win-2",model:"Windows 11",state:"ok",accounts:[acc("claude","me@example.com",1)]},
  {id:"pi5-s3",host:"pi5-s3",model:"Raspberry Pi 5",state:"none",accounts:[]},
  {id:"cm5-1",host:"cm5-1",model:"CM5",state:"old",accounts:[]}
 ];
 DEVICES.forEach((d,i)=>{d.fetched_at=now-(40+i*17)*1000;});
 const PROVIDERS=[
  {id:"claude",name:"Claude",tone:"s4"},{id:"codex",name:"Codex",tone:"s1"},
  {id:"grok",name:"Grok",tone:"s3"},{id:"antigravity",name:"Antigravity",tone:"s2"}
 ];
 const WIN={session:"5시간",weekly:"주간",fable:"Fable 주간",monthly:"월간"};
 const STATE={off:"Orca 가 꺼져 있어 사용량을 읽지 못했습니다.",none:"이 기기에는 Orca 가 없습니다.",
  old:"이 기기 jarvis 가 사용량 API 를 지원하지 않습니다(갱신 필요).",down:"기기에 닿지 못했습니다."};
 const usage=a=>U[a.key];
 const peak=a=>{const u=usage(a);return u.status==="error"?null:Math.max(0,...u.windows.map(w=>w.used));};
 const tone=p=>p==null?"muted":p>=90?"crit":p>=75?"warn":"ok";
 const left=(ts,t=Date.now())=>{
  const m=Math.max(0,Math.round((ts-t)/60000));
  if(m<60)return m+"분 후";
  if(m<1440)return Math.floor(m/60)+"시간 "+m%60+"분 후";
  return Math.floor(m/1440)+"일 "+Math.floor(m%1440/60)+"시간 후";
 };
 const at=ts=>new Date(ts).toLocaleString("ko-KR",{month:"numeric",day:"numeric",weekday:"short",hour:"2-digit",minute:"2-digit",hour12:false});
 const ago=ts=>{const s=(Date.now()-ts)/1000;return s<60?"방금":s<3600?Math.floor(s/60)+"분 전":Math.floor(s/3600)+"시간 전";};
 // 시뮬레이션: 새로고침은 0.9초 뒤 끝난다. Orca 가 꺼졌거나 jarvis 가 오래된 기기는 실패한다.
 function refresh(id){
  return new Promise((res,rej)=>setTimeout(()=>{
   const d=DEVICES.find(x=>x.id===id);
   if(d.state!=="ok")return rej(new Error(STATE[d.state]));
   d.fetched_at=Date.now();res(d);
  },900));
 }
 window.USAGE_LAB={DEVICES,PROVIDERS,WIN,STATE,usage,peak,tone,left,at,ago,refresh};
})();
