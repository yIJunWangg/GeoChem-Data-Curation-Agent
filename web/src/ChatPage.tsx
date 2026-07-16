import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Bot, ChevronRight, FileSearch, FileUp, Link2, LoaderCircle, MessageSquarePlus, PanelRightClose, Send, Sparkles, Trash2 } from 'lucide-react'
import { api, watchAgentRun } from './api'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { useAppStore } from './store'
import type { ChatCitation, ChatThread } from './types'

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

const QUICK_QUESTIONS = [
  '这篇文章发现了哪些可抽取资源？',
  'HDP-B1 的 SiO2 数据来自哪里？',
  '当前文章有多少已标准化样品？',
  '哪些字段仍然证据不足？',
]

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
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [source, setSource] = useState('')
  const [chatError, setChatError] = useState('')
  const [chatNotice, setChatNotice] = useState('')
  const [selectedResourceIds, setSelectedResourceIds] = useState<string[]>([])
  const [mappingEdits, setMappingEdits] = useState<Record<string, { enabled: boolean; target: string }>>({})
  const fileInput = useRef<HTMLInputElement>(null)
  const invalidThreadIds = useRef(new Set<string>())
  const invalidRunIds = useRef(new Set<string>())
  const requestedThreadId = searchParams.get('thread_id') || ''
  const requestedRunId = searchParams.get('run_id') || ''

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
    navigate(fallbackId ? `/chat?thread_id=${encodeURIComponent(fallbackId)}` : '/chat', { replace: true })
  }, [activeThreadId, navigate, thread.error, thread.isError, threads.data])
  useEffect(() => {
    const status = (activeRun.error as (Error & { status?: number }) | null)?.status
    if (!activeRun.isError || !activeRunId || thread.isError || ![400, 404].includes(status || 0)) return
    invalidRunIds.current.add(activeRunId)
    setActiveRunId('')
    navigate(activeThreadId ? `/chat?thread_id=${encodeURIComponent(activeThreadId)}` : '/chat', { replace: true })
  }, [activeRun.error, activeRun.isError, activeRunId, activeThreadId, navigate, thread.isError])
  useEffect(() => {
    const latest = threadState.data?.latest_run as { run_id?: string; status?: string } | null | undefined
    if (!activeRunId && latest?.run_id && ['pending', 'running', 'waiting_user', 'waiting_workbench'].includes(latest.status || '')) {
      setActiveRunId(latest.run_id)
    }
  }, [activeRunId, threadState.data?.latest_run])
  const conversationArticleId = activeRun.data?.article_id || threadState.data?.active_article_id || thread.data?.article_id || ''
  const selection = (thread.data?.selection_context || {}) as Record<string, AgentEntity | string | undefined>
  const selectedArticle = selection.article as AgentEntity | undefined
  const selectedHeader = selection.header_config as AgentEntity | undefined
  const selectedResource = selection.resource as AgentEntity | undefined
  const resources = useQuery({ queryKey: ['resources', projectId, conversationArticleId], queryFn: () => api.resources(projectId, conversationArticleId), enabled: Boolean(projectId && conversationArticleId) })
  const targetHeaders = useQuery({ queryKey: ['target-headers', projectId, conversationArticleId], queryFn: () => api.articleTargetHeaders(projectId, conversationArticleId), enabled: Boolean(projectId && conversationArticleId) })

  const createThread = useMutation({
    mutationFn: () => api.createChatThread(projectId, '', '', 'workspace'),
    onSuccess: (created) => {
      setActiveThreadId(created.thread_id); setActiveRunId(''); setActiveCitation(undefined); setChatError(''); setChatNotice('')
      navigate(`/chat?thread_id=${encodeURIComponent(created.thread_id)}`, { replace: true })
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
      navigate(`/chat?thread_id=${encodeURIComponent(resolvedThreadId)}&run_id=${encodeURIComponent(run_id)}`, { replace: true })
      if (message) setChatNotice(message)
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, resolvedThreadId] })
    },
    onMutate: () => { setChatError(''); setChatNotice('') },
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
      navigate(`/chat?thread_id=${encodeURIComponent(resolvedThreadId)}${runId ? `&run_id=${encodeURIComponent(runId)}` : ''}`, { replace: true })
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
  const importSource = useMutation({
    mutationFn: async () => {
      const threadId = (await api.createChatThread(projectId, '', '文章导入', 'workspace')).thread_id
      const result = await api.chatMessage(projectId, threadId, `我要提取这篇文章的数据：${source.trim()}`, '')
      return { ...result, threadId }
    },
    onSuccess: ({ run_id, threadId }) => {
      setActiveThreadId(threadId); setActiveRunId(run_id); setSource('')
      navigate(`/chat?thread_id=${encodeURIComponent(threadId)}&run_id=${encodeURIComponent(run_id)}`, { replace: true })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, threadId] })
    },
    onMutate: () => { setChatError(''); setChatNotice('') },
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
      navigate(`/chat?thread_id=${encodeURIComponent(result.threadId)}`, { replace: true })
      client.invalidateQueries({ queryKey: ['articles', projectId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, result.threadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, result.threadId] })
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
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
    return watchAgentRun(projectId, activeRunId, () => {
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
    }, () => {
      client.invalidateQueries({ queryKey: ['agent-run', projectId, activeRunId] })
      client.invalidateQueries({ queryKey: ['chat-thread', projectId, activeThreadId] })
      client.invalidateQueries({ queryKey: ['chat-thread-state', projectId, activeThreadId] })
    })
  }, [activeRunId, activeThreadId, client, projectId])

  const latestAssistant = useMemo(() => [...(thread.data?.messages || [])].reverse().find((message) => message.role === 'assistant'), [thread.data?.messages])
  const latestCitations = useQuery({ queryKey: ['latest-chat-citations', projectId, latestAssistant?.message_id], queryFn: () => api.chatCitations(projectId, latestAssistant!.message_id), enabled: Boolean(projectId && latestAssistant?.message_id) })
  useEffect(() => { if (!activeCitation && latestCitations.data?.length) setActiveCitation(latestCitations.data[0]) }, [activeCitation, latestCitations.data])
  const pending = activeRun.data?.pending_interrupt || {}
  const runActivity = (activeRun.data?.state_summary?.activity || {}) as { message?: string; progress?: number; details?: Record<string, unknown> }
  const runProgress = typeof runActivity.progress === 'number' ? Math.round(runActivity.progress * 100) : 0
  const articlePdfOnly = Boolean(selectedArticle && !selectedHeader && !selectedResource && !activeCitation && !pending.kind)
  const options = Array.isArray(pending.options) ? pending.options.map(String) : []
  const pendingElements = Array.isArray(pending.elements) ? pending.elements as Record<string, unknown>[] : []
  const pendingMappings = Array.isArray((pending.mapping as Record<string, unknown> | undefined)?.items)
    ? ((pending.mapping as Record<string, unknown>).items as Record<string, unknown>[]) : []
  const sourceCandidates = Array.isArray(pending.candidates) ? pending.candidates as Record<string, unknown>[] : []
  const literatureResults = Array.isArray(pending.results) ? pending.results as Record<string, unknown>[] : []
  const activePendingElement = pendingElements.find((item) => String(item.element_id || '') === selectedResourceIds.at(-1)) || pendingElements[0]
  const inspectorEvidence = evidenceFrom(activeCitation) || evidenceFromElement(activePendingElement)
  const selectedHeaderFields = Array.isArray(selectedHeader?.metadata?.headers)
    ? selectedHeader.metadata.headers as Record<string, unknown>[]
    : []
  const inspectorTitle = articlePdfOnly
    ? '文章原文'
    : pending.kind === 'header_config_confirmation'
      ? '目标表头'
      : pending.kind === 'resource_confirmation'
        ? '资源与原文'
        : pending.kind === 'mapping_confirmation'
          ? '字段映射'
          : pending.kind
            ? '当前任务工具'
            : '证据与溯源'
  const actualModel = activeRun.data?.model_provider && activeRun.data?.model_name
    ? `${activeRun.data.model_provider} / ${activeRun.data.model_name}`
    : threadState.data?.actual_model?.provider && threadState.data?.actual_model?.model
      ? `${threadState.data.actual_model.provider} / ${threadState.data.actual_model.model}`
      : '等待首次模型调用'
  const handoffContext = (activeRun.data?.handoff_context || {}) as Record<string, unknown>
  const handoffPath = activeRunId
    ? `/workbench?${new URLSearchParams({
        agent_run_id: activeRunId,
        thread_id: activeThreadId,
        step: String(handoffContext.workbench_step ?? 1),
        stage: String(handoffContext.workbench_stage || ''),
        return_to: String(handoffContext.return_path || `/chat?thread_id=${activeThreadId}&run_id=${activeRunId}`),
      }).toString()}`
    : '/workbench'

  useEffect(() => {
    if (pending.kind !== 'resource_confirmation') return
    const recommended = Array.isArray(pending.recommended_element_ids) ? pending.recommended_element_ids.map(String) : []
    setSelectedResourceIds(recommended)
  }, [activeRunId, pending.kind, pending.recommended_element_ids])
  useEffect(() => {
    if (pending.kind !== 'mapping_confirmation') return
    setMappingEdits(Object.fromEntries(pendingMappings.map((item, index) => [
      `${String(item.element_id || '')}:${String(item.source_header || index)}`,
      { enabled: Boolean(item.suggested_target_header), target: String(item.suggested_target_header || '') },
    ])))
  }, [activeRunId, pending.kind])

  const submit = (event: FormEvent) => { event.preventDefault(); if (input.trim()) send.mutate(input.trim()) }
  const respond = (action: string) => {
    const response: Record<string, unknown> = { action, free_text: freeInput }
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
  const handoff = useMutation({
    mutationFn: () => api.handoffAgentRun(projectId, activeRunId),
    onSuccess: (result) => navigate(result.workbench_path),
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
        navigate('/chat', { replace: true })
      }
      client.invalidateQueries({ queryKey: ['chat-threads', projectId] })
      client.removeQueries({ queryKey: ['chat-thread', projectId, variables.threadId] })
    },
    onError: (error) => setChatError(readableError(error)),
  })
  const selectEntity = useMutation({
    mutationFn: ({ entityType, entityId }: { entityType: string; entityId?: string }) => api.selectChatEntity(projectId, activeThreadId, entityType, entityId || ''),
    onSuccess: (result, variables) => {
      if (result.article_id) setArticleId(result.article_id)
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

  return <div className={`page chat-page ${inspectorOpen ? '' : 'inspector-closed'}`}>
    <div className="chat-title-row">
      <div><h1>对话助手</h1><p>处理文章、核验数据或追溯任意已抽取条目。</p></div>
      <button className="icon-button" title="收起证据面板" onClick={() => setInspectorOpen((value) => !value)}><PanelRightClose size={18}/></button>
    </div>
    <div className="chat-selection-bar">
      <span>当前选择</span>
      {selectedArticle ? <button onClick={() => selectEntity.mutate({ entityType: 'article', entityId: selectedArticle.entity_id })}>文章：{selectedArticle.title}</button> : <em>未选择文章</em>}
      {selectedArticle && <button className="selection-start" onClick={() => send.mutate('开始数据提取')} disabled={send.isPending}>开始处理当前文章</button>}
      {selectedHeader && <button onClick={() => selectEntity.mutate({ entityType: 'header_config', entityId: selectedHeader.entity_id })}>表头：{selectedHeader.title}</button>}
      {selectedResource && <button onClick={() => selectEntity.mutate({ entityType: 'resource', entityId: selectedResource.entity_id })}>资源：{selectedResource.title}</button>}
      {(selectedArticle || selectedHeader || selectedResource) && <button className="clear-selection" onClick={() => selectEntity.mutate({ entityType: 'clear' })}>清除选择</button>}
      <span className="chat-model-status">模型：{actualModel}</span>
    </div>
    <div className="chat-workspace">
      <aside className="panel chat-threads">
        <button className="primary-button wide" onClick={() => createThread.mutate()} disabled={!projectId || createThread.isPending}><MessageSquarePlus size={17}/> 新建对话</button>
        <div className="chat-import">
          <input value={source} onChange={(event) => setSource(event.target.value)} placeholder="输入 DOI 或 URL" />
          <button className="secondary-button" onClick={() => importSource.mutate()} disabled={!source.trim() || importSource.isPending}><Link2 size={15}/> 导入</button>
          <input ref={fileInput} className="visually-hidden" type="file" accept=".pdf" onChange={choosePdf} />
          <button className="secondary-button" onClick={() => fileInput.current?.click()} disabled={importPdf.isPending}><FileUp size={15}/> PDF</button>
        </div>
        <div className="chat-scope">当前范围：{conversationArticleId ? '当前文章' : '独立对话（尚未选择文章）'}</div>
        <div className="chat-thread-list">
          {threads.data?.map((item: ChatThread) => <div key={item.thread_id} className="chat-thread-row">
            <button onClick={() => {
              setActiveThreadId(item.thread_id); setActiveRunId(item.active_run_id || ''); setActiveCitation(undefined)
              navigate(`/chat?thread_id=${encodeURIComponent(item.thread_id)}${item.active_run_id ? `&run_id=${encodeURIComponent(item.active_run_id)}` : ''}`, { replace: true })
            }} className={item.thread_id === activeThreadId ? 'chat-thread active' : 'chat-thread'}>
              <strong>{item.title || '文章处理对话'}</strong><small>{item.active_run_status === 'waiting_user' ? '等待确认' : item.active_run_status === 'waiting_workbench' ? '工作台处理中' : item.active_run_status ? '处理中' : item.scope === 'workspace' ? '工作区' : '当前文章'} · {item.updated_at?.slice(0, 16).replace('T', ' ')}</small>
            </button>
            <button className="icon-button chat-thread-delete" title={item.active_run_status === 'pending' || item.active_run_status === 'running' ? '任务执行中，暂不能删除' : item.active_run_status ? '停止任务并删除对话' : '删除对话'} aria-label={`删除对话 ${item.title || item.thread_id}`} onClick={(event) => { event.stopPropagation(); deleteConversation(item) }} disabled={deleteThread.isPending || item.active_run_status === 'pending' || item.active_run_status === 'running'}><Trash2 size={15}/></button>
          </div>)}
          {!threads.data?.length && <div className="empty-state compact"><Bot size={28}/><p>从一个问题开始处理当前文章。</p></div>}
        </div>
      </aside>
      <section className="panel chat-main">
        <div className="chat-transcript">
          {(thread.data?.messages || []).map((message) => {
            const payload = (message.ui_payload || {}) as {
              kind?: string
              entities?: AgentEntity[]
              results?: Record<string, unknown>[]
              article_id?: string
              workbench_step?: number
              workbench_stage?: string
              reason?: string
              path?: string
              operation?: string
              format?: string
              output_dir?: string
              settings_path?: string
            }
            return <article key={message.message_id} className={`chat-bubble ${message.role}`}>
              <strong>{message.role === 'assistant' ? 'GeoChem Agent' : '你'}</strong>
              {message.role === 'assistant' && message.model_provider && message.model_name && <small className="message-model">{message.model_provider} / {message.model_name}</small>}
              <p>{message.content}</p>
              {payload.kind === 'entity_cards' && Array.isArray(payload.entities) && <div className="chat-entity-cards">{payload.entities.map((entity) => {
                const selected = entity.entity_type === 'article'
                  ? selectedArticle?.entity_id === entity.entity_id
                  : entity.entity_type === 'header_config' && selectedHeader?.entity_id === entity.entity_id
                return <article className={`chat-entity-card ${selected ? 'selected' : ''}`} key={`${entity.entity_type}:${entity.entity_id}`}><strong>{entity.title}</strong><small>{entity.subtitle || entity.entity_type}</small><button className="secondary-button select-entity-button" onClick={() => selectEntity.mutate({ entityType: entity.entity_type, entityId: entity.entity_id })} disabled={selectEntity.isPending || selected}>{selected ? '已选择' : `选择此${entity.entity_type === 'article' ? '文章' : '对象'}`}</button></article>
              })}</div>}
              {(payload.kind === 'selected_entity' || payload.kind === 'article_selection_confirmation' || payload.kind === 'header_selection_confirmation') && Array.isArray(payload.entities) && <div className="chat-selected-card"><span>已绑定：{payload.entities[0]?.title}</span>{['article', 'header_config'].includes(payload.entities[0]?.entity_type || '') && <button className="primary-button" onClick={() => startProcessing.mutate()} disabled={startProcessing.isPending}>{startProcessing.isPending ? '正在启动…' : payload.entities[0]?.entity_type === 'header_config' ? '继续资源发现' : '开始数据提取'}</button>}</div>}
              {payload.kind === 'literature_cards' && Array.isArray(payload.results) && <div className="chat-entity-cards literature-cards">{payload.results.map((result) => {
                const source = String(result.doi || result.landing_url || '')
                return <article className="chat-entity-card" key={String(result.result_id || source)}><strong>{String(result.title || '未命名文献')}</strong><small>{String(result.year || '年份未知')} · {String(result.doi || result.venue || '未登记 DOI')}</small><button className="secondary-button select-entity-button" disabled={!source || send.isPending} onClick={() => send.mutate(`我要导入这篇文章：${source}`)}>检查并导入</button></article>
              })}</div>}
              {payload.kind === 'workbench_link' && <div className="chat-selected-card"><span>{payload.reason || '该操作适合在专家工作台中完成。'}</span><button className="primary-button" onClick={() => navigate(`/workbench?${new URLSearchParams({ step: String(payload.workbench_step ?? 1), stage: String(payload.workbench_stage || ''), return_to: `/chat?thread_id=${activeThreadId}` }).toString()}`)}>前往工作台</button></div>}
              {payload.kind === 'navigation_action' && <div className="chat-selected-card"><span>需要在对应的治理页面继续。</span><button className="primary-button" onClick={() => navigate(payload.path || '/review')}>打开页面</button></div>}
              {payload.kind === 'critical_action' && <div className="chat-critical-card"><div><strong>{payload.operation === 'export' ? '正式导出' : '生成标准化记录'}</strong><span>此操作只会在你点击确认后执行，并写入 Agent 工具审计。</span></div><button className="primary-button" disabled={!message.agent_run_id || executeConfirmed.isPending} onClick={() => executeConfirmed.mutate({ runId: message.agent_run_id || '', payload: { operation: payload.operation || '', format: payload.format || 'csv', output_dir: payload.output_dir || '' } })}>{payload.operation === 'export' ? '确认导出' : '确认标准化'}</button></div>}
              {(payload.kind === 'model_unavailable' || payload.kind === 'model_configuration_required') && <div className="chat-selected-card"><span>{payload.kind === 'model_configuration_required' ? '尚未配置 API Key，配置完成后即可继续使用对话助手。' : '对话模型未完成本次请求。'}</span><button className="primary-button" onClick={() => navigate(payload.settings_path || '/settings')}>前往模型配置</button></div>}
            </article>
          })}
          {(activeRun.data?.status === 'running' || activeRun.data?.status === 'pending') && <div className="agent-progress"><LoaderCircle size={16} className="spin"/><span>正在执行：{agentNodeLabel(activeRun.data.current_node)}{runActivity.message ? ` · ${runActivity.message}` : ''}{runProgress ? ` · ${runProgress}%` : ''}</span><button className="secondary-button agent-cancel" onClick={() => cancelRun.mutate()} disabled={cancelRun.isPending}>停止任务</button></div>}
          {activeRun.data?.status === 'failed' && <div className="agent-error">任务失败：{activeRun.data.error_message}</div>}
          {activeRun.data?.status === 'waiting_workbench' && <div className="agent-notice workbench-waiting"><span>任务正在等待工作台中的人工处理。</span><button className="primary-button" onClick={() => navigate(handoffPath)}>返回交接位置</button></div>}
          {chatError && <div className="agent-error">{chatError}</div>}
          {chatNotice && <div className="agent-notice">{chatNotice}</div>}
          {activeRun.data?.status === 'waiting_user' && <section className="agent-checkpoint">
            <div><Sparkles size={18}/><strong>{String(pending.title || '需要你的确认')}</strong></div>
            <p>{String(pending.recommendation || pending.message || '请选择下一步操作。')}</p>
            {pending.kind === 'workbench_return_review' && Array.isArray((pending.diff as Record<string, unknown> | undefined)?.summary) && <div className="workbench-diff-summary"><strong>本次人工修改</strong>{((pending.diff as Record<string, unknown>).summary as string[]).map((line) => <span key={line}>{line}</span>)}</div>}
            <div className="checkpoint-actions">{options.map((option) => <button key={option} className={option.includes('accept') || option.includes('open') || option.includes('adopt') ? 'primary-button' : 'secondary-button'} onClick={() => respond(option)} disabled={resume.isPending}>{option === 'accept_recommended' ? '接受推荐' : option === 'accept_suggestions' ? '确认建议' : option === 'open_review' ? '进入人工审核' : option === 'reextract' ? '重新抽取' : option === 'custom_selection' ? '自定义资源' : option === 'edit_rules' ? '编辑规则' : option === 'adopt_workbench_changes' ? '采用修改并继续' : option === 'return_workbench' ? '返回工作台调整' : option === 'stop' ? '停止本次任务' : option}</button>)}</div>
            {['resource_confirmation', 'mapping_confirmation', 'quality_confirmation'].includes(String(pending.kind || '')) && <button className="secondary-button workbench-handoff" onClick={() => handoff.mutate()} disabled={handoff.isPending}>在智能体工作台中手动处理</button>}
            {pending.kind === 'source_confirmation' && <div className="checkpoint-picker source-picker">{sourceCandidates.map((candidate) => <button key={String(candidate.source_id)} className="source-candidate" onClick={() => resume.mutate({ action: 'confirm_source', source_id: candidate.source_id })}><strong>{String(candidate.title || candidate.doi || '公开 PDF')}</strong><small>{String(candidate.journal || '')} {candidate.year ? `· ${candidate.year}` : ''}</small><span>{candidate.access_status === 'public' ? '确认并下载公开 PDF' : String(candidate.validation_message || '需要上传 PDF')}</span></button>)}</div>}
            {pending.kind === 'literature_search_results' && <div className="checkpoint-picker source-picker literature-picker">{literatureResults.map((result) => <button key={String(result.literature_id)} className="source-candidate" onClick={() => resume.mutate({ action: 'select_literature', literature_id: result.literature_id })}><strong>{String(result.title || '未命名文献')}</strong><small>{Array.isArray(result.authors) ? (result.authors as string[]).slice(0, 3).join(', ') : ''}{result.year ? ` · ${result.year}` : ''}{result.venue ? ` · ${result.venue}` : ''}</small><span>{result.doi ? `DOI: ${String(result.doi)}` : String(result.landing_url || '无 DOI')} · {result.open_access ? '开放获取线索' : '需继续检查公开 PDF'}</span></button>)}{!literatureResults.length && <button className="secondary-button" onClick={() => setInput('搜索 geochemistry')}>换一个关键词</button>}</div>}
            {pending.kind === 'header_config_confirmation' && <div className="checkpoint-picker header-picker">{(Array.isArray(pending.header_configs) ? pending.header_configs as Record<string, unknown>[] : []).map((config) => <button key={String(config.config_id)} className="source-candidate" onClick={() => resume.mutate({ action: 'select_header_config', config_id: config.config_id })}><strong>{String(config.name || '表头配置')}</strong><small>{String(config.field_count || 0)} 个字段 · {Array.isArray(config.preview) ? (config.preview as string[]).slice(0, 4).join('、') : ''}</small></button>)}<button className="secondary-button" onClick={() => navigate('/headers')}>前往表头配置</button></div>}
            {pending.kind === 'resource_confirmation' && <div className="checkpoint-picker resource-checkpoint-picker">
              <div className="resource-checkpoint-summary"><strong>已选 {selectedResourceIds.length} / {pendingElements.length}</strong><button className="secondary-button" onClick={() => setSelectedResourceIds(Array.isArray(pending.recommended_element_ids) ? pending.recommended_element_ids.map(String) : [])}>恢复推荐</button><button className="secondary-button" onClick={() => setSelectedResourceIds([])}>清空</button></div>
              {pendingElements.map((element) => {
                const id = String(element.element_id || '')
                const checked = selectedResourceIds.includes(id)
                return <label key={id}><input type="checkbox" checked={checked} onChange={() => setSelectedResourceIds((ids) => checked ? ids.filter((value) => value !== id) : [...ids, id])} /><span>{String(element.element_type || '资源')} · p{String(element.page_number || '?')}</span><small>{String(element.caption || element.text_content || '').slice(0, 76)}</small></label>
              })}
            </div>}
            {pending.kind === 'mapping_confirmation' && <div className="checkpoint-picker mapping-picker">
              {pendingMappings.slice(0, 24).map((item, index) => {
                const key = `${String(item.element_id || '')}:${String(item.source_header || index)}`
                const edit = mappingEdits[key] || { enabled: false, target: '' }
                return <label key={key}><input type="checkbox" checked={edit.enabled} onChange={() => setMappingEdits((current) => ({ ...current, [key]: { ...edit, enabled: !edit.enabled } }))} /><strong>{String(item.source_header || '')}</strong><select value={edit.target} onChange={(event) => setMappingEdits((current) => ({ ...current, [key]: { ...edit, target: event.target.value } }))}><option value="">忽略</option>{targetHeaders.data?.map((header) => <option key={header.header_id} value={header.display_header}>{header.display_header}</option>)}</select></label>
              })}
            </div>}
            <textarea value={freeInput} onChange={(event) => setFreeInput(event.target.value)} placeholder="可输入补充说明或自定义要求" />
          </section>}
        </div>
        <div className="chat-suggestions">{QUICK_QUESTIONS.map((question) => <button key={question} onClick={() => setInput(question)}>{question}</button>)}</div>
        <form className="chat-composer" onSubmit={submit}><textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder={conversationArticleId ? '例如：开始处理这篇文章，或询问一个数据来源…' : '例如：搜索地化数据文献，或输入 DOI: 10.xxxx/xxxx'} disabled={send.isPending}/><button className="primary-button" type="submit" disabled={!input.trim() || send.isPending}><Send size={18}/></button></form>
      </section>
      {inspectorOpen && <aside className="panel chat-inspector">
        <div className="inspector-heading"><FileSearch size={18}/><strong>{inspectorTitle}</strong></div>
        {!articlePdfOnly && selectedArticle && <div className="source-preview-card"><strong>{selectedArticle.title}</strong><small>当前文章 · {selectedArticle.subtitle}</small><button className="secondary-button" onClick={() => navigate('/workbench')}>在智能体工作台中手动处理</button></div>}
        {selectedHeader && <div className="source-preview-card"><strong>{selectedHeader.title}</strong><p>{selectedHeader.subtitle}</p>{selectedHeaderFields.length > 0 && <div className="chat-header-preview">{selectedHeaderFields.slice(0, 16).map((field, index) => <span key={`${String(field.display_header || field.name || index)}`}>{String(field.display_header || field.name || field['字段名'] || '')}</span>)}</div>}</div>}
        {selectedResource && <div className="source-preview-card"><strong>{selectedResource.title}</strong><small>{selectedResource.subtitle}</small></div>}
        {pending.kind === 'source_confirmation' && sourceCandidates[0] && <div className="source-preview-card"><strong>{String(sourceCandidates[0].title || sourceCandidates[0].doi || '公开文献候选')}</strong><small>{String(sourceCandidates[0].doi || '')}</small><p>{String(sourceCandidates[0].journal || '')} {sourceCandidates[0].year ? `· ${sourceCandidates[0].year}` : ''}</p>{sourceCandidates[0].pdf_url ? <a href={String(sourceCandidates[0].pdf_url)} target="_blank" rel="noreferrer">在浏览器预览公开 PDF</a> : <p className="muted">未发现可公开下载的 PDF，请上传文件。</p>}</div>}
        {pending.kind === 'header_config_confirmation' && <div className="source-preview-card"><strong>目标表头配置</strong><p>请选择一套表头后再开始资源发现。字段名称、单位和顺序会作为本次抽取的唯一目标结构。</p></div>}
        {pending.kind === 'resource_confirmation' && <div className="source-preview-card"><strong>已选 {selectedResourceIds.length} / {pendingElements.length} 个资源</strong><p>勾选资源后，PDF 会定位到当前资源。复杂增删和框选可转到智能体工作台。</p></div>}
        {pending.kind === 'mapping_confirmation' && <div className="source-preview-card"><strong>待确认字段映射</strong><div className="chat-mapping-preview">{pendingMappings.slice(0, 12).map((item, index) => <span key={`${String(item.element_id || '')}:${String(item.source_header || index)}`}><b>{String(item.source_header || '未命名字段')}</b><ChevronRight size={13}/>{String(mappingEdits[`${String(item.element_id || '')}:${String(item.source_header || index)}`]?.target || item.suggested_target_header || '忽略')}</span>)}</div></div>}
        {pending.kind === 'literature_search_results' && <div className="source-preview-card"><strong>公开学术检索结果</strong><p>请选择一篇文章后，Agent 会继续检查公开 PDF 是否可获取。</p></div>}
        {!articlePdfOnly && <div className="citation-list">{(latestCitations.data || []).map((citation) => <button key={citation.citation_id || citation.document_id} className={(activeCitation?.document_id === citation.document_id) ? 'citation-card active' : 'citation-card'} onClick={() => setActiveCitation(citation)}><strong>{citation.label || '检索证据'}</strong><small>{citation.element_type || '记录'} {citation.page_number ? `· p${citation.page_number}` : ''}</small></button>)}{!latestCitations.data?.length && <div className="empty-state compact"><FileSearch size={28}/><p>提问后，这里会自动定位表格、段落、图像或数据记录。</p></div>}</div>}
        {!articlePdfOnly && activeCitation && <div className="citation-detail"><p>{activeCitation.content}</p><div className="source-chain">{activeCitation.element_type || '数据'} <ChevronRight size={14}/> {activeCitation.label || '来源'} {activeCitation.page_number ? <><ChevronRight size={14}/> p{activeCitation.page_number}</> : null}</div></div>}
        <div className="chat-pdf"><PdfEvidenceViewer projectId={projectId} resources={resources.data || []} evidence={inspectorEvidence} /></div>
      </aside>}
    </div>
  </div>
}
