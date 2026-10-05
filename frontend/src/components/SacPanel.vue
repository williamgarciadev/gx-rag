<template>
  <div class="panel">
    <h2 class="panel-title">Ingesta SAC — Barrido</h2>

    <form @submit.prevent="launch" class="form">
      <div class="mode-row">
        <label v-for="m in MODES" :key="m.value" class="mode-opt" :class="{ active: mode === m.value }">
          <input type="radio" :value="m.value" v-model="mode" :disabled="running" />
          {{ m.label }}
        </label>
      </div>

      <div class="fields">
        <label v-if="mode === 'ids'" class="field field-wide">
          <span>Article IDs</span>
          <input v-model="idsText" class="input" placeholder="61036, 61037, 61040" :disabled="running" />
        </label>

        <label v-if="mode !== 'ids'" class="field">
          <span>{{ mode === 'sweep' ? 'Start' : 'Desde' }}</span>
          <input v-model.number="start" type="number" min="1" class="input" :disabled="running" />
        </label>
        <label v-if="mode === 'range'" class="field">
          <span>Hasta</span>
          <input v-model.number="end" type="number" min="1" class="input" :disabled="running" />
        </label>
        <label v-if="mode === 'sweep'" class="field">
          <span>Max misses</span>
          <input v-model.number="maxMisses" type="number" min="1" max="500" class="input" :disabled="running" />
        </label>

        <label class="field">
          <span>Min words</span>
          <input v-model.number="minWords" type="number" min="0" max="1000" class="input" :disabled="running" />
        </label>
        <label class="field">
          <span>Delay (s)</span>
          <input v-model.number="delay" type="number" min="0.1" max="10" step="0.1" class="input" :disabled="running" />
        </label>
        <label class="field">
          <span>Limit (0=∞)</span>
          <input v-model.number="limit" type="number" min="0" max="10000" class="input" :disabled="running" />
        </label>
      </div>

      <div class="actions">
        <label class="dry">
          <input type="checkbox" v-model="dryRun" :disabled="running" />
          Dry run (no escribe ni indexa)
        </label>
        <button v-if="!running" type="submit" class="btn">Lanzar</button>
        <button v-else type="button" class="btn btn-cancel" @click="cancel">Cancelar</button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </form>

    <div v-if="job" class="job">
      <div class="job-header">
        <span class="badge" :class="job.status">{{ job.status }}</span>
        <span class="job-meta">procesados: {{ job.processed }}</span>
        <span v-if="job.current_id" class="job-meta">actual: {{ job.current_id }}</span>
      </div>

      <div class="counts">
        <span v-for="(v, k) in job.counts" :key="k" class="count" :class="k">{{ k }}: {{ v }}</span>
        <span v-if="!hasCounts" class="count muted">sin resultados aún</span>
      </div>

      <pre v-if="job.log && job.log.length" class="log">{{ logTail }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, onUnmounted, ref } from 'vue'
import { useApi } from '../composables/useApi.js'

const MODES = [
  { value: 'sweep', label: 'Sweep' },
  { value: 'range', label: 'Range' },
  { value: 'ids', label: 'IDs' },
]
const POLL_MS = 1500

const { sacScrape, sacJob, sacCancel } = useApi()

const mode = ref('sweep')
const start = ref(49983)
const end = ref(49983)
const idsText = ref('')
const maxMisses = ref(50)
const minWords = ref(70)
const delay = ref(1.0)
const limit = ref(0)
const dryRun = ref(true)

const job = ref(null)
const jobId = ref(null)
const error = ref('')
let timer = null

const running = computed(() => job.value?.status === 'running')
const hasCounts = computed(() => job.value && Object.keys(job.value.counts || {}).length > 0)
const logTail = computed(() => (job.value?.log || []).slice(-15).join('\n'))

function buildParams() {
  const base = {
    mode: mode.value,
    min_words: minWords.value,
    delay: delay.value,
    limit: limit.value,
    dry_run: dryRun.value,
  }
  if (mode.value === 'ids') {
    base.ids = idsText.value
      .split(/[\s,]+/)
      .filter(Boolean)
      .map(Number)
      .filter((n) => Number.isInteger(n) && n > 0)
  } else if (mode.value === 'range') {
    base.start = start.value
    base.end = end.value
  } else {
    base.start = start.value
    base.max_misses = maxMisses.value
  }
  return base
}

