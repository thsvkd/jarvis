/* Jarvis — AI 셋업(core). 디자인 랩 05 의 C 안: 승인 대기·할 일 먼저, 기기 상세는 서랍.
 * API(docs/SPEC.md "AI 셋업 화면", jarvis/modules_api.py):
 *   GET  /api/devices/modules[?refresh=1 + X-Jarvis-Action: modules-refresh]  기기마다 {modules: {orca, doctor}, job} 또는 {status: 501/502, error}
 *   GET  /api/devices/<id>/modules/<이름>/plan                                  모듈 계약의 plan
 *   POST /api/devices/<id>/modules/<이름>/apply?plan_id=…  (X-Jarvis-Action: module-apply, viewers 만) → 202 작업. 진행은 그 기기의 job 을 다시 읽어 본다
 * 승인 목록·휴대폰 알림·결과 기록 API 는 아직 없다(D7): '승인 대기'는 이 화면에서 연 계획 중 승인이 필요한 것이고, 실행을 누르는 것이 승인이다.
 * '최근 결과'는 기기마다 게이트웨이가 기억하는 마지막 작업(job)이다.
 * 시크릿: doctor 는 파일 위치와 키 이름만 준다. 이 화면도 그것만 보인다. */
(function () {
  "use strict";
  const P = window.JARVIS, { html } = P, $ = (s) => document.querySelector(s);
  const AUTO = new Set(["read", "update"]); // SPEC 기본 정책: read·update 는 자동, 나머지는 승인
  const RISK = { read: ["muted", "읽기"], update: ["info", "갱신"], install: ["warn", "설치"], config: ["warn", "설정 변경"],
    external: ["warn", "외부 접속"], sudo: ["crit", "관리자 권한"], remove: ["crit", "삭제"] };
  const ACTION = { install: "설치", update: "갱신", reconfigure: "재구성", uninstall: "제거" };
  const RESULT = {
    done: { tone: "ok", label: "완료", desc: "계획한 단계를 모두 실행했습니다." },
    current: { tone: "info", label: "이미 최신", desc: "바꿀 것이 없어 아무것도 실행하지 않았습니다." },
    deferred: { tone: "warn", label: "보류", desc: "열린 터미널·데스크톱 앱 때문에 미뤘습니다. 그것이 닫히면 계획을 다시 세워 실행하세요." },
    stale: { tone: "muted", label: "계획 바뀜", desc: "계획을 본 뒤 기기 상태가 바뀌어 아무것도 실행하지 않았습니다. 계획을 다시 세워 확인하세요." },
    failed: { tone: "crit", label: "실패", desc: "실행하지 못한 단계가 있습니다." },
    interrupted: { tone: "warn", label: "중단됨", desc: "적용 중에 게이트웨이가 다시 시작되어 끝까지 했는지 모릅니다. 기기 상태를 다시 확인하세요." } };
  const STEP = { ok: ["ok", "✓", "완료"], skipped: ["muted", "–", "건너뜀"], deferred: ["warn", "Ⅱ", "보류"], failed: ["crit", "✕", "실패"] };
  const KIND = {
    secret: { tone: "crit", label: "평문 시크릿", hint: "값은 표시하지 않습니다. 환경 변수로 옮기세요." },
    hook_drift: { tone: "warn", label: "은퇴한 훅이 남은 설정", hint: "ai-setup 이 은퇴시킨 훅을 아직 부르고 있습니다." },
    duplicate: { tone: "warn", label: "PATH 중복 설치", hint: "PATH 에서 먼저 나오는 쪽이 실행됩니다." },
    missing: { tone: "info", label: "설치되지 않은 하네스", hint: "설치는 하네스 모듈을 옮긴 뒤 여기서 합니다. 지금은 명령만 안내합니다." },
    skill: { tone: "muted", label: "lock 밖의 스킬", hint: "그대로 둡니다." } };
  const KIND_ORDER = ["secret", "hook_drift", "duplicate", "missing", "skill"];
  const LEGACY = "훅·하네스·스킬·플러그인·MCP 는 아직 jarvis 모듈이 아니라 ai-setup 이 관리합니다. 이전 순서: Orca(시범) → doctor → 훅 → 하네스 → 스킬·플러그인·MCP";
  const POLL_RUN = 3000, POLL_IDLE = 60000;

  // plans[id] = {phase: loading|plan|error, plan, at, error, sent}   sent: 실행을 보낸 plan_id
  const S = { data: null, error: null, loading: false, refreshing: false, plans: {}, applyErr: {}, sending: new Set(),
    denied: null, dismissed: {}, drawer: null };

  // ---------------------------------------------------------------- 값 읽기
  const name = (d) => d.host || d.id;
  const reach = (d) => d.status === 200 && d.modules;
  const mod = (d, m) => (reach(d) && d.modules[m]) || null;
  const newer = (a, b) => { // a 가 b 보다 새 판인가(점으로 나눈 숫자)
    const x = String(a).split(".").map(Number), y = String(b).split(".").map(Number);
    for (let i = 0; i < Math.max(x.length, y.length); i++) { const k = (x[i] || 0) - (y[i] || 0); if (k) return k > 0; }
    return false;
  };
  const ago = (s) => { if (!s) return "–"; const t = Date.now() / 1000 - s; return t < 45 ? "방금" : t < 3600 ? Math.max(1, Math.round(t / 60)) + "분 전" : t < 86400 ? Math.floor(t / 3600) + "시간 전" : Math.floor(t / 86400) + "일 전"; };
  const needs = (p) => p.steps.filter((s) => !AUTO.has(s.risk));
  const job = (d, m) => (d.job && (!m || d.job.module === m) ? d.job : null);
  // 작업의 결과 종류: viewers 는 result.status, 그 밖에는 job.status 만 온다. interrupted 는 state 다
  const jstat = (j) => (j.state === "interrupted" ? "interrupted" : j.result ? j.result.status || null : j.status || null);
  const jend = (j) => j.finished_at || j.started_at; // interrupted 는 끝 시각이 없다
  const full = () => !!(S.data && S.data.full !== false); // viewers 가 아니면 상태만 온다(계획·실행·doctor 세부 없음)
  const VIEWERS_ONLY = "doctor 세부·계획·실행은 허용된 기기(viewers)에서만 볼 수 있습니다.";
  const running = (d) => !!(d.job && d.job.state === "running");
  const plan = (id) => S.plans[id] || null;
  const pending = () => (S.data ? S.data.devices : []).filter((d) => { const p = plan(d.id); return p && p.phase === "plan" && !p.sent && needs(p.plan).length && !running(d); });
  function offline(d) {
    if (d.status === 501) return { tone: "muted", label: "jarvis 갱신 필요" };
    if (d.status === 403) return { tone: "crit", label: "권한 없음" };
    return { tone: "muted", label: "연결 안 됨" };
  }
  function orcaState(d) {
    if (!reach(d)) return offline(d);
    const o = mod(d, "orca"), p = plan(d.id), j = job(d, "orca");
    if (j && j.state === "running") return { tone: "info", label: "실행 중" };
    if (p && p.phase === "plan" && !p.sent && needs(p.plan).length) return { tone: "warn", label: "승인 대기" };
    if (!o || o.error || o.ok === false) return { tone: "crit", label: "상태 확인 실패" };
    if (!o.installed) return { tone: "muted", label: "설치 안 됨" };
    if (o.latest && o.version && newer(o.latest, o.version)) return { tone: "info", label: "갱신 가능" };
    if (o.health === "down") return { tone: "crit", label: "서버 응답 없음" };
    if (o.health !== "ok") return { tone: "warn", label: "상태 저하" };
    return { tone: "ok", label: o.latest ? "최신" : "설치됨" };
  }
  const TODO = new Set(["설치 안 됨", "갱신 가능", "상태 저하", "서버 응답 없음", "상태 확인 실패"]);
  // doctor 메시지(영어, ai-setup 에서 옮긴 모듈의 원문)를 읽어 위치·이름만 뽑는다. 모양이 다르면 원문을 그대로 보인다.
  const RX = {
    secret: /^plaintext secret: (.+):(\d+) key (\S+) holds a literal value/,
    duplicate: /^(.+?): duplicate installs on PATH; first wins: (.+?); also: (.+)$/,
    hook_drift: /^retired hook (.+?) is still referenced in (.+)$/,
    skill: /^skill (.+?)( \(link\))? is not in \.skill-lock\.json/ };
  function findings(d) {
    const doc = mod(d, "doctor"), x = doc && doc.details;
    if (!x) return [];
    const out = (x.findings || []).map((f) => ({ kind: KIND[f.kind] ? f.kind : "skill", m: (RX[f.kind] && RX[f.kind].exec(f.message || "")) || null, raw: f.message || "" }));
    for (const t of x.tools || []) if (!(t.found || []).length && t.kind === "harness") out.push({ kind: "missing", tool: t });
    return out.sort((a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind));
  }
  const worst = (xs) => ["crit", "warn", "info", "muted"].find((t) => xs.some((x) => KIND[x.kind].tone === t)) || "ok";

  // ---------------------------------------------------------------- 작은 조각
  const pill = (t, label) => html`<span class="pill" data-tone="${t}"><i class="shape"></i>${label}</span>`;
  const risk = (r) => html`<span class="badge" data-tone="${(RISK[r] || ["muted"])[0]}">${(RISK[r] || [0, r])[1]}</span>`;
  const need = (r) => (AUTO.has(r) ? html`<span class="su-auto">자동</span>` : html`<span class="su-need">승인 필요</span>`);
  const act = (a, id, label, cls, extra) => html`<button type="button" class="${"su-btn " + (cls || "")}" data-act="${a}" data-id="${id || ""}" ${extra || ""}>${label}</button>`;
  const ver = (v) => (v ? html`<b class="mono">${v}</b>` : html`<b class="su-none">없음</b>`);
  const short = (id) => String(id || "").slice(0, 12);
  const errBox = (m) => html`<p class="su-warn" role="alert">${m}</p>`;

  function flow(idx, auto) {
    return html`<ol class="su-flow" aria-label="진행 단계">${["상태", "계획", "승인", "실행", "결과"].map((n, i) => html`<li data-s="${i < idx ? "past" : i === idx ? "now" : "next"}" ${i === idx ? 'aria-current="step"' : ""}>${i === 2 && auto && idx >= 1 ? "승인 없음" : n}</li>`)}</ol>`;
  }
  function status(d) {
    const o = mod(d, "orca"), st = orcaState(d);
    if (!o) return html`<div class="su-status">${pill(st.tone, st.label)}</div>`;
    const x = o.details || {}, facts = [];
    if (x.kind) facts.push(html`<span>설치 형태 <b>${x.kind}</b></span>`);
    if (o.installed && o.details) facts.push(html`<span>실행 중인 판 <b>${x.running_version || "확인 안 됨"}</b></span>`); // 공개 모양에는 details 가 없다
    if (x.server) facts.push(html`<span>서버 <b>${[x.server.service_manager, x.server.state].filter(Boolean).join(" · ") || "기록됨"}</b></span>`);
    if (o.installed) facts.push(html`<span>소유 <b>${o.owned ? "jarvis 설치" : "기록만"}</b></span>`);
    facts.push(html`<span>${ago(o.checked_at)} 확인</span>`);
    const notes = [o.error, x.error, x.update_supported === false && x.update_reason ? "갱신 불가: " + x.update_reason : null].filter(Boolean);
    return html`<div class="su-status"><div class="su-ver"><span class="su-k">설치본</span>${ver(o.version)}${o.latest && o.version && newer(o.latest, o.version) ? html`<span class="su-to">→ ${o.latest}</span>` : ""}</div>${pill(st.tone, st.label)}<p class="su-facts">${facts}</p>${notes.map((n) => html`<p class="su-note" data-tone="${o.health === "down" || o.error ? "crit" : "warn"}">${n}</p>`)}</div>`;
  }
  function steps(p, results, cursor) {
    const got = Object.fromEntries((results || []).map((r) => [r.id, r]));
    const blocked = Object.fromEntries((p.blocked || []).map((b) => [b.step, b.reason]));
    return html`<ol class="su-steps">${p.steps.map((s, i) => {
      const r = got[s.id], b = blocked[s.id], live = cursor && !r;
      return html`<li class="su-step" data-blocked="${!!b}" ${r ? html`data-r="${r.result}"` : ""}>
        <span class="su-i" ${r && STEP[r.result] ? html`data-tone="${STEP[r.result][0]}"` : ""} aria-hidden="true">${r && STEP[r.result] ? STEP[r.result][1] : live ? "…" : i + 1}</span>
        <div class="su-sm"><b>${s.summary || s.id}</b>${Array.isArray(s.argv) && s.argv.length ? html`<code>${s.argv.join(" ")}</code>` : ""}${b ? html`<p class="su-block"><b>막힘</b> ${b}</p>` : ""}${r && !(b && r.message === b) ? html`<p class="su-rmsg" data-tone="${(STEP[r.result] || ["muted"])[0]}"><span class="sh-sr">${(STEP[r.result] || [0, 0, r.result])[2]}: </span>${r.message || ""}</p>` : ""}</div>
        <div class="su-rk">${risk(s.risk)}${need(s.risk)}</div></li>`;
    })}</ol>${(p.blocked || []).filter((b) => !p.steps.some((s) => s.id === b.step)).map((b) => html`<p class="su-block"><b>막힘 · ${b.step}</b> ${b.reason}</p>`)}`;
  }
  function planSum(p) {
    const n = needs(p), kinds = [...new Set(n.map((s) => (RISK[s.risk] || [0, s.risk])[1]))];
    return html`<p class="su-psum"><span>단계 <b>${p.steps.length}</b></span><span>자동 <b>${p.steps.length - n.length}</b></span><span data-tone="${n.length ? "warn" : "ok"}">승인 필요 <b>${n.length}</b>${kinds.length ? " · " + kinds.join("·") : ""}</span>${p.blocked.length ? html`<span data-tone="crit">막힘 <b>${p.blocked.length}</b></span>` : ""}</p>`;
  }
  function what(p) {
    const pkg = p.steps.find((s) => s.id === "package" && s.target);
    return "Orca " + (ACTION[p.action] || p.action) + (pkg ? " → " + pkg.target : "");
  }
  function result(j) {
    const r = j.result || {}, st = jstat(j), m = RESULT[st] || { tone: "crit", label: "실패", desc: "적용 결과를 받지 못했습니다." };
    const extra = st !== "interrupted" && r.error ? r.error : st === "stale" && r.plan_id ? "지금 계획의 plan_id 는 " + short(r.plan_id) + "… 입니다." : "";
    return html`<div class="su-result" data-tone="${m.tone}" role="status"><i class="shape" aria-hidden="true"></i><div><b>${m.label}</b> <span class="su-code">${st || "error"}${j.code != null ? " · 종료 코드 " + j.code : ""}</span><p>${m.desc}${extra ? " " + extra : ""}</p><p class="su-hint">${st === "interrupted" ? ago(j.started_at) + " 시작" : ago(j.finished_at) + " 끝남"}${j.plan_id ? " · 실행한 계획 " + short(j.plan_id) + "…" : ""}</p></div></div>`;
  }
  function runBtn(id, p) {
    const n = needs(p).length, busy = S.sending.has(id);
    const label = busy ? "보내는 중…" : n ? "승인하고 실행" : "실행";
    return act("apply", id, label, "primary", S.denied || busy ? "disabled" : "");
  }
  const denied = () => (S.denied ? html`<p class="su-warn">실행할 수 없습니다: ${S.denied}</p>` : "");

  // Orca 한 기기의 흐름: 상태 → 계획 → (승인) → 실행 → 결과
  function orca(d) {
    const id = d.id;
    if (!reach(d)) return html`<div class="su-orca">${status(d)}<p class="su-muted">${d.error || "기기에 닿지 못했습니다."}</p></div>`;
    const p = plan(id), j = job(d, "orca"), err = S.applyErr[id];
    if (!full()) return html`<div class="su-orca" data-flow="${id}" tabindex="-1">${status(d)}${j && j.state === "running" ? html`<div class="su-prog su-prog-run" aria-hidden="true"><i></i></div><p class="su-hint su-run" role="status">적용 실행 중 · ${ago(j.started_at)} 시작</p>` : j && S.dismissed[id] !== jend(j) ? result(j) : ""}<p class="su-note" data-tone="muted">계획·실행은 허용된 기기(viewers)에서만 할 수 있습니다.</p></div>`;
    const mine = j && p && j.plan_id && p.sent === j.plan_id;
    const showResult = j && j.state !== "running" && S.dismissed[id] !== jend(j) && (mine || !p);
    let idx = 0, body;
    if (j && j.state === "running") {
      idx = 3;
      body = html`<section class="su-plan" aria-label="실행 중인 작업"><header class="su-ph"><div class="su-pt"><span class="su-k">실행 중 · ${name(d)}</span><b>${mine ? what(p.plan) : (j.module || "") + " 적용"}</b></div><code class="su-pid">plan_id ${short(j.plan_id)}…</code></header>${err ? errBox(err) : ""}<div class="su-prog su-prog-run" aria-hidden="true"><i></i></div><p class="su-hint su-run" role="status">${ago(j.started_at)} 시작 · 끝나면 결과가 여기에 나옵니다(몇 초마다 확인)</p>${mine ? steps(p.plan, null, true) : ""}</section>`;
    } else if (showResult) {
      idx = 4;
      body = html`<section class="su-plan" aria-label="실행 결과">${mine ? html`<header class="su-ph"><div class="su-pt"><span class="su-k">결과 · ${name(d)}</span><b>${what(p.plan)}</b></div></header>` : ""}${result(j)}<div class="su-acts">${act("plan", id, "계획 다시 세우기", "primary")}${act("dismiss", id, "닫기")}</div>${mine ? steps(p.plan, (j.result || {}).steps) : (j.result && j.result.steps || []).length ? html`<ul class="su-finds">${j.result.steps.map((r) => html`<li class="su-find" data-tone="${(STEP[r.result] || ["muted"])[0]}"><i class="shape" aria-hidden="true"></i><div><b>${r.id} · ${(STEP[r.result] || [0, 0, r.result])[2]}</b><p>${r.message || ""}</p></div></li>`)}</ul>` : ""}</section>`;
    } else if (p && p.phase === "loading") {
      idx = 1;
      body = html`<p class="su-hint su-run" role="status">계획 세우는 중… (아무것도 바꾸지 않습니다)</p>`;
    } else if (p && p.phase === "error") {
      idx = 1;
      body = html`${errBox("계획을 세우지 못했습니다: " + p.error)}<div class="su-acts">${act("plan", id, "다시 시도", "primary")}${act("drop", id, "닫기")}</div>`;
    } else if (p && p.phase === "plan") {
      const n = needs(p.plan).length;
      idx = n ? 2 : 1;
      const empty = !p.plan.steps.length;
      body = html`<section class="su-plan" aria-label="실행 계획"><header class="su-ph"><div class="su-pt"><span class="su-k">계획 · ${name(d)} · ${ago(p.at / 1000)} 세움</span><b>${empty ? "바꿀 것이 없습니다" : what(p.plan)}</b></div><code class="su-pid" title="plan_id 앞 12자. 실행은 이 계획과 지금 다시 세운 계획이 같을 때만 합니다">plan_id ${short(p.plan.plan_id)}…</code></header>${empty ? "" : steps(p.plan)}${empty ? "" : planSum(p.plan)}${p.plan.blocked.length && !empty ? html`<p class="su-warn">막힌 단계가 있어 실행하면 <b>failed</b> 로 끝납니다. 막힌 이유를 먼저 풀어야 합니다.</p>` : ""}${err ? errBox(err) : ""}${denied()}<div class="su-acts">${empty ? "" : runBtn(id, p.plan)}${act("drop", id, empty ? "닫기" : "계획 버리기")}<span class="su-hint">${empty ? "Orca 가 이미 계획대로입니다." : n ? "관리자 권한·설정 변경이 있어 누르는 것이 승인입니다" : "읽기·갱신 단계뿐이라 승인 없이 실행합니다"}</span></div></section>`;
    } else {
      body = html`${err ? errBox(err) : ""}<div class="su-acts">${act("plan", id, mod(d, "orca") && mod(d, "orca").installed === false ? "설치 계획 미리 보기" : "계획 미리 보기", "primary")}<span class="su-hint">계획은 아무것도 바꾸지 않습니다(dry-run)</span></div>`;
    }
    return html`<div class="su-orca" data-flow="${id}" tabindex="-1">${status(d)}${flow(idx, p && p.plan && !needs(p.plan).length)}${body}</div>`;
  }
  function fbody(f) {
    const m = f.m, c = (s) => html`<code>${s}</code>`;
    if (f.kind === "missing") return html`${c(f.tool.id)} 없음${f.tool.hint ? html` · 설치 명령 ${c(f.tool.hint)}` : ""}`;
    if (!m) return f.raw;
    if (f.kind === "secret") return html`${c(m[1] + ":" + m[2])} · 키 ${c(m[3])} · <span class="su-hide">값 숨김</span>`;
    if (f.kind === "duplicate") return html`${c(m[1])} · 우선 ${c(m[2])} · 가려짐 ${m[3].split(", ").map((a, i) => html`${i ? ", " : ""}${c(a)}`)}`;
    if (f.kind === "hook_drift") return html`${c(m[2])} 에 은퇴한 훅 ${c(m[1])} 이 남아 있음`;
    if (f.kind === "skill") return html`${c(m[1])}${m[2] ? " (링크)" : ""} · .skill-lock.json 에 없음, 그대로 둠`;
    return f.raw;
  }
  function doctor(d) {
    if (!reach(d)) return html`<p class="su-muted">점검하지 못했습니다.</p>`;
    const doc = mod(d, "doctor");
    if (!doc || doc.error) return errBox("doctor 를 읽지 못했습니다: " + ((doc && doc.error) || "응답 없음"));
    if (!full()) return html`<div class="su-doc"><p class="su-meta"><span class="badge">읽기 전용</span>${pill(doc.health === "ok" ? "ok" : "warn", doc.health === "ok" ? "발견 없음" : "발견 있음")}<span>${ago(doc.checked_at)} 점검</span></p><p class="su-note" data-tone="muted">발견 항목의 위치는 허용된 기기(viewers)에서만 볼 수 있습니다.</p></div>`;
    const xs = findings(d), tools = (doc.details && doc.details.tools) || [];
    const groups = KIND_ORDER.map((k) => ({ k, list: xs.filter((f) => f.kind === k) })).filter((g) => g.list.length);
    return html`<div class="su-doc"><p class="su-meta"><span class="badge">읽기 전용</span><span>${ago(doc.checked_at)} 점검 · 실행할 것이 없습니다</span></p>${xs.length ? groups.map((g) => html`<div class="su-kg"><p class="su-kh" data-tone="${KIND[g.k].tone}"><i class="shape" aria-hidden="true"></i><b>${KIND[g.k].label}</b> <span>${g.list.length}건 · ${KIND[g.k].hint}</span></p><ul class="su-finds">${g.list.map((f) => html`<li class="su-find"><p>${fbody(f)}</p></li>`)}</ul></div>`) : html`<p class="su-clean" data-tone="ok"><i class="shape" aria-hidden="true"></i>발견 항목 없음</p>`}${tools.length ? html`<details class="su-tools-d"><summary>하네스·도구 ${tools.length}개</summary><ul class="su-tl">${tools.map((t) => html`<li data-off="${!(t.found || []).length}"><b>${t.id}</b><span>${(t.found || []).length ? t.version || "판 확인 안 됨" : "없음"}</span>${(t.found || [])[0] ? html`<code>${t.found[0]}</code>` : ""}</li>`)}</ul></details>` : ""}</div>`;
  }
  function approvals() {
    const xs = pending();
    if (!xs.length) return "";
    return html`<div class="tile su-appr"><header class="tile-head wrap-head"><h2 class="tile-title">승인 대기</h2><span class="badge" data-tone="warn">${xs.length}건</span><span class="tile-aside">이 화면에서 연 계획입니다. 공용 승인 목록·휴대폰 알림은 아직 없습니다.</span></header><ul>${xs.map((d) => { const p = plan(d.id).plan;
      return html`<li class="su-ar"><div class="su-ar-t"><b>${name(d)}</b><span>${what(p)}</span><code class="su-pid">${short(p.plan_id)}…</code></div><div class="su-ar-n">${needs(p).map((s) => html`<span class="su-ar-s">${risk(s.risk)}${s.summary || s.id}</span>`)}</div>${S.applyErr[d.id] ? errBox(S.applyErr[d.id]) : ""}<div class="su-acts">${runBtn(d.id, p)}${act("drop", d.id, "계획 버리기")}${act("open", d.id, "계획 전체 보기")}<span class="su-hint">${ago(plan(d.id).at / 1000)} 세운 계획</span></div></li>`; })}</ul>${denied()}</div>`;
  }

  // ---------------------------------------------------------------- 본문(C 안)
  function body() {
    if (!S.data) return S.error ? errBox(S.error) : html`<p class="su-muted">기기 목록을 읽는 중…</p>`;
    const devs = S.data.devices;
    const off = devs.filter((d) => !reach(d)), all = devs.flatMap((d) => findings(d).map((f) => ({ d, f })));
    const secrets = all.filter((x) => x.f.kind === "secret").length;
    const todoOrca = devs.filter((d) => reach(d) && TODO.has(orcaState(d).label));
    const live = devs.filter((d) => { const j = job(d, "orca"), p = plan(d.id); return j && (j.state === "running" || (p && j.plan_id && p.sent === j.plan_id && S.dismissed[d.id] !== jend(j))); });
    const todo = [];
    todoOrca.filter((d) => !plan(d.id)).forEach((d) => { const st = orcaState(d), o = mod(d, "orca");
      todo.push({ t: st.tone, d, what: html`<b>Orca ${st.label}</b> <span>${o && o.version ? o.version + (o.latest && newer(o.latest, o.version) ? " → " + o.latest : "") : o && o.latest ? "→ " + o.latest : ""}</span>`, a: full() ? act("planopen", d.id, st.label === "설치 안 됨" ? "설치 계획" : "계획 보기") : act("open", d.id, "기기 보기") }); });
    if (!full()) devs.filter((d) => reach(d) && mod(d, "doctor") && mod(d, "doctor").health && mod(d, "doctor").health !== "ok").forEach((d) => todo.push({ t: "warn", d, what: html`<b>doctor 발견 있음</b> <span>세부는 허용된 기기(viewers)에서 봅니다</span>`, a: act("open", d.id, "기기 보기") }));
    all.filter((x) => x.f.kind === "secret" || x.f.kind === "hook_drift").forEach(({ d, f }) => todo.push({ t: KIND[f.kind].tone, d, what: html`<b>${KIND[f.kind].label}</b> <span>${fbody(f)}</span>`, a: act("open", d.id, "자세히") }));
    off.forEach((d) => { const st = offline(d); todo.push({ t: st.tone, d, what: html`<b>${st.label}</b> <span>${d.error || ""}</span>`, a: act("open", d.id, "기기 보기") }); });
    const rest = all.length - all.filter((x) => x.f.kind === "secret" || x.f.kind === "hook_drift").length;
    const docBad = devs.filter((d) => reach(d) && mod(d, "doctor") && mod(d, "doctor").health && mod(d, "doctor").health !== "ok").length;
    const kpi = html`<div class="bento su-kpis">
      <div class="tile span-3 m-3 su-kpi" data-tone="${pending().length ? "warn" : "ok"}"><span class="su-k">승인 대기</span><b>${pending().length}</b><span class="su-muted">${full() ? "이 화면에서 연 계획" : "계획은 viewers 에서만"}</span></div>
      <div class="tile span-3 m-3 su-kpi" data-tone="${todoOrca.length ? "info" : "ok"}"><span class="su-k">Orca 할 일</span><b>${todoOrca.length}</b><span class="su-muted">설치·갱신·상태 저하</span></div>
      <div class="tile span-3 m-3 su-kpi" data-tone="${secrets ? "crit" : all.length || (!full() && docBad) ? "warn" : "ok"}"><span class="su-k">${full() ? "doctor 발견" : "doctor 발견 있는 기기"}</span><b>${full() ? all.length : docBad}</b><span class="su-muted">${full() ? "평문 시크릿 " + secrets + "건 포함" : "세부는 viewers 에서"}</span></div>
      <div class="tile span-3 m-3 su-kpi" data-tone="${off.length ? "muted" : "ok"}"><span class="su-k">닿지 않는 기기</span><b>${off.length}</b><span class="su-muted">${off.map(name).join(" · ") || "없음"}</span></div></div>`;
    const liveT = live.length ? html`<div class="tile su-live-t"><header class="tile-head"><h2 class="tile-title">방금 실행한 작업</h2></header>${live.map((d) => { const j = job(d, "orca");
      return html`<div class="su-lj"><div class="su-x-h"><h3>${name(d)} · ${plan(d.id) && plan(d.id).plan ? what(plan(d.id).plan) : "Orca 적용"}</h3>${act("open", d.id, "서랍에서 보기")}</div>${j.state === "running" ? html`<div class="su-prog su-prog-run" aria-hidden="true"><i></i></div><p class="su-hint su-run" role="status">실행 중 · ${ago(j.started_at)} 시작</p>` : html`${result(j)}<div class="su-acts">${act("plan", d.id, "계획 다시 세우기")}${act("dismiss", d.id, "닫기")}</div>`}</div>`; })}</div>` : "";
    const todoT = html`<div class="tile su-todo"><header class="tile-head"><h2 class="tile-title">할 일</h2><span class="badge">${todo.length}</span></header>${todo.length ? html`<ul>${todo.map((t) => html`<li data-tone="${t.t}"><i class="shape" aria-hidden="true"></i><div class="su-tw"><span class="su-th">${name(t.d)}</span><p>${t.what}</p></div>${t.a}</li>`)}</ul>` : html`<p class="su-muted">할 일이 없습니다.</p>`}${rest ? html`<p class="su-muted">그 밖의 doctor 발견 ${rest}건(PATH 중복·미설치 하네스·lock 밖 스킬)은 기기 서랍에서 봅니다.</p>` : ""}</div>`;
    const jobs = devs.filter((d) => d.job).sort((a, b) => jend(b.job) - jend(a.job));
    const hist = html`<div class="tile su-hist"><header class="tile-head"><h2 class="tile-title">최근 결과</h2></header>${jobs.length ? html`<ol>${jobs.map((d) => { const j = d.job, m = j.state === "running" ? { tone: "info", label: "실행 중" } : RESULT[jstat(j)] || { tone: "crit", label: "실패" };
      return html`<li data-tone="${m.tone}"><i class="shape" aria-hidden="true"></i><div><b>${name(d)}</b> · ${j.module} 적용<span>${m.label} · ${ago(jend(j))}</span></div></li>`; })}</ol>` : ""}<p class="su-muted">기기마다 게이트웨이가 기억하는 마지막 적용 작업입니다(게이트웨이를 다시 시작하면 지워집니다).</p></div>`;
    const tiles = html`<div class="sec-head su-sh"><h2>기기</h2><span class="sec-sub">누르면 서랍에서 모듈별 상세를 봅니다</span></div><ul class="su-devs">${devs.map((d) => { const st = orcaState(d), o = mod(d, "orca"), xs = findings(d);
      return html`<li><button type="button" class="tile su-dev" data-act="open" data-id="${d.id}" data-off="${!reach(d)}"><span class="su-dh"><b>${name(d)}</b><span class="su-os">${o && o.details && o.details.kind ? o.details.kind : d.host && d.host !== d.id ? d.id : ""}</span></span><span class="su-dl"><span class="su-k">Orca</span>${reach(d) ? ver(o && o.version) : ""}${pill(st.tone, st.label)}</span><span class="su-dl"><span class="su-k">doctor</span>${reach(d) && mod(d, "doctor") && !mod(d, "doctor").error ? full() ? html`<span data-tone="${worst(xs)}" class="su-dn"><i class="shape" aria-hidden="true"></i>${xs.length ? "발견 " + xs.length : "이상 없음"}</span>` : html`<span data-tone="${mod(d, "doctor").health === "ok" ? "ok" : "warn"}" class="su-dn"><i class="shape" aria-hidden="true"></i>${mod(d, "doctor").health === "ok" ? "이상 없음" : "발견 있음"}</span>` : html`<span class="su-muted">점검 못 함</span>`}</span></button></li>`; })}</ul><p class="su-muted su-legacy">${LEGACY}</p>`;
    const limited = full() ? "" : html`<p class="su-note su-limited" data-tone="info">이 기기는 허용된 기기(viewers)가 아니라 상태만 봅니다. ${VIEWERS_ONLY}</p>`;
    return html`${S.error ? errBox(S.error) : ""}${limited}${kpi}<div class="bento su-cgrid"><div class="span-7 su-col">${approvals()}${liveT}${todoT}</div><div class="span-5 su-col">${hist}</div></div>${tiles}`;
  }
  const sheet = $("#suSheet");
  function drawer() {
    if (!S.drawer || !S.data) return;
    const d = S.data.devices.find((x) => x.id === S.drawer);
    if (!d) return;
    const st = reach(d) ? { tone: "ok", label: "연결됨" } : offline(d);
    P.render($("#suSheetHead"), html`<div class="sht-top">${d.host && d.host !== d.id ? "core 의 기기 이름 " + d.id : "jarvis 게이트웨이"}</div><h2 id="suSheetTitle">${name(d)}</h2><div class="sht-row">${pill(st.tone, st.label)}</div>`);
    P.render($("#suSheetBody"), html`<div><h3 class="grp-title">Orca</h3><div class="grp su-grp">${orca(d)}</div></div><div><h3 class="grp-title">doctor</h3><div class="grp su-grp">${doctor(d)}</div></div><div><h3 class="grp-title">아직 ai-setup 이 관리</h3><div class="grp su-grp"><p class="su-muted">${LEGACY}</p></div></div>`);
  }
  function openDrawer(id) { S.drawer = id; drawer(); if (!sheet.open) sheet.showModal(); }
  sheet.addEventListener("close", () => { S.drawer = null; });
  sheet.addEventListener("click", (e) => { if (e.target === sheet) sheet.close(); });

  // ---------------------------------------------------------------- 그리기(포커스 유지)
  function draw() {
    const a = document.activeElement;
    const sel = a && a.dataset && a.dataset.act ? '[data-act="' + a.dataset.act + '"][data-id="' + (a.dataset.id || "") + '"]' : null;
    const fid = a && a.closest && a.closest("[data-flow]") ? a.closest("[data-flow]").dataset.flow : null;
    const inSheet = !!(a && a.closest && a.closest("#suSheet"));
    P.render($("#suBody"), body());
    drawer();
    const age = $("#suAge");
    age.textContent = S.refreshing ? "모든 기기를 다시 점검하는 중…" : S.data ? ago(S.data.generated_at) + " 모음" : "";
    $("#suRefresh").disabled = S.refreshing;
    if (sel && (!document.activeElement || document.activeElement === document.body)) {
      const scope = inSheet ? sheet : document;
      const t = scope.querySelector(sel) || (fid && scope.querySelector('[data-flow="' + fid + '"]'));
      if (t) t.focus({ preventScroll: true });
    }
  }

  // ---------------------------------------------------------------- API
  async function call(url, opt) {
    const r = await fetch(url, Object.assign({ cache: "no-store" }, opt || {}));
    let b = null;
    try { b = await r.json(); } catch (e) { b = { error: "HTTP " + r.status }; }
    return { code: r.status, body: b || {} };
  }
  let timer = null;
  async function load(force) {
    if (S.loading && !force) { S.again = true; return; } // 진행 중인 읽기가 끝나면 한 번 더 읽는다(실행 직후 등)
    S.loading = true; if (force) S.refreshing = true; draw();
    try {
      const { code, body: b } = await call("/api/devices/modules" + (force ? "?refresh=1" : ""), force ? { headers: { "X-Jarvis-Action": "modules-refresh" } } : null);
      if (code === 404) S.error = "이 jarvis 는 AI 셋업 API 를 제공하지 않습니다. core 기기가 아니거나 jarvis 갱신이 필요합니다.";
      else if (code !== 200 || !Array.isArray(b.devices)) S.error = "기기 목록을 읽지 못했습니다: " + (b.error || "HTTP " + code);
      else { S.data = b; S.error = null; }
    } catch (e) {
      S.error = "기기 목록을 읽지 못했습니다: " + (e.message || e);
    } finally {
      S.loading = false; S.refreshing = false; draw(); schedule();
      if (S.again) { S.again = false; load(false); }
    }
  }
  function schedule() {
    clearTimeout(timer);
    const busy = S.data && S.data.devices.some(running);
    timer = setTimeout(() => load(false), document.hidden ? POLL_IDLE : busy ? POLL_RUN : POLL_IDLE);
  }
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(false); });
  async function makePlan(id) {
    S.plans[id] = { phase: "loading" }; delete S.applyErr[id];
    const d = S.data && S.data.devices.find((x) => x.id === id);
    if (d && d.job) S.dismissed[id] = jend(d.job); // 새 계획을 세우면 지난 결과는 접는다
    draw();
    try {
      const { code, body: b } = await call("/api/devices/" + encodeURIComponent(id) + "/modules/orca/plan");
      if (code === 403) S.denied = b.error || "권한이 없습니다(HTTP 403)";
      if (code !== 200 || b.error || !Array.isArray(b.steps)) S.plans[id] = { phase: "error", error: b.error || "HTTP " + code };
      else S.plans[id] = { phase: "plan", plan: Object.assign({ blocked: [] }, b), at: Date.now() };
    } catch (e) { S.plans[id] = { phase: "error", error: String(e.message || e) }; }
    draw();
  }
  async function apply(id) {
    const p = plan(id);
    if (!p || p.phase !== "plan" || S.denied || S.sending.has(id)) return;
    S.sending.add(id); delete S.applyErr[id]; draw();
    try {
      const { code, body: b } = await call("/api/devices/" + encodeURIComponent(id) + "/modules/orca/apply?plan_id=" + encodeURIComponent(p.plan.plan_id),
        { method: "POST", headers: { "X-Jarvis-Action": "module-apply" } });
      if (code === 202) { p.sent = p.plan.plan_id; }
      else if (code === 403) S.denied = b.error || "권한이 없습니다(HTTP 403)";
      else if (code === 409) S.applyErr[id] = "지금은 적용할 수 없습니다: " + (b.error || "다른 작업이 진행 중입니다");
      else S.applyErr[id] = "실행하지 못했습니다: " + (b.error || "HTTP " + code);
    } catch (e) { S.applyErr[id] = "실행하지 못했습니다: " + (e.message || e); }
    S.sending.delete(id);
    await load(false);
  }

  document.addEventListener("click", (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    if (t.hasAttribute("data-close")) { sheet.close(); return; }
    const a = t.dataset.act, id = t.dataset.id;
    if (t.id === "suRefresh") { load(true); return; }
    if (!a) return;
    ({
      plan: () => makePlan(id),
      apply: () => apply(id),
      drop: () => { delete S.plans[id]; delete S.applyErr[id]; draw(); },
      dismiss: () => { const d = S.data.devices.find((x) => x.id === id); if (d && d.job) S.dismissed[id] = jend(d.job); delete S.plans[id]; draw(); },
      open: () => openDrawer(id),
      planopen: () => { openDrawer(id); makePlan(id); },
    })[a]?.();
  });

  P.subscribe(() => {}, {}); // 헤더(연결 상태·경보)는 공용 스냅샷 폴링으로 갱신된다(shell.js 의 약속)
  load(false);
  setInterval(draw, 30000); // 'n분 전' 표시
})();
