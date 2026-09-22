import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { AgGridReact } from 'ag-grid-react'
import type { CellClickedEvent, ColDef } from 'ag-grid-community'
import { FileCheck2, PanelRightOpen, Search } from 'lucide-react'
import { api } from './api'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { useAppStore } from './store'
import { ContextDrawer } from './WorkspaceUI'
import type { TraceField, TraceRecordDetail, TraceRecordSummary } from './types'

type StandardizedApiRow = {
  record_id: string
  article_id: string
  article_title: string
  processed_at: string
  data: Record<string, string>
}

type StandardizedGridRow = Record<string, string> & {
  __record_id: string
  __article_id: string
  __article_title: string
  __processed_at: string
}

function traceChain(detail?: TraceRecordDetail, field?: TraceField | null) {
  if (!field) return <div className="trace-incomplete">选择表格中的非空单元格后查看完整溯源链。</div>
  const sourceType = field.source?.element_type === 'table' ? '表格' : field.source?.element_type === 'figure' ? '图像' : '段落'
  return <div className="trace-evidence-chain">
    <div><span>文章</span><strong>{detail?.article_title || '—'}</strong></div><i/>
    <div><span>资源</span><strong>{field.source ? `${sourceType} · ${field.source.resource_name || 'PDF'} · p${field.source.page_number || '?'}` : '溯源信息不完整'}</strong></div><i/>
    <div><span>原始数据</span><strong>{field.original_field || '—'} = {field.original_value || '—'} {field.original_unit}</strong></div><i/>
    <div><span>映射 / 换算</span><strong>{field.mapping?.mapping_type || '直接映射'}{field.calculation?.formula ? ` · ${field.calculation.formula}` : ''}</strong></div><i/>
    <div><span>审核决策</span><strong>{field.review_status || '—'}</strong></div><i/>
    <div><span>标准值</span><strong>{field.target_header} = {field.value} {field.target_unit}</strong></div>
  </div>
}

