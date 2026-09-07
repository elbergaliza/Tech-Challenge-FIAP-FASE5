# Integração Frontend → Backend

O que a Parte 4 precisa chamar. Backend em `http://localhost:8000`, docs
interativas (com "try it out") em `/docs`.

Sobe com `python backend/run.py`. **Não precisa de chave de API** — sem
`GEMINI_API_KEY` o chat responde por um agente mock e todas as rotas funcionam
igual, então dá para construir a tela inteira antes de a Parte 1 estar pronta.

CORS já está liberado para `localhost:5173` (Vite) e `localhost:3000` (CRA/Next).
Outra porta, é só acrescentar em `CORS_ORIGINS` no `.env` da raiz.

---

## As duas telas

### Tela 1 — Chat (o lead conversando)

**Só uma rota importa: `POST /chat`.**

```ts
const r = await fetch("http://localhost:8000/chat", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    lead_id: localStorage.getItem("lead_id"),  // null na 1ª mensagem
    mensagem: texto,
    consentimento: true,                        // só na 1ª (LGPD)
  }),
});
const turno = await r.json();
localStorage.setItem("lead_id", turno.lead_id); // guarde o id que voltou
```

Sem `lead_id`, o backend cria o lead e devolve o id gerado (`lead-0001`). Guarde
e mande nas próximas. É assim que um visitante anônimo entra no funil.

A resposta traz tudo que a tela precisa, num payload só:

```jsonc
{
  "lead_id": "lead-0001",
  "resposta": "E a urgência, precisa para logo?",   // a bolha do agente
  "status": "EM_ANDAMENTO",
  "score": 55,
  "temperatura": "WARM",
  "temperatura_label": "MORNO",                      // já em português

  "perfil": {                                        // o que a IA sabe até agora
    "intent": "RENT", "region": "Botafogo",
    "bedrooms": "2", "price_range": "5 mil", "name": "Bruna"
  },

  "novidades": [                                     // o que ela aprendeu NESTE turno
    { "field": "region", "from": null, "to": "Botafogo", "kind": "new" }
  ],

  "imoveis": [                                       // sugestões do RAG
    { "id": "IMV-0086", "title": "Apartamento de 2 quartos em Botafogo",
      "neighborhood": "Botafogo", "price": 3700, "bedrooms": 2, "area_m2": 56.7,
      "reason": "no bairro pedido, exatamente 2 quartos, dentro do orçamento" }
  ],

  "proxima_acao": "Pedir telefone ou e-mail antes de avançar",
  "origem": "gemini",                                // ou "mock"
  "sugerir_agendamento": false
}
```

**Três campos que valem virar UI:**

- **`novidades`** — pisque um chip "anotei: Botafogo" ao lado da mensagem. É a
  prova visual, na tela, de que o agente tem memória. `kind` é `"new"` ou
  `"correction"` (o lead mudou de ideia); vale um ícone diferente pra cada.
- **`imoveis`** — renderize como cards abaixo da resposta. O `reason` já vem
  escrito em português explicando por que aquele imóvel foi escolhido; é o que
  faz o RAG parecer inteligente em vez de aleatório.
- **`sugerir_agendamento`** — quando virar `true`, mostre o botão "Agendar
  visita". Significa que o perfil está completo.

`origem: "mock"` significa que está rodando sem chave. Vale um badge discreto no
canto durante o desenvolvimento, pra ninguém se assustar com respostas robóticas.

**Reabrir uma conversa:** `GET /chat/{lead_id}/historico` devolve as mensagens
em ordem (`papel` é `"user"`, `"assistant"` ou `"followup"`).

---

### Tela 2 — Dashboard (o corretor)

**`GET /dashboard/summary`** — os números da tela inicial, tudo pronto:

```jsonc
{
  "total_leads": 12, "leads_quentes": 3, "leads_mornos": 5, "leads_frios": 4,
  "qualificados": 6, "aguardando_followup": 2,
  "agendamentos_proximos": 4, "agendamentos_hoje": 1,
  "total_mensagens": 87, "total_imoveis": 140,
  "taxa_qualificacao": 50.0, "score_medio": 47.3,
  "por_status":   [{ "chave": "QUALIFICADO", "label": "Qualificado", "total": 6 }],
  "por_intencao": [{ "chave": "RENT", "label": "aluguel", "total": 7 }],
  "por_regiao":   [{ "chave": "Botafogo", "label": "Botafogo", "total": 3 }],
  "ultimos_leads": [ /* 5 leads no mesmo formato de GET /leads */ ]
}
```

Os `por_*` já vêm ordenados por total e com `label` em português — dá pra jogar
direto num gráfico de barras sem tratar nada.

**`GET /leads`** — a lista. Ordenada por **score desc** por padrão, porque a
pergunta do corretor ao abrir a tela é "quem eu ligo agora", não "quem chegou por
último".

```
GET /leads?temperatura=HOT&status=QUALIFICADO&intencao=BUY&busca=maria
          &ordenar_por=score|criado_em|ultima_mensagem_em&limite=50&offset=0
```

Cada item traz `intencao_label`, `urgencia_label`, `temperatura_label` e
`status_label` já traduzidos, mais `total_mensagens` e `horas_sem_resposta`. Não
monte tabela de tradução no front — se traduzir de novo aí, as duas saem de
sincronia na primeira mudança.

