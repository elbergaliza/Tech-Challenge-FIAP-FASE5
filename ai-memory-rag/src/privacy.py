"""
Pseudonimização de dados pessoais antes de chegarem ao LLM.

O problema concreto que isto resolve: o Gemini é um terceiro. Todo texto que o
agente manda para ele sai do nosso controle. Hoje o `ai-core/src/agent.py`
concatena a conversa inteira e envia, e-mail e telefone do lead junto. Para a
LGPD isso é tratamento de dado pessoal com transferência a terceiro, e os
princípios de Necessidade e Minimização dizem que só deve sair o mínimo
necessário para a finalidade.

A técnica é PSEUDONIMIZAÇÃO, não anonimização, e a diferença importa. A Aula 04
de "Privacidade e Proteção de Dados" alerta que a IA pode reidentificar dados
anonimizados combinando conjuntos; e, do lado prático, simplesmente apagar o
nome estragaria a conversa humanizada, que é critério de avaliação. Então:

    "Oi, meu nome é João, meu email é joao@x.com"
        -> mascarar ->
    "Oi, meu nome é [NOME_1], meu email é [EMAIL_1]"        (isto vai ao LLM)
        -> LLM responde ->
    "Claro, [NOME_1]! Vou enviar as opções para [EMAIL_1]."
        -> restaurar ->
    "Claro, João! Vou enviar as opções para joao@x.com."    (isto vai ao lead)

O mapa de substituição nunca sai da nossa máquina. O LLM trabalha com
referências opacas e o lead continua sendo chamado pelo nome.

Uso:

    pseudo = Pseudonymizer()
    safe, mapping = pseudo.mask(texto_do_lead)
    reply = chamar_llm(safe)
    final = pseudo.restore(reply, mapping)

Para manter o mesmo apelido entre turnos da conversa, passe o mapa anterior:

    safe, mapping = pseudo.mask(nova_mensagem, mapping=mapa_da_sessao)

Os apelidos ([NOME_1], [EMAIL_1]) ficam em português porque aparecem dentro de
prompts em português e o modelo os lê como parte daquele texto.
"""

import re

