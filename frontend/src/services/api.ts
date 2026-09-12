// Cliente da API da Parte 3. Um lugar só que fala HTTP.
//
// Nenhuma tela monta URL ou chama fetch direto: quando o backend renomear um
// query param, o conserto é aqui e não em cinco componentes.

import type {
  Agendamento,
  ChatSaida,
  DashboardResumo,
  FollowUpPendente,
  HistoricoApi,
  ImoveisFiltros,
  ImoveisPagina,
  Imovel,
  LeadDetalhe,
  LeadResumo,
  SaudeApi,
  StatusAgendamento,
  StatusLead,
  TipoAgendamento,
} from "../types";

const BASE_URL = (
  import.meta.env.VITE_API_URL ?? "http://localhost:8000"
).replace(/\/$/, "");

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// O `detail` do 422 do FastAPI é uma LISTA de objetos, não uma string. Jogar
// isso num elemento de texto renderiza "[object Object]"; virar mensagem
// legível é responsabilidade daqui.
function extrairMensagem(status: number, corpo: unknown): string {
  const detail = (corpo as { detail?: unknown } | null)?.detail;

  if (typeof detail === "string") return detail;

  if (Array.isArray(detail)) {
    const partes = detail.map((item) => {
      const erro = item as { loc?: unknown[]; msg?: string };
      const campo = Array.isArray(erro.loc)
        ? erro.loc.filter((p) => p !== "body").join(".")
        : "";
      return campo ? `${campo}: ${erro.msg ?? "inválido"}` : (erro.msg ?? "inválido");
    });
    return partes.join("; ");
  }

  if (status === 404) return "Não encontrado.";
  return `Falha na requisição (HTTP ${status}).`;
}

function montarUrl(caminho: string, params?: Record<string, unknown>): string {
  const url = new URL(BASE_URL + caminho);
  for (const [chave, valor] of Object.entries(params ?? {})) {
    // Filtro não escolhido some da URL: mandar `status=` vazio faria o backend
    // filtrar por string vazia e devolver lista sempre vazia.
    if (valor === undefined || valor === null || valor === "") continue;
    url.searchParams.set(chave, String(valor));
  }
  return url.toString();
}

// Teto de espera por requisição.
//
// Tem que ser GENEROSO: um turno de chat é uma chamada de LLM (que pode
// retentar três vezes) mais a busca do RAG. Dez segundos, que é o valor que
// costuma aparecer em exemplo de tutorial, cortaria toda demo pela metade. O
// que este número evita é o caso em que a espera não terminaria NUNCA.
const TIMEOUT_MS = 90_000;

// O `AbortSignal` desta realidade serve para o `fetch` desta realidade?
//
// No navegador, sempre. Sob jsdom (que é onde a suíte roda), não: o
// `AbortSignal` vem do jsdom e o `fetch` vem do Node, e o Node recusa o sinal
// com `TypeError: Expected signal to be an instance of AbortSignal` ANTES de
// mandar qualquer byte. Sem esta sonda, o efeito era silencioso e total: toda
// chamada da API virava "o backend está no ar?" sem nenhuma requisição ter
// saído, e a suíte de ponta a ponta inteira falhava acusando o backend.
//
// A sonda roda uma vez, não faz rede (só monta um `Request` e joga fora) e
// decide para sempre. Quando ela reprova, o timeout fica de fora e o resto
// continua igual: perde-se a proteção contra conexão pendurada num ambiente
// que não tem conexão pendurada nenhuma.
const TIMEOUT_UTILIZAVEL = (() => {
  try {
    new Request("http://localhost/", { signal: AbortSignal.timeout(1_000) });
    return true;
  } catch {
    return false;
  }
})();

async function requisitar<T>(
  caminho: string,
  opcoes: { metodo?: string; corpo?: unknown; params?: Record<string, unknown> } = {},
): Promise<T> {
  const { metodo = "GET", corpo, params } = opcoes;

  let resposta: Response;
  try {
    resposta = await fetch(montarUrl(caminho, params), {
      method: metodo,
      headers: corpo ? { "Content-Type": "application/json" } : undefined,
      body: corpo ? JSON.stringify(corpo) : undefined,
      signal: TIMEOUT_UTILIZAVEL ? AbortSignal.timeout(TIMEOUT_MS) : undefined,
    });
  } catch (erro) {
    // Uma conexão que TRAVA (e não cai) não rejeita a promessa nunca: sem o
    // `signal` acima, o chat ficava em "digitando..." para sempre, com o botão
    // Enviar desabilitado e nem "Nova conversa" destravando. Só F5 recuperava,
    // e nada na tela explicava o que tinha acontecido.
    if (erro instanceof DOMException && erro.name === "TimeoutError") {
      throw new ApiError(
        0,
        "A resposta demorou mais de 90 segundos e foi cancelada. Tente enviar de novo.",
      );
    }
    // Rede caída e backend no ar são falhas diferentes para quem está na demo:
    // aqui é quase sempre "esqueci de subir o uvicorn".
    throw new ApiError(
      0,
      `Não foi possível falar com a API em ${BASE_URL}. O backend está no ar?`,
    );
  }

  if (resposta.status === 204) return undefined as T;

  const texto = await resposta.text();
  const dados = texto ? JSON.parse(texto) : null;

  if (!resposta.ok) throw new ApiError(resposta.status, extrairMensagem(resposta.status, dados));

  return dados as T;
}

