"""
Contrato dos dados de imóvel usados pelo RAG.

Este arquivo é a fonte da verdade sobre o formato de um imóvel. Três lados do
projeto o consomem:

  * o gerador da base sintética (scripts/generate_properties.py);
  * o indexador e o retriever deste módulo;
  * a Pessoa 3, que carrega `shared/data/imoveis.json` no banco.

IDIOMA: nomes de campo, valores de enum e identificadores estão em inglês.
Só duas coisas ficam em português, e ambas por bom motivo:

  * NOMES PRÓPRIOS como valor ("Copacabana", "Zona Sul", "Rio de Janeiro").
    São nomes reais de lugares reais; traduzir seria errado.
  * RÓTULOS DE EXIBIÇÃO, guardados nos mapas *_LABELS abaixo. Os enums são em
    inglês para o código, mas o texto mostrado ao lead ou ao corretor tem de
    ler como português: o agente diz "apartamento", não "APARTMENT".

O desenho dos campos segue o esquema de índice ensinado na Aula 04 de "Análise
de Documentos com Serviços do Azure" (Azure Cognitive Search): cada campo é
classificado como pesquisável, filtrável, ordenável ou facetável. Aqui a busca
roda local, mas a classificação está registrada em AZURE_FIELD_MAP para que
trocar por Azure AI Search seja trabalho de tradução, não de redesenho.
"""

# ---------------------------------------------------------------------------
# Vocabulários fechados
# ---------------------------------------------------------------------------

# Um investidor compra, então INVEST não é um tipo de negócio: ele mapeia para
# SALE mais um filtro de rentabilidade. Ver `filters_from_profile` no retriever.
DEAL_TYPES = ("SALE", "RENTAL")

PROPERTY_TYPES = (
    "APARTMENT",
    "PENTHOUSE",
    "HOUSE",
    "STUDIO",
    "COMMERCIAL",
)

STATUSES = ("AVAILABLE", "RESERVED", "SOLD")

# Rótulos em português para tudo que um lead ou corretor lê. O código decide
# pelo enum em inglês; o texto usa estes.
DEAL_TYPE_LABELS = {"SALE": "venda", "RENTAL": "aluguel"}

PROPERTY_TYPE_LABELS = {
    "APARTMENT": "Apartamento",
    "PENTHOUSE": "Cobertura",
    "HOUSE": "Casa",
    "STUDIO": "Studio",
    "COMMERCIAL": "Sala comercial",
}


# ---------------------------------------------------------------------------
# Catálogo canônico de localidades
# ---------------------------------------------------------------------------
# bairro -> (zona, cidade, lat, lon, preco_m2_venda_base)
#
# Os nomes de lugar ficam em português: são nomes próprios.
#
# IMPORTANTE (integração com a Pessoa 1): a função `extrair_regiao` do
# ai-core/src/agent.py só reconhece uma lista fixa de localidades e devolve o
# valor em Title Case. Os bairros e nomes de zona abaixo foram escolhidos para
# casar com aquela lista. Se a Pessoa 1 ampliar `regioes_conhecidas`, ampliar
# aqui também, senão o lead informa uma região que não tem estoque.
NEIGHBORHOODS = {
    "Leblon":       ("Zona Sul",   "Rio de Janeiro", -22.9838, -43.2226, 25000),
    "Ipanema":      ("Zona Sul",   "Rio de Janeiro", -22.9838, -43.2045, 22000),
    "Lagoa":        ("Zona Sul",   "Rio de Janeiro", -22.9686, -43.2049, 18000),
    "Gávea":        ("Zona Sul",   "Rio de Janeiro", -22.9760, -43.2320, 16000),
    "Copacabana":   ("Zona Sul",   "Rio de Janeiro", -22.9711, -43.1822, 14000),
    "Botafogo":     ("Zona Sul",   "Rio de Janeiro", -22.9519, -43.1837, 13000),
    "Flamengo":     ("Zona Sul",   "Rio de Janeiro", -22.9328, -43.1750, 12000),
    "Vidigal":      ("Zona Sul",   "Rio de Janeiro", -22.9945, -43.2440,  8000),
    "Barra":        ("Zona Oeste", "Rio de Janeiro", -23.0045, -43.3650, 11000),
    "Recreio":      ("Zona Oeste", "Rio de Janeiro", -23.0200, -43.4650,  8500),
    "Jacarepaguá":  ("Zona Oeste", "Rio de Janeiro", -22.9600, -43.3700,  7000),
    "Tijuca":       ("Zona Norte", "Rio de Janeiro", -22.9245, -43.2286,  8000),
    "Vila Isabel":  ("Zona Norte", "Rio de Janeiro", -22.9160, -43.2470,  7000),
    "Méier":        ("Zona Norte", "Rio de Janeiro", -22.9020, -43.2790,  6000),
    "Centro":       ("Centro",     "Rio de Janeiro", -22.9068, -43.1729,  6000),
    "Lapa":         ("Centro",     "Rio de Janeiro", -22.9133, -43.1800,  6500),
    "Santa Teresa": ("Centro",     "Rio de Janeiro", -22.9250, -43.1875,  8000),
    "Saúde":        ("Centro",     "Rio de Janeiro", -22.8971, -43.1873,  5500),
    "Glória":       ("Centro",     "Rio de Janeiro", -22.9200, -43.1750,  7000),
    "Niterói":      ("Niterói",    "Niterói",        -22.9060, -43.1050,  9000),
}

