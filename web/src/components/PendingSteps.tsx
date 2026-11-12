import { useEffect, useState } from 'react'
import { Icon } from './Icon'

// Этапы показываются по таймеру: бэк пока не сообщает реальный прогресс.
// Когда появится потоковый ответ (/chat/stream), этапы можно брать из него.
const STEPS = ['Разбираю вопрос', 'Ищу в документах базы', 'Формирую ответ', 'Сверяю ссылки с документами']
const STEP_MS = 900

export function PendingSteps() {
  const [step, setStep] = useState(0)

  useEffect(() => {
    if (step >= STEPS.length - 1) return
    const timer = window.setTimeout(() => setStep((s) => s + 1), STEP_MS)
    return () => window.clearTimeout(timer)
  }, [step])

  return (
    <div className="pending" aria-live="polite">
      {STEPS.map((label, i) => (
        <div key={label} className={`step ${i < step ? 'done' : i === step ? 'active' : ''}`}>
          <span className="step-mark">{i < step ? <Icon name="check" size={10} /> : null}</span>
          {label}
        </div>
      ))}
    </div>
  )
}
