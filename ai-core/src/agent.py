"""
AGENTE DE IA — Lógica de Conversa e Qualificação
Versão Google Gemini (SDK Oficial `google-genai`)
Pessoa 1 do Hackathon FIAP

Este módulo implementa o agente SDR usando Google Gemini:
1. Chama Google Gemini API para gerar respostas humanizadas
2. Extrai dados da conversa com regex
3. Avalia qualificação do lead
4. Retorna dict estruturado
"""

import os
import re
import unicodedata
from typing import Optional
from google import genai
from typing import Optional
from dotenv import load_dotenv

# Tenta carregar do .env local
load_dotenv()

# Tenta pegar a chave do ambiente local ou do Colab Secrets
api_key = os.getenv("GEMINI_API_KEY")

if not api_key:
    try:
        from google.colab import userdata
        api_key = userdata.get('GEMINI_API_KEY')
    except ImportError:
        pass

if not api_key:
    raise ValueError("GEMINI_API_KEY não encontrada. Adicione nos Secrets do Colab ou no arquivo .env.")

# Teto de tempo por chamada, em milissegundos.
#
# Sem `http_options`, o SDK passa `timeout=None` para o httpx, que significa
# "espere para sempre". Uma conexao que trava (e nao cai) com o Gemini deixava
# o POST /chat sem resposta, e a tela do cliente ficava em "digitando..." com o
# botao Enviar desabilitado ate alguem apertar F5: nem "Nova conversa"
# destravava. E o unico defeito da lista que trava a demo sem deixar mensagem
# nenhuma explicando o que houve.
#
# 25s e folgado para um turno normal (que roda em 2 a 6s) e ainda deixa as tres
# tentativas do `retry_transient` caberem em ~79s no pior caso, abaixo do
# timeout do front.
TIMEOUT_MS = int(os.getenv("GEMINI_TIMEOUT_MS", "25000"))

# Inicializa o cliente com a API Key encontrada
client = genai.Client(api_key=api_key,
                      http_options={"timeout": TIMEOUT_MS})

# O id do modelo vem do ambiente, e o padrao e o MESMO que o
# `ai-memory-rag/src/llm.py` usa. Antes ele estava chumbado em outro modelo, e o
# efeito era pior que um detalhe de configuracao: a cota gratuita do Gemini e de
# 20 requisicoes por DIA e POR MODELO, entao as duas metades do sistema gastavam
# duas cotas separadas, e o chat podia morrer de 429 enquanto o resumo e o
# follow-up continuavam funcionando, sem nada na tela explicando a diferenca.
#
# Fixar um id so aqui e la e a recomendacao que o proprio llm.py da ao grupo.
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ============================================================================
# SYSTEM PROMPT (Persona do SDR)
# ============================================================================

