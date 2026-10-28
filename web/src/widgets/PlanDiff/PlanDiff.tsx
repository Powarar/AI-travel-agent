import type { CSSProperties } from 'react'

import { COMPONENT_LABEL } from '../../shared/lib/labels'
import { formatMoney, fromMinor, groupLedger, toMinor } from '../../shared/lib/money'
import { componentLabel } from '../../shared/lib/plan'
import type { SSEPayloads, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './PlanDiff.module.scss'

interface Props {
  previous: TripState
  current: TripState
  update: SSEPayloads['plan_updated']
}

/** Что поменялось после правки: по событию plan_updated, подписи — из обеих версий состояния. */
export function PlanDiff({ previous, current, update }: Props) {
  const before = groupLedger(previous.chosen.items).total
  const after = groupLedger(current.chosen.items).total
  const delta = before && after && before.currency === after.currency
    ? fromMinor(toMinor(after.amount) - toMinor(before.amount), after.currency)
    : null
  const cheaper = delta?.amount.startsWith('-')
  return (
    <section className={`${styles.diff} motion-fade-up`} aria-labelledby="diff-title">
      <div className={styles.head}>
        <h3 id="diff-title"><Icon name="refresh" size={18} /> Что изменилось</h3>
        <span className={styles.versions}>v{previous.version} → v{current.version}</span>
      </div>
      <div className={styles.summary}>
        <span className={styles.changedPill}>изменено {update.changed_components.length}</span>
        <span className={styles.keptPill}>сохранено {update.kept_components.length}</span>
        {delta && delta.amount !== '0' && (
          <span className={`${styles.delta} ${cheaper ? styles.cheaper : styles.pricier}`}>
            {cheaper ? '' : '+'}{formatMoney(delta)}
          </span>
        )}
      </div>
      <ul className={styles.rows}>
        {update.changed_components.map((c, i) => (
          <li key={c} className={`${styles.row} ${styles.changed}`} style={{ '--i': i } as CSSProperties}>
            <span className={styles.label}>{COMPONENT_LABEL[c] ?? c}</span>
            <span className={styles.before}>{componentLabel(previous, c)}</span>
            <Icon name="arrow" size={16} className={styles.arrow} />
            <span className={styles.after}>{componentLabel(current, c)}</span>
          </li>
        ))}
        {update.kept_components.map((c) => (
          <li key={c} className={`${styles.row} ${styles.kept}`}>
            <span className={styles.label}>{COMPONENT_LABEL[c] ?? c}</span>
            <span className={styles.keptValue}><Icon name="check" size={14} /> {componentLabel(current, c)}</span>
          </li>
        ))}
      </ul>
      {before && after && (
        <p className={styles.totals}>Итого: {formatMoney(before)} → <b>{formatMoney(after)}</b></p>
      )}
    </section>
  )
}
