# ai-memory-rag — Memória, RAG e Resumo (Parte 2)

Módulo "avançado" da IA do Agente SDR Imobiliário: busca de imóveis compatíveis
com o perfil do lead (RAG), memória conversacional, resumo para o corretor e
texto de follow-up.

## Estado atual

| Entrega | Arquivo | Status |
|---|---|---|
| Base simulada de imóveis | `scripts/generate_properties.py` → `shared/data/imoveis.json` | pronto |
| Contrato do documento | `src/rag/schema.py`, `shared/schemas/imovel_schema.json` | pronto |
| Embeddings | `src/rag/embeddings.py` | pronto |
| Indexador | `src/rag/indexer.py` | pronto |
| Busca híbrida | `src/rag/retriever.py` | pronto |
| Pseudonimização de PII | `src/privacy.py` | pronto |
| Memória conversacional | `src/memory/conversation_memory.py` | pronto |
| Acesso ao LLM | `src/llm.py` | pronto |
| Resumo para o corretor | `src/summarizer.py` | pronto |
| Follow-up automático | `src/followup.py` | pronto |
| Contrato com a Pessoa 1 | `src/lead_profile.py` | pronto |
| Testes | `tests/` (263 testes) | pronto |

A Parte 2 está completa. Os quatro entregáveis combinados (memória, RAG, resumo
e follow-up) estão implementados, testados e demonstráveis offline.

## Como rodar

Não precisa instalar nada. Python 3.9+ da caixa, sem dependências:

```bash
# 1. gerar a base simulada (determinística; já está versionada)
python ai-memory-rag/scripts/generate_properties.py

# 2. ver o RAG funcionando nos cenários do PDF do desafio
python ai-memory-rag/scripts/demo_rag.py

# 3. ver memória + pseudonimização + RAG numa conversa de 4 turnos
python ai-memory-rag/scripts/demo_memory.py

# 4. ver a carteira do corretor e a escada de follow-up
python ai-memory-rag/scripts/demo_broker.py

# 5. rodar os testes
python -m unittest discover -s ai-memory-rag/tests -v
```

Para usar embeddings semânticos de verdade (opcional, precisa de
`GEMINI_API_KEY` no `.env` e de `pip install google-genai`):

```bash
python ai-memory-rag/scripts/demo_rag.py --embedder gemini
cd ai-memory-rag && python -m src.rag.indexer --embedder gemini
```

## Convenção de idioma

O código deste módulo é **em inglês**; o produto é **em português**.

| Em inglês | Em português |
|---|---|
| funções, classes, variáveis | prompts enviados ao Gemini |
| comentários, docstrings, testes | tudo que o lead ou o corretor lê |
| chaves de dados (`price`, `bedrooms`) | nomes próprios (`Copacabana`, `Zona Sul`) |
| valores de enum (`SALE`, `HOT`) | os rótulos nos mapas `*_LABELS` |
| chaves dos `to_dict()` | |

Enums são em inglês para o código e ganham rótulo em português na exibição.
O código decide com `intent == "BUY"`; o agente escreve "compra".

```python
TEMPERATURE_LABELS = {"HOT": "QUENTE", "WARM": "MORNO", "COLD": "FRIO"}
```

### A fronteira com a Pessoa 1

Um único lugar no módulo fala o dialeto dela: **`src/lead_profile.py`**. O
`chamar_agente()` devolve chaves e enums em português, e isso é contrato dela,
que não podemos mudar. Em vez de deixar esse formato se espalhar, ele é
traduzido uma vez, na borda:

```python
from_agent({"nome": "João", "intencao": "COMPRA", "regiao": "undefined"})
# -> {"name": "João", "intent": "BUY"}
```

Valores `"undefined"` são descartados em vez de carregados, para que o resto do
código nunca precise lembrar que aquela string é mágica. `from_agent` é
idempotente: aceita o perfil já traduzido, então dá para reprocessar sem
corromper. `to_agent()` faz o caminho inverso, se alguém precisar devolver o
perfil no formato da Pessoa 1.

O `ai-core` não foi tocado.

### A fronteira com a Pessoa 3

`shared/data/imoveis.json` mudou: as chaves e os enums agora são inglês.

| Antes | Agora | | Antes | Agora |
|---|---|---|---|---|
| `preco` | `price` | | `tipo_negocio` | `deal_type` |
| `quartos` | `bedrooms` | | `tipo_imovel` | `property_type` |
| `bairro` | `neighborhood` | | `caracteristicas` | `features` |
| `zona` | `zone` | | `vagas` | `parking` |
| `cidade` | `city` | | `banheiros` | `bathrooms` |
| `titulo` | `title` | | `condominio` | `condo_fee` |
| `descricao` | `description` | | `iptu` | `property_tax` |
| `yield_anual_pct` | `annual_yield_pct` | | `atualizado_em` | `updated_at` |
| `aceita_financiamento` | `accepts_financing` | | | |

