import type { CheckoutResult, TripState } from '../../shared/types/contracts'
import { Button } from '../../shared/ui/Button/Button'

import styles from './CheckoutPanel.module.scss'

interface Props {
  state: TripState
  order: CheckoutResult | null
  busy: boolean
  onCheckout: () => void
}

export function CheckoutPanel({ state, order, busy, onCheckout }: Props) {
  const ready = state.outcome === 'plan_ready' && !state.violations.some((v) => v.severity === 'blocking')
  return (
    <section className={styles.panel} aria-labelledby="checkout-title">
      <div className={styles.banner} role="note">Симуляция: деньги не списываются, бронь не создаётся.</div>
      <h3 id="checkout-title">Оформление</h3>
      {order ? (
        <p className={styles.done} role="status">
          Заказ <b>{order.order_id}</b> оформлен в симуляции
          {order.idempotent_replay && ' — повторный запрос вернул тот же заказ'}.
        </p>
      ) : (
        <p className={styles.hint}>
          {ready ? 'План собран без блокирующих нарушений.' : 'Оформить можно только готовый план без блокирующих нарушений.'}
        </p>
      )}
      <Button onClick={onCheckout} disabled={!ready || busy} variant={order ? 'secondary' : 'primary'}>
        {order ? 'Оформить ещё раз (тот же ключ)' : 'Оформить поездку'}
      </Button>
    </section>
  )
}
