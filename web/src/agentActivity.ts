import type { AgentActivityEvent } from './types'

export function foldActivityEvents(events: AgentActivityEvent[]): AgentActivityEvent[] {
  const output: AgentActivityEvent[] = []
  const toolIndexes = new Map<string, number>()
  events.forEach((event) => {
    const callId = event.tool_call_id || ''
    if (event.event_type !== 'tool' || !callId) {
      output.push(event)
      return
    }
    const currentIndex = toolIndexes.get(callId)
    if (currentIndex === undefined) {
      toolIndexes.set(callId, output.length)
      output.push(event)
      return
    }
    const current = output[currentIndex]
    output[currentIndex] = {
      ...current,
      ...event,
      event_id: current.event_id,
      tool_call_id: callId,
      created_at: current.created_at || event.created_at,
      safe_input_summary: current.safe_input_summary || event.safe_input_summary,
      safe_output_summary: event.safe_output_summary || current.safe_output_summary,
      duration_ms: event.duration_ms || current.duration_ms,
      error: event.error || current.error,
    }
  })
  return output
}
