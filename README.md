# Tech-Challenge-FIAP-FASE5
Tech-Challenge-FIAP-FASE5

# Iniciação do Agente de IA

Pessoa 1: Agente de IA — Google Gemini (GRATUITO) ✅

Implementação completa do agente SDR usando Google Gemini (completamente gratuito).

* Por Que Gemini?
* 100% GRATUITO (sem cartão de crédito)
* Sem limite de requisições (praticamente)
* Boa qualidade de resposta
* Rápido
* Fácil de configurar
* Instalação (5 minutos)

1. Obter Chave Grátis do Gemini

Acesse: https://ai.google.dev/

Clique em "Get API Key"
Clique em "Create API Key"
Copie a chave (formato: AIza...)

Pronto! Sua chave é gratuita e ilimitada.

2. Criar Arquivo .env
bash
# Copiar template
cp .env.gemini.example .env

# Editar .env
# Trocar "sua-chave-gemini-aqui" pela sua chave real
3. Instalar Dependências
bash
pip install -r requirements_gemini.txt
4. Testar
bash
python test_agente_ia.py

Você deve ver:

* TODOS OS TESTES PASSARAM!
* Como Usar
Uso Básico
python
from agente_ia_gemini import chamar_agente

# Primeira mensagem
resultado = chamar_agente("Oi! Procuro comprar apartamento", [])

print("Resposta:", resultado["resposta"])
print("Status:", resultado["status_qualificacao"])
Conversa Completa
python
from agente_ia_gemini import chamar_agente

historico = []

# Mensagem 1
msg1 = "Olá! Estou procurando comprar na zona sul"
resp1 = chamar_agente(msg1, historico)

print("Cliente:", msg1)
print("Agente:", resp1["resposta"])

# Adicionar ao histórico
historico.append({"role": "user", "content": msg1})
historico.append({"role": "assistant", "content": resp1["resposta"]})

# Mensagem 2
msg2 = "Meu orçamento é 500k a 800k, 3 quartos"
resp2 = chamar_agente(msg2, historico)

print("Cliente:", msg2)
print("Status:", resp2["status_qualificacao"])
print("Dados:", resp2["dados_coletados"])
* O Que a Função Retorna
python
{
    "resposta": "Ótimo! Qual seria sua faixa de preço?",
    
    "dados_coletados": {
        "nome": "João",
        "intencao": "COMPRA",
        "preco_faixa": "500k-800k",
        "regiao": "Zona Sul",
        "quartos": "3",
        "urgencia": "alta",
        "email": "undefined",
        "telefone": "undefined"
    },
    
    "status_qualificacao": "em_andamento",
    "extracoes_completas": ["intencao", "preco_faixa", "regiao"],
    "confianca": 0.37
}
* Testes
bash
python test_agente_ia.py

Testa:

* Extração de dados
* Avaliação de status
* Fluxo completo de conversa
* Arquivos Necessários
seu_projeto_gemini/
│
├── agente_ia_gemini.py        ← Código principal
├── test_agente_ia.py          ← Testes (compartilhado)
├── requirements_gemini.txt    ← Dependências
├── .env.gemini.example        ← Template
├── .env                       ← Seu arquivo com chave
└── README_GEMINI.md           ← Este arquivo
* Troubleshooting
* "GEMINI_API_KEY not found"

Solução:

bash
# Verificar se .env existe
ls .env

# Se não existir, criar:
cp .env.gemini.example .env

# Editar .env e adicionar sua chave
* "ModuleNotFoundError: No module named 'google'"

Solução:

bash
pip install -r requirements_gemini.txt
* "Invalid API Key"

Solução:

Verifique se copiou a chave corretamente
Vá em https://ai.google.dev/ e regenere a chave se necessário
* "Rate limit exceeded"

Solução:

Gemini gratuito tem limite de 60 requisições por minuto
Espere um minuto e tente novamente
Ou distribua as requisições no tempo
