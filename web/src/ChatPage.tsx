import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertCircle, Bot, CheckCircle2, ChevronRight, Circle, CirclePause, Eye, FileSearch, FileUp, LoaderCircle, Maximize2, MessageSquarePlus, Minimize2, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Search, Send, Sparkles, TableProperties, Trash2, Wrench } from 'lucide-react'
import { api, watchAgentRun } from './api'
import { foldActivityEvents } from './agentActivity'
import { AgentDecisionPrompt, type AgentDecisionOption } from './AgentDecisionPrompt'
import { EmbeddedWorkbench } from './EmbeddedWorkbench'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { useAppStore } from './store'
import type {
  AgentActivityEvent,
  ChatCitation,
  ChatThread,
  HeaderConfig,
  WorkbenchStage,
  WorkbenchView,
} from './types'

type AgentEntity = {
  entity_type: string
  entity_id: string
  title: string
  subtitle?: string
  article_id?: string
  resource_id?: string
  element_id?: string
  record_id?: string
  alias?: string
  metadata?: Record<string, unknown>
}

type ChatUiPayload = {
  kind?: string
  entities?: AgentEntity[]
  results?: Record<string, unknown>[]
  article_id?: string
  workbench_view?: 'resources' | 'extract' | 'quality'
  workbench_step?: number
  workbench_stage?: string
  reason?: string
  path?: string
  operation?: string
  format?: string
  output_dir?: string
  settings_path?: string
  suggested_name?: string
  accepted_formats?: string[]
}

const QUICK_QUESTIONS = [
  '这篇文章发现了哪些可抽取资源？',
  'HDP-B1 的 SiO2 数据来自哪里？',
  '当前文章有多少已标准化样品？',
  '哪些字段仍然证据不足？',
]

const legacyWorkbenchView = (step: unknown): 'resources' | 'extract' | 'quality' => {
  const value = Number(step ?? 1)
  if (value <= 1) return 'resources'
  if (value === 2) return 'extract'
  return 'quality'
}

const isWorkbenchView = (value: string | null): value is WorkbenchView =>
  value === 'resources' || value === 'extract' || value === 'quality'

const isWorkbenchStage = (value: string | null): value is WorkbenchStage =>
  value === 'table_standardize'
  || value === 'mapping'
  || value === 'tables'
  || value === 'paragraphs'
  || value === 'figures'
  || value === 'edit'

function checkpointWorkbenchTarget(kind: string): { view: WorkbenchView; stage: WorkbenchStage } {
  if (kind === 'mapping_confirmation') return { view: 'extract', stage: 'mapping' }
  if (kind === 'quality_confirmation') return { view: 'quality', stage: 'edit' }
  return { view: 'resources', stage: 'table_standardize' }
}

const AGENT_NODE_LABELS: Record<string, string> = {
  start: '准备任务',
  classify: '理解请求并选择工具',
  resource_discovery: '自动发现资源',
  table_standardization: '标准化表格资源',
  mapping_confirmation: '生成字段映射建议',
  mapping_application: '应用字段映射规则',
  table_extraction: '抽取表格资源',
  paragraph_extraction: '抽取段落资源',
  candidate_merge: '合并候选结果',
  evidence_validation: '校验证据完整性',
  quality_confirmation: '准备候选结果质检',
  model_configuration_required: '等待模型配置',
}

function agentNodeLabel(node?: string): string {
  return AGENT_NODE_LABELS[node || ''] || node || '准备中'
}

function evidenceFrom(citation?: ChatCitation): PdfEvidence | undefined {
  if (!citation?.resource_id) return undefined
  return {
    resource_id: citation.resource_id,
    element_id: citation.element_id,
    element_type: citation.element_type || '',
    page_number: citation.page_number,
    bbox: citation.bbox || [],
    caption: citation.label,
    context: citation.content || '',
  }
}

function evidenceFromElement(element?: Record<string, unknown>): PdfEvidence | undefined {
  if (!element?.resource_id) return undefined
  const bbox = Array.isArray(element.bbox) ? element.bbox.map(Number) : []
  return {
    resource_id: String(element.resource_id),
    element_id: String(element.element_id || ''),
    element_type: String(element.element_type || ''),
    page_number: Number(element.page_number || 1),
    bbox: bbox.length === 4 ? bbox : [],
    caption: String(element.caption || ''),
    context: String(element.context_text || element.text_content || ''),
  }
}

function readableError(error: unknown): string {
  return error instanceof Error ? error.message : '操作失败，请稍后重试。'
}

function normalizeSafeSummary(value: unknown): string | Record<string, unknown> | undefined {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object' && !Array.isArray(value)) return value as Record<string, unknown>
  return undefined
}

function normalizeActivity(event: AgentActivityEvent): AgentActivityEvent {
  const details = event.details || {}
  return {
    ...event,
    tool_call_id: String(details.tool_call_id || event.tool_call_id || ''),
    event_type: String(details.event_type || event.event_type || ''),
    tool_name: String(details.tool_name || event.tool_name || ''),
    label: String(details.label || event.label || event.message || ''),
    status: String(details.status || event.status || ''),
    safe_input_summary: normalizeSafeSummary(details.safe_input_summary ?? event.safe_input_summary),
    safe_output_summary: normalizeSafeSummary(details.safe_output_summary ?? event.safe_output_summary),
    duration_ms: Number(details.duration_ms ?? event.duration_ms ?? 0),
    checkpoint: String(details.checkpoint || event.checkpoint || ''),
    error: String(details.error || event.error || ''),
  }
}

const THREAD_PANEL_WIDTH = 232
const THREAD_PANEL_COLLAPSED_WIDTH = 52
const CHAT_MIN_WIDTH = 420
const INSPECTOR_MIN_WIDTH = 640
const INSPECTOR_RESIZER_WIDTH = 8

function inspectorBounds(workspaceWidth: number, threadOpen: boolean) {
  const threadWidth = threadOpen ? THREAD_PANEL_WIDTH : THREAD_PANEL_COLLAPSED_WIDTH
  const available = workspaceWidth - threadWidth - CHAT_MIN_WIDTH - INSPECTOR_RESIZER_WIDTH
  const maximum = Math.max(420, Math.min(workspaceWidth * .76, available))
  return { minimum: Math.min(INSPECTOR_MIN_WIDTH, maximum), maximum }
}

function activityDuration(durationMs: number) {
  if (!durationMs) return ''
  if (durationMs < 1000) return `${Math.max(1, Math.round(durationMs))}ms`
  if (durationMs < 60_000) return `${Math.round(durationMs / 100) / 10}s`
  const minutes = Math.floor(durationMs / 60_000)
  const seconds = Math.round((durationMs % 60_000) / 1000)
  return `${minutes}m ${seconds}s`
}

function activityStatusIcon(status?: string) {
  if (status === 'running') return <LoaderCircle size={15} className="spin" aria-label="执行中"/>
  if (status === 'completed') return <CheckCircle2 size={15} aria-label="已完成"/>
  if (status === 'waiting') return <CirclePause size={15} aria-label="等待确认"/>
  if (status === 'failed' || status === 'rejected') return <AlertCircle size={15} aria-label="失败"/>
  return <Circle size={12} aria-label="待执行"/>
}

function activitySummary(value: AgentActivityEvent['safe_output_summary']): string {
  if (!value) return ''
  if (typeof value === 'string') return value
  const entries = Object.entries(value).slice(0, 4)
  return entries.map(([key, item]) => `${key}: ${typeof item === 'object' ? JSON.stringify(item) : String(item)}`).join(' · ')
}

function renderChatInline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g
  const output: ReactNode[] = []
  let cursor = 0
  let tokenIndex = 0
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0
    if (start > cursor) output.push(text.slice(cursor, start))
    const token = match[0]
    const key = `${keyPrefix}-${tokenIndex++}`
    if (token.startsWith('**')) {
      output.push(<strong key={key}>{token.slice(2, -2)}</strong>)
    } else if (token.startsWith('`')) {
      output.push(<code key={key}>{token.slice(1, -1)}</code>)
    } else {
      const link = token.match(/^\[([^\]]+)\]\((https?:\/\/[^)]+)\)$/)
      output.push(link
        ? <a key={key} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>
        : token)
    }
    cursor = start + token.length
  }
  if (cursor < text.length) output.push(text.slice(cursor))
  return output
}

function markdownTableCells(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim())
}

function ChatMarkdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n?/g, '\n').split('\n')
  const blocks: ReactNode[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    const trimmed = line.trim()
    if (!trimmed) {
      index += 1
      continue
    }
    if (trimmed.startsWith('```')) {
      const language = trimmed.slice(3).trim()
      const code: string[] = []
      index += 1
      while (index < lines.length && !lines[index].trim().startsWith('```')) {
        code.push(lines[index])
        index += 1
      }
      index += 1
      blocks.push(<pre key={`code-${index}`} data-language={language || undefined}><code>{code.join('\n')}</code></pre>)
      continue
    }
    const heading = trimmed.match(/^(#{1,3})\s+(.+)$/)
    if (heading) {
      const level = heading[1].length
      const children = renderChatInline(heading[2], `heading-${index}`)
      blocks.push(level === 1
        ? <h2 key={`heading-${index}`}>{children}</h2>
        : level === 2
          ? <h3 key={`heading-${index}`}>{children}</h3>
          : <h4 key={`heading-${index}`}>{children}</h4>)
      index += 1
      continue
    }
    if (trimmed === '---' || trimmed === '***') {
      blocks.push(<hr key={`rule-${index}`}/>)
      index += 1
      continue
    }
    const nextLine = lines[index + 1]?.trim() || ''
    if (trimmed.includes('|') && /^\|?\s*:?-{3,}/.test(nextLine)) {
      const headers = markdownTableCells(trimmed)
      const rows: string[][] = []
      index += 2
      while (index < lines.length && lines[index].trim().includes('|')) {
        rows.push(markdownTableCells(lines[index]))
        index += 1
      }
      blocks.push(<div className="chat-markdown-table-wrap" key={`table-${index}`}><table>
        <thead><tr>{headers.map((cell, cellIndex) => <th key={`head-${cellIndex}`}>{renderChatInline(cell, `head-${cellIndex}`)}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={`row-${rowIndex}`}>{headers.map((_, cellIndex) => <td key={`cell-${cellIndex}`}>{renderChatInline(row[cellIndex] || '', `cell-${rowIndex}-${cellIndex}`)}</td>)}</tr>)}</tbody>
      </table></div>)
      continue
    }
    if (/^[-*]\s+/.test(trimmed)) {
      const items: string[] = []
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^[-*]\s+/, ''))
        index += 1
      }
      blocks.push(<ul key={`list-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderChatInline(item, `list-${index}-${itemIndex}`)}</li>)}</ul>)
      continue
    }
    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items: string[] = []
      while (index < lines.length && /^\d+[.)]\s+/.test(lines[index].trim())) {
        items.push(lines[index].trim().replace(/^\d+[.)]\s+/, ''))
        index += 1
      }
      blocks.push(<ol key={`ordered-${index}`}>{items.map((item, itemIndex) => <li key={itemIndex}>{renderChatInline(item, `ordered-${index}-${itemIndex}`)}</li>)}</ol>)
      continue
    }
    if (trimmed.startsWith('> ')) {
      const quotes: string[] = []
      while (index < lines.length && lines[index].trim().startsWith('> ')) {
        quotes.push(lines[index].trim().slice(2))
        index += 1
      }
      blocks.push(<blockquote key={`quote-${index}`}>{renderChatInline(quotes.join(' '), `quote-${index}`)}</blockquote>)
      continue
    }
    const paragraph: string[] = [trimmed]
    index += 1
    while (index < lines.length) {
      const candidate = lines[index].trim()
      const after = lines[index + 1]?.trim() || ''
      if (!candidate
        || /^(#{1,3})\s+/.test(candidate)
        || candidate.startsWith('```')
        || /^[-*]\s+/.test(candidate)
        || /^\d+[.)]\s+/.test(candidate)
        || candidate.startsWith('> ')
        || candidate === '---'
        || (candidate.includes('|') && /^\|?\s*:?-{3,}/.test(after))) break
      paragraph.push(candidate)
      index += 1
    }
    blocks.push(<p key={`paragraph-${index}`}>{renderChatInline(paragraph.join(' '), `paragraph-${index}`)}</p>)
  }
  return <div className="chat-markdown">{blocks}</div>
}

