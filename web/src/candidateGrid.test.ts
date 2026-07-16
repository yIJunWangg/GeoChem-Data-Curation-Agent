import { describe, expect, it } from 'vitest'
import { buildGridRows, WORKBENCH_STEPS } from './candidateGrid'
import type { BatchPayload } from './types'

describe('candidate grid', () => {
  it('keeps the required workbench order', () => {
    expect(WORKBENCH_STEPS).toEqual(['自动资源发现', '原文核校与资源补充', '资源抽取与候选处理', '映射确认与质检'])
  })

  it('keeps exact target headers and fills missing cells with empty strings', () => {
    const payload = {
      batch: { batch_id: 'BAT_1', article_id: 'ART_1', status: 'completed' },
      headers: [
        { header_id: 'H:0', display_header: 'SampleID', canonical_field: 'SampleID', target_unit: '', description: '', order: 0 },
        { header_id: 'H:1', display_header: 'Li ppm', canonical_field: 'Li', target_unit: 'ppm', description: '', order: 1 },
        { header_id: 'H:2', display_header: 'Al2O3(wt%)', canonical_field: 'Al2O3', target_unit: 'wt%', description: '', order: 2 },
      ],
      records: [{ candidate_record_id: 'R1', sample_id: 'XM-01', merge_status: 'matched', quality_grade: 'D', data: { SampleID: 'XM-01', 'Li ppm': '42.1' }, cells: {} }],
    } satisfies BatchPayload

    const rows = buildGridRows(payload)

    expect(Object.keys(rows[0]).slice(1)).toEqual(['SampleID', 'Li ppm', 'Al2O3(wt%)'])
    expect(rows[0]['Al2O3(wt%)']).toBe('')
  })
})