export function StandardizedTraceView() {
  const { projectId } = useAppStore()
  const [searchParams] = useSearchParams()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const standardized = useQuery({ queryKey: ['standardized', projectId], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  const traceRecords = useQuery({ queryKey: ['trace-records', projectId], queryFn: () => api.traceRecords(projectId), enabled: Boolean(projectId) })
  const [articleFilter, setArticleFilter] = useState(searchParams.get('article_id') || searchParams.get('article') || '')
  const [query, setQuery] = useState(searchParams.get('q') || '')
  const [activeRecordId, setActiveRecordId] = useState(searchParams.get('record_id') || searchParams.get('record') || '')
  const [requestedField, setRequestedField] = useState(searchParams.get('field') || '')
  const [activeField, setActiveField] = useState<TraceField | null>(null)
  const [evidenceOpen, setEvidenceOpen] = useState(Boolean(searchParams.get('record_id') || searchParams.get('record')))
  const [evidenceFocused, setEvidenceFocused] = useState(false)
  const detailQuery = useQuery({
    queryKey: ['trace-record', projectId, activeRecordId],
    queryFn: () => api.traceRecord(projectId, activeRecordId) as Promise<TraceRecordDetail>,
    enabled: Boolean(projectId && activeRecordId),
  })
  useEffect(() => {
    setArticleFilter(searchParams.get('article_id') || searchParams.get('article') || '')
    setQuery(searchParams.get('q') || '')
    setActiveRecordId(searchParams.get('record_id') || searchParams.get('record') || '')
    setRequestedField(searchParams.get('field') || '')
  }, [searchParams])
  const summaries = useMemo(() => new Map(((traceRecords.data?.items || []) as TraceRecordSummary[]).map((record) => [record.record_id, record])), [traceRecords.data?.items])
  const apiRows = (standardized.data || []) as unknown as StandardizedApiRow[]
  const fieldNames = useMemo(() => {
    const names: string[] = []
    const seen = new Set<string>()
    for (const row of apiRows) for (const [field, value] of Object.entries(row.data || {})) {
      if (value === '' || value == null || seen.has(field)) continue
      seen.add(field); names.push(field)
    }
    const sampleIndex = names.findIndex((name) => /sample.?id/i.test(name))
    if (sampleIndex > 0) names.unshift(names.splice(sampleIndex, 1)[0])
    return names
  }, [apiRows])
  const gridRows = useMemo<StandardizedGridRow[]>(() => apiRows
    .filter((row) => !articleFilter || row.article_id === articleFilter)
    .map((row) => ({
      __record_id: row.record_id,
      __article_id: row.article_id,
      __article_title: row.article_title,
      __processed_at: row.processed_at,
      ...(row.data || {}),
    }))
    .filter((row) => !query || Object.values(row).join(' ').toLowerCase().includes(query.toLowerCase())), [apiRows, articleFilter, query])

  useEffect(() => {
    const fields = detailQuery.data?.fields || []
    const next = fields.find((field) => field.target_header === requestedField) || fields[0] || null
    setActiveField(next)
  }, [detailQuery.data?.record_id, requestedField])

  const columns = useMemo<ColDef<StandardizedGridRow>[]>(() => {
    const sourceColumn: ColDef<StandardizedGridRow> = {
      colId: '__source', headerName: '来源', pinned: 'left', width: 70, minWidth: 64, maxWidth: 82, sortable: false, filter: false,
      cellRenderer: ({ data }: { data?: StandardizedGridRow }) => {
        const summary = data ? summaries.get(data.__record_id) : undefined
        return <span className={`provenance-dot ${summary?.source_complete ? 'complete' : 'incomplete'}`} title={summary?.sources?.length ? summary.sources.map((source) => `${source.element_type === 'table' ? '表格' : source.element_type === 'figure' ? '图像' : '段落'} p${source.page_number || '?'}`).join('；') : '溯源信息不完整'}><FileCheck2 size={14}/>{summary?.sources?.length || 0}</span>
      },
    }
    const sampleField = fieldNames.find((field) => /sample.?id/i.test(field))
    const dataColumns = fieldNames.map<ColDef<StandardizedGridRow>>((field) => ({
      field, headerName: field, pinned: field === sampleField ? 'left' : undefined,
      minWidth: 88, width: Math.min(150, Math.max(94, field.length * 8 + 26)),
      tooltipValueGetter: ({ data }) => `${data?.[field] || '空'}${summaries.get(data?.__record_id || '')?.source_complete ? ' · 有完整来源' : ' · 来源不完整'}`,
      cellRenderer: ({ data }: { data?: StandardizedGridRow }) => {
        const value = data?.[field] || ''
        const complete = summaries.get(data?.__record_id || '')?.source_complete
        return <span className="review-cell-content"><span>{value}</span>{value && <i className={complete ? 'cell-source-dot complete' : 'cell-source-dot incomplete'}/>}</span>
      },
      cellClass: ({ data }) => data?.[field] ? 'standardized-value-cell' : 'cell-missing',
    }))
    return [
      { field: '__article_title', headerName: '文章', width: 190, minWidth: 150, hide: Boolean(articleFilter), tooltipField: '__article_title' },
      sourceColumn,
      ...dataColumns,
    ]
  }, [articleFilter, fieldNames, summaries])

  const selectCell = (event: CellClickedEvent<StandardizedGridRow>) => {
    if (!event.data) return
    const field = event.column.getColId()
    setActiveRecordId(event.data.__record_id)
    if (!field.startsWith('__')) setRequestedField(field)
    else setRequestedField('')
    setEvidenceOpen(true)
  }
  const evidence = activeField?.source as PdfEvidence | null
  const gridPanel = <section className="panel review-grid-panel"><div className="ag-theme-quartz review-grid standardized-review-grid"><AgGridReact<StandardizedGridRow>
    theme="legacy"
    rowData={gridRows}
    columnDefs={columns}
    defaultColDef={{ sortable: true, filter: true, resizable: true }}
    getRowId={({ data }) => data.__record_id}
    onCellClicked={selectCell}
    tooltipShowDelay={250}
  /></div></section>
  return <div className="standardized-trace-view">
    <div className="review-toolbar">
      <select value={articleFilter} onChange={(event) => { setArticleFilter(event.target.value); setActiveRecordId(''); setActiveField(null) }}><option value="">全部文章</option>{(articles.data || []).map((article) => <option key={article.article_id} value={article.article_id}>{article.title || article.article_id}</option>)}</select>
      <div className="search-box"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文章、SampleID、字段或值"/></div>
      <span className="review-count">{gridRows.length} 条标准化记录 · {fieldNames.length} 个非空字段</span><div className="toolbar-spacer"/>
      <button className="icon-button" title="查看当前溯源" disabled={!activeRecordId} onClick={() => setEvidenceOpen(true)}><PanelRightOpen size={16}/></button>
    </div>
    <div className="review-content-shell">
      <div className="review-content-main">{gridPanel}</div>
      <ContextDrawer
        open={evidenceOpen}
        title={activeField ? `${activeField.target_header} · 完整溯源` : '标准化记录溯源'}
        onClose={() => { setEvidenceOpen(false); setEvidenceFocused(false) }}
        focused={evidenceFocused}
        onFocusedChange={setEvidenceFocused}
        storageKey="geochem.review.standardized.evidence.width"
        defaultWidth={660}
        minWidth={440}
        maxWidth={1120}
      >
        <PdfEvidenceViewer projectId={projectId} resources={detailQuery.data?.resources || []} evidence={evidence} details={traceChain(detailQuery.data, activeField)}/>
      </ContextDrawer>
    </div>
  </div>
}

export function TracePage() {
  return <div className="page trace-page"><div className="page-title-row"><div><h1>已标准化数据回查</h1><p>从标准化单元格返回原始文章、资源、映射、换算和审核决策。</p></div></div><StandardizedTraceView/></div>
}
