<template>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-icon stat-api">
        <span class="pulse-dot" :class="apiOk ? 'ok' : 'err'"></span>
      </div>
      <div class="stat-body">
        <span class="stat-label">API Status</span>
        <span class="stat-value" :class="apiOk ? 'text-green' : 'text-red'">{{ apiOk ? 'Online' : 'Offline' }}</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-icon stat-docs">📄</div>
      <div class="stat-body">
        <span class="stat-label">Artículos</span>
        <span class="stat-value">{{ statusData?.scraped_articles ?? '—' }}</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-icon stat-collections">🗂️</div>
      <div class="stat-body">
        <span class="stat-label">Colecciones</span>
        <span class="stat-value" :title="collectionsDetail">{{ collectionsCount }}</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-icon stat-disk">💾</div>
      <div class="stat-body">
        <span class="stat-label">Espacio libre</span>
        <span class="stat-value">{{ statusData?.disk_free_gb ?? '—' }} GB</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-icon stat-size">📊</div>
      <div class="stat-body">
        <span class="stat-label">Total scrapeado</span>
        <span class="stat-value">{{ formatBytes(statusData?.total_size_bytes ?? 0) }}</span>
      </div>
    </div>

    <div class="stat-card">
      <div class="stat-icon stat-chunks">🧩</div>
      <div class="stat-body">
        <span class="stat-label">Chunks indexados</span>
        <span class="stat-value" :class="{ 'text-red': storeError }" :title="collectionsDetail">
          {{ chunksLabel }}
        </span>
      </div>
    </div>
  </div>

  <!-- Un store caído no puede pasar por "corpus vacío": si /status trae
       store_error, se dice, en vez de mostrar ceros y guiones. -->
  <p v-if="storeError" class="store-error">
    ⚠️ No se pudo consultar el store: {{ storeError }}
  </p>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useApi } from '../composables/useApi.js'

// `fetchStatus` y no `status`: el composable exporta una FUNCIÓN llamada `status`,
// y en <script setup> todo binding queda visible en el template. El template usaba
// `status?.scraped_articles`, que resolvía a la función y daba undefined SIEMPRE —
// cinco de las seis tarjetas mostraban "—" pasara lo que pasara. Los datos viven
// en statusData; renombrar la función es lo que rompe la colisión.
const { health, status: fetchStatus } = useApi()
const apiOk = ref(false)
const statusData = ref(null)
const POLL = 30000

const storeError = computed(() => statusData.value?.store_error || null)

const collectionsCount = computed(() => {
  const cols = statusData.value?.collections
  return cols?.length || '—'
})

// Los conteos por colección, para el tooltip: "gx_docs: 50.063 · bt_docs: 1.122".
const collectionsDetail = computed(() => {
  const counts = statusData.value?.chunk_counts
  if (!counts || !Object.keys(counts).length) return ''
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k}: ${v.toLocaleString()}`)
    .join(' · ')
})

const chunksLabel = computed(() => {
  if (storeError.value) return 'error'
  const total = statusData.value?.total_chunks
  return total ? total.toLocaleString() : '—'
})

let timer = null

async function load() {
  try {
    const [h, s] = await Promise.all([health(), fetchStatus()])
    apiOk.value = h.status === 'ok'
    statusData.value = s
  } catch {
    apiOk.value = false
  }
}

function formatBytes(b) {
  if (!b) return '0 B'
  if (b < 1024) return b + ' B'
  if (b < 1_048_576) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1_048_576).toFixed(1) + ' MB'
}

// formatCounts() y defineExpose({ status: statusData }) se fueron: la primera la
// reemplaza collectionsDetail, y el expose no lo consumía nadie (App.vue monta
// <StatusBar /> sin ref).

onMounted(() => { load(); timer = setInterval(load, POLL) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
  gap: 12px;
  margin-bottom: 28px;
}

.stat-card {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 14px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.stat-card:hover {
  border-color: #3b4a6b;
  box-shadow: 0 2px 8px rgba(0,0,0,0.2);
}

.stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 1.1rem;
  flex-shrink: 0;
}
.stat-api   { background: #1e293b; }
.stat-docs  { background: #1e3a5f; }
.stat-collections { background: #3d2e00; }
.stat-disk  { background: #1a2e1a; }
.stat-size  { background: #2d1a3d; }
.stat-chunks { background: #1a2d2d; }

.pulse-dot {
  width: 10px; height: 10px;
  border-radius: 50%;
  display: block;
}
.pulse-dot.ok { background: #22c55e; }
.pulse-dot.err { background: #ef4444; }

.stat-body {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-label {
  font-size: 0.6875rem;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.stat-value {
  font-size: 0.9375rem;
  font-weight: 700;
  color: #e2e8f0;
  font-variant-numeric: tabular-nums;
}
.text-green { color: #22c55e; }
.text-red { color: #ef4444; }

.store-error {
  margin: -16px 0 24px;
  padding: 10px 14px;
  background: rgba(239, 68, 68, 0.08);
  border: 1px solid rgba(239, 68, 68, 0.35);
  border-radius: 8px;
  color: #fca5a5;
  font-size: 0.8125rem;
}

@media (max-width: 640px) {
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
</style>
