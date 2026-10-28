import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './PhaseStepper.module.scss'

const STEPS: { label: string; stages: string[] }[] = [
  { label: 'Запрос', stages: ['PARSE'] },
  { label: 'Уточнение', stages: ['CLARIFY'] },
  { label: 'Направления', stages: ['INSPIRE', 'DECIDE'] },
  { label: 'Поиск', stages: ['SEARCH'] },
  { label: 'Проверка', stages: ['COMPOSE', 'VALIDATE', 'REPAIR'] },
  { label: 'План', stages: ['SHORTLIST', 'COMPARE', 'EXPLAIN', 'PLAN', 'CHECKOUT'] },
]

/** Прогресс хода по фазам рантайма: заполненные — пройдены, текущая пульсирует. */
export function PhaseStepper({ phase, busy, done }: { phase: string | null; busy: boolean; done: boolean }) {
  const index = Math.max(0, STEPS.findIndex((s) => phase && s.stages.includes(phase)))
  const current = done ? STEPS.length - 1 : index
  const progress = (current / (STEPS.length - 1)) * 100
  return (
    <ol className={styles.stepper} aria-label="Ход подбора">
      <span className={styles.track} aria-hidden="true">
        <span className={styles.fill} style={{ width: `${progress}%` }} />
      </span>
      {STEPS.map((step, i) => {
        const state = i < current || (done && i === current) ? 'done' : i === current ? 'active' : 'todo'
        return (
          <li key={step.label} className={[styles.step, state !== 'todo' && styles[state], busy && state === 'active' && styles.live].filter(Boolean).join(' ')}
            aria-current={state === 'active' ? 'step' : undefined}>
            <span className={styles.dot}>{state === 'done' ? <Icon name="check" size={13} /> : i + 1}</span>
            <span className={styles.label}>{step.label}</span>
          </li>
        )
      })}
    </ol>
  )
}
