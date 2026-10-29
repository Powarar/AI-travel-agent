import { useEffect, useRef, useState } from 'react'

import { useTripSession } from '../features/trip-session/useTripSession'
import { HomePage } from '../pages/HomePage/HomePage'
import { MiniApp } from '../pages/MiniApp/MiniApp'
import { PlanningPage } from '../pages/PlanningPage/PlanningPage'
import { isMiniApp } from '../shared/lib/telegram'

export default function App() {
  const session = useTripSession()
  const [initial, setInitial] = useState<string | null>(null)
  const live = useRef(session)
  live.current = session
  useEffect(() => {
    const saved = localStorage.getItem('okuda.session') ?? ''
    if (!new URLSearchParams(location.search).has('record-sochi') && !['8a2605de-ef38-4d22-a675-891dfa74e521', '56997725-65cd-4f57-8b3e-9ca681fcd062', '0bf7f886-ca6b-49f2-a219-34eb2d36ae58'].some(id => saved.includes(id))) return
    let step = 0
    let next = Date.now() + 5000
    const timer = window.setInterval(() => {
      const s = live.current
      if (Date.now() < next || s.busy) return
      next = Date.now() + 5000
      if (step === 0) {
        s.reset()
        setInitial(null)
        window.scrollTo({ top: 0, behavior: 'smooth' })
        step = -1
      } else if (step === -1) {
        setInitial('Хочу в Сочи с 14 по 21 ноября вдвоём, бюджет 200к, атмосферный отель у моря')
        window.scrollTo({ top: 0, behavior: 'smooth' })
        step = 1
      } else if (s.question?.field === 'origin') {
        void s.answer('origin', 'Москва')
      } else if (step === 1 && s.state?.outcome === 'plan_ready') {
        step = 2
        window.scrollTo({ top: 450, behavior: 'smooth' })
      } else if (step === 2) {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        void s.send('Нас будет трое')
        step = 3
      } else if (step === 3 && s.state?.request.party.adults.value === 3) {
        window.scrollTo({ top: 400, behavior: 'smooth' })
        step = 4
      } else if (step === 4) {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        void s.send('Снизим бюджет до 155000 рублей')
        step = 5
      } else if (step === 5 && s.state?.request.budget.amount.value?.amount === '155000') {
        window.scrollTo({ top: 650, behavior: 'smooth' })
        step = 6
      } else if (step === 6) {
        window.scrollTo({ top: 0, behavior: 'smooth' })
        window.clearInterval(timer)
      }
    }, 500)
    return () => window.clearInterval(timer)
  }, [])
  // Внутри Telegram лендинг не нужен: сразу приложение с нижним меню.
  if (isMiniApp) return <MiniApp session={session} />

  // Сохранённый trip открывает экран подбора сразу: прерванный ход продолжается после перезагрузки.
  const planning = initial !== null || session.tripId !== null

  if (!planning) return <HomePage onStart={setInitial} />
  return (
    <PlanningPage
      session={session}
      initialMessage={initial}
      onExit={() => {
        session.reset()
        setInitial(null)
      }}
    />
  )
}
