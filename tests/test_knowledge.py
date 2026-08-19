import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import knowledge


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.path = os.path.join(self.tmpdir.name, "knowledge.json")

    def test_load_missing_defaults(self):
        state = knowledge.load_state(self.path)
        self.assertIsNone(state["collection_id"])
        self.assertEqual(state["documents"], [])

    def test_save_roundtrip(self):
        state = knowledge.default_state()
        knowledge.set_collection_id(state, "collection_test123", name="My Manuals")
        state["documents"] = [{"name": "fsm.pdf", "file_id": "file_1", "added_at": "x"}]
        knowledge.save_state(state, self.path)
        loaded = knowledge.load_state(self.path)
        self.assertEqual(loaded["collection_id"], "collection_test123")
        self.assertEqual(loaded["documents"][0]["name"], "fsm.pdf")

    def test_collection_ids_empty_without_id(self):
        self.assertEqual(knowledge.collection_ids(knowledge.default_state()), [])

    def test_collection_ids_with_id(self):
        state = knowledge.default_state()
        state["collection_id"] = "collection_xyz"
        self.assertEqual(knowledge.collection_ids(state), ["collection_xyz"])

    def test_format_for_prompt_no_manuals(self):
        text = knowledge.format_for_prompt(knowledge.default_state())
        self.assertIn("No workshop manuals", text)

    def test_format_for_prompt_with_docs(self):
        state = knowledge.default_state()
        state["collection_id"] = "collection_1"
        state["documents"] = [{"name": "4g15-fsm.pdf"}]
        text = knowledge.format_for_prompt(state)
        self.assertIn("4g15-fsm.pdf", text)
        self.assertIn("factory specs", text)

    def test_stats_readable(self):
        state = knowledge.default_state()
        self.assertIn("Manuals", knowledge.stats(state))

    def test_ingest_local_text_and_prompt(self):
        fpath = os.path.join(self.tmpdir.name, "ce-4g15.txt")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("AU CE 4G15 idle 750+/-50. Thermostat opens 82C.")
        state = knowledge.default_state()
        name, n = knowledge.ingest_local(state, fpath)
        self.assertEqual(name, "ce-4g15.txt")
        self.assertGreater(n, 10)
        text = knowledge.format_for_prompt(state)
        self.assertIn("ce-4g15.txt", text)
        self.assertIn("Thermostat opens 82C", text)
        self.assertIn("local extract", knowledge.stats(state))

    @patch("knowledge._mgmt_request")
    def test_ensure_collection_reuses_existing(self, mock_mgmt):
        state = knowledge.default_state()
        state["collection_id"] = "collection_existing"
        cid = knowledge.ensure_collection(state)
        self.assertEqual(cid, "collection_existing")
        mock_mgmt.assert_not_called()

    @patch("knowledge._mgmt_request")
    def test_ensure_collection_creates(self, mock_mgmt):
        mock_mgmt.return_value = {"collection_id": "collection_new"}
        state = knowledge.default_state()
        cid = knowledge.ensure_collection(state)
        self.assertEqual(cid, "collection_new")
        self.assertEqual(state["collection_id"], "collection_new")
        mock_mgmt.assert_called_once()

    @patch("knowledge.add_document_to_collection")
    @patch("knowledge._upload_file_to_xai")
    @patch("knowledge.ensure_collection")
    def test_upload_document(self, mock_ensure, mock_upload, mock_add):
        mock_ensure.return_value = "collection_1"
        mock_upload.return_value = "file_99"
        state = knowledge.default_state()
        # create a real temp file to satisfy isfile
        fpath = os.path.join(self.tmpdir.name, "notes.txt")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write("idle 800 rpm")
        file_id, cid, name = knowledge.upload_document(state, fpath)
        self.assertEqual(file_id, "file_99")
        self.assertEqual(cid, "collection_1")
        self.assertEqual(name, "notes.txt")
        self.assertEqual(len(state["documents"]), 1)
        mock_add.assert_called_once_with("collection_1", "file_99")

    def test_build_tools_helper(self):
        import ai_assistant
        # force=True restores always-on tools (legacy / explicit)
        tools = ai_assistant.build_tools(["collection_abc"], force=True)
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["type"], "file_search")
        self.assertEqual(tools[0]["vector_store_ids"], ["collection_abc"])
        self.assertEqual(
            ai_assistant.build_tools([], force=True),
            [ai_assistant.WEB_SEARCH_TOOL],
        )
        # Smart path: live-status questions skip tools for speed
        self.assertIsNone(
            ai_assistant.build_tools([], question="is coolant normal")
        )


if __name__ == "__main__":
    unittest.main()
