import styles from './BrandLockup.module.scss'

export function BrandLockup() {
  return (
    <a className={styles.lockup} href="#top" aria-label="О! Куда — на главную">
      <span className={styles.product}>О! Куда</span>
      <span className={styles.divider} aria-hidden="true" />
      <img className={styles.parentLogo} src="/icons/ostrovok-logo.svg" alt="Островок!" />
    </a>
  )
}
