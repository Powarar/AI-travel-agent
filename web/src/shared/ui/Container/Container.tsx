import type { HTMLAttributes, ReactNode } from 'react'

import styles from './Container.module.scss'

type ContainerProps = HTMLAttributes<HTMLDivElement> & {
  children: ReactNode
}

export function Container({ children, className = '', ...props }: ContainerProps) {
  return (
    <div className={`${styles.container} ${className}`.trim()} {...props}>
      {children}
    </div>
  )
}
