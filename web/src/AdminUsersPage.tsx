import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check, HardDrive, KeyRound, LogOut, Plus, RefreshCw, Search, ShieldCheck,
  Trash2, UserRound, Users,
} from 'lucide-react'
import { api, type AdminUser } from './api'
import { useAuth } from './auth'

const ROLE_OPTIONS = [
  ['admin', '管理员', '用户、系统设置和全部数据权限'],
  ['curator', '数据整理员', '导入、抽取、映射和候选数据编辑'],
  ['reviewer', '审核员', '审核、标准化与导出'],
  ['viewer', '查看者', '只读查看数据与溯源'],
] as const

const GIB = 1024 ** 3

type UserForm = {
  username: string
  email: string
  first_name: string
  last_name: string
  enabled: boolean
  roles: string[]
  password: string
  temporary_password: boolean
}

const EMPTY_FORM: UserForm = {
  username: '', email: '', first_name: '', last_name: '', enabled: true,
  roles: ['viewer'], password: '', temporary_password: true,
}

function formFromUser(user?: AdminUser): UserForm {
  if (!user) return { ...EMPTY_FORM, roles: [...EMPTY_FORM.roles] }
  return {
    username: user.username,
    email: user.email,
    first_name: user.first_name,
    last_name: user.last_name,
    enabled: user.enabled,
    roles: [...user.roles],
    password: '',
    temporary_password: true,
  }
}

