import { useEffect } from 'react'
import { NavLink, Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  BarChart3, BookOpen, Bot, Database, FileInput, FileSpreadsheet, Gauge,
  Columns3, GitBranch, MemoryStick, MessageSquare, Settings, ShieldCheck,
} from 'lucide-react'
import { api } from './api'
import { useAppStore } from './store'
import WorkbenchPage from './WorkbenchPage'
import { CostPage, DashboardPage, HeaderPage, ImportPage, RulesPage, SettingsPage, StandardizedPage } from './Pages'
import { ReviewPage } from './ReviewPage'
import { TracePage } from './TracePage'
import { ChatPage } from './ChatPage'

const NAV = [
  ['/', '项目总览', Gauge],
  ['/headers', '表头配置', Columns3],
  ['/import', '文献导入', FileInput],
  ['/workbench', '智能体工作台', Bot],
  ['/chat', '对话助手', MessageSquare],
  ['/review', '人工审核', ShieldCheck],
  ['/rules', '规则记忆', MemoryStick],
  ['/standardized', '标准化导出', FileSpreadsheet],
  ['/trace', '溯源查看', GitBranch],
  ['/cost', 'Token统计', BarChart3],
  ['/settings', '设置', Settings],
] as const

export default function App() {
  const { projectId, setProjectId } = useAppStore()
  const navigate = useNavigate()
  const workspace = useQuery({ queryKey: ['workspace'], queryFn: api.workspace })
  const articles = useQuery({ queryKey: ['articles', projectId], queryFn: () => api.articles(projectId), enabled: Boolean(projectId) })
  const { articleId, setArticleId } = useAppStore()

  useEffect(() => { if (workspace.data) setProjectId(workspace.data.project_id) }, [workspace.data, setProjectId])
  useEffect(() => {
    if (!articleId && articles.data?.length) setArticleId(articles.data[0].article_id)
  }, [articleId, articles.data, setArticleId])

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><Database size={24} /><strong>GeoChem</strong></div>
        <p className="brand-sub">Data Curation Agent<br />地球化学文献数据整理与标准化</p>
        <nav className="nav-list">
          {NAV.map(([path, label, Icon]) => (
            <NavLink key={path} to={path} end={path === '/'} className={({ isActive }) => isActive ? 'nav-item active' : 'nav-item'}>
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
            <button className="primary-button" onClick={() => navigate('/import')}><Bot size={17} /> 新建任务</button>
          </div>
        </header>
        <main className="page-viewport">
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/headers" element={<HeaderPage />} />
            <Route path="/import" element={<ImportPage />} />
            <Route path="/workbench" element={<WorkbenchPage />} />
            <Route path="/chat" element={<ChatPage />} />
            <Route path="/review" element={<ReviewPage />} />
            <Route path="/rules" element={<RulesPage />} />
            <Route path="/standardized" element={<StandardizedPage />} />
            <Route path="/trace" element={<TracePage />} />
            <Route path="/cost" element={<CostPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
