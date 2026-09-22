import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, BookCheck, Check, CheckCircle2, Database, Download,
  FileUp, History, Plus, RefreshCw, Save, Search, Send, ShieldCheck,
  Upload, X, XCircle,
} from 'lucide-react'
import {
  api, type PublishedRule, type RuleImportPreview, type RuleSubmission,
} from './api'
import { useAuth } from './auth'
import { useAppStore } from './store'

type RuleTab = 'published' | 'mine' | 'pending' | 'knowledge' | 'conflicts'
type DetailMode = 'empty' | 'rule' | 'submission' | 'create' | 'edit' | 'import' | 'knowledge'

type RuleDraft = {
  source_term: string
  target_canonical_field: string
  target_header: string
  source_unit: string
  target_unit: string
  chemical_form: string
  context: string
  conversion_formula: string
  evidence: string
  notes: string
  scope: 'article' | 'project' | 'organization'
  article_id: string
  confidence: number
  supersedes_rule_id: string
}

const EMPTY_DRAFT: RuleDraft = {
  source_term: '', target_canonical_field: '', target_header: '', source_unit: '', target_unit: '',
  chemical_form: '', context: '', conversion_formula: '', evidence: '', notes: '', scope: 'organization',
  article_id: '', confidence: 0.95, supersedes_rule_id: '',
}

const TEMPLATE_COLUMNS = [
  'source_term', 'target_canonical_field', 'source_unit', 'target_unit', 'chemical_form',
  'context', 'conversion_formula', 'evidence', 'notes',
]

