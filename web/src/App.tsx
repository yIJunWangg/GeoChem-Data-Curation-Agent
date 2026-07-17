import { useEffect, useState } from 'react'
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen, Bot, ChevronLeft, ChevronRight, Columns3, Database, FileSpreadsheet,
  Gauge, Settings, ShieldCheck, SquarePlus, Workflow,
} from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import WorkbenchPage from './WorkbenchPage'
import { DashboardPage, HeaderPage, SettingsPage, StandardizedPage } from './Pages'
import { ReviewPage } from './ReviewPage'
import { ChatPage } from './ChatPage'

const NAV = [
  ['/', '项目概览', Gauge],
  ['/headers', '表头管理', Columns3],
  ['/workbench', '抽取工作台', Workflow],
  ['/chat', '对话助手', Bot],
  ['/review', '人工审核', ShieldCheck],
  ['/standardized', '标准化导出', FileSpreadsheet],
  ['/settings', '设置', Settings],
] as const

function LegacyRedirect({ pathname, defaults = {} }: { pathname: string; defaults?: Record<string, string> }) {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  Object.entries(defaults).forEach(([key, value]) => { if (!params.has(key)) params.set(key, value) })
  return <Navigate to={`${pathname}?${params.toString()}`} replace />
}

export default function App() {
  const { projectId, setProjectId } = useAppStore()
  const navigate = useNavigate()
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.localStorage.getItem('geochem.sidebar.collapsed') === 'true')
  const workspace = useQuery({ queryKey: ['workspace'], queryFn: api.workspace })
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const { articleId, setArticleId } = useAppStore()

  useEffect(() => { if (workspace.data) setProjectId(workspace.data.project_id) }, [workspace.data, setProjectId])
  useEffect(() => {
    if (!articleId && articles.data?.length) setArticleId(articles.data[0].article_id)
  }, [articleId, articles.data, setArticleId])
  useEffect(() => { window.localStorage.setItem('geochem.sidebar.collapsed', String(sidebarCollapsed)) }, [sidebarCollapsed])

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-is-collapsed' : ''}`}>
      <aside className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        <div className="brand"><Database size={24} /><strong>GeoChem</strong><button className="sidebar-toggle icon-button" title={sidebarCollapsed ? '展开导航' : '收起导航'} onClick={() => setSidebarCollapsed((value) => !value)}>{sidebarCollapsed ? <ChevronRight size={16}/> : <ChevronLeft size={16}/>}</button></div>
        <p className="brand-sub">Data Curation Agent<br />地球化学文献数据整理与标准化</p>
        <nav className="nav-list">
          {NAV.map(([path, label, Icon]) => (
            <NavLink key={path} to={path} title={label} end={path === '/'} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
              <Icon size={18} /><span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="service-state"><span className="online-dot" /> 本地服务在线<br /><small>Web Preview v1.2</small></div>
      </aside>

      <div className="main-shell">
        <header className="topbar">
          <div className="workspace-state"><BookOpen size={17} /><span>当前工作区</span><strong>{workspace.data?.project_name || '正在载入...'}</strong></div>
          <div className="topbar-actions">
            <label htmlFor="global-article">当前文献</label>
            <select id="global-article" value={articleId} onChange={(event) => setArticleId(event.target.value)}>
              {!articles.data?.length && <option value="">暂无文献</option>}
              {articles.data?.map((article) => <option key={article.article_id} value={article.article_id}>{article.title || article.doi || article.article_id}</option>)}
            </select>
            <button className="primary-button" onClick={() => navigate('/?panel=import')}><SquarePlus size={17} /> 新建任务</button>
          </div>
        </header>
        <main className="page-viewport">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/headers" element={<HeaderPage />} />
            <Route path="/import" element={<LegacyRedirect pathname="/" defaults={{ panel: 'import' }} />} />
            <Route path="/workbench" element={<WorkbenchPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/rules" element={<LegacyRedirect pathname="/workbench" defaults={{ view: 'rules' }} />} />
            <Route path="/standardized" element={<StandardizedPage />} />
            <Route path="/trace" element={<LegacyRedirect pathname="/review" defaults={{ mode: 'standardized' }} />} />
            <Route path="/cost" element={<LegacyRedirect pathname="/settings" defaults={{ tab: 'usage' }} />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
