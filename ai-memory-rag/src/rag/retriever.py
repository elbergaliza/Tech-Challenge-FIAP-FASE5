"""
Busca híbrida de imóveis compatíveis com o perfil do lead.

A estratégia, e por que ela é híbrida e não puramente vetorial:

  1. FILTRO ESTRUTURADO (eliminatório). Preço, quartos, vagas, tipo de negócio,
     região e rentabilidade são restrições duras. Busca vetorial erra feio
     nisso: o embedding de "até 500 mil" fica perto do de "800 mil", e um
     imóvel fora do orçamento nunca deve aparecer só porque a descrição é
     parecida. Esses campos entram como predicado booleano, equivalente ao
     `filter="price lt 500000 and bedrooms ge 3"` do Azure Cognitive Search.

  2. RANKING SEMÂNTICO (ordenação). Entre os que passaram, ordena por
     similaridade de cosseno com o texto da conversa. É aqui que "quero algo
     silencioso para trabalhar de casa" encontra "escritório, sol da manhã, rua
     tranquila".

  3. BOOSTING (ajuste fino). Bônus pequenos e explicáveis para bairro exato,
     número de quartos exato, aproveitamento do orçamento e rentabilidade acima
     da esperada. Análogo ao boosting por campo do Azure.

  4. RELAXAMENTO PROGRESSIVO. Se o filtro duro não devolve nada, afrouxa uma
     restrição por vez e registra o que foi afrouxado. Um SDR humano faz
     exatamente isso: "não achei nada com 3 quartos até 500 mil na Zona Sul,
     mas tenho estas opções um pouco acima do orçamento". Devolver lista vazia
     seria a pior resposta possível para o lead.

Todo resultado carrega um campo `reason` em texto. Isso serve a três leitores:
o LLM, que parafraseia na resposta ao lead; o corretor, que vê no dashboard; e
a gente, quando o ranking sair estranho e for preciso depurar. É
explicabilidade barata, no espírito do que a Aula 04 de Privacidade pede de
sistemas de IA.

IDIOMA: identificadores, nomes de campo e valores de enum estão em inglês. Só
duas coisas ficam em português: nomes próprios como valor ("Copacabana") e todo
texto que um lead ou corretor lê (`reason`, `mismatches`, o bloco do prompt).
"""

import math
import re

import lead_profile

from . import schema
from .embeddings import get_embedder

EARTH_RADIUS_KM = 6371.0

# Pesos do score final. Centralizados de propósito: são as alavancas de negócio
# do ranking, e mexer neles não deveria exigir ler o resto do arquivo.
WEIGHT_SIMILARITY = 1.00

# Quando o lead pede "perto de X", proximidade é critério primário, não enfeite.
WEIGHT_PROXIMITY = 0.50

BONUS_EXACT_NEIGHBORHOOD = 0.15
BONUS_EXACT_BEDROOMS = 0.08
BONUS_MAX_YIELD = 0.10

# Aproveitamento do orçamento: entre duas opções dentro do teto, a que usa mais
# do orçamento encaixa melhor. Premiar "o mais barato" seria errado, faz o
# agente oferecer um studio de 200 mil a um investidor com ticket de 1 milhão.
BONUS_BUDGET_USAGE = 0.20

# Penalidades. Só têm efeito depois de relaxamento, porque antes disso o filtro
# duro já teria eliminado o imóvel. Servem para que, ao afrouxar, o ranking
# ainda prefira o que está mais perto do que o lead pediu.
#
# Maior que BONUS_BUDGET_USAGE de propósito: faltar um quarto é uma necessidade
# não atendida, aproveitar bem o orçamento é só uma preferência. Com o peso
# menor, um imóvel de 1 quarto no topo do orçamento vencia um de 2 quartos para
# quem tinha pedido 4.
PENALTY_MISSING_BEDROOM = 0.25
PENALTY_OVER_BUDGET = 0.50


# ---------------------------------------------------------------------------
# Filtros
# ---------------------------------------------------------------------------

class Filters:
    """Restrições duras da busca. Campo None significa "não restringe"."""

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
            raise TypeError("filtros desconhecidos: %s" % sorted(unknown))

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
# Tradução do perfil da Pessoa 1 para filtros
# ---------------------------------------------------------------------------

