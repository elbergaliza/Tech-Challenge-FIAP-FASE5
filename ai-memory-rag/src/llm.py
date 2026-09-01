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


class GeminiClient:

    available = True

    def __init__(self, api_key=None, model=None):
        self.model = model or DEFAULT_MODEL
        self.name = self.model

        api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY não encontrada")

        from google import genai  # import lazy: offline não precisa do pacote

        self._client = genai.Client(api_key=api_key)

    def generate(self, prompt, temperature=0.4):
        try:
            from google.genai import types

            response = self._client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
        except ImportError:
            response = self._client.models.generate_content(
                model=self.model, contents=prompt
            )

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
