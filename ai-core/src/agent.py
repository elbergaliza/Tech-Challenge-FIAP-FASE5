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

# Inicializa o cliente com a API Key encontrada
client = genai.Client(api_key=api_key)

# ============================================================================
# SYSTEM PROMPT (Persona do SDR)
# ============================================================================

SYSTEM_PROMPT = """Você é um agente SDR imobiliário experiente e amigável da imobiliária TOP.

🎯 SUA MISSÃO:
- Atender leads com humanidade
- Entender suas necessidades imobiliárias
- Qualificá-los de forma natural
- Oferecer agendamento quando qualificado

📋 DADOS QUE VOCÊ PRECISA COLETAR:
1. Nome (sempre)
2. Intenção: COMPRA, ALUGUEL ou INVESTIMENTO
3. Faixa de preço
4. Região/localização de interesse
5. Número de quartos
6. Urgência (alta/média/baixa)
7. Email e telefone (se mencionar)

💬 COMO AGIR:
- Seja conversacional e natural, parecendo um SDR real
- Uma pergunta por mensagem (máximo 2 perguntas se fizer sentido)
- Não pergunte dados já mencionados
- Use o nome do cliente quando souber
- Faça pausas naturais, não pareça um questionário

🔄 FLUXO POR TIPO DE INTENÇÃO:

[COMPRA]
1. Pergunte: "Qual região você está procurando?" 
2. Depois: "Qual sua faixa de preço?"
3. Depois: "Quantos quartos você precisa?"
4. Depois: "Como está a urgência? Precisa rápido?"
5. Ofereça agendar visita

[ALUGUEL]
1. Pergunte: "Qual região você prefere?"
2. Depois: "Qual seria seu orçamento de aluguel?"
3. Depois: "Quantos quartos você procura?"
4. Depois: "Quando você precisa se mudar?"
5. Ofereça agendar visita

[INVESTIMENTO]
1. Pergunte: "Qual é seu ticket de investimento?"
2. Depois: "Qual expectativa de retorno você espera?"
3. Depois: "Você prefere imóvel pronto ou em desenvolvimento?"
4. Depois: "Qual região te interessa?"
5. Ofereça agendar conversa com especialista

⚡ AÇÕES FINAIS:
- Quando tiver 4+ dados coletados → Ofereça agendar
- Formato de oferta: "Perfeito! Gostaria de agendar uma visita?"
- Sempre seja positivo e entusiasmado

❌ NÃO FAÇA ISSO:
- Não pergunte "qual é seu nome" se já sabe
- Não pareça um robô: nada de "campo obrigatório"
- Não seja muito formal (evite "prezado cliente")
- Não demore: respostas curtas (máx 3 linhas)
- Não insista se o cliente disser "não agora" - seja educado

✅ EXEMPLOS DE RESPOSTAS BOM:
✓ "Entendi! Zona sul é ótima opção. Qual seria sua faixa de preço ideal?"
✓ "Ah, você investe em imóveis? Legal! Qual é seu ticket típico?"
✓ "Perfeito, 3 quartos é bem comum por lá. E em termos de urgência, você busca algo rápido?"

❌ EXEMPLOS RUINS:
✗ "CAMPO OBRIGATÓRIO: Digite seu nome completo"
✗ "Prezado cliente, solicito informações sobre sua localização preferencial"
✗ "1) Nome? 2) Telefone? 3) Email? (Responda em ordem)"
"""

# ============================================================================
# FUNÇÃO PRINCIPAL: CHAMAR AGENTE (COM GEMINI)
# ============================================================================

