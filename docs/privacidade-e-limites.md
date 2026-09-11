# Privacidade: o que o sistema faz, e até onde ele faz

Este documento existe para o grupo conseguir responder, sem improvisar, a
pergunta que uma banca faz sobre qualquer projeto que manda texto de usuário
para uma API de terceiro: **o que sai daqui, e o que fica?**

Ele descreve o que está implementado e, com o mesmo cuidado, **o que não
está**. Declarar o limite vale mais que a proteção em si: uma proteção
apresentada como maior do que é vira um problema quando alguém a testa.

---

## O que sai do sistema

Toda mensagem do lead passa por pseudonimização antes de chegar ao Google
Gemini. Nome, e-mail e telefone são trocados por apelidos:

```
ORIGINAL : meu nome é Marcos, email marcos@teste.com, telefone 21 99999-4410,
           quero 3 quartos em Botafogo
ENVIADO  : meu nome é [NOME_1], email [EMAIL_1], telefone [TELEFONE_1],
           quero 3 quartos em Botafogo
```

Repare que o que interessa para achar imóvel ("3 quartos em Botafogo")
atravessa intacto: a pseudonimização não custa qualidade de resposta.

São **três** os caminhos que falam com o Gemini, e os três recebem o texto
mascarado:

| Caminho | O que manda | Onde |
| --- | --- | --- |
| Geração da resposta | a mensagem mascarada, mais o histórico mascarado | `ai-core/src/agent.py` |
| Busca de imóveis (embedding) | a mensagem mascarada | `backend/src/services/ai_service.py`, em `_buscar` |
| Resumo e follow-up | o perfil e o histórico mascarados | `ai-memory-rag/src/summarizer.py`, `followup.py` |

A busca por embedding foi o último a ser corrigido: ela mandava a mensagem
**crua**, e era o único ponto do sistema que furava a pseudonimização.
`backend/tests/test_smoke.py`, bloco 14, é o teste que impede a regressão.

O caminho de volta desfaz a troca antes de o lead ler, e apelidos que o modelo
inventou (ou usou antes de existir valor) são removidos junto com a pontuação
que só existia por causa deles, para o lead não receber `Entendido, !`.

---

## O que NÃO está protegido, e por quê

Quatro limites reais. Nenhum deles é acidente: são decisões de escopo de um
trabalho acadêmico, e é assim que devem ser apresentados.

### 1. A detecção de dado pessoal é por regex, não por modelo

`ai-memory-rag/src/privacy.py` acha e-mail, telefone, CPF e nomes por padrão de
texto. Isso cobre bem o formato brasileiro comum e **deixa passar**:

- nome escrito em minúscula (`sou marcos`), porque a detecção de nome exige
  inicial maiúscula para não confundir qualquer palavra com nome próprio;
- nome estrangeiro ou composto fora dos padrões esperados;
- endereço residencial completo, que não é procurado;
- qualquer dado pessoal que o lead escreva de forma livre ("moro no prédio
  amarelo em frente à padaria do Zé").

O trade-off é conhecido: uma detecção mais agressiva mascararia bairro e nome
de imóvel, e o agente perderia a informação de que precisa para responder.

### 2. O mapa de apelidos fica no mesmo lugar que o texto

O `alias_map` que traduz `[NOME_1]` de volta para "Marcos" é gravado **na mesma
linha** do banco em que está a conversa pseudonimizada. Quem tem acesso ao
arquivo `backend/data/app.db` reverte tudo com uma consulta.

Isso é proposital e é o que a proteção se propõe a fazer: **ela protege contra
o terceiro (o Google), não contra quem tem o banco.** Um sistema de produção
guardaria o mapa em outro armazenamento, com outra chave de acesso.

### 3. Não há autenticação

O painel do corretor e o chat do lead são a mesma aplicação, no mesmo endereço,
sem login. O `lead_id` é o único identificador, e ele vem do próprio cliente.

Duas coisas reduzem o risco:

- o `lead_id` deixou de ser sequencial (`lead-0001`) e passou a ser
  `lead-a3f1c8d902b45e17`, com 16 dígitos hexadecimais. Com o id sequencial,
  trocar um dígito em `/leads/{id}/exportar` devolvia nome, telefone e a
  conversa inteira de outra pessoa, e `DELETE /leads/{id}` apagava os dados
  dela;
- `VITE_MODO_CORRETOR=false` esconde as abas do painel, deixando só o chat.

**Esconder a navegação não é autenticação**, e as rotas continuam existindo.
O projeto roda em localhost e foi pensado para isso.

### 4. A conversa vai para um terceiro, e o lead é informado disso

O Google Gemini processa o texto. O lead vê o aviso na primeira mensagem, sem
caixa para marcar: é informação (LGPD art. 9º), não consentimento. O
consentimento, separado, é só para ser procurado depois do chat, e a caixa
nasce **desmarcada** de propósito.

---

## Direitos do titular, e onde estão na tela

| Direito | Onde | O que faz |
| --- | --- | --- |
| Acesso (art. 18, II) | Chat, card **Meus dados** → Baixar | JSON com tudo que o sistema guarda |
| Exclusão (art. 18, VI) | Chat, card **Meus dados** → Excluir | Apaga a linha do lead, as mensagens, os agendamentos e a memória da IA |
| Revogação (art. 8º, §5º) | Chat, card **Meus dados** → Não quero mais ser procurado | Desliga o follow-up sem apagar a conversa |

A revogação **não pede confirmação** de propósito: uma caixa de diálogo no
caminho de quem quer sair é atrito colocado a mão, e é exatamente o que a lei
não admite. Excluir pede, porque é destrutivo e irreversível.

Existe também a **retenção**: a memória da IA tem prazo, e o `purgar_expirados`
roda no boot do backend, apagando as duas representações (memória da IA e
tabelas relacionais) dos leads fora do prazo. Até pouco tempo atrás a função
existia e nunca era chamada, o que é o mesmo que não existir.

---

## Como demonstrar em 60 segundos

1. Abra o chat e mostre o aviso de transparência, abaixo da caixa de
   consentimento.
2. Mande `meu nome é Marcos, telefone 21 99999-4410, quero 3 quartos em
   Botafogo` e mostre no terminal do backend que o que sai é o texto com
   apelidos.
3. Clique em **Baixar meus dados** e abra o JSON.
4. Clique em **Não quero mais ser procurado**: sem confirmação, sem atrito.
5. Diga o limite antes de perguntarem: *"o mapa de apelidos fica no mesmo
   banco, então isso protege contra o Google, não contra quem tem o banco"*.

O bloco 16.1 do `roteiro-de-teste-manual.md` tem a versão com checkboxes.
