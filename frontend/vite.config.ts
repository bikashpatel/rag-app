import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, run the backend on :8000 (or via docker compose) and use `npm run dev`.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: false },
    },
  },
});
