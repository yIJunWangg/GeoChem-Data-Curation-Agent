import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ExternalLink,
  FileSearch,
  LoaderCircle,
  Save,
  Sparkles,
  Table2,
  X,
} from 'lucide-react'
import { api, watchTask } from './api'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { CompactEmptyState, PageCommandBar, SplitWorkspace } from './WorkspaceUI'
import type {
  CandidateCell,
  CandidateRecord,
  DocumentElement,
  TableRuleSuggestion,
  WorkbenchStage,
  WorkbenchView,
} from './types'

type Props = {
  projectId: string
  articleId: string
  initialView?: WorkbenchView
  initialStage?: WorkbenchStage
  agentRunId?: string
  pendingSelectedElementIds?: string[]
  onPendingSelectionChange?: (elementIds: string[]) => void
  selectionPersistenceMode?: 'immediate' | 'deferred'
  onSaved?: (message: string) => void
}

const STAGE_LABELS: Array<{ id: WorkbenchStage; label: string }> = [
  { id: 'table_standardize', label: '标准化' },
  { id: 'mapping', label: '映射' },
  { id: 'tables', label: '表格抽取' },
  { id: 'paragraphs', label: '段落抽取' },
  { id: 'figures', label: '图像补值' },
  { id: 'edit', label: '候选修改' },
]

function elementEvidence(element?: DocumentElement): PdfEvidence | undefined {
  if (!element) return undefined
  return {
    resource_id: element.resource_id,
    element_id: element.element_id,
    element_type: element.element_type,
    page_number: element.page_number,
    bbox: element.bbox || [],
    page_spans: element.page_spans,
    caption: element.caption,
    context: element.context_text || element.text_content,
  }
}

function cellEvidence(cell?: CandidateCell, element?: DocumentElement): PdfEvidence | undefined {
  if (!cell || !element) return undefined
  const base = elementEvidence(element)
  if (!base) return undefined
  return {
    ...base,
    target_header: cell.target_header,
    value: cell.value,
    target_unit: cell.target_unit,
    original_field: cell.original_field,
    original_value: cell.original_value,
    original_unit: cell.original_unit,
    confidence: cell.confidence,
    review_status: cell.review_status,
  }
}

function elementTitle(element: DocumentElement) {
  const type = element.element_type === 'table' ? '表格' : element.element_type === 'figure' ? '图像' : '段落'
  const page = element.page_spans?.length
    ? `p${Math.min(...element.page_spans.map((span) => span.page_number))}${element.page_spans.length > 1 ? ` · ${element.page_spans.length} 处` : ''}`
    : `p${element.page_number}`
  return `${type} · ${page}`
}

function normalizeHeaders(headers: Array<Record<string, string>>) {
  return headers.map((header) => header.display_header || header.canonical_field || '').filter(Boolean)
}

