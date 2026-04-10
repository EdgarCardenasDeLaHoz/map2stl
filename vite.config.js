import { defineConfig } from 'vite';
import { resolve } from 'path';

export default defineConfig({
  // Root is the static dir — Vite resolves imports from here.
  // index.html is served by FastAPI (Jinja template), not Vite.
  root: 'app/client/static',

  // Production build — bundle main.js and all modules into dist/
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      // Entry point relative to root
      input: resolve(__dirname, 'app/client/static/js/main.js'),
      output: {
        // Keep chunk names stable across builds so the FastAPI template
        // can reference them with a fixed path.
        entryFileNames: 'js/[name].js',
        chunkFileNames: 'js/[name]-[hash].js',
        assetFileNames: '[ext]/[name]-[hash][extname]',
      },
    },
  },

  // Dev server — used only for iterating on JS with HMR.
  // Start FastAPI on port 9000 first; Vite proxies API calls there.
  // Then open http://localhost:5173 (Vite will NOT render the Jinja template —
  // use the FastAPI URL http://localhost:9000 for full integration testing).
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:9000', changeOrigin: true },
      '/static': { target: 'http://localhost:9000', changeOrigin: true },
    },
  },
});
