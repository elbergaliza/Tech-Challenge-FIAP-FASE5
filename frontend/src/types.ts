// Espelho dos contratos do backend (backend/src/dto/schemas.py).
//
// Os enums vêm do backend sempre em par: o valor cru (`temperatura`) e o rótulo
// já em português (`temperatura_label`). O front NUNCA traduz de novo: se
// traduzisse, as duas tabelas sairiam de sincronia na primeira mudança do
// backend, e a tela mostraria "HOT" para um lead que a API já chama de QUENTE.

export type Temperatura = "HOT" | "WARM" | "COLD";
export type StatusLead =
  | "NOVO"
  | "EM_ANDAMENTO"
  | "QUALIFICADO"
  | "AGENDADO"
  | "DESCARTADO";
export type Intencao = "BUY" | "RENT" | "INVEST";
export type TipoAgendamento = "VISITA" | "REUNIAO" | "CONSULTORIA";
export type StatusAgendamento =
  | "AGENDADO"
  | "REALIZADO"
  | "CANCELADO"
  | "NAO_COMPARECEU";

export interface ImovelSugerido {
  id: string;
  title: string;
  neighborhood: string;
  zone: string | null;
  deal_type: string;
  price: number;
  bedrooms: number;
  area_m2: number | null;
  score: number | null;
  reason: string | null;
}

export interface Novidade {
  field: string;
  from: string | null;
  to: string | null;
  kind: "new" | "correction";
  // Prontos para ler, do backend: "Urgência", "baixa", "alta". Opcionais para
  // a tela não quebrar contra um backend mais antigo.
  field_label?: string;
  from_label?: string | null;
  to_label?: string | null;
}

export interface ChatSaida {
  lead_id: string;
  resposta: string;
  status: string;
  score: number;
  temperatura: Temperatura;
  temperatura_label: string;
  perfil: Record<string, string | null>;
  // O mesmo perfil pronto para ler ("aluguel", "alta", "R$ 8.550"). O cru
  // continua acima; a tela usa este, e a tradução mora no backend, junto das
  // outras tabelas de rótulo.
  perfil_label: Record<string, string>;
  // O nome de exibição de cada campo ("intent" -> "Intenção"), da mesma
  // tabela que a Parte 2 usa.
  perfil_campos: Record<string, string>;
  novidades: Novidade[];
  imoveis: ImovelSugerido[];
  proxima_acao: string | null;
  origem: "gemini" | "mock" | string;
  sugerir_agendamento: boolean;
}

export interface Mensagem {
  id: number;
  papel: "user" | "assistant" | "followup";
  conteudo: string;
  origem: string | null;
  criado_em: string;
  imoveis: string[];
}

export interface LeadResumo {
  id: string;
  nome: string | null;
  email: string | null;
  telefone: string | null;
  intencao: Intencao | null;
  regiao: string | null;
  faixa_preco: string | null;
  quartos: string | null;
  urgencia: string | null;
  status: StatusLead;
  score: number;
  temperatura: Temperatura;
  proxima_acao: string | null;
  origem: string;
  consentimento: boolean;
  criado_em: string;
  atualizado_em: string;
  ultima_mensagem_em: string | null;
  followups_enviados: number;
  intencao_label: string | null;
  urgencia_label: string | null;
  // "R$ 8.550" em vez do "8.55k" que a memória guarda para comparar.
  faixa_preco_label: string | null;
  temperatura_label: string | null;
  status_label: string | null;
  total_mensagens: number;
  horas_sem_resposta: number | null;
}