def chamar_agente(
    mensagem_usuario: str,
    historico: list,
    lead_id: Optional[str] = None
) -> dict:
    """
    Chama o Google Gemini e retorna resposta + dados extraídos.
    """
    
    print(f"[LOG] Processando mensagem do lead {lead_id}...")
    
    try:
        # 1. CHAMAR GEMINI (Nova sintaxe do cliente)
        prompt_completo = _construir_prompt_com_historico(mensagem_usuario, historico)
        
        response = client.models.generate_content(
            model='gemini-3.6-flash',
            contents=prompt_completo
        )
        resposta_texto = response.text
        
    except Exception as e:
        print(f"❌ Erro ao chamar Gemini: {e}")
        return {
            "resposta": "Desculpe, tive um problema técnico. Pode tentar novamente?",
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
    conversa_completa = " ".join([msg["content"] for msg in historico]) + " " + mensagem_usuario
    dados_coletados = extrair_dados_estruturados(conversa_completa)
    
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

def _construir_prompt_com_historico(mensagem_usuario: str, historico: list) -> str:
    """Constrói prompt com contexto de histórico para o Gemini"""
    
    historico_texto = ""
    for msg in historico[-10:]:
        role = "Cliente" if msg["role"] == "user" else "Você (Agente)"
        historico_texto += f"\n{role}: {msg['content']}"
    
    prompt = f"""{SYSTEM_PROMPT}

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
        "email": extrair_email(texto),
        "telefone": extrair_telefone(texto)
    }


# ============================================================================
# FUNÇÕES DE EXTRAÇÃO (REGEX)
# ============================================================================

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
            # Retorna a primeira palavra com a inicial maiúscula (ex: "joão" -> "João")
            return palavras[0].capitalize() if palavras else "undefined"
    
    return "undefined"


def extrair_intencao(texto: str) -> str:
    keywords_compra = ["comprar", "compra", "adquirir", "vou comprar", "estou procurando comprar", "quero comprar"]
    keywords_aluguel = ["alugar", "aluguel", "alugando", "para alugar", "procuro para alugar", "vou alugar", "quero alugar"]
    keywords_investimento = ["investir", "investimento", "renda", "rentabilidade", "retorno", "para investir", "vou investir"]
    
    pontuacao_compra = sum(1 for kw in keywords_compra if kw in texto)
    pontuacao_aluguel = sum(1 for kw in keywords_aluguel if kw in texto)
    pontuacao_investimento = sum(1 for kw in keywords_investimento if kw in texto)
    
    max_score = max(pontuacao_compra, pontuacao_aluguel, pontuacao_investimento)
    
    if max_score == 0:
        return "undefined"
    elif pontuacao_compra == max_score:
        return "COMPRA"
    elif pontuacao_aluguel == max_score:
        return "ALUGUEL"
    else:
        return "INVESTIMENTO"


def extrair_regiao(texto: str) -> str:
    """Extrai região procurando por menções de localidades"""
    regioes_conhecidas = [
        "zona sul", "zona norte", "zona leste", "zona oeste",
        "centro", "barra", "leblon", "copacabana", "ipanema",
        "lapa", "botafogo", "niterói", "flamengo", "gávea",
        "lagoa", "vidigal", "santa teresa", "saúde", "glória"
    ]
    
    texto_lower = texto.lower()
    for regiao in regioes_conhecidas:
        if regiao in texto_lower:
            return regiao.title()
    
    return "undefined"


def extrair_preco(texto: str) -> str:
    pattern = r"(\d+(?:[.,]\d+)?)\s*[kmK]"
    matches = re.findall(pattern, texto)
    if not matches:
        return "undefined"
    if len(matches) >= 2:
        valores_unicos = list(set(matches[:2]))
        return f"{valores_unicos[0]}k-{valores_unicos[1]}k"
    else:
        return f"{matches[0]}k"


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


def extrair_urgencia(texto: str) -> str:
    urgencias_alta = ["urgente", "rápido", "logo", "já", "essa semana", "muito urgente", "preciso rápido", "urgência"]
    urgencias_media = ["logo", "breve", "próximos meses", "em poucos meses"]
    
    pontuacao_alta = sum(1 for kw in urgencias_alta if kw in texto)
    pontuacao_media = sum(1 for kw in urgencias_media if kw in texto)
    
    if pontuacao_alta > 0:
        return "alta"
    elif pontuacao_media > 0:
        return "media"
    else:
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