import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { AgGridReact } from 'ag-grid-react'
import type { CellClickedEvent, CellValueChangedEvent, ColDef } from 'ag-grid-community'
import { Document, Page, pdfjs } from 'react-pdf'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  ArrowRight, BoxSelect, Check, ChevronLeft, ChevronRight, Eye, FileText,
  Image, LoaderCircle, Play, Search, Sparkles, Table2, Trash2,
} from 'lucide-react'
import { api, watchTask } from './api'
import { buildGridRows, WORKBENCH_STEPS } from './candidateGrid'
import { useAppStore } from './store'
import type { BatchPayload, CandidateCell, CandidateRecord, DocumentElement, WorkflowEvent } from './types'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker

function ElementIcon({ type }: { type: DocumentElement['element_type'] }) {
  if (type === 'table') return <Table2 size={17} />
  if (type === 'figure') return <Image size={17} />
  return <FileText size={17} />
}

function ElementCard({ element, active, selected, onOpen, onToggle }: {
  element: DocumentElement; active?: boolean; selected: boolean
  onOpen: () => void; onToggle: () => void
}) {
  return (
    <article className={`element-card ${active ? 'active' : ''}`} onClick={onOpen}>
      <button className={`check-button ${selected ? 'checked' : ''}`} onClick={(event) => { event.stopPropagation(); onToggle() }} aria-label={selected ? '移出抽取队列' : '加入抽取队列'}>
        {selected && <Check size={14} />}
      </button>
      <div className="element-card-body">
        <div className="element-meta"><ElementIcon type={element.element_type} /><strong>{element.element_type === 'table' ? '表格' : element.element_type === 'figure' ? '图像' : '相关段落'}</strong><span>第 {element.page_number} 页</span><b>{Math.round(element.relevance_score * 100)}%</b></div>
        {element.preview_path && element.element_type !== 'paragraph'
          ? <img src={api.previewUrl(useAppStore.getState().projectId, element.element_id)} alt="资源预览" />
          : <p>{element.text_content}</p>}
        <div className="matched-fields">{element.matched_headers.slice(0, 7).map((field) => <span key={field}>{field}</span>)}</div>
      </div>
    </article>
  )
}

function TaskConsole({ logs, busy }: { logs: WorkflowEvent[]; busy: boolean }) {
  const last = logs.at(-1)
  return (
    <section className="task-console">
      <div className="task-console-head"><strong>{busy ? <><LoaderCircle className="spin" size={15} /> 智能体处理中</> : '工作台控制台'}</strong><span>{Math.round((last?.progress || 0) * 100)}%</span></div>
      <div className="progress-track"><i style={{ width: `${(last?.progress || 0) * 100}%` }} /></div>
      <div className="console-lines">{logs.length ? logs.slice(-5).map((log) => <div key={log.event_id}><span>{log.level}</span> {log.message}</div>) : '等待任务...'}</div>
    </section>
  )
}