E os valores: `VENDA`/`ALUGUEL` → `SALE`/`RENTAL`, `APARTAMENTO` →
`APARTMENT`, `COBERTURA` → `PENTHOUSE`, `CASA` → `HOUSE`, `SALA_COMERCIAL` →
`COMMERCIAL`, `DISPONIVEL` → `AVAILABLE`, `RESERVADO` → `RESERVED`, `VENDIDO`
→ `SOLD`.

Nomes de lugar continuam em português, porque são nomes próprios: `Copacabana`,
`Zona Sul`, `Rio de Janeiro`. O texto dos anúncios (`title`, `description`,
`features`) também, porque é o que o lead lê.

**Isso mudou depois que o `shared/data/imoveis.json` já estava versionado.** Os
140 imóveis são os mesmos, byte a byte nos valores; só as chaves foram
renomeadas. Como o `backend/` ainda estava vazio, nada quebrou, mas o schema do
banco tem de nascer com estes nomes.

## Decisões de arquitetura

### Por que a busca é híbrida e não puramente vetorial

Busca vetorial pura é a escolha errada para imóveis. O embedding de
"até 500 mil" fica perto do de "800 mil", e um imóvel fora do orçamento nunca
deve aparecer só porque a descrição é parecida. O pipeline é:

1. **Filtro estruturado**, eliminatório: preço, quartos, vagas, tipo de negócio,
   região, raio geográfico e rentabilidade. São restrições duras.
2. **Ranking semântico** entre os sobreviventes, por cosseno. É aqui que
   "quero algo silencioso para trabalhar de casa" encontra "escritório, sol da
   manhã, rua tranquila".
3. **Boosting** explicável: bairro exato, quartos exatos, aproveitamento do
   orçamento, rentabilidade acima da esperada.
4. **Relaxamento progressivo** quando nada bate.

### Relaxamento progressivo

Devolver lista vazia é a pior resposta possível para um lead. Quando o filtro
duro não devolve nada, o retriever afrouxa uma restrição por vez, na ordem que
menos machuca o lead: orçamento +15% → um quarto a menos → bairro vira zona →
raio dobrado → características viram preferência → rentabilidade −1pt →
orçamento +40% → sai da região → quartos flexíveis → último recurso.

Cada nível é calculado a partir do pedido **original**, não do estado anterior,
para que o rótulo enviado ao LLM seja literalmente verdadeiro.

### Relaxamento ≠ desvio

Distinção que custou um bug: a escada de relaxamento é um fato sobre a *busca*,
não sobre o *resultado*. Ela pode subir cinco degraus e os imóveis devolvidos
ainda respeitarem tudo o que o lead pediu — isso acontece quando a escada foi
acionada só para completar o `top_k`.

Por isso `SearchResult` tem dois campos:

- `relaxations`: degraus percorridos (diagnóstico, dashboard);
- `mismatches`: como os imóveis **devolvidos** diferem do pedido, restrição por
  restrição. É esse que vai para o prompt.

Sem essa separação o agente dizia ao lead "precisei ampliar seu orçamento"
quando não tinha precisado. Coberto por
`TestMismatches.test_mismatch_does_not_mention_a_respected_constraint`.

### Explicabilidade

Todo resultado carrega um campo `reason` em texto natural, e todo resultado que
foge do perfil carrega `mismatches`. Servem a três leitores: o LLM, que parafraseia
para o lead; o corretor, no dashboard; e nós, quando o ranking sair estranho.
É explicabilidade barata, no espírito do que a Aula 04 de Privacidade pede de
sistemas de IA ("caixas pretas" e o direito a explicação sobre decisão
automatizada).

### Zero dependências no caminho local

`HashingEmbedder` é uma projeção lexical determinística (hashing de unigramas e
bigramas, no espírito do `HashingVectorizer`), sem rede e sem pacotes. Isso faz
os testes rodarem em qualquer máquina do grupo sem instalar nada e sem gastar
cota de API.

Ele **não é semântico**, e isso é uma limitação honesta: acerta bem em
"apartamento 3 quartos zona sul", porque o vocabulário do domínio é pequeno e
repetitivo, e erra em sinônimos ("perto do mar" × "vista para a praia"). O
`GeminiEmbedder` (`gemini-embedding-001`) é o caminho de qualidade; a interface
é a mesma e a troca é um parâmetro.

