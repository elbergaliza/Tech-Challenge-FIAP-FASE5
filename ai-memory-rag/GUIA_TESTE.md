# Guia de teste do `run_chat.py`

Passo a passo para rodar a Parte 2 de ponta a ponta no terminal, com e sem
Gemini.

O `run_chat.py` é o mais perto de "rodar o projeto" que existe hoje, porque o
backend (Parte 3) e o frontend (Parte 4) ainda estão vazios. Ele faz o papel dos
dois: recebe a mensagem digitada, chama a Parte 2, chama o agente da Parte 1 e
devolve a resposta na tela.

---

## Parte A — Sem Gemini

Não instala nada. Python da caixa, dois minutos.

### 1. Abra o terminal na raiz do repositório

```bash
cd "D:\Projects\Pos\Fase 05\Tech-Challenge-FIAP-FASE5"
```

### 2. Rode

```bash
python ai-memory-rag/scripts/run_chat.py marcos-teste
```

Confira o cabeçalho:

```
==========================================================================
AGENTE SDR IMOBILIÁRIO  ·  Parte 2 rodando de ponta a ponta
==========================================================================
lead_id:   marcos-teste
agente:    SIMULADO (agente real indisponível)
resumo:    heurístico (sem LLM)
embedder:  hashing-v1  ·  140 imóveis indexados
memória:   ...\ai-memory-rag\data\conversas
```

`SIMULADO` é o esperado aqui. O agente falso pergunta o próximo campo que a
**memória** ainda não tem, o que já demonstra a memória guiando o diálogo.

### 3. Converse, uma linha por vez

```
Oi, meu nome é Marcos
quero comprar em Copacabana
3 quartos, até 1.5 milhão
é urgente, meu zap é (21) 98765-4321
```

A conversa inteira sai assim:

```
você > Oi, meu nome é Marcos

agente > Você está procurando para comprar, alugar ou investir?
         [+ Nome: Marcos]
         [log: lead=marcos-teste messages=2 fields=1 followups=0]

você > quero comprar em Copacabana

agente > Quantos quartos você precisa?
         [+ Intenção: BUY]
         [+ Região: Copacabana]
         [rag: IMV-0085, IMV-0025, IMV-0005]
         [log: lead=marcos-teste messages=4 fields=3 followups=0]

você > 3 quartos, até 1.5 milhão

agente > Como está a urgência? Precisa para logo?
         [+ Quartos: 3]
         [+ Faixa de preço: 1.5 milhão]
         [rag: IMV-0006, IMV-0028, IMV-0126]
         [log: lead=marcos-teste messages=6 fields=5 followups=0]

você > é urgente, meu zap é (21) 98765-4321

agente > Perfeito, tenho tudo que preciso. Quer agendar uma visita?
         [+ Urgência: high]
         [+ Telefone: (21) 98765-4321]
         [rag: IMV-0006, IMV-0126, IMV-0128]
         [log: lead=marcos-teste messages=8 fields=7 followups=0]
```

O que olhar:

- **Cada resposta avança.** O agente nunca repete uma pergunta que o lead
  acabou de responder. Isso depende da ordem de orquestração, e é o assunto da
  última seção deste guia.
- `[+ campo]` é a memória sendo preenchida
- `[~ campo]` é uma correção do lead, quando ele muda de ideia. Para ver uma,
  diga depois `na verdade prefiro a Tijuca`.
- `[rag: ...]` são os imóveis encontrados. Só aparece a partir do momento em
  que já se sabe algo do lead; nos primeiros turnos o RAG devolveria imóveis ao
  acaso, o que confunde mais do que ajuda.
- `[log: ...]` é a linha de log, sem nenhum dado pessoal

### 4. Inspecione

| Comando | O que mostra |
|---|---|
| `/perfil` | tudo que a memória acumulou |
| `/prompt` | o que **saiu** para o LLM, mascarado, e o mapa de apelidos |
| `/resumo` | o card do corretor, com score e próxima ação |
| `/imoveis` | a busca agora e o bloco que vai para o prompt |
| `/followup` | se caberia retomar contato e o texto que seria enviado |

O `/prompt` é o mais interessante para a apresentação. Ele mostra `[NOME_1]` e
`[TELEFONE_1]` no que sai para o Gemini, e o mapa real que fica só na sua
máquina:

