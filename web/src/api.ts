import type { Article, BatchPayload, DocumentElement, HeaderConfig, Resource, WorkflowEvent, Workspace } from './types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const detail = await response.text()
    throw new Error(detail || `${response.status} ${response.statusText}`)
  }
  return response.json() as Promise<T>
}

export const api = {
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
  selectElements: (projectId: string, articleId: string, elementIds: string[]) => request(`/api/v1/articles/${articleId}/selections?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ element_ids: elementIds }) }),
  addManualElement: (projectId: string, articleId: string, payload: Record<string, unknown>) => request<DocumentElement>(`/api/v1/articles/${articleId}/elements/manual?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  createBatch: (projectId: string, articleId: string, useLlm = true) => request<{task_id: string}>('/api/v1/extraction-batches', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, use_llm: useLlm }) }),
  batch: (projectId: string, batchId: string) => request<BatchPayload>(`/api/v1/extraction-batches/${batchId}/records?project_id=${encodeURIComponent(projectId)}`),
  updateCell: (projectId: string, cellId: string, payload: Record<string, unknown>) => request(`/api/v1/candidate-cells/${cellId}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  mergeRecords: (projectId: string, recordIds: string[]) => request('/api/v1/candidate-records/merge', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, record_ids: recordIds }) }),
  assignHeader: (projectId: string, articleId: string, configId: string) => request(`/api/v1/articles/${articleId}/header-config`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, config_id: configId }) }),
  reviews: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/reviews?project_id=${encodeURIComponent(projectId)}`),
  rules: (projectId: string) => request<{mapping: Record<string, unknown>[]; extraction: Record<string, unknown>[]}>(`/api/v1/rules?project_id=${encodeURIComponent(projectId)}`),
  standardized: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/standardized-records?project_id=${encodeURIComponent(projectId)}`),
  costs: (projectId: string) => request<Record<string, unknown>[]>(`/api/v1/costs?project_id=${encodeURIComponent(projectId)}`),
  settings: () => request<Record<string, any>>('/api/v1/settings'),
  saveSettings: (payload: Record<string, unknown>) => request('/api/v1/settings', { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  openSource: (projectId: string, source: string) => request<Record<string, string>>('/api/v1/import/open-source', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, source }) }),
  confirmAccess: (projectId: string, articleId: string, url: string) => request<{task_id: string}>('/api/v1/import/confirm-access', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ project_id: projectId, article_id: articleId, url }) }),
  uploadResource: (projectId: string, articleId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<Record<string, unknown>>(`/api/v1/articles/${articleId}/upload?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  importArticleFile: (projectId: string, file: File) => {
    const data = new FormData(); data.append('file', file)
    return request<{article_id:string;resource_id:string;file_name:string}>(`/api/v1/import/file?project_id=${encodeURIComponent(projectId)}`, { method: 'POST', body: data })
  },
  task: (projectId: string, taskId: string) => request<Record<string, any>>(`/api/v1/tasks/${taskId}?project_id=${encodeURIComponent(projectId)}`),
  pdfUrl: (projectId: string, resourceId: string) => `/api/v1/resources/${resourceId}/pdf?project_id=${encodeURIComponent(projectId)}`,
  previewUrl: (projectId: string, elementId: string) => `/api/v1/elements/${elementId}/preview?project_id=${encodeURIComponent(projectId)}`,
  updateElement: (projectId: string, elementId: string, payload: Record<string, unknown>) =>
    request<DocumentElement>(`/api/v1/elements/${elementId}?project_id=${encodeURIComponent(projectId)}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(payload) }),
  deleteElement: (projectId: string, elementId: string) =>
    request<Record<string, unknown>>(`/api/v1/elements/${elementId}?project_id=${encodeURIComponent(projectId)}`, { method: 'DELETE' }),
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
