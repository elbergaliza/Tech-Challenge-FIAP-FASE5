"""
Testes do resumo para o corretor e do resumo incremental.

    python -m unittest discover -s ai-memory-rag/tests -v

Nenhum teste chama o Gemini. O caminho com LLM é exercitado com `StubClient`,
que devolve respostas pré-definidas, inclusive MALFORMADAS, porque é assim que
um modelo real erra.
"""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from llm import StubClient, UnavailableClient, extract_json              # noqa: E402
from memory.conversation_memory import ConversationMemory, InMemoryStore  # noqa: E402
from summarizer import (                                                 # noqa: E402
    HOT_THRESHOLD, WARM_THRESHOLD, Summarizer, build_alerts,
    classify_temperature, compute_score, suggest_next_action,
    summarize_pipeline,
)


class FakeClock:
    def __init__(self, start=None):
        self.moment = start or datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment = self.moment + timedelta(**delta)
        return self.moment


# Escrito no nosso dialeto. O `update_profile` aceita os dois formatos, e o
# `compute_score` recebe este aqui direto.
FULL_PROFILE = {
    "name": "João", "intent": "BUY", "price_range": "800k-1.5m",
    "region": "Copacabana", "bedrooms": "3", "urgency": "high",
    "email": "joao@x.com", "phone": "(21) 98765-4321",
}

EMPTY_PROFILE = {
    "name": "undefined", "intent": "undefined", "price_range": "undefined",
    "region": "undefined", "bedrooms": "undefined", "urgency": "low",
    "email": "undefined", "phone": "undefined",
}


def memory_with_conversation(clock=None, profile=None, messages=4):
    memory = ConversationMemory(InMemoryStore(), clock=clock or FakeClock())
    for i in range(messages):
        memory.record_message("lead-1", "user", "mensagem %d do lead" % i)
        memory.record_message("lead-1", "assistant", "resposta %d" % i)
    if profile:
        memory.update_profile("lead-1", profile)
    return memory


# ===========================================================================
# Score
# ===========================================================================


class TestScore(unittest.TestCase):

    def test_full_engaged_profile_is_hot(self):
        score, _ = compute_score(FULL_PROFILE, lead_messages=5, hours_of_silence=1)
        self.assertEqual(score, 100)
        self.assertEqual(classify_temperature(score), "HOT")

    def test_empty_profile_is_cold(self):
        score, _ = compute_score(EMPTY_PROFILE, lead_messages=1, hours_of_silence=1)
        self.assertEqual(score, 0)
        self.assertEqual(classify_temperature(score), "COLD")

    def test_score_never_leaves_the_range(self):
        high, _ = compute_score(FULL_PROFILE, 99, 0)
        low, _ = compute_score(EMPTY_PROFILE, 0, 24 * 365)
        self.assertLessEqual(high, 100)
        self.assertGreaterEqual(low, 0)

    def test_contact_weighs_as_much_as_intent(self):
        # Para um SDR, lead sem telefone é lead que não dá para trabalhar.
        intent_only, _ = compute_score({"intent": "BUY"}, 0, 0)
        contact_only, _ = compute_score({"phone": "(21) 99999-9999"}, 0, 0)
        self.assertEqual(intent_only, contact_only)

    def test_email_or_phone_counts_once(self):
        one, _ = compute_score({"email": "a@b.com"}, 0, 0)
        two, _ = compute_score({"email": "a@b.com", "phone": "(21) 99999-9999"}, 0, 0)
        self.assertEqual(one, two)

    def test_silence_lowers_the_score(self):
        hot, _ = compute_score(FULL_PROFILE, 5, hours_of_silence=2)
        cold, _ = compute_score(FULL_PROFILE, 5, hours_of_silence=24 * 60)
        self.assertGreater(hot, cold)
        self.assertEqual(hot - cold, 40)

    def test_long_silence_cools_a_perfect_lead(self):
        # Um lead que pontuou 100 e sumiu há dois meses não pode ficar no topo
        # da lista do corretor.
        score, _ = compute_score(FULL_PROFILE, 5, hours_of_silence=24 * 60)
        self.assertEqual(score, 60)
        self.assertEqual(classify_temperature(score), "WARM")

    def test_factors_explain_the_score(self):
        _, factors = compute_score(FULL_PROFILE, 5, 1)
        text = " | ".join(factors)
        self.assertIn("intenção", text)
        self.assertIn("contato", text)
        self.assertIn("urgência alta", text)

    def test_factors_show_the_penalty(self):
        _, factors = compute_score(FULL_PROFILE, 5, hours_of_silence=24 * 10)
        self.assertTrue(any(f.startswith("-") for f in factors), factors)

    def test_thresholds_are_consistent(self):
        self.assertEqual(classify_temperature(HOT_THRESHOLD), "HOT")
        self.assertEqual(classify_temperature(HOT_THRESHOLD - 1), "WARM")
        self.assertEqual(classify_temperature(WARM_THRESHOLD), "WARM")
        self.assertEqual(classify_temperature(WARM_THRESHOLD - 1), "COLD")

    def test_missing_profile_does_not_break(self):
        self.assertEqual(compute_score(None, 0, 0)[0], 0)


