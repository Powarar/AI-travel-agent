import type { SSEPayloads, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './TripAlerts.module.scss'

/** Ответ агента; если план не сложился — заметная плашка, причины в самом ответе. */
export function TripAlerts({ state, explanation }: { state: TripState; explanation: SSEPayloads['answer_text'] | null }) {
  const blocked = state.violations.some((v) => v.severity === 'blocking')
  return (
    <>
      {blocked && (
        <section className={`${styles.alert} ${styles.blocking} motion-fade-up`} aria-label="План не сложился">
          <Icon name="alert" size={20} />
          <div>
            <strong>Так поездка не складывается</strong>
            <span className={styles.hint}>Попробуйте изменить даты, бюджет или направление в чате.</span>
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
