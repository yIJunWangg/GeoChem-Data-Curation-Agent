import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8765' },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
    // AG Grid is intentionally isolated into a long-lived vendor chunk. Its
    // gzip payload is well below this raw-size threshold and no longer blocks
    // the application shell from loading independently.
    chunkSizeWarningLimit: 1000,
    rollupOptions: {
      output: {
        manualChunks: {
          'react-vendor': ['react', 'react-dom', 'react-router-dom'],
          'grid-vendor': ['ag-grid-community', 'ag-grid-react'],
          'pdf-vendor': ['pdfjs-dist', 'react-pdf'],
          'auth-vendor': ['oidc-client-ts'],
        },
      },
    },
  },
})