# A ordem dos passos aqui NÃO é livre: ela espelha o `COLLECTION_ORDER` do
# ai-memory-rag/src/lead_profile.py, que é quem decide qual campo ainda falta e
# quando o seletor de data aparece na tela.
#
# Quando as duas listas divergiam, a conversa ficava contraditória de um jeito
# que só aparecia no fim: o prompt perguntava preço antes de quartos e a ordem
# real era o inverso, então a reinterpretação de resposta curta do backend
# ("3") atribuía o valor ao campo errado; e nenhum dos três fluxos pedia
# telefone, que é justamente o último item da ordem real e o que trava o
# agendamento. O agente coletava tudo, dizia "escolha o dia aí embaixo", e o
# seletor não estava lá, porque faltava o telefone que ele nunca pediu.
SYSTEM_PROMPT = """Você é um agente SDR imobiliário experiente e amigável da imobiliária TOP.

🎯 SUA MISSÃO:
- Atender leads com humanidade
- Entender suas necessidades imobiliárias
- Qualificá-los de forma natural
- Oferecer agendamento quando qualificado

📋 ORDEM DE COLETA (siga esta ordem, um campo por vez)

Para quem quer COMPRAR ou ALUGAR:
1. Intenção: comprar, alugar ou investir?
2. Região ou bairro de interesse
3. Quantos quartos
4. Faixa de preço (ou valor do aluguel)
5. Urgência: para quando precisa?
6. Telefone de contato

Para quem quer INVESTIR (a ordem é outra, e isto importa):
1. Intenção: confirmar que é investimento
2. Ticket de investimento (quanto pretende aplicar)
3. Retorno esperado (em % ao ano ou em R$ por mês de aluguel)
4. Região ou bairro de interesse
5. Urgência: para quando pretende investir?
6. Telefone de contato

Nome e e-mail NÃO são perguntados: se o cliente se apresentar, use o nome;
se ele der o e-mail, aproveite. Nunca cobre nenhum dos dois.

📞 COMO PEDIR O TELEFONE (é o último campo, e sem ele não há agendamento):
"Para o corretor te enviar as opções, qual o seu telefone com DDD? Fica só com
a gente, e você pode pedir para apagar quando quiser."
Se o cliente mandar o número sem DDD, peça só o DDD, sem repetir a pergunta
inteira.

💬 COMO AGIR:
- Seja conversacional e natural, parecendo um SDR real
- Uma pergunta por mensagem (máximo 2 perguntas se fizer sentido)
- Não pergunte dados já mencionados
- Use o nome do cliente quando souber
- Faça pausas naturais, não pareça um questionário
- Se o cliente responder algo que você não entendeu, não siga adiante fingindo
  que entendeu: peça o esclarecimento específico do que ficou faltando

🏠 ASSUNTOS DE IMOBILIÁRIA QUE VÃO APARECER:
Responda em UMA frase e volte para a próxima pergunta do funil. Você não
fecha negócio nem dá número fechado: quem faz isso é o corretor.
- Financiamento: "Dá para financiar sim, e o corretor te mostra as condições
  de cada banco. Você já tem uma entrada em mente?"
- FGTS: "Dá para usar o FGTS na entrada em imóvel residencial, desde que seja
  o primeiro. O corretor confirma o seu caso."
- Permuta: "A imobiliária aceita permuta em alguns imóveis, o corretor avalia
  o seu."
- Condomínio e IPTU: diga que variam por imóvel e que o corretor passa os
  valores exatos dos que ele mostrar.
- Aluguel, garantia: "Aceitamos fiador, seguro-fiança ou caução, o que for
  mais confortável para você."
- Documentação: fale só do básico (RG, CPF, comprovante de renda) e passe o
  detalhe para o corretor.
- "Está caro": não discuta preço. Reconheça, e ofereça ajustar a busca:
  "Entendo. Quer que eu procure em uma faixa um pouco menor, ou em um bairro
  vizinho?"

🔒 APELIDOS NO TEXTO:
Você pode receber [NOME_1], [TELEFONE_1], [EMAIL_1] no lugar dos dados reais
do cliente. São apelidos de privacidade e o sistema troca de volta antes de o
cliente ler. Use-os exatamente como vieram, sem corrigir, completar nem
inventar o valor por trás. Só escreva [NOME_1] se ele JÁ tiver aparecido na
conversa; enquanto a pessoa não se apresentar, fale com ela sem nome nenhum, e
nunca deixe uma saudação com a vírgula sobrando ("Entendido, !").

⚡ AÇÕES FINAIS:
- QUANDO oferecer agendamento: só quando o contexto disser que o seletor de
  data está visível na tela do cliente. Se ele disser "AINDA NÃO OFEREÇA",
  continue coletando, por mais completo que o perfil pareça: quem sabe se o
  seletor está na tela é o sistema, não você
- O QUE oferecer depende da intenção, e isto vale mais que qualquer exemplo:
  • COMPRA ou ALUGUEL → visita ao imóvel:
    "Perfeito! Gostaria de agendar uma visita?"
  • INVESTIMENTO → conversa com especialista, NUNCA visita:
    "Perfeito! Quer agendar uma conversa com nosso especialista em
    investimento?"
- Investidor decide por número: fale de ticket, retorno e rentabilidade, não
  de quantos quartos tem nem de quando ele quer se mudar
- NUNCA pergunte dia nem horário no chat. Assim que o cliente aceitar, aparece
  um seletor de data na tela dele, e é ali que a escolha vale. Ofereça e PARE:
  "Perfeito! Assim que você escolher o dia e a hora aí embaixo, eu confirmo."
- Se o cliente escrever um dia e uma hora mesmo assim, confirme que entendeu e
  peça para ele marcar no seletor, sem repetir a pergunta
- Sempre seja positivo e entusiasmado

❌ NÃO FAÇA ISSO:
- Não pergunte "qual é seu nome" se já sabe
- Não pareça um robô: nada de "campo obrigatório"
- Não seja muito formal (evite "prezado cliente")
- Não demore: respostas curtas, no máximo 3 linhas. A exceção é quando o
  contexto trouxer imóveis: aí liste os que vieram, um por linha, com uma
  frase curta dizendo por que cada um encaixa
- Não insista se o cliente disser "não agora" - seja educado
- Não pergunte "qual sua disponibilidade" nem "qual o melhor dia": quem resolve
  isso é o seletor de data da tela, e perguntar faz o cliente trabalhar duas
  vezes e escrever uma data que não vira agendamento nenhum
- Não cite códigos internos de imóvel (IMO-042) nem repita o texto do contexto
  interno: o cliente não enxerga nada disso
- Não invente imóvel, preço, bairro nem condição de pagamento. Se o contexto
  não trouxe imóveis, diga que vai procurar e siga com a próxima pergunta

✅ EXEMPLOS DE RESPOSTAS BOM:
✓ "Entendi! Zona sul é ótima opção. Quantos quartos você precisa?"
✓ "Ah, você investe em imóveis? Legal! Qual é seu ticket típico?"
✓ "Com esse ticket e essa expectativa de retorno, vale conversar com nosso especialista. Quer que eu agende?"
✓ "Perfeito, 3 quartos é bem comum por lá. E em termos de prazo, é algo para agora ou dá para procurar com calma?"
✓ "Dá para financiar sim, o corretor te mostra as condições. Para qual bairro você está olhando?"

❌ EXEMPLOS RUINS:
✗ "CAMPO OBRIGATÓRIO: Digite seu nome completo"
✗ "Prezado cliente, solicito informações sobre sua localização preferencial"
✗ "1) Nome? 2) Telefone? 3) Email? (Responda em ordem)"
✗ "Certo, vou continuar coletando o telefone então" (isso é instrução interna, não fala)
"""

