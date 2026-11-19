import { Icon } from '../../shared/ui/Icon/Icon'
import { useAuth } from './useAuth'

import styles from './AccountControl.module.scss'

/** Email вошедшего и выход. В Mini App не используется: там вход через Telegram. */
export function AccountControl() {
  const { user, logout } = useAuth()
  if (!user) return null
  return (
    <div className={styles.account}>
      <span className={styles.email} title={user.email}>{user.email}</span>
      <button type="button" className={styles.logout} onClick={() => void logout()} aria-label="Выйти из аккаунта">
        <Icon name="logout" size={16} /> <span>Выйти</span>
      </button>
    </div>
  )
}