import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_URL || 'http://localhost:8000'

  return {
    plugins: [react()],
    // `npm run dev:mock` (--mode mock): экраны из фикстур src/api/fixtures без API и Telegram (src/api/mock.ts).
    // Флаг задаётся здесь, а не в .env.mock: .env.* в .gitignore. В production-сборке mock.ts его не читает.
    define: { 'import.meta.env.VITE_MOCK': JSON.stringify(mode === 'mock' ? '1' : '') },
    server: {
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
        },
      },
    },
  }
})