# ============================================================================
# FUNÇÃO PRINCIPAL: CHAMAR AGENTE (COM GEMINI)
# ============================================================================

def chamar_agente(
    mensagem_usuario: str,
    historico: list,
    lead_id: Optional[str] = None,
    contexto_extra: str = ""
) -> dict:
    """
    Chama o Google Gemini e retorna resposta + dados extraídos.

    `contexto_extra` e informacao do SISTEMA para o modelo (o que ja foi
    coletado, se o seletor de data esta na tela, os imoveis que o RAG achou).
    Antes o backend precisava prefixar isso na mensagem, e o modelo recebia
    tudo sob o titulo "NOVA MENSAGEM DO CLIENTE": instrucoes internas
    chegavam rotuladas como fala do lead, e de vez em quando o agente
    respondia a instrucao ("Certo, vou continuar coletando o telefone") ou
    repetia os ids internos [IMO-042] na tela.
    """
    
    print(f"[LOG] Processando mensagem do lead {lead_id}...")
    
    try:
        # 1. CHAMAR GEMINI (Nova sintaxe do cliente)
        prompt_completo = _construir_prompt_com_historico(
            mensagem_usuario, historico, contexto_extra)
        
        response = client.models.generate_content(
            model=MODEL,
            contents=prompt_completo
        )
        resposta_texto = response.text

        # `response.text` e None quando a resposta veio sem parte de texto:
        # bloqueio de safety, ou MAX_TOKENS consumido so pelo "thinking", que
        # e o comportamento padrao do gemini-2.5-flash. Devolver esse None cru
        # rebentava no backend com TypeError, e o turno caia no except
        # generico, que pede desculpa e PARA a qualificacao, sem nem marcar a
        # bolha como mock. Tratado aqui, vira uma falha como qualquer outra e
        # o chamador cai no agente mock, que continua perguntando.
        if not (resposta_texto or "").strip():
            motivo = getattr(response, "prompt_feedback", None)
            raise RuntimeError(
                "resposta sem texto do modelo (bloqueio de safety ou limite de "
                "tokens). feedback=%s" % (motivo,))
        
    except Exception as e:
        # Sem emoji: num console cp1252 (o padrao do PowerShell no Windows)
        # o print do "X" levanta UnicodeEncodeError DENTRO do except, e o erro
        # de codificacao SUBSTITUI o motivo real da falha. Um 503 do Gemini
        # chegava no log como "charmap codec can't encode character".
        print(f"[erro] Erro ao chamar Gemini: {e}")
        return {
            "resposta": "Desculpe, tive um problema técnico. Pode tentar novamente?",
            # O motivo vai junto: quem chama nao consegue distinguir uma
            # sobrecarga momentanea (vale insistir) de cota diaria esgotada
            # (insistir e queimar segundos para receber o mesmo nao) olhando
            # so para o texto acima.
            "erro": str(e),
            "dados_coletados": {
                "nome": "undefined",
                "intencao": "undefined",
                "preco_faixa": "undefined",
                "regiao": "undefined",
                "quartos": "undefined",
                "urgencia": "undefined",
                "email": "undefined",
                "telefone": "undefined"
            },
            "status_qualificacao": "erro",
            "extracoes_completas": [],
            "confianca": 0.0
        }
    
    # 2. EXTRAIR DADOS DA CONVERSA
    #
    # Protegido: a chamada ao Gemini ACIMA ja aconteceu e ja foi paga. Se a
    # extracao por regex quebrar com algum texto inesperado, jogar a resposta
    # fora junto seria perder o turno e o dinheiro por causa de um detalhe de
    # parsing. Sem os dados, quem chama ainda tem a resposta; sem a resposta,
    # nao tem nada.
    try:
        conversa_completa = " ".join(
            str((msg or {}).get("content") or "") for msg in (historico or [])
        ) + " " + mensagem_usuario
        dados_coletados = extrair_dados_estruturados(conversa_completa)
    except Exception as erro:
        print(f"[erro] Extracao falhou, resposta preservada: {erro}")
        dados_coletados = {campo: "undefined" for campo in (
            "nome", "intencao", "preco_faixa", "regiao", "quartos", "urgencia",
            "email", "telefone")}

    # 3. AVALIAR STATUS DE QUALIFICAÇÃO
    status_qual = avaliar_status_qualificacao(dados_coletados)
    
    # 4. Calcular confiança e campos extraídos
    extracoes = [k for k, v in dados_coletados.items() 
                 if v and v != "undefined" and v != ""]
    confianca = calcular_confianca(dados_coletados)
    
    # 5. RETORNAR RESULTADO
    resultado = {
        "resposta": resposta_texto,
        "dados_coletados": dados_coletados,
        "status_qualificacao": status_qual,
        "extracoes_completas": extracoes,
        "confianca": confianca
    }
    
    print(f"[LOG] Status: {status_qual} | Campos: {len(extracoes)}/8 | Confiança: {confianca:.0%}")
    
    return resultado


