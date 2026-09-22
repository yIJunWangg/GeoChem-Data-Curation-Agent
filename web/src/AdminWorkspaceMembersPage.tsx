import { useEffect, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Building2, Plus, RefreshCw, ShieldCheck, Trash2, UserRound } from 'lucide-react'
import { api, type WorkspaceMember } from './api'

const ROLE_LABELS: Record<WorkspaceMember['role'], string> = {
  owner: 'Owner',
  curator: '数据整理员',
  reviewer: '审核员',
  viewer: '查看者',
}

const ROLES = Object.keys(ROLE_LABELS) as WorkspaceMember['role'][]

export default function AdminWorkspaceMembersPage() {
  const queryClient = useQueryClient()
  const workspaces = useQuery({ queryKey: ['admin-workspaces'], queryFn: api.workspaces })
  const users = useQuery({ queryKey: ['admin-users-for-membership'], queryFn: () => api.adminUsers('', 0, 500) })
  const [projectId, setProjectId] = useState('')
  const [newUserId, setNewUserId] = useState('')
  const [newRole, setNewRole] = useState<WorkspaceMember['role']>('viewer')
  const [busy, setBusy] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!projectId && workspaces.data?.workspaces.length) setProjectId(workspaces.data.workspaces[0].project_id)
  }, [projectId, workspaces.data?.workspaces])

  const members = useQuery({
    queryKey: ['admin-workspace-members', projectId],
    queryFn: () => api.adminWorkspaceMembers(projectId),
    enabled: Boolean(projectId),
  })
  const userById = useMemo(() => new Map((users.data?.users || []).map((user) => [user.id, user])), [users.data?.users])
  const memberIds = useMemo(() => new Set((members.data?.members || []).map((member) => member.user_id)), [members.data?.members])
  const availableUsers = useMemo(() => (users.data?.users || []).filter((user) => !memberIds.has(user.id)), [memberIds, users.data?.users])
  const selectedWorkspace = workspaces.data?.workspaces.find((item) => item.project_id === projectId)

  async function refresh() {
    await queryClient.invalidateQueries({ queryKey: ['admin-workspace-members', projectId] })
    await queryClient.invalidateQueries({ queryKey: ['admin-workspaces'] })
  }

  async function addMember() {
    if (!projectId || !newUserId) return
    setBusy('add'); setMessage(''); setError('')
    try {
      await api.addAdminWorkspaceMember(projectId, newUserId, newRole)
      setNewUserId('')
      setMessage('成员已加入工作区。')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '添加成员失败。')
    } finally {
      setBusy('')
    }
  }

  async function updateMember(member: WorkspaceMember, payload: Partial<Pick<WorkspaceMember, 'role' | 'status'>>) {
    setBusy(member.user_id); setMessage(''); setError('')
    try {
      await api.updateAdminWorkspaceMember(projectId, member.user_id, payload)
      setMessage('成员权限已更新。')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '更新成员失败。')
    } finally {
      setBusy('')
    }
  }

  async function removeMember(member: WorkspaceMember) {
    const name = member.display_name || member.username || userById.get(member.user_id)?.username || member.user_id
    if (!window.confirm(`确定从当前工作区移除 ${name}？`)) return
    setBusy(member.user_id); setMessage(''); setError('')
    try {
      await api.deleteAdminWorkspaceMember(projectId, member.user_id)
      setMessage('成员已移除。')
      await refresh()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '移除成员失败。')
    } finally {
      setBusy('')
    }
  }

  return <div className="page admin-workspace-members-page">
    <div className="page-title-row admin-title-row">
      <div><h1>工作区成员</h1><p>控制账号可以访问哪些科研数据，以及在工作区内可执行的操作。</p></div>
      <button className="icon-button" title="刷新" onClick={() => void refresh()}><RefreshCw size={17} className={members.isFetching ? 'spin' : ''}/></button>
    </div>

    <section className="panel workspace-member-toolbar">
      <label><span>工作区</span><select value={projectId} onChange={(event) => setProjectId(event.target.value)}>{workspaces.data?.workspaces.map((workspace) => <option key={workspace.project_id} value={workspace.project_id}>{workspace.project_name}</option>)}</select></label>
      <div className="workspace-member-summary"><Building2 size={18}/><span><strong>{selectedWorkspace?.project_name || '正在载入'}</strong><small>{selectedWorkspace?.organization_name || 'GeoChem 组织'} · {members.data?.members.length || 0} 位成员</small></span></div>
    </section>

    <section className="panel workspace-member-add">
      <div><strong>添加现有账号</strong><small>账号需先在“用户管理”中创建；加入后才可访问该工作区的数据。</small></div>
      <select value={newUserId} onChange={(event) => setNewUserId(event.target.value)}>
        <option value="">选择账号</option>
        {availableUsers.map((user) => <option key={user.id} value={user.id}>{user.username}{user.email ? ` · ${user.email}` : ''}</option>)}
      </select>
      <select value={newRole} onChange={(event) => setNewRole(event.target.value as WorkspaceMember['role'])}>{ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}</select>
      <button className="primary-button" disabled={!newUserId || busy === 'add'} onClick={() => void addMember()}><Plus size={16}/> 加入工作区</button>
    </section>

    {message && <div className="admin-form-message">{message}</div>}
    {error && <div className="admin-form-message error">{error}</div>}

    <section className="panel workspace-member-table">
      <div className="workspace-member-head"><span>成员</span><span>工作区角色</span><span>状态</span><span>加入时间</span><span>操作</span></div>
      <div className="workspace-member-body">
        {!members.isLoading && !members.data?.members.length && <div className="admin-table-empty">当前工作区还没有成员。</div>}
        {members.data?.members.map((member) => {
          const account = userById.get(member.user_id)
          const username = member.display_name || member.username || account?.username || member.user_id
          const email = member.email || account?.email || member.user_id
          return <div className="workspace-member-row" key={member.membership_id}>
            <span className="workspace-member-person"><i><UserRound size={17}/></i><span><strong>{username}</strong><small>{email}</small></span></span>
            <select value={member.role} disabled={busy === member.user_id} onChange={(event) => void updateMember(member, { role: event.target.value as WorkspaceMember['role'] })}>{ROLES.map((role) => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}</select>
            <button className={`member-status ${member.status}`} disabled={busy === member.user_id} onClick={() => void updateMember(member, { status: member.status === 'active' ? 'disabled' : 'active' })}><ShieldCheck size={14}/>{member.status === 'active' ? '已启用' : '已停用'}</button>
            <span>{new Date(member.created_at).toLocaleString('zh-CN', { hour12: false })}</span>
            <button className="icon-button danger-icon" title="移除成员" disabled={busy === member.user_id} onClick={() => void removeMember(member)}><Trash2 size={16}/></button>
          </div>
        })}
      </div>
    </section>
  </div>
}
