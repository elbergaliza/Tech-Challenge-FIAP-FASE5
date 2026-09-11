# Orquestracao de um turno de chat: IA + persistencia.
#
# Divisao de responsabilidade, que e o ponto deste arquivo existir separado:
#
#   * `ai_service`   fala com a Parte 1 e a Parte 2. Nao conhece SQLAlchemy.
#   * `chat_service` fala com o banco. Nao conhece prompt, embedding nem memoria.
#   * a rota         so traduz HTTP.
#
# Trocar o agente por outro, ou o banco por Postgres, mexe em um arquivo so.

from datetime import datetime, timezone

import services.ai_service as ai_service
import services.lead_service as lead_service
from models.conversa import Mensagem, PapelMensagem
from models.lead import Lead


def responder(db, lead: Lead, mensagem: str) -> dict:
    # Processa a mensagem e persiste tudo. Devolve o payload da rota.
    resultado = ai_service.processar_mensagem(lead.id, mensagem)

    agora = datetime.now(timezone.utc)

    db.add(Mensagem(
        lead_id=lead.id, papel=PapelMensagem.USER.value,
        conteudo=mensagem, criado_em=agora,
    ))
    db.add(Mensagem(
        lead_id=lead.id, papel=PapelMensagem.ASSISTANT.value,
        conteudo=resultado["resposta"],
        origem=resultado.get("origem"),
        imoveis_sugeridos=",".join(i["id"] for i in resultado.get("imoveis", [])),
        criado_em=agora,
    ))

    # So a mensagem do LEAD move este relogio. Se a resposta do agente o movesse,
    # o contador de silencio nunca acumularia e o follow-up jamais dispararia.
    lead.ultima_mensagem_em = agora
    db.commit()

    lead_service.sincronizar_do_perfil(
        db, lead, resultado.get("perfil") or {}, resultado.get("card"),
    )

    card = resultado.get("card") or {}

    return {
        "lead_id": lead.id,
        "resposta": resultado["resposta"],
        "status": lead.status,
        "score": lead.score,
        "temperatura": lead.temperatura,
        "temperatura_label": card.get("temperature_label", lead.temperatura),
        "perfil": resultado.get("perfil") or {},
        "perfil_label": lead_service.rotulos_do_perfil(resultado.get("perfil") or {}),
        "perfil_campos": lead_service.nomes_dos_campos(resultado.get("perfil") or {}),
        "novidades": lead_service.rotular_novidades(resultado.get("novidades")),
        "imoveis": resultado.get("imoveis") or [],
        "proxima_acao": lead.proxima_acao,
        "origem": resultado.get("origem", "mock"),
        "sugerir_agendamento": bool(resultado.get("sugerir_agendamento")),
    }


def registrar_followup(db, lead: Lead, texto: str) -> Mensagem:
    # Grava um follow-up no historico relacional.
    #
    # Papel `followup` e nao `assistant` para o dashboard conseguir contar
    # "3 tentativas sem resposta" com um WHERE, sem heuristica em cima do texto.
    # E, como no lado da memoria, NAO mexe em `ultima_mensagem_em`.
    mensagem = Mensagem(
        lead_id=lead.id, papel=PapelMensagem.FOLLOWUP.value, conteudo=texto,
        origem="followup", criado_em=datetime.now(timezone.utc),
    )
    db.add(mensagem)
    lead.followups_enviados = (lead.followups_enviados or 0) + 1
    db.commit()

    return mensagem


def historico(db, lead_id: str, limite: int = 200) -> list[Mensagem]:
    return (
        db.query(Mensagem)
        .filter(Mensagem.lead_id == lead_id)
        .order_by(Mensagem.criado_em.asc(), Mensagem.id.asc())
        .limit(limite)
        .all()
    )
