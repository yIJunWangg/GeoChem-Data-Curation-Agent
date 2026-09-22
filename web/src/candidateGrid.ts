import type { BatchPayload, CandidateRecord } from './types'

export const WORKBENCH_STEPS = ['资源发现与原文校核', '资源抽取与候选处理', '映射确认与质检'] as const

export type WorkbenchView = 'resources' | 'extract' | 'quality'

export function resolveWorkbenchStep(view: string | null, legacyStep: string | null) {
  if (view === 'resources') return 0
  if (view === 'extract' || view === 'rules') return 1
  if (view === 'quality') return 2

  const numericStep = legacyStep === null ? Number.NaN : Number(legacyStep)
  if (!Number.isInteger(numericStep)) return 0
  if (numericStep <= 1) return 0
  if (numericStep === 2) return 1
  return 2
}

export function buildGridRows(payload: BatchPayload) {
  return payload.records.map<Record<string, unknown> & { __record: CandidateRecord }>((record) => ({
    __record: record,
    ...Object.fromEntries(payload.headers.map((header) => [header.display_header, record.data[header.display_header] ?? ''])),
  }))
}
