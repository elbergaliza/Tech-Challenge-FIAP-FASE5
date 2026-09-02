"""
Testes do follow-up automático.

    python -m unittest discover -s ai-memory-rag/tests -v

O relógio é injetado, então cadência de 7 dias é testada em microssegundos.
Nenhum teste chama o Gemini.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from followup import (                                                   # noqa: E402
    DEFAULT_CADENCE, URGENT_CADENCE, FollowUpGenerator, cadence_for,
    detect_opt_out, evaluate_followup, leads_due_for_followup,
    suggested_channel,
)
from llm import StubClient, UnavailableClient                            # noqa: E402
from memory.conversation_memory import ConversationMemory, InMemoryStore  # noqa: E402


class FakeClock:
    def __init__(self, start=None):
        self.moment = start or datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.moment

    def advance(self, **delta):
        self.moment = self.moment + timedelta(**delta)
        return self.moment


PROFILE = {
    "name": "João", "intent": "BUY", "price_range": "800k",
    "region": "Copacabana", "bedrooms": "3", "urgency": "low",
    "phone": "(21) 98765-4321",
}

NEW_PROPERTY = {
    "id": "IMV-0028", "title": "Apartamento de 3 quartos no Vidigal",
    "neighborhood": "Vidigal", "price": 1125000.0,
}


def build(clock=None, profile=None, with_consent=True, message="quero comprar"):
    clock = clock or FakeClock()
    memory = ConversationMemory(InMemoryStore(), clock=clock)

    memory.record_message("lead-1", "user", message)
    memory.record_message("lead-1", "assistant", "legal, me conta mais")
    memory.update_profile("lead-1", profile if profile is not None else PROFILE)

    if with_consent:
        memory.record_consent("lead-1", True, "atendimento imobiliário")

    return memory, clock


# ===========================================================================
# Decisão
# ===========================================================================


class TestCadence(unittest.TestCase):

    def test_normal_lead_uses_the_default_cadence(self):
        self.assertEqual(cadence_for(PROFILE), DEFAULT_CADENCE)

    def test_urgent_lead_uses_the_short_cadence(self):
        # Quem precisa mudar em duas semanas não espera três dias.
        self.assertEqual(cadence_for(dict(PROFILE, urgency="high")), URGENT_CADENCE)

    def test_missing_profile_does_not_break(self):
        self.assertEqual(cadence_for(None), DEFAULT_CADENCE)


class TestWhenToSend(unittest.TestCase):

    def test_does_not_send_before_time(self):
        memory, clock = build()
        clock.advance(hours=5)

        decision = evaluate_followup(memory, "lead-1")
        self.assertFalse(decision.send)
        self.assertIn("faltam", decision.reason)
        self.assertAlmostEqual(decision.hours_remaining, 19.0, places=1)

    def test_sends_after_24h(self):
        memory, clock = build()
        clock.advance(hours=25)

        decision = evaluate_followup(memory, "lead-1")
        self.assertTrue(decision.send)
        self.assertEqual(decision.attempt, 1)
        self.assertEqual(decision.tone, "reopen")

    def test_urgent_lead_is_nudged_after_4h(self):
        memory, clock = build(profile=dict(PROFILE, urgency="high"))
        clock.advance(hours=5)

        self.assertTrue(evaluate_followup(memory, "lead-1").send)

    def test_second_attempt_waits_longer(self):
        memory, clock = build()
        clock.advance(hours=25)
        memory.record_followup("lead-1")

        clock.advance(hours=25)  # 50h of silence, cadence asks for 72h
        self.assertFalse(evaluate_followup(memory, "lead-1").send)

        clock.advance(hours=25)  # 75h
        decision = evaluate_followup(memory, "lead-1")
        self.assertTrue(decision.send)
        self.assertEqual(decision.attempt, 2)
        self.assertEqual(decision.tone, "offer")

    def test_third_attempt_is_the_signoff(self):
        memory, clock = build()
        memory.record_followup("lead-1")
        memory.record_followup("lead-1")
        clock.advance(hours=200)

        decision = evaluate_followup(memory, "lead-1")
        self.assertTrue(decision.send)
        self.assertEqual(decision.tone, "signoff")

    def test_stops_after_the_ceiling(self):
        memory, clock = build()
        for _ in range(3):
            memory.record_followup("lead-1")
        clock.advance(days=90)

        decision = evaluate_followup(memory, "lead-1")
        self.assertFalse(decision.send)
        self.assertIn("teto", decision.reason)

    def test_a_lead_reply_restarts_the_ladder(self):
        # O lead respondeu ao primeiro follow-up: ele está conversando, não
        # fugindo. A próxima retomada volta ao intervalo curto de 24h.
        memory, clock = build()
        clock.advance(hours=25)
        memory.record_followup("lead-1")

        clock.advance(hours=2)
        memory.record_message("lead-1", "user", "opa, desculpa a demora")

        self.assertEqual(memory.state("lead-1")["followups_sent"], 0)

        clock.advance(hours=25)
        decision = evaluate_followup(memory, "lead-1")
        self.assertTrue(decision.send)
        self.assertEqual(decision.attempt, 1)

    def test_lifetime_total_does_not_reset(self):
        memory, _ = build()
        memory.record_followup("lead-1")
        memory.record_message("lead-1", "user", "oi")

        state = memory.state("lead-1")
        self.assertEqual(state["followups_sent"], 0)
        self.assertEqual(state["followups_total"], 1)


class TestWhenNotToSend(unittest.TestCase):
    """A metade que separa follow-up de spam."""

    def test_no_consent_means_no_send(self):
        memory, clock = build(with_consent=False)
        clock.advance(days=5)

        decision = evaluate_followup(memory, "lead-1")
        self.assertFalse(decision.send)
        self.assertIn("LGPD", decision.reason)

    def test_consent_can_be_waived_for_the_site_chat(self):
        # No chat do próprio site o lead está ali; a exigência é do canal
        # externo. Fica como parâmetro, não como decisão escondida.
        memory, clock = build(with_consent=False)
        clock.advance(days=5)

        self.assertTrue(
            evaluate_followup(memory, "lead-1", require_consent=False).send
        )

    def test_opt_out_blocks_forever(self):
        memory, clock = build()
        memory.record_message("lead-1", "user", "não quero mais receber mensagens")
        clock.advance(days=30)

        decision = evaluate_followup(memory, "lead-1")
        self.assertFalse(decision.send)
        self.assertIn("não ser mais contatado", decision.reason)

    def test_opt_out_variations(self):
        for phrase in ["já comprei outro", "pare de me mandar mensagem",
                       "não tenho interesse", "me tira da lista", "desisti"]:
            memory, _ = build()
            memory.record_message("lead-1", "user", phrase)
            self.assertTrue(detect_opt_out(memory, "lead-1"), phrase)

    def test_an_agent_phrase_does_not_trigger_opt_out(self):
        # "me avisa se não quiser mais receber" é fala do AGENTE.
        memory, _ = build()
        memory.record_message(
            "lead-1", "assistant", "me avisa se não quiser mais receber novidades"
        )
        self.assertFalse(detect_opt_out(memory, "lead-1"))

    def test_a_lead_without_conversation_gets_no_followup(self):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())
        decision = evaluate_followup(memory, "ghost-lead")

        self.assertFalse(decision.send)
        self.assertIn("sem conversa", decision.reason)

    def test_followup_does_not_reset_its_own_silence_counter(self):
        # Regressão: a mensagem do follow-up entra no histórico como fala do
        # agente. Se isso contasse como interação, o silêncio zerava e o segundo
        # follow-up nunca chegaria.
        memory, clock = build()
        clock.advance(hours=25)

        FollowUpGenerator(client=UnavailableClient()).send(memory, "lead-1")

        self.assertGreaterEqual(memory.hours_of_silence("lead-1"), 25.0)


class TestPipelineSweep(unittest.TestCase):

    def test_lists_only_who_is_due(self):
        clock = FakeClock()
        memory = ConversationMemory(InMemoryStore(), clock=clock)

        for lead in ("due", "recent"):
            memory.record_message(lead, "user", "quero comprar")
            memory.record_consent(lead, True, "atendimento")

        clock.advance(hours=30)
        memory.record_message("recent", "user", "ainda estou vendo")

        due = leads_due_for_followup(memory)
        self.assertEqual([lead for lead, _ in due], ["due"])

    def test_orders_the_most_silent_first(self):
        clock = FakeClock()
        memory = ConversationMemory(InMemoryStore(), clock=clock)

        memory.record_message("old", "user", "oi")
        memory.record_consent("old", True, "atendimento")
        clock.advance(days=3)

        memory.record_message("new", "user", "oi")
        memory.record_consent("new", True, "atendimento")
        clock.advance(days=2)

        self.assertEqual(
            [lead for lead, _ in leads_due_for_followup(memory)], ["old", "new"]
        )

    def test_empty_pipeline(self):
        memory = ConversationMemory(InMemoryStore(), clock=FakeClock())
        self.assertEqual(leads_due_for_followup(memory), [])


# ===========================================================================
# Texto
# ===========================================================================


class TestChannel(unittest.TestCase):

    def test_a_phone_means_whatsapp(self):
        self.assertEqual(suggested_channel(PROFILE), "whatsapp")

    def test_email_only(self):
        profile = dict(PROFILE, phone="undefined", email="a@b.com")
        self.assertEqual(suggested_channel(profile), "email")

    def test_no_contact_leaves_the_chat(self):
        self.assertEqual(suggested_channel({}), "chat")


class TestHeuristicText(unittest.TestCase):
    """Sem LLM, os moldes precisam ser concretos, não genéricos."""

    def generate(self, advance_hours, followups=0, **kwargs):
        memory, clock = build(**kwargs)
        for _ in range(followups):
            memory.record_followup("lead-1")
        clock.advance(hours=advance_hours)

        return FollowUpGenerator(client=UnavailableClient()).generate(memory, "lead-1")

    def test_reopen_cites_the_profile(self):
        followup = self.generate(25)

        self.assertEqual(followup.source, "heuristic")
        self.assertEqual(followup.tone, "reopen")
        self.assertIn("João", followup.text)
        self.assertIn("Copacabana", followup.text)
        self.assertIn("3 quartos", followup.text)

    def test_offer_cites_a_new_property(self):
        memory, clock = build()
        memory.record_followup("lead-1")
        clock.advance(hours=80)

        followup = FollowUpGenerator(client=UnavailableClient()).generate(
            memory, "lead-1", new_properties=[NEW_PROPERTY]
        )

        self.assertEqual(followup.tone, "offer")
        self.assertIn("IMV-0028", followup.text)
        self.assertIn("Vidigal", followup.text)

    def test_signoff_does_not_pressure(self):
        followup = self.generate(200, followups=2)

        self.assertEqual(followup.tone, "signoff")
        self.assertIn("não quero insistir", followup.text.lower())
        self.assertNotIn("?", followup.text)

    def test_the_three_tones_produce_different_texts(self):
        texts = {
            self.generate(25).text,
            self.generate(80, followups=1).text,
            self.generate(200, followups=2).text,
        }
        self.assertEqual(len(texts), 3)

    def test_a_lead_with_almost_no_profile_still_gets_useful_text(self):
        followup = self.generate(25, profile={"intent": "undefined"})

        self.assertTrue(followup.text)
        self.assertIn("comprar ou pra alugar", followup.text)

    def test_does_not_generate_when_it_is_not_time(self):
        self.assertIsNone(self.generate(2))

    def test_serializes_for_the_api(self):
        import json
        data = self.generate(25).to_dict()
        json.dumps(data, ensure_ascii=False)

        self.assertEqual(data["channel"], "whatsapp")
        self.assertIn("decision", data)


class TestTextWithLLM(unittest.TestCase):

    def test_uses_the_llm_reply(self):
        memory, clock = build()
        clock.advance(hours=25)
        client = StubClient("Oi, [NOME_1]! Achei umas opções em Copacabana, quer ver?")

        followup = FollowUpGenerator(client=client).generate(memory, "lead-1")

        self.assertEqual(followup.source, "llm")
        self.assertIn("João", followup.text)
        self.assertNotIn("[NOME_1]", followup.text)

    def test_the_prompt_changes_with_the_tone(self):
        prompts = []
        for followups, hours in ((0, 25), (1, 80), (2, 200)):
            memory, clock = build()
            for _ in range(followups):
                memory.record_followup("lead-1")
            clock.advance(hours=hours)

            client = StubClient("mensagem")
            FollowUpGenerator(client=client).generate(memory, "lead-1")
            prompts.append(client.calls[0])

        self.assertEqual(len(set(prompts)), 3)
        self.assertIn("Primeira retomada", prompts[0])
        self.assertIn("Segunda tentativa", prompts[1])
        self.assertIn("Última tentativa", prompts[2])

    def test_the_prompt_carries_memory_context(self):
        memory, clock = build(message="preciso de 3 quartos em Copacabana")
        memory.record_shown_properties("lead-1", ["IMV-0001"])
        clock.advance(hours=25)

        client = StubClient("mensagem")
        FollowUpGenerator(client=client).generate(
            memory, "lead-1", new_properties=[NEW_PROPERTY]
        )

        prompt = client.calls[0]
        self.assertIn("ÚLTIMA COISA QUE O LEAD DISSE", prompt)
        self.assertIn("IMV-0001", prompt)
        self.assertIn("IMV-0028", prompt)
        self.assertIn("não repita", prompt.lower())

    def test_the_prompt_goes_pseudonymised(self):
        memory, clock = build()
        memory.record_message("lead-1", "user", "meu contato é joao@x.com")
        memory.update_profile("lead-1", {"email": "joao@x.com"})
        clock.advance(hours=25)

        client = StubClient("mensagem")
        FollowUpGenerator(client=client).generate(memory, "lead-1")

        prompt = client.calls[0]
        self.assertNotIn("joao@x.com", prompt)
        self.assertNotIn("João", prompt)

    def test_the_prompt_limits_length_and_questions(self):
        memory, clock = build()
        clock.advance(hours=25)
        client = StubClient("mensagem")
        FollowUpGenerator(client=client).generate(memory, "lead-1")

        prompt = client.calls[0]
        self.assertIn("máximo 3 linhas", prompt)
        self.assertIn("UMA pergunta", prompt)
        self.assertIn("Não invente", prompt)

    def test_a_failing_llm_falls_back_to_the_template(self):
        class BrokenClient:
            name, available = "broken", True

            def generate(self, prompt, temperature=0.4):
                raise RuntimeError("timeout")

        memory, clock = build()
        clock.advance(hours=25)

        followup = FollowUpGenerator(client=BrokenClient()).generate(memory, "lead-1")
        self.assertEqual(followup.source, "heuristic")
        self.assertTrue(followup.text)

    def test_an_empty_reply_falls_back_to_the_template(self):
        memory, clock = build()
        clock.advance(hours=25)

        followup = FollowUpGenerator(client=StubClient("   ")).generate(memory, "lead-1")
        self.assertEqual(followup.source, "heuristic")

    def test_quotes_are_stripped_from_the_reply(self):
        memory, clock = build()
        clock.advance(hours=25)

        followup = FollowUpGenerator(client=StubClient('"Oi, tudo bem?"')).generate(
            memory, "lead-1"
        )
        self.assertEqual(followup.text, "Oi, tudo bem?")


class TestSending(unittest.TestCase):

    def test_send_records_everything(self):
        memory, clock = build()
        clock.advance(hours=25)

        followup = FollowUpGenerator(client=UnavailableClient()).send(
            memory, "lead-1", new_properties=[NEW_PROPERTY]
        )

        state = memory.state("lead-1")
        self.assertEqual(state["followups_sent"], 1)
        self.assertIsNotNone(state["last_followup_at"])
        self.assertIn("IMV-0028", state["shown_properties"])
        self.assertEqual(state["messages"][-1]["content"], followup.text)
        self.assertEqual(state["messages"][-1]["role"], "assistant")

    def test_sending_before_time_does_nothing(self):
        memory, clock = build()
        clock.advance(hours=2)

        self.assertIsNone(
            FollowUpGenerator(client=UnavailableClient()).send(memory, "lead-1")
        )
        self.assertEqual(memory.state("lead-1")["followups_sent"], 0)

    def test_the_full_three_attempt_cycle(self):
        memory, clock = build()
        generator = FollowUpGenerator(client=UnavailableClient())
        tones = []

        for _ in range(4):
            clock.advance(days=10)
            followup = generator.send(memory, "lead-1")
            if followup:
                tones.append(followup.tone)

        # Três tentativas e para. A quarta não acontece.
        self.assertEqual(tones, ["reopen", "offer", "signoff"])
        self.assertEqual(memory.state("lead-1")["followups_sent"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
