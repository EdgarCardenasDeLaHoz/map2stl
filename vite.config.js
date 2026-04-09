import { defineConfig } from 'vite';

export default defineConfig({
  // Serve from static/ so Vite can find main.js and modules/
  root: 'static',

  // Where to write the production build
  build: {
    outDir: '../dist',
    emptyOutDir: true,
    rollupOptions: {
      input: 'static/js/main.js',
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
