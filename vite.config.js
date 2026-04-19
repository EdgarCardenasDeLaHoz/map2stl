import { defineConfig } from 'vite';
import { resolve } from 'path';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],

  test: {
    environment: 'node',
    root: resolve(__dirname, 'tests/js'),
    include: ['**/*.test.js'],
  },

  // Root is the static dir — Vite resolves imports from here.
  // index.html is served by FastAPI (Jinja template), not Vite.
  root: 'app/client/static',

  // Production build — bundle main.js + vue-main.js into dist/
  build: {
    outDir: resolve(__dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main:     resolve(__dirname, 'app/client/static/js/main.js'),
        'vue-main': resolve(__dirname, 'app/client/static/js/vue/main-vue.ts'),
      },
      output: {
        // Stable names so the FastAPI template can reference them with fixed paths.
        entryFileNames: 'js/[name].js',
        chunkFileNames: 'js/[name]-[hash].js',
        assetFileNames: '[ext]/[name]-[hash][extname]',
      },
    },
  },

  // Dev server — used only for iterating on JS with HMR.
  server: {
    port: 5173,
    proxy: {
      '/api':    { target: 'http://localhost:9000', changeOrigin: true },
      '/static': { target: 'http://localhost:9000', changeOrigin: true },
    },
  },

  resolve: {
    alias: {
      '@app-vue': resolve(__dirname, 'app/client/static/js/vue'),
    },
  },
});
