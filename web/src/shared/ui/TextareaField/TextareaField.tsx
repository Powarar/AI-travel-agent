import { useId, type TextareaHTMLAttributes } from 'react'

import styles from './TextareaField.module.scss'

type TextareaFieldProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label: string
  hint?: string
}

export function TextareaField({ hint, id, label, className = '', ...props }: TextareaFieldProps) {
  const generatedId = useId()
  const fieldId = id ?? generatedId

  return (
    <div className={`${styles.field} ${className}`.trim()}>
      <label className={styles.label} htmlFor={fieldId}>{label}</label>
      <textarea className={styles.textarea} id={fieldId} {...props} />
      {hint && <p className={styles.hint}>{hint}</p>}
    </div>
  )
}