```
mensagem: é urgente, meu zap é [TELEFONE_1]

contexto:
  O QUE VOCÊ JÁ SABE SOBRE ESTE LEAD (não pergunte de novo):
  - Nome: [NOME_1]
  - Intenção: compra
  - Região: Copacabana
  ...

mapa de apelidos (NUNCA sai daqui):
  {'[NOME_1]': 'Marcos', '[TELEFONE_1]': '(21) 98765-4321'}
```

### 5. Prove a memória entre sessões

Saia com `/sair` e rode de novo com o **mesmo** `lead_id`:

```bash
python ai-memory-rag/scripts/run_chat.py marcos-teste
```

Deve aparecer:

```
Conversa anterior encontrada: 8 mensagens. Retomando.
```

Digite `/perfil`: está tudo lá. É esse o cenário 3 do PDF do desafio, o lead que
volta depois de sumir.

### 6. Prove a LGPD

```
/exportar   → o pacote do direito de acesso
/esquecer   → direito de exclusão, apaga inclusive o mapa de apelidos
/perfil     → vazio agora
```

---

## Parte B — Com Gemini

### 1. Crie e ative o ambiente virtual

Na raiz do repositório:

```bash
python -m venv .venv
```

Ativar muda conforme o terminal:

| Terminal | Comando |
|---|---|
| Git Bash | `source .venv/Scripts/activate` |
| PowerShell | `.venv\Scripts\Activate.ps1` |
| Prompt (cmd) | `.venv\Scriptsctivate.bat` |
| Linux / macOS | `source .venv/bin/activate` |

Deu certo quando o prompt passa a começar com `(.venv)`.

> Se o PowerShell recusar o `Activate.ps1` falando de política de execução,
> rode `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` na mesma
> janela e tente de novo. Vale só para aquela sessão.

O `.venv` já está no `.gitignore`.

### 2. Instale as duas dependências

Com o ambiente ativo:

```bash
pip install google-genai python-dotenv
```

O `python-dotenv` é do `agent.py` da Pessoa 1, não do meu módulo. Sem ele o
import dela falha e o script cai no modo simulado.

> **`ERROR: Could not find an activated virtualenv (required)`** significa que
> o passo 1 não foi feito, ou que o ambiente não está ativo nesta janela. O
> erro vem da variável `PIP_REQUIRE_VIRTUALENV`, que existe para impedir
> instalação no Python do sistema. Ative o `.venv` em vez de desligar a
> variável.

### 3. Pegue a chave

Em <https://ai.google.dev/> → **Get API Key** → **Create API Key**. Formato
`AIza...`, gratuita.

### 4. Crie o `.env` na raiz do repositório

```
GEMINI_API_KEY=AIza...
```

Tem que ser na **raiz do repositório**, ao lado do `README.md`. O script procura
lá de propósito, então funciona rodando de qualquer pasta.

> O `.env` já está no `.gitignore`. Não commite a chave.

### 5. Rode de novo

```bash
python ai-memory-rag/scripts/run_chat.py marcos-real
```

Agora o cabeçalho deve mostrar:

```
agente:    Pessoa 1 + Gemini
resumo:    gemini-2.5-flash
embedder:  gemini-embedding-001  ·  140 imóveis indexados
```

Três coisas mudam de verdade:

1. **O agente conversa.** Em vez de seguir uma lista de perguntas, responde
   como um SDR.
2. **O resumo fica melhor.** O `/resumo` passa a trazer sinais de compra e
   objeções extraídos da conversa, e o rodapé deixa de dizer "IA indisponível".
3. **A busca fica semântica.** "Quero algo silencioso para trabalhar de casa"
   encontra imóveis com "escritório, rua tranquila", coisa que o embedder
   offline não faz.

### 6. Compare os dois lado a lado

```bash
python ai-memory-rag/scripts/run_chat.py comparar --simulado
```

Mesmo tendo chave, `--simulado` força o modo sem IA. Serve para mostrar que o
módulo funciona igual nos dois casos; o que muda é a qualidade do texto.

---

## Se der errado

