import { useState, type ReactNode } from 'react'
import { ChevronDown, CircleStop } from 'lucide-react'

export type AgentDecisionOption = {
  id: string
  label: string
  description?: string
  recommended?: boolean
  disabled?: boolean
}

type Props = {
  question: string
  description?: string
  options: AgentDecisionOption[]
  selectionMode?: 'single' | 'multiple'
  selectedIds: string[]
  onSelectedIdsChange: (ids: string[]) => void
  primaryActionLabel?: string
  allowCustomInput?: boolean
  customInput?: string
  onCustomInputChange?: (value: string) => void
  busy?: boolean
  onContinue: () => void
  onStop?: () => void
  details?: ReactNode
  secondaryActions?: ReactNode
}

export function AgentDecisionPrompt({
  question,
  description,
  options,
  selectionMode = 'single',
  selectedIds,
  onSelectedIdsChange,
  primaryActionLabel = '继续',
  allowCustomInput = false,
  customInput = '',
  onCustomInputChange,
  busy = false,
  onContinue,
  onStop,
  details,
  secondaryActions,
}: Props) {
  const [customOpen, setCustomOpen] = useState(false)
  const choose = (id: string) => {
    if (selectionMode === 'single') {
      onSelectedIdsChange([id])
      return
    }
    onSelectedIdsChange(selectedIds.includes(id)
      ? selectedIds.filter((value) => value !== id)
      : [...selectedIds, id])
  }
  const canContinue = options.length === 0 || selectedIds.length > 0

  return <section className="agent-decision-prompt" aria-label={question}>
    <header>
      <strong>{question}</strong>
      {description && <p>{description}</p>}
    </header>
    {options.length > 0 && <div className="agent-decision-options" role={selectionMode === 'single' ? 'radiogroup' : 'group'}>
      {options.map((option) => {
        const selected = selectedIds.includes(option.id)
        return <button
          type="button"
          key={option.id}
          className={`agent-decision-option ${selected ? 'selected' : ''}`}
          role={selectionMode === 'single' ? 'radio' : 'checkbox'}
          aria-checked={selected}
          disabled={option.disabled || busy}
          onClick={() => choose(option.id)}
        >
          <span className="agent-decision-control" aria-hidden="true"/>
          <span className="agent-decision-copy">
            <span><strong>{option.label}</strong>{option.recommended && <em>推荐</em>}</span>
            {option.description && <small>{option.description}</small>}
          </span>
        </button>
      })}
    </div>}
    {details && <div className="agent-decision-details">{details}</div>}
    {allowCustomInput && <div className={`agent-decision-custom ${customOpen ? 'open' : ''}`}>
      <button type="button" onClick={() => setCustomOpen((value) => !value)} aria-expanded={customOpen}>
        补充说明或自定义要求 <ChevronDown size={15}/>
      </button>
      {customOpen && <textarea
        value={customInput}
        onChange={(event) => onCustomInputChange?.(event.target.value)}
        placeholder="输入需要 Agent 在继续时遵守的补充说明"
      />}
    </div>}
    <footer>
      <div className="agent-decision-secondary">{secondaryActions}</div>
      {onStop && <button type="button" className="agent-decision-stop" onClick={onStop} disabled={busy}><CircleStop size={15}/>停止任务</button>}
      <button type="button" className="primary-button agent-decision-continue" onClick={onContinue} disabled={!canContinue || busy}>
        {busy ? '正在提交…' : primaryActionLabel}
      </button>
    </footer>
  </section>
}