export function EmbeddedWorkbench({
  projectId,
  articleId,
  initialView = 'resources',
  initialStage = 'table_standardize',
  agentRunId = '',
  pendingSelectedElementIds,
  onPendingSelectionChange,
  selectionPersistenceMode = 'immediate',
  onSaved,
}: Props) {
  const client = useQueryClient()
  const navigate = useNavigate()
  const [view, setView] = useState<WorkbenchView>(initialView)
  const [stage, setStage] = useState<WorkbenchStage>(initialStage)
  const [activeElementId, setActiveElementId] = useState('')
  const [activeCell, setActiveCell] = useState<CandidateCell>()
  const [busyLabel, setBusyLabel] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => setView(initialView), [initialView])
  useEffect(() => setStage(initialStage), [initialStage])

  const session = useQuery({
    queryKey: ['session', projectId, articleId],
    queryFn: () => api.session(projectId, articleId),
    enabled: Boolean(projectId && articleId),
  })
  const elements = useQuery({
    queryKey: ['elements', projectId, articleId],
    queryFn: () => api.elements(projectId, articleId),
    enabled: Boolean(projectId && articleId),
  })
  const resources = useQuery({
    queryKey: ['resources', projectId, articleId],
    queryFn: () => api.resources(projectId, articleId),
    enabled: Boolean(projectId && articleId),
  })
  const targetHeaders = useQuery({
    queryKey: ['target-headers', projectId, articleId],
    queryFn: () => api.articleTargetHeaders(projectId, articleId),
    enabled: Boolean(projectId && articleId),
  })
  const preflight = useQuery({
    queryKey: ['table-preflight', projectId, articleId],
    queryFn: () => api.tableRulePreflight(projectId, articleId),
    enabled: Boolean(projectId && articleId && view === 'extract' && stage === 'mapping'),
  })
  const activeBatchId = session.data?.active_batch_id || ''
  const batch = useQuery({
    queryKey: ['batch', projectId, activeBatchId],
    queryFn: () => api.batch(projectId, activeBatchId),
    enabled: Boolean(projectId && activeBatchId),
  })

  const persistedElements = elements.data || []
  const pendingSelectionSet = useMemo(
    () => new Set(pendingSelectedElementIds || []),
    [pendingSelectedElementIds],
  )
  const allElements = useMemo(() => persistedElements.map((element) => ({
    ...element,
    selected: selectionPersistenceMode === 'deferred'
      ? pendingSelectionSet.has(element.element_id)
      : element.selected,
  })), [pendingSelectionSet, persistedElements, selectionPersistenceMode])
  const selected = allElements.filter((element) => element.selected)
  const selectedTables = selected.filter((element) => element.element_type === 'table')
  const activeElement = allElements.find((element) => element.element_id === activeElementId)
    || selected[0]
    || allElements[0]

  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: ['session', projectId, articleId] }),
      client.invalidateQueries({ queryKey: ['elements', projectId, articleId] }),
      client.invalidateQueries({ queryKey: ['resources', projectId, articleId] }),
      client.invalidateQueries({ queryKey: ['batch', projectId] }),
      client.invalidateQueries({ queryKey: ['table-preflight', projectId, articleId] }),
    ])
  }

  const runTask = (label: string, starter: () => Promise<{ task_id: string }>) => {
    setBusyLabel(label)
    setNotice('')
    starter().then(({ task_id }) => {
      watchTask(projectId, task_id, () => undefined, async () => {
        setBusyLabel('')
        await refresh()
        setNotice(`${label}已完成。`)
      })
    }).catch((error) => {
      setBusyLabel('')
      setNotice(`${label}失败：${error instanceof Error ? error.message : String(error)}`)
    })
  }

  const fullWorkbenchPath = `/workbench?${new URLSearchParams({
    view,
    stage,
    article_id: articleId,
    ...(agentRunId ? { agent_run_id: agentRunId, return_to: window.location.pathname + window.location.search } : {}),
  }).toString()}`

  if (!articleId) {
    return <CompactEmptyState
      icon={FileSearch}
      title="请先选择文章"
      description="选择文章后，可以在这里校核资源、标准化表格和修改候选数据。"
    />
  }

  const viewTabs = <div className="embedded-workbench-tabs" role="tablist" aria-label="工作台阶段">
    <button className={view === 'resources' ? 'active' : ''} onClick={() => setView('resources')}>资源</button>
    <button className={view === 'extract' ? 'active' : ''} onClick={() => setView('extract')}>抽取</button>
    <button className={view === 'quality' ? 'active' : ''} onClick={() => setView('quality')}>质检</button>
  </div>

  const finishHandoff = useMutation({
    mutationFn: async () => {
      if (selectionPersistenceMode === 'deferred') {
        await api.selectElements(projectId, articleId, selected.map((element) => element.element_id))
      }
      return api.resumeAgentFromWorkbench(projectId, agentRunId)
    },
    onSuccess: () => {
      onSaved?.('人工修改已保存，Agent 将从原检查点继续。')
      refresh()
    },
  })

  return <section className="embedded-workbench">
    <PageCommandBar
      title={view === 'resources' ? '资源确认' : view === 'extract' ? '资源抽取' : '映射与质检'}
      description={`${selected.length} 个已选资源${activeBatchId ? ' · 已有候选批次' : ''}`}
      leading={viewTabs}
      status={busyLabel ? <span className="inline-task-status"><LoaderCircle className="spin" size={14}/>{busyLabel}</span> : undefined}
    >
      {agentRunId && <button disabled={finishHandoff.isPending} onClick={() => finishHandoff.mutate()}>
        <Save size={15}/>{finishHandoff.isPending ? '正在保存' : '保存并继续'}
      </button>}
      <button title="打开完整工作台" onClick={() => navigate(fullWorkbenchPath)}><ExternalLink size={15}/>完整工作台</button>
    </PageCommandBar>
    {notice && <div className="embedded-workbench-notice">{notice}</div>}

    {view === 'resources' && <EmbeddedResources
      projectId={projectId}
      articleId={articleId}
      elements={allElements}
      resources={resources.data || []}
      activeElement={activeElement}
      busy={Boolean(busyLabel)}
      onActive={(element) => setActiveElementId(element.element_id)}
      onDiscover={() => runTask('资源发现', () => api.discover(projectId, articleId))}
      onToggle={async (element) => {
        const ids = element.selected
          ? selected.filter((item) => item.element_id !== element.element_id).map((item) => item.element_id)
          : [...selected.map((item) => item.element_id), element.element_id]
        if (selectionPersistenceMode === 'deferred') {
          onPendingSelectionChange?.(ids)
          return
        }
        await api.selectElements(projectId, articleId, ids)
        refresh()
      }}
    />}

    {view === 'extract' && <>
      <div className="embedded-stage-tabs">
        {STAGE_LABELS.map((item) => <button
          key={item.id}
          className={stage === item.id ? 'active' : ''}
          onClick={() => { setStage(item.id); setActiveCell(undefined) }}
        >{item.label}</button>)}
      </div>
      {stage === 'table_standardize' && <EmbeddedTableStandardization
        projectId={projectId}
        articleId={articleId}
        tables={selectedTables}
        activeElementId={activeElement?.element_type === 'table' ? activeElement.element_id : ''}
        onActive={setActiveElementId}
        onRun={() => runTask('表格标准化', () => api.standardizeTables(projectId, articleId, false))}
        onRefresh={refresh}
      />}
      {stage === 'mapping' && <EmbeddedMapping
        projectId={projectId}
        articleId={articleId}
        items={preflight.data?.items || []}
        targetHeaders={targetHeaders.data?.map((header) => header.display_header) || []}
        loading={preflight.isLoading}
        onRefresh={refresh}
      />}
      {stage === 'tables' && <EmbeddedCandidateStage
        title="表格候选数据"
        emptyText="先抽取已选表格，系统会生成样品级候选表。"
        batch={batch.data}
        elements={allElements}
        resources={resources.data || []}
        activeCell={activeCell}
        onCell={setActiveCell}
        onRun={() => runTask('表格抽取', () => api.extractTables(projectId, articleId, false))}
        projectId={projectId}
        onRefresh={refresh}
      />}
      {stage === 'paragraphs' && <EmbeddedCandidateStage
        title="段落候选数据"
        emptyText="只会读取当前已选段落资源。"
        batch={batch.data}
        elements={allElements}
        resources={resources.data || []}
        activeCell={activeCell}
        onCell={setActiveCell}
        onRun={() => runTask('段落抽取', () => api.extractParagraphs(
          projectId,
          articleId,
          selected.filter((item) => item.element_type === 'paragraph').map((item) => item.element_id),
          true,
        ))}
        projectId={projectId}
        onRefresh={refresh}
      />}
      {stage === 'figures' && <EmbeddedCandidateStage
        title="图像人工补值"
        emptyText="选择图像后，在候选表中人工填写；复杂操作请打开完整工作台。"
        batch={batch.data}
        elements={allElements}
        resources={resources.data || []}
        activeCell={activeCell}
        onCell={setActiveCell}
        projectId={projectId}
        onRefresh={refresh}
      />}
      {stage === 'edit' && <EmbeddedCandidateStage
        title="候选结果修改"
        emptyText="尚未生成候选表。"
        batch={batch.data}
        elements={allElements}
        resources={resources.data || []}
        activeCell={activeCell}
        onCell={setActiveCell}
        projectId={projectId}
        onRefresh={refresh}
      />}
    </>}

    {view === 'quality' && <EmbeddedQuality
      batch={batch.data}
      elements={allElements}
      resources={resources.data || []}
      activeCell={activeCell}
      onCell={setActiveCell}
      projectId={projectId}
      onRefresh={refresh}
    />}

  </section>
}

