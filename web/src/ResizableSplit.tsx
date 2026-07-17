import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'

type Props = {
  left: ReactNode
  right: ReactNode
  storageKey: string
  className?: string
  initialPercent?: number
  minLeft?: number
  minRight?: number
}

const clamp = (value: number, minimum: number, maximum: number) => Math.max(minimum, Math.min(maximum, value))

export function ResizableSplit({
  left,
  right,
  storageKey,
  className = '',
  initialPercent = 60,
  minLeft = 420,
  minRight = 420,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null)
  const [percent, setPercent] = useState(() => {
    const saved = Number(window.localStorage.getItem(storageKey))
    return Number.isFinite(saved) && saved > 0 ? saved : initialPercent
  })

  useEffect(() => { window.localStorage.setItem(storageKey, String(percent)) }, [percent, storageKey])

  const startResize = (event: ReactPointerEvent<HTMLButtonElement>) => {
    event.preventDefault()
    const resize = (moveEvent: PointerEvent) => {
      const rect = rootRef.current?.getBoundingClientRect()
      if (!rect?.width) return
      const minimum = (minLeft / rect.width) * 100
      const maximum = 100 - (minRight / rect.width) * 100
      setPercent(clamp(((moveEvent.clientX - rect.left) / rect.width) * 100, minimum, Math.max(minimum, maximum)))
    }
    const stop = () => {
      window.removeEventListener('pointermove', resize)
      window.removeEventListener('pointerup', stop)
      document.body.classList.remove('is-resizing-panels')
    }
    document.body.classList.add('is-resizing-panels')
    window.addEventListener('pointermove', resize)
    window.addEventListener('pointerup', stop)
  }

  return <div ref={rootRef} className={`resizable-split ${className}`} style={{ gridTemplateColumns: `minmax(${minLeft}px, ${percent}fr) 8px minmax(${minRight}px, ${100 - percent}fr)` }}>
    <div className="resizable-pane">{left}</div>
    <button className="splitter-handle" aria-label="调整表格与 PDF 宽度" title="拖拽调整宽度" onPointerDown={startResize}><span/></button>
    <div className="resizable-pane">{right}</div>
  </div>
}