# A ordem das alternativas importa: formas mais longas primeiro, senão "mil"
# casa dentro de "milhões" e 2 milhões viram 2 mil. O \b final impede que o "m"
# solto case dentro de palavras como "metros".
_NUMBER_WITH_SCALE = re.compile(
    r"(\d[\d.,]*\d|\d)\s*(milh(?:ao|ão|oes|ões)|mil|mi|m|k)?\b",
    re.IGNORECASE,
)

_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")

# Valores abaixo disto são ruído, não orçamento: quando a faixa é extraída de
# texto livre, números como o "3" de "3 quartos" entrariam como preço.
MIN_PLAUSIBLE_VALUE = 100


def _to_float(number_text, scale):
    """Converte número em português para float, tratando o ponto de milhar.

    Em pt-BR o ponto é ambíguo: decimal em "1.5m", milhar em "500.000". A
    desambiguação usa a forma do número, não o sufixo de escala.
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
    """Interpreta a faixa de preço vinda da Pessoa 1 ou da conversa crua.

    >>> parse_price_range("500k-800k")
    (500000.0, 800000.0)
    >>> parse_price_range("300k")
    (None, 300000.0)
    >>> parse_price_range("1.5m")
    (None, 1500000.0)
    >>> parse_price_range("undefined")
    (None, None)

    Um valor único é lido como TETO, não como piso: quando o lead diz "tenho
    300 mil", ele está informando o limite do que pode pagar.

    Nota de integração: o `extrair_preco` do ai-core devolve os dois valores
    através de um `set()`, cuja ordem de iteração de strings varia entre
    execuções do Python. Por isso os números são ordenados aqui, e "800k-500k"
    e "500k-800k" produzem o mesmo resultado.
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
    """Extrai uma expectativa de retorno em % ao ano.

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


# Intenção do lead -> o que ele de fato procura. Um investidor compra, então
# INVEST mapeia para SALE mais um piso de rentabilidade.
_INTENT_TO_DEAL_TYPE = {"BUY": "SALE", "RENT": "RENTAL", "INVEST": "SALE"}


def filters_from_profile(profile, expected_return=None, radius_km=None):
    """Converte um perfil de lead em Filters.

    É a costura entre a Parte 1 e a Parte 2. Aceita tanto o nosso perfil quanto
    o `dados_coletados` cru da Pessoa 1: o `lead_profile.from_agent()` normaliza
    os dois, então dá para passar `resultado["dados_coletados"]` direto.
    """
    data = lead_profile.from_agent(profile)

    intent = data.get("intent")
    deal_type = _INTENT_TO_DEAL_TYPE.get(intent)

    min_price, max_price = parse_price_range(data.get("price_range"))
    neighborhood, zone = schema.resolve_region(data.get("region"))

    # Perfil investidor: o retorno esperado vira piso de rentabilidade. Se o
    # lead não disse um número, nenhum piso é aplicado, então a ordenação ainda
    # favorece imóveis rentáveis sem descartar estoque.
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
# Geografia
# ---------------------------------------------------------------------------

def distance_km(lat1, lon1, lat2, lon2):
    """Haversine. Equivale ao `geo.distance()` do Azure Cognitive Search."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)

    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)

    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Aplicação dos filtros
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
    """Devolve o conjunto de ids que satisfazem todas as restrições duras."""
    return {d["id"] for d in index.documents if _matches(d, filters)}


# ---------------------------------------------------------------------------
# Relaxamento progressivo
# ---------------------------------------------------------------------------

