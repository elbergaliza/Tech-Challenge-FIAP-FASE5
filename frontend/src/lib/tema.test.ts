// O tema tem três estados, e o que mais quebra na prática é o terceiro:
// "sistema" precisa ser a AUSÊNCIA do atributo, senão o `prefers-color-scheme`
// do CSS nunca volta a mandar.

import { beforeEach, describe, expect, it, vi } from "vitest";

import { aplicarTema, lerTema } from "./tema";

const raiz = document.documentElement;

beforeEach(() => {
  localStorage.clear();
  raiz.removeAttribute("data-theme");
});

describe("lerTema", () => {
  it("nasce escuro quando não há escolha guardada", () => {
    expect(lerTema()).toBe("escuro");
  });

  it("lê a escolha guardada", () => {
    localStorage.setItem("sdr-tema", "claro");
    expect(lerTema()).toBe("claro");

    localStorage.setItem("sdr-tema", "sistema");
    expect(lerTema()).toBe("sistema");
  });

  it("ignora valor estragado no storage em vez de aplicar um tema inválido", () => {
    localStorage.setItem("sdr-tema", "roxo");
    expect(lerTema()).toBe("escuro");
  });

  it("sobrevive a localStorage bloqueado (navegação privada)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("acesso negado");
    });

    expect(lerTema()).toBe("escuro");
  });
});

describe("aplicarTema", () => {
  it("estampa o atributo que o CSS espera", () => {
    aplicarTema("escuro");
    expect(raiz.dataset.theme).toBe("dark");

    aplicarTema("claro");
    expect(raiz.dataset.theme).toBe("light");
  });

  it("remove o atributo em 'sistema', devolvendo a decisão ao navegador", () => {
    aplicarTema("claro");
    aplicarTema("sistema");

    expect(raiz.hasAttribute("data-theme")).toBe(false);
  });

  it("guarda a escolha para o script do index.html achar no próximo boot", () => {
    aplicarTema("claro");
    expect(localStorage.getItem("sdr-tema")).toBe("claro");
  });

  it("aplica na tela mesmo se não conseguir guardar", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("cota cheia");
    });

    expect(() => aplicarTema("claro")).not.toThrow();
    expect(raiz.dataset.theme).toBe("light");
  });
});
