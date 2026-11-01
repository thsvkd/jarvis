import { describe, expect, mock, test } from 'claude-code/testing'
import { format, touchesCache } from './register'

const T0 = 1_000_000_000

describe('format', () => {
  test('shows nothing when the cache has never been used', () => {
    expect(format(undefined, T0)).toBe(undefined)
  })
  test('minutes at 1 minute or more, seconds below that, cold once expired', () => {
    expect(format(T0 + 57 * 60_000 + 5_000, T0)).toBe('cache:57m')
    expect(format(T0 + 45_000, T0)).toBe('cache:45s')
    expect(format(T0, T0)).toBe('cache:cold')
    expect(format(0, T0)).toBe('cache:cold')
  })
})

describe('touchesCache', () => {
  test('only responses that read or wrote the cache refresh the TTL', () => {
    expect(touchesCache({ cache_read_input_tokens: 10, cache_creation_input_tokens: 0 })).toBe(true)
    expect(touchesCache({ cache_read_input_tokens: 0, cache_creation_input_tokens: 5 })).toBe(true)
    expect(touchesCache({ cache_read_input_tokens: 0, cache_creation_input_tokens: 0 })).toBe(false)
    expect(touchesCache(null)).toBe(false)
  })
})

function stepResult(e: { turnId: string; index: number }, read: number) {
  return {
    turnId: e.turnId, index: e.index, answer: '', toolUses: [], stopReason: 'end_turn' as const,
    usage: { input_tokens: 1, output_tokens: 1, cache_read_input_tokens: read, cache_creation_input_tokens: 0, model: 'm' },
  }
}

async function runStep($: any, input: object) {
  const stream = $.turn.step(input)
  for await (const _ of stream) { /* 응답 조각은 보지 않는다 */ }
  return stream.result
}

test('a main request reading the cache starts the TTL, and it goes cold once it passes', { options: { ttl: '5m' } }, async ($, on) => {
  const clock = mock.clock(on, { now: T0 })
  const seen: (string | undefined)[] = []
  on('ui.status', async (_$, e) => { seen.push(e.text); return { value: undefined } })
  on('turn.step', async function* (_$, e) { return stepResult(e, 100) })
  on('session.start', async (_$, e) => e as never)

  await $.session.start({ cwd: '/work' } as never)
  await runStep($, { turnId: 't', index: 0, model: 'm', messageCount: 1 })
  expect(seen.at(-1)).toBe('cache:5m')

  await clock.advance(4 * 60_000 + 30_000)
  expect(seen.at(-1)).toBe('cache:30s')

  await clock.advance(31_000)
  expect(seen.at(-1)).toBe('cache:cold')
})

test('ignores subagent requests and requests that did not touch the cache', async ($, on) => {
  mock.clock(on, { now: T0 })
  const seen: (string | undefined)[] = []
  on('ui.status', async (_$, e) => { seen.push(e.text); return { value: undefined } })
  on('turn.step', async function* (_$, e) { return stepResult(e, e.agentId ? 100 : 0) })

  await runStep($, { turnId: 't', index: 0, model: 'm', messageCount: 1, agentId: 'a1' })
  await runStep($, { turnId: 't', index: 1, model: 'm', messageCount: 1 })
  expect(seen).toEqual([])
})

test('switching models goes cold and uses the TTL the engine reports', { options: { ttl: '1h' } }, async ($, on) => {
  mock.clock(on, { now: T0 })
  const seen: (string | undefined)[] = []
  on('ui.status', async (_$, e) => { seen.push(e.text); return { value: undefined } })
  on('turn.step', async function* (_$, e) { return stepResult(e, 100) })
  on('classic.PostModelSwitch', async () => ({}) as never)

  await runStep($, { turnId: 't', index: 0, model: 'm', messageCount: 1 })
  expect(seen.at(-1)).toBe('cache:60m')

  await $.classic.PostModelSwitch({
    from_model: 'a', to_model: 'b', requested_model: null, source: 'command',
    context_tokens: 1, prompt_cache_warm: true, cache_ttl: '5m', estimated_cache_write_usd: 0, pricing: 'catalog',
  } as never)
  expect(seen.at(-1)).toBe('cache:cold')

  await runStep($, { turnId: 't', index: 1, model: 'b', messageCount: 2 })
  expect(seen.at(-1)).toBe('cache:5m')
})

test('the countdown runs from the first request even when loaded without session.start (reload)', { options: { ttl: '5m' } }, async ($, on) => {
  const clock = mock.clock(on, { now: T0 })
  const seen: (string | undefined)[] = []
  on('ui.status', async (_$, e) => { seen.push(e.text); return { value: undefined } })
  on('turn.step', async function* (_$, e) { return stepResult(e, 100) })

  await runStep($, { turnId: 't', index: 0, model: 'm', messageCount: 1 })
  // 예전 글자를 먼저 지우고 새 값을 그린다
  expect(seen).toEqual([undefined, 'cache:5m'])
  await clock.advance(4 * 60_000 + 30_000)
  expect(seen.at(-1)).toBe('cache:30s')
})