function PdfWorkbench({ projectId, articleId, elements, resources, active, onActive, refresh, onToggle }: {
  projectId: string; articleId: string; elements: DocumentElement[]; resources: {resource_id:string;file_name:string;resource_type:string}[]
  active?: DocumentElement; onActive: (element: DocumentElement) => void; refresh: () => void; onToggle: (element: DocumentElement) => void
}) {
  const pdfResources = resources.filter((resource) => resource.resource_type.includes('pdf'))
  const [resourceId, setResourceId] = useState('')
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [pageWidth, setPageWidth] = useState(820)
  const [drawing, setDrawing] = useState<{startX:number;startY:number;x:number;y:number} | null>(null)
  const [manualType, setManualType] = useState('table')
  const [editingText, setEditingText] = useState('')
  const [editingCaption, setEditingCaption] = useState('')
  const [resizing, setResizing] = useState<{elementId:string;handle:string;startX:number;startY:number;origBbox:number[]} | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => { if (!resourceId && pdfResources.length) setResourceId(pdfResources[0].resource_id) }, [pdfResources, resourceId])
  useEffect(() => { if (active) { setPageNumber(active.page_number); setResourceId(active.resource_id); setEditingText(active.text_content); setEditingCaption(active.caption || '') } }, [active])
  useEffect(() => {
    const update = () => setPageWidth(Math.min(980, Math.max(520, (containerRef.current?.clientWidth || 860) - 42)))
    update(); window.addEventListener('resize', update); return () => window.removeEventListener('resize', update)
  }, [])
  const pageElements = elements.filter((element) => element.resource_id === resourceId && element.page_number === pageNumber)

  const beginDraw = (event: React.PointerEvent<HTMLDivElement>) => {
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
    onActive(undefined as unknown as DocumentElement); refresh()
  }

  const RESIZE_HANDLES = ['nw','n','ne','e','se','s','sw','w'] as const
  const handlePos: Record<string, {left?:string;right?:string;top?:string;bottom?:string}> = {
    nw:{left:'-4px',top:'-4px'}, n:{left:'50%',top:'-4px'}, ne:{right:'-4px',top:'-4px'},
    e:{right:'-4px',top:'50%'}, se:{right:'-4px',bottom:'-4px'}, s:{left:'50%',bottom:'-4px'},
    sw:{left:'-4px',bottom:'-4px'}, w:{left:'-4px',top:'50%'},
  }

  return (
    <div className="pdf-workbench">
      <aside className="pdf-index panel">
        <div className="panel-heading"><strong>自动定位资源</strong><span>{elements.length}</span></div>
        <div className="resource-index-list">{elements.map((element) => <button key={element.element_id} className={active?.element_id === element.element_id ? 'active' : ''} onClick={() => onActive(element)}><ElementIcon type={element.element_type} /><span>第 {element.page_number} 页<br /><small>{element.caption || element.text_content.slice(0, 48)}</small></span></button>)}</div>
      </aside>
      <section className="pdf-center panel" ref={containerRef}>
        <div className="pdf-toolbar">
          <select value={resourceId} onChange={(event) => { setResourceId(event.target.value); setPageNumber(1) }}>{pdfResources.map((resource) => <option key={resource.resource_id} value={resource.resource_id}>{resource.file_name}</option>)}</select>
          <button className="icon-button" onClick={() => setPageNumber((page) => Math.max(1, page - 1))}><ChevronLeft size={17} /></button>
          <span>{pageNumber} / {pageCount || '?'}</span>
          <button className="icon-button" onClick={() => setPageNumber((page) => Math.min(pageCount || page + 1, page + 1))}><ChevronRight size={17} /></button>
          <div className="toolbar-spacer" />
          <BoxSelect size={16} /><select value={manualType} onChange={(event) => setManualType(event.target.value)}><option value="table">框选表格</option><option value="figure">框选图像</option><option value="paragraph">框选段落</option></select>
        </div>
        <div className="pdf-scroll">
          {resourceId ? <Document file={api.pdfUrl(projectId, resourceId)} onLoadSuccess={({ numPages }) => setPageCount(numPages)} loading={<div className="empty-state">正在载入 PDF...</div>}>
            <div className="pdf-page-wrap" onPointerDown={beginDraw} onPointerMove={moveDraw} onPointerUp={finishDraw}>
              <Page pageNumber={pageNumber} width={pageWidth} renderAnnotationLayer renderTextLayer />
              <div className="pdf-overlay">
                {pageElements.map((element) => <div key={element.element_id} data-eid={element.element_id} title={`${element.element_type} · ${element.matched_headers.join(', ')}\n双击取消选择`} className={`evidence-box-wrap ${element.element_type} ${active?.element_id === element.element_id ? 'active' : ''} ${element.selected ? 'selected' : ''}`} style={{ left: `${element.bbox[0] * 100}%`, top: `${element.bbox[1] * 100}%`, width: `${(element.bbox[2] - element.bbox[0]) * 100}%`, height: `${(element.bbox[3] - element.bbox[1]) * 100}%` }} onClick={(event) => { event.stopPropagation(); onActive(element) }} onDoubleClick={(event) => { event.stopPropagation(); onToggle(element) }}>
                  {active?.element_id === element.element_id && RESIZE_HANDLES.map((h) => <div key={h} className={`resize-handle ${h}`} style={handlePos[h]} onMouseDown={(e) => { e.stopPropagation(); setResizing({ elementId: element.element_id, handle: h, startX: e.clientX, startY: e.clientY, origBbox: [...element.bbox] }) }} />)}
                </div>)}
                {drawingStyle && <div className="manual-box" style={drawingStyle} />}
              </div>
            </div>
          </Document> : <div className="empty-state">当前文章没有 PDF。</div>}
        </div>
      </section>
      <aside className="pdf-inspector panel">
        <div className="panel-heading"><strong>定位详情</strong></div>
        {active ? <>
          <span className="type-label"><ElementIcon type={active.element_type} /> {active.element_type} · 第 {active.page_number} 页 {active.selected ? '· 已选中' : ''}</span>
          {active.preview_path && <img className="inspector-preview" src={api.previewUrl(projectId, active.element_id)} />}
          <label className="edit-label">标题 / 图注<textarea value={editingCaption} onChange={(e) => setEditingCaption(e.target.value)} rows={2} /></label>
          <label className="edit-label">识别文字<textarea value={editingText} onChange={(e) => setEditingText(e.target.value)} rows={5} /></label>
          <div className="inspector-actions">
            <button className="primary-button" onClick={saveEdit}>保存修改</button>
            <button className={active.selected ? '' : 'primary-button'} onClick={() => onToggle(active)}>{active.selected ? '移出抽取队列' : '加入抽取队列'}</button>
            {active.parser_version === 'manual' && <button className="danger-button" onClick={deleteActive}><Trash2 size={14} /> 删除</button>}
          </div>
          <div className="matched-fields">{active.matched_headers.map((field) => <span key={field}>{field}</span>)}</div>
        </> : <div className="empty-state compact">点击左侧资源查看原文位置，或在页面上拖拽框选。<br />双击资源可取消选择。</div>}
      </aside>
    </div>
  )
}

