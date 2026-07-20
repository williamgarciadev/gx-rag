<template>
  <div class="app">
    <header class="header">
      <div class="header-inner">
        <div class="logo">
          <span class="logo-icon">📚</span>
          <span class="logo-text">gx-bt-rag</span>
          <span class="logo-sub">Gestión de Corpus</span>
        </div>
        <nav class="nav">
          <span class="nav-item">Ingesta Wiki</span>
          <span class="nav-item nav-soon">PDF Bantotal</span>
          <span class="nav-item nav-soon">XPZ</span>
        </nav>
      </div>
    </header>

    <main class="main">
      <StatusBar />
      <div class="content-grid">
        <ScrapeForm @scraped="onScraped" />
        <ArticleList :articles="articles" />
      </div>
    </main>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import StatusBar from './components/StatusBar.vue'
import ScrapeForm from './components/ScrapeForm.vue'
import ArticleList from './components/ArticleList.vue'

const articles = ref([])

function onScraped(article) {
  const exists = articles.value.find(a => a.page_id === article.page_id)
  if (!exists) {
    articles.value.unshift(article)
  }
}
</script>

<style scoped>
.app { min-height: 100vh; display: flex; flex-direction: column; }

.header {
  background: #1a1d27;
  border-bottom: 1px solid #2d3348;
  padding: 0 24px;
}
.header-inner {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
}
.logo { display: flex; align-items: center; gap: 10px; }
.logo-icon { font-size: 1.4rem; }
.logo-text { font-size: 1.1rem; font-weight: 700; color: #f1f5f9; }
.logo-sub { font-size: 0.8rem; color: #64748b; border-left: 1px solid #2d3348; padding-left: 10px; margin-left: 2px; }

.nav { display: flex; gap: 8px; }
.nav-item { padding: 6px 14px; border-radius: 6px; font-size: 0.875rem; color: #94a3b8; cursor: pointer; }
.nav-item:not(.nav-soon):hover { background: #2d3348; color: #e2e8f0; }
.nav-soon { opacity: 0.4; cursor: default; }

.main { flex: 1; max-width: 1200px; width: 100%; margin: 0 auto; padding: 24px; }
.content-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 20px; }

@media (max-width: 768px) {
  .content-grid { grid-template-columns: 1fr; }
}
</style>
