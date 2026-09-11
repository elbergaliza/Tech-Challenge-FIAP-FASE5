# Frontend (Parte 4)

Chat do lead e painel do corretor, consumindo a API da Parte 3.

React 19 + TypeScript + Vite + Tailwind 4. Sem lib de estado nem de gráfico: as
telas são poucas e a API já devolve tudo pronto (rótulos em português, listas
ordenadas, contagens), então um `useState` e barras em CSS resolvem.

## Como rodar

O backend precisa estar no ar primeiro:

```bash
# na raiz do repositório
python backend/run.py          # http://localhost:8000
```

Depois:

```bash
cd frontend
npm install
npm run dev                    # http://localhost:5173
```

A porta 5173 é fixa (`strictPort` no `vite.config.ts`) porque é ela que está em
`CORS_ORIGINS` no backend. Se o Vite escolhesse 5174 sozinho, toda chamada
morreria no CORS com um erro que não parece ser de porta.

Backend em outro endereço: crie `frontend/.env.local` com
`VITE_API_URL=http://outro-host:8000`.

**Não precisa de chave de API para desenvolver.** Sem `GEMINI_API_KEY` o backend
responde por um agente mock e todas as rotas funcionam igual; o cabeçalho mostra
um aviso "IA em modo mock" para ninguém se assustar com as respostas robóticas.

**A cota gratuita é de 20 requisições por dia e por modelo**, e cada mensagem
no chat é uma. As suítes automáticas gastam: `test:e2e` umas duas, `test:contrato`
uma, e o smoke do backend algumas. Rodar tudo várias vezes num dia acaba com a
cota do dia. `GEMINI_MODEL` no `.env` da raiz troca o modelo, e como a cota é
por modelo, isso serve para separar o que se gasta testando do que se guarda
para apresentar.

O mock também é a rede de segurança **com** chave: o 503 por demanda alta do
Gemini é rotina em horário de pico (cerca de 1 em 3 chamadas, medido com a
chave do projeto). O backend retenta, e se o Gemini continuar fora, o mock
assume o turno e pergunta o próximo campo que falta, em vez de o lead receber
um pedido de desculpa e a qualificação parar. A bolha vem marcada com `mock`,
então a tela nunca deixa de dizer quem respondeu. Memória e RAG não dependem
do agente e seguem normais nesse cenário.

Scripts: `npm run dev`, `npm run dev:3000` (quando a 5173 está ocupada; a 3000 também está no CORS), `npm run build`, `npm run preview`, `npm run lint`
(`tsc --noEmit`).

## As telas

| Rota                  | O que é                                                      |
| --------------------- | ------------------------------------------------------------ |
| `/`                   | Chat do lead: conversa, perfil sendo montado, LGPD           |
| `/painel`             | Dashboard do corretor: KPIs, lista de leads, follow-ups      |
| `/painel/leads/:id`   | Detalhe do lead: resumo da IA, histórico, agendamentos        |
| `/painel/agenda`      | Agenda por dia, com remarcar / realizado / cancelar          |
| `/painel/imoveis`     | Catálogo com filtros vindos de `GET /imoveis/filtros`        |

### Chat

Uma rota sustenta a tela: `POST /chat`. O `lead_id` fica no `localStorage`, então
recarregar a página continua a mesma conversa em vez de criar um lead duplicado
no funil. Se o backend responder 404 no histórico (lead apagado por LGPD, ou
banco recriado), o id guardado é descartado sozinho.

Três campos do payload viraram UI de propósito:

- `novidades` → chips "anotei: região = Botafogo" ao lado da bolha. É a prova
  visual, na tela, de que o agente tem memória. `kind: "correction"` aparece em
  âmbar com o valor anterior no tooltip.
- `imoveis` → cards abaixo da resposta, com o `reason` visível. É o que faz o
  RAG parecer inteligente em vez de aleatório.
- `sugerir_agendamento` → abre o convite para marcar visita. Para
  `intent: INVEST` o tipo enviado é `CONSULTORIA`, não `VISITA`.

### Consentimento

A caixa da primeira mensagem pede **só a retomada de contato**, não o
atendimento: responder quem chegou perguntando sobre imóvel é atendimento
pedido pelo próprio titular e não depende de aceite. Empacotar as duas coisas
num consentimento só é o tipo de aceite genérico que não vale, por não ser para
finalidade determinada.

Recusar não bloqueia nada: a conversa acontece igual, o RAG sugere, o lead entra
no funil. O que não acontece é a retomada automática.

