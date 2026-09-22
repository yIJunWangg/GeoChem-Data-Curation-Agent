import type { AgentActivityEvent, AgentRun, Article, BatchPayload, ChatCitation, ChatThread, ChatThreadState, DocumentElement, HeaderConfig, HeaderField, ParagraphCue, Resource, TableRulePreflight, TraceRecordDetail, TraceRecordSummary, WorkflowEvent, Workspace } from './types'

export type AdminRuntimeStatus = {
  profile: string
  build_id: string
  auth_mode: string
  database: string
  task_backend: string
  credential_vault_ready?: boolean
  user_management: { mode: string; available: boolean; realm: string; message: string }
}

export type AdminOverview = {
  users_total: number
  users_enabled: number
  active_credentials: number
  active_allocations: number
  storage_quota_bytes: number
  storage_used_bytes: number
  running_tasks: number
  failed_tasks: number
  credential_vault_ready: boolean
}

export type AdminModelCredential = {
  credential_id: string
  name: string
  provider: string
  api_format: string
  base_url: string
  model_id: string
  secret_ref: string
  key_fingerprint: string
  secret_configured: boolean
  enabled: boolean
  created_by: string
  created_at: string
  updated_at: string
}

export type AdminUserPolicy = {
  user_id: string
  storage: { quota_bytes: number; used_bytes: number; updated_at: string }
  model_allocations: Array<{
    allocation_id: string
    credential_id: string
    credential_name: string
    provider: string
    model_id: string
    enabled: number | boolean
    monthly_token_limit: number
    monthly_cost_limit: number
  }>
}

export type AdminUser = {
  id: string
  username: string
  email: string
  first_name: string
  last_name: string
  enabled: boolean
  email_verified: boolean
  created_at: number
  roles: string[]
}

export type AdminUsersResponse = {
  users: AdminUser[]
  total: number
  first: number
  limit: number
  preview?: boolean
}

export type WorkspaceMember = {
  membership_id: string
  user_id: string
  role: 'owner' | 'curator' | 'reviewer' | 'viewer'
  status: 'active' | 'disabled'
  invited_by: string
  username: string
  display_name: string
  email: string
  created_at: string
  updated_at: string
}

export type PublishedRule = {
  rule_id: string
  project_id: string
  organization_id?: string
  article_id?: string
  pattern: string
  target_field: string
  target_header: string
  target_unit?: string
  rule_type: string
  source_type?: string
  evidence?: string
  conditions: Record<string, unknown>
  confidence: number
  review_status: string
  scope: 'article' | 'project' | 'organization'
  enabled: boolean
  revision: number
  supersedes_rule_id?: string
  created_by?: string
  submitter_name?: string
  published_by?: string
  published_at?: string
  created_at?: string
  usage_count: number
  related_article_count: number
}

export type RuleSubmission = {
  submission_id: string
  import_id?: string
  project_id: string
  organization_id: string
  article_id?: string
  source_term: string
  target_canonical_field: string
  target_header?: string
  source_unit?: string
  target_unit?: string
  chemical_form?: string
  context_text?: string
  conversion_formula?: string
  evidence?: string
  notes?: string
  knowledge_concept_id?: string
  knowledge_release_id?: string
  scope: 'article' | 'project' | 'organization'
  confidence: number
  status: 'draft' | 'pending' | 'approved' | 'rejected'
  conflict_status: string
  validation: {
    valid?: boolean
    errors?: string[]
    warnings?: string[]
    duplicates?: string[]
    conflicts?: Array<{rule_id:string;target:string}>
    knowledge_score?: number
    auto_apply_allowed?: boolean
    unit_compatibility?: string
    chemical_form?: string
  }
  supersedes_rule_id?: string
  created_by: string
  submitter_name?: string
  created_at: string
  updated_at: string
  submitted_at?: string
  reviewed_at?: string
  reviews?: Array<Record<string, unknown>>
}

export type RuleImportPreview = {
  import_id: string
  filename: string
  status: string
  total_rows: number
  valid_rows: number
  invalid_rows: number
  preview: Array<Record<string, unknown>>
  errors: Array<Record<string, unknown>>
  submission_ids?: string[]
}

const JSON_HEADERS = { 'Content-Type': 'application/json' }
let apiAccessToken = ''
const authenticatedFileCache = new Map<string, { url: string; httpHeaders: Record<string, string> }>()

export function setApiAccessToken(value: string) {
  if (apiAccessToken !== value) authenticatedFileCache.clear()
  apiAccessToken = value
}

export function apiAuthorizationHeaders(): Record<string, string> {
  return apiAccessToken ? { Authorization: `Bearer ${apiAccessToken}` } : {}
}

