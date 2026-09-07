# Backend — Parte 3

API FastAPI que orquestra o agente de conversa (`ai-core`, Parte 1) e a
memória/RAG (`ai-memory-rag`, Parte 2), e serve o dashboard (Parte 4).

As duas partes de IA são **importadas como módulo**, não chamadas por HTTP. É a
razão de o backend ser Python: não há serialização entre serviços, não há uma
segunda porta para subir, e a demo roda em um processo só.

## Subir

```bash
pip install -r backend/requirements.txt
python backend/run.py
```

Docs interativas: <http://localhost:8000/docs>

**Não precisa de chave de API para rodar.** Sem `GEMINI_API_KEY` o backend sobe
completo, com todas as rotas: o chat responde por um agente mock que persegue o
próximo campo faltante, o RAG usa o embedder lexical offline e o resumo do
corretor usa heurística. `GET /health` diz o que está ligado de verdade.

Com chave, crie um `.env` **na raiz do repositório** (não dentro de `backend/`),
usando `backend/.env.example` como modelo — a raiz é onde as três partes
procuram, então um arquivo só serve para todas.

## Rotas

| Método | Rota | O que faz |
|---|---|---|
| `POST` | `/chat` | Turno de conversa. Sem `lead_id`, cria o lead e devolve o id |
| `GET` | `/chat/{lead_id}/historico` | Histórico, para o front repopular a tela |
| `GET` | `/leads` | Lista do dashboard. Filtra por status, temperatura, intenção, busca |
| `POST` | `/leads` | Cadastro manual, para lead que chegou por fora do chat |
| `GET` | `/leads/{id}` | Detalhe + histórico + agendamentos + card do corretor |
| `PATCH` | `/leads/{id}` | Atualização parcial (o corretor corrigindo algo) |
| `DELETE` | `/leads/{id}` | Exclusão LGPD: apaga o lead **e** a memória da IA |
| `GET` | `/leads/{id}/exportar` | Direito de acesso LGPD: tudo que se guarda do lead |
| `GET` | `/imoveis` | Catálogo com filtros e paginação |
| `GET` | `/imoveis/filtros` | Valores disponíveis, para o front não chumbar listas |
| `GET` | `/imoveis/{id}` | Detalhe do imóvel |
| `POST` | `/leads/{id}/schedule` | Agenda visita/reunião e promove o lead a AGENDADO |
| `GET` | `/schedule` | Agenda do corretor (`?proximos_dias=7`) |
| `PATCH` | `/schedule/{id}` | Remarcar, cancelar, marcar como realizado |
| `DELETE` | `/schedule/{id}` | Remover agendamento |
| `GET` | `/dashboard/summary` | Números agregados da tela inicial |
| `GET` | `/dashboard/followups` | Quem merece follow-up agora (decidido pela Parte 2) |
| `GET` | `/health` | Banco, agente, LLM e RAG — o que está realmente ativo |

### O que o `POST /chat` devolve

```jsonc
{
  "lead_id": "lead-0001",
  "resposta": "E a urgência, precisa para logo?",
  "status": "EM_ANDAMENTO", "score": 55,
  "temperatura": "WARM", "temperatura_label": "MORNO",
  "perfil":  { "intent": "RENT", "region": "Botafogo", "bedrooms": "2" },
  "novidades": [ { "field": "region", "from": null, "to": "Botafogo", "kind": "new" } ],
  "imoveis": [ { "id": "IMV-0086", "title": "...", "reason": "no bairro pedido, exatamente 2 quartos, dentro do orçamento" } ],
  "proxima_acao": "Pedir telefone ou e-mail antes de avançar",
  "origem": "gemini",
  "sugerir_agendamento": false
}
```

`novidades` é o que a memória aprendeu **neste turno** — serve para o front
piscar "anotei: 2 quartos" na tela, que é a prova visual de que há memória.
`origem` diz quem respondeu (`gemini` ou `mock`), útil quando a cota acaba no
meio da apresentação.

## Estrutura

```
backend/
  run.py                      entrypoint (resolve sys.path e sobe o uvicorn)
  src/
    bootstrap.py              põe ai-core e ai-memory-rag no path; carrega .env
    config.py                 configuração lida do ambiente
    main.py                   app, CORS, lifespan, /health
    models/                   Lead, Mensagem, Imovel, Agendamento, EstadoConversa
    dto/schemas.py            contratos pydantic da API
    database/
      db.py                   engine, sessão, Base
      memory_store.py         store da memória da Parte 2 sobre o banco
      seed_imoveis.py         importa shared/data/imoveis.json
    services/
      ai_service.py           a cola com a IA (o coração da integração)
      chat_service.py         turno de chat + persistência
      lead_service.py         regras de lead e sincronização com o perfil
    routes/                   chat, leads, imoveis, scheduling, dashboard
    jobs/followup_scheduler.py  job de retomada (APScheduler)
  tests/test_smoke.py         teste de ponta a ponta, sem pytest
```

