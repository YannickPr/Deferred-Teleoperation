from __future__ import annotations

import unittest
from concurrent.futures import Future
from unittest.mock import patch

from slow_client import SlowWorker, strict_json, validate_endpoint
from text_head import render_input, validate_splits


class IOTests(unittest.TestCase):
    def test_no_label_or_id_leakage(self):
        row = {"features": [0, 0.3, 0, 1, 1, 0, 0, 1], "label": 2,
               "sample_id": "secret-row", "session_id": "secret-session"}
        rendered = render_input(row)
        self.assertNotIn("label", rendered)
        self.assertNotIn("secret", rendered)

    def test_reject_nonfinite_features(self):
        with self.assertRaises(ValueError):
            render_input({"features": [float("nan")] * 8})

    def test_disjoint_sessions(self):
        with self.assertRaises(ValueError):
            validate_splits({"train": [{"session_id": "a", "sample_id": "1"}],
                             "test": [{"session_id": "a", "sample_id": "2"}]})

    def test_json_collisions_and_constants(self):
        for text in ('{"a":1,"a":2}', '{"a":1,"A":2}', '{"a":NaN}',
                     '[]', '```json\n{}\n```'):
            with self.assertRaises(ValueError):
                strict_json(text)

    def test_remote_endpoint_rejected(self):
        for endpoint in ("http://example.org/v1", "https://127.0.0.1/v1",
                         "http://user:pw@127.0.0.1/v1", "http://localhost/v1"):
            with self.assertRaises(ValueError):
                validate_endpoint(endpoint)
        validate_endpoint("http://127.0.0.1:8080/v1/chat/completions")

    def test_single_outstanding_and_nonblocking_poll(self):
        worker = SlowWorker("http://127.0.0.1:8080/v1/chat/completions", "local")
        pending = Future()
        with patch.object(worker.executor, "submit", return_value=pending):
            self.assertTrue(worker.submit("r1", {"request_id": "r1"}))
            self.assertFalse(worker.submit("r2", {"request_id": "r2"}))
            self.assertIsNone(worker.poll("r1"))
            pending.set_result({"request_id": "r1"})
            self.assertEqual(worker.poll("r2").error, "SUPERSEDED")
        worker.close()

    def test_fault_does_not_escape_to_control_loop(self):
        worker = SlowWorker("http://127.0.0.1:8080/v1/chat/completions", "local")
        pending = Future()
        with patch.object(worker.executor, "submit", return_value=pending):
            worker.submit("r1", {"request_id": "r1"})
            pending.set_exception(TimeoutError())
            self.assertEqual(worker.poll("r1").error, "TimeoutError")
        worker.close()


if __name__ == "__main__":
    unittest.main()
