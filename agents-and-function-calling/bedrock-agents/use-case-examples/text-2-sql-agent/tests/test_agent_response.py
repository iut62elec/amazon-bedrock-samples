from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dependencies.agent_response import read_agent_answer


class AgentStreamTests(unittest.TestCase):
    def test_trace_timestamp_and_split_utf8_answer(self):
        events = iter([
            {"trace": {"eventTime": datetime(2026, 1, 1, tzinfo=timezone.utc)}},
            {"chunk": {"bytes": b"caf\xc3"}},
            {"chunk": {"bytes": b"\xa9"}},
        ])
        self.assertEqual(read_agent_answer({"completion": events}, Mock()), "caf\u00e9")

    def test_trace_without_answer_does_not_report_success(self):
        with self.assertRaisesRegex(ValueError, "no answer chunks"):
            read_agent_answer({"completion": [{"trace": {}}]}, Mock())
