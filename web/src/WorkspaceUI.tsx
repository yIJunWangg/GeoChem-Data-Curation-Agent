import {
  ChevronLeft,
  ChevronRight,
  Maximize2,
  Minimize2,
  PanelRightClose,
  type LucideIcon,
} from 'lucide-react'
import {
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from 'react'

export type PageCommandBarProps = {
  title?: ReactNode
  description?: ReactNode
  leading?: ReactNode
  children?: ReactNode
  status?: ReactNode
  className?: string
}

export function PageCommandBar({
  title,
  description,
  leading,
  children,
  status,
  className = '',
}: PageCommandBarProps) {
  return <header className={`page-command-bar ${className}`.trim()}>
    {(leading || title || description) && <div className="page-command-context">
      {leading}
      <div className="page-command-copy">
        {title && <strong>{title}</strong>}
        {description && <small>{description}</small>}
      </div>
    </div>}
    <div className="page-command-actions">{status}{children}</div>
  </header>
}

export type SplitWorkspaceProps = {
  primary: ReactNode
  secondary?: ReactNode
  tertiary?: ReactNode
  className?: string
  primaryMin?: number
  secondaryWidth?: number
  tertiaryWidth?: number
  secondaryCollapsed?: boolean
  tertiaryCollapsed?: boolean
}

export function SplitWorkspace({
  primary,
  secondary,
  tertiary,
  className = '',
  primaryMin = 420,
  secondaryWidth = 240,
  tertiaryWidth = 340,
  secondaryCollapsed = false,
  tertiaryCollapsed = false,
}: SplitWorkspaceProps) {
  const columns = [
    !secondaryCollapsed && secondary ? `${secondaryWidth}px` : '',
    `minmax(${primaryMin}px, 1fr)`,
    !tertiaryCollapsed && tertiary ? `${tertiaryWidth}px` : '',
  ].filter(Boolean).join(' ')
  return <div
    className={`split-workspace ${className}`.trim()}
    style={{ '--split-columns': columns } as CSSProperties}
  >
    {!secondaryCollapsed && secondary && <aside className="split-workspace-secondary">{secondary}</aside>}
    <main className="split-workspace-primary">{primary}</main>
    {!tertiaryCollapsed && tertiary && <aside className="split-workspace-tertiary">{tertiary}</aside>}
  </div>
}

export type ContextDrawerProps = {
  open: boolean
  title: ReactNode
  children: ReactNode
  onClose: () => void
  actions?: ReactNode
  className?: string
  storageKey?: string
  defaultWidth?: number
  minWidth?: number
  maxWidth?: number
  focused?: boolean
  onFocusedChange?: (focused: boolean) => void
}

export function ContextDrawer({
  open,
  title,
  children,
  onClose,
  actions,
  className = '',
  storageKey = 'geochem.context-drawer-width',
  defaultWidth = 680,
  minWidth = 420,
  maxWidth = 1080,
  focused = false,
  onFocusedChange,
}: ContextDrawerProps) {
  const drawerRef = useRef<HTMLElement>(null)
  const [width, setWidth] = useState(() => {
    const stored = Number(window.localStorage.getItem(storageKey))
    return Number.isFinite(stored) && stored >= minWidth ? stored : defaultWidth
  })

  useEffect(() => {
    window.localStorage.setItem(storageKey, String(Math.round(width)))
  }, [storageKey, width])

  if (!open) return null

  const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const move = (moveEvent: PointerEvent) => {
      const viewportMaximum = Math.max(minWidth, Math.min(maxWidth, window.innerWidth - 320))
      setWidth(Math.max(minWidth, Math.min(viewportMaximum, window.innerWidth - moveEvent.clientX)))
    }
    const stop = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      document.body.classList.remove('is-resizing-panels')
    }
    document.body.classList.add('is-resizing-panels')
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
  }

  return <>
    <button
      className="context-drawer-resizer"
      aria-label="调整上下文面板宽度"
      title="拖拽调整宽度"
      onPointerDown={startResize}
    ><span/></button>
    <aside
      ref={drawerRef}
      className={`context-drawer ${focused ? 'focused' : ''} ${className}`.trim()}
      style={{ '--context-drawer-width': `${width}px` } as CSSProperties}
    >
      <div className="context-drawer-heading">
        <strong>{title}</strong>
        <div className="context-drawer-actions">
          {actions}
          {onFocusedChange && <button
            className="icon-button"
            title={focused ? '退出聚焦' : '聚焦面板'}
            aria-label={focused ? '退出聚焦' : '聚焦面板'}
            onClick={() => onFocusedChange(!focused)}
          >{focused ? <Minimize2 size={16}/> : <Maximize2 size={16}/>}</button>}
          <button className="icon-button" title="关闭面板" aria-label="关闭面板" onClick={onClose}>
            <PanelRightClose size={17}/>
          </button>
        </div>
      </div>
      <div className="context-drawer-body">{children}</div>
    </aside>
  </>
}

export type CompactEmptyStateProps = {
  icon?: LucideIcon
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  className?: string
}

export function CompactEmptyState({
  icon: Icon,
  title,
  description,
  action,
  className = '',
}: CompactEmptyStateProps) {
  return <div className={`compact-empty-state ${className}`.trim()}>
    {Icon && <Icon size={22}/>}
    <div><strong>{title}</strong>{description && <small>{description}</small>}</div>
    {action && <div className="compact-empty-action">{action}</div>}
  </div>
}

export function CollapsibleRailButton({
  collapsed,
  onClick,
  label = '阶段栏',
}: {
  collapsed: boolean
  onClick: () => void
  label?: string
}) {
  return <button
    className="rail-toggle"
    title={collapsed ? `展开${label}` : `收起${label}`}
    aria-label={collapsed ? `展开${label}` : `收起${label}`}
    onClick={onClick}
  >
    {collapsed ? <ChevronRight size={17}/> : <ChevronLeft size={17}/>}
    {!collapsed && <span>收起</span>}
  </button>
}