export function ChatPage() {
  const { projectId, articleId, setArticleId } = useAppStore()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const client = useQueryClient()
  const [activeThreadId, setActiveThreadId] = useState('')
  const [input, setInput] = useState('')
  const [activeRunId, setActiveRunId] = useState('')
  const [activeCitation, setActiveCitation] = useState<ChatCitation | undefined>()
  const [freeInput, setFreeInput] = useState('')
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [inspectorMode, setInspectorMode] = useState<'article' | 'evidence' | 'header' | 'resource' | 'workbench'>('article')
  const [inspectorArticleId, setInspectorArticleId] = useState('')
  const [inspectorEntity, setInspectorEntity] = useState<AgentEntity | undefined>()
  const [inspectorHeaderId, setInspectorHeaderId] = useState('')
  const [workbenchView, setWorkbenchView] = useState<WorkbenchView>(() => {
    const value = searchParams.get('view')
    return isWorkbenchView(value) ? value : 'resources'
  })
  const [workbenchStage, setWorkbenchStage] = useState<WorkbenchStage>(() => {
    const value = searchParams.get('stage')
    return isWorkbenchStage(value) ? value : 'table_standardize'
  })
  const [inspectorFocused, setInspectorFocused] = useState(false)
  const [liveActivities, setLiveActivities] = useState<AgentActivityEvent[]>([])
  const [activityExpanded, setActivityExpanded] = useState(false)
  const [decisionSelection, setDecisionSelection] = useState<string[]>([])
  const [threadSearch, setThreadSearch] = useState('')
  const [threadPanelOpen, setThreadPanelOpen] = useState(() => {
    const saved = window.localStorage.getItem('geochem.chat.thread-panel-open')
    return saved === null ? window.innerWidth > 1450 : saved === 'true'
  })
  const [inspectorWidth, setInspectorWidth] = useState(() => {
    const saved = Number(window.localStorage.getItem('geochem.chat.inspector-width'))
    return Number.isFinite(saved) && saved >= 420 ? saved : Math.round(window.innerWidth * .52)
  })
  const [chatError, setChatError] = useState('')
  const [chatNotice, setChatNotice] = useState('')
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([])
  const [entityDraftSelections, setEntityDraftSelections] = useState<Record<string, string>>({})
  const [mappingEdits, setMappingEdits] = useState<Record<string, { enabled: boolean; target: string }>>({})
  const [lastImportedHeader, setLastImportedHeader] = useState<HeaderConfig | undefined>()
  const fileInput = useRef<HTMLInputElement>(null)
  const headerFileInput = useRef<HTMLInputElement>(null)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const invalidThreadIds = useRef(new Set<string>())
  const invalidRunIds = useRef(new Set<string>())
  const requestedThreadId = searchParams.get('thread_id') || ''
  const requestedRunId = searchParams.get('run_id') || ''

  useEffect(() => { window.localStorage.setItem('geochem.chat.thread-panel-open', String(threadPanelOpen)) }, [threadPanelOpen])
  useEffect(() => { window.localStorage.setItem('geochem.chat.inspector-width', String(Math.round(inspectorWidth))) }, [inspectorWidth])
  useEffect(() => {
    const constrainInspector = () => {
      const width = workspaceRef.current?.getBoundingClientRect().width || 0
      if (!width || window.innerWidth <= 1180 || !inspectorOpen) return
      const bounds = inspectorBounds(width, threadPanelOpen)
      if (threadPanelOpen && bounds.maximum < INSPECTOR_MIN_WIDTH) {
        setThreadPanelOpen(false)
        return
      }
      const { minimum, maximum } = bounds
      setInspectorWidth((current) => Math.max(minimum, Math.min(current, maximum)))
    }
    constrainInspector()
    window.addEventListener('resize', constrainInspector)
    return () => window.removeEventListener('resize', constrainInspector)
  }, [inspectorOpen, threadPanelOpen])

  const startInspectorResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const resize = (moveEvent: PointerEvent) => {
      const rect = workspaceRef.current?.getBoundingClientRect()
      if (!rect?.width) return
      const requested = rect.right - moveEvent.clientX
      let threadOpen = threadPanelOpen
      let bounds = inspectorBounds(rect.width, threadOpen)
      if (threadOpen && (bounds.maximum < INSPECTOR_MIN_WIDTH || requested > bounds.maximum)) {
        threadOpen = false
        setThreadPanelOpen(false)
        bounds = inspectorBounds(rect.width, false)
      }
      setInspectorWidth(Math.max(bounds.minimum, Math.min(bounds.maximum, requested)))
    }
    const stop = () => {
      window.removeEventListener('pointermove', resize)
      window.removeEventListener('pointerup', stop)
      document.body.classList.remove('is-resizing-panels')
    }
    document.body.classList.add('is-resizing-panels')
    window.addEventListener('pointermove', resize)
    window.addEventListener('pointerup', stop)
  }

  const resetInspectorWidth = () => {
    const width = workspaceRef.current?.getBoundingClientRect().width || window.innerWidth
    let threadOpen = threadPanelOpen
    let bounds = inspectorBounds(width, threadOpen)
    if (threadOpen && bounds.maximum < INSPECTOR_MIN_WIDTH) {
      threadOpen = false
      setThreadPanelOpen(false)
      bounds = inspectorBounds(width, false)
    }
    setInspectorWidth(Math.max(bounds.minimum, Math.min(bounds.maximum, Math.round(width * .52))))
  }

  const threads = useQuery({ queryKey: ['chat-threads', projectId], queryFn: () => api.chatThreads(projectId), enabled: Boolean(projectId) })
  useEffect(() => {
    if (requestedThreadId && requestedThreadId !== activeThreadId && !invalidThreadIds.current.has(requestedThreadId)) {
      setActiveThreadId(requestedThreadId)
      setActiveRunId(invalidRunIds.current.has(requestedRunId) ? '' : requestedRunId)
      return
    }
    if (!activeThreadId && threads.data?.length) setActiveThreadId(threads.data[0].thread_id)
  }, [activeThreadId, requestedRunId, requestedThreadId, threads.data])
  const thread = useQuery({ queryKey: ['chat-thread', projectId, activeThreadId], queryFn: () => api.chatThread(projectId, activeThreadId), enabled: Boolean(projectId && activeThreadId), refetchInterval: activeRunId ? 900 : false, retry: false })
  const threadState = useQuery({ queryKey: ['chat-thread-state', projectId, activeThreadId], queryFn: () => api.chatThreadState(projectId, activeThreadId), enabled: Boolean(projectId && activeThreadId), refetchInterval: activeRunId ? 900 : false, retry: false })
  const activeRun = useQuery({ queryKey: ['agent-run', projectId, activeRunId], queryFn: () => api.agentRun(projectId, activeRunId), enabled: Boolean(projectId && activeRunId), refetchInterval: (query) => query.state.data?.status && ['completed', 'failed', 'cancelled'].includes(query.state.data.status) ? false : 750, retry: false })
  useEffect(() => {
    const status = (thread.error as (Error & { status?: number }) | null)?.status
    if (!thread.isError || !activeThreadId || ![400, 404].includes(status || 0)) return
    invalidThreadIds.current.add(activeThreadId)
    const fallback = threads.data?.find((item) => item.thread_id !== activeThreadId)
    const fallbackId = fallback?.thread_id || ''
    setActiveThreadId(fallbackId)
    setActiveRunId('')
    navigate(fallbackId ? `/?thread_id=${encodeURIComponent(fallbackId)}` : '/', { replace: true })
  }, [activeThreadId, navigate, thread.error, thread.isError, threads.data])
  useEffect(() => {
    const status = (activeRun.error as (Error & { status?: number }) | null)?.status
    if (!activeRun.isError || !activeRunId || thread.isError || ![400, 404].includes(status || 0)) return
    invalidRunIds.current.add(activeRunId)
    setActiveRunId('')
    navigate(activeThreadId ? `/?thread_id=${encodeURIComponent(activeThreadId)}` : '/', { replace: true })
  }, [activeRun.error, activeRun.isError, activeRunId, activeThreadId, navigate, thread.isError])
  useEffect(() => {
    const latest = threadState.data?.latest_run as { run_id?: string; status?: string } | null | undefined
    if (!activeRunId && latest?.run_id && ['pending', 'running', 'waiting_user', 'waiting_workbench'].includes(latest.status || '')) {
      setActiveRunId(latest.run_id)
    }
  }, [activeRunId, threadState.data?.latest_run])
  const conversationArticleId = activeRun.data?.article_id || threadState.data?.active_article_id || thread.data?.article_id || ''
  useEffect(() => {
    if (searchParams.get('panel') !== 'workbench') return
    const view = searchParams.get('view')
    const stage = searchParams.get('stage')
    const panelArticleId = searchParams.get('article_id') || conversationArticleId
    if (isWorkbenchView(view)) setWorkbenchView(view)
    if (isWorkbenchStage(stage)) setWorkbenchStage(stage)
    if (panelArticleId) setInspectorArticleId(panelArticleId)
    setInspectorMode('workbench')
    setInspectorOpen(true)
  }, [conversationArticleId, searchParams])
  useEffect(() => {
    if (!activeThreadId || thread.isLoading || threadState.isLoading) return
    if (articleId !== conversationArticleId) setArticleId(conversationArticleId)
  }, [activeThreadId, articleId, conversationArticleId, setArticleId, thread.isLoading, threadState.isLoading])
  const selection = (thread.data?.selection_context || {}) as Record<string, AgentEntity | string | undefined>
  const selectedArticle = selection.article as AgentEntity | undefined
  const selectedHeader = selection.header_config as AgentEntity | undefined
  const selectedResource = selection.resource as AgentEntity | undefined
  const viewerArticleId = inspectorArticleId || conversationArticleId
  const headerConfigs = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const resources = useQuery({ queryKey: ['resources', projectId, viewerArticleId], queryFn: () => api.resources(projectId, viewerArticleId), enabled: Boolean(projectId && viewerArticleId && inspectorOpen) })
  const targetHeaders = useQuery({ queryKey: ['target-headers', projectId, conversationArticleId], queryFn: () => api.articleTargetHeaders(projectId, conversationArticleId), enabled: Boolean(projectId && conversationArticleId) })

  const createThread = useMutation({
    mutationFn: () => api.createChatThread(projectId, '', '', 'workspace'),
    onSuccess: (created) => {
      setActiveThreadId(created.thread_id); setActiveRunId(''); setActiveCitation(undefined); setChatError(''); setChatNotice('')
      setInspectorOpen(false); setInspectorArticleId(''); setInspectorEntity(undefined); setInspectorHeaderId(''); setLiveActivities([])
      setArticleId('')
      navigate(`/?thread_id=${encodeURIComponent(created.thread_id)}`, { replace: true })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
    },
  })
  const send = useMutation({
    mutationFn: async (content: string) => {
      let threadId = activeThreadId
      const scopedArticleId = thread.data?.article_id || ''
      if (!threadId) threadId = (await api.createChatThread(projectId, '', '', 'workspace')).thread_id
      const result = await api.chatMessage(projectId, threadId, content, scopedArticleId)
      return { ...result, threadId }
    },
    onSuccess: ({ run_id, threadId, thread_id, status, message }) => {
      const resolvedThreadId = status === 'existing' ? thread_id || threadId : threadId
      setActiveThreadId(resolvedThreadId); setActiveRunId(run_id); setInput('')
      navigate(`/?thread_id=${encodeURIComponent(resolvedThreadId)}&run_id=${encodeURIComponent(run_id)}`, { replace: true })
      if (message) setChatNotice(message)
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, resolvedThreadId] })
    },
    onMutate: () => { setChatError(''); setChatNotice(''); setLastImportedHeader(undefined) },
    onError: (error) => setChatError(readableError(error)),
  })
  const startProcessing = useMutation({
    mutationFn: async () => {
      if (!activeThreadId) throw new Error('请先新建对话并选择文章。')
      const result = await api.chatAction(projectId, activeThreadId, 'start_processing')
      return result as { run_id?: string; thread_id?: string; status?: string; message?: string }
    },
    onSuccess: (result) => {
      const resolvedThreadId = result.thread_id || activeThreadId
      const runId = result.run_id || ''
      if (resolvedThreadId) setActiveThreadId(resolvedThreadId)
      if (runId) setActiveRunId(runId)
      if (result.message) setChatNotice(result.message)
      setChatError('')
      navigate(`/?thread_id=${encodeURIComponent(resolvedThreadId)}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`, { replace: true })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, resolvedThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, resolvedThreadId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const resume = useMutation({
    mutationFn: (response: Record<string, unknown>) => api.resumeAgentRun(projectId, activeRunId, response),
    onSuccess: (result) => {
      if (result.message) setChatNotice(result.message)
      else if (pending.kind === 'header_config_confirmation') setChatNotice('表头已确认，正在继续资源发现。')
      else if (pending.kind === 'resource_confirmation') setChatNotice(`已确认 ${selectedResourceIds.length} 个资源，后续抽取只会处理这些资源。`)
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const importPdf = useMutation({
    mutationFn: async (file: File) => {
      const threadId = (await api.createChatThread(projectId, '', 'PDF 文章处理', 'workspace')).thread_id
      const result = await api.uploadChatArticle(projectId, threadId, file)
      return { ...result, threadId }
    },
    onSuccess: (result) => {
      setActiveThreadId(result.threadId)
      setArticleId(result.article_id)
      setActiveRunId('')
      setChatNotice(result.message || 'PDF 已导入并选择为当前文章，请确认是否开始数据提取。')
      navigate(`/?thread_id=${encodeURIComponent(result.threadId)}`, { replace: true })
      client.invalidateQueries({ queryKey: ['articles', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, result.threadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, result.threadId] })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
    },
    onMutate: () => { setChatError(''); setChatNotice('') },
    onError: (error) => setChatError(readableError(error)),
  })
  const importHeader = useMutation({
    mutationFn: (file: File) => api.importHeaders(projectId, file),
    onSuccess: (result, file) => {
      const imported: HeaderConfig = {
        config_id: String(result.config_id || ''),
        name: file.name.replace(/\.[^.]+$/, ''),
        description: '',
        field_count: Number(result.field_count || 0),
        headers: Array.isArray(result.headers) ? result.headers as Record<string, string>[] : [],
      }
      setLastImportedHeader(imported)
      setActiveCitation(undefined)
      setInspectorMode('header')
      setInspectorHeaderId(imported.config_id)
      setInspectorOpen(true)
      setChatError('')
      setChatNotice(`已导入表头配置“${imported.name}”，共 ${imported.field_count} 个目标字段。`)
      client.invalidateQueries({ queryKey: ['headers', projectId] })
    },
    onMutate: () => { setChatError(''); setChatNotice('') },
    onError: (error) => setChatError(readableError(error)),
  })

  useEffect(() => {
    if (activeRun.data?.article_id && activeRun.data.article_id !== articleId) {
      setArticleId(activeRun.data.article_id)
      client.invalidateQueries({ queryKey: ['articles', projectId] })
    }
    if (activeRun.data?.status === 'completed' || activeRun.data?.status === 'failed') {
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['rag-status', projectId, articleId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
    }
  }, [activeRun.data?.status, activeThreadId, articleId, client, projectId])
  useEffect(() => {
    if (!projectId || !activeRunId) return
    return watchAgentRun(projectId, activeRunId, (event) => {
      const activity = normalizeActivity(event)
      if (activity.event_type || activity.tool_name || activity.label) {
        setLiveActivities((current) => {
          const eventId = String(activity.event_id || `${activity.tool_call_id || activity.tool_name}:${activity.status}:${activity.created_at || current.length}`)
          const next = current.filter((item) => String(item.event_id || '') !== eventId)
          return [...next, { ...activity, event_id: eventId }].slice(-80)
        })
      }
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
    }, () => {
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
    })
  }, [activeRunId, activeThreadId, client, projectId])

  const pending = activeRun.data?.pending_interrupt || {}
  const activityEvents = useMemo(() => {
    const stored = Array.isArray(activeRun.data?.activity_events)
      ? activeRun.data.activity_events.map(normalizeActivity)
      : []
    const byId = new Map<string, AgentActivityEvent>()
    ;[...stored, ...liveActivities].forEach((event, index) => {
      const id = String(event.event_id || `${event.tool_call_id || event.tool_name}:${event.status}:${event.created_at || index}`)
      byId.set(id, { ...event, event_id: id })
    })
    return foldActivityEvents([...byId.values()]).slice(-80)
  }, [activeRun.data?.activity_events, liveActivities])
  const toolActivityEvents = useMemo(
    () => activityEvents.filter((event) => event.event_type === 'tool'),
    [activityEvents],
  )
  const latestActiveActivity = useMemo(
    () => [...activityEvents].reverse().find((event) => event.status === 'running' || event.status === 'pending') || activityEvents.at(-1),
    [activityEvents],
  )
  const activityElapsed = useMemo(
    () => toolActivityEvents.reduce((total, event) => total + Number(event.duration_ms || 0), 0),
    [toolActivityEvents],
  )
  useEffect(() => {
    setLiveActivities([])
  }, [activeRunId])
  useEffect(() => {
    const status = activeRun.data?.status || ''
    if (status === 'running' || status === 'pending') setActivityExpanded(true)
    else if (['waiting_user', 'waiting_workbench', 'completed', 'failed', 'cancelled'].includes(status)) setActivityExpanded(false)
  }, [activeRun.data?.status, activeRunId])
  const latestAssistant = useMemo(() => [...(thread.data?.messages || [])].reverse().find((message) => message.role === 'assistant'), [thread.data?.messages])
  const latestUiPayload = (latestAssistant?.ui_payload || {}) as ChatUiPayload
  const headerInspectorContext = inspectorMode === 'header'
  const workbenchInspectorContext = inspectorMode === 'workbench'
  const latestCitations = useQuery({ queryKey: ['latest-chat-citations', projectId, latestAssistant?.message_id], queryFn: () => api.chatCitations(projectId, latestAssistant!.message_id), enabled: Boolean(projectId && latestAssistant?.message_id) })
  const evidenceCitations = useMemo(
    () => (latestCitations.data || []).filter((citation) => !['article', 'header_config', 'rule', 'export_job'].includes(citation.element_type || '')),
    [latestCitations.data],
  )
  const runActivity = (activeRun.data?.state_summary?.activity || {}) as { message?: string; progress?: number; details?: Record<string, unknown> }
  const runProgress = typeof runActivity.progress === 'number' ? Math.round(runActivity.progress * 100) : 0
  const articlePdfOnly = inspectorMode === 'article'
  const options = Array.isArray(pending.options) ? pending.options.map((option) => {
    if (typeof option === 'string') return option
    if (option && typeof option === 'object') return String((option as Record<string, unknown>).id || (option as Record<string, unknown>).value || '')
    return ''
  }).filter(Boolean) : []
  const pendingElements = Array.isArray(pending.elements) ? pending.elements as Record<string, unknown>[] : []
  const pendingMappings = Array.isArray((pending.mapping as Record<string, unknown> | undefined)?.items)
    ? ((pending.mapping as Record<string, unknown>).items as Record<string, unknown>[]) : []
  const sourceCandidates = Array.isArray(pending.candidates) ? pending.candidates as Record<string, unknown>[] : []
  const literatureResults = Array.isArray(pending.results) ? pending.results as Record<string, unknown>[] : []
  const pendingHeaderConfigs = Array.isArray(pending.header_configs) ? pending.header_configs as Record<string, unknown>[] : []
  const decisionOptions = useMemo<AgentDecisionOption[]>(() => {
    if (pending.kind === 'source_confirmation') {
      return sourceCandidates.map((candidate, index) => ({
        id: String(candidate.source_id || ''),
        label: String(candidate.title || candidate.doi || '公开 PDF'),
        description: `${String(candidate.journal || '来源待确认')}${candidate.year ? ` · ${candidate.year}` : ''} · ${candidate.access_status === 'public' ? '可公开下载' : String(candidate.validation_message || '需要手动上传')}`,
        recommended: index === 0 && candidate.access_status === 'public',
        disabled: !candidate.source_id,
      })).filter((option) => option.id)
    }
    if (pending.kind === 'literature_search_results') {
      return literatureResults.map((result, index) => ({
        id: String(result.literature_id || ''),
        label: String(result.title || '未命名文献'),
        description: `${Array.isArray(result.authors) ? (result.authors as string[]).slice(0, 2).join(', ') : ''}${result.year ? ` · ${result.year}` : ''}${result.venue ? ` · ${result.venue}` : ''}${result.doi ? ` · DOI ${result.doi}` : ''}`,
        recommended: index === 0 && Boolean(result.open_access),
        disabled: !result.literature_id,
      })).filter((option) => option.id)
    }
    if (pending.kind === 'header_config_confirmation') {
      const configs = [...pendingHeaderConfigs]
      if (lastImportedHeader && !configs.some((config) => String(config.config_id || '') === lastImportedHeader.config_id)) {
        configs.unshift({
          config_id: lastImportedHeader.config_id,
          name: lastImportedHeader.name,
          field_count: lastImportedHeader.field_count,
          preview: lastImportedHeader.headers.slice(0, 4).map((field) => String(field.display_header || field.name || '')),
          recently_imported: true,
        })
      }
      return configs.map((config, index) => ({
        id: String(config.config_id || ''),
        label: String(config.name || '表头配置'),
        description: `${String(config.field_count || 0)} 个目标字段${Array.isArray(config.preview) && config.preview.length ? ` · ${(config.preview as string[]).slice(0, 4).join('、')}` : ''}`,
        recommended: Boolean(config.recently_imported) || (configs.length === 1 && index === 0),
        disabled: !config.config_id,
      })).filter((option) => option.id)
    }
    const labels: Record<string, { label: string; description: string; recommended?: boolean }> = {
      accept_recommended: { label: '接受推荐资源', description: '使用当前阈值预选的资源继续抽取，可稍后在工作台调整。', recommended: true },
      custom_selection: { label: '自定义资源', description: '在下方逐项勾选本次参与抽取的表格、段落和图像。' },
      accept_suggestions: { label: '确认映射建议', description: '应用当前已勾选的字段映射并继续处理。', recommended: true },
      edit_rules: { label: '编辑映射规则', description: '先修改目标表头或忽略不适用的建议。' },
      open_review: { label: '进入人工审核', description: '候选结果已就绪，进入审核页面逐项确认。', recommended: true },
      reextract: { label: '重新抽取', description: '保留当前资源选择，重新执行候选数据抽取。' },
      adopt_workbench_changes: { label: '采用人工修改', description: '以工作台中的最新结果为准，从原检查点继续。', recommended: true },
      return_workbench: { label: '返回工作台调整', description: '继续在工作台修改，不推进当前任务。' },
      skip_rules: { label: '暂不保存规则', description: '本次继续处理，但不将映射沉淀到规则记忆。' },
      continue: { label: '继续', description: '继续执行当前文章处理流程。', recommended: true },
    }
    const candidates = options.filter((option) => option !== 'stop')
    const normalized = candidates.length ? candidates : ['continue']
    return normalized.map((id, index) => ({
      id,
      label: labels[id]?.label || id,
      description: labels[id]?.description || '执行此操作并继续当前任务。',
      recommended: labels[id]?.recommended || (!normalized.some((option) => labels[option]?.recommended) && index === 0),
    }))
  }, [lastImportedHeader, literatureResults, options, pending.kind, pendingHeaderConfigs, sourceCandidates])
  const decisionSelectionMode = pending.selection_mode === 'multiple' && !['resource_confirmation', 'mapping_confirmation'].includes(String(pending.kind || ''))
    ? 'multiple' as const
    : 'single' as const
  const activePendingElement = pendingElements.find((item) => String(item.element_id || '') === selectedResourceIds.at(-1)) || pendingElements[0]
  const activeInspectorEntity = inspectorEntity || selectedResource
  const inspectorEvidence = evidenceFrom(activeCitation)
    || evidenceFromElement(
      activeInspectorEntity?.metadata
        ? { ...activeInspectorEntity.metadata, element_id: activeInspectorEntity.element_id || activeInspectorEntity.entity_id, resource_id: activeInspectorEntity.resource_id }
        : activePendingElement,
    )
  const requestedHeaderId = inspectorHeaderId || selectedHeader?.entity_id || ''
  const selectedHeaderConfig = headerConfigs.data?.find((config) => config.config_id === requestedHeaderId)
    || (!requestedHeaderId ? lastImportedHeader : undefined)
  const selectedHeaderFields = selectedHeaderConfig?.headers
    || (Array.isArray(selectedHeader?.metadata?.headers) ? selectedHeader.metadata.headers as Record<string, string>[] : [])
  const selectedHeaderIsRecent = Boolean(
    selectedHeaderConfig?.config_id
    && selectedHeaderConfig.config_id === lastImportedHeader?.config_id,
  )
  const evidenceInspectorContext = inspectorMode === 'evidence' || inspectorMode === 'resource'
  const showPdfInspector = Boolean(viewerArticleId && !headerInspectorContext && !workbenchInspectorContext && (articlePdfOnly || evidenceInspectorContext))
  const inspectorTitle = headerInspectorContext
    ? '表头配置'
    : workbenchInspectorContext
      ? '精简工作台'
    : articlePdfOnly
    ? '文章原文'
    : inspectorMode === 'resource'
      ? '资源详情'
      : '证据与溯源'
  const actualModel = activeRun.data?.model_provider && activeRun.data?.model_name
    ? `${activeRun.data.model_provider} / ${activeRun.data.model_name}`
    : threadState.data?.actual_model?.provider && threadState.data?.actual_model?.model
      ? `${threadState.data.actual_model.provider} / ${threadState.data.actual_model.model}`
      : '等待首次模型调用'

  const handoffContext = (activeRun.data?.handoff_context || {}) as Record<string, unknown>
  const handoffView = isWorkbenchView(String(handoffContext.workbench_view || ''))
    ? String(handoffContext.workbench_view) as WorkbenchView
    : legacyWorkbenchView(handoffContext.workbench_step)
  const handoffStage = isWorkbenchStage(String(handoffContext.workbench_stage || ''))
    ? String(handoffContext.workbench_stage) as WorkbenchStage
    : 'table_standardize'
  const handoffPath = activeRunId
    ? `/workbench?${new URLSearchParams({
        agent_run_id: activeRunId,
        thread_id: activeThreadId,
        view: handoffView,
        step: String(handoffContext.workbench_step ?? 1),
        stage: handoffStage,
        return_to: String(handoffContext.return_path || `/?thread_id=${activeThreadId}&run_id=${activeRunId}`),
      }).toString()}`
    : '/workbench'

  const pendingResourceSignature = `${activeRunId}:${String(pending.kind || '')}:${Array.isArray(pending.recommended_element_ids) ? pending.recommended_element_ids.map(String).sort().join('|') : ''}`
  useEffect(() => {
    if (pending.kind !== 'resource_confirmation') return
    const recommended = Array.isArray(pending.recommended_element_ids) ? pending.recommended_element_ids.map(String) : []
    setSelectedResourceIds(recommended)
  }, [pendingResourceSignature])
  useEffect(() => {
    if (pending.kind !== 'mapping_confirmation') return
    setMappingEdits(Object.fromEntries(pendingMappings.map((item, index) => [
      `${String(item.element_id || '')}:${String(item.source_header || index)}`,
      { enabled: Boolean(item.suggested_target_header), target: String(item.suggested_target_header || '') },
    ])))
  }, [activeRunId, pending.kind])
  const decisionSignature = `${activeRunId}:${String(pending.kind || '')}:${decisionOptions.map((option) => option.id).join('|')}`
  useEffect(() => {
    const recommended = decisionOptions.find((option) => option.recommended && !option.disabled)
      || decisionOptions.find((option) => !option.disabled)
    setDecisionSelection(recommended ? [recommended.id] : [])
    setFreeInput('')
  }, [decisionSignature])

  const submit = (event: FormEvent) => { event.preventDefault(); if (input.trim()) send.mutate(input.trim()) }
  const respond = (action: string, customText = freeInput) => {
    const response: Record<string, unknown> = { action, free_text: customText }
    if (pending.kind === 'resource_confirmation' && action !== 'stop') response.element_ids = selectedResourceIds
    if (pending.kind === 'mapping_confirmation' && action !== 'skip_rules') {
      response.rules = pendingMappings.flatMap((item, index) => {
        const key = `${String(item.element_id || '')}:${String(item.source_header || index)}`
        const edit = mappingEdits[key]
        return edit?.enabled && edit.target ? [{ ...item, target_header: edit.target }] : []
      })
    }
    resume.mutate(response)
  }
  const submitDecision = () => {
    const selected = decisionSelection[0]
    if (!selected) return
    if (pending.kind === 'source_confirmation') {
      resume.mutate({ action: 'confirm_source', source_id: selected, free_text: freeInput })
      return
    }
    if (pending.kind === 'literature_search_results') {
      resume.mutate({ action: 'select_literature', literature_id: selected, free_text: freeInput })
      return
    }
    if (pending.kind === 'header_config_confirmation') {
      resume.mutate({ action: 'select_header_config', config_id: selected, free_text: freeInput })
      return
    }
    respond(selected, freeInput)
  }
  const handoff = useMutation({
    mutationFn: ({
      presentation,
      view,
      stage,
    }: {
      presentation: 'inline' | 'full'
      view: WorkbenchView
      stage?: WorkbenchStage
    }) => api.handoffAgentRun(projectId, activeRunId, presentation, view, stage),
    onSuccess: (result, variables) => {
      if (variables.presentation === 'inline') {
        openInlineWorkbench(
          variables.view,
          variables.stage || 'table_standardize',
          result.article_id || conversationArticleId,
        )
        client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
        return
      }
      navigate(result.workbench_path)
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const cancelRun = useMutation({
    mutationFn: () => api.cancelAgentRun(projectId, activeRunId),
    onSuccess: () => {
      setChatNotice('当前 Agent 任务已停止。现在可以删除该对话，或重新开始。')
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const deleteThread = useMutation({
    mutationFn: ({ threadId, cancelWaiting }: { threadId: string; cancelWaiting: boolean }) => api.deleteChatThread(projectId, threadId, cancelWaiting),
    onSuccess: (_result, variables) => {
      if (activeThreadId === variables.threadId) {
        setActiveThreadId('')
        setActiveRunId('')
        setActiveCitation(undefined)
        setArticleId('')
        navigate('/', { replace: true })
      }
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.removeQueries({ queryKey: ['chat-thread', projectId, variables.threadId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const selectEntity = useMutation({
    mutationFn: ({ entityType, entityId }: { entityType: string; entityId?: string }) => api.selectChatEntity(projectId, activeThreadId, entityType, entityId || ''),
    onSuccess: (result, variables) => {
      setArticleId(result.article_id || '')
      setActiveCitation(undefined)
      const selected = result.selection?.[variables.entityType] as AgentEntity | undefined
      setChatNotice(variables.entityType === 'clear'
        ? '已清除当前对话选择。'
        : `已选择${variables.entityType === 'article' ? '文章' : '对象'}：${selected?.title || variables.entityId || ''}`)
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.invalidateQueries({ queryKey: ['articles', projectId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const executeConfirmed = useMutation({
    mutationFn: ({ runId, payload }: { runId: string; payload: Record<string, unknown> }) =>
      api.chatAction(projectId, activeThreadId, 'execute_confirmed', runId, payload),
    onSuccess: () => {
      setChatNotice('已执行你明确确认的正式操作。')
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['trace-records', projectId] })
      client.invalidateQueries({ queryKey: ['standardized', projectId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const deleteConversation = (item: ChatThread) => {
    const waiting = ['waiting_user', 'waiting_workbench'].includes(item.active_run_status || '')
    if (waiting && !window.confirm('这会停止当前等待确认的任务，并永久删除该对话及其引用记录。是否继续？')) return
    deleteThread.mutate({ threadId: item.thread_id, cancelWaiting: waiting })
  }
  const choosePdf = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) importPdf.mutate(file)
    event.target.value = ''
  }
  const chooseHeaderFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) importHeader.mutate(file)
    event.target.value = ''
  }
  const filteredThreads = useMemo(() => {
    const query = threadSearch.trim().toLocaleLowerCase()
    if (!query) return threads.data || []
    return (threads.data || []).filter((item) => `${item.title || ''} ${item.thread_id}`.toLocaleLowerCase().includes(query))
  }, [threadSearch, threads.data])
  const latestActionableMessageId = threadState.data?.latest_actionable_message_id
    || thread.data?.latest_actionable_message_id
    || ''
  const openEntityPreview = (entity: AgentEntity) => {
    setInspectorEntity(entity)
    setActiveCitation(undefined)
    if (entity.entity_type === 'header_config') {
      setInspectorMode('header')
      setInspectorHeaderId(entity.entity_id)
    } else if (entity.entity_type === 'article') {
      setInspectorMode('article')
      setInspectorArticleId(entity.article_id || entity.entity_id)
    } else {
      setInspectorMode('resource')
      setInspectorArticleId(entity.article_id || conversationArticleId)
    }
    setInspectorFocused(false)
    setInspectorOpen(true)
  }
  const openSources = () => {
    const citation = activeCitation || evidenceCitations[0]
    setActiveCitation(citation)
    setInspectorEntity(undefined)
    setInspectorMode('evidence')
    setInspectorArticleId(citation?.article_id || conversationArticleId)
    setInspectorFocused(false)
    setInspectorOpen(true)
  }
  const openInlineWorkbench = (
    view: WorkbenchView,
    stage: WorkbenchStage = view === 'extract' ? 'table_standardize' : 'table_standardize',
    targetArticleId = conversationArticleId,
  ) => {
    setWorkbenchView(view)
    setWorkbenchStage(stage)
    setInspectorArticleId(targetArticleId)
    setInspectorEntity(undefined)
    setActiveCitation(undefined)
    setInspectorMode('workbench')
    setInspectorFocused(false)
    setInspectorOpen(true)
    const next = new URLSearchParams(searchParams)
    next.set('panel', 'workbench')
    next.set('view', view)
    next.set('stage', stage)
    if (targetArticleId) next.set('article_id', targetArticleId)
    else next.delete('article_id')
    navigate(`/?${next.toString()}`, { replace: true })
  }
  const closeInspector = () => {
    setInspectorOpen(false)
    setInspectorFocused(false)
    if (searchParams.get('panel')) {
      const next = new URLSearchParams(searchParams)
      next.delete('panel')
      next.delete('view')
      next.delete('stage')
      next.delete('article_id')
      navigate(`/?${next.toString()}`, { replace: true })
    }
  }

  const decisionDetails = <>
    {pending.kind === 'workbench_return_review' && Array.isArray((pending.diff as Record<string, unknown> | undefined)?.summary) && <div className="workbench-diff-summary"><strong>本次人工修改</strong>{((pending.diff as Record<string, unknown>).summary as string[]).map((line) => <span key={line}>{line}</span>)}</div>}
    {pending.kind === 'resource_confirmation' && decisionSelection.includes('custom_selection') && <div className="resource-checkpoint-picker agent-decision-resource-picker">
      <div className="resource-checkpoint-summary"><strong>已选 {selectedResourceIds.length} / {pendingElements.length}</strong><button className="secondary-button" onClick={() => setSelectedResourceIds(Array.isArray(pending.recommended_element_ids) ? pending.recommended_element_ids.map(String) : [])}>恢复推荐</button><button className="secondary-button" onClick={() => setSelectedResourceIds([])}>清空</button></div>
      {pendingElements.map((element) => {
        const id = String(element.element_id || '')
        const checked = selectedResourceIds.includes(id)
        return <label key={id}><input type="checkbox" checked={checked} onChange={() => setSelectedResourceIds((ids) => checked ? ids.filter((value) => value !== id) : [...ids, id])}/><span>{String(element.element_type || '资源')} · p{String(element.page_number || '?')}</span><small>{String(element.caption || element.text_content || '').slice(0, 76)}</small></label>
      })}
    </div>}
    {pending.kind === 'mapping_confirmation' && decisionSelection.includes('edit_rules') && <div className="mapping-picker agent-decision-mapping-picker">
      {pendingMappings.slice(0, 24).map((item, index) => {
        const key = `${String(item.element_id || '')}:${String(item.source_header || index)}`
        const edit = mappingEdits[key] || { enabled: false, target: '' }
        return <label key={key}><input type="checkbox" checked={edit.enabled} onChange={() => setMappingEdits((current) => ({ ...current, [key]: { ...edit, enabled: !edit.enabled } }))}/><strong>{String(item.source_header || '')}</strong><select value={edit.target} onChange={(event) => setMappingEdits((current) => ({ ...current, [key]: { ...edit, enabled: true, target: event.target.value } }))}><option value="">忽略</option>{targetHeaders.data?.map((header) => <option key={header.header_id} value={header.display_header}>{header.display_header}</option>)}</select></label>
      })}
    </div>}
  </>
  const showDecisionDetails = pending.kind === 'workbench_return_review'
    || (pending.kind === 'resource_confirmation' && decisionSelection.includes('custom_selection'))
    || (pending.kind === 'mapping_confirmation' && decisionSelection.includes('edit_rules'))
  const decisionSecondaryActions = <>
    {pending.kind === 'header_config_confirmation' && <><button type="button" onClick={() => headerFileInput.current?.click()} disabled={importHeader.isPending}><FileUp size={15}/>导入新表头</button><button type="button" onClick={() => navigate('/headers')}>表头管理</button></>}
    {['resource_confirmation', 'mapping_confirmation', 'quality_confirmation'].includes(String(pending.kind || '')) && <><button type="button" onClick={() => {
      const target = checkpointWorkbenchTarget(String(pending.kind || ''))
      handoff.mutate({ presentation: 'inline', ...target })
    }} disabled={handoff.isPending}>右侧精简工作台</button><button type="button" onClick={() => {
      const target = checkpointWorkbenchTarget(String(pending.kind || ''))
      handoff.mutate({ presentation: 'full', ...target })
    }} disabled={handoff.isPending}>完整工作台</button></>}
  </>
  const decisionPrimaryLabel = pending.primary_action_label
    ? String(pending.primary_action_label)
    : pending.kind === 'source_confirmation'
      ? '确认来源'
      : pending.kind === 'literature_search_results'
        ? '选择文献'
        : pending.kind === 'header_config_confirmation'
          ? '确认表头'
          : pending.kind === 'resource_confirmation'
            ? '继续抽取'
            : pending.kind === 'mapping_confirmation'
              ? '确认并继续'
              : '继续'

  const workspaceStyle = { '--chat-inspector-width': `${inspectorWidth}px` } as CSSProperties

  return <div className={`page chat-page ${inspectorOpen ? '' : 'inspector-closed'} ${threadPanelOpen ? '' : 'threads-closed'} ${inspectorFocused ? 'inspector-focused' : ''}`}>
    <input ref={headerFileInput} className="visually-hidden" type="file" accept=".csv,.xlsx,.xls" onChange={chooseHeaderFile} />
    <input ref={fileInput} className="visually-hidden" type="file" accept=".pdf" onChange={choosePdf} />
    <div ref={workspaceRef} className="chat-workspace chat-resizable-workspace" style={workspaceStyle}>
      <aside className={`panel chat-threads ${threadPanelOpen ? '' : 'collapsed'}`}>
        <div className="chat-sidebar-heading">
          {threadPanelOpen && <><Bot size={19}/><h1>对话助手</h1></>}
          <button className="icon-button chat-thread-panel-toggle" title={threadPanelOpen ? '收起对话列表' : '展开对话列表'} aria-label={threadPanelOpen ? '收起对话列表' : '展开对话列表'} onClick={() => setThreadPanelOpen((value) => !value)}>{threadPanelOpen ? <PanelLeftClose size={17}/> : <PanelLeftOpen size={18}/>}</button>
        </div>
        {threadPanelOpen && <>
        <button className="primary-button wide" onClick={() => createThread.mutate()} disabled={!projectId || createThread.isPending}><MessageSquarePlus size={17}/> 新建对话</button>
        <label className="chat-thread-search"><Search size={15}/><input value={threadSearch} onChange={(event) => setThreadSearch(event.target.value)} placeholder="搜索对话" /></label>
        <div className="chat-thread-list">
          {filteredThreads.map((item: ChatThread) => <div key={item.thread_id} className="chat-thread-row">
            <button onClick={() => {
              setActiveThreadId(item.thread_id); setActiveRunId(item.active_run_id || ''); setActiveCitation(undefined)
              setInspectorOpen(false); setInspectorEntity(undefined); setInspectorArticleId(''); setInspectorHeaderId('')
              navigate(`/?thread_id=${encodeURIComponent(item.thread_id)}${item.active_run_id ? `&run_id=${encodeURIComponent(item.active_run_id)}` : ''}`, { replace: true })
            }} className={item.thread_id === activeThreadId ? 'chat-thread active' : 'chat-thread'}>
              <strong>{item.title || '文章处理对话'}</strong><small>{item.active_run_status === 'waiting_user' ? '等待确认' : item.active_run_status === 'waiting_workbench' ? '工作台处理中' : item.active_run_status ? '处理中' : item.scope === 'workspace' ? '工作区' : '当前文章'} · {item.updated_at?.slice(0, 16).replace('T', ' ')}</small>
            </button>
            <button className="icon-button chat-thread-delete" title={item.active_run_status === 'pending' || item.active_run_status === 'running' ? '任务执行中，暂不能删除' : item.active_run_status ? '停止任务并删除对话' : '删除对话'} aria-label={`删除对话 ${item.title || item.thread_id}`} onClick={(event) => { event.stopPropagation(); deleteConversation(item) }} disabled={deleteThread.isPending || item.active_run_status === 'pending' || item.active_run_status === 'running'}><Trash2 size={15}/></button>
          </div>)}
          {!filteredThreads.length && <div className="empty-state compact"><Bot size={28}/><p>{threadSearch ? '没有匹配的对话。' : '从一个问题开始。'}</p></div>}
        </div>
        </>}
      </aside>
      <section className="panel chat-main">
        <div className="chat-main-toolbar">
          <div className="chat-context-summary" aria-label="当前对话上下文">
            <span className="context-label">当前文章</span>
            {selectedArticle ? <button className="context-chip article" title={`查看 ${selectedArticle.title}`} onClick={() => openEntityPreview(selectedArticle)}>{selectedArticle.title}</button> : <em>未选择</em>}
            {selectedHeader && <button className="context-chip" title={`查看表头 ${selectedHeader.title}`} onClick={() => openEntityPreview(selectedHeader)}>表头：{selectedHeader.title}</button>}
            {selectedResource && <button className="context-chip" title={`查看资源 ${selectedResource.title}`} onClick={() => openEntityPreview(selectedResource)}>资源：{selectedResource.title}</button>}
            {selectedArticle && <button className="context-start-button" onClick={() => send.mutate('开始数据提取')} disabled={send.isPending}>开始处理</button>}
            {(selectedArticle || selectedHeader || selectedResource) && <button className="context-clear-button" onClick={() => selectEntity.mutate({ entityType: 'clear' })}>清除</button>}
            <span className="chat-model-status" title={actualModel}>模型：{actualModel}</span>
          </div>
          {!inspectorOpen && <div className="chat-panel-launchers">
            {selectedArticle && <button className="icon-button" title="查看当前文章 PDF" aria-label="查看当前文章 PDF" onClick={() => openEntityPreview(selectedArticle)}><FileSearch size={17}/></button>}
            <button className="icon-button" title="打开精简工作台" aria-label="打开精简工作台" onClick={() => openInlineWorkbench('resources')}><PanelRightOpen size={18}/></button>
          </div>}
        </div>
        <div className="chat-transcript">
          {!thread.data?.messages?.length && !activeRun.data?.status && <section className="chat-welcome" aria-label="对话快捷开始">
            <div className="chat-welcome-heading"><Bot size={24}/><div><strong>你好，我是科研数据抽取助手</strong><span><b>从一项科研数据任务开始</b>。我会调用项目工具执行任务，并在需要确认时停下来。</span></div></div>
            <div className="chat-welcome-actions">
              <button onClick={() => setInput('搜索最新的地球化学数据文章')}><FileSearch size={18}/><span><strong>检索或导入文献</strong><small>查找公开论文、输入 DOI，或上传 PDF</small></span></button>
              <button onClick={() => setInput('查询当前工作区已导入的文献')}><Sparkles size={18}/><span><strong>处理已导入文献</strong><small>选择文章并启动资源发现与数据抽取</small></span></button>
              <button onClick={() => setInput('查询当前工作区的标准化数据及其溯源')}><FileSearch size={18}/><span><strong>查询数据与溯源</strong><small>定位样品、字段、原始证据和计算过程</small></span></button>
            </div>
            <button className="chat-welcome-upload" onClick={() => fileInput.current?.click()} disabled={importPdf.isPending}><FileUp size={16}/>{importPdf.isPending ? '正在导入 PDF…' : '上传本地 PDF'}</button>
          </section>}
          {(thread.data?.messages || []).map((message) => {
            const payload = (message.ui_payload || {}) as ChatUiPayload
            const actionable = [
              'entity_cards',
              'selected_entity',
              'article_selection_confirmation',
              'header_selection_confirmation',
              'literature_cards',
              'workbench_link',
              'navigation_action',
              'critical_action',
              'header_import_action',
            ].includes(String(payload.kind || ''))
            const isLatestAction = message.message_id === latestActionableMessageId
              || (!latestActionableMessageId && message.message_id === latestAssistant?.message_id)
            const actionPending = actionable
              && isLatestAction
              && !['selected', 'superseded', 'expired'].includes(message.action_state || '')
            const actionStateLabel = message.action_state === 'selected'
              ? '已完成'
              : message.action_state === 'expired'
                ? '已过期'
                : actionable && !actionPending
                  ? '已被后续操作替代'
                  : ''
            return <article key={message.message_id} className={`chat-bubble ${message.role}`}>
              <div className="chat-message-meta"><strong>{message.role === 'assistant' ? 'GeoChem Agent' : '你'}</strong>
              {message.role === 'assistant' && message.model_provider && message.model_name && <small className="message-model">{message.model_provider} / {message.model_name}</small>}
              {actionStateLabel && <small className="message-action-state">{actionStateLabel}</small>}</div>
              {message.role === 'assistant'
                ? !['entity_cards', 'selected_entity', 'article_selection_confirmation', 'header_selection_confirmation'].includes(String(payload.kind || '')) && <ChatMarkdown content={message.content}/>
                : <p>{message.content}</p>}
              {payload.kind === 'entity_cards' && Array.isArray(payload.entities) && (() => {
                const selectedEntity = payload.entities.find((entity) => entity.entity_type === 'article'
                  ? selectedArticle?.entity_id === entity.entity_id
                  : entity.entity_type === 'header_config'
                    ? selectedHeader?.entity_id === entity.entity_id
                    : selectedResource?.entity_id === entity.entity_id)
                if (!actionPending) {
                  return <div className="chat-selection-summary resolved">
                    <CheckCircle2 size={16}/>
                    <span>{selectedEntity
                      ? <>已选择{selectedEntity.entity_type === 'article' ? '文章' : selectedEntity.entity_type === 'header_config' ? '表头' : '对象'}：<strong>{selectedEntity.title}</strong></>
                      : actionStateLabel || '选择已结束'}</span>
                    {selectedEntity && ['article', 'header_config', 'resource'].includes(selectedEntity.entity_type) && <button title="查看，不改变当前选择" onClick={() => openEntityPreview(selectedEntity)}><Eye size={15}/>查看</button>}
                  </div>
                }
                const draftId = entityDraftSelections[message.message_id] || selectedEntity?.entity_id || ''
                const draft = payload.entities.find((entity) => entity.entity_id === draftId)
                const subject = payload.entities[0]?.entity_type === 'article' ? '文章' : payload.entities[0]?.entity_type === 'header_config' ? '表头' : '对象'
                return <section className="chat-entity-decision">
                  <header><strong>{`选择${subject}`}</strong><small>查看不会改变当前选择。选中一项后点击继续。</small></header>
                  <div className="chat-entity-choice-list" role="radiogroup" aria-label={`选择${subject}`}>{payload.entities.map((entity) => {
                const selected = entity.entity_type === 'article'
                  ? selectedArticle?.entity_id === entity.entity_id
                  : entity.entity_type === 'header_config'
                    ? selectedHeader?.entity_id === entity.entity_id
                    : selectedResource?.entity_id === entity.entity_id
                const viewable = ['article', 'header_config', 'resource'].includes(entity.entity_type)
                const checked = draftId === entity.entity_id
                return <div
                  className={`chat-entity-choice-row ${checked ? 'selected' : ''} ${selected ? 'bound' : ''}`}
                  key={`${entity.entity_type}:${entity.entity_id}`}
                  role="radio"
                  aria-checked={checked}
                  tabIndex={actionPending ? 0 : -1}
                  onClick={() => actionPending && setEntityDraftSelections((current) => ({ ...current, [message.message_id]: entity.entity_id }))}
                  onKeyDown={(event) => {
                    if (actionPending && (event.key === 'Enter' || event.key === ' ')) {
                      event.preventDefault()
                      setEntityDraftSelections((current) => ({ ...current, [message.message_id]: entity.entity_id }))
                    }
                  }}
                >
                  <span className="chat-entity-radio" aria-hidden="true"/>
                  <span className="chat-entity-choice-copy"><strong title={entity.title}>{entity.title}</strong><small>{entity.subtitle || entity.entity_type}</small></span>
                  {selected && <em>已绑定</em>}
                  {viewable && <button className="icon-button" title="查看，不改变当前选择" aria-label={`查看 ${entity.title}`} onClick={(event) => { event.stopPropagation(); openEntityPreview(entity) }}><Eye size={15}/></button>}
                </div>
              })}</div>
                  {actionPending && <footer><button
                    className="primary-button"
                    disabled={!draft || selectEntity.isPending}
                    onClick={() => draft && selectEntity.mutate({ entityType: draft.entity_type, entityId: draft.entity_id })}
                  >{selectEntity.isPending ? '正在选择…' : '继续'}</button></footer>}
                </section>
              })()}
              {(payload.kind === 'selected_entity' || payload.kind === 'article_selection_confirmation' || payload.kind === 'header_selection_confirmation') && Array.isArray(payload.entities) && <div className="chat-selection-summary"><CheckCircle2 size={16}/><span>已选择{payload.entities[0]?.entity_type === 'article' ? '文章' : payload.entities[0]?.entity_type === 'header_config' ? '表头' : '对象'}：<strong>{payload.entities[0]?.title}</strong></span>{payload.entities[0] && ['article', 'header_config', 'resource'].includes(payload.entities[0].entity_type || '') && <button title="查看" onClick={() => openEntityPreview(payload.entities![0])}><Eye size={15}/>查看</button>}{actionPending && ['article', 'header_config'].includes(payload.entities[0]?.entity_type || '') && <button className="primary-button" onClick={() => startProcessing.mutate()} disabled={startProcessing.isPending}>{startProcessing.isPending ? '正在启动…' : payload.entities[0]?.entity_type === 'header_config' ? '继续' : '开始数据提取'}</button>}</div>}
              {payload.kind === 'literature_cards' && Array.isArray(payload.results) && <div className="chat-entity-cards literature-cards">{payload.results.map((result) => {
                const source = String(result.doi || result.landing_url || '')
                return <article className={`chat-entity-card ${actionPending ? '' : 'inactive'}`} key={String(result.result_id || source)}><strong>{String(result.title || '未命名文献')}</strong><small>{String(result.year || '年份未知')} · {String(result.doi || result.venue || '未登记 DOI')}</small><button className="secondary-button select-entity-button" disabled={!actionPending || !source || send.isPending} onClick={() => send.mutate(`我要导入这篇文章：${source}`)}>{actionPending ? '检查并导入' : '已失效'}</button></article>
              })}</div>}
              {payload.kind === 'workbench_link' && <div className="chat-selected-card"><span>{payload.reason || '可以在右侧精简工作台处理；复杂操作再进入完整工作台。'}</span>{actionPending && <div className="chat-inline-actions"><button className="primary-button" onClick={() => openInlineWorkbench(
                payload.workbench_view || legacyWorkbenchView(payload.workbench_step),
                isWorkbenchStage(String(payload.workbench_stage || '')) ? String(payload.workbench_stage) as WorkbenchStage : 'table_standardize',
                payload.article_id || conversationArticleId,
              )}>右侧处理</button><button className="secondary-button" onClick={() => navigate(`/workbench?${new URLSearchParams({ view: payload.workbench_view || legacyWorkbenchView(payload.workbench_step), step: String(payload.workbench_step ?? 1), stage: String(payload.workbench_stage || ''), article_id: payload.article_id || conversationArticleId, return_to: `/?thread_id=${activeThreadId}` }).toString()}`)}>完整工作台</button></div>}</div>}
              {payload.kind === 'navigation_action' && <div className="chat-selected-card"><span>需要在对应的治理页面继续。</span>{actionPending && <button className="primary-button" onClick={() => navigate(payload.path || '/review')}>打开页面</button>}</div>}
              {payload.kind === 'critical_action' && <div className="chat-critical-card"><div><strong>{payload.operation === 'export' ? '正式导出' : '生成标准化记录'}</strong><span>此操作只会在你点击确认后执行，并写入 Agent 工具审计。</span></div>{actionPending && <button className="primary-button" disabled={!message.agent_run_id || executeConfirmed.isPending} onClick={() => executeConfirmed.mutate({ runId: message.agent_run_id || '', payload: { operation: payload.operation || '', format: payload.format || 'csv', output_dir: payload.output_dir || '' } })}>{payload.operation === 'export' ? '确认导出' : '确认标准化'}</button>}</div>}
              {payload.kind === 'header_import_action' && <div className="chat-header-import-action"><span><TableProperties size={17}/><span><strong>导入表头配置</strong><small>支持只有表头行的 CSV、XLSX 和 XLS 文件，导入后可继续编辑和复用。</small></span></span>{actionPending && <button className="primary-button" onClick={() => headerFileInput.current?.click()} disabled={importHeader.isPending}>{importHeader.isPending ? '正在导入…' : '选择表头文件'}</button>}</div>}
              {(payload.kind === 'model_unavailable' || payload.kind === 'model_configuration_required') && <div className="chat-selected-card"><span>{payload.kind === 'model_configuration_required' ? '尚未配置 API Key，配置完成后即可继续使用对话助手。' : '对话模型未完成本次请求。'}</span><button className="primary-button" onClick={() => navigate(payload.settings_path || '/settings')}>前往模型配置</button></div>}
              {message.message_id === latestAssistant?.message_id && evidenceCitations.length > 0 && <div className="chat-message-actions"><button onClick={openSources}><FileSearch size={15}/> Sources · {evidenceCitations.length}</button></div>}
            </article>
          })}
          {activityEvents.length > 0 && <details className="agent-activity" open={activityExpanded} onToggle={(event) => setActivityExpanded(event.currentTarget.open)}>
            <summary><Wrench size={15}/><span>{['running', 'pending'].includes(activeRun.data?.status || '') ? `正在工作 · ${latestActiveActivity?.label || agentNodeLabel(activeRun.data?.current_node)}` : `工作过程${activityElapsed ? ` · ${activityDuration(activityElapsed)}` : ''}`}</span><small>{toolActivityEvents.length} 个工具</small></summary>
            <div className="agent-activity-list">{activityEvents.map((event) => <div className={`agent-activity-row ${event.status || 'pending'}`} key={String(event.tool_call_id || event.event_id)}>
              <span className="activity-status-icon">{activityStatusIcon(event.status)}</span>
              <div><strong>{event.label || event.tool_name || 'Agent 活动'}</strong><small>{event.tool_name && event.label !== event.tool_name ? event.tool_name : event.checkpoint || event.event_type}</small>{activitySummary(event.safe_output_summary) && <p>{activitySummary(event.safe_output_summary)}</p>}{event.error && <p className="activity-error">{event.error}</p>}</div>
              <time>{activityDuration(Number(event.duration_ms || 0)) || (event.status === 'waiting' ? '等待确认' : event.status)}</time>
            </div>)}</div>
          </details>}
          {(activeRun.data?.status === 'running' || activeRun.data?.status === 'pending') && <div className="agent-progress"><LoaderCircle size={16} className="spin"/><span>正在执行：{agentNodeLabel(activeRun.data.current_node)}{runActivity.message ? ` · ${runActivity.message}` : ''}{runProgress ? ` · ${runProgress}%` : ''}</span><button className="secondary-button agent-cancel" onClick={() => cancelRun.mutate()} disabled={cancelRun.isPending}>停止任务</button></div>}
          {activeRun.data?.status === 'failed' && <div className="agent-error">任务失败：{activeRun.data.error_message}</div>}
          {activeRun.data?.status === 'waiting_workbench' && <div className="agent-notice workbench-waiting"><span>任务正在等待人工处理。可以留在对话右侧完成，也可以进入完整工作台。</span><div className="chat-inline-actions"><button className="primary-button" onClick={() => openInlineWorkbench(handoffView, handoffStage, activeRun.data?.article_id || conversationArticleId)}>右侧继续</button><button className="secondary-button" onClick={() => navigate(handoffPath)}>完整工作台</button></div></div>}
          {chatError && <div className="agent-error">{chatError}</div>}
          {chatNotice && <div className="agent-notice">{chatNotice}</div>}
          {activeRun.data?.status === 'waiting_user' && <AgentDecisionPrompt
            question={String(pending.title || '需要你的确认')}
            description={String(pending.recommendation || pending.message || '选择下一步操作，Agent 会在确认后继续。')}
            options={decisionOptions}
            selectionMode={decisionSelectionMode}
            selectedIds={decisionSelection}
            onSelectedIdsChange={setDecisionSelection}
            primaryActionLabel={decisionPrimaryLabel}
            allowCustomInput={Boolean(pending.allow_custom_input ?? true)}
            customInput={freeInput}
            onCustomInputChange={setFreeInput}
            busy={resume.isPending || handoff.isPending}
            onContinue={submitDecision}
            onStop={() => respond('stop')}
            details={showDecisionDetails ? decisionDetails : undefined}
            secondaryActions={decisionSecondaryActions}
          />}
        </div>
        <div className="chat-suggestions">{QUICK_QUESTIONS.map((question) => <button key={question} onClick={() => setInput(question)}>{question}</button>)}</div>
        <form className="chat-composer" onSubmit={submit}><button className="icon-button chat-attach-button" type="button" title="上传 PDF" onClick={() => fileInput.current?.click()} disabled={importPdf.isPending}><FileUp size={18}/></button><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={conversationArticleId ? '例如：开始处理这篇文章，或询问一个数据来源…' : '例如：搜索地化数据文献，或输入 DOI: 10.xxxx/xxxx'} disabled={send.isPending}/><button className="primary-button" type="submit" disabled={!input.trim() || send.isPending}><Send size={18}/></button></form>
      </section>
      {inspectorOpen && <button className="chat-inspector-resizer" aria-label="调整对话与资源查看宽度" title="拖拽调整宽度，双击恢复默认" onPointerDown={startInspectorResize} onDoubleClick={resetInspectorWidth}><span/></button>}
      {inspectorOpen && <aside className={`panel chat-inspector ${inspectorFocused ? 'focused' : ''}`}>
        <div className="inspector-heading">{headerInspectorContext ? <TableProperties size={18}/> : workbenchInspectorContext ? <Wrench size={18}/> : <FileSearch size={18}/>}<strong>{inspectorTitle}</strong><span className="inspector-heading-actions"><button className="icon-button" title={inspectorFocused ? '退出聚焦' : '聚焦查看'} aria-label={inspectorFocused ? '退出聚焦' : '聚焦查看'} onClick={() => setInspectorFocused((value) => !value)}>{inspectorFocused ? <Minimize2 size={17}/> : <Maximize2 size={17}/>}</button><button className="icon-button" title="关闭查看面板" aria-label="关闭查看面板" onClick={closeInspector}><PanelRightClose size={18}/></button></span></div>
        <div className="chat-inspector-body">
          {workbenchInspectorContext && <EmbeddedWorkbench
            projectId={projectId}
            articleId={inspectorArticleId || conversationArticleId}
            initialView={workbenchView}
            initialStage={workbenchStage}
            agentRunId={activeRun.data?.status === 'waiting_workbench' ? activeRunId : ''}
            pendingSelectedElementIds={pending.kind === 'resource_confirmation' && (inspectorArticleId || conversationArticleId) === conversationArticleId ? selectedResourceIds : undefined}
            onPendingSelectionChange={pending.kind === 'resource_confirmation' ? setSelectedResourceIds : undefined}
            selectionPersistenceMode={pending.kind === 'resource_confirmation' ? 'deferred' : 'immediate'}
            onSaved={(message) => {
              setChatNotice(message)
              closeInspector()
              client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
              client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
            }}
          />}
          {headerInspectorContext && <section className="chat-header-inspector">
            <div className="chat-header-import-panel"><div><strong>表头配置</strong><span>从 CSV、XLSX 或 XLS 导入，可独立编辑并复用于多篇文章。</span></div><button className="primary-button" onClick={() => headerFileInput.current?.click()} disabled={importHeader.isPending}><FileUp size={15}/>{importHeader.isPending ? '正在导入…' : '导入表头'}</button></div>
            {selectedHeaderConfig && <div className="source-preview-card header-config-detail"><div className="header-config-title"><strong>{selectedHeaderConfig.name}</strong><small>{selectedHeaderConfig.field_count} 个目标字段{selectedHeaderIsRecent ? ' · 刚刚导入' : ''}</small></div>{selectedHeaderFields.length > 0 && <div className="chat-header-preview">{selectedHeaderFields.slice(0, 40).map((field, index) => <span key={`${String(field.display_header || field.name || index)}`}>{String(field.display_header || field.name || field['字段名'] || '')}</span>)}</div>}<div className="header-config-actions"><button className="secondary-button" onClick={() => navigate('/headers')}>打开表头管理</button>{selectedHeaderIsRecent && activeThreadId && <button className="primary-button" onClick={() => selectEntity.mutate({ entityType: 'header_config', entityId: selectedHeaderConfig.config_id })} disabled={selectEntity.isPending}>选择此表头</button>}</div></div>}
            {!selectedHeaderConfig && <div className="chat-header-config-list">{(headerConfigs.data || []).map((config) => <button key={config.config_id} onClick={() => selectEntity.mutate({ entityType: 'header_config', entityId: config.config_id })}><span><strong>{config.name}</strong><small>{config.field_count} 个目标字段</small></span><ChevronRight size={16}/></button>)}{!headerConfigs.data?.length && <div className="empty-state compact"><TableProperties size={28}/><p>还没有表头配置，可以直接导入一份。</p></div>}</div>}
          </section>}
          {!headerInspectorContext && inspectorMode === 'resource' && activeInspectorEntity && <div className="source-preview-card"><strong>{activeInspectorEntity.title}</strong><small>{activeInspectorEntity.subtitle}</small></div>}
          {inspectorMode === 'evidence' && <div className="citation-list">{evidenceCitations.map((citation) => <button key={citation.citation_id || citation.document_id} className={(activeCitation?.document_id === citation.document_id) ? 'citation-card active' : 'citation-card'} onClick={() => { setActiveCitation(citation); if (citation.article_id) setInspectorArticleId(citation.article_id) }}><strong>{citation.label || '检索证据'}</strong><small>{citation.element_type || '记录'} {citation.page_number ? `· p${citation.page_number}` : ''}</small></button>)}{!evidenceCitations.length && <div className="empty-state compact"><FileSearch size={28}/><p>当前回答没有可定位的文章证据。</p></div>}</div>}
          {evidenceInspectorContext && activeCitation && <div className="citation-detail"><p>{activeCitation.content}</p><div className="source-chain">{activeCitation.element_type || '数据'} <ChevronRight size={14}/> {activeCitation.label || '来源'} {activeCitation.page_number ? <><ChevronRight size={14}/> p{activeCitation.page_number}</> : null}</div></div>}
          {showPdfInspector && <div className="chat-pdf"><PdfEvidenceViewer key={viewerArticleId} projectId={projectId} articleScopeKey={viewerArticleId} resources={resources.data || []} evidence={inspectorEvidence} /></div>}
          {!headerInspectorContext && !workbenchInspectorContext && !showPdfInspector && !evidenceInspectorContext && <div className="empty-state chat-context-empty"><Bot size={30}/><p>这里会随对话展示表头、文章、资源或数据证据。</p></div>}
        </div>
      </aside>}
    </div>
  </div>
}
