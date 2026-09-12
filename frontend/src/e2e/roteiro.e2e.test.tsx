// O roteiro de teste manual, executado.
//
// Diferente do resto da suíte, aqui não há dublê nenhum: são as telas de
// verdade, o cliente HTTP de verdade e o backend no ar. É o mais perto que dá
// para chegar do `docs/roteiro-de-teste-manual.md` sem um navegador.
//
//   npm run test:e2e
//
// O que ISTO cobre: o comportamento. O que ISTO NÃO cobre, e continua
// dependendo de olho humano no navegador: como a tela fica. O jsdom não faz
// layout nem aplica CSS, então tema, contraste, responsivo, anel de foco e o
// flash de tema no recarregamento ficam fora. Os blocos 1 e 17 do roteiro são
// inteiramente visuais e não têm equivalente aqui.
//
// Ele ESCREVE no banco: cria um lead pelo chat e apaga no fim. Cada mensagem
// gasta uma chamada do Gemini, se houver chave configurada.

import { StrictMode } from "react";

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import App from "../App";
import { api } from "../services/api";

const BASE = "http://localhost:8000";

// O selo do id na barra da conversa mostra o id ENCURTADO, e guarda o valor
// inteiro no `title`.
//
// O id deixou de ser "lead-0011" e virou um hexadecimal de 21 caracteres,
// porque sequencial ele transformava as rotas de LGPD num oraculo sobre
// qualquer lead. Com 21 caracteres, ele sozinho ocupava metade da barra num
// celular e espremia o botao "Nova conversa" em duas linhas; por isso o texto
// e cortado. Procurar pelo `title` testa a mesma coisa que antes (o id certo
// esta na tela) sem depender de quantos caracteres cabem.
function selarIdNaTela(leadId: string) {
  const selo = document.querySelector(`[title="${leadId}"]`);
  expect(selo, `nao achei o selo do lead ${leadId} na barra da conversa`).not.toBeNull();
  return selo as HTMLElement;
}

// O id nasce no primeiro turno e atravessa a jornada inteira.
let leadId: string | null = null;

beforeAll(async () => {
  try {
    const saude = await fetch(`${BASE}/health`);
    if (!saude.ok) throw new Error(`health respondeu ${saude.status}`);

    const dados = await saude.json();
    console.log(
      `[e2e] backend no ar, agente: ${dados.ia?.agente}` +
        (dados.ia?.agente === "mock" ? " (sem chave, respostas simuladas)" : ""),
    );
  } catch (erro) {
    // Falhar alto: uma suíte de ponta a ponta que passa com o backend fora
    // não testou nada, e é pior que não existir.
    throw new Error(
      `Backend fora do ar em ${BASE}. Suba com ".venv\\Scripts\\python.exe ` +
        `backend/run.py" e rode de novo. (${(erro as Error).message})`,
    );
  }
});

afterAll(async () => {
  // Limpeza no lugar certo: roda mesmo se um passo falhar no meio.
  if (!leadId) return;
  try {
    await api.apagarLead(leadId);
    console.log(`[e2e] lead de teste ${leadId} apagado`);
  } catch {
    console.warn(`[e2e] atenção: o lead ${leadId} ficou no banco`);
  }
});

function abrir(rota = "/") {
  // A jornada é uma sequência: o passo anterior deixou o lead no navegador.
  // Só preenche se estiver vazio, para não atropelar o teste que coloca um id
  // inválido de propósito.
  if (leadId && !localStorage.getItem("lead_id")) {
    localStorage.setItem("lead_id", leadId);
  }

  // Com `StrictMode`, como o `main.tsx` monta: em desenvolvimento ele roda
  // cada efeito duas vezes, e é ali que mora uma classe inteira de defeito.
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={[rota]}>
        <App />
      </MemoryRouter>
    </StrictMode>,
  );
}

const campoMensagem = () => screen.getByRole("textbox", { name: "Sua mensagem" });

// Manda uma mensagem e espera a bolha do agente aparecer. Devolve o texto da
// resposta, para o teste poder afirmar sobre ela.
async function conversar(texto: string): Promise<string> {
  const usuario = userEvent.setup();
  const antes = document.querySelectorAll("[data-papel='assistant']").length;

  await usuario.type(campoMensagem(), texto);
  await usuario.click(screen.getByRole("button", { name: "Enviar" }));

  await waitFor(
    () => {
      const agora = document.querySelectorAll("[data-papel='assistant']").length;
      expect(agora).toBeGreaterThan(antes);
    },
    { timeout: 60_000 },
  );

  const bolhas = document.querySelectorAll("[data-papel='assistant']");
  return bolhas[bolhas.length - 1].textContent ?? "";
}

