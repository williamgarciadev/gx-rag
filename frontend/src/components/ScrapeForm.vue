<template>
  <div class="panel">
    <h2 class="panel-title">Scrape Artículo Wiki</h2>
    <form @submit.prevent="submit" class="form">
      <div class="input-row">
        <input
          v-model="url"
          type="url"
          placeholder="https://docs.genexus.com/en/wiki?24744,For+each+command"
          required
          class="input"
          :disabled="loading"
        />
        <button type="submit" class="btn" :disabled="loading || !url">
          {{ loading ? 'Scraping...' : 'Scrapear' }}
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <div v-if="result" class="result">
      <div class="result-header">
        <span class="badge" :class="result.genre">{{ result.genre }}</span>
        <span class="badge-dest">{{ result.destination }}</span>
      </div>
      <h3 class="result-title">{{ result.title }}</h3>
      <div class="result-meta">
        <span>page_id: {{ result.page_id }}</span>
        <span>v: {{ result.version_hint ?? 'N/A' }}</span>
        <span>{{ result.word_count }} palabras</span>
      </div>
      <div class="result-hash">
        <code>{{ result.content_hash.slice(0, 16) }}...</code>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useApi } from '../composables/useApi.js'

const emit = defineEmits(['scraped'])
const { scrape } = useApi()

const url = ref('')
const loading = ref(false)
const error = ref('')
const result = ref(null)

async function submit() {
  loading.value = true
  error.value = ''
  result.value = null
  try {
    const r = await scrape(url.value)
    result.value = r
    emit('scraped', r)
    url.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.panel {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 20px;
}
.panel-title { font-size: 0.95rem; font-weight: 600; color: #94a3b8; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.05em; }

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

.result { margin-top: 16px; padding-top: 16px; border-top: 1px solid #2d3348; }
.result-header { display: flex; gap: 8px; margin-bottom: 8px; }
.badge {
  font-size: 0.7rem;
  font-weight: 700;
  padding: 3px 8px;
  border-radius: 4px;
  text-transform: uppercase;
}
.badge.reference { background: #1e3a5f; color: #60a5fa; }
.badge.guide { background: #1e3a2f; color: #34d399; }
.badge-dest { font-size: 0.7rem; padding: 3px 8px; border-radius: 4px; background: #1e293b; color: #94a3b8; }

.result-title { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; margin-bottom: 8px; }
.result-meta { display: flex; gap: 16px; font-size: 0.8rem; color: #64748b; flex-wrap: wrap; margin-bottom: 8px; }
.result-hash code { font-size: 0.75rem; color: #475569; }
</style>
