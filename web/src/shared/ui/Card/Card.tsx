import type { HTMLAttributes, ReactNode } from 'react'

import styles from './Card.module.scss'

type CardProps = HTMLAttributes<HTMLElement> & {
  children: ReactNode
  as?: 'article' | 'section' | 'div'
}

export function Card({ as: Element = 'div', children, className = '', ...props }: CardProps) {
  return (
    <Element className={`${styles.card} ${className}`.trim()} {...props}>
      {children}
    </Element>
  )
}