Usa `hashlib`, não o `hash()` embutido, porque o hash de strings do Python é
randomizado por processo e o índice salvo em disco deixaria de bater com as
consultas da execução seguinte.

### Memória: por que o perfil é monotônico

A extração da Pessoa 1 é **sem estado**. Ela roda regex sobre a conversa
concatenada a cada chamada, então um campo já descoberto volta a `"undefined"`
quando o texto cresce e o padrão deixa de casar. Dá para ver isso na
`demo_memory.py`: o nome é extraído no turno 1 e some nos turnos 2, 3 e 4.

Aqui o perfil só muda de um valor conhecido para outro valor conhecido. Valor
desconhecido nunca sobrescreve. Quando o valor muda de verdade, a alteração é
registrada como `correcao`, porque "o lead subiu o orçamento de 1.5m para 2m" é
sinal de compra que o corretor quer ver, não ruído.

### Pseudonimização, não anonimização

O Gemini é um terceiro, e hoje o `agent.py` manda a conversa inteira para ele,
nome, e-mail e telefone junto. `src/privacy.py` substitui isso por apelidos
antes do envio e desfaz na volta:

```
lead escreve   Oi! Meu nome é João Pereira, quero comprar
vai ao LLM     Oi! Meu nome é [NOME_1], quero comprar
LLM responde   Prazer, [NOME_1]! Qual região você procura?
lead recebe    Prazer, João! Qual região você procura?
```

O mapa nunca sai da máquina. Anonimizar (apagar) estragaria a conversa
humanizada, que é critério de avaliação; pseudonimizar preserva as duas coisas.

Três detalhes que custaram bug:

1. **O nome é detectado na própria mensagem de apresentação.** Mascarar só o
   que já está no perfil deixava o nome vazar em claro exatamente no turno em
   que ele é revelado.
2. **Todas as variantes do nome dividem um apelido.** "João Pereira", "João" e
   "Pereira" viravam `[NOME_1]`, `[NOME_2]` e `[NOME_3]`, e o LLM concluía que
   eram três pessoas.
3. **A memória extrai e-mail, telefone e nome do texto original.** Como o LLM
   recebe texto mascarado, o regex da Pessoa 1 fica cego justamente para os
   campos de PII e devolve `"undefined"`.

### Resumo e follow-up: degradação graciosa

Os dois módulos **nunca falham por falta de chave**. Sem `GEMINI_API_KEY`, sem
rede ou com a API fora do ar, um caminho heurístico determinístico assume. Não é
preciosismo: o resumo aparece no dashboard, e se a chamada cair durante a
apresentação é melhor um resumo mais pobre do que uma tela de erro.

Todo resultado carrega `source` (`"llm"` ou `"heuristic"`), e o card do
dashboard imprime "Resumo gerado por regras (IA indisponível)". A interface
nunca apresenta texto de regra como se fosse texto de IA.

### Por que o score de lead é regra, e não modelo

O score de temperatura é deliberadamente baseado em sete fatores explícitos, e
não num Isolation Forest. Com dezenas de leads sintéticos, um modelo daria a
aparência de rigor sem o rigor: as próprias aulas de Detecção de Anomalias
insistem que o problema central é a definição do limiar e a validação com dados
realistas, e que validar sobre dados sintéticos compromete a confiabilidade.

Uma regra auditável ganha em três frentes: o corretor vê **por que** o lead está
quente (`fatores` lista cada parcela), o comportamento é reproduzível, e é
defensável na banca. Trocar por um modelo depois é substituir `compute_score`.

O LLM opina sobre texto; a priorização continua sendo da regra.

### Follow-up: a metade que evita virar spam

A parte fácil é a cadência (24h, 3 dias, 7 dias, e para; lead com urgência alta
usa 4h, 24h, 72h). A parte que costuma faltar é quando **não** enviar:

- sem consentimento registrado, não envia (LGPD art. 8º);
- se o lead pediu para parar ("já comprei", "não tenho interesse"), bloqueia
  para sempre. `detectar_opt_out` varre só as mensagens do lead, para que a
  frase do próprio agente não dispare o bloqueio;
- se o lead respondeu depois do último follow-up, a régua **volta ao início**:
  ele está conversando, não fugindo;
- atingido o teto, encerra e devolve o lead ao corretor.

`followups_enviados` conta consecutivos sem resposta e zera quando o lead volta
a falar; `followups_total` guarda o histórico para relatório.

