"""
TESTES UNITÁRIOS - Agente de IA (Pessoa 1)

Este arquivo testa se o agente_ia.py funciona corretamente.

Rodar com: python test_agente_ia.py
"""

from agent import (
    chamar_agente,
    extrair_nome,
    extrair_intencao,
    extrair_preco,
    extrair_regiao,
    extrair_quartos,
    extrair_urgencia,
    extrair_email,
    extrair_telefone,
    avaliar_status_qualificacao
)


# ============================================================================
# TESTES DE EXTRAÇÃO INDIVIDUAL
# ============================================================================

def test_extrair_nome():
    """Testa extração de nome"""
    print("🧪 Test 1: Extração de nome...")
    
    assert extrair_nome("meu nome é joão silva") == "João"
    assert extrair_nome("eu sou maria") == "Maria"
    assert extrair_nome("chamo-me pedro") == "Pedro"
    assert extrair_nome("sem nome aqui") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_intencao():
    """Testa detecção de intenção"""
    print("🧪 Test 2: Extração de intenção...")
    
    assert extrair_intencao("quero comprar um apartamento") == "COMPRA"
    assert extrair_intencao("procuro para alugar") == "ALUGUEL"
    assert extrair_intencao("vou investir em imóvel") == "INVESTIMENTO"
    assert extrair_intencao("só consultando") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_regiao():
    """Testa extração de região"""
    print("🧪 Test 3: Extração de região...")
    
    assert extrair_regiao("zona sul é ótima") == "Zona Sul"
    assert extrair_regiao("prefiro zona norte") == "Zona Norte"
    assert extrair_regiao("centro da cidade") == "Centro"
    assert extrair_regiao("niterói") == "Niterói"
    assert extrair_regiao("sem região") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_preco():
    """Testa extração de faixa de preço"""
    print("🧪 Test 4: Extração de preço...")
    
    resultado = extrair_preco("tenho orçamento de 300k")
    assert "300" in resultado
    
    resultado = extrair_preco("até 1.5m")
    assert "1" in resultado or "1.5" in resultado
    
    assert extrair_preco("sem preço") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_quartos():
    """Testa extração de número de quartos"""
    print("🧪 Test 5: Extração de quartos...")
    
    assert extrair_quartos("procuro 3 quartos") == "3"
    assert extrair_quartos("preciso de t2") == "2"
    assert extrair_quartos("2 bedroom") == "2"
    assert extrair_quartos("4 dormitórios") == "4"
    assert extrair_quartos("sem quartos") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_urgencia():
    """Testa extração de urgência"""
    print("🧪 Test 6: Extração de urgência...")
    
    assert extrair_urgencia("é urgente!") == "alta"
    assert extrair_urgencia("preciso rápido") == "alta"
    assert extrair_urgencia("sem pressa") == "baixa"
    assert extrair_urgencia("em breve") == "media"
    
    print("   ✅ Passou!")


def test_extrair_email():
    """Testa extração de email"""
    print("🧪 Test 7: Extração de email...")
    
    assert extrair_email("meu email é joao@email.com") == "joao@email.com"
    assert extrair_email("contato: maria.silva@dominio.com.br") == "maria.silva@dominio.com.br"
    assert extrair_email("sem email") == "undefined"
    
    print("   ✅ Passou!")


def test_extrair_telefone():
    """Testa extração de telefone"""
    print("🧪 Test 8: Extração de telefone...")
    
    # Telefone com parênteses e hífen
    resultado = extrair_telefone("meu telefone é (11) 98765-4321")
    assert resultado != "undefined"
    
    resultado = extrair_telefone("sem telefone")
    assert resultado == "undefined"
    
    print("   ✅ Passou!")


# ============================================================================
# TESTES DE AVALIAÇÃO DE STATUS
# ============================================================================

