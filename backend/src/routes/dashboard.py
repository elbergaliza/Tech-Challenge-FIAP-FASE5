# GET /dashboard/summary — os numeros agregados que o front exibe.
#
# Tudo em SQL, sem carregar lead nenhum para a memoria do processo. Score e
# temperatura estao em coluna justamente para isso: com o perfil so dentro do
# blob da memoria, "quantos leads quentes" viraria um for em Python sobre todos os
# leads, rodando o summarizer em cada um.

from datetime import datetime, timedelta, timezone

import services.ai_service as ai_service
import services.lead_service as lead_service
from database.db import get_db
from dto.schemas import (
    INTENT_LABELS, STATUS_LABELS, ContagemItem, DashboardResumo, FollowUpPendente,
)
from fastapi import APIRouter, Depends, Query
from models.agendamento import Agendamento, StatusAgendamento
from models.conversa import Mensagem
from models.imovel import Imovel
from models.lead import Lead, StatusLead, Temperatura
from sqlalchemy import func, select
from sqlalchemy.orm import Session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Depois de 48h sem resposta o lead entra na fila de follow-up. Mesmo corte que
# a cadencia da Parte 2 usa para a primeira retomada.
HORAS_PARA_FOLLOWUP = 48


@router.get("/summary", response_model=DashboardResumo, summary="Numeros do dashboard")
def resumo(db: Session = Depends(get_db)):
    agora = datetime.now(timezone.utc)

    def contar(*filtros):
        stmt = select(func.count()).select_from(Lead)
        for filtro in filtros:
            stmt = stmt.where(filtro)
        return db.scalar(stmt) or 0

    total_leads = contar()
    quentes = contar(Lead.temperatura == Temperatura.HOT.value)
    mornos = contar(Lead.temperatura == Temperatura.WARM.value)
    frios = contar(Lead.temperatura == Temperatura.COLD.value)
    qualificados = contar(Lead.status.in_(
        (StatusLead.QUALIFICADO.value, StatusLead.AGENDADO.value),
    ))

    corte = agora - timedelta(hours=HORAS_PARA_FOLLOWUP)
    aguardando = contar(
        Lead.ultima_mensagem_em.isnot(None),
        Lead.ultima_mensagem_em < corte,
        Lead.status.notin_((StatusLead.DESCARTADO.value, StatusLead.AGENDADO.value)),
    )

    agendamentos_proximos = db.scalar(
        select(func.count()).select_from(Agendamento).where(
            Agendamento.status == StatusAgendamento.AGENDADO.value,
            Agendamento.data_hora >= agora,
        )
    ) or 0

    fim_do_dia = agora.replace(hour=23, minute=59, second=59, microsecond=999999)
    agendamentos_hoje = db.scalar(
        select(func.count()).select_from(Agendamento).where(
            Agendamento.status == StatusAgendamento.AGENDADO.value,
            Agendamento.data_hora >= agora.replace(hour=0, minute=0, second=0,
                                                   microsecond=0),
            Agendamento.data_hora <= fim_do_dia,
        )
    ) or 0

    ultimos = (
        db.query(Lead)
        .order_by(Lead.criado_em.desc())
        .limit(5)
        .all()
    )

    return DashboardResumo(
        total_leads=total_leads,
        leads_quentes=quentes,
        leads_mornos=mornos,
        leads_frios=frios,
        qualificados=qualificados,
        aguardando_followup=aguardando,
        agendamentos_proximos=agendamentos_proximos,
        agendamentos_hoje=agendamentos_hoje,
        total_mensagens=db.scalar(select(func.count()).select_from(Mensagem)) or 0,
        total_imoveis=db.scalar(select(func.count()).select_from(Imovel)) or 0,
        # Sem lead, a taxa e 0 e nao uma divisao por zero. Parece obvio e e
        # exatamente o 500 que o dashboard daria no primeiro boot, com a base
        # vazia, que e justamente quando alguem abre a tela pela primeira vez.
        taxa_qualificacao=round(qualificados / total_leads * 100, 1) if total_leads else 0.0,
        score_medio=round(db.scalar(select(func.avg(Lead.score))) or 0.0, 1),
        por_status=_contagem(db, Lead.status, STATUS_LABELS),
        por_intencao=_contagem(db, Lead.intencao, INTENT_LABELS),
        por_regiao=_contagem(db, Lead.regiao, {}, limite=8),
        ultimos_leads=[lead_service.para_dto(db, lead) for lead in ultimos],
    )


def _contagem(db, coluna, rotulos, limite=None) -> list[ContagemItem]:
    query = (
        db.query(coluna, func.count())
        .filter(coluna.isnot(None))
        .group_by(coluna)
        .order_by(func.count().desc())
    )
    if limite:
        query = query.limit(limite)

    return [
        ContagemItem(chave=chave, label=rotulos.get(chave, chave), total=total)
        for chave, total in query.all()
    ]


@router.get("/followups", response_model=list[FollowUpPendente],
            summary="Leads que merecem follow-up agora")
def followups(
    db: Session = Depends(get_db),
    com_texto: bool = Query(False, description="gera o texto sugerido (gasta cota)"),
):
    # Quem decide e a Parte 2 (cadencia, opt-out, consentimento), nao um
    # `WHERE ultima_mensagem_em < 48h`.
    #
    # O corte por tempo do `/summary` e uma aproximacao boa para um numero grande
    # na tela; esta lista e a de verdade, e por isso pode divergir dele.
    pendentes = ai_service.followups_pendentes(gerar_texto=com_texto)

    nomes = dict(db.query(Lead.id, Lead.nome).all())

    return [
        FollowUpPendente(lead_nome=nomes.get(item["lead_id"]), **item)
        for item in pendentes
    ]