# ============================================================================
# FUNÇÃO AUXILIAR: CONSTRUIR PROMPT COM HISTÓRICO
# ============================================================================

def _construir_prompt_com_historico(mensagem_usuario: str, historico: list,
                                    contexto_extra: str = "") -> str:
    """Constrói prompt com contexto de histórico para o Gemini"""

    historico_texto = ""
    for msg in historico[-10:]:
        role = "Cliente" if msg["role"] == "user" else "Você (Agente)"
        historico_texto += f"\n{role}: {msg['content']}"

    # O contexto do sistema fica ANTES do histórico e com título próprio. A
    # fronteira entre "o que o sistema te conta" e "o que o cliente disse"
    # precisa estar desenhada no texto, senão o modelo responde à instrução.
    bloco_contexto = ""
    if (contexto_extra or "").strip():
        bloco_contexto = f"""

CONTEXTO INTERNO (informação do sistema, NÃO é fala do cliente e NÃO deve ser
repetida nem citada na resposta):
{contexto_extra.strip()}
FIM DO CONTEXTO INTERNO
"""

    prompt = f"""{SYSTEM_PROMPT}
{bloco_contexto}
HISTÓRICO DA CONVERSA:
{historico_texto}

NOVA MENSAGEM DO CLIENTE:
{mensagem_usuario}

Responda como um SDR imobiliário. Seja breve (máximo 3 linhas)."""

    return prompt


# ============================================================================
# FUNÇÃO 2: EXTRAIR DADOS ESTRUTURADOS
# ============================================================================

def extrair_dados_estruturados(texto: str) -> dict:
    """Extrai dados estruturados da conversa usando padrões regex."""
    texto_lower = texto.lower()
    
    return {
        "nome": extrair_nome(texto_lower),
        "intencao": extrair_intencao(texto_lower),
        "preco_faixa": extrair_preco(texto_lower),
        "regiao": extrair_regiao(texto_lower),
        "quartos": extrair_quartos(texto_lower),
        "urgencia": extrair_urgencia(texto_lower),
        "tipo_imovel": extrair_tipo_imovel(texto_lower),
        "email": extrair_email(texto),
        "telefone": extrair_telefone(texto)
    }


# ============================================================================
# FUNÇÕES DE EXTRAÇÃO (REGEX)
# ============================================================================

# Palavras que aparecem logo depois de "sou" e não são nome de ninguém.
# Estado civil, situação de moradia, profissão e os artigos que sobram quando a
# frase é "sou o Marcos". A lista é curta de propósito: ela só precisa cobrir o
# que um lead de imobiliária realmente escreve.
NAO_SAO_NOMES = {
    "casado", "casada", "solteiro", "solteira", "divorciado", "divorciada",
    "viuvo", "viúvo", "viuva", "viúva", "noivo", "noiva",
    "investidor", "investidora", "corretor", "corretora", "proprietario",
    "proprietária", "proprietario", "aposentado", "aposentada",
    "autonomo", "autônomo", "autonoma", "autônoma", "empresario", "empresário",
    "empresaria", "empresária", "estudante", "funcionario", "funcionário",
    "servidor", "servidora", "medico", "médico", "medica", "médica",
    "advogado", "advogada", "engenheiro", "engenheira", "professor",
    "professora", "de", "do", "da", "o", "a", "um", "uma", "so", "só",
    "muito", "meio", "bem", "novo", "nova", "cliente", "interessado",
    "interessada", "morador", "moradora",
}

# Artigos são PULADOS, não rejeitados: "sou o Marcos" e "eu sou a Ana"
# apresentam alguém, e o nome é a palavra seguinte.
ARTIGOS = {"o", "a", "os", "as", "um", "uma"}


