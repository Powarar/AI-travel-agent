import type { DataStatus } from '../../types/contracts'

import styles from './StatusBadge.module.scss'

const LABEL: Record<DataStatus, string> = {
  verified: 'подтверждено',
  partially_verified: 'частично подтверждено',
  real: 'из источника',
  stale: 'устарело',
  unknown: 'неизвестно',
  synthetic: 'синтетика',
}

export function StatusBadge({ status, prefix }: { status: DataStatus; prefix?: string }) {
  return (
    <span className={`${styles.badge} ${styles[status] ?? ''}`.trim()} title={`data_status: ${status}`}>
      {prefix && <b>{prefix}</b>} {LABEL[status] ?? status}
    </span>
  )
}
