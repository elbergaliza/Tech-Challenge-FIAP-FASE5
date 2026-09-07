# Historico de mensagens.
#
# Por que existe, se a memoria da Pessoa 2 ja guarda as mensagens
# --------------------------------------------------------------
# Sao duas leituras diferentes do mesmo fato, e nenhuma serve para as duas coisas:
#
#   * `estado_conversa` (a memoria) guarda o estado que a IA precisa para pensar:
#     janela, mapa de apelidos, resumo, contadores de esquiva. E um blob e so faz
#     sentido inteiro.
#   * `mensagens` (esta tabela) e a projecao relacional que o dashboard e a API
#     consultam: paginar, filtrar por lead, contar por dia, juntar com agendamento.
#
# Escrever nos dois na mesma transacao e barato e evita ter que desserializar o
# blob inteiro para responder um GET. As duas vivem no MESMO banco, entao nao ha
# "duas fontes de verdade" em sistemas separados que possam divergir por rede.

import enum
from datetime import datetime, timezone

from database.db import Base
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship


class PapelMensagem(str, enum.Enum):
    USER = "user"
    ASSISTANT = "assistant"
    # Follow-up nao e resposta a nada: e o agente iniciando contato. Separar do
    # `assistant` deixa o dashboard mostrar "3 follow-ups sem resposta" sem
    # heuristica em cima do texto.
    FOLLOWUP = "followup"
    SYSTEM = "system"


class Mensagem(Base):
    __tablename__ = "mensagens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    lead_id: Mapped[str] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True,
    )

    papel: Mapped[str] = mapped_column(String(16))
    conteudo: Mapped[str] = mapped_column(Text)

    # Ids dos imoveis que o RAG mostrou junto desta resposta, separados por
    # virgula. Serve para a tela mostrar os cards e para nao repetir imovel.
    imoveis_sugeridos: Mapped[str | None] = mapped_column(Text)
    # "gemini", "mock", "heuristic": de onde saiu a resposta. Numa demo em que
    # a cota pode estourar no meio, saber isso vale o campo.
    origem: Mapped[str | None] = mapped_column(String(30))

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True,
    )

    lead: Mapped["Lead"] = relationship(back_populates="mensagens")  # noqa: F821

    @property
    def imoveis(self) -> list[str]:
        return [i for i in (self.imoveis_sugeridos or "").split(",") if i]

    def __repr__(self):
        return "<Mensagem %s %s %r>" % (self.lead_id, self.papel, self.conteudo[:30])
