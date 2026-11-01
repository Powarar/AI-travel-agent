import styles from './TypingDots.module.scss'

export function TypingDots({ label = 'Агент думает' }: { label?: string }) {
  return (
    <span className={styles.dots} role="status" aria-label={label}>
      <i /><i /><i />
    </span>
  )
}
