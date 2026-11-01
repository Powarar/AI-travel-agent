import type { SSEEvent } from '../types/contracts'

// POST + text/event-stream: EventSource умеет только GET, поэтому поток читается через fetch.
export async function* readSSE(response: Response): AsyncGenerator<SSEEvent> {
  if (!response.body) return
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    let boundary = buffer.indexOf('\n\n')
    while (boundary !== -1) {
      const parsed = parseBlock(buffer.slice(0, boundary))
      if (parsed) yield parsed
      buffer = buffer.slice(boundary + 2)
      boundary = buffer.indexOf('\n\n')
    }
    if (done) break
  }
  const tail = parseBlock(buffer)
  if (tail) yield tail
}

function parseBlock(block: string): SSEEvent | null {
  let event = ''
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!event || !data.length) return null
  return { event, data: JSON.parse(data.join('\n')) } as SSEEvent
}
