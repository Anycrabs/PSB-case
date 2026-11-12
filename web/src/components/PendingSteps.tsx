import type { PipelineStage } from '../api/client'
import { Icon } from './Icon'

// Этапы приходят из потока /chat/stream. Разбор вопроса бывает только в режиме full,
// поэтому показывается, только если бэкенд его прислал.
const STEPS: { key: string; label: string; stages: PipelineStage[] }[] = [
  { key: 'analyze', label: 'Разбираю вопрос', stages: ['analyze'] },
  { key: 'retrieve', label: 'Ищу в документах базы', stages: ['retrieve'] },
  { key: 'generate', label: 'Формирую ответ', stages: ['generate'] },
  { key: 'verify', label: 'Сверяю ссылки с документами', stages: ['review', 'verify'] },
]

export function PendingSteps({ stages }: { stages: PipelineStage[] }) {
  const steps = stages.includes('analyze') ? STEPS : STEPS.slice(1)
  const current = stages.length ? stages[stages.length - 1] : null
  const activeIndex = current ? steps.findIndex((s) => s.stages.includes(current)) : -1

  return (
    <div className="pending" aria-live="polite">
      {activeIndex === -1 && (
        <div className="step active">
          <span className="step-mark" />
          Отправляю вопрос
        </div>
      )}
      {steps.map((step, i) => (
        <div key={step.key} className={`step ${i < activeIndex ? 'done' : i === activeIndex ? 'active' : ''}`}>
          <span className="step-mark">{i < activeIndex ? <Icon name="check" size={10} /> : null}</span>
          {step.label}
        </div>
      ))}
    </div>
  )
}