# ---------------------------------------------------------------------------
# Detectores
# ---------------------------------------------------------------------------
# A ordem importa: o padrão mais específico casa primeiro, senão um e-mail vira
# meio telefone. E-mail vem primeiro porque contém dígitos; CPF e CEP vêm antes
# de telefone porque suas formas são mais restritas.
#
# O que NÃO é detectado, de propósito:
#   * CPF sem formatação (11 dígitos crus) é ambíguo com celular com DDD. Fica
#     tratado como telefone, que é a leitura mais provável numa conversa de
#     imobiliária.
#   * CEP exige o hífen. Sem ele, 8 dígitos crus casariam com valores de imóvel.
PATTERNS = (
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("CPF", re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")),
    ("CEP", re.compile(r"\b\d{5}-\d{3}\b")),
    ("TELEFONE", re.compile(
        r"(?:\+55\s?)?\(?\d{2}\)?[\s.-]?9?\d{4}[\s.-]?\d{4}\b"
    )),
)

_ALIAS = re.compile(r"\[([A-Z]+)_(\d+)\]")

# Nomes muito curtos não são mascarados: o risco de trocar "Ana" dentro de
# "Ananindeua" é real, mas o risco maior é mascarar partículas de duas letras e
# picotar o texto inteiro.
MIN_NAME_LENGTH = 3

# Nome próprio não é detectável por regex em geral, mas em conversa de
# atendimento ele quase sempre chega DECLARADO: "meu nome é X", "sou o X".
# Exigir inicial maiúscula na captura descarta a maior parte dos falsos
# positivos ("sou casado", "sou de niterói" em minúscula não casam).
#
# Isto existe por um motivo específico: o nome do lead só passa a ser conhecido
# DEPOIS que ele se apresenta, e sem detecção na entrada ele iria em claro para
# o LLM exatamente na mensagem que o revela, que é a que mais precisa de
# proteção.
#
# O gatilho ("meu nome é", "sou o") casa sem diferenciar maiúsculas, com a flag
# escopada `(?i:...)`; a captura NÃO, porque é a inicial maiúscula que separa
# "Sou Marina" de "sou casado". Compilar o padrão inteiro com IGNORECASE
# perderia essa distinção; compilar sem nenhum ignore-case perderia "Sou João"
# no início de frase, que é o caso mais comum.
_NAME_CAPTURE = r"([A-ZÀ-Ý][\wÀ-ÿ]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ]+)*)"

_NAME_DECLARATIONS = (
    re.compile(r"(?i:(?:meu\s+)?nome\s+[ée])\s+" + _NAME_CAPTURE),
    re.compile(r"(?i:\bchamo[-\s]me)\s+" + _NAME_CAPTURE),
    re.compile(r"(?i:\b(?:eu\s+)?sou\s+(?:[oa]\s+)?)" + _NAME_CAPTURE),
    re.compile(r"(?i:\baqui\s+[ée]\s+(?:[oa]\s+)?)" + _NAME_CAPTURE),
)


def detect_names(text):
    """Nomes próprios declarados no texto.

    Devolve o nome completo e também cada parte com 3+ letras, para que uma
    menção posterior só ao primeiro nome também seja mascarada. O primeiro nome
    vem na frente da lista, porque é a forma usada na restauração: "Prazer,
    João!" soa melhor do que "Prazer, João Pereira!" numa conversa de WhatsApp.
    """
    if not text:
        return []

    found = []
    for pattern in _NAME_DECLARATIONS:
        for match in pattern.finditer(text):
            full = match.group(1).strip()
            if len(full) < MIN_NAME_LENGTH:
                continue

            parts = full.split()
            for candidate in [parts[0], full] + parts[1:]:
                if len(candidate) >= MIN_NAME_LENGTH and candidate not in found:
                    found.append(candidate)

    return found


def _next_alias(mapping, kind):
    """Gera o próximo apelido livre do tipo, olhando o mapa já existente."""
    used = []
    for alias in mapping:
        match = _ALIAS.fullmatch(alias)
        if match and match.group(1) == kind:
            used.append(int(match.group(2)))

    return "[%s_%d]" % (kind, max(used) + 1 if used else 1)


class Pseudonymizer:
    """Substitui dados pessoais por apelidos estáveis e reversíveis."""

    def mask(self, text, mapping=None, names=None):
        """Devolve `(texto_mascarado, mapping)`.

        `mapping` é `{apelido: valor_original}`. Passe o mapa da sessão para que
        o mesmo e-mail receba o mesmo apelido em todos os turnos, senão o LLM vê
        [EMAIL_1] e [EMAIL_2] para uma pessoa só e se confunde.

        `names` é a lista de nomes próprios conhecidos do lead. Nome não é
        detectável por regex com confiabilidade, mas já o temos no perfil, então
        é mascarado por correspondência exata.
        """
        if text is None:
            return "", dict(mapping or {})

        mapping = dict(mapping or {})
        # Índice inverso para reaproveitar o apelido de um valor já visto.
        by_value = {value: alias for alias, value in mapping.items()}
        result = text

        for kind, pattern in PATTERNS:
            def substitute(match, kind=kind):
                value = match.group(0)
                if value in by_value:
                    return by_value[value]

                alias = _next_alias(mapping, kind)
                mapping[alias] = value
                by_value[value] = alias
                return alias

            result = pattern.sub(substitute, result)

        # Todas as variantes recebidas são da MESMA pessoa: a memória é por
        # lead, e `names` traz o nome do perfil mais o que foi declarado na
        # mensagem. Sem isto, "João Pereira", "João" e "Pereira" virariam
        # [NOME_1], [NOME_2] e [NOME_3], e o LLM concluiria que são três pessoas
        # diferentes.
        variants = [
            name for name in (names or [])
            if name and len(name) >= MIN_NAME_LENGTH and name != "undefined"
        ]

        if variants:
            alias = next((by_value[v] for v in variants if v in by_value), None)

            if alias is None:
                alias = _next_alias(mapping, "NOME")
                # A primeira variante é a forma preferida na restauração, e
                # `detect_names` coloca o primeiro nome na frente.
                mapping[alias] = variants[0]

            for variant in variants:
                by_value[variant] = alias

            # Do mais longo para o mais curto, senão "João" consumiria a
            # primeira metade e sobraria "[NOME_1] Pereira".
            for variant in sorted(set(variants), key=lambda n: (-len(n), n)):
                result = re.sub(
                    r"\b%s\b" % re.escape(variant), alias, result, flags=re.IGNORECASE
                )

        return result, mapping

    def restore(self, text, mapping):
        """Troca os apelidos de volta pelos valores reais.

        Aplicado na resposta do LLM antes de ela chegar ao lead. Apelidos que o
        modelo tenha inventado e não estejam no mapa são removidos, para que o
        lead nunca veja um "[EMAIL_7]" solto na tela.
        """
        if text is None:
            return ""

        result = text
        for alias, value in (mapping or {}).items():
            result = result.replace(alias, value)

        return _ALIAS.sub("", result)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def contains_pii(text):
    """Tipos de dado pessoal presentes no texto. Útil em teste e auditoria."""
    if not text:
        return []

    return sorted({kind for kind, pattern in PATTERNS if pattern.search(text)})


def mask_for_log(text, names=None):
    """Mascaramento IRREVERSÍVEL, para logs e telemetria.

    Log é o lugar clássico de vazamento: fica em disco, vai para agregador e
    ninguém aplica retenção. Aqui não há mapa para desfazer, de propósito.
    """
    if not text:
        return ""

    result = text
    for kind, pattern in PATTERNS:
        result = pattern.sub("[%s]" % kind, result)

    for name in (names or []):
        if name and len(name) >= MIN_NAME_LENGTH and name != "undefined":
            result = re.sub(
                r"\b%s\b" % re.escape(name), "[NOME]", result, flags=re.IGNORECASE
            )

    return result
