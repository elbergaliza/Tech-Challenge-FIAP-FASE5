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
        checar(len(historico["mensagens"]) == 6,
               "historico com 6 mensagens (%d)" % len(historico["mensagens"]))

        # A rota devolve o ESTADO da conversa, e nao so as mensagens. Sem isto o
        # painel "O que ja entendi" voltava VAZIO depois de um F5, dizendo
        # "manda a primeira mensagem" para quem tinha a conversa inteira atras:
        # o sistema lembrava e a tela desmentia, justamente no cenario 2 do
        # desafio.
        checar(historico["perfil"].get("region") == "Copacabana",
               "e o perfil volta junto, para o painel se redesenhar")
        checar(historico["perfil_label"].get("intent") == "compra",
               "com os rotulos ja em portugues")
        checar(historico["perfil_campos"].get("region") == "Região",
               "e com o nome de exibicao de cada campo")
        checar(historico["sugerir_agendamento"] is True,
               "e o seletor de data reabre se o perfil ja estava completo")
        checar(historico["score"] > 0 and historico["temperatura_label"] != "",
               "score e temperatura vem juntos, sem gastar cota")

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
        # O consentimento mora em dois lugares, e quem decide o follow-up e a
        # memoria da Parte 2, nao a coluna. Um PATCH que grava so a coluna faz
        # a tela dizer que o lead aceitou enquanto o agente segue proibido de
        # retomar: divergencia que so aparece dias depois, quando alguem
        # pergunta por que ninguem foi retomado.
        import services.ai_service as ai_service
        memoria = ai_service._init()["memory"]

        cliente.patch("/leads/%s" % lead_id, json={"consentimento": False})
        checar(not memoria.has_consent(lead_id),
               "revogar por PATCH chega na memoria da IA")

        cliente.patch("/leads/%s" % lead_id, json={"consentimento": True})
        checar(memoria.has_consent(lead_id),
               "conceder por PATCH chega na memoria da IA")

        manual = cliente.post("/leads", json={"nome": "Fora do chat",
                                              "consentimento": True}).json()
        checar(memoria.has_consent(manual["id"]),
               "consentimento no cadastro manual tambem chega na memoria")

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

        print("\n[9] Gemini fora do ar")
        # O 503 por demanda alta e rotina com a chave gratuita: em pico chega a
        # ~1 em 3 chamadas. Com as retentativas esgotadas, o turno nao pode
        # virar um pedido de desculpa, senao a qualificacao para justamente na
        # hora da demonstracao. O mock assume e pergunta o que falta.
        estado_ia = ai_service._init()
        agente_real = estado_ia["agente"]

        def agente_sobrecarregado(*_args, **_kwargs):
            return {"resposta": "Desculpe, tive um problema tecnico. "
                                "Pode tentar novamente?",
                    "dados_coletados": {}, "status_qualificacao": "erro",
                    "confianca": 0.0}

        estado_ia["agente"] = agente_sobrecarregado
        try:
            caido = cliente.post("/chat", json={"mensagem": "quero alugar"}).json()
        finally:
            estado_ia["agente"] = agente_real

        checar(caido["origem"] == "mock", "503 insistente cai no agente mock")
        checar("problema" not in caido["resposta"],
               "o lead recebe uma pergunta, nao um pedido de desculpa")
        checar(bool(caido["resposta"]), "a resposta nao vem vazia")
        # A memoria nao depende do agente: o que o lead disse tem que continuar
        # sendo anotado mesmo com o Gemini fora.
        checar(caido["perfil"].get("intent") == "RENT",
               "a memoria segue anotando com o Gemini fora")

        print("\n[10] Numeracao dos leads")
        # O id vinha da CONTAGEM de leads. Apagar um do meio (o botao de LGPD
        # do chat faz isso, e o roteiro de teste manda fazer) deixava o proximo
        # numero apontando para um id ocupado, e o cadastro do proximo
        # visitante morria em 500. Com o maximo, o buraco fica no lugar.
        primeiro = cliente.post("/chat", json={"mensagem": "oi, procuro imovel"}).json()
        segundo = cliente.post("/chat", json={"mensagem": "oi, procuro imovel"}).json()
        checar(primeiro["lead_id"] != segundo["lead_id"], "dois visitantes, dois ids")

        checar(cliente.delete("/leads/%s" % primeiro["lead_id"]).status_code == 204,
               "apaga o lead do meio")

        terceiro = cliente.post("/chat", json={"mensagem": "oi, procuro imovel"})
        checar(terceiro.status_code == 200,
               "o proximo visitante ainda entra depois de uma exclusao")
        novo_id = terceiro.json()["lead_id"] if terceiro.status_code == 200 else None
        checar(novo_id not in (primeiro["lead_id"], segundo["lead_id"]),
               "e nao herda o id de quem foi apagado")

        print("\n[11] Resposta curta no contexto da pergunta")
        # A extracao da Pessoa 1 le "3 quartos" e nao le "3". So que "3" e o
        # que se responde a "quantos quartos voce precisa?", e sem interpretar
        # no contexto a conversa entrava em looping: o campo nunca era gravado
        # e o agente repetia a mesma pergunta para sempre.
        estado_ia = ai_service._init()
        agente_real = estado_ia["agente"]
        estado_ia["agente"] = None  # forca o mock, como um Gemini fora do ar

        try:
            curto = cliente.post("/chat", json={"mensagem": "quero alugar"}).json()
            curto_id = curto["lead_id"]
            cliente.post("/chat", json={"lead_id": curto_id, "mensagem": "Botafogo"})

            antes = cliente.post("/chat", json={"lead_id": curto_id,
                                                "mensagem": "quantos?"}).json()
            depois = cliente.post("/chat", json={"lead_id": curto_id,
                                                 "mensagem": "3"}).json()

            checar(depois["perfil"].get("bedrooms") == "3",
                   "'3' vira quartos quando a pergunta em aberto e quartos")
            checar(depois["resposta"] != antes["resposta"],
                   "e o agente para de repetir a mesma pergunta")

            # O anti-looping: mesmo o que a extracao NAO entende nao pode
            # produzir a mesma frase duas vezes seguidas.
            um = cliente.post("/chat", json={"lead_id": curto_id,
                                             "mensagem": "xpto"}).json()
            dois = cliente.post("/chat", json={"lead_id": curto_id,
                                               "mensagem": "xpto"}).json()
            checar(um["resposta"] != dois["resposta"],
                   "resposta nao entendida faz o mock pedir o formato")

            prazo = cliente.post("/chat", json={"lead_id": curto_id,
                                                "mensagem": "preciso mudar esse mes"}).json()
            checar(prazo["perfil"].get("urgency") == "high",
                   "'esse mes' e urgencia alta, nao o default baixa")

            # Regressao: "R$ 8.550" respondido enquanto a pergunta em aberto
            # era QUARTOS virava "550 quartos" e o orcamento sumia. A forma da
            # resposta tem que mandar mais que a ordem da coleta.
            dinheiro = cliente.post("/chat", json={"mensagem": "quero alugar"}).json()
            dinheiro_id = dinheiro["lead_id"]
            cliente.post("/chat", json={"lead_id": dinheiro_id, "mensagem": "botafogo"})
            valor = cliente.post("/chat", json={"lead_id": dinheiro_id,
                                                "mensagem": "R$ 8.550"}).json()

            checar(valor["perfil"].get("bedrooms") is None,
                   "valor em reais nao vira quantidade de quartos")
            checar(valor["perfil"].get("price_range") is not None,
                   "e e gravado como orcamento, que era o que o lead quis dizer")

            quartos = cliente.post("/chat", json={"lead_id": dinheiro_id,
                                                  "mensagem": "3"}).json()
            checar(quartos["perfil"].get("bedrooms") == "3",
                   "e o numero de quartos ainda pode ser informado depois")
        finally:
            estado_ia["agente"] = agente_real

        print("\n[12] Perfil investidor e dado incompleto")
        estado_ia["agente"] = None  # mock, para o roteiro ser deterministico
        try:
            inv = cliente.post("/chat", json={
                "mensagem": "quero investir em imoveis para alugar"}).json()
            inv_id = inv["lead_id"]
            checar(inv["perfil"].get("intent") == "INVEST",
                   "'investir para alugar' e investimento, nao aluguel")
            checar("ticket" in inv["resposta"].lower(),
                   "e a proxima pergunta e o ticket, nao quartos")

            ticket = cliente.post("/chat", json={"lead_id": inv_id,
                                                 "mensagem": "tenho 800 mil"}).json()
            checar(ticket["perfil"].get("investor_ticket") == "800k",
                   "o valor vira ticket do investidor")
            checar(ticket["perfil"].get("price_range") is None,
                   "e nao orcamento: sao coisas diferentes")

            retorno = cliente.post("/chat", json={"lead_id": inv_id,
                                                  "mensagem": "2000"}).json()
            checar(retorno["perfil"].get("expected_return") == "2k",
                   "o retorno esperado e gravado")

            # O que nao da para usar tem que virar pedido, nao silencio.
            cliente.post("/chat", json={"lead_id": inv_id, "mensagem": "Leblon"})
            cliente.post("/chat", json={"lead_id": inv_id, "mensagem": "urgente"})
            sem_ddd = cliente.post("/chat", json={"lead_id": inv_id,
                                                  "mensagem": "Marcos, 33410549"}).json()

            checar(sem_ddd["perfil"].get("phone") is None,
                   "telefone sem DDD nao e gravado")
            checar("ddd" in sem_ddd["resposta"].lower(),
                   "e o agente pede o DDD em vez de seguir em frente")
        finally:
            estado_ia["agente"] = agente_real

        print("\n[13] O convite de agendamento e o seletor da tela")
        # O agente convidava para "escolher o dia ai embaixo" antes de o
        # seletor existir, e o cliente ficava procurando um botao que nao
        # estava na tela. Quem sabe se da para agendar e o backend.
        import lead_profile

        parcial = ai_service.processar_mensagem("lead-convite", "quero alugar")
        checar(parcial["sugerir_agendamento"] is False,
               "perfil incompleto nao habilita o seletor")
        checar("AINDA NAO OFERECA AGENDAMENTO" in parcial["contexto"],
               "e o agente e instruido a nao convidar ainda")

        for texto in ("Botafogo", "3 quartos", "4 mil por mes", "urgente",
                      "meu telefone e 21 99999-4410"):
            completo = ai_service.processar_mensagem("lead-convite", texto)

        checar(lead_profile.next_to_collect(completo["perfil"]) is None,
               "o perfil fecha depois de todas as respostas")
        checar(completo["sugerir_agendamento"] is True,
               "e ai sim o seletor aparece")
        checar("SELETOR DE DATA ESTA VISIVEL" in completo["contexto"],
               "e o agente e liberado a convidar")

        print("\n[14] Pseudonimizacao na busca de imoveis")
        # A busca do RAG embeda o texto no Gemini, que e terceiro. Com a
        # mensagem crua, nome, e-mail e telefone saiam junto, furando a unica
        # protecao que o projeto afirma ter na apresentacao.
        capturado = {}
        buscar_real = ai_service._buscar

        def espiao(memory, lead_id, perfil, texto):
            capturado["texto"] = texto
            return buscar_real(memory, lead_id, perfil, texto)

        ai_service._buscar = espiao
        try:
            cliente.post("/chat", json={
                "mensagem": "meu nome e Marcos, email marcos@teste.com, "
                            "telefone 21 99999-4410, quero 3 quartos em Botafogo"})
        finally:
            ai_service._buscar = buscar_real

        enviado = capturado.get("texto", "")
        checar(bool(enviado), "a busca foi chamada")
        for pii in ("marcos@teste.com", "99999-4410", "Marcos"):
            checar(pii not in enviado,
                   "%s nao vai para o embedding do Gemini" % pii)
        checar("Botafogo" in enviado and "3 quartos" in enviado,
               "e o que serve para buscar imovel continua na consulta")

        print("\n[15] Validacao de entrada")
        checar(cliente.post("/chat", json={"mensagem": "   "}).status_code == 422,
               "mensagem vazia da 422")
        checar(cliente.post("/chat", json={"lead_id": "../../.env",
                                           "mensagem": "oi"}).status_code == 422,
               "lead_id com path traversal e recusado")

        print("\n[16] Agendamento no passado")
        # O seletor da tela ja tem `min`, mas validacao de front e
        # conveniencia, nao garantia: qualquer POST direto passava, e o backend
        # respondia 201. O lead saia da conversa achando que tinha visita
        # marcada para o mes anterior.
        lead_ag = cliente.post("/chat", json={"mensagem": "quero visitar"}) \
            .json()["lead_id"]
        ontem = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        amanha = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

        r = cliente.post("/leads/%s/schedule" % lead_ag,
                         json={"data_hora": ontem, "tipo": "VISITA"})
        checar(r.status_code == 422, "visita no passado e recusada com 422")

        r = cliente.post("/leads/%s/schedule" % lead_ag,
                         json={"data_hora": amanha, "tipo": "VISITA"})
        checar(r.status_code in (200, 201), "visita no futuro continua aceita")

        print("\n[17] Resposta vazia do agente cai no mock, nao em erro")
        # `response.text` e None quando a resposta vem sem parte de texto
        # (bloqueio de safety, ou MAX_TOKENS gasto so em "thinking", que e o
        # padrao do gemini-2.5-flash). Devolver isso cru rebentava com
        # TypeError, caia no except generico e virava "tive um problema
        # tecnico" SEM marca na tela e sem avancar a qualificacao: repetir a
        # mensagem dava o mesmo resultado, e quem testava ficava travado.
        agente_original = ai_service._estado.get("agente")
        ai_service._estado["agente"] = lambda *a, **k: {"resposta": None}
        try:
            r = cliente.post("/chat", json={"mensagem": "quero alugar"}).json()
        finally:
            ai_service._estado["agente"] = agente_original

        checar(r.get("origem") == "mock",
               "resposta sem texto e marcada como mock, nao como erro")
        checar("problema" not in (r.get("resposta") or "").lower(),
               "e o lead recebe a proxima pergunta, nao um pedido de desculpa")

        print("\n[18] /health conta o que aconteceu de verdade")
        # O modo era decidido UMA vez, no boot, e nunca mais: com chave
        # invalida, projeto bloqueado ou cota estourada, o /health continuava
        # dizendo "gemini" e o selo do cabecalho ficava verde enquanto TODO
        # turno era respondido pelo mock.
        saude = cliente.get("/health").json()["ia"]
        checar(saude["agente"] == "mock",
               "depois de um turno em mock, /health para de dizer gemini")
        checar("modo_configurado" in saude,
               "e o modo configurado no boot continua visivel, para comparar")

        print("\n[19] lead_id nao e adivinhavel")
        # Nao ha login, e o id vem do proprio cliente: sequencial, trocar um
        # digito na URL dava acesso a EXPORTAR e a APAGAR os dados de qualquer
        # outra pessoa.
        ids = [cliente.post("/chat", json={"mensagem": "oi"}).json()["lead_id"]
               for _ in range(3)]
        checar(len(set(ids)) == 3, "ids diferentes a cada lead")
        checar(not any(i.endswith(("0001", "0002", "0003")) for i in ids),
               "e nenhum deles e o proximo numero da sequencia")
        checar(all(len(i) >= 16 for i in ids),
               "o espaco e grande demais para chute")

        print("\n[20] Estado ilegivel nao derruba o painel")
        # Um `estado_json` corrompido (edicao manual, um script interrompido,
        # um estado gravado por versao anterior) derrubava a varredura INTEIRA
        # com KeyError, e o corretor perdia a lista de TODOS os leads por causa
        # de um registro ruim.
        from database.db import SessionLocal
        from models.estado_conversa import EstadoConversa

        with SessionLocal() as db:
            linha = db.get(EstadoConversa, ids[0])
            if linha is not None:
                linha.estado_json = "{isto nao e json valido"
                db.commit()

        r = cliente.get("/dashboard/followups")
        checar(r.status_code == 200,
               "a lista de follow-ups responde mesmo com um estado corrompido")
        r = cliente.get("/leads")
        checar(r.status_code == 200, "e a lista de leads tambem")

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
