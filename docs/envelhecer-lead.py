"""Envelhece um lead para testar o follow-up automático sem esperar um dia.

O follow-up da Parte 2 é restritivo de propósito: exige 24h de silêncio (4h se a
urgência do lead for alta), consentimento registrado e nenhum pedido de
descadastro. Numa sessão de teste ninguém está calado há um dia, então a seção
"Precisam de atenção" do dashboard aparece vazia, e é impossível conferir a
tela.

Este script mexe só no relógio: recua a última mensagem do lead e zera o contador
de tentativas consecutivas. Nada de inventar dado.

    python docs/envelhecer-lead.py lead-0001
    python docs/envelhecer-lead.py lead-0001 --horas 80    # segunda tentativa
    python docs/envelhecer-lead.py --listar

Não precisa reiniciar o backend: a memória lê o estado do banco a cada consulta.

Usado pelo `docs/roteiro-de-teste-manual.md`, bloco 11.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANCO = os.path.join(RAIZ, "backend", "data", "app.db")


def conectar():
    if not os.path.exists(BANCO):
        sys.exit(
            "Banco nao encontrado em %s.\n"
            "Suba o backend uma vez (python backend/run.py) para ele nascer." % BANCO
        )
    return sqlite3.connect(BANCO)


def listar(banco):
    linhas = banco.execute(
        "select l.id, l.nome, l.status, e.estado_json"
        " from leads l left join estado_conversa e on e.lead_id = l.id"
        " order by l.id"
    ).fetchall()

    if not linhas:
        print("Nenhum lead no banco. Mande uma mensagem no chat primeiro.")
        return

    print("%-12s %-22s %-14s %s" % ("id", "nome", "status", "silencio"))
    for lead_id, nome, status, estado_json in linhas:
        if estado_json:
            estado = json.loads(estado_json)
            marca = estado.get("last_lead_message_at") or estado.get("created_at")
            horas = (
                datetime.now(timezone.utc) - datetime.fromisoformat(marca)
            ).total_seconds() / 3600
            silencio = "%.1fh" % horas
        else:
            silencio = "sem conversa"

        print("%-12s %-22s %-14s %s" % (lead_id, nome or "(sem nome)", status, silencio))


def envelhecer(banco, lead_id, horas):
    linha = banco.execute(
        "select estado_json from estado_conversa where lead_id = ?", (lead_id,)
    ).fetchone()

    if linha is None:
        sys.exit(
            "O lead %s nao tem memoria de conversa.\n"
            "Use --listar para ver os ids disponiveis." % lead_id
        )

    estado = json.loads(linha[0])

    if not estado.get("messages"):
        sys.exit("O lead %s nao tem mensagens: follow-up nao se aplica." % lead_id)

    estado["last_lead_message_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=horas)
    ).isoformat()
    # O contador conta tentativas CONSECUTIVAS sem resposta. Zerar coloca o lead
    # no primeiro passo da cadencia (24h), que e o caso mais facil de conferir.
    estado["followups_sent"] = 0

    banco.execute(
        "update estado_conversa set estado_json = ? where lead_id = ?",
        (json.dumps(estado, ensure_ascii=False), lead_id),
    )
    banco.commit()

    consentimento = (estado.get("consent") or {}).get("granted")
    print("%s agora esta com %dh de silencio." % (lead_id, horas))

    if not consentimento:
        print(
            "Atencao: este lead nao registrou consentimento, e a Parte 2 nao"
            " retoma sem ele (LGPD). Ele nao vai aparecer na lista."
        )
        return

    print("Confira em GET /dashboard/followups ou na secao 'Precisam de atencao'.")


def main():
    parser = argparse.ArgumentParser(
        description="Envelhece um lead para testar o follow-up.",
    )
    parser.add_argument("lead_id", nargs="?", help="id do lead, ex: lead-0001")
    parser.add_argument(
        "--horas",
        type=int,
        default=30,
        help="horas de silencio a simular (padrao: 30, acima das 24 da cadencia)",
    )
    parser.add_argument(
        "--listar",
        action="store_true",
        help="mostra os leads e quanto tempo cada um esta calado",
    )
    args = parser.parse_args()

    banco = conectar()

    if args.listar or not args.lead_id:
        listar(banco)
        if not args.lead_id and not args.listar:
            print("\nPasse o id de um lead para envelhecer. Ex: lead-0001")
        return

    envelhecer(banco, args.lead_id, args.horas)


if __name__ == "__main__":
    main()