export const api = {
  saude: () => requisitar<SaudeApi>("/health"),

  // --- Chat -------------------------------------------------------------
  //
  // `lead_id` nulo na primeira mensagem: o backend cria o lead e devolve o id
  // gerado, que a tela guarda para os turnos seguintes.
  enviarMensagem: (entrada: {
    lead_id: string | null;
    mensagem: string;
    consentimento?: boolean;
  }) => requisitar<ChatSaida>("/chat", { metodo: "POST", corpo: entrada }),

  historico: (leadId: string) =>
    requisitar<HistoricoApi>(`/chat/${encodeURIComponent(leadId)}/historico`),

  // --- Leads ------------------------------------------------------------
  listarLeads: (filtros: {
    status?: string;
    temperatura?: string;
    intencao?: string;
    busca?: string;
    ordenar_por?: "score" | "criado_em" | "ultima_mensagem_em";
    limite?: number;
    offset?: number;
  } = {}) => requisitar<LeadResumo[]>("/leads", { params: filtros }),

  // `resumoIa` regera o resumo com LLM e gasta cota: só quando o corretor
  // pedir, nunca no carregamento de uma lista.
  detalheLead: (leadId: string, resumoIa = false) =>
    requisitar<LeadDetalhe>(`/leads/${encodeURIComponent(leadId)}`, {
      params: { resumo_ia: resumoIa || undefined },
    }),

  // PATCH parcial: só o que mudou. Mandar o lead inteiro apagaria os campos
  // que a tela não conhece.
  atualizarLead: (
    leadId: string,
    alteracoes: Partial<{
      nome: string | null;
      email: string | null;
      telefone: string | null;
      status: StatusLead;
      proxima_acao: string | null;
      // O backend espelha isto na memória da Parte 2, que é quem o follow-up
      // consulta. Gravar só a coluna deixaria a tela dizendo que o lead
      // aceitou enquanto o agente segue proibido de retomar.
      consentimento: boolean;
    }>,
  ) =>
    requisitar<LeadResumo>(`/leads/${encodeURIComponent(leadId)}`, {
      metodo: "PATCH",
      corpo: alteracoes,
    }),

  // LGPD: apaga o lead E a memória da IA.
  apagarLead: (leadId: string) =>
    requisitar<void>(`/leads/${encodeURIComponent(leadId)}`, { metodo: "DELETE" }),

  exportarLead: (leadId: string) =>
    requisitar<unknown>(`/leads/${encodeURIComponent(leadId)}/exportar`),

  // --- Dashboard --------------------------------------------------------
  resumoDashboard: () => requisitar<DashboardResumo>("/dashboard/summary"),

  // `comTexto` gasta uma chamada de LLM POR LEAD: só ao abrir um item.
  followups: (comTexto = false) =>
    requisitar<FollowUpPendente[]>("/dashboard/followups", {
      params: { com_texto: comTexto || undefined },
    }),

  // --- Agenda -----------------------------------------------------------
  agendar: (
    leadId: string,
    dados: {
      data_hora: string;
      tipo: TipoAgendamento;
      imovel_id?: string | null;
      corretor?: string | null;
      observacoes?: string | null;
    },
  ) =>
    requisitar<Agendamento>(`/leads/${encodeURIComponent(leadId)}/schedule`, {
      metodo: "POST",
      corpo: dados,
    }),

  listarAgenda: (params: { proximos_dias?: number; status?: string } = {}) =>
    requisitar<Agendamento[]>("/schedule", { params }),

  atualizarAgendamento: (
    id: number,
    alteracoes: Partial<{ data_hora: string; status: StatusAgendamento; corretor: string }>,
  ) => requisitar<Agendamento>(`/schedule/${id}`, { metodo: "PATCH", corpo: alteracoes }),

  // --- Imóveis ----------------------------------------------------------
  listarImoveis: (
    filtros: {
      deal_type?: string;
      property_type?: string;
      neighborhood?: string;
      zone?: string;
      bedrooms_min?: number;
      preco_min?: number;
      preco_max?: number;
      limite?: number;
      offset?: number;
    } = {},
  ) => requisitar<ImoveisPagina>("/imoveis", { params: filtros }),

  // Os selects saem daqui em vez de arrays chumbados no código, senão eles
  // saem de sincronia com a base de imóveis.
  filtrosImoveis: () => requisitar<ImoveisFiltros>("/imoveis/filtros"),

  detalheImovel: (id: string) =>
    requisitar<Imovel>(`/imoveis/${encodeURIComponent(id)}`),
};