## Decisões que valem explicar

**Banco: SQLite.** Zero fricção, arquivo em `backend/data/app.db`, criado
sozinho. Trocar por Postgres é mudar `DATABASE_URL` — o código não assume
SQLite em lugar nenhum, só liga `PRAGMA foreign_keys` quando é ele.

**`lead_id` é string (`lead-0001`), não inteiro.** O mesmo id é a chave da
memória da Parte 2, que o valida contra `^[A-Za-z0-9_-]{1,64}$` porque ele
viraria nome de arquivo no store dela. Um inteiro serviria, mas string legível
aparece melhor na tela e na URL da demo.

**A memória da IA mora no mesmo banco.** O `JsonFileStore` da Parte 2 diz, no
próprio docstring, que "para produção a Pessoa 3 implementa a mesma interface
sobre o banco". É o que `database/memory_store.py` faz: quatro métodos, e nada
no módulo dela mudou. Um `docker rm` não come mais o histórico das conversas.

**Perfil na memória, colunas no banco.** A memória é a fonte de verdade do
perfil (é monotônica, distingue correção de novidade, não esquece o que saiu da
janela de contexto). As colunas em `leads` são uma projeção sincronizada a cada
turno, para o dashboard filtrar e ordenar em SQL — com o perfil só dentro de um
blob JSON, "quantos leads quentes" viraria um `for` em Python sobre todos eles.
O que o corretor editou à mão não é sobrescrito por vazio.

**Score e temperatura não são calculados aqui.** Quem calcula é o
`summarizer.compute_score()` da Parte 2, que enxerga silêncio, engajamento e
campos esquivados. As colunas são cache do último cálculo.

**O índice do RAG é construído na primeira mensagem, não no boot.** Indexar os
140 imóveis custa uma chamada de embedding por imóvel; com o embedder do Gemini
isso leva dezenas de segundos, e um healthcheck que expira antes disso mataria
o container antes de a API existir. O `indexer` guarda o índice em disco e só o
refaz quando a base ou o embedder mudam.

**O seed importa `shared/data/imoveis.json` em vez de gerar imóveis próprios.**
O RAG indexa *aquele* arquivo. Se o seed inventasse os seus, o agente
recomendaria um imóvel que o `GET /imoveis/{id}` do dashboard não encontraria —
o tipo de furo impossível de explicar numa demo ao vivo.

**O job de follow-up sobe junto com a API** (a cada 30 min). Quem decide se cabe
retomar não é o job: é o `evaluate_followup` da Parte 2, que pesa consentimento,
opt-out explícito, tentativas anteriores e cadência por temperatura. O filtro é
restritivo (48h de silêncio), então numa base nova o ciclo acorda, não acha
ninguém e dorme — sem custo. Com chave de verdade rodando dias, o consumo é uma
chamada por lead retomado; `--dry-run` mostra quem entraria sem enviar nada, e
`FOLLOWUP_ENABLED=false` desliga.

## Comandos

```bash
# popular imóveis (o startup já faz; útil para repovoar)
python backend/src/database/seed_imoveis.py --limit 30
python backend/src/database/seed_imoveis.py --reset

# follow-up avulso
python backend/src/jobs/followup_scheduler.py --dry-run   # quem seria retomado
python backend/src/jobs/followup_scheduler.py --once      # roda um ciclo

# teste de ponta a ponta (banco temporário, não precisa de chave)
python backend/tests/test_smoke.py
```

Se mudar um modelo depois de o banco existir: apague `backend/data/app.db` e
suba de novo. Não há Alembic — é um POC de hackathon, e migração de verdade não
paga o tempo aqui.

## Combinado com as outras partes

O contrato com a Parte 1 é `chamar_agente(mensagem, historico, lead_id) ->
{resposta, dados_coletados, ...}`. Duas observações da integração real, ambas
herdadas do `run_chat.py` da Parte 2:

1. **`dados_coletados` do agente é ignorado de propósito.** A extração dela é
   regex sobre o texto recebido, e nós prefixamos o contexto e o bloco do RAG na
   mensagem (contorno até `chamar_agente` aceitar um `contexto_extra`). Ela
   extrairia dados dos *imóveis* como se o lead os tivesse dito: o bairro do
   imóvel virava a região desejada, o preço do imóvel virava o orçamento. A
   extração correta acontece antes, sobre a mensagem limpa.
2. **A memória absorve a mensagem antes de o agente pensar sobre ela.** Sem
   isso, o agente decide a próxima pergunta lendo um perfil defasado em um turno
   e repete o que o lead acabou de responder.

O dia em que `chamar_agente` aceitar `contexto_extra=`, o item 1 desaparece —
está marcado com comentário em `services/ai_service.py`.
