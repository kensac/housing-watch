import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Built into the Python package, which serves it (src/housing_watch/web/app.py). `npm run dev` proxies
// /graphql and the PDF route to a running `serve` (HW_API), by default a local one started with HW_WEB_PORT=8095.
// The production UI is refused: a view saved or a tracker row linked from the dev server would write production.
const PRODUCTION = '100.79.4.41'
const HW_API = process.env.HW_API ?? 'http://127.0.0.1:8095'
if (new URL(HW_API).hostname === PRODUCTION) {
  throw new Error(`HW_API=${HW_API} is the production UI. Point the dev server at a local \`serve\` instead.`)
}

// cssTarget: browsers with native light-dark(). Older targets make lightningcss lower it to two var() fallbacks,
// which, with no color-scheme rule of ours to switch them, both apply: `border-color: <light> <dark>`.
export default defineConfig({
  plugins: [react()],
  build: { cssTarget: ['chrome123', 'edge123', 'firefox120', 'safari17.5'], outDir: '../src/housing_watch/web/dist', emptyOutDir: true, chunkSizeWarningLimit: 1500 },
  server: { proxy: { '/graphql': HW_API, '/api': HW_API } },
})
