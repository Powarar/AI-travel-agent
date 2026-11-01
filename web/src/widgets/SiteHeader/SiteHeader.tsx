import { BrandLockup } from '../../shared/ui/BrandLockup/BrandLockup'
import { Container } from '../../shared/ui/Container/Container'

import styles from './SiteHeader.module.scss'

export function SiteHeader() {
  return (
    <header className={styles.header}>
      <Container className={styles.inner}>
        <BrandLockup />
        <nav className={styles.nav} aria-label="Основная навигация">
          <a href="#how">Как это работает</a>
          <a href="#planner">Начать планировать</a>
        </nav>
      </Container>
    </header>
  )
}