O card **Meus dados (LGPD)** do chat tem três controles, para três direitos
diferentes: baixar (acesso), excluir (exclusão) e ligar ou desligar o contato
(revogação do consentimento). Revogar não pede confirmação e não apaga a
conversa, porque sair tem que ser tão fácil quanto entrar.

O consentimento mora em dois lugares no backend: a coluna do lead e o `consent`
da memória da Parte 2, que é quem o follow-up consulta. As rotas `POST /chat`,
`PATCH /leads/{id}` e `POST /leads` espelham nos dois; o `backend/tests/
test_smoke.py` cobre isso, porque a divergência entre eles é silenciosa e só
aparece dias depois, quando alguém pergunta por que ninguém foi retomado.

### Painel

Ordenado por score desc por padrão: a pergunta do corretor ao abrir a tela é
"quem eu ligo agora". O card de resumo da IA destaca `next_action`, `alerts` e
`buying_signals`, que são o que muda a próxima ligação.

## Testes

```bash
npm test              # 110 testes de unidade e de componente (Vitest + jsdom)
npm run test:contrato # 74 verificações contra a API no ar
npm run test:e2e      # a jornada do roteiro manual, telas reais + backend real
npm run verificar     # tsc -b, testes e build, na ordem
```

Três camadas, porque elas pegam defeitos diferentes:

**Unidade e componente** (`src/**/*.test.ts(x)`) rodam com dublê da API, então
são rápidos e determinísticos. Cobrem o que quebraria calado: o `lead_id` que
precisa ser guardado, o consentimento que só vai na primeira mensagem, o 404
que invalida o id guardado, `INVEST` virando `CONSULTORIA`, a faixa de
temperatura na linha do lead, o debounce da busca, as duas rotas de LLM que só
podem sair no clique, os três estados do tema, o selo de saúde que reconsulta a API a cada 20s e o
`detail` do 422 que é lista.

**Os testes montam dentro de `StrictMode`, como o `main.tsx` monta.** Não é
detalhe: em desenvolvimento o React executa cada efeito duas vezes (executa,
limpa, executa), e testar fora disso é testar outro app. Foi assim que passou
despercebido um guarda que impedia o histórico da conversa de carregar depois
do F5, com a suíte inteira verde.

Os testes de data não dependem do fuso da máquina: em vez de comparar com uma
string formatada em -03:00, comparam instantes e derivam a expectativa por um
caminho independente. Um teste que só passa no notebook de quem escreveu não
vale como teste.

**Contrato** (`scripts/contrato.mjs`) bate na API de verdade e confere que cada
payload tem o que `src/types.ts` assume. É a camada que os dublês não cobrem: se
o backend renomear um campo, os testes de componente continuam verdes e este
falha. Ele escreve no banco (cria lead pelo chat, agenda, remarca, cancela) e
apaga tudo no fim, então aponte para banco de desenvolvimento. Com a API fora do
ar ele sai com código 1 dizendo que nada foi verificado, em vez de passar vazio.

Além dos payloads, ele confere afirmações do `docs/frontend-integracao.md` que a
tela leva a sério: lista ordenada por score desc, `PATCH` parcial que não apaga
o telefone, agendar promovendo o lead para `AGENDADO`, cancelar a única visita
devolvendo para `QUALIFICADO`, `por_*` ordenado por total e o follow-up que não
traz `texto_sugerido` sem pedir.

**Teste manual, ponta a ponta:**
[`docs/roteiro-de-teste-manual.md`](../docs/roteiro-de-teste-manual.md) exercita
cada funcionalidade pela tela, com caixas para marcar, e termina num roteiro
curto de 5 minutos para a apresentação. É a camada que pega o que nenhum teste
automático vê: o visual, o tema no projetor e o fluxo real de uso.

**Ponta a ponta** (`src/e2e/roteiro.e2e.test.tsx`) executa a jornada do
[`docs/roteiro-de-teste-manual.md`](../docs/roteiro-de-teste-manual.md) sem
dublê nenhum: as telas de verdade, o cliente HTTP de verdade e o backend no ar.
Cobre os blocos 2 a 16, do primeiro "oi" até cancelar a visita, e falha alto se
o backend estiver fora, em vez de passar vazio. Fica fora do `npm test` porque
cria um lead no banco e gasta uma chamada do Gemini por mensagem.

O que ela **não** cobre, e continua dependendo de olho humano no navegador: como
a tela fica. O jsdom não faz layout nem aplica CSS, então tema, contraste,
responsivo, anel de foco e o flash de tema no recarregamento ficam de fora. Os
blocos 1 e 17 do roteiro são inteiramente visuais e não têm equivalente
automático.

