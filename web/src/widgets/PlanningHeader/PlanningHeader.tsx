import { AccountControl } from '../../features/auth/AccountControl'
import { BrandLockup } from '../../shared/ui/BrandLockup/BrandLockup'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './PlanningHeader.module.scss'

export function PlanningHeader({ onNewTrip }: { onNewTrip: () => void }) {
  return (
    <header className={styles.header}>
      <BrandLockup />
      <div className={styles.actions}>
        <AccountControl />
        <button type="button" className={styles.newTrip} onClick={onNewTrip} aria-label="Новая поездка">
          <Icon name="refresh" size={16} /> <span>Новая поездка</span>
        </button>
      </div>
    </header>
  )
}
