import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': {
        target: 'http://45.150.38.138:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
