import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.RI_API ?? "http://localhost:8000";      // RI_API=http://localhost:8001 previews against the imported lead list

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": api, "/webhooks": api } },
  build: { sourcemap: false },
});