export default function AdminUsersPage() {
  const auth = useAuth()
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState('')
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState<UserForm>(() => formFromUser())
  const [busy, setBusy] = useState('')
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [quotaGiB, setQuotaGiB] = useState('10')
  const [allocationDrafts, setAllocationDrafts] = useState<Record<string, { enabled: boolean; tokens: string; cost: string }>>({})

  const status = useQuery({ queryKey: ['admin-status'], queryFn: api.adminStatus })
  const users = useQuery({
    queryKey: ['admin-users', search],
    queryFn: () => api.adminUsers(search),
  })
  const credentials = useQuery({ queryKey: ['admin-model-credentials'], queryFn: api.adminModelCredentials })
  const policy = useQuery({
    queryKey: ['admin-user-policy', selectedId],
    queryFn: () => api.adminUserPolicy(selectedId),
    enabled: Boolean(selectedId && !creating),
  })
  const selected = useMemo(
    () => users.data?.users.find((item) => item.id === selectedId),
    [selectedId, users.data?.users],
  )
  const isPreview = status.data?.auth_mode !== 'oidc' || Boolean(users.data?.preview)

  useEffect(() => {
    if (!creating && !selectedId && users.data?.users.length) setSelectedId(users.data.users[0].id)
  }, [creating, selectedId, users.data?.users])
  useEffect(() => {
    if (!creating && selected) setForm(formFromUser(selected))
  }, [creating, selected])
  useEffect(() => {
    if (!policy.data) return
    setQuotaGiB(String(Math.round((policy.data.storage.quota_bytes / GIB) * 10) / 10))
    const existing = new Map(policy.data.model_allocations.map((item) => [item.credential_id, item]))
    setAllocationDrafts(Object.fromEntries((credentials.data?.credentials || []).map((credential) => {
      const allocation = existing.get(credential.credential_id)
      return [credential.credential_id, {
        enabled: Boolean(allocation?.enabled),
        tokens: String(allocation?.monthly_token_limit || 0),
        cost: String(allocation?.monthly_cost_limit || 0),
      }]
    })))
  }, [credentials.data?.credentials, policy.data])

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['admin-status'] }),
      queryClient.invalidateQueries({ queryKey: ['admin-users'] }),
      queryClient.invalidateQueries({ queryKey: ['admin-user-policy'] }),
      queryClient.invalidateQueries({ queryKey: ['admin-overview'] }),
    ])
  }
  const run = async (label: string, operation: () => Promise<unknown>, success: string) => {
    setBusy(label)
    setError('')
    setNotice('')
    try {
      await operation()
      setNotice(success)
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setBusy('')
    }
  }
  const toggleRole = (role: string) => {
    setForm((current) => ({
      ...current,
      roles: current.roles.includes(role)
        ? current.roles.filter((item) => item !== role)
        : [...current.roles, role],
    }))
  }
  const save = async () => {
    if (!form.username.trim()) {
      setError('请输入用户名。')
      return
    }
    if (creating) {
      await run('save', async () => {
        const created = await api.createAdminUser(form)
        setCreating(false)
        setSelectedId(created.id)
      }, '用户已创建。')
      return
    }
    if (!selected) return
    await run('save', async () => {
      await api.updateAdminUser(selected.id, {
        username: form.username,
        email: form.email,
        first_name: form.first_name,
        last_name: form.last_name,
        enabled: form.enabled,
      })
      await api.updateAdminUserRoles(selected.id, form.roles)
    }, '用户资料和角色已保存。')
  }
  const saveQuota = async () => {
    if (!selected) return
    const value = Number(quotaGiB)
    if (!Number.isFinite(value) || value < 0) {
      setError('请输入有效的 GiB 存储配额。')
      return
    }
    await run('quota', () => api.updateAdminUserStorageQuota(selected.id, Math.round(value * GIB)), '存储配额已保存。')
  }
  const saveAllocation = async (credentialId: string) => {
    if (!selected) return
    const draft = allocationDrafts[credentialId] || { enabled: false, tokens: '0', cost: '0' }
    await run(`allocation:${credentialId}`, () => api.updateAdminUserModelAllocation(selected.id, {
      credential_id: credentialId,
      enabled: draft.enabled,
      monthly_token_limit: Math.max(0, Number(draft.tokens) || 0),
      monthly_cost_limit: Math.max(0, Number(draft.cost) || 0),
    }), '模型权限与额度已保存。')
  }

  return <div className="page admin-users-page">
    <div className="page-title-row admin-title-row">
      <div>
        <h1>用户管理</h1>
        <p>管理工作室账号、访问角色、临时密码与登录会话。</p>
      </div>
      <button className="icon-button" title="刷新用户和身份服务状态" onClick={() => void refresh()} disabled={status.isFetching || users.isFetching}>
        <RefreshCw size={17} className={status.isFetching || users.isFetching ? 'spin' : ''}/>
      </button>
    </div>

    <div className="admin-runtime-strip" aria-label="运行环境状态">
      <div><span>运行配置</span><strong>{status.data?.profile || '读取中'}</strong></div>
      <div><span>运行版本</span><strong>{status.data?.build_id || '读取中'}</strong></div>
      <div><span>身份认证</span><strong>{status.data?.auth_mode === 'oidc' ? 'Keycloak OIDC' : '本地开发模式'}</strong></div>
      <div><span>数据库</span><strong>{status.data?.database || '读取中'}</strong></div>
      <div><span>后台任务</span><strong>{status.data?.task_backend || '读取中'}</strong></div>
      <div className={status.data?.user_management.available ? 'ready' : 'preview'}>
        <span>用户服务</span><strong>{status.data?.user_management.available ? '已连接' : '预览状态'}</strong>
      </div>
    </div>

    {isPreview && <div className="admin-preview-banner">
      <ShieldCheck size={18}/>
      <div><strong>当前是 Mac 本地开发预览</strong><span>界面与生产版一致，但没有启用登录和 Keycloak，因此用户写操作被安全禁用。局域网部署后会在这里管理真实账号。</span></div>
    </div>}
    {status.data?.auth_mode === 'oidc' && !status.data.user_management.available && <div className="admin-preview-banner warning">
      <ShieldCheck size={18}/><div><strong>身份服务账号尚未连接</strong><span>{status.data.user_management.message}</span></div>
    </div>}

    <div className="admin-users-workspace">
      <section className="panel admin-user-list">
        <div className="panel-heading"><strong>账号</strong><span>{users.data?.total ?? 0}</span></div>
        <div className="admin-user-tools">
          <label className="search-box"><Search size={15}/><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索用户名或邮箱"/></label>
          <button className="icon-button primary-button" title="新增用户" onClick={() => { setCreating(true); setSelectedId(''); setForm(formFromUser()); setError(''); setNotice('') }} disabled={isPreview}><Plus size={17}/></button>
        </div>
        <div className="admin-user-rows">
          {users.isLoading && <div className="admin-list-empty">正在读取用户...</div>}
          {!users.isLoading && !users.data?.users.length && <div className="admin-list-empty">没有匹配的用户</div>}
          {users.data?.users.map((user) => <button key={user.id} className={selectedId === user.id && !creating ? 'admin-user-row active' : 'admin-user-row'} onClick={() => { setCreating(false); setSelectedId(user.id); setNotice(''); setError('') }}>
            <span className="admin-avatar"><UserRound size={17}/></span>
            <span className="admin-user-summary"><strong>{user.username}</strong><small>{user.email || '未填写邮箱'}</small></span>
            <span className={user.enabled ? 'user-state enabled' : 'user-state'}>{user.enabled ? '启用' : '停用'}</span>
          </button>)}
        </div>
      </section>

      <section className="panel admin-user-editor">
        <div className="panel-heading">
          <strong>{creating ? '新增用户' : selected ? `编辑 ${selected.username}` : '选择一个用户'}</strong>
          {!creating && selected && <span>{selected.roles.length} 个角色</span>}
        </div>
        {!creating && !selected ? <div className="admin-editor-empty"><Users size={34}/><span>从左侧选择账号查看资料和权限。</span></div> : <div className="admin-editor-body">
          <div className="admin-form-section">
            <div className="admin-section-heading"><strong>基本资料</strong><label className="toggle-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })}/><span>允许登录</span></label></div>
            <div className="admin-form-grid">
              <label><span>用户名</span><input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} autoComplete="off"/></label>
              <label><span>邮箱</span><input type="email" value={form.email} onChange={(event) => setForm({ ...form, email: event.target.value })}/></label>
              <label><span>名</span><input value={form.first_name} onChange={(event) => setForm({ ...form, first_name: event.target.value })}/></label>
              <label><span>姓</span><input value={form.last_name} onChange={(event) => setForm({ ...form, last_name: event.target.value })}/></label>
            </div>
          </div>

          <div className="admin-form-section">
            <div className="admin-section-heading"><strong>GeoChem 角色</strong><small>至少保留一个角色；未选择时后端会使用查看者。</small></div>
            <div className="admin-role-grid">
              {ROLE_OPTIONS.map(([role, label, description]) => <label key={role} className={form.roles.includes(role) ? 'admin-role-option selected' : 'admin-role-option'}>
                <input type="checkbox" checked={form.roles.includes(role)} onChange={() => toggleRole(role)}/>
                <span><strong>{label}</strong><small>{description}</small></span>
                {form.roles.includes(role) && <Check size={16}/>} 
              </label>)}
            </div>
          </div>

          {!creating && selected && <div className="admin-form-section">
            <div className="admin-section-heading"><strong>存储与模型资源</strong><small>用户只能调用被分配的共享模型，无法查看服务器 API Key。</small></div>
            <div className="admin-user-quota-row">
              <span className="admin-resource-icon"><HardDrive size={17}/></span>
              <span><strong>存储配额</strong><small>已使用 {((policy.data?.storage.used_bytes || 0) / GIB).toFixed(2)} GiB</small></span>
              <input type="number" min="0" step="1" value={quotaGiB} onChange={(event) => setQuotaGiB(event.target.value)}/><b>GiB</b>
              <button onClick={() => void saveQuota()} disabled={busy !== ''}>{busy === 'quota' ? '保存中' : '保存'}</button>
            </div>
            <div className="admin-allocation-list">
              {!credentials.data?.credentials.length && <div className="admin-policy-empty">尚未配置共享模型，请先前往“模型与密钥”。</div>}
              {credentials.data?.credentials.map((credential) => {
                const draft = allocationDrafts[credential.credential_id] || { enabled: false, tokens: '0', cost: '0' }
                return <div className="admin-allocation-row" key={credential.credential_id}>
                  <label className="toggle-row"><input type="checkbox" checked={draft.enabled} onChange={(event) => setAllocationDrafts((current) => ({ ...current, [credential.credential_id]: { ...draft, enabled: event.target.checked } }))}/></label>
                  <span><strong>{credential.name}</strong><small>{credential.provider} · {credential.model_id}</small></span>
                  <label><span>月 Token 上限</span><input type="number" min="0" value={draft.tokens} onChange={(event) => setAllocationDrafts((current) => ({ ...current, [credential.credential_id]: { ...draft, tokens: event.target.value } }))}/></label>
                  <label><span>月成本上限</span><input type="number" min="0" step="0.01" value={draft.cost} onChange={(event) => setAllocationDrafts((current) => ({ ...current, [credential.credential_id]: { ...draft, cost: event.target.value } }))}/></label>
                  <button onClick={() => void saveAllocation(credential.credential_id)} disabled={busy !== ''}>{busy === `allocation:${credential.credential_id}` ? '保存中' : '应用'}</button>
                </div>
              })}
            </div>
          </div>}

          <div className="admin-form-section">
            <div className="admin-section-heading"><strong>{creating ? '初始密码' : '重置密码'}</strong><small>建议使用临时密码，用户首次登录后必须修改。</small></div>
            <div className="admin-password-row">
              <label><span>新密码</span><input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} placeholder={creating ? '可选，至少 10 位' : '输入后单独重置'} autoComplete="new-password"/></label>
              <label className="toggle-row"><input type="checkbox" checked={form.temporary_password} onChange={(event) => setForm({ ...form, temporary_password: event.target.checked })}/><span>首次登录强制修改</span></label>
              {!creating && <button onClick={() => selected && void run('password', () => api.resetAdminUserPassword(selected.id, form.password, form.temporary_password), '密码已重置。')} disabled={isPreview || busy !== '' || form.password.length < 10}><KeyRound size={16}/> 重置密码</button>}
            </div>
          </div>

          {(notice || error) && <div className={error ? 'admin-form-message error' : 'admin-form-message'}>{error || notice}</div>}
          <div className="admin-editor-actions">
            <button className="primary-button" onClick={() => void save()} disabled={isPreview || busy !== ''}>{busy === 'save' ? '保存中...' : creating ? '创建用户' : '保存资料与角色'}</button>
            {!creating && selected && <>
              <button onClick={() => void run('logout', () => api.logoutAdminUser(selected.id), '该用户的登录会话已撤销。')} disabled={isPreview || busy !== ''}><LogOut size={16}/> 强制下线</button>
              <button className="danger-button" onClick={() => {
                if (!window.confirm(`确定删除用户 ${selected.username}？此操作不能撤销。`)) return
                void run('delete', async () => { await api.deleteAdminUser(selected.id); setSelectedId('') }, '用户已删除。')
              }} disabled={isPreview || busy !== ''}><Trash2 size={16}/> 删除用户</button>
            </>}
            {creating && <button onClick={() => { setCreating(false); setSelectedId(users.data?.users[0]?.id || '') }}>取消</button>}
          </div>
        </div>}
      </section>
    </div>
  </div>
}
