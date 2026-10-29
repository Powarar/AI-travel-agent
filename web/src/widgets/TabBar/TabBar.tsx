import { haptic } from '../../shared/lib/telegram'
import { Icon, type IconName } from '../../shared/ui/Icon/Icon'

import styles from './TabBar.module.scss'

export interface Tab<T extends string> {
  id: T
  label: string
  icon: IconName
  badge?: number
}

interface Props<T extends string> {
  tabs: Tab<T>[]
  active: T
  onChange: (id: T) => void
}

/** Нижнее меню Mini App: переключает экраны без перезагрузки, как вкладки нативного приложения. */
export function TabBar<T extends string>({ tabs, active, onChange }: Props<T>) {
  return (
    <nav className={styles.bar} aria-label="Разделы">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          type="button"
          className={`${styles.tab} ${tab.id === active ? styles.active : ''}`.trim()}
          aria-current={tab.id === active ? 'page' : undefined}
          onClick={() => {
            if (tab.id === active) return
            haptic.select()
            onChange(tab.id)
          }}
        >
          <span className={styles.icon}>
            <Icon name={tab.icon} size={22} />
            {Boolean(tab.badge) && <span className={styles.badge}>{tab.badge}</span>}
          </span>
          <span className={styles.label}>{tab.label}</span>
        </button>
      ))}
    </nav>
  )
}