export function authenticatedFile(url: string) {
  const cacheKey = `${apiAccessToken}\n${url}`
  const existing = authenticatedFileCache.get(cacheKey)
  if (existing) return existing
  const next = { url, httpHeaders: apiAuthorizationHeaders() }
  authenticatedFileCache.set(cacheKey, next)
  return next
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (apiAccessToken) headers.set('Authorization', `Bearer ${apiAccessToken}`)
  const response = await fetch(url, apiAccessToken || init?.headers ? { ...init, headers } : init)
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

async function download(url: string): Promise<string> {
  const response = await fetch(url, { headers: apiAuthorizationHeaders() })
  if (!response.ok) {
    const body = await response.text()
    let detail = body
    try {
      const parsed = JSON.parse(body)
      detail = typeof parsed.detail === 'string' ? parsed.detail : body
    } catch {
      detail = body
    }
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  const disposition = response.headers.get('content-disposition') || ''
  const encoded = disposition.match(/filename\*=utf-8''([^;]+)/i)?.[1]
  const plain = disposition.match(/filename="?([^";]+)"?/i)?.[1]
  const filename = encoded ? decodeURIComponent(encoded) : (plain || 'geochem-export')
  const objectUrl = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = objectUrl
  anchor.download = filename
  anchor.style.display = 'none'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000)
  return filename
}

