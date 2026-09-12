# Agente SDR Imobiliário

Tech Challenge FIAP, Fase 5.

Um SDR de software para imobiliária. Ele conversa com o lead, extrai o que
define a oportunidade, sugere imóveis do catálogo, marca a visita e entrega ao
corretor uma ficha pronta com nota e próxima ação.

> **Chegando agora? Comece pelo manual:**
> [`docs/Manual-Agente-SDR-Imobiliario.pdf`](docs/Manual-Agente-SDR-Imobiliario.pdf)
>
> São 44 páginas que levam do zero até o sistema rodando, explicam cada tela e
> dizem o que observar durante a avaliação. Este README é o resumo; o manual é
> o caminho completo.

---

## Subir em cinco minutos

Precisa de **Python 3.11+** e **Node 20+**.

```powershell
# --- preparar, uma vez so ---
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt

cd frontend
npm install
cd ..
```

A chave do Gemini é **opcional**: sem ela o sistema sobe inteiro e responde por
um agente simulado, com um selo âmbar avisando na tela. Para usar a IA de
verdade, copie o modelo e preencha:

```powershell
copy backend\.env.example .env
notepad .env        # preencha GEMINI_API_KEY
```

A chave sai de [aistudio.google.com/apikey](https://aistudio.google.com/apikey),
é gratuita e não pede cartão.

Depois, **dois terminais**:

```powershell
# terminal 1
.venv\Scripts\python.exe backend\run.py      # http://localhost:8000

# terminal 2
cd frontend
npm run dev                                   # http://localhost:5173
```

Confira a chave antes de gastar tempo adivinhando o erro:

```powershell
.venv\Scripts\python.exe docs\verificar-chave.py
```

Ele traduz o erro do Gemini em o que fazer, porque "429" e "403" se parecem e
significam coisas diferentes: cota do dia, crédito zerado, projeto bloqueado ou
tipo de chave errado.

### Sobre a cota gratuita

São **20 requisições por dia, por modelo**. Cada mensagem no chat gasta uma, e
uma qualificação completa tem de seis a oito mensagens: dá para duas ou três
conversas inteiras por dia.

Quando a cota acaba, o chat **não quebra**. Ele cai no agente simulado, marca a
bolha na tela e a qualificação continua andando. A busca de imóveis usa outro
modelo, com cota própria, e fica em cache no disco.

---

## As quatro partes

| Parte | Pasta | Do que cuida |
| --- | --- | --- |
| 1. Agente de IA | [`ai-core/`](ai-core/) | Conversa com o lead e extrai dados da fala |
| 2. Memória e RAG | [`ai-memory-rag/`](ai-memory-rag/) | Lembra do lead, busca imóveis, calcula o score, decide o follow-up |
| 3. Backend | [`backend/`](backend/) | API HTTP, banco, agendamentos, job de retomada |
| 4. Frontend | [`frontend/`](frontend/) | O chat do lead e o painel do corretor |

O caminho de uma mensagem atravessa as quatro: a tela manda para a API, a
memória absorve e mascara os dados pessoais, o RAG busca imóveis compatíveis, o
agente escreve a resposta com esse contexto, a memória grava o que mudou e
recalcula o score, e a tela mostra resposta, etiquetas, perfil e imóveis.

Cada pasta tem README próprio com as decisões dela.

---

## As telas

| Rota | O que é |
| --- | --- |
| `/` | O chat, como o lead vê |
| `/painel` | Dashboard do corretor |
| `/painel/leads/:id` | Ficha do lead, com o score aberto fator a fator |
| `/painel/agenda` | Visitas e consultorias |
| `/painel/imoveis` | Catálogo, 140 imóveis em 20 bairros do Rio |

As duas experiências convivem no mesmo endereço porque isto é uma demonstração:
você escreve no chat e vê o painel reagir. `VITE_MODO_CORRETOR=false` esconde as
abas do painel, deixando só o chat.

---

## Documentação

| Documento | Para quê |
| --- | --- |
| [Manual em PDF](docs/Manual-Agente-SDR-Imobiliario.pdf) | Do zero ao sistema rodando, cada tela, e o que observar na avaliação |
| [`docs/roteiro-de-teste-manual.md`](docs/roteiro-de-teste-manual.md) | Conferir funcionalidade por funcionalidade, item a item |
| [`docs/privacidade-e-limites.md`](docs/privacidade-e-limites.md) | O que sai para o Gemini, os direitos do titular, e **até onde a proteção vai** |
| [`docs/frontend-integracao.md`](docs/frontend-integracao.md) | Contrato das rotas da API |
| [`frontend/README.md`](frontend/README.md) | Decisões de interface e como cada campo da API virou tela |

Com o backend no ar, [`/docs`](http://localhost:8000/docs) abre a documentação
interativa da API, onde dá para executar cada rota pelo navegador.

---

## Como conversar com o agente

O agente é um modelo de linguagem, e **a redação dele muda a cada execução**. O
que não muda é o comportamento: quais campos ele persegue e em que ordem, quais
etiquetas aparecem, quando o seletor de data surge, se os imóveis respeitam o
orçamento.

Por isso não existe um script de frases para conferir palavra por palavra. Há
**frases de partida**, e dali você conversa do seu jeito:

```
Oi, quero comprar um apartamento
Oi, estou procurando um apartamento para alugar
Oi, quero investir em imóveis para alugar
```

A terceira tem a palavra "alugar" dentro e mesmo assim a intenção correta é
investimento: ali, "alugar" diz o que a pessoa vai fazer com o imóvel, não por
que está procurando. Vale observar essa etiqueta.

A seção 7 do manual detalha o que observar em cada caminho. O investidor segue
uma ordem de coleta diferente, e **nunca é perguntado sobre quartos**: quem
investe decide por ticket e retorno.

---

## Testes

```powershell
.venv\Scripts\python.exe -m pytest ai-core/tests ai-memory-rag/tests -q
.venv\Scripts\python.exe backend\tests\test_smoke.py

cd frontend
npm test
npm run test:e2e        # exige o backend no ar
npm run test:contrato   # exige o backend no ar
```

| Suíte | Quantidade |
| --- | --- |
| Partes 1 e 2 | 401 testes |
| Backend, ponta a ponta | 97 verificações |
| Frontend, unitários e componente | 121 testes |
| Frontend contra a API no ar | 12 testes |
| Contrato entre front e API | 75 verificações |

Este README também é verificado:

```powershell
.venv\Scripts\python.exe docs\conferir-readme.py
```

Ele confere que todo link aponta para algo que existe, que todo arquivo e
script citado nos comandos é real, que as rotas existem no front, e que os
números aqui batem com o repositório. Escrito porque a versão anterior deste
arquivo mandava rodar três comandos sobre arquivos que não existiam mais, e
ninguém percebeu: quem relê o README depois que o projeto muda de forma é
justamente ninguém.

---

## Limites conhecidos

Escritos aqui de propósito, porque um sistema apresentado como maior do que é
vira um problema quando alguém testa.

- **Não há autenticação.** Chat e painel são a mesma aplicação, no mesmo
  endereço. O projeto roda em localhost e foi pensado para isso.
- **A detecção de dado pessoal é por expressão regular.** Cobre e-mail,
  telefone, CPF e nome declarado com inicial maiúscula; deixa passar nome em
  minúscula e endereço escrito de forma livre.
- **O mapa de apelidos fica no mesmo banco que o texto pseudonimizado.** A
  proteção é contra o terceiro que processa a conversa, não contra quem tem o
  arquivo do banco.
- **O banco é um SQLite de arquivo único.** Suficiente para uma demonstração de
  um usuário, não para vários corretores escrevendo ao mesmo tempo.
- **O catálogo é gerado sinteticamente.** Os 140 imóveis são consistentes, com
  preço por metro quadrado coerente com o bairro, mas não são anúncios reais.
- **O sistema não fecha negócio.** Ele qualifica e agenda; preço final,
  pagamento e documentação são trabalho do corretor.

O detalhamento está em
[`docs/privacidade-e-limites.md`](docs/privacidade-e-limites.md).

---

## Recomeçar do zero

```powershell
# pare o backend antes
Remove-Item backend\data\app.db
```

Ele recria o banco e repopula os 140 imóveis na próxima subida.
