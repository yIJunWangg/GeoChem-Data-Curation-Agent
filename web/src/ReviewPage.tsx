import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AgGridReact } from 'ag-grid-react'
import type { CellClickedEvent, ColDef, SelectionChangedEvent } from 'ag-grid-community'
import {
  Columns3, FileText, Image, Maximize2, PanelRightOpen, RotateCcw,
  Search, ShieldCheck, ShieldX, Table2,
} from 'lucide-react'
import { api } from './api'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { useAppStore } from './store'
import type { CandidateCell, CandidateRecord, DocumentElement, EvidenceSource } from './types'

type Header = { display_header: string; canonical_field: string; target_unit: string }
type CandidatePayload = { headers: Header[]; records: CandidateRecord[] }
type LayoutMode = 'balanced' | 'table' | 'pdf'

const sourceLabel = (type?: string) => type === 'table' ? '表格' : type === 'figure' ? '图像' : '段落'
const SourceIcon = ({ type }: { type?: string }) => type === 'table' ? <Table2 size={12}/> : type === 'figure' ? <Image size={12}/> : <FileText size={12}/>

function fieldGroup(header: string) {
  if (/sample|author|year|title|doi|reference|source/i.test(header)) return 'basic'
  if (/sio2|al2o3|cao|k2o|na2o|fe2o3|mgo|mno|tio2|p2o5|wt\s*%/i.test(header)) return 'major'
  if (/δ|toc|tn|ts|tmax|\bs[123]\b|hi|oi/i.test(header)) return 'organic'
  if (/ppm|ppb|ree|lree|hree|fepy|fehr|fecarb|feox|femag/i.test(header)) return 'trace'
  return 'context'
}

