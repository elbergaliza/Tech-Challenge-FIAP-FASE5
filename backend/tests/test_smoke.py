# Teste de fumaca da API: sobe tudo e passa por todas as rotas principais.
#
#     python backend/tests/test_smoke.py
#
# Roda sem pytest de proposito, para nao adicionar dependencia ao POC, e usa um
# banco temporario proprio (nunca o backend/data/app.db de desenvolvimento).
#
# NAO precisa de GEMINI_API_KEY: sem chave, o chat responde pelo agente mock. Se
# houver chave no ambiente, o teste ainda passa, mas gasta cota.

import os
import sys
import tempfile
import traceback
from datetime import datetime, timedelta, timezone

AQUI = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(AQUI), "src")
sys.path.insert(0, SRC)

# Banco proprio, definido ANTES de o config ser importado (ele le o ambiente no
# momento do import). Sem isto, o teste sujaria o banco de desenvolvimento com
# leads de mentira.
_tmp = tempfile.mkdtemp(prefix="sdr-teste-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "teste.db")
os.environ["SEED_LIMIT"] = "30"
os.environ["FOLLOWUP_ENABLED"] = "false"

from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402

falhas = []


def checar(condicao, descricao):
    if condicao:
        print("  ok    %s" % descricao)
    else:
        print("  FALHA %s" % descricao)
        falhas.append(descricao)


def main():
    with TestClient(app) as cliente:
        print("\n[1] Saude e catalogo")
        saude = cliente.get("/health").json()
        checar(saude["status"] == "ok", "GET /health responde ok")
        print("        agente: %s | rag: %s"
              % (saude["ia"]["agente"], saude["ia"]["rag"]["carregado"]))

        imoveis = cliente.get("/imoveis?limite=5").json()
        checar(imoveis["total"] >= 20, "catalogo tem imoveis (%d)" % imoveis["total"])
        checar(len(imoveis["items"]) == 5, "paginacao devolve 5")
        primeiro = imoveis["items"][0]["id"]
        checar(cliente.get("/imoveis/%s" % primeiro).status_code == 200,
               "GET /imoveis/{id}")
        checar(cliente.get("/imoveis/NAO-EXISTE").status_code == 404,
               "imovel inexistente da 404")
        checar("neighborhood" in cliente.get("/imoveis/filtros").json(),
               "GET /imoveis/filtros")

        print("\n[2] Chat cria lead e alimenta o perfil")
        r1 = cliente.post("/chat", json={
            "mensagem": "Oi, meu nome e Marcos, quero comprar um apartamento",
            "consentimento": True,
        })
        checar(r1.status_code == 200, "POST /chat sem lead_id")
        turno1 = r1.json()
        lead_id = turno1["lead_id"]
        checar(bool(turno1["resposta"]), "veio resposta do agente")
        checar(turno1["perfil"].get("intent") == "BUY", "extraiu intencao COMPRA")
        checar(turno1["perfil"].get("name") == "Marcos", "extraiu o nome")
        print("        lead: %s | origem: %s" % (lead_id, turno1["origem"]))

        r2 = cliente.post("/chat", json={
            "lead_id": lead_id,
            "mensagem": "Procuro em Copacabana, 3 quartos, ate 900k. E urgente!",
        }).json()
        checar(r2["perfil"].get("region") == "Copacabana", "extraiu a regiao")
        checar(r2["perfil"].get("bedrooms") == "3", "extraiu os quartos")
        checar(r2["perfil"].get("urgency") == "high", "extraiu a urgencia")
        checar(r2["score"] > turno1["score"], "score subiu (%d -> %d)"
               % (turno1["score"], r2["score"]))

        r3 = cliente.post("/chat", json={
            "lead_id": lead_id, "mensagem": "Meu telefone e (21) 98888-7777",
        }).json()
        checar(r3["perfil"].get("phone") is not None, "extraiu o telefone")
        checar(r3["status"] == "QUALIFICADO", "lead virou QUALIFICADO (%s)"
               % r3["status"])
        checar(r3["sugerir_agendamento"], "sugere agendamento com perfil completo")

        historico = cliente.get("/chat/%s/historico" % lead_id).json()
        checar(len(historico) == 6, "historico com 6 mensagens (%d)" % len(historico))

        print("\n[3] Memoria entre turnos")
        # A prova de que ha memoria: o perfil do turno 3 ainda sabe o que foi
        # dito no turno 1, que ja saiu do texto da mensagem atual.
        checar(r3["perfil"].get("intent") == "BUY", "turno 3 lembra da intencao do 1")
        checar(r3["perfil"].get("region") == "Copacabana", "turno 3 lembra da regiao")

        print("\n[4] CRUD de leads")
        lista = cliente.get("/leads").json()
        checar(any(item["id"] == lead_id for item in lista), "lead aparece na lista")
        checar(lista[0].get("temperatura_label") is not None, "lista traz os rotulos")

        detalhe = cliente.get("/leads/%s" % lead_id).json()
        checar(len(detalhe["mensagens"]) == 6, "detalhe traz o historico")
        checar(detalhe["resumo_ia"] is not None, "detalhe traz o card do corretor")
        checar(detalhe["resumo_ia"].get("next_action") is not None,
               "card sugere proxima acao")

        patch = cliente.patch("/leads/%s" % lead_id,
                              json={"status": "EM_ANDAMENTO"}).json()
        checar(patch["status"] == "EM_ANDAMENTO", "PATCH muda o status")
        # A garantia de que o PATCH parcial nao apaga o resto.
        checar(patch["telefone"] is not None, "PATCH parcial preserva o telefone")

        criado = cliente.post("/leads", json={
            "nome": "Lead manual", "telefone": "(21) 90000-0000", "intencao": "RENT",
        })
        checar(criado.status_code == 201, "POST /leads cria manualmente")
        checar(cliente.get("/leads/nao-existe").status_code == 404,
               "lead inexistente da 404")

        print("\n[5] Agendamento")
        quando = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        agendou = cliente.post("/leads/%s/schedule" % lead_id, json={
            "data_hora": quando, "tipo": "VISITA", "imovel_id": primeiro,
            "corretor": "Ana",
        })
        checar(agendou.status_code == 201, "POST /leads/{id}/schedule")
        agendamento = agendou.json()
        checar(agendamento["imovel_titulo"] is not None, "agendamento traz o imovel")
        checar(cliente.get("/leads/%s" % lead_id).json()["status"] == "AGENDADO",
               "lead virou AGENDADO")

        agenda = cliente.get("/schedule?proximos_dias=7").json()
        checar(len(agenda) == 1, "GET /schedule lista a agenda")

        remarcado = cliente.patch("/schedule/%d" % agendamento["id"],
                                  json={"status": "CANCELADO"}).json()
        checar(remarcado["status"] == "CANCELADO", "PATCH cancela")
        checar(cliente.get("/leads/%s" % lead_id).json()["status"] == "QUALIFICADO",
               "cancelar devolve o lead para QUALIFICADO")

        erro = cliente.post("/leads/%s/schedule" % lead_id,
                            json={"data_hora": quando, "imovel_id": "IMV-9999"})
        checar(erro.status_code == 422, "agendar com imovel inexistente da 422")

        print("\n[6] Dashboard")
        resumo = cliente.get("/dashboard/summary").json()
        checar(resumo["total_leads"] >= 2, "conta os leads (%d)" % resumo["total_leads"])
        checar(resumo["total_imoveis"] >= 20, "conta os imoveis")
        checar(resumo["total_mensagens"] == 6, "conta as mensagens")
        checar(isinstance(resumo["por_status"], list), "agrupa por status")
        checar(len(resumo["ultimos_leads"]) >= 2, "traz os ultimos leads")
        print("        quentes=%d mornos=%d frios=%d | taxa=%.1f%% | score medio=%.1f"
              % (resumo["leads_quentes"], resumo["leads_mornos"], resumo["leads_frios"],
                 resumo["taxa_qualificacao"], resumo["score_medio"]))

        pendentes = cliente.get("/dashboard/followups")
        checar(pendentes.status_code == 200, "GET /dashboard/followups")
        # Lead que acabou de falar nao pode estar na fila de retomada.
        checar(all(p["lead_id"] != lead_id for p in pendentes.json()),
               "lead ativo nao entra na fila de follow-up")

        print("\n[7] Job de follow-up")
        from jobs import followup_scheduler
        checar(isinstance(followup_scheduler.rodar_uma_vez(dry_run=True), list),
               "ciclo dry-run roda sem erro")

        print("\n[8] LGPD")
        pacote = cliente.get("/leads/%s/exportar" % lead_id).json()
        checar(pacote["memoria"] is not None, "exportar traz a memoria da IA")
        checar(len(pacote["mensagens"]) == 6, "exportar traz as mensagens")

        checar(cliente.delete("/leads/%s" % lead_id).status_code == 204,
               "DELETE apaga o lead")
        checar(cliente.get("/leads/%s" % lead_id).status_code == 404,
               "lead apagado some do banco")
        # O teste que importa: apagar so a linha relacional deixaria o perfil
        # inteiro vivo na memoria e o lead ressuscitaria na proxima mensagem.
        depois = cliente.post("/chat", json={"lead_id": lead_id,
                                             "mensagem": "oi de novo"}).json()
        checar(not depois["perfil"].get("region"),
               "memoria da IA tambem foi apagada")

        print("\n[9] Validacao de entrada")
        checar(cliente.post("/chat", json={"mensagem": "   "}).status_code == 422,
               "mensagem vazia da 422")
        checar(cliente.post("/chat", json={"lead_id": "../../.env",
                                           "mensagem": "oi"}).status_code == 422,
               "lead_id com path traversal e recusado")

    print("\n" + "=" * 60)
    if falhas:
        print("%d FALHA(S):" % len(falhas))
        for f in falhas:
            print("  - %s" % f)
        return 1

    print("Tudo passou.")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    except Exception:
        traceback.print_exc()
        codigo = 2
    sys.exit(codigo)
