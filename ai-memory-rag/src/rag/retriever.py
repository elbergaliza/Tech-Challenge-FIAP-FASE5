"""
Hybrid property search matched to a lead's profile.

The strategy, and why it is hybrid rather than purely vector based:

  1. STRUCTURED FILTER (eliminating). Price, bedrooms, parking, deal type,
     region and yield are hard constraints. Vector search fails badly at these:
     the embedding of "até 500 mil" sits close to that of "800 mil", and a
     property outside the budget must never surface just because its
     description reads similarly. These fields are applied as boolean
     predicates, the equivalent of Azure Cognitive Search's
     `filter="price lt 500000 and bedrooms ge 3"`.

  2. SEMANTIC RANKING (ordering). Among the survivors, order by cosine
     similarity against the conversation text. This is where "quero algo
     silencioso para trabalhar de casa" finds "escritório, sol da manhã, rua
     tranquila".

  3. BOOSTING (fine tuning). Small, explainable bonuses for exact
     neighborhood, exact bedroom count, budget utilisation and yield above
     expectation. Analogous to Azure's per-field boosting.

  4. PROGRESSIVE RELAXATION. When the hard filter returns nothing, loosen one
     constraint at a time and record what was loosened. A human SDR does
     exactly this: "I could not find a 3-bedroom under 500k in Zona Sul, but I
     have these slightly above budget". Returning an empty list is the worst
     possible answer for a lead.

Every result carries a `reason` in plain text. It serves three readers: the
LLM, which paraphrases it for the lead; the broker, who sees it on the
dashboard; and us, when the ranking looks odd and needs debugging. Cheap
explainability, in the spirit of what lesson 04 on Privacy asks of AI systems.

NOTE ON LANGUAGE: identifiers, field names and enum values are in English.
Only two things stay in Portuguese: proper nouns as values ("Copacabana") and
every string a lead or broker reads (`reason`, `mismatches`, the prompt block).
"""

import math
import re

import lead_profile

from . import schema
from .embeddings import get_embedder

EARTH_RADIUS_KM = 6371.0

# Final score weights. Centralised on purpose: these are the business levers of
# the ranking, and tuning them should not require reading the rest of the file.
WEIGHT_SIMILARITY = 1.00

# When a lead asks for "near X", proximity is a primary criterion, not a garnish.
WEIGHT_PROXIMITY = 0.50

BONUS_EXACT_NEIGHBORHOOD = 0.15
BONUS_EXACT_BEDROOMS = 0.08
BONUS_MAX_YIELD = 0.10

# Budget utilisation: between two options under the ceiling, the one that uses
# more of the budget is the better fit. Rewarding "the cheapest" would be wrong;
# it makes the agent offer a 200k studio to an investor with a 1M ticket.
BONUS_BUDGET_USAGE = 0.20

# Penalties. They only bite after relaxation, because before that the hard
# filter would already have removed the property. They exist so that, once
# loosened, the ranking still prefers what is closest to what the lead asked.
#
# Larger than BONUS_BUDGET_USAGE on purpose: a missing bedroom is an unmet
# need, using the budget well is only a preference. With the smaller weight, a
# 1-bedroom at the top of the budget beat a 2-bedroom for someone who asked
# for 4.
PENALTY_MISSING_BEDROOM = 0.25
PENALTY_OVER_BUDGET = 0.50


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class Filters:
    """Hard search constraints. A None field means "do not restrict"."""

    FIELDS = (
        "deal_type", "property_type", "min_price", "max_price",
        "min_bedrooms", "min_parking", "min_area",
        "neighborhood", "zone", "city",
        "lat", "lon", "radius_km",
        "min_yield", "features",
    )

    def __init__(self, **kwargs):
        unknown = set(kwargs) - set(self.FIELDS)
        if unknown:
            raise TypeError("unknown filters: %s" % sorted(unknown))

        for field in self.FIELDS:
            setattr(self, field, kwargs.get(field))

        if self.features is None:
            self.features = []

    def replace(self, **changes):
        current = {field: getattr(self, field) for field in self.FIELDS}
        current.update(changes)
        return Filters(**current)

    def __repr__(self):
        active = {
            field: getattr(self, field)
            for field in self.FIELDS
            if getattr(self, field) not in (None, [])
        }
        return "Filters(%s)" % ", ".join("%s=%r" % item for item in sorted(active.items()))


