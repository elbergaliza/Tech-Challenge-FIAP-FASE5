# Regras de lead: criacao, sincronizacao com o perfil da IA e montagem do dto.

from datetime import datetime, timezone

import services.ai_service as ai_service
from dto.schemas import (
    INTENT_LABELS, STATUS_LABELS, TEMPERATURE_LABELS, URGENCY_LABELS,
    LeadResumo,
)
from models.conversa import Mensagem
from models.lead import Lead, StatusLead, Temperatura
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

# Campo do perfil da Parte 2 -> coluna nossa. Os valores ja vem no dialeto
# canonico (BUY/RENT/INVEST, high/medium/low) porque o `lead_profile.from_agent`
# traduz na borda; aqui e copia, nao conversao.
PERFIL_PARA_COLUNA = {
    "name": "nome",
    "email": "email",
    "phone": "telefone",
    "intent": "intencao",
    "region": "regiao",
    "price_range": "faixa_preco",
    "bedrooms": "quartos",
    "urgency": "urgencia",
}


def novo_id(db) -> str:
    # Gera "lead-0001", "lead-0002"...
    #
    # Sequencial e legivel em vez de UUID porque este id aparece na tela do
    # corretor e na URL da demo. O formato respeita o `^[A-Za-z0-9_-]{1,64}$` que
    # a memoria da Parte 2 exige.
    #
    # A corrida entre dois chats simultaneos e resolvida pelo retry do `criar`, e
    # nao por lock: o custo de um id pulado num POC e zero.
    total = db.scalar(select(func.count()).select_from(Lead)) or 0
    return "lead-%04d" % (total + 1)


def criar(db, dados=None, lead_id=None, origem="chat") -> Lead:
    # Cria o lead no banco e registra o consentimento na memoria da IA.
    dados = dados or {}

    for tentativa in range(5):
        lead = Lead(
            id=lead_id or novo_id(db),
            nome=dados.get("nome"),
            email=dados.get("email"),
            telefone=dados.get("telefone"),
            intencao=_valor(dados.get("intencao")),
            regiao=dados.get("regiao"),
            faixa_preco=dados.get("faixa_preco"),
            quartos=dados.get("quartos"),
            urgencia=_valor(dados.get("urgencia")),
            status=StatusLead.NOVO.value,
            temperatura=Temperatura.COLD.value,
            origem=dados.get("origem", origem),
            consentimento=bool(dados.get("consentimento", False)),
        )
        db.add(lead)

        try:
            db.commit()
            break
        except IntegrityError:
            # Outro request pegou este id entre o count e o insert. Tenta o
            # proximo; nao insiste para sempre para nao virar loop infinito se
            # o erro for outro.
            db.rollback()
            if lead_id or tentativa == 4:
                raise
    else:  # pragma: no cover
        raise RuntimeError("nao consegui gerar um lead_id livre")

    if lead.consentimento:
        ai_service.registrar_consentimento(lead.id, True)

    return lead


def obter(db, lead_id: str) -> Lead | None:
    return db.get(Lead, lead_id)


def obter_ou_criar(db, lead_id: str | None) -> Lead:
    if lead_id:
        lead = db.get(Lead, lead_id)
        if lead:
            return lead
        # lead_id vindo do front que nao existe mais no banco (banco apagado,
        # localStorage antigo): recria com o MESMO id em vez de 404. A conversa
        # do usuario nao deveria morrer por causa disso.
        return criar(db, lead_id=lead_id)

    return criar(db)


def atualizar(db, lead: Lead, alteracoes: dict) -> Lead:
    # PATCH. So mexe no que veio no corpo.
    for campo, valor in alteracoes.items():
        if hasattr(lead, campo):
            setattr(lead, campo, _valor(valor))

    db.commit()
    return lead


def sincronizar_do_perfil(db, lead: Lead, perfil: dict, card: dict | None) -> Lead:
    # Copia o perfil da memoria da IA para as colunas do lead.
    #
    # A memoria e a fonte de verdade do PERFIL: ela e monotonica, sabe distinguir
    # correcao de novidade e nao esquece o que saiu da janela de contexto. As
    # colunas aqui existem para o dashboard filtrar e ordenar em SQL, o que seria
    # impossivel com o perfil dentro de um blob JSON.
    #
    # Campo que o corretor editou a mao NAO e sobrescrito por valor vazio: a
    # condicao e "a memoria sabe algo", nunca "a memoria nao sabe, entao apague".
    for campo_perfil, coluna in PERFIL_PARA_COLUNA.items():
        valor = perfil.get(campo_perfil)
        if valor not in (None, "", "undefined"):
            setattr(lead, coluna, str(valor))

    if card:
        lead.score = int(card.get("score") or 0)
        lead.temperatura = card.get("temperature") or Temperatura.COLD.value
        lead.proxima_acao = card.get("next_action")

    # Status so avanca automaticamente ate QUALIFICADO. AGENDADO e DESCARTADO
    # sao decisao humana (ou da rota de agendamento) e a IA nao os desfaz: um
    # lead que agendou visita nao pode voltar a "em andamento" porque mandou
    # mais uma mensagem.
    if lead.status not in (StatusLead.AGENDADO.value, StatusLead.DESCARTADO.value):
        if lead.temperatura == Temperatura.HOT.value or _tem_contato_e_intencao(lead):
            lead.status = StatusLead.QUALIFICADO.value
        else:
            lead.status = StatusLead.EM_ANDAMENTO.value

    db.commit()
    return lead


def _tem_contato_e_intencao(lead: Lead) -> bool:
    # Qualificado = da para o corretor ligar E ele sabe do que falar.
    #
    # Contato pesa tanto quanto intencao porque, para um SDR, contato e o produto
    # final: um lead perfeitamente qualificado sem telefone e um lead que ninguem
    # consegue atender. E o mesmo criterio do `SCORE_WEIGHTS` da Parte 2.
    return bool(lead.intencao) and bool(lead.telefone or lead.email)


def para_dto(db, lead: Lead, com_contagens=True) -> LeadResumo:
    # Modelo -> dto, preenchendo rotulos e contagens.
    dto = LeadResumo.model_validate(lead)

    dto.intencao_label = INTENT_LABELS.get(lead.intencao or "")
    dto.urgencia_label = URGENCY_LABELS.get(lead.urgencia or "")
    dto.temperatura_label = TEMPERATURE_LABELS.get(lead.temperatura, lead.temperatura)
    dto.status_label = STATUS_LABELS.get(lead.status, lead.status)

    if com_contagens:
        dto.total_mensagens = db.scalar(
            select(func.count()).select_from(Mensagem).where(Mensagem.lead_id == lead.id)
        ) or 0

    dto.horas_sem_resposta = _horas_desde(lead.ultima_mensagem_em)
    return dto


def _horas_desde(momento: datetime | None) -> float | None:
    if momento is None:
        return None

    # SQLite devolve datetime ingenuo mesmo com `DateTime(timezone=True)`: ele
    # nao tem tipo de data nativo e nao guarda offset. Assumir UTC e correto
    # porque e sempre em UTC que gravamos.
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)

    delta = datetime.now(timezone.utc) - momento
    return round(delta.total_seconds() / 3600.0, 1)


def _valor(v):
    # Enum do pydantic -> str da coluna.
    return v.value if hasattr(v, "value") else v
