// Тонкая обёртка над Telegram WebApp SDK (telegram-web-app.js в index.html).
// Вне Telegram SDK тоже грузится, но initData пустой — по нему и отличаем Mini App.
// ?tg=1 включает режим Mini App в обычном браузере: так его удобно проверять локально.

export interface TelegramUser {
  id: number
  first_name: string
  last_name?: string
  username?: string
  photo_url?: string
  language_code?: string
}

interface TelegramWebApp {
  initData: string
  initDataUnsafe: { user?: TelegramUser }
  colorScheme: 'light' | 'dark'
  ready(): void
  expand(): void
  setHeaderColor(color: string): void
  setBackgroundColor(color: string): void
  HapticFeedback?: {
    selectionChanged(): void
    notificationOccurred(type: 'success' | 'warning' | 'error'): void
  }
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp }
  }
}

const webApp = typeof window === 'undefined' ? undefined : window.Telegram?.WebApp

export const isMiniApp =
  Boolean(webApp?.initData) || (typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('tg'))

export function initMiniApp() {
  if (!webApp?.initData) return
  webApp.ready()
  webApp.expand()
  // Шапка Telegram в цвет страницы: приложение выглядит частью чата, а не вставкой.
  webApp.setHeaderColor('#eaf6df')
  webApp.setBackgroundColor('#f4f7f1')
}

/** Подписанная строка запуска: backend проверяет её токеном бота и узнаёт пользователя. */
export function telegramInitData(): string | null {
  return webApp?.initData || null
}

export function telegramUser(): TelegramUser | null {
  return webApp?.initDataUnsafe.user ?? null
}

export const haptic = {
  select: () => webApp?.HapticFeedback?.selectionChanged(),
  success: () => webApp?.HapticFeedback?.notificationOccurred('success'),
}
