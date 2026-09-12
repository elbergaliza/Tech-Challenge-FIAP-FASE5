// O selo do cabeçalho tem que envelhecer: consultado só na montagem, ele
// continuaria dizendo "API no ar" depois de o backend cair, e mandaria a pessoa
// procurar o defeito na tela errada.
//
// Os três primeiros testes usam timer de verdade, porque `waitFor` também
// depende de timer e não convive com o falso. Os que exercitam o intervalo usam
// timer falso e avançam o relógio à mão, dentro de `act`.

import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useSaude } from "./useSaude";
import { ApiError, api } from "../services/api";
import type { SaudeApi } from "../types";

vi.mock("../services/api", async (carregarOriginal) => {
  const real = await carregarOriginal<typeof import("../services/api")>();
  return { ...real, api: { saude: vi.fn() } };
});

const mock = api as unknown as { saude: ReturnType<typeof vi.fn> };

const SAUDE: SaudeApi = {
  status: "ok",
  banco: "ok",
  database_url: "app.db",
  ia: {
    parte2: true,
    agente: "gemini",
    llm: "gemini-2.5-flash",
    rag: { carregado: true, imoveis: 140, embedder: null },
    detalhe: "",
  },
};

beforeEach(() => {
  mock.saude.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

async function avancar(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("useSaude", () => {
  it("começa em null, que é 'ainda verificando' e não 'fora do ar'", () => {
    mock.saude.mockResolvedValue(SAUDE);
    const { result } = renderHook(() => useSaude());

    // A distinção existe para o cabeçalho não piscar "API offline" no primeiro
    // frame de todo carregamento.
    expect(result.current).toBeNull();
  });

  it("entrega a saúde depois da primeira consulta", async () => {
    mock.saude.mockResolvedValue(SAUDE);
    const { result } = renderHook(() => useSaude());

    await waitFor(() => expect(result.current).toEqual(SAUDE));
  });

  it("passa a false quando a API não responde", async () => {
    mock.saude.mockRejectedValue(new ApiError(0, "sem rede"));
    const { result } = renderHook(() => useSaude());

    await waitFor(() => expect(result.current).toBe(false));
  });

  it("reconsulta sozinho, então o selo acompanha a queda do backend", async () => {
    vi.useFakeTimers();
    mock.saude.mockResolvedValue(SAUDE);

    const { result } = renderHook(() => useSaude());
    await avancar(0);
    expect(result.current).toEqual(SAUDE);

    mock.saude.mockRejectedValue(new ApiError(0, "sem rede"));
    await avancar(60_000);

    expect(result.current).toBe(false);
  });

  it("com tudo no ar, consulta de minuto em minuto e não mais que isso", async () => {
    // A 20 segundos, o terminal de quem está com o uvicorn aberto vira uma
    // parede de `GET /health` e o log deixa de servir para depurar.
    vi.useFakeTimers();
    mock.saude.mockResolvedValue(SAUDE);

    renderHook(() => useSaude());
    await avancar(0);
    expect(mock.saude).toHaveBeenCalledTimes(1);

    await avancar(59_000);
    expect(mock.saude).toHaveBeenCalledTimes(1);

    await avancar(2_000);
    expect(mock.saude).toHaveBeenCalledTimes(2);
  });

  it("volta a verde depressa quando o backend sobe de novo", async () => {
    vi.useFakeTimers();
    mock.saude.mockRejectedValue(new ApiError(0, "sem rede"));

    const { result } = renderHook(() => useSaude());
    await avancar(0);
    expect(result.current).toBe(false);

    // Fora do ar a cadência aperta: quem acabou de subir o backend quer ver o
    // selo voltar sem recarregar.
    mock.saude.mockResolvedValue(SAUDE);
    await avancar(10_000);

    expect(result.current).toEqual(SAUDE);
  });

  it("para de consultar com a aba escondida e confere ao voltar", async () => {
    vi.useFakeTimers();
    mock.saude.mockResolvedValue(SAUDE);

    renderHook(() => useSaude());
    await avancar(0);
    expect(mock.saude).toHaveBeenCalledTimes(1);

    const escondida = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    document.dispatchEvent(new Event("visibilitychange"));
    await avancar(120_000);

    expect(mock.saude).toHaveBeenCalledTimes(1);

    escondida.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });

    expect(mock.saude).toHaveBeenCalledTimes(2);
  });

  it("para de consultar quando a tela sai", async () => {
    vi.useFakeTimers();
    mock.saude.mockResolvedValue(SAUDE);

    const { unmount } = renderHook(() => useSaude());
    await avancar(0);
    expect(mock.saude).toHaveBeenCalledTimes(1);

    unmount();
    await avancar(180_000);

    // Um intervalo vazado continuaria batendo na API por toda a sessão.
    expect(mock.saude).toHaveBeenCalledTimes(1);
  });
});
