import { useEffect, useRef, useState, type ComponentType } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Activity, BookCheck, BookOpen, Bot, Boxes, ChevronDown, ChevronLeft, ChevronRight,
  ClipboardList, Columns3, Database, FileSpreadsheet, FlaskConical, Gauge,
  HardDrive, KeyRound, LayoutDashboard, LogOut, ScrollText, ServerCog,
  Settings, ShieldCheck, SquarePlus, UserRound, Users, Workflow,
} from 'lucide-react'
import { api } from './api'
import { useAuth } from './auth'
import { useAppStore } from './store'

type NavEntry = readonly [string, string, ComponentType<{ size?: number }>]

const WORKSPACE_NAV: NavEntry[] = [
  ['/', '科研助手', Bot],
  ['/overview', '项目概览', Gauge],
  ['/headers', '表头管理', Columns3],
  ['/workbench', '抽取工作台', Workflow],
  ['/rules', '规则中心', BookCheck],
  ['/review', '人工审核', ShieldCheck],
  ['/standardized', '标准化导出', FileSpreadsheet],
  ['/settings', '设置', Settings],
]

const ADMIN_NAV: NavEntry[] = [
  ['/admin', '管理概览', LayoutDashboard],
  ['/admin/users', '用户管理', Users],
  ['/admin/workspaces', '工作区成员', Boxes],
  ['/admin/roles', '角色与权限', ShieldCheck],
  ['/admin/rules', '规则审批', BookCheck],
  ['/admin/models', '模型与密钥', KeyRound],
  ['/admin/storage', '存储配额', HardDrive],
  ['/admin/tasks', '任务监控', ClipboardList],
  ['/admin/audit', '审计日志', ScrollText],
  ['/admin/system', '系统运行', ServerCog],
]

function AccountMenu({ adminShell = false }: { adminShell?: boolean }) {
  const auth = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const menuRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  useEffect(() => setOpen(false), [location.pathname])
  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [open])

  return <div className="account-control" ref={menuRef}>
    <button className="account-button" title={`${auth.username} · ${auth.roles.join(', ')}`} onClick={() => setOpen((value) => !value)} aria-expanded={open}>
      <UserRound size={16}/><span>{auth.username}</span><ChevronDown size={14}/>
    </button>
    {open && <div className="account-menu">
      <div className="account-menu-profile"><span className="account-menu-avatar"><UserRound size={18}/></span><div><strong>{auth.username}</strong><small>{auth.enabled ? '组织账号' : '本地预览账号'}</small></div></div>
      <div className="account-role-list">{auth.roles.map((role) => <span key={role}>{role}</span>)}</div>
      {!auth.enabled && <div className="account-mode-note">当前为本地界面身份模拟，真实权限由 Keycloak 令牌决定。</div>}
      {auth.roles.includes('admin') && <button onClick={() => navigate(adminShell ? '/portal' : '/admin')}><Settings size={16}/>{adminShell ? '切换入口' : '进入管理后台'}</button>}
      {!adminShell && <button onClick={() => navigate('/settings')}><Settings size={16}/> 工作区设置</button>}
      <button onClick={() => void auth.signOut()}><LogOut size={16}/> {auth.enabled ? '退出登录' : '退出预览'}</button>
    </div>}
  </div>
}

function Sidebar({ entries, collapsed, setCollapsed, admin = false }: {
  entries: NavEntry[]
  collapsed: boolean
  setCollapsed: (value: boolean) => void
  admin?: boolean
}) {
  return <aside className={`sidebar ${admin ? 'admin-sidebar' : ''} ${collapsed ? 'collapsed' : ''}`}>
    <div className="brand">
      {admin ? <ShieldCheck size={24}/> : <Database size={24}/>}
      <strong>{admin ? 'GeoChem Admin' : 'GeoChem'}</strong>
      <button className="sidebar-toggle icon-button" title={collapsed ? '展开导航' : '收起导航'} onClick={() => setCollapsed(!collapsed)}>{collapsed ? <ChevronRight size={16}/> : <ChevronLeft size={16}/>}</button>
    </div>
    <p className="brand-sub">{admin ? <>Administration Console<br/>用户、资源与系统治理</> : <>Data Curation Agent<br/>地球化学文献数据整理与标准化</>}</p>
    <nav className="nav-list">
      {entries.map(([path, label, Icon]) => <NavLink key={path} to={path} title={label} end={path === '/' || path === '/admin'} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
        <Icon size={18}/><span>{label}</span>
      </NavLink>)}
    </nav>
    <div className="service-state"><span className="online-dot"/> {admin ? '管理服务在线' : 'GeoChem 服务在线'}<br/><small>{admin ? '安全与配额策略已加载' : '科研工作区可用'}</small></div>
  </aside>
}

