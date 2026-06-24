import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { Document, Page, pdfjs } from 'react-pdf'
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import {
  ArrowRight, BarChart3, BookOpen, CheckCircle2, Database, Download, ExternalLink,
  FileInput, FileSpreadsheet, KeyRound, Link2, Plus, Search, Settings as SettingsIcon,
  ShieldAlert, Upload, WandSparkles,
} from 'lucide-react'
import { api, watchTask } from './api'
import { useAppStore } from './store'
import type { Article } from './types'

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker

function PageHeader({ title, subtitle, action }: { title: string; subtitle: string; action?: React.ReactNode }) {
  return <div className="page-title-row"><div><h1>{title}</h1><p>{subtitle}</p></div>{action}</div>
}

function SimpleTable({ rows, columns }: { rows: Record<string, any>[]; columns: {key:string;label:string;render?:(row:Record<string,any>)=>React.ReactNode}[] }) {
  return <div className="simple-table-wrap"><table className="simple-table"><thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id || row.record_id || row.rule_id || row.review_id || index)}>{columns.map((column) => <td key={column.key}>{column.render ? column.render(row) : String(row[column.key] ?? '—')}</td>)}</tr>)}</tbody></table>{!rows.length && <div className="empty-state compact">暂无数据</div>}</div>
}

export function DashboardPage() {
  const { projectId } = useAppStore()
  const metrics = useQuery({ queryKey: ['dashboard', projectId], queryFn: () => api.dashboard(projectId), enabled: Boolean(projectId) })
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const cards = [
    ['文献', metrics.data?.articles || 0, BookOpen, '#1468d8'], ['表头配置', metrics.data?.headers || 0, FileSpreadsheet, '#7c3aed'],
    ['相关资源', metrics.data?.elements || 0, WandSparkles, '#00875a'], ['待审核', metrics.data?.reviews || 0, ShieldAlert, '#c2410c'],
    ['标准记录', metrics.data?.records || 0, Database, '#0f766e'], ['学习规则', metrics.data?.rules || 0, CheckCircle2, '#53637a'],
  ] as const
  return <div className="page"><PageHeader title="项目总览" subtitle="一个工作区内管理多篇文章、多套表头和可追溯的标准化数据。" />
    <div className="metric-grid">{cards.map(([label, value, Icon, color]) => <section className="metric-tile" key={label}><Icon color={color} size={20} /><span>{label}</span><strong>{value}</strong></section>)}</div>
    <section className="panel dashboard-section"><div className="panel-heading"><strong>最近文献</strong><span>{articles.data?.length || 0}</span></div><SimpleTable rows={articles.data || []} columns={[{key:'title',label:'文章'},{key:'doi',label:'DOI'},{key:'resource_count',label:'资源'},{key:'element_count',label:'相关证据'},{key:'status',label:'状态'}]} /></section>
  </div>
}

export function HeaderPage() {
  const { projectId } = useAppStore()
  const queryClient = useQueryClient()
  const configs = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const [activeId, setActiveId] = useState('')
  const active = configs.data?.find((item) => item.config_id === activeId) || configs.data?.[0]
  const rows = active?.headers.map((field, index) => ({ index: index + 1, ...field })) || []
  return <div className="page"><PageHeader title="表头配置" subtitle="保存多套可复用表头，字段名称、顺序和单位将直接决定最终输出。" action={<label className="primary-button file-button"><Upload size={16}/> 导入 CSV / XLSX<input type="file" accept=".csv,.xlsx,.xls" onChange={async (event) => { const file=event.target.files?.[0]; if(file){ await api.importHeaders(projectId,file); queryClient.invalidateQueries({queryKey:['headers',projectId]}); event.target.value='' } }}/></label>} />
    <div className="header-layout"><aside className="panel config-list"><div className="panel-heading"><strong>已保存配置</strong><span>{configs.data?.length || 0}</span></div>{configs.data?.map((config) => <button className={active?.config_id === config.config_id ? 'active' : ''} key={config.config_id} onClick={() => setActiveId(config.config_id)}><FileSpreadsheet size={17}/><span><strong>{config.name}</strong><small>{config.field_count} 个字段</small></span></button>)}</aside>
      <section className="panel header-editor"><div className="panel-heading"><strong>{active?.name || '未选择配置'}</strong><span>{rows.length} 列</span></div><SimpleTable rows={rows} columns={[{key:'index',label:'#'},{key:'字段名',label:'显示表头'},{key:'canonical_field',label:'标准字段'},{key:'默认单位',label:'目标单位'},{key:'description',label:'字段说明'}]} /></section></div>
  </div>
}

