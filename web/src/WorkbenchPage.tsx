import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AgGridReact } from 'ag-grid-react'
import type { CellClickedEvent, CellValueChangedEvent, ColDef } from 'ag-grid-community'
import { Document, Page, pdfjs } from 'react-pdf'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  ArrowRight, BoxSelect, Check, ChevronLeft, ChevronRight, FileText,
  Image, LoaderCircle, Maximize2, Minus, PanelLeftClose, PanelLeftOpen,
  PanelRightClose, PanelRightOpen, Play, Plus, Search, Sparkles, Table2,
  Trash2,
} from 'lucide-react'
import { api, authenticatedFile, watchTask } from './api'
import { AuthenticatedImage } from './AuthenticatedImage'
import { buildGridRows, resolveWorkbenchStep, WORKBENCH_STEPS } from './candidateGrid'
import { useAppStore } from './store'
import { PageCommandBar } from './WorkspaceUI'
import type { BatchPayload, CandidateCell, CandidateRecord, DocumentElement, HeaderField, ParagraphCue, TableRuleSuggestion, WorkflowEvent } from './types'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker

type ExtractionStage = 'table_standardize' | 'mapping' | 'tables' | 'paragraphs' | 'figures' | 'edit'
type RulePanelMode = 'detail' | 'create'
type StandardTableDraftState = {
  dirty: boolean
  saving: boolean
  userEdited: boolean
  sourceLabel: string
  save: () => Promise<void>
  discard: () => void
}
type SuggestionPatch = {
  source: string
  targetHeader: string
  targetUnit: string
  conversionFormula: string
  checked: boolean
}
type RuleDetail = {
  kind: 'suggestion' | 'memory'
  id: string
  source: string
  targetHeader: string
  targetUnit: string
  conversionFormula: string
  reason: string
  confidence: number
  enabled: boolean
  scope: string
  sourceType: string
  evidence: string
  onApplySuggestion?: (patch: SuggestionPatch) => void
  onIgnoreSuggestion?: () => void
}

const EXTRACTION_STAGE_LABELS: Record<ExtractionStage, string> = {
  table_standardize: '表格资源标准化',
  tables: '抽取表格资源',
  mapping: '映射表头检查',
  paragraphs: '抽取段落资源',
  figures: '抽取图像资源',
  edit: '候选结果修改',
}

function extractionStageFromQuery(stage: string): ExtractionStage | null {
  if (stage === 'standardize') return 'table_standardize'
  return ['table_standardize', 'mapping', 'tables', 'paragraphs', 'figures', 'edit'].includes(stage)
    ? stage as ExtractionStage
    : null
}

function ElementIcon({ type }: { type: DocumentElement['element_type'] }) {
  if (type === 'table') return <Table2 size={17} />
  if (type === 'figure') return <Image size={17} />
  return <FileText size={17} />
}

function elementPageLabel(element: DocumentElement) {
  const spans = element.page_spans?.length ? element.page_spans : [{ page_number: element.page_number, bbox: element.bbox }]
  const pages = Array.from(new Set(spans.map((span) => Number(span.page_number)).filter(Boolean))).sort((a, b) => a - b)
  const pageText = pages.length > 1 ? `p${pages[0]}-${pages[pages.length - 1]}` : `p${pages[0] || '?'}`
  return spans.length > 1 ? `${pageText} spans:${spans.length}` : pageText
}

const normalizedSpan = (span: { page_number?: number | string; bbox?: unknown }) => {
  const page = Number(span.page_number)
  const bbox = Array.isArray(span.bbox) ? span.bbox.map((value) => Number(value)) : []
  if (!Number.isFinite(page) || bbox.length !== 4 || bbox.some((value) => !Number.isFinite(value))) return null
  const [x0, y0, x1, y1] = bbox
  if (x1 <= x0 || y1 <= y0) return null
  return { page_number: page, bbox: [x0, y0, x1, y1] as number[] }
}

function TableResourcePreview({ table, fallbackText }: { table?: DocumentElement['raw_table']; fallbackText: string }) {
  const bodyLines = (table?.body_text || '').split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
  const bodyRows = bodyLines.map((line) => line.split(/\t|,\s*/).map((cell) => cell.trim())).filter((row) => row.some(Boolean))
  const rows = (table?.rows?.length ? table.rows : bodyRows).slice(0, 5)
  const width = Math.max(table?.headers?.length || 0, ...rows.map((row) => row.length), 0)
  if (!width || !rows.length) return <p>{fallbackText}</p>
  const headers = table?.headers?.length
    ? table.headers.slice(0, width)
    : Array.from({ length: width }, (_, index) => `列 ${index + 1}`)
  const columns = headers.slice(0, 6)
  return (
    <div className="table-resource-preview">
      <table>
        <thead><tr>{columns.map((header, index) => <th key={`${header}-${index}`}>{header || `列 ${index + 1}`}</th>)}</tr></thead>
        <tbody>{rows.map((row, rowIndex) => <tr key={rowIndex}>{columns.map((_, columnIndex) => <td key={columnIndex}>{row[columnIndex] || ''}</td>)}</tr>)}</tbody>
      </table>
    </div>
  )
}

