"""
Data contract for the property documents used by the RAG.

This file is the source of truth for what a property looks like. Three sides of
the project consume it:

  * the synthetic base generator (scripts/generate_properties.py);
  * this module's indexer and retriever;
  * Person 3, who loads `shared/data/imoveis.json` into the database.

LANGUAGE: field names, enum values and identifiers are in English. Only two
things stay in Portuguese, and both for good reason:

  * PROPER NOUNS as values ("Copacabana", "Zona Sul", "Rio de Janeiro"). These
    are the real names of real places; translating them would be wrong.
  * DISPLAY LABELS, kept in the *_LABELS maps below. Enums are English for the
    code, but the text shown to a lead or a broker has to read as Portuguese:
    the agent says "apartamento", not "APARTMENT".

The field design follows the index schema taught in lesson 04 of "Análise de
Documentos com Serviços do Azure" (Azure Cognitive Search): every field is
classified as searchable, filterable, sortable or facetable. Search runs
locally here, but the classification is recorded in AZURE_FIELD_MAP so that
swapping in Azure AI Search would be translation work, not redesign.
"""

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

# An investor buys, so INVEST is not a deal type: it maps to SALE plus a yield
# filter. See `filters_from_profile` in the retriever.
DEAL_TYPES = ("SALE", "RENTAL")

PROPERTY_TYPES = (
    "APARTMENT",
    "PENTHOUSE",
    "HOUSE",
    "STUDIO",
    "COMMERCIAL",
)

STATUSES = ("AVAILABLE", "RESERVED", "SOLD")

# Portuguese labels for anything a lead or broker reads. The code branches on
# the English enum; the text uses these.
DEAL_TYPE_LABELS = {"SALE": "venda", "RENTAL": "aluguel"}

PROPERTY_TYPE_LABELS = {
    "APARTMENT": "Apartamento",
    "PENTHOUSE": "Cobertura",
    "HOUSE": "Casa",
    "STUDIO": "Studio",
    "COMMERCIAL": "Sala comercial",
}


# ---------------------------------------------------------------------------
# Canonical location catalogue
# ---------------------------------------------------------------------------
# neighborhood -> (zone, city, lat, lon, base_sale_price_per_m2)
#
# Place names stay in Portuguese: they are proper nouns.
#
# IMPORTANT (integration with Person 1): `extrair_regiao` in
# ai-core/src/agent.py only recognises a fixed list of locations and returns
# them in Title Case. The neighborhoods and zone names below were chosen to
# match that list. If Person 1 extends `regioes_conhecidas`, extend this too,
# otherwise a lead asks for a region that has no inventory.
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

# Terms a lead may use that name a whole zone rather than a neighborhood.
BROAD_REGIONS = ZONES


def resolve_region(region):
    """Classify a region string as either a neighborhood or a zone.

    Takes the region the lead mentioned and returns `(neighborhood, zone)`,
    with at most one of them filled in.

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
# Document fields
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

# How each field would be declared in an Azure Cognitive Search index,
# following the `Search.py` pattern from lesson 04. Serves as documentation and
# as a migration roadmap if the project ever gets an Azure account.
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
    # In Azure this would be a single GeographyPoint field, queried with
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

# Relative weight of each field in the text that goes into the embedding.
# Equivalent to Azure's per-field boosting
# (`search_fields=["title^3", "description"]`).
TEXT_WEIGHTS = {
    "title": 3,
    "neighborhood": 2,
    "property_type": 2,
    "features": 2,
    "description": 1,
}


def validate_property(prop):
    """Return the list of problems found in a document. Empty means valid."""
    problems = []

    for field in REQUIRED_FIELDS:
        if field not in prop:
            problems.append("missing required field: " + field)

    if prop.get("deal_type") not in DEAL_TYPES:
        problems.append("invalid deal_type: %r" % (prop.get("deal_type"),))

    if prop.get("property_type") not in PROPERTY_TYPES:
        problems.append("invalid property_type: %r" % (prop.get("property_type"),))

    if prop.get("status") not in STATUSES:
        problems.append("invalid status: %r" % (prop.get("status"),))

    if prop.get("neighborhood") not in NEIGHBORHOODS:
        problems.append("neighborhood not in catalogue: %r" % (prop.get("neighborhood"),))

    price = prop.get("price")
    if not isinstance(price, (int, float)) or price <= 0:
        problems.append("invalid price: %r" % (price,))

    if not isinstance(prop.get("features"), list):
        problems.append("features must be a list")

    return problems
