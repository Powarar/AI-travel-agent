import { useState } from 'react'

import { useOrders } from '../../entities/order/orders'
import type { TripSession } from '../../features/trip-session/useTripSession'
import { TabBar, type Tab } from '../../widgets/TabBar/TabBar'
import { PlanScreen } from './screens/PlanScreen'
import { ProfileScreen } from './screens/ProfileScreen'
import { TripsScreen } from './screens/TripsScreen'

import styles from './MiniApp.module.scss'

export type Screen = 'plan' | 'trips' | 'profile'

/** Telegram Mini App: без лендинга, экраны переключаются нижним меню. */
export function MiniApp({ session }: { session: TripSession }) {
  const [screen, setScreen] = useState<Screen>('plan')
  const { items: orders, error: ordersError } = useOrders()

  const tabs: Tab<Screen>[] = [
    { id: 'plan', label: 'Подбор', icon: 'sparkle' },
    { id: 'trips', label: 'Поездки', icon: 'ticket', badge: orders.length },
    { id: 'profile', label: 'Профиль', icon: 'user' },
  ]

  return (
    <div className={styles.app}>
      <span className={styles.blob} aria-hidden="true" />
      {/* Подбор остаётся смонтированным: переход в профиль не обрывает ход агента и не сбрасывает чат. */}
      <div className={styles.screen} hidden={screen !== 'plan'}>
        <PlanScreen session={session} />
      </div>
      {screen === 'trips' && (
        <div className={styles.screen}>
          <TripsScreen orders={orders} error={ordersError} session={session} onPlan={() => setScreen('plan')} />
        </div>
      )}
      {screen === 'profile' && (
        <div className={styles.screen}>
          <ProfileScreen orders={orders} session={session} onNavigate={setScreen} />
        </div>
      )}
      <TabBar tabs={tabs} active={screen} onChange={setScreen} />
    </div>
  )
}
