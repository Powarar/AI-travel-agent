import { useState } from 'react'

import { useTripSession } from '../features/trip-session/useTripSession'
import { HomePage } from '../pages/HomePage/HomePage'
import { PlanningPage } from '../pages/PlanningPage/PlanningPage'

export default function App() {
  const session = useTripSession()
  const [initial, setInitial] = useState<string | null>(null)
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