def extrair_nome(texto: str) -> str:
    """Extrai nome usando padrões como 'meu nome é X' ou 'sou X'"""
    patterns = [
        r"(?:meu )?nome (?:é|e) ([a-záéíóúâêôãõç\s]+?)(?:\.|,|$)",
        r"(?:eu )?sou ([a-záéíóúâêôãõç\s]+?)(?:\.|,|$)",
        r"chamo.?me ([a-záéíóúâêôãõç\s]+?)(?:\.|,|$)"
    ]

    for pattern in patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            nome = match.group(1).strip()
            palavras = nome.split()
            if not palavras:
                continue
            while palavras and palavras[0].lower() in ARTIGOS:
                palavras.pop(0)
            if not palavras:
                continue
            primeira = palavras[0].lower()
            # O padrão "sou X" casa com qualquer palavra, e a maioria das
            # frases que começam com "sou" não apresenta ninguém: "sou casado
            # e tenho dois filhos" gravava o nome "Casado", e como o perfil é
            # monotônico o agente chamava o lead assim até o fim da conversa,
            # inclusive no card que o corretor lê antes de ligar.
            if primeira in NAO_SAO_NOMES:
                continue
            # Retorna a primeira palavra com a inicial maiúscula (ex: "joão" -> "João")
            return palavras[0].capitalize()

    return "undefined"


# Palavras que NEGAM o que vem logo depois.
#
# A extracao contava palavra por palavra sem olhar o que estava na frente
# delas, e o resultado era o oposto do que a pessoa disse: "nao quero comprar,
# quero alugar" contava um ponto para compra e dois para aluguel e ate
# acertava, mas "nao e para investir, e para morar" contava investimento e
# devolvia INVESTIMENTO, e "nao quero alugar, quero comprar" empatava dois a
# dois e o empate era resolvido a favor de compra por acaso, nao por analise.
#
# Errar a intencao e o erro mais caro da qualificacao: ele troca a ORDEM DE
# COLETA inteira (investidor nao responde quantos quartos), troca o que o
# agente oferece no fim (visita x conversa com especialista) e troca o filtro
# de venda/aluguel da busca de imoveis.
NEGACOES = ("nao ", "não ", "nem ", "nunca ")

# Ate onde a negacao alcanca, em caracteres. "nao quero comprar" tem a negacao
# a 10 caracteres de "comprar"; ja "nao gostei do ultimo apartamento que vi,
# mas quero comprar" tem 45 e nao deve contar como negacao.
ALCANCE_DA_NEGACAO = 22


def _negado(texto: str, palavra: str) -> bool:
    """Diz se a ocorrencia da palavra vem logo depois de uma negacao."""
    inicio = texto.find(palavra)
    if inicio < 0:
        return False

    antes = texto[max(0, inicio - ALCANCE_DA_NEGACAO):inicio]
    return any(neg in antes for neg in NEGACOES)


def _pontuar(texto: str, palavras) -> int:
    return sum(1 for kw in palavras if kw in texto and not _negado(texto, kw))


def extrair_intencao(texto: str) -> str:
    keywords_compra = ["comprar", "compra", "adquirir", "vou comprar", "estou procurando comprar", "quero comprar"]
    keywords_aluguel = ["alugar", "aluguel", "alugando", "para alugar", "procuro para alugar", "vou alugar", "quero alugar"]
    # "renda" sozinho saiu da lista: "tenho renda de 5 mil" e um locatario
    # informando salario, nao um investidor. As expressoes abaixo so aparecem
    # em conversa de investimento.
    keywords_investimento = ["investir", "investimento", "rentabilidade", "retorno",
                             "renda passiva", "renda de aluguel", "valorizacao",
                             "valorização", "para investir", "vou investir"]

    pontuacao_compra = _pontuar(texto, keywords_compra)
    pontuacao_aluguel = _pontuar(texto, keywords_aluguel)
    pontuacao_investimento = _pontuar(texto, keywords_investimento)

    # "e para morar" desempata contra investimento.
    #
    # Sem isto, "nao e para investir, e para morar" continuava caindo em
    # INVESTIMENTO em algumas redacoes, porque basta uma das dez expressoes de
    # investimento escapar da negacao para a regra do `> 0` decidir sozinha.
    if pontuacao_investimento and any(
        pista in texto for pista in ("para morar", "pra morar", "vou morar",
                                     "morar nele", "moradia", "minha familia",
                                     "minha família")
    ):
        pontuacao_investimento = 0

    # Investimento DECIDE quando aparece junto das outras, em vez de disputar
    # ponto a ponto.
    #
    # "Quero investir em imoveis para alugar" e a frase classica do investidor,
    # e ela contava dois pontos para aluguel ("alugar" e "para alugar") contra
    # um de investimento: o investidor entrava no funil como locatario. Mesma
    # coisa em "comprar para investir", que virava compra.
    #
    # Ali "alugar" e "comprar" dizem o que ele vai FAZER com o imovel, nao por
    # que esta procurando. Quem procura para morar nao fala em rentabilidade.
    if pontuacao_investimento > 0:
        return "INVESTIMENTO"

    max_score = max(pontuacao_compra, pontuacao_aluguel)

    if max_score == 0:
        return "undefined"
    elif pontuacao_compra == max_score:
        return "COMPRA"
    else:
        return "ALUGUEL"