function EmbeddedResources({
  projectId,
  articleId,
  elements,
  resources,
  activeElement,
  busy,
  onActive,
  onToggle,
  onDiscover,
}: {
  projectId: string
  articleId: string
  elements: DocumentElement[]
  resources: Awaited<ReturnType<typeof api.resources>>
  activeElement?: DocumentElement
  busy: boolean
  onActive: (element: DocumentElement) => void
  onToggle: (element: DocumentElement) => void
  onDiscover: () => void
}) {
  const [selectedOnly, setSelectedOnly] = useState(false)
  const [compactPane, setCompactPane] = useState<'resources' | 'pdf'>('pdf')
  const visible = selectedOnly ? elements.filter((element) => element.selected) : elements
  const list = <div className="embedded-resource-index">
    <PageCommandBar title="资源索引" description={`${visible.length} / ${elements.length}`}>
      <button className={selectedOnly ? '' : 'active'} onClick={() => setSelectedOnly(false)}>全部</button>
      <button className={selectedOnly ? 'active' : ''} onClick={() => setSelectedOnly(true)}>已选</button>
      <button className="icon-button" disabled={busy} title="重新发现" onClick={onDiscover}>
        {busy ? <LoaderCircle className="spin" size={15}/> : <Sparkles size={15}/>}
      </button>
    </PageCommandBar>
    <div className="embedded-resource-list">
      {visible.map((element) => <div
        key={element.element_id}
        className={`embedded-resource-row ${activeElement?.element_id === element.element_id ? 'active' : ''}`}
        onClick={() => onActive(element)}
      >
        <input
          type="checkbox"
          checked={element.selected}
          aria-label={`选择 ${elementTitle(element)}`}
          onChange={() => onToggle(element)}
          onClick={(event) => event.stopPropagation()}
        />
        <div><strong>{elementTitle(element)}</strong><small>{element.caption || element.text_content || '无标题'}</small></div>
        <span>{Math.round(element.relevance_score * 100)}%</span>
      </div>)}
      {!visible.length && <CompactEmptyState title={selectedOnly ? '尚未选择资源' : '尚未发现资源'} description="运行资源发现，或在完整工作台中手工框选。"/>}
    </div>
  </div>
  const pdf = <PdfEvidenceViewer projectId={projectId} articleScopeKey={articleId} resources={resources} evidence={elementEvidence(activeElement)}/>
  return <div className={`embedded-resource-surface compact-pane-${compactPane}`}>
    <div className="embedded-resource-pane-tabs" role="tablist" aria-label="资源与 PDF">
      <button className={compactPane === 'resources' ? 'active' : ''} onClick={() => setCompactPane('resources')}>资源 · {visible.length}</button>
      <button className={compactPane === 'pdf' ? 'active' : ''} onClick={() => setCompactPane('pdf')}>PDF</button>
    </div>
    <SplitWorkspace
      className="embedded-resource-workspace"
      secondary={list}
      primary={pdf}
      secondaryWidth={240}
      primaryMin={430}
    />
  </div>
}