# ---------------------------------------------------------------------------
# Translating Person 1's profile into filters
# ---------------------------------------------------------------------------

# Alternation order matters: longer forms first, otherwise "mil" matches inside
# "milhões" and 2 million becomes 2 thousand. The trailing \b keeps a bare "m"
# from matching inside words such as "metros".
_NUMBER_WITH_SCALE = re.compile(
    r"(\d[\d.,]*\d|\d)\s*(milh(?:ao|ão|oes|ões)|mil|mi|m|k)?\b",
    re.IGNORECASE,
)

_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")

# Values below this are noise, not budget: when the range is parsed from free
# text, numbers like the "3" in "3 quartos" would otherwise become a price.
MIN_PLAUSIBLE_VALUE = 100


def _to_float(number_text, scale):
    """Convert a Portuguese number to float, handling the thousands separator.

    In pt-BR the dot is ambiguous: decimal in "1.5m", thousands in "500.000".
    Disambiguation uses the shape of the number, not the scale suffix.
    """
    if _THOUSANDS_DOT.match(number_text):
        cleaned = number_text.replace(".", "")
    elif _THOUSANDS_COMMA.match(number_text):
        cleaned = number_text.replace(",", "")
    else:
        cleaned = number_text.replace(",", ".")

    value = float(cleaned)

    scale = (scale or "").lower()
    if scale in ("k", "mil"):
        return value * 1_000
    if scale.startswith("m"):
        return value * 1_000_000

    return value


def parse_price_range(text):
    """Parse a price range from Person 1's output or from raw conversation text.

    >>> parse_price_range("500k-800k")
    (500000.0, 800000.0)
    >>> parse_price_range("300k")
    (None, 300000.0)
    >>> parse_price_range("1.5m")
    (None, 1500000.0)
    >>> parse_price_range("undefined")
    (None, None)

    A single value is read as a CEILING, not a floor: when a lead says "tenho
    300 mil", they are stating the limit of what they can pay.

    Integration note: `extrair_preco` in ai-core returns both values through a
    `set()`, whose string iteration order varies between Python runs. That is
    why the numbers are sorted here, so "800k-500k" and "500k-800k" produce the
    same result.
    """
    if not text or str(text).strip().lower() in ("undefined", "", "none"):
        return None, None

    values = []
    for number, scale in _NUMBER_WITH_SCALE.findall(str(text)):
        if not number:
            continue
        values.append(_to_float(number, scale))

    values = sorted(v for v in values if v >= MIN_PLAUSIBLE_VALUE)

    if not values:
        return None, None
    if len(values) == 1:
        return None, values[0]

    return values[0], values[-1]


def _to_int(value):
    if value is None:
        return None

    text = str(value).strip().lower()
    if text in ("undefined", "", "none"):
        return None

    found = re.search(r"\d+", text)
    return int(found.group(0)) if found else None


def parse_percentage(text):
    """Extract an expected annual return, in percent.

    >>> parse_percentage("espero 8% ao ano")
    8.0
    >>> parse_percentage("undefined")
    """
    if not text or str(text).strip().lower() in ("undefined", "", "none"):
        return None

    found = re.search(r"(\d+(?:[.,]\d+)?)\s*%", str(text))
    if not found:
        return None

    return float(found.group(1).replace(",", "."))


