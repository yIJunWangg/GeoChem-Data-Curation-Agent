import type { BatchPayload, CandidateRecord } from './types'

export const WORKBENCH_STEPS = ['自动资源发现', '原文校核与补充', '表头数据抽取', '映射与确认'] as const

export function buildGridRows(payload: BatchPayload) {
  return payload.records.map<Record<string, unknown> & { __record: CandidateRecord }>((record) => ({
    __record: record,
    ...Object.fromEntries(payload.headers.map((header) => [header.display_header, record.data[header.display_header] ?? ''])),
  }))
}
