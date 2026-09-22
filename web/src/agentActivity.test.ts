import { describe, expect, it } from 'vitest'
import { foldActivityEvents } from './agentActivity'
import type { AgentActivityEvent } from './types'

describe('foldActivityEvents', () => {
  it('updates a tool row in place from running to completed', () => {
    const events: AgentActivityEvent[] = [
      {
        event_id: 11,
        tool_call_id: 'ATC_1',
        event_type: 'tool',
        tool_name: 'discover_article_resources',
        label: '正在发现 PDF 资源',
        status: 'running',
        created_at: '2026-08-03T10:00:00',
        safe_input_summary: { article_id: 'ART_1' },
      },
      {
        event_id: 12,
        tool_call_id: 'ATC_1',
        event_type: 'tool',
        tool_name: 'discover_article_resources',
        label: '正在发现 PDF 资源',
        status: 'completed',
        created_at: '2026-08-03T10:00:01',
        safe_output_summary: { elements: 28 },
        duration_ms: 1250,
      },
    ]

    expect(foldActivityEvents(events)).toEqual([expect.objectContaining({
      event_id: 11,
      tool_call_id: 'ATC_1',
      status: 'completed',
      safe_input_summary: { article_id: 'ART_1' },
      safe_output_summary: { elements: 28 },
      duration_ms: 1250,
      created_at: '2026-08-03T10:00:00',
    })])
  })

  it('does not count ordinary workflow status events as tools', () => {
    const events: AgentActivityEvent[] = [
      { event_id: 1, event_type: 'status', label: '等待用户确认', status: 'waiting' },
      { event_id: 2, tool_call_id: 'ATC_2', event_type: 'tool', tool_name: 'sync_article_retrieval', status: 'completed' },
    ]

    const folded = foldActivityEvents(events)
    expect(folded).toHaveLength(2)
    expect(folded.filter((event) => event.event_type === 'tool')).toHaveLength(1)
  })
})
