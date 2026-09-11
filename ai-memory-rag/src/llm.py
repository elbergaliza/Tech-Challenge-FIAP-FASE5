"""
Acesso ao LLM, com degradação graciosa.

Dois módulos dependem disto: o summarizer e o gerador de follow-up. Ambos
seguem a mesma regra: **nunca falhar por falta de chave**. Sem
`GEMINI_API_KEY`, sem rede ou com a API fora do ar, quem chama cai num caminho
heurístico determinístico.

Isso não é preciosismo. O resumo aparece no dashboard do corretor: se a chamada
ao Gemini falhar durante a apresentação, um resumo mais pobre montado por regra
é melhor do que uma tela de erro. E permite que os testes rodem offline, sem
gastar cota.

Todo resultado registra de onde veio (`source`), para que a interface nunca
apresente texto de regra como se fosse texto de IA.

Sobre o id do modelo: o `ai-core/src/agent.py` usa `gemini-3.6-flash`. Não
consegui confirmar que esse id existe. Aqui o padrão é configurável por
`GEMINI_MODEL`, e a recomendação para o grupo é fixar UM id verificado nessa
variável e os dois módulos passarem a lê-la.
"""

import json
import os
import re
import time

DEFAULT_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class UnavailableClient:
    """Nenhum LLM configurado. Quem chama usa o caminho heurístico."""

    name = "unavailable"
    available = False

    def __init__(self, reason="LLM não configurado"):
        self.reason = reason

    def generate(self, prompt, temperature=0.4):
        raise RuntimeError(self.reason)


# Erros que valem uma segunda tentativa: sao de capacidade momentanea do lado
# do Google, nao de nada errado no pedido. Medidos com a chave do projeto, ~1 em
# 3 chamadas voltava 503 em horario de pico, no `gemini-3.6-flash` e igualmente
# no `gemini-2.5-flash`, entao trocar de modelo nao resolve; esperar resolve.
# 429 NAO esta nesta lista, e a ausencia dele e deliberada.
#
# A janela de cota por minuto do Gemini so reabre em ate 60 segundos, e a
# cadencia aqui e de 1s e 3s: as tres tentativas falhavam do mesmo jeito, o
# turno caia no mock exatamente como cairia na primeira, e o custo eram TRES
# requisicoes da cota diaria de 20 em vez de uma. Numa tarde de testes isso
# sozinho consumia a cota do dia em um terco do tempo.
#
# O que sobra aqui sao os erros que quatro segundos realmente resolvem:
# capacidade momentanea do lado do Google. Medidos com a chave do projeto, ~1 em
# 3 chamadas voltava 503 em horario de pico, no `gemini-3.6-flash` e igualmente
# no `gemini-2.5-flash`, entao trocar de modelo nao resolve; esperar resolve.
TRANSIENT_MARKERS = ("503", "UNAVAILABLE", "500", "INTERNAL",
                     "overloaded", "high demand")

# Curto de proposito: isto roda no meio de uma conversa, e um humano do outro
# lado da tela desiste antes de dez segundos.
RETRY_WAITS = (1.0, 3.0)

# Teto por chamada, em milissegundos. Ver o comentario no `GeminiClient`.
TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "25000"))


# Mantido para quem ainda chama `is_transient` passando um 429 explicito: com
# o 429 fora de TRANSIENT_MARKERS os dois sabores ja caem no mesmo lugar, e
# esta tupla existe para o motivo continuar documentado e testavel.
#
# A cota gratuita do Gemini e de 20 requisicoes por dia e POR MODELO, entao o
# 429 de dia e rotina numa tarde de testes, nao excecao.
COTA_DIARIA = ("PerDay", "per day")


def is_transient(error):
    texto = str(error)
    if any(marca in texto for marca in COTA_DIARIA):
        return False
    return any(marker in texto for marker in TRANSIENT_MARKERS)


def retry_transient(fn, waits=RETRY_WAITS, sleep=time.sleep, on_retry=None,
                    retryable=None):
    """Roda `fn`, retentando so o que e transitorio.

    Erro permanente (chave invalida, modelo inexistente, pedido malformado)
    sobe na primeira tentativa: retentar so gastaria cota e tempo.

    `retryable` troca o criterio. O padrao le o texto do erro, que serve para
    excecoes vindas da API; quem detecta sobrecarga de outro jeito, como pelo
    texto de uma resposta ja tratada, passa o seu proprio.
    """
    retryable = retryable or is_transient
    ultimo = None

    for tentativa in range(len(waits) + 1):
        try:
            return fn()
        except Exception as error:
            ultimo = error
            if not retryable(error) or tentativa == len(waits):
                raise
            if on_retry:
                on_retry(tentativa + 1, waits[tentativa], error)
            sleep(waits[tentativa])

    raise ultimo  # inalcancavel; deixa a intencao explicita


class GeminiClient:

    available = True

    def __init__(self, api_key=None, model=None):
        self.model = model or DEFAULT_MODEL
        self.name = self.model

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY não encontrada")

        from google import genai  # import lazy: offline não precisa do pacote

        # Mesmo teto do cliente da Parte 1, e pelo mesmo motivo: sem
        # `http_options`, o SDK passa `timeout=None` para o httpx, que
        # significa "espere para sempre". Aqui a espera nao trava uma tela (o
        # resumo e o follow-up rodam em segundo plano), mas trava o JOB: uma
        # conexao pendurada segurava o ciclo inteiro e o `max_instances=1`
        # impedia o proximo de comecar, entao o follow-up simplesmente parava
        # de acontecer, em silencio.
        self._client = genai.Client(api_key=api_key,
                                    http_options={"timeout": TIMEOUT_MS})
        self._sleep = time.sleep

    def generate(self, prompt, temperature=0.4):
        def chamada():
            try:
                from google.genai import types

                return self._client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=temperature),
                )
            except ImportError:
                return self._client.models.generate_content(
                    model=self.model, contents=prompt
                )

        response = retry_transient(chamada, sleep=self._sleep)
        return (response.text or "").strip()


class StubClient:
    """Devolve respostas pré-definidas. Para testes e demonstração."""

    name = "stub"
    available = True

    def __init__(self, replies):
        self.replies = list(replies) if isinstance(replies, (list, tuple)) else [replies]
        self.calls = []

    def generate(self, prompt, temperature=0.4):
        self.calls.append(prompt)
        if not self.replies:
            raise RuntimeError("StubClient ficou sem respostas")

        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def get_client(prefer=None, api_key=None, model=None):
    """Devolve o melhor cliente disponível, sem levantar exceção."""
    if prefer == "unavailable":
        return UnavailableClient("forçado pelo chamador")

    try:
        return GeminiClient(api_key=api_key, model=model)
    except Exception as error:
        return UnavailableClient(str(error))


def extract_json(text):
    """Extrai o objeto JSON da resposta do modelo.

    Modelos costumam embrulhar JSON em cerca de markdown, ou prefaciar com
    "Aqui está o resumo:". Falhar por causa disso seria desperdício, então a
    extração é tolerante: acha o primeiro bloco entre chaves e tenta ler.
    Devolve None quando não há JSON válido, e quem chama decide o fallback.
    """
    if not text:
        return None

    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except (ValueError, TypeError):
        pass

    match = _JSON_BLOCK.search(cleaned)
    if not match:
        return None

    try:
        return json.loads(match.group(0))
    except (ValueError, TypeError):
        return None
