# Agendamentos: criar pelo lead, listar para a agenda do corretor.

from datetime import datetime, timedelta, timezone

from database.db import get_db
from dto.schemas import AgendamentoAtualizar, AgendamentoCriar, AgendamentoOut
from fastapi import APIRouter, Depends, HTTPException, Query
from models.agendamento import Agendamento, StatusAgendamento
from models.imovel import Imovel
from models.lead import Lead, StatusLead
from sqlalchemy.orm import Session

router = APIRouter(tags=["agendamentos"])


def _para_dto(agendamento: Agendamento) -> AgendamentoOut:
    return AgendamentoOut.model_validate(agendamento).model_copy(update={
        "lead_nome": agendamento.lead.nome if agendamento.lead else None,
        "imovel_titulo": agendamento.imovel.title if agendamento.imovel else None,
    })


@router.post("/leads/{lead_id}/schedule", response_model=AgendamentoOut,
             status_code=201, summary="Agendar visita ou reuniao")
def agendar(lead_id: str, dados: AgendamentoCriar, db: Session = Depends(get_db)):
    # Cria o agendamento e promove o lead a AGENDADO.
    #
    # O status vira AGENDADO aqui e o `sincronizar_do_perfil` nao o desfaz: um
    # lead que marcou visita nao pode voltar para "em andamento" so porque mandou
    # mais uma mensagem no chat depois.
    lead = db.get(Lead, lead_id)
    if not lead:
        raise HTTPException(404, "Lead nao encontrado.")

    if dados.imovel_id and not db.get(Imovel, dados.imovel_id):
        raise HTTPException(422, "Imovel %s nao existe." % dados.imovel_id)

    agendamento = Agendamento(
        lead_id=lead_id,
        imovel_id=dados.imovel_id,
        tipo=dados.tipo,
        data_hora=dados.data_hora,
        corretor=dados.corretor,
        observacoes=dados.observacoes,
        status=StatusAgendamento.AGENDADO.value,
    )
    db.add(agendamento)

    lead.status = StatusLead.AGENDADO.value
    db.commit()
    db.refresh(agendamento)

    return _para_dto(agendamento)


@router.get("/schedule", response_model=list[AgendamentoOut],
            summary="Agenda do corretor")
def listar(
    db: Session = Depends(get_db),
    status: str | None = Query(None, description="AGENDADO, REALIZADO, CANCELADO..."),
    lead_id: str | None = None,
    de: datetime | None = Query(None, description="inicio da janela (ISO)"),
    ate: datetime | None = Query(None, description="fim da janela (ISO)"),
    proximos_dias: int | None = Query(
        None, ge=1, le=365,
        description="atalho: janela de agora ate N dias a frente",
    ),
    limite: int = Query(200, ge=1, le=1000),
):
    # Ordenada por data crescente: agenda se le do proximo para o ultimo.
    query = db.query(Agendamento)

    if status:
        query = query.filter(Agendamento.status == status)
    if lead_id:
        query = query.filter(Agendamento.lead_id == lead_id)

    if proximos_dias:
        agora = datetime.now(timezone.utc)
        de = de or agora
        ate = ate or (agora + timedelta(days=proximos_dias))

    if de:
        query = query.filter(Agendamento.data_hora >= de)
    if ate:
        query = query.filter(Agendamento.data_hora <= ate)

    itens = query.order_by(Agendamento.data_hora.asc()).limit(limite).all()
    return [_para_dto(a) for a in itens]


@router.patch("/schedule/{agendamento_id}", response_model=AgendamentoOut,
              summary="Atualizar agendamento")
def atualizar(agendamento_id: int, alteracoes: AgendamentoAtualizar,
              db: Session = Depends(get_db)):
    # Remarcar, cancelar ou marcar como realizado.
    agendamento = db.get(Agendamento, agendamento_id)
    if not agendamento:
        raise HTTPException(404, "Agendamento nao encontrado.")

    dados = alteracoes.model_dump(exclude_unset=True)

    if dados.get("imovel_id") and not db.get(Imovel, dados["imovel_id"]):
        raise HTTPException(422, "Imovel %s nao existe." % dados["imovel_id"])

    for campo, valor in dados.items():
        setattr(agendamento, campo, valor)

    # Cancelou a unica visita: o lead volta a ser trabalhavel, senao ele fica
    # preso em AGENDADO e some das listas de quem precisa de atencao.
    if dados.get("status") == StatusAgendamento.CANCELADO.value:
        ativos = [
            a for a in agendamento.lead.agendamentos
            if a.status == StatusAgendamento.AGENDADO.value and a.id != agendamento.id
        ]
        if not ativos:
            agendamento.lead.status = StatusLead.QUALIFICADO.value

    db.commit()
    db.refresh(agendamento)

    return _para_dto(agendamento)


@router.delete("/schedule/{agendamento_id}", status_code=204,
               summary="Remover agendamento")
def remover(agendamento_id: int, db: Session = Depends(get_db)):
    agendamento = db.get(Agendamento, agendamento_id)
    if not agendamento:
        raise HTTPException(404, "Agendamento nao encontrado.")

    db.delete(agendamento)
    db.commit()
