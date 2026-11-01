import type { EngineInterface, Register } from 'claude-code'

// 메인 대화의 모델 요청이 캐시를 읽거나 쓰면 그 순간부터 TTL 동안 캐시가 살아 있다.
// mod API 는 statusLine 의 prompt_cache.expires_at 을 주지 않으므로, 요청을 보낸 시각과
// TTL 로 만료 시각을 계산한다. TTL 은 설정값으로 시작하고, 모델을 바꿀 때 엔진이
// 알려 주는 cache_ttl 로 갱신한다.
const TTL_MS: Record<string, number> = { '1h': 3_600_000, '5m': 300_000 }

type Usage = { cache_read_input_tokens: number; cache_creation_input_tokens: number } | null | undefined

/** 응답이 캐시를 건드렸는가 — 읽기든 쓰기든 그 시점부터 TTL 이 다시 흐른다. */
export function touchesCache(usage: Usage): boolean {
  return !!usage && (usage.cache_read_input_tokens > 0 || usage.cache_creation_input_tokens > 0)
}

/** omc-hud-ttl.mjs 와 같은 표기: 1분 이상은 분, 미만은 초, 지났으면 cold. */
export function format(expiresAt: number | undefined, now: number): string | undefined {
  if (expiresAt === undefined) return undefined
  const left = Math.floor((expiresAt - now) / 1000)
  if (left <= 0) return 'cache:cold'
  return `cache:${left >= 60 ? `${Math.floor(left / 60)}m` : `${left}s`}`
}

// 모듈을 다시 불러오면(reload) 처음부터 — 다음 요청이 다시 채운다.
let ttlMs = TTL_MS['1h']
let expiresAt: number | undefined
let shown: string | undefined
let ticking = false

async function draw($: EngineInterface) {
  const text = format(expiresAt, await $.clock.now())
  if (text !== shown) {
    shown = text
    $.ui.status(text)
  }
}

/**
 * 1초마다 다시 계산하는 타이머를 처음 필요할 때 건다. reload 는 예전 타이머를 걷어 가지만
 * session.start 를 다시 부르지 않는다(설정에서 ttl 을 바꿔도 reload 다). 그래서 session.start
 * 에만 기대지 않고, 표시할 일이 생길 때마다 확인한다. 1분 이상은 분 단위지만 마지막 1분은
 * 초로 보여 주므로 1초 간격이고, 글자가 바뀔 때만 다시 그린다.
 */
function startTicking($: EngineInterface) {
  if (ticking) return
  ticking = true
  // reload 전에 그려 둔 글자가 남아 있으면 지운다 — 이 모듈은 아직 아무것도 모른다.
  $.ui.status(undefined)
  $.clock.every(1000, () => draw($))
}

/** 표시는 덤이다. 실패해도 모델 요청이나 모델 전환을 방해하지 않는다. */
async function refresh($: EngineInterface) {
  try {
    startTicking($)
    await draw($)
  } catch {
    // 다음 틱이 다시 그린다.
  }
}

export const register: Register = (on, options) => {
  ttlMs = TTL_MS[String(options.ttl)] ?? TTL_MS['1h']

  on('session.start', async ($, e, next) => {
    startTicking($)
    return next(e)
  })

  on('turn.step', async function* ($, e, next) {
    // 서브에이전트 요청은 메인 대화의 캐시를 갱신하지 않는다.
    if (e.agentId) return yield* next(e)
    const sentAt = await $.clock.now()
    const result = yield* next(e)
    if (touchesCache(result?.usage)) {
      expiresAt = sentAt + ttlMs
      await refresh($)
    }
    return result
  })

  on('classic.PostModelSwitch', async ($, e, next) => {
    ttlMs = TTL_MS[e.cache_ttl] ?? ttlMs
    // 모델을 바꾸면 새 모델의 캐시는 비어 있다. 다음 요청이 다시 채운다.
    if (expiresAt !== undefined) expiresAt = 0
    await refresh($)
    return next(e)
  }).catch(($, e, next) => (next.called ? undefined : next(e)))
}
