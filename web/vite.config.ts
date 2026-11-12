import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// В dev-режиме /api проксируется на шлюз (gateway). В Docker то же делает nginx.
const API_TARGET = process.env.VITE_API_TARGET ?? 'http://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': { target: API_TARGET, changeOrigin: true } },
  },
})
