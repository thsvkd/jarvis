import type { Register } from 'claude-code'

// OMC HUD 는 statusLine 이 그려질 때마다(refreshInterval 마다) 이 파일을 프로젝트 안에 다시 쓴다.
// 그래서 Bash 가 실행되는 동안 거의 항상 '바뀐 파일' 로 잡힌다. 레거시 평면 경로와
// 세션별 경로(state/sessions/<id>/) 둘 다 해당한다.
const NOISE = /[\\/]\.omc[\\/]state[\\/](?:sessions[\\/][^\\/]+[\\/])?hud-stdin-cache\.json$/

type EditDiff = { files: { filePath: string }[]; moreFiles: number; changedFiles?: string[] }

/**
 * Bash 결과 레코드의 bashEditDiff 에서 잡음 파일을 뺀다. 바뀐 게 없으면 같은 객체를 돌려준다.
 * bashEditDiff 는 화면과 PostToolUse 훅용이고 모델에는 가지 않는다 — 그래서 그리기만 고친다.
 */
export function filterOutput<T>(output: T): T {
  const diff = (output as { bashEditDiff?: EditDiff } | null | undefined)?.bashEditDiff
  if (!diff || !Array.isArray(diff.files)) return output
  const files = diff.files.filter((f) => !NOISE.test(f.filePath))
  // 목록에서 잘려 '+N more' 숫자에만 들어간 잡음도 그 숫자에서 뺀다.
  const shown = new Set(diff.files.map((f) => f.filePath))
  const hidden = (diff.changedFiles ?? []).filter((p) => NOISE.test(p) && !shown.has(p)).length
  const moreFiles = Math.max(0, (diff.moreFiles ?? 0) - hidden)
  if (files.length === diff.files.length && hidden === 0) return output
  const rest = { ...(output as object) } as { bashEditDiff?: EditDiff }
  if (files.length === 0 && moreFiles === 0) {
    delete rest.bashEditDiff
  } else {
    rest.bashEditDiff = {
      ...diff,
      files,
      moreFiles,
      ...(diff.changedFiles ? { changedFiles: diff.changedFiles.filter((p) => !NOISE.test(p)) } : {}),
    }
  }
  return rest as T
}

export const register: Register = (on) => {
  // 단독 도구 행 아래의 결과 블록
  on('ui.render', { component: 'ToolResult' }, async ($, e, next) => {
    if (e.props.tool !== 'Bash') return next(e)
    const output = filterOutput(e.props.output)
    return output === e.props.output ? next(e) : next({ ...e, props: { ...e.props, output } })
  })
  // 펼친 도구 그룹 안의 행은 결과를 ToolUse.output 으로 직접 그린다
  on('ui.render', { component: 'ToolUse' }, async ($, e, next) => {
    if (e.props.tool !== 'Bash') return next(e)
    const output = filterOutput(e.props.output)
    return output === e.props.output ? next(e) : next({ ...e, props: { ...e.props, output } })
  })
}