export function ImportPage() {
  const { projectId, setArticleId } = useAppStore()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const headers = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const [source, setSource] = useState('')
  const [pending, setPending] = useState<Record<string,string> | null>(null)
  const [status, setStatus] = useState('等待输入 DOI、URL 或拖入本地 PDF。')
  const open = async () => {
    if (!source.trim()) return
    setStatus('正在解析 DOI/URL 并打开访问页面...')
    const result = await api.openSource(projectId, source.trim())
    setPending(result); setStatus('浏览器已打开。确认正文或 PDF 可以完整访问后继续。')
  }
  const confirm = async () => {
    if (!pending?.article_id) return
    const { task_id } = await api.confirmAccess(projectId, pending.article_id, pending.url || source)
    setStatus('AI 正在读取期刊页面并发现 PDF、表格、图像和相关段落...')
    watchTask(projectId, task_id, (event) => setStatus(event.message), () => {
      setStatus('读取完成。可以进入智能体工作台。'); queryClient.invalidateQueries({ queryKey: ['articles', projectId] })
    })
  }
  return <div className="page"><PageHeader title="文献导入" subtitle="一次导入多篇文献，为每篇文章分配表头，然后交给智能体自动发现资源。" />
    <div className="import-layout"><section className="panel import-source"><h2>DOI 或期刊页面</h2><div className="doi-input"><Link2 size={18}/><input value={source} onChange={(event) => setSource(event.target.value)} placeholder="10.xxxx/xxxxx 或 https://..."/><button className="primary-button" onClick={open}>在浏览器中打开 <ExternalLink size={15}/></button></div>{pending && <div className="access-confirm"><CheckCircle2 size={19}/><div><strong>{pending.title || pending.doi || '已建立文章记录'}</strong><p>请在浏览器中完成人机验证并确认全文可访问。</p></div><button onClick={confirm}>已确认可访问</button></div>}<label className="drop-zone"><Upload size={29}/><strong>拖入 PDF 创建新文章</strong><span>Excel / CSV 可在右侧添加到已存在文章</span><input type="file" accept=".pdf" onChange={async(event)=>{const file=event.target.files?.[0];if(file){setStatus(`正在导入 ${file.name}...`);const result=await api.importArticleFile(projectId,file);setArticleId(result.article_id);setStatus(`${file.name} 已建立文章与独立目录`);await queryClient.invalidateQueries({queryKey:['articles',projectId]});event.target.value=''}}}/></label><div className="import-console"><span>IMPORT AGENT</span><p>{status}</p></div></section>
      <aside className="panel import-queue"><div className="panel-heading"><strong>文献列表</strong><span>{articles.data?.length || 0}</span></div>{articles.data?.map((article) => <article key={article.article_id}><div><strong>{article.title || article.doi}</strong><p>{article.doi || article.url}</p></div><label>表头<select value={article.header_config_id || ''} onChange={async (event) => { await api.assignHeader(projectId, article.article_id, event.target.value); queryClient.invalidateQueries({queryKey:['articles',projectId]}) }}><option value="">未分配</option>{headers.data?.map((config) => <option key={config.config_id} value={config.config_id}>{config.name}</option>)}</select></label><label className="resource-upload-button"><Upload size={14}/> 添加本地 PDF / 数据附件<input type="file" accept=".pdf,.xlsx,.xls,.csv" onChange={async(event)=>{const file=event.target.files?.[0];if(file){setStatus(`正在导入 ${file.name}...`);await api.uploadResource(projectId,article.article_id,file);setStatus(`${file.name} 已保存到文章目录`);queryClient.invalidateQueries({queryKey:['articles',projectId]});event.target.value=''}}}/></label><button onClick={() => { setArticleId(article.article_id); navigate('/workbench') }}>进入工作台 <ArrowRight size={15}/></button></article>)}</aside></div>
  </div>
}