class TestAlertsAndActions(unittest.TestCase):

    def state(self, **fields):
        base = {"followups_sent": 0, "consent": {"granted": True}}
        base.update(fields)
        return base

    def test_alert_for_missing_contact(self):
        alerts = build_alerts(EMPTY_PROFILE, self.state())
        self.assertTrue(any("Sem contato" in a for a in alerts))

    def test_full_profile_raises_no_alerts(self):
        self.assertEqual(build_alerts(FULL_PROFILE, self.state()), [])

    def test_alert_for_too_many_followups(self):
        alerts = build_alerts(FULL_PROFILE, self.state(followups_sent=3))
        self.assertTrue(any("follow-up" in a for a in alerts))

    def test_alert_when_the_lead_dodges_a_field(self):
        # O corretor precisa saber que o lead se esquiva de um ponto
        # específico: é sinal diferente de "ainda não perguntamos".
        alerts = build_alerts(
            FULL_PROFILE, self.state(unanswered={"price_range": 4, "phone": 1})
        )
        esquiva = [a for a in alerts if "esquiva" in a]

        self.assertEqual(len(esquiva), 1)
        self.assertIn("faixa de preço", esquiva[0])
        # phone ficou abaixo do limiar, então não entra.
        self.assertNotIn("telefone", esquiva[0])

    def test_no_dodge_alert_below_the_threshold(self):
        alerts = build_alerts(FULL_PROFILE, self.state(unanswered={"price_range": 2}))
        self.assertFalse(any("esquiva" in a for a in alerts))

    def test_alert_for_missing_consent(self):
        alerts = build_alerts(FULL_PROFILE, self.state(consent=None))
        self.assertTrue(any("LGPD" in a for a in alerts))

    def test_action_prioritises_finding_the_intent(self):
        action = suggest_next_action(EMPTY_PROFILE, "COLD", self.state())
        self.assertIn("compra, aluguel ou investimento", action)

    def test_action_asks_for_contact_before_advancing(self):
        profile = dict(FULL_PROFILE, email="undefined", phone="undefined")
        action = suggest_next_action(profile, "HOT", self.state())
        self.assertIn("telefone", action)

    def test_hot_lead_goes_to_a_call(self):
        action = suggest_next_action(FULL_PROFILE, "HOT", self.state())
        self.assertIn("hoje", action)

    def test_hot_investor_goes_to_the_specialist(self):
        # Cenário 2 do PDF: "Direcionar para especialista".
        profile = dict(FULL_PROFILE, intent="INVEST")
        action = suggest_next_action(profile, "HOT", self.state())
        self.assertIn("especialista", action)

    def test_warm_lead_is_told_what_to_find_out(self):
        profile = dict(FULL_PROFILE, region="undefined")
        action = suggest_next_action(profile, "WARM", self.state())
        self.assertIn("região", action)


# ===========================================================================
# Resumo para o corretor
# ===========================================================================


class TestHeuristicSummary(unittest.TestCase):
    """Sem LLM configurado, o dashboard ainda precisa funcionar."""

    def setUp(self):
        self.memory = memory_with_conversation(profile=FULL_PROFILE)
        self.summarizer = Summarizer(client=UnavailableClient())

    def test_produces_a_summary_without_an_llm(self):
        summary = self.summarizer.summarize_for_broker(self.memory, "lead-1")

        self.assertEqual(summary.source, "heuristic")
        self.assertTrue(summary.summary)
        self.assertTrue(summary.next_action)

    def test_summary_mentions_the_profile(self):
        summary = self.summarizer.summarize_for_broker(self.memory, "lead-1")

        self.assertIn("João", summary.summary)
        self.assertIn("Copacabana", summary.summary)
        self.assertIn("3 quartos", summary.summary)

    def test_summary_points_out_what_is_missing(self):
        memory = memory_with_conversation(
            profile=dict(FULL_PROFILE, price_range="undefined")
        )
        summary = Summarizer(client=UnavailableClient()).summarize_for_broker(
            memory, "lead-1"
        )
        self.assertIn("Ainda não informou", summary.summary)

    def test_lead_with_no_conversation_does_not_break(self):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())
        summary = self.summarizer.summarize_for_broker(memory, "new-lead")

        self.assertEqual(summary.temperature, "COLD")
        self.assertTrue(summary.summary)

    def test_markdown_for_the_dashboard(self):
        markdown = self.summarizer.summarize_for_broker(
            self.memory, "lead-1"
        ).to_markdown()

        self.assertIn("QUENTE", markdown)
        self.assertIn("**Próxima ação:**", markdown)
        # A origem precisa ficar visível: regra não pode passar por IA.
        self.assertIn("IA indisponível", markdown)

    def test_serializes_for_the_api(self):
        data = self.summarizer.summarize_for_broker(self.memory, "lead-1").to_dict()
        json.dumps(data, ensure_ascii=False)

        self.assertEqual(data["temperature"], "HOT")
        self.assertIn("factors", data)


