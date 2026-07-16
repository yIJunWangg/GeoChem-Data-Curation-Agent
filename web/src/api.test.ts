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

  it('uses simplified model-setup endpoints', async () => {
    const makeResponse = () => new Response(JSON.stringify({ success: true }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(makeResponse())
      .mockResolvedValueOnce(makeResponse())

    await api.testModelSetup({ provider_preset: 'opencode-go', model_id: 'kimi-k2.7-code', api_key: 'sk-test' })
    await api.saveModelSetup({ provider_preset: 'opencode-go', model_id: 'kimi-k2.7-code', api_key: 'sk-test' })

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/model-setup/test', expect.objectContaining({ method: 'POST' }))
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/model-setup', expect.objectContaining({ method: 'PUT' }))
  })

  it('allows manual candidate records without SampleID', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ candidate_record_id: 'REC_001' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api.manualRecord({ project_id: 'WEB_TEST', article_id: 'ART_1', batch_id: 'BAT_1', sample_id: '' })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/candidate-records/manual', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ project_id: 'WEB_TEST', article_id: 'ART_1', batch_id: 'BAT_1', sample_id: '' }),
    }))
  })

  it('sends explicitly confirmed chat actions through the guarded action endpoint', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ status: 'completed' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )

    await api.chatAction('WEB_TEST', 'CHAT_1', 'execute_confirmed', 'RUN_1', {
      operation: 'finalize_standardized',
    })

    expect(fetchMock).toHaveBeenCalledWith('/api/v1/chat/threads/CHAT_1/actions', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({
        project_id: 'WEB_TEST',
        action: 'execute_confirmed',
        run_id: 'RUN_1',
        payload: { operation: 'finalize_standardized' },
      }),
    }))
  })
})
