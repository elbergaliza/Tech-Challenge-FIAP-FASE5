// O cliente é o único lugar do front que fala HTTP, então é aqui que os
// contratos chatos da API precisam estar travados: filtro vazio que não pode
// virar `?status=`, o `detail` do 422 que é lista e não string, e as duas
// rotas que gastam cota de LLM e só podem sair com o parâmetro explícito.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./api";

function responder(corpo: unknown, init: { status?: number } = {}) {
  const status = init.status ?? 200;
  return new Response(status === 204 ? null : JSON.stringify(corpo), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let chamadas: { url: string; init?: RequestInit }[] = [];

beforeEach(() => {
  chamadas = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      chamadas.push({ url: String(url), init });
      return Promise.resolve(responder({ ok: true }));
    }),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const ultimaUrl = () => new URL(chamadas.at(-1)!.url);

describe("montagem da URL", () => {
  it("não manda filtro vazio, que faria o backend filtrar por string vazia", async () => {
    await api.listarLeads({ status: "", temperatura: undefined, busca: "maria" });

    const url = ultimaUrl();
    expect(url.pathname).toBe("/leads");
    expect(url.searchParams.has("status")).toBe(false);
    expect(url.searchParams.has("temperatura")).toBe(false);
    expect(url.searchParams.get("busca")).toBe("maria");
  });

  it("manda os filtros que existem, com os nomes que o backend espera", async () => {
    await api.listarLeads({
      temperatura: "HOT",
      status: "QUALIFICADO",
      intencao: "BUY",
      ordenar_por: "score",
      limite: 100,
    });

    const url = ultimaUrl();
    expect(url.searchParams.get("temperatura")).toBe("HOT");
    expect(url.searchParams.get("ordenar_por")).toBe("score");
    expect(url.searchParams.get("limite")).toBe("100");
  });

  it("escapa o id do lead no caminho", async () => {
    await api.historico("lead/0001 x");
    expect(ultimaUrl().pathname).toBe("/chat/lead%2F0001%20x/historico");
  });

  it("aceita zero como filtro de preço, que não é ausência", async () => {
    await api.listarImoveis({ preco_min: 0, bedrooms_min: 0 });

    const url = ultimaUrl();
    expect(url.searchParams.get("preco_min")).toBe("0");
    expect(url.searchParams.get("bedrooms_min")).toBe("0");
  });
});

describe("as duas rotas que gastam cota de LLM", () => {
  it("não liga o resumo com IA sem pedido explícito", async () => {
    await api.detalheLead("lead-0001");
    expect(ultimaUrl().searchParams.has("resumo_ia")).toBe(false);
  });

  it("liga quando o corretor pede", async () => {
    await api.detalheLead("lead-0001", true);
    expect(ultimaUrl().searchParams.get("resumo_ia")).toBe("true");
  });

  it("não liga o texto sugerido do follow-up por padrão", async () => {
    await api.followups();
    expect(ultimaUrl().searchParams.has("com_texto")).toBe(false);

    await api.followups(true);
    expect(ultimaUrl().searchParams.get("com_texto")).toBe("true");
  });
});

describe("corpo e método", () => {
  it("manda o turno do chat como JSON, com o content-type", async () => {
    await api.enviarMensagem({ lead_id: null, mensagem: "oi", consentimento: true });

    const { init } = chamadas.at(-1)!;
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(JSON.parse(String(init?.body))).toEqual({
      lead_id: null,
      mensagem: "oi",
      consentimento: true,
    });
  });

  it("manda o consentimento no PATCH, que o backend espelha na memória da IA", async () => {
    await api.atualizarLead("lead-0001", { consentimento: true });

    const { init } = chamadas.at(-1)!;
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ consentimento: true });
  });

  it("faz PATCH parcial: só o campo alterado sai no corpo", async () => {
    await api.atualizarLead("lead-0001", { status: "DESCARTADO" });

    const { init } = chamadas.at(-1)!;
    expect(init?.method).toBe("PATCH");
    expect(JSON.parse(String(init?.body))).toEqual({ status: "DESCARTADO" });
  });
});

describe("erros", () => {
  it("transforma o detail em lista do 422 numa frase legível", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          responder(
            {
              detail: [
                {
                  type: "string_too_short",
                  loc: ["body", "mensagem"],
                  msg: "String should have at least 1 character",
                },
              ],
            },
            { status: 422 },
          ),
        ),
      ),
    );

    const falha = await api
      .enviarMensagem({ lead_id: null, mensagem: "" })
      .catch((erro: unknown) => erro);

    expect(falha).toBeInstanceOf(ApiError);
    expect((falha as ApiError).status).toBe(422);
    // O que NÃO pode acontecer é "[object Object]" na tela.
    expect((falha as ApiError).message).toBe(
      "mensagem: String should have at least 1 character",
    );
  });

  it("usa o detail em string quando o backend manda uma", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(responder({ detail: "Lead não encontrado" }, { status: 404 })),
      ),
    );

    const falha = await api.detalheLead("lead-9999").catch((erro: unknown) => erro);
    expect((falha as ApiError).message).toBe("Lead não encontrado");
  });

  it("tem mensagem própria para 404 sem detail", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(responder({}, { status: 404 }))));

    const falha = await api.detalheLead("lead-9999").catch((erro: unknown) => erro);
    expect((falha as ApiError).status).toBe(404);
    expect((falha as ApiError).message).toBe("Não encontrado.");
  });

  it("separa rede caída de erro da API, e diz onde tentou falar", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("failed to fetch"))));

    const falha = await api.saude().catch((erro: unknown) => erro);
    expect((falha as ApiError).status).toBe(0);
    expect((falha as ApiError).message).toContain("localhost:8000");
    expect((falha as ApiError).message).toContain("backend está no ar");
  });
});

describe("respostas sem corpo", () => {
  it("aceita o 204 do DELETE sem tentar parsear JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response(null, { status: 204 }))),
    );

    await expect(api.apagarLead("lead-0001")).resolves.toBeUndefined();
  });
});