def test_avaliar_status():
    """Testa avaliação de status de qualificação"""
    print("🧪 Test 9: Avaliação de status...")
    
    # Qualificado (4+ campos)
    dados_qualificado = {
        "nome": "João",
        "intencao": "COMPRA",
        "regiao": "Zona Sul",
        "quartos": "3",
        "urgencia": "alta",
        "preco_faixa": "500k",
        "email": "undefined",
        "telefone": "undefined"
    }
    assert avaliar_status_qualificacao(dados_qualificado) == "qualificado"
    
    # Em andamento (2-3 campos)
    dados_andamento = {
        "nome": "João",
        "intencao": "COMPRA",
        "regiao": "undefined",
        "quartos": "undefined",
        "urgencia": "undefined",
        "preco_faixa": "undefined",
        "email": "undefined",
        "telefone": "undefined"
    }
    assert avaliar_status_qualificacao(dados_andamento) == "em_andamento"
    
    # Descartado (0-1 campos)
    dados_descartado = {
        "nome": "undefined",
        "intencao": "undefined",
        "regiao": "undefined",
        "quartos": "undefined",
        "urgencia": "undefined",
        "preco_faixa": "undefined",
        "email": "undefined",
        "telefone": "undefined"
    }
    assert avaliar_status_qualificacao(dados_descartado) == "descartado"
    
    print("   ✅ Passou!")


# ============================================================================
# TESTES DE FLUXO COMPLETO
# ============================================================================

def test_fluxo_compra():
    """Testa fluxo completo de qualificação para COMPRA"""
    print("🧪 Test 10: Fluxo completo - COMPRA...")
    
    historico = []
    
    # Mensagem 1: Cliente apresenta intenção
    msg1 = "Oi! Estou procurando comprar um apartamento"
    resp1 = chamar_agente(msg1, historico)
    
    assert resp1["status_qualificacao"] in ["em_andamento", "qualificado"]
    assert "COMPRA" in resp1["dados_coletados"]["intencao"]
    assert len(resp1["resposta"]) > 0
    assert isinstance(resp1["confianca"], float)
    
    # Adicionar ao histórico
    historico.append({"role": "user", "content": msg1})
    historico.append({"role": "assistant", "content": resp1["resposta"]})
    
    # Mensagem 2: Cliente menciona região
    msg2 = "Na zona sul, perto da praia"
    resp2 = chamar_agente(msg2, historico)
    
    assert resp2["dados_coletados"]["regiao"] != "undefined"
    
    print("   ✅ Passou!")


def test_fluxo_aluguel():
    """Testa fluxo de aluguel"""
    print("🧪 Test 11: Fluxo completo - ALUGUEL...")
    
    historico = []
    msg1 = "Olá! Preciso alugar um apartamento"
    resp1 = chamar_agente(msg1, historico)
    
    assert "ALUGUEL" in resp1["dados_coletados"]["intencao"]
    assert resp1["status_qualificacao"] in ["em_andamento", "qualificado"]
    
    print("   ✅ Passou!")


def test_fluxo_investimento():
    """Testa fluxo de investimento"""
    print("🧪 Test 12: Fluxo completo - INVESTIMENTO...")
    
    historico = []
    msg1 = "Quero investir em imóvel para renda"
    resp1 = chamar_agente(msg1, historico)
    
    assert "INVESTIMENTO" in resp1["dados_coletados"]["intencao"]
    assert resp1["status_qualificacao"] in ["em_andamento", "qualificado"]
    
    print("   ✅ Passou!")


# ============================================================================
# RODAR TESTES
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("🧪 EXECUTANDO TESTES DO AGENTE DE IA")
    print("=" * 70)
    print()
    
    try:
        # Testes de extração
        test_extrair_nome()
        test_extrair_intencao()
        test_extrair_regiao()
        test_extrair_preco()
        test_extrair_quartos()
        test_extrair_urgencia()
        test_extrair_email()
        test_extrair_telefone()
        
        # Testes de status
        test_avaliar_status()
        
        # Testes de fluxo
        test_fluxo_compra()
        test_fluxo_aluguel()
        test_fluxo_investimento()
        
        print()
        print("=" * 70)
        print("✅ TODOS OS TESTES PASSARAM!")
        print("=" * 70)
        print()
        print("Seu agente_ia.py está funcionando corretamente! 🎉")
        
    except AssertionError as e:
        print()
        print("=" * 70)
        print("❌ TESTE FALHOU!")
        print("=" * 70)
        print(f"Erro: {e}")
        exit(1)
    except Exception as e:
        print()
        print("=" * 70)
        print("❌ ERRO AO EXECUTAR TESTES!")
        print("=" * 70)
        print(f"Erro: {e}")
        exit(1)