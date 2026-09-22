import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { UserManager, WebStorageStateStore, type User } from 'oidc-client-ts'
import { setApiAccessToken } from './api'
import type { Workspace } from './types'

export type AuthConfig = {
  enabled: boolean
  issuer_url: string
  client_id: string
  audience: string
  scopes: string
}

type AuthState = {
  enabled: boolean
  loading: boolean
  error: string
  authenticated: boolean
  user?: User
  username: string
  roles: string[]
  workspaces: Workspace[]
  currentWorkspace?: Workspace
  workspaceRole: string
  signIn: (returnUrl?: string) => Promise<void>
  signOut: () => Promise<void>
  enterPreview: (role: 'admin' | 'curator') => void
}

const AuthContext = createContext<AuthState>({
  enabled: false,
  loading: true,
  error: '',
  authenticated: false,
  username: '',
  roles: [],
  workspaces: [],
  workspaceRole: '',
  signIn: async () => undefined,
  signOut: async () => undefined,
  enterPreview: () => undefined,
})

const PREVIEW_ROLE_KEY = 'geochem.auth.preview-role'

type BackendIdentity = {
  workspaces?: Workspace[]
  current_workspace?: Workspace | null
  workspace_role?: string
}

function claimsRoles(user?: User): string[] {
  const profile = user?.profile as Record<string, any> | undefined
  const values = new Set<string>()
  const realmRoles = profile?.realm_access?.roles
  if (Array.isArray(realmRoles)) realmRoles.forEach((role) => values.add(String(role).replace(/^geochem-/, '')))
  const resources = profile?.resource_access
  if (resources && typeof resources === 'object') {
    Object.values(resources).forEach((entry: any) => {
      if (Array.isArray(entry?.roles)) entry.roles.forEach((role: unknown) => values.add(String(role).replace(/^geochem-/, '')))
    })
  }
  return [...values].filter((role) => ['admin', 'curator', 'reviewer', 'viewer'].includes(role)).sort()
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [config, setConfig] = useState<AuthConfig>()
  const [user, setUser] = useState<User>()
  const [backendVerified, setBackendVerified] = useState(false)
  const [backendIdentity, setBackendIdentity] = useState<BackendIdentity>()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [previewRole, setPreviewRole] = useState<'admin' | 'curator' | ''>(() => {
    const value = window.sessionStorage.getItem(PREVIEW_ROLE_KEY)
    return value === 'admin' || value === 'curator' ? value : ''
  })
  const started = useRef(false)
  const manager = useMemo(() => {
    if (!config?.enabled) return undefined
    return new UserManager({
      authority: config.issuer_url,
      client_id: config.client_id,
      redirect_uri: `${window.location.origin}/auth/callback`,
      silent_redirect_uri: `${window.location.origin}/auth/silent-callback`,
      post_logout_redirect_uri: window.location.origin,
      response_type: 'code',
      scope: config.scopes || 'openid profile email',
      automaticSilentRenew: true,
      monitorSession: false,
      loadUserInfo: true,
      userStore: new WebStorageStateStore({ store: window.localStorage }),
    })
  }, [config])

  useEffect(() => {
    let cancelled = false
    void fetch('/api/v1/auth/config')
      .then(async (response) => {
        if (!response.ok) throw new Error(await response.text())
        return response.json() as Promise<AuthConfig>
      })
      .then((value) => {
        if (cancelled) return
        setConfig(value)
        if (!value.enabled) {
          setApiAccessToken('')
          setBackendVerified(true)
          setBackendIdentity(undefined)
          setLoading(false)
        }
      })
      .catch((reason) => {
        if (cancelled) return
        setError(`无法读取登录配置：${String(reason)}`)
        setLoading(false)
      })
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    if (!manager || started.current) return
    started.current = true
    let cancelled = false
    const applyUser = async (value?: User | null) => {
      if (cancelled) return
      const next = value && !value.expired ? value : undefined
      setUser(next)
      setApiAccessToken(next?.access_token || '')
      setBackendVerified(false)
      setBackendIdentity(undefined)
      if (!next) {
        setLoading(false)
        return
      }
      setLoading(true)
      try {
        const response = await fetch('/api/v1/auth/me', {
          headers: { Authorization: `Bearer ${next.access_token}` },
        })
        if (!response.ok) {
          const body = await response.text()
          let detail = body
          try {
            const parsed = JSON.parse(body)
            detail = typeof parsed.detail === 'string' ? parsed.detail : body
          } catch {
            detail = body
          }
          throw new Error(detail || `HTTP ${response.status}`)
        }
        const identity = await response.json() as BackendIdentity
        if (!cancelled) {
          setBackendVerified(true)
          setBackendIdentity(identity)
          setError('')
        }
      } catch (reason) {
        if (!cancelled) {
          setError(`后端登录验证失败：${reason instanceof Error ? reason.message : String(reason)}`)
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    const load = async () => {
      try {
        if (window.location.pathname === '/auth/silent-callback') {
          await manager.signinSilentCallback()
          return
        }
        if (window.location.pathname === '/auth/callback') {
          const callbackUser = await manager.signinRedirectCallback()
          await applyUser(callbackUser)
          const returnUrl = typeof callbackUser.state === 'object' && callbackUser.state
            ? String((callbackUser.state as Record<string, unknown>).returnUrl || '/')
            : '/'
          window.history.replaceState({}, '', returnUrl.startsWith('/') ? returnUrl : '/')
          return
        }
        const current = await manager.getUser()
        if (current && !current.expired) {
          await applyUser(current)
          return
        }
        await applyUser(undefined)
      } catch (reason) {
        if (!cancelled) {
          setError(`登录失败：${reason instanceof Error ? reason.message : String(reason)}`)
          setLoading(false)
        }
      }
    }
    const onLoaded = (value: User) => { void applyUser(value) }
    const onUnloaded = () => { void applyUser(undefined) }
    const onExpired = () => {
      void manager.signinSilent().then((value) => applyUser(value)).catch(() => manager.signinRedirect())
    }
    manager.events.addUserLoaded(onLoaded)
    manager.events.addUserUnloaded(onUnloaded)
    manager.events.addAccessTokenExpired(onExpired)
    void load()
    return () => {
      cancelled = true
      manager.events.removeUserLoaded(onLoaded)
      manager.events.removeUserUnloaded(onUnloaded)
      manager.events.removeAccessTokenExpired(onExpired)
    }
  }, [manager])

  const value = useMemo<AuthState>(() => ({
    enabled: Boolean(config?.enabled),
    loading,
    error,
    authenticated: config?.enabled ? Boolean(user && backendVerified) : Boolean(previewRole),
    user,
    username: config?.enabled
      ? String(user?.profile?.preferred_username || user?.profile?.name || user?.profile?.email || '')
      : previewRole === 'admin' ? 'Local Administrator' : previewRole === 'curator' ? 'Local Researcher' : '',
    roles: config?.enabled ? claimsRoles(user) : previewRole === 'admin' ? ['admin'] : previewRole === 'curator' ? ['curator'] : [],
    workspaces: config?.enabled ? backendIdentity?.workspaces || [] : [],
    currentWorkspace: config?.enabled ? backendIdentity?.current_workspace || undefined : undefined,
    workspaceRole: config?.enabled
      ? String(backendIdentity?.workspace_role || '')
      : previewRole === 'admin' ? 'owner' : previewRole === 'curator' ? 'curator' : '',
    signIn: async (returnUrl = `${window.location.pathname}${window.location.search}${window.location.hash}`) => {
      if (!manager) return
      await manager.signinRedirect({
        state: { returnUrl },
      })
    },
    signOut: async () => {
      setApiAccessToken('')
      setBackendVerified(false)
      setBackendIdentity(undefined)
      if (manager) {
        await manager.signoutRedirect()
        return
      }
      window.sessionStorage.removeItem(PREVIEW_ROLE_KEY)
      setPreviewRole('')
    },
    enterPreview: (role) => {
      window.sessionStorage.setItem(PREVIEW_ROLE_KEY, role)
      setPreviewRole(role)
    },
  }), [backendIdentity, backendVerified, config?.enabled, error, loading, manager, previewRole, user])

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth() {
  return useContext(AuthContext)
}
