<template>
  <div class="status-bar">
    <div class="status-item">
      <span class="status-dot" :class="apiOk ? 'ok' : 'err'"></span>
      <span>API</span>
    </div>
    <div class="status-item">
      <span class="status-label">Artículos:</span>
      <span class="status-value">{{ status?.scraped_articles ?? '—' }}</span>
    </div>
    <div class="status-item">
      <span class="status-label">Espacio libre:</span>
      <span class="status-value">{{ status?.disk_free_gb ?? '—' }} GB</span>
    </div>
    <div class="status-item">
      <span class="status-label">Total scraped:</span>
      <span class="status-value">{{ formatBytes(status?.total_size_bytes ?? 0) }}</span>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useApi } from '../composables/useApi.js'

const { health, status } = useApi()
const apiOk = ref(false)
const statusData = ref(null)
const POLL = 30000

let timer = null

async function load() {
  try {
    const [h, s] = await Promise.all([health(), status()])
    apiOk.value = h.status === 'ok'
    statusData.value = s
  } catch {
    apiOk.value = false
  }
}

function formatBytes(b) {
  if (b < 1024) return b + ' B'
  if (b < 1024 ** 2) return (b / 1024).toFixed(1) + ' KB'
  return (b / 1024 ** 2).toFixed(1) + ' MB'
}

defineExpose({ status: statusData })

onMounted(() => { load(); timer = setInterval(load, POLL) })
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.status-bar {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 8px;
  padding: 12px 20px;
  display: flex;
  gap: 24px;
  align-items: center;
  flex-wrap: wrap;
}
.status-item { display: flex; align-items: center; gap: 6px; font-size: 0.875rem; }
.status-label { color: #64748b; }
.status-value { color: #e2e8f0; font-weight: 600; }
.status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: #ef4444;
}
.status-dot.ok { background: #22c55e; }
.status-dot.err { background: #ef4444; }
</style>
