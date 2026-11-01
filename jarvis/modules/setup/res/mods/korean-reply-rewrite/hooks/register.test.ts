import { describe, expect, mock, test } from 'claude-code/testing'
import { isEnglish, protect, restore, wantsKorean } from './register'

describe('isEnglish', () => {
  test('flags an English reply', () => {
    expect(isEnglish('The workflow is running in the background, and nothing has come back yet.')).toBe(true)
  })
  test('keeps a Korean reply that names code and paths', () => {
    expect(isEnglish('`tool.call` hook 에서 `next(e)` 결과를 받아 `res/mods/apply.py` 의 context 에 덧붙이면 됩니다.')).toBe(false)
  })
  test('flags English prose that quotes Korean names', () => {
    expect(isEnglish('All 8 mockups are built, but only 5 finished the independent check: 판단일지형, 시험대형, 단계승격형 and 질문형 passed it.')).toBe(true)
  })
  test('keeps Korean prose dense with English terms', () => {
    expect(isEnglish('mod 로 매 도구 결과 뒤에 context 를 덧붙이려면 tool call hook 에서 next 결과를 받아 반환하면 됩니다.')).toBe(false)
  })
  test('does not count Windows path segments as words', () => {
    expect(isEnglish('C:\\Users\\pi\\AppData\\Local\\Programs\\Microsoft VS Code\\resources\\app 경로를 확인했습니다.')).toBe(false)
  })
  test('ignores text too short to judge', () => {
    expect(isEnglish('Done.')).toBe(false)
  })
})

describe('wantsKorean', () => {
  test('rewrites after a Korean prompt', () => {
    expect(wantsKorean('지금까지 진행 상황을 정리해 줘')).toBe(true)
  })
  for (const prompt of [
    '이 메일을 영어로 다듬어 줘',
    '영어 메일 초안 써줘',
    '이 문장 영작해 줘',
    '영문 README 작성해줘',
    'English로 답해줘',
    '이 문단 번역해줘',
    '커밋 메시지 초안 써 줘',
  ]) {
    test(`keeps English the person asked for: ${prompt}`, () => {
      expect(wantsKorean(prompt)).toBe(false)
    })
  }
  test('keeps the reply after an English prompt with pasted Korean', () => {
    expect(wantsKorean('Fix this error, the app prints it on startup: 파일을 찾을 수 없습니다')).toBe(false)
  })
  test('keeps the reply after a prompt in another language', () => {
    expect(wantsKorean('この関数が何をするか説明してください')).toBe(false)
  })
})

describe('protect and restore', () => {
  const text = 'Run `npm test` first.\n```sh\nnpm ci\n```\nThen commit.'
  test('round-trips code through placeholders', () => {
    const { masked, codes } = protect(text)
    expect(masked).not.toContain('npm')
    expect(restore(masked, codes)).toBe(text)
  })
  test('refuses a translation that lost a placeholder', () => {
    const { codes } = protect(text)
    expect(restore('먼저 \u27e60\u27e7 를 실행합니다.', codes)).toBeNull()
  })
})

const EN = 'The workflow is running in the background, and nothing has come back yet.'
const KO = '워크플로가 백그라운드에서 실행 중이며, 아직 돌아온 결과가 없습니다.'
const USAGE = { input_tokens: 1, cache_creation_input_tokens: 0, cache_read_input_tokens: 0 }

function reply(text = EN, agentId?: string) {
  return {
    message: { type: 'assistant', role: 'assistant', content: [{ type: 'text', text }] },
    door: 'response', origin: { kind: 'model', model: 'claude-opus-5-5' }, uuid: 'u1',
    ...(agentId ? { agentId } : {}),
  } as never
}

type T = Parameters<typeof test>[1]
type Engine = Parameters<Extract<T, (...a: never[]) => unknown>>[0]
type On = Parameters<Extract<T, (...a: never[]) => unknown>>[1]

