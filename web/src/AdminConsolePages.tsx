import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity, CheckCircle2, Database, HardDrive, KeyRound, Plus, RefreshCw,
  Save, ServerCog, ShieldCheck, Trash2, UserRound, Users, XCircle,
} from 'lucide-react'
import { api, type AdminModelCredential } from './api'

const GIB = 1024 ** 3

function AdminPageTitle({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="page-title-row admin-title-row"><div><h1>{title}</h1><p>{description}</p></div>{action}</div>
}

function formatBytes(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0 B'
  if (value >= GIB) return `${(value / GIB).toFixed(value >= 10 * GIB ? 0 : 1)} GiB`
  if (value >= 1024 ** 2) return `${(value / 1024 ** 2).toFixed(1)} MiB`
  return `${Math.round(value / 1024)} KiB`
}

function formatDate(value: unknown) {
  const text = String(value || '')
  if (!text) return '—'
  const date = new Date(text)
  return Number.isNaN(date.getTime()) ? text : date.toLocaleString('zh-CN', { hour12: false })
}

function recordText(record: Record<string, unknown>, key: string, fallback = '—') {
  const value = record[key]
  return value === undefined || value === null || value === '' ? fallback : String(value)
}

export function AdminOverviewPage() {
  const status = useQuery({ queryKey: ['admin-status'], queryFn: api.adminStatus })
  const overview = useQuery({ queryKey: ['admin-overview'], queryFn: api.adminOverview })
  const users = useQuery({ queryKey: ['admin-users-overview'], queryFn: () => api.adminUsers('', 0, 100) })
  const metrics = useMemo(() => {
    const rows = users.data?.users || []
    return {
      users: overview.data?.users_total ?? users.data?.total ?? 0,
      enabled: overview.data?.users_enabled ?? rows.filter((item) => item.enabled).length,
      admins: rows.filter((item) => item.roles.includes('admin')).length,
    }
  }, [overview.data, users.data])
  const storagePercent = Math.min(100, Math.round(100 * (overview.data?.storage_used_bytes || 0) / Math.max(1, overview.data?.storage_quota_bytes || 0)))
  return <div className="page admin-overview-page">
    <AdminPageTitle title="管理概览" description="查看账号、模型资源、存储配额与系统运行状态。"/>
    <div className="admin-overview-metrics">
      <div><span className="metric-icon blue"><Users size={20}/></span><span>用户总数</span><strong>{metrics.users}</strong><small>{metrics.enabled} 个账号启用 · {metrics.admins} 位管理员</small></div>
      <div><span className="metric-icon violet"><KeyRound size={20}/></span><span>共享模型</span><strong>{overview.data?.active_credentials ?? 0}</strong><small>{overview.data?.active_allocations ?? 0} 项用户授权</small></div>
      <div><span className="metric-icon orange"><HardDrive size={20}/></span><span>存储使用</span><strong>{storagePercent}%</strong><small>{formatBytes(overview.data?.storage_used_bytes || 0)} / {formatBytes(overview.data?.storage_quota_bytes || 0)}</small></div>
      <div><span className="metric-icon green"><Activity size={20}/></span><span>后台任务</span><strong>{overview.data?.running_tasks ?? 0}</strong><small>{overview.data?.failed_tasks ?? 0} 个失败任务待检查</small></div>
    </div>
    <div className="admin-overview-grid">
      <section className="panel admin-overview-panel">
        <div className="panel-heading"><strong>系统运行</strong><span>{status.data?.profile || 'loading'}</span></div>
        <div className="admin-health-list">
          <div><Database size={17}/><span><strong>业务数据库</strong><small>科研数据与治理记录</small></span><b>{status.data?.database || '读取中'}</b></div>
          <div><Activity size={17}/><span><strong>后台任务</strong><small>解析、抽取与导出任务</small></span><b>{status.data?.task_backend || '读取中'}</b></div>
          <div><ShieldCheck size={17}/><span><strong>身份服务</strong><small>登录、角色和会话</small></span><b>{status.data?.auth_mode === 'oidc' ? 'Keycloak' : '本地预览'}</b></div>
          <div><KeyRound size={17}/><span><strong>凭据保险库</strong><small>AES-256-GCM，不向浏览器返回原始 Key</small></span><b>{overview.data?.credential_vault_ready ? '已就绪' : '待配置'}</b></div>
          <div><ServerCog size={17}/><span><strong>运行版本</strong><small>当前部署构建</small></span><b>{status.data?.build_id || 'unversioned'}</b></div>
        </div>
      </section>
      <section className="panel admin-overview-panel">
        <div className="panel-heading"><strong>治理状态</strong><span>生产策略</span></div>
        <div className="admin-notice-list">
          <div><span className={overview.data?.credential_vault_ready ? 'notice-dot' : 'notice-dot warning'}/><span><strong>{overview.data?.credential_vault_ready ? '共享密钥保险库可用' : '需要配置凭据主密钥'}</strong><small>API Key 仅在服务端解密，用户只获得模型使用授权。</small></span></div>
          <div><span className="notice-dot"/><span><strong>存储按用户配额治理</strong><small>默认 10 GiB，可在用户或存储页面单独调整。</small></span></div>
          <div><span className="notice-dot"/><span><strong>关键操作进入审计日志</strong><small>账号、凭据、配额和正式导出均记录操作者与结果。</small></span></div>
        </div>
      </section>
    </div>
  </div>
}

export function AdminRolesPage() {
  const roles = [
    ['管理员', 'admin', '管理账号、凭据、配额、系统设置及全部工作区数据'],
    ['数据整理员', 'curator', '导入、资源发现、字段映射和候选数据编辑'],
    ['审核员', 'reviewer', '人工审核、正式标准化和导出'],
    ['查看者', 'viewer', '只读查看数据、证据与溯源'],
  ]
  return <div className="page admin-simple-page"><AdminPageTitle title="角色与权限" description="GeoChem 使用固定业务角色，减少误配置和越权风险。"/>
    <section className="panel admin-role-matrix"><div className="panel-heading"><strong>固定角色</strong><span>4</span></div>{roles.map(([label, code, description]) => <div className="admin-role-row" key={code}><span className="admin-role-badge"><ShieldCheck size={17}/></span><span><strong>{label}</strong><small>{description}</small></span><code>{code}</code></div>)}</section>
  </div>
}

type CredentialForm = {
  name: string
  provider: string
  api_format: string
  base_url: string
  model_id: string
  api_key: string
  secret_ref: string
  enabled: boolean
}

const EMPTY_CREDENTIAL: CredentialForm = {
  name: '', provider: 'opencode-go', api_format: 'openai', base_url: '', model_id: '',
  api_key: '', secret_ref: '', enabled: true,
}

function credentialForm(value?: AdminModelCredential): CredentialForm {
  if (!value) return { ...EMPTY_CREDENTIAL }
  return {
    name: value.name, provider: value.provider, api_format: value.api_format,
    base_url: value.base_url, model_id: value.model_id, api_key: '',
    secret_ref: value.secret_ref, enabled: value.enabled,
  }
}

export function AdminModelsPage() {
  const queryClient = useQueryClient()
  const credentials = useQuery({ queryKey: ['admin-model-credentials'], queryFn: api.adminModelCredentials })
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<CredentialForm>({ ...EMPTY_CREDENTIAL })
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const selected = credentials.data?.credentials.find((item) => item.credential_id === selectedId)

  useEffect(() => {
    if (!creating && !selectedId && credentials.data?.credentials.length) setSelectedId(credentials.data.credentials[0].credential_id)
  }, [creating, credentials.data?.credentials, selectedId])
  useEffect(() => { if (!creating && selected) setForm(credentialForm(selected)) }, [creating, selected])

  const save = async () => {
    setError(''); setMessage('')
    if (!form.name.trim() || !form.model_id.trim()) { setError('请填写名称和模型 ID。'); return }
    if (creating && !form.api_key.trim() && !form.secret_ref.trim()) { setError('请填写 API Key 或环境变量引用。'); return }
    setBusy(true)
    try {
      const payload = { ...form, api_key: form.api_key.trim(), secret_ref: form.secret_ref.trim() }
      const result = creating
        ? await api.createAdminModelCredential(payload)
        : await api.updateAdminModelCredential(selectedId, payload)
      setCreating(false); setSelectedId(result.credential_id); setMessage('共享模型凭据已安全保存。'); setForm(credentialForm(result))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['admin-model-credentials'] }),
        queryClient.invalidateQueries({ queryKey: ['admin-overview'] }),
      ])
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }

  const remove = async () => {
    if (!selected || !window.confirm(`删除共享模型“${selected.name}”？关联用户授权也会被移除。`)) return
    setBusy(true); setError(''); setMessage('')
    try {
      await api.deleteAdminModelCredential(selected.credential_id)
      setSelectedId(''); setMessage('共享模型凭据已删除。')
      await queryClient.invalidateQueries({ queryKey: ['admin-model-credentials'] })
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) } finally { setBusy(false) }
  }

  return <div className="page admin-resource-page">
    <AdminPageTitle title="模型与密钥" description="维护服务器共享模型凭据；用户只获得使用权，永远看不到原始 API Key。" action={<button className="primary-button" onClick={() => { setCreating(true); setSelectedId(''); setForm({ ...EMPTY_CREDENTIAL }); setMessage(''); setError('') }}><Plus size={16}/> 新增共享模型</button>}/>
    {!credentials.data?.vault_ready && <div className="admin-preview-banner warning"><KeyRound size={18}/><div><strong>凭据保险库尚未就绪</strong><span>Mac 预览可使用环境变量引用；Linux 部署需要配置 GEOCHEM_CREDENTIAL_MASTER_KEY 才能加密保存明文 API Key。</span></div></div>}
    <div className="admin-resource-layout">
      <section className="panel admin-resource-list">
        <div className="panel-heading"><strong>共享模型</strong><span>{credentials.data?.credentials.length || 0}</span></div>
        <div className="admin-resource-rows">
          {credentials.isLoading && <div className="admin-list-empty">正在读取模型凭据...</div>}
          {!credentials.isLoading && !credentials.data?.credentials.length && <div className="admin-list-empty">尚未配置共享模型</div>}
          {credentials.data?.credentials.map((item) => <button key={item.credential_id} className={selectedId === item.credential_id && !creating ? 'admin-resource-row active' : 'admin-resource-row'} onClick={() => { setCreating(false); setSelectedId(item.credential_id); setMessage(''); setError('') }}>
            <span className="admin-resource-icon"><KeyRound size={17}/></span>
            <span><strong>{item.name}</strong><small>{item.provider} · {item.model_id}</small></span>
            <b className={item.enabled ? 'ready' : ''}>{item.enabled ? '启用' : '停用'}</b>
          </button>)}
        </div>
      </section>
      <section className="panel admin-resource-editor">
        <div className="panel-heading"><strong>{creating ? '新增共享模型' : selected ? `编辑 ${selected.name}` : '模型详情'}</strong>{selected && <span>Key …{selected.key_fingerprint.slice(-6) || '受保护'}</span>}</div>
        {!creating && !selected ? <div className="admin-editor-empty"><KeyRound size={34}/><span>选择共享模型查看配置。</span></div> : <div className="admin-editor-body">
          <div className="admin-form-grid three">
            <label><span>显示名称</span><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="工作室默认模型"/></label>
            <label><span>服务商</span><input value={form.provider} onChange={(event) => setForm({ ...form, provider: event.target.value })} placeholder="opencode-go"/></label>
            <label><span>协议</span><select value={form.api_format} onChange={(event) => setForm({ ...form, api_format: event.target.value })}><option value="openai">OpenAI-compatible</option><option value="anthropic">Anthropic-compatible</option></select></label>
            <label><span>Base URL</span><input value={form.base_url} onChange={(event) => setForm({ ...form, base_url: event.target.value })} placeholder="https://.../v1"/></label>
            <label><span>模型 ID</span><input value={form.model_id} onChange={(event) => setForm({ ...form, model_id: event.target.value })} placeholder="deepseek-v4-flash"/></label>
            <label className="toggle-field"><span>状态</span><span className="toggle-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })}/> 允许分配给用户</span></label>
          </div>
          <div className="admin-secret-section">
            <div><strong>服务端密钥</strong><small>填写新 Key 会替换旧值；保存后输入框会立即清空，浏览器无法再次读取。</small></div>
            <div className="admin-secret-grid">
              <label><span>API Key</span><input type="password" value={form.api_key} onChange={(event) => setForm({ ...form, api_key: event.target.value })} placeholder={selected?.secret_configured ? '已配置；留空保持不变' : '仅本次提交可见'} autoComplete="new-password"/></label>
              <span className="or-divider">或</span>
              <label><span>服务器环境变量引用</span><input value={form.secret_ref} onChange={(event) => setForm({ ...form, secret_ref: event.target.value })} placeholder="${OPENCODE_GO_API_KEY}"/></label>
            </div>
          </div>
          {(message || error) && <div className={error ? 'admin-form-message error' : 'admin-form-message'}>{error || message}</div>}
          <div className="admin-editor-actions"><button className="primary-button" onClick={() => void save()} disabled={busy}><Save size={16}/> {busy ? '保存中...' : '保存配置'}</button>{!creating && selected && <button className="danger-button" onClick={() => void remove()} disabled={busy}><Trash2 size={16}/> 删除</button>}</div>
        </div>}
      </section>
    </div>
  </div>
}