# Lead intent -> what they are actually shopping for. An investor buys, so
# INVEST maps to SALE plus a yield floor.
_INTENT_TO_DEAL_TYPE = {"BUY": "SALE", "RENT": "RENTAL", "INVEST": "SALE"}


def filters_from_profile(profile, expected_return=None, radius_km=None):
    """Convert a lead profile into Filters.

    This is the seam between Part 1 and Part 2. It accepts either our English
    profile or Person 1's raw `dados_coletados`: `lead_profile.from_agent()`
    normalises both, so callers can pass `resultado["dados_coletados"]` straight
    through.
    """
    data = lead_profile.from_agent(profile)

    intent = data.get("intent")
    deal_type = _INTENT_TO_DEAL_TYPE.get(intent)

    min_price, max_price = parse_price_range(data.get("price_range"))
    neighborhood, zone = schema.resolve_region(data.get("region"))

    # Investor profile: the expected return becomes a yield floor. If the lead
    # gave no number, no floor is applied, so ranking still favours profitable
    # properties without discarding inventory.
    min_yield = None
    if intent == "INVEST" and expected_return:
        min_yield = parse_percentage(expected_return)

    filters = Filters(
        deal_type=deal_type,
        min_price=min_price,
        max_price=max_price,
        min_bedrooms=_to_int(data.get("bedrooms")),
        neighborhood=neighborhood,
        zone=zone,
        min_yield=min_yield,
    )

    if radius_km and neighborhood and neighborhood in schema.NEIGHBORHOODS:
        _, _, lat, lon, _ = schema.NEIGHBORHOODS[neighborhood]
        filters = filters.replace(
            lat=lat, lon=lon, radius_km=radius_km, neighborhood=None
        )

    return filters


# ---------------------------------------------------------------------------
# Geography
# ---------------------------------------------------------------------------

