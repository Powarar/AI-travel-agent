import { formatMoney, fromMinor, toMinor } from '../../shared/lib/money'
import type { Money } from '../../shared/types/contracts'

import styles from './BudgetMeter.module.scss'

/** Шкала бюджета: доля потраченного считается в копейках (BigInt), не во float. */
export function BudgetMeter({ total, budget }: { total: Money; budget: Money }) {
  const spent = toMinor(total.amount)
  const limit = toMinor(budget.amount)
  if (limit <= 0n || total.currency !== budget.currency) return null
  const permille = Number((spent * 1000n) / limit)
  const over = spent > limit
  const rest = fromMinor(limit - spent, total.currency)
  return (
    <div className={`${styles.meter} ${over ? styles.over : ''}`.trim()}>
      <div className={styles.labels}>
        <span>Бюджет {formatMoney(budget)}</span>
        <b>{over ? `сверх ${formatMoney(fromMinor(spent - limit, total.currency))}` : `остаток ${formatMoney(rest)}`}</b>
      </div>
      <div className={styles.bar} role="meter" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.min(100, Math.round(permille / 10))} aria-label="Доля бюджета">
        <span style={{ width: `${Math.min(100, permille / 10)}%` }} />
      </div>
      <small>{(permille / 10).toFixed(1).replace('.', ',')}% бюджета</small>
    </div>
  )
}
