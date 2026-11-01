import { Container } from '../../shared/ui/Container/Container'

import styles from './SiteFooter.module.scss'

export function SiteFooter() {
  return (
    <footer className={styles.footer}>
      <Container className={styles.inner}>
        <span className={styles.brand}>О! Куда</span>
        <span>Планируйте поездку с удовольствием</span>
      </Container>
    </footer>
  )
}