function CandidateGrid({ payload, projectId, onRefresh, onCell }: { payload: BatchPayload; projectId: string; onRefresh: () => void; onCell: (cell?: CandidateCell) => void }) {
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const sampleHeader = payload.headers.find((header) => /sample.?id/i.test(header.canonical_field))?.display_header || payload.headers[0]?.display_header
  const rowData = buildGridRows(payload)
  const columns = useMemo<ColDef[]>(() => payload.headers.map((header) => ({
    field: header.display_header,
    headerName: header.display_header,
    editable: true,
    minWidth: header.display_header === sampleHeader ? 145 : 112,
    pinned: header.display_header === sampleHeader ? 'left' : undefined,
    cellClass: (params) => {
      const record = params.data?.__record as CandidateRecord | undefined
      const cell = record?.cells[header.display_header]
      if (!cell) return 'cell-missing'
      if (cell.alternatives.length) return 'cell-conflict'
      if (cell.review_status === 'pending') return 'cell-review'
      return 'cell-confirmed'
    },
  })), [payload.headers, sampleHeader])
  const valueChanged = async (event: CellValueChangedEvent) => {
    const record = event.data.__record as CandidateRecord
    const header = event.colDef.field || ''
    const cell = record.cells[header]
    if (cell) await api.updateCell(projectId, cell.cell_id, { value: String(event.newValue ?? ''), review_status: 'confirmed' })
    onRefresh()
  }
  const clicked = (event: CellClickedEvent) => {
    const record = event.data?.__record as CandidateRecord | undefined
    onCell(record?.cells[event.colDef.field || ''])
  }
  return <>
    <div className="grid-toolbar"><strong>{payload.records.length} 个样品 · {payload.headers.length} 个目标表头</strong><span>空白单元格保留为空；黄色待审核；红色存在冲突</span><div className="toolbar-spacer" /><button disabled={selectedRows.length < 2} onClick={async () => { await api.mergeRecords(projectId, selectedRows); setSelectedRows([]); onRefresh() }}>合并所选行</button></div>
    <div className="ag-theme-quartz candidate-grid"><AgGridReact rowData={rowData} columnDefs={columns} defaultColDef={{ sortable: true, filter: true, resizable: true }} rowSelection={{ mode: 'multiRow' }} getRowId={(params) => params.data.__record.candidate_record_id} onSelectionChanged={(event) => setSelectedRows(event.api.getSelectedRows().map((row) => row.__record.candidate_record_id))} onCellValueChanged={valueChanged} onCellClicked={clicked} /></div>
  </>
}