describe("bloco 2: primeira mensagem e consentimento", () => {
  it("pede o aceite, cria o lead e guarda o id", async () => {
    abrir();

    // O roteiro manda começar sem lead.
    expect(localStorage.getItem("lead_id")).toBeNull();
    const aceite = screen.getByRole("checkbox", {
      name: /me procure depois sobre imóveis/i,
    });
    expect(aceite).toBeInTheDocument();

    const usuario = userEvent.setup();
    await usuario.click(aceite);

    const resposta = await conversar("quero alugar um apartamento de 2 quartos em Botafogo");

    expect(resposta.length).toBeGreaterThan(10);
    // Isto é o que impede um lead duplicado no funil a cada recarga.
    leadId = localStorage.getItem("lead_id");
    expect(leadId).toMatch(/^lead-/);
    selarIdNaTela(leadId!);

    // O aceite não é pedido de novo depois do primeiro turno.
    expect(
      screen.queryByRole("checkbox", { name: /me procure depois/i }),
    ).toBeNull();
  });
});

describe("blocos 3 e 4: memória e RAG", () => {
  it("anota o que o lead disse e sugere imóveis com o porquê", async () => {
    abrir();
    await waitFor(() => selarIdNaTela(leadId!));

    await conversar("meu orçamento é até 4 mil por mês e preciso para esse mês");

    // Bloco 3: o chip é a prova visual de que existe memória.
    await waitFor(
      () => expect(document.querySelectorAll(".chip-novidade").length).toBeGreaterThan(0),
      { timeout: 30_000 },
    );

    // Bloco 4: o card do RAG com a justificativa da escolha.
    const porques = await waitFor(
      () => {
        const achados = screen.getAllByText(/Por que este:/);
        expect(achados.length).toBeGreaterThan(0);
        return achados;
      },
      { timeout: 30_000 },
    );
    expect(porques[0].closest("p")?.textContent).toMatch(/Botafogo|quartos|orçamento/i);

    // O painel do perfil acompanha.
    expect(screen.getByText("Botafogo")).toBeInTheDocument();
  });
});

describe("bloco 5: reabrir a conversa", () => {
  it("recarregar traz o histórico e não cria outro lead", async () => {
    const antes = await api.listarLeads({ limite: 200 });

    // Montar de novo é o equivalente ao F5: o localStorage sobrevive.
    const tela = abrir();
    await waitFor(
      () => expect(document.querySelectorAll("[data-papel]").length).toBeGreaterThan(2),
      { timeout: 30_000 },
    );

    selarIdNaTela(leadId!);
    expect(
      screen.getByText(/quero alugar um apartamento de 2 quartos em Botafogo/),
    ).toBeInTheDocument();

    const depois = await api.listarLeads({ limite: 200 });
    expect(depois.length).toBe(antes.length);

    tela.unmount();
  });

  it("descarta o lead_id que o backend não conhece mais", async () => {
    localStorage.setItem("lead_id", "lead-inexistente-9999");
    abrir();

    await waitFor(() => expect(localStorage.getItem("lead_id")).toBeNull(), {
      timeout: 30_000,
    });
    expect(
      screen.getByRole("checkbox", { name: /me procure depois sobre imóveis/i }),
    ).toBeInTheDocument();

    localStorage.setItem("lead_id", leadId!);
  });
});

describe("bloco 7: LGPD pela tela do lead", () => {
  it("mostra o estado do contato e deixa revogar sem apagar a conversa", async () => {
    abrir();
    const usuario = userEvent.setup();

    // O aceite foi dado no bloco 2, então o controle abre em "autorizado".
    const revogar = await waitFor(
      () => screen.getByRole("button", { name: "Não quero mais ser procurado" }),
      { timeout: 30_000 },
    );

    await usuario.click(revogar);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Pode me procurar" })).toBeInTheDocument(),
    );

    // O que importa é ter chegado nas DUAS casas do consentimento: a coluna e
    // a memória da Parte 2, que é quem decide o follow-up.
    const lead = await api.detalheLead(leadId!);
    expect(lead.consentimento).toBe(false);

    const pacote = (await api.exportarLead(leadId!)) as {
      memoria?: { consent?: { granted?: boolean } };
    };
    expect(pacote.memoria?.consent?.granted).toBe(false);

    // Revogar não é excluir: a conversa continua de pé.
    expect(lead.mensagens.length).toBeGreaterThan(2);

    // Devolve o aceite, para o resto da jornada seguir como o roteiro prevê.
    await usuario.click(screen.getByRole("button", { name: "Pode me procurar" }));
    await waitFor(async () =>
      expect((await api.detalheLead(leadId!)).consentimento).toBe(true),
    );
  });

  it("exporta os dados do lead num JSON com conteúdo", async () => {
    const pacote = (await api.exportarLead(leadId!)) as Record<string, unknown>;

    expect(pacote).toHaveProperty("mensagens");
    expect(pacote).toHaveProperty("memoria");
  });
});

describe("bloco 10: a lista de leads no painel", () => {
  it("mostra o lead com a faixa de temperatura e liga para o detalhe", async () => {
    abrir("/painel");

    const linha = await waitFor(
      () => {
        const link = document.querySelector(`a[href="/painel/leads/${leadId}"]`);
        expect(link).not.toBeNull();
        return link!;
      },
      { timeout: 30_000 },
    );

    const item = linha.closest("li")!;
    expect(item).toHaveClass("faixa");
    // A cor da borda sai deste atributo: sem ele a lista perde o eixo de
    // leitura que substituiu uma coluna.
    expect(["HOT", "WARM", "COLD"]).toContain(item.getAttribute("data-t"));

    // Score visível, porque é ele que ordena a lista.
    expect(within(linha as HTMLElement).getByText(/^\d+$/)).toBeInTheDocument();
  });
});