function EmbeddedTableStandardization({
  projectId,
  articleId,
  tables,
  activeElementId,
  onActive,
  onRun,
  onRefresh,
}: {
  projectId: string
  articleId: string
  tables: DocumentElement[]
  activeElementId: string
  onActive: (elementId: string) => void
  onRun: () => void
  onRefresh: () => void
}) {
  const table = tables.find((item) => item.element_id === activeElementId) || tables[0]
  const [headers, setHeaders] = useState<string[]>([])
  const [rows, setRows] = useState<string[][]>([])

  useEffect(() => {
    setHeaders([...(table?.raw_table?.headers || [])])
    setRows((table?.raw_table?.rows || []).map((row) => [...row]))
  }, [table?.element_id, table?.raw_table?.headers, table?.raw_table?.rows])

  if (!tables.length) return <CompactEmptyState
    icon={Table2}
    title="没有已选表格"
    description="回到资源阶段勾选表格，或在完整工作台中补充资源。"
  />

  return <div className="embedded-standard-table">
    <PageCommandBar title="标准化表格" description={`${headers.length} 列 · ${rows.length} 行`}>
      <select value={table?.element_id || ''} onChange={(event) => onActive(event.target.value)}>
        {tables.map((item, index) => <option key={item.element_id} value={item.element_id}>表格 {index + 1} · p{item.page_number}</option>)}
      </select>
      <button onClick={onRun}><Sparkles size={15}/>重新标准化</button>
      <button className="primary-button" disabled={!table} onClick={async () => {
        if (!table) return
        await api.updateStandardTable(projectId, table.element_id, headers, rows, '对话内精简工作台编辑')
        onRefresh()
      }}><Save size={15}/>保存修改</button>
    </PageCommandBar>
    <div className="embedded-table-scroll">
      <table><thead><tr>{headers.map((header, index) => <th key={index}><input value={header} onChange={(event) => setHeaders((current) => current.map((value, column) => column === index ? event.target.value : value))}/></th>)}</tr></thead>
      <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{headers.map((_, columnIndex) => <td key={columnIndex}><input value={row[columnIndex] || ''} onChange={(event) => setRows((current) => current.map((values, index) => index === rowIndex ? headers.map((__, column) => column === columnIndex ? event.target.value : (values[column] || '')) : values))}/></td>)}</tr>)}</tbody></table>
    </div>
  </div>
}

