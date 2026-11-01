import { describe, expect, test } from 'claude-code/testing'
import { filterOutput } from './register'

const HUD = 'C:\\work\\proj\\.omc\\state\\hud-stdin-cache.json'
const HUD_SESSION = '/work/proj/.omc/state/sessions/abc-123/hud-stdin-cache.json'
const SRC = '/work/proj/src/app.ts'
const hunk = { oldStart: 1, oldLines: 1, newStart: 1, newLines: 1, lines: ['-a', '+b'] }

function bash(files: string[], moreFiles = 0) {
  return {
    stdout: 'ok', stderr: '', interrupted: false,
    bashEditDiff: { files: files.map((filePath) => ({ filePath, hunks: [hunk] })), moreFiles, changedFiles: files },
  }
}

describe('filterOutput', () => {
  test('drops only the HUD cache and keeps other files', () => {
    const out = filterOutput(bash([HUD, SRC]))
    expect(out.bashEditDiff?.files.map((f) => f.filePath)).toEqual([SRC])
    expect(out.bashEditDiff?.changedFiles).toEqual([SRC])
    expect(out.stdout).toBe('ok')
  })

  test('also drops the per-session path', () => {
    expect(filterOutput(bash([HUD_SESSION, SRC])).bashEditDiff?.files.length).toBe(1)
  })

  test('removes bashEditDiff entirely when only the HUD cache is present', () => {
    expect('bashEditDiff' in filterOutput(bash([HUD]))).toBe(false)
  })

  test('keeps bashEditDiff even with an empty list when truncated files remain', () => {
    const out = filterOutput(bash([HUD], 3))
    expect(out.bashEditDiff?.files).toEqual([])
    expect(out.bashEditDiff?.moreFiles).toBe(3)
  })

  test('subtracts a HUD cache truncated from the list out of the +N more count', () => {
    const input = bash([SRC], 2)
    input.bashEditDiff.changedFiles = [SRC, HUD, '/work/proj/other.ts']
    const out = filterOutput(input)
    expect(out.bashEditDiff?.files.map((f) => f.filePath)).toEqual([SRC])
    expect(out.bashEditDiff?.moreFiles).toBe(1)
    expect(out.bashEditDiff?.changedFiles).toEqual([SRC, '/work/proj/other.ts'])
  })

  test('returns the same object when there is nothing to filter', () => {
    const input = bash([SRC])
    expect(filterOutput(input)).toBe(input)
    const plain = { stdout: '', stderr: '', interrupted: false }
    expect(filterOutput(plain)).toBe(plain)
  })

  test('keeps files that only share the name but live elsewhere', () => {
    const other = '/work/proj/docs/hud-stdin-cache.json'
    expect(filterOutput(bash([other])).bashEditDiff?.files.length).toBe(1)
  })
})

for (const component of ['ToolResult', 'ToolUse'] as const) {
  test(`${component}: filters Bash results before handing them to the engine`, async ($, on) => {
    let seen: unknown
    on('ui.render', { component }, async (_$, e) => {
      seen = e.props.output
      return { type: 'Text' as const }
    })
    const base = { tool_use_id: 't1', tool: 'Bash', output: bash([HUD, SRC]), isErrored: false }
    const props = component === 'ToolUse'
      ? { ...base, input: { command: 'ls' }, isRunning: false, isInterrupted: false }
      : base
    await $.ui.render({ surface: 'terminal', component, requestId: 't1', props } as never)
    expect((seen as ReturnType<typeof bash>).bashEditDiff?.files.map((f) => f.filePath)).toEqual([SRC])
  })

  test(`${component}: passes non-Bash tools through unchanged`, async ($, on) => {
    let seen: unknown
    on('ui.render', { component }, async (_$, e) => {
      seen = e.props.output
      return { type: 'Text' as const }
    })
    const output = bash([HUD])
    const base = { tool_use_id: 't2', tool: 'Read', output, isErrored: false }
    const props = component === 'ToolUse'
      ? { ...base, input: {}, isRunning: false, isInterrupted: false }
      : base
    await $.ui.render({ surface: 'terminal', component, requestId: 't2', props } as never)
    expect(seen).toEqual(output)
  })
}
