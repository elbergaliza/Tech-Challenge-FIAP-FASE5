# Fonte do manual em PDF

O entregável é `docs/Manual-Agente-SDR-Imobiliario.pdf`, 44 páginas. Esta pasta
tem o que é preciso para editar e gerar de novo.

## Por que HTML e não Word

O manual é escrito em HTML e impresso pelo Chrome, que já está em qualquer
máquina do grupo. Isso dá três coisas que um editor de texto não dá de graça:
o documento usa as mesmas fontes do produto, as capturas de tela são geradas
por script contra o sistema rodando (então não envelhecem em silêncio), e o
arquivo é texto, então o Git mostra o que mudou entre duas versões.

Não é preciso instalar nada além do que o projeto já usa.

## Arquivos

| Arquivo | O que é |
| --- | --- |
| `manual.html` | Cabeça do documento: o CSS de impressão, a capa e o sumário |
| `corpo-a.html` | Seções 1 a 3: o sistema, o que instalar, a chave do Gemini |
| `corpo-b.html` | Seções 4 a 6: subir, conferir, a tela do chat |
| `corpo-c.html` | Seções 7 e 8: como conversar, as telas do corretor |
| `corpo-d.html` | Seções 9 a 12: privacidade, cenários, problemas, limites |
| `corpo-e.html` | Apêndices A, B e C |
| `img/` | As capturas, em 2x |
| `montar.py` | Junta tudo em `manual-completo.html` e confere |
| `capturar.mjs` | Tira as capturas de tela |
| `pdf.mjs` | Imprime o PDF |

O corpo é dividido em cinco arquivos porque um HTML de 80 KB num arquivo só é
desagradável de editar, e porque assim duas pessoas conseguem mexer em seções
diferentes sem conflito.

## Gerar de novo

Os dois scripts falam com o Chrome pelo protocolo DevTools, então ele precisa
estar aberto em modo de depuração. Numa janela separada:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --headless=new --disable-gpu --no-sandbox --no-first-run `
  --user-data-dir="$env:TEMP\chrome-manual" `
  --remote-debugging-port=9222 about:blank
```

Depois, na pasta `docs/manual`:

```powershell
..\..\.venv\Scripts\python.exe montar.py
node pdf.mjs "file:///D:/Projects/Pos/Fase 05/Tech-Challenge-FIAP-FASE5/docs/manual/manual-completo.html" "..\Manual-Agente-SDR-Imobiliario.pdf"
```

O `montar.py` confere três coisas antes de deixar passar: se alguma imagem
citada no texto não existe, quantas figuras e tabelas ficaram, e se sobrou
algum travessão (o projeto não usa).

## Tirar as capturas de novo

Exigem **o sistema no ar**, backend e frontend, além do Chrome acima.

```powershell
# tela inteira
node capturar.mjs "/painel" "img\09-dashboard.png" --largura=1440 --altura=940

# recorte de um elemento, por seletor CSS
node capturar.mjs "/" "img\04-selos-mock.png" --seletor=header --margem=0

# recorte pelo texto que o elemento contém
node capturar.mjs "/" "img\08-lgpd.png" --contendo="Meus dados (LGPD)"

# abrindo uma conversa existente, e mandando uma mensagem antes de capturar
node capturar.mjs "/" "img\05-chat-completo.png" `
  --lead=lead-c03741594f3f4181 --digitar="preciso urgente"
```

Opções: `--largura`, `--altura`, `--esperar` (ms antes do disparo), `--rolar`,
`--clicar="texto do botão"`, `--celular` (emula toque e ponteiro grosso).

Duas coisas que custaram tempo e ficam registradas aqui:

- **`--digitar` gasta cota do Gemini**, porque manda a mensagem de verdade. A
  espera depois do envio é de 28 segundos, que é o suficiente para a resposta
  chegar e as etiquetas de "anotei" aparecerem na captura.
- **A captura do selo âmbar de mock** exige subir o backend sem chave:
  `$env:GEMINI_API_KEY=""` antes do `run.py`, e restaurar depois.

## Ao trocar uma captura

As figuras são numeradas à mão no texto (`<b>Figura 7.</b>`). Se você
acrescentar uma no meio, renumere as seguintes: o `montar.py` conta as figuras
mas não confere a numeração.