function EmbeddedMapping({
  projectId,
  articleId,
  items,
  targetHeaders,
  loading,
  onRefresh,
}: {
  projectId: string
  articleId: string
  items: TableRuleSuggestion[]
  targetHeaders: string[]
  loading: boolean
  onRefresh: () => void
}) {
  const [drafts, setDrafts] = useState<Record<string, { checked: boolean; target: string }>>({})
  useEffect(() => {
    setDrafts(Object.fromEntries(items.map((item) => [item.source_header, {
      checked: Boolean(item.suggested_target_header),
      target: item.suggested_target_header || '',
    }])))
  }, [items])

  if (loading) return <CompactEmptyState icon={LoaderCircle} title="正在检查表头映射"/>
  return <div className="embedded-mapping-list">
    <PageCommandBar title="表头映射建议" description={`${items.length} 个原始字段`}>
      <button className="primary-button" disabled={!Object.values(drafts).some((item) => item.checked && item.target)} onClick={async () => {
        const rules = items.filter((item) => drafts[item.source_header]?.checked && drafts[item.source_header]?.target).map((item) => ({
          ...item,
          source_header: item.source_header,
          target_header: drafts[item.source_header].target,
        }))
        await api.confirmTableRulePreflight(projectId, articleId, rules)
        onRefresh()
      }}><Check size={15}/>确认选中规则</button>
    </PageCommandBar>
    <div className="embedded-mapping-scroll">
      {items.map((item) => <div className="embedded-mapping-row" key={`${item.element_id}-${item.source_header}`}>
        <input type="checkbox" checked={drafts[item.source_header]?.checked || false} onChange={(event) => setDrafts((current) => ({ ...current, [item.source_header]: { checked: event.target.checked, target: current[item.source_header]?.target || '' } }))}/>
        <strong title={item.source_header}>{item.source_header}</strong><ArrowRight size={14}/>
        <select value={drafts[item.source_header]?.target || ''} onChange={(event) => setDrafts((current) => ({ ...current, [item.source_header]: { checked: true, target: event.target.value } }))}>
          <option value="">选择目标表头</option>
          {targetHeaders.map((header) => <option key={header} value={header}>{header}</option>)}
        </select>
        <small><span className={`mapping-source-badge ${item.mapping_source || 'unresolved'}`}>{item.mapping_source === 'knowledge' ? '知识库' : item.mapping_source === 'memory' ? '规则' : item.mapping_source === 'organization_advisory' ? '组织规则' : item.mapping_source === 'exact' ? '精确' : item.mapping_source === 'builtin' ? '内置' : item.mapping_source === 'leaf_exact' ? '叶子字段' : item.mapping_source === 'llm' ? 'AI' : '建议'}</span>{Math.round(item.confidence * 100)}%</small>
      </div>)}
      {!items.length && <CompactEmptyState title="没有待确认的表头映射" description="请先标准化已选表格。"/>}
    </div>
  </div>
}

