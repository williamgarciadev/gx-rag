<template>
  <section class="search-hero">
    <div class="search-container">
      <h2 class="hero-title">
        <span class="title-icon">🔍</span>
        Buscar en el corpus GeneXus
      </h2>
      <p class="hero-sub">
        Búsqueda semántica híbrida (dense + BM25 + RRF) sobre documentación Wiki y artículos SAC
      </p>

      <form @submit.prevent="submit" class="search-form">
        <div class="input-group">
          <div class="search-input-wrap">
            <svg class="search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
            </svg>
            <input
              v-model="query"
              type="text"
              placeholder='"ControlValueChanged evento enter" o "disminuir tamaño de BD"'
              class="search-input"
              :disabled="loading"
              autofocus
            />
            <button v-if="query" type="button" class="clear-btn" @click="query = ''" aria-label="Limpiar">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
                <path d="M18 6 6 18M6 6l12 12"/>
              </svg>
            </button>
          </div>

          <div class="filters-row">
            <select v-model="collection" class="filter-select" title="Colección">
              <option v-for="c in collections" :key="c" :value="c">{{ formatCollection(c) }}</option>
            </select>

            <div class="source-chips">
              <button
                v-for="src in sourceFilters"
                :key="src.key"
                type="button"
                class="chip"
                :class="{ active: activeSources.includes(src.key) }"
                @click="toggleSource(src.key)"
              >
                <span class="chip-dot" :style="{ background: src.color }"></span>
                {{ src.label }}
              </button>
            </div>

            <select v-model="topK" class="filter-select filter-select-sm" title="Resultados">
              <option :value="3">3 resultados</option>
              <option :value="5">5 resultados</option>
              <option :value="10">10 resultados</option>
            </select>

            <button type="submit" class="search-btn" :disabled="loading || !query">
              <template v-if="loading">
                <span class="spinner"></span> Buscando
              </template>
              <template v-else>
                Buscar
              </template>
            </button>
          </div>
        </div>

        <p v-if="error" class="search-error">{{ error }}</p>
      </form>

      <div v-if="!searched" class="search-hints">
        <p class="hints-title">Consultas de ejemplo</p>
        <div class="hints-grid">
          <button
            v-for="hint in exampleQueries"
            :key="hint"
            class="hint-chip"
            @click="query = hint; submit()"
          >{{ hint }}</button>
        </div>
      </div>
    </div>

    <!-- Results -->
    <div v-if="searched" class="results-section">
      <div class="results-header">
        <p class="results-summary">
          <strong>{{ filteredResults.length }}</strong> resultado{{ filteredResults.length !== 1 ? 's' : '' }}
          <span v-if="hiddenByFilter" class="summary-hidden">
            (de {{ results.length }} recuperados; {{ hiddenByFilter }} oculto{{ hiddenByFilter !== 1 ? 's' : '' }} por el filtro de fuente)
          </span>
          <span v-if="lastQuery" class="summary-query">· «{{ lastQuery }}»</span>
          <span class="summary-collection">· {{ formatCollection(lastCollection) }}</span>
        </p>
        <!-- Dos vacíos distintos, y confundirlos es mentirle al curador: que la
             búsqueda no devuelva nada NO es lo mismo que que el filtro de fuente
             esconda lo que sí devolvió. -->
        <p v-if="!results.length" class="empty-state">Sin resultados para esta consulta. Probá con otros términos o cambiá la colección.</p>
        <p v-else-if="!filteredResults.length" class="empty-state">
          La consulta devolvió {{ results.length }} resultado{{ results.length !== 1 ? 's' : '' }}, pero el filtro de fuente los oculta todos. Activá otra fuente para verlos.
        </p>
      </div>

      <ol class="results-list">
        <!-- filteredResults, no results: los chips de fuente ya calculaban el
             filtro y el template lo ignoraba, así que eran decorativos. -->
        <li v-for="(r, i) in filteredResults" :key="i" class="result-card">
          <div class="card-header">
            <div class="card-meta">
              <span class="card-rank">#{{ i + 1 }}</span>
              <span class="source-badge" :class="sourceClass(r.source)">
                <span class="badge-dot"></span>
                {{ sourceLabel(r.source) }}
              </span>
              <span v-if="r.genre" class="genre-badge">{{ r.genre }}</span>
            </div>
            <span class="card-score" :title="'RRF score (rank-based)'">{{ r.score?.toFixed(4) }}</span>
          </div>

          <h3 class="card-title">{{ r.title || '(sin título)' }}</h3>
          <p class="card-snippet">{{ snippet(r.content) }}</p>

          <div class="card-footer">
            <a v-if="r.source" :href="r.source" target="_blank" rel="noopener" class="card-link">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3"/>
              </svg>
              {{ formatUrl(r.source) }}
            </a>
            <button class="copy-btn" @click="copySnippet(r.content)" title="Copiar snippet">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="14" height="14">
                <rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>
              </svg>
            </button>
          </div>
        </li>
      </ol>
    </div>
  </section>