export function ReviewPage() {
  const { projectId, articleId } = useAppStore()
  const queryClient = useQueryClient()
  const records = useQuery({ queryKey: ['candidate-records', projectId, articleId], queryFn: () => api.articleCandidateRecords(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const resources = useQuery({ queryKey: ['resources', projectId, articleId], queryFn: () => api.resources(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const elements = useQuery({ queryKey: ['elements', projectId, articleId], queryFn: () => api.elements(projectId, articleId), enabled: Boolean(projectId && articleId) })
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [activeCell, setActiveCell] = useState<Record<string, unknown> | null>(null)
  const [pdfPage, setPdfPage] = useState(1)
  const [pageCount, setPageCount] = useState(0)

  const rows = records.data?.records || []
  const allHeaders = records.data?.headers || []
  const pdfResources = (resources.data || []).filter((r: Record<string,string>) => r.resource_type?.includes('pdf'))
  const pdfResourceId = pdfResources[0]?.resource_id || ''

  // Build element_id → element info map
  const elementMap = useMemo(() => {
    const map = new Map<string, Record<string, unknown>>()
    for (const el of (elements.data || [])) {
      map.set(el.element_id, el)
    }
    return map
  }, [elements.data])

  // Find SampleID header
  const sampleHeader = useMemo(() => {
    return allHeaders.find((h: {display_header:string;canonical_field:string}) =>
      /sample.?id/i.test(h.canonical_field) || /sample.?id/i.test(h.display_header)
    )?.display_header || allHeaders[0]?.display_header || 'SampleID'
  }, [allHeaders])

  // Extracted data headers (excluding SampleID and "原文" since they're pinned)
  const extractedHeaders = useMemo(() => {
    const seen = new Set<string>()
    const result: string[] = []
    const skip = new Set([sampleHeader, '原文'])
    for (const record of rows) {
      const cells = record.cells as Record<string, Record<string,unknown>>
      for (const [header, cell] of Object.entries(cells || {})) {
        if (skip.has(header)) continue
        if (cell?.value && !seen.has(header)) { seen.add(header); result.push(header) }
      }
    }
    return result
  }, [rows, sampleHeader])

  // Build color map for element_ids
  const RESOURCE_COLORS = ['#e8f4fd', '#fde8f0', '#e8fde8', '#fdf8e8', '#f0e8fd', '#fde8e8', '#e8fdfd', '#fdece8']
  const elementColorMap = useMemo(() => {
    const map = new Map<string, string>()
    let colorIdx = 0
    for (const record of rows) {
      const cells = record.cells as Record<string, Record<string,unknown>>
      for (const cell of Object.values(cells || {})) {
        const eid = cell?.element_id as string
        if (eid && !map.has(eid)) { map.set(eid, RESOURCE_COLORS[colorIdx % RESOURCE_COLORS.length]); colorIdx++ }
      }
    }
    return map
  }, [rows])

  // Get all source labels for a record (may span multiple elements)
  const getSourceLabel = (record: Record<string, unknown>): string => {
    const cells = record.cells as Record<string, Record<string,unknown>>
    const sources = new Map<string, {type: string; page: number}>()
    for (const cell of Object.values(cells || {})) {
      const eid = cell?.element_id as string
      if (!eid || sources.has(eid)) continue
      const el = elementMap.get(eid)
      if (el) {
        sources.set(eid, { type: el.element_type as string, page: el.page_number as number })
      } else if (cell.page_number) {
        sources.set(eid, { type: 'text', page: cell.page_number as number })
      }
    }
    if (!sources.size) return '—'
    const parts: string[] = []
    for (const [, s] of sources) {
      const icon = s.type === 'table' ? '📊' : s.type === 'figure' ? '🖼' : '📝'
      const label = s.type === 'table' ? '表格' : s.type === 'figure' ? '图像' : '段落'
      parts.push(`${icon}${label} p${s.page}`)
    }
    return parts.join(' + ')
  }

  const refresh = () => { queryClient.invalidateQueries({ queryKey: ['candidate-records', projectId, articleId] }) }
  const approve = async (recordId: string) => { await api.approveRecord(projectId, recordId); refresh() }
  const reject = async (recordId: string) => { await api.rejectRecord(projectId, recordId); refresh() }
  const batchApprove = async () => { if (selectedRows.length) { await api.batchApprove(projectId, articleId, selectedRows); setSelectedRows([]); refresh() } }

  const onCellClick = (cell: Record<string, unknown>) => {
    setActiveCell(cell)
    if (cell.page_number) setPdfPage(cell.page_number as number)
  }

  const bbox = activeCell?.bbox as number[] | undefined

  return <div className="page"><PageHeader title="人工审核" subtitle="逐行审核候选数据，颜色区分来源资源，点击单元格查看 PDF 位置。" action={<div style={{display:'flex',gap:8}}><button className="primary-button" disabled={!selectedRows.length} onClick={batchApprove}>批量通过 ({selectedRows.length})</button><button className="primary-button" onClick={async()=>{await api.finalize(projectId,articleId);queryClient.invalidateQueries({queryKey:['standardized']})}}>生成标准化记录</button></div>} />
    <div className="review-layout">
      <section className="panel review-table">
        <div className="panel-heading"><strong>候选记录</strong><span>{rows.length} 行 · {extractedHeaders.length + 1} 列</span></div>
        <div className="simple-table-wrap">
          <table className="simple-table"><thead><tr>
            <th><input type="checkbox" onChange={(e) => setSelectedRows(e.target.checked ? rows.map((r: Record<string,unknown>) => r.candidate_record_id as string) : [])} /></th>
            <th className="col-sample">{sampleHeader}</th>
            <th className="col-source">原文</th>
            {extractedHeaders.map((h) => <th key={h}>{h}</th>)}
            <th>操作</th>
          </tr></thead>
          <tbody>{rows.map((record: Record<string,unknown>) => {
            const cells = record.cells as Record<string, Record<string,unknown>>
            const isSelected = selectedRows.includes(record.candidate_record_id as string)
            const sampleCell = cells?.[sampleHeader] as Record<string, unknown> | undefined
            return <tr key={record.candidate_record_id as string} className={isSelected ? 'selected' : ''}>
              <td><input type="checkbox" checked={isSelected} onChange={() => setSelectedRows(isSelected ? selectedRows.filter(id => id !== record.candidate_record_id) : [...selectedRows, record.candidate_record_id as string])} /></td>
              <td className="col-sample" onClick={() => sampleCell && onCellClick(sampleCell)}>{String(sampleCell?.value ?? record.sample_id ?? '')}</td>
              <td className="col-source">{getSourceLabel(record)}</td>
              {extractedHeaders.map((h) => {
                const cell = cells?.[h] as Record<string, unknown> | undefined
                const eid = cell?.element_id as string | undefined
                const bgColor = eid ? elementColorMap.get(eid) : undefined
                const cls = cell ? (cell.review_status === 'approved' ? 'cell-ok' : cell.mapping_status === 'auto_applied' ? 'cell-auto' : (cell.alternatives as unknown[])?.length ? 'cell-conflict' : '') : 'cell-empty'
                return <td key={h} className={cls} style={bgColor ? {backgroundColor: bgColor} : undefined} onClick={() => cell && onCellClick(cell)}>{String(cell?.value ?? '')}</td>
              })}
              <td><button onClick={() => approve(record.candidate_record_id as string)}>通过</button> <button onClick={() => reject(record.candidate_record_id as string)}>拒绝</button></td>
            </tr>
          })}</tbody></table>
        </div>
      </section>
      <aside className="panel review-pdf">
        <div className="panel-heading"><strong>PDF 原文</strong>{activeCell && <span>第 {pdfPage} / {pageCount || '?'} 页</span>}</div>
        <div className="review-pdf-view">
          {pdfResourceId ? <Document file={api.pdfUrl(projectId, pdfResourceId)} onLoadSuccess={({ numPages }) => setPageCount(numPages)} loading={<div className="empty-state compact">正在载入 PDF...</div>}>
            <div className="pdf-page-wrap" style={{position:'relative'}}>
              <Page pageNumber={pdfPage} width={480} renderAnnotationLayer={false} renderTextLayer={false} />
              {bbox && bbox.length === 4 && <div className="evidence-box-wrap active" style={{left:`${bbox[0]*100}%`,top:`${bbox[1]*100}%`,width:`${(bbox[2]-bbox[0])*100}%`,height:`${(bbox[3]-bbox[1])*100}%`}} />}
            </div>
          </Document> : <div className="empty-state compact">当前文章没有 PDF。</div>}
        </div>
        {activeCell && <div className="cell-provenance">
          <h4>{String(activeCell.target_header || '')}</h4>
          <dl><dt>当前值</dt><dd>{String(activeCell.value || '空')}</dd><dt>原始字段</dt><dd>{String(activeCell.original_field || '—')}</dd><dt>原始单位</dt><dd>{String(activeCell.original_unit || '—')}</dd><dt>目标单位</dt><dd>{String(activeCell.target_unit || '—')}</dd><dt>置信度</dt><dd>{Math.round((activeCell.confidence as number || 0) * 100)}%</dd><dt>PDF 页</dt><dd>{activeCell.page_number ? `第 ${activeCell.page_number} 页` : '—'}</dd></dl>
        </div>}
      </aside>
    </div>
  </div>
}

export function RulesPage() {
  const { projectId } = useAppStore()
  const queryClient = useQueryClient()
  const rules = useQuery({ queryKey: ['rules', projectId], queryFn: () => api.rules(projectId), enabled: Boolean(projectId) })
  const headers = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const [configFilter, setConfigFilter] = useState('')
  const [tab, setTab] = useState<'mapping' | 'extraction'>('mapping')

  const mappingRules = rules.data?.mapping || []
  const extractionRules = rules.data?.extraction || []
  const currentRules = tab === 'mapping' ? mappingRules : extractionRules

  const doDelete = async (ruleId: string) => {
    if (!confirm('确定要删除这条规则吗？')) return
    await api.deleteRule(projectId, ruleId, tab)
    queryClient.invalidateQueries({ queryKey: ['rules', projectId] })
  }

  return <div className="page"><PageHeader title="规则记忆" subtitle="查看用户确认后保存的字段别名、单位换算和资源抽取经验。支持按表头配置筛选和删除。"
    action={<select value={configFilter} onChange={(e) => setConfigFilter(e.target.value)}><option value="">全部表头配置</option>{(headers.data||[]).map((h) => <option key={h.config_id} value={h.config_id}>{h.name}</option>)}</select>} />
    <div className="rules-tabs">
      <button className={tab === 'mapping' ? 'active' : ''} onClick={() => setTab('mapping')}>字段映射规则 ({mappingRules.length})</button>
      <button className={tab === 'extraction' ? 'active' : ''} onClick={() => setTab('extraction')}>抽取规则 ({extractionRules.length})</button>
    </div>
    <section className="panel">
      <table className="simple-table"><thead><tr><th>来源字段</th><th>目标字段</th><th>单位</th><th>公式</th><th>类型</th><th>操作</th></tr></thead>
      <tbody>{currentRules.map((rule: Record<string,unknown>) => <tr key={rule.rule_id as string}>
        <td>{String(rule.source_field || rule.target_field || '—')}</td>
        <td>{String(rule.target_field || '—')}</td>
        <td>{String(rule.target_unit || '—')}</td>
        <td>{String(rule.formula || '—')}</td>
        <td>{tab === 'mapping' ? String(rule.mapping_type || '—') : String(rule.rule_type || '—')}</td>
        <td><button className="danger-button" onClick={() => doDelete(rule.rule_id as string)}>删除</button></td>
      </tr>)}</tbody></table>
      {!currentRules.length && <div className="empty-state compact">暂无规则。映射确认时修改的内容会自动保存为规则。</div>}
    </section>
  </div>
}

export function StandardizedPage() {
  const { projectId, articleId } = useAppStore()
  const queryClient = useQueryClient()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const [filterArticle, setFilterArticle] = useState('')
  const aid = filterArticle || articleId
  const data = useQuery({ queryKey: ['standardized', projectId, aid], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  const rows = (data.data || []).filter((r: Record<string,unknown>) => !aid || r.article_id === aid)
  const SKIP_KEYS = new Set(['quality_grade', 'record_id', 'article_id', 'table_id', 'row_id', 'processed_at', 'data', 'original_fields', 'original_values', 'mapped_fields', 'confidence_scores', 'review_statuses', 'source_file', 'source_table', 'source_row', 'reference', 'doi', 'mapping_rule_ids', 'calculation_ids', 'mapped_units', 'original_units'])
  const fields = useMemo(() => {
    const seen = new Set<string>()
    const result: string[] = []
    for (const row of rows) {
      for (const key of Object.keys((row.data || {}) as Record<string,unknown>)) {
        if (!seen.has(key)) { seen.add(key); result.push(key) }
      }
    }
    return result
  }, [rows])
  const flattened = rows.map((row: Record<string,unknown>) => ({...(row.data as object)}))

  const doExport = async (format: string) => {
    if (!aid) return
    const result = await api.exportArticle(projectId, aid, format)
    alert(`已导出 ${result.records} 条记录到 ${result.path}`)
  }

  return <div className="page"><PageHeader title="标准化导出" subtitle="仅展示满足审核和质量要求的记录，导出时保留完整用户表头。"
    action={<div style={{display:'flex',gap:8}}>
      <select value={filterArticle} onChange={(e) => setFilterArticle(e.target.value)}><option value="">全部文章</option>{(articles.data||[]).map((a) => <option key={a.article_id} value={a.article_id}>{a.title || a.article_id}</option>)}</select>
      <button className="primary-button" onClick={() => doExport('csv')}><Download size={16}/> CSV</button>
      <button className="primary-button" onClick={() => doExport('xlsx')}><Download size={16}/> XLSX</button>
    </div>} />
    <section className="panel"><SimpleTable rows={flattened} columns={fields.map((field) => ({key:field,label:field}))} /></section>
  </div>
}

export function TracePage() {
  const { projectId, articleId } = useAppStore()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const [filterArticle, setFilterArticle] = useState('')
  const aid = filterArticle || articleId
  const trace = useQuery({ queryKey: ['trace', projectId, aid], queryFn: () => api.articleTrace(projectId, aid), enabled: Boolean(projectId && aid) })
  const records = trace.data || []
  const [activeRecord, setActiveRecord] = useState<Record<string, unknown> | null>(null)

  return <div className="page"><PageHeader title="溯源查看" subtitle="从标准化记录返回论文、PDF 页、资源框选、字段映射和计算过程。"
    action={<select value={filterArticle} onChange={(e) => setFilterArticle(e.target.value)}><option value="">全部文章</option>{(articles.data||[]).map((a) => <option key={a.article_id} value={a.article_id}>{a.title || a.article_id}</option>)}</select>} />
    <div className="trace-layout">
      <section className="panel trace-list">
        <div className="panel-heading"><strong>标准化记录</strong><span>{records.length}</span></div>
        <table className="simple-table"><thead><tr><th>记录</th><th>质量</th><th>来源行</th></tr></thead>
        <tbody>{records.map((rec: Record<string,unknown>) => <tr key={rec.record_id as string} className={activeRecord?.record_id === rec.record_id ? 'active' : ''} onClick={() => setActiveRecord(rec)}>
          <td>{rec.record_id as string}</td><td><span className={`grade-badge grade-${rec.quality_grade}`}>{rec.quality_grade as string}</span></td><td>{rec.source_row as string || '—'}</td>
        </tr>)}</tbody></table>
      </section>
      <aside className="panel trace-detail">
        <div className="panel-heading"><strong>溯源链路</strong></div>
        {activeRecord ? <>
          <div className="trace-chain"><span>论文</span><i/><span>PDF 第 {activeRecord.source_page as number || '?'} 页</span><i/><span>资源坐标</span><i/><span>字段映射</span><i/><span>标准值</span></div>
          <h4>数据</h4>
          <dl>{Object.entries((activeRecord.data || {}) as Record<string,string>).slice(0, 12).map(([k, v]) => <dt key={k}>{k}</dt>)}{Object.entries((activeRecord.data || {}) as Record<string,string>).slice(0, 12).map(([k, v]) => <dd key={k+'_v'}>{v || '—'}</dd>)}</dl>
          <h4>原始值</h4>
          <dl>{Object.entries((activeRecord.original_values || {}) as Record<string,string>).slice(0, 8).map(([k, v]) => <dt key={k}>{k}</dt>)}{Object.entries((activeRecord.original_values || {}) as Record<string,string>).slice(0, 8).map(([k, v]) => <dd key={k+'_v'}>{v || '—'}</dd>)}</dl>
          {activeRecord.source_preview && <img src={activeRecord.source_preview as string} style={{maxWidth:'100%',marginTop:8,borderRadius:6}} />}
        </> : <div className="empty-state compact">点击左侧记录查看完整溯源链路。</div>}
      </aside>
    </div>
  </div>
}

export function CostPage() {
  const { projectId } = useAppStore(); const costs = useQuery({ queryKey: ['costs', projectId], queryFn: () => api.costs(projectId), enabled: Boolean(projectId) })
  const rows = costs.data || []; const total = rows.reduce((sum, row) => sum + Number(row.total_tokens || 0), 0)
  return <div className="page"><PageHeader title="Token 统计" subtitle="按模型、Agent 和任务查看调用量、错误和估算成本。" /><div className="metric-grid compact"><section className="metric-tile"><BarChart3 size={20}/><span>总 Token</span><strong>{total.toLocaleString()}</strong></section><section className="metric-tile"><Database size={20}/><span>调用次数</span><strong>{rows.reduce((sum,row)=>sum+Number(row.calls||0),0)}</strong></section><section className="metric-tile"><ShieldAlert size={20}/><span>失败调用</span><strong>{rows.reduce((sum,row)=>sum+Number(row.errors||0),0)}</strong></section></div><section className="panel"><SimpleTable rows={rows} columns={[{key:'model_provider',label:'Provider'},{key:'model_name',label:'模型'},{key:'agent_name',label:'Agent'},{key:'skill_name',label:'任务'},{key:'calls',label:'Calls'},{key:'input_tokens',label:'输入'},{key:'output_tokens',label:'输出'},{key:'total_tokens',label:'总量'},{key:'estimated_cost',label:'成本'}]} /></section></div>
}

export function SettingsPage() {
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const [textModel, setTextModel] = useState('')
  const [visionModel, setVisionModel] = useState('')
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [activeTab, setActiveTab] = useState('model')
  const [testResult, setTestResult] = useState<Record<string, string>>({})
  const textOptions = settings.data?.providers.flatMap((p: any) => p.models.map((m: any) => ({ value: `${p.name}/${m.name}`, label: `${p.display_name} / ${m.display_name}`}))) || []
  const visionOptions = settings.data?.providers.flatMap((p: any) => p.models.filter((m:any)=>m.supports_vision).map((m: any) => ({ value: `${p.name}/${m.name}`, label: `${p.display_name} / ${m.display_name}`}))) || []
  const selectedText = textModel || (settings.data ? `${settings.data.default_provider}/${settings.data.default_model}` : '')
  const selectedVision = visionModel || (settings.data?.vision_provider ? `${settings.data.vision_provider}/${settings.data.vision_model}` : '')

  const setKey = (provider: string, value: string) => setApiKeys(prev => ({...prev, [provider]: value}))

  const save = async () => {
    const [dp, ...dr] = selectedText.split('/')
    const [vp, ...vr] = selectedVision.split('/')
    const keys: Record<string, string> = {}
    for (const [n, v] of Object.entries(apiKeys)) { if (v) keys[n] = v }
    await api.saveSettings({default_provider:dp,default_model:dr.join('/'),vision_provider:vp||'',vision_model:vr.join('/'),api_keys:keys})
    setApiKeys({}); queryClient.invalidateQueries({queryKey:['settings']})
  }

  const TABS = [
    { key: 'model', label: '模型路由', icon: '🧠' },
    { key: 'keys', label: 'API Key', icon: '🔑' },
    { key: 'service', label: '本地服务', icon: '🖥' },
  ]

  return <div className="page"><PageHeader title="设置" subtitle="配置模型、API Key 和任务路由。" action={<button className="primary-button" onClick={save}>保存所有设置</button>} />
    <div className="settings-layout">
      <nav className="panel settings-nav">
        {TABS.map(tab => <button key={tab.key} className={activeTab === tab.key ? 'active' : ''} onClick={() => setActiveTab(tab.key)}>
          <span className="nav-icon">{tab.icon}</span><span>{tab.label}</span>
        </button>)}
      </nav>
      <div className="settings-body">
        {activeTab === 'model' && <section className="panel settings-card">
          <h2>🧠 模型任务路由</h2>
          <p className="card-desc">为不同类型的任务选择最合适的模型。文本模型用于字段映射和规则学习，视觉模型用于图像抽取。</p>
          <div className="setting-item">
            <div className="setting-label"><strong>默认文本模型</strong><span>字段映射、段落抽取和规则学习</span></div>
            <select value={selectedText} onChange={(e)=>setTextModel(e.target.value)}>{textOptions.map((o:any)=><option key={o.value} value={o.value}>{o.label}</option>)}</select>
          </div>
          <div className="setting-item">
            <div className="setting-label"><strong>图像抽取模型</strong><span>必须选择 supports_vision 的模型</span></div>
            <select value={selectedVision} onChange={(e)=>setVisionModel(e.target.value)}><option value="">未配置</option>{visionOptions.map((o:any)=><option key={o.value} value={o.value}>{o.label}</option>)}</select>
          </div>
        </section>}

        {activeTab === 'keys' && <section className="panel settings-card">
          <h2>🔑 API Key 管理</h2>
          <p className="card-desc">密钥保存在本地 config/settings.yaml，不会发送到外部。留空则保持现有值。</p>
          <div className="key-grid">
            {(settings.data?.providers || []).map((provider: any) => {
              const statusColor = provider.key_status === 'configured' ? '#027a48' : provider.key_status === 'missing' ? '#d92d20' : '#53637a'
              const statusLabel = provider.key_status === 'configured' ? '已配置' : provider.key_status === 'missing' ? '未配置' : provider.key_status
              return <div className="key-card" key={provider.name}>
                <div className="key-card-header">
                  <strong>{provider.display_name}</strong>
                  <span className="key-status" style={{color: statusColor}}>{statusLabel}</span>
                </div>
                <input type="password" placeholder="sk-..." value={apiKeys[provider.name] || ''} onChange={(e) => setKey(provider.name, e.target.value)} />
                <div className="key-card-footer">
                  <small>{provider.env_name ? `ENV: ${provider.env_name}` : '直接配置'}</small>
                  <small>{provider.models.length} 个模型</small>
                </div>
              </div>
            })}
          </div>
        </section>}

        {activeTab === 'service' && <section className="panel settings-card">
          <h2>🖥 本地服务</h2>
          <p className="card-desc">GeoChem 本地服务器状态和配置。</p>
          <div className="service-status">
            <div className="status-row"><span className="status-dot online" /><strong>服务运行中</strong><code>127.0.0.1:8765</code></div>
            <div className="status-row"><span>安全模式</span><span>仅本机访问，无外部暴露</span></div>
            <div className="status-row"><span>数据库</span><span>SQLite WAL 模式</span></div>
          </div>
        </section>}
      </div>
    </div>
  </div>
}
