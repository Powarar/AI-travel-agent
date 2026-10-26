import type { ButtonHTMLAttributes, ReactNode } from 'react'

import styles from './PromptChip.module.scss'

type PromptChipProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode
}

export function PromptChip({ children, className = '', ...props }: PromptChipProps) {
  return (
    <button className={`${styles.chip} ${className}`.trim()} type="button" {...props}>
      {children}
    </button>
  )
}
