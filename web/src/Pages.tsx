import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  ArrowRight, BarChart3, BookOpen, CheckCircle2, Database, Download, ExternalLink,
  FileInput, FileSpreadsheet, KeyRound, Link2, Plus, Search, Settings as SettingsIcon,
  ShieldAlert, Upload, WandSparkles,
} from 'lucide-react'
import { api, watchTask } from './api'
import { useAppStore } from './store'

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
    <div className="header-layout"><aside className="panel config-list"><div className="panel-heading"><strong>已保存配置</strong><span>{configs.data?.length || 0}</span></div>{configs.data?.map((config) => <button className={active?.config_id === config.config_id ? 'active' : ''} key={config.config_id} onClick={() => setActiveId(config.config_id)}><FileSpreadsheet size={17}/><span><strong>{config.name}</strong><small>{config.field_count} 个字段</small></span></button>)}<button className="dashed-button"><Plus size={16}/> 新建表头配置</button></aside>
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
  const { projectId } = useAppStore()
  const rows = useQuery({ queryKey: ['reviews', projectId], queryFn: () => api.reviews(projectId), enabled: Boolean(projectId) })
  return <div className="page"><PageHeader title="人工审核" subtitle="集中处理低置信度、单位冲突、化学换算和来源不清的候选值。" /><section className="panel"><SimpleTable rows={rows.data || []} columns={[{key:'risk_level',label:'风险'},{key:'article_title',label:'文章'},{key:'original_field',label:'原始字段'},{key:'original_value',label:'原始值'},{key:'ai_suggestion',label:'AI 建议'},{key:'confidence',label:'置信度'},{key:'status',label:'状态'}]} /></section></div>
}

export function RulesPage() {
  const { projectId } = useAppStore(); const rules = useQuery({ queryKey: ['rules', projectId], queryFn: () => api.rules(projectId), enabled: Boolean(projectId) })
  const rows = [...(rules.data?.mapping || []).map((row) => ({...row,type:'字段映射'})), ...(rules.data?.extraction || []).map((row) => ({...row,type:'抽取规则'}))]
  return <div className="page"><PageHeader title="规则记忆" subtitle="查看用户确认后保存的字段别名、单位换算和资源抽取经验。" /><section className="panel"><SimpleTable rows={rows} columns={[{key:'type',label:'类型'},{key:'source_field',label:'来源字段'},{key:'target_field',label:'目标字段'},{key:'target_unit',label:'单位'},{key:'scope',label:'范围'},{key:'review_status',label:'状态'},{key:'confidence',label:'置信度'}]} /></section></div>
}

export function StandardizedPage() {
  const { projectId } = useAppStore(); const data = useQuery({ queryKey: ['standardized', projectId], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  const rows = data.data || []; const fields = useMemo(() => Array.from(new Set(rows.flatMap((row) => Object.keys((row.data || {}) as Record<string,unknown>)))).slice(0, 18), [rows])
  const flattened = rows.map((row) => ({...row, ...(row.data as object)}))
  return <div className="page"><PageHeader title="标准化导出" subtitle="仅展示满足审核和质量要求的记录，导出时保留完整用户表头。" action={<button className="primary-button"><Download size={16}/> 导出审计包</button>} /><section className="panel"><SimpleTable rows={flattened} columns={[{key:'quality_grade',label:'质量'},{key:'article_title',label:'文章'},...fields.map((field) => ({key:field,label:field}))]} /></section></div>
}

export function TracePage() {
  const { projectId } = useAppStore(); const records = useQuery({ queryKey: ['standardized', projectId], queryFn: () => api.standardized(projectId), enabled: Boolean(projectId) })
  return <div className="page"><PageHeader title="溯源查看" subtitle="从标准化记录返回论文、PDF 页、资源框选、字段映射和计算过程。" /><div className="trace-layout"><section className="panel"><SimpleTable rows={records.data || []} columns={[{key:'record_id',label:'记录'},{key:'article_title',label:'文章'},{key:'source_table',label:'来源表'},{key:'source_row',label:'来源行'},{key:'quality_grade',label:'质量'}]} /></section><aside className="panel trace-detail"><h3>来源链路</h3><div className="trace-chain"><span>论文</span><i/><span>PDF / 附件</span><i/><span>资源坐标</span><i/><span>字段映射</span><i/><span>标准值</span></div><p>选择一条记录后，这里将展示原文页码、框选位置、原始值、单位换算和审核决策。</p></aside></div></div>
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
  const textOptions = settings.data?.providers.flatMap((provider: any) => provider.models.map((model: any) => ({ value: `${provider.name}/${model.name}`, label: `${provider.display_name} / ${model.display_name}`}))) || []
  const visionOptions = settings.data?.providers.flatMap((provider: any) => provider.models.filter((model:any)=>model.supports_vision).map((model: any) => ({ value: `${provider.name}/${model.name}`, label: `${provider.display_name} / ${model.display_name}`}))) || []
  const selectedText = textModel || (settings.data ? `${settings.data.default_provider}/${settings.data.default_model}` : '')
  const selectedVision = visionModel || (settings.data?.vision_provider ? `${settings.data.vision_provider}/${settings.data.vision_model}` : '')
  const save = async () => {
    const [default_provider, ...defaultRest] = selectedText.split('/')
    const [vision_provider, ...visionRest] = selectedVision.split('/')
    await api.saveSettings({default_provider,default_model:defaultRest.join('/'),vision_provider:vision_provider||'',vision_model:visionRest.join('/')})
    queryClient.invalidateQueries({queryKey:['settings']})
  }
  const mimo = settings.data?.providers.find((provider:any)=>provider.name.startsWith('xiaomi'))
  return <div className="page"><PageHeader title="设置" subtitle="配置文本模型、视觉模型、本地存储和隐私策略。" /><div className="settings-layout"><nav className="panel settings-nav"><button className="active"><KeyRound size={17}/> 模型与 API</button><button><Database size={17}/> 数据存储</button><button><SettingsIcon size={17}/> 常规设置</button></nav><section className="panel settings-form"><h2>模型任务路由</h2><div className="form-row"><label>默认文本模型<span>字段映射、段落抽取和规则学习</span></label><select value={selectedText} onChange={(event)=>setTextModel(event.target.value)}>{textOptions.map((option:any)=><option key={option.value} value={option.value}>{option.label}</option>)}</select></div><div className="form-row"><label>图像抽取模型<span>必须选择 supports_vision 的模型</span></label><select value={selectedVision} onChange={(event)=>setVisionModel(event.target.value)}><option value="">未配置，图像仅用于选择与追溯</option>{visionOptions.map((option:any)=><option key={option.value} value={option.value}>{option.label}</option>)}</select></div><div className="form-row"><label>API Key 安全<span>密钥只从后端环境变量读取，不会发送到浏览器</span></label><div className="secure-value"><CheckCircle2 size={17}/> {mimo?.env_name || '环境变量'}：{mimo?.key_status || '未检测'}</div></div><h2>本地服务</h2><div className="form-row"><label>监听地址<span>只允许本机访问</span></label><code>127.0.0.1:8765</code></div><button className="primary-button" onClick={save}>保存设置</button></section></div></div>
}
