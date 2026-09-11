"""Diagnostica a chave do Gemini e traduz o erro em o que fazer.

A API devolve mensagens que parecem todas iguais ("429", "403") e significam
coisas bem diferentes: cota do dia acabada, credito pre-pago zerado, projeto
bloqueado, tipo de chave errado. Cada uma pede uma acao diferente, e adivinhar
custa uma tarde.

    python docs/verificar-chave.py

Le o `.env` da raiz. Nao imprime a chave: so o prefixo e o tamanho.
"""

import os
import sys
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    from google import genai
except ImportError:
    sys.exit(
        "Faltam dependencias. Rode:\n"
        "  .venv\\Scripts\\python.exe -m pip install -r backend/requirements.txt"
    )

load_dotenv(os.path.join(RAIZ, ".env"))

CHAVE = (os.getenv("GEMINI_API_KEY") or "").strip()
MODELO = (os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()


def diagnostico(erro: str) -> str:
    # Traduz o erro bruto na acao correspondente.
    if "ACCESS_TOKEN_TYPE_UNSUPPORTED" in erro or "401" in erro:
        return ("O tipo da chave nao serve para esta API. Gere uma chave de API\n"
                "        padrao (prefixo AIza) no AI Studio, nao um token de outro tipo.")
    if "PERMISSION_DENIED" in erro or "denied access" in erro:
        return ("O PROJETO esta bloqueado, nao e cota. Abra https://ai.studio/projects,\n"
                "        veja o aviso no projeto. Recarregar credito nao resolve sozinho;\n"
                "        o caminho rapido costuma ser criar um projeto novo com chave nova.")
    if "prepayment" in erro or "credits are depleted" in erro:
        return ("Credito pre-pago zerado. AI Studio > Projects > Set up billing,\n"
                "        recarregue (minimo US$ 5) e ligue o Monthly spend cap.")
    if "free_tier" in erro or ("429" in erro and "PerDay" in erro):
        return ("Cota GRATUITA do dia acabada para este modelo (sao 20 por dia e por\n"
                "        modelo). Espere amanha, troque o GEMINI_MODEL, ou habilite\n"
                "        faturamento.")
    if "429" in erro:
        return "Limite por minuto. Espere alguns segundos e tente de novo."
    if "503" in erro or "UNAVAILABLE" in erro:
        return ("Modelo sobrecarregado no lado do Google. E passageiro, e o app cai\n"
                "        no agente mock sozinho enquanto durar.")
    if "404" in erro:
        return "Este id de modelo nao existe ou nao esta liberado para a sua chave."
    return "Erro nao mapeado. Leve a mensagem acima para o grupo."


def main():
    print("Chave e modelo configurados no .env da raiz\n")

    if not CHAVE:
        print("  GEMINI_API_KEY nao esta definida.")
        print("  Sem ela o sistema inteiro funciona em modo mock, o que serve para")
        print("  desenvolver e para apresentar, so nao usa IA de verdade.")
        return 1

    print("  chave:  %s... (%d caracteres)" % (CHAVE[:8], len(CHAVE)))
    print("  modelo: %s" % MODELO)

    if not CHAVE.startswith("AIza"):
        print("\n  ATENCAO: a chave nao comeca com 'AIza', que e o formato padrao da")
        print("  API. Chaves com outro prefixo sao emitidas em algumas contas e")
        print("  costumam ser recusadas por esta API. Se os testes abaixo falharem")
        print("  com 401 ou 403, gere uma chave de API padrao no AI Studio.")

    cliente = genai.Client(api_key=CHAVE)
    verificacoes = [
        ("geracao (%s)" % MODELO,
         lambda: cliente.models.generate_content(model=MODELO, contents="Responda: ok")),
        ("embedding (RAG)",
         lambda: cliente.models.embed_content(model="gemini-embedding-001",
                                              contents="teste")),
    ]

    falhas = 0
    print()
    for rotulo, chamada in verificacoes:
        inicio = time.time()
        try:
            chamada()
            print("  ok    %-28s %.1fs" % (rotulo, time.time() - inicio))
        except Exception as erro:
            falhas += 1
            texto = str(erro).replace("\n", " ")
            print("  FALHA %-28s %s" % (rotulo, texto[:120]))
            print("        -> %s" % diagnostico(str(erro)))

    print()
    if falhas:
        print("Enquanto nao resolver: o chat responde pelo agente mock, a memoria, o")
        print("RAG em cache, a agenda e o painel seguem normais. Da para desenvolver")
        print("e ate apresentar assim, com o selo 'IA em modo mock' avisando na tela.")
        return 1

    print("Tudo certo. As duas metades do sistema vao usar %s." % MODELO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