export function AdminStoragePage() {
  const queryClient = useQueryClient()
  const users = useQuery({ queryKey: ['admin-users-storage'], queryFn: () => api.adminUsers('', 0, 200) })
  const [selectedId, setSelectedId] = useState('')
  const [quotaGiB, setQuotaGiB] = useState('10')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const policy = useQuery({ queryKey: ['admin-user-policy', selectedId], queryFn: () => api.adminUserPolicy(selectedId), enabled: Boolean(selectedId) })
  const selected = users.data?.users.find((user) => user.id === selectedId)
  useEffect(() => { if (!selectedId && users.data?.users.length) setSelectedId(users.data.users[0].id) }, [selectedId, users.data?.users])
  useEffect(() => { if (policy.data) setQuotaGiB(String(Math.round((policy.data.storage.quota_bytes / GIB) * 10) / 10)) }, [policy.data])
  const used = policy.data?.storage.used_bytes || 0
  const quota = policy.data?.storage.quota_bytes || 0
  const percent = Math.min(100, Math.round(used * 100 / Math.max(1, quota)))
  const save = async () => {
    setNotice(''); setError('')
    const value = Number(quotaGiB)
    if (!Number.isFinite(value) || value < 0) { setError('请输入有效的 GiB 配额。'); return }
    try {
      await api.updateAdminUserStorageQuota(selectedId, Math.round(value * GIB))
      setNotice('存储配额已更新。')
      await Promise.all([queryClient.invalidateQueries({ queryKey: ['admin-user-policy', selectedId] }), queryClient.invalidateQueries({ queryKey: ['admin-overview'] })])
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)) }
  }
  return <div className="page admin-resource-page">
    <AdminPageTitle title="存储配额" description="按用户分配文章原件、派生资产与导出文件的空间。"/>
    <div className="admin-resource-layout">
      <section className="panel admin-resource-list"><div className="panel-heading"><strong>用户</strong><span>{users.data?.total || 0}</span></div><div className="admin-resource-rows">
        {users.data?.users.map((user) => <button key={user.id} className={selectedId === user.id ? 'admin-resource-row active' : 'admin-resource-row'} onClick={() => { setSelectedId(user.id); setNotice(''); setError('') }}><span className="admin-resource-icon"><UserRound size={17}/></span><span><strong>{user.username}</strong><small>{user.email || user.roles.join(' / ')}</small></span></button>)}
      </div></section>
      <section className="panel admin-resource-editor"><div className="panel-heading"><strong>{selected ? `${selected.username} 的存储` : '存储详情'}</strong><span>{percent}%</span></div>{!selected ? <div className="admin-editor-empty"><HardDrive size={34}/><span>选择用户配置存储空间。</span></div> : <div className="admin-editor-body">
        <div className="storage-usage-hero"><span><HardDrive size={22}/></span><div><strong>{formatBytes(used)} 已使用</strong><small>配额 {formatBytes(quota)} · 剩余 {formatBytes(Math.max(0, quota - used))}</small></div><b>{percent}%</b></div>
        <div className="storage-progress"><span style={{ width: `${percent}%` }}/></div>
        <div className="admin-form-section"><div className="admin-section-heading"><strong>用户配额</strong><small>0 表示禁止继续写入，不表示无限制。</small></div><label className="quota-input"><span>容量</span><input type="number" min="0" step="1" value={quotaGiB} onChange={(event) => setQuotaGiB(event.target.value)}/><b>GiB</b><button className="primary-button" onClick={() => void save()}>保存配额</button></label></div>
        {(notice || error) && <div className={error ? 'admin-form-message error' : 'admin-form-message'}>{error || notice}</div>}
        <div className="admin-policy-note"><ShieldCheck size={18}/><span><strong>归属策略</strong><small>文章原始文件及其候选表、图片、标准化和导出资产计入导入者空间；共享数据库本身不重复计费。</small></span></div>
      </div>}</section>
    </div>
  </div>
}

