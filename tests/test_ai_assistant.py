import unittest
from unittest.mock import MagicMock, patch

import ai_assistant


class _FakeResponse:
    def __init__(self, text):
        self.output_text = text
        self.output = []


class AiAssistantTests(unittest.TestCase):
    def _reading(self, **overrides):
        base = dict(
            name="Coolant", value=105.0, units="°C", verdict="bad",
            note="NTC ADC -> °C  ·  warm 80-100",
            criteria="Warm green 80-100C, red <=-20 or >=110",
            baseline_z=3.2,
            ctx_readings={"RPM": 900.0, "Speed": 0.0},
            vehicle_profile="CE Lancer / Mirage 1.5",
            engine_state="warm idle",
        )
        base.update(overrides)
        return ai_assistant.Reading(**base)

    def test_prompt_includes_key_facts(self):
        prompt = ai_assistant._build_prompt(self._reading())
        self.assertIn("Coolant", prompt)
        self.assertIn("105.0", prompt)
        self.assertIn("bad", prompt)
        self.assertIn("CE Lancer / Mirage 1.5", prompt)
        self.assertIn("Engine state: warm idle", prompt)
        self.assertIn("+3.2", prompt)
        self.assertIn("RPM=900.0", prompt)

    def test_prompt_omits_baseline_line_when_none(self):
        prompt = ai_assistant._build_prompt(self._reading(baseline_z=None))
        self.assertNotIn("standard deviations", prompt)
        self.assertNotIn("σ from its normal", prompt)

    @patch("ai_assistant.OpenAI")
    def test_explain_returns_text(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("Check the radiator fan.")
        mock_openai_cls.return_value = mock_client

        result = ai_assistant.explain(
            self._reading(), api_key="test-key", memory_brief="User's name: Simon.",
        )

        self.assertEqual(result, "Check the radiator fan.")
        mock_openai_cls.assert_called_once()
        _, kwargs = mock_openai_cls.call_args
        self.assertEqual(kwargs["api_key"], "test-key")
        self.assertEqual(kwargs["base_url"], ai_assistant.XAI_BASE_URL)
        _, create_kwargs = mock_client.responses.create.call_args
        self.assertEqual(create_kwargs["model"], ai_assistant.DEFAULT_MODEL)
        self.assertEqual(create_kwargs["store"], False)
        self.assertNotIn("tools", create_kwargs)  # explain is tool-free (fast)
        self.assertEqual(create_kwargs["reasoning"], {"effort": "low"})
        roles = [m["role"] for m in create_kwargs["input"]]
        self.assertEqual(roles[0], "system")
        self.assertIn("Simon", create_kwargs["input"][0]["content"])

    @patch("ai_assistant.OpenAI")
    def test_explain_propagates_api_errors(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = RuntimeError("network down")
        mock_openai_cls.return_value = mock_client

        with self.assertRaises(RuntimeError):
            ai_assistant.explain(self._reading(), api_key="test-key")

    @patch("ai_assistant.OpenAI")
    def test_interpret_log_skips_tools_and_returns_text(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("WATCH — coolant climbed.")
        mock_openai_cls.return_value = mock_client
        text = ai_assistant.interpret_log(
            "RPM 900-6500  Coolant 82-98",
            "CE 4G15 12V",
            memory_brief="User's name: Simon.",
            api_key="test-key",
            collection_ids=["collection_should_be_ignored"],
        )
        self.assertIn("WATCH", text)
        kwargs = mock_client.responses.create.call_args.kwargs
        self.assertNotIn("tools", kwargs)
        mock_openai_cls.assert_called()
        self.assertEqual(mock_openai_cls.call_args.kwargs.get("max_retries"), 0)

    def test_question_prompt_includes_key_facts(self):
        prompt = ai_assistant._build_question_prompt(
            "is the coolant temp normal right now",
            "RPM=900.0, Coolant=104.0",
            "CE Lancer / Mirage 1.5",
        )
        self.assertIn("is the coolant temp normal right now", prompt)
        self.assertIn("RPM=900.0, Coolant=104.0", prompt)
        self.assertIn("CE Lancer / Mirage 1.5", prompt)

    def test_voice_prompt_is_shorter_style(self):
        prompt = ai_assistant._build_question_prompt(
            "is coolant ok", "Coolant=90", "CE 4G15", for_voice=True,
        )
        self.assertIn("VOICE", prompt)
        self.assertIn("MAX 2", prompt)

    def test_question_prompt_handles_no_live_data(self):
        prompt = ai_assistant._build_question_prompt("what's normal idle rpm", "", "CE Lancer / Mirage 1.5")
        self.assertIn("no live data right now", prompt)

    def test_needs_external_tools_for_specs_not_live_status(self):
        self.assertTrue(ai_assistant.needs_external_tools("what's the torque on the rocker cover bolts"))
        self.assertTrue(ai_assistant.needs_external_tools("show me the pinout for the ISC"))
        self.assertFalse(ai_assistant.needs_external_tools("is the coolant normal right now"))
        self.assertFalse(ai_assistant.needs_external_tools("is idle too high?"))
        # manuals: short live checks still skip tools
        self.assertFalse(
            ai_assistant.needs_external_tools("is coolant ok?", has_manuals=True)
        )
        self.assertTrue(
            ai_assistant.needs_external_tools("what's stock idle from the FSM?", has_manuals=True)
        )

    def test_build_tools_skips_web_for_live_status(self):
        self.assertIsNone(
            ai_assistant.build_tools([], question="is the coolant normal")
        )
        tools = ai_assistant.build_tools([], question="what is the torque spec")
        self.assertEqual(tools, [ai_assistant.WEB_SEARCH_TOOL])
        tools = ai_assistant.build_tools(["collection_abc"], question="is coolant ok?")
        self.assertIsNone(tools)
        tools = ai_assistant.build_tools(
            ["collection_abc"], question="FSM idle target for warm engine"
        )
        self.assertIsNotNone(tools)
        self.assertEqual(tools[0]["type"], "file_search")

    @patch("ai_assistant.OpenAI")
    def test_ask_returns_text_and_history(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("Coolant's fine at idle.")
        mock_openai_cls.return_value = mock_client

        answer, history = ai_assistant.ask(
            "is the coolant okay", "Coolant=85.0", "CE Lancer / Mirage 1.5",
            api_key="test-key", memory_brief="User's name: Simon.",
        )

        self.assertEqual(answer, "Coolant's fine at idle.")
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["content"], "is the coolant okay")  # compact
        self.assertEqual(history[1]["role"], "assistant")
        self.assertEqual(history[1]["content"], "Coolant's fine at idle.")
        _, create_kwargs = mock_client.responses.create.call_args
        self.assertEqual(create_kwargs["model"], ai_assistant.DEFAULT_MODEL)
        # live-status question → no tools
        self.assertNotIn("tools", create_kwargs)

    @patch("ai_assistant.OpenAI")
    def test_ask_voice_uses_smaller_token_budget(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("NORMAL. Coolant is fine.")
        mock_openai_cls.return_value = mock_client

        ai_assistant.ask(
            "is coolant ok", "Coolant=90", "CE 4G15",
            api_key="test-key", for_voice=True,
        )
        _, create_kwargs = mock_client.responses.create.call_args
        self.assertLessEqual(create_kwargs["max_output_tokens"], 180)
        self.assertIn("VOICE", create_kwargs["input"][-1]["content"])

    @patch("ai_assistant.OpenAI")
    def test_ask_uses_tools_for_spec_questions(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("Cover bolts are 10 Nm.")
        mock_openai_cls.return_value = mock_client

        answer, _ = ai_assistant.ask(
            "what's the torque on the rocker cover", "", "CE Lancer / Mirage 1.5",
            api_key="test-key",
        )
        self.assertIn("10 Nm", answer)
        _, create_kwargs = mock_client.responses.create.call_args
        self.assertEqual(create_kwargs["tools"], [ai_assistant.WEB_SEARCH_TOOL])

    @patch("ai_assistant.OpenAI")
    def test_ask_includes_file_search_when_collection_linked(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("FSM says 700-900 idle.")
        mock_openai_cls.return_value = mock_client

        answer, _ = ai_assistant.ask(
            "what's stock idle from the workshop manual", "", "CE Lancer / Mirage 1.5",
            api_key="test-key", collection_ids=["collection_abc"],
        )
        self.assertIn("700-900", answer)
        _, create_kwargs = mock_client.responses.create.call_args
        tools = create_kwargs["tools"]
        self.assertEqual(tools[0]["type"], "file_search")
        self.assertEqual(tools[0]["vector_store_ids"], ["collection_abc"])
        user_content = create_kwargs["input"][-1]["content"]
        self.assertIn("manual", user_content.lower())

    @patch("ai_assistant.OpenAI")
    def test_ask_continues_prior_history(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse("At higher RPM it should climb a bit.")
        mock_openai_cls.return_value = mock_client

        prior_history = [
            {"role": "user", "content": "is the coolant okay"},
            {"role": "assistant", "content": "Coolant's fine at idle."},
        ]

        answer, new_history = ai_assistant.ask(
            "what about at higher rpm", "Coolant=85.0", "CE Lancer / Mirage 1.5",
            history=prior_history, api_key="test-key",
        )

        self.assertEqual(answer, "At higher RPM it should climb a bit.")
        self.assertEqual(len(new_history), 4)
        # original history untouched — ask() copies rather than mutating
        self.assertEqual(len(prior_history), 2)
        _, create_kwargs = mock_client.responses.create.call_args
        # system + 2 prior compact + new user (full prompt)
        self.assertEqual(len(create_kwargs["input"]), 4)

    @patch("ai_assistant.OpenAI")
    def test_ask_propagates_api_errors(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.side_effect = RuntimeError("network down")
        mock_openai_cls.return_value = mock_client

        with self.assertRaises(RuntimeError):
            ai_assistant.ask("anything", "", "CE Lancer / Mirage 1.5", api_key="test-key")

    def test_extract_json_object_from_fence(self):
        raw = 'Here you go:\n```json\n{"user_name": "Simon", "lessons": ["PCV hose"]}\n```\n'
        data = ai_assistant._extract_json_object(raw)
        self.assertEqual(data["user_name"], "Simon")
        self.assertEqual(data["lessons"], ["PCV hose"])

    @patch("ai_assistant.OpenAI")
    def test_consolidate_parses_json(self, mock_openai_cls):
        mock_client = MagicMock()
        mock_client.responses.create.return_value = _FakeResponse(
            '{"user_name": "Simon", "lessons": ["hot idle after heatsoak"], '
            '"summary": "Checked coolant; fine.", "vehicle_profile_id": "4g15_12v"}'
        )
        mock_openai_cls.return_value = mock_client

        update = ai_assistant.consolidate(
            [{"role": "user", "content": "idle is high when hot"},
             {"role": "assistant", "content": "Could be heatsoak ISC"}],
            vehicle_profile_id="4g15_12v",
            vehicle_label="CE Lancer / Mirage 1.5",
            api_key="test-key",
        )
        self.assertEqual(update["user_name"], "Simon")
        self.assertIn("hot idle", update["lessons"][0])
        self.assertEqual(update["vehicle_profile_id"], "4g15_12v")

    def test_default_model_is_smart_flagship(self):
        self.assertEqual(ai_assistant.DEFAULT_MODEL, "grok-4.6")
        self.assertIn(ai_assistant.DEFAULT_MODEL, ai_assistant.MODEL_CHOICES)
        self.assertIn("grok-4.6", ai_assistant.MODEL_CHOICES)
        self.assertIn("grok-4.20-0309-non-reasoning", ai_assistant.MODEL_CHOICES)

    def test_resolve_api_key_prefers_explicit(self):
        self.assertEqual(ai_assistant.resolve_api_key("xai-test-key-12345678901234567890"),
                         "xai-test-key-12345678901234567890")

    @patch.dict("os.environ", {"XAI_API_KEY": "xai-env-key-12345678901234567890"}, clear=False)
    def test_resolve_api_key_uses_env_console_key(self):
        self.assertTrue(ai_assistant.resolve_api_key().startswith("xai-env-key"))

    @patch("ai_assistant.read_grok_login_token", return_value="eyJhbGciOi.test.token.value.here")
    @patch.dict("os.environ", {"XAI_API_KEY": ""}, clear=False)
    def test_resolve_api_key_falls_back_to_grok_login(self, _mock):
        # Empty env → Grok login token
        os = __import__("os")
        os.environ.pop("XAI_API_KEY", None)
        self.assertTrue(ai_assistant.resolve_api_key().startswith("eyJ"))


if __name__ == "__main__":
    unittest.main()
