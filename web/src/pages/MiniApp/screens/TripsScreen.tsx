import { useState, type CSSProperties } from 'react'

import { refreshOrders, type Order } from '../../../entities/order/orders'
import type { TripSession } from '../../../features/trip-session/useTripSession'
import { KIND_LABEL } from '../../../shared/lib/labels'
import { formatMoney } from '../../../shared/lib/money'
import { dateRange, plural, summarize } from '../../../shared/lib/plan'
import { Icon } from '../../../shared/ui/Icon/Icon'

import styles from './Screens.module.scss'

interface Props {
  orders: Order[]
  error: string | null
  session: TripSession
  onPlan: () => void
}

export function formatOrderDate(iso: string) {
  return new Date(iso).toLocaleDateString('ru-RU', { day: 'numeric', month: 'long', year: 'numeric' })
}

function OrderCard({ order, index }: { order: Order; index: number }) {
  const [open, setOpen] = useState(false)
  // snapshot пуст только у заказов, оформленных до того, как backend начал его сохранять.
  const s = order.snapshot
  const dates = s && dateRange(s.date_start, s.date_end)
  return (
    <article className={`${styles.order} motion-fade-up`} style={{ '--i': index } as CSSProperties}>
      <button type="button" className={styles.orderHead} onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={styles.orderIcon}><Icon name="plane" size={20} /></span>
        <span className={styles.orderMain}>
          <b>{s?.destination ?? 'Поездка'}</b>
          <small>{[dates, s?.country].filter(Boolean).join(' · ') || formatOrderDate(order.created_at)}</small>
        </span>
        <span className={styles.orderTotal}>
          {s?.total ? formatMoney(s.total) : '—'}
          <Icon name="chevron" size={16} className={open ? styles.chevronOpen : styles.chevron} />
        </span>
      </button>
      <div className={styles.orderMeta}>
        <span className={styles.status}><Icon name="check" size={14} /> Оформлена</span>
        {s?.nights != null && <span>{s.nights} {plural(s.nights, 'ночь', 'ночи', 'ночей')}</span>}
        {s?.guests != null && <span>{s.guests} {plural(s.guests, 'гость', 'гостя', 'гостей')}</span>}
        {s?.pets && <span>с питомцем</span>}
      </div>
      {open && (
        <div className={styles.orderBody}>
          <ul className={styles.lines}>
            {(s?.lines ?? []).map((g) => (
              <li key={g.kind}>
                <span>{KIND_LABEL[g.kind] ?? g.kind}{g.count > 1 && ` × ${g.count}`}</span>
                <b>{formatMoney(g.total)}</b>
              </li>
            ))}
          </ul>
          <p className={styles.orderFoot}>
            Заказ <b>{order.order_id}</b> · {formatOrderDate(order.created_at)}
            {s?.origin && <> · вылет: {s.origin}</>}
            {order.simulated && <> · демо-оформление</>}
          </p>
        </div>
      )}
    </article>
  )
}

/** Мои поездки: текущий подбор и оформленные заказы. */
export function TripsScreen({ orders, error, session, onPlan }: Props) {
  const draft = session.state && !session.order ? summarize(session.state) : null

  return (
    <>
      <header className={styles.bar}>
        <h1>Мои поездки</h1>
      </header>

      {draft && (
        <button type="button" className={styles.draft} onClick={onPlan}>
          <span className={styles.draftDot} aria-hidden="true" />
          <span className={styles.orderMain}>
            <small>Сейчас подбираем</small>
            <b>{draft.destination ?? 'Подбираем направление'}</b>
          </span>
          <Icon name="arrow" size={18} />
        </button>
      )}

      {error && (
        <p className={styles.error} role="alert">
          {error} <button type="button" onClick={() => void refreshOrders()}>Повторить</button>
        </p>
      )}

      {orders.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.emptyOrb} aria-hidden="true"><Icon name="ticket" size={34} /></div>
          <h2>Пока ни одной поездки</h2>
          <p>Оформленные поездки появятся здесь — с ценой по строкам и номером заказа.</p>
          <button type="button" className={styles.primary} onClick={onPlan}>
            Подобрать поездку <Icon name="arrow" size={18} />
          </button>
        </div>
      ) : (
        <div className={styles.list}>
          {orders.map((order, i) => <OrderCard key={order.order_id} order={order} index={i} />)}
        </div>
      )}
    </>
  )
}
