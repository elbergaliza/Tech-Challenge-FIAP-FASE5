# Modelos do banco.
#
# Importar este pacote registra todas as tabelas no metadata do SQLAlchemy, que
# e o que o `criar_tabelas()` precisa.

from models.agendamento import Agendamento, StatusAgendamento, TipoAgendamento
from models.conversa import Mensagem, PapelMensagem
from models.estado_conversa import EstadoConversa
from models.imovel import Imovel
from models.lead import Lead, Intencao, StatusLead, Temperatura, Urgencia

__all__ = [
    "Agendamento", "StatusAgendamento", "TipoAgendamento",
    "Mensagem", "PapelMensagem",
    "EstadoConversa",
    "Imovel",
    "Lead", "Intencao", "StatusLead", "Temperatura", "Urgencia",
]