describe("bloco 13: o resumo do corretor", () => {
  it("abre o detalhe com a próxima ação e os fatores do score", async () => {
    abrir(`/painel/leads/${leadId}`);

    await waitFor(() => expect(screen.getByText("Resumo para o corretor")).toBeInTheDocument(), {
      timeout: 30_000,
    });

    // O heurístico é o padrão: não pode ter gasto cota para abrir a tela.
    expect(screen.getByRole("button", { name: "Melhorar com IA" })).toBeInTheDocument();

    // As três coisas que mudam a próxima ligação.
    expect(screen.getByText(/Como o score \d+ foi montado/)).toBeInTheDocument();
    expect(screen.getByText("Perfil qualificado")).toBeInTheDocument();
    expect(screen.getByText(/Conversa \(\d+ mensagens\)/)).toBeInTheDocument();
  });
});

describe("bloco 14: ações do corretor", () => {
  it("move o status sem apagar o que o chat já sabia", async () => {
    const antes = await api.detalheLead(leadId!);
    expect(antes.regiao).toBeTruthy();

    abrir(`/painel/leads/${leadId}`);
    const usuario = userEvent.setup();

    await waitFor(() => expect(screen.getByText("Mover para")).toBeInTheDocument(), {
      timeout: 30_000,
    });
    await usuario.click(screen.getByRole("button", { name: "Qualificado" }));

    await waitFor(async () => {
      const depois = await api.detalheLead(leadId!);
      expect(depois.status).toBe("QUALIFICADO");
      // PATCH parcial: mexer no status não pode limpar o perfil.
      expect(depois.regiao).toBe(antes.regiao);
    });
  });

  it("agenda uma visita e promove o lead", async () => {
    abrir(`/painel/leads/${leadId}`);
    const usuario = userEvent.setup();

    await waitFor(() => expect(screen.getByText("Agendamentos")).toBeInTheDocument(), {
      timeout: 30_000,
    });
    await usuario.click(screen.getByRole("button", { name: "Novo agendamento" }));

    const quando = new Date(Date.now() + 5 * 24 * 3600 * 1000);
    const pad = (n: number) => String(n).padStart(2, "0");
    const local = `${quando.getFullYear()}-${pad(quando.getMonth() + 1)}-${pad(
      quando.getDate(),
    )}T15:00`;

    fireEvent.change(screen.getByLabelText("Data e hora"), {
      target: { value: local },
    });

    await usuario.type(screen.getByLabelText("Corretor"), "Teste e2e");
    await usuario.click(screen.getByRole("button", { name: "Agendar" }));

    await waitFor(
      async () => {
        const lead = await api.detalheLead(leadId!);
        expect(lead.agendamentos.length).toBeGreaterThan(0);
        // Agendar promove o lead sozinho, sem o corretor mover o status.
        expect(lead.status).toBe("AGENDADO");
      },
      { timeout: 30_000 },
    );
  });
});

describe("bloco 15: a agenda", () => {
  it("mostra o agendamento e cancela devolvendo o lead para qualificado", async () => {
    abrir("/painel/agenda");
    const usuario = userEvent.setup();

    // A agenda mostra os compromissos de TODOS os leads na janela, então o
    // teste precisa achar a linha deste lead. Procurar "Cancelar" solto
    // quebrava assim que existisse um segundo agendamento no banco.
    const linha = await waitFor(
      () => {
        const link = document.querySelector(`a[href="/painel/leads/${leadId}"]`);
        expect(link).not.toBeNull();
        return link!.closest("li")!;
      },
      { timeout: 30_000 },
    );

    await usuario.click(within(linha).getByRole("button", { name: "Cancelar" }));

    await waitFor(
      async () => {
        const lead = await api.detalheLead(leadId!);
        // Cancelar a única visita não pode deixar o lead preso em AGENDADO, ou
        // ele desaparece das listas de quem precisa de atenção.
        expect(lead.status).toBe("QUALIFICADO");
      },
      { timeout: 30_000 },
    );
  });
});

describe("bloco 16: o catálogo", () => {
  it("filtra por bairro com os valores que vieram da base", async () => {
    abrir("/painel/imoveis");

    await waitFor(() => expect(screen.getByText("Catálogo")).toBeInTheDocument(), {
      timeout: 30_000,
    });

    const bairros = screen.getByRole("combobox", { name: "Filtrar por bairro" });
    // Os selects não são arrays chumbados no código: vieram de /imoveis/filtros.
    await waitFor(() =>
      expect(bairros.querySelectorAll("option").length).toBeGreaterThan(5),
    );

    const total = await api.listarImoveis({ limite: 1 });
    const emBotafogo = await api.listarImoveis({ neighborhood: "Botafogo", limite: 1 });

    expect(emBotafogo.total).toBeGreaterThan(0);
    expect(emBotafogo.total).toBeLessThan(total.total);
  });
});
