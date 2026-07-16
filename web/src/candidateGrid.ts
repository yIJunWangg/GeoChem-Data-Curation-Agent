import type { BatchPayload, CandidateRecord } from './types'

export const WORKBENCH_STEPS = ['自动资源发现', '原文核校与资源补充', '资源抽取与候选处理', '映射确认与质检'] as const

export function buildGridRows(payload: BatchPayload) {
  return payload.records.map<Record<string, unknown> & { __record: CandidateRecord }>((record) => ({
    __record: record,
    ...Object.fromEntries(payload.headers.map((header) => [header.display_header, record.data[header.display_header] ?? ''])),
  }))
}
