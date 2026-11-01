import type { ButtonHTMLAttributes, ReactNode } from 'react'

import styles from './Button.module.scss'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode
  variant?: 'primary' | 'secondary'
}

export function Button({ children, className = '', variant = 'primary', ...props }: ButtonProps) {
  return (
    <button className={`${styles.button} ${styles[variant]} ${className}`.trim()} {...props}>
      {children}
    </button>
  )
}