**Fora do front:** `python backend/tests/test_smoke.py` (Parte 3, roda sem
pytest e usa banco temporário), `pytest ai-memory-rag/tests` (Parte 2) e
`pytest ai-core/tests` (Parte 1, a extração por regex).

## Direção visual: Calçadão

Escolhida pelo grupo entre três propostas. Verde-mar sobre azul profundo no
escuro, areia no claro, canto de 15px, sombra macia e o mosaico do calçadão
como único ornamento, só no cabeçalho. Tipografia: Bricolage Grotesque no
display, Karla no corpo.

Tudo mora em `src/index.css`, em duas camadas:

1. Os tokens `--sdr-*`, declarados três vezes (claro, `prefers-color-scheme:
   dark` e `[data-theme="dark"]`).
2. O bloco `@theme inline`, que expõe cada token como utilitário do Tailwind
   (`bg-superficie`, `text-suave`, `border-linha`, `rounded-cartao`,
   `shadow-cartao`).

**Escala.** Um segundo bloco `@theme` sobe a escala de tipo e o `--spacing`
base. O app tinha nascido lido de perto, com quase todo texto em `text-sm`
(14px) e `text-xs` (12px), o que fica miúdo em monitor grande. Subir os tokens
faz cada classe já usada crescer junto, sem trocar `text-sm` por `text-base` em
cinquenta lugares, e as proporções de `line-height` do Tailwind acompanham
porque são calculadas em cima desses mesmos tokens. Nada abaixo de 13px.

O container do app vai a 88rem, mas o que é texto corrido tem medida própria: a
bolha do chat e o resumo da IA param em 62ch e 70ch. Largura de painel não é
largura de leitura.

O `inline` do `@theme` não é enfeite: sem ele o Tailwind congela o valor do
token no build e o app fica com um tema só, imune à troca. E nenhum componente
escreve cor: trocar a direção visual inteira é mexer só nesses dois blocos.

**Tema.** Nasce escuro. O `index.html` aplica a escolha guardada antes da
primeira pintura, senão o app abre claro e pisca para o escuro quando o React
monta, que é o defeito que mais aparece em demo com projetor. O controle no
cabeçalho tem três estados: Escuro, Claro e Auto, sendo Auto a ausência do
atributo, que devolve a decisão para o `prefers-color-scheme`.

**Temperatura é uma escala só.** `--sdr-quente`, `--sdr-morno` e `--sdr-frio`
pintam o KPI, o badge, a faixa da lista e a agenda. Na lista de leads a
temperatura virou faixa de 3px na borda esquerda (classe `.faixa` com
`data-t`), o que liberou uma coluna e deixou o corretor varrer trinta linhas de
44px pela cor, sem ler badge por badge.

**Cor semântica separada do acento.** Verde-mar é identidade; `--sdr-sucesso`,
`--sdr-morno` e `--sdr-erro` são estado. Âmbar quer dizer "precisa de atenção"
tanto no aviso quanto no lead morno, e por isso os dois compartilham a família.

## Decisões que valem saber

**Rótulo nenhum é traduzido aqui, sem exceção.** O backend manda o par
(`temperatura` + `temperatura_label`, `perfil` + `perfil_label` +
`perfil_campos`, `field` + `field_label`); o front só escolhe a cor e desenha.

Havia uma exceção, a tabela `CAMPOS` em `lib/formatar.ts`, cópia da
`FIELD_LABELS` da Parte 2. As duas divergiram exatamente como a regra prevê:
aqui dizia "orçamento", lá "Faixa de preço". A tabela foi removida e o backend
passou a mandar os nomes de campo junto.

**As duas chamadas que gastam cota de LLM ficam atrás de clique**, nunca em
`useEffect` de lista: "Melhorar com IA" no detalhe do lead (`?resumo_ia=true`) e
"ver texto sugerido" no follow-up (`?com_texto=true`).

**HTTP só em `services/api.ts`.** Nenhuma tela monta URL nem chama `fetch`: se um
query param mudar de nome, o conserto é num arquivo. O `detail` do 422 do
FastAPI é uma lista de objetos, e virar frase legível também acontece lá.

**Datas.** A API fala ISO 8601 UTC; a conversão para o fuso local e para o
formato pt-BR mora em `lib/formatar.ts`. Os `datetime-local` dos formulários
voltam para ISO via `paraIso` antes de subir.