function StandardTablePreview({
  table,
  maxRows = 40,
  onSave,
  onDraftState,
}: {
  table?: DocumentElement['raw_table']
  maxRows?: number
  onSave?: (headers: string[], rows: string[][]) => Promise<void>
  onDraftState?: (state: StandardTableDraftState) => void
}) {
  const [headers, setHeaders] = useState<string[]>(table?.headers || [])
  const [rows, setRows] = useState<string[][]>(table?.rows || [])
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    setHeaders(table?.headers || [])
    setRows(table?.rows || [])
  }, [table])
  const visibleRows = rows.slice(0, maxRows)
  const dirty = JSON.stringify(headers) !== JSON.stringify(table?.headers || []) || JSON.stringify(rows) !== JSON.stringify(table?.rows || [])
  const saveDraft = useCallback(async () => {
    if (!onSave || saving) return
    setSaving(true)
    try {
      await onSave(headers, rows)
    } finally {
      setSaving(false)
    }
  }, [headers, onSave, rows, saving])
  const discardDraft = useCallback(() => {
    setHeaders(table?.headers || [])
    setRows(table?.rows || [])
  }, [table?.headers, table?.rows])
  const updateHeader = (index: number, value: string) => {
    const next = headers.map((header, headerIndex) => headerIndex === index ? value : header)
    setHeaders(next)
  }
  const updateCell = (rowIndex: number, columnIndex: number, value: string) => {
    const absoluteRowIndex = rowIndex
    const next = rows.map((row, index) => index === absoluteRowIndex
      ? headers.map((_, colIndex) => colIndex === columnIndex ? value : (row[colIndex] || ''))
      : row)
    setRows(next)
  }
  useEffect(() => {
    onDraftState?.({
      dirty,
      saving,
      userEdited: Boolean(table?.user_edited),
      sourceLabel: table?.user_edited ? '人工编辑版' : `自动标准化 · ${table?.standardization_source || 'source'}`,
      save: saveDraft,
      discard: discardDraft,
    })
  }, [dirty, saving, table?.user_edited, table?.standardization_source, onDraftState, saveDraft, discardDraft])
  if (!headers.length || !rows.length) {
    return <div className="empty-state compact">这张表还没有可用的标准化结构。</div>
  }
  return <div className="standard-table-preview">
    <table>
      <thead><tr>{headers.map((header, index) => <th key={`${index}`}>
        <input
          value={header || `Column_${index + 1}`}
          onChange={(event) => updateHeader(index, event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') (event.currentTarget as HTMLInputElement).blur() }}
        />
      </th>)}</tr></thead>
      <tbody>{visibleRows.map((row, rowIndex) => <tr key={rowIndex}>{headers.map((_, columnIndex) => <td key={columnIndex}>
        <input
          value={row[columnIndex] || ''}
          onChange={(event) => updateCell(rowIndex, columnIndex, event.target.value)}
          onKeyDown={(event) => { if (event.key === 'Enter') (event.currentTarget as HTMLInputElement).blur() }}
        />
      </td>)}</tr>)}</tbody>
    </table>
  </div>
}

function DetailPreview({ element, projectId }: { element: DocumentElement; projectId: string }) {
  const [imageFailed, setImageFailed] = useState(false)
  if (element.element_type === 'table') {
    return <TableResourcePreview table={element.raw_table} fallbackText={element.text_content || element.caption || '表格资源'} />
  }
  if (element.preview_path && !imageFailed) {
    return <AuthenticatedImage className="inspector-preview" src={api.previewUrl(projectId, element.element_id)} onLoadError={() => setImageFailed(true)} />
  }
  return null
}

function confidenceBand(score: number) {
  if (score >= 0.65) return 'high'
  if (score >= 0.5) return 'medium'
  return 'low'
}

function confidenceLabel(score: number) {
  if (score >= 0.65) return '高'
  if (score >= 0.5) return '中'
  return '低'
}

function TaskConsole({ logs, busy }: { logs: WorkflowEvent[]; busy: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const last = logs.at(-1)
  useEffect(() => setExpanded(busy), [busy])
  if (!busy && !logs.length) return null
  return (
    <section className={`task-console ${expanded ? 'expanded' : 'collapsed'}`}>
      <button className="task-console-head" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        <strong>{busy ? <><LoaderCircle className="spin" size={15} /> 智能体处理中</> : '工作台控制台'}</strong>
        <span>{Math.round((last?.progress || 0) * 100)}% · {expanded ? '收起' : '展开'}</span>
      </button>
      {expanded && <>
        <div className="progress-track"><i style={{ width: `${(last?.progress || 0) * 100}%` }} /></div>
        <div className="console-lines">{logs.length ? logs.slice(-5).map((log) => <div key={log.event_id}><span>{log.level}</span> {log.message}</div>) : '等待任务...'}</div>
      </>}
    </section>
  )
}

function PdfWorkbench({
  projectId, articleId, elements, indexElements, resources, active, onActive, refresh, onToggle,
  selectedCount, extractableCount, figureCount, confidenceThreshold, busy, onContinue,
}: {
  projectId: string
  articleId: string
  elements: DocumentElement[]
  indexElements: DocumentElement[]
  resources: {resource_id:string;file_name:string;resource_type:string}[]
  active?: DocumentElement
  onActive: (element?: DocumentElement) => void
  refresh: () => void
  onToggle: (element: DocumentElement) => void
  selectedCount: number
  extractableCount: number
  figureCount: number
  confidenceThreshold: number
  busy: boolean
  onContinue: () => void
}) {
  const queryClient = useQueryClient()
  const pdfResources = resources.filter((resource) => resource.resource_type.includes('pdf'))
  const [resourceId, setResourceId] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [fitMode, setFitMode] = useState<'page' | 'width' | 'custom'>('page')
  const [zoom, setZoom] = useState(1)
  const [pageAspect, setPageAspect] = useState(0.773)
  const [viewportSize, setViewportSize] = useState({ width: 860, height: 620 })
  const [indexOpen, setIndexOpen] = useState(true)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [indexMode, setIndexMode] = useState<'all' | 'selected'>('all')
  const [drawing, setDrawing] = useState<{startX:number;startY:number;x:number;y:number} | null>(null)
  const [manualType, setManualType] = useState('inspect')
  const [editingText, setEditingText] = useState('')
  const [editingCaption, setEditingCaption] = useState('')
  const [resizing, setResizing] = useState<{elementId:string;handle:string;startX:number;startY:number;origBbox:number[]} | null>(null)
  const viewportRef = useRef<HTMLDivElement>(null)
  const indexRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (!resourceId && pdfResources.length) setResourceId(pdfResources[0].resource_id) }, [pdfResources, resourceId])
  useEffect(() => {
    if (!active) return
    const firstSpan = (active.page_spans || [])
      .map((span) => normalizedSpan(span))
      .find((span) => span)
    setPageNumber(firstSpan?.page_number || active.page_number)
    setResourceId(active.resource_id)
    setEditingText(active.text_content)
    setEditingCaption(active.caption || '')
  }, [active])
  useEffect(() => {
    if (!active) return
    const node = indexRef.current?.querySelector(`[data-element-id="${active.element_id}"]`) as HTMLElement | null
    node?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [active?.element_id])
  useEffect(() => {
    if (!viewportRef.current) return
    const observer = new ResizeObserver(([entry]) => setViewportSize({ width: entry.contentRect.width, height: entry.contentRect.height }))
    observer.observe(viewportRef.current)
    return () => observer.disconnect()
  }, [])
  const availableWidth = Math.max(320, viewportSize.width - 30)
  const availableHeight = Math.max(320, viewportSize.height - 30)
  const fitPageWidth = Math.min(availableWidth, availableHeight * pageAspect)
  const pageWidth = Math.round(fitMode === 'page' ? fitPageWidth : fitMode === 'width' ? availableWidth : 820 * zoom)
  const changeZoom = (delta: number) => {
    const current = fitMode === 'custom' ? zoom : pageWidth / 820
    setZoom(Math.max(0.5, Math.min(2.5, Math.round((current + delta) * 10) / 10)))
    setFitMode('custom')
  }
  const pageElementSpans = elements
    .filter((element) => element.resource_id === resourceId)
    .flatMap((element) => {
      const spans = element.page_spans?.length ? element.page_spans : [{ page_number: element.page_number, bbox: element.bbox, role: element.element_type }]
      return spans.flatMap((span, index) => {
        const clean = normalizedSpan(span)
        if (!clean || clean.page_number !== pageNumber) return []
        return [{ element, span: clean, key: `${element.element_id}:${pageNumber}:${index}` }]
      })
    })

  const beginDraw = (event: React.PointerEvent<HTMLDivElement>) => {
    if (manualType === 'inspect') return
    if (event.button !== 0 || resizing) return
    const rect = event.currentTarget.getBoundingClientRect()
    const x = (event.clientX - rect.left) / rect.width
    const y = (event.clientY - rect.top) / rect.height
    setDrawing({ startX: x, startY: y, x, y })
    event.currentTarget.setPointerCapture(event.pointerId)
  }
  const moveDraw = (event: React.PointerEvent<HTMLDivElement>) => {
    if (drawing) {
      const rect = event.currentTarget.getBoundingClientRect()
      setDrawing({ ...drawing, x: Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), y: Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height)) })
    }
    if (resizing) {
      const rect = event.currentTarget.getBoundingClientRect()
      const mx = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
      const my = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
      const [ox0, oy0, ox1, oy1] = resizing.origBbox
      let nb = [...resizing.origBbox]
      if (resizing.handle.includes('w')) nb[0] = Math.min(mx, ox1 - 0.01)
      if (resizing.handle.includes('e')) nb[2] = Math.max(mx, ox0 + 0.01)
      if (resizing.handle.includes('n')) nb[1] = Math.min(my, oy1 - 0.01)
      if (resizing.handle.includes('s')) nb[3] = Math.max(my, oy0 + 0.01)
      // Update visual in real-time via DOM
      const el = document.querySelector(`[data-eid="${resizing.elementId}"]`) as HTMLElement
      if (el) { el.style.left = `${nb[0]*100}%`; el.style.top = `${nb[1]*100}%`; el.style.width = `${(nb[2]-nb[0])*100}%`; el.style.height = `${(nb[3]-nb[1])*100}%` }
    }
  }
  const finishDraw = async () => {
    if (drawing && resourceId) {
	      const bbox = [Math.min(drawing.startX, drawing.x), Math.min(drawing.startY, drawing.y), Math.max(drawing.startX, drawing.x), Math.max(drawing.startY, drawing.y)]
	      setDrawing(null)
	      if (bbox[2] - bbox[0] < .01 || bbox[3] - bbox[1] < .01) return
	      const hit = await api.hitTestElements(projectId, articleId, { resource_id: resourceId, page_number: pageNumber, bbox })
	      const best = hit.hits?.[0]
	      if (best && hit.suggested_action === 'use_existing') {
	        onActive(best.element)
	        return
	      }
	      if (best && hit.suggested_action === 'choose_or_merge') {
	        const merge = window.confirm(`框选区域与已有资源重叠 ${Math.round(best.overlap * 100)}%。点击“确定”合并到已有资源，点击“取消”新建资源。`)
	        if (merge) {
	          const updated = await api.mergeSelection(projectId, best.element.element_id, { page_number: pageNumber, bbox })
	          onActive(updated); refresh()
	          return
	        }
	      }
	      const created = await api.addManualElement(projectId, articleId, { resource_id: resourceId, page_number: pageNumber, bbox, element_type: manualType, note: '用户在原文校核中框选' })
	      onActive(created); refresh()
      return
    }
    if (resizing) {
      const el = document.querySelector(`[data-eid="${resizing.elementId}"]`) as HTMLElement
      const nb = el ? [
        parseFloat(el.style.left) / 100, parseFloat(el.style.top) / 100,
        parseFloat(el.style.left) / 100 + parseFloat(el.style.width) / 100,
        parseFloat(el.style.top) / 100 + parseFloat(el.style.height) / 100,
      ] : resizing.origBbox
      await api.updateElement(projectId, resizing.elementId, { bbox: nb })
      setResizing(null); refresh()
    }
  }
  const drawingStyle = drawing ? { left: `${Math.min(drawing.startX, drawing.x) * 100}%`, top: `${Math.min(drawing.startY, drawing.y) * 100}%`, width: `${Math.abs(drawing.x - drawing.startX) * 100}%`, height: `${Math.abs(drawing.y - drawing.startY) * 100}%` } : undefined

  const saveEdit = async () => {
    if (!active) return
    await api.updateElement(projectId, active.element_id, { text_content: editingText, caption: editingCaption })
    refresh()
  }
	  const deleteActive = async () => {
    if (!active || active.parser_version !== 'manual') return
    await api.deleteElement(projectId, active.element_id)
    onActive(undefined); refresh()
	  }
	  const teachActive = async (label: string) => {
	    if (!active) return
	    await api.teachElement(projectId, active.element_id, { label })
	    queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
	    queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
	    refresh()
	  }

  const RESIZE_HANDLES = ['nw','n','ne','e','se','s','sw','w'] as const
  const handlePos: Record<string, {left?:string;right?:string;top?:string;bottom?:string}> = {
    nw:{left:'-4px',top:'-4px'}, n:{left:'50%',top:'-4px'}, ne:{right:'-4px',top:'-4px'},
    e:{right:'-4px',top:'50%'}, se:{right:'-4px',bottom:'-4px'}, s:{left:'50%',bottom:'-4px'},
    sw:{left:'-4px',bottom:'-4px'}, w:{left:'-4px',top:'50%'},
  }
  const listedElements = indexMode === 'selected'
    ? indexElements.filter((element) => element.selected)
    : indexElements
  const listedSelectedCount = indexElements.filter((element) => element.selected).length
  const resourceTitle = (element: DocumentElement) => (
    element.caption || element.text_content || (element.element_type === 'table' ? '表格资源' : element.element_type === 'figure' ? '图像资源' : '段落资源')
  ).replace(/\s+/g, ' ').trim()

  return (
    <div className={`pdf-workbench ${indexOpen ? '' : 'index-collapsed'} ${inspectorOpen ? '' : 'inspector-collapsed'}`}>
      <aside className="pdf-index panel">
        {indexOpen ? <>
          <div className="panel-heading"><strong>资源索引</strong><div className="panel-heading-actions"><span>{indexElements.length}</span><button className="icon-button" title="收起资源索引" onClick={() => setIndexOpen(false)}><PanelLeftClose size={16} /></button></div></div>
          <div className="resource-index-tabs" role="tablist" aria-label="资源范围">
            <button className={indexMode === 'all' ? 'active' : ''} onClick={() => setIndexMode('all')}>全部 <span>{indexElements.length}</span></button>
            <button className={indexMode === 'selected' ? 'active' : ''} onClick={() => setIndexMode('selected')}>已选 <span>{listedSelectedCount}</span></button>
          </div>
          <div className="resource-index-list" ref={indexRef}>
            {listedElements.map((element) => <div
              key={element.element_id}
              role="button"
              tabIndex={0}
              data-element-id={element.element_id}
              className={`resource-index-row ${active?.element_id === element.element_id ? 'active' : ''}`}
              onClick={() => onActive(element)}
              onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onActive(element) } }}
              onDoubleClick={(event) => { event.preventDefault(); onToggle(element) }}
            >
              <button className={`check-button ${element.selected ? 'checked' : ''}`} title={element.selected ? '移出抽取队列' : '加入抽取队列'} aria-label={element.selected ? '移出抽取队列' : '加入抽取队列'} onClick={(event) => { event.stopPropagation(); onToggle(element) }}>{element.selected && <Check size={13} />}</button>
              <ElementIcon type={element.element_type} />
              <span className="resource-index-copy"><strong>{elementPageLabel(element)}</strong><small title={resourceTitle(element)}>{resourceTitle(element)}</small></span>
              <b className={`resource-index-confidence ${confidenceBand(element.relevance_score)}`} title={`${confidenceLabel(element.relevance_score)}置信度`}>{Math.round(element.relevance_score * 100)}%</b>
            </div>)}
            {!listedElements.length && <div className="empty-state compact">{busy ? '正在发现资源...' : indexMode === 'selected' ? '当前筛选中还没有已选资源。' : '没有符合当前筛选的资源。'}</div>}
          </div>
        </> : <button className="resource-rail-expand" title="展开资源索引" onClick={() => setIndexOpen(true)}><PanelLeftOpen size={18} /><span>{indexElements.length}</span></button>}
      </aside>
      <section className="pdf-center panel">
        <div className="pdf-toolbar">
          <select value={resourceId} onChange={(event) => { setResourceId(event.target.value); setPageNumber(1) }}>{pdfResources.map((resource) => <option key={resource.resource_id} value={resource.resource_id}>{resource.file_name}</option>)}</select>
          <button className="icon-button" onClick={() => setPageNumber((page) => Math.max(1, page - 1))}><ChevronLeft size={17} /></button>
          <span>{pageNumber} / {pageCount || '?'}</span>
          <button className="icon-button" onClick={() => setPageNumber((page) => Math.min(pageCount || page + 1, page + 1))}><ChevronRight size={17} /></button>
          <div className="toolbar-spacer" />
          <div className="pdf-zoom-controls">
            <button className="icon-button" title="缩小" onClick={() => changeZoom(-0.1)}><Minus size={15} /></button>
            <button title="恢复 100%" onClick={() => { setFitMode('custom'); setZoom(1) }}>{Math.round((fitMode === 'custom' ? zoom : pageWidth / 820) * 100)}%</button>
            <button className="icon-button" title="放大" onClick={() => changeZoom(0.1)}><Plus size={15} /></button>
            <button className={fitMode === 'page' ? 'active' : ''} title="显示完整页面" onClick={() => setFitMode('page')}><Maximize2 size={15} />适页</button>
            <button className={fitMode === 'width' ? 'active' : ''} title="适应可用宽度" onClick={() => setFitMode('width')}>适宽</button>
          </div>
          <BoxSelect size={16} /><select value={manualType} onChange={(event) => setManualType(event.target.value)}><option value="inspect">点击查看资源</option><option value="table">框选表格</option><option value="figure">框选图像</option><option value="paragraph">框选段落</option></select>
          {!inspectorOpen && <button className="icon-button" title="展开定位详情" onClick={() => setInspectorOpen(true)}><PanelRightOpen size={16} /></button>}
        </div>
        <div className="pdf-scroll" ref={viewportRef}>
          {resourceId ? <Document file={authenticatedFile(api.pdfUrl(projectId, resourceId))} onLoadSuccess={({ numPages }) => setPageCount(numPages)} loading={<div className="empty-state">正在载入 PDF...</div>}>
            <div className="pdf-page-wrap" onPointerDown={beginDraw} onPointerMove={moveDraw} onPointerUp={finishDraw}>
              <Page pageNumber={pageNumber} width={pageWidth} renderAnnotationLayer={false} renderTextLayer onLoadSuccess={(page) => { const viewport = page.getViewport({ scale: 1 }); setPageAspect(viewport.width / viewport.height) }} />
              <div className="pdf-overlay">
                {pageElementSpans.map(({ element, span, key }) => <div key={key} data-eid={element.element_id} title={`${element.element_type} · ${elementPageLabel(element)}`} aria-label={`查看${element.element_type}资源 ${elementPageLabel(element)}`} className={`evidence-box-wrap ${element.element_type} ${active?.element_id === element.element_id ? 'active' : ''} ${element.selected ? 'selected' : ''}`} style={{ left: `${span.bbox[0] * 100}%`, top: `${span.bbox[1] * 100}%`, width: `${(span.bbox[2] - span.bbox[0]) * 100}%`, height: `${(span.bbox[3] - span.bbox[1]) * 100}%` }} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => { event.stopPropagation(); onActive(element) }} onDoubleClick={(event) => { event.stopPropagation(); onToggle(element) }}>
                  {active?.element_id === element.element_id && RESIZE_HANDLES.map((h) => <div key={h} className={`resize-handle ${h}`} style={handlePos[h]} onMouseDown={(e) => { e.stopPropagation(); setResizing({ elementId: element.element_id, handle: h, startX: e.clientX, startY: e.clientY, origBbox: [...element.bbox] }) }} />)}
                </div>)}
                {drawingStyle && <div className="manual-box" style={drawingStyle} />}
              </div>
            </div>
          </Document> : <div className="empty-state">当前文章没有 PDF。</div>}
        </div>
      </section>
      <aside className="pdf-inspector panel">
        <div className="panel-heading"><strong>{active ? '资源详情' : '选择汇总'}</strong><button className="icon-button" title="收起资源详情" onClick={() => setInspectorOpen(false)}><PanelRightClose size={16} /></button></div>
        {active ? <>
          <span className="type-label"><ElementIcon type={active.element_type} /> {active.element_type === 'table' ? '表格' : active.element_type === 'figure' ? '图像' : '段落'} · {elementPageLabel(active)} {active.selected ? '· 已选中' : ''}</span>
          <DetailPreview element={active} projectId={projectId} />
          <label className="edit-label">标题 / 图注<textarea value={editingCaption} onChange={(e) => setEditingCaption(e.target.value)} rows={2} /></label>
          <label className="edit-label">识别文字<textarea value={editingText} onChange={(e) => setEditingText(e.target.value)} rows={5} /></label>
	          <div className="inspector-actions">
	            <button className="primary-button" onClick={saveEdit}>保存修改</button>
	            <button className={active.selected ? '' : 'primary-button'} onClick={() => onToggle(active)}>{active.selected ? '移出抽取队列' : '加入抽取队列'}</button>
	            {active.parser_version === 'manual' && <button className="danger-button" onClick={deleteActive}><Trash2 size={14} /> 删除</button>}
	          </div>
	          <div className="teach-tags">
	            {['包含数据', '可能包含数据', '不是数据', '参考文献/排除', '样品描述', '方法描述'].map((label) => <button key={label} onClick={() => teachActive(label)}>{label}</button>)}
	          </div>
	          <RuleMemoryPanel projectId={projectId} articleId={articleId} activeElement={active} compact />
	          {!!active.score_reasons?.length && <div className="score-reason-panel"><strong>置信度原因</strong>{active.score_reasons.map((reason) => <span key={reason}>{reason}</span>)}</div>}
	          <div className="matched-fields">{active.matched_headers.map((field) => <span key={field}>{field}</span>)}</div>
        </> : <div className="resource-selection-summary">
          <div><span>表格 / 段落</span><strong>{extractableCount}</strong></div>
          <div><span>图像</span><strong>{figureCount}</strong></div>
          <div><span>当前阈值</span><strong>{Math.round(confidenceThreshold * 100)}%</strong></div>
          <p>点击左侧资源定位原文；双击证据框可加入或移出抽取队列。</p>
          <button className="primary-button wide" disabled={!selectedCount} onClick={onContinue}>进入资源抽取</button>
        </div>}
      </aside>
    </div>
  )
}

