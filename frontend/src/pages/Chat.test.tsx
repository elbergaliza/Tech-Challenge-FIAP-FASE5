// Testes da tela de chat: o fluxo do primeiro "oi" até o agendamento.
//
// O que está travado aqui é o que quebraria em silêncio: o lead_id que precisa
// ser guardado, o consentimento que só vai na primeira mensagem, o id inválido
// que precisa ser descartado, e a regra de INVEST virar CONSULTORIA.

import { StrictMode } from "react";

import { fireEvent, render as montar, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import Chat from "./Chat";
import { ApiError, api } from "../services/api";
import type { ChatSaida, Mensagem } from "../types";

vi.mock("../services/api", async (carregarOriginal) => {
  // `ApiError` fica o original de propósito: a tela usa `instanceof` para
  // decidir o que fazer, e uma classe dublê passaria batido no teste e
  // falharia no navegador.
  const real = await carregarOriginal<typeof import("../services/api")>();
  return {
    ...real,
    api: {
      enviarMensagem: vi.fn(),
      historico: vi.fn(),
      exportarLead: vi.fn(),
      apagarLead: vi.fn(),
      agendar: vi.fn(),
      detalheLead: vi.fn(),
      atualizarLead: vi.fn(),
    },
  };
});

// O `main.tsx` monta o app dentro de `StrictMode`, e em desenvolvimento ele
// executa cada efeito duas vezes. Testar fora dele é testar outro app: foi
// assim que passou despercebido um guarda que impedia o histórico de carregar
// depois do F5.
function render(elemento: React.ReactElement) {
  return montar(<StrictMode>{elemento}</StrictMode>);
}

const mock = api as unknown as {
  enviarMensagem: ReturnType<typeof vi.fn>;
  historico: ReturnType<typeof vi.fn>;
  exportarLead: ReturnType<typeof vi.fn>;
  apagarLead: ReturnType<typeof vi.fn>;
  agendar: ReturnType<typeof vi.fn>;
  detalheLead: ReturnType<typeof vi.fn>;
  atualizarLead: ReturnType<typeof vi.fn>;
};

// Uma data FUTURA, derivada do relogio, e nao uma string chumbada.
//
// O seletor passou a ter `min` = agora, porque ele aceitava marcar visita para
// uma data ja passada e o backend confirmava com 201. Uma data chumbada no
// teste funciona ate o dia em que ela vira passado, e entao o teste quebra
// sozinho acusando o codigo errado. Tres dias a frente sempre e amanha.
const DAQUI_A_TRES_DIAS = (() => {
  const d = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T15:00`;
})();

const TURNO: ChatSaida = {
  lead_id: "lead-0001",
  resposta: "Ótima escolha. Qual a sua faixa de preço para o aluguel?",
  status: "EM_ANDAMENTO",
  score: 40,
  temperatura: "WARM",
  temperatura_label: "MORNO",
  perfil: { intent: "RENT", region: "Botafogo", bedrooms: "2" },
  // O backend manda o mesmo perfil pronto para ler; a tela usa este.
  perfil_label: { intent: "aluguel", region: "Botafogo", bedrooms: "2" },
  perfil_campos: { intent: "Intenção", region: "Região", bedrooms: "Quartos" },
  novidades: [
    {
      field: "region",
      from: null,
      to: "Botafogo",
      kind: "new",
      field_label: "Região",
      from_label: null,
      to_label: "Botafogo",
    },
  ],
  imoveis: [
    {
      id: "IMV-0086",
      title: "Apartamento de 2 quartos em Botafogo",
      neighborhood: "Botafogo",
      zone: "Zona Sul",
      deal_type: "RENTAL",
      price: 3700,
      bedrooms: 2,
      area_m2: 56.7,
      score: 1.017,
      reason: "no bairro pedido, exatamente 2 quartos",
    },
  ],
  proxima_acao: "Pedir telefone ou e-mail antes de avançar",
  origem: "gemini",
  sugerir_agendamento: false,
};

beforeEach(() => {
  localStorage.clear();
  mock.enviarMensagem.mockReset();
  mock.historico.mockReset();
  mock.agendar.mockReset();
  mock.detalheLead.mockReset();
  mock.atualizarLead.mockReset();
  mock.historico.mockResolvedValue([]);
  mock.detalheLead.mockResolvedValue({ consentimento: false });
});

async function escrever(texto: string) {
  const usuario = userEvent.setup();
  await usuario.type(screen.getByPlaceholderText("Escreva sua mensagem..."), texto);
  await usuario.click(screen.getByRole("button", { name: "Enviar" }));
  return usuario;
}

describe("primeira mensagem", () => {
  it("pede o consentimento antes de existir lead", () => {
    render(<Chat />);
    expect(screen.getByText(/me procure depois sobre imóveis/i)).toBeInTheDocument();
  });

  it("manda lead_id nulo e o aceite, e guarda o id que voltou", async () => {
    mock.enviarMensagem.mockResolvedValue(TURNO);
    render(<Chat />);

    const usuario = userEvent.setup();
    await usuario.click(screen.getByRole("checkbox"));
    await usuario.type(
      screen.getByPlaceholderText("Escreva sua mensagem..."),
      "quero alugar em Botafogo",
    );
    await usuario.click(screen.getByRole("button", { name: "Enviar" }));

    await waitFor(() =>
      expect(mock.enviarMensagem).toHaveBeenCalledWith({
        lead_id: null,
        mensagem: "quero alugar em Botafogo",
        consentimento: true,
      }),
    );

    // Sem isto, cada recarga da página criaria um lead duplicado no funil.
    await waitFor(() => expect(localStorage.getItem("lead_id")).toBe("lead-0001"));
  });

  it("não repete o consentimento nos turnos seguintes", async () => {
    mock.enviarMensagem.mockResolvedValue(TURNO);
    render(<Chat />);

    await escrever("oi");
    await waitFor(() => expect(mock.enviarMensagem).toHaveBeenCalledTimes(1));

    await escrever("2 quartos");
    await waitFor(() => expect(mock.enviarMensagem).toHaveBeenCalledTimes(2));

    expect(mock.enviarMensagem.mock.calls[1][0]).toEqual({
      lead_id: "lead-0001",
      mensagem: "2 quartos",
      consentimento: undefined,
    });
  });
});

describe("o que o payload do turno vira na tela", () => {
  beforeEach(() => {
    mock.enviarMensagem.mockResolvedValue(TURNO);
  });

  it("mostra a resposta do agente", async () => {
    render(<Chat />);
    await escrever("oi");

    expect(await screen.findByText(TURNO.resposta)).toBeInTheDocument();
  });

  it("pisca o chip do que a memória anotou", async () => {
    render(<Chat />);
    await escrever("oi");

    expect(await screen.findByText(/anotei: Região = Botafogo/)).toBeInTheDocument();
  });

  it("distingue correção de anotação nova", async () => {
    mock.enviarMensagem.mockResolvedValue({
      ...TURNO,
      novidades: [
        {
          field: "bedrooms",
          from: "2",
          to: "3",
          kind: "correction",
          field_label: "Quartos",
          from_label: "2",
          to_label: "3",
        },
      ],
    });

    render(<Chat />);
    await escrever("na verdade 3 quartos");

    const chip = await screen.findByText(/corrigi: Quartos = 3/);
    expect(chip).toBeInTheDocument();
    // O valor anterior fica no title: é o que prova que houve correção.
    expect(chip).toHaveAttribute("title", "Antes: 2");
  });

  it("renderiza o imóvel do RAG com o porquê da escolha", async () => {
    render(<Chat />);
    await escrever("oi");

    expect(
      await screen.findByText("Apartamento de 2 quartos em Botafogo"),
    ).toBeInTheDocument();
    expect(screen.getByText(/no bairro pedido, exatamente 2 quartos/)).toBeInTheDocument();
    expect(screen.getByText("IMV-0086")).toBeInTheDocument();
  });

  it("mostra o perfil que a IA montou, com a temperatura", async () => {
    render(<Chat />);
    await escrever("oi");

    expect(await screen.findByText(/MORNO/)).toBeInTheDocument();
    expect(screen.getByText(/· 40/)).toBeInTheDocument();
    expect(screen.getByText("Botafogo")).toBeInTheDocument();
  });

  it("mostra o perfil em português, não o enum cru", async () => {
    render(<Chat />);
    await escrever("oi");

    // Quem lê este painel é o lead, não o programador: "RENT" e "high" não
    // dizem nada para ele.
    expect(await screen.findByText("aluguel")).toBeInTheDocument();
    expect(screen.getByText("Quartos")).toBeInTheDocument();
    expect(screen.queryByText("RENT")).toBeNull();
  });

  it("cai no valor cru se o backend não mandar o rótulo", async () => {
    // Compatibilidade: um backend mais antigo não tem `perfil_label` nem
    // `perfil_campos`, e mostrar "RENT" é melhor do que apagar a informação.
    mock.enviarMensagem.mockResolvedValue({
      ...TURNO,
      perfil_label: {},
      perfil_campos: {},
      novidades: [{ field: "price_range", from: null, to: "5k", kind: "new" }],
    });

    render(<Chat />);
    await escrever("oi");

    expect(await screen.findByText("RENT")).toBeInTheDocument();
    expect(screen.getByText(/anotei: price range = 5k/)).toBeInTheDocument();
  });

  it("mostra o chip em português, com o valor legível", async () => {
    mock.enviarMensagem.mockResolvedValue({
      ...TURNO,
      novidades: [
        {
          field: "urgency",
          from: "low",
          to: "high",
          kind: "correction",
          field_label: "Urgência",
          from_label: "baixa",
          to_label: "alta",
        },
      ],
    });

    render(<Chat />);
    await escrever("preciso mudar esse mês");

    const chip = await screen.findByText(/corrigi: Urgência = alta/);
    expect(chip).toHaveAttribute("title", "Antes: baixa");
    expect(screen.queryByText(/high/)).toBeNull();
  });

  it("marca a resposta quando ela vem do agente mock", async () => {
    mock.enviarMensagem.mockResolvedValue({ ...TURNO, origem: "mock" });
    render(<Chat />);
    await escrever("oi");

    expect(await screen.findByText("mock")).toBeInTheDocument();
  });
});

describe("reabrir a conversa", () => {
  it("carrega o histórico do lead guardado", async () => {
    localStorage.setItem("lead_id", "lead-0007");
    const historico: Mensagem[] = [
      {
        id: 1,
        papel: "user",
        conteudo: "quero comprar na Tijuca",
        origem: null,
        criado_em: "2026-09-07T16:00:00",
        imoveis: [],
      },
      {
        id: 2,
        papel: "followup",
        conteudo: "Oi! Ainda procurando na Tijuca?",
        origem: "gemini",
        criado_em: "2026-09-08T16:00:00",
        imoveis: [],
      },
    ];
    mock.historico.mockResolvedValue(historico);

    render(<Chat />);

    expect(await screen.findByText("quero comprar na Tijuca")).toBeInTheDocument();
    // Follow-up é iniciativa do agente: precisa ser visualmente distinto.
    expect(screen.getByText("Retomada automática")).toBeInTheDocument();
    expect(mock.historico).toHaveBeenCalledWith("lead-0007");
  });

  it("descarta o lead_id que o backend não conhece mais", async () => {
    // Acontece de verdade: lead apagado por LGPD, ou banco recriado. Insistir
    // no id velho quebraria todo turno seguinte.
    localStorage.setItem("lead_id", "lead-morto");
    mock.historico.mockRejectedValue(new ApiError(404, "Não encontrado."));

    render(<Chat />);

    await waitFor(() => expect(localStorage.getItem("lead_id")).toBeNull());
    expect(screen.getByText(/me procure depois sobre imóveis/i)).toBeInTheDocument();
  });

  it("mostra erro de rede sem apagar o id guardado", async () => {
    localStorage.setItem("lead_id", "lead-0007");
    mock.historico.mockRejectedValue(new ApiError(0, "Não foi possível falar com a API"));

    render(<Chat />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/não foi possível falar/i);
    expect(localStorage.getItem("lead_id")).toBe("lead-0007");
  });
});

describe("quando o envio falha", () => {
  it("devolve o texto para o campo e desfaz a bolha otimista", async () => {
    mock.enviarMensagem.mockRejectedValue(new ApiError(500, "Erro no servidor"));
    render(<Chat />);

    await escrever("mensagem que vai falhar");

    expect(await screen.findByRole("alert")).toHaveTextContent("Erro no servidor");
    // Perder o que a pessoa escreveu é pior que o erro em si.
    expect(screen.getByPlaceholderText("Escreva sua mensagem...")).toHaveValue(
      "mensagem que vai falhar",
    );
    expect(screen.queryByText("mensagem que vai falhar")).toBeNull();
  });
});

describe("agendamento a partir do chat", () => {
  it("só oferece agendar quando o backend diz que o perfil está completo", async () => {
    mock.enviarMensagem.mockResolvedValue(TURNO);
    render(<Chat />);
    await escrever("oi");

    await screen.findByText(TURNO.resposta);
    expect(screen.queryByLabelText(/hora para a/i)).toBeNull();
    expect(screen.queryByRole("button", { name: "Agendar visita" })).toBeNull();
  });

  it("quem disse 'Depois' não recebe o formulário de volta a cada turno", async () => {
    mock.enviarMensagem.mockResolvedValue({ ...TURNO, sugerir_agendamento: true });
    render(<Chat />);

    const usuario = await escrever("quero alugar");
    await screen.findByLabelText(/hora para a visita/i);

    await usuario.click(screen.getByRole("button", { name: "Depois" }));
    expect(screen.queryByLabelText(/hora para a visita/i)).toBeNull();

    // Próximo turno: o formulário NÃO volta sozinho, mas o convite continua ali
    // para quem mudar de ideia.
    await escrever("mais uma pergunta");
    expect(screen.queryByLabelText(/hora para a visita/i)).toBeNull();
    expect(screen.getByRole("button", { name: "Agendar visita" })).toBeInTheDocument();
  });

  it("manda CONSULTORIA quando a intenção é investir, não VISITA", async () => {
    mock.enviarMensagem.mockResolvedValue({
      ...TURNO,
      perfil: { ...TURNO.perfil, intent: "INVEST" },
      sugerir_agendamento: true,
    });
    mock.agendar.mockResolvedValue({});

    render(<Chat />);
    const usuario = await escrever("quero investir");

    // O seletor abre junto com o convite: é para ele que a resposta do agente
    // aponta ("é só escolher o dia aí embaixo"). Esconder atrás de um clique
    // fazia o cliente procurar o que a mensagem dizia estar na tela.
    expect(
      await screen.findByText(/conversa com o especialista/i),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Agendar visita" })).toBeNull();

    // O rótulo do campo também muda: não se marca "visita" para investidor.
    expect(screen.getByText(/hora para a consultoria/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/hora para a consultoria/i), {
      target: { value: DAQUI_A_TRES_DIAS },
    });
    await usuario.click(screen.getByRole("button", { name: "Confirmar" }));

    await waitFor(() => expect(mock.agendar).toHaveBeenCalledTimes(1));
    const [leadId, dados] = mock.agendar.mock.calls[0];
    expect(leadId).toBe("lead-0001");
    expect(dados.tipo).toBe("CONSULTORIA");
    expect(dados.data_hora).toMatch(/Z$/);
  });

  it("marca VISITA para quem quer alugar", async () => {
    mock.enviarMensagem.mockResolvedValue({ ...TURNO, sugerir_agendamento: true });
    mock.agendar.mockResolvedValue({});

    render(<Chat />);
    const usuario = await escrever("quero alugar");

    // Para quem aluga, o seletor abre falando em visita.
    expect(await screen.findByText(/marcar sua visita/i)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/hora para a visita/i), {
      target: { value: DAQUI_A_TRES_DIAS },
    });
    await usuario.click(screen.getByRole("button", { name: "Confirmar" }));

    await waitFor(() => expect(mock.agendar.mock.calls[0][1].tipo).toBe("VISITA"));
  });
});

describe("consentimento depois da primeira mensagem", () => {
  beforeEach(() => {
    localStorage.setItem("lead_id", "lead-0007");
  });

  it("mostra que hoje o contato é só pelo chat", async () => {
    mock.detalheLead.mockResolvedValue({ consentimento: false });
    render(<Chat />);

    expect(await screen.findByText(/só aqui no chat/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Pode me procurar" })).toBeInTheDocument();
  });

  it("deixa o lead conceder depois de ter recusado no começo", async () => {
    // A lacuna que existia: quem recusava na primeira mensagem não tinha
    // caminho nenhum para mudar de ideia.
    mock.detalheLead.mockResolvedValue({ consentimento: false });
    mock.atualizarLead.mockResolvedValue({ consentimento: true });

    render(<Chat />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Pode me procurar" }));

    await waitFor(() =>
      expect(mock.atualizarLead).toHaveBeenCalledWith("lead-0007", {
        consentimento: true,
      }),
    );
    expect(await screen.findByText(/autorizou a imobiliária/i)).toBeInTheDocument();
  });

  it("deixa revogar sem apagar a conversa", async () => {
    mock.detalheLead.mockResolvedValue({ consentimento: true });
    mock.atualizarLead.mockResolvedValue({ consentimento: false });

    render(<Chat />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Não quero mais ser procurado" }));

    await waitFor(() =>
      expect(mock.atualizarLead).toHaveBeenCalledWith("lead-0007", {
        consentimento: false,
      }),
    );
    // Revogar contato não é excluir dados: o histórico continua de pé.
    expect(mock.apagarLead).not.toHaveBeenCalled();
    expect(localStorage.getItem("lead_id")).toBe("lead-0007");
  });

  it("não põe atrito na revogação", async () => {
    const perguntou = vi.spyOn(window, "confirm").mockReturnValue(true);
    mock.detalheLead.mockResolvedValue({ consentimento: true });
    mock.atualizarLead.mockResolvedValue({ consentimento: false });

    render(<Chat />);
    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Não quero mais ser procurado" }));

    await waitFor(() => expect(mock.atualizarLead).toHaveBeenCalled());
    // Revogar tem que ser tão fácil quanto aceitar. Caixa de diálogo no
    // caminho da saída é atrito colocado de propósito.
    expect(perguntou).not.toHaveBeenCalled();
  });

  it("não mostra o controle antes de saber o estado", () => {
    // Chutar "não autorizado" diria ao lead que ele recusou algo que talvez
    // tenha aceitado.
    mock.detalheLead.mockReturnValue(new Promise(() => {}));
    render(<Chat />);

    expect(screen.queryByRole("button", { name: "Pode me procurar" })).toBeNull();
    expect(
      screen.queryByRole("button", { name: "Não quero mais ser procurado" }),
    ).toBeNull();
  });
});

describe("acessibilidade dos controles", () => {
  it("a caixa de mensagem tem nome acessível, não só placeholder", () => {
    // Placeholder desaparece quando a pessoa digita, e leitor de tela não
    // deveria depender dele para saber o que é o campo.
    render(<Chat />);
    expect(screen.getByRole("textbox", { name: "Sua mensagem" })).toBeInTheDocument();
  });

  it("o aceite da LGPD é um checkbox rotulado", () => {
    render(<Chat />);
    expect(
      screen.getByRole("checkbox", { name: /me procure depois sobre imóveis/i }),
    ).toBeInTheDocument();
  });
});

describe("nova conversa", () => {
  it("esquece o lead e volta a pedir consentimento", async () => {
    mock.enviarMensagem.mockResolvedValue(TURNO);
    render(<Chat />);

    const usuario = await escrever("oi");
    await waitFor(() => expect(localStorage.getItem("lead_id")).toBe("lead-0001"));

    await usuario.click(screen.getByRole("button", { name: "Nova conversa" }));

    expect(localStorage.getItem("lead_id")).toBeNull();
    expect(screen.getByText(/me procure depois sobre imóveis/i)).toBeInTheDocument();
    expect(screen.queryByText(TURNO.resposta)).toBeNull();
  });
});

describe("quando a API não responde", () => {
  it('"Nova conversa" destrava o botão Enviar depois de um envio pendurado', async () => {
    // Uma conexão que TRAVA não rejeita a promessa nunca. Sem o reset, o
    // `finally` que desliga `enviando` não rodava, o botão ficava desabilitado
    // para sempre e nem o botão de escape escapava: só F5.
    mock.historico.mockResolvedValue([]);
    mock.enviarMensagem.mockReturnValue(new Promise(() => {}));

    montar(
      <StrictMode>
        <Chat />
      </StrictMode>,
    );

    const usuario = userEvent.setup();
    await usuario.type(screen.getByPlaceholderText(/escreva/i), "oi");
    await usuario.click(screen.getByRole("button", { name: "Enviar" }));

    // Enquanto pendura, o rótulo é "Enviando" e o botão está desabilitado.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Enviando" })).toBeDisabled(),
    );

    await usuario.click(screen.getByRole("button", { name: /nova conversa/i }));

    // O rótulo voltar para "Enviar" já prova que `enviando` foi resetado. O
    // botão continua desabilitado só porque o campo está vazio, que é a regra
    // normal: escrever de novo destrava, e era exatamente isso que não
    // acontecia antes.
    expect(screen.getByRole("button", { name: "Enviar" })).toBeInTheDocument();

    await usuario.type(screen.getByPlaceholderText(/escreva/i), "de novo");
    expect(screen.getByRole("button", { name: "Enviar" })).not.toBeDisabled();
  });

  it("descarta a resposta de um envio que ficou para trás", async () => {
    // Sem o marcador de geração, a resposta antiga chegava depois do clique em
    // "Nova conversa" e RESSUSCITAVA o lead descartado, com o id de volta no
    // localStorage e a bolha numa tela que já tinha sido limpa.
    mock.historico.mockResolvedValue([]);

    let responder: (valor: ChatSaida) => void = () => {};
    mock.enviarMensagem.mockReturnValue(
      new Promise<ChatSaida>((resolve) => {
        responder = resolve;
      }),
    );

    montar(
      <StrictMode>
        <Chat />
      </StrictMode>,
    );

    const usuario = userEvent.setup();
    await usuario.type(screen.getByPlaceholderText(/escreva/i), "quero alugar");
    await usuario.click(screen.getByRole("button", { name: "Enviar" }));
    await usuario.click(screen.getByRole("button", { name: /nova conversa/i }));

    responder({ ...TURNO, resposta: "resposta da conversa antiga" });

    await waitFor(() =>
      expect(screen.queryByText("resposta da conversa antiga")).toBeNull(),
    );
    expect(localStorage.getItem("sdr.lead_id")).toBeNull();
  });
});

describe("controles de LGPD com o backend fora", () => {
  it('avisa que os dados NÃO foram apagados, em vez de piscar o botão', async () => {
    // `try/finally` sem `catch`: o DELETE falhava, nada mudava na tela, e o
    // usuário concluía que os dados não tinham sido apagados. Estava certo, e
    // ninguém tinha dito a ele.
    mock.historico.mockResolvedValue([]);
    mock.enviarMensagem.mockResolvedValue(TURNO);
    mock.detalheLead.mockResolvedValue({ consentimento: true });
    mock.apagarLead.mockRejectedValue(new ApiError(0, "backend fora do ar"));
    vi.spyOn(window, "confirm").mockReturnValue(true);

    montar(
      <StrictMode>
        <Chat />
      </StrictMode>,
    );

    const usuario = userEvent.setup();
    await usuario.type(screen.getByPlaceholderText(/escreva/i), "oi");
    await usuario.click(screen.getByRole("button", { name: "Enviar" }));

    const excluir = await screen.findByRole("button", { name: /excluir meus dados/i });
    await usuario.click(excluir);

    expect(await screen.findByText(/NÃO foram apagados/i)).toBeInTheDocument();
  });
});
