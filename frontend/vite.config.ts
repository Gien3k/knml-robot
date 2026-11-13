import { defineConfig } from "vite";
import tailwindcss from "@tailwindcss/vite";

// `npm run dev` -> http://localhost:5173 z proxy do backendu w Dockerze (port 8000)
export default defineConfig({
  plugins: [tailwindcss()],
  server: {
    proxy: {
      "/api": "http://localhost:8000",
      "/branding": "http://localhost:8000",
      "/ws": { target: "ws://localhost:8000", ws: true },
    },
  },
  build: { target: "es2022", chunkSizeWarningLimit: 800 },
});