Detalhe que quase virou bug: a mensagem de follow-up entra no histórico como
fala do agente. Se isso contasse como interação, o contador de silêncio zerava e
o segundo follow-up nunca chegaria. Por isso a memória tem duas datas
(`ultima_interacao_em` para retenção, `ultima_mensagem_lead_em` para silêncio).

### LGPD, item a item

| Princípio ou direito | Onde |
|---|---|
| Consentimento (art. 8º) | `registrar_consentimento()` / `tem_consentimento()` |
| Necessidade e Minimização | pseudonimização antes da chamada ao LLM |
| Direito de acesso e portabilidade | `export()` |
| Direito de exclusão | `forget()`, apaga inclusive o mapa de apelidos |
| Limitação de Armazenamento | `purge_expired()`, retenção de 180 dias |
| Segurança | `validate_lead_id()` contra path traversal; escrita atômica |
| Dados sintéticos | base de imóveis gerada, sem dado real |
| Transparência e explicabilidade | `reason`, `mismatches`, log sem PII |

## Relação com as disciplinas da fase

### Análise de Documentos com Azure (Aula 04, Cognitive Search)

O projeto não usa Azure (decisão do grupo: sem conta). O desenho do índice,
porém, segue o esquema da aula, e `schema.AZURE_FIELD_MAP` documenta campo a
campo como cada um seria declarado. A migração seria tradução, não redesenho:

| Aqui | Azure Cognitive Search |
|---|---|
| `schema.AZURE_FIELD_MAP` | `SimpleField` / `SearchableField` com `searchable`, `filterable`, `sortable`, `facetable` |
| `indexer.build_index()` | `search_client.upload_documents()` |
| `indexer.reindex()` | indexador agendado (`schedule="*/15 * * * *"`) |
| `retriever.apply_filters()` | `filter="price lt 500000 and bedrooms ge 3"` |
| `retriever.distance_km()` | `geo.distance(location, geography'POINT(lon lat)') le 4` |
| `indexer.Index.facets()` | `facets=["zone"]` |
| `schema.TEXT_WEIGHTS` | boosting por campo, `search_fields=["title^3", "description"]` |

### Privacidade e Proteção de Dados

- **Dados sintéticos.** A Aula 04 lista geração de dados sintéticos como técnica
  de proteção de privacidade. Não há um único imóvel, endereço ou proprietário
  real na base.
- **Explicabilidade.** `reason` e `mismatches`, acima.
- **Pseudonimização, retenção, consentimento, exclusão e portabilidade.** Ver a
  tabela LGPD acima.

## Contrato de integração

### Com a Parte 1 (agente)

O retriever consome exatamente o `dados_coletados` que `chamar_agente()`
devolve:

```python
from rag import indexer, retriever

index = indexer.load_index()                   # ou build_index(...)
result = retriever.search_for_lead(
    index,
    resposta_do_agente["dados_coletados"],     # dict da Pessoa 1, cru
    query_text=mensagem_do_lead,
    top_k=3,
)
bloco = retriever.format_for_prompt(result)    # texto pronto para o prompt
```

`format_for_prompt()` produz o bloco a ser injetado no prompt do LLM, com os
ids explícitos e instrução anti-alucinação. Sugestão de mudança mínima no
`agent.py`: um parâmetro opcional `contexto_extra` em `chamar_agente()`,
concatenado ao `SYSTEM_PROMPT`.

Valores `"undefined"` são tratados como "não informado" e não viram filtro.

Com a memória, o turno completo fica assim, e o par `start_turn` /
`finish_turn` garante que mascarar e restaurar nunca se separem:

```python
from memory.conversation_memory import ConversationMemory, JsonFileStore

memory = ConversationMemory(JsonFileStore("dados/memoria"))

turn = memory.start_turn(lead_id, mensagem_do_lead)

resultado = chamar_agente(
    turn.message,              # pseudonimizada
    turn.history,              # pseudonimizado
    lead_id,
    contexto_extra=turn.context + "\n" + retriever.format_for_prompt(result),
)

resposta, alteracoes = memory.finish_turn(
    lead_id, turn, resultado["resposta"], resultado["dados_coletados"]
)
# `resposta` já está restaurada e é o que vai para o lead.
```

### Com a Parte 3 (backend)

- `shared/data/imoveis.json` é a base para carregar no banco.
- `shared/schemas/imovel_schema.json` é o JSON Schema do documento.
- Todos os resultados têm `to_dict()` serializável em JSON direto:
  `SearchResult`, `RecommendedProperty`, `BrokerSummary`, `FollowUp`,
  `FollowUpDecision`.
