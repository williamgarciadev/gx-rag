<template>
  <div class="panel">
    <h2 class="panel-title">Artículos ({{ articles.length }})</h2>

    <div v-if="articles.length === 0" class="empty">
      No hay artículos aún. Scrippeá uno para empezar.
    </div>

    <ul v-else class="list">
      <li v-for="a in articles" :key="a.page_id" class="item">
        <div class="item-header">
          <span class="badge" :class="a.genre">{{ a.genre }}</span>
          <span class="item-id">{{ a.page_id }}</span>
        </div>
        <div class="item-title">{{ a.title }}</div>
        <div class="item-meta">
          <span>{{ a.version_hint ?? 'N/A' }}</span>
          <span>{{ a.word_count }} palabras</span>
          <span class="dest">{{ a.destination }}</span>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
defineProps({
  articles: {
    type: Array,
    default: () => [],
  },
})
</script>

<style scoped>
.panel {
  background: #1a1d27;
  border: 1px solid #2d3348;
  border-radius: 10px;
  padding: 20px;
}
.panel-title {
  font-size: 0.95rem;
  font-weight: 600;
  color: #94a3b8;
  margin-bottom: 14px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.empty { color: #475569; font-size: 0.875rem; text-align: center; padding: 32px 0; }

.list { list-style: none; display: flex; flex-direction: column; gap: 8px; max-height: 500px; overflow-y: auto; }
.item {
  background: #111827;
  border: 1px solid #1e293b;
  border-radius: 6px;
  padding: 12px 14px;
  transition: border-color 0.15s;
}
.item:hover { border-color: #2d3348; }
.item-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.badge {
  font-size: 0.65rem;
  font-weight: 700;
  padding: 2px 6px;
  border-radius: 3px;
  text-transform: uppercase;
}
.badge.reference { background: #1e3a5f; color: #60a5fa; }
.badge.guide { background: #1e3a2f; color: #34d399; }
.item-id { font-size: 0.75rem; color: #475569; font-family: monospace; }
.item-title { font-size: 0.875rem; font-weight: 600; color: #e2e8f0; margin-bottom: 6px; }
.item-meta { display: flex; gap: 12px; font-size: 0.75rem; color: #64748b; flex-wrap: wrap; }
.dest { color: #60a5fa; }
</style>
