import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Este proxy SÓLO actúa en `npm run dev`. En Docker el frontend se sirve como
// estáticos por nginx, que hace su propio proxy (frontend/nginx.conf).
//
// Por eso el default es 127.0.0.1 y no `gx-rag-api`: ese hostname existe únicamente
// dentro de la red de Docker, donde este proxy no se usa. O sea que el default
// anterior no funcionaba en el único escenario donde corre — daba 500 en TODAS las
// llamadas, incluida /api/health, que ni toca la base.
//
// Y VITE_API_TARGET ahora se lee de verdad: CLAUDE.md la documentaba desde antes,
// pero el target estaba hardcodeado y la variable no tenía ningún efecto.
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: API_TARGET,
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