def distance_km(lat1, lon1, lat2, lon2):
    """Haversine. Equivalent to Azure Cognitive Search's `geo.distance()`."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)

    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Applying the filters
# ---------------------------------------------------------------------------

def _matches(prop, filters):
    if prop.get("status") != "AVAILABLE":
        return False

    if filters.deal_type and prop.get("deal_type") != filters.deal_type:
        return False

    if filters.property_type and prop.get("property_type") != filters.property_type:
        return False

    price = prop.get("price") or 0
    if filters.min_price is not None and price < filters.min_price:
        return False
    if filters.max_price is not None and price > filters.max_price:
        return False

    if filters.min_bedrooms is not None and (prop.get("bedrooms") or 0) < filters.min_bedrooms:
        return False

    if filters.min_parking is not None and (prop.get("parking") or 0) < filters.min_parking:
        return False

    if filters.min_area is not None and (prop.get("area_m2") or 0) < filters.min_area:
        return False

    if filters.neighborhood and prop.get("neighborhood") != filters.neighborhood:
        return False

    if filters.zone and prop.get("zone") != filters.zone:
        return False

    if filters.city and prop.get("city") != filters.city:
        return False

    if filters.radius_km is not None and filters.lat is not None:
        if distance_km(filters.lat, filters.lon, prop["lat"], prop["lon"]) > filters.radius_km:
            return False

    if filters.min_yield is not None:
        yield_pct = prop.get("annual_yield_pct")
        if yield_pct is None or yield_pct < filters.min_yield:
            return False

    if filters.features:
        present = {c.lower() for c in prop.get("features", [])}
        for wanted in filters.features:
            if wanted.lower() not in present:
                return False

    return True


def apply_filters(index, filters):
    """Return the set of ids satisfying every hard constraint."""
    return {d["id"] for d in index.documents if _matches(d, filters)}


# ---------------------------------------------------------------------------
# Progressive relaxation
# ---------------------------------------------------------------------------

def _relaxation_steps(original):
    """Loosening steps, from least to most costly for the lead.

    The order is a business decision, not a technical one: showing a property
    15% over budget hurts less than showing one in the wrong neighborhood, and
    widening from neighborhood to zone hurts less than leaving the zone. The
    bedroom count is only really loosened at the end, because someone who needs
    4 bedrooms needs 4 bedrooms.

    Each step is computed from the ORIGINAL filters, not from the current
    state. That way "budget widened by 40%" means 40% above what the lead said,
    not 40% above the previous step, and the label sent to the LLM prompt is
    literally true.
    """
    steps = []

    if original.max_price is not None:
        steps.append((
            "orçamento ampliado em 15%",
            lambda f: f.replace(max_price=original.max_price * 1.15),
        ))

    if original.min_bedrooms is not None and original.min_bedrooms > 1:
        steps.append((
            "aceitando um quarto a menos",
            lambda f: f.replace(min_bedrooms=original.min_bedrooms - 1),
        ))

    if original.neighborhood:
        zone_of_neighborhood = schema.NEIGHBORHOODS.get(original.neighborhood, (None,))[0]
        steps.append((
            "busca ampliada do bairro para a zona inteira",
            lambda f: f.replace(neighborhood=None, zone=zone_of_neighborhood),
        ))

    if original.radius_km is not None:
        steps.append((
            "raio de busca dobrado",
            lambda f: f.replace(radius_km=original.radius_km * 2),
        ))

    if original.features:
        steps.append((
            "características desejadas tratadas como preferência",
            lambda f: f.replace(features=[]),
        ))

    if original.min_yield is not None:
        steps.append((
            "expectativa de rentabilidade reduzida em 1 ponto",
            lambda f: f.replace(min_yield=max(0.0, original.min_yield - 1.0)),
        ))

    if original.max_price is not None:
        steps.append((
            "orçamento ampliado em 40%",
            lambda f: f.replace(max_price=original.max_price * 1.40),
        ))

    # Last geographic resort. Included whenever ANY geographic restriction
    # existed, including when the lead asked for a neighborhood: the previous
    # step already swapped neighborhood for zone, and without this one the
    # search would stay stuck in the zone and could end up empty.
    if original.zone or original.neighborhood or original.city:
        steps.append((
            "busca estendida para fora da região pedida",
            lambda f: f.replace(zone=None, neighborhood=None, city=None),
        ))

    if original.min_bedrooms is not None and original.min_bedrooms > 1:
        steps.append((
            "número de quartos flexibilizado",
            lambda f: f.replace(min_bedrooms=1),
        ))

    # Final resort. Keeps only the deal type, the one constraint it never makes
    # sense to violate: never offer a sale to someone who wants to rent. It
    # guarantees the lead gets some option instead of silence, and the label is
    # explicit so the LLM warns that the profile was not met.
    steps.append((
        "mostrando as opções mais próximas, fora dos critérios informados",
        lambda f: Filters(deal_type=original.deal_type),
    ))

    return steps


# ---------------------------------------------------------------------------
# Scoring and reasons
# ---------------------------------------------------------------------------

def _currency(value):
    if value is None:
        return "-"

    whole = "%d" % round(value)
    parts = []
    while len(whole) > 3:
        parts.insert(0, whole[-3:])
        whole = whole[:-3]
    parts.insert(0, whole)

    return "R$ " + ".".join(parts)


def _score(prop, similarity, original, distance=None, radius_reference=None):
    """Final score plus the list of reasons that justify it.

    `original` are the filters the LEAD asked for, not the relaxed ones. That
    is what lets the reason say "19% acima do orçamento" instead of pretending
    the property is within budget.

    Reason strings stay in Portuguese: they reach the lead and the broker.
    """
    score = WEIGHT_SIMILARITY * similarity
    reasons = []

    # -- location -----------------------------------------------------------

    if original.neighborhood and prop.get("neighborhood") == original.neighborhood:
        score += BONUS_EXACT_NEIGHBORHOOD
        reasons.append("no bairro pedido")
    elif original.zone and prop.get("zone") == original.zone:
        reasons.append("na zona pedida")

    if distance is not None and radius_reference:
        proximity = max(0.0, 1.0 - distance / float(radius_reference))
        score += WEIGHT_PROXIMITY * proximity
        reasons.append("a %.1f km do ponto de referência" % distance)

    # -- bedrooms -----------------------------------------------------------

    if original.min_bedrooms is not None:
        bedrooms = prop.get("bedrooms") or 0
        if bedrooms == original.min_bedrooms:
            score += BONUS_EXACT_BEDROOMS
            reasons.append("exatamente %d quartos" % original.min_bedrooms)
        elif bedrooms > original.min_bedrooms:
            reasons.append("%d quartos, mais do que pediu" % bedrooms)
        else:
            missing = original.min_bedrooms - bedrooms
            score -= PENALTY_MISSING_BEDROOM * missing
            reasons.append("%d quartos, %d a menos do que pediu" % (bedrooms, missing))

    # -- budget -------------------------------------------------------------

    if original.max_price is not None:
        price = prop.get("price") or 0
        usage = price / float(original.max_price)

        if usage > 1.0:
            excess = usage - 1.0
            score -= PENALTY_OVER_BUDGET * excess
            reasons.append("%.0f%% acima do orçamento" % (excess * 100))
        else:
            score += BONUS_BUDGET_USAGE * usage
            if usage >= 0.90:
                reasons.append("no topo do orçamento")
            elif usage >= 0.60:
                reasons.append("dentro do orçamento")
            else:
                reasons.append("bem abaixo do orçamento")

    # -- yield (investor profile) -------------------------------------------

    yield_pct = prop.get("annual_yield_pct")
    if original.min_yield is not None and yield_pct is not None:
        surplus = yield_pct - original.min_yield
        if surplus > 0:
            score += BONUS_MAX_YIELD * min(1.0, surplus / 2.0)
        reasons.append("rentabilidade de %.1f%% ao ano" % yield_pct)
    elif yield_pct is not None and original.deal_type == "SALE":
        reasons.append("rentabilidade estimada de %.1f%% ao ano" % yield_pct)

    return score, reasons


def _build_reason(prop, reasons):
    summary = "%s de %d quartos em %s, %s, %d m2" % (
        # Portuguese label: this string is read by the lead and the broker.
        schema.PROPERTY_TYPE_LABELS.get(prop.get("property_type"), "Imóvel"),
        prop.get("bedrooms") or 0,
        prop.get("neighborhood", "-"),
        _currency(prop.get("price")),
        round(prop.get("area_m2") or 0),
    )

    if prop.get("parking"):
        summary += ", %d vaga(s)" % prop["parking"]

    if not reasons:
        return summary + "."

    return summary + ". Encaixe: " + ", ".join(reasons) + "."


class RecommendedProperty:

    def __init__(self, prop, score, similarity, reason, distance_km=None):
        self.property = prop
        self.score = score
        self.similarity = similarity
        self.reason = reason
        self.distance_km = distance_km

    @property
    def id(self):
        return self.property["id"]

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.property.get("title"),
            "neighborhood": self.property.get("neighborhood"),
            "zone": self.property.get("zone"),
            "price": self.property.get("price"),
            "bedrooms": self.property.get("bedrooms"),
            "area_m2": self.property.get("area_m2"),
            "parking": self.property.get("parking"),
            "annual_yield_pct": self.property.get("annual_yield_pct"),
            "score": round(self.score, 4),
            "similarity": round(self.similarity, 4),
            "reason": self.reason,
            "distance_km": round(self.distance_km, 1) if self.distance_km else None,
        }

    def __repr__(self):
        return "<RecommendedProperty %s score=%.3f>" % (self.id, self.score)


def _mismatch_notes(recommendations, original):
    """Describe, constraint by constraint, how the result differs from the ask.

    This exists because the relaxation ladder is a fact about the SEARCH, not
    about the RESULT: the ladder may climb five steps and the returned
    properties still respect everything the lead asked for. Sending the raw
    steps to the prompt made the LLM tell the lead "I had to widen your budget"
    when it had not. These notes look at what was actually returned.

    Note strings stay in Portuguese: they end up in front of the lead.
    """
    properties = [r.property for r in recommendations]
    if not properties:
        return []

    notes = []

    if original.max_price is not None:
        over = [p for p in properties if (p.get("price") or 0) > original.max_price]
        if over:
            worst = max((p["price"] / original.max_price - 1) for p in over)
            notes.append(
                "%d das %d opções passam do orçamento informado (a maior em %.0f%%)"
                % (len(over), len(properties), worst * 100)
            )

    if original.min_bedrooms is not None:
        fewer = [p for p in properties if (p.get("bedrooms") or 0) < original.min_bedrooms]
        if fewer:
            notes.append(
                "%d das %d opções têm menos de %d quartos"
                % (len(fewer), len(properties), original.min_bedrooms)
            )

    if original.neighborhood:
        outside = [p for p in properties if p.get("neighborhood") != original.neighborhood]
        if outside:
            names = sorted({p.get("neighborhood") for p in outside})
            notes.append(
                "%d das %d opções não estão em %s (estão em %s)"
                % (len(outside), len(properties), original.neighborhood, ", ".join(names))
            )

    if original.zone:
        outside = [p for p in properties if p.get("zone") != original.zone]
        if outside:
            notes.append(
                "%d das %d opções estão fora da %s"
                % (len(outside), len(properties), original.zone)
            )

    if original.radius_km is not None and original.lat is not None:
        far = [
            r for r in recommendations
            if r.distance_km is not None and r.distance_km > original.radius_km
        ]
        if far:
            farthest = max(r.distance_km for r in far)
            notes.append(
                "%d das %d opções estão além dos %g km pedidos "
                "(a mais distante a %.1f km)"
                % (len(far), len(properties), original.radius_km, farthest)
            )

    if original.min_yield is not None:
        below = [
            p for p in properties
            if p.get("annual_yield_pct") is None
            or p["annual_yield_pct"] < original.min_yield
        ]
        if below:
            notes.append(
                "%d das %d opções rendem menos de %.1f%% ao ano"
                % (len(below), len(properties), original.min_yield)
            )

    for wanted in original.features:
        without = [
            p for p in properties
            if wanted.lower() not in {c.lower() for c in p.get("features", [])}
        ]
        if without:
            notes.append(
                "%d das %d opções não têm '%s'"
                % (len(without), len(properties), wanted)
            )

    return notes


class SearchResult:
    """Result of a search.

    relaxations  ladder steps that were walked. A fact about the search.
    mismatches   how the RETURNED properties differ from what the lead asked
                 for. This is what goes into the LLM prompt, because it is what
                 the lead needs to hear. Empty means everything matched.
    """

    def __init__(self, recommendations, relaxations, candidate_count, filters_used,
                 mismatches=None):
        self.recommendations = recommendations
        self.relaxations = relaxations
        self.candidate_count = candidate_count
        self.filters_used = filters_used
        self.mismatches = mismatches if mismatches is not None else []

    def __len__(self):
        return len(self.recommendations)

    def __iter__(self):
        return iter(self.recommendations)

    @property
    def was_relaxed(self):
        """True when some returned property falls short of what the lead asked."""
        return bool(self.mismatches)

    @property
    def matches_profile(self):
        return bool(self.recommendations) and not self.mismatches

    def to_dict(self):
        return {
            "candidate_count": self.candidate_count,
            "relaxations": self.relaxations,
            "mismatches": self.mismatches,
            "matches_profile": self.matches_profile,
            "properties": [r.to_dict() for r in self.recommendations],
        }


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(index, filters, query_text="", embedder=None, top_k=3,
           allow_relaxation=True):
    """Hybrid search. Always returns a SearchResult, never None.

    `query_text` should be the free text of the conversation (the lead's most
    recent messages), not a structured summary: it carries the semantic signal
    the filters cannot capture.
    """
    embedder = embedder or get_embedder(prefer="hashing")

    original_filters = filters
    current_filters = filters
    relaxations = []

    ids = apply_filters(index, current_filters)

    if allow_relaxation and len(ids) < top_k:
        for description, loosen in _relaxation_steps(original_filters):
            if len(ids) >= top_k:
                break
            current_filters = loosen(current_filters)
            relaxations.append(description)
            ids = apply_filters(index, current_filters)

    if not ids:
        return SearchResult([], relaxations, 0, current_filters)

    query_vector = embedder.embed_query(query_text or "")
    ranked = index.similarities(query_vector, allowed_ids=ids)

    # Reference for the proximity score: the radius actually used in the search
    # (already relaxed, if relaxation happened). With no declared radius, use an
    # urban scale so distance still orders instead of being ignored.
    radius_reference = current_filters.radius_km or original_filters.radius_km or 15.0

    recommendations = []
    for prop, similarity in ranked:
        distance = None
        if original_filters.lat is not None:
            distance = distance_km(
                original_filters.lat, original_filters.lon,
                prop["lat"], prop["lon"],
            )

        score, reasons = _score(
            prop, similarity, original_filters,
            distance=distance, radius_reference=radius_reference,
        )

        recommendations.append(RecommendedProperty(
            prop=prop,
            score=score,
            similarity=similarity,
            reason=_build_reason(prop, reasons),
            distance_km=distance,
        ))

    recommendations.sort(key=lambda r: r.score, reverse=True)
    selected = recommendations[:top_k]

    return SearchResult(
        recommendations=selected,
        relaxations=relaxations,
        candidate_count=len(ids),
        filters_used=current_filters,
        mismatches=_mismatch_notes(selected, original_filters),
    )


def search_for_lead(index, profile, query_text="", embedder=None,
                    top_k=3, expected_return=None, radius_km=None):
    """Integration shortcut: takes Person 1's dict (or our profile) and searches."""
    filters = filters_from_profile(
        profile,
        expected_return=expected_return,
        radius_km=radius_km,
    )
    return search(index, filters, query_text, embedder=embedder, top_k=top_k)


