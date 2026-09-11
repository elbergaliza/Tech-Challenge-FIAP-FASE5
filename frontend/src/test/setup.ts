// `.../vitest` em vez de só `jest-dom`: essa entrada registra os matchers no
// `expect` do Vitest e traz a tipagem deles junto.
import "@testing-library/jest-dom/vitest";

import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Cada teste monta a sua árvore; sem isto a segunda montagem acha dois
// elementos com o mesmo texto e o erro não parece ser de vazamento.
afterEach(() => {
  cleanup();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

// O jsdom não implementa scrollIntoView, e o chat chama isso a cada turno para
// acompanhar a conversa. Sem o stub, o teste falha por um detalhe do ambiente
// e não por um defeito da tela.
Element.prototype.scrollIntoView = () => {};
