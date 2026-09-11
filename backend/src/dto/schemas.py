# Contratos de entrada e saida da API (pydantic).
#
# O pacote se chama `dto` e nao `schemas` porque `ai-core/src` esta no sys.path e
# tem um `schemas.py` proprio. Com os dois no caminho, qual dos dois venceria
# dependeria da ordem do sys.path, e o bug apareceria como um ImportError
# misterioso em producao. Nome diferente resolve de vez.
#
# Todo enum sai com o valor cru E o rotulo em portugues (`intencao` /
# `intencao_label`). O front nao deveria precisar carregar uma tabela de traducao
# para desenhar um badge, e o `lead_profile.py` ja e o dono dessas tabelas.

from datetime import datetime, timedelta
from typing import Any, Literal

from models.lead import Intencao, StatusLead, Urgencia
from pydantic import BaseModel, ConfigDict, Field, field_validator

# Os rotulos vem do lead_profile da Pessoa 2, que e o dono declarado da
# traducao. Import com fallback: o backend precisa subir mesmo se a Parte 2 nao
# estiver no lugar, senao um erro de checkout derruba a API inteira.
try:
    from lead_profile import FIELD_LABELS, INTENT_LABELS, URGENCY_LABELS
except ImportError:  # pragma: no cover
    INTENT_LABELS = {"BUY": "compra", "RENT": "aluguel", "INVEST": "investimento"}
    URGENCY_LABELS = {"high": "alta", "medium": "média", "low": "baixa"}
    FIELD_LABELS = {
        "name": "Nome", "intent": "Intenção", "price_range": "Faixa de preço",
        "region": "Região", "bedrooms": "Quartos", "urgency": "Urgência",
        "email": "E-mail", "phone": "Telefone",
    }

TEMPERATURE_LABELS = {"HOT": "QUENTE", "WARM": "MORNO", "COLD": "FRIO"}

STATUS_LABELS = {
    "NOVO": "Novo",
    "EM_ANDAMENTO": "Em andamento",
    "QUALIFICADO": "Qualificado",
    "AGENDADO": "Agendado",
    "DESCARTADO": "Descartado",
}


# ---------------------------------------------------------------------------
# Lead
# ---------------------------------------------------------------------------

class LeadBase(BaseModel):
    nome: str | None = None
    email: str | None = None
    telefone: str | None = None
    intencao: Intencao | None = None
    regiao: str | None = None
    faixa_preco: str | None = None
    quartos: str | None = None
    urgencia: Urgencia | None = None


class LeadCriar(LeadBase):
    # Criacao manual, pelo corretor. O chat cria lead sozinho.

    origem: str = "manual"
    consentimento: bool = False


class LeadAtualizar(LeadBase):
    # PATCH: tudo opcional, e so o que vier no corpo e alterado.
    #
    # `model_fields_set` distingue "nao mandou o campo" de "mandou null para
    # limpar". Sem isso, um PATCH so de status apagaria o telefone do lead.

    status: StatusLead | None = None
    proxima_acao: str | None = None
    consentimento: bool | None = None


class LeadResumo(BaseModel):
    # O que a LISTA do dashboard precisa. Sem historico, sem resumo de IA.

    model_config = ConfigDict(from_attributes=True)

    id: str
    nome: str | None
    email: str | None
    telefone: str | None
    intencao: str | None
    regiao: str | None
    faixa_preco: str | None
    quartos: str | None
    urgencia: str | None
    status: str
    score: int
    temperatura: str
    proxima_acao: str | None
    origem: str
    consentimento: bool
    criado_em: datetime
    atualizado_em: datetime
    ultima_mensagem_em: datetime | None
    followups_enviados: int

    # Campos calculados: rotulos e contagens que a lista mostra.
    intencao_label: str | None = None
    urgencia_label: str | None = None
    # "8.55k" e formato de comparar, nao de ler. Aqui vai "R$ 8.550".
    faixa_preco_label: str | None = None
    temperatura_label: str | None = None
    status_label: str | None = None
    total_mensagens: int = 0
    horas_sem_resposta: float | None = None


class MensagemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    papel: str
    conteudo: str
    origem: str | None
    criado_em: datetime
    imoveis: list[str] = Field(default_factory=list)


class LeadDetalhe(LeadResumo):
    # Detalhe = resumo + historico + o card do corretor gerado pela Parte 2.

    mensagens: list[MensagemOut] = Field(default_factory=list)
    agendamentos: list["AgendamentoOut"] = Field(default_factory=list)
    resumo_ia: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

