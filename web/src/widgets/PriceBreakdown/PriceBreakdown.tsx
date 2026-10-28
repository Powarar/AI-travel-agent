import type { CSSProperties } from 'react'

import { KIND_LABEL } from '../../shared/lib/labels'
import { formatMoney, groupLedger, toMinor } from '../../shared/lib/money'
import { plural } from '../../shared/lib/plan'
import type { DataStatus, TripState } from '../../shared/types/contracts'
import { AnimatedMoney } from '../../shared/ui/AnimatedNumber/AnimatedMoney'
import { StatusBadge } from '../../shared/ui/StatusBadge/StatusBadge'

import styles from './PriceBreakdown.module.scss'

const ORDER = ['transport', 'lodging', 'transfer', 'activities']

/** Разбивка цены: строки BudgetLedger из состояния, доли — в копейках через BigInt. */
export function PriceBreakdown({ state }: { state: TripState }) {
  const { groups, total } = groupLedger(state.chosen.items)
  if (!total) return null
  const all = toMinor(total.amount)
  const sorted = [...groups].sort((a, b) => ORDER.indexOf(a.kind) - ORDER.indexOf(b.kind))
  const share = (amount: string) => (all > 0n ? Number((toMinor(amount) * 10000n) / all) / 100 : 0)
  return (
    <section className={styles.card} aria-labelledby="price-title">
      <div className={styles.head}>
        <h3 id="price-title">Из чего цена</h3>
        <AnimatedMoney value={total} className={styles.total} />
      </div>
      <div className={styles.bar} aria-hidden="true">
        {sorted.map((g, i) => (
          <span key={g.kind} className={styles[`seg_${g.kind}`] ?? styles.seg_activities}
            style={{ width: `${share(g.total.amount)}%`, '--i': i } as CSSProperties} />
        ))}
      </div>
      <ul className={styles.rows}>
        {sorted.map((g, i) => {
          const statuses = [...new Set(g.lines.map((l) => l.data_status))] as DataStatus[]
          return (
            <li key={g.kind} className="motion-fade-up" style={{ '--i': i } as CSSProperties}>
              <span className={`${styles.swatch} ${styles[`seg_${g.kind}`] ?? styles.seg_activities}`} />
              <span className={styles.name}>
                {KIND_LABEL[g.kind] ?? g.kind}
                <small>{g.lines.length} {plural(g.lines.length, 'строка', 'строки', 'строк')} · {share(g.total.amount).toFixed(0)}%</small>
              </span>
              <span className={styles.badges}>{statuses.map((s) => <StatusBadge key={s} status={s} />)}</span>
              <b className={styles.amount}>{formatMoney(g.total)}</b>
            </li>
          )
        })}
      </ul>
      <p className={styles.foot}>Суммы — строки учёта из состояния плана, а не числа из текста.</p>
    </section>
  )
}
