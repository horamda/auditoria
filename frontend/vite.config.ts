import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// QA_API_URL permite apuntar el proxy a otra instancia del backend (por defecto :8000)
const api = process.env.QA_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, proxy: { "/api": api } },
  preview: { proxy: { "/api": api } },
});
