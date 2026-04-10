import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/debate": "http://localhost:8000",
      "/agents": "http://localhost:8000",
      "/health": "http://localhost:8000",
      "/knowledge": "http://localhost:8000",
      "/decisions": "http://localhost:8000",
      "/weights": "http://localhost:8000",
    },
  },
});
