import { Card } from '../../shared/ui/Card/Card'
import { Container } from '../../shared/ui/Container/Container'

import styles from './HowItWorksSection.module.scss'

const steps = [
  { number: '01', title: 'Расскажите о поездке', description: 'Напишите как другу: куда хочется, с кем едете, даты и бюджет.' },
  { number: '02', title: 'Сравните варианты', description: 'Соберём жильё, дорогу и впечатления в один понятный план.' },
  { number: '03', title: 'Уточните детали', description: 'Меняйте пожелания по ходу разговора, не начиная поиск заново.' },
]

export function HowItWorksSection() {
  return (
    <section id="how" className={styles.section} aria-labelledby="steps-title">
      <Container>
        <div className={styles.heading}>
          <span className={styles.kicker}>Просто и по делу</span>
          <h2 id="steps-title">От идеи до маршрута</h2>
        </div>
        <div className={styles.grid}>
          {steps.map((step) => (
            <Card as="article" className={styles.stepCard} key={step.number}>
              <span className={styles.stepNumber}>{step.number}</span>
              <h3>{step.title}</h3>
              <p>{step.description}</p>
            </Card>
          ))}
        </div>
      </Container>
    </section>
  )
}
