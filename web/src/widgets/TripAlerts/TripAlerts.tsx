import { COMPONENT_LABEL } from '../../shared/lib/labels'
import type { SSEPayloads, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './TripAlerts.module.scss'

function capitalize(text: string) {
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Ответ агента и то, что мешает плану, — простыми словами. */
export function TripAlerts({ state, explanation }: { state: TripState; explanation: SSEPayloads['answer_text'] | null }) {
  const blocking = state.violations.filter((v) => v.severity === 'blocking')
  const notes = state.violations.filter((v) => v.severity !== 'blocking')
  return (
    <>
      {blocking.length > 0 && (
        <section className={`${styles.alert} ${styles.blocking} motion-fade-up`} aria-label="Что мешает плану">
          <Icon name="alert" size={20} />
          <div>
            <strong>Так поездка не складывается</strong>
            <ul>
              {blocking.map((v, i) => (
                <li key={i}>
                  {COMPONENT_LABEL[v.affected_component] ?? capitalize(v.affected_component)}: нужно {v.expected ?? '—'}, а нашлось {v.actual ?? '—'}
                </li>
              ))}
            </ul>
            <span className={styles.hint}>Попробуйте изменить даты, бюджет или направление в чате.</span>
          </div>
        </section>
      )}
      {notes.length > 0 && blocking.length === 0 && (
        <section className={`${styles.alert} motion-fade-up`} aria-label="Обратите внимание">
          <Icon name="alert" size={20} />
          <div>
            <strong>Обратите внимание</strong>
            <ul>
              {notes.map((v, i) => (
                <li key={i}>{COMPONENT_LABEL[v.affected_component] ?? capitalize(v.affected_component)}: {v.actual ?? v.expected ?? ''}</li>
              ))}
            </ul>
          </div>
        </section>
      )}
      {explanation && (
        <figure className={`${styles.quote} motion-fade-up`}>
          <span className={styles.mark} aria-hidden="true">О!</span>
          <blockquote>{explanation.text}</blockquote>
        </figure>
      )}
    </>
  )
}
