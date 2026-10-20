import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built into the Python package, which serves it (src/housing_watch/web/app.py). `npm run dev` proxies the
// API to a running `serve` (HW_API, default the tailnet UI).
export default defineConfig({
  plugins: [react()],
  build: { outDir: '../src/housing_watch/web/dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { '/api': process.env.HW_API ?? 'http://100.79.4.41:8095' } },
})
