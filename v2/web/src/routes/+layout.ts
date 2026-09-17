// Fully static: no server-side rendering, no per-route Node runtime. The page is a
// client application that talks to the FastAPI server over the API described in
// v2/server/main.py.
export const prerender = true;
export const ssr = false;
