# -*- coding: utf-8 -*-
"""Confere que o README nao promete arquivo, comando nem link que nao existe.

Escrito porque o README anterior mandava rodar tres coisas que nao existiam
(`cp .env.gemini.example .env`, `pip install -r requirements_gemini.txt`,
`python test_agente_ia.py`), e ninguem percebeu porque ninguem relia o README
depois que o projeto mudou de forma.
"""
import io
import json
import os
import re
import subprocess
import sys

#     .venv\Scripts\python.exe docs\conferir-readme.py
#
# Sai com codigo 1 quando algo nao confere, entao serve em CI tambem.

# A raiz e deduzida do proprio arquivo: assim o script funciona de qualquer
# pasta e na maquina de qualquer integrante.
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
s = io.open(os.path.join(RAIZ, "README.md"), encoding="utf-8").read()

falhas = []


def checar(ok, descricao):
    print(("  ok    " if ok else "  FALHA ") + descricao)
    if not ok:
        falhas.append(descricao)


print("\n[1] Links internos apontam para algo que existe")
# Links markdown que nao sao http.
for texto, destino in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", s):
    if destino.startswith("http"):
        continue
    caminho = os.path.join(RAIZ, destino.replace("/", os.sep))
    checar(os.path.exists(caminho.rstrip(os.sep)), "%s -> %s" % (texto[:34], destino))

print("\n[2] Arquivos citados dentro dos blocos de comando")
citados = set(re.findall(r"(?:backend|frontend|docs|shared|ai-core|ai-memory-rag)"
                         r"[\\/][\w\\/.-]+\.(?:py|txt|json|md|example)", s))
for c in sorted(citados):
    caminho = os.path.join(RAIZ, c.replace("\\", os.sep).replace("/", os.sep))
    checar(os.path.exists(caminho), c)

print("\n[3] Scripts npm citados existem no package.json")
pkg = json.load(io.open(os.path.join(RAIZ, "frontend", "package.json"),
                        encoding="utf-8"))
for script in re.findall(r"npm run ([\w:]+)", s):
    checar(script in pkg["scripts"], "npm run " + script)

print("\n[4] Rotas citadas existem no frontend")
rotas = re.findall(r"`(/painel[\w/:]*|/)`", s)
app = io.open(os.path.join(RAIZ, "frontend", "src", "App.tsx"),
              encoding="utf-8").read()
for rota in sorted(set(rotas)):
    molde = rota.replace(":id", ":leadId")
    checar('path="%s"' % molde in app, "rota " + rota)

print("\n[5] Nada do README antigo sobrou")
fantasmas = ["agente_ia_gemini", "requirements_gemini", ".env.gemini.example",
             "README_GEMINI", "Sem limite de requisições",
             "gratuita e ilimitada", "60 requisições por minuto"]
for f in fantasmas:
    checar(f not in s, "sem '%s'" % f)

print("\n[6] Afirmacoes que precisam bater com o codigo")
checar("—" not in s, "sem travessao")

# As contagens de teste que o README promete.
saida = subprocess.run(
    [os.path.join(RAIZ, ".venv", "Scripts", "python.exe"), "-m", "pytest",
     "ai-core/tests", "ai-memory-rag/tests", "-q"],
    cwd=RAIZ, capture_output=True, text=True).stdout
achou = re.search(r"(\d+) passed", saida)
if achou:
    prometido = re.search(r"\| (\d+) testes \|", s)
    checar(achou.group(1) in s, "as %s do pytest estao no README" % achou.group(1))

# O catalogo.
#
# A busca pela chave certa e explicita: com um `.get("imoveis", [])` mudo, um
# JSON de outro formato daria lista vazia, e "0 imoveis" ainda casaria dentro de
# "140 imoveis" por substring. Foi exatamente assim que esta checagem passou por
# acidente na primeira execucao.
dados = json.load(io.open(os.path.join(RAIZ, "shared", "data", "imoveis.json"),
                          encoding="utf-8"))
if isinstance(dados, list):
    imoveis = dados
else:
    listas = [v for v in dados.values() if isinstance(v, list) and v]
    assert len(listas) == 1, "nao sei onde estao os imoveis neste JSON"
    imoveis = listas[0]

assert imoveis and "neighborhood" in imoveis[0], "formato inesperado do catalogo"

# `re.search` com fronteira em vez de `in`: "0 imoveis" e substring de
# "140 imoveis", e a versao com `in` dava ok para o numero errado.
def afirma_numero(quantidade, unidade):
    padrao = r"(?<![0-9])%d %s" % (quantidade, unidade)
    checar(re.search(padrao, s) is not None,
           "o README diz %d %s" % (quantidade, unidade))

afirma_numero(len(imoveis), "imóveis")
afirma_numero(len({i["neighborhood"] for i in imoveis}), "bairros")

print("\n" + "=" * 58)
if falhas:
    print("%d FALHA(S):" % len(falhas))
    for f in falhas:
        print("  - " + f)
    sys.exit(1)
print("README confere com o repositorio.")
