import { Container } from '../../shared/ui/Container/Container'

import styles from './HeroSection.module.scss'

export function HeroSection() {
  return (
    <section className={styles.hero} aria-labelledby="hero-title">
      <Container className={styles.inner}>
        <div className={styles.content}>
          <span className={styles.eyebrow}>Путешествие начинается с одного сообщения</span>
          <h1 id="hero-title">О! Куда <span>поедем?</span></h1>
          <p className={styles.lead}>Расскажите, какого отдыха хочется. Поможем превратить идею в удобный план поездки.</p>
          <a className={styles.link} href="#planner">Начать планировать <span aria-hidden="true">↗</span></a>
        </div>
        <div className={styles.art} aria-hidden="true">
          <div className={`${styles.ring} ${styles.outerRing}`} />
          <div className={`${styles.ring} ${styles.innerRing}`} />
          <span className={styles.exclamation}>!</span>
          <span className={styles.artNote}>Ваш следующий<br />О! момент</span>
        </div>
      </Container>
    </section>
  )
}
