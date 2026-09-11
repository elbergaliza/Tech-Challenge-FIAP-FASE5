# CRUD de leads + as rotas de LGPD.

import services.ai_service as ai_service
import services.chat_service as chat_service
import services.lead_service as lead_service
from database.db import get_db
from dto.schemas import (
    AgendamentoOut, LeadAtualizar, LeadCriar, LeadDetalhe, LeadResumo, MensagemOut,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from models.lead import Lead
from sqlalchemy import or_
from sqlalchemy.orm import Session

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("", response_model=list[LeadResumo], summary="Listar leads")
def listar(
    db: Session = Depends(get_db),
    status: str | None = Query(None, description="NOVO, EM_ANDAMENTO, QUALIFICADO..."),
    temperatura: str | None = Query(None, description="HOT, WARM, COLD"),
    intencao: str | None = Query(None, description="BUY, RENT, INVEST"),
    busca: str | None = Query(None, description="parte do nome, e-mail ou telefone"),
    ordenar_por: str = Query("score", pattern="^(score|criado_em|ultima_mensagem_em)$"),
    limite: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # A lista do dashboard. Ordenada por score desc por padrao.
    #
    # Score primeiro, e nao data, porque a pergunta do corretor ao abrir a tela e
    # "quem eu ligo agora", nao "quem chegou por ultimo".
    query = db.query(Lead)

    if status:
        query = query.filter(Lead.status == status)
    if temperatura:
        query = query.filter(Lead.temperatura == temperatura)
    if intencao:
        query = query.filter(Lead.intencao == intencao)
    if busca:
        alvo = "%%%s%%" % busca
        query = query.filter(or_(
            Lead.nome.ilike(alvo), Lead.email.ilike(alvo), Lead.telefone.ilike(alvo),
        ))

    coluna = {"score": Lead.score, "criado_em": Lead.criado_em,
              "ultima_mensagem_em": Lead.ultima_mensagem_em}[ordenar_por]

    leads = query.order_by(coluna.desc()).offset(offset).limit(limite).all()
    return [lead_service.para_dto(db, lead) for lead in leads]


@router.post("", response_model=LeadResumo, status_code=201,
             summary="Criar lead manualmente")
def criar(dados: LeadCriar, db: Session = Depends(get_db)):
    # Para o corretor cadastrar um lead que chegou por fora do chat.
    lead = lead_service.criar(db, dados.model_dump(), origem=dados.origem)

    # Consentimento tem duas casas (ver o PATCH abaixo). Aqui quem marca e o
    # corretor, nao o titular, entao a origem entra no proposito registrado: o
    # aceite veio de fora do chat e a prova dele mora fora do sistema.
    if dados.consentimento:
        ai_service.registrar_consentimento(lead.id, True, origem=dados.origem)

    return lead_service.para_dto(db, lead)


@router.get("/{lead_id}", response_model=LeadDetalhe, summary="Detalhe do lead")
def detalhe(
    lead_id: str,
    db: Session = Depends(get_db),
    resumo_ia: bool = Query(False, description="gera o resumo com LLM (gasta cota)"),
):
    # Detalhe + historico + card do corretor.
    #
    # `resumo_ia=false` por padrao: o card heuristico ja vem pronto e nao custa
    # nada. O resumo escrito por LLM e uma chamada de API por abertura de lead, e
    # ninguem quer descobrir isso quando a cota acabar no meio da apresentacao.
    lead = lead_service.obter(db, lead_id)
    if not lead:
        raise HTTPException(404, "Lead nao encontrado.")

    dto = LeadDetalhe.model_validate(lead_service.para_dto(db, lead).model_dump())

    dto.mensagens = [
        MensagemOut.model_validate(m).model_copy(update={"imoveis": m.imoveis})
        for m in chat_service.historico(db, lead_id)
    ]
    dto.agendamentos = [
        AgendamentoOut.model_validate(a).model_copy(update={
            "lead_nome": lead.nome,
            "imovel_titulo": a.imovel.title if a.imovel else None,
        })
        for a in lead.agendamentos
    ]
    dto.resumo_ia = ai_service.card_do_corretor(lead_id, use_llm=resumo_ia)

    return dto


@router.patch("/{lead_id}", response_model=LeadResumo, summary="Atualizar lead")
def atualizar(lead_id: str, alteracoes: LeadAtualizar, db: Session = Depends(get_db)):
    # PATCH parcial: so os campos presentes no corpo mudam.
    #
    # `exclude_unset` e o que separa "nao mandou o campo" de "mandou null para
    # limpar". Sem ele, um PATCH so de status apagaria o telefone do lead.
    lead = lead_service.obter(db, lead_id)
    if not lead:
        raise HTTPException(404, "Lead nao encontrado.")

    mudancas = alteracoes.model_dump(exclude_unset=True)

    # O consentimento mora em dois lugares: a coluna do lead e o `consent` da
    # memoria da Parte 2, que e quem o follow-up consulta. Gravar so a coluna
    # fazia a tela dizer que o lead aceitou enquanto o agente seguia proibido
    # de retomar, e nada na resposta denunciava a divergencia.
    #
    # A comparacao vem ANTES do update: depois dele, `lead.consentimento` ja e
    # o valor novo e a mudanca nunca seria detectada.
    consentimento = mudancas.get("consentimento")
    mudou = consentimento is not None and consentimento != lead.consentimento

    lead = lead_service.atualizar(db, lead, mudancas)

    if mudou:
        ai_service.registrar_consentimento(lead.id, consentimento)

    return lead_service.para_dto(db, lead)


@router.delete("/{lead_id}", status_code=204,
               summary="Apagar lead (direito de exclusao, LGPD)")
def apagar(lead_id: str, db: Session = Depends(get_db)):
    # Apaga as DUAS representacoes: a linha relacional e a memoria da IA.
    #
    # Apagar so uma deixaria o lead ressuscitar na proxima mensagem, com o perfil
    # inteiro de volta. Como e o mesmo banco, as duas escritas caem juntas.
    lead = lead_service.obter(db, lead_id)
    if not lead:
        raise HTTPException(404, "Lead nao encontrado.")

    ai_service.esquecer(lead_id)
    db.delete(lead)   # mensagens e agendamentos vao junto (cascade)
    db.commit()


@router.get("/{lead_id}/exportar", summary="Exportar dados (direito de acesso, LGPD)")
def exportar(lead_id: str, db: Session = Depends(get_db)):
    # Tudo que o sistema guarda deste lead, num pacote so.
    lead = lead_service.obter(db, lead_id)
    if not lead:
        raise HTTPException(404, "Lead nao encontrado.")

    return {
        "lead": lead_service.para_dto(db, lead).model_dump(),
        "mensagens": [
            {"papel": m.papel, "conteudo": m.conteudo, "em": m.criado_em}
            for m in chat_service.historico(db, lead_id, limite=1000)
        ],
        "memoria": ai_service.exportar(lead_id),
    }