export function WorkspaceShell() {
  const navigate = useNavigate()
  const location = useLocation()
  const auth = useAuth()
  const { projectId, setProjectId, articleId, setArticleId } = useAppStore()
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem('geochem.sidebar.collapsed') === 'true')
  const previewWorkspace = useQuery({
    queryKey: ['workspace-preview'],
    queryFn: api.workspace,
    enabled: !auth.enabled,
  })
  const activeWorkspace = auth.currentWorkspace || previewWorkspace.data
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  useEffect(() => {
    if (activeWorkspace?.project_id && activeWorkspace.project_id !== projectId) {
      setProjectId(activeWorkspace.project_id)
      setArticleId('')
    }
  }, [activeWorkspace?.project_id, projectId, setArticleId, setProjectId])
  useEffect(() => window.localStorage.setItem('geochem.sidebar.collapsed', String(collapsed)), [collapsed])

  return <div className={`app-shell ${collapsed ? 'sidebar-is-collapsed' : ''}`}>
    <Sidebar entries={WORKSPACE_NAV} collapsed={collapsed} setCollapsed={setCollapsed}/>
    <div className="main-shell">
      <header className="topbar">
        <div className="workspace-state"><BookOpen size={17}/><span>当前工作区</span><strong>{activeWorkspace?.project_name || '正在载入...'}</strong>{auth.workspaceRole && <small className="workspace-role-badge">{auth.workspaceRole}</small>}</div>
        <div className="topbar-actions">
          <label htmlFor="global-article">当前文献</label>
          <select
            id="global-article"
            value={articleId}
            onChange={(event) => setArticleId(event.target.value)}
            disabled={location.pathname === '/'}
            title={location.pathname === '/' ? '科研助手中的文章需通过对话明确选择' : '切换当前文献'}
          >
            <option value="">{articles.data?.length ? '未选择文章' : '暂无文献'}</option>
            {articles.data?.map((article) => <option key={article.article_id} value={article.article_id}>{article.title || article.doi || article.article_id}</option>)}
          </select>
          <button className="primary-button" onClick={() => navigate('/')}><SquarePlus size={17}/> 新建任务</button>
          <AccountMenu/>
        </div>
      </header>
      <main className="page-viewport"><Outlet/></main>
    </div>
  </div>
}

export function AdminShell() {
  const navigate = useNavigate()
  const [collapsed, setCollapsed] = useState(() => window.localStorage.getItem('geochem.admin.sidebar.collapsed') === 'true')
  useEffect(() => window.localStorage.setItem('geochem.admin.sidebar.collapsed', String(collapsed)), [collapsed])
  return <div className={`app-shell admin-app-shell ${collapsed ? 'sidebar-is-collapsed' : ''}`}>
    <Sidebar entries={ADMIN_NAV} collapsed={collapsed} setCollapsed={setCollapsed} admin/>
    <div className="main-shell">
      <header className="topbar admin-topbar">
        <div className="workspace-state"><ShieldCheck size={18}/><span>GeoChem</span><strong>管理后台</strong></div>
        <div className="topbar-actions">
          <span className="admin-environment"><span className="online-dot"/> 生产治理视图</span>
          <button onClick={() => navigate('/')}><FlaskConical size={17}/> 进入 GeoChem</button>
          <AccountMenu adminShell/>
        </div>
      </header>
      <main className="page-viewport admin-page-viewport"><Outlet/></main>
    </div>
  </div>
}
