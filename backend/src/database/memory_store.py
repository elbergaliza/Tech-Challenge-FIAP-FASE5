# Store do `ConversationMemory` da Pessoa 2, sobre o nosso banco.
#
# Implementa os quatro metodos que ela definiu (`read`, `write`, `delete`,
# `list_ids`), como o docstring do `JsonFileStore` previu. Trocar o store nao muda
# nada no modulo dela: a memoria continua sendo dela, so o disco e nosso.
#
# O ganho de fazer isso em vez de deixar os JSONs soltos: um `docker rm` nao come
# o historico das conversas, `forget()` da LGPD apaga do mesmo lugar que o resto,
# e backup do banco leva a memoria junto.
#
# Cada chamada abre a propria sessao, e nao recebe a do request. O
# `ConversationMemory` e construido uma vez, no start do app, e vive alem de
# qualquer request; segurar uma sessao de request dentro dele daria uso de sessao
# ja fechada no job de follow-up.

import json

from database.db import SessionLocal
from models.estado_conversa import EstadoConversa


class SqlAlchemyStore:

    def __init__(self, session_factory=None):
        self.session_factory = session_factory or SessionLocal

    def read(self, lead_id):
        with self.session_factory() as db:
            linha = db.get(EstadoConversa, lead_id)
            if not linha:
                return None

            try:
                return json.loads(linha.estado_json)
            except (ValueError, TypeError) as erro:
                # Estado ilegivel e tratado como lead NOVO, nao como erro fatal.
                #
                # Sem isto, um unico registro corrompido (edicao manual, um
                # `docs/envelhecer-lead.py` interrompido no meio, um estado
                # gravado por uma versao anterior) rebentava a leitura e
                # derrubava junto o /dashboard/followups INTEIRO, para todos os
                # leads. Perder a memoria de UM lead e ruim; perder a tela e
                # pior.
                print("[memoria] estado_json ilegivel em %s, tratando como "
                      "novo: %s" % (lead_id, erro))
                return None

    def write(self, lead_id, state):
        payload = json.dumps(state, ensure_ascii=False)

        with self.session_factory() as db:
            linha = db.get(EstadoConversa, lead_id)
            if linha is None:
                db.add(EstadoConversa(lead_id=lead_id, estado_json=payload))
            else:
                linha.estado_json = payload
            db.commit()

    def delete(self, lead_id):
        with self.session_factory() as db:
            linha = db.get(EstadoConversa, lead_id)
            if linha is None:
                return False

            db.delete(linha)
            db.commit()
            return True

    def list_ids(self):
        with self.session_factory() as db:
            linhas = db.query(EstadoConversa.lead_id).order_by(
                EstadoConversa.lead_id
            ).all()
            return [linha[0] for linha in linhas]
