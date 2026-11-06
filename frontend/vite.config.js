import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { defineConfig } from 'vite'

// HTTPS is required for camera access on a phone; /api is proxied so the page has one origin.
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: { host: true, proxy: { '/api': 'http://127.0.0.1:8000' } },
})
