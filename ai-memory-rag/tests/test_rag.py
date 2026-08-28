"""
Tests for the RAG module (Part 2).

Run offline, with no API key and no external dependency:

    python -m unittest discover -s ai-memory-rag/tests -v

The embedder is always HashingEmbedder so the tests stay deterministic. Testing
against the real Gemini API would make the suite slow, network-dependent and
quota-dependent.

Assertions on user-facing strings stay in Portuguese, because that text is the
product output and does not get translated.
"""

import json
import os
import sys
import unittest

MODULE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, MODULE_ROOT)

from rag import indexer, retriever, schema                     # noqa: E402
from rag.embeddings import HashingEmbedder, cosine, tokenize    # noqa: E402


def valid_property(**overrides):
    """Minimal valid document, for validation and filter tests."""
    base = {
        "id": "IMV-9999",
        "title": "Apartamento de 3 quartos em Copacabana",
        "description": "Apartamento com varanda e vista mar.",
        "deal_type": "SALE",
        "property_type": "APARTMENT",
        "price": 900000.0,
        "condo_fee": 1200.0,
        "property_tax": 450.0,
        "bedrooms": 3,
        "suites": 1,
        "bathrooms": 2,
        "parking": 1,
        "area_m2": 95.0,
        "neighborhood": "Copacabana",
        "zone": "Zona Sul",
        "city": "Rio de Janeiro",
        "lat": -22.9711,
        "lon": -43.1822,
        "features": ["varanda", "vista mar"],
        "accepts_financing": True,
        "annual_yield_pct": 5.4,
        "status": "AVAILABLE",
        "updated_at": "2026-08-08",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------


class TestSchema(unittest.TestCase):

    def test_resolve_region_identifies_neighborhood(self):
        self.assertEqual(schema.resolve_region("Copacabana"), ("Copacabana", None))

    def test_resolve_region_identifies_zone(self):
        self.assertEqual(schema.resolve_region("Zona Sul"), (None, "Zona Sul"))

    def test_resolve_region_ignores_case_and_spaces(self):
        self.assertEqual(schema.resolve_region("  zona sul  "), (None, "Zona Sul"))
        self.assertEqual(schema.resolve_region("LEBLON"), ("Leblon", None))

    def test_resolve_region_handles_person_1_undefined(self):
        # "undefined" is literally what Person 1's agent returns when it could
        # not extract the field. It must not become a filter.
        self.assertEqual(schema.resolve_region("undefined"), (None, None))
        self.assertEqual(schema.resolve_region(None), (None, None))
        self.assertEqual(schema.resolve_region(""), (None, None))

    def test_unknown_region_does_not_filter(self):
        self.assertEqual(schema.resolve_region("Moema"), (None, None))

    def test_validate_accepts_a_good_document(self):
        self.assertEqual(schema.validate_property(valid_property()), [])

    def test_validate_rejects_invalid_fields(self):
        problems = schema.validate_property(valid_property(
            deal_type="INVESTIMENTO",  # not a deal type
            price=0,
            neighborhood="Moema",
        ))
        text = " | ".join(problems)
        self.assertIn("deal_type", text)
        self.assertIn("price", text)
        self.assertIn("neighborhood", text)

    def test_validate_rejects_missing_field(self):
        incomplete = valid_property()
        del incomplete["bedrooms"]
        self.assertTrue(
            any("bedrooms" in p for p in schema.validate_property(incomplete))
        )

    def test_every_catalogue_zone_is_in_zones(self):
        for neighborhood, data in schema.NEIGHBORHOODS.items():
            self.assertIn(data[0], schema.ZONES, "invalid zone in %s" % neighborhood)


# ---------------------------------------------------------------------------


class TestEmbeddings(unittest.TestCase):

    def setUp(self):
        self.embedder = HashingEmbedder(dim=256)

    def test_tokenize_produces_bigrams(self):
        tokens = tokenize("zona sul")
        self.assertIn("zona", tokens)
        self.assertIn("sul", tokens)
        self.assertIn("zona_sul", tokens)

    def test_tokenize_normalizes_accents(self):
        self.assertEqual(tokenize("Gávea"), tokenize("gavea"))

    def test_tokenize_drops_stopwords(self):
        self.assertNotIn("de", tokenize("casa de praia"))

    def test_vector_is_normalized(self):
        vector = self.embedder.embed_query("apartamento na zona sul")
        norm = sum(v * v for v in vector) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_deterministic_across_instances(self):
        # Regression: an earlier version would have used the built-in hash(),
        # which is randomised per process and would make the saved index
        # disagree with later queries.
        a = HashingEmbedder(dim=256).embed_query("cobertura no Leblon")
        b = HashingEmbedder(dim=256).embed_query("cobertura no Leblon")
        self.assertEqual(a, b)

    def test_cosine_with_itself_is_one(self):
        vector = self.embedder.embed_query("casa com quintal na Tijuca")
        self.assertAlmostEqual(cosine(vector, vector), 1.0, places=6)

    def test_related_text_scores_above_unrelated(self):
        query = self.embedder.embed_query("apartamento 3 quartos Copacabana")
        near, far = self.embedder.embed_documents([
            "Apartamento de 3 quartos em Copacabana com varanda",
            "Sala comercial no Centro, sem vaga",
        ])
        self.assertGreater(cosine(query, near), cosine(query, far))

    def test_cosine_rejects_different_dimensions(self):
        with self.assertRaises(ValueError):
            cosine([1.0, 0.0], [1.0, 0.0, 0.0])

    def test_empty_text_does_not_break(self):
        self.assertEqual(len(self.embedder.embed_query("")), 256)


# ---------------------------------------------------------------------------


class TestParsers(unittest.TestCase):

    def test_range_with_two_values(self):
        self.assertEqual(retriever.parse_price_range("500k-800k"), (500000.0, 800000.0))

    def test_range_is_order_independent(self):
        # Person 1 builds the range through a set(), whose iteration order
        # varies between Python runs. Both spellings must agree.
        self.assertEqual(
            retriever.parse_price_range("800k-500k"),
            retriever.parse_price_range("500k-800k"),
        )

    def test_single_value_is_a_ceiling_not_a_floor(self):
        self.assertEqual(retriever.parse_price_range("300k"), (None, 300000.0))

    def test_understands_millions(self):
        self.assertEqual(retriever.parse_price_range("1.5m"), (None, 1500000.0))
        self.assertEqual(retriever.parse_price_range("2 milhões"), (None, 2000000.0))

    def test_understands_free_text(self):
        self.assertEqual(retriever.parse_price_range("até 600 mil"), (None, 600000.0))

    def test_rental_in_reais(self):
        self.assertEqual(retriever.parse_price_range("3500"), (None, 3500.0))

    def test_undefined_creates_no_filter(self):
        self.assertEqual(retriever.parse_price_range("undefined"), (None, None))
        self.assertEqual(retriever.parse_price_range(None), (None, None))

    def test_percentage(self):
        self.assertEqual(retriever.parse_percentage("espero 8% ao ano"), 8.0)
        self.assertEqual(retriever.parse_percentage("7,5%"), 7.5)
        self.assertIsNone(retriever.parse_percentage("undefined"))


# ---------------------------------------------------------------------------


class TestFiltersFromProfile(unittest.TestCase):

    def test_purchase_becomes_sale(self):
        filters = retriever.filters_from_profile({"intencao": "COMPRA"})
        self.assertEqual(filters.deal_type, "SALE")

    def test_rental_stays_rental(self):
        filters = retriever.filters_from_profile({"intencao": "ALUGUEL"})
        self.assertEqual(filters.deal_type, "RENTAL")

    def test_investment_becomes_sale(self):
        # An investor buys. INVESTIMENTO is a profile, not a deal type.
        filters = retriever.filters_from_profile({"intencao": "INVESTIMENTO"})
        self.assertEqual(filters.deal_type, "SALE")

    def test_investment_uses_expected_return(self):
        filters = retriever.filters_from_profile(
            {"intencao": "INVESTIMENTO"},
            expected_return="quero pelo menos 6% ao ano",
        )
        self.assertEqual(filters.min_yield, 6.0)

    def test_full_profile_from_person_1(self):
        # Exact `dados_coletados` shape documented in the repo README.
        data = {
            "nome": "João",
            "intencao": "COMPRA",
            "preco_faixa": "500k-800k",
            "regiao": "Copacabana",
            "bedrooms": "3",
            "urgencia": "alta",
            "email": "undefined",
            "telefone": "undefined",
        }
        filters = retriever.filters_from_profile(data)

        self.assertEqual(filters.deal_type, "SALE")
        self.assertEqual(filters.min_price, 500000.0)
        self.assertEqual(filters.max_price, 800000.0)
        self.assertEqual(filters.min_bedrooms, 3)
        self.assertEqual(filters.neighborhood, "Copacabana")
        self.assertIsNone(filters.zone)

    def test_empty_profile_creates_no_filters(self):
        filters = retriever.filters_from_profile({
            "intencao": "undefined",
            "preco_faixa": "undefined",
            "regiao": "undefined",
            "bedrooms": "undefined",
        })
        for field in ("deal_type", "min_price", "max_price", "min_bedrooms",
                      "neighborhood", "zone"):
            self.assertIsNone(getattr(filters, field), field)

    def test_radius_turns_neighborhood_into_coordinates(self):
        filters = retriever.filters_from_profile({"regiao": "Leblon"}, radius_km=5)
        self.assertIsNone(filters.neighborhood)
        self.assertEqual(filters.radius_km, 5)
        self.assertAlmostEqual(filters.lat, -22.9838, places=4)

    def test_filters_reject_unknown_field(self):
        with self.assertRaises(TypeError):
            retriever.Filters(maximum_price=100)


# ---------------------------------------------------------------------------


class TestGeography(unittest.TestCase):

    def test_leblon_and_ipanema_are_neighbours(self):
        d = retriever.distance_km(-22.9838, -43.2226, -22.9838, -43.2045)
        self.assertLess(d, 3.0)

    def test_leblon_and_barra_are_far_apart(self):
        d = retriever.distance_km(-22.9838, -43.2226, -23.0045, -43.3650)
        self.assertGreater(d, 10.0)

    def test_distance_from_a_point_to_itself_is_zero(self):
        self.assertAlmostEqual(
            retriever.distance_km(-22.98, -43.22, -22.98, -43.22), 0.0, places=6
        )


# ---------------------------------------------------------------------------


class TestGeneratedBase(unittest.TestCase):
    """The versioned synthetic base must stay intact."""

    @classmethod
    def setUpClass(cls):
        cls.properties = indexer.load_properties()

    def test_base_has_reasonable_volume(self):
        self.assertGreaterEqual(len(self.properties), 100)

    def test_every_document_is_valid(self):
        # load_properties() already raises on problems; this test makes the
        # guarantee explicit and readable in the suite report.
        for prop in self.properties:
            self.assertEqual(schema.validate_property(prop), [], prop["id"])

    def test_ids_are_unique(self):
        ids = [p["id"] for p in self.properties]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_catalogue_neighborhood_has_available_stock(self):
        available = [p for p in self.properties if p["status"] == "AVAILABLE"]
        with_stock = {p["neighborhood"] for p in available}
        missing = set(schema.NEIGHBORHOODS) - with_stock
        self.assertEqual(missing, set(), "neighborhoods without stock: %s" % missing)

    def test_both_deal_types_exist(self):
        self.assertEqual(
            {p["deal_type"] for p in self.properties}, {"SALE", "RENTAL"}
        )

    def test_rentals_have_no_yield_and_sales_do(self):
        for prop in self.properties:
            if prop["deal_type"] == "RENTAL":
                self.assertIsNone(prop["annual_yield_pct"], prop["id"])
            else:
                self.assertIsNotNone(prop["annual_yield_pct"], prop["id"])

    def test_rentals_are_far_cheaper_than_sales(self):
        sales = [p["price"] for p in self.properties if p["deal_type"] == "SALE"]
        rentals = [p["price"] for p in self.properties if p["deal_type"] == "RENTAL"]
        self.assertLess(max(rentals), min(sales))

    def test_json_schema_was_written(self):
        path = os.path.join(REPO_ROOT, "shared", "schemas", "imovel_schema.json")
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(data["title"], "Property")
        self.assertIn("price", data["properties"])


# ---------------------------------------------------------------------------


class TestIndex(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.properties = indexer.load_properties()
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def test_index_covers_the_whole_base(self):
        self.assertEqual(len(self.index), len(self.properties))

    def test_search_text_repeats_weighted_fields(self):
        text = indexer.build_search_text(valid_property())
        # title has weight 3
        self.assertEqual(text.count("Apartamento de 3 quartos em Copacabana"), 3)
        # the zone is included even when the listing never mentions it
        self.assertIn("Zona Sul", text)

    def test_search_text_marks_the_deal_type(self):
        self.assertIn("aluguel", indexer.build_search_text(
            valid_property(deal_type="RENTAL")))
        self.assertIn("venda", indexer.build_search_text(
            valid_property(deal_type="SALE")))

    def test_by_id(self):
        first = self.properties[0]["id"]
        self.assertEqual(self.index.by_id(first)["id"], first)
        self.assertIsNone(self.index.by_id("IMV-0000"))

    def test_facets_sum_to_the_total(self):
        facets = self.index.facets("deal_type")
        self.assertEqual(sum(facets.values()), len(self.index))

    def test_disk_round_trip(self):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "index.json")
            indexer.save_index(self.index, path)
            reloaded = indexer.load_index(path)

        self.assertEqual(len(reloaded), len(self.index))
        self.assertEqual(reloaded.embedder_name, self.index.embedder_name)
        # A query after reloading must agree with the original.
        query = self.embedder.embed_query("cobertura no Leblon")
        before = [d["id"] for d, _ in self.index.similarities(query)[:5]]
        after = [d["id"] for d, _ in reloaded.similarities(query)[:5]]
        self.assertEqual(before, after)

    def test_index_rejects_mismatched_lengths(self):
        with self.assertRaises(ValueError):
            indexer.Index("x", 3, [valid_property()], [])


# ---------------------------------------------------------------------------


class TestSearch(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.properties = indexer.load_properties()
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def search(self, filters, text="", top_k=3, relax=True):
        return retriever.search(
            self.index, filters, text,
            embedder=self.embedder, top_k=top_k, allow_relaxation=relax,
        )

    # -- hard filters -------------------------------------------------------

    def test_price_filter_is_respected(self):
        filters = retriever.Filters(deal_type="SALE", max_price=700000)
        result = self.search(filters, "apartamento", relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertLessEqual(recommendation.property["price"], 700000)

    def test_bedroom_filter_is_respected(self):
        filters = retriever.Filters(min_bedrooms=3)
        result = self.search(filters, "familia", top_k=10, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertGreaterEqual(recommendation.property["bedrooms"], 3)

    def test_unavailable_properties_never_surface(self):
        # With no filters at all, every property is a candidate except the
        # unavailable ones. This is the test that keeps the broker from
        # offering something already sold.
        ids = retriever.apply_filters(self.index, retriever.Filters())

        self.assertGreater(len(ids), 0)
        for property_id in ids:
            self.assertEqual(self.index.by_id(property_id)["status"], "AVAILABLE")

    def test_filter_matches_a_manual_count(self):
        filters = retriever.Filters(
            deal_type="SALE", zone="Zona Sul", min_bedrooms=3, max_price=2_000_000
        )
        expected = {
            p["id"] for p in self.properties
            if p["status"] == "AVAILABLE"
            and p["deal_type"] == "SALE"
            and p["zone"] == "Zona Sul"
            and p["bedrooms"] >= 3
            and p["price"] <= 2_000_000
        }
        self.assertEqual(retriever.apply_filters(self.index, filters), expected)

    def test_geographic_filter(self):
        _, _, lat, lon, _ = schema.NEIGHBORHOODS["Leblon"]
        filters = retriever.Filters(lat=lat, lon=lon, radius_km=4)
        result = self.search(filters, "apartamento", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertLessEqual(recommendation.distance_km, 4.0)
            # Nothing in Barra belongs within 4 km of Leblon.
            self.assertNotEqual(recommendation.property["neighborhood"], "Barra")

    def test_feature_filter(self):
        filters = retriever.Filters(features=["vista mar"])
        result = self.search(filters, "quero vista para o mar", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertIn("vista mar", recommendation.property["features"])

    def test_minimum_yield_for_investors(self):
        filters = retriever.Filters(deal_type="SALE", min_yield=6.0)
        result = self.search(filters, "investimento renda", top_k=5, relax=False)

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertGreaterEqual(recommendation.property["annual_yield_pct"], 6.0)

    # -- ranking ------------------------------------------------------------

    def test_respects_top_k(self):
        self.assertEqual(len(self.search(retriever.Filters(), "apartamento", top_k=3)), 3)

    def test_results_come_sorted_by_score(self):
        result = self.search(retriever.Filters(), "cobertura com piscina", top_k=5)
        scores = [r.score for r in result]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_exact_neighborhood_gets_a_bonus(self):
        filters = retriever.Filters(neighborhood="Ipanema")
        result = self.search(filters, "apartamento", top_k=3, relax=False)
        for recommendation in result:
            self.assertEqual(recommendation.property["neighborhood"], "Ipanema")
            self.assertIn("no bairro pedido", recommendation.reason)

    def test_semantics_influence_the_order(self):
        # Same filters, different queries: the order must change, otherwise the
        # semantic ranking is doing nothing.
        filters = retriever.Filters(deal_type="SALE", zone="Zona Sul")
        beach = [r.id for r in self.search(
            filters, "quero vista para o mar, perto da praia", top_k=5)]
        house = [r.id for r in self.search(
            filters, "casa grande com quintal para cachorro", top_k=5)]
        self.assertNotEqual(beach, house)

    # -- relaxation ---------------------------------------------------------

    def test_satisfiable_profile_does_not_relax(self):
        filters = retriever.Filters(deal_type="SALE", zone="Zona Sul", min_bedrooms=2)
        result = self.search(filters, "apartamento")

        self.assertFalse(result.was_relaxed)
        self.assertEqual(result.mismatches, [])

    def test_impossible_profile_relaxes_and_still_returns_options(self):
        # Four bedrooms in Leblon for up to 300k does not exist anywhere.
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters)

        self.assertTrue(result.was_relaxed)
        self.assertGreater(len(result), 0, "relaxation should find alternatives")

    def test_relaxation_is_reported_in_text(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters)
        self.assertTrue(any("orçamento" in r for r in result.relaxations))

    def test_relaxation_disabled_returns_empty_without_breaking(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        result = self.search(filters, relax=False)

        self.assertEqual(len(result), 0)
        self.assertEqual(result.candidate_count, 0)

    # -- output -------------------------------------------------------------

    def test_reason_describes_the_property(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Botafogo", min_bedrooms=2
        )
        recommendation = self.search(filters, "apartamento", top_k=1).recommendations[0]

        self.assertIn("Botafogo", recommendation.reason)
        self.assertIn("R$", recommendation.reason)
        self.assertIn("quartos", recommendation.reason)

    def test_to_dict_serializes(self):
        result = self.search(retriever.Filters(), "apartamento", top_k=2)
        # It has to cross Person 3's API as JSON.
        self.assertIn("reason", json.dumps(result.to_dict(), ensure_ascii=False))

    def test_prompt_lists_ids_and_forbids_invention(self):
        result = self.search(retriever.Filters(deal_type="SALE"), "apartamento", top_k=2)
        prompt = retriever.format_for_prompt(result)

        self.assertIn("não invente", prompt)
        for recommendation in result:
            self.assertIn(recommendation.id, prompt)

    def test_empty_prompt_tells_the_model_not_to_hallucinate(self):
        empty = retriever.SearchResult([], [], 0, retriever.Filters())
        prompt = retriever.format_for_prompt(empty)

        self.assertIn("nenhum", prompt)
        self.assertIn("não invente", prompt)

    def test_prompt_warns_about_relaxation(self):
        filters = retriever.Filters(
            deal_type="SALE", neighborhood="Leblon", min_bedrooms=4, max_price=300000
        )
        prompt = retriever.format_for_prompt(self.search(filters))
        self.assertIn("OBSERVAÇÃO", prompt)


# ---------------------------------------------------------------------------


class TestMismatches(unittest.TestCase):
    """Precision of what gets reported to the lead when the result falls short.

    Uses a tiny hand-built index so the scenario is exact rather than dependent
    on how the synthetic base happened to fall.
    """

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=128)
        cls.properties = [
            valid_property(
                id="IMV-0001", neighborhood="Copacabana", zone="Zona Sul",
                bedrooms=3, price=500000.0,
                title="Apartamento de 3 quartos em Copacabana",
            ),
            valid_property(
                id="IMV-0002", neighborhood="Copacabana", zone="Zona Sul",
                bedrooms=3, price=900000.0,
                title="Apartamento de 3 quartos em Copacabana",
            ),
            valid_property(
                id="IMV-0003", neighborhood="Botafogo", zone="Zona Sul",
                bedrooms=1, price=400000.0,
                title="Apartamento de 1 quarto em Botafogo",
            ),
        ]
        cls.index = indexer.build_index(cls.properties, cls.embedder)

    def search(self, filters, top_k):
        return retriever.search(
            self.index, filters, "apartamento", embedder=self.embedder, top_k=top_k
        )

    def test_satisfied_profile_produces_no_mismatch(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        result = self.search(filters, top_k=1)

        self.assertEqual(len(result), 1)
        self.assertEqual(result.recommendations[0].id, "IMV-0001")
        self.assertEqual(result.mismatches, [])
        self.assertTrue(result.matches_profile)
        self.assertFalse(result.was_relaxed)

    def test_mismatch_does_not_mention_a_respected_constraint(self):
        # Regression for the bug found in the demo: the ladder has to climb the
        # "budget widened" step to fill top_k, but both returned properties
        # (500k and 400k) fit the 600k budget. The prompt must NOT tell the lead
        # their budget was exceeded.
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        result = self.search(filters, top_k=2)

        self.assertEqual(len(result), 2)
        for recommendation in result:
            self.assertLessEqual(recommendation.property["price"], 600000)

        # The step was climbed...
        self.assertTrue(any("orçamento" in r for r in result.relaxations))
        # ...but it is not reported as a mismatch, because nothing overflowed.
        self.assertFalse(
            any("orçamento" in m for m in result.mismatches),
            "mismatches wrongly mention the budget: %r" % result.mismatches,
        )

    def test_mismatch_reports_real_neighborhood_and_bedrooms(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        text = " | ".join(self.search(filters, top_k=2).mismatches)

        self.assertIn("Copacabana", text)
        self.assertIn("Botafogo", text)
        self.assertIn("quartos", text)

    def test_prompt_uses_mismatches_not_the_ladder(self):
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=600000
        )
        prompt = retriever.format_for_prompt(self.search(filters, top_k=2))

        self.assertIn("OBSERVAÇÃO", prompt)
        self.assertNotIn("orçamento ampliado", prompt)

    def test_budget_mismatch_is_reported_when_real(self):
        # Here the overflow is genuine: the only 3-bedroom options in
        # Copacabana above 450k cost 500k and 900k, so the mismatch must show.
        filters = retriever.Filters(
            neighborhood="Copacabana", min_bedrooms=3, max_price=450000
        )
        result = self.search(filters, top_k=1)

        self.assertTrue(any("orçamento" in m for m in result.mismatches),
                        result.mismatches)


# ---------------------------------------------------------------------------


class TestIntegrationWithPerson1(unittest.TestCase):
    """Scenarios from the brief, entering through Person 1's real format."""

    @classmethod
    def setUpClass(cls):
        cls.embedder = HashingEmbedder(dim=256)
        cls.index = indexer.build_index(indexer.load_properties(), cls.embedder)

    def search_lead(self, data, text="", **kwargs):
        return retriever.search_for_lead(
            self.index, data, text, embedder=self.embedder, **kwargs
        )

    def test_scenario_1_buying_in_the_south_zone(self):
        data = {
            "nome": "João", "intencao": "COMPRA", "preco_faixa": "800k-1.5m",
            "regiao": "Zona Sul", "bedrooms": "3", "urgencia": "alta",
            "email": "undefined", "telefone": "undefined",
        }
        result = self.search_lead(
            data, "Estou procurando apartamento na zona sul, 3 quartos"
        )

        self.assertGreater(len(result), 0)
        for recommendation in result:
            prop = recommendation.property
            self.assertEqual(prop["deal_type"], "SALE")
            self.assertEqual(prop["zone"], "Zona Sul")
            self.assertGreaterEqual(prop["bedrooms"], 3)
            self.assertLessEqual(prop["price"], 1_500_000)

    def test_scenario_2_investing_for_income(self):
        data = {"intencao": "INVESTIMENTO", "preco_faixa": "1m", "regiao": "undefined"}
        result = self.search_lead(
            data,
            "Quero investir em imóveis para renda",
            expected_return="espero 6% ao ano",
        )

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertEqual(recommendation.property["deal_type"], "SALE")
            self.assertGreaterEqual(recommendation.property["annual_yield_pct"], 6.0)
            self.assertLessEqual(recommendation.property["price"], 1_000_000)

    def test_rental_scenario(self):
        data = {
            "intencao": "ALUGUEL", "preco_faixa": "4000",
            "regiao": "Tijuca", "bedrooms": "2",
        }
        result = self.search_lead(data, "procuro para alugar na Tijuca, 2 quartos")

        self.assertGreater(len(result), 0)
        for recommendation in result:
            self.assertEqual(recommendation.property["deal_type"], "RENTAL")

    def test_first_message_with_no_data_still_works(self):
        # On the first interaction Person 1 returns almost everything as
        # "undefined". The RAG must neither blow up nor return empty: it shows
        # what it has.
        data = {
            "nome": "undefined", "intencao": "undefined", "preco_faixa": "undefined",
            "regiao": "undefined", "bedrooms": "undefined", "urgencia": "undefined",
            "email": "undefined", "telefone": "undefined",
        }
        self.assertGreater(len(self.search_lead(data, "Oi, tudo bem?")), 0)

    def test_missing_collected_data_does_not_break(self):
        self.assertIsNotNone(self.search_lead(None, "oi"))
        self.assertIsNotNone(self.search_lead({}, "oi"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