</template>

<script setup>
import { ref, onMounted, computed } from 'vue'
import { useApi } from '../composables/useApi.js'

const { search, status } = useApi()

const SNIPPET_LEN = 320

const query = ref('')
const collection = ref('gx_docs')
const collections = ref([])
const topK = ref(5)
const loading = ref(false)
const error = ref('')
const results = ref([])
const searched = ref(false)
const lastQuery = ref('')
const lastCollection = ref('')
const copied = ref(false)

const sourceFilters = [
  { key: 'wiki', label: 'Wiki', color: '#60a5fa' },
  { key: 'sac', label: 'SAC', color: '#f59e0b' },
  { key: 'bt', label: 'Bantotal', color: '#22c55e' },
]
const activeSources = ref(['wiki', 'sac', 'bt'])

const exampleQueries = [
  'ControlValueChanged evento enter',
  'disminuir tamaño base de datos SQL Server',
  'propiedad Case Upper longvarchar',
  'Web Panel Design System',
  'error 403 token invalid',
]

function snippet(text) {
  if (!text) return ''
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > SNIPPET_LEN ? `${clean.slice(0, SNIPPET_LEN)}…` : clean
}

function formatCollection(c) {
  const map = { gx_docs: '📚 Wiki + SAC', gx_syntax: '💻 Sintaxis', bt_docs: '🏦 Bantotal' }
  return map[c] || c
}

function sourceLabel(url) {
  if (!url) return 'Desconocido'
  if (url.includes('sac.genexus.com')) return 'SAC'
  if (url.includes('wiki.genexus.com')) return 'Wiki'
  if (url.includes('bantotal')) return 'Bantotal'
  return 'Web'
}

function sourceClass(url) {
  if (!url) return ''
  if (url.includes('sac.genexus.com')) return 'src-sac'
  if (url.includes('wiki.genexus.com')) return 'src-wiki'
  if (url.includes('bantotal')) return 'src-bt'
  return ''
}

function formatUrl(url) {
  try {
    const u = new URL(url)
    return u.hostname + u.pathname.substring(0, 40) + (u.pathname.length > 40 ? '…' : '')
  } catch { return url?.substring(0, 60) || '' }
}

function toggleSource(key) {
  const idx = activeSources.value.indexOf(key)
  if (idx === -1) activeSources.value.push(key)
  else if (activeSources.value.length > 1) activeSources.value.splice(idx, 1)
}

const hiddenByFilter = computed(() => results.value.length - filteredResults.value.length)

const filteredResults = computed(() => {
  return results.value.filter(r => {
    const src = sourceLabel(r.source).toLowerCase()
    return activeSources.value.includes(
      src === 'web' ? (r.source?.includes('sac') ? 'sac' : 'wiki') : src
    )
  })
})

async function copySnippet(text) {
  try {
    await navigator.clipboard.writeText(text?.substring(0, 500) || '')
    copied.value = true
    setTimeout(() => copied.value = false, 2000)
  } catch {}
}

async function loadCollections() {
  try {
    const s = await status()
    collections.value = s.chroma_collections || []
    if (collections.value.length && !collection.value) {
      collection.value = collections.value[0]
    }
  } catch (e) {
    console.error('Failed to load collections', e)
    collections.value = ['gx_docs', 'gx_syntax', 'bt_docs']
  }
}

async function submit() {
  if (!query.value.trim()) return
  loading.value = true
  error.value = ''
  try {
    const r = await search(query.value.trim(), collection.value, topK.value)
    results.value = r.results || []
    lastQuery.value = query.value
    lastCollection.value = collection.value
    searched.value = true
  } catch (e) {
    error.value = e.message || 'Error al buscar'
  } finally {
    loading.value = false
  }
}

onMounted(loadCollections)
</script>

<style scoped>
/* ===== Search Hero ===== */
.search-hero {
  max-width: 900px;
  margin: 0 auto;
  width: 100%;
}

.search-container {
  text-align: center;
  padding: 48px 0 32px;
}

.hero-title {
  font-size: 1.75rem;
  font-weight: 700;
  color: #f1f5f9;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 8px;
}
.title-icon { font-size: 1.5rem; }
.hero-sub {
  font-size: 0.875rem;
  color: #64748b;
  margin-bottom: 28px;
  max-width: 520px;
  margin-left: auto;
  margin-right: auto;
}

/* ===== Search Form ===== */
.search-form { margin-bottom: 20px; }
.input-group {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 12px;
  padding: 6px;
  transition: border-color 0.2s, box-shadow 0.2s;
}
.input-group:focus-within {
  border-color: #60a5fa;
  box-shadow: 0 0 0 3px rgba(96, 165, 250, 0.15);
}