def _relaxation_steps(original):
    """Sequência de afrouxamentos, do menos ao mais custoso para o lead.

    A ordem é decisão de negócio, não técnica: mostrar um imóvel 15% acima do
    orçamento machuca menos do que mostrar um no bairro errado, e ampliar do
    bairro para a zona machuca menos do que sair da zona. O número de quartos
    só é flexibilizado de verdade no fim, porque quem precisa de 4 quartos
    precisa de 4 quartos.

    Cada nível é calculado a partir dos filtros ORIGINAIS, não do estado
    corrente. Assim "orçamento ampliado em 40%" significa 40% acima do que o
    lead disse, e não 40% acima do nível anterior, e o rótulo que vai para o
    prompt do LLM é literalmente verdadeiro.
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

    # Último recurso geográfico. Entra sempre que houve QUALQUER restrição
    # geográfica, inclusive quando o lead pediu um bairro: o nível anterior já
    # trocou bairro por zona, e sem este a busca ficaria presa na zona e poderia
    # terminar vazia.
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

    # Último recurso. Mantém só o tipo de negócio, a única restrição que nunca
    # faz sentido violar: nunca oferecer venda a quem quer alugar. Garante que o
    # lead receba alguma opção em vez de silêncio, e o rótulo é explícito para
    # que o LLM avise que fugiu do perfil.
    steps.append((
        "mostrando as opções mais próximas, fora dos critérios informados",
        lambda f: Filters(deal_type=original.deal_type),
    ))

    return steps


# ---------------------------------------------------------------------------
# Score e motivos
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
    """Score final e a lista de motivos que o justificam.

    `original` são os filtros que o LEAD pediu, não os relaxados. É o que
    permite dizer "19% acima do orçamento" em vez de fingir que o imóvel está
    dentro do orçamento.

    Os motivos ficam em português: chegam ao lead e ao corretor.
    """
    score = WEIGHT_SIMILARITY * similarity
    reasons = []

    # -- localização --------------------------------------------------------

    if original.neighborhood and prop.get("neighborhood") == original.neighborhood:
        score += BONUS_EXACT_NEIGHBORHOOD
        reasons.append("no bairro pedido")
    elif original.zone and prop.get("zone") == original.zone:
        reasons.append("na zona pedida")

    if distance is not None and radius_reference:
        proximity = max(0.0, 1.0 - distance / float(radius_reference))
        score += WEIGHT_PROXIMITY * proximity
        reasons.append("a %.1f km do ponto de referência" % distance)

    # -- quartos ------------------------------------------------------------

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

    # -- orçamento ----------------------------------------------------------

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

    # -- rentabilidade (perfil investidor) ----------------------------------

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
        # Rótulo em português: esta string é lida pelo lead e pelo corretor.
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
    """Descreve, restrição por restrição, como o resultado difere do pedido.

    Existe porque a escada de relaxamento é um fato sobre a BUSCA, não sobre o
    RESULTADO: ela pode subir cinco degraus e os imóveis devolvidos ainda
    respeitarem tudo o que o lead pediu. Mandar os degraus crus para o prompt
    fazia o LLM dizer ao lead "precisei ampliar seu orçamento" quando não
    precisou. Estas notas olham o que de fato foi devolvido.

    As notas ficam em português: elas chegam na frente do lead.
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
    """Resultado da busca.

    relaxations  degraus da escada percorridos. Fato sobre a busca.
    mismatches   como os imóveis DEVOLVIDOS diferem do que o lead pediu. É o
                 que vai para o prompt do LLM, porque é o que o lead precisa
                 ouvir. Vazio significa que tudo bateu com o pedido.
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
        """Verdadeiro quando algum imóvel devolvido foge do que o lead pediu."""
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
# Busca
# ---------------------------------------------------------------------------

def search(index, filters, query_text="", embedder=None, top_k=3,
           allow_relaxation=True):
    """Busca híbrida. Devolve sempre um SearchResult, nunca None.

    `query_text` deve ser o texto livre da conversa (as últimas mensagens do
    lead), não um resumo estruturado: é ele que carrega o sinal semântico que
    os filtros não capturam.
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

    # Referência para a nota de proximidade: o raio efetivamente usado na busca
    # (já relaxado, se houve relaxamento). Sem raio declarado, usa uma escala
    # urbana para que a distância ainda ordene em vez de ser ignorada.
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
    """Atalho de integração: recebe o dict da Pessoa 1 (ou nosso perfil) e busca."""
    filters = filters_from_profile(
        profile,
        expected_return=expected_return,
        radius_km=radius_km,
    )
    return search(index, filters, query_text, embedder=embedder, top_k=top_k)


# ---------------------------------------------------------------------------
# Saída para o prompt do agente
# ---------------------------------------------------------------------------

def format_for_prompt(result):
    """Renderiza o resultado como bloco de texto para injetar no prompt do LLM.

    O formato é pensado para ser barato em tokens e difícil de o modelo alucinar
    em cima: cada imóvel numerado, com id explícito, para que a resposta ao lead
    possa citar imóveis reais e o corretor consiga rastreá-los depois.

    Em português, porque entra num prompt em português.
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