async function poll(jobId) {
  try {
    job.value = await sacJob(jobId)
  } catch (e) {
    error.value = e.message
  }
  if (job.value && job.value.status !== 'running') {
    stopTimer()
  }
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

async function launch() {
  error.value = ''
  stopTimer()
  try {
    const res = await sacScrape(buildParams())
    jobId.value = res.job_id
    job.value = { status: 'running', processed: 0, current_id: null, counts: {}, log: [] }
    timer = setInterval(() => poll(res.job_id), POLL_MS)
    poll(res.job_id)
  } catch (e) {
    error.value = e.message
  }
}

async function cancel() {
  if (!jobId.value) return
  try {
    await sacCancel(jobId.value)
  } catch (e) {
    error.value = e.message
  }
}

onUnmounted(stopTimer)
</script>

<style scoped>
.panel {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 20px;
}
.panel-title { font-size: 0.95rem; font-weight: 600; color: #94a3b8; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.05em; }

.form { display: flex; flex-direction: column; gap: 12px; }

.mode-row { display: flex; gap: 8px; }
.mode-opt {
  display: flex; align-items: center; gap: 6px;
  font-size: 0.8rem; color: #94a3b8; cursor: pointer;
  padding: 5px 12px; border: 1px solid #2d3348; border-radius: 6px;
}
.mode-opt.active { border-color: #60a5fa; color: #e2e8f0; background: #172033; }
.mode-opt input { accent-color: #3b82f6; }

.fields { display: flex; flex-wrap: wrap; gap: 10px; }
.field { display: flex; flex-direction: column; gap: 4px; }
.field > span { font-size: 0.7rem; color: #64748b; text-transform: uppercase; letter-spacing: 0.04em; }
.field-wide { flex: 1 1 100%; }
.input {
  background: #111827; border: 1px solid #2d3348; border-radius: 6px;
  color: #e2e8f0; font-size: 0.875rem; padding: 8px 10px; outline: none;
  width: 120px; transition: border-color 0.15s;
}
.field-wide .input { width: 100%; }
.input:focus { border-color: #60a5fa; }
.input:disabled { opacity: 0.5; }

.actions { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; }
.dry { display: flex; align-items: center; gap: 6px; font-size: 0.8rem; color: #94a3b8; cursor: pointer; margin-right: auto; }
.dry input { accent-color: #3b82f6; }

.btn {
  background: #3b82f6; color: #fff; border: none; border-radius: 6px;
  padding: 9px 20px; font-size: 0.875rem; font-weight: 600; cursor: pointer;
  transition: background 0.15s;
}
.btn:hover { background: #2563eb; }
.btn-cancel { background: #b91c1c; }
.btn-cancel:hover { background: #991b1b; }

.error { color: #f87171; font-size: 0.8rem; }

.job { margin-top: 16px; padding-top: 16px; border-top: 1px solid #2d3348; }
.job-header { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.job-meta { font-size: 0.8rem; color: #64748b; }
.badge {
  font-size: 0.7rem; font-weight: 700; padding: 3px 9px; border-radius: 4px; text-transform: uppercase;
  background: #1e293b; color: #94a3b8;
}
.badge.running { background: #1e3a5f; color: #60a5fa; }
.badge.done { background: #1e3a2f; color: #34d399; }
.badge.error { background: #3f1d1d; color: #f87171; }
.badge.cancelled { background: #3a2f1e; color: #fbbf24; }

.counts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.count { font-size: 0.75rem; padding: 3px 9px; border-radius: 4px; background: #1e293b; color: #cbd5e1; }
.count.indexed { background: #1e3a2f; color: #34d399; }
.count.reindexed { background: #1e3350; color: #93c5fd; }
.count.skipped { background: #26304a; color: #a5b4fc; }
.count.empty { background: #2b2f3a; color: #94a3b8; }
.count.failed { background: #3f1d1d; color: #f87171; }
.count.muted { color: #475569; }

.log {
  background: #0d1117; border: 1px solid #1f2937; border-radius: 6px;
  padding: 10px; font-size: 0.72rem; line-height: 1.5; color: #94a3b8;
  max-height: 180px; overflow-y: auto; white-space: pre-wrap; margin: 0;
}
</style>