function dateText(value?: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

function scopeLabel(value: string) {
  return value === 'organization' ? '组织' : value === 'project' ? '项目' : '文章'
}

function statusLabel(value: string) {
  return ({ draft: '草稿', pending: '待审批', approved: '已批准', rejected: '已驳回', confirmed: '已发布', superseded: '历史版本' } as Record<string, string>)[value] || value
}

function statusClass(value: string) {
  if (value === 'approved' || value === 'confirmed') return 'success'
  if (value === 'pending') return 'warning'
  if (value === 'rejected') return 'danger'
  return 'neutral'
}

function submissionDraft(item: RuleSubmission): RuleDraft {
  return {
    source_term: item.source_term || '', target_canonical_field: item.target_canonical_field || '',
    target_header: item.target_header || '', source_unit: item.source_unit || '', target_unit: item.target_unit || '',
    chemical_form: item.chemical_form || '', context: item.context_text || '',
    conversion_formula: item.conversion_formula || '', evidence: item.evidence || '', notes: item.notes || '',
    scope: item.scope || 'organization', article_id: item.article_id || '', confidence: item.confidence || 0.95,
    supersedes_rule_id: item.supersedes_rule_id || '',
  }
}

function ValidationSummary({ item }: { item: RuleSubmission }) {
  const validation = item.validation || {}
  const errors = validation.errors || []
  const warnings = validation.warnings || []
  return <div className="rule-validation">
    <div className={validation.valid ? 'rule-validation-line success' : 'rule-validation-line danger'}>
      {validation.valid ? <CheckCircle2 size={15}/> : <XCircle size={15}/>}
      <span>{validation.valid ? '格式与安全校验通过' : '校验未通过'}</span>
    </div>
    <div className="rule-validation-grid">
      <span>知识库评分 <b>{Math.round(Number(validation.knowledge_score || 0) * 100)}%</b></span>
      <span>单位兼容 <b>{validation.unit_compatibility || 'unknown'}</b></span>
      <span>自动应用 <b>{validation.auto_apply_allowed ? '允许' : '仅作候选'}</b></span>
      <span>冲突状态 <b>{item.conflict_status || 'clear'}</b></span>
    </div>
    {[...errors, ...warnings].map((message, index) => <div className="rule-validation-message" key={`${message}-${index}`}><AlertTriangle size={13}/>{message}</div>)}
  </div>
}

function RuleEditor({ value, busy, submitLabel, onCancel, onSave }: {
  value: RuleDraft
  busy: boolean
  submitLabel: string
  onCancel: () => void
  onSave: (value: RuleDraft) => Promise<void>
}) {
  const [draft, setDraft] = useState<RuleDraft>(value)
  const [error, setError] = useState('')
  useEffect(() => setDraft(value), [value])
  const change = <K extends keyof RuleDraft,>(key: K, next: RuleDraft[K]) => setDraft((current) => ({ ...current, [key]: next }))
  const save = async () => {
    if (!draft.source_term.trim() || !draft.target_canonical_field.trim()) {
      setError('请填写原始术语和目标规范字段。')
      return
    }
    if (draft.scope === 'article' && !draft.article_id.trim()) {
      setError('文章级规则必须填写文章 ID。')
      return
    }
    setError('')
    await onSave(draft)
  }
  return <div className="rule-detail-scroll">
    <div className="rule-form-grid">
      <label><span>原始字段 / 别名 *</span><input value={draft.source_term} onChange={(event) => change('source_term', event.target.value)} placeholder="例如 Sample No."/></label>
      <label><span>目标规范字段 *</span><input value={draft.target_canonical_field} onChange={(event) => change('target_canonical_field', event.target.value)} placeholder="例如 SampleID"/></label>
      <label><span>当前表头显示名</span><input value={draft.target_header} onChange={(event) => change('target_header', event.target.value)} placeholder="留空时使用规范字段"/></label>
      <label><span>化学形态</span><input value={draft.chemical_form} onChange={(event) => change('chemical_form', event.target.value)} placeholder="element / oxide / isotope"/></label>
      <label><span>源单位</span><input value={draft.source_unit} onChange={(event) => change('source_unit', event.target.value)} placeholder="ppm"/></label>
      <label><span>目标单位</span><input value={draft.target_unit} onChange={(event) => change('target_unit', event.target.value)} placeholder="wt%"/></label>
      <label><span>作用范围</span><select value={draft.scope} onChange={(event) => change('scope', event.target.value as RuleDraft['scope'])}><option value="organization">组织共享</option><option value="project">当前项目</option><option value="article">当前文章</option></select></label>
      <label><span>建议置信度</span><input type="number" min="0" max="1" step="0.01" value={draft.confidence} onChange={(event) => change('confidence', Number(event.target.value))}/></label>
      {draft.scope === 'article' && <label className="wide"><span>文章 ID *</span><input value={draft.article_id} onChange={(event) => change('article_id', event.target.value)} placeholder="ART_..."/></label>}
      <label className="wide"><span>适用上下文</span><textarea value={draft.context} onChange={(event) => change('context', event.target.value)} placeholder="例如：样品表第一列；主量元素表中的氧化物列"/></label>
      <label className="wide"><span>转换公式 / 计算说明</span><textarea value={draft.conversion_formula} onChange={(event) => change('conversion_formula', event.target.value)} placeholder="无转换时留空；涉及换算时写明公式和条件"/></label>
      <label className="wide"><span>证据 / 来源</span><textarea value={draft.evidence} onChange={(event) => change('evidence', event.target.value)} placeholder="来源文章、标准词表、组织约定或人工说明"/></label>
      <label className="wide"><span>备注</span><textarea value={draft.notes} onChange={(event) => change('notes', event.target.value)}/></label>
      <label className="wide"><span>替代规则 ID</span><input value={draft.supersedes_rule_id} onChange={(event) => change('supersedes_rule_id', event.target.value)} placeholder="修订已发布规则时填写"/></label>
    </div>
    {error && <div className="rule-form-error">{error}</div>}
    <div className="rule-detail-actions"><button onClick={onCancel}>取消</button><button className="primary-button" disabled={busy} onClick={() => void save()}><Save size={15}/>{busy ? '保存中...' : submitLabel}</button></div>
  </div>
}

export default function RuleCenterPage({ adminMode = false }: { adminMode?: boolean }) {
  const queryClient = useQueryClient()
  const auth = useAuth()
  const appProjectId = useAppStore((state) => state.projectId)
  const fallbackWorkspace = useQuery({ queryKey: ['rule-center-workspace'], queryFn: api.workspace, enabled: !auth.currentWorkspace?.project_id && !appProjectId })
  const projectId = appProjectId || auth.currentWorkspace?.project_id || fallbackWorkspace.data?.project_id || ''
  const canApprove = auth.roles.includes('admin') || auth.workspaceRole === 'owner'
  const canSubmit = canApprove || ['curator', 'reviewer'].includes(auth.workspaceRole) || auth.roles.some((role) => ['curator', 'reviewer'].includes(role))
  const [tab, setTab] = useState<RuleTab>(adminMode ? 'pending' : 'published')
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState('')
  const [detailMode, setDetailMode] = useState<DetailMode>('empty')
  const [selectedRuleId, setSelectedRuleId] = useState('')
  const [selectedSubmissionId, setSelectedSubmissionId] = useState('')
  const [selectedKnowledge, setSelectedKnowledge] = useState<Record<string, unknown> | null>(null)
  const [formValue, setFormValue] = useState<RuleDraft>({ ...EMPTY_DRAFT })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const [reviewComment, setReviewComment] = useState('')
  const [importPreview, setImportPreview] = useState<RuleImportPreview | null>(null)
  const [knowledgeQuery, setKnowledgeQuery] = useState('SiO2')
  const [stagedKnowledgeImport, setStagedKnowledgeImport] = useState<Record<string, unknown> | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const published = useQuery({
    queryKey: ['rule-center-published', projectId, query, scope],
    queryFn: () => api.rules(projectId, { q: query, scope }),
    enabled: Boolean(projectId),
  })
  const mine = useQuery({
    queryKey: ['rule-center-mine', projectId, query],
    queryFn: () => api.ruleSubmissions(projectId, { mine: true, q: query }),
    enabled: Boolean(projectId),
  })
  const pending = useQuery({
    queryKey: ['rule-center-pending', projectId, query],
    queryFn: () => api.adminRuleSubmissions(projectId, { status: 'pending', q: query }),
    enabled: Boolean(projectId && canApprove),
  })
  const allSubmissions = useQuery({
    queryKey: ['rule-center-conflicts', projectId, query],
    queryFn: () => api.ruleSubmissions(projectId, { q: query }),
    enabled: Boolean(projectId && tab === 'conflicts'),
  })
  const currentRelease = useQuery({ queryKey: ['mapping-release', projectId], queryFn: () => api.currentMappingKnowledgeRelease(projectId), enabled: Boolean(projectId) })
  const knowledge = useQuery({
    queryKey: ['mapping-knowledge-search', projectId, knowledgeQuery],
    queryFn: () => api.mappingKnowledgeSearch(projectId, knowledgeQuery, 50),
    enabled: Boolean(projectId && tab === 'knowledge' && knowledgeQuery.trim()),
  })
  const ruleDetail = useQuery({ queryKey: ['rule-detail', projectId, selectedRuleId], queryFn: () => api.ruleDetail(projectId, selectedRuleId), enabled: Boolean(projectId && selectedRuleId && detailMode === 'rule') })
  const selectedSubmission = useMemo(() => {
    const items = [...(mine.data?.items || []), ...(pending.data?.items || []), ...(allSubmissions.data?.items || [])]
    return items.find((item) => item.submission_id === selectedSubmissionId)
  }, [allSubmissions.data?.items, mine.data?.items, pending.data?.items, selectedSubmissionId])

  const invalidate = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['rule-center-published'] }),
      queryClient.invalidateQueries({ queryKey: ['rule-center-mine'] }),
      queryClient.invalidateQueries({ queryKey: ['rule-center-pending'] }),
      queryClient.invalidateQueries({ queryKey: ['rule-center-conflicts'] }),
      queryClient.invalidateQueries({ queryKey: ['rule-memory'] }),
    ])
  }
  const run = async (operation: () => Promise<void>) => {
    setBusy(true); setError(''); setMessage('')
    try { await operation() } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }
  const selectRule = (rule: PublishedRule) => { setSelectedRuleId(rule.rule_id); setSelectedSubmissionId(''); setDetailMode('rule'); setMessage(''); setError('') }
  const selectSubmission = (item: RuleSubmission) => { setSelectedSubmissionId(item.submission_id); setSelectedRuleId(''); setDetailMode('submission'); setReviewComment(''); setMessage(''); setError('') }
  const createRule = () => { setFormValue({ ...EMPTY_DRAFT }); setDetailMode('create'); setSelectedRuleId(''); setSelectedSubmissionId(''); setMessage(''); setError('') }

  const saveNew = async (draft: RuleDraft) => run(async () => {
    const created = await api.createRuleSubmission(projectId, draft)
    setSelectedSubmissionId(created.submission_id); setDetailMode('submission'); setTab('mine'); setMessage('规则草稿已保存，提交审批前不会参与抽取。')
    await invalidate()
  })
  const saveExisting = async (draft: RuleDraft) => run(async () => {
    if (!selectedSubmission) return
    await api.updateRuleSubmission(projectId, selectedSubmission.submission_id, draft)
    setMessage('规则草稿已更新。'); setDetailMode('submission'); await invalidate()
  })
  const submit = (item: RuleSubmission) => run(async () => {
    await api.submitRuleSubmission(projectId, item.submission_id); setMessage('规则已提交组织审批；审批前不会参与抽取。'); await invalidate()
  })
  const approve = (item: RuleSubmission) => run(async () => {
    await api.approveRuleSubmission(projectId, item.submission_id, reviewComment); setMessage('规则已发布并进入组织规则库。'); setReviewComment(''); await invalidate()
  })
  const reject = (item: RuleSubmission) => run(async () => {
    if (!reviewComment.trim()) { setError('驳回时必须填写原因。'); return }
    await api.rejectRuleSubmission(projectId, item.submission_id, reviewComment); setMessage('规则已驳回。'); setReviewComment(''); await invalidate()
  })
  const uploadFile = (file?: File) => {
    if (!file) return
    void run(async () => { const preview = await api.importRules(projectId, file); setImportPreview(preview); setDetailMode('import'); setMessage('文件已解析为规则草稿，请检查后再提交审批。'); await invalidate() })
  }
  const submitImported = () => run(async () => {
    const ids = importPreview?.submission_ids || []
    let submitted = 0
    for (const id of ids) { await api.submitRuleSubmission(projectId, id); submitted += 1 }
    setMessage(`已提交 ${submitted} 条有效规则，等待组织审批。`); setTab('mine'); setDetailMode('empty'); await invalidate()
  })
  const downloadTemplate = () => {
    const csv = `${TEMPLATE_COLUMNS.join(',')}\nSample No.,SampleID,,,,sample metadata,,,\n`
    const url = URL.createObjectURL(new Blob([`\ufeff${csv}`], { type: 'text/csv;charset=utf-8' }))
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'geochem_mapping_rules_template.csv'; anchor.click(); URL.revokeObjectURL(url)
  }
  const syncKnowledge = () => run(async () => {
    const result = await api.syncMappingKnowledge(projectId); setStagedKnowledgeImport(result); setMessage(String(result.status) === 'no_changes' ? '内置知识库已是最新版本。' : '已生成知识库差异，请确认后发布。')
  })
  const publishKnowledge = () => run(async () => {
    const importId = String(stagedKnowledgeImport?.import_id || '')
    if (!importId) return
    await api.publishMappingKnowledgeImport(projectId, importId); setStagedKnowledgeImport(null); setMessage('知识库快照已发布。')
    await Promise.all([queryClient.invalidateQueries({ queryKey: ['mapping-release'] }), queryClient.invalidateQueries({ queryKey: ['mapping-knowledge-search'] })])
  })

  const tabs = [
    ['published', '已发布规则', published.data?.count || 0],
    ['mine', '我的提交', mine.data?.count || 0],
    ...(canApprove ? [['pending', '待审批', pending.data?.count || 0] as const] : []),
    ['knowledge', '外部知识库', Number((currentRelease.data as Record<string, unknown> | undefined)?.concept_count || 0)],
    ['conflicts', '冲突与历史', (allSubmissions.data?.items || []).filter((item) => item.conflict_status !== 'clear' || item.status === 'rejected').length],
  ] as Array<[RuleTab, string, number]>
  const conflictItems = (allSubmissions.data?.items || []).filter((item) => item.conflict_status !== 'clear' || item.status === 'rejected')
  const knowledgeItems = (((knowledge.data || {}) as Record<string, unknown>).items || []) as Record<string, unknown>[]

  return <div className={`page rule-center-page ${adminMode ? 'admin-rule-center' : ''}`}>
    <div className="rule-command-bar">
      <div className="rule-page-identity"><BookCheck size={19}/><strong>{adminMode ? '规则审批' : '规则中心'}</strong><span>{adminMode ? '组织规则发布与冲突治理' : '组织共享映射规则与标准概念'}</span></div>
      <div className="rule-command-actions">
        {tab !== 'knowledge' && <label className="rule-search"><Search size={15}/><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索原始术语、目标字段或单位"/></label>}
        {tab === 'published' && <select value={scope} onChange={(event) => setScope(event.target.value)}><option value="">全部范围</option><option value="article">文章</option><option value="project">项目</option><option value="organization">组织</option></select>}
        {canSubmit && !adminMode && <><button onClick={() => fileRef.current?.click()}><Upload size={15}/>批量上传</button><input ref={fileRef} hidden type="file" accept=".csv,.xlsx,.xls" onChange={(event) => uploadFile(event.target.files?.[0])}/><button className="primary-button" onClick={createRule}><Plus size={15}/>新增规则</button></>}
      </div>
    </div>
    <div className="rule-tabs" role="tablist">
      {tabs.map(([key, label, count]) => <button key={key} className={tab === key ? 'active' : ''} onClick={() => { setTab(key); setDetailMode('empty'); setError(''); setMessage('') }}>{label}<span>{count}</span></button>)}
    </div>
    {(message || error) && <div className={`rule-banner ${error ? 'error' : ''}`}>{error || message}<button className="icon-button" onClick={() => { setMessage(''); setError('') }}><X size={14}/></button></div>}
    <div className="rule-center-layout">
      <section className="rule-table-panel">
        {tab === 'published' && <><div className="rule-table-head"><span>原始术语</span><span>目标字段</span><span>单位 / 形态</span><span>范围</span><span>使用</span><span>发布</span></div><div className="rule-table-body">
          {published.isLoading && <div className="rule-empty">正在读取已发布规则...</div>}
          {!published.isLoading && !published.data?.items.length && <div className="rule-empty">当前筛选下没有已发布规则。</div>}
          {published.data?.items.map((item) => <button className={selectedRuleId === item.rule_id ? 'rule-table-row active' : 'rule-table-row'} key={item.rule_id} onClick={() => selectRule(item)}><strong>{item.pattern}</strong><span>{item.target_header || item.target_field}</span><span>{String(item.conditions?.source_unit || '—')} → {item.target_unit || String(item.conditions?.target_unit || '—')}<small>{String(item.conditions?.chemical_form || '未标记形态')}</small></span><span className="rule-scope">{scopeLabel(item.scope)}</span><span>{item.usage_count} 次<small>{item.related_article_count} 篇文章</small></span><span>{dateText(item.published_at || item.created_at)}<small>v{item.revision}</small></span></button>)}
        </div></>}
        {(tab === 'mine' || tab === 'pending' || tab === 'conflicts') && <><div className="rule-submission-head"><span>原始术语</span><span>目标字段</span><span>状态</span><span>校验</span><span>提交人</span><span>更新时间</span></div><div className="rule-table-body">
          {(tab === 'mine' ? mine.isLoading : tab === 'pending' ? pending.isLoading : allSubmissions.isLoading) && <div className="rule-empty">正在读取规则提交...</div>}
          {(tab === 'mine' ? mine.data?.items || [] : tab === 'pending' ? pending.data?.items || [] : conflictItems).map((item) => <button className={selectedSubmissionId === item.submission_id ? 'rule-submission-row active' : 'rule-submission-row'} key={item.submission_id} onClick={() => selectSubmission(item)}><strong>{item.source_term}</strong><span>{item.target_canonical_field}</span><span><b className={`rule-status ${statusClass(item.status)}`}>{statusLabel(item.status)}</b><small>{scopeLabel(item.scope)}</small></span><span>{item.validation?.valid ? '通过' : '需修正'}<small>{item.conflict_status}</small></span><span>{item.submitter_name || item.created_by}</span><span>{dateText(item.updated_at)}</span></button>)}
          {!(tab === 'mine' ? mine.data?.items.length : tab === 'pending' ? pending.data?.items.length : conflictItems.length) && <div className="rule-empty">当前没有{tab === 'pending' ? '待审批' : tab === 'conflicts' ? '冲突或驳回' : '个人提交'}规则。</div>}
        </div></>}
        {tab === 'knowledge' && <div className="knowledge-browser">
          <div className="knowledge-release-bar"><div><Database size={17}/><span><strong>{String((currentRelease.data as Record<string, unknown> | undefined)?.name || '地化映射知识库')}</strong><small>发布版本 {String((currentRelease.data as Record<string, unknown> | undefined)?.version || '—')} · 离线运行</small></span></div>{auth.roles.includes('admin') && <button disabled={busy} onClick={() => void syncKnowledge()}><RefreshCw size={15}/>检查内置快照</button>}</div>
          <label className="knowledge-search"><Search size={16}/><input value={knowledgeQuery} onChange={(event) => setKnowledgeQuery(event.target.value)} placeholder="搜索 SiO2、TOC、87Sr/86Sr、Sample No."/></label>
          {stagedKnowledgeImport && <div className="knowledge-stage"><span><strong>待发布快照</strong><small>{String((stagedKnowledgeImport.diff as Record<string, unknown> | undefined)?.incoming_version || stagedKnowledgeImport.source_version || '')}</small></span>{String(stagedKnowledgeImport.status) === 'staged' && <button className="primary-button" onClick={() => void publishKnowledge()}><Check size={15}/>审核并发布</button>}</div>}
          <div className="knowledge-list">{knowledgeItems.map((item) => <button key={String(item.concept_version_id || item.concept_id)} className={selectedKnowledge?.concept_version_id === item.concept_version_id ? 'active' : ''} onClick={() => { setSelectedKnowledge(item); setDetailMode('knowledge') }}><span><strong>{String(item.canonical_name || item.display_term)}</strong><small>{String(item.concept_type || 'concept')} · {String(item.chemical_form || '未标记形态')}</small></span><b>{Math.round(Number(item.match_score || 0) * 100)}%</b></button>)}{knowledge.isLoading && <div className="rule-empty">正在检索知识库...</div>}{!knowledge.isLoading && !knowledgeItems.length && <div className="rule-empty">没有匹配的标准概念。</div>}</div>
        </div>}
      </section>
      <aside className="rule-detail-panel">
        <div className="rule-detail-heading"><strong>{detailMode === 'create' ? '新增规则草稿' : detailMode === 'edit' ? '编辑规则草稿' : detailMode === 'import' ? '批量导入预览' : detailMode === 'knowledge' ? '标准概念' : detailMode === 'submission' ? '规则提交详情' : detailMode === 'rule' ? '已发布规则' : '规则详情'}</strong>{detailMode !== 'empty' && <button className="icon-button" onClick={() => setDetailMode('empty')}><X size={15}/></button>}</div>
        {detailMode === 'empty' && <div className="rule-detail-empty"><BookCheck size={34}/><strong>选择一条规则查看完整信息</strong><span>已发布规则不可直接覆盖；修改需创建新版本并重新审批。</span>{canSubmit && !adminMode && <button className="primary-button" onClick={createRule}><Plus size={15}/>新增规则</button>}</div>}
        {detailMode === 'create' && <RuleEditor value={formValue} busy={busy} submitLabel="保存草稿" onCancel={() => setDetailMode('empty')} onSave={saveNew}/>} 
        {detailMode === 'edit' && <RuleEditor value={formValue} busy={busy} submitLabel="保存修改" onCancel={() => setDetailMode('submission')} onSave={saveExisting}/>} 
        {detailMode === 'rule' && ruleDetail.data && <PublishedRuleDetail item={ruleDetail.data}/>} 
        {detailMode === 'submission' && selectedSubmission && <div className="rule-detail-scroll">
          <div className="rule-detail-definition"><span>映射</span><strong>{selectedSubmission.source_term} → {selectedSubmission.target_canonical_field}</strong></div>
          <dl className="rule-definition-list"><div><dt>范围</dt><dd>{scopeLabel(selectedSubmission.scope)}</dd></div><div><dt>单位</dt><dd>{selectedSubmission.source_unit || '—'} → {selectedSubmission.target_unit || '—'}</dd></div><div><dt>化学形态</dt><dd>{selectedSubmission.chemical_form || '—'}</dd></div><div><dt>上下文</dt><dd>{selectedSubmission.context_text || '—'}</dd></div><div><dt>转换说明</dt><dd>{selectedSubmission.conversion_formula || '—'}</dd></div><div><dt>证据</dt><dd>{selectedSubmission.evidence || '—'}</dd></div><div><dt>提交人</dt><dd>{selectedSubmission.submitter_name || selectedSubmission.created_by}</dd></div><div><dt>状态</dt><dd><b className={`rule-status ${statusClass(selectedSubmission.status)}`}>{statusLabel(selectedSubmission.status)}</b></dd></div></dl>
          <ValidationSummary item={selectedSubmission}/>
          {selectedSubmission.reviews?.map((review, index) => <div className="rule-review-note" key={index}><ShieldCheck size={14}/><span><strong>{String(review.decision || '')}</strong><small>{String(review.comment || '无审批意见')} · {dateText(String(review.reviewed_at || ''))}</small></span></div>)}
          {selectedSubmission.status === 'draft' && <div className="rule-detail-actions"><button onClick={() => { setFormValue(submissionDraft(selectedSubmission)); setDetailMode('edit') }}>编辑草稿</button><button className="primary-button" disabled={busy || !selectedSubmission.validation?.valid} onClick={() => void submit(selectedSubmission)}><Send size={15}/>提交审批</button></div>}
          {selectedSubmission.status === 'pending' && canApprove && <div className="rule-review-box"><label><span>审批意见</span><textarea value={reviewComment} onChange={(event) => setReviewComment(event.target.value)} placeholder="批准可选；驳回时必填"/></label><div><button className="danger-button" disabled={busy} onClick={() => void reject(selectedSubmission)}><XCircle size={15}/>驳回</button><button className="primary-button" disabled={busy} onClick={() => void approve(selectedSubmission)}><CheckCircle2 size={15}/>批准并发布</button></div></div>}
        </div>}
        {detailMode === 'import' && importPreview && <div className="rule-detail-scroll"><div className="rule-import-summary"><FileUp size={22}/><span><strong>{importPreview.filename}</strong><small>{importPreview.total_rows} 行 · {importPreview.valid_rows} 有效 · {importPreview.invalid_rows} 需修正</small></span></div><div className="rule-import-list">{importPreview.preview.slice(0, 100).map((row, index) => <div key={index} className={String(row.status) === 'valid' ? 'valid' : 'invalid'}><span><strong>{String(row.source_term || '空字段')} → {String(row.target_canonical_field || '空字段')}</strong><small>第 {String(row.row || index + 2)} 行 · {String(row.status || '')}</small></span>{String(row.status) === 'valid' ? <Check size={14}/> : <AlertTriangle size={14}/>}</div>)}</div><div className="rule-detail-actions"><button onClick={downloadTemplate}><Download size={15}/>下载模板</button><button className="primary-button" disabled={busy || !importPreview.submission_ids?.length} onClick={() => void submitImported()}><Send size={15}/>提交 {importPreview.submission_ids?.length || 0} 条审批</button></div></div>}
        {detailMode === 'knowledge' && selectedKnowledge && <KnowledgeDetail item={selectedKnowledge} release={currentRelease.data as Record<string, unknown> | undefined}/>} 
      </aside>
    </div>
  </div>
}

