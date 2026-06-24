import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { FileText, Image, Search, Table2 } from 'lucide-react'
import { api } from './api'
import { PdfEvidenceViewer, type PdfEvidence } from './PdfEvidenceViewer'
import { useAppStore } from './store'
import type { EvidenceSource, TraceField, TraceRecordSummary } from './types'

const sourceLabel = (type?: string) => type === 'table' ? '表格' : type === 'figure' ? '图像' : '段落'
const SourceIcon = ({ type }: { type?: string }) => type === 'table' ? <Table2 size={12}/> : type === 'figure' ? <Image size={12}/> : <FileText size={12}/>
const formatTime = (value: string) => value ? new Date(value).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'

export function TracePage() {
  const { projectId } = useAppStore()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const [articleFilter, setArticleFilter] = useState('')
  const [query, setQuery] = useState('')
  const [fieldQuery, setFieldQuery] = useState('')
  const [activeRecordId, setActiveRecordId] = useState('')
  const [activeField, setActiveField] = useState<TraceField | null>(null)
  const [activeSource, setActiveSource] = useState<EvidenceSource | null>(null)
  const recordsQuery = useQuery({
    queryKey: ['trace-records', projectId, articleFilter, query],
    queryFn: () => api.traceRecords(projectId, articleFilter, query),
    enabled: Boolean(projectId),
  })
  const detailQuery = useQuery({
    queryKey: ['trace-record', projectId, activeRecordId],
    queryFn: () => api.traceRecord(projectId, activeRecordId),
    enabled: Boolean(projectId && activeRecordId),
  })
  const records = recordsQuery.data?.items || []

  useEffect(() => {
    if (records.length && !records.some((record) => record.record_id === activeRecordId)) setActiveRecordId(records[0].record_id)
    if (!records.length) setActiveRecordId('')
  }, [activeRecordId, records])

  useEffect(() => {
    const first = detailQuery.data?.fields[0] || null
    setActiveField(first)
    setActiveSource(first?.source || detailQuery.data?.sources[0] || null)
  }, [detailQuery.data?.record_id])

  const grouped = useMemo(() => {
    const groups = new Map<string, { title: string; records: TraceRecordSummary[] }>()
    for (const record of records) {
      if (!groups.has(record.article_id)) groups.set(record.article_id, { title: record.article_title, records: [] })
      groups.get(record.article_id)!.records.push(record)
    }
    return [...groups.entries()]
  }, [records])
  const fields = useMemo(() => (detailQuery.data?.fields || []).filter((field) => !fieldQuery || `${field.target_header} ${field.value} ${field.original_value}`.toLowerCase().includes(fieldQuery.toLowerCase())), [detailQuery.data?.fields, fieldQuery])
  const evidence = (activeField?.source || activeSource) as PdfEvidence | null

  const selectRecord = (record: TraceRecordSummary, source?: EvidenceSource) => {
    setActiveRecordId(record.record_id)
    setActiveField(null)
    setActiveSource(source || record.sources[0] || null)
  }

  const chain = activeField ? <div className="trace-evidence-chain">
    <div><span>文章</span><strong>{detailQuery.data?.article_title || '—'}</strong></div>
    <i/>
    <div><span>资源</span><strong>{activeField.source ? `${sourceLabel(activeField.source.element_type)} · ${activeField.source.resource_name || 'PDF'}` : '溯源信息不完整'}</strong></div>
    <i/>
    <div><span>原始数据</span><strong>{activeField.original_field || '—'} = {activeField.original_value || '—'} {activeField.original_unit}</strong></div>
    <i/>
    <div><span>映射 / 换算</span><strong>{activeField.mapping?.mapping_type || '直接映射'}{activeField.calculation?.formula ? ` · ${activeField.calculation.formula}` : ''}</strong></div>
    <i/>
    <div><span>标准值</span><strong>{activeField.target_header} = {activeField.value} {activeField.target_unit}</strong></div>
  </div> : <div className="trace-incomplete">选择中栏字段后查看完整溯源链。</div>

  return <div className="page trace-page">
    <div className="page-title-row"><div><h1>溯源查看</h1><p>从标准化记录返回文章、原始资源、字段映射和计算过程。</p></div><div className="trace-filters">
      <select value={articleFilter} onChange={(event) => { setArticleFilter(event.target.value); setActiveRecordId('') }}><option value="">全部文章</option>{(articles.data || []).map((article) => <option key={article.article_id} value={article.article_id}>{article.title || article.article_id}</option>)}</select>
      <div className="search-box"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文章、DOI、SampleID、字段或值"/></div>
    </div></div>
    <div className="trace-workspace">
      <section className="panel trace-record-panel">
        <div className="panel-heading"><strong>标准化记录</strong><span>{recordsQuery.data?.total || 0}</span></div>
        <div className="trace-record-list">{grouped.map(([articleId, group]) => <div className="trace-article-group" key={articleId}>
          <h3>{group.title}</h3>
          {group.records.map((record) => <article key={record.record_id} className={record.record_id === activeRecordId ? 'active' : ''} onClick={() => selectRecord(record)}>
            <div className="trace-record-head"><strong>{record.sample_id || record.record_id}</strong><time>{formatTime(record.processed_at)}</time></div>
            <small>{record.record_id} · {record.nonempty_count} 个值</small>
            <div className="source-chip-list">{record.sources.map((source, index) => <button key={`${source.element_id || index}`} className={`source-chip ${source.element_type}`} onClick={(event) => { event.stopPropagation(); selectRecord(record, source) }}><SourceIcon type={source.element_type}/>{sourceLabel(source.element_type)} p{source.page_number || '?'}</button>)}{!record.sources.length && <span className="source-missing">来源不完整</span>}</div>
          </article>)}
        </div>)}{!records.length && <div className="empty-state compact">暂无标准化记录，请先完成人工审核与标准化。</div>}</div>
      </section>
      <section className="panel trace-value-panel">
        <div className="panel-heading"><strong>{detailQuery.data ? `${detailQuery.data.sample_id || detailQuery.data.record_id} 的值` : '字段值'}</strong><span>{fields.length}</span></div>
        <div className="trace-field-search"><Search size={14}/><input value={fieldQuery} onChange={(event) => setFieldQuery(event.target.value)} placeholder="搜索字段或值"/></div>
        <div className="trace-field-list">{fields.map((field) => <button key={field.target_header} className={activeField?.target_header === field.target_header ? 'active' : ''} onClick={() => { setActiveField(field); setActiveSource(field.source || null) }}>
          <div><strong>{field.target_header}</strong><span>{field.value || '—'} {field.target_unit}</span></div>
          <small>原始：{field.original_value || '—'} {field.original_unit} · {field.review_status || '未知状态'}</small>
          <em className={field.source_complete ? 'complete' : 'incomplete'}>{field.source ? `${sourceLabel(field.source.element_type)} p${field.source.page_number || '?'}` : '来源不完整'}</em>
        </button>)}{detailQuery.data && !fields.length && <div className="empty-state compact">没有匹配的非空字段。</div>}</div>
      </section>
      <aside className="panel trace-pdf-panel">
        <PdfEvidenceViewer projectId={projectId} resources={detailQuery.data?.resources || []} evidence={evidence} details={chain}/>
      </aside>
    </div>
  </div>
}