- Para trocar o store de memória por banco, basta implementar quatro métodos
  (`ler`, `escrever`, `remover`, `listar`) e passar a instância ao construtor.
  Nada mais no módulo muda.

O job de `backend/src/jobs/followup_scheduler.py` fica assim:

```python
from followup import FollowUpGenerator, leads_due_for_followup

generator = FollowUpGenerator()

for lead_id, decision in leads_due_for_followup(memory):
    followup = generator.send(memory, lead_id, new_properties=rag_search(lead_id))
    if followup:
        despachar(followup.channel, lead_id, followup.text)
```

`leads_due_for_followup()` já aplica consentimento, opt-out, cadência e teto, e
vem ordenado do mais silencioso ao menos. O scheduler não precisa reimplementar
nenhuma regra; quando pula um lead, `decision.reason` explica por quê, em texto,
pronto para log.

E o dashboard (Parte 4):

```python
from summarizer import Summarizer, summarize_pipeline

summarize_pipeline(memory)                                  # lista, ordenada por score
Summarizer().summarize_for_broker(memory, lead_id)          # card completo, com IA
```

## Achados no código da Parte 1

Encontrados ao escrever o parser de perfil. Não mexi no `ai-core`, é território
da Pessoa 1:

1. **`extrair_preco` perde a escala de milhão.** `"até 1.5m"` retorna `"1.5k"`,
   porque a f-string tem `k` fixo. O teste passa porque só assere `"1" in
   resultado`.
2. **`extrair_preco` monta a faixa com `set()`**, cuja ordem de iteração de
   strings varia entre execuções do Python. A mesma frase pode virar
   `"500k-800k"` ou `"800k-500k"`. Meu `parse_price_range` ordena os valores, e
   há teste garantindo que as duas formas dão o mesmo resultado.
3. **`extrair_regiao` só conhece uma lista fixa de localidades.** Ela e o
   `schema.BAIRROS` daqui precisam andar juntas, senão o lead informa uma região
   sem estoque. O catálogo canônico está em `src/rag/schema.py`.
4. **Model id `gemini-3.6-flash`** em `agent.py` merece conferência contra os
   modelos realmente disponíveis na chave. Este módulo lê o id da variável de
   ambiente `GEMINI_MODEL` (`src/llm.py`); a sugestão é o grupo fixar UM id
   verificado nessa variável e o `agent.py` passar a lê-la também, para não
   existirem dois ids diferentes no projeto.
5. **`extrair_urgencia` nunca devolve `"undefined"`**: sem palavra-chave, cai em
   `"baixa"`. Como `avaliar_status_qualificacao` conta campos preenchidos, todo
   lead começa com um campo a mais do que realmente tem. O score deste módulo
   não usa aquela contagem, mas vale corrigir na origem.

## Decisões de prompt

A disciplina "IA: Guia de Prompts" não tem material escrito, então as escolhas
seguem prática padrão e ficam registradas aqui e nos comentários acima de cada
prompt:

- papel e tarefa explícitos na primeira linha;
- formato de saída declarado como JSON e **repetido no fim**, que é onde o
  modelo mais presta atenção;
- proibição explícita de inventar. Resumo e follow-up são onde alucinação causa
  mais estrago: o corretor liga acreditando num dado que o lead nunca disse;
- instrução para deixar campo vazio quando não souber, em vez de chutar;
- os apelidos `[NOME_1]` são explicados no prompt, senão o modelo tenta
  "corrigi-los" para nomes inventados;
- no follow-up, limite de tamanho agressivo (3 linhas, é WhatsApp) e no máximo
  UMA pergunta, para o lead ter resposta fácil de dar;
- o tom do follow-up entra como instrução completa por tentativa, e não como
  adjetivo solto, senão o modelo escreve três vezes a mesma mensagem.

Do lado do parsing, `llm.extrair_json` é tolerante de propósito: aceita cerca de
markdown e texto em volta, porque é assim que modelos reais respondem. Resposta
irrecuperável cai na heurística em vez de quebrar.

## Ajuste fino do ranking

Os pesos ficam todos no topo de `retriever.py`. Os dois que mais mudam
comportamento:

- `BONUS_BUDGET_USAGE` (0.20): entre opções dentro do teto, prefere a que usa
  mais do orçamento. Premiar o mais barato faria o agente oferecer um studio de
  200 mil a um investidor com ticket de 1 milhão.
- `PENALTY_MISSING_BEDROOM` (0.25): maior que o anterior de propósito. Faltar
  quarto é necessidade não atendida; aproveitar orçamento é preferência.