ZONES = tuple(sorted({data[0] for data in NEIGHBORHOODS.values()}))

# Termos que o lead pode usar e que designam uma zona inteira, não um bairro.
BROAD_REGIONS = ZONES


def resolve_region(region):
    """Classifica um texto de região como bairro ou como zona.

    Recebe a região que o lead mencionou e devolve `(bairro, zona)`, com no
    máximo um dos dois preenchido.

    >>> resolve_region("Copacabana")
    ('Copacabana', None)
    >>> resolve_region("Zona Sul")
    (None, 'Zona Sul')
    >>> resolve_region(None)
    (None, None)
    """
    if not region:
        return None, None

    target = str(region).strip().lower()

    for neighborhood in NEIGHBORHOODS:
        if neighborhood.lower() == target:
            return neighborhood, None

    for zone in ZONES:
        if zone.lower() == target:
            return None, zone

    return None, None


# ---------------------------------------------------------------------------
# Campos do documento
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = (
    "id",
    "title",
    "description",
    "deal_type",
    "property_type",
    "price",
    "bedrooms",
    "bathrooms",
    "parking",
    "area_m2",
    "neighborhood",
    "zone",
    "city",
    "lat",
    "lon",
    "features",
    "status",
)

# Como cada campo seria declarado em um índice do Azure Cognitive Search,
# seguindo o padrão do `Search.py` da Aula 04. Serve de documentação e de
# roteiro caso o projeto ganhe uma conta Azure depois.
#   key        -> SimpleField(key=True)
#   searchable -> SearchableField(searchable=True)
#   filterable -> filterable=True
#   sortable   -> sortable=True
#   facetable  -> facetable=True
AZURE_FIELD_MAP = {
    "id":                 {"type": "String",  "key": True},
    "title":              {"type": "String",  "searchable": True},
    "description":        {"type": "String",  "searchable": True},
    "deal_type":          {"type": "String",  "filterable": True, "facetable": True},
    "property_type":      {"type": "String",  "filterable": True, "facetable": True},
    "price":              {"type": "Double",  "filterable": True, "sortable": True},
    "condo_fee":          {"type": "Double",  "filterable": True, "sortable": True},
    "property_tax":       {"type": "Double",  "filterable": True},
    "bedrooms":           {"type": "Int32",   "filterable": True, "facetable": True,
                           "sortable": True},
    "suites":             {"type": "Int32",   "filterable": True},
    "bathrooms":          {"type": "Int32",   "filterable": True},
    "parking":            {"type": "Int32",   "filterable": True, "facetable": True},
    "area_m2":            {"type": "Double",  "filterable": True, "sortable": True},
    "neighborhood":       {"type": "String",  "searchable": True, "filterable": True,
                           "facetable": True},
    "zone":               {"type": "String",  "filterable": True, "facetable": True},
    "city":               {"type": "String",  "filterable": True, "facetable": True},
    # No Azure isto seria um único campo GeographyPoint, consultado com
    # geo.distance(location, geography'POINT(lon lat)') le radius_km.
    "lat":                {"type": "Double",  "geo": "location"},
    "lon":                {"type": "Double",  "geo": "location"},
    "features":           {"type": "Collection(String)", "searchable": True,
                           "filterable": True, "facetable": True},
    "accepts_financing":  {"type": "Boolean", "filterable": True},
    "annual_yield_pct":   {"type": "Double",  "filterable": True, "sortable": True},
    "status":             {"type": "String",  "filterable": True, "facetable": True},
    "updated_at":         {"type": "String",  "filterable": True, "sortable": True},
}

# Peso relativo de cada campo no texto que vai para o embedding. Equivale ao
# boosting por campo do Azure (`search_fields=["title^3", "description"]`).
TEXT_WEIGHTS = {
    "title": 3,
    "neighborhood": 2,
    "property_type": 2,
    "features": 2,
    "description": 1,
}


def validate_property(prop):
    """Devolve a lista de problemas do documento. Vazia significa válido."""
    problems = []

    for field in REQUIRED_FIELDS:
        if field not in prop:
            problems.append("campo obrigatório ausente: " + field)

    if prop.get("deal_type") not in DEAL_TYPES:
        problems.append("deal_type inválido: %r" % (prop.get("deal_type"),))

    if prop.get("property_type") not in PROPERTY_TYPES:
        problems.append("property_type inválido: %r" % (prop.get("property_type"),))

    if prop.get("status") not in STATUSES:
        problems.append("status inválido: %r" % (prop.get("status"),))

    if prop.get("neighborhood") not in NEIGHBORHOODS:
        problems.append("neighborhood fora do catálogo: %r" % (prop.get("neighborhood"),))

    price = prop.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        problems.append("price inválido: %r" % (price,))

    if not isinstance(prop.get("features"), list):
        problems.append("features precisa ser uma lista")

    return problems
