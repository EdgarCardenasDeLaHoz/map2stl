import adapter from '@sveltejs/adapter-static';

/** @type {import('@sveltejs/kit').Config} */
export default {
  kit: {
    // The FastAPI server hosts the built files directly. No Node runtime in production;
    // one process serves the API and the page.
    adapter: adapter({ pages: 'build', assets: 'build', fallback: 'index.html', precompress: false }),
    alias: { $lib: 'src/lib' }
  }
};