/** 프롬프트로 턴을 열고 응답 한 행을 저장한 뒤, 저장된 첫 텍스트 블록을 돌려준다. */
async function run($: Engine, on: On, prompts: string[], answer: { text: string; outputTokens?: number; isAnswered?: boolean }, row = reply(), isInteractive?: boolean) {
  const session = mock.session(on)
  on('session.start', async (_$, e) => ({ cwd: e.cwd }) as never)
  on('turn.start', async (_$, e) => ({ turnId: e.turnId }) as never)
  on('model.complete', async () => ({
    value: answer.isAnswered === false
      ? { isAnswered: false, reason: 'empty-reply', usage: { ...USAGE, output_tokens: 0 } }
      : { isAnswered: true, text: answer.text, usage: { ...USAGE, output_tokens: answer.outputTokens ?? 10 } },
  }) as never)
  if (isInteractive !== undefined) await $.session.start({ isInteractive, cwd: '/work' } as never)
  for (const [i, text] of prompts.entries()) await $.turn.start({ text, turnId: `t${i}` } as never)
  await $.session.append(row)
  return (session.appended().at(-1)?.message.content[0] as { text: string }).text
}

test('stores an English reply in Korean after a Korean prompt', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: KO })).toBe(KO)
})

test('keeps the reply before any prompt was seen', async ($, on) => {
  expect(await run($, on, [], { text: KO })).toBe(EN)
})

test('keeps the reply after an English prompt', async ($, on) => {
  expect(await run($, on, ['What is the status?'], { text: KO })).toBe(EN)
})

test('a turn with no prompt text keeps the previous decision', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘', ''], { text: KO })).toBe(KO)
})

test('keeps the original when translation fails', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: '', isAnswered: false })).toBe(EN)
})

test('keeps the original when the translation hit the token cap', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: KO, outputTokens: 16000 })).toBe(EN)
})

test('keeps the original when the answer is not Korean', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: 'Sure, I can help with that migration right now.' })).toBe(EN)
})

test('puts code back unchanged after translation', async ($, on) => {
  const en = 'Run `npm test` before you push, because the guard hook checks the snapshot refs first.'
  const ko = '푸시하기 전에 \u27e60\u27e7 를 실행하십시오. 가드 훅이 먼저 스냅숏 참조를 확인하기 때문입니다.'
  expect(await run($, on, ['다음에 뭘 해야 해?'], { text: ko }, reply(en))).toBe(ko.replace('\u27e60\u27e7', '`npm test`'))
})

test('leaves a subagent reply alone', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: KO }, reply(EN, 'agent-1'))).toBe(EN)
})

test('passes non-text blocks through', async ($, on) => {
  const session = mock.session(on)
  on('turn.start', async (_$, e) => ({ turnId: e.turnId }) as never)
  on('model.complete', async () => ({ value: { isAnswered: true, text: KO, usage: { ...USAGE, output_tokens: 10 } } }) as never)
  await $.turn.start({ text: '진행 상황 알려 줘', turnId: 't0' } as never)
  const tool = { type: 'tool_use', id: 'toolu_1', name: 'Read', input: { file_path: '/work/a.ts' } }
  await $.session.append({
    message: { type: 'assistant', role: 'assistant', content: [{ type: 'text', text: EN }, tool] },
    door: 'response', origin: { kind: 'model', model: 'claude-opus-5-5' }, uuid: 'u2',
  } as never)
  const content = session.appended().at(-1)?.message.content as { type: string }[]
  // 엔진은 바뀐 응답 텍스트에 citations: null 을 붙여 저장한다
  expect(content[0]).toEqual({ type: 'text', text: KO, citations: null })
  expect(content[1]).toEqual(tool)
})

test('strips the message tags if the translator echoes them', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: `<message>\n${KO}\n</message>` })).toBe(KO)
})

test('leaves replies alone in a non-interactive run', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: KO }, reply(), false)).toBe(EN)
})

test('rewrites in an interactive session', async ($, on) => {
  expect(await run($, on, ['진행 상황 알려 줘'], { text: KO }, reply(), true)).toBe(KO)
})
