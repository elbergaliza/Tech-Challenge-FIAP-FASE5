// Testes do painel do corretor. O que importa travar aqui: a ordem padrão da
// lista (score desc, porque a pergunta é "quem eu ligo agora"), a faixa de
// temperatura que substituiu uma coluna, os rótulos que vêm do backend e a
// chamada de LLM que só pode sair no clique.

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Dashboard from "./Dashboard";
import { api } from "../services/api";
import type { DashboardResumo, FollowUpPendente, LeadResumo } from "../types";

vi.mock("../services/api", async (carregarOriginal) => {
  const real = await carregarOriginal<typeof import("../services/api")>();
  return {
    ...real,
    api: {
      resumoDashboard: vi.fn(),
      listarLeads: vi.fn(),
      followups: vi.fn(),
    },
  };
});

const mock = api as unknown as {
  resumoDashboard: ReturnType<typeof vi.fn>;
  listarLeads: ReturnType<typeof vi.fn>;
  followups: ReturnType<typeof vi.fn>;
};

function lead(parcial: Partial<LeadResumo>): LeadResumo {
  return {
    id: "lead-0001",
    nome: "Bruna Álvares",
    email: null,
    telefone: "21 99999-4410",
    intencao: "RENT",
    regiao: "Botafogo",
    faixa_preco: "5 mil",
    quartos: "2",
    urgencia: "high",
    status: "QUALIFICADO",
    score: 78,
    temperatura: "HOT",
    proxima_acao: null,
    origem: "chat",
    consentimento: true,
    criado_em: "2026-09-07T16:00:00",
    atualizado_em: "2026-09-07T16:00:00",
    ultima_mensagem_em: "2026-09-07T16:00:00",
    followups_enviados: 0,
    intencao_label: "aluguel",
    urgencia_label: "alta",
    faixa_preco_label: "R$ 5.000",
    temperatura_label: "QUENTE",
    status_label: "Qualificado",
    total_mensagens: 7,
    horas_sem_resposta: 3.4,
    ...parcial,
  };
}

const RESUMO: DashboardResumo = {
  total_leads: 12,
  leads_quentes: 3,
  leads_mornos: 5,
  leads_frios: 4,
  qualificados: 6,
  aguardando_followup: 2,
  agendamentos_proximos: 4,
  agendamentos_hoje: 1,
  total_mensagens: 87,
  total_imoveis: 140,
  taxa_qualificacao: 50,
  score_medio: 47.3,
  por_status: [{ chave: "QUALIFICADO", label: "Qualificado", total: 6 }],
  por_intencao: [{ chave: "RENT", label: "aluguel", total: 7 }],
  por_regiao: [{ chave: "Botafogo", label: "Botafogo", total: 3 }],
  ultimos_leads: [],
};

const LEADS = [
  lead({}),
  lead({ id: "lead-0002", nome: "Diego Fontes", temperatura: "WARM", temperatura_label: "MORNO", score: 55, status: "EM_ANDAMENTO", status_label: "Em andamento" }),
  lead({ id: "lead-0003", nome: null, temperatura: "COLD", temperatura_label: "FRIO", score: 20, status: "NOVO", status_label: "Novo", horas_sem_resposta: 72, urgencia_label: "baixa", intencao_label: "investimento", regiao: null, quartos: null }),
];

function montar() {
  return render(
    <MemoryRouter>
      <Dashboard />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  mock.resumoDashboard.mockReset().mockResolvedValue(RESUMO);
  mock.listarLeads.mockReset().mockResolvedValue(LEADS);
  mock.followups.mockReset().mockResolvedValue([]);
});

describe("números do topo", () => {
  it("mostra os KPIs que o backend já calculou", async () => {
    montar();

    expect(await screen.findByText("12")).toBeInTheDocument();
    expect(screen.getByText("50,0%")).toBeInTheDocument();
    expect(screen.getByText("6 qualificados")).toBeInTheDocument();
    expect(screen.getByText("1 hoje")).toBeInTheDocument();
  });

  it("não recalcula nada: taxa e contagens saem prontas da API", async () => {
    montar();
    await screen.findByText("12");

    expect(mock.resumoDashboard).toHaveBeenCalledTimes(1);
  });
});

