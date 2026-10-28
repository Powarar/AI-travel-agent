import { COMPONENT_LABEL } from '../../shared/lib/labels'
import type { SSEPayloads, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './TripAlerts.module.scss'

/** Объяснение агента и нарушения проверок: текст — подпись, числа — из состояния. */
export function TripAlerts({ state, explanation }: { state: TripState; explanation: SSEPayloads['answer_text'] | null }) {
  const violations = state.violations
  return (
    <>
      {violations.length > 0 && (
        <section className={`${styles.alerts} motion-fade-up`} aria-label="Проверки">
          {violations.map((v, i) => (
            <div key={`${v.code}-${i}`} className={`${styles.alert} ${v.severity === 'blocking' ? styles.blocking : ''}`.trim()}>
              <Icon name="alert" size={18} />
              <div>
                <strong>{v.severity === 'blocking' ? 'Не складывается' : 'Обратите внимание'} · {COMPONENT_LABEL[v.affected_component] ?? v.affected_component}</strong>
                <span>Ждали {v.expected ?? '—'}, вышло {v.actual ?? '—'}</span>
                <code>{v.code}</code>
              </div>
            </div>
          ))}
        </section>
      )}
      {explanation && (
        <figure className={`${styles.quote} motion-fade-up`}>
          <span className={styles.mark} aria-hidden="true">О!</span>
          <blockquote>{explanation.text}</blockquote>
          <figcaption>
            <Icon name={explanation.numbers_verified ? 'shield' : 'alert'} size={14} />
            {explanation.numbers_verified ? 'Числа в тексте сверены с планом' : 'Числа в тексте не сверены'}
          </figcaption>
        </figure>
      )}
    </>
  )
}
