import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import WorkbenchPage from './WorkbenchPage'
import { DashboardPage, HeaderPage, SettingsPage, StandardizedPage } from './Pages'
import { ReviewPage } from './ReviewPage'
import { ChatPage } from './ChatPage'
import AdminUsersPage from './AdminUsersPage'
import AdminWorkspaceMembersPage from './AdminWorkspaceMembersPage'
import RuleCenterPage from './RuleCenterPage'
import { AuthGate, LoginPage, PortalPage } from './AuthPages'
import { AdminShell, WorkspaceShell } from './Shells'
import {
  AdminAuditPage, AdminModelsPage, AdminOverviewPage, AdminRolesPage,
  AdminStoragePage, AdminSystemPage, AdminTasksPage,
} from './AdminConsolePages'

function LegacyRedirect({ pathname, defaults = {} }: { pathname: string; defaults?: Record<string, string> }) {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  Object.entries(defaults).forEach(([key, value]) => { if (!params.has(key)) params.set(key, value) })
  return <Navigate to={`${pathname}?${params.toString()}`} replace/>
}

function CallbackScreen() {
  return <div className="auth-screen"><div className="auth-card"><strong>GeoChem</strong><span>正在完成安全登录...</span></div></div>
}

export default function App() {
  return <Routes>
    <Route path="/login" element={<LoginPage/>}/>
    <Route path="/auth/callback" element={<CallbackScreen/>}/>
    <Route path="/auth/silent-callback" element={<CallbackScreen/>}/>

    <Route element={<AuthGate/>}>
      <Route path="/portal" element={<PortalPage/>}/>
      <Route element={<WorkspaceShell/>}>
        <Route index element={<ChatPage/>}/>
        <Route path="/overview" element={<DashboardPage/>}/>
        <Route path="/headers" element={<HeaderPage/>}/>
        <Route path="/import" element={<LegacyRedirect pathname="/overview" defaults={{ panel: 'import' }}/>}/>
        <Route path="/workbench" element={<WorkbenchPage/>}/>
        <Route path="/chat" element={<LegacyRedirect pathname="/"/>}/>
        <Route path="/review" element={<ReviewPage/>}/>
        <Route path="/rules" element={<RuleCenterPage/>}/>
        <Route path="/standardized" element={<StandardizedPage/>}/>
        <Route path="/trace" element={<LegacyRedirect pathname="/review" defaults={{ mode: 'standardized' }}/>}/>
        <Route path="/cost" element={<LegacyRedirect pathname="/settings" defaults={{ tab: 'usage' }}/>}/>
        <Route path="/settings" element={<SettingsPage/>}/>
      </Route>
    </Route>

    <Route element={<AuthGate admin/>}>
      <Route path="/admin" element={<AdminShell/>}>
        <Route index element={<AdminOverviewPage/>}/>
        <Route path="users" element={<AdminUsersPage/>}/>
        <Route path="workspaces" element={<AdminWorkspaceMembersPage/>}/>
        <Route path="roles" element={<AdminRolesPage/>}/>
        <Route path="rules" element={<RuleCenterPage adminMode/>}/>
        <Route path="models" element={<AdminModelsPage/>}/>
        <Route path="storage" element={<AdminStoragePage/>}/>
        <Route path="tasks" element={<AdminTasksPage/>}/>
        <Route path="audit" element={<AdminAuditPage/>}/>
        <Route path="system" element={<AdminSystemPage/>}/>
      </Route>
    </Route>

    <Route path="*" element={<Navigate to="/" replace/>}/>
  </Routes>
}