function CandidateGrid({
  payload,
  projectId,
  onRefresh,
  onCell,
  manualImageElement,
  compact = false,
}: {
  payload: BatchPayload
  projectId: string
  onRefresh: () => void
  onCell: (cell?: CandidateCell, record?: CandidateRecord) => void
  manualImageElement?: DocumentElement
  compact?: boolean
}) {
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const sampleHeader = payload.headers.find((header) => /sample.?id/i.test(header.canonical_field))?.display_header || payload.headers[0]?.display_header
  const rowData = buildGridRows(payload)
  const compactWidth = (header: HeaderField) => {
    const name = header.display_header
    const normalized = `${name} ${header.canonical_field}`.toLowerCase()
    if (name === sampleHeader) return 108
    if (/^(year|age)$/.test(normalized) || /^(year|age)\b/.test(normalized)) return 62
    if (/^(doi|toc|tn|ts|hi|oi|s1|s2|s3|cia|pia|ciw|wip)\b/.test(normalized)) return 70
    if (/title|reference|formation|location|section|author/.test(normalized)) return 104
    if (name.length <= 5) return 66
    if (name.length <= 10) return 82
    return 96
  }
  const columns = useMemo<ColDef[]>(() => payload.headers.map((header) => {
    const width = compact ? compactWidth(header) : undefined
    return {
      field: header.display_header,
      headerName: header.display_header,
      editable: true,
      width,
      minWidth: compact ? Math.min(width || 84, 72) : (header.display_header === sampleHeader ? 145 : 112),
      pinned: header.display_header === sampleHeader ? 'left' : undefined,
      cellClass: (params) => {
        const record = params.data?.__record as CandidateRecord | undefined
        const cell = record?.cells[header.display_header]
        if (!cell) return 'cell-missing'
        if (cell.evidence_status === 'insufficient') return 'cell-insufficient'
        if (cell.alternatives.length) return 'cell-conflict'
        if (cell.review_status === 'pending') return 'cell-review'
        return 'cell-confirmed'
      },
    }
  }), [compact, payload.headers, sampleHeader])
  const valueChanged = async (event: CellValueChangedEvent) => {
    const record = event.data.__record as CandidateRecord
    const header = event.colDef.field || ''
    const cell = record.cells[header]
    const nextValue = String(event.newValue ?? '')
    if (manualImageElement && nextValue) {
      await api.manualImageCell({
        project_id: projectId,
        candidate_record_id: record.candidate_record_id,
        element_id: manualImageElement.element_id,
        target_header: header,
        value: nextValue,
        evidence_note: `图像人工补值：${manualImageElement.caption || manualImageElement.text_content || manualImageElement.element_id}`,
      })
    } else if (cell) {
      await api.updateCell(projectId, cell.cell_id, { value: nextValue, review_status: 'confirmed' })
    }
    onRefresh()
  }
  const clicked = (event: CellClickedEvent) => {
    const record = event.data?.__record as CandidateRecord | undefined
    onCell(record?.cells[event.colDef.field || ''], record)
  }
  return <>
    <div className={`grid-toolbar ${compact ? 'compact' : ''}`}>
      <div className="grid-toolbar-title">
        <strong>{payload.records.length} 个样品 · {payload.headers.length} 个目标表头</strong>
        <span>已读取 {payload.applied_rules_count || 0} 条规则；空白保留为空，黄色待审核，红色冲突</span>
      </div>
      <button disabled={selectedRows.length < 2} onClick={async () => { await api.mergeRecords(projectId, selectedRows); setSelectedRows([]); onRefresh() }}>合并所选行</button>
    </div>
    <div className={`ag-theme-quartz candidate-grid ${compact ? 'compact' : ''}`}><AgGridReact theme="legacy" rowData={rowData} columnDefs={columns} defaultColDef={{ sortable: true, filter: true, resizable: true }} rowSelection={{ mode: 'multiRow' }} selectionColumnDef={{ pinned: 'left', lockPosition: 'left', width: 42, minWidth: 42, maxWidth: 42, suppressHeaderMenuButton: true }} getRowId={(params) => params.data.__record.candidate_record_id} onSelectionChanged={(event) => setSelectedRows(event.api.getSelectedRows().map((row) => row.__record.candidate_record_id))} onCellValueChanged={valueChanged} onCellClicked={clicked} /></div>
  </>
}

