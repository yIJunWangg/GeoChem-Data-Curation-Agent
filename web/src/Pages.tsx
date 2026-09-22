import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Activity, ArrowRight, BarChart3, BookOpen, Bot, CheckCircle2, Clock3, Database,
  Download, FileInput, FileSpreadsheet, FileText, Link2,
  ShieldAlert, Upload, WandSparkles, Workflow,
} from 'lucide-react'
import { api, authenticatedFile, watchTask } from './api'
import { useAppStore } from './store'
import { PageCommandBar } from './WorkspaceUI'
import type { Article } from './types'

function PageHeader({ title, subtitle, action }: { title: string; subtitle: string; action?: React.ReactNode }) {
  return <PageCommandBar title={title} description={subtitle}>{action}</PageCommandBar>
}

function SimpleTable({ rows, columns }: { rows: Record<string, any>[]; columns: {key:string;label:string;render?:(row:Record<string,any>)=>React.ReactNode}[] }) {
  return <div className="simple-table-wrap"><table className="simple-table"><thead><tr>{columns.map((column) => <th key={column.key}>{column.label}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={String(row.id || row.record_id || row.rule_id || row.review_id || index)}>{columns.map((column) => <td key={column.key}>{column.render ? column.render(row) : String(row[column.key] ?? '—')}</td>)}</tr>)}</tbody></table>{!rows.length && <div className="empty-state compact">暂无数据</div>}</div>
}

