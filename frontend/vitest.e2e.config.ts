import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Suíte separada de propósito: ela sobe as telas de verdade contra o backend
// no ar, cria um lead e gasta chamadas do Gemini. `npm test` continua
// hermético e rápido; isto roda quando alguém pede, com `npm run test:e2e`.
export default defineConfig({
  plugins: [react()],
  test: {
    include: ["src/e2e/**/*.test.tsx"],
    environment: "jsdom",
    setupFiles: ["./src/e2e/setup.ts"],
    css: false,
    // Um turno com Gemini leva de 2 a 6 segundos, e o primeiro depois de subir
    // o backend leva mais, porque constrói o índice do RAG.
    testTimeout: 90_000,
    hookTimeout: 60_000,
    // A jornada é uma sequência: um passo depende do lead que o anterior
    // criou, então nada de paralelismo aqui.
    fileParallelism: false,
    sequence: { concurrent: false },
  },
});
