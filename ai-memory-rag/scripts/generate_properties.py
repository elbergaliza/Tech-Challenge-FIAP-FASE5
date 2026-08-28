"""
Generator for the simulated property base.

Writes two files under `shared/`, because they are a team contract rather than
a private artifact of this module:

    shared/data/imoveis.json           the base itself (Person 3 loads it)
    shared/schemas/imovel_schema.json  the JSON Schema of the document

The generator is deterministic (fixed seed). Running it twice produces a
byte-identical file, so the JSON can be versioned in git without spurious diffs.
Do not reorder the random calls below: doing so changes the whole base.

A synthetic base is a design decision, not a shortcut: lesson 04 of "Privacidade
e Proteção de Dados" lists synthetic data generation as a privacy protection
technique, precisely so AI systems are neither trained nor demonstrated on real
people's data. There is not a single real property, address or owner here.

NOTE ON LANGUAGE: field names and enum values are in English. Only two things
stay in Portuguese: proper nouns as values ("Copacabana", "Zona Sul") and the
listing copy in `title`/`description`/`features`, which is text a lead reads.

Usage:
    python ai-memory-rag/scripts/generate_properties.py
    python ai-memory-rag/scripts/generate_properties.py --total 120
"""

import argparse
import json
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from rag import schema  # noqa: E402

SEED = 20260826

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DATA_TARGET = os.path.join(ROOT, "shared", "data", "imoveis.json")
SCHEMA_TARGET = os.path.join(ROOT, "shared", "schemas", "imovel_schema.json")

# Neighborhoods near the beach: they get their own features and descriptions.
BEACH_NEIGHBORHOODS = {
    "Leblon", "Ipanema", "Copacabana", "Barra", "Recreio", "Vidigal", "Niterói",
}

# Price multiplier per property type, applied over the neighborhood's price/m2.
TYPE_FACTOR = {
    "APARTMENT": 1.00,
    "PENTHOUSE": 1.35,
    "HOUSE": 0.90,
    "STUDIO": 1.10,
    "COMMERCIAL": 0.80,
}

# Plausible area per bedroom count (min, max).
AREA_BY_BEDROOMS = {
    0: (24, 38),
    1: (38, 55),
    2: (55, 82),
    3: (82, 130),
    4: (130, 215),
}

GENERAL_FEATURES = [
    "varanda", "portaria 24h", "elevador", "área de serviço", "reformado",
    "andar alto", "sol da manhã", "pet friendly", "salão de festas",
    "churrasqueira", "academia", "piscina", "playground", "mobiliado",
    "closet", "escritório", "próximo ao metrô", "rua tranquila",
    "aceita permuta", "vista livre",
]

BEACH_FEATURES = ["vista mar", "duas quadras da praia", "vista parcial mar"]

OPENINGS = [
    "Excelente {tipo} em {bairro}, ideal para quem busca localização e conforto.",
    "{tipo} bem distribuído em {bairro}, pronto para morar.",
    "Oportunidade em {bairro}: {tipo} com ótima planta e boa iluminação natural.",
    "{tipo} em uma das ruas mais procuradas de {bairro}.",
    "Charmoso {tipo} em {bairro}, com acabamento cuidadoso.",
]

SALE_CLOSINGS = [
    "Ótima opção para primeira moradia.",
    "Imóvel indicado para famílias que querem espaço e boa vizinhança.",
    "Boa alternativa para quem busca valorização na região.",
    "Documentação em ordem e pronto para financiamento.",
    "Indicado para investidores que buscam renda de aluguel na região.",
]

RENTAL_CLOSINGS = [
    "Disponível para locação imediata.",
    "Contrato flexível, ideal para quem precisa se mudar rápido.",
    "Ótimo custo-benefício de aluguel para a região.",
    "Locação sem burocracia, aceita fiador ou seguro fiança.",
]

DATES = [
    "2026-06-14", "2026-06-28", "2026-07-05", "2026-07-19",
    "2026-07-30", "2026-08-08", "2026-08-16", "2026-08-22",
]


def _readable_type(property_type):
    return {
        "APARTMENT": "apartamento",
        "PENTHOUSE": "cobertura",
        "HOUSE": "casa",
        "STUDIO": "studio",
        "COMMERCIAL": "sala comercial",
    }[property_type]


def _pick_type(rng):
    return rng.choices(
        ["APARTMENT", "PENTHOUSE", "HOUSE", "STUDIO", "COMMERCIAL"],
        weights=[62, 9, 13, 11, 5],
    )[0]


def _pick_bedrooms(rng, property_type):
    if property_type == "STUDIO":
        return 0
    if property_type == "COMMERCIAL":
        return 0
    if property_type == "HOUSE":
        return rng.choices([2, 3, 4], weights=[20, 45, 35])[0]
    if property_type == "PENTHOUSE":
        return rng.choices([2, 3, 4], weights=[25, 45, 30])[0]

    return rng.choices([1, 2, 3, 4], weights=[18, 34, 36, 12])[0]


