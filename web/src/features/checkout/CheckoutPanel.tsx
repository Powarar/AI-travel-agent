import type { CSSProperties } from 'react'

import { formatMoney, groupLedger } from '../../shared/lib/money'
import type { CheckoutResult, TripState } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './CheckoutPanel.module.scss'

interface Props {
  state: TripState
  order: CheckoutResult | null
  busy: boolean
  onCheckout: () => void
}

const CONFETTI = Array.from({ length: 14 }, (_, i) => i)

export function CheckoutPanel({ state, order, busy, onCheckout }: Props) {
  const ready = state.outcome === 'plan_ready' && !state.violations.some((v) => v.severity === 'blocking')
  const total = groupLedger(state.chosen.items).total
  return (
    <section className={`${styles.panel} ${order ? styles.done : ''}`.trim()} aria-labelledby="checkout-title">
      <span className={styles.ribbon} role="note"><Icon name="shield" size={13} /> Симуляция: деньги не списываются</span>
      {order ? (
        <div className={styles.success} role="status">
          <div className={styles.burst} aria-hidden="true">
            {CONFETTI.map((i) => <i key={i} style={{ '--i': i } as CSSProperties} />)}
          </div>
          <svg className={styles.check} viewBox="0 0 52 52" aria-hidden="true">
            <circle cx="26" cy="26" r="23" />
            <path d="M15 27l7 7 15-16" />
          </svg>
          <h3 id="checkout-title">Поездка оформлена!</h3>
          <p>Заказ <b>{order.order_id}</b>{order.idempotent_replay ? ' — повторное нажатие вернуло тот же заказ' : ''}</p>
          <button type="button" className={styles.secondary} onClick={onCheckout} disabled={busy}>
            Нажать ещё раз — заказ не задвоится
          </button>
        </div>
      ) : (
        <div className={styles.cta}>
          <div>
            <h3 id="checkout-title">Всё готово к оформлению</h3>
            <p>{ready ? 'План собран без блокирующих нарушений.' : 'Оформить можно только готовый план без блокирующих нарушений.'}</p>
          </div>
          <button type="button" className={styles.primary} onClick={onCheckout} disabled={!ready || busy}>
            {busy ? 'Оформляем…' : <>Оформить{total ? ` за ${formatMoney(total)}` : ''} <Icon name="arrow" size={18} /></>}
          </button>
        </div>
      )}
    </section>
  )
}
