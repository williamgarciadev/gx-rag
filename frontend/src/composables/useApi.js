/**
 * useApi — Capa de acceso a datos (Clean Architecture: Domain/Data separation).
 * No hay lógica de presentación aquí, solo llamadas HTTP.
 */

const API_BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `HTTP ${res.status}`)
  }
  return res.json()
}

export function useApi() {
  async function health() {
    return request('/health')
  }

  async function status() {
    return request('/status')
  }

  async function scrape(url) {
    return request('/scrape', {
      method: 'POST',
      body: JSON.stringify({ url }),
    })
  }

  async function scrapeGet(url) {
    return request(`/scrape?url=${encodeURIComponent(url)}`)
  }

  return { health, status, scrape, scrapeGet }
}
