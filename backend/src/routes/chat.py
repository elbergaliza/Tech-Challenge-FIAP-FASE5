# POST /chat — a rota mais importante do backend.
#
# Recebe a mensagem, orquestra memoria + RAG + agente, persiste e devolve a
# resposta com tudo que o front precisa para desenhar a tela: perfil atualizado,
# o que foi aprendido neste turno, imoveis sugeridos e se ja cabe agendar.

import re

import services.ai_service as ai_service
import services.chat_service as chat_service
import services.lead_service as lead_service
from database.db import get_db
from dto.schemas import ChatEntrada, ChatSaida, MensagemOut
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

router = APIRouter(prefix="/chat", tags=["chat"])

# Mesmo regex que a memoria da Parte 2 exige. Validar aqui, na borda, e o que
# impede um lead_id como "../../.env" de chegar ao store.
LEAD_ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@router.post("", response_model=ChatSaida, summary="Enviar mensagem ao agente")
def conversar(entrada: ChatEntrada, db: Session = Depends(get_db)):
    # Sem `lead_id` no corpo, um lead novo e criado e o id volta na resposta.
    #
    # E assim que um visitante anonimo entra no funil: o front guarda o id que
    # voltou e o manda nas mensagens seguintes.
    if entrada.lead_id and not LEAD_ID_VALIDO.match(entrada.lead_id):
        raise HTTPException(422, "lead_id invalido: use letras, digitos, '_' e '-'.")

    lead = lead_service.obter_ou_criar(db, entrada.lead_id)

    if entrada.consentimento is not None and entrada.consentimento != lead.consentimento:
        lead.consentimento = entrada.consentimento
        db.commit()
        ai_service.registrar_consentimento(lead.id, entrada.consentimento)

    return chat_service.responder(db, lead, entrada.mensagem)


@router.get("/{lead_id}/historico", response_model=list[MensagemOut],
            summary="Historico de mensagens do lead")
def historico(lead_id: str, limite: int = Query(200, ge=1, le=1000),
              db: Session = Depends(get_db)):
    # Usado quando o front reabre uma conversa: repopula a tela.
    if not lead_service.obter(db, lead_id):
        raise HTTPException(404, "Lead nao encontrado.")

    return [
        MensagemOut.model_validate(m).model_copy(update={"imoveis": m.imoveis})
        for m in chat_service.historico(db, lead_id, limite)
    ]