function EmbeddedCandidateStage({
  title,
  emptyText,
  batch,
  elements,
  resources,
  activeCell,
  onCell,
  onRun,
  projectId,
  onRefresh,
}: {
  title: string
  emptyText: string
  batch?: Awaited<ReturnType<typeof api.batch>>
  elements: DocumentElement[]
  resources: Awaited<ReturnType<typeof api.resources>>
  activeCell?: CandidateCell
  onCell: (cell?: CandidateCell) => void
  onRun?: () => void
  projectId: string
  onRefresh: () => void
}) {
  if (!batch) return <CompactEmptyState
    icon={Table2}
    title="尚未生成候选表"
    description={emptyText}
    action={onRun && <button className="primary-button" onClick={onRun}>开始抽取</button>}
  />
  const sourceElement = activeCell?.element_id
    ? elements.find((element) => element.element_id === activeCell.element_id)
    : undefined
  return <div className={`embedded-candidate-stage ${activeCell ? 'has-source' : ''}`}>
    <PageCommandBar title={title} description={`${batch.records.length} 个样品 · ${batch.headers.length} 个目标字段`}>
      {onRun && <button onClick={onRun}>重新抽取</button>}
    </PageCommandBar>
    <EmbeddedCandidateTable
      records={batch.records}
      headers={normalizeHeaders(batch.headers as unknown as Array<Record<string, string>>)}
      elements={elements}
      activeCell={activeCell}
      onCell={onCell}
      projectId={projectId}
      onRefresh={onRefresh}
    />
    {activeCell && <EmbeddedCellEvidence
      projectId={projectId}
      resources={resources}
      cell={activeCell}
      element={sourceElement}
      onClose={() => onCell(undefined)}
    />}
  </div>
}

function EmbeddedCandidateTable({
  records,
  headers,
  elements,
  activeCell,
  onCell,
  projectId,
  onRefresh,
}: {
  records: CandidateRecord[]
  headers: string[]
  elements: DocumentElement[]
  activeCell?: CandidateCell
  onCell: (cell?: CandidateCell) => void
  projectId: string
  onRefresh: () => void
}) {
  const visibleHeaders = useMemo(() => {
    const withValues = headers.filter((header) => records.some((record) => record.data[header]))
    return (withValues.length ? withValues : headers).slice(0, 24)
  }, [headers, records])
  return <div className="embedded-table-scroll candidate">
    <table><thead><tr><th>SampleID</th>{visibleHeaders.filter((header) => header !== 'SampleID').map((header) => <th key={header}>{header}</th>)}</tr></thead>
    <tbody>{records.map((record) => <tr key={record.candidate_record_id}><th>{record.sample_id || '待确认'}</th>{visibleHeaders.filter((header) => header !== 'SampleID').map((header) => {
      const cell = record.cells[header]
      const value = record.data[header] || ''
      const source = cell?.element_id ? elements.find((element) => element.element_id === cell.element_id) : undefined
      return <td
        key={header}
        className={`${activeCell?.cell_id === cell?.cell_id ? 'active' : ''} ${cell?.evidence_status || ''}`}
        title={source ? `${elementTitle(source)} · ${cell?.extraction_method || 'unknown'}` : '无完整溯源'}
        onClick={() => onCell(cell)}
      ><input
        value={value}
        aria-label={`${record.sample_id} ${header}`}
        onClick={(event) => event.stopPropagation()}
        onFocus={() => onCell(cell)}
        onChange={(event) => {
          record.data[header] = event.target.value
          event.currentTarget.dataset.dirty = 'true'
        }}
        onBlur={async (event) => {
          if (!cell || event.currentTarget.dataset.dirty !== 'true') return
          await api.updateCell(projectId, cell.cell_id, { value: event.currentTarget.value, review_status: 'pending' })
          onRefresh()
        }}
      /></td>
    })}</tr>)}</tbody></table>
  </div>
}

