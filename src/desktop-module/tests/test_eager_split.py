import sys
import os
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import llm

class TestEagerSplit(unittest.TestCase):
    def setUp(self):
        # Override configuration for tests to ensure known values
        llm._FIRST_SENTENCE_EAGER = True
        llm._FIRST_FLUSH_MIN_CHARS = 12
        llm._FIRST_FLUSH_CLAUSE_MIN_CHARS = 5
        
        # Save real session
        self.real_session = llm._SESSION
        llm._SESSION = MagicMock()

    def tearDown(self):
        llm._SESSION = self.real_session

    def mock_sse_stream(self, text_chunks):
        """Creates bytes generator in SSE format: data: {"choices": [{"delta": {"content": "..."}}]}"""
        lines = []
        for chunk in text_chunks:
            payload = {"choices": [{"delta": {"content": chunk}}]}
            import json
            lines.append(f"data: {json.dumps(payload)}".encode('utf-8'))
        lines.append(b"data: [DONE]")
        
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = iter(lines)
        llm._SESSION.post.return_value = mock_response

    def test_eager_split_on_comma(self):
        # "Sure, let me check that."
        # "Sure," is 5 chars, which matches clause boundary and is >= _FIRST_FLUSH_CLAUSE_MIN_CHARS (5)
        self.mock_sse_stream(["Su", "re,", " let", " me", " check", " that."])
        
        generator = llm._stream_local_inner([], {}, verify_prefill=False)
        results = list(generator)
        
        sentences = [r[1] for r in results if r[0] == "sentence"]
        # The first yielded sentence should be "Sure,"
        self.assertIn("Sure,", sentences)
        self.assertTrue(len(sentences) >= 2)
        print("test_eager_split_on_comma passed. Sentences:", sentences)

    def test_eager_split_on_space(self):
        # "I think that we should go."
        # No early punctuation. "I think that" is 12 chars. The space after "that" should trigger the split.
        self.mock_sse_stream(["I ", "th", "ink", " th", "at ", "we", " sh", "ould", " go."])
        
        generator = llm._stream_local_inner([], {}, verify_prefill=False)
        results = list(generator)
        
        sentences = [r[1] for r in results if r[0] == "sentence"]
        self.assertIn("I think that", sentences)
        self.assertTrue(len(sentences) >= 2)
        print("test_eager_split_on_space passed. Sentences:", sentences)

    def test_hindi_danda_streams_each_sentence_without_waiting_for_done(self):
        # Isolate normal sentence boundaries from the separate first-clause
        # optimization covered above.
        llm._FIRST_SENTENCE_EAGER = False
        self.mock_sse_stream([
            "यह पहला वाक्य है।",
            " यह दूसरा वाक्य है।",
        ])

        results = list(llm._stream_local_inner([], {}, verify_prefill=False))
        sentences = [item[1] for item in results if item[0] == "sentence"]

        self.assertEqual(sentences, [
            "यह पहला वाक्य है।",
            "यह दूसरा वाक्य है।",
        ])

    def test_extract_sentences_flushes_danda_at_current_stream_end(self):
        sentences, remaining = llm._extract_sentences("ठीक है।")
        self.assertEqual(sentences, ["ठीक है।"])
        self.assertEqual(remaining, "")

    def test_latin_period_still_waits_for_context(self):
        sentences, remaining = llm._extract_sentences("Value is 3.")
        self.assertEqual(sentences, [])
        self.assertEqual(remaining, "Value is 3.")

if __name__ == "__main__":
    unittest.main()