# ---------------------------------------------------------------------------
# Output for the agent prompt
# ---------------------------------------------------------------------------

def format_for_prompt(result):
    """Render the result as a text block to inject into the LLM prompt.

    The format is designed to be cheap in tokens and hard for the model to
    hallucinate on top of: every property numbered, with an explicit id, so the
    reply to the lead can cite real properties and the broker can trace them
    later.

    Portuguese, because it goes into a Portuguese prompt.
    """
    if not result.recommendations:
        return (
            "IMÓVEIS ENCONTRADOS: nenhum no momento.\n"
            "Instrução: não invente imóveis. Reconheça que não há opção exata "
            "agora, pergunte se o lead aceita flexibilizar algum critério e "
            "ofereça avisá-lo quando entrar algo no perfil."
        )

    lines = ["IMÓVEIS ENCONTRADOS NA BASE (use apenas estes, não invente outros):"]

    for position, recommendation in enumerate(result.recommendations, start=1):
        lines.append("%d. [%s] %s" % (position, recommendation.id, recommendation.reason))

    if result.mismatches:
        lines.append(
            "OBSERVAÇÃO: não havia opção que atendesse tudo o que o lead pediu. "
            "Diferenças do que ele pediu: " + "; ".join(result.mismatches) + ". "
            "Seja transparente sobre isso ao apresentar as opções, sem se "
            "desculpar em excesso, e pergunte qual critério ele prefere flexibilizar."
        )

    return "\n".join(lines)
