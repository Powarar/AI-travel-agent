import { BrandLockup } from '../../shared/ui/BrandLockup/BrandLockup'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './PlanningHeader.module.scss'

export function PlanningHeader({ mode, onNewTrip }: { mode: 'http' | 'mock'; onNewTrip: () => void }) {
  return (
    <header className={styles.header}>
      <BrandLockup />
      <div className={styles.actions}>
        <span className={`${styles.mode} ${mode === 'http' ? styles.live : ''}`.trim()}
          title={mode === 'mock' ? 'Ответы записаны прогоном настоящего агента на каталоге' : 'Ход исполняет агент на backend'}>
          <i aria-hidden="true" />
          {mode === 'mock' ? 'Демо · записанные ответы агента' : 'Live · агент'}
        </span>
        <button type="button" className={styles.newTrip} onClick={onNewTrip}>
          <Icon name="refresh" size={16} /> <span>Новая поездка</span>
        </button>
      </div>
    </header>
  )
}
