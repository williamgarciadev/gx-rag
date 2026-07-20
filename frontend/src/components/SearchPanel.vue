<template>
  <div class="panel">
    <h2 class="panel-title">QA · Preview de Retrieval</h2>
    <p class="hint">Verificá qué devuelve el retriever (dense + BM25 + RRF) para una consulta. Solo lectura — no es un chat RAG.</p>

    <form @submit.prevent="submit" class="form">
      <div class="input-row">
        <input
          v-model="query"
          type="text"
          placeholder="Consulta, ej: default rule"
          class="input"
          :disabled="loading"
        />
        <select v-model="collection" class="select" :disabled="loading || !collections.length">
          <option v-for="c in collections" :key="c" :value="c">{{ c }}</option>
        </select>
        <button type="submit" class="btn" :disabled="loading || !query || !collection">
          {{ loading ? 'Buscando...' : 'Buscar' }}
        </button>
      </div>
      <p v-if="!collections.length" class="hint warn">No hay colecciones con contenido todavía. Ingestá un artículo primero.</p>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <div v-if="searched" class="results">
      <p class="results-count">
        {{ results.length }} resultado(s)<span v-if="lastQuery"> · «{{ lastQuery }}» en {{ lastCollection }}</span>
      </p>
      <p v-if="!results.length" class="empty">Sin resultados para esta consulta en «{{ lastCollection }}».</p>
      <ol class="result-list">
        <li v-for="(r, i) in results" :key="i" class="result-item">
          <div class="result-head">
            <span class="rank">#{{ i + 1 }}</span>
            <span class="result-title">{{ r.title || '(sin título)' }}</span>
            <span class="score" title="Reciprocal Rank Fusion score (rank-based, no es similitud)">{{ formatScore(r.score) }}</span>
          </div>
          <p class="snippet">{{ snippet(r.content) }}</p>
          <a v-if="r.source" :href="r.source" target="_blank" rel="noopener" class="source">{{ r.source }}</a>
        </li>
      </ol>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useApi } from '../composables/useApi.js'

const { search, status } = useApi()

const SNIPPET_LEN = 240

const query = ref('')
const collection = ref('')
const collections = ref([])
const loading = ref(false)
const error = ref('')
const results = ref([])
const searched = ref(false)
const lastQuery = ref('')
const lastCollection = ref('')

function snippet(text) {
  if (!text) return ''
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > SNIPPET_LEN ? `${clean.slice(0, SNIPPET_LEN)}…` : clean
}

function formatScore(score) {
  return typeof score === 'number' ? score.toFixed(5) : '—'
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
  }
}

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const r = await search(query.value, collection.value)
    results.value = r.results || []
    lastQuery.value = query.value
    lastCollection.value = collection.value
    searched.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(loadCollections)
</script>

<style scoped>
.panel {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 20px;
}
.panel-title { font-size: 0.95rem; font-weight: 600; color: #94a3b8; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.05em; }
.hint { font-size: 0.8rem; color: #64748b; margin-bottom: 14px; }
.hint.warn { color: #fbbf24; margin-top: 8px; margin-bottom: 0; }

.form { display: flex; flex-direction: column; gap: 10px; }
.input-row { display: flex; gap: 8px; }
.input {
  flex: 1;
  background: #111827;
  border: 1px solid #2d3348;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 0.875rem;
  padding: 9px 12px;
  outline: none;
  transition: border-color 0.15s;
}
.input:focus { border-color: #60a5fa; }
.input::placeholder { color: #475569; }
.input:disabled { opacity: 0.5; }

.select {
  background: #111827;
  border: 1px solid #2d3348;
  border-radius: 6px;
  color: #e2e8f0;
  font-size: 0.875rem;
  padding: 9px 12px;
  outline: none;
  cursor: pointer;
}
.select:focus { border-color: #60a5fa; }
.select:disabled { opacity: 0.5; }

.btn {
  background: #3b82f6;
  color: #fff;
  border: none;
  border-radius: 6px;
  padding: 9px 18px;
  font-size: 0.875rem;
  font-weight: 600;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s;
}
.btn:hover:not(:disabled) { background: #2563eb; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }

.error { color: #f87171; font-size: 0.8rem; }

.results { margin-top: 16px; padding-top: 16px; border-top: 1px solid #2d3348; }
.results-count { font-size: 0.8rem; color: #64748b; margin-bottom: 12px; }
.empty { font-size: 0.85rem; color: #94a3b8; }

.result-list { list-style: none; display: flex; flex-direction: column; gap: 12px; }
.result-item { background: #111827; border: 1px solid #2d3348; border-radius: 8px; padding: 12px 14px; }
.result-head { display: flex; align-items: center; gap: 10px; margin-bottom: 6px; }
.rank { font-size: 0.75rem; font-weight: 700; color: #475569; }
.result-title { flex: 1; font-size: 0.95rem; font-weight: 600; color: #f1f5f9; }
.score {
  font-size: 0.75rem;
  font-weight: 700;
  color: #60a5fa;
  background: #1e3a5f;
  padding: 2px 8px;
  border-radius: 4px;
  font-variant-numeric: tabular-nums;
}
.snippet { font-size: 0.82rem; color: #94a3b8; line-height: 1.45; margin-bottom: 8px; }
.source { font-size: 0.72rem; color: #475569; text-decoration: none; word-break: break-all; }
.source:hover { color: #60a5fa; }
</style>
