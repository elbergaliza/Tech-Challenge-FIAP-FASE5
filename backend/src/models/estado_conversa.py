# O estado da memoria da Pessoa 2, persistido no banco.
#
# Uma linha por lead, com o JSON inteiro que o `ConversationMemory` produz. Nao e
# para ser lido campo a campo pelo backend: quem entende esse formato e o modulo
# dela, e ele evolui (tem `version` la dentro). Aqui e so o meio de guardar.
#
# O `JsonFileStore` dela diz, no proprio docstring: "Para producao a Pessoa 3
# implementa a mesma interface sobre o banco; nada mais no modulo precisa mudar."
# E o que o `database/memory_store.py` faz.

from datetime import datetime, timezone

from database.db import Base
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column


def agora():
    return datetime.now(timezone.utc)


class EstadoConversa(Base):
    __tablename__ = "estado_conversa"

    # Sem ForeignKey para `leads` de proposito. A memoria e criada no
    # `start_turn`, e o `record_consent` pode acontecer antes de existir linha
    # de lead nenhuma; uma FK obrigaria o modulo dela a conhecer a nossa ordem
    # de escrita. O `forget()` da LGPD apaga esta linha diretamente.
    lead_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    estado_json: Mapped[str] = mapped_column(Text)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=agora, onupdate=agora,
    )

    def __repr__(self):
        return "<EstadoConversa %s>" % self.lead_id
