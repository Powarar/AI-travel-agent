import type { Outcome, SSEPayloads, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './TripAlerts.module.scss'

// Запасное объяснение для нештатных исходов: если агент не прислал текст, экран всё равно
// честно говорит, что произошло, а не показывает пустую карточку.
const NOTICE: Partial<Record<Outcome, { title: string; hint: string; blocking?: boolean }>> = {
  infeasible: {
    title: 'Так поездка не складывается',
    hint: 'Измените даты, бюджет или направление в чате — агент пересоберёт план.',
    blocking: true,
  },
  unsupported: {
    title: 'Этого мы не делаем',
    hint: 'Агент не берётся за такой запрос. Опишите поездку иначе: другое направление, даты или формат.',
  },
  best_effort: {
    title: 'Лучший найденный вариант',
    hint: 'Выполнить все условия не удалось — показан ближайший допустимый план.',
  },
  degraded: {
    title: 'План на устаревших данных',
    hint: 'Часть данных давно не обновлялась. Перед покупкой проверьте цены и наличие.',
  },
}

/** Ответ агента; если план не сложился — заметная плашка, причины в самом ответе. */
export function TripAlerts({ state, explanation }: { state: TripState; explanation: SSEPayloads['answer_text'] | null }) {
  const blocked = state.violations.some((v) => v.severity === 'blocking')
  const notice = state.outcome ? NOTICE[state.outcome] : undefined
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
      {!blocked && !explanation && notice && (
        <section className={`${styles.alert} ${notice.blocking ? styles.blocking : ''} motion-fade-up`.trim()} aria-label={notice.title}>
          <Icon name="alert" size={20} />
          <div>
            <strong>{notice.title}</strong>
            <span className={styles.hint}>{notice.hint}</span>
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