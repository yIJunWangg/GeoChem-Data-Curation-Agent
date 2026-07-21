import { Navigate, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  ArrowRight, Database, FlaskConical, LogIn, Settings, ShieldCheck, Users,
} from 'lucide-react'
import { useAuth } from './auth'

function safeReturnUrl(value: string | null) {
  return value?.startsWith('/') && !value.startsWith('//') ? value : '/'
}

function AuthStateScreen({ kind, message }: { kind: 'loading' | 'error'; message: string }) {
  return <div className="auth-screen auth-product-screen">
    <div className={`auth-card ${kind === 'error' ? 'error' : ''}`}>
      <span className="auth-product-mark"><Database size={24}/></span>
      <strong>{kind === 'loading' ? '正在连接 GeoChem' : '暂时无法进入 GeoChem'}</strong>
      <span>{message}</span>
      {kind === 'error' && <button onClick={() => window.location.reload()}>重新连接</button>}
    </div>
  </div>
}

export function AuthGate({ admin = false }: { admin?: boolean }) {
  const auth = useAuth()
  const location = useLocation()
  if (auth.loading) return <AuthStateScreen kind="loading" message="正在验证登录状态和访问权限..."/>
  if (auth.error) return <AuthStateScreen kind="error" message={auth.error}/>
  if (!auth.authenticated) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`
    return <Navigate to={`/login?returnTo=${encodeURIComponent(returnTo)}`} replace/>
  }
  if (admin && !auth.roles.includes('admin')) return <Navigate to="/" replace/>
  return <Outlet/>
}

export function LoginPage() {
  const auth = useAuth()
  const [params] = useSearchParams()
  const returnTo = safeReturnUrl(params.get('returnTo'))
  if (auth.loading) return <AuthStateScreen kind="loading" message="正在读取身份服务配置..."/>
  if (auth.error) return <AuthStateScreen kind="error" message={auth.error}/>
  if (auth.authenticated) {
    return <Navigate to={auth.roles.includes('admin') ? `/portal?returnTo=${encodeURIComponent(returnTo)}` : returnTo} replace/>
  }

  const continueWithPreview = (role: 'admin' | 'curator') => {
    auth.enterPreview(role)
  }

  return <div className="login-layout">
    <section className="login-story">
      <div className="login-brand"><span><Database size={25}/></span><strong>GeoChem</strong></div>
      <div className="login-story-copy">
        <span className="login-eyebrow">Data Curation Workspace</span>
        <h1>可审核、可追溯的<br/>地球化学数据整理</h1>
        <p>从论文与补充材料发现证据，经过字段映射、人工审核和标准化后，形成可复核的数据记录。</p>
      </div>
      <div className="login-capabilities">
        <span><FlaskConical size={17}/> 科研数据工作流</span>
        <span><ShieldCheck size={17}/> 人工确认与审计</span>
        <span><Database size={17}/> 本地数据资产</span>
      </div>
    </section>
    <section className="login-form-side">
      <div className="login-panel">
        <div className="login-panel-heading">
          <span className="auth-product-mark"><Database size={22}/></span>
          <div><h2>登录 GeoChem</h2><p>使用工作室账号继续</p></div>
        </div>
        {auth.enabled ? <>
          <button className="primary-button login-submit" onClick={() => void auth.signIn(`/portal?returnTo=${encodeURIComponent(returnTo)}`)}>
            <LogIn size={18}/> 使用组织账号登录
          </button>
          <div className="login-security-note"><ShieldCheck size={16}/><span>账号、角色和会话由 Keycloak 身份服务统一管理。</span></div>
        </> : <>
          <div className="preview-login-note"><strong>Mac 本地界面预览</strong><span>当前未启用 Keycloak。请选择一个界面身份查看实际产品结构。</span></div>
          <button className="primary-button login-submit" onClick={() => continueWithPreview('admin')}><ShieldCheck size={18}/> 以管理员身份预览</button>
          <button className="login-submit" onClick={() => continueWithPreview('curator')}><FlaskConical size={18}/> 以科研用户身份预览</button>
        </>}
        <small className="login-help">登录即表示你仅在授权范围内处理科研数据和模型资源。</small>
      </div>
    </section>
  </div>
}

export function PortalPage() {
  const auth = useAuth()
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const returnTo = safeReturnUrl(params.get('returnTo'))
  if (!auth.roles.includes('admin')) return <Navigate to={returnTo} replace/>

  return <div className="portal-page">
    <header className="portal-header">
      <div className="login-brand"><span><Database size={23}/></span><strong>GeoChem</strong></div>
      <div className="portal-account"><span>{auth.username}</span><button onClick={() => void auth.signOut()}>退出登录</button></div>
    </header>
    <main className="portal-content">
      <div className="portal-intro"><span>欢迎回来</span><h1>选择本次要进入的工作区域</h1><p>管理员账号可进入科研数据工作区，也可以维护用户、模型权限、存储与系统运行状态。</p></div>
      <div className="portal-options">
        <button className="portal-option workspace" onClick={() => navigate(returnTo.startsWith('/admin') ? '/' : returnTo)}>
          <span className="portal-option-icon"><FlaskConical size={26}/></span>
          <span className="portal-option-copy"><strong>GeoChem 工作区</strong><small>导入文献、抽取数据、人工审核与导出</small></span>
          <ArrowRight size={20}/>
        </button>
        <button className="portal-option admin" onClick={() => navigate('/admin')}>
          <span className="portal-option-icon"><Settings size={26}/></span>
          <span className="portal-option-copy"><strong>管理后台</strong><small>用户、角色、模型权限、存储配额与审计</small></span>
          <ArrowRight size={20}/>
        </button>
      </div>
      <div className="portal-role-note"><Users size={16}/><span>当前账号拥有管理员权限。普通用户登录后会直接进入 GeoChem 工作区。</span></div>
    </main>
  </div>
}
