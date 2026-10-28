import type { ReactNode } from 'react'

import { OUTCOME_LABEL } from '../../shared/lib/labels'
import { groupLedger } from '../../shared/lib/money'
import { plural, summarize } from '../../shared/lib/plan'
import type { TripState } from '../../shared/types/contracts'
import { AnimatedMoney } from '../../shared/ui/AnimatedNumber/AnimatedMoney'
import { Icon, type IconName } from '../../shared/ui/Icon/Icon'
import { BudgetMeter } from '../BudgetMeter/BudgetMeter'

import styles from './TripSummary.module.scss'

function Fact({ icon, children }: { icon: IconName; children: ReactNode }) {
  return <span className={styles.fact}><Icon name={icon} size={16} />{children}</span>
}

/** Геро-карточка поездки: куда, когда, кто, сколько — всё из TripState. */
export function TripSummary({ state }: { state: TripState }) {
  const s = summarize(state)
  const { total } = groupLedger(state.chosen.items)
  const budget = state.request.budget.amount.value
  const ready = state.outcome === 'plan_ready'
  const hasPlan = Object.keys(state.chosen.component_refs).length > 0
  return (
    <section className={`${styles.hero} ${ready ? '' : styles.muted} motion-fade-up`} aria-labelledby="trip-title">
      <span className={styles.ring} aria-hidden="true" />
      <span className={styles.ringSmall} aria-hidden="true" />
      <div className={styles.top}>
        <span className={`${styles.outcome} ${styles[state.outcome ?? 'unknown'] ?? ''}`.trim()}>
          {state.outcome ? OUTCOME_LABEL[state.outcome] : 'Собираем план'}
        </span>
        <span className={styles.version}>версия {state.version}</span>
      </div>
      <div className={styles.main}>
        <div>
          {s.country && <span className={styles.country}><Icon name="pin" size={14} /> {s.country}</span>}
          <h2 id="trip-title" className={styles.title}>{s.destination ?? 'Подбираем направление'}</h2>
          {!ready && hasPlan && <p className={styles.note}>Показан прошлый план: новая правка в него не уложилась.</p>}
          <div className={styles.facts}>
            {s.dates && <Fact icon="calendar">{s.dates}</Fact>}
            {s.nights !== null && <Fact icon="hotel">{s.nights} {plural(s.nights, 'ночь', 'ночи', 'ночей')}</Fact>}
            {s.guests !== null && <Fact icon="users">{s.guests} {plural(s.guests, 'гость', 'гостя', 'гостей')}</Fact>}
            {s.pets && <Fact icon="paw">с питомцем</Fact>}
            {s.dayTemp && <Fact icon="sun">+{s.dayTemp} °C</Fact>}
            {s.flightHours && <Fact icon="plane">{s.flightHours} ч{s.origin ? ` из ${s.origin}` : ''}</Fact>}
          </div>
        </div>
        {total && (
          <div className={styles.price}>
            <span className={styles.priceLabel}>Итого за поездку</span>
            <AnimatedMoney value={total} className={styles.total} />
            {budget && <BudgetMeter total={total} budget={budget} />}
          </div>
        )}
      </div>
    </section>
  )
}
