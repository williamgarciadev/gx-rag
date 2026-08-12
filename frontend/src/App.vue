<template>
  <div class="app">
    <header class="header">
      <div class="header-inner">
        <div class="logo">
          <span class="logo-icon">🧬</span>
          <div class="logo-text-group">
            <span class="logo-text">gx-bt-rag</span>
            <span class="logo-sub">Gestión de Corpus GeneXus</span>
          </div>
        </div>
        <nav class="nav">
          <a href="https://gxrag.wgsoft.com.co/mcp/" target="_blank" class="nav-link" title="MCP Endpoint">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>
            </svg>
            MCP
          </a>
          <a href="https://github.com" target="_blank" class="nav-link" title="Documentación">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="16" height="16">
              <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
            </svg>
            Docs
          </a>
        </nav>
      </div>
    </header>

    <main class="main">
      <StatusBar />
      <SearchPanel />
    </main>

    <footer class="footer">
      <span>GeneXus RAG · MCP: <code>gxrag.wgsoft.com.co/mcp</code></span>
      <span class="footer-dot">·</span>
      <span>API: <code>/api</code></span>
    </footer>
  </div>
</template>

<script setup>
import StatusBar from './components/StatusBar.vue'
import SearchPanel from './components/SearchPanel.vue'
</script>

<style>
/* ===== Global Reset & Tokens ===== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg-primary: #0f1117;
  --bg-card: #1a1d27;
  --bg-input: #111827;
  --border-default: #2d3348;
  --border-hover: #3b4a6b;
  --text-primary: #f1f5f9;
  --text-secondary: #e2e8f0;
  --text-muted: #94a3b8;
  --text-dim: #64748b;
  --text-faint: #475569;
  --accent: #3b82f6;
  --accent-hover: #2563eb;
  --accent-soft: rgba(59, 130, 246, 0.15);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 12px;
}

body {
  font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
  background: var(--bg-primary);
  color: var(--text-secondary);
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

code {
  background: var(--bg-input);
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  font-family: 'Cascadia Code', 'JetBrains Mono', 'Fira Code', monospace;
  color: #60a5fa;
}

a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
</style>

<style scoped>
.app {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  background:
    radial-gradient(ellipse at 20% 0%, rgba(59, 130, 246, 0.06) 0%, transparent 50%),
    radial-gradient(ellipse at 80% 100%, rgba(245, 158, 11, 0.04) 0%, transparent 50%),
    var(--bg-primary);
}

/* ===== Header ===== */
.header {
  background: rgba(26, 29, 39, 0.8);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border-default);
  padding: 0 24px;
  position: sticky;
  top: 0;
  z-index: 50;
}
.header-inner {
  max-width: 1200px;
  margin: 0 auto;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 56px;
}

.logo {
  display: flex;
  align-items: center;
  gap: 10px;
}
.logo-icon {
  font-size: 1.5rem;
  filter: grayscale(0.2);
}
.logo-text-group {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.logo-text {
  font-size: 1rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.01em;
}
.logo-sub {
  font-size: 0.675rem;
  color: var(--text-dim);
  letter-spacing: 0.03em;
  text-transform: uppercase;
}

.nav {
  display: flex;
  align-items: center;
  gap: 4px;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  font-size: 0.8125rem;
  color: var(--text-muted);
  transition: all 0.15s;
  text-decoration: none;
}
.nav-link:hover {
  background: var(--border-default);
  color: var(--text-secondary);
  text-decoration: none;
}

/* ===== Main ===== */
.main {
  flex: 1;
  max-width: 1100px;
  width: 100%;
  margin: 0 auto;
  padding: 32px 24px 48px;
}

/* ===== Footer ===== */
.footer {
  border-top: 1px solid var(--border-default);
  padding: 16px 24px;
  text-align: center;
  font-size: 0.75rem;
  color: var(--text-faint);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  flex-wrap: wrap;
}
.footer code {
  font-size: 0.7rem;
  background: #1e293b;
  padding: 1px 5px;
}
.footer-dot { color: var(--border-hover); }

@media (max-width: 640px) {
  .main { padding: 20px 16px 32px; }
  .header { padding: 0 16px; }
  .logo-text-group { display: none; }
}
</style>
