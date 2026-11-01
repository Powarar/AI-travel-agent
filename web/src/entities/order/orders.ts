import { useEffect, useSyncExternalStore } from 'react'

import { api } from '../../shared/api'
import type { OrderOut } from '../../shared/types/contracts'

// Оформленные поездки пользователя живут на backend (GET /v1/orders): в Telegram заказы
// привязаны к пользователю по initData и видны с любого устройства. Здесь — только кэш экрана.

export type Order = OrderOut

interface OrdersState {
  items: Order[]
  loading: boolean
  error: string | null
}

const listeners = new Set<() => void>()
let snapshot: OrdersState = { items: [], loading: false, error: null }
let inflight: Promise<void> | null = null

function set(next: Partial<OrdersState>) {
  snapshot = { ...snapshot, ...next }
  listeners.forEach((notify) => notify())
}

export function refreshOrders(): Promise<void> {
  inflight ??= (async () => {
    set({ loading: true, error: null })
    try {
      set({ items: await api.listOrders(), loading: false })
    } catch (e) {
      set({ loading: false, error: e instanceof Error ? e.message : 'Не удалось загрузить заказы' })
    } finally {
      inflight = null
    }
  })()
  return inflight
}

function subscribe(notify: () => void) {
  listeners.add(notify)
  return () => listeners.delete(notify)
}

export function useOrders(): OrdersState {
  const state = useSyncExternalStore(subscribe, () => snapshot)
  useEffect(() => {
    void refreshOrders()
  }, [])
  return state
}
