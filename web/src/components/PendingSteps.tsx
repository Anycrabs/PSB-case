import { useEffect, useState } from 'react'

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
    <ol className="steps" aria-live="polite">
      {STEPS.map((label, i) => (
        <li key={label} className={i < step ? 'done' : i === step ? 'now' : ''}>
          <span className="dot" aria-hidden="true" />
          {label}
        </li>
      ))}
    </ol>
  )
}
