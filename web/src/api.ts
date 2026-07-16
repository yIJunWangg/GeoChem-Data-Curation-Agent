import type { AgentRun, Article, BatchPayload, ChatCitation, ChatThread, ChatThreadState, DocumentElement, HeaderConfig, HeaderField, ParagraphCue, Resource, TableRulePreflight, TraceRecordDetail, TraceRecordSummary, WorkflowEvent, Workspace } from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const body = await response.text()
    let detail = body
    try {
      const parsed = JSON.parse(body)
      detail = typeof parsed.detail === 'string' ? parsed.detail : body
    } catch {
      detail = body
    }
    const error = new Error(detail || `${response.status} ${response.statusText}`) as Error & { status: number }
    error.status = response.status
    throw error
  }
  return response.json() as Promise<T>
}

export const api = {
  chatThreads: (projectId: string, articleId = '') => {
    const params = new URLSearchParams({ project_id: projectId })
    if (articleId) params.set('article_id', articleId)
    return request<ChatThread[]>(`/api/v1/chat/threads?${params.toString()}`)
  },
  createChatThread: (projectId: string, articleId = '', title = '', scope = 'article') =>
    request<ChatThread>('/api/v1/chat/threads', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, title, scope }) }),
  chatThread: (projectId: string, threadId: string) =>
    request<ChatThread>(`/api/v1/chat/threads/${threadId}?project_id=${encodeURIComponent(projectId)}`),
  chatThreadState: (projectId: string, threadId: string) =>
    request<ChatThreadState>(`/api/v1/chat/threads/${threadId}/state?project_id=${encodeURIComponent(projectId)}`),
  deleteChatThread: (projectId: string, threadId: string, cancelWaiting = false) =>
    request<{thread_id:string;status:string}>(`/api/v1/chat/threads/${threadId}?project_id=${encodeURIComponent(projectId)}&cancel_waiting=${String(cancelWaiting)}`, { method: 'DELETE' }),
  chatMessage: (projectId: string, threadId: string, content: string, articleId = '') =>
    request<{run_id: string; status: string; thread_id?: string; message?: string}>(`/api/v1/chat/threads/${threadId}/messages`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, content }) }),
  chatAction: (projectId: string, threadId: string, action: string, runId = '', payload: Record<string, unknown> = {}) =>
    request<Record<string, unknown>>(`/api/v1/chat/threads/${threadId}/actions`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, action, run_id: runId, payload }) }),
  agentEntities: (projectId: string, type = '', query = '', contextId = '') => {
    const params = new URLSearchParams({ project_id: projectId })
    if (type) params.set('type', type)
    if (query) params.set('q', query)
    if (contextId) params.set('context_id', contextId)
    return request<Record<string, unknown>[]>(`/api/v1/agent/entities?${params.toString()}`)
  },
  selectChatEntity: (projectId: string, threadId: string, entityType: string, entityId = '', query = '') =>
    request<{selection: Record<string, unknown>; article_id: string; thread: ChatThread}>(`/api/v1/chat/threads/${threadId}/select`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, entity_type: entityType, entity_id: entityId, query }) }),
  searchLiterature: (projectId: string, threadId: string, query: string, limit = 8, sortMode = 'relevance') =>
    request<{query:string;search_id:string;results:Record<string, unknown>[];providers:string[];errors:string[]}>(`/api/v1/literature/search`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, thread_id: threadId, query, limit, sort_mode: sortMode }) }),
  importLiteratureResult: (projectId: string, threadId: string, resultId: string) =>
    request<Record<string, unknown>>(`/api/v1/literature/results/${resultId}/import`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, thread_id: threadId }) }),
  agentRun: (projectId: string, runId: string) =>
    request<AgentRun>(`/api/v1/agent-runs/${runId}?project_id=${encodeURIComponent(projectId)}`),
  resumeAgentRun: (projectId: string, runId: string, response: Record<string, unknown>) =>
    request<{run_id: string; status: string; message?: string; configuration_required?: boolean}>(`/api/v1/agent-runs/${runId}/resume`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, response }) }),
  cancelAgentRun: (projectId: string, runId: string) =>
    request<AgentRun>(`/api/v1/agent-runs/${runId}/cancel?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  chatCitations: (projectId: string, messageId: string) =>
    request<ChatCitation[]>(`/api/v1/chat/messages/${messageId}/citations?project_id=${encodeURIComponent(projectId)}`),
  ragStatus: (projectId: string, articleId = '') => {
    const params = new URLSearchParams({ project_id: projectId })
    if (articleId) params.set('article_id', articleId)
    return request<{ready:boolean;counts:Record<string,number>}>(`/api/v1/rag/status?${params.toString()}`)
  },
  ragQuery: (projectId: string, articleId: string, question: string, mode = 'answer', field = '', operation = 'summary') =>
    request<Record<string, unknown>>('/api/v1/rag/query', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, question, mode, field, operation }) }),
  workspace: () => request<Workspace>('/api/v1/workspace'),
  dashboard: (projectId: string) => request<Record<string, number>>(`/api/v1/dashboard?project_id=${encodeURIComponent(projectId)}`),
  articles: (projectId: string) => request<Article[]>(`/api/v1/articles?project_id=${encodeURIComponent(projectId)}`),
  headers: (projectId: string) => request<HeaderConfig[]>(`/api/v1/header-configs?project_id=${encodeURIComponent(projectId)}`),
  importHeaders: (projectId: string, file: File, name = '') => {
    const data = new FormData(); data.append('file', file)
    return request<Record<string, unknown>>(`/api/v1/header-configs/import?project_id=${encodeURIComponent(projectId)}&name=${encodeURIComponent(name || file.name.replace(/\.[^.]+$/, ''))}`, { method: 'POST', body: data })
  },
  session: (projectId: string, articleId: string) => request<Record<string, string>>(`/api/v1/articles/${articleId}/session?project_id=${encodeURIComponent(projectId)}`),
  resources: (projectId: string, articleId: string) => request<Resource[]>(`/api/v1/articles/${articleId}/resources?project_id=${encodeURIComponent(projectId)}`),
  elements: (projectId: string, articleId: string) => request<DocumentElement[]>(`/api/v1/articles/${articleId}/elements?project_id=${encodeURIComponent(projectId)}`),
  discover: (projectId: string, articleId: string) => request<{task_id: string}>(`/api/v1/articles/${articleId}/discover?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  rediscoverWithRules: (projectId: string, articleId: string) => request<{task_id: string}>(`/api/v1/articles/${articleId}/rediscover-with-rules?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  preExtractionRules: (projectId: string, articleId: string) => request<Record<string, unknown>[]>(`/api/v1/articles/${articleId}/pre-extraction-rules?project_id=${encodeURIComponent(projectId)}`),
  addPreExtractionRule: (projectId: string, articleId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/articles/${articleId}/pre-extraction-rules?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  tableRulePreflight: (projectId: string, articleId: string) =>
    request<TableRulePreflight>(`/api/v1/articles/${articleId}/table-rule-preflight?project_id=${encodeURIComponent(projectId)}`),
  assistTableRulePreflight: (projectId: string, articleId: string, sourceHeaders: string[] = []) =>
    request<TableRulePreflight>(`/api/v1/articles/${articleId}/table-rule-preflight/assist`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, source_headers: sourceHeaders }) }),
  modelCapabilities: () => request<Record<string, unknown>>('/api/v1/model-capabilities'),
  runHeaderMappingBenchmark: (projectId: string) =>
    request<{task_id: string}>(`/api/v1/model-benchmarks/header-mapping?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  articleTargetHeaders: (projectId: string, articleId: string) =>
    request<HeaderField[]>(`/api/v1/articles/${articleId}/target-headers?project_id=${encodeURIComponent(projectId)}`),
  confirmTableRulePreflight: (projectId: string, articleId: string, rules: Record<string, unknown>[], scope = 'article') =>
    request<{created: number; rules: Record<string, unknown>[]}>(`/api/v1/articles/${articleId}/table-rule-preflight/confirm`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, rules, scope }) }),
  standardizeTables: (projectId: string, articleId: string, useLlm = false) =>
    request<{task_id: string}>(`/api/v1/articles/${articleId}/standardize-tables`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, use_llm: useLlm }) }),
  updateStandardTable: (projectId: string, elementId: string, headers: string[], rows: string[][], editReason = '') =>
    request<DocumentElement>(`/api/v1/elements/${elementId}/standard-table`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, headers, rows, edit_reason: editReason }) }),
  restandardizeTable: (projectId: string, elementId: string, mode = 'source_headers') =>
    request<DocumentElement>(`/api/v1/elements/${elementId}/restandardize-table`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, mode }) }),
  paragraphCues: (projectId: string, articleId: string) =>
    request<{article_id: string; cues: ParagraphCue[]}>(`/api/v1/articles/${articleId}/paragraph-cues?project_id=${encodeURIComponent(projectId)}`),
  refreshParagraphCues: (projectId: string, articleId: string) =>
    request<{article_id: string; cues: ParagraphCue[]; refreshed_from_samples?: string[]}>(`/api/v1/articles/${articleId}/paragraph-cues/refresh?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  selectElements: (projectId: string, articleId: string, elementIds: string[]) => request(`/api/v1/articles/${articleId}/selections?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ element_ids: elementIds }) }),
  addManualElement: (projectId: string, articleId: string, payload: Record<string, unknown>) => request<DocumentElement>(`/api/v1/articles/${articleId}/elements/manual?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  hitTestElements: (projectId: string, articleId: string, payload: Record<string, unknown>) =>
    request<{suggested_action:string;hits:{element:DocumentElement;overlap:number}[]}>(`/api/v1/articles/${articleId}/elements/hit-test?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  teachElement: (projectId: string, elementId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/elements/${elementId}/teach?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  mergeSelection: (projectId: string, elementId: string, payload: Record<string, unknown>) =>
    request<DocumentElement>(`/api/v1/elements/${elementId}/merge-selection?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  createBatch: (projectId: string, articleId: string, useLlm = true) => request<{task_id: string}>('/api/v1/extraction-batches', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, use_llm: useLlm }) }),
  extractTables: (projectId: string, articleId: string, useLlm = false) =>
    request<{task_id: string}>(`/api/v1/articles/${articleId}/extract-tables`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, use_llm: useLlm }) }),
  extractParagraphs: (projectId: string, articleId: string, elementIds: string[] = [], useLlm = true) =>
    request<{task_id: string}>(`/api/v1/articles/${articleId}/extract-paragraphs`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, element_ids: elementIds, use_llm: useLlm }) }),
  mergeCandidates: (projectId: string, articleId: string) =>
    request<{batch_id: string; record_count: number; status: string}>(`/api/v1/articles/${articleId}/merge-candidates`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, use_llm: false }) }),
  reapplyRules: (projectId: string, batchId: string) =>
    request<{batch_id: string; updated: number}>(`/api/v1/extraction-batches/${batchId}/reapply-rules`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) }),
  validateEvidence: (projectId: string, batchId: string) =>
    request<{batch_id: string; updated: number; insufficient: number}>(`/api/v1/extraction-batches/${batchId}/validate-evidence`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) }),
  fieldMappings: (projectId: string, batchId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/extraction-batches/${batchId}/field-mappings?project_id=${encodeURIComponent(projectId)}`),
  qualitySlice: (projectId: string, batchId: string, sourceType = '') => {
    const params = new URLSearchParams({ project_id: projectId })
    if (sourceType) params.set('source_type', sourceType)
    return request<{batch_id: string; source_type: string; cells: Record<string, unknown>[]}>(`/api/v1/extraction-batches/${batchId}/quality-slices?${params.toString()}`)
  },
  reextractElement: (projectId: string, articleId: string, elementId: string, useLlm = true) =>
    request<{task_id: string}>(`/api/v1/articles/${articleId}/elements/${elementId}/reextract`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, element_ids: [elementId], use_llm: useLlm }) }),
  batch: (projectId: string, batchId: string) => request<BatchPayload>(`/api/v1/extraction-batches/${batchId}/records?project_id=${encodeURIComponent(projectId)}`),
  updateCell: (projectId: string, cellId: string, payload: Record<string, unknown>) => request(`/api/v1/candidate-cells/${cellId}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteCell: (projectId: string, cellId: string) => request(`/api/v1/candidate-cells/${cellId}?project_id=${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  manualImageCell: (payload: Record<string, unknown>) => request('/api/v1/candidate-cells/manual-image', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  manualRecord: (payload: Record<string, unknown>) => request('/api/v1/candidate-records/manual', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  mergeRecords: (projectId: string, recordIds: string[]) => request('/api/v1/candidate-records/merge', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, record_ids: recordIds }) }),
  deleteRecord: (projectId: string, recordId: string) => request(`/api/v1/candidate-records/${recordId}?project_id=${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  assignHeader: (projectId: string, articleId: string, configId: string) => request(`/api/v1/articles/${articleId}/header-config`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, config_id: configId }) }),
  reviews: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/reviews?project_id=${encodeURIComponent(projectId)}`),
  rules: (projectId: string) => request<{mapping: Record<string, unknown>[]; extraction: Record<string, unknown>[]}>(`/api/v1/rules?project_id=${encodeURIComponent(projectId)}`),
  ruleMemory: (projectId: string, articleId = '') => {
    const params = new URLSearchParams({ project_id: projectId })
    if (articleId) params.set('article_id', articleId)
    return request<{mapping: Record<string, unknown>[]; extraction: Record<string, unknown>[]}>(`/api/v1/rule-memory?${params.toString()}`)
  },
  updateRuleMemory: (projectId: string, ruleId: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/v1/rule-memory/${ruleId}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteRuleMemory: (projectId: string, ruleId: string) =>
    request<Record<string, unknown>>(`/api/v1/rule-memory/${ruleId}?project_id=${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  standardized: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/standardized-records?project_id=${encodeURIComponent(projectId)}`),
  costs: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/costs?project_id=${encodeURIComponent(projectId)}`),
  modelSetup: () => request<Record<string, any>>('/api/v1/model-setup'),
  testModelSetup: (payload: Record<string, unknown>) =>
    request<Record<string, any>>('/api/v1/model-setup/test', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  saveModelSetup: (payload: Record<string, unknown>) =>
    request<Record<string, any>>('/api/v1/model-setup', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  settings: () => request<Record<string, any>>('/api/v1/settings'),
  saveSettings: (payload: Record<string, unknown>) => request('/api/v1/settings', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  providerPresets: () => request<Record<string, any>[]>('/api/v1/provider-presets'),
  testCustomProvider: (payload: Record<string, unknown>) =>
    request<{success: boolean; models: {name:string;display_name:string;supports_vision:boolean;max_tokens?:number}[]; error?: string}>('/api/v1/providers/test', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  addProviderModel: (providerName: string, payload: Record<string, unknown>) =>
    request(`/api/v1/providers/${providerName}/models`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteProvider: (providerName: string) =>
    request(`/api/v1/providers/${providerName}`, { method: 'DELETE' }),
  openSource: (projectId: string, source: string) => request<Record<string, string>>('/api/v1/import/open-source', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, source }) }),
  searchArticleSources: (projectId: string, source: string) =>
    request<{input:string;doi:string;candidates:Record<string, unknown>[];upload_required:boolean}>('/api/v1/article-sources/search', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, source }) }),
  confirmArticleSource: (projectId: string, sourceId: string) =>
    request<{status:string;article_id?:string;resource_id?:string;file_name?:string;message?:string}>(`/api/v1/article-sources/${encodeURIComponent(sourceId)}/confirm`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) }),
  confirmAccess: (projectId: string, articleId: string, url: string) => request<{task_id: string}>('/api/v1/import/confirm-access', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, url }) }),
  uploadResource: (projectId: string, articleId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<Record<string, unknown>>(`/api/v1/articles/${articleId}/upload?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  importArticleFile: (projectId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<{article_id:string;resource_id:string;file_name:string}>(`/api/v1/import/file?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  uploadChatArticle: (projectId: string, threadId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<{article_id:string;resource_id:string;file_name:string;status:string;message?:string;selection?:Record<string,unknown>}>(`/api/v1/chat/threads/${threadId}/upload?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  handoffAgentRun: (projectId: string, runId: string) =>
    request<{run_id:string;status:string;article_id?:string;workbench_path:string}>(`/api/v1/agent-runs/${runId}/handoff`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) }),
  resumeAgentFromWorkbench: (projectId: string, runId: string) =>
    request<{run_id:string;status:string;return_path?:string;pending_interrupt?:Record<string,unknown>}>(`/api/v1/agent-runs/${runId}/return-from-workbench`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId }) }),
  workbenchDiff: (projectId: string, runId: string) =>
    request<Record<string, unknown>>(`/api/v1/agent-runs/${runId}/workbench-diff?project_id=${encodeURIComponent(projectId)}`),
  task: (projectId: string, taskId: string) => request<Record<string, any>>(`/api/v1/tasks/${taskId}?project_id=${encodeURIComponent(projectId)}`),
  pdfUrl: (projectId: string, resourceId: string) => `/api/v1/resources/${resourceId}/pdf?project_id=${encodeURIComponent(projectId)}`,
  previewUrl: (projectId: string, elementId: string) => `/api/v1/elements/${elementId}/preview?project_id=${encodeURIComponent(projectId)}`,
  updateElement: (projectId: string, elementId: string, payload: Record<string, unknown>) =>
    request<DocumentElement>(`/api/v1/elements/${elementId}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteElement: (projectId: string, elementId: string) =>
    request<Record<string, unknown>>(`/api/v1/elements/${elementId}?project_id=${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
  confirmCell: (projectId: string, cellId: string, payload: Record<string, unknown>) =>
    request(`/api/v1/candidate-cells/${cellId}/confirm?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  suggestConversion: (projectId: string, cellId: string) =>
    request<{formula: string|null; factor: number; explanation: string}>(`/api/v1/candidate-cells/${cellId}/suggest-conversion?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  articleCandidateRecords: (projectId: string, articleId: string) =>
    request<{headers: {display_header:string;canonical_field:string;target_unit:string}[]; records: Record<string, unknown>[]}>(`/api/v1/articles/${articleId}/candidate-records?project_id=${encodeURIComponent(projectId)}`),
  approveRecord: (projectId: string, recordId: string) =>
    request(`/api/v1/candidate-records/${recordId}/approve?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  rejectRecord: (projectId: string, recordId: string) =>
    request(`/api/v1/candidate-records/${recordId}/reject?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  batchApprove: (projectId: string, articleId: string, recordIds: string[]) =>
    request(`/api/v1/articles/${articleId}/batch-approve?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, record_ids: recordIds }) }),
  batchConfirmMappings: (projectId: string, articleId: string, confirmations: Record<string, unknown>[]) =>
    request<{confirmed: number; errors: Record<string, unknown>[]}>(`/api/v1/articles/${articleId}/batch-confirm?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, confirmations }) }),
  deleteRule: (projectId: string, ruleId: string, ruleType = 'mapping') =>
    request(`/api/v1/rules/${ruleId}?project_id=${encodeURIComponent(projectId)}&rule_type=${ruleType}`, { method: 'DELETE' }),
  testProvider: (providerName: string, apiKey: string) =>
    request<{success: boolean; models: {name:string;display_name:string;supports_vision:boolean}[]; error?: string}>(`/api/v1/providers/${providerName}/test`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ api_key: apiKey }) }),
  finalize: (projectId: string, articleId: string) =>
    request<{records: number}>(`/api/v1/articles/${articleId}/finalize?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId }) }),
  traceRecords: (projectId: string, articleId?: string, query?: string) => {
    const params = new URLSearchParams({ project_id: projectId })
    if (articleId) params.set('article_id', articleId)
    if (query) params.set('q', query)
    return request<any>(`/api/v1/trace-records?${params.toString()}`)
  },
  traceRecord: (projectId: string, recordId: string) =>
    request<any>(`/api/v1/trace-records/${recordId}?project_id=${encodeURIComponent(projectId)}`),
  exportArticle: (projectId: string, articleId: string, format = 'csv', outputDir = '') => {
    const params = new URLSearchParams({ project_id: projectId, format })
    if (outputDir) params.set('output_dir', outputDir)
    return request<{path: string; records: number; format: string}>(`/api/v1/articles/${articleId}/export?${params.toString()}`)
  },
  exportDirectory: (projectId: string) =>
    request<{path: string; is_default: boolean}>(`/api/v1/export-directory?project_id=${encodeURIComponent(projectId)}`),
  saveExportDirectory: (path: string) =>
    request<{path: string; status: string}>('/api/v1/export-directory', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ path }) }),
  openExportDirectory: (projectId: string, path: string) =>
    request<{path: string; status: string}>(`/api/v1/export-directory/open?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ path }) }),
  articleTrace: (projectId: string, articleId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/articles/${articleId}/trace?project_id=${encodeURIComponent(projectId)}`),
}

export function watchTask(projectId: string, taskId: string, onEvent: (event: WorkflowEvent) => void, onDone: () => void): () => void {
  const source = new EventSource(`/api/v1/tasks/${taskId}/events?project_id=${encodeURIComponent(projectId)}`)
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as WorkflowEvent
    onEvent(event)
    if (event.progress >= 1) {
      source.close()
      onDone()
    }
  }
  source.onerror = () => {
    source.close()
    onDone()
  }
  return () => source.close()
}

export function watchAgentRun(
  projectId: string,
  runId: string,
  onEvent: (event: { event_id: number; level: string; message: string; done?: boolean }) => void,
  onDone: () => void,
): () => void {
  const source = new EventSource(`/api/v1/agent-runs/${runId}/events?project_id=${encodeURIComponent(projectId)}`)
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as { event_id: number; level: string; message: string; done?: boolean }
    onEvent(event)
    if (event.done) {
      source.close()
      onDone()
    }
  }
  source.onerror = () => {
    source.close()
    onDone()
  }
  return () => source.close()
}
