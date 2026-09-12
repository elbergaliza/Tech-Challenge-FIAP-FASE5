# -*- coding: utf-8 -*-
"""Junta a capa com os fragmentos do corpo e gera o manual completo."""
import io
import os
import re

AQUI = os.path.dirname(os.path.abspath(__file__))

base = io.open(os.path.join(AQUI, "manual.html"), encoding="utf-8").read()

corpo = []
for nome in ("corpo-a", "corpo-b", "corpo-c", "corpo-d", "corpo-e"):
    caminho = os.path.join(AQUI, nome + ".html")
    corpo.append(io.open(caminho, encoding="utf-8").read())

final = base.replace("</body>\n</html>", "\n".join(corpo) + "\n</body>\n</html>")

saida = os.path.join(AQUI, "manual-completo.html")
io.open(saida, "w", encoding="utf-8").write(final)

# Conferencias que valem antes de imprimir.
figuras = re.findall(r'src="(img/[^"]+)"', final)
faltando = [f for f in figuras if not os.path.exists(os.path.join(AQUI, f))]

print("montado: %s" % os.path.basename(saida))
print("  tamanho:   %d KB" % (len(final.encode("utf-8")) / 1024))
print("  secoes:    %d" % final.count("<h1>"))
print("  figuras:   %d" % len(figuras))
print("  tabelas:   %d" % final.count("<table>"))
print("  travessao: %d  (tem que ser 0)" % final.count("—"))

if faltando:
    print("\n  IMAGENS AUSENTES:")
    for f in faltando:
        print("    " + f)
else:
    print("  imagens:   todas presentes")
