import type { CSSProperties } from 'react'

import { finalists, monthName } from '../../shared/lib/plan'
import type { TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './FinalistCards.module.scss'

/** Три финалиста-направления с причиной выбора; выбранное в плане подсвечено. */
export function FinalistCards({ state }: { state: TripState }) {
  const items = finalists(state).slice(0, 3)
  if (items.length < 2) return null
  const chosen = state.chosen.component_refs.destination
  // корзина не перезаписывается молча: при infeasible в ней остаётся прошлый выбор
  const current = state.outcome === 'plan_ready'
  return (
    <section aria-labelledby="finalists-title">
      <h3 id="finalists-title" className={styles.title}>Финалисты <small>сравнили {items.length} направления</small></h3>
      <div className={styles.grid}>
        {items.map((c, i) => {
          const isChosen = c.candidate_id === chosen
          return (
            <article key={c.candidate_id} className={`${styles.card} ${isChosen ? styles.chosen : ''} motion-pop`}
              style={{ '--i': i } as CSSProperties}>
              <div className={styles.band} data-index={i}>
                <span className={styles.rank}>{i + 1}</span>
                {isChosen && <span className={`${styles.pick} ${current ? '' : styles.stale}`.trim()}>
                  <Icon name="check" size={12} /> {current ? 'в плане' : 'прошлый план'}
                </span>}
              </div>
              <div className={styles.body}>
                <h4>{c.ref.name ?? c.candidate_id}</h4>
                <span className={styles.country}>{c.ref.country}</span>
                <div className={styles.stats}>
                  <span><Icon name="sun" size={15} />{c.ref.day_temp_c ? `+${c.ref.day_temp_c}°` : '—'}<small>{monthName(c.ref.climate_month)}</small></span>
                  {c.ref.sea_temp_c && <span><Icon name="wave" size={15} />+{c.ref.sea_temp_c}°<small>море</small></span>}
                  <span><Icon name="plane" size={15} />{c.ref.flight_h ? `${c.ref.flight_h} ч` : '—'}<small>в пути</small></span>
                </div>
                {c.ref.why && <p className={styles.why}>{c.ref.why}</p>}
              </div>
            </article>
          )
        })}
      </div>
    </section>
  )
}
