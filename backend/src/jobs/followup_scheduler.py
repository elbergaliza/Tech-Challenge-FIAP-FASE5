# Job de follow-up: acorda de tempos em tempos e retoma quem sumiu.
#
#     python backend/src/jobs/followup_scheduler.py --once   # roda uma vez e sai
#     python backend/src/jobs/followup_scheduler.py          # fica rodando
#
# Dentro da API ele sobe junto com o app (FOLLOWUP_ENABLED, ligado por padrao).
#
# Quem decide se cabe follow-up NAO e este arquivo. E o `evaluate_followup` da
# Parte 2, que ja pesa consentimento, opt-out explicito ("nao me manda mais
# mensagem"), numero de tentativas anteriores e a cadencia por temperatura. Este
# job so acorda, pergunta e registra a resposta nas duas pontas: memoria da IA e
# tabela de mensagens.
#
# SOBRE CUSTO: um ciclo so gasta LLM se houver lead elegivel, e o filtro da
# Parte 2 e restritivo (48h de silencio, com consentimento, sem opt-out, dentro do
# limite de tentativas). Numa base de teste recem-criada o ciclo nao acha ninguem
# e nao custa nada. Rodando dias com chave de verdade, o consumo e uma chamada por
# lead retomado por ciclo; `--dry-run` mostra quem entraria sem enviar nada.

import argparse
import os
import sys

# Prelude de caminho: roda como script solto tambem.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bootstrap  # noqa: E402,F401
import config  # noqa: E402
import services.ai_service as ai_service  # noqa: E402
import services.chat_service as chat_service  # noqa: E402
import followup  # noqa: E402
from database.db import SessionLocal  # noqa: E402
from models.lead import Lead, StatusLead  # noqa: E402

_scheduler = None


def rodar_uma_vez(dry_run: bool = False) -> list[dict]:
    # Um ciclo. Devolve o que foi (ou seria) enviado.
    #
    # Horario comercial primeiro, antes de qualquer consulta: um lead urgente
    # que escrevia as 22h40 e sumia recebia "Oi! Continuo de olho em opcoes de
    # 2 quartos em Botafogo pra voce" as 02h40, porque a cadencia so media
    # horas de silencio e o job acorda a cada 30 minutos. Nenhuma imobiliaria
    # faz isso, e para o lead e motivo de bloquear o contato.
    #
    # O `--dry-run` ignora a janela de proposito: ele nao envia nada, e serve
    # justamente para conferir a lista fora do horario.
    pode_agora, motivo = followup.dentro_do_horario()
    if not pode_agora and not dry_run:
        print("[followup] %s. Nada enviado neste ciclo." % motivo)
        return []

    pendentes = ai_service.followups_pendentes(gerar_texto=False)
    if not pendentes:
        print("[followup] Ninguem para retomar agora.")
        return []

    enviados = []

    with SessionLocal() as db:
        for item in pendentes:
            lead = db.get(Lead, item["lead_id"])

            # A memoria pode conhecer um lead que o banco nao tem: um lead
            # apagado pela LGPD, ou uma conversa criada direto pelo run_chat da
            # Parte 2. Nao inventamos linha de lead a partir do job.
            if lead is None:
                continue

            if lead.status == StatusLead.DESCARTADO.value:
                continue

            if dry_run:
                enviados.append({**item, "enviado": False, "motivo": "dry-run"})
                continue

            try:
                resultado = ai_service.gerar_followup(lead.id)
            except Exception as erro:
                print("[followup] %s falhou: %s" % (lead.id, erro))
                continue

            if not resultado or not resultado.get("enviado"):
                continue

            # Registra no historico relacional tambem. O `send()` da Parte 2 ja
            # gravou na memoria dela; sem esta linha o follow-up nao apareceria
            # na tela da conversa e o corretor nao saberia o que foi dito.
            chat_service.registrar_followup(db, lead, resultado["text"])

            enviados.append({
                "lead_id": lead.id, "texto": resultado["text"],
                "tentativa": resultado["attempt"], "tom": resultado["tone"],
                "canal": resultado["channel"], "enviado": True,
            })
            print("[followup] %s (tentativa %d, %s): %s"
                  % (lead.id, resultado["attempt"], resultado["channel"],
                     resultado["text"][:70]))

    print("[followup] %d de %d retomados." % (len(enviados), len(pendentes)))
    return enviados


def iniciar(app=None):
    # Sobe o APScheduler junto com a API. Silencioso se estiver desligado.
    global _scheduler

    if not config.FOLLOWUP_ENABLED:
        print("[followup] Desligado (FOLLOWUP_ENABLED=false).")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        print("[followup] APScheduler nao instalado; job nao vai rodar.")
        return None

    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        rodar_uma_vez,
        "interval",
        minutes=config.FOLLOWUP_INTERVAL_MINUTES,
        id="followup",
        # Se um ciclo demorar mais que o intervalo, o proximo NAO comeca em
        # paralelo: dois ciclos simultaneos mandariam dois follow-ups para o
        # mesmo lead, porque o contador de tentativas so sobe no fim.
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()

    print("[followup] Ativo, a cada %d min." % config.FOLLOWUP_INTERVAL_MINUTES)
    return _scheduler


def parar():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def main():
    parser = argparse.ArgumentParser(description="Job de follow-up.")
    parser.add_argument("--once", action="store_true", help="roda um ciclo e sai")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostra quem seria retomado, sem enviar nada")
    args = parser.parse_args()

    if args.once or args.dry_run:
        rodar_uma_vez(dry_run=args.dry_run)
        return 0

    import time

    if not iniciar():
        print("[followup] Ligue FOLLOWUP_ENABLED=true no .env, "
              "ou use --once para um ciclo avulso.")
        return 1

    print("[followup] Ctrl+C para parar.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        parar()

    return 0


if __name__ == "__main__":
    sys.exit(main())