export function AdminTasksPage() {
  const tasks = useQuery({ queryKey: ['admin-tasks'], queryFn: () => api.adminTasks(300), refetchInterval: 5000 })
  return <div className="page admin-table-page"><AdminPageTitle title="任务监控" description="查看解析、抽取、LLM 与导出任务的执行状态。" action={<button className="icon-button" title="刷新" onClick={() => void tasks.refetch()}><RefreshCw size={17} className={tasks.isFetching ? 'spin' : ''}/></button>}/>
    <section className="panel admin-data-table"><div className="admin-table-head"><span>任务</span><span>文章</span><span>状态</span><span>进度</span><span>消息 / 错误</span><span>创建时间</span></div><div className="admin-table-body">
      {!tasks.data?.tasks.length && <div className="admin-table-empty">暂无后台任务</div>}
      {tasks.data?.tasks.map((task, index) => <div className="admin-table-row" key={recordText(task, 'task_id', String(index))}><span><strong>{recordText(task, 'task_type')}</strong><small>{recordText(task, 'task_id')}</small></span><span>{recordText(task, 'article_id')}</span><span className={`task-state ${recordText(task, 'status', '').toLowerCase()}`}>{recordText(task, 'status')}</span><span>{Math.round(Number(task.progress || 0) * 100)}%</span><span title={recordText(task, 'error_message', recordText(task, 'message'))}>{recordText(task, 'error_message', recordText(task, 'message'))}</span><span>{formatDate(task.created_at)}</span></div>)}
    </div></section>
  </div>
}