function PublishedRuleDetail({ item }: { item: PublishedRule & {history?: PublishedRule[]} }) {
  const history = item.history || []
  return <div className="rule-detail-scroll"><div className="rule-detail-definition"><span>已发布映射</span><strong>{item.pattern} → {item.target_header || item.target_field}</strong></div><dl className="rule-definition-list"><div><dt>作用范围</dt><dd>{scopeLabel(item.scope)}</dd></div><div><dt>版本</dt><dd>v{item.revision}</dd></div><div><dt>单位</dt><dd>{String(item.conditions?.source_unit || '—')} → {item.target_unit || String(item.conditions?.target_unit || '—')}</dd></div><div><dt>化学形态</dt><dd>{String(item.conditions?.chemical_form || '—')}</dd></div><div><dt>转换说明</dt><dd>{String(item.conditions?.conversion_formula || '—')}</dd></div><div><dt>发布人</dt><dd>{item.published_by || '—'}</dd></div><div><dt>发布时间</dt><dd>{dateText(item.published_at)}</dd></div><div><dt>自动应用</dt><dd>{item.conditions?.auto_apply_allowed ? '满足安全条件时允许' : '仅作为候选，需人工确认'}</dd></div><div><dt>使用情况</dt><dd>{item.usage_count} 次 · {item.related_article_count} 篇文章</dd></div></dl>{item.evidence && <div className="rule-evidence"><strong>来源与证据</strong><p>{item.evidence}</p></div>}<div className="rule-history"><div><History size={15}/><strong>版本历史</strong></div>{history.map((entry) => <div key={entry.rule_id}><span>v{entry.revision} · {statusLabel(entry.review_status)}</span><small>{dateText(entry.published_at || entry.created_at)}</small></div>)}</div></div>
}

