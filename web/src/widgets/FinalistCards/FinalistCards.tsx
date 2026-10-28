import { formatMoney, groupLedger } from '../../shared/lib/money'
import { KIND_LABEL } from '../../shared/lib/labels'
import { finalists, monthName } from '../../shared/lib/plan'
import type { Candidate, TripState } from '../../shared/types/contracts'
import { StatusBadge } from '../../shared/ui/StatusBadge/StatusBadge'

import styles from './FinalistCards.module.scss'

export function FinalistCards({ state, streamed }: { state: TripState | null; streamed: Candidate[] }) {
  const items = finalists(state, streamed)
  if (!items.length) return null
  const chosen = state?.chosen.component_refs.destination
  const ledger = state ? groupLedger(state.chosen.items) : null
  // корзина не перезаписывается молча: при infeasible в ней остаётся прошлый выбор
  const current = state?.outcome === 'plan_ready'

  return (
    <section aria-labelledby="finalists-title">
      <h3 id="finalists-title" className={styles.title}>Финалисты</h3>
      <div className={styles.grid}>
        {items.slice(0, 3).map((c) => {
          const isChosen = c.candidate_id === chosen
          return (
            <article key={c.candidate_id} className={`${styles.card} ${isChosen ? styles.chosen : ''}`.trim()}>
              <header>
                <div>
                  <h4>{c.ref.name ?? c.candidate_id}</h4>
                  <span className={styles.country}>{c.ref.country}</span>
                </div>
                {isChosen && <span className={`${styles.pick} ${current ? '' : styles.stale}`.trim()}>{current ? 'в плане' : 'прошлый план'}</span>}
              </header>
              <p className={styles.why}>{c.ref.why}</p>
              <dl className={styles.facts}>
                <div><dt>Воздух</dt><dd>{c.ref.day_temp_c ? `+${c.ref.day_temp_c} °C в ${monthName(c.ref.climate_month)}` : 'нет данных'}</dd></div>
                <div><dt>Море</dt><dd>{c.ref.sea_temp_c ? `+${c.ref.sea_temp_c} °C` : 'нет данных'}</dd></div>
                <div><dt>Перелёт</dt><dd>{c.ref.flight_h ? `${c.ref.flight_h} ч` : 'нет данных'}</dd></div>
              </dl>
              {isChosen && ledger?.total ? (
                <ul className={styles.price}>
                  {ledger.groups.map((g) => (
                    <li key={g.kind}><span>{KIND_LABEL[g.kind] ?? g.kind}</span><b>{formatMoney(g.total)}</b></li>
                  ))}
                  <li className={styles.total}><span>Итого</span><b>{formatMoney(ledger.total)}</b></li>
                </ul>
              ) : c.ref.est_total ? (
                <p className={styles.estimate}>Оценка: {c.ref.est_total}</p>
              ) : (
                <p className={styles.estimate}>Стоимость не посчитана: в данных подбора нет итоговой цены.</p>
              )}
              <footer><StatusBadge status={c.data_status} prefix="направление" /></footer>
            </article>
          )
        })}
      </div>
    </section>
  )
}