export default function WorkbenchPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { projectId, articleId, activeElement, setActiveElement, activeCell, setActiveCell, logs, addLog, clearLogs } = useAppStore()
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('all')
  const session = useQuery({ queryKey: ['session', projectId, articleId], queryFn: () => api.session(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const elements = useQuery({ queryKey: ['elements', projectId, articleId], queryFn: () => api.elements(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const resources = useQuery({ queryKey: ['resources', projectId, articleId], queryFn: () => api.resources(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const activeBatchId = session.data?.active_batch_id || ''
  const batch = useQuery({ queryKey: ['batch', projectId, activeBatchId], queryFn: () => api.batch(projectId, activeBatchId), enabled: Boolean(projectId && activeBatchId) })

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
      setBusy(false); refreshWorkbench()
      if (task?.status === 'completed' && nextStep !== undefined) setStep(nextStep)
    })
  }, [addLog, clearLogs, projectId, refreshWorkbench])
  useEffect(() => {
    if (!busy && session.data && ['pending', 'stale'].includes(session.data.discovery_status) && resources.data?.some((resource) => resource.resource_type.includes('pdf'))) {
      runTask(() => api.discover(projectId, articleId))
    }
  }, [articleId, busy, projectId, resources.data, runTask, session.data])

  const all = elements.data || []
  const visible = all.filter((element) => (typeFilter === 'all' || element.element_type === typeFilter) && (!search || `${element.text_content} ${element.caption} ${element.matched_headers.join(' ')}`.toLowerCase().includes(search.toLowerCase())))
  const selected = all.filter((element) => element.selected)
  const toggle = async (element: DocumentElement) => {
    const ids = element.selected ? selected.filter((item) => item.element_id !== element.element_id).map((item) => item.element_id) : [...selected.map((item) => item.element_id), element.element_id]
    await api.selectElements(projectId, articleId, ids); refreshWorkbench()
  }
  const openInPdf = (element: DocumentElement) => { setActiveElement(element); setStep(1) }

  return <div className="page workbench-page">
    <div className="page-title-row"><div><h1>智能体工作台</h1><p>围绕目标表头发现证据、校核原文并生成样品级候选表。</p></div><span className={`status-pill ${session.data?.discovery_status}`}>{session.data?.discovery_status || '未开始'}</span></div>
    <div className="stepper">{WORKBENCH_STEPS.map((label, index) => <div
      key={label}
      role="button"
      tabIndex={0}
      className={`step-button ${step === index ? 'active' : index < step ? 'done' : ''}`}
      onClick={() => setStep(index)}
      onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setStep(index) } }}
    ><span>{index < step ? <Check size={14} /> : index + 1}</span>{label}</div>)}</div>

    <div className="workbench-stage">
      {step === 0 && <div className="discovery-layout">
        <section className="candidate-pool panel">
          <div className="resource-toolbar"><button className="primary-button" disabled={busy || !articleId} onClick={() => runTask(() => api.discover(projectId, articleId))}>{busy ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}重新发现</button><select value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="all">全部资源</option><option value="table">表格</option><option value="figure">图像</option><option value="paragraph">相关段落</option></select><div className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索页码、表头或证据..." /></div></div>
          <div className="panel-heading"><strong>自动发现的相关资源</strong><span>{visible.length}</span></div>
          <div className="element-list">{visible.map((element) => <ElementCard key={element.element_id} element={element} active={activeElement?.element_id === element.element_id} selected={element.selected} onOpen={() => setActiveElement(element)} onToggle={() => toggle(element)} />)}{!visible.length && <div className="empty-state">{busy ? '智能体正在逐页分析...' : '当前文章还没有发现资源。'}</div>}</div>
        </section>
        <section className="selection-panel panel"><div className="panel-heading"><strong>抽取队列</strong><span>{selected.length}</span></div><div className="selected-list">{selected.map((element) => <button key={element.element_id} onClick={() => openInPdf(element)}><ElementIcon type={element.element_type} /><span>第 {element.page_number} 页<br /><small>{element.caption || element.text_content.slice(0, 65)}</small></span><Eye size={15} /></button>)}{!selected.length && <div className="empty-state compact">点击资源左侧复选按钮，将可信证据加入抽取队列。</div>}</div><button className="primary-button wide" disabled={!selected.length} onClick={() => setStep(1)}>校核原文 <ArrowRight size={16} /></button></section>
      </div>}
      {step === 1 && <PdfWorkbench projectId={projectId} articleId={articleId} elements={all} resources={resources.data || []} active={activeElement} onActive={setActiveElement} refresh={refreshWorkbench} onToggle={toggle} />}
      {step === 2 && <div className="extraction-layout">
        <section className="source-strip panel"><div className="panel-heading"><strong>参与抽取的资源</strong><span>{selected.length}</span></div><div>{selected.map((element) => <button key={element.element_id} onClick={() => setActiveElement(element)} className={activeElement?.element_id === element.element_id ? 'active' : ''}><ElementIcon type={element.element_type} /> 第 {element.page_number} 页</button>)}</div><button className="primary-button" disabled={busy || !selected.length} onClick={() => runTask(() => api.createBatch(projectId, articleId, true), 2)}><Play size={16} /> AI 生成/刷新候选表</button></section>
        <section className="candidate-sheet panel">{batch.data ? <CandidateGrid payload={batch.data} projectId={projectId} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['batch', projectId, activeBatchId] })} onCell={setActiveCell} /> : <div className="empty-state"><Table2 size={34} /><h3>尚未生成样品级候选表</h3><p>选择资源后运行 AI 抽取，结果将严格使用目标表头，缺失字段保留为空。</p></div>}</section>
        <aside className="cell-inspector panel"><div className="panel-heading"><strong>单元格证据</strong></div>{activeCell ? <CellInspector cell={activeCell} elements={all} projectId={projectId} /> : <div className="empty-state compact">点击候选表中的单元格，查看原始值、单位、证据和冲突候选。</div>}</aside>
      </div>}
      {step === 3 && <MappingReview payload={batch.data} elements={all} projectId={projectId} onRefresh={() => queryClient.invalidateQueries({ queryKey: ['batch', projectId, activeBatchId] })} onNavigate={(path) => { refreshWorkbench(); navigate(path) }} />}
    </div>
    <TaskConsole logs={logs} busy={busy} />
  </div>
}