class TestSummaryWithLLM(unittest.TestCase):

    REPLY = json.dumps({
        "resumo": "João procura apartamento de 3 quartos em Copacabana com urgência.",
        "main_interest": "Apartamento familiar na zona sul",
        "buying_signals": ["Disse que precisa mudar até dezembro"],
        "objections": ["Achou o condomínio caro"],
        "next_action": "Ligar hoje e agendar visita ao IMV-0028",
    }, ensure_ascii=False)

    def setUp(self):
        self.memory = memory_with_conversation(profile=FULL_PROFILE)

    def summarize(self, reply):
        client = StubClient(reply)
        return Summarizer(client=client).summarize_for_broker(self.memory, "lead-1"), client

    def test_uses_the_llm_reply(self):
        summary, _ = self.summarize(self.REPLY)

        self.assertEqual(summary.source, "llm")
        self.assertIn("Copacabana", summary.summary)
        self.assertEqual(summary.objections, ["Achou o condomínio caro"])
        self.assertIn("IMV-0028", summary.next_action)

    def test_score_still_comes_from_the_rule(self):
        # O LLM opina sobre texto, não sobre priorização: o score precisa
        # continuar auditável e reproduzível.
        summary, _ = self.summarize(self.REPLY)
        self.assertEqual(summary.score, 100)
        self.assertEqual(summary.temperature, "HOT")

    def test_conversation_reaches_the_llm_pseudonymised(self):
        self.memory.record_message("lead-1", "user", "meu email é joao@x.com")
        _, client = self.summarize(self.REPLY)

        prompt = client.calls[0]
        self.assertNotIn("joao@x.com", prompt)
        self.assertIn("[EMAIL_1]", prompt)

    def test_prompt_forbids_invention(self):
        _, client = self.summarize(self.REPLY)
        self.assertIn("Não invente", client.calls[0])

    def test_json_in_markdown_fences_is_accepted(self):
        summary, _ = self.summarize("```json\n%s\n```" % self.REPLY)
        self.assertEqual(summary.source, "llm")

    def test_json_with_surrounding_text_is_accepted(self):
        summary, _ = self.summarize(
            "Claro! Aqui está:\n%s\nEspero ter ajudado." % self.REPLY
        )
        self.assertEqual(summary.source, "llm")

    def test_malformed_reply_falls_back_to_the_heuristic(self):
        summary, _ = self.summarize("desculpe, não consegui processar")

        self.assertEqual(summary.source, "heuristic")
        self.assertTrue(summary.summary)

    def test_a_failing_llm_does_not_take_down_the_dashboard(self):
        class BrokenClient:
            name, available = "broken", True

            def generate(self, prompt, temperature=0.4):
                raise RuntimeError("503 Service Unavailable")

        summary = Summarizer(client=BrokenClient()).summarize_for_broker(
            self.memory, "lead-1"
        )
        self.assertEqual(summary.source, "heuristic")
        self.assertTrue(summary.summary)

    def test_empty_llm_field_falls_back_to_the_rule(self):
        summary, _ = self.summarize(
            json.dumps({"summary": "texto", "next_action": ""})
        )

        self.assertEqual(summary.source, "llm")
        self.assertTrue(summary.next_action, "next_action can never be empty")

    def test_aliases_become_real_data_on_the_dashboard(self):
        # O corretor é destinatário autorizado.
        self.memory.record_message("lead-1", "user", "escreve pra joao@x.com")
        self.memory.history("lead-1", mask=True)

        reply = json.dumps({"summary": "Lead pediu contato em [EMAIL_1].",
                            "next_action": "Escrever para [EMAIL_1]"})
        summary, _ = self.summarize(reply)

        self.assertIn("joao@x.com", summary.summary)
        self.assertNotIn("[EMAIL_1]", summary.summary)

    def test_use_llm_false_skips_the_call(self):
        client = StubClient(self.REPLY)
        summary = Summarizer(client=client).summarize_for_broker(
            self.memory, "lead-1", use_llm=False
        )

        self.assertEqual(summary.source, "heuristic")
        self.assertEqual(client.calls, [])


# ===========================================================================
# Compressão da memória
# ===========================================================================