export const api = {
  adminStatus: () => request<AdminRuntimeStatus>('/api/v1/admin/status'),
  adminOverview: () => request<AdminOverview>('/api/v1/admin/overview'),
  adminUsers: (query = '', first = 0, limit = 100) => {
    const params = new URLSearchParams({ q: query, first: String(first), limit: String(limit) })
    return request<AdminUsersResponse>(`/api/v1/admin/users?${params.toString()}`)
  },
  createAdminUser: (payload: Record<string, unknown>) =>
    request<AdminUser>('/api/v1/admin/users', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  updateAdminUser: (userId: string, payload: Record<string, unknown>) =>
    request<AdminUser>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  updateAdminUserRoles: (userId: string, roles: string[]) =>
    request<AdminUser>(`/api/v1/admin/users/${encodeURIComponent(userId)}/roles`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ roles }) }),
  resetAdminUserPassword: (userId: string, password: string, temporary = true) =>
    request<{status:string;user_id:string;temporary:boolean}>(`/api/v1/admin/users/${encodeURIComponent(userId)}/reset-password`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ password, temporary }) }),
  logoutAdminUser: (userId: string) =>
    request<{status:string;user_id:string}>(`/api/v1/admin/users/${encodeURIComponent(userId)}/logout`, { method: 'POST' }),
  deleteAdminUser: (userId: string) =>
    request<{status:string;user_id:string}>(`/api/v1/admin/users/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
  adminModelCredentials: () => request<{vault_ready:boolean;credentials:AdminModelCredential[]}>('/api/v1/admin/model-credentials'),
  createAdminModelCredential: (payload: Record<string, unknown>) =>
    request<AdminModelCredential>('/api/v1/admin/model-credentials', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  updateAdminModelCredential: (credentialId: string, payload: Record<string, unknown>) =>
    request<AdminModelCredential>(`/api/v1/admin/model-credentials/${encodeURIComponent(credentialId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteAdminModelCredential: (credentialId: string) =>
    request<{status:string;credential_id:string}>(`/api/v1/admin/model-credentials/${encodeURIComponent(credentialId)}`, { method: 'DELETE' }),
  adminUserPolicy: (userId: string) => request<AdminUserPolicy>(`/api/v1/admin/users/${encodeURIComponent(userId)}/policy`),
  updateAdminUserStorageQuota: (userId: string, quotaBytes: number) =>
    request<AdminUserPolicy['storage']>(`/api/v1/admin/users/${encodeURIComponent(userId)}/storage-quota`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ quota_bytes: quotaBytes }) }),
  updateAdminUserModelAllocation: (userId: string, payload: Record<string, unknown>) =>
    request<AdminUserPolicy>(`/api/v1/admin/users/${encodeURIComponent(userId)}/model-allocation`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  adminTasks: (limit = 200) => request<{tasks:Record<string, unknown>[]}>(`/api/v1/admin/tasks?limit=${limit}`),
  adminAuditEvents: (limit = 200, userId = '') => {
    const params = new URLSearchParams({ limit: String(limit) })
    if (userId) params.set('user_id', userId)
    return request<{events:Record<string, unknown>[]}>(`/api/v1/admin/audit-events?${params.toString()}`)
  },
  workspaces: () => request<{workspaces:Workspace[]}>('/api/v1/workspaces'),
  adminWorkspaceMembers: (projectId: string) =>
    request<{members:WorkspaceMember[]}>(`/api/v1/admin/workspaces/${encodeURIComponent(projectId)}/members`),
  addAdminWorkspaceMember: (projectId: string, userId: string, role: WorkspaceMember['role'], status: WorkspaceMember['status'] = 'active') =>
    request<WorkspaceMember>(`/api/v1/admin/workspaces/${encodeURIComponent(projectId)}/members`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ user_id: userId, role, status }) }),
  updateAdminWorkspaceMember: (projectId: string, userId: string, payload: Partial<Pick<WorkspaceMember, 'role' | 'status'>>) =>
    request<WorkspaceMember>(`/api/v1/admin/workspaces/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteAdminWorkspaceMember: (projectId: string, userId: string) =>
    request<{status:string;project_id:string;user_id:string}>(`/api/v1/admin/workspaces/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`, { method: 'DELETE' }),
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
  mappingKnowledgeSearch: (projectId: string, query: string, limit = 20) =>
    request<Record<string, unknown>>(`/api/v1/mapping-knowledge/search?project_id=${encodeURIComponent(projectId)}&q=${encodeURIComponent(query)}&limit=${limit}`),
  currentMappingKnowledgeRelease: (projectId: string) =>
    request<Record<string, unknown>>(`/api/v1/mapping-knowledge/releases/current?project_id=${encodeURIComponent(projectId)}`),
  syncMappingKnowledge: (projectId: string) =>
    request<Record<string, unknown>>(`/api/v1/admin/mapping-knowledge/sync?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  mappingKnowledgeImportDiff: (projectId: string, importId: string) =>
    request<Record<string, unknown>>(`/api/v1/admin/mapping-knowledge/imports/${importId}/diff?project_id=${encodeURIComponent(projectId)}`),
  publishMappingKnowledgeImport: (projectId: string, importId: string) =>
    request<Record<string, unknown>>(`/api/v1/admin/mapping-knowledge/imports/${importId}/publish?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
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
  rules: (projectId: string, filters: Record<string, string | number> = {}) => {
    const params = new URLSearchParams({ project_id: projectId })
    Object.entries(filters).forEach(([key, value]) => { if (String(value)) params.set(key, String(value)) })
    return request<{items: PublishedRule[]; count: number; organization_id: string}>(`/api/v1/rules?${params.toString()}`)
  },
  ruleDetail: (projectId: string, ruleId: string) =>
    request<PublishedRule & {history?: PublishedRule[]}>(`/api/v1/rules/${encodeURIComponent(ruleId)}?project_id=${encodeURIComponent(projectId)}`),
  ruleHistory: (projectId: string, ruleId: string) =>
    request<{items: PublishedRule[]}>(`/api/v1/rules/${encodeURIComponent(ruleId)}/history?project_id=${encodeURIComponent(projectId)}`),
  ruleSubmissions: (projectId: string, filters: {status?:string;mine?:boolean;q?:string} = {}) => {
    const params = new URLSearchParams({ project_id: projectId })
    if (filters.status) params.set('status', filters.status)
    if (filters.mine) params.set('mine', 'true')
    if (filters.q) params.set('q', filters.q)
    return request<{items: RuleSubmission[]; count: number}>(`/api/v1/rule-submissions?${params.toString()}`)
  },
  createRuleSubmission: (projectId: string, payload: Record<string, unknown>) =>
    request<RuleSubmission>(`/api/v1/rule-submissions?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  updateRuleSubmission: (projectId: string, submissionId: string, payload: Record<string, unknown>) =>
    request<RuleSubmission>(`/api/v1/rule-submissions/${encodeURIComponent(submissionId)}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  submitRuleSubmission: (projectId: string, submissionId: string) =>
    request<RuleSubmission>(`/api/v1/rule-submissions/${encodeURIComponent(submissionId)}/submit?project_id=${encodeURIComponent(projectId)}`, { method: 'POST' }),
  importRules: (projectId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<RuleImportPreview>(`/api/v1/rule-imports?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  ruleImportPreview: (projectId: string, importId: string) =>
    request<RuleImportPreview>(`/api/v1/rule-imports/${encodeURIComponent(importId)}/preview?project_id=${encodeURIComponent(projectId)}`),
  adminRuleSubmissions: (projectId: string, filters: {status?:string;q?:string} = {}) => {
    const params = new URLSearchParams({ project_id: projectId })
    if (filters.status) params.set('status', filters.status)
    if (filters.q) params.set('q', filters.q)
    return request<{items: RuleSubmission[]; count: number}>(`/api/v1/admin/rule-submissions?${params.toString()}`)
  },
  approveRuleSubmission: (projectId: string, submissionId: string, comment = '') =>
    request<{submission:RuleSubmission;rule:PublishedRule}>(`/api/v1/admin/rule-submissions/${encodeURIComponent(submissionId)}/approve?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ comment }) }),
  rejectRuleSubmission: (projectId: string, submissionId: string, comment: string) =>
    request<RuleSubmission>(`/api/v1/admin/rule-submissions/${encodeURIComponent(submissionId)}/reject?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ comment }) }),
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
  handoffAgentRun: (
    projectId: string,
    runId: string,
    presentation: 'inline' | 'full' = 'full',
    workbenchView?: 'resources' | 'extract' | 'quality',
    workbenchStage?: string,
  ) =>
    request<{run_id:string;status:string;article_id?:string;workbench_path:string;handoff_context?:Record<string,unknown>}>(`/api/v1/agent-runs/${runId}/handoff`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({
        project_id: projectId,
        presentation,
        workbench_view: workbenchView,
        workbench_stage: workbenchStage,
      }),
    }),
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
  exportArticle: (projectId: string, articleId: string, format = 'csv', outputDir = '') =>
    request<{job_id: string; path: string; records: number; format: string}>(`/api/v1/articles/${articleId}/export`, {
      method: 'POST',
      headers: JSON_HEADERS,
      body: JSON.stringify({ project_id: projectId, format, output_dir: outputDir }),
    }),
  exportJobs: (projectId: string, articleId = '', limit = 20) => {
    const params = new URLSearchParams({ project_id: projectId, limit: String(limit) })
    if (articleId) params.set('article_id', articleId)
    return request<{job_id:string;article_id:string;export_format:string;record_count:number;status:string;created_at:string}[]>(`/api/v1/export-jobs?${params.toString()}`)
  },
  downloadExport: (projectId: string, jobId: string) =>
    download(`/api/v1/export-jobs/${jobId}/download?project_id=${encodeURIComponent(projectId)}`),
  exportDirectory: (projectId: string) =>
    request<{path: string; is_default: boolean; managed?: boolean}>(`/api/v1/export-directory?project_id=${encodeURIComponent(projectId)}`),
  saveExportDirectory: (path: string) =>
    request<{path: string; status: string}>('/api/v1/export-directory', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify({ path }) }),
  openExportDirectory: (projectId: string, path: string) =>
    request<{path: string; status: string}>(`/api/v1/export-directory/open?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ path }) }),
  articleTrace: (projectId: string, articleId: string) =>
    request<Record<string, unknown>[]>(`/api/v1/articles/${articleId}/trace?project_id=${encodeURIComponent(projectId)}`),
}

export function watchTask(projectId: string, taskId: string, onEvent: (event: WorkflowEvent) => void, onDone: () => void): () => void {
  return watchSse(
    `/api/v1/tasks/${taskId}/events?project_id=${encodeURIComponent(projectId)}`,
    (payload) => {
      const event = payload as WorkflowEvent
      onEvent(event)
      return event.progress >= 1
    },
    onDone,
  )
}

export function watchAgentRun(
  projectId: string,
  runId: string,
  onEvent: (event: AgentActivityEvent) => void,
  onDone: () => void,
): () => void {
  return watchSse(
    `/api/v1/agent-runs/${runId}/events?project_id=${encodeURIComponent(projectId)}`,
    (payload) => {
      const event = payload as AgentActivityEvent
      onEvent(event)
      return Boolean(event.done)
    },
    onDone,
  )
}

function watchSse(
  url: string,
  onPayload: (payload: Record<string, any>) => boolean,
  onDone: () => void,
): () => void {
  const controller = new AbortController()
  let completed = false
  const finish = () => {
    if (completed) return
    completed = true
    onDone()
  }
  void (async () => {
    try {
      const response = await fetch(url, {
        headers: { Accept: 'text/event-stream', ...apiAuthorizationHeaders() },
        signal: controller.signal,
      })
      if (!response.ok || !response.body) throw new Error(`${response.status} ${response.statusText}`)
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      while (!controller.signal.aborted) {
        const { value, done } = await reader.read()
        buffer += decoder.decode(value, { stream: !done })
        let boundary = buffer.indexOf('\n\n')
        while (boundary >= 0) {
          const block = buffer.slice(0, boundary)
          buffer = buffer.slice(boundary + 2)
          const data = block.split('\n').filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim()).join('\n')
          if (data && onPayload(JSON.parse(data))) {
            controller.abort()
            finish()
            return
          }
          boundary = buffer.indexOf('\n\n')
        }
        if (done) break
      }
      if (!controller.signal.aborted) finish()
    } catch {
      if (!controller.signal.aborted) finish()
    }
  })()
  return () => controller.abort()
}