function CellInspector({ cell, elements, projectId }: { cell: CandidateCell; elements: DocumentElement[]; projectId: string }) {
  const element = elements.find((item) => item.element_id === cell.element_id)
  return <div className="cell-detail"><span className={`risk-tag ${cell.risk_level}`}>{cell.risk_level} risk</span><h3>{cell.target_header}</h3><dl><dt>当前值</dt><dd>{cell.value || '空'}</dd><dt>原始字段</dt><dd>{cell.original_field || '—'}</dd><dt>原始单位</dt><dd>{cell.original_unit || '—'}</dd><dt>目标单位</dt><dd>{cell.target_unit || '—'}</dd><dt>置信度</dt><dd>{Math.round(cell.confidence * 100)}%</dd><dt>PDF 位置</dt><dd>{cell.page_number ? `第 ${cell.page_number} 页` : '—'}</dd></dl>{element?.preview_path && <img src={api.previewUrl(projectId, element.element_id)} />}{element && <p className="source-text">{element.context_text || element.text_content}</p>}{cell.alternatives.length > 0 && <div className="conflict-box"><strong>存在 {cell.alternatives.length} 个冲突候选</strong>{cell.alternatives.map((value, index) => <p key={index}>{String(value.value || '')}</p>)}</div>}</div>
}

function MappingReview({ payload, elements, projectId, onRefresh, onNavigate }: { payload?: BatchPayload; elements: DocumentElement[]; projectId: string; onRefresh: () => void; onNavigate: (path: string) => void }) {
  const unresolved = payload?.records.flatMap((record) => Object.values(record.cells).filter((cell) => cell.mapping_status !== 'confirmed' || cell.review_status === 'pending' || cell.alternatives.length)) || []
  return <div className="mapping-review"><section className="panel mapping-list"><div className="panel-heading"><strong>需要确认的映射</strong><span>{unresolved.length}</span></div>{unresolved.map((cell) => <div className="mapping-row" key={cell.cell_id}><div><strong>{cell.original_field || '原文候选'} <ArrowRight size={14} /> {cell.target_header}</strong><p>{cell.value} {cell.original_unit} · 目标单位 {cell.target_unit || '未指定'}</p></div><span className={`risk-tag ${cell.risk_level}`}>{cell.risk_level}</span><button onClick={async () => { await api.updateCell(projectId, cell.cell_id, { mapping_status: 'confirmed', review_status: 'confirmed' }); onRefresh() }}>确认</button></div>)}{!unresolved.length && <div className="empty-state"><Check size={32} /><h3>当前没有待确认映射</h3></div>}</section><aside className="panel mapping-help"><h3>确认原则</h3><p>单位不一致、化学形态换算和冲突值不会自动覆盖。确认后的字段会立即回写样品候选表，并进入后续人工审核与标准化流程。</p><button className="primary-button wide" onClick={() => onNavigate('/review')}>进入人工审核</button></aside></div>
}