function cellEvidence(cell: CandidateCell, elements: Map<string, DocumentElement>): PdfEvidence {
  const element = cell.element_id ? elements.get(cell.element_id) : undefined
  return {
    resource_id: element?.resource_id,
    resource_name: '',
    element_id: cell.element_id,
    element_type: element?.element_type || 'paragraph',
    page_number: cell.page_number || element?.page_number,
    bbox: cell.bbox?.length ? cell.bbox : element?.bbox || [],
    caption: element?.caption,
    context: element?.context_text,
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

function recordSources(record: CandidateRecord, elements: Map<string, DocumentElement>) {
  const sources = new Map<string, { source: EvidenceSource; cell: CandidateCell }>()
  for (const cell of Object.values(record.cells || {})) {
    if (!cell.element_id || sources.has(cell.element_id)) continue
    const element = elements.get(cell.element_id)
    sources.set(cell.element_id, {
      cell,
      source: {
        resource_id: element?.resource_id,
        element_id: cell.element_id,
        element_type: element?.element_type || 'paragraph',
        page_number: cell.page_number || element?.page_number,
        bbox: cell.bbox?.length ? cell.bbox : element?.bbox || [],
        caption: element?.caption,
      },
    })
  }
  return [...sources.values()]
}

export function ReviewPage() {
  const { projectId, articleId } = useAppStore()
  const queryClient = useQueryClient()
  const recordsQuery = useQuery({
    queryKey: ['candidate-records', projectId, articleId],
    queryFn: () => api.articleCandidateRecords(projectId, articleId) as Promise<CandidatePayload>,
    enabled: Boolean(projectId && articleId),
  })
  const resources = useQuery({ queryKey: ['resources', projectId, articleId], queryFn: () => api.resources(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const elementsQuery = useQuery({ queryKey: ['elements', projectId, articleId], queryFn: () => api.elements(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [activeEvidence, setActiveEvidence] = useState<PdfEvidence | null>(null)
  const [layout, setLayout] = useState<LayoutMode>('balanced')
  const [columnMode, setColumnMode] = useState<'data' | 'all'>('data')
  const [group, setGroup] = useState('all')
  const [risk, setRisk] = useState('all')
  const [status, setStatus] = useState('all')
  const [search, setSearch] = useState('')

  const payload = recordsQuery.data
  const rows = payload?.records || []
  const headers = payload?.headers || []
  const elements = useMemo(() => new Map((elementsQuery.data || []).map((element) => [element.element_id, element])), [elementsQuery.data])
  const sampleHeader = useMemo(() => headers.find((header) => /sample.?id/i.test(header.canonical_field) || /sample.?id/i.test(header.display_header))?.display_header || headers[0]?.display_header || 'SampleID', [headers])
  const headersWithData = useMemo(() => new Set(rows.flatMap((record) => Object.entries(record.cells || {}).filter(([, cell]) => cell.value !== '' || cell.review_status === 'pending' || cell.alternatives.length).map(([header]) => header))), [rows])
  const displayedHeaders = useMemo(() => headers.filter((header) => header.display_header !== sampleHeader && (columnMode === 'all' || headersWithData.has(header.display_header)) && (group === 'all' || fieldGroup(header.display_header) === group)), [columnMode, group, headers, headersWithData, sampleHeader])
  const filteredRows = useMemo(() => rows.filter((record) => {
    const cells = Object.values(record.cells || {})
    if (risk !== 'all' && !cells.some((cell) => cell.risk_level === risk)) return false
    if (status !== 'all' && !cells.some((cell) => cell.review_status === status)) return false
    if (search && !`${record.sample_id} ${Object.values(record.data || {}).join(' ')}`.toLowerCase().includes(search.toLowerCase())) return false
    return true
  }), [risk, rows, search, status])

  useEffect(() => {
    if (!activeEvidence && rows.length) {
      const first = Object.values(rows[0].cells || {}).find((cell) => cell.element_id)
      if (first) setActiveEvidence(cellEvidence(first, elements))
    }
  }, [activeEvidence, elements, rows])

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['candidate-records', projectId, articleId] })
  const applyToSelected = async (action: 'approve' | 'reject') => {
    await Promise.all(selectedRows.map((recordId) => action === 'approve' ? api.approveRecord(projectId, recordId) : api.rejectRecord(projectId, recordId)))
    setSelectedRows([])
    refresh()
  }

  const columns = useMemo<ColDef<CandidateRecord>[]>(() => {
    const sourceColumn: ColDef<CandidateRecord> = {
      colId: '__source', headerName: '原文', pinned: 'left', width: 150, minWidth: 130, sortable: false, filter: false,
      cellRenderer: ({ data }: { data?: CandidateRecord }) => <div className="source-chip-list">{data && recordSources(data, elements).map(({ source, cell }) => <button key={source.element_id} className={`source-chip ${source.element_type}`} title={`${sourceLabel(source.element_type)}，第 ${source.page_number || '?'} 页`} onClick={(event) => { event.stopPropagation(); setActiveEvidence(cellEvidence(cell, elements)) }}><SourceIcon type={source.element_type}/>{sourceLabel(source.element_type)} p{source.page_number || '?'}</button>)}</div>,
    }
    const dataColumns = displayedHeaders.map<ColDef<CandidateRecord>>((header) => ({
      field: `data.${header.display_header}`, colId: header.display_header, headerName: header.display_header,
      minWidth: 86, width: Math.min(132, Math.max(92, header.display_header.length * 8 + 24)),
      editable: true,
      valueGetter: ({ data }) => data?.data?.[header.display_header] || '',
      valueSetter: ({ data, newValue }) => {
        if (!data) return false
        if (!data.data) data.data = {}
        const old = data.data[header.display_header] || ''
        const val = String(newValue ?? '')
        if (old === val) return false
        data.data[header.display_header] = val
        return true
      },
      tooltipValueGetter: ({ data }) => data?.data?.[header.display_header] || '空',
      cellClass: ({ data }) => {
        const cell = data?.cells?.[header.display_header]
        if (!cell) return 'cell-missing'
        if (cell.alternatives.length) return 'cell-conflict'
        if (cell.review_status === 'pending') return 'cell-review'
        return 'cell-confirmed'
      },
    }))
    return [
      { field: 'sample_id', headerName: sampleHeader, pinned: 'left', width: 112, minWidth: 100 },
      sourceColumn,
      ...dataColumns,
    ]
  }, [displayedHeaders, elements, sampleHeader])

  const onCellClicked = (event: CellClickedEvent<CandidateRecord>) => {
    const header = event.column.getColId()
    const cell = event.data?.cells?.[header]
    if (cell) setActiveEvidence(cellEvidence(cell, elements))
  }
  const onCellValueChanged = async (event: any) => {
    const header = event.column?.getColId?.()
    const record = event.data as CandidateRecord | undefined
    if (!header || !record) return
    const cell = record.cells?.[header]
    const newValue = String(event.newValue ?? '')
    if (cell) {
      await api.updateCell(projectId, cell.cell_id, { value: newValue, review_status: 'confirmed' })
    }
    refresh()
  }

  const onSelectionChanged = (event: SelectionChangedEvent<CandidateRecord>) => setSelectedRows(event.api.getSelectedRows().map((record) => record.candidate_record_id))

  return <div className="page review-page">
    <div className="page-title-row review-title-row"><div><h1>人工审核</h1><p>逐行确认候选数据，点击原文标签或单元格核对 PDF 证据。</p></div><div className="review-actions">
      <button disabled={!selectedRows.length} onClick={() => applyToSelected('reject')}><ShieldX size={15}/>拒绝 ({selectedRows.length})</button>
      <button className="primary-button" disabled={!selectedRows.length} onClick={() => applyToSelected('approve')}><ShieldCheck size={15}/>通过 ({selectedRows.length})</button>
      <button className="primary-button" onClick={async () => {
        await api.finalize(projectId, articleId)
        queryClient.invalidateQueries({ queryKey: ['standardized'] })
        queryClient.invalidateQueries({ queryKey: ['standardized', projectId] })
        queryClient.invalidateQueries({ queryKey: ['trace-records', projectId] })
        queryClient.invalidateQueries({ queryKey: ['candidate-records', projectId, articleId] })
      }}>生成标准化记录</button>
    </div></div>
    <div className="review-toolbar panel">
      <div className="search-box"><Search size={15}/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索 SampleID 或值"/></div>
      <select value={columnMode} onChange={(event) => setColumnMode(event.target.value as 'data' | 'all')}><option value="data">有值/待审核字段</option><option value="all">全部156字段</option></select>
      <select value={group} onChange={(event) => setGroup(event.target.value)}><option value="all">全部字段组</option><option value="basic">基本信息</option><option value="context">地层与样品</option><option value="organic">同位素与有机质</option><option value="major">主量元素</option><option value="trace">微量/REE/铁组分</option></select>
      <select value={risk} onChange={(event) => setRisk(event.target.value)}><option value="all">全部风险</option><option value="high">高风险</option><option value="medium">中风险</option><option value="low">低风险</option></select>
      <select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">全部审核状态</option><option value="pending">待审核</option><option value="approved">已通过</option><option value="rejected">已拒绝</option></select>
      <div className="toolbar-spacer"/><span className="review-count">{filteredRows.length} 行 · {displayedHeaders.length + 2} 列</span>
      <button className="icon-button" title="聚焦表格" onClick={() => setLayout('table')}><Columns3 size={16}/></button>
      <button className="icon-button" title="恢复分栏" onClick={() => setLayout('balanced')}><RotateCcw size={16}/></button>
      <button className="icon-button" title="聚焦 PDF" onClick={() => setLayout('pdf')}><PanelRightOpen size={16}/></button>
    </div>
    <div className={`review-workspace layout-${layout}`}>
      <section className="panel review-grid-panel">
        <div className="ag-theme-quartz review-grid"><AgGridReact<CandidateRecord>
          rowData={filteredRows}
          columnDefs={columns}
          defaultColDef={{ sortable: true, filter: true, resizable: true }}
          rowSelection={{ mode: 'multiRow' }}
          selectionColumnDef={{ pinned: 'left', width: 42, maxWidth: 42, suppressHeaderMenuButton: true }}
          getRowId={({ data }) => data.candidate_record_id}
          onSelectionChanged={onSelectionChanged}
          onCellClicked={onCellClicked}
          onCellValueChanged={onCellValueChanged}
          tooltipShowDelay={250}
        /></div>
      </section>
      <aside className="panel review-pdf-panel">
        <PdfEvidenceViewer projectId={projectId} resources={resources.data || []} evidence={activeEvidence} onRequestFocus={() => setLayout(layout === 'pdf' ? 'balanced' : 'pdf')}/>
      </aside>
      {layout !== 'balanced' && <button className="workspace-restore" onClick={() => setLayout('balanced')}><Maximize2 size={15}/>恢复双栏</button>}
    </div>
  </div>
}
