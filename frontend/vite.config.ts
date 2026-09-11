import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 8000 é o backend; strictPort evita o Vite escolher 5174 sozinho, que
    // ficaria de fora do CORS_ORIGINS do backend e quebraria toda chamada.
    port: 5173,
    strictPort: true,
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    restoreMocks: true,
    // A jornada de ponta a ponta vive em `vitest.e2e.config.ts`: ela precisa
    // do backend no ar e gasta cota. Fica fora do `npm test`.
    exclude: ["node_modules/**", "dist/**", "src/e2e/**"],
  },
});
