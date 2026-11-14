// Общая граница разбора ответов: используется и trip-клиентом, и auth-хранилищем,
// чтобы не было цикла импортов между ними.
export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

export async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, typeof body.detail === 'string' ? body.detail : `Ошибка ${response.status}`)
  }
  return response.json() as Promise<T>
}