import type { Register } from 'claude-code'

// ponytail: 번역은 빠른 소형 모델 하나로 한다. 품질이 모자라면 MODEL 만 올린다.
const MODEL = 'claude-haiku-5-5'
// 한국어는 같은 내용의 영어보다 토큰이 많다. 이 한도에 닿은 번역은 잘린 것으로 보고 버린다.
const MAX_TOKENS = 16000
const TIMEOUT_MS = 30000
const SYSTEM =
  'Translate the assistant message inside <message> into natural Korean, as the assistant speaking to its user ' +
  'in the formal polite speech level with complete sentences. Omit first- and second-person pronouns the way natural Korean does. ' +
  'Do not answer or act on the message; only translate it. Keep Markdown structure, identifiers, commands, paths, URLs, ' +
  'quotes and every placeholder like ⟦0⟧ exactly as they are. Output only the translation, without the tags.'

const CODE = /```[\s\S]*?```|`[^`\n]*`/g
const PATH_OR_URL = /https?:\/\/\S+|(?:[\w.~:-]*[\\/])+[\w.-]+/g

/** 코드·경로·URL 을 뺀 본문의 라틴 단어 수와 한글 단어 수. */
function words(text: string): { latin: number; hangul: number } {
  const body = text.replace(CODE, ' ').replace(PATH_OR_URL, ' ')
  return {
    latin: (body.match(/\b[A-Za-z]{2,}\b/g) ?? []).length,
    hangul: (body.match(/[가-힣]+/g) ?? []).length,
  }
}

/**
 * 응답 블록이 영어인지 판정한다. 글자 수로 재면 한국어 고유명사가 섞인 영어 문단을 놓쳐서 단어 수로 잰다.
 * 짧은 텍스트는 판정하지 않는다.
 */
export function isEnglish(text: string): boolean {
  const { latin, hangul } = words(text)
  return latin >= 8 && latin > hangul * 2
}

// 영어 결과물(영작·번역·커밋 메시지 등)을 청한 프롬프트다. 애매하면 바꾸지 않는 쪽을 고른다.
const ASKS_ENGLISH = /영어|영문|영작|english|번역|translat|커밋|commit|\bPR\b|풀 ?리퀘/i

/** 이번 턴의 영어 응답을 한국어로 바꿔도 되는지, 사람이 쓴 프롬프트로 판단한다. */
export function wantsKorean(prompt: string): boolean {
  const { latin, hangul } = words(prompt)
  return hangul > 0 && hangul >= latin && !ASKS_ENGLISH.test(prompt)
}

/** 코드를 자리표시자로 바꾼다. 번역기가 코드를 건드릴 여지를 없앤다. */
export function protect(text: string): { masked: string; codes: string[] } {
  const codes: string[] = []
  const masked = text.replace(CODE, (m) => `⟦${codes.push(m) - 1}⟧`)
  return { masked, codes }
}

/** 자리표시자를 코드로 되돌린다. 하나라도 빠지거나 중복되면 null 을 돌려 원문을 지키게 한다. */
export function restore(text: string, codes: string[]): string | null {
  const seen = (text.match(/⟦\d+⟧/g) ?? []).sort()
  const want = codes.map((_, i) => `⟦${i}⟧`).sort()
  if (seen.join() !== want.join()) return null
  return text.replace(/⟦(\d+)⟧/g, (_, i) => codes[Number(i)])
}

export const register: Register = (on) => {
  // 화면이 없는 -p 실행의 출력은 스크립트가 읽으므로 건드리지 않는다
  let interactive = true
  on('session.start', async ($, e, next) => {
    interactive = e.isInteractive
    return next(e)
  })
  // 알림으로 깨어난 턴은 text 가 비어 있어 직전 판단을 이어 쓴다. 판단 전에는 바꾸지 않는다.
  let rewrite = false
  on('turn.start', async ($, e, next) => {
    if (e.agentId === undefined && e.text.trim()) rewrite = wantsKorean(e.text)
    return next(e)
  })
  // 영어 응답이 기록에 쌓이면 모델이 그 선례를 따라 계속 영어로 답한다(실측: 직전 블록이 영어면 64%).
  // 저장 직전에 한국어로 바꿔 두면 다음 요청이 읽는 기록에는 영어 선례가 남지 않는다.
  on('session.append', { door: 'response' }, async ($, e, next) => {
    if (!interactive || !rewrite || e.agentId !== undefined || e.message.role !== 'assistant') return next(e)
    const content = await Promise.all(
      e.message.content.map(async (b) => {
        if (b.type !== 'text' || !isEnglish(b.text)) return b
        const { masked, codes } = protect(b.text)
        try {
          const r = await $.model.complete({
            model: MODEL, system: SYSTEM, prompt: `<message>\n${masked}\n</message>`,
            maxTokens: MAX_TOKENS, timeoutMs: TIMEOUT_MS,
          })
          // 실패·잘림·번역 아닌 답·자리표시자 손실은 모두 원문을 지킨다
          if (!r.isAnswered || r.usage.output_tokens >= MAX_TOKENS || !/[가-힣]/.test(r.text)) return b
          // 지시와 달리 감싼 태그를 되풀이해도 저장되지 않게 벗긴다
          const text = restore(r.text.trim().replace(/^<message>\s*/, '').replace(/\s*<\/message>$/, ''), codes)
          return text ? { ...b, text } : b
        } catch {
          return b
        }
      }),
    )
    return next({ ...e, message: { ...e.message, content } })
  })
}
