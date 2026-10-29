import type { ReactNode } from 'react'

import type { Order } from '../../../entities/order/orders'
import type { TripSession } from '../../../features/trip-session/useTripSession'
import { formatMoney, fromMinor, toMinor } from '../../../shared/lib/money'
import { plural } from '../../../shared/lib/plan'
import { telegramUser } from '../../../shared/lib/telegram'
import { Icon, type IconName } from '../../../shared/ui/Icon/Icon'
import type { Screen } from '../MiniApp'
import { formatOrderDate } from './TripsScreen'

import styles from './Screens.module.scss'

interface Props {
  orders: Order[]
  session: TripSession
  onNavigate: (screen: Screen) => void
}

function Row({ icon, title, hint, onClick }: {
  icon: IconName; title: string; hint?: ReactNode; onClick?: () => void
}) {
  const content = (
    <>
      <span className={styles.rowIcon}><Icon name={icon} size={18} /></span>
      <span className={styles.rowText}><b>{title}</b>{hint && <small>{hint}</small>}</span>
      {onClick && <Icon name="chevron" size={16} />}
    </>
  )
  return onClick
    ? <button type="button" className={styles.row} onClick={onClick}>{content}</button>
    : <div className={styles.row}>{content}</div>
}

function spent(orders: Order[]) {
  const totals = orders.flatMap((o) => (o.snapshot?.total ? [o.snapshot.total] : []))
  const currency = totals[0]?.currency
  if (!currency) return null
  const sum = totals.filter((t) => t.currency === currency).reduce((acc, t) => acc + toMinor(t.amount), 0n)
  return formatMoney(fromMinor(sum, currency))
}

export function ProfileScreen({ orders, session, onNavigate }: Props) {
  const user = telegramUser()
  const name = user ? [user.first_name, user.last_name].filter(Boolean).join(' ') : 'Путешественник'
  const nights = orders.reduce((acc, o) => acc + (o.snapshot?.nights ?? 0), 0)
  const places = new Set(orders.map((o) => o.snapshot?.destination).filter(Boolean)).size
  const last = orders[0]

  return (
    <>
      <section className={`${styles.profile} motion-fade-up`}>
        {user?.photo_url
          ? <img className={styles.avatar} src={user.photo_url} alt="" />
          : <span className={styles.avatar} aria-hidden="true">{name.charAt(0).toUpperCase()}</span>}
        <div>
          <h1>{name}</h1>
          <p>{user?.username ? `@${user.username}` : 'Профиль О! Куда'}</p>
        </div>
      </section>

      <section className={styles.stats} aria-label="Статистика">
        <div><b>{orders.length}</b><span>{plural(orders.length, 'поездка', 'поездки', 'поездок')}</span></div>
        <div><b>{places}</b><span>{plural(places, 'направление', 'направления', 'направлений')}</span></div>
        <div><b>{nights}</b><span>{plural(nights, 'ночь', 'ночи', 'ночей')} в пути</span></div>
      </section>

      <h2 className={styles.section}>Поездки</h2>
      <div className={styles.group}>
        <Row
          icon="ticket"
          title="Мои заказы"
          hint={orders.length ? `${orders.length} · потрачено ${spent(orders) ?? '—'}` : 'Пока пусто'}
          onClick={() => onNavigate('trips')}
        />
        {last && (
          <Row icon="calendar" title="Последний заказ" hint={`${last.snapshot?.destination ?? 'Поездка'} · ${formatOrderDate(last.created_at)}`} />
        )}
        <Row
          icon="sparkle"
          title={session.tripId ? 'Продолжить подбор' : 'Новый подбор'}
          hint={session.tripId ? 'Чат с агентом сохранён' : 'Опишите поездку своими словами'}
          onClick={() => onNavigate('plan')}
        />
      </div>
    </>
  )
}