export interface ResumoIA {
  temperature?: Temperatura;
  temperature_label?: string;
  score?: number;
  summary?: string;
  next_action?: string;
  main_interest?: string;
  buying_signals?: string[];
  objections?: string[];
  alerts?: string[];
  shown_properties?: string[];
  // O perfil que a memória acumulou. É por aqui que os campos de investidor
  // (ticket e retorno esperado) chegam à tela do corretor: eles vivem na
  // memória da IA, não em coluna do banco.
  profile?: Record<string, string>;
  // Como o score foi montado ("+20 intenção informado"). Torna o número
  // auditável em vez de mágico.
  factors?: string[];
  // "heuristic" (pronto, sem custo) ou o gerador com LLM.
  source?: string;
  generated_at?: string;
  message_count?: number;
  hours_of_silence?: number;
}

export interface Agendamento {
  id: number;
  lead_id: string;
  imovel_id: string | null;
  tipo: TipoAgendamento;
  status: StatusAgendamento;
  data_hora: string;
  corretor: string | null;
  observacoes: string | null;
  criado_em: string;
  lead_nome: string | null;
  imovel_titulo: string | null;
}

export interface LeadDetalhe extends LeadResumo {
  mensagens: Mensagem[];
  agendamentos: Agendamento[];
  resumo_ia: ResumoIA | null;
}

export interface ContagemItem {
  chave: string;
  label: string;
  total: number;
}

export interface DashboardResumo {
  total_leads: number;
  leads_quentes: number;
  leads_mornos: number;
  leads_frios: number;
  qualificados: number;
  aguardando_followup: number;
  agendamentos_proximos: number;
  agendamentos_hoje: number;
  total_mensagens: number;
  total_imoveis: number;
  taxa_qualificacao: number;
  score_medio: number;
  por_status: ContagemItem[];
  por_intencao: ContagemItem[];
  por_regiao: ContagemItem[];
  ultimos_leads: LeadResumo[];
}

export interface FollowUpPendente {
  lead_id: string;
  lead_nome: string | null;
  horas_de_silencio: number;
  tentativa: number;
  tom: string | null;
  motivo: string;
  canal: string | null;
  texto_sugerido: string | null;
}

export interface Imovel {
  id: string;
  title: string;
  description: string;
  deal_type: string;
  property_type: string;
  price: number;
  condo_fee: number | null;
  property_tax: number | null;
  bedrooms: number;
  suites: number | null;
  bathrooms: number;
  parking: number;
  area_m2: number;
  neighborhood: string;
  zone: string;
  city: string;
  lat: number;
  lon: number;
  features: string[];
  accepts_financing: boolean | null;
  annual_yield_pct: number | null;
  status: string;
}

export interface ImoveisPagina {
  total: number;
  limit: number;
  offset: number;
  items: Imovel[];
}

export interface ImoveisFiltros {
  deal_type: string[];
  property_type: string[];
  neighborhood: string[];
  zone: string[];
  bedrooms: number[];
  preco_min: number;
  preco_max: number;
}

export interface SaudeApi {
  status: string;
  banco: string;
  database_url: string;
  ia: {
    parte2: boolean;
    // "mock" quando o backend subiu sem GEMINI_API_KEY.
    agente: string;
    llm: string;
    rag: { carregado: boolean; imoveis: number; embedder: string | null };
    detalhe: string;
  };
}

// O que a rota de historico devolve.
//
// Ela nao entrega so as mensagens: entrega o ESTADO da conversa, porque a
// conversa sozinha nao remonta a tela. Depois de um F5 o painel "O que ja
// entendi" voltava vazio, dizendo "manda a primeira mensagem" para quem tinha
// uma conversa inteira atras. O sistema lembrava, e a tela desmentia.
export type HistoricoApi = {
  mensagens: Mensagem[];
  status: string;
  score: number;
  // `Temperatura` e nao `string`: o painel colore a faixa por este valor, e um
  // literal solto passaria batido aqui e quebraria a cor no navegador.
  temperatura: Temperatura;
  temperatura_label: string;
  perfil: Record<string, string>;
  perfil_label: Record<string, string>;
  perfil_campos: Record<string, string>;
  proxima_acao: string | null;
  sugerir_agendamento: boolean;
};