**`GET /leads/{id}`** — a tela de detalhe. Traz `mensagens`, `agendamentos` e
`resumo_ia`, que é o card do corretor gerado pela Parte 2:

```jsonc
"resumo_ia": {
  "temperature": "HOT", "temperature_label": "QUENTE", "score": 78,
  "summary": "Lead procura apartamento de 2 quartos em Botafogo para alugar...",
  "next_action": "Ligar hoje: lead quente com contato disponível",
  "main_interest": "...",
  "buying_signals": ["mencionou urgência", "pediu para ver opções"],
  "objections": ["achou o condomínio caro"],
  "alerts": ["sem telefone há 3 mensagens"],
  "shown_properties": ["IMV-0086", "IMV-0067"]
}
```

`next_action`, `alerts` e `buying_signals` são as três coisas mais úteis pro
corretor — merecem destaque, não um `<pre>` no rodapé.

> `?resumo_ia=true` regera o resumo com LLM (texto melhor, **gasta cota**). O
> padrão é o heurístico, que já vem pronto e não custa nada. Não ligue isso no
> `useEffect` de uma lista.

**`PATCH /leads/{id}`** — o corretor corrigindo algo. Parcial: mande só o que
mudou, `{"status": "DESCARTADO"}` não apaga o telefone.

---

## Agendamento

```ts
POST /leads/{lead_id}/schedule
{ "data_hora": "2026-09-10T15:00:00Z",   // ISO 8601
  "tipo": "VISITA",                       // VISITA | REUNIAO | CONSULTORIA
  "imovel_id": "IMV-0086",                // opcional
  "corretor": "Ana" }
```

Promove o lead para `AGENDADO` automaticamente. Use `CONSULTORIA` quando a
intenção for `INVEST` — o fluxo de investimento termina em conversa com
especialista, não em visita a imóvel.

- `GET /schedule?proximos_dias=7` — a agenda, ordenada da próxima para a última.
  Cada item já vem com `lead_nome` e `imovel_titulo` (não precisa buscar os dois).
- `PATCH /schedule/{id}` — remarcar (`data_hora`) ou mudar status
  (`REALIZADO` / `CANCELADO` / `NAO_COMPARECEU`). Cancelar a única visita devolve
  o lead para `QUALIFICADO`, pra ele não sumir das listas de quem precisa de
  atenção.

## Catálogo de imóveis

```
GET /imoveis?deal_type=RENTAL&neighborhood=Botafogo&bedrooms_min=2
            &preco_min=2000&preco_max=6000&limite=24&offset=0
→ { "total": 37, "limit": 24, "offset": 0, "items": [...] }
```

`GET /imoveis/filtros` devolve os valores disponíveis (bairros, zonas, tipos,
faixa de preço) — use isso pra montar os selects em vez de chumbar arrays no
código, senão eles saem de sincronia com a base.

## Follow-up

`GET /dashboard/followups` — quem parou de responder e merece retomada, do mais
silencioso ao menos. Vira uma seção "precisam de atenção" no dashboard.

Com `?com_texto=true` vem também o `texto_sugerido` que o agente escreveria —
**gasta uma chamada de LLM por lead**, então chame só quando o corretor abrir o
item, nunca no carregamento da lista.

Um job roda a cada 30 min e retoma esses leads sozinho; as mensagens dele
aparecem no histórico com `papel: "followup"`. Vale distinguir visualmente das
respostas normais: são iniciativa do agente, não resposta a nada.

## LGPD

- `DELETE /leads/{id}` — apaga o lead **e** a memória da IA. Um botão "excluir
  meus dados" no chat resolve o direito de exclusão.
- `GET /leads/{id}/exportar` — tudo que o sistema guarda do lead, num JSON. É o
  direito de acesso; dá pra oferecer como download.

---

## Detalhes que economizam tempo

**Erros.** Todos vêm no formato do FastAPI: `{ "detail": "..." }`. Os que você
vai encontrar: `404` (lead/imóvel não existe), `422` (mensagem vazia, `lead_id`
inválido, imóvel inexistente no agendamento). O `detail` do 422 é uma lista de
objetos, não uma string — não jogue direto num `<div>`.

**Datas** saem em ISO 8601 UTC (`2026-09-06T18:30:00+00:00`). Converta pro fuso
local na hora de exibir.

**Nada de auth.** É um POC: não há login nem token. Se a demo precisar de "qual
corretor está logado", mande o nome no campo `corretor` do agendamento.

**Latência do chat.** Com Gemini de verdade, um turno leva de 2 a 6 segundos
(chamada ao LLM + busca no RAG). Mostre um indicador de "digitando" — sem ele a
tela parece travada. A primeira mensagem depois de subir o backend pode demorar
mais: é quando o índice do RAG é construído.

**Estados vazios.** Todas as listas devolvem `[]` e o `/dashboard/summary`
devolve zeros quando a base está vazia — nada de `null` ou 500. Mas vale desenhar
o estado vazio, porque é exatamente o que aparece no primeiro boot.

Qualquer dúvida sobre um campo, `/docs` tem o schema completo de todas as rotas e
deixa testar na hora.
