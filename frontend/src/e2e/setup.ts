// Setup da jornada de ponta a ponta.
//
// Igual ao `src/test/setup.ts`, MENOS o `localStorage.clear()`: aqui os testes
// são passos de uma mesma jornada, e o `lead_id` guardado é justamente o que
// liga um passo ao seguinte. Limpar entre eles fazia cada bloco começar como
// visitante novo, e a jornada nunca saía do primeiro turno.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

Element.prototype.scrollIntoView = () => {};
