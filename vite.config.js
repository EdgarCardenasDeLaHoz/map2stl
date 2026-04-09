import { defineConfig } from 'vite';

export default defineConfig({
  // Serve from app/client/static/ so Vite can find main.js and modules/
  root: 'app/client/static',

  // Where to write the production build
  build: {
    outDir: '../../../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'app/client/static/js/main.js',
    },
  },

  server: {
    port: 5173,
    // Proxy all /api/* requests to the FastAPI backend
    proxy: {
      '/api': {
        target: 'http://localhost:9000',
        changeOrigin: true,
      },
      '/static': {
        target: 'http://localhost:9000',
        changeOrigin: true,
      },
    },
  },
});
