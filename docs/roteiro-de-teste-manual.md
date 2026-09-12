# Roteiro de teste manual, ponta a ponta

Cada funcionalidade do sistema exercitada pela tela, na ordem em que um lead e
um corretor a usariam. Serve para conferir uma entrega, para ensaiar a
apresentação e para achar o que os testes automáticos não pegam: o que só se vê
com o olho.

Roteiro completo: cerca de 30 minutos. Se o tempo for curto, vá direto para o
[roteiro curto de 5 minutos](#19-roteiro-curto-de-5-minutos-para-a-apresentação),
no fim.

**Como usar:** marque a caixa de cada passo que passar. Onde há **Esperado**, é
isso que a tela tem que fazer; se não fizer, anote e siga, porque a maioria dos
blocos é independente.

**Convenções deste documento**

- Todo comando sai da **raiz do repositório** e está escrito para o PowerShell
  do Windows, que é onde o time roda. Em outro terminal, troque só o caminho do
  Python:

  | Terminal | Python do projeto |
  | --- | --- |
  | PowerShell / cmd | `.venv\Scripts\python.exe` |
  | Git Bash | `./.venv/Scripts/python.exe` |
  | Linux ou Mac | `.venv/bin/python` |

  Quem preferir digitar só `python` pode ativar o ambiente uma vez por terminal
  com `.venv\Scripts\Activate.ps1`, mas o caminho direto acima funciona sempre,
  inclusive quando a política de execução do PowerShell bloqueia a ativação.
- Onde aparece "DevTools", é `F12` no navegador.

---

## 0. Preparação

### 0.1 Subir os dois lados

```powershell
# terminal 1, na raiz
.venv\Scripts\python.exe backend/run.py            # http://localhost:8000

# terminal 2
cd frontend
npm install                  # só na primeira vez
npm run dev                  # http://localhost:5173
```

- [ ] `http://localhost:8000/health` responde JSON com `"status": "ok"`
- [ ] `http://localhost:8000/docs` abre a documentação interativa da API
- [ ] `http://localhost:5173` abre o app no chat

Se a 5173 estiver ocupada, use `npm run dev:3000`. Essas são as duas portas
liberadas no CORS do backend; qualquer outra precisa entrar em `CORS_ORIGINS`
no `.env` da raiz, senão toda chamada morre com um erro que não parece ser de
porta.

> **No dia da apresentação, suba com `--no-reload`:**
>
> ```powershell
> .venv\Scripts\python.exe backend/run.py --no-reload
> ```
>
> O autoreload é ótimo enquanto se escreve código e atrapalha na hora de
> mostrar: qualquer arquivo salvo sem querer reinicia o backend no meio de uma
> frase do agente, e cada reinício paga uma sonda de embedding no Gemini antes
> de responder a primeira mensagem.

### 0.2 Conferir o cabeçalho

- [ ] Aparece o selo verde **API no ar** no canto direito
- [ ] Se o backend subiu **sem** `GEMINI_API_KEY`, aparece também o selo âmbar
      **IA em modo mock**

O selo de mock não é decoração: ele avisa que as respostas vêm de um agente
simulado, para ninguém achar que a IA ficou robótica.

### 0.3 Dois cenários opcionais de partida

**Base limpa** (para ver os estados vazios, que é o que a banca vê no primeiro
boot): pare o backend, apague `backend/data/app.db` e suba de novo. O catálogo
de imóveis é semeado sozinho no boot; leads, conversas e agendamentos nascem
vazios.

**Modo mock** (para testar sem gastar cota): comente a linha `GEMINI_API_KEY`
no `.env` da raiz e reinicie o backend. Todas as rotas continuam funcionando.

### 0.4 A cota do Gemini, antes de sair testando

A camada gratuita dá **20 requisições por dia e POR MODELO**. Cada mensagem no
chat é uma requisição; "Melhorar com IA" e "ver texto sugerido" são mais uma
cada. Dá para acabar a cota do dia numa sessão de testes e chegar na
apresentação sem nada.

Quando ela acaba, o agente devolve `429 RESOURCE_EXHAUSTED` e o mock assume o
turno (bloco 8.1). O resto do sistema continua: memória, RAG, agenda e painel
não passam pelo Gemini.

Duas coisas que economizam cota:

- **Teste em modo mock.** Comente `GEMINI_API_KEY` no `.env` e reinicie. Todos
  os blocos deste roteiro funcionam, menos a qualidade da conversa.
- **Guarde um modelo para a apresentação.** A cota é por modelo, então
  `GEMINI_MODEL` no `.env` da raiz é o interruptor: teste com um id e apresente
  com outro. Os dois verificados neste projeto são `gemini-2.5-flash` (o padrão)
  e `gemini-3.6-flash`.

Para saber em que pé está a chave, sem precisar interpretar log:

```powershell
.venv\Scripts\python.exe docs/verificar-chave.py
```

Ele testa geração e embedding e traduz o erro na ação: cota do dia acabada,
crédito pré-pago zerado, projeto bloqueado ou tipo de chave errado significam
coisas diferentes, e a mensagem crua da API não deixa isso claro.

### 0.5 Onde olhar o `lead_id`

DevTools → **Application** → **Local Storage** → `http://localhost:5173` →
chave `lead_id`. Vários passos abaixo pedem para conferir isso.

---

## 1. Tema: os três estados

- [ ] Em aba nova, o app abre **escuro** (é o padrão do produto)
- [ ] Clicar em **Claro** no cabeçalho troca a tela na hora
- [ ] Recarregar com `F5` mantém o claro, **sem piscar escuro antes**
- [ ] Clicar em **Auto** faz a tela seguir o tema do sistema operacional
- [ ] Com Auto ligado, trocar o tema do sistema troca o do app (no Windows:
      Configurações → Personalização → Cores)
- [ ] No claro, abrir um campo de data (bloco 6) mostra o calendário claro; no
      escuro, escuro

**Esperado:** nenhum flash de tema errado ao recarregar. O tema é aplicado antes
da primeira pintura; um flash aqui é defeito, e é o que mais aparece em
projetor.

---

## 2. Chat: o roteiro do locatário, mensagem por mensagem

As mensagens abaixo são para **copiar e enviar na ordem**, e não sugestões. Elas
foram executadas contra o agente real, e é o resultado delas que está descrito
aqui. Improvisar é bem-vindo depois, mas a primeira passada tem que ser esta:
assim, quando algo falhar, você sabe que falhou de verdade e não que escreveu de
um jeito que o sistema não entende.

Comece sem lead: se houver `lead_id` no Local Storage, clique em **Nova
conversa**.

- [ ] O aviso de LGPD com a caixa "Quero que a imobiliária me procure depois
      sobre imóveis" aparece acima do campo de mensagem
- [ ] O texto deixa claro que é opcional e que a conversa funciona sem isso
- [ ] Marque a caixa

| # | Envie exatamente isto | O que tem que acontecer |
| --- | --- | --- |
| 1 | `quero alugar um apartamento de 2 quartos em Botafogo` | Três chips verdes: **Intenção = aluguel**, **Região = Botafogo**, **Quartos = 2** |
| 2 | `na verdade preciso de 3 quartos` | Um chip **âmbar**: **corrigi: Quartos = 3**, com `Antes: 2` no tooltip |
| 3 | `até 4 mil por mês` | Chip **Faixa de preço = R$ 4.000** e cards de imóvel abaixo da resposta |
| 4 | `preciso mudar esse mês` | Chip **Urgência = alta** |
| 5 | `sou a Bruna, 21 99999-4410` | Chips de **Telefone** e **Nome**, e aparece o convite para agendar |

- [ ] Enquanto espera cada resposta, aparecem os três pontinhos e a palavra
      **digitando**
- [ ] A primeira resposta chega em até 6 segundos (a primeira depois de subir o
      backend pode demorar mais, é quando o índice do RAG é construído)
- [ ] O título do card "Conversa" mostra o id do lead, tipo `lead-0001`
- [ ] O Local Storage passou a ter esse mesmo `lead_id`
- [ ] O campo de consentimento **desapareceu** a partir da segunda mensagem

**Por que essas frases:** cada uma exercita um caminho diferente. A 2 é
correção de um dado já conhecido, a 3 é um valor que a extração só entende
escrito assim ("4 mil", e não "4000"), a 4 é uma expressão de pressa que a
lista original da Parte 1 não cobria, e a 5 traz nome e telefone na mesma
mensagem.

---

## 3. Chat: o que a memória mostrou pelo caminho

Conferindo o que os chips e o painel fizeram durante o roteiro acima:

- [ ] Os chips apareceram **ao lado da mensagem**, e não num canto qualquer
- [ ] O chip está em português dos dois lados: **Intenção = aluguel**, e não
      `intent = RENT`
- [ ] Verde para o que é novo, **âmbar para correção**
- [ ] No painel da direita, **O que já entendi** listou os mesmos campos
- [ ] Os valores estão legíveis: **aluguel** e não `RENT`, **alta** e não
      `high`, **R$ 4.000** e não `4k`
- [ ] A barra de score encheu e o selo de temperatura mostra **MORNO** ou
      **QUENTE** com o número
- [ ] Ao final, o painel mostra intenção, região, quartos, orçamento, urgência,
      nome e telefone
- [ ] Com a conversa já longa, **a área de mensagens rola** e o campo de texto
      com o botão Enviar continuam visíveis, presos no rodapé do cartão
- [ ] A conversa acompanha sozinha: a mensagem nova entra e a rolagem desce
      até ela

**Esperado:** o chip é a prova, na tela, de que o agente tem memória e não está
recomeçando a cada mensagem.

---

## 4. Chat: RAG na base de imóveis

Os cards apareceram na mensagem 3 do roteiro, quando o orçamento entrou.

- [ ] Abaixo da resposta aparecem cards de imóvel
- [ ] Cada card mostra título, preço com `/mês`, bairro, quartos, área e o id
      (`IMV-0086`)
- [ ] Cada card termina com **Por que este:** e uma frase em português
      explicando o encaixe
- [ ] Os imóveis são de **Botafogo** ou vizinhos, e não de qualquer lugar

**Esperado:** o texto do "Por que este" cita o que você pediu (bairro, número de
quartos, orçamento). É isso que separa uma sugestão do RAG de uma lista
aleatória. Se vier vazio, o card perde a razão de existir.

---

## 5. Chat: reabrir a conversa

- [ ] Aperte `F5`
- [ ] O histórico volta na ordem, começando por
      `quero alugar um apartamento de 2 quartos em Botafogo`
- [ ] O id no título continua o mesmo
- [ ] No Dashboard (bloco 9), **não** apareceu um segundo lead

**Esperado e correto:** os chips "anotei" e os cards de imóvel **não** voltam
depois do `F5`. O endpoint de histórico devolve só as mensagens; novidades e
imóveis são do turno, não da mensagem. A conversa volta, a marcação daquele
instante não.

- [ ] Agora simule um id inválido: no DevTools, troque o valor de `lead_id` por
      `lead-9999` e recarregue
- [ ] A tela volta ao estado de conversa nova, com o aviso de LGPD de novo, e o
      `lead_id` inválido saiu do Local Storage

**Por que isso importa:** é o que acontece de verdade quando o lead usa o botão
de excluir dados, ou quando alguém apaga o `app.db`. Insistir num id morto
quebraria todas as mensagens seguintes.

---

## 6. Chat: agendamento

O convite só aparece quando o backend considera o perfil completo (campo
`sugerir_agendamento`), o que acontece na **mensagem 5** do roteiro do bloco 2.

- [ ] O agente **não convida para agendar antes da hora**: enquanto faltar
      algum dado, ele continua coletando e não fala em escolher dia
- [ ] Quando o perfil fecha, o seletor de data **abre sozinho**, com o título
      **Vamos marcar sua visita**, no mesmo turno em que ele convida
- [ ] O agente **não** pergunta dia e hora por escrito. Se você escrever
      `amanhã às 10h` mesmo assim, ele confirma que entendeu e pede para você
      marcar no seletor, sem repetir a pergunta
- [ ] Clique em **Depois**: o formulário some e sobra o convite com o botão
      **Agendar visita**
- [ ] Mande mais uma mensagem: o formulário **não** volta sozinho, porque você
      já disse que era depois
- [ ] Clique em **Agendar visita** para reabrir
- [ ] Escolha uma data futura e clique em **Confirmar**
- [ ] A mensagem **Visita marcada. Um corretor confirma com você.** aparece
- [ ] Na aba **Agenda**, o agendamento está lá, no dia certo e na hora certa

---

## 6.1 Chat: o roteiro do investidor

Outra conversa, outro fluxo. Clique em **Nova conversa** e envie na ordem. Este
roteiro também foi executado contra o agente real.

| # | Envie exatamente isto | O que tem que acontecer |
| --- | --- | --- |
| 1 | `quero investir em imóveis para alugar` | Chip **Intenção = investimento**, apesar da palavra "alugar" na frase |
| 2 | `tenho 800 mil` | Chip **Ticket de investimento**, e a próxima pergunta é o retorno |
| 3 | `quero uns 5 mil por mês` | Chip **Retorno esperado**. Seis palavras: antes o limite era cinco, a frase caía fora, o retorno ficava pendente para sempre e o convite nunca era liberado |
| 4 | `pronto para alugar` | **Nenhum chip**: a intenção continua investimento |
| 5 | `Leblon` | Chip **Região = Leblon** |
| 6 | `urgente` | Chip **Urgência = alta** |
| 7 | `Marcos, 33410549` | **Nenhum chip de telefone**, e o agente pede o número com DDD |
| 8 | `21 99999-4410` | Chip de **Telefone**, e aparece o convite para agendar |

- [ ] Em **nenhum momento** ele pergunta quantos quartos: quem investe decide
      por ticket e retorno
- [ ] Os imóveis sugeridos são **à venda**, não de aluguel: o preço aparece sem
      o `/mês`
- [ ] Nenhum imóvel sugerido custa **mais que o ticket** informado. Com 800 mil,
      nada de casa de 6 milhões
- [ ] Os cards mostram **retorno estimado ao ano**, que é o número pelo qual o
      investidor decide
- [ ] O seletor abre sozinho com o título **Vamos marcar sua conversa com o
      especialista**, e não "Vamos marcar sua visita"
- [ ] A mensagem 4 é a armadilha: a conversa inteira fala em "alugar", e a
      intenção não pode voltar para aluguel por causa disso
- [ ] Na mensagem 7 o agente pede o DDD e **não** diz que vai te ligar
- [ ] O convite fala em **consultoria** ou **especialista**, não em visita
- [ ] O campo diz **Melhor dia e hora para a consultoria**
- [ ] Na tela do corretor, o card **Perfil qualificado** mostra **Ticket** e
      **Retorno esperado** no lugar de Quartos
- [ ] No painel do corretor, este lead aparece como **QUENTE**, e não MORNO. O
      score contava `Faixa de preço` e `Quartos`, que são exatamente os dois
      campos que o funil de investimento não pergunta: o investidor perfeito
      ficava com 60 de 100 e nunca cruzava o limiar de 70
- [ ] E o resumo do card cita o **ticket**, em vez de dizer "Ainda não
      informou: quartos" para quem vai investir 800 mil

**Por que essas frases:** as mensagens 4 e 7 são as que já quebraram em
conversa real. A 4 rebaixava o investidor a locatário e jogava fora o ticket; a
7 era aceita em silêncio, e o agente chegava a prometer uma ligação para um
número que o sistema nunca guardou.

---

## 6.2 Chat: respostas que o sistema não entende

A extração é por regex e tem limites conhecidos. O ponto aqui não é que ela
entenda tudo, é que ela **peça de novo** em vez de seguir fingindo.

Em uma conversa nova, com a pergunta de quartos em aberto:

| Envie | O que tem que acontecer |
| --- | --- |
| `550` | O agente não grava 550 quartos e pergunta de novo |
| `R$ 8.550` | Vira **orçamento**, não quartos: a forma do valor manda mais que a pergunta |
| `xpto` | O agente pede de outro jeito, com um exemplo do formato |

- [ ] Em nenhum dos três casos a mesma frase é repetida palavra por palavra
- [ ] Em nenhum deles um valor absurdo aparece no painel

### 6.2.1 O que a extração passou a entender

Cada linha abaixo foi um defeito que alguém viu numa conversa de verdade.

| Envie | O que tem que aparecer no painel |
| --- | --- |
| `posso ir até 1 milhão` | Faixa de preço **1m**, e não `1k`. Se aparecer `1k`, os imóveis vêm com "13800% acima do orçamento" |
| `quero na Tijuca` | Região **Tijuca**. Antes a Tijuca não era reconhecida, o agente repetia a pergunta e o seletor de data nunca abria |
| `quero no Meier` (sem acento) | Região **Méier**, com acento: é a chave da base de imóveis |
| `sou casado e tenho dois filhos` | Nome **continua vazio**. Antes gravava "Casado" e o agente chamava o lead assim até o fim |
| `já tenho imóvel, sem pressa` | Urgência **baixa**. Antes o "já" dava urgência alta, e o lead que pediu calma virava o mais perseguido da base |
| `não quero comprar, quero alugar` | Intenção **aluguel** |
| `quero uma casa` | Tipo **Casa**, e a lista para de trazer sala comercial |

- [ ] Nenhuma das sete linhas grava o oposto do que foi dito

**Esperado:** o painel nunca mostra um dado que o lead não disse. Preferir o
campo vazio a um número inventado é o que mantém o painel do corretor
confiável.

---

## 7. Chat: LGPD pela tela do lead

No painel da direita, card **Meus dados (LGPD)**:

- [ ] O card mostra o estado do **Contato fora do chat**: autorizado ou só pelo
      chat
- [ ] Se você recusou no começo, o botão diz **Pode me procurar**; clicar muda
      o estado para autorizado, sem recarregar
- [ ] Autorizado, o botão vira **Não quero mais ser procurado**; clicar revoga
- [ ] **Revogar não pede confirmação**, e não apaga a conversa
- [ ] **Baixar meus dados** salva um arquivo `.json` com o que o sistema guarda
- [ ] Abrindo o arquivo, dá para ver as mensagens e o perfil extraído
- [ ] **Excluir meus dados** pede confirmação antes de qualquer coisa
- [ ] Confirmando, a conversa volta ao início e o `lead_id` sai do Local Storage
- [ ] No Dashboard, o lead não existe mais na lista

**Esperado:** o excluir apaga o lead **e** a memória da IA sobre ele.

Três direitos diferentes, três controles diferentes, e isso é de propósito:
acesso (baixar), exclusão (apagar) e revogação do consentimento (o botão de
contato). Revogar não é excluir: quem só quer parar de ser procurado não deveria
precisar destruir a própria conversa para conseguir, e a lei pede que sair seja
tão fácil quanto entrar.

**Vale conferir que a revogação chega até o fim**, porque o consentimento mora
em dois lugares (a coluna do lead e a memória da Parte 2, que é quem o
follow-up consulta):

- [ ] Autorize o contato, envelheça o lead (bloco 11) e confirme que ele aparece
      em **Precisam de atenção**
- [ ] Revogue pelo card e recarregue o painel: ele **sai** da lista

---

## 8. Chat: quando a API cai

- [ ] Com uma conversa aberta, pare o backend no terminal 1 (`Ctrl+C`)
- [ ] Em até 20 segundos, o cabeçalho troca para o selo vermelho **API offline**
      (o cabeçalho reconsulta a saúde nesse intervalo, sem precisar recarregar)
- [ ] Escreva uma mensagem e envie
- [ ] Aparece um alerta vermelho dizendo que não foi possível falar com a API,
      com o endereço que ele tentou
- [ ] **O texto que você escreveu volta para o campo**, sem perder nada
- [ ] A bolha da sua mensagem não fica sozinha na tela como se tivesse sido
      enviada
- [ ] Suba o backend de novo: em até 20 segundos o selo volta a **API no ar**,
      sem recarregar a página

**Esperado:** erro de rede e erro da API dizem coisas diferentes. Perder o que a
pessoa escreveu porque a rede piscou é pior que o erro em si.

---

## 8.0 Chat: quando a conexão trava em vez de cair

Cair e travar são coisas diferentes. Uma conexão que **trava** nunca devolve
erro nenhum, e era o único defeito da lista que deixava a tela sem saída: os
três pontinhos rodavam para sempre, o botão Enviar ficava desabilitado, e nem
"Nova conversa" destravava. Só o F5.

Para simular no Windows, sem matar o processo: abra o Gerenciador de Tarefas,
aba **Detalhes**, ache o `python.exe` do backend, botão direito, **Suspender**.

- [ ] Com o backend suspenso, envie uma mensagem
- [ ] Os três pontinhos aparecem normalmente
- [ ] Clique em **Nova conversa**: a tela limpa **e o botão Enviar volta a
      funcionar**, sem recarregar a página
- [ ] Escreva de novo: dá para digitar e o botão habilita
- [ ] Retome o processo (botão direito, **Retomar**) e, se preferir esperar em
      vez de clicar em Nova conversa: em até 90 segundos aparece
      *"A resposta demorou mais de 90 segundos e foi cancelada"*

**Esperado:** nenhum estado da tela fica sem saída. O limite de 90 segundos é
generoso de propósito: um turno com retentativa do LLM mais a busca do RAG
passa dos 10 segundos com folga, e cortar cedo quebraria a demo em vez de
salvá-la.

---

## 8.1 Chat: quando o Gemini está sobrecarregado

O `503 UNAVAILABLE` do Gemini é rotina em horário de pico, e não é defeito do
projeto: o backend retenta duas vezes e, se o modelo continuar fora, o agente
mock assume o turno.

Para simular sem depender do humor do Google, tire a chave: comente a linha
`GEMINI_API_KEY` no `.env` da raiz e reinicie o backend.

- [ ] O cabeçalho mostra o selo âmbar **IA em modo mock**
- [ ] O chat continua respondendo, com perguntas diretas sobre o que falta
- [ ] A bolha do agente traz a marca discreta **mock** ao lado
- [ ] Os chips "anotei" continuam aparecendo: a memória não depende do agente
- [ ] Os cards de imóvel continuam vindo: o RAG também não depende

### 8.1.1 O caso que o selo escondia

Comentar a chave é o único jeito em que o modo realmente vira "mock" no boot.
Os casos que de fato acontecem numa tarde de testes são outros: **chave
inválida**, **projeto bloqueado** e **cota do dia estourada**. Neles o backend
sobe achando que tem Gemini, e todo turno cai no mock em silêncio.

Para simular: troque **um caractere** da `GEMINI_API_KEY` no `.env` e reinicie.

- [ ] A bolha vem marcada como **mock**
- [ ] `curl http://localhost:8000/health` responde `"agente": "mock"`, e não
      `"gemini"`
- [ ] O campo `modo_configurado` continua dizendo `"gemini"`, que é o que
      permite ver a diferença entre o que foi configurado e o que aconteceu
- [ ] O selo âmbar do cabeçalho aparece em até um minuto, sem recarregar

**Esperado:** o `/health` conta o que aconteceu no último turno, não o que o
boot achou que ia acontecer. Antes ele dizia "gemini" e o selo ficava verde
enquanto toda resposta vinha do mock.

**Esperado (8.1):** com o Gemini fora, a conversa fica robótica e continua útil.
O que não pode acontecer é o lead receber "tive um problema técnico" e a
qualificação parar. Devolva a chave ao `.env` e reinicie para voltar ao normal.

---

## 9. Dashboard: números do topo

Antes deste bloco, deixe pelo menos dois ou três leads no sistema (repita o
bloco 2 com perfis diferentes: compra na Tijuca, investimento, aluguel em
Copacabana).

- [ ] Os seis KPIs mostram Leads, Quentes, Mornos, Frios, Qualificação e Agenda
- [ ] **Quentes** aparece na cor quente e traz o detalhe "ligar hoje"
- [ ] **Qualificação** mostra a porcentagem com vírgula decimal e o número de
      qualificados embaixo
- [ ] Os três gráficos de barra (**Por status**, **Por intenção**, **Regiões
      mais pedidas**) têm a maior barra cheia e as outras em proporção
- [ ] Os rótulos das barras estão em português (`aluguel`, `Qualificado`)

**Esperado:** nada aqui é calculado na tela. Os rótulos e as contagens vêm
prontos do backend, para a tela e a API nunca discordarem.

---

## 10. Dashboard: a lista de leads

- [ ] A ordem padrão é do maior score para o menor
- [ ] Cada linha tem uma **faixa colorida na borda esquerda**: quente, morno ou
      frio
- [ ] A legenda embaixo da lista explica a cor
- [ ] Cada linha mostra nome, contato, número de mensagens, interesse, urgência,
      score, status e silêncio
- [ ] Lead sem nome aparece como **(sem nome)**, sem quebrar a linha
- [ ] Passar o mouse na linha mostra a temperatura e o score no tooltip
- [ ] Clicar na linha abre o detalhe do lead

Ordenação e filtros:

- [ ] Trocar para **Última mensagem** reordena a lista
- [ ] Escolher **Quentes** em temperatura deixa só linhas com faixa quente
- [ ] Escolher um status filtra por status
- [ ] Clicar em **Compra**, **Aluguel** ou **Investimento** filtra por intenção
- [ ] Digitar um nome na busca filtra, e a requisição só sai quando você para de
      digitar (não é uma busca por tecla)
- [ ] Busque por algo inexistente: aparece **Nenhum lead com esses filtros** com
      a saída sugerida
- [ ] Limpe os filtros numa base vazia: a mensagem muda para **Nenhum lead
      ainda**, explicando de onde vem o primeiro

**Esperado:** os dois vazios dizem coisas diferentes. "Não achei com esse
filtro" e "ainda não existe nada" pedem ações opostas de quem está olhando.

---

## 11. Dashboard: precisam de atenção

> **Duas coisas antes de rodar este bloco.**
>
> 1. **O follow-up só dispara com consentimento.** A caixa "Quero que a
>    imobiliária me procure" nasce **desmarcada** de propósito: sob a LGPD, o
>    aceite tem que ser um ato da pessoa, e caixa pré-marcada é exatamente o
>    que a lei não admite. Se o lead que você envelheceu não marcou a caixa, a
>    decisão vai dizer "sem consentimento registrado (LGPD)" e nada será
>    enviado. Isso é o comportamento certo: para ver o envio, marque a caixa na
>    primeira mensagem do lead de teste.
> 2. **O job não gasta cota do Gemini.** O texto do follow-up automático é
>    escrito pela heurística, não pelo LLM (`FOLLOWUP_USA_LLM=false`, que é o
>    padrão). Antes ele acordava a cada 30 minutos gastando a mesma cota diária
>    de 20 requisições que o chat usa, em segundo plano: o testador envelhecia
>    leads seguindo este roteiro e, meia hora depois, o chat caía no mock sem
>    explicação. O texto com IA continua disponível sob demanda, quando o
>    corretor abre o item.
> 3. **Follow-up só em horário comercial.** O job não envia antes das 9h,
>    depois das 20h nem aos domingos. A lista "Precisam de atenção" continua
>    mostrando quem está devendo resposta a qualquer hora: o que a janela adia
>    é o envio, não a constatação.


O follow-up automático da Parte 2 é restritivo de propósito: exige **24h de
silêncio** (ou 4h se a urgência do lead for alta), consentimento registrado e
nenhum pedido de descadastro. Numa sessão de teste, ninguém está calado há um
dia, então a seção mostra o estado vazio.

- [ ] Com todos os leads recentes, a seção mostra **Ninguém esperando
      resposta**, explicando que o job roda a cada 30 minutos

Para ver a seção preenchida, envelheça um lead com o script que acompanha este
roteiro:

```powershell
.venv\Scripts\python.exe docs/envelhecer-lead.py --listar              # ids e quanto cada um está calado
.venv\Scripts\python.exe docs/envelhecer-lead.py lead-0001             # simula 30h de silêncio
.venv\Scripts\python.exe docs/envelhecer-lead.py lead-0001 --horas 80  # para testar a segunda tentativa
```

Ele recua a última mensagem do lead e zera o contador de tentativas, sem
inventar dado nenhum. Se o lead não tiver consentimento registrado, o script
avisa que ele não vai aparecer na lista, porque a Parte 2 não retoma sem aceite.

Não precisa reiniciar o backend: a memória lê o estado do banco a cada consulta.

- [ ] Recarregue o Dashboard: o lead aparece em **Precisam de atenção**
- [ ] O item mostra o motivo (`30h de silêncio, tentativa 1 de 3`), o selo
      **1d calado** e a tentativa com o tom
- [ ] Clique em **ver texto sugerido**: aparece "gerando..." e depois o texto
      que o agente mandaria
- [ ] Clicar de novo esconde o texto, sem gerar outra vez

**Esperado:** o texto sugerido **não** vem no carregamento da lista. Ele custa
uma chamada de LLM por lead, então só sai no clique. Se ele aparecer sozinho, a
tela está queimando cota a cada visita ao Dashboard.

---

## 12. Chat: a bolha de retomada automática

As mensagens que o agente manda por iniciativa própria são desenhadas diferente
das respostas. Para produzir uma, rode um ciclo do job (usa o lead envelhecido
no bloco 11 e gasta uma chamada de LLM):

```powershell
.venv\Scripts\python.exe backend/src/jobs/followup_scheduler.py --dry-run   # mostra quem seria retomado
.venv\Scripts\python.exe backend/src/jobs/followup_scheduler.py --once      # retoma de verdade
```

- [ ] No DevTools, ponha o id desse lead no `lead_id` do Local Storage e abra o
      chat
- [ ] A mensagem do job aparece numa bolha **âmbar de borda tracejada**, com o
      rótulo **Retomada automática** em cima
- [ ] Ela é visivelmente diferente de uma resposta normal do agente

**Esperado:** o lead precisa entender que aquilo é a imobiliária puxando
conversa, não a resposta a uma pergunta que ele não fez.

---

## 13. Detalhe do lead: o resumo do corretor

Abra um lead com conversa pelo Dashboard.

- [ ] O topo mostra nome, contatos, temperatura com score e status
- [ ] Lead que não aceitou a LGPD mostra o selo **sem consentimento**
- [ ] A faixa de dados mostra origem, primeiro contato, última mensagem e
      silêncio, com datas no fuso local
- [ ] O card **Resumo para o corretor** abre com a próxima ação em destaque
      verde, com a seta
- [ ] Abaixo dela vem o resumo em texto corrido
- [ ] **Alertas** aparecem em âmbar (por exemplo "Sem contato: não dá para
      retomar fora do chat")
- [ ] **Sinais de compra** e **Objeções** aparecem quando existem
- [ ] **Imóveis já mostrados** lista os ids que o RAG enviou
- [ ] Abrir **Como o score N foi montado** mostra os fatores
      (`+20 intenção informado`)

**Esperado:** o número do score é auditável. Corretor não confia em número
mágico para decidir quem ligar.

Agora o resumo gerado por LLM:

- [ ] Clique em **Melhorar com IA**
- [ ] O selo mostra "gerando com IA..." e depois **gerado com IA**
- [ ] O texto do resumo fica melhor escrito que o heurístico

**Esperado:** se a cota do Gemini estourar, o selo diz **heurístico (IA
indisponível)** em vez de mentir. E esse botão nunca dispara sozinho ao abrir a
tela: ele gasta cota.

---

## 14. Detalhe do lead: ações do corretor

- [ ] Em **Mover para**, clique em **Qualificado**: o badge do topo muda
- [ ] O status atual fica desabilitado na barra (não dá para reaplicar)
- [ ] Volte ao Dashboard: a lista já mostra o status novo
- [ ] Reabra o lead: o telefone e o e-mail continuam lá

**Por que isso importa:** a atualização é parcial. Mandar só o status não pode
apagar o contato do lead, que costuma ser o dado mais caro da base.

- [ ] **Exportar JSON** baixa tudo o que o sistema guarda do lead
- [ ] No card **Agendamentos**, clique em **Novo agendamento**
- [ ] Preencha data e hora, escolha o tipo, cole um id de imóvel
      (`IMV-0086`) e ponha seu nome como corretor
- [ ] Clique em **Agendar**: o agendamento aparece na lista do card
- [ ] O status do lead virou **Agendado** sozinho
- [ ] Ponha um id de imóvel que não existe (`IMV-0000000`) e tente agendar: a
      tela mostra uma mensagem de erro legível, não `[object Object]`

---

## 15. Agenda

- [ ] Os agendamentos aparecem agrupados por dia, com o dia da semana no título
- [ ] Dentro do dia, ordenados por hora
- [ ] Cada linha mostra hora, tipo, nome do lead e título do imóvel
- [ ] Clicar no nome do lead abre o detalhe dele
- [ ] Trocar a janela para **Próximos 30 dias** traz mais itens
- [ ] O filtro de status funciona
- [ ] Em um agendamento pendente, **Remarcar** abre o campo com a data atual já
      preenchida; salvar move o item para o novo dia
- [ ] **Realizado** muda o selo para Realizado e as ações desaparecem
- [ ] **Não compareceu** muda o selo para Não compareceu
- [ ] Em um lead com um único agendamento, **Cancelar** muda o selo para
      Cancelado
- [ ] Depois de cancelar, abra o lead: o status voltou para **Qualificado**

**Esperado:** cancelar a única visita não deixa o lead preso em "Agendado", ou
ele desapareceria das listas de quem precisa de atenção.

---

## 16. Imóveis

- [ ] O card **Catálogo** mostra o total de imóveis da base no selo
- [ ] Os cards mostram título, preço, bairro, zona, tipo, quartos, banheiros,
      área, vagas, condomínio e as características
- [ ] Imóvel de aluguel mostra `/mês` no preço
- [ ] Imóvel com rendimento estimado mostra "retorno estimado X% ao ano"
- [ ] O select de bairros veio da base (tem Botafogo, Copacabana, Tijuca e
      outros, não uma lista curta chumbada)
- [ ] Filtrar por bairro reduz a lista e o total
- [ ] Filtrar por **aluguel** deixa só imóveis com `/mês`
- [ ] Escolher **2+ quartos** e um preço máximo combina os filtros
- [ ] A paginação mostra "página 1 de N"; **Próxima** avança e **Anterior**
      volta
- [ ] Trocar um filtro estando na página 3 **volta para a página 1**
- [ ] Um filtro impossível (preço máximo 1) mostra **Nenhum imóvel com esses
      filtros**

**Por que a volta para a página 1 importa:** sem ela, um filtro com poucos
resultados mostraria "nenhum resultado" enquanto os resultados existem, na
primeira página.

---

## 16.1 Privacidade e limites, na prática

Três coisas que a banca costuma perguntar e que dá para mostrar na tela. O que
o sistema protege, o que ele **não** protege e por quê está em
[`privacidade-e-limites.md`](privacidade-e-limites.md), com a demonstração de
60 segundos no fim.

**O aviso de que a IA processa a conversa**

- [ ] Numa conversa nova, abaixo da caixa de consentimento, aparece o texto
      dizendo que a conversa é respondida por IA e processada pelo Google
      Gemini, e que nome, telefone e e-mail viram apelidos antes de sair
- [ ] O aviso **não** tem caixa para marcar: é informação (LGPD art. 9º), não
      consentimento

**O `lead_id` não é adivinhável**

- [ ] Abra duas conversas novas (aba anônima serve) e compare os dois ids no
      cabeçalho da conversa
- [ ] Eles **não** são `lead-0001` e `lead-0002`: são hexadecimais longos
- [ ] Isso importa porque não há login: com id sequencial, trocar um dígito em
      `/leads/{id}/exportar` devolvia o nome, o telefone e a conversa inteira
      de outra pessoa, e `DELETE /leads/{id}` apagava os dados dela

**Visita no passado é recusada**

- [ ] No seletor de data, tente escolher uma data de ontem: o navegador não
      deixa
- [ ] Pelo terminal, force pela API:

```bash
curl -X POST http://localhost:8000/leads/SEU_LEAD_ID/schedule \
  -H "Content-Type: application/json" \
  -d '{"data_hora":"2020-01-01T10:00:00Z","tipo":"VISITA"}'
```

- [ ] Responde **422**, e não 201. Antes o backend aceitava, e o lead saía da
      conversa achando que tinha visita marcada para o mês anterior

**As abas do corretor podem sumir**

- [ ] O painel e o chat são a mesma aplicação, no mesmo endereço e sem login
- [ ] Ponha `VITE_MODO_CORRETOR=false` no `.env` do front e reinicie o `npm run
      dev`: as abas Dashboard, Agenda e Imóveis somem do cabeçalho
- [ ] Isso **esconde a navegação**; não é autenticação, e as rotas continuam
      existindo. O padrão é `true` justamente para a banca navegar sem
      configurar nada

---

## 17. Responsivo, teclado e movimento

- [ ] Em tela cheia de monitor grande, o texto não parece miúdo, e o conteúdo
      não deixa faixas largas de fundo sobrando dos dois lados
- [ ] A conversa e o resumo da IA param de crescer antes de virarem linhas
      longas demais para ler, mesmo com o painel ocupando a tela toda
- [ ] Estreite a janela até a largura de um celular: o chat empilha o painel do
      perfil embaixo da conversa
- [ ] O Dashboard mantém a lista rolável na horizontal, sem a página inteira
      rolar de lado
- [ ] Nenhum texto fica cortado nem invade o card vizinho
- [ ] Navegue só de `Tab`: todo botão, campo e link recebe um anel de foco
      visível
- [ ] `Enter` no campo de mensagem envia (é um formulário)
- [ ] Ligue "reduzir movimento" no sistema e recarregue: os pontinhos do
      "digitando" param de pular e o chip entra sem animação

No Windows, reduzir movimento fica em Configurações → Acessibilidade → Efeitos
visuais → Efeitos de animação.

---

## 18. Checklist de fechamento

Marque o que passou ponta a ponta:

| Bloco | Funcionalidade | Passou |
| --- | --- | --- |
| 1 | Tema escuro, claro e automático | [ ] |
| 2 | Roteiro do locatário, as cinco mensagens | [ ] |
| 3 | Memória: chips de "anotei" e "corrigi" | [ ] |
| 4 | RAG: cards de imóvel com o porquê | [ ] |
| 5 | Reabrir conversa e descartar id inválido | [ ] |
| 6 | Agendamento de visita pelo chat | [ ] |
| 6.1 | Roteiro do investidor: ticket, retorno e consultoria | [ ] |
| 6.2 | Respostas que o sistema não entende | [ ] |
| 7 | LGPD: baixar, excluir e revogar o contato | [ ] |
| 8 | Comportamento com a API fora do ar | [ ] |
| 8.1 | Gemini sobrecarregado: mock assume o turno | [ ] |
| 9 | KPIs e gráficos do Dashboard | [ ] |
| 10 | Lista de leads, faixa de temperatura, filtros e busca | [ ] |
| 11 | Follow-ups pendentes e texto sugerido | [ ] |
| 12 | Bolha de retomada automática | [ ] |
| 13 | Resumo do corretor, fatores do score e "Melhorar com IA" | [ ] |
| 14 | Mover status, exportar e agendar pelo detalhe | [ ] |
| 15 | Agenda: remarcar, realizado, não compareceu, cancelar | [ ] |
| 16 | Catálogo: filtros da base e paginação | [ ] |
| 17 | Responsivo, teclado e movimento reduzido | [ ] |

**Parte deste roteiro roda sozinha.** `npm run test:e2e` executa os blocos 2 a
16 com as telas de verdade contra o backend no ar, do primeiro "oi" até
cancelar a visita. Use para saber se algo quebrou no comportamento antes de
gastar meia hora clicando.

O que ela não faz é ver: tema, contraste, responsivo, anel de foco e o flash no
recarregamento não têm como ser verificados sem navegador. Os blocos 1 e 17 são
só seus, e são os que mais aparecem numa apresentação com projetor.

Testes automáticos, para rodar junto:

```powershell
cd frontend
npm run verificar        # tipos, 110 testes de unidade e componente, build
npm run test:contrato    # 74 verificações contra a API no ar
npm run test:e2e         # os blocos 2 a 16 deste roteiro, nas telas de verdade

cd ..
.venv\Scripts\python.exe backend/tests/test_smoke.py        # Parte 3, banco temporário
.venv\Scripts\python.exe -m pytest ai-memory-rag/tests -q   # Parte 2
.venv\Scripts\python.exe -m pytest ai-core/tests -q        # Parte 1, extração
```

O teste de contrato **escreve no banco**: ele cria um lead pelo chat, agenda,
remarca, cancela e apaga tudo no fim. Rode contra banco de desenvolvimento.

---

## 19. Roteiro curto de 5 minutos, para a apresentação

A sequência que mostra o sistema inteiro sem tempo morto. Ensaie na ordem.

1. **Chat, escuro.** `quero alugar um apartamento de 2 quartos em Botafogo`.
   Aponte os três chips chegando e o perfil se montando à direita.
2. **Correção.** `na verdade preciso de 3 quartos`. Aponte o chip âmbar e o
   tooltip com o valor anterior: é a memória se corrigindo, na tela.
3. **RAG.** `até 4 mil por mês`. Leia em voz alta o "Por que este" de um card.
4. **Fechamento.** `preciso mudar esse mês`, depois
   `sou a Bruna, 21 99999-4410`. O convite aparece; marque a visita.

   São as mesmas cinco mensagens do bloco 2, de propósito: você ensaia
   exatamente o que testou.
5. **Vira a mesa para o corretor.** Dashboard: leads quentes no topo, faixa de
   temperatura na lista.
6. **Detalhe do lead.** Próxima ação em destaque, alertas, e abra "Como o score
   foi montado".
7. **Agenda.** A visita que você acabou de marcar, no dia certo.
8. **LGPD.** Volte ao chat e mostre os três controles do card: baixar, excluir e
   o de contato. Revogue e reative na frente da banca: é um clique, sem caixa de
   confirmação, e é o argumento de que sair é tão fácil quanto entrar.

Se for apresentar com a chave do Gemini ativa, mande a primeira mensagem antes
de começar: ela é a mais lenta, porque constrói o índice do RAG.
