import { useState } from 'react'

import { AuthLoading, AuthScreen } from '../features/auth/AuthScreen'
import { useAuth } from '../features/auth/useAuth'
import { useTripSession } from '../features/trip-session/useTripSession'
import { HomePage } from '../pages/HomePage/HomePage'
import { MiniApp } from '../pages/MiniApp/MiniApp'
import { PlanningPage } from '../pages/PlanningPage/PlanningPage'
import { api } from '../shared/api'
import { isMiniApp } from '../shared/lib/telegram'

export default function App() {
  // Внутри Telegram лендинг и формы входа не нужны: там подписанный initData.
  if (isMiniApp) return <MiniAppRoot />
  return <WebRoot />
}

function MiniAppRoot() {
  const session = useTripSession()
  return <MiniApp session={session} />
}

function WebRoot() {
  const auth = useAuth()
  // Офлайн-демо с записанными сценариями (VITE_API_MODE=mock) работает без backend и без входа.
  // В продакшене режим http, и эта ветка не активируется.
  if (api.mode === 'mock') return <PlannerRoot />
  if (auth.status === 'loading') return <AuthLoading />
  if (auth.status === 'anonymous') return <AuthScreen />
  return <PlannerRoot />
}

function PlannerRoot() {
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