export function DashboardPage() {
  const { projectId, articleId, setArticleId } = useAppStore()
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const importRef = useRef<HTMLElement>(null)
  const metrics = useQuery({ queryKey: ['dashboard', projectId], queryFn: () => api.dashboard(projectId), enabled: Boolean(projectId) })
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const headers = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const standardized = useQuery({ queryKey: ['standardized', projectId, 'dashboard'], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  const threads = useQuery({ queryKey: ['chat-threads', projectId, 'dashboard'], queryFn: () => api.chatThreads(projectId), enabled: Boolean(projectId) })
  const [source, setSource] = useState('')
  const [pending, setPending] = useState<Record<string, string> | null>(null)
  const [importedResource, setImportedResource] = useState<{article_id:string;resource_id:string;file_name:string;ready:boolean} | null>(null)
  const [importHeaderId, setImportHeaderId] = useState('')
  const [status, setStatus] = useState('等待导入文献。')
  const cards = [
    ['文献', metrics.data?.articles || 0, BookOpen, '#1468d8'],
    ['相关资源', metrics.data?.elements || 0, WandSparkles, '#00875a'],
    ['标准记录', metrics.data?.records || 0, Database, '#0f766e'],
    ['待审核', metrics.data?.reviews || 0, ShieldAlert, '#c2410c'],
    ['学习规则', metrics.data?.rules || 0, CheckCircle2, '#7c3aed'],
  ] as const

  useEffect(() => {
    if (searchParams.get('panel') !== 'import') return
    window.setTimeout(() => importRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 80)
  }, [searchParams])

  const openSource = async () => {
    if (!source.trim()) return
    try {
      setStatus('正在解析 DOI / URL，并打开公开来源页面...')
      const result = await api.openSource(projectId, source.trim())
      setPending(result)
      if (result.article_id) {
        setArticleId(result.article_id)
        setImportedResource({ article_id: result.article_id, resource_id: result.resource_id || '', file_name: result.title || result.doi || source.trim(), ready: false })
        setImportHeaderId('')
      }
      setStatus('浏览器已打开。确认正文或 PDF 可访问后继续。')
    } catch (error) { setStatus(error instanceof Error ? error.message : '来源解析失败，请改用本地 PDF。') }
  }
  const confirmSource = async () => {
    if (!pending?.article_id) return
    try {
      const { task_id } = await api.confirmAccess(projectId, pending.article_id, pending.url || source)
      setStatus('正在读取正文并发现表格、图像和相关段落...')
      watchTask(projectId, task_id, (event) => setStatus(event.message), () => {
        setStatus('文献读取完成。')
        setImportedResource((current) => current ? { ...current, ready: true } : current)
        queryClient.invalidateQueries({ queryKey: ['articles', projectId] })
        queryClient.invalidateQueries({ queryKey: ['dashboard', projectId] })
      })
    } catch (error) { setStatus(error instanceof Error ? error.message : '读取失败，请上传本地 PDF。') }
  }
  const importFile = async (file?: File) => {
    if (!file) return
    try {
      setStatus(`正在导入 ${file.name}...`)
      if (file.name.toLowerCase().endsWith('.pdf')) {
        const result = await api.importArticleFile(projectId, file)
        setArticleId(result.article_id)
        setPending(null)
        setImportedResource({ article_id: result.article_id, resource_id: result.resource_id, file_name: result.file_name || file.name, ready: true })
        setImportHeaderId('')
      } else if (articleId) {
        await api.uploadResource(projectId, articleId, file)
      } else {
        setStatus('请先从最近文献中选择文章，再添加 Excel / CSV 附件。')
        return
      }
      setStatus(`${file.name} 已保存到文章目录。`)
      await queryClient.invalidateQueries({ queryKey: ['articles', projectId] })
      await queryClient.invalidateQueries({ queryKey: ['dashboard', projectId] })
    } catch (error) { setStatus(error instanceof Error ? error.message : '文件导入失败。') }
  }
  const recentThreads = (threads.data || []).slice(0, 5)
  const quickResourceArticleId = importedResource?.article_id || articleId
  const importedArticle = articles.data?.find((article) => article.article_id === quickResourceArticleId)
  const quickResource = importedResource || (importedArticle ? {
    article_id: importedArticle.article_id,
    resource_id: '',
    file_name: importedArticle.title || importedArticle.article_id,
    ready: true,
  } : null)
  useEffect(() => setImportHeaderId(''), [quickResourceArticleId])
  const selectedImportHeader = importHeaderId || importedArticle?.header_config_id || ''
  const assignImportedHeader = async (configId: string) => {
    if (!quickResource?.article_id) return
    try {
      setImportHeaderId(configId)
      await api.assignHeader(projectId, quickResource.article_id, configId)
      await queryClient.invalidateQueries({ queryKey: ['articles', projectId] })
      await queryClient.invalidateQueries({ queryKey: ['dashboard', projectId] })
      const configName = headers.data?.find((item) => item.config_id === configId)?.name
      setStatus(configName ? `已为当前文献分配表头：${configName}` : '已取消当前文献的表头分配。')
    } catch (error) {
      setStatus(error instanceof Error ? error.message : '表头分配失败。')
    }
  }
  const standardizedByArticle = useMemo(() => {
    const counts = new Map<string, number>()
    for (const record of standardized.data || []) {
      const recordArticleId = String((record as Record<string, unknown>).article_id || '')
      if (recordArticleId) counts.set(recordArticleId, (counts.get(recordArticleId) || 0) + 1)
    }
    return counts
  }, [standardized.data])

  return <div className="page dashboard-page gpt-workspace-page"><PageHeader title="项目概览" subtitle="文献、导入与任务状态" />
    <div className="metric-grid dashboard-metrics">{cards.map(([label, value, Icon, color]) => <section className="metric-tile" key={label}><Icon color={color} size={19} /><span>{label}</span><strong>{value}</strong></section>)}</div>
    <div className="dashboard-grid">
      <section className="panel dashboard-literature"><div className="panel-heading"><strong>最近文献</strong><span>{articles.data?.length || 0}</span></div>
        <div className="dashboard-article-table"><table><thead><tr><th>文献标题</th><th>DOI / 来源</th><th>表头</th><th>证据</th><th>审核状态</th><th aria-label="操作"/></tr></thead><tbody>{(articles.data || []).slice(0, 8).map((article) => {
          const config = headers.data?.find((item) => item.config_id === article.header_config_id)
          const standardizedCount = standardizedByArticle.get(article.article_id) || 0
          const reviewComplete = standardizedCount > 0 || ['completed', 'standardized', 'reviewed'].includes(article.status || '')
          return <tr key={article.article_id} className={article.article_id === articleId ? 'active' : ''} onClick={() => setArticleId(article.article_id)}><td><strong>{article.title || article.article_id}</strong><small>{article.journal || article.url || article.article_id}</small></td><td>{article.doi || '未登记 DOI'}</td><td>{config?.name || <span className="status-text warning">未分配</span>}</td><td>{article.element_count || 0}</td><td><span className={`article-status ${reviewComplete ? 'reviewed' : 'pending'}`} title={reviewComplete ? `${standardizedCount} 条标准化记录` : '尚未生成标准化记录'}>{reviewComplete ? '完成审核' : '待审核'}</span></td><td><div className="row-actions"><button title="进入抽取工作台" onClick={(event) => { event.stopPropagation(); setArticleId(article.article_id); navigate('/workbench') }}><Workflow size={14}/></button><button title="在科研助手中打开" onClick={(event) => { event.stopPropagation(); setArticleId(article.article_id); navigate('/') }}><Bot size={14}/></button></div></td></tr>
        })}</tbody></table>{!articles.data?.length && <div className="empty-state compact">还没有文献，可从右侧快速导入。</div>}</div>
      </section>
      <section ref={importRef} className={`panel dashboard-import ${quickResource ? 'has-resource' : ''}`}><div className="panel-heading"><strong>快速导入文献</strong><FileInput size={17}/></div>
        <div className="import-tabs"><span className="active">DOI / URL</span><span>本地文件</span></div>
        <div className="quick-import-form"><div className="doi-input compact"><Link2 size={16}/><input value={source} onChange={(event) => setSource(event.target.value)} placeholder="输入 DOI、URL 或期刊主页链接"/></div><button className="primary-button" onClick={openSource}>导入并解析</button></div>
        {quickResource && <article className="quick-import-resource-card">
          <div className="quick-resource-summary"><FileText size={18}/><span><strong>{importedArticle?.title || quickResource.file_name}</strong><small>{quickResource.ready ? 'PDF / 文献资源已导入' : '等待确认公开正文或 PDF'} · {quickResource.article_id}</small></span>{quickResource.ready && <CheckCircle2 size={16}/>}</div>
          <div className="quick-resource-controls"><label><span>目标表头</span><select value={selectedImportHeader} onChange={(event) => assignImportedHeader(event.target.value)}><option value="">未分配</option>{headers.data?.map((config) => <option key={config.config_id} value={config.config_id}>{config.name} · {config.field_count} 字段</option>)}</select></label>{pending && !quickResource.ready ? <button onClick={confirmSource}>确认可访问</button> : <button onClick={() => { setArticleId(quickResource.article_id); navigate('/workbench') }}>进入工作台 <ArrowRight size={14}/></button>}</div>
        </article>}
        <label className="dashboard-drop-zone"><Upload size={22}/><strong>拖放 PDF / Excel / CSV 到此处</strong><span>PDF 建立新文章；数据附件加入当前文献</span><input type="file" accept=".pdf,.xlsx,.xls,.csv" onChange={(event) => { importFile(event.target.files?.[0]); event.target.value = '' }}/></label>
        <p className="quick-import-status">{status}</p>
      </section>
      <section className="panel dashboard-tasks"><div className="panel-heading"><strong>最近处理任务</strong><span>{recentThreads.length}</span></div><div className="recent-task-list">{recentThreads.map((thread) => <button key={thread.thread_id} onClick={() => navigate(`/?thread_id=${encodeURIComponent(thread.thread_id)}`)}><Bot size={15}/><span><strong>{thread.title}</strong><small>{thread.active_run_status || '对话已保存'}</small></span><time>{thread.updated_at ? new Date(thread.updated_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '—'}</time></button>)}{!recentThreads.length && <div className="empty-state compact">暂无最近任务。</div>}</div></section>
      <section className="panel dashboard-activity"><div className="panel-heading"><strong>活动与状态</strong><Activity size={17}/></div><div className="activity-list"><div><CheckCircle2 size={16}/><span><strong>本地服务正常</strong><small>API 与项目数据库可用</small></span></div><div><BookOpen size={16}/><span><strong>{metrics.data?.articles || 0} 篇文献</strong><small>{metrics.data?.elements || 0} 个相关资源</small></span></div><div><Clock3 size={16}/><span><strong>{metrics.data?.reviews || 0} 项待审核</strong><small>确认后可生成标准化记录</small></span></div></div></section>
    </div>
  </div>
}

export function HeaderPage() {
  const { projectId, articleId } = useAppStore()
  const queryClient = useQueryClient()
  const configs = useQuery({ queryKey: ['headers', projectId], queryFn: () => api.headers(projectId), enabled: Boolean(projectId) })
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const [activeId, setActiveId] = useState('')
  const [activeFieldIndex, setActiveFieldIndex] = useState(0)
  const active = configs.data?.find((item) => item.config_id === activeId) || configs.data?.[0]
  const rows = active?.headers.map((field, index) => ({
    index: index + 1,
    display_header: field.display_header || field['字段名'] || field['表头'] || '',
    canonical_field: field.canonical_field || field['标准字段'] || '',
    target_unit: field.target_unit || field['默认单位'] || field['单位'] || '',
    description: field.description || field['description'] || field['字段说明'] || '',
    ...field,
  })) || []
  const selectedField = rows[Math.min(activeFieldIndex, Math.max(0, rows.length - 1))]
  return <div className="page header-page gpt-workspace-page"><PageHeader title="表头管理" subtitle={active ? `${active.name} · ${rows.length} 个字段` : '维护可复用目标表头'} action={<label className="primary-button file-button"><Upload size={16}/> 导入 CSV / XLSX<input type="file" accept=".csv,.xlsx,.xls" onChange={async (event) => { const file=event.target.files?.[0]; if(file){ await api.importHeaders(projectId,file); queryClient.invalidateQueries({queryKey:['headers',projectId]}); event.target.value='' } }}/></label>} />
    <div className="header-management-layout"><aside className="panel config-list"><div className="panel-heading"><strong>已保存表头</strong><span>{configs.data?.length || 0}</span></div>{configs.data?.map((config) => <button className={active?.config_id === config.config_id ? 'active' : ''} key={config.config_id} onClick={() => { setActiveId(config.config_id); setActiveFieldIndex(0) }}><FileSpreadsheet size={17}/><span><strong>{config.name}</strong><small>{config.field_count} 个字段</small></span></button>)}</aside>
      <section className="panel header-editor"><div className="panel-heading"><strong>{active?.name || '未选择配置'}</strong><span>{rows.length} 列</span></div><div className="simple-table-wrap selectable-header-table"><table className="simple-table"><thead><tr><th>#</th><th>显示表头</th><th>标准字段</th><th>目标单位</th><th>字段说明</th></tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.display_header}-${index}`} className={index === activeFieldIndex ? 'active' : ''} onClick={() => setActiveFieldIndex(index)}><td>{index + 1}</td><td>{row.display_header || '—'}</td><td>{row.canonical_field || '—'}</td><td>{row.target_unit || '—'}</td><td>{row.description || '—'}</td></tr>)}</tbody></table></div></section>
      <aside className="panel header-field-inspector"><div className="panel-heading"><strong>字段详情</strong></div>{selectedField ? <div className="header-field-detail"><label>显示表头<input value={selectedField.display_header || ''} readOnly/></label><label>标准字段<input value={selectedField.canonical_field || ''} readOnly/></label><label>目标单位<input value={selectedField.target_unit || ''} readOnly/></label><label>字段说明<textarea value={selectedField.description || ''} readOnly rows={6}/></label><small>字段名称、单位和说明来自当前保存配置。重新导入配置可批量更新。</small></div> : <div className="empty-state compact">选择中间字段查看详情。</div>}<div className="header-assignment"><strong>分配给文章</strong><select value={(articles.data || []).find((article) => article.article_id === articleId)?.header_config_id || ''} disabled={!articleId || !active} onChange={async (event) => { if (!articleId) return; await api.assignHeader(projectId, articleId, event.target.value); queryClient.invalidateQueries({ queryKey: ['articles', projectId] }) }}><option value="">未分配</option>{configs.data?.map((config) => <option key={config.config_id} value={config.config_id}>{config.name}</option>)}</select><small>{articleId ? '应用到当前文献。' : '请先在顶部选择当前文献。'}</small></div></aside>
    </div>
  </div>
}

export function StandardizedPage() {
  const { projectId, articleId } = useAppStore()
  const queryClient = useQueryClient()
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const [filterArticle, setFilterArticle] = useState('')
  const [exportDir, setExportDir] = useState('')
  const [exportMessage, setExportMessage] = useState('')
  const exportArticleId = filterArticle || articleId
  const data = useQuery({ queryKey: ['standardized', projectId], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  const exportDirectory = useQuery({ queryKey: ['export-directory', projectId], queryFn: () => api.exportDirectory(projectId), enabled: Boolean(projectId) })
  const exportJobs = useQuery({ queryKey: ['export-jobs', projectId, exportArticleId], queryFn: () => api.exportJobs(projectId, exportArticleId), enabled: Boolean(projectId) })
  const rows = (data.data || []).filter((r: Record<string,unknown>) => !filterArticle || r.article_id === filterArticle)
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

  useEffect(() => {
    if (exportDirectory.data?.path && !exportDir) setExportDir(exportDirectory.data.path)
  }, [exportDir, exportDirectory.data?.path])

  const saveExportDir = async () => {
    await api.saveExportDirectory(exportDir === exportDirectory.data?.path && exportDirectory.data?.is_default ? '' : exportDir)
    await queryClient.invalidateQueries({ queryKey: ['export-directory', projectId] })
    setExportMessage('导出目录已保存。')
  }

  const resetExportDir = async () => {
    await api.saveExportDirectory('')
    setExportDir('')
    await queryClient.invalidateQueries({ queryKey: ['export-directory', projectId] })
    setExportMessage('已恢复为当前工作区 output 目录。')
  }

  const openExportDir = async () => {
    const result = await api.openExportDirectory(projectId, exportDir)
    setExportMessage(`已打开目录：${result.path}`)
  }

  const doExport = async (format: string) => {
    if (!exportArticleId) return
    try {
      setExportMessage('正在生成导出文件…')
      const result = await api.exportArticle(
        projectId,
        exportArticleId,
        format,
        exportDirectory.data?.managed ? '' : exportDir,
      )
      const filename = await api.downloadExport(projectId, result.job_id)
      setExportMessage(`已生成并下载 ${filename}（${result.records} 条记录）`)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['standardized', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['trace-records', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['dashboard', projectId] }),
        queryClient.invalidateQueries({ queryKey: ['export-jobs', projectId] }),
      ])
    } catch (error) {
      setExportMessage(`导出失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  return <div className="page standardized-page gpt-workspace-page"><PageHeader title="标准化导出" subtitle={`${rows.length} 条已完成审核记录`}
    action={<div className="standardized-export-actions">
      <select value={filterArticle} onChange={(e) => setFilterArticle(e.target.value)}><option value="">全部文章</option>{(articles.data||[]).map((a) => <option key={a.article_id} value={a.article_id}>{a.title || a.article_id}</option>)}</select>
      <button className="primary-button" title="导出顶部当前文献或筛选文献" disabled={!exportArticleId} onClick={() => doExport('csv')}><Download size={16}/> CSV</button>
      <button className="primary-button" title="导出顶部当前文献或筛选文献" disabled={!exportArticleId} onClick={() => doExport('xlsx')}><Download size={16}/> XLSX</button>
    </div>} />
    <section className="panel standardized-data-panel">
      <div className="panel-heading standardized-table-heading"><div><strong>已完成审核的记录</strong><small>{filterArticle ? '当前筛选文献' : '全部文章'}</small></div><span>{rows.length}</span></div>
      {rows.length ? <SimpleTable rows={flattened} columns={fields.map((field) => ({key:field,label:field}))} /> : <div className="standardized-empty"><Database size={30}/><strong>当前范围暂无标准化记录</strong><span>请切换文章，或先到“人工审核”生成标准化记录。</span></div>}
    </section>
    <section className="panel export-directory-card compact-export-directory">
      <div className="export-directory-label"><strong>导出文件</strong><small>{exportDirectory.data?.managed ? '服务器统一保存，生成后自动下载' : (exportDirectory.data?.is_default ? '工作区默认目录' : '自定义目录')}</small></div>
      {!exportDirectory.data?.managed && <>
        <input value={exportDir} onChange={(event) => setExportDir(event.target.value)} placeholder="默认使用当前工作区 output 目录" />
        <button onClick={saveExportDir}>保存</button>
        <button onClick={resetExportDir}>恢复默认</button>
        <button onClick={openExportDir}>打开目录</button>
      </>}
      {exportDirectory.data?.managed && <span className="managed-export-note">导出文件保存在服务器受管目录，可从下方记录再次下载。</span>}
      {exportMessage && <span className="export-inline-message">{exportMessage}</span>}
    </section>
    {!!exportJobs.data?.length && <section className="panel recent-export-jobs">
      <div className="panel-heading"><strong>最近导出</strong><span>{exportJobs.data.length}</span></div>
      <div className="recent-export-list">{exportJobs.data.map((job) => <div key={job.job_id} className="recent-export-row">
        <span>{job.article_id || '全部记录'}</span>
        <span>{job.export_format.toUpperCase()} · {job.record_count} 条</span>
        <time>{job.created_at?.replace('T', ' ').slice(0, 16)}</time>
        <button disabled={job.status !== 'completed'} onClick={async () => {
          try { setExportMessage(`已下载 ${await api.downloadExport(projectId, job.job_id)}`) }
          catch (error) { setExportMessage(`下载失败：${error instanceof Error ? error.message : String(error)}`) }
        }}><Download size={15}/> 下载</button>
      </div>)}</div>
    </section>}
  </div>
}

export function SettingsPage() {
  const { projectId } = useAppStore()
  const [searchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const settings = useQuery({ queryKey: ['settings'], queryFn: api.settings })
  const modelSetup = useQuery({ queryKey: ['model-setup'], queryFn: api.modelSetup })
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({})
  const [liveModels, setLiveModels] = useState<Record<string, {name:string;display_name:string;supports_vision:boolean}[]>>({})
  const [, setTesting] = useState<Record<string, string>>({})
  const [activeTab, setActiveTab] = useState(() => searchParams.get('tab') === 'usage' ? 'usage' : 'model')
  const costs = useQuery({ queryKey: ['costs', projectId], queryFn: () => api.costs(projectId), enabled: Boolean(projectId && activeTab === 'usage') })
  const [setupTesting, setSetupTesting] = useState(false)
  const [setupLog, setSetupLog] = useState('')
  const [setupForm, setSetupForm] = useState({ provider_preset: 'opencode-go', model_id: '', api_key: '', base_url: '' })
  const [providersDraft, setProvidersDraft] = useState<any[]>([])
  const [taskRoutes, setTaskRoutes] = useState<Record<string, any>>({})
  const [activeProvider, setActiveProvider] = useState('')
  const [newModelName, setNewModelName] = useState('')
  const [customProvider, setCustomProvider] = useState({
    name: 'opencode-go',
    display_name: 'OpenCode GO',
    api_format: 'openai',
    base_url: '',
    api_key_ref: '${OPENCODE_GO_API_KEY}',
    auth_type: 'bearer',
    api_key_header: 'Authorization',
    model: 'go-plan',
  })

  useEffect(() => {
    if (!settings.data) return
    setProvidersDraft(settings.data.providers || [])
    setTaskRoutes(settings.data.task_models || {
      _default: { provider: settings.data.default_provider, model: settings.data.default_model, temperature: 0.1, max_tokens: 4096 },
    })
    setActiveProvider((settings.data.providers || [])[0]?.name || '')
  }, [settings.data])

  useEffect(() => {
    if (!modelSetup.data) return
    setSetupForm(prev => ({
      ...prev,
      provider_preset: modelSetup.data.provider_preset || prev.provider_preset || 'opencode-go',
      model_id: modelSetup.data.model || prev.model_id || '',
      base_url: '',
    }))
  }, [modelSetup.data])

  useEffect(() => {
    const requested = searchParams.get('tab')
    if (requested && ['model', 'usage', 'advanced', 'service'].includes(requested)) setActiveTab(requested)
  }, [searchParams])

  const getModels = (providerName: string) => {
    if (liveModels[providerName]) return liveModels[providerName]
    const p = providersDraft.find((x: any) => x.name === providerName)
    return p?.models || []
  }

  const setupPresets = modelSetup.data?.presets || [
    { id: 'opencode-go', label: 'OpenCode Go' },
    { id: 'deepseek', label: 'DeepSeek' },
    { id: 'xiaomi', label: 'Xiaomi MiMo' },
    { id: 'openrouter', label: 'OpenRouter' },
    { id: 'openai', label: 'OpenAI' },
    { id: 'anthropic', label: 'Anthropic' },
    { id: 'custom-openai', label: '自定义 OpenAI-compatible' },
    { id: 'custom-anthropic', label: '自定义 Anthropic-compatible' },
  ]
  const routeKeys = Object.keys(settings.data?.task_route_meta || { _default: '默认文本模型', field_mapping: '字段映射', document_record_extraction: '文献/段落抽取', unit_suggestion: '单位换算建议', rule_learning: '规则学习', figure_extraction: '图像处理' })
  const updateRoute = (name: string, patch: Record<string, unknown>) => setTaskRoutes(prev => ({...prev, [name]: {...(prev[name] || {}), ...patch}}))
  const activeProviderData = providersDraft.find((provider: any) => provider.name === activeProvider)
  const upsertProviderDraft = (provider: any) => setProvidersDraft(prev => {
    const exists = prev.some((item: any) => item.name === provider.name)
    return exists ? prev.map((item: any) => item.name === provider.name ? provider : item) : [...prev, provider]
  })
  const addModelToProvider = () => {
    if (!activeProviderData || !newModelName.trim()) return
    const model = { name: newModelName.trim(), display_name: newModelName.trim(), supports_vision: false, max_tokens: 4096 }
    upsertProviderDraft({...activeProviderData, models: [...(activeProviderData.models || []).filter((m: any) => m.name !== model.name), model]})
    setNewModelName('')
  }
  const toggleModelVision = (modelName: string) => {
    if (!activeProviderData) return
    upsertProviderDraft({...activeProviderData, models: (activeProviderData.models || []).map((m: any) => m.name === modelName ? {...m, supports_vision: !m.supports_vision} : m)})
  }
  const updateActiveProvider = (patch: Record<string, unknown>) => {
    if (!activeProviderData) return
    upsertProviderDraft({...activeProviderData, ...patch})
  }
  const applyProviderTemplate = (kind: 'openai' | 'anthropic') => {
    setCustomProvider({
      name: kind === 'openai' ? 'custom-openai-compatible' : 'custom-anthropic-compatible',
      display_name: kind === 'openai' ? '自定义 OpenAI-compatible' : '自定义 Anthropic-compatible',
      api_format: kind,
      base_url: '',
      api_key_ref: kind === 'openai' ? '${CUSTOM_OPENAI_API_KEY}' : '${CUSTOM_ANTHROPIC_API_KEY}',
      auth_type: 'bearer',
      api_key_header: 'Authorization',
      model: '',
    })
  }
  const setupProviderNames = (preset: string) => {
    if (preset === 'opencode-go') return ['opencode-go-openai', 'opencode-go-anthropic']
    if (preset === 'deepseek') return ['deepseek-openai', 'deepseek-anthropic']
    if (preset === 'xiaomi') return ['xiaomi', 'xiaomi-anthropic']
    if (preset === 'custom-openai') return ['custom-openai-compatible']
    if (preset === 'custom-anthropic') return ['custom-anthropic-compatible']
    return [preset]
  }
  const setupModelOptions = Array.from(new Set(setupProviderNames(setupForm.provider_preset).flatMap(name => (providersDraft.find((p: any) => p.name === name)?.models || []).map((model: any) => model.name))))
  const isCustomSetup = setupForm.provider_preset.startsWith('custom-')
  const keyStatusText = modelSetup.data?.key_status_label || (
    modelSetup.data?.key_status === 'configured' ? '已配置' :
    modelSetup.data?.key_status === 'missing' ? '等待环境变量或钥匙串' :
    modelSetup.data?.key_status === 'inline' ? '需要迁移明文 Key' : '未配置'
  )
  const runSetup = async (saveIt: boolean) => {
    if (!setupForm.provider_preset || !setupForm.model_id.trim()) {
      setSetupLog('请选择服务商并输入模型 ID。')
      return
    }
    setSetupTesting(true)
    setSetupLog(saveIt ? '正在测试并保存模型配置...' : '正在测试模型配置...')
    try {
      const payload = {...setupForm, model_id: setupForm.model_id.trim()}
      const result = saveIt ? await api.saveModelSetup(payload) : await api.testModelSetup(payload)
      if (result.success === false) {
        setSetupLog(result.message || '测试失败')
      } else {
        setSetupLog(result.last_test?.message || result.message || (saveIt ? '已保存并应用到全部智能体任务。' : '测试通过，尚未保存。'))
        setSetupForm(prev => ({...prev, api_key: ''}))
        queryClient.invalidateQueries({queryKey:['settings']})
        queryClient.invalidateQueries({queryKey:['model-setup']})
      }
    } catch (err) {
      setSetupLog(err instanceof Error ? err.message : '测试失败')
    } finally {
      setSetupTesting(false)
    }
  }

  const testConnection = async (providerName: string) => {
    const key = apiKeys[providerName]
    const provider = providersDraft.find((item: any) => item.name === providerName)
    if (!key && !provider?.api_key_ref) { alert('请先输入 API Key 或配置环境变量占位'); return }
    setTesting(prev => ({...prev, [providerName]: 'testing'}))
    try {
      const result = await api.testCustomProvider({
        provider: providerName,
        display_name: provider?.display_name || providerName,
        api_format: provider?.api_format || 'openai',
        base_url: provider?.base_url || '',
        api_key_ref: provider?.api_key_ref || '',
        api_key: key,
        model: provider?.models?.[0]?.name || '',
        default_headers: provider?.default_headers || {},
        auth_type: provider?.auth_type || 'bearer',
        api_key_header: provider?.api_key_header || 'Authorization',
      })
      if (result.success) {
        setLiveModels(prev => ({...prev, [providerName]: result.models}))
        if (result.models.length && provider) {
          upsertProviderDraft({...provider, models: result.models})
        }
        setTesting(prev => ({...prev, [providerName]: 'ok'}))
      } else {
        setTesting(prev => ({...prev, [providerName]: 'fail'}))
      }
    } catch {
      setTesting(prev => ({...prev, [providerName]: 'fail'}))
    }
  }

  const save = async () => {
    const keys: Record<string, string> = {}
    for (const [n, v] of Object.entries(apiKeys)) { if (v) keys[n] = v }
    const defaultRoute = taskRoutes._default || {}
    const visionRoute = taskRoutes.figure_extraction || {}
    await api.saveSettings({
      default_provider: defaultRoute.provider || settings.data?.default_provider || '',
      default_model: defaultRoute.model || settings.data?.default_model || '',
      vision_provider: visionRoute.provider || '',
      vision_model: visionRoute.model || '',
      task_models: taskRoutes,
      providers: providersDraft,
      api_keys: keys,
    })
    setApiKeys({}); setLiveModels({}); setTesting({})
    queryClient.invalidateQueries({queryKey:['settings']})
  }

  const TABS = [
    { key: 'model', label: '模型配置', icon: '🧠' },
    { key: 'usage', label: '用量与成本', icon: '📊' },
    { key: 'advanced', label: '高级设置', icon: '🧩' },
    { key: 'service', label: '本地服务', icon: '🖥' },
  ]

  const addCustomProvider = () => {
    const provider = {
      name: customProvider.name.trim(),
      display_name: customProvider.display_name.trim() || customProvider.name.trim(),
      api_format: customProvider.api_format,
      base_url: customProvider.base_url.trim(),
      api_key_ref: customProvider.api_key_ref.trim(),
      enabled: true,
      is_custom: true,
      auth_type: customProvider.auth_type,
      api_key_header: customProvider.api_key_header,
      default_headers: {},
      models: customProvider.model.trim() ? [{ name: customProvider.model.trim(), display_name: customProvider.model.trim(), supports_vision: false, max_tokens: 4096 }] : [],
    }
    if (!provider.name) return
    upsertProviderDraft(provider)
    setActiveProvider(provider.name)
  }

  return <div className="page settings-page gpt-workspace-page"><PageHeader title="设置" subtitle="模型、用量与本地服务" action={activeTab === 'advanced' ? <button className="primary-button" onClick={save}>保存高级设置</button> : undefined} />
    <div className="settings-layout">
      <nav className="panel settings-nav">
        {TABS.map(tab => <button key={tab.key} className={activeTab === tab.key ? 'active' : ''} onClick={() => setActiveTab(tab.key)}>
          <span className="nav-icon">{tab.icon}</span><span>{tab.label}</span>
        </button>)}
      </nav>
      <div className="settings-body">

        {activeTab === 'model' && <div className="model-setup-page">
          <section className={`panel model-status-card ${modelSetup.data?.configured ? 'configured' : ''}`}>
            <div>
              <h2>{modelSetup.data?.configured ? '模型已配置' : '模型尚未完整配置'}</h2>
              <p>{modelSetup.data?.configured ? `${modelSetup.data.provider_label || modelSetup.data.provider} / ${modelSetup.data.model}` : '选择服务商、输入模型 ID 和 API Key 后即可使用。'}</p>
            </div>
            <div className="model-status-grid">
              <span><small>Key 状态</small><strong>{keyStatusText}</strong></span>
              <span><small>任务应用</small><strong>{modelSetup.data?.all_text_tasks_use_same_model ? '全部文本任务' : '尚未统一'}</strong></span>
              <span><small>最近测试</small><strong>{modelSetup.data?.last_test?.last_tested_at || '--'}</strong></span>
            </div>
          </section>
          <section className="panel settings-card model-setup-card">
            <h2>模型配置</h2>
            <p className="card-desc">普通情况下只需要配置一个模型。保存后字段映射、文献抽取、规则学习等文本任务都会使用它。</p>
            <div className="simple-model-form">
              <label>服务商<select value={setupForm.provider_preset} onChange={(e) => setSetupForm({...setupForm, provider_preset: e.target.value, model_id: '', base_url: ''})}>{setupPresets.map((preset: any) => <option key={preset.id} value={preset.id}>{preset.label}</option>)}</select></label>
              {isCustomSetup && <label>Base URL<input value={setupForm.base_url} onChange={(e) => setSetupForm({...setupForm, base_url: e.target.value})} placeholder="https://.../v1" /></label>}
              <label>模型 ID<input list="setup-models" value={setupForm.model_id} onChange={(e) => setSetupForm({...setupForm, model_id: e.target.value})} placeholder="例如 kimi-k2.7-code" /></label>
              <datalist id="setup-models">{setupModelOptions.map(name => <option key={name} value={name} />)}</datalist>
              <label>API Key<input type="password" value={setupForm.api_key} onChange={(e) => setSetupForm({...setupForm, api_key: e.target.value})} placeholder={modelSetup.data?.key_status === 'configured' ? '已安全保存，可留空继续使用' : '粘贴一次用于测试和安全保存'} /></label>
            </div>
            <p className="secret-hint">保存后配置文件只记录环境变量引用，不保存明文 API Key。当前引用：<code>{modelSetup.data?.api_key_ref || '尚未生成'}</code></p>
            <div className="model-setup-actions"><button onClick={() => runSetup(false)} disabled={setupTesting}>仅测试</button><button className="primary-button" onClick={() => runSetup(true)} disabled={setupTesting}>{setupTesting ? '处理中...' : modelSetup.data?.key_status === 'configured' ? '更新 Key / 测试并保存' : '测试并保存'}</button></div>
            <div className={`setup-console ${setupLog.includes('失败') || setupLog.includes('错误') ? 'fail' : ''}`}>{setupLog || modelSetup.data?.last_test?.message || '等待测试。'}</div>
          </section>
        </div>}

        {activeTab === 'usage' && <div className="usage-settings-page">
          <div className="metric-grid compact usage-metrics"><section className="metric-tile"><BarChart3 size={20}/><span>总 Token</span><strong>{(costs.data || []).reduce((sum, row) => sum + Number(row.total_tokens || 0), 0).toLocaleString()}</strong></section><section className="metric-tile"><Database size={20}/><span>调用次数</span><strong>{(costs.data || []).reduce((sum, row) => sum + Number(row.calls || 0), 0)}</strong></section><section className="metric-tile"><ShieldAlert size={20}/><span>失败调用</span><strong>{(costs.data || []).reduce((sum, row) => sum + Number(row.errors || 0), 0)}</strong></section></div>
          <section className="panel usage-table"><div className="panel-heading"><strong>模型调用明细</strong><span>{costs.data?.length || 0}</span></div><SimpleTable rows={costs.data || []} columns={[{key:'model_provider',label:'Provider'},{key:'model_name',label:'模型'},{key:'agent_name',label:'Agent'},{key:'skill_name',label:'任务'},{key:'calls',label:'Calls'},{key:'input_tokens',label:'输入'},{key:'output_tokens',label:'输出'},{key:'total_tokens',label:'总量'},{key:'estimated_cost',label:'成本'}]} /></section>
        </div>}

        {activeTab === 'advanced' && <div className="advanced-settings">
          <details className="panel settings-card" open><summary>任务级模型路由</summary><p className="card-desc">通常不需要修改。只有当你想让不同任务使用不同模型时再调整这里。</p><div className="route-table">{routeKeys.map((name) => { const route = taskRoutes[name] || {}; const models = getModels(route.provider || ''); return <div className="route-row" key={name}><strong>{settings.data?.task_route_meta?.[name] || name}<small>{name}</small></strong><select value={route.provider || ''} onChange={(e) => updateRoute(name, { provider: e.target.value, model: '' })}><option value="">继承默认/未启用</option>{providersDraft.map((p: any) => <option key={p.name} value={p.name}>{p.display_name || p.name}</option>)}</select><input list={`models-${name}`} value={route.model || ''} onChange={(e) => updateRoute(name, { model: e.target.value })} placeholder="输入或选择模型名" /><datalist id={`models-${name}`}>{models.map((m: any) => <option key={m.name} value={m.name}>{m.display_name || m.name}</option>)}</datalist><input type="number" step="0.1" min="0" max="2" value={route.temperature ?? 0.1} onChange={(e) => updateRoute(name, { temperature: Number(e.target.value) })} title="temperature" /><input type="number" min="1" value={route.max_tokens ?? 4096} onChange={(e) => updateRoute(name, { max_tokens: Number(e.target.value) })} title="max_tokens" /></div>})}</div></details>
          <details className="panel settings-card"><summary>服务商与自定义配置</summary><p className="card-desc">通常不需要修改。这里保留 base_url、headers、auth type 等兼容配置。</p><div className="provider-manager"><section className="provider-list"><h2>服务商</h2>{providersDraft.map((provider: any) => <button key={provider.name} className={activeProvider === provider.name ? 'active' : ''} onClick={() => setActiveProvider(provider.name)}><strong>{provider.display_name || provider.name}</strong><small>{provider.api_format} · {provider.models?.length || 0} models</small></button>)}</section><section className="provider-detail"><div className="template-row"><button onClick={() => applyProviderTemplate('openai')}>自定义 OpenAI-compatible</button><button onClick={() => applyProviderTemplate('anthropic')}>自定义 Anthropic-compatible</button></div><div className="model-select-row"><div className="model-select-item"><label>Name</label><input value={customProvider.name} onChange={(e) => setCustomProvider({...customProvider, name: e.target.value})} /></div><div className="model-select-item"><label>Display Name</label><input value={customProvider.display_name} onChange={(e) => setCustomProvider({...customProvider, display_name: e.target.value})} /></div><div className="model-select-item"><label>API Format</label><select value={customProvider.api_format} onChange={(e) => setCustomProvider({...customProvider, api_format: e.target.value})}><option value="openai">openai</option><option value="anthropic">anthropic</option><option value="native">native</option></select></div></div><div className="model-select-row"><div className="model-select-item"><label>Base URL</label><input value={customProvider.base_url} onChange={(e) => setCustomProvider({...customProvider, base_url: e.target.value})} placeholder="https://.../v1" /></div><div className="model-select-item"><label>API Key Ref</label><input value={customProvider.api_key_ref} onChange={(e) => setCustomProvider({...customProvider, api_key_ref: e.target.value})} placeholder="${CUSTOM_API_KEY}" /></div><div className="model-select-item"><label>初始模型</label><input value={customProvider.model} onChange={(e) => setCustomProvider({...customProvider, model: e.target.value})} placeholder="model-id" /></div></div><button className="primary-button" onClick={addCustomProvider}>加入服务商列表</button>{activeProviderData && <div className="provider-editor"><h3>{activeProviderData.display_name || activeProviderData.name}</h3><div className="model-select-row"><div className="model-select-item"><label>Base URL</label><input value={activeProviderData.base_url || ''} onChange={(e) => updateActiveProvider({base_url: e.target.value})} /></div><div className="model-select-item"><label>API Key Ref</label><input value={activeProviderData.api_key_ref || ''} onChange={(e) => updateActiveProvider({api_key_ref: e.target.value})} /></div><div className="model-select-item"><label>API Format</label><select value={activeProviderData.api_format || 'openai'} onChange={(e) => updateActiveProvider({api_format: e.target.value})}><option value="openai">openai</option><option value="anthropic">anthropic</option><option value="native">native</option></select></div></div><div className="model-select-row"><div className="model-select-item"><label>Auth Type</label><select value={activeProviderData.auth_type || 'bearer'} onChange={(e) => updateActiveProvider({auth_type: e.target.value})}><option value="bearer">bearer</option><option value="api-key-header">api-key-header</option><option value="none">none</option></select></div><div className="model-select-item"><label>API Key Header</label><input value={activeProviderData.api_key_header || 'Authorization'} onChange={(e) => updateActiveProvider({api_key_header: e.target.value})} /></div></div><div className="model-select-item full-width"><label>Default Headers JSON</label><textarea key={activeProviderData.name} defaultValue={JSON.stringify(activeProviderData.default_headers || {}, null, 2)} onBlur={(e) => { try { updateActiveProvider({default_headers: JSON.parse(e.target.value || '{}')}) } catch { alert('Default Headers 需要是合法 JSON') } }} /></div><div className="key-input-row"><input value={newModelName} onChange={(e) => setNewModelName(e.target.value)} placeholder="手动添加模型" /><button onClick={addModelToProvider}>添加模型</button><button onClick={() => testConnection(activeProviderData.name)}>测试/拉取模型</button></div><div className="model-chip-list">{(activeProviderData.models || []).map((model: any) => <span key={model.name}>{model.name}<label><input type="checkbox" checked={!!model.supports_vision} onChange={() => toggleModelVision(model.name)} /> vision</label></span>)}</div></div>}</section></div></details>
        </div>}

        {activeTab === 'service' && <section className="panel settings-card">
          <h2>🖥 本地服务</h2>
          <p className="card-desc">GeoChem 本地服务器状态。</p>
          <div className="service-status">
            <div className="status-row"><span className="status-dot online" /><strong>服务运行中</strong><code>127.0.0.1:8765</code></div>
            <div className="status-row"><span>安全模式</span><span>仅本机访问</span></div>
            <div className="status-row"><span>数据库</span><span>SQLite WAL 模式</span></div>
          </div>
        </section>}

      </div>
    </div>
  </div>
}