function RuleMemoryPanel({ projectId, articleId, activeElement, compact = false, onRediscover }: { projectId: string; articleId: string; activeElement?: DocumentElement; compact?: boolean; onRediscover?: () => void }) {
  const queryClient = useQueryClient()
  const rules = useQuery({ queryKey: ['rule-memory', projectId, articleId], queryFn: () => api.ruleMemory(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headers = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const headerOptions = headers.data?.flatMap((config) => config.headers || []) || []
  const sampleHeader = headerOptions.find((header: any) => /sample.?id/i.test(`${header.canonical_field} ${header.display_header}`))?.display_header || 'SampleID'
  const [sourceAlias, setSourceAlias] = useState('sample')
  const [targetHeader, setTargetHeader] = useState(sampleHeader)
  const [ruleType, setRuleType] = useState('source_alias')
  const extractionRules = rules.data?.extraction || []
  const addRule = async () => {
    if (!sourceAlias.trim()) return
    await api.addPreExtractionRule(projectId, articleId, {
      source_alias: sourceAlias.trim(),
      pattern: sourceAlias.trim(),
      target_header: ['resource_hint', 'resource_exclude'].includes(ruleType) ? '' : targetHeader.trim(),
      rule_type: ruleType,
      source_type: activeElement ? activeElement.element_type : 'table_header',
      element_id: activeElement?.element_id || '',
      evidence: activeElement ? (activeElement.caption || activeElement.text_content || activeElement.element_id) : '',
      scope: 'article',
      enabled: true,
    })
    setSourceAlias('')
    queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
  }
  const toggleRule = async (rule: Record<string, any>) => {
    await api.updateRuleMemory(projectId, rule.rule_id, { enabled: !rule.enabled })
    queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
  }
  return <div className={compact ? 'rule-memory-panel compact' : 'rule-memory-panel'}>
    <section className="rule-card">
      <div className="panel-heading"><strong>规则记忆</strong><span>{extractionRules.length}</span></div>
      <p className="card-desc">这些规则会先于 LLM 使用，并同步显示在左侧「规则记忆」中。</p>
      <div className="rule-form">
        <label>规则类型<select value={ruleType} onChange={(event) => setRuleType(event.target.value)}><option value="source_alias">字段别名</option><option value="sample_identifier">样品识别</option><option value="resource_hint">资源提示</option><option value="resource_exclude">资源排除</option></select></label>
        <label>原始表头 / 模式 / 提示<input value={sourceAlias} onChange={(event) => setSourceAlias(event.target.value)} placeholder="sample / WC-\\d+ / References" /></label>
        {!['resource_hint', 'resource_exclude'].includes(ruleType) && <label>目标表头<input list="target-headers" value={targetHeader} onChange={(event) => setTargetHeader(event.target.value)} placeholder="SampleID" /></label>}
        <datalist id="target-headers">{headerOptions.map((header: any) => <option key={header.header_id || header.display_header} value={header.display_header} />)}</datalist>
        <button className="primary-button" onClick={addRule}>新增规则</button>
      </div>
      <div className="rule-list">
        <article><strong>sample / sample no. / sample name</strong><span>→ {sampleHeader}</span><small>内置，已启用</small></article>
        {extractionRules.slice(0, compact ? 4 : 12).map((rule: any) => <article key={rule.rule_id} className={rule.enabled === false ? 'disabled' : ''}><strong>{rule.pattern || '—'}</strong><span>→ {rule.target_header || rule.target_field || rule.rule_type}</span><small>{rule.rule_type} · {rule.scope || 'article'} · {rule.enabled === false ? '停用' : '启用'}</small><button onClick={() => toggleRule(rule)}>{rule.enabled === false ? '启用' : '停用'}</button></article>)}
      </div>
      {onRediscover && <button onClick={onRediscover}>按规则重新发现资源</button>}
    </section>
  </div>
}

function PreflightAndCuePage({
  projectId,
  articleId,
  selectedIds,
  elements,
  setSelectedIds,
  onOpenPdf,
  onNext,
}: {
  projectId: string
  articleId: string
  selectedIds: string[]
  elements: DocumentElement[]
  setSelectedIds: (ids: string[]) => Promise<void>
  onOpenPdf: (element: DocumentElement) => void
  onNext: () => void
}) {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<'tables' | 'paragraphs' | 'rules'>('tables')
  const preflight = useQuery({ queryKey: ['table-rule-preflight', projectId, articleId], queryFn: () => api.tableRulePreflight(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const cues = useQuery({ queryKey: ['paragraph-cues', projectId, articleId], queryFn: () => api.paragraphCues(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headers = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const headerOptions = headers.data?.flatMap((config) => config.headers || []) || []
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const suggestions = preflight.data?.items || []

  useEffect(() => {
    const high = suggestions
      .filter((item) => item.confidence >= 0.78 && item.suggested_target_header)
      .map((item) => `${item.element_id}:${item.source_header}`)
    setChecked(new Set(high))
    setTargets(Object.fromEntries(suggestions.map((item) => [`${item.element_id}:${item.source_header}`, item.suggested_target_header || ''])))
  }, [suggestions.map((item) => `${item.element_id}:${item.source_header}:${item.suggested_target_header}:${item.confidence}`).join('|')])

  const confirmRules = async () => {
    setBusy(true)
    const rules = suggestions
      .map((item) => {
        const key = `${item.element_id}:${item.source_header}`
        return { ...item, target_header: targets[key] || item.suggested_target_header, ignore: !checked.has(key) }
      })
      .filter((item) => !item.ignore && item.target_header)
    await api.confirmTableRulePreflight(projectId, articleId, rules)
    await queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    await queryClient.invalidateQueries({ queryKey: ['table-rule-preflight', projectId, articleId] })
    setBusy(false)
  }

  const addCue = async (cue: ParagraphCue) => {
    await setSelectedIds([...selectedIds, cue.element_id])
    await queryClient.invalidateQueries({ queryKey: ['paragraph-cues', projectId, articleId] })
  }
  const refreshCues = async () => {
    setBusy(true)
    await api.refreshParagraphCues(projectId, articleId)
    await queryClient.invalidateQueries({ queryKey: ['paragraph-cues', projectId, articleId] })
    setBusy(false)
  }
  const groupedCues = (cues.data?.cues || []).reduce<Record<string, ParagraphCue[]>>((acc, cue) => {
    ;(acc[cue.bucket] ||= []).push(cue)
    return acc
  }, {})
  const cueLabels: Record<string, string> = {
    selected: '已选资源',
    high: '高相关线索',
    possible_missed: '可能漏选',
    low: '低相关',
    excluded: '已排除',
  }
  const elementById = new Map(elements.map((element) => [element.element_id, element]))

  return <div className="preflight-layout">
    <section className="preflight-main panel">
      <div className="preflight-tabs">
        <button className={tab === 'tables' ? 'active' : ''} onClick={() => setTab('tables')}>表格规则预检</button>
        <button className={tab === 'paragraphs' ? 'active' : ''} onClick={() => setTab('paragraphs')}>段落线索检查</button>
        <button className={tab === 'rules' ? 'active' : ''} onClick={() => setTab('rules')}>规则记忆</button>
      </div>
      {tab === 'tables' && <div className="preflight-panel">
        <div className="panel-heading"><strong>表格字段映射建议</strong><span>{suggestions.length}</span></div>
        <p className="card-desc">先确认表格原始表头如何进入目标表头。确认后的规则会在正式抽取前生效，避免 Sample、Sample No. 漏到 SampleID 之外。</p>
        <div className="table-rule-list">
          {suggestions.map((item: TableRuleSuggestion) => {
            const key = `${item.element_id}:${item.source_header}`
            return <article key={key} className={checked.has(key) ? 'selected' : ''}>
              <input type="checkbox" checked={checked.has(key)} onChange={() => setChecked((prev) => { const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next })} />
              <span className="source-header">{item.source_header}</span>
              <ArrowRight size={15} />
              <input list="preflight-target-headers" value={targets[key] ?? item.suggested_target_header ?? ''} onChange={(event) => setTargets((prev) => ({ ...prev, [key]: event.target.value }))} placeholder="目标表头" />
              <small>{Math.round(item.confidence * 100)}% · {item.reason}</small>
            </article>
          })}
          {!suggestions.length && <div className="empty-state compact">当前抽取队列里没有可预检的表格，或表格尚未解析出原始表头。</div>}
        </div>
        <datalist id="preflight-target-headers">{headerOptions.map((header: any) => <option key={header.header_id || header.display_header} value={header.display_header} />)}</datalist>
        <div className="preflight-actions"><button className="primary-button" disabled={!suggestions.length || busy} onClick={confirmRules}>确认勾选规则</button><button onClick={() => preflight.refetch()}>刷新预检</button><button onClick={onNext}>进入数据抽取</button></div>
      </div>}
      {tab === 'paragraphs' && <div className="preflight-panel">
        <div className="panel-heading"><strong>段落漏选线索</strong><span>{cues.data?.cues.length || 0}</span></div>
        <p className="card-desc">这里不做完整抽取，只检查可能被模型漏掉的数据段落。确认后加入抽取队列，第 4 步才会读取。</p>
        <div className="preflight-actions"><button onClick={refreshCues} disabled={busy}>用表格结果刷新线索</button></div>
        <div className="paragraph-cue-groups">
          {Object.entries(cueLabels).map(([bucket, label]) => <section key={bucket}>
            <h4>{label}<span>{groupedCues[bucket]?.length || 0}</span></h4>
            {(groupedCues[bucket] || []).slice(0, 12).map((cue) => {
              const element = elementById.get(cue.element_id)
              return <article key={cue.element_id} className={`cue-card ${cue.bucket}`}>
                <div><strong>{cue.page_label || `p${cue.page_number || '?'}`}</strong><span>{Math.round(cue.relevance_score * 100)}% · {cue.reason}</span></div>
                <p>{cue.text.slice(0, 260)}</p>
                <div className="cue-actions">
                  {element && <button onClick={() => onOpenPdf(element)}>定位 PDF</button>}
                  {!cue.selected && cue.bucket !== 'excluded' && <button className="primary-button" onClick={() => addCue(cue)}>加入抽取队列</button>}
                </div>
              </article>
            })}
          </section>)}
        </div>
      </div>}
      {tab === 'rules' && <RuleMemoryPanel projectId={projectId} articleId={articleId} />}
    </section>
    <aside className="preflight-side panel">
      <div className="panel-heading"><strong>本步骤做什么</strong></div>
      <ol className="stage-list">
        <li>表格先确认字段规则，优先解决 Sample → SampleID 这类稳定映射。</li>
        <li>段落只做线索检查，用户把可能有数据的段落加入队列。</li>
        <li>规则统一进入规则记忆，第 4 步抽取时会被 LLM 和本地抽取读取。</li>
      </ol>
      <button className="primary-button wide" onClick={onNext}>进入数据抽取与处理</button>
    </aside>
  </div>
}

function ProcessingStageCards({
  busy,
  selected,
  activeBatchId,
  activeStage,
  collapsed,
  onStage,
  onToggleCollapsed,
  onStandardizeTables,
  onExtractTables,
  onExtractAll,
  onValidateEvidence,
  onMerge,
  onNext,
}: {
  busy: boolean
  selected: DocumentElement[]
  activeBatchId: string
  activeStage: ExtractionStage
  collapsed: boolean
  onStage: (stage: ExtractionStage) => void
  onToggleCollapsed: () => void
  onStandardizeTables: () => void
  onExtractTables: () => void
  onExtractAll: () => void
  onValidateEvidence: () => void
  onMerge: () => void
  onNext: () => void
}) {
  const stageListRef = useRef<HTMLDivElement>(null)
  const tableCount = selected.filter((element) => element.element_type === 'table').length
  const paragraphCount = selected.filter((element) => element.element_type === 'paragraph').length
  const figureCount = selected.filter((element) => element.element_type === 'figure').length
  const cards = [
    { id: 'table_standardize' as ExtractionStage, short: '标', title: EXTRACTION_STAGE_LABELS.table_standardize, meta: `${tableCount} 个表格 · 先整理为标准表`, action: onStandardizeTables, actionText: '开始标准化表格', disabled: busy || !tableCount },
    { id: 'mapping' as ExtractionStage, short: '映', title: EXTRACTION_STAGE_LABELS.mapping, meta: '检查 source header → target header', action: undefined, actionText: '在中间确认映射规则', disabled: true },
    { id: 'tables' as ExtractionStage, short: '表', title: EXTRACTION_STAGE_LABELS.tables, meta: `${tableCount} 个表格 · 按规则生成候选`, action: onExtractTables, actionText: '开始抽取表格资源', disabled: busy || !tableCount },
    { id: 'paragraphs' as ExtractionStage, short: '段', title: EXTRACTION_STAGE_LABELS.paragraphs, meta: `${paragraphCount} 个段落 · 线索确认后局部抽取`, action: onExtractAll, actionText: '开始抽取段落资源', disabled: busy || !paragraphCount },
    { id: 'figures' as ExtractionStage, short: '图', title: EXTRACTION_STAGE_LABELS.figures, meta: `${figureCount} 个图像 · 人工补值`, action: undefined, actionText: '在中间选择图像补值', disabled: true },
    { id: 'edit' as ExtractionStage, short: '改', title: EXTRACTION_STAGE_LABELS.edit, meta: activeBatchId ? '合并、增删改查、校验证据' : '尚未生成候选表', action: onMerge, actionText: '合并候选结果', disabled: !activeBatchId },
  ]
  useEffect(() => {
    if (collapsed) return
    stageListRef.current?.querySelector<HTMLElement>(`[data-stage="${activeStage}"]`)?.scrollIntoView({ block: 'nearest' })
  }, [activeStage, collapsed])
  if (collapsed) {
    return <div className="stage-rail-collapsed">
      <button className="rail-toggle" onClick={onToggleCollapsed} title="展开抽取阶段"><ChevronRight size={16} /></button>
      {cards.map((card, index) => <button
        key={card.id}
        className={`rail-step ${activeStage === card.id ? 'active' : ''}`}
        onClick={() => onStage(card.id)}
        title={`${index + 1}. ${card.title}`}
      >
        <b>{card.short}</b>
        <small>{index + 1}</small>
      </button>)}
    </div>
  }
  return <div className="stage-card-list" ref={stageListRef}>
    <button className="stage-collapse-button" onClick={onToggleCollapsed}><ChevronLeft size={14} />收起阶段栏</button>
    {cards.map((card) => <article key={card.id} data-stage={card.id} className={`stage-card ${activeStage === card.id ? 'open' : ''}`}>
      <button className="stage-card-head" onClick={() => onStage(card.id)}>
        <span><strong>{card.title}</strong></span><b>{activeStage === card.id ? '⌃' : '⌄'}</b>
      </button>
      {activeStage === card.id && (card.action || card.id === 'edit') && <div className="stage-card-body">
        {card.action
          ? <button className={card.id === 'paragraphs' || card.id === 'tables' || card.id === 'table_standardize' ? 'primary-button' : ''} disabled={card.disabled} onClick={card.action}>{card.actionText}</button>
          : null}
        {card.id === 'edit' && <button disabled={!activeBatchId || busy} onClick={onValidateEvidence}>校验证据完整性</button>}
        {card.id === 'edit' && <button className="primary-button" onClick={onNext}>进入映射确认与质检</button>}
      </div>}
    </article>)}
  </div>
}

function SourceEvidencePanel({ cell, elements, projectId }: { cell: CandidateCell; elements: DocumentElement[]; projectId: string }) {
  const [expanded, setExpanded] = useState(false)
  const element = elements.find((item) => item.element_id === cell.element_id)
  const typeLabel = element?.element_type === 'table' ? '表格' : element?.element_type === 'figure' ? '图片' : element?.element_type === 'paragraph' ? '段落' : '资源'
  const ordinal = element ? elements.filter((item) => item.element_type === element.element_type).findIndex((item) => item.element_id === element.element_id) + 1 : 0
  const snapshot = cell.source_row_snapshot || {}
  return <div className={`source-evidence-panel ${expanded ? 'expanded' : ''}`}>
    <div className="source-evidence-head">
      <span className={`source-chip ${element?.element_type || 'paragraph'}`}>{cell.source_label || `${typeLabel}${ordinal || ''}`}</span>
      <button onClick={() => setExpanded(!expanded)}>{expanded ? '缩小' : '放大'}</button>
    </div>
    <strong>{cell.target_header}</strong>
    <small>{cell.extraction_method || 'unknown'} · {cell.evidence_status || 'weak'} · {cell.page_number ? `p${cell.page_number}` : '无页码'}</small>
    {cell.evidence_reason && <p className={`evidence-reason ${cell.evidence_status}`}>{cell.evidence_reason}</p>}
    {cell.source_quote && <blockquote>{cell.source_quote}</blockquote>}
    {!!Object.keys(snapshot).length && <div className="source-row-snapshot"><strong>原始表格行</strong>{Object.entries(snapshot).slice(0, expanded ? 80 : 12).map(([key, value]) => <span key={key}><b>{key}</b>{String(value)}</span>)}</div>}
    {element && <div className="evidence-preview"><DetailPreview element={element} projectId={projectId} />{element.element_type !== 'figure' && <p className="source-text">{element.context_text || element.text_content}</p>}</div>}
  </div>
}

function SourceMiniInspector({ cell, elements }: { cell: CandidateCell; elements: DocumentElement[] }) {
  const element = elements.find((item) => item.element_id === cell.element_id)
  const typeLabel = element?.element_type === 'table' ? '表格' : element?.element_type === 'figure' ? '图片' : element?.element_type === 'paragraph' ? '段落' : '资源'
  const ordinal = element ? elements.filter((item) => item.element_type === element.element_type).findIndex((item) => item.element_id === element.element_id) + 1 : 0
  return <div className="source-mini-inspector">
    <span className={`source-chip ${element?.element_type || 'paragraph'}`}>{cell.source_label || `${typeLabel}${ordinal || ''}`}</span>
    <strong>{cell.target_header}</strong>
    <small>{cell.original_field || '原文字段未知'} · {cell.extraction_method || 'unknown'} · {cell.evidence_status || 'weak'}</small>
  </div>
}

function TargetHeaderPicker({
  value,
  options,
  onChange,
  placeholder = '选择目标表头',
  compact = false,
}: {
  value: string
  options: HeaderField[]
  onChange: (value: string) => void
  placeholder?: string
  compact?: boolean
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState(value)
  useEffect(() => setQuery(value), [value])
  const normalizedQuery = query.trim().toLowerCase()
  const filtered = options.filter((header) => {
    if (!normalizedQuery || normalizedQuery === value.toLowerCase()) return true
    return `${header.display_header} ${header.canonical_field} ${header.target_unit} ${header.description}`.toLowerCase().includes(normalizedQuery)
  })
  const choose = (header: HeaderField) => {
    onChange(header.display_header)
    setQuery(header.display_header)
    setOpen(false)
  }
  return <div className={`target-header-picker ${compact ? 'compact' : ''}`} onBlur={() => window.setTimeout(() => setOpen(false), 120)}>
    <div className="target-header-input">
      <Search size={compact ? 13 : 15} />
      <input
        value={query}
        placeholder={placeholder}
        onFocus={() => setOpen(true)}
        onChange={(event) => { setQuery(event.target.value); setOpen(true) }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' && filtered.length) {
            event.preventDefault()
            choose(filtered[0])
          }
        }}
      />
      <button type="button" aria-label="展开目标表头" onMouseDown={(event) => event.preventDefault()} onClick={() => setOpen((current) => !current)}>⌄</button>
    </div>
    {open && <div className="target-header-menu">
      {filtered.slice(0, 80).map((header) => <button type="button" key={header.header_id} className={header.display_header === value ? 'active' : ''} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(header)}>
        <span><strong>{header.display_header}</strong>{header.target_unit && <em>{header.target_unit}</em>}</span>
        <small>{header.description || header.canonical_field}</small>
      </button>)}
      {!filtered.length && <div className="empty-state compact">没有匹配的目标表头</div>}
      {filtered.length > 80 && <small className="target-header-more">继续输入可缩小 {filtered.length} 条结果</small>}
    </div>}
  </div>
}

function TableMappingCheckPanel({
  projectId,
  articleId,
  activeRuleDetail,
  onRuleDetail,
  onCreateRule,
}: {
  projectId: string
  articleId: string
  activeRuleDetail: RuleDetail | null
  onRuleDetail: (detail: RuleDetail) => void
  onCreateRule: () => void
}) {
  const queryClient = useQueryClient()
  const preflight = useQuery({ queryKey: ['table-rule-preflight', projectId, articleId], queryFn: () => api.tableRulePreflight(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const ruleMemory = useQuery({ queryKey: ['rule-memory', projectId, articleId], queryFn: () => api.ruleMemory(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headers = useQuery({ queryKey: ['article-target-headers', projectId, articleId], queryFn: () => api.articleTargetHeaders(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headerOptions = headers.data || []
  const [assistedPreflight, setAssistedPreflight] = useState(preflight.data || null)
  const suggestions = assistedPreflight?.items || preflight.data?.items || []
  const memoryRules = (ruleMemory.data?.extraction || []).filter((rule: any) => ['source_alias', 'column_alias', 'sample_identifier'].includes(rule.rule_type || ''))
  const targetUnitByHeader = new Map(headerOptions.map((header: any) => [header.display_header, header.target_unit || '']))
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [targets, setTargets] = useState<Record<string, string>>({})
  const [suggestionOverrides, setSuggestionOverrides] = useState<Record<string, Partial<SuggestionPatch>>>({})
  const [busy, setBusy] = useState(false)
  const [assistBusy, setAssistBusy] = useState(false)
  const [assistError, setAssistError] = useState('')
  const [assistStatus, setAssistStatus] = useState('')
  const normalizeRulePart = (value: string) => value
    .toLowerCase()
    .replace(/[（]/g, '(')
    .replace(/[）]/g, ')')
    .replace(/\([^)]*\)/g, '')
    .replace(/[\s._\-/%％‰()[\]{}]+/g, '')
  const normalizeRuleKey = (source: string, target: string) => `${normalizeRulePart(source)}->${normalizeRulePart(target)}`
  const suggestionKey = (item: TableRuleSuggestion) => `${item.element_id}:${item.source_header}`
  const memoryRuleKeys = new Set(memoryRules.map((rule: any) => normalizeRuleKey(
    String(rule.pattern || rule.source_alias || ''),
    String(rule.target_header || rule.target_field || ''),
  )))
  const suggestionValue = (item: TableRuleSuggestion) => {
    const key = suggestionKey(item)
    const override = suggestionOverrides[key] || {}
    const targetHeader = override.targetHeader ?? targets[key] ?? item.suggested_target_header ?? ''
    return {
      key,
      source: override.source ?? item.source_header,
      targetHeader,
      targetUnit: override.targetUnit ?? targetUnitByHeader.get(targetHeader) ?? '',
      conversionFormula: override.conversionFormula ?? '',
      checked: override.checked ?? checked.has(key),
    }
  }
  const visibleSuggestions = suggestions.filter((item: TableRuleSuggestion) => {
    const value = suggestionValue(item)
    if (!value.targetHeader) return true
    return !memoryRuleKeys.has(normalizeRuleKey(value.source, value.targetHeader))
  })

  useEffect(() => {
    const high = suggestions
      .filter((item) => item.confidence >= 0.78 && item.suggested_target_header)
      .map((item) => `${item.element_id}:${item.source_header}`)
    setChecked(new Set(high))
    setTargets(Object.fromEntries(suggestions.map((item) => [`${item.element_id}:${item.source_header}`, item.suggested_target_header || ''])))
  }, [suggestions.map((item) => `${item.element_id}:${item.source_header}:${item.suggested_target_header}:${item.confidence}`).join('|')])

  useEffect(() => {
    setAssistedPreflight(null)
    setAssistError('')
    setAssistStatus('')
  }, [projectId, articleId])

  const assistMappings = async (sourceHeaders: string[] = []) => {
    setAssistBusy(true)
    setAssistError('')
    setAssistStatus('')
    try {
      const result = await api.assistTableRulePreflight(projectId, articleId, sourceHeaders)
      setAssistedPreflight(result)
      const actual = result.actual_model
      const callLabel = actual?.provider && actual?.model ? `实际调用：${actual.provider} / ${actual.model}` : ''
      const batchSummary = result.batches?.length
        ? ` · ${result.batches.filter((batch) => batch.status === 'success').length}/${result.batches.length} 批返回有效 JSON`
        : ''
      setAssistStatus(`${callLabel}${batchSummary}${result.config_version ? ` · 配置 ${result.config_version}` : ''}`.trim())
      if (result.llm_message) setAssistError(result.llm_message)
    } catch (error) {
      setAssistError(error instanceof Error ? error.message : String(error))
    } finally {
      setAssistBusy(false)
    }
  }

  const confirmRules = async () => {
    setBusy(true)
    const rules = visibleSuggestions
      .map((item) => {
        const value = suggestionValue(item)
        return {
          ...item,
          source_header: value.source,
          target_header: value.targetHeader,
          target_unit: value.targetUnit,
          conditions: { conversion_formula: value.conversionFormula },
          ignore: !value.checked,
        }
      })
      .filter((item) => !item.ignore && item.target_header)
    await api.confirmTableRulePreflight(projectId, articleId, rules)
    await queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    await queryClient.invalidateQueries({ queryKey: ['table-rule-preflight', projectId, articleId] })
    setAssistedPreflight(null)
    setBusy(false)
  }
  const suggestionDetail = (item: TableRuleSuggestion): RuleDetail => {
    const value = suggestionValue(item)
    return {
      kind: 'suggestion',
      id: value.key,
      source: value.source,
      targetHeader: value.targetHeader,
      targetUnit: value.targetUnit,
      conversionFormula: value.conversionFormula,
      reason: item.reason,
      confidence: item.confidence,
      enabled: value.checked,
      scope: 'article',
      sourceType: 'table_header',
      evidence: item.table_label || '',
      onApplySuggestion: (patch) => {
        setSuggestionOverrides((prev) => ({ ...prev, [value.key]: patch }))
        setTargets((prev) => ({ ...prev, [value.key]: patch.targetHeader }))
        setChecked((prev) => {
          const next = new Set(prev)
          patch.checked ? next.add(value.key) : next.delete(value.key)
          return next
        })
      },
      onIgnoreSuggestion: () => {
        setSuggestionOverrides((prev) => ({ ...prev, [value.key]: { ...(prev[value.key] || {}), checked: false } }))
        setChecked((prev) => {
          const next = new Set(prev)
          next.delete(value.key)
          return next
        })
      },
    }
  }
  const memoryDetail = (rule: any): RuleDetail => {
    let conditions: any = rule.conditions || {}
    if (typeof conditions === 'string') {
      try { conditions = JSON.parse(conditions) } catch { conditions = {} }
    }
    const targetHeader = rule.target_header || rule.target_field || ''
    return {
      kind: 'memory',
      id: rule.rule_id || `${rule.pattern}:${targetHeader}`,
      source: rule.pattern || rule.source_alias || '',
      targetHeader,
      targetUnit: rule.target_unit || targetUnitByHeader.get(targetHeader) || '',
      conversionFormula: conditions?.conversion_formula || conditions?.formula || '',
      reason: rule.reason || rule.evidence || '已保存规则',
      confidence: Number(rule.confidence || 0),
      enabled: rule.enabled !== false && rule.enabled !== 0,
      scope: rule.scope || 'article',
      sourceType: rule.source_type || rule.rule_type || '',
      evidence: rule.evidence || '',
    }
  }

  return <section className="stage-main-panel">
    <div className="mapping-rule-toolbar">
      <span>{visibleSuggestions.length} 条新建议 · {memoryRules.length} 条旧规则{assistedPreflight?.llm_assisted_count ? ` · AI 补充 ${assistedPreflight.llm_assisted_count} 条` : ''}</span>
      <div className="mapping-rule-actions">
        <button className="primary-button" onClick={onCreateRule}>新增规则</button>
        <button disabled={assistBusy || !suggestions.length} onClick={() => assistMappings()}>{assistBusy ? <><LoaderCircle className="spin" size={14} />AI 分析中</> : <><Sparkles size={14} />AI 辅助映射</>}</button>
        <button className="primary-button" disabled={!visibleSuggestions.length || busy} onClick={confirmRules}>确认勾选规则</button>
        <button onClick={() => { setAssistedPreflight(null); preflight.refetch() }}>刷新</button>
      </div>
    </div>
    {assistStatus && <div className="inline-info">{assistStatus}</div>}
    {assistError && <div className="inline-error">{assistError}</div>}
    {!!assistedPreflight?.failed_source_headers?.length && <div className="mapping-retry-row">
      <span>{assistedPreflight.failed_source_headers.length} 个字段未获得可验证的最终 JSON。</span>
      <button disabled={assistBusy} onClick={() => assistMappings(assistedPreflight.failed_source_headers || [])}>重试失败批次</button>
    </div>}
    <section className="mapping-rule-section">
      <h4>新检测出的映射建议</h4>
      <div className="table-rule-list">
      {visibleSuggestions.map((item: TableRuleSuggestion) => {
        const value = suggestionValue(item)
        const key = value.key
        const detail = suggestionDetail(item)
        return <article key={key} className={`${value.checked ? 'selected' : ''} ${activeRuleDetail?.id === key ? 'active' : ''}`} onClick={() => onRuleDetail(detail)}>
          <input type="checkbox" checked={value.checked} onChange={() => setChecked((prev) => { const next = new Set(prev); next.has(key) ? next.delete(key) : next.add(key); return next })} />
          <span className="source-header">{value.source}</span>
          <ArrowRight size={15} />
          <TargetHeaderPicker compact value={value.targetHeader} options={headerOptions} onChange={(targetHeader) => {
            setTargets((prev) => ({ ...prev, [key]: targetHeader }))
            setSuggestionOverrides((prev) => ({ ...prev, [key]: { ...(prev[key] || {}), targetHeader, targetUnit: targetUnitByHeader.get(targetHeader) || prev[key]?.targetUnit || '' } }))
          }} />
          <small><span className={`mapping-source-badge ${item.mapping_source || 'unresolved'}`}>{item.mapping_source === 'llm' ? 'AI' : item.mapping_source === 'memory' ? '规则' : item.mapping_source === 'organization_advisory' ? '组织规则' : item.mapping_source === 'knowledge' ? '知识库' : item.mapping_source === 'exact' ? '精确' : item.mapping_source === 'leaf_exact' ? '叶子字段' : item.mapping_source === 'builtin' ? '内置' : item.mapping_source === 'similarity' ? '相似' : '待定'}</span>{Math.round(item.confidence * 100)}% · {item.reason}</small>
        </article>
      })}
      {!visibleSuggestions.length && <div className="empty-state compact">没有新的映射建议；可能都已被规则记忆覆盖。</div>}
      </div>
    </section>
    <section className="mapping-rule-section">
      <h4>已保存的规则记忆</h4>
      <div className="saved-rule-list">
        {memoryRules.map((rule: any) => {
          const detail = memoryDetail(rule)
          return <button key={detail.id} className={activeRuleDetail?.id === detail.id ? 'active' : ''} onClick={() => onRuleDetail(detail)}>
            <strong>{detail.source || '—'} <ArrowRight size={13} /> {detail.targetHeader || '未指定'}</strong>
            <small>{detail.sourceType} · {detail.enabled ? '启用' : '停用'} · {Math.round(detail.confidence * 100)}%</small>
          </button>
        })}
        {!memoryRules.length && <div className="empty-state compact">还没有保存的字段映射规则。</div>}
      </div>
    </section>
  </section>
}

function ParagraphExtractionPanel({
  projectId,
  articleId,
  selectedIds,
  elements,
  setSelectedIds,
  onOpenPdf,
  onCueDetail,
}: {
  projectId: string
  articleId: string
  selectedIds: string[]
  elements: DocumentElement[]
  setSelectedIds: (ids: string[]) => Promise<void>
  onOpenPdf: (element: DocumentElement) => void
  onCueDetail: (cue: ParagraphCue) => void
}) {
  const queryClient = useQueryClient()
  const cues = useQuery({ queryKey: ['paragraph-cues', projectId, articleId], queryFn: () => api.paragraphCues(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const addCue = async (cue: ParagraphCue) => {
    await setSelectedIds([...selectedIds, cue.element_id])
    await queryClient.invalidateQueries({ queryKey: ['paragraph-cues', projectId, articleId] })
  }
  const groupedCues = (cues.data?.cues || []).reduce<Record<string, ParagraphCue[]>>((acc, cue) => {
    ;(acc[cue.bucket] ||= []).push(cue)
    return acc
  }, {})
  const cueLabels: Record<string, string> = {
    selected: '已选段落',
    high: '高相关线索',
    possible_missed: '可能漏选',
    low: '低相关',
    excluded: '已排除',
  }
  const elementById = new Map(elements.map((element) => [element.element_id, element]))

  return <section className="stage-main-panel">
    <div className="paragraph-cue-groups">
      {Object.entries(cueLabels).map(([bucket, label]) => <section key={bucket}>
        <h4>{label}<span>{groupedCues[bucket]?.length || 0}</span></h4>
        {(groupedCues[bucket] || []).slice(0, 12).map((cue) => {
          const element = elementById.get(cue.element_id)
          return <article key={cue.element_id} className={`cue-card ${cue.bucket}`}>
            <div><strong>{cue.page_label || `p${cue.page_number || '?'}`}</strong><span>{Math.round(cue.relevance_score * 100)}% · {cue.reason}</span></div>
            <p>{cue.text.slice(0, 260)}</p>
            <div className="cue-actions">
              <button onClick={() => onCueDetail(cue)}>详情</button>
              {element && <button onClick={() => onOpenPdf(element)}>定位 PDF</button>}
              {!cue.selected && cue.bucket !== 'excluded' && <button className="primary-button" onClick={() => addCue(cue)}>加入抽取队列</button>}
            </div>
          </article>
        })}
      </section>)}
    </div>
  </section>
}

function TableStandardizationPanel({
  tables,
  activeTable,
  onSelect,
  onSave,
}: {
  tables: DocumentElement[]
  activeTable?: DocumentElement
  onSelect: (element: DocumentElement) => void
  onSave: (element: DocumentElement, headers: string[], rows: string[][]) => Promise<void>
}) {
  const current = activeTable || tables[0]
  const [draftState, setDraftState] = useState<StandardTableDraftState | null>(null)
  useEffect(() => {
    setDraftState(null)
  }, [current?.element_id])
  return <section className="stage-main-panel table-standardization-panel">
    <div className="standardization-toolbar">
      <div className="standardization-title">
        <strong>标准化后的表格</strong>
        <span>{current?.raw_table?.table_standardized ? '已标准化' : '待标准化'} · {current?.raw_table?.headers?.length || 0} 列 · {current?.raw_table?.rows?.length || 0} 行</span>
      </div>
      <div className="standardization-actions">
        {draftState && <span className={draftState.userEdited ? 'standard-table-state edited' : 'standard-table-state'}>{draftState.sourceLabel}</span>}
        {draftState?.dirty && <span className="standard-table-dirty">未保存</span>}
        <button type="button" disabled={!draftState?.dirty || draftState.saving} onClick={() => void draftState?.save()}>{draftState?.saving ? '保存中...' : '保存修改'}</button>
        <button type="button" disabled={!draftState?.dirty || draftState.saving} onClick={() => draftState?.discard()}>撤销未保存</button>
        <select value={current?.element_id || ''} onChange={(event) => {
          const next = tables.find((item) => item.element_id === event.target.value)
          if (next) onSelect(next)
        }}>
          {!tables.length && <option value="">没有表格资源</option>}
          {tables.map((table, index) => <option key={table.element_id} value={table.element_id}>表格 {index + 1} · {elementPageLabel(table)}</option>)}
        </select>
      </div>
    </div>
    {current ? <StandardTablePreview
      table={current.raw_table}
      maxRows={80}
      onSave={(headers, rows) => onSave(current, headers, rows)}
      onDraftState={setDraftState}
    /> : <div className="empty-state">先在前一步把表格资源加入抽取队列。</div>}
  </section>
}

function TableStandardizationSidePanel({
  tables,
  activeTable,
  onSelect,
}: {
  tables: DocumentElement[]
  activeTable?: DocumentElement
  onSelect: (element: DocumentElement) => void
}) {
  const current = activeTable || tables[0]
  return <div className="table-standardization-side">
    <div className="figure-source-list compact table-selector-row">
      {tables.map((element, index) => <button key={element.element_id} className={current?.element_id === element.element_id ? 'active' : ''} onClick={() => onSelect(element)}>
        <ElementIcon type={element.element_type} />
        <span>表格 {index + 1}<small>{elementPageLabel(element)} · {element.raw_table?.table_standardized ? '已标准化' : '待标准化'}</small></span>
      </button>)}
      {!tables.length && <div className="empty-state compact">当前抽取队列没有表格资源。</div>}
    </div>
    {current ? <>
      <div className="standardization-meta">
        <span className="source-chip table">原始资源 · {elementPageLabel(current)}</span>
        <small>表头置信度 {Math.round(Number(current.raw_table?.header_confidence || 0) * 100)}%</small>
      </div>
      <div className="raw-header-box">
        <strong>原始/多级表头</strong>
        {(current.raw_table?.header_rows || [current.raw_table?.source_headers || current.raw_table?.original_headers || current.raw_table?.headers || []]).slice(0, 4).map((row, rowIndex) => (
          <p key={rowIndex}>{row.filter(Boolean).join(' | ') || '—'}</p>
        ))}
      </div>
      <div className="standardization-preview-scroll">
        <TableResourcePreview table={current.raw_table} fallbackText={current.text_content || current.caption || '表格资源'} />
        {current.caption && <p>{current.caption}</p>}
      </div>
    </> : <div className="empty-state compact">选择一个表格查看原始结构。</div>}
  </div>
}

function FigureExtractionPanel({
  children,
}: {
  children: ReactNode
}) {
  return <section className="stage-main-panel figure-stage-panel">
    <div className="figure-candidate-area">{children}</div>
  </section>
}

function FigureEvidencePanel({
  figures,
  activeFigure,
  onSelect,
}: {
  figures: DocumentElement[]
  activeFigure?: DocumentElement
  onSelect: (element: DocumentElement) => void
}) {
  const [zoom, setZoom] = useState(1)
  const [imageFailed, setImageFailed] = useState(false)
  useEffect(() => {
    setImageFailed(false)
    setZoom(1)
  }, [activeFigure?.element_id])
  const zoomOut = () => setZoom((value) => Math.max(0.5, Math.round((value - 0.25) * 100) / 100))
  const zoomIn = () => setZoom((value) => Math.min(3, Math.round((value + 0.25) * 100) / 100))
  return <div className="figure-evidence-panel">
    {activeFigure ? <>
      <div className="source-evidence-head">
        <span className="source-chip figure">当前图像 · {elementPageLabel(activeFigure)}</span>
        <div className="figure-zoom-controls">
          <button onClick={zoomOut}>-</button>
          <button onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
          <button onClick={zoomIn}>+</button>
          <button onClick={() => setZoom(0.75)}>适宽</button>
        </div>
      </div>
      <div className="figure-zoom-viewport">
        {activeFigure.preview_path && !imageFailed
          ? <AuthenticatedImage className="figure-large-preview" style={{ width: `${zoom * 100}%` }} src={api.previewUrl(useAppStore.getState().projectId, activeFigure.element_id)} onLoadError={() => setImageFailed(true)} />
          : <div className="figure-placeholder">{activeFigure.text_content || activeFigure.caption || '当前图像没有可用预览。'}</div>}
      </div>
      <p>{activeFigure.caption || activeFigure.text_content || '当前图像没有图注。'}</p>
    </> : <p className="card-desc">先在第 2 步把图像加入抽取队列。</p>}
    <div className="figure-source-list compact">
      {figures.map((element) => <button key={element.element_id} className={activeFigure?.element_id === element.element_id ? 'active' : ''} onClick={() => onSelect(element)}>
        <ElementIcon type={element.element_type} />
        <span>{elementPageLabel(element)}<small>{element.caption || element.text_content.slice(0, 72)}</small></span>
      </button>)}
      {!figures.length && <div className="empty-state compact">当前抽取队列里没有图像资源。</div>}
    </div>
  </div>
}

function MappingRuleDetailPanel({
  projectId,
  articleId,
  detail,
  onChanged,
  onDeleted,
}: {
  projectId: string
  articleId: string
  detail: RuleDetail | null
  onChanged: (detail: RuleDetail) => void
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const headers = useQuery({ queryKey: ['article-target-headers', projectId, articleId], queryFn: () => api.articleTargetHeaders(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headerOptions = headers.data || []
  const [source, setSource] = useState(detail?.source || '')
  const [targetHeader, setTargetHeader] = useState(detail?.targetHeader || '')
  const [targetUnit, setTargetUnit] = useState(detail?.targetUnit || '')
  const [formula, setFormula] = useState(detail?.conversionFormula || '')
  const [scope, setScope] = useState(detail?.scope || 'article')
  const [enabled, setEnabled] = useState(detail?.enabled ?? true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setSource(detail?.source || '')
    setTargetHeader(detail?.targetHeader || '')
    setTargetUnit(detail?.targetUnit || '')
    setFormula(detail?.conversionFormula || '')
    setScope(detail?.scope || 'article')
    setEnabled(detail?.enabled ?? true)
  }, [detail?.id])

  useEffect(() => {
    const target = headerOptions.find((header: any) => header.display_header === targetHeader)
    if (target?.target_unit && !targetUnit) setTargetUnit(target.target_unit)
  }, [headerOptions, targetHeader, targetUnit])

  if (!detail) {
    return <div className="empty-state compact">点击中间的规则，查看映射字段、目标单位和转换说明。</div>
  }

  const persisted = detail.kind === 'memory'
  const save = async () => {
    if (!source.trim() || !targetHeader.trim()) return
    if (!persisted) {
      const updated = {
        ...detail,
        source: source.trim(),
        targetHeader: targetHeader.trim(),
        targetUnit: targetUnit.trim(),
        conversionFormula: formula.trim(),
        enabled,
      }
      detail.onApplySuggestion?.({
        source: updated.source,
        targetHeader: updated.targetHeader,
        targetUnit: updated.targetUnit,
        conversionFormula: updated.conversionFormula,
        checked: enabled,
      })
      onChanged(updated)
      return
    }
    setSaving(true)
    await api.updateRuleMemory(projectId, detail.id, {
      pattern: source.trim(),
      target_header: targetHeader.trim(),
      target_unit: targetUnit.trim(),
      scope,
      enabled,
      conditions: { conversion_formula: formula.trim() },
    })
    await queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    await queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
    await queryClient.invalidateQueries({ queryKey: ['table-rule-preflight', projectId, articleId] })
    setSaving(false)
    onChanged({
      ...detail,
      source: source.trim(),
      targetHeader: targetHeader.trim(),
      targetUnit: targetUnit.trim(),
      conversionFormula: formula.trim(),
      scope,
      enabled,
    })
  }
  const remove = async () => {
    if (!persisted) {
      detail.onIgnoreSuggestion?.()
      onChanged({ ...detail, enabled: false })
      return
    }
    if (!window.confirm('删除这条规则记忆？这不会删除已经抽取的候选数据。')) return
    setSaving(true)
    await api.deleteRuleMemory(projectId, detail.id)
    await queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    await queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
    await queryClient.invalidateQueries({ queryKey: ['table-rule-preflight', projectId, articleId] })
    setSaving(false)
    onDeleted()
  }

  return <div className="rule-detail-panel">
    <span className={`source-chip ${detail.kind === 'memory' ? 'table' : 'paragraph'}`}>{detail.kind === 'memory' ? '旧规则' : '新建议'}</span>
    <h3>{detail.source || '—'} <ArrowRight size={15} /> {detail.targetHeader || '未指定'}</h3>
    {!persisted && <p className="card-desc">这里修改的是本次预检建议；点击“确认勾选规则”后才会保存到规则记忆。</p>}
    <label>原始字段 / 别名
      <input value={source} onChange={(event) => setSource(event.target.value)} />
    </label>
    <label>映射字段
      <TargetHeaderPicker value={targetHeader} options={headerOptions} onChange={(nextHeader) => {
        setTargetHeader(nextHeader)
        setTargetUnit(headerOptions.find((header) => header.display_header === nextHeader)?.target_unit || '')
      }} />
    </label>
    <label>映射单位
      <input value={targetUnit} onChange={(event) => setTargetUnit(event.target.value)} />
    </label>
    <label>如何计算转换
      <textarea rows={3} value={formula} onChange={(event) => setFormula(event.target.value)} placeholder="例如 Na2O wt% -> Na wt%，乘以 0.7419；无转换可留空。" />
    </label>
    <div className="rule-detail-grid">
      {persisted ? <label>作用范围
        <select value={scope} onChange={(event) => setScope(event.target.value)}>
          <option value="article">当前文章</option>
          <option value="project">整个工作区</option>
        </select>
      </label> : null}
      <label>状态
        <select value={enabled ? 'enabled' : 'disabled'} onChange={(event) => setEnabled(event.target.value === 'enabled')}>
          <option value="enabled">{persisted ? '启用' : '勾选'}</option>
          <option value="disabled">{persisted ? '停用' : '忽略'}</option>
        </select>
      </label>
    </div>
    <dl>
      <dt>来源类型</dt><dd>{detail.sourceType || '未知'}</dd>
      <dt>置信度</dt><dd>{detail.confidence ? `${Math.round(detail.confidence * 100)}%` : '未记录'}</dd>
    </dl>
    <div className="rule-detail-actions">
      <button className="primary-button" disabled={saving || !source.trim() || !targetHeader.trim()} onClick={save}>{persisted ? '保存修改' : '应用到本次建议'}</button>
      <button className={persisted ? 'danger-button' : ''} disabled={saving} onClick={remove}>{persisted ? <><Trash2 size={14} />删除规则</> : '忽略本建议'}</button>
    </div>
    {detail.reason && <p className="card-desc">{detail.reason}</p>}
    {detail.evidence && <blockquote>{detail.evidence}</blockquote>}
  </div>
}

function RuleCreatePanel({
  projectId,
  articleId,
  onSaved,
}: {
  projectId: string
  articleId: string
  onSaved: (detail: RuleDetail) => void
}) {
  const queryClient = useQueryClient()
  const headers = useQuery({ queryKey: ['article-target-headers', projectId, articleId], queryFn: () => api.articleTargetHeaders(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const headerOptions = headers.data || []
  const sampleHeader = headerOptions.find((header: any) => /sample.?id/i.test(`${header.canonical_field} ${header.display_header}`))?.display_header || 'SampleID'
  const [source, setSource] = useState('sample')
  const [targetHeader, setTargetHeader] = useState(sampleHeader)
  const [targetUnit, setTargetUnit] = useState('')
  const [formula, setFormula] = useState('')
  const [scope, setScope] = useState('article')
  const [enabled, setEnabled] = useState(true)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    const target = headerOptions.find((header: any) => header.display_header === targetHeader)
    if (target?.target_unit) setTargetUnit(target.target_unit)
  }, [headerOptions, targetHeader])

  const save = async () => {
    if (!source.trim() || !targetHeader.trim()) return
    setSaving(true)
    const saved: any = await api.addPreExtractionRule(projectId, articleId, {
      source_alias: source.trim(),
      pattern: source.trim(),
      target_header: targetHeader.trim(),
      target_unit: targetUnit.trim(),
      rule_type: 'source_alias',
      source_type: 'table_header',
      evidence: '用户在映射表头检查右侧表单新增',
      conditions: { conversion_formula: formula.trim() },
      confidence: 0.98,
      scope,
      enabled,
    })
    await queryClient.invalidateQueries({ queryKey: ['rule-memory', projectId, articleId] })
    await queryClient.invalidateQueries({ queryKey: ['table-rule-preflight', projectId, articleId] })
    setSaving(false)
    onSaved({
      kind: 'memory',
      id: saved.rule_id || `${source}:${targetHeader}`,
      source,
      targetHeader,
      targetUnit,
      conversionFormula: formula,
      reason: '用户手动新增',
      confidence: 0.98,
      enabled,
      scope,
      sourceType: 'table_header',
      evidence: '用户在映射表头检查右侧表单新增',
    })
  }

  return <div className="rule-create-panel">
    <strong>新增映射规则</strong>
    <label>原始字段 / 别名<input value={source} onChange={(event) => setSource(event.target.value)} placeholder="sample / Trace.Mo" /></label>
    <label>映射字段<TargetHeaderPicker value={targetHeader} options={headerOptions} onChange={(nextHeader) => {
      setTargetHeader(nextHeader)
      setTargetUnit(headerOptions.find((header) => header.display_header === nextHeader)?.target_unit || '')
    }} placeholder="选择目标表头" /></label>
    <label>映射单位<input value={targetUnit} onChange={(event) => setTargetUnit(event.target.value)} placeholder="ppm / wt% / %" /></label>
    <label>如何计算转换<textarea value={formula} onChange={(event) => setFormula(event.target.value)} rows={3} placeholder="如 Na = Na2O × 0.741857；无转换可留空" /></label>
    <label>作用范围<select value={scope} onChange={(event) => setScope(event.target.value)}><option value="article">当前文章</option><option value="project">整个工作区</option></select></label>
    <label className="inline-check"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} /> 启用规则</label>
    <button className="primary-button wide" disabled={saving || !source.trim() || !targetHeader.trim()} onClick={save}>保存规则</button>
  </div>
}

function ParagraphCueDetailPanel({
  cue,
  element,
  onOpenPdf,
}: {
  cue: ParagraphCue | null
  element?: DocumentElement
  onOpenPdf: (element: DocumentElement) => void
}) {
  if (!cue) return <div className="empty-state compact">点击段落卡片的“详情”，查看完整段落、相关表头和入队状态。</div>
  return <div className="paragraph-detail-panel">
    <span className="source-chip paragraph">{cue.page_label || `p${cue.page_number || '?'}`}</span>
    <h3>{cue.bucket === 'selected' ? '已选段落' : cue.bucket === 'possible_missed' ? '可能漏选段落' : '段落线索'}</h3>
    <dl>
      <dt>相关度</dt><dd>{Math.round(cue.relevance_score * 100)}%</dd>
      <dt>状态</dt><dd>{cue.selected ? '已加入抽取队列' : '未加入抽取队列'}</dd>
      <dt>原因</dt><dd>{cue.reason || '未记录'}</dd>
    </dl>
    {!!cue.matched_headers.length && <div className="matched-fields">{cue.matched_headers.map((field) => <span key={field}>{field}</span>)}</div>}
    <p className="source-text">{element?.context_text || cue.text}</p>
    {element && <button className="primary-button wide" onClick={() => onOpenPdf(element)}>定位 PDF</button>}
  </div>
}

function CandidateEditToolbar({
  projectId,
  articleId,
  batchId,
  activeRecord,
  activeCell,
  onRefresh,
  onClearActiveCell,
}: {
  projectId: string
  articleId: string
  batchId: string
  activeRecord: CandidateRecord | null
  activeCell?: CandidateCell
  onRefresh: () => void
  onClearActiveCell: () => void
}) {
  const [sampleId, setSampleId] = useState('')
  const addRecord = async () => {
    await api.manualRecord({ project_id: projectId, article_id: articleId, batch_id: batchId, sample_id: sampleId })
    setSampleId('')
    onRefresh()
  }
  const deleteRecord = async () => {
    if (!activeRecord) return
    await api.deleteRecord(projectId, activeRecord.candidate_record_id)
    onClearActiveCell()
    onRefresh()
  }
  const deleteCell = async () => {
    if (!activeCell) return
    await api.deleteCell(projectId, activeCell.cell_id)
    onClearActiveCell()
    onRefresh()
  }
  return <div className="candidate-edit-toolbar">
    <input value={sampleId} onChange={(event) => setSampleId(event.target.value)} placeholder="新增 SampleID" />
    <button className="primary-button" disabled={!batchId} onClick={addRecord}>新增样品行</button>
    <button disabled={!activeCell} onClick={deleteCell}>清空当前单元格</button>
    <button className="danger-button" disabled={!activeRecord} onClick={deleteRecord}>删除当前行</button>
  </div>
}

export default function WorkbenchPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const agentRunId = searchParams.get('agent_run_id') || ''
  const requestedView = searchParams.get('view')
  const showRulesView = requestedView === 'rules'
  const handedOffStage = searchParams.get('stage') || ''
  const returnTo = searchParams.get('return_to') || '/'
  const { projectId, articleId, activeElement, setActiveElement, activeCell, setActiveCell, logs, addLog, clearLogs } = useAppStore()
  const step = resolveWorkbenchStep(requestedView, searchParams.get('step'))
  const setStep = useCallback((nextStep: number, nextStage?: string) => {
    const next = new URLSearchParams(searchParams)
    next.set('view', nextStep === 0 ? 'resources' : nextStep === 1 ? 'extract' : 'quality')
    next.delete('step')
    if (nextStep !== 1) next.delete('stage')
    else if (nextStage !== undefined) {
      if (nextStage) next.set('stage', nextStage)
      else next.delete('stage')
    }
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])
  const [extractionStage, setExtractionStage] = useState<ExtractionStage>(() => (
    showRulesView
      ? 'mapping'
      : extractionStageFromQuery(handedOffStage) || 'table_standardize'
  ))
  const [activeRecord, setActiveRecord] = useState<CandidateRecord | null>(null)
  const [activeRuleDetail, setActiveRuleDetail] = useState<RuleDetail | null>(null)
  const [rulePanelMode, setRulePanelMode] = useState<RulePanelMode>('detail')
  const [activeParagraphCue, setActiveParagraphCue] = useState<ParagraphCue | null>(null)
  const [stageRailCollapsed, setStageRailCollapsed] = useState(false)
  const [busy, setBusy] = useState(false)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const [confidenceThreshold, setConfidenceThreshold] = useState(() => {
    const stored = window.localStorage.getItem('geochem.resourceConfidenceThreshold')
    const value = stored ? Number(stored) : 0.65
    return Number.isFinite(value) ? value : 0.65
  })
  const returnToChat = useMutation({
    mutationFn: () => api.resumeAgentFromWorkbench(projectId, agentRunId),
    onSuccess: (result) => navigate(result.return_path || returnTo),
  })
  const autoSelectionRef = useRef('')
  const session = useQuery({ queryKey: ['session', projectId, articleId], queryFn: () => api.session(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const elements = useQuery({ queryKey: ['elements', projectId, articleId], queryFn: () => api.elements(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const resources = useQuery({ queryKey: ['resources', projectId, articleId], queryFn: () => api.resources(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const activeBatchId = session.data?.active_batch_id || ''
  const batch = useQuery({ queryKey: ['batch', projectId, activeBatchId], queryFn: () => api.batch(projectId, activeBatchId), enabled: Boolean(projectId && activeBatchId) })

  useEffect(() => {
    if (!showRulesView) return
    setStep(1, 'mapping')
    setExtractionStage('mapping')
  }, [setStep, showRulesView])
  useEffect(() => {
    const requestedStage = extractionStageFromQuery(handedOffStage)
    if (step === 1 && requestedStage) setExtractionStage(requestedStage)
  }, [handedOffStage, step])

  const refreshWorkbench = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ['session', projectId, articleId] })
    queryClient.invalidateQueries({ queryKey: ['elements', projectId, articleId] })
    queryClient.invalidateQueries({ queryKey: ['batch', projectId] })
  }, [articleId, projectId, queryClient])
  const runTask = useCallback(async (starter: () => Promise<{task_id:string}>, nextStep?: number) => {
    clearLogs(); setBusy(true)
    const { task_id } = await starter()
    watchTask(projectId, task_id, addLog, async () => {
      const task = await api.task(projectId, task_id).catch(() => undefined)
      setBusy(false)
      // If task result has batch_id, directly set it so batch query can fire immediately
      const resultBatchId = task?.result?.batch_id
      if (resultBatchId) {
        queryClient.setQueryData(['session', projectId, articleId], (old: any) => old ? {...old, active_batch_id: resultBatchId} : old)
      }
      refreshWorkbench()
      if (task?.status === 'completed' && nextStep !== undefined) setStep(nextStep)
    })
  }, [addLog, articleId, clearLogs, projectId, queryClient, refreshWorkbench])
  useEffect(() => {
    if (!busy && session.data && ['pending', 'stale'].includes(session.data.discovery_status) && resources.data?.some((resource) => resource.resource_type.includes('pdf'))) {
      runTask(() => api.discover(projectId, articleId))
    }
  }, [articleId, busy, projectId, resources.data, runTask, session.data])

  const all = elements.data || []
  const visible = all.filter((element) => (typeFilter === 'all' || element.element_type === typeFilter) && (!search || `${element.text_content} ${element.caption} ${element.matched_headers.join(' ')}`.toLowerCase().includes(search.toLowerCase())))
  const selected = all.filter((element) => element.selected)
  const selectedTables = selected.filter((element) => element.element_type === 'table')
  const selectedFigures = selected.filter((element) => element.element_type === 'figure')
  const extractableSelected = selected.filter((element) => element.element_type !== 'figure')
  const activeTable = (activeElement?.element_type === 'table' ? activeElement : selectedTables[0]) as DocumentElement | undefined
  const activeFigure = (activeElement?.element_type === 'figure' ? activeElement : selectedFigures[0]) as DocumentElement | undefined
  const toggle = async (element: DocumentElement) => {
    const ids = element.selected ? selected.filter((item) => item.element_id !== element.element_id).map((item) => item.element_id) : [...selected.map((item) => item.element_id), element.element_id]
    await api.selectElements(projectId, articleId, ids); refreshWorkbench()
  }
  const setSelectedIds = async (ids: string[]) => {
    await api.selectElements(projectId, articleId, Array.from(new Set(ids)))
    refreshWorkbench()
  }
  const applyThresholdSelection = async () => {
    window.localStorage.setItem('geochem.resourceConfidenceThreshold', String(confidenceThreshold))
    await setSelectedIds(all.filter((element) => element.relevance_score >= confidenceThreshold).map((element) => element.element_id))
  }
  const selectVisible = async () => {
    await setSelectedIds([...selected.map((element) => element.element_id), ...visible.map((element) => element.element_id)])
  }
  const clearVisible = async () => {
    const visibleIds = new Set(visible.map((element) => element.element_id))
    await setSelectedIds(selected.filter((element) => !visibleIds.has(element.element_id)).map((element) => element.element_id))
  }
  const openInPdf = (element: DocumentElement) => { setActiveElement(element); setStep(0) }
  useEffect(() => {
    window.localStorage.setItem('geochem.resourceConfidenceThreshold', String(confidenceThreshold))
  }, [confidenceThreshold])
  useEffect(() => {
    const signature = `${session.data?.discovery_hash || session.data?.updated_at || ''}:${all.map((element) => `${element.element_id}:${element.relevance_score}`).join('|')}`
    if (session.data?.discovery_status !== 'completed' || !all.length || selected.length || !signature || autoSelectionRef.current === signature) return
    autoSelectionRef.current = signature
    const ids = all.filter((element) => element.relevance_score >= confidenceThreshold).map((element) => element.element_id)
    if (ids.length) api.selectElements(projectId, articleId, ids).then(refreshWorkbench).catch(() => undefined)
  }, [all, articleId, confidenceThreshold, projectId, refreshWorkbench, selected.length, session.data])

  const refreshBatch = () => queryClient.invalidateQueries({ queryKey: ['batch', projectId, activeBatchId] })
  const selectCandidateCell = (cell?: CandidateCell, record?: CandidateRecord) => {
    setActiveCell(cell)
    setActiveRecord(record || null)
  }
  const switchExtractionStage = (stage: ExtractionStage) => {
    setExtractionStage(stage)
    setStep(1, stage === 'table_standardize' ? 'standardize' : stage)
    setActiveCell(undefined)
    setActiveRecord(null)
    if (stage !== 'mapping') {
      setRulePanelMode('detail')
      setActiveRuleDetail(null)
    }
    if (stage !== 'paragraphs') setActiveParagraphCue(null)
  }
  const candidateGrid = (manualImageElement?: DocumentElement, compact = false) => batch.data
    ? <CandidateGrid payload={batch.data} projectId={projectId} onRefresh={refreshBatch} onCell={selectCandidateCell} manualImageElement={manualImageElement} compact={compact} />
    : <div className="empty-state"><Table2 size={34} /><h3>尚未生成样品级候选表</h3><p>先运行“抽取表格资源”，再检查表头映射、抽取段落和补充图像数据。</p></div>
  const extractionMain = (() => {
    if (extractionStage === 'table_standardize') {
      return <TableStandardizationPanel
        tables={selectedTables}
        activeTable={activeTable}
        onSelect={(element) => setActiveElement(element)}
        onSave={async (element, headers, rows) => {
          const updated = await api.updateStandardTable(projectId, element.element_id, headers, rows, '用户在标准化表格中编辑')
          setActiveElement(updated)
          queryClient.invalidateQueries({ queryKey: ['elements', projectId, articleId] })
        }}
      />
    }
    if (extractionStage === 'mapping') {
      return <TableMappingCheckPanel
        projectId={projectId}
        articleId={articleId}
        activeRuleDetail={activeRuleDetail}
        onRuleDetail={(detail) => { setActiveRuleDetail(detail); setRulePanelMode('detail'); setActiveCell(undefined) }}
        onCreateRule={() => { setRulePanelMode('create'); setActiveRuleDetail(null); setActiveCell(undefined) }}
      />
    }
    if (extractionStage === 'paragraphs') {
      return <ParagraphExtractionPanel
        projectId={projectId}
        articleId={articleId}
        selectedIds={selected.map((element) => element.element_id)}
        elements={all}
        setSelectedIds={setSelectedIds}
        onOpenPdf={openInPdf}
        onCueDetail={(cue) => { setActiveParagraphCue(cue); setActiveCell(undefined) }}
      />
    }
    if (extractionStage === 'tables' && !batch.data) {
      const tables = selected.filter((element) => element.element_type === 'table')
      return <section className="stage-main-panel">
        <div className="panel-heading"><strong>抽取表格资源</strong><span>{tables.length}</span></div>
        <p className="card-desc">从已加入抽取队列的表格资源生成第一版样品级候选表。表格值会标记为本地表格解析或 LLM 表格抽取来源。</p>
        <div className="stage-resource-list">{tables.map((element) => <button key={element.element_id} onClick={() => openInPdf(element)}><ElementIcon type={element.element_type} />{elementPageLabel(element)}<small>{element.caption || element.text_content.slice(0, 80)}</small></button>)}</div>
      </section>
    }
    if (extractionStage === 'tables') {
      return <section className="candidate-sheet panel">
        <div className="stage-inline-note">当前显示表格资源生成的候选结果。下一步进入“映射表头检查”，确认原始表头是否正确进入目标表头。</div>
        {candidateGrid(undefined, true)}
      </section>
    }
    if (extractionStage === 'figures') {
      return <FigureExtractionPanel>
        <section className="candidate-sheet embedded">
          {candidateGrid(activeFigure, true)}
        </section>
      </FigureExtractionPanel>
    }
    if (extractionStage === 'edit') {
      return <section className="candidate-sheet panel">
        {activeBatchId && <CandidateEditToolbar
          projectId={projectId}
          articleId={articleId}
          batchId={activeBatchId}
          activeRecord={activeRecord}
          activeCell={activeCell}
          onRefresh={refreshBatch}
          onClearActiveCell={() => { setActiveCell(undefined); setActiveRecord(null) }}
        />}
        {candidateGrid()}
      </section>
    }
    return <section className="candidate-sheet panel">
      {candidateGrid()}
    </section>
  })()
  const extractionRight = activeCell
    ? <SourceEvidencePanel cell={activeCell} elements={all} projectId={projectId} />
    : <div className="stage-side-help">
        <strong>{EXTRACTION_STAGE_LABELS[extractionStage]}</strong>
        {extractionStage === 'table_standardize' && <p>先把复杂、多级或错位的 PDF 表格整理为标准 headers + rows；后续映射和抽取都读取这个标准表。</p>}
        {extractionStage === 'tables' && <p>表格资源会优先生成候选表。抽取后进入“映射表头检查”确认 source header 是否正确进入目标表头。</p>}
        {extractionStage === 'mapping' && <p>确认 `sample / specimen / sample no.` 等原始表头到目标表头的规则，避免后续抽取漏填 SampleID。</p>}
        {extractionStage === 'paragraphs' && <p>段落只读取已入队的资源和线索检查中加入的段落。点击段落可回到 PDF 定位原文。</p>}
        {extractionStage === 'figures' && <p>图像阶段不调用视觉模型。用户根据图像和图注在候选表里人工补值，系统保留图像来源。</p>}
        {extractionStage === 'edit' && <p>这里处理合并、增删改查和证据完整性。证据不足的值会继续留待质检。</p>}
      </div>
  const stageRightPanel = (() => {
    if (activeCell) return <SourceEvidencePanel cell={activeCell} elements={all} projectId={projectId} />
    if (extractionStage === 'mapping') {
      return rulePanelMode === 'create'
        ? <RuleCreatePanel projectId={projectId} articleId={articleId} onSaved={(detail) => { setActiveRuleDetail(detail); setRulePanelMode('detail') }} />
        : <MappingRuleDetailPanel
            projectId={projectId}
            articleId={articleId}
            detail={activeRuleDetail}
            onChanged={(detail) => setActiveRuleDetail(detail)}
            onDeleted={() => setActiveRuleDetail(null)}
          />
    }
    if (extractionStage === 'table_standardize') {
      return <TableStandardizationSidePanel
        tables={selectedTables}
        activeTable={activeTable}
        onSelect={(element) => setActiveElement(element)}
      />
    }
    if (extractionStage === 'paragraphs') {
      const element = activeParagraphCue ? all.find((item) => item.element_id === activeParagraphCue.element_id) : undefined
      return <ParagraphCueDetailPanel cue={activeParagraphCue} element={element} onOpenPdf={openInPdf} />
    }
    if (extractionStage === 'figures') {
      return <FigureEvidencePanel figures={selectedFigures} activeFigure={activeFigure} onSelect={(element) => setActiveElement(element)} />
    }
    if (extractionStage === 'edit') {
      return <div className="stage-side-help">
        <strong>候选结果修改</strong>
        <p>点击候选表单元格后，这里会显示 `表格1 / 段落8 / 图片3` 来源、原始行快照和证据状态。未选中单元格时可以在左侧卡片执行合并和证据校验。</p>
        <RuleMemoryPanel projectId={projectId} articleId={articleId} activeElement={activeElement} compact onRediscover={() => runTask(() => api.rediscoverWithRules(projectId, articleId), 0)} />
      </div>
    }
    return extractionRight
  })()

  return <div className={`page workbench-page gpt-workspace-page workbench-step-${step}`}>
    {agentRunId && <div className="agent-return-banner"><span>由对话助手交接到此处。保存人工修改后，可返回原对话查看变更摘要并继续。</span><button className="primary-button" onClick={() => returnToChat.mutate()} disabled={returnToChat.isPending}>保存并返回原对话</button></div>}
    <PageCommandBar
      className="workbench-titlebar"
      title="抽取工作台"
      description={WORKBENCH_STEPS[step]}
      status={<span className={`status-pill ${session.data?.discovery_status}`}>{session.data?.discovery_status || '未开始'}</span>}
    />
    <div className="stepper">{WORKBENCH_STEPS.map((label, index) => <div
      key={label}
      role="button"
      tabIndex={0}
      className={`step-button ${step === index ? 'active' : index < step ? 'done' : ''}`}
      onClick={() => setStep(index)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setStep(index) } }}
    ><span>{index < step ? <Check size={14} /> : index + 1}</span>{label}</div>)}</div>

    <div className="workbench-stage">
      {step === 0 && <div className="resource-review-workspace">
        <div className="resource-review-toolbar panel">
          <button className="primary-button" disabled={busy || !articleId} onClick={() => runTask(() => api.discover(projectId, articleId))}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}重新发现</button>
          <select value={typeFilter} aria-label="筛选资源类型" onChange={(event) => setTypeFilter(event.target.value)}><option value="all">全部资源</option><option value="table">表格</option><option value="figure">图像</option><option value="paragraph">相关段落</option></select>
          <label className="threshold-control">阈值 <input type="range" min="0" max="1" step="0.01" value={confidenceThreshold} onChange={(event) => setConfidenceThreshold(Number(event.target.value))} /><strong>{Math.round(confidenceThreshold * 100)}%</strong></label>
          <button onClick={applyThresholdSelection}>按阈值预选</button>
          <button disabled={!visible.length} onClick={selectVisible}>全选</button>
          <button disabled={!visible.some((element) => element.selected)} onClick={clearVisible}>取消全选</button>
          <div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索页码、表头或证据" /></div>
          <span className="resource-selection-count">已选 <strong>{selected.length}</strong></span>
          <button className="primary-button" disabled={!selected.length} title={!selected.length ? '请先选择至少一个资源' : '进入资源抽取与候选处理'} onClick={() => setStep(1)}>进入资源抽取 <ArrowRight size={15} /></button>
        </div>
        <PdfWorkbench
          projectId={projectId}
          articleId={articleId}
          elements={all}
          indexElements={activeElement && !visible.some((element) => element.element_id === activeElement.element_id) ? [activeElement, ...visible] : visible}
          resources={resources.data || []}
          active={activeElement}
          onActive={setActiveElement}
          refresh={refreshWorkbench}
          onToggle={toggle}
          selectedCount={selected.length}
          extractableCount={extractableSelected.length}
          figureCount={selectedFigures.length}
          confidenceThreshold={confidenceThreshold}
          busy={busy}
          onContinue={() => setStep(1)}
        />
      </div>}
      {step === 1 && <div className={`processing-layout ${extractionStage === 'figures' ? 'figure-mode' : ''} ${stageRailCollapsed ? 'rail-collapsed' : ''}`}>
        <section className="source-strip panel">
          {!stageRailCollapsed && <div className="panel-heading"><strong>资源抽取</strong><span>{selected.length}</span></div>}
          <ProcessingStageCards
            busy={busy}
            selected={selected}
            activeBatchId={activeBatchId}
            activeStage={extractionStage}
            collapsed={stageRailCollapsed}
            onStage={switchExtractionStage}
            onToggleCollapsed={() => setStageRailCollapsed((value) => !value)}
            onStandardizeTables={() => runTask(() => api.standardizeTables(projectId, articleId, false), 1)}
            onExtractTables={() => runTask(() => api.extractTables(projectId, articleId, false), 1)}
            onExtractAll={() => runTask(() => api.extractParagraphs(projectId, articleId, [], true), 1)}
            onValidateEvidence={async () => { if (activeBatchId) { await api.validateEvidence(projectId, activeBatchId); queryClient.invalidateQueries({ queryKey: ['batch', projectId, activeBatchId] }) } }}
            onMerge={async () => { await api.mergeCandidates(projectId, articleId); if (activeBatchId) await api.validateEvidence(projectId, activeBatchId); refreshWorkbench(); }}
            onNext={() => setStep(2)}
          />
        </section>
        {extractionMain}
        <aside className="processing-side panel">
          <div className="panel-heading"><strong>{activeCell ? '单元格来源' : '阶段详情'}</strong></div>
          {stageRightPanel}
        </aside>
      </div>}
      {step === 2 && <MappingReview payload={batch.data} elements={all} projectId={projectId} headerConfigId={session.data?.header_config_id || ''} onRefresh={() => {
        queryClient.invalidateQueries({ queryKey: ['batch', projectId, activeBatchId] })
        queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
        queryClient.invalidateQueries({ queryKey: ['reviews', projectId] })
      }} onNavigate={(path) => { refreshWorkbench(); navigate(path) }} />}
    </div>
    <TaskConsole logs={logs} busy={busy} />
  </div>
}

function MappingReview({ payload, elements, projectId, headerConfigId, onRefresh, onNavigate }: { payload?: BatchPayload; elements: DocumentElement[]; projectId: string; headerConfigId: string; onRefresh: () => void; onNavigate: (path: string) => void }) {
  const { articleId } = useAppStore()
  const activeBatchId = payload?.batch.batch_id || ''
  const [activeCell, setActiveCell] = useState<CandidateCell | null>(null)
  const [qualityTab, setQualityTab] = useState<'mappings' | 'table' | 'paragraph' | 'figure' | 'finish'>('mappings')
  const [formula, setFormula] = useState('')
  const [factor, setFactor] = useState('')
  const [llmLoading, setLlmLoading] = useState(false)
  const [llmSuggestion, setLlmSuggestion] = useState<{formula:string|null;factor:number;explanation:string}|null>(null)
  const [selectedCells, setSelectedCells] = useState<Set<string>>(new Set())
  const [error, setError] = useState('')
  const initRef = useRef('')

  // Load target headers from assigned config
  const headersQuery = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const allHeaders = headersQuery.data || []
  const currentConfig = allHeaders.find((config) => config.config_id === headerConfigId)
  const configHeaders = currentConfig?.headers || []
  const fieldMappings = useQuery({ queryKey: ['field-mappings', projectId, activeBatchId], queryFn: () => api.fieldMappings(projectId, activeBatchId), enabled: Boolean(projectId && activeBatchId) })

  const allCells = payload?.records.flatMap((record) => Object.values(record.cells)) || []
  const elementTypeById = new Map(elements.map((element) => [element.element_id, element.element_type]))
  const visibleCells = allCells.filter((cell) => {
    if (qualityTab === 'table') return elementTypeById.get(cell.element_id || '') === 'table'
    if (qualityTab === 'paragraph') return elementTypeById.get(cell.element_id || '') === 'paragraph'
    if (qualityTab === 'figure') return elementTypeById.get(cell.element_id || '') === 'figure' || cell.extraction_method === 'manual_image'
    return true
  })
  const unresolved = visibleCells.filter((cell) =>
    cell.mapping_status !== 'confirmed' || cell.review_status === 'pending' || cell.alternatives.length
    || cell.evidence_status === 'insufficient'
  )
  const insufficient = allCells.filter((cell) => cell.evidence_status === 'insufficient')
  const conflicts = allCells.filter((cell) => cell.alternatives.length)

  useEffect(() => {
    const signature = unresolved.map((cell) => cell.cell_id).join('|')
    if (signature && initRef.current !== signature && selectedCells.size === 0) {
      initRef.current = signature
      setSelectedCells(new Set(unresolved.map((cell) => cell.cell_id)))
    }
  }, [selectedCells.size, unresolved])

  const toggleCell = (cellId: string) => {
    setSelectedCells(prev => { const next = new Set(prev); next.has(cellId) ? next.delete(cellId) : next.add(cellId); return next })
  }
  const toggleAll = () => {
    if (selectedCells.size === unresolved.length) setSelectedCells(new Set())
    else setSelectedCells(new Set(unresolved.map(c => c.cell_id)))
  }

  const selectCell = async (cell: CandidateCell) => {
    setActiveCell(cell); setFormula(''); setFactor(''); setLlmSuggestion(null)
    if (cell.original_unit && cell.target_unit && cell.original_unit !== cell.target_unit) {
      setLlmLoading(true)
      try {
        const suggestion = await api.suggestConversion(projectId, cell.cell_id)
        setLlmSuggestion(suggestion)
        setFormula(suggestion.formula || '')
        setFactor(String(suggestion.factor ?? ''))
      } catch (err) {
        setLlmSuggestion(null)
        setError(err instanceof Error ? err.message : 'AI 换算建议失败')
      }
      setLlmLoading(false)
    }
  }

  const doConfirm = async () => {
    if (!activeCell) return
    setError('')
    try {
      await api.confirmCell(projectId, activeCell.cell_id, {
        target_field: activeCell.target_field,
        target_unit: activeCell.target_unit,
        formula: formula || undefined,
        conversion_factor: factor ? parseFloat(factor) : undefined,
        llm_suggestion: llmSuggestion,
      })
      setActiveCell(null); onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '确认映射失败')
    }
  }

  const doBatchConfirm = async () => {
    const items = unresolved.filter(c => selectedCells.has(c.cell_id) && c.evidence_status !== 'insufficient').map(cell => ({
      cell_id: cell.cell_id,
      target_field: cell.target_field,
      target_unit: cell.target_unit,
    }))
    if (!items.length) return
    setError('')
    try {
      const result = await api.batchConfirmMappings(projectId, articleId, items)
      if (result.errors?.length) setError(`${result.errors.length} 条映射确认失败，请逐条检查。`)
      setSelectedCells(new Set()); onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量确认失败')
    }
  }

  if (!headerConfigId || !currentConfig) {
    return <div className="mapping-review"><section className="panel mapping-list"><div className="empty-state"><FileText size={32} /><h3>当前文章还没有绑定表头配置</h3><p>请先在文献导入或表头配置页面为文章选择一套表头，再进行字段映射。</p></div></section></div>
  }

  return <div className="mapping-review">
    <section className="panel mapping-list">
      <div className="quality-tabs">
        <button className={qualityTab === 'mappings' ? 'active' : ''} onClick={() => setQualityTab('mappings')}>字段映射检查</button>
        <button className={qualityTab === 'table' ? 'active' : ''} onClick={() => setQualityTab('table')}>表格数据检查</button>
        <button className={qualityTab === 'paragraph' ? 'active' : ''} onClick={() => setQualityTab('paragraph')}>段落数据检查</button>
        <button className={qualityTab === 'figure' ? 'active' : ''} onClick={() => setQualityTab('figure')}>图像数据检查</button>
        <button className={qualityTab === 'finish' ? 'active' : ''} onClick={() => setQualityTab('finish')}>完成并进入审核</button>
      </div>
      {qualityTab === 'mappings' ? <>
        <div className="panel-heading"><strong>当前文章字段映射</strong><span>{fieldMappings.data?.length || 0}</span></div>
        <div className="field-mapping-list">
          {(fieldMappings.data || []).map((mapping: any, index) => <article key={`${mapping.original_field}-${mapping.target_header}-${index}`}>
            <strong>{mapping.original_field || '原文候选'} <ArrowRight size={14} /> {mapping.target_header}</strong>
            <span>{mapping.extraction_method || 'unknown'} · {Math.round(Number(mapping.confidence || 0) * 100)}% · {mapping.count || 0} 个值</span>
            <small>{mapping.applied_rule_id ? `规则 ${mapping.applied_rule_id}` : '无规则'} · {mapping.evidence_status || 'weak'}</small>
          </article>)}
          {!fieldMappings.data?.length && <div className="empty-state compact">当前批次还没有字段映射。</div>}
        </div>
      </> : qualityTab === 'finish' ? <>
        <div className="panel-heading"><strong>质检汇总</strong></div>
        <div className="quality-summary">
          <span><b>{allCells.length}</b>候选单元格</span>
          <span><b>{unresolved.length}</b>当前页待确认</span>
          <span className={insufficient.length ? 'danger' : ''}><b>{insufficient.length}</b>证据不足</span>
          <span className={conflicts.length ? 'danger' : ''}><b>{conflicts.length}</b>冲突</span>
        </div>
        {insufficient.length > 0 && <p className="form-error">仍有证据不足字段，请先逐条确认或拒绝；这些字段不会被批量确认。</p>}
        <button className="primary-button wide" onClick={() => onNavigate('/review')}>进入左侧栏人工审核</button>
      </> : <>
      <div className="panel-heading"><strong>{qualityTab === 'table' ? '表格数据检查' : qualityTab === 'paragraph' ? '段落数据检查' : '图像数据检查'}</strong><span>{unresolved.length}</span></div>
      <div className="mapping-toolbar">
        <label><input type="checkbox" checked={selectedCells.size === unresolved.length && unresolved.length > 0} onChange={toggleAll} /> 全选</label>
        <button className="primary-button" disabled={!selectedCells.size || unresolved.some((cell) => selectedCells.has(cell.cell_id) && cell.evidence_status === 'insufficient')} onClick={doBatchConfirm}>确认选中 ({selectedCells.size})</button>
      </div>
      {unresolved.map((cell) => <div className={`mapping-row ${activeCell?.cell_id === cell.cell_id ? 'active' : ''}`} key={cell.cell_id}>
        <input type="checkbox" checked={selectedCells.has(cell.cell_id)} onChange={() => toggleCell(cell.cell_id)} onClick={(e) => e.stopPropagation()} />
        <div onClick={() => selectCell(cell)} style={{flex:1,cursor:'pointer'}}><strong>{cell.original_field || '原文候选'} <ArrowRight size={14} /> {cell.target_header}</strong>
        <p>{cell.value} {cell.original_unit} · 目标单位 {cell.target_unit || '未指定'}</p>
        {cell.mapping_status === 'auto_applied' && <span className="auto-badge">自动应用 · 人工确认</span>}</div>
        <span className={`evidence-badge ${cell.evidence_status || 'weak'}`}>{cell.evidence_status || 'weak'}</span>
        <span className={`risk-tag ${cell.risk_level}`}>{cell.risk_level}</span>
      </div>)}
      {!unresolved.length && <div className="empty-state"><Check size={32} /><h3>当前没有待确认映射</h3></div>}
      </>}
    </section>
    <aside className="panel mapping-detail">
      <div className="panel-heading"><strong>映射确认</strong></div>
      {error && <p className="form-error">{error}</p>}
      {activeCell ? <>
        <h3>{activeCell.original_field} → {activeCell.target_header}</h3>
        <dl><dt>当前值</dt><dd>{activeCell.value}</dd><dt>原始单位</dt><dd>{activeCell.original_unit}</dd><dt>目标单位</dt><dd>{activeCell.target_unit}</dd><dt>置信度</dt><dd>{Math.round(activeCell.confidence * 100)}%</dd><dt>证据状态</dt><dd>{activeCell.evidence_status || 'weak'}</dd></dl>
        <SourceEvidencePanel cell={activeCell} elements={elements} projectId={projectId} />
        <label>目标字段<select value={activeCell.target_field} onChange={(e) => setActiveCell({...activeCell, target_field: e.target.value})}>
          {configHeaders.map((h: Record<string, string>) => <option key={h.display_header || String(h)} value={h.display_header || String(h)}>{h.display_header || String(h)}</option>)}
        </select></label>
        {llmLoading && <p className="llm-hint">AI 正在分析换算关系...</p>}
        {llmSuggestion && <div className="llm-suggestion"><strong>AI 建议</strong><p>{llmSuggestion.explanation}</p></div>}
        <label>换算公式<textarea value={formula} onChange={(e) => setFormula(e.target.value)} rows={2} placeholder={llmSuggestion?.formula || '如 Na = Na2O × 0.741857'} /></label>
        <label>转换因子<input value={factor} onChange={(e) => setFactor(e.target.value)} type="number" step="any" placeholder={llmSuggestion?.factor?.toString() || '1.0'} /></label>
        <div className="mapping-actions">
          <button className="primary-button" disabled={activeCell.evidence_status === 'insufficient'} onClick={doConfirm}>确认此条</button>
          <button onClick={() => setActiveCell(null)}>跳过</button>
        </div>
        <p className="rule-hint">如果你修改了 AI 建议的字段、单位或公式，修改内容将自动保存为映射规则。</p>
      </> : <div className="empty-state compact">点击左侧映射查看详情，或直接批量确认选中项。</div>}
      <button className="primary-button wide" style={{marginTop:16}} onClick={() => onNavigate('/review')}>进入人工审核</button>
    </aside>
  </div>
}