| O que aparece | O que fazer |
|---|---|
| `[agente real indisponível: No module named 'google']` | `pip install google-genai` |
| `[agente real indisponível: No module named 'dotenv']` | `pip install python-dotenv` |
| `[agente real indisponível: GEMINI_API_KEY não encontrada]` | `.env` ausente ou fora da raiz do repo |
| Agente responde *"Desculpe, tive um problema técnico"* | chave inválida **ou** model id inexistente |
| `ERROR: Could not find an activated virtualenv` | o `.venv` não está ativo nesta janela (passo 1) |
| `[rag] Gemini indisponível (400 INVALID_ARGUMENT...)` | chave errada; o RAG segue no embedder offline |

O último é o mais provável. O `agent.py` usa `gemini-3.6-flash`, e não consegui
confirmar que esse id existe. Dá para testar trocando o id direto no `agent.py`,
ou definindo `GEMINI_MODEL` no `.env` — o meu módulo já lê essa variável, o dela
ainda não.

**Em qualquer falha o script continua rodando no modo simulado.** A Parte 2
nunca fica intestável por causa da chave.

---

## O resto da suíte

```bash
# 263 testes, offline, sem instalar nada
python -m unittest discover -s ai-memory-rag/tests -v

# demos automáticas, sem interação
python ai-memory-rag/scripts/demo_rag.py      # RAG nos cenários do PDF
python ai-memory-rag/scripts/demo_memory.py   # memória + pseudonimização
python ai-memory-rag/scripts/demo_broker.py   # carteira e escada de follow-up

# regenerar a base (determinística) e o índice
python ai-memory-rag/scripts/generate_properties.py
cd ai-memory-rag && python -m src.rag.indexer --embedder hashing
```

A `demo_memory.py` é a mais forte para apresentar: mostra, turno a turno, o que
o lead escreveu, o que saiu mascarado para o Gemini, o que ele respondeu com
apelidos e o que o lead recebeu restaurado.

---

## Dois detalhes técnicos que valem para a Parte 3

Os dois vieram de bugs encontrados construindo este script, e os dois valem ser
repassados a quem for escrever a rota de chat no backend.

### 1. A ordem importa: absorver antes de perguntar

A sequência correta de um turno é:

```
start_turn  →  memória absorve a mensagem  →  contexto refeito  →  RAG  →  agente  →  finish_turn
```

Na primeira versão a absorção acontecia só no `finish_turn`, ou seja, **depois**
de o agente responder. O agente então decidia a próxima pergunta lendo um perfil
defasado em um turno, e repetia exatamente o que o lead acabara de responder:

```
você > quero comprar em Copacabana

agente > Você está procurando para comprar, alugar ou investir?   ← errado
         [+ Intenção: BUY]
         [+ Região: Copacabana]
```

O sintoma era ter que digitar tudo duas vezes. O contexto do prompt sofria do
mesmo mal: dizia "AINDA FALTA DESCOBRIR: quartos" na mesma mensagem em que o
lead informou os quartos.

Não era bug do módulo de memória, que sempre esteve certo. Era da orquestração.
E é exatamente essa orquestração que a rota de chat da Parte 3 vai reescrever,
então vale o aviso.

### 2. Enriquecer a mensagem corrompe o perfil

O `run_chat.py` prefixa o contexto da memória e o bloco do RAG na mensagem
enviada ao agente, porque o `chamar_agente` da Pessoa 1 ainda não aceita um
parâmetro `contexto_extra`.

A extração dela é regex sobre o texto recebido, então ela lia os **imóveis**
como se o lead os tivesse dito. Uma descrição com "renda de aluguel" virava
intenção `INVESTIMENTO`, o bairro do imóvel virava a região desejada, o preço do
imóvel virava o orçamento:

```
você > 3 quartos, até 1.5 milhão

         [~ Intenção: INVEST]              ← veio da descrição de um imóvel
         [~ Região: Lagoa]                 ← veio do bairro de um imóvel
         [+ Faixa de preço: R$ 3.166.000]  ← veio do preço de um imóvel
```

O script contorna ignorando o `dados_coletados` do agente e extraindo só da
mensagem limpa. Mas o contorno é a prova de que o `contexto_extra` precisa
existir: enquanto a extração for regex sobre o texto, enriquecer a mensagem é
ativamente perigoso.

Ver `ai-memory-rag/README.md`, seção "Achados no código da Parte 1".