function EmbeddedQuality({
  batch,
  elements,
  resources,
  activeCell,
  onCell,
  projectId,
  onRefresh,
}: {
  batch?: Awaited<ReturnType<typeof api.batch>>
  elements: DocumentElement[]
  resources: Awaited<ReturnType<typeof api.resources>>
  activeCell?: CandidateCell
  onCell: (cell?: CandidateCell) => void
  projectId: string
  onRefresh: () => void
}) {
  if (!batch) return <CompactEmptyState icon={AlertTriangle} title="尚无可质检候选数据" description="先完成资源抽取。"/>
  const cells = batch.records.flatMap((record) => Object.values(record.cells))
  const abnormal = cells.filter((cell) => cell.evidence_status === 'insufficient' || cell.alternatives.length || cell.mapping_status !== 'confirmed')
  const sourceElement = activeCell?.element_id
    ? elements.find((element) => element.element_id === activeCell.element_id)
    : undefined
  return <div className={`embedded-quality ${activeCell ? 'has-source' : ''}`}>
    <PageCommandBar title="异常与待确认项" description={`${abnormal.length} / ${cells.length}`}>
      <button onClick={async () => {
        await api.validateEvidence(projectId, batch.batch.batch_id)
        onRefresh()
      }}>重新校验证据</button>
    </PageCommandBar>
    <div className="embedded-quality-list">
      {abnormal.map((cell) => {
        const source = cell.element_id ? elements.find((element) => element.element_id === cell.element_id) : undefined
        return <button key={cell.cell_id} className={activeCell?.cell_id === cell.cell_id ? 'active' : ''} onClick={() => onCell(cell)}>
          <span><strong>{cell.target_header}</strong><small>{cell.value || '空值'} · {source ? elementTitle(source) : '来源不完整'}</small></span>
          <i className={cell.evidence_status === 'insufficient' ? 'danger' : ''}>{cell.evidence_status || cell.mapping_status}</i>
        </button>
      })}
      {!abnormal.length && <CompactEmptyState icon={Check} title="当前候选数据没有异常项" description="可进入完整工作台或人工审核继续。"/>}
    </div>
    {activeCell && <EmbeddedCellEvidence
      projectId={projectId}
      resources={resources}
      cell={activeCell}
      element={sourceElement}
      onClose={() => onCell(undefined)}
    />}
  </div>
}

function EmbeddedCellEvidence({
  projectId,
  resources,
  cell,
  element,
  onClose,
}: {
  projectId: string
  resources: Awaited<ReturnType<typeof api.resources>>
  cell: CandidateCell
  element?: DocumentElement
  onClose: () => void
}) {
  return <section className="embedded-cell-evidence">
    <PageCommandBar
      title={element ? elementTitle(element) : '来源不完整'}
      description={`${cell.target_header} · ${cell.value || '空值'} · ${cell.evidence_status || '未校验'}`}
    >
      <button className="icon-button" title="关闭来源" aria-label="关闭来源" onClick={onClose}><X size={15}/></button>
    </PageCommandBar>
    {element
      ? <PdfEvidenceViewer projectId={projectId} resources={resources} evidence={cellEvidence(cell, element)}/>
      : <CompactEmptyState icon={AlertTriangle} title="没有可定位的原始资源" description={cell.evidence_reason || '该单元格需要在质检中补充证据。'}/>}
  </section>
}
