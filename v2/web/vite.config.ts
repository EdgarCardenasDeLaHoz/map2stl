import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    // Dev only. In production the same FastAPI process serves both the API and the
    // built page, so there is no proxy and no second origin.
    proxy: {
      '/api': { target: 'http://127.0.0.1:9100', changeOrigin: true }
    }
  }
});
