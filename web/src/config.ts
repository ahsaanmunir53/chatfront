// Where the API lives. Empty means the same origin (the API serves the app); a full URL
// when the app is hosted on its own (GitHub Pages) and the API sleeps on Render.
export const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

// The path the app is served under: "/" or "/chatfront/".
export const BASE_PATH = import.meta.env.BASE_URL;