class ChatEntrada(BaseModel):
    # Opcional: sem lead_id a rota cria um lead novo e devolve o id gerado. E
    # como o primeiro "oi" de um visitante anonimo entra no sistema.
    lead_id: str | None = Field(default=None, max_length=64)
    mensagem: str = Field(min_length=1, max_length=4000)
    # LGPD: o front marca o aceite na primeira mensagem. Sem consentimento a
    # conversa acontece, mas o follow-up automatico nao dispara.
    consentimento: bool | None = None

    @field_validator("mensagem")
    @classmethod
    def _nao_vazia(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("mensagem vazia")
        return valor


class ImovelSugerido(BaseModel):
    # O imovel como o RAG devolveu, com o porque de ele ter sido escolhido.

    id: str
    title: str
    neighborhood: str
    zone: str | None = None
    deal_type: str
    price: float
    bedrooms: int
    area_m2: float | None = None
    score: float | None = None
    reason: str | None = None


class ChatSaida(BaseModel):
    lead_id: str
    resposta: str
    status: str
    score: int
    temperatura: str
    temperatura_label: str
    perfil: dict[str, Any] = Field(default_factory=dict)
    # O mesmo perfil, pronto para LER: "aluguel" em vez de "RENT", "alta" em
    # vez de "high", "R$ 8.550" em vez de "8.55k". O cru continua acima para
    # quem precisa comparar; a tela usa este.
    perfil_label: dict[str, str] = Field(default_factory=dict)
    # O nome de exibição de cada campo ("intent" -> "Intenção"), da mesma
    # tabela que a Parte 2 usa. Vai junto para o front não manter a própria
    # lista, que era o que estava acontecendo e já tinha divergido.
    perfil_campos: dict[str, str] = Field(default_factory=dict)
    # O que a memoria aprendeu NESTE turno. O front usa para piscar "anotei:
    # 3 quartos" na tela, que e a prova visual de que ha memoria.
    novidades: list[dict[str, Any]] = Field(default_factory=list)
    imoveis: list[ImovelSugerido] = Field(default_factory=list)
    proxima_acao: str | None = None
    # "gemini" ou "mock": honestidade sobre quem respondeu.
    origem: str = "mock"
    sugerir_agendamento: bool = False


# ---------------------------------------------------------------------------
# Imovel
# ---------------------------------------------------------------------------

class ImovelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    deal_type: str
    property_type: str
    price: float
    condo_fee: float | None
    property_tax: float | None
    bedrooms: int
    suites: int | None
    bathrooms: int
    parking: int
    area_m2: float
    neighborhood: str
    zone: str
    city: str
    lat: float
    lon: float
    features: list[str] = Field(default_factory=list)
    accepts_financing: bool | None
    annual_yield_pct: float | None
    status: str


class ImoveisPagina(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[ImovelOut]


# ---------------------------------------------------------------------------
# Agendamento
# ---------------------------------------------------------------------------

# Um pouco de folga para tras, e ela nao e capricho: o cliente manda a hora que
# ele escolheu no seletor, e entre escolher e clicar em Confirmar passam alguns
# segundos. Recusar por trinta segundos de atraso seria recusar um agendamento
# legitimo e deixar a pessoa sem entender o motivo.
TOLERANCIA_PARA_TRAS = timedelta(minutes=5)


def _recusar_passado(quando: datetime | None) -> datetime | None:
    """Visita no passado nao e agendamento, e um dado errado no banco.

    O seletor da tela ja tem `min`, mas validacao de front e conveniencia, nao
    garantia: qualquer POST direto na API passava, e o backend respondia 201. O
    lead saia da conversa achando que tinha visita marcada para o mes anterior,
    e o corretor via a visita no passado na agenda.
    """
    if quando is None:
        return None

    # O DTO aceita data com e sem fuso. Comparar os dois jeitos levanta
    # TypeError, entao a referencia e escolhida conforme o que chegou.
    agora = datetime.now(quando.tzinfo) if quando.tzinfo else datetime.now()

    if quando < agora - TOLERANCIA_PARA_TRAS:
        raise ValueError("A data escolhida ja passou. Escolha um horario futuro.")

    return quando


class AgendamentoCriar(BaseModel):
    data_hora: datetime
    tipo: Literal["VISITA", "REUNIAO", "CONSULTORIA"] = "VISITA"
    imovel_id: str | None = None
    corretor: str | None = None
    observacoes: str | None = None

    _no_futuro = field_validator("data_hora")(_recusar_passado)


class AgendamentoAtualizar(BaseModel):
    data_hora: datetime | None = None
    tipo: Literal["VISITA", "REUNIAO", "CONSULTORIA"] | None = None
    status: Literal["AGENDADO", "REALIZADO", "CANCELADO", "NAO_COMPARECEU"] | None = None
    imovel_id: str | None = None
    corretor: str | None = None
    observacoes: str | None = None

    # Remarcar tambem e para o futuro. O corretor que registra uma visita ja
    # realizada muda o STATUS, nao a data.
    _no_futuro = field_validator("data_hora")(_recusar_passado)


class AgendamentoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: str
    imovel_id: str | None
    tipo: str
    status: str
    data_hora: datetime
    corretor: str | None
    observacoes: str | None
    criado_em: datetime

    # Desnormalizado de proposito: a agenda do corretor mostra "Joao - Leblon"
    # e nao deveria precisar de duas chamadas a mais para descobrir os nomes.
    lead_nome: str | None = None
    imovel_titulo: str | None = None


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class ContagemItem(BaseModel):
    chave: str
    label: str
    total: int


class DashboardResumo(BaseModel):
    total_leads: int
    leads_quentes: int
    leads_mornos: int
    leads_frios: int
    qualificados: int
    aguardando_followup: int
    agendamentos_proximos: int
    agendamentos_hoje: int
    total_mensagens: int
    total_imoveis: int
    taxa_qualificacao: float
    score_medio: float
    por_status: list[ContagemItem]
    por_intencao: list[ContagemItem]
    por_regiao: list[ContagemItem]
    ultimos_leads: list[LeadResumo]


class FollowUpPendente(BaseModel):
    lead_id: str
    lead_nome: str | None
    horas_de_silencio: float
    tentativa: int
    tom: str | None
    motivo: str
    canal: str | None = None
    texto_sugerido: str | None = None


LeadDetalhe.model_rebuild()