export function AdminAuditPage() {
  const events = useQuery({ queryKey: ['admin-audit-events'], queryFn: () => api.adminAuditEvents(300) })
  return <div className="page admin-table-page"><AdminPageTitle title="审计日志" description="检索用户管理、权限、配额和关键业务操作；日志不记录 API Key 明文。" action={<button className="icon-button" title="刷新" onClick={() => void events.refetch()}><RefreshCw size={17} className={events.isFetching ? 'spin' : ''}/></button>}/>
    <section className="panel admin-data-table audit"><div className="admin-table-head"><span>时间</span><span>操作者</span><span>角色</span><span>操作</span><span>路径</span><span>结果</span><span>来源 IP</span></div><div className="admin-table-body">
      {!events.data?.events.length && <div className="admin-table-empty">暂无审计事件</div>}
      {events.data?.events.map((event, index) => { const status = Number(event.status_code || 0); return <div className="admin-table-row" key={recordText(event, 'audit_id', String(index))}><span>{formatDate(event.created_at)}</span><span><strong>{recordText(event, 'username', '系统')}</strong><small>{recordText(event, 'user_id')}</small></span><span>{Array.isArray(event.roles) ? event.roles.join(' / ') : '—'}</span><span>{recordText(event, 'method')}</span><span title={recordText(event, 'path')}>{recordText(event, 'path')}</span><span className={status >= 400 ? 'audit-status error' : 'audit-status'}>{status || '—'}</span><span>{recordText(event, 'client_ip')}</span></div> })}
    </div></section>
  </div>
}