.search-input-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 12px 4px;
}
.search-icon {
  width: 20px;
  height: 20px;
  color: #475569;
  flex-shrink: 0;
}
.search-input {
  flex: 1;
  background: transparent;
  border: none;
  color: #e2e8f0;
  font-size: 1.05rem;
  padding: 8px 0;
  outline: none;
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
}
.search-input::placeholder { color: #475569; }
.search-input:disabled { opacity: 0.5; }
.clear-btn {
  background: none;
  border: none;
  color: #64748b;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  display: flex;
  align-items: center;
}
.clear-btn:hover { color: #94a3b8; background: #2d3348; }

/* ===== Filters Row ===== */
.filters-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 12px 8px;
  flex-wrap: wrap;
}

.filter-select {
  background: #111827;
  border: 1px solid #2d3348;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 0.8125rem;
  padding: 6px 10px;
  outline: none;
  cursor: pointer;
}
.filter-select:focus { border-color: #60a5fa; }
.filter-select-sm { width: 130px; }

.source-chips { display: flex; gap: 4px; }
.chip {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 10px;
  border-radius: 20px;
  font-size: 0.75rem;
  font-weight: 500;
  background: #111827;
  border: 1px solid #2d3348;
  color: #64748b;
  cursor: pointer;
  transition: all 0.15s;
}
.chip:hover { border-color: #475569; color: #94a3b8; }
.chip.active { border-color: #60a5fa; color: #e2e8f0; background: #1e293b; }
.chip-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }

.search-btn {
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 7px 20px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.search-btn:hover:not(:disabled) { background: #2563eb; }
.search-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.spinner {
  width: 14px; height: 14px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.6s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }

.search-error {
  color: #f87171;
  font-size: 0.8125rem;
  margin-top: 8px;
  text-align: left;
  padding-left: 4px;
}

/* ===== Hint queries ===== */
.search-hints { margin-top: 8px; }
.hints-title {
  font-size: 0.75rem;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 10px;
}
.hints-grid {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  justify-content: center;
}
.hint-chip {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 20px;
  color: #94a3b8;
  font-size: 0.8125rem;
  padding: 6px 14px;
  cursor: pointer;
  transition: all 0.15s;
}
.hint-chip:hover {
  border-color: #60a5fa;
  color: #e2e8f0;
  background: #1e293b;
}

/* ===== Results ===== */
.results-section { margin-top: 24px; }
.results-header { margin-bottom: 16px; }
.results-summary {
  font-size: 0.875rem;
  color: #94a3b8;
}
.results-summary strong { color: #e2e8f0; font-size: 1.05rem; }
.summary-query { color: #64748b; }
.summary-collection { color: #475569; font-size: 0.8125rem; }
.empty-state { font-size: 0.875rem; color: #64748b; margin-top: 6px; }

.results-list { list-style: none; display: flex; flex-direction: column; gap: 12px; }

.result-card {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 16px 20px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.result-card:hover {
  border-color: #3b4a6b;
  box-shadow: 0 2px 12px rgba(0,0,0,0.3);
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 8px;
}
.card-meta { display: flex; align-items: center; gap: 8px; }
.card-rank {
  font-size: 0.7rem;
  font-weight: 700;
  color: #475569;
  background: #111827;
  padding: 2px 6px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}

.source-badge {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 0.7rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  padding: 2px 8px;
  border-radius: 4px;
}
.source-badge.src-wiki { background: #1e3a5f; color: #60a5fa; }
.source-badge.src-sac  { background: #3d2e00; color: #f59e0b; }
.source-badge.src-bt   { background: #0f3d1a; color: #22c55e; }
.badge-dot {
  width: 6px; height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.genre-badge {
  font-size: 0.675rem;
  color: #64748b;
  background: #111827;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid #2d3348;
}

.card-score {
  font-size: 0.7rem;
  font-weight: 600;
  color: #60a5fa;
  background: #1e3a5f;
  padding: 3px 8px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}

.card-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: #f1f5f9;
  margin-bottom: 10px;
  line-height: 1.35;
}

.card-snippet {
  font-size: 0.8125rem;
  color: #94a3b8;
  line-height: 1.55;
  margin-bottom: 12px;
}

.card-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.card-link {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 0.7rem;
  color: #475569;
  text-decoration: none;
  word-break: break-all;
  min-width: 0;
}
.card-link:hover { color: #60a5fa; }

.copy-btn {
  background: #111827;
  border: 1px solid #2d3348;
  color: #64748b;
  cursor: pointer;
  padding: 5px 8px;
  border-radius: 4px;
  display: flex;
  align-items: center;
  transition: all 0.15s;
  flex-shrink: 0;
}
.copy-btn:hover { color: #e2e8f0; border-color: #475569; }

@media (max-width: 640px) {
  .hero-title { font-size: 1.3rem; }
  .filters-row { flex-direction: column; align-items: stretch; }
  .search-btn { margin-left: 0; justify-content: center; }
  .source-chips { justify-content: center; }
}
</style>