class TestMemoryCompression(unittest.TestCase):

    def long_memory(self, messages=40):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())
        for i in range(messages):
            memory.record_message("lead-1", "user", "mensagem %d" % i)
        memory.update_profile("lead-1", FULL_PROFILE)
        return memory

    def test_short_conversation_is_not_compressed(self):
        memory = memory_with_conversation(messages=2)
        self.assertIsNone(
            Summarizer(client=UnavailableClient()).compress_memory(memory, "lead-1")
        )

    def test_compresses_and_stores_in_memory(self):
        memory = self.long_memory()
        client = StubClient("Lead quer 3 quartos em Copacabana, urgente.")

        text = Summarizer(client=client).compress_memory(memory, "lead-1")

        self.assertEqual(text, "Lead quer 3 quartos em Copacabana, urgente.")
        self.assertEqual(memory.summary("lead-1"), text)
        self.assertFalse(memory.needs_summary("lead-1"))

    def test_the_live_window_is_untouched(self):
        memory = self.long_memory()
        Summarizer(client=StubClient("resumo")).compress_memory(memory, "lead-1")

        # As últimas mensagens continuam literais no histórico.
        self.assertEqual(memory.history("lead-1")[-1]["content"], "mensagem 39")

    def test_compresses_without_an_llm(self):
        memory = self.long_memory()
        text = Summarizer(client=UnavailableClient()).compress_memory(memory, "lead-1")

        self.assertTrue(text)
        # Sem modelo, o resumo admite o que não preservou em vez de inventar.
        self.assertIn("sem IA", text)
        self.assertIn("Copacabana", text)

    def test_previous_summary_is_folded_in(self):
        memory = self.long_memory()
        client = StubClient("resumo 1")
        summarizer = Summarizer(client=client)
        summarizer.compress_memory(memory, "lead-1")

        for i in range(40, 80):
            memory.record_message("lead-1", "user", "mensagem %d" % i)

        client.replies = ["resumo 1 + resumo 2", "resumo 1 + resumo 2"]
        summarizer.compress_memory(memory, "lead-1")

        self.assertIn("RESUMO ANTERIOR", client.calls[-1])
        self.assertIn("resumo 1", client.calls[-1])

    def test_conversation_reaches_the_llm_pseudonymised(self):
        memory = self.long_memory()
        memory.record_message("lead-1", "user", "meu email é joao@x.com")
        client = StubClient("resumo")

        Summarizer(client=client).compress_memory(memory, "lead-1", force=True)

        self.assertNotIn("joao@x.com", client.calls[0])

    def test_force_compresses_a_short_conversation(self):
        memory = memory_with_conversation(messages=8, profile=FULL_PROFILE)
        text = Summarizer(client=StubClient("resumo curto")).compress_memory(
            memory, "lead-1", force=True
        )
        self.assertEqual(text, "resumo curto")


# ===========================================================================
# Pipeline
# ===========================================================================


class TestPipeline(unittest.TestCase):

    def test_orders_from_hottest_to_coldest(self):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())

        memory.record_message("cold", "user", "oi")
        memory.update_profile("cold", EMPTY_PROFILE)

        for i in range(4):
            memory.record_message("hot", "user", "msg %d" % i)
        memory.update_profile("hot", FULL_PROFILE)

        summaries = summarize_pipeline(memory)

        self.assertEqual([s.lead_id for s in summaries], ["hot", "cold"])
        self.assertEqual(summaries[0].temperature, "HOT")

    def test_empty_pipeline(self):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())
        self.assertEqual(summarize_pipeline(memory), [])

    def test_pipeline_does_not_call_the_llm_by_default(self):
        # Varrer a carteira inteira com uma chamada ao Gemini por lead é lento
        # e caro.
        memory = memory_with_conversation(profile=FULL_PROFILE)
        client = StubClient('{"resumo": "x"}')

        summarize_pipeline(memory, Summarizer(client=client))
        self.assertEqual(client.calls, [])


# ===========================================================================
# Parser de JSON do LLM
# ===========================================================================


class TestExtractJson(unittest.TestCase):

    def test_plain_json(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_markdown_fences(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(extract_json('```\n{"a": 1}\n```'), {"a": 1})

    def test_surrounding_text(self):
        self.assertEqual(extract_json('Claro:\n{"a": 1}\nAbraço'), {"a": 1})

    def test_json_with_internal_newlines(self):
        self.assertEqual(extract_json('{\n  "a": [1,\n2]\n}'), {"a": [1, 2]})

    def test_garbage_returns_none(self):
        self.assertIsNone(extract_json("não consegui"))
        self.assertIsNone(extract_json(""))
        self.assertIsNone(extract_json(None))
        self.assertIsNone(extract_json('{"a": '))


if __name__ == "__main__":
    unittest.main(verbosity=2)