export function AdminSystemPage() {
  const status = useQuery({ queryKey: ['admin-status'], queryFn: api.adminStatus })
  const overview = useQuery({ queryKey: ['admin-overview'], queryFn: api.adminOverview })
  const checks = [
    ['业务数据库', status.data?.database || '读取中', Boolean(status.data?.database)],
    ['后台任务', status.data?.task_backend || '读取中', Boolean(status.data?.task_backend)],
    ['身份认证', status.data?.auth_mode === 'oidc' ? 'Keycloak OIDC' : '本地预览', status.data?.auth_mode === 'oidc'],
    ['用户管理', status.data?.user_management.available ? '已连接' : status.data?.user_management.message || '预览模式', Boolean(status.data?.user_management.available)],
    ['凭据保险库', overview.data?.credential_vault_ready ? '已就绪' : '待配置', Boolean(overview.data?.credential_vault_ready)],
  ] as const
  return <div className="page admin-simple-page"><AdminPageTitle title="系统运行" description="检查数据库、任务队列、身份服务、存储和凭据保险库状态。" action={<button className="icon-button" title="刷新" onClick={() => { void status.refetch(); void overview.refetch() }}><RefreshCw size={17}/></button>}/>
    <div className="admin-system-grid"><section className="panel"><div className="panel-heading"><strong>组件状态</strong><span>{status.data?.profile || 'loading'}</span></div><div className="admin-system-checks">{checks.map(([label, value, ready]) => <div key={label}>{ready ? <CheckCircle2 size={18}/> : <XCircle size={18}/>}<span><strong>{label}</strong><small>{value}</small></span></div>)}</div></section><section className="panel"><div className="panel-heading"><strong>部署信息</strong><span>{status.data?.build_id || 'unversioned'}</span></div><dl className="admin-definition-list"><div><dt>运行配置</dt><dd>{status.data?.profile || '—'}</dd></div><div><dt>认证模式</dt><dd>{status.data?.auth_mode || '—'}</dd></div><div><dt>数据库</dt><dd>{status.data?.database || '—'}</dd></div><div><dt>任务执行器</dt><dd>{status.data?.task_backend || '—'}</dd></div></dl></section></div>
  </div>
}
