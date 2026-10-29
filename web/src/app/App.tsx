import { useState } from 'react'

import { useTripSession } from '../features/trip-session/useTripSession'
import { HomePage } from '../pages/HomePage/HomePage'
import { MiniApp } from '../pages/MiniApp/MiniApp'
import { PlanningPage } from '../pages/PlanningPage/PlanningPage'
import { isMiniApp } from '../shared/lib/telegram'

export default function App() {
  const session = useTripSession()
  const [initial, setInitial] = useState<string | null>(null)
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