def _pick_features(rng, neighborhood, property_type):
    pool = list(GENERAL_FEATURES)
    if neighborhood in BEACH_NEIGHBORHOODS:
        pool += BEACH_FEATURES

    count = rng.randint(3, 6)
    chosen = rng.sample(pool, count)

    if property_type == "PENTHOUSE" and "churrasqueira" not in chosen:
        chosen.append("churrasqueira")
    if property_type == "HOUSE" and rng.random() < 0.6:
        chosen.append("quintal")

    return sorted(set(chosen))


def _build_description(rng, prop):
    readable = _readable_type(prop["property_type"])
    opening = rng.choice(OPENINGS).format(
        tipo=readable.capitalize(), bairro=prop["neighborhood"]
    )

    body = "São %d m2" % round(prop["area_m2"])
    if prop["bedrooms"]:
        body += ", %d quartos" % prop["bedrooms"]
        if prop["suites"]:
            body += " (sendo %d suíte)" % prop["suites"]
    body += ", %d banheiro(s)" % prop["bathrooms"]
    body += " e %s." % ("%d vaga(s) de garagem" % prop["parking"] if prop["parking"]
                        else "sem vaga de garagem")

    highlights = "Destaques: " + ", ".join(prop["features"]) + "."

    if prop["deal_type"] == "RENTAL":
        closing = rng.choice(RENTAL_CLOSINGS)
    else:
        closing = rng.choice(SALE_CLOSINGS)

    return " ".join([opening, body, highlights, closing])


def _build_title(prop):
    readable = _readable_type(prop["property_type"]).capitalize()

    if prop["bedrooms"]:
        return "%s de %d quartos em %s" % (
            readable, prop["bedrooms"], prop["neighborhood"]
        )

    return "%s em %s" % (readable, prop["neighborhood"])


def generate(total=140, seed=SEED):
    rng = random.Random(seed)
    neighborhoods = list(schema.NEIGHBORHOODS.keys())

    properties = []
    counter = 0

    # Distributed by neighborhood in rounds, so every neighborhood in the
    # catalogue holds inventory for both deal types. Without that, a lead asking
    # for a specific neighborhood falls into relaxation for no reason.
    round_index = 0
    while counter < total:
        for neighborhood_index, neighborhood in enumerate(neighborhoods):
            if counter >= total:
                break

            zone, city, lat, lon, price_m2 = schema.NEIGHBORHOODS[neighborhood]

            # A third of the base is rental, spread along the diagonal
            # (round + neighborhood) so each neighborhood receives both deal
            # types across rounds, instead of whole rounds of a single type.
            # Without this, a neighborhood could end up with no rentals at all.
            if (round_index + neighborhood_index) % 3 == 0:
                deal_type = "RENTAL"
            else:
                deal_type = "SALE"

            property_type = _pick_type(rng)
            bedrooms = _pick_bedrooms(rng, property_type)

            area_min, area_max = AREA_BY_BEDROOMS[bedrooms]
            if property_type == "HOUSE":
                area_min, area_max = area_min * 1.2, area_max * 1.5
            area = round(rng.uniform(area_min, area_max), 1)

            # Reference sale value, with market dispersion.
            sale_value = area * price_m2 * TYPE_FACTOR[property_type] * rng.uniform(0.82, 1.18)
            sale_value = round(sale_value / 1000) * 1000

            # Typical monthly yield in the Rio market: 0.35% to 0.55%.
            monthly_factor = rng.uniform(0.0035, 0.0055)

            if deal_type == "RENTAL":
                price = round(sale_value * monthly_factor / 50) * 50
                annual_yield = None
            else:
                price = sale_value
                annual_yield = round(monthly_factor * 12 * 100, 2)

            bathrooms = max(1, min(4, bedrooms if bedrooms else 1))
            suites = 1 if bedrooms >= 3 and rng.random() < 0.7 else 0
            parking = rng.choices([0, 1, 2, 3], weights=[22, 46, 26, 6])[0]

            counter += 1
            prop = {
                "id": "IMV-%04d" % counter,
                "title": None,
                "description": None,
                "deal_type": deal_type,
                "property_type": property_type,
                "price": float(price),
                "condo_fee": float(round(area * rng.uniform(11, 21) / 10) * 10),
                "property_tax": float(round(sale_value * 0.006 / 12 / 10) * 10),
                "bedrooms": bedrooms,
                "suites": suites,
                "bathrooms": bathrooms,
                "parking": parking,
                "area_m2": area,
                "neighborhood": neighborhood,
                "zone": zone,
                "city": city,
                # ~1 km of jitter so geographic search has something to order
                # within a single neighborhood.
                "lat": round(lat + rng.uniform(-0.009, 0.009), 6),
                "lon": round(lon + rng.uniform(-0.009, 0.009), 6),
                "features": _pick_features(rng, neighborhood, property_type),
                "accepts_financing": deal_type == "SALE" and rng.random() < 0.85,
                "annual_yield_pct": annual_yield,
                # Some deliberately unavailable: the status filter needs a
                # visible effect, otherwise nobody notices it exists.
                "status": rng.choices(
                    ["AVAILABLE", "RESERVED", "SOLD"],
                    weights=[88, 8, 4],
                )[0],
                "updated_at": rng.choice(DATES),
            }

            prop["title"] = _build_title(prop)
            prop["description"] = _build_description(rng, prop)

            properties.append(prop)

        round_index += 1

    return properties


