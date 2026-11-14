import { useCallback, useSyncExternalStore } from 'react'

import { resetOrders } from '../../entities/order/orders'
import { getAuthState, login, logout as apiLogout, register, subscribeAuth } from '../../shared/api/auth'
import { clearPersistedSession } from '../trip-session/useTripSession'

export function useAuth() {
  const state = useSyncExternalStore(subscribeAuth, getAuthState)
  // Выход гасит и сохранённый ход с кэшем заказов: следующий вход не должен увидеть чужие данные.
  const logout = useCallback(async () => {
    clearPersistedSession()
    resetOrders()
    await apiLogout()
  }, [])
  return { ...state, login, register, logout }
}