function KnowledgeDetail({ item, release }: { item: Record<string, unknown>; release?: Record<string, unknown> }) {
  const sources = (item.source_references || release?.sources || []) as Array<Record<string, unknown> | string>
  const units = (item.allowed_units || []) as string[]
  const contexts = (item.contexts || []) as string[]
  return <div className="rule-detail-scroll"><div className="rule-detail-definition"><span>规范概念</span><strong>{String(item.canonical_name || item.display_term || '')}</strong></div><dl className="rule-definition-list"><div><dt>概念 ID</dt><dd>{String(item.concept_id || '—')}</dd></div><div><dt>类型</dt><dd>{String(item.concept_type || '—')}</dd></div><div><dt>化学形态</dt><dd>{String(item.chemical_form || '—')}</dd></div><div><dt>单位维度</dt><dd>{String(item.unit_dimension || '—')}</dd></div><div><dt>允许单位</dt><dd>{units.join(', ') || '—'}</dd></div><div><dt>上下文</dt><dd>{contexts.join(', ') || '—'}</dd></div><div><dt>匹配术语</dt><dd>{String(item.display_term || '—')}</dd></div><div><dt>发布版本</dt><dd>{String(release?.version || item.release_id || '—')}</dd></div></dl><div className="rule-evidence"><strong>来源</strong>{sources.length ? sources.map((source, index) => <p key={index}>{typeof source === 'string' ? source : String(source.name || source.url || JSON.stringify(source))}</p>) : <p>随本地发布快照提供。</p>}</div></div>
}