# Os 20 bairros da base (ai-memory-rag/src/rag/schema.py) e as zonas.
#
# A lista antiga era escrita à mão e ficou fora de sincronia com o estoque: não
# tinha Tijuca, Recreio, Jacarepaguá, Vila Isabel nem Méier, que juntos são 35
# dos 140 imóveis. O efeito não era só "região em branco": `next_to_collect`
# travava em `region`, o agente repetia a mesma pergunta a cada turno, o
# seletor de data nunca chegava a aparecer e o card do corretor saía dizendo
# que o lead se esquivava de informar a região que ele tinha acabado de dar
# três vezes.
#
# A comparação também era sensível a acento, então "Gavea" e "Niteroi" digitados
# sem acento (que é como a maioria digita) não casavam com "gávea"/"niterói".
#
# A ordem importa: "Vila Isabel" antes de "Barra" evitaria um casamento parcial
# errado, e os nomes compostos vêm antes dos simples pelo mesmo motivo.
REGIOES_CONHECIDAS = (
    # zonas, primeiro, porque "zona sul" é mais específico que "sul"
    "Zona Sul", "Zona Norte", "Zona Oeste", "Zona Leste",
    # bairros compostos antes dos simples
    "Santa Teresa", "Vila Isabel", "Jacarepaguá",
    # demais bairros da base
    "Leblon", "Ipanema", "Lagoa", "Gávea", "Copacabana", "Botafogo",
    "Flamengo", "Vidigal", "Barra", "Recreio", "Tijuca", "Méier",
    "Centro", "Lapa", "Saúde", "Glória", "Niterói",
)


def _sem_acento(texto: str) -> str:
    """Tira acento para comparar. 'Gavea' e 'Gávea' são o mesmo bairro."""
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if unicodedata.category(c) != "Mn")


def extrair_regiao(texto: str) -> str:
    """Extrai região procurando por menções de localidades"""
    alvo = _sem_acento(texto)

    for regiao in REGIOES_CONHECIDAS:
        if _sem_acento(regiao) in alvo:
            # Devolve o nome canônico COM acento, que é a chave usada na base
            # de imóveis: devolver "Meier" faria a busca não achar nada.
            return regiao

    return "undefined"


# Número seguido da escala, em português.
#
# `milhões` e `milhão` vêm antes de `mi` e `mil` porque a alternância do `re` é
# da esquerda para a direita e pararia no prefixo mais curto. `mi` antes de
# `mil` é seguro por causa do \b: em "mil", o `mi` não fecha fronteira.
_ESCALA_DO_VALOR = re.compile(
    r"(r\$\s*)?(\d[\d.,]*\d|\d)\s*(milh[õo]es|milh[ãa]o|mi|mil|k|m)?\b",
    re.IGNORECASE,
)

_MILHAR_COM_PONTO = re.compile(r"^\d{1,3}(\.\d{3})+$")
_MILHAR_COM_VIRGULA = re.compile(r"^\d{1,3}(,\d{3})+$")

_MULTIPLICADOR = {
    "k": 1_000, "mil": 1_000,
    "m": 1_000_000, "mi": 1_000_000,
    "milhao": 1_000_000, "milhão": 1_000_000,
    "milhoes": 1_000_000, "milhões": 1_000_000,
}

# Abaixo disto não é orçamento de imóvel. Serve para o "3" de "3 quartos" e o
# "2" de "2 banheiros" não entrarem como preço quando vêm sem escala.
_MINIMO_PLAUSIVEL = 1_000


def _valor_em_reais(numero: str, escala: str):
    """Converte "1,5" + "milhão" em 1500000.0.

    O ponto é ambíguo em pt-BR: decimal em "1.5m", milhar em "500.000". A
    desambiguação usa a FORMA do número, não a escala, que é a mesma regra que
    o `parse_price_range` da Parte 2 aplica do outro lado.
    """
    if _MILHAR_COM_PONTO.match(numero):
        limpo = numero.replace(".", "")
    elif _MILHAR_COM_VIRGULA.match(numero):
        limpo = numero.replace(",", "")
    else:
        limpo = numero.replace(".", "").replace(",", ".") if numero.count(",") == 1 \
            else numero.replace(",", "")

    try:
        valor = float(limpo)
    except ValueError:
        return None

    return valor * _MULTIPLICADOR.get((escala or "").lower(), 1)


def _texto_do_valor(valor: float) -> str:
    """Devolve o valor no formato curto que a Parte 2 sabe reler."""
    if valor >= 1_000_000:
        return ("%g" % (valor / 1_000_000)) + "m"
    if valor >= 1_000:
        return ("%g" % (valor / 1_000)) + "k"
    return "%g" % valor