JSON_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "Property",
    "description": (
        "Property document from the simulated base of the Real Estate SDR Agent. "
        "Generated by ai-memory-rag/scripts/generate_properties.py. "
        "100% synthetic data."
    ),
    "type": "object",
    "required": list(schema.REQUIRED_FIELDS),
    "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "pattern": "^IMV-[0-9]{4}$"},
        "title": {"type": "string"},
        "description": {"type": "string"},
        "deal_type": {"type": "string", "enum": list(schema.DEAL_TYPES)},
        "property_type": {"type": "string", "enum": list(schema.PROPERTY_TYPES)},
        "price": {"type": "number", "exclusiveMinimum": 0,
                  "description": "sale value or monthly rent, depending on deal_type"},
        "condo_fee": {"type": "number", "minimum": 0},
        "property_tax": {"type": "number", "minimum": 0},
        "bedrooms": {"type": "integer", "minimum": 0},
        "suites": {"type": "integer", "minimum": 0},
        "bathrooms": {"type": "integer", "minimum": 1},
        "parking": {"type": "integer", "minimum": 0},
        "area_m2": {"type": "number", "exclusiveMinimum": 0},
        "neighborhood": {"type": "string", "enum": sorted(schema.NEIGHBORHOODS.keys())},
        "zone": {"type": "string", "enum": list(schema.ZONES)},
        "city": {"type": "string"},
        "lat": {"type": "number", "minimum": -90, "maximum": 90},
        "lon": {"type": "number", "minimum": -180, "maximum": 180},
        "features": {"type": "array", "items": {"type": "string"}},
        "accepts_financing": {"type": "boolean"},
        "annual_yield_pct": {
            "type": ["number", "null"],
            "description": "estimated annual yield; null when deal_type is RENTAL",
        },
        "status": {"type": "string", "enum": list(schema.STATUSES)},
        "updated_at": {"type": "string", "format": "date"},
    },
}


def _report(properties):
    def count_by(field):
        counts = {}
        for prop in properties:
            value = prop[field]
            counts[value] = counts.get(value, 0) + 1
        return dict(sorted(counts.items(), key=lambda p: -p[1]))

    available = [p for p in properties if p["status"] == "AVAILABLE"]
    sales = [p["price"] for p in available if p["deal_type"] == "SALE"]
    rentals = [p["price"] for p in available if p["deal_type"] == "RENTAL"]

    print("Total: %d properties (%d available)" % (len(properties), len(available)))
    print("By deal type: %s" % count_by("deal_type"))
    print("By zone: %s" % count_by("zone"))
    print("By bedrooms: %s" % count_by("bedrooms"))
    if sales:
        print("Sale:   R$ %s to R$ %s"
              % (format(int(min(sales)), ","), format(int(max(sales)), ",")))
    if rentals:
        print("Rental: R$ %s to R$ %s"
              % (format(int(min(rentals)), ","), format(int(max(rentals)), ",")))


def _main():
    parser = argparse.ArgumentParser(description="Generate the simulated property base.")
    parser.add_argument("--total", type=int, default=140)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    properties = generate(total=args.total, seed=args.seed)

    problems = []
    for prop in properties:
        for problem in schema.validate_property(prop):
            problems.append("%s: %s" % (prop["id"], problem))

    if problems:
        print("FAILED: generated base is invalid:")
        for problem in problems[:20]:
            print("  " + problem)
        return 1

    os.makedirs(os.path.dirname(DATA_TARGET), exist_ok=True)
    os.makedirs(os.path.dirname(SCHEMA_TARGET), exist_ok=True)

    with open(DATA_TARGET, "w", encoding="utf-8") as handle:
        json.dump(
            {
                "source": "synthetic",
                "generator": "ai-memory-rag/scripts/generate_properties.py",
                "seed": args.seed,
                "total": len(properties),
                "properties": properties,
            },
            handle,
            ensure_ascii=False,
            indent=2,
        )

    with open(SCHEMA_TARGET, "w", encoding="utf-8") as handle:
        json.dump(JSON_SCHEMA, handle, ensure_ascii=False, indent=2)

    _report(properties)
    print()
    print("Base:   %s" % DATA_TARGET)
    print("Schema: %s" % SCHEMA_TARGET)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
