import type { PipelineStage } from '../api/client'

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
  // Пока поток не прислал ни одного этапа, первым активен поиск
  const activeIndex = current ? Math.max(0, steps.findIndex((s) => s.stages.includes(current))) : 0

  return (
    <ol className="steps" aria-live="polite">
      {steps.map((step, i) => (
        <li key={step.key} className={i < activeIndex ? 'done' : i === activeIndex ? 'now' : ''}>
          <span className="dot" aria-hidden="true" />
          {step.label}
        </li>
      ))}
    </ol>
  )
}
