import { afterEach, describe, expect, it, vi } from 'vitest'
import { api } from './api'

afterEach(() => {
  vi.restoreAllMocks()
})

describe('api client', () => {
  it('uses workspace trace-record endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0 }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api.traceRecords('WEB_TEST', 'ART_WEB', 'Th ppm')

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/trace-records?project_id=WEB_TEST&article_id=ART_WEB&q=Th+ppm', undefined)
  })

  it('surfaces JSON detail from failed responses', async () => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: '目标字段不存在' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await expect(api.traceRecord('WEB_TEST', 'STD_404')).rejects.toThrow('目标字段不存在')
  })
})
