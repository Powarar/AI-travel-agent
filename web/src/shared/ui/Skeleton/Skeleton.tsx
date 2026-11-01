import type { CSSProperties } from 'react'

import styles from './Skeleton.module.scss'

export function Skeleton({ height = 16, width = '100%', radius = 12, style }: {
  height?: number | string
  width?: number | string
  radius?: number
  style?: CSSProperties
}) {
  return <span className={styles.skeleton} style={{ height, width, borderRadius: radius, ...style }} aria-hidden="true" />
}