describe("lista de leads", () => {
  it("pede por score desc, que é a ordem que responde 'quem eu ligo agora'", async () => {
    montar();

    await waitFor(() => expect(mock.listarLeads).toHaveBeenCalled());
    expect(mock.listarLeads.mock.calls[0][0]).toMatchObject({ ordenar_por: "score" });
  });

  it("carrega a temperatura na faixa da borda, não numa coluna", async () => {
    montar();

    const linha = (await screen.findByText("Bruna Álvares")).closest("li");
    // O CSS pinta `--faixa-cor` por `data-t`: sem o par classe + atributo, a
    // borda fica transparente e a lista perde o eixo de leitura.
    expect(linha).toHaveClass("faixa");
    expect(linha).toHaveAttribute("data-t", "HOT");

    expect(screen.getByText("Diego Fontes").closest("li")).toHaveAttribute(
      "data-t",
      "WARM",
    );
  });

  it("mostra o score, que é o critério da ordenação", async () => {
    montar();

    const linha = (await screen.findByText("Bruna Álvares")).closest("a")!;
    expect(within(linha).getByText("78")).toBeInTheDocument();
  });

  it("usa os rótulos traduzidos pelo backend, sem tabela própria", async () => {
    montar();

    const quente = (await screen.findByText("Bruna Álvares")).closest("a")!;
    expect(within(quente).getByText("Qualificado")).toBeInTheDocument();
    expect(within(quente).getByText(/aluguel · Botafogo · 2q/)).toBeInTheDocument();
    expect(within(quente).getByText("alta")).toBeInTheDocument();

    const morno = screen.getByText("Diego Fontes").closest("a")!;
    expect(within(morno).getByText("Em andamento")).toBeInTheDocument();

    const frio = screen.getByText("(sem nome)").closest("a")!;
    // Sem região nem quartos, a coluna de interesse não pode ficar vazia.
    expect(within(frio).getByText("investimento")).toBeInTheDocument();
  });

  it("resume o silêncio em hora e dia", async () => {
    montar();

    const quente = (await screen.findByText("Bruna Álvares")).closest("a")!;
    expect(within(quente).getByText("3h")).toBeInTheDocument();

    const frio = screen.getByText("(sem nome)").closest("a")!;
    expect(within(frio).getByText("3d")).toBeInTheDocument();
  });

  it("liga cada linha ao detalhe do lead", async () => {
    montar();

    const link = (await screen.findByText("Bruna Álvares")).closest("a");
    expect(link).toHaveAttribute("href", "/painel/leads/lead-0001");
  });

  it("mostra lead sem nome sem quebrar a linha", async () => {
    montar();
    expect(await screen.findByText("(sem nome)")).toBeInTheDocument();
  });
});

describe("filtros", () => {
  it("refaz a busca com a intenção escolhida", async () => {
    montar();
    await screen.findByText("Bruna Álvares");

    await userEvent.setup().click(screen.getByRole("button", { name: "Compra" }));

    await waitFor(() =>
      expect(mock.listarLeads.mock.calls.at(-1)?.[0]).toMatchObject({ intencao: "BUY" }),
    );
  });

  it("espera a pessoa parar de digitar antes de buscar", async () => {
    montar();
    await screen.findByText("Bruna Álvares");

    const antes = mock.listarLeads.mock.calls.length;
    await userEvent
      .setup()
      .type(screen.getByPlaceholderText("Nome, e-mail ou telefone"), "maria");

    // Uma requisição por tecla seria cinco: o debounce colapsa em uma.
    await waitFor(() =>
      expect(mock.listarLeads.mock.calls.at(-1)?.[0]).toMatchObject({ busca: "maria" }),
    );
    expect(mock.listarLeads.mock.calls.length - antes).toBeLessThan(5);
  });

  it("explica o vazio de filtro diferente do vazio de base nova", async () => {
    mock.listarLeads.mockResolvedValue([]);
    montar();

    expect(await screen.findByText("Nenhum lead ainda")).toBeInTheDocument();
    expect(screen.getByText(/ordenado por score/)).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Aluguel" }));

    expect(await screen.findByText("Nenhum lead com esses filtros")).toBeInTheDocument();
  });
});

describe("follow-ups", () => {
  const pendente: FollowUpPendente = {
    lead_id: "lead-0009",
    lead_nome: "Carla Nunes",
    horas_de_silencio: 50,
    tentativa: 1,
    tom: "leve",
    motivo: "Parou de responder depois de ver os imóveis",
    canal: null,
    texto_sugerido: null,
  };

  it("lista quem precisa de atenção, do mais calado ao menos", async () => {
    mock.followups.mockResolvedValue([pendente]);
    montar();

    expect(await screen.findByText("Carla Nunes")).toBeInTheDocument();
    expect(screen.getByText(pendente.motivo)).toBeInTheDocument();
    expect(screen.getByText("2d calado")).toBeInTheDocument();
  });

  it("não gasta cota de LLM no carregamento da lista", async () => {
    mock.followups.mockResolvedValue([pendente]);
    montar();
    await screen.findByText("Carla Nunes");

    // Uma chamada de LLM POR LEAD sairia daqui se o `com_texto` viesse ligado.
    expect(mock.followups).toHaveBeenCalledWith();
    expect(mock.followups).not.toHaveBeenCalledWith(true);
  });

  it("gera o texto sugerido só quando o corretor abre o item", async () => {
    mock.followups.mockResolvedValue([pendente]);
    montar();
    await screen.findByText("Carla Nunes");

    mock.followups.mockResolvedValue([
      { ...pendente, texto_sugerido: "Oi Carla! Achei mais duas opções em Botafogo." },
    ]);
    await userEvent.setup().click(screen.getByText("ver texto sugerido"));

    expect(
      await screen.findByText(/Achei mais duas opções em Botafogo/),
    ).toBeInTheDocument();
    expect(mock.followups).toHaveBeenCalledWith(true);
  });
});

describe("acessibilidade dos filtros", () => {
  it("todo filtro tem nome acessível", async () => {
    montar();
    await screen.findByText("Bruna Álvares");

    expect(
      screen.getByRole("textbox", { name: /Buscar lead/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Filtrar por temperatura" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Filtrar por status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Ordenar os leads" }),
    ).toBeInTheDocument();
  });
});

describe("quando a API cai", () => {
  it("mostra o erro com a saída de tentar de novo", async () => {
    const { ApiError } = await import("../services/api");
    mock.resumoDashboard.mockRejectedValue(new ApiError(0, "API fora do ar"));
    montar();

    expect(await screen.findByRole("alert")).toHaveTextContent("API fora do ar");
    expect(screen.getByRole("button", { name: "Tentar de novo" })).toBeInTheDocument();
  });
});