def extrair_preco(texto: str) -> str:
    r"""Extrai orçamento, preservando a ESCALA que o lead disse.

    O defeito que isto conserta: o padrão antigo era `(\d+...)\s*[kmK]`, que
    casava com o "m" de "milhão" mas rotulava o resultado sempre como "k". Um
    investidor dizendo "ticket de 2 milhões" entrava no perfil com `2k`, e o
    RAG respondia com "Sala comercial em Méier, 11050% acima do orçamento".
    Era o pior texto que a banca podia ver.
    """
    valores = []
    for moeda, numero, escala in _ESCALA_DO_VALOR.findall(texto):
        valor = _valor_em_reais(numero, escala)
        if valor is None:
            continue

        # Numero solto, sem escala e sem "R$", e quase sempre outra coisa:
        # quantidade de quartos, DDD, numero de telefone, CPF.
        #
        # A magnitude sozinha nao serve de criterio, e isso custou uma conversa
        # de verdade: "Marcos, 33410549" (um telefone sem DDD) virou orcamento
        # de R$ 33,4 milhoes, e o investidor de 800 mil passou a receber casas
        # de R$ 6,5 milhoes no Leblon, porque o orcamento inventado atropelou o
        # ticket que ele tinha acabado de informar.
        #
        # O separador de milhar tambem conta como sinal: "450.000" e dinheiro
        # escrito por gente, "33410549" nao e. E era a regra de magnitude que
        # fazia "R$ 8.550" (um aluguel) ser descartado por ser pequeno demais.
        tem_forma_de_dinheiro = (
            escala
            or moeda
            or _MILHAR_COM_PONTO.match(numero)
            or _MILHAR_COM_VIRGULA.match(numero)
        )
        if not tem_forma_de_dinheiro:
            continue

        if valor < _MINIMO_PLAUSIVEL:
            continue
        valores.append(valor)

    # `dict.fromkeys` no lugar de `set` conserta duas coisas de uma vez.
    #
    # A primeira derrubava o turno: quando o texto repete o mesmo valor
    # ("ticket de 800k" no contexto e "800k" na fala anterior), o `set`
    # colapsa os dois em um, o índice [1] não existe e o IndexError sobe pela
    # extração inteira. A resposta do Gemini já tinha sido paga e era jogada
    # fora junto.
    #
    # A segunda era silenciosa: `set` não tem ordem, então "entre 500k e 800k"
    # saía como "800k-500k" na cara do usuário, metade das vezes.
    unicos = list(dict.fromkeys(valores))[:2]

    if not unicos:
        return "undefined"
    if len(unicos) == 1:
        return _texto_do_valor(unicos[0])

    # "1 milhão e 200 mil" é UM valor de R$ 1.200.000, não a faixa de R$ 1 mi a
    # R$ 200 mil. Quando os dois números vêm fora de ordem crescente, não é
    # faixa: é um número escrito por extenso, e o teto é o maior dos dois.
    if unicos[0] > unicos[1]:
        return _texto_do_valor(unicos[0])

    return _texto_do_valor(unicos[0]) + "-" + _texto_do_valor(unicos[1])


# Palavra que o lead escreve -> tipo na base de imoveis.
#
# "sala" e "loja" ficam de FORA de proposito: quem escreve "sala" quase sempre
# quer dizer sala de estar, nao sala comercial. Comercial so entra quando o
# lead diz "comercial" ou "escritorio", que nao tem outra leitura.
#
# A ordem importa: "cobertura" antes de "apartamento", porque uma cobertura
# tambem e descrita como apartamento e o mais especifico tem que vencer.
TIPOS_DE_IMOVEL = (
    ("cobertura", "PENTHOUSE"),
    ("kitnet", "STUDIO"),
    ("quitinete", "STUDIO"),
    ("conjugado", "STUDIO"),
    ("studio", "STUDIO"),
    ("estúdio", "STUDIO"),
    ("estudio", "STUDIO"),
    ("comercial", "COMMERCIAL"),
    ("escritório", "COMMERCIAL"),
    ("escritorio", "COMMERCIAL"),
    ("sobrado", "HOUSE"),
    ("casa", "HOUSE"),
    ("apartamento", "APARTMENT"),
    ("apto", "APARTMENT"),
)


def extrair_tipo_imovel(texto: str) -> str:
    """Casa, apartamento, cobertura, studio ou sala comercial.

    O tipo nunca era coletado nem filtrado, e a base tem 9 salas comerciais e
    11 studios: quem pedia 2 quartos para morar acabava recebendo "Sala
    comercial de 0 quartos em Meier" quando a busca relaxava os filtros.
    """
    baixo = texto.lower()
    for palavra, tipo in TIPOS_DE_IMOVEL:
        if palavra in baixo:
            return tipo

    return "undefined"


def extrair_quartos(texto: str) -> str:
    patterns = [
        r"(\d+)\s*(?:quartos?|qto|bedroom|bedrooms|beds?)",
        r"[tT](\d+)",
        r"(\d+)\s*(?:dorms?|dormitórios?)"
    ]
    for pattern in patterns:
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            return match.group(1)
    return "undefined"


# "já" e "logo" saíram das pistas de urgência alta.
#
# São dois fragmentos que aparecem em qualquer frase, e os dois casos reais
# eram o oposto do que marcavam: "já tenho imóvel, sem pressa" e "moro logo ali
# no Leblon" entravam como urgência ALTA. O efeito não parava no card: eram 15
# pontos a mais no score e a cadência de follow-up caindo para a de 4 horas,
# ou seja, o lead que disse "sem pressa" era o mais perseguido da base.
#
# As expressões abaixo carregam a pressa dentro delas e não sobrevivem fora de
# uma frase sobre prazo.
URGENCIAS_ALTA = (
    "urgente", "urgência", "urgencia", "com urgência", "com urgencia",
    "o quanto antes", "o mais rápido possível", "o mais rapido possivel",
    "preciso rápido", "preciso rapido", "preciso já", "preciso ja",
    "quero já", "quero ja", "para ontem", "pra ontem",
    "essa semana", "esta semana", "nesta semana", "ainda este mês",
    "ainda esse mês", "imediato", "imediatamente", "com pressa",
    "estou com pressa", "to com pressa", "tô com pressa",
)

# "logo" sozinho também saiu daqui: "moro logo ali no Leblon" é endereço, não
# prazo. "logo logo" e "em breve" carregam o sentido temporal sem ambiguidade.
URGENCIAS_MEDIA = (
    "logo logo", "em breve", "breve", "próximos meses", "proximos meses",
    "em poucos meses", "nos próximos", "nos proximos", "até o fim do ano",
    "ate o fim do ano", "esse semestre", "este semestre",
)

# Ditas em qualquer lugar da frase, estas viram a decisão para baixa, mesmo que
# alguma pista de pressa apareça junto ("preciso rápido, mas sem pressa").
URGENCIAS_BAIXA = (
    "sem pressa", "não tenho pressa", "nao tenho pressa", "sem urgência",
    "sem urgencia", "com calma", "no meu tempo", "só pesquisando",
    "so pesquisando", "apenas pesquisando", "só olhando", "so olhando",
    "dando uma olhada", "ano que vem", "sem data", "não tem pressa",
    "nao tem pressa", "quando aparecer", "curiosidade",
)


def extrair_urgencia(texto: str) -> str:
    baixo = texto.lower()

    # A negação vence: quem diz "sem pressa" está informando o prazo de forma
    # explícita, e nenhuma pista indireta deve passar por cima disso.
    if any(pista in baixo for pista in URGENCIAS_BAIXA):
        return "baixa"

    if any(pista in baixo for pista in URGENCIAS_ALTA):
        return "alta"

    if any(pista in baixo for pista in URGENCIAS_MEDIA):
        return "media"

    return "baixa"


def extrair_email(texto: str) -> str:
    pattern = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    match = re.search(pattern, texto)
    return match.group(0) if match else "undefined"


def extrair_telefone(texto: str) -> str:
    patterns = [
        r"\(?(\d{2})\)?[\s-]?(\d{4,5})[\s-]?(\d{4})",
        r"\+55\s?(\d{2})\s?(\d{4,5})[\s-]?(\d{4})"
    ]
    for pattern in patterns:
        match = re.search(pattern, texto)
        if match:
            grupos = match.groups()
            if len(grupos) >= 3:
                return f"({grupos[0]}) {grupos[1]}-{grupos[2]}"
    return "undefined"


# ============================================================================
# FUNÇÕES AUXILIARES
# ============================================================================

def avaliar_status_qualificacao(dados: dict) -> str:
    campos_preenchidos = sum(1 for k, v in dados.items() if v and v != "undefined" and v != "")
    if campos_preenchidos >= 4:
        return "qualificado"
    elif campos_preenchidos >= 2:
        return "em_andamento"
    else:
        return "descartado"


def calcular_confianca(dados: dict) -> float:
    total_campos = len(dados)
    campos_preenchidos = sum(1 for k, v in dados.items() if v and v != "undefined" and v != "")
    return round(campos_preenchidos / total_campos, 2) if total_campos > 0 else 0.0


# ============================================================================
# TESTE RÁPIDO
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("🧪 Teste Rápido do Agente de IA (GEMINI)")
    print("=" * 70)
    
    msg1 = "Oi! Estou procurando comprar um apartamento na zona sul"
    resp1 = chamar_agente(msg1, [])
    
    print(f"Cliente: {msg1}")
    print(f"Agente: {resp1['resposta']}")
    print(f"Status: {resp1['status_qualificacao']}")
    print(f"Confiança: {resp1['confianca']:.0%}")
    
    historico = [
        {"role": "user", "content": msg1},
        {"role": "assistant", "content": resp1['resposta']}
    ]
    
    msg2 = "Meu orçamento é 500k a 800k, preciso de 3 quartos, é urgente!"
    resp2 = chamar_agente(msg2, historico)
    
    print("\n" + "-" * 40)
    print(f"Cliente: {msg2}")
    print(f"Agente: {resp2['resposta']}")
    print(f"Status: {resp2['status_qualificacao']}")
    print(f"Dados: {resp2['dados_coletados']}")
    print(f"Confiança: {resp2['confianca']:.0%}")
    
    print("\n" + "=" * 70)
    print("✅ Testes Concluídos!")
    print("=" * 70)