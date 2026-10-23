"""Reject receipts that could disguise missing Graph records or mutated history."""

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from verify_packaged_recovery import collect_records, owned_ports, recovery_notice, retained_records, tree


class RecoveryEvidenceContract(unittest.TestCase):
    def setUp(self):
        self.expected = [{"id": f"memory-{i}", "title": f"record {i}", "content": f"committed {i}"} for i in range(10)]
        self.observed = {item["id"]: item for item in self.expected[:9]}

    def test_only_the_torn_final_memory_is_optional(self):
        self.assertEqual(len(retained_records(self.expected, self.observed)), 9)
        self.assertEqual(len(retained_records(self.expected, {item["id"]: item for item in self.expected})), 10)

    def test_checkpointed_or_earlier_wal_loss_is_never_prefix_success(self):
        for index in range(9):
            observed = dict(self.observed)
            del observed[self.expected[index]["id"]]
            with self.subTest(index=index), self.assertRaises(RuntimeError):
                retained_records(self.expected, observed)

    def test_same_id_with_changed_graph_content_is_rejected(self):
        observed = copy.deepcopy(self.observed)
        observed["memory-6"]["content"] = "a stale copy"
        with self.assertRaises(RuntimeError):
            retained_records(self.expected, observed)

    def test_duplicate_seed_identity_cannot_hide_a_missing_wal_record(self):
        expected = list(self.expected)
        expected[9] = expected[8]
        with self.assertRaises(RuntimeError):
            retained_records(expected, self.observed)

    def test_historical_memory_is_not_an_admitted_current_record(self):
        with self.assertRaises(RuntimeError):
            retained_records(self.expected, {**self.observed, "historical-only": self.expected[0]})

    def test_collection_reaches_unexpected_memory_before_acceptance(self):
        extra = {"id": "unknown-history", "title": "Unexpected", "content": "Not part of the current store"}
        rows = list(self.observed.values()) + [extra]
        page = {"memories": rows, "pagination": {"total": 10, "offset": 0, "limit": 100, "has_more": False}}
        with patch("verify_packaged_recovery.request", return_value=page) as get:
            actual = collect_records("http://127.0.0.1:4321")
        get.assert_called_once_with("http://127.0.0.1:4321", "GET", "/memories?state=all&space_id=all&limit=100&offset=0")
        self.assertIn(extra["id"], actual)
        with self.assertRaises(RuntimeError):
            retained_records(self.expected, actual)

    def test_collection_rejects_a_partial_page_even_when_known_ids_match(self):
        page = {"memories": list(self.observed.values()), "pagination": {"total": 10, "offset": 0, "limit": 100, "has_more": False}}
        with patch("verify_packaged_recovery.request", return_value=page), self.assertRaises(RuntimeError):
            collect_records("http://127.0.0.1:4321")

    def test_notice_requires_typed_recovery_and_the_exact_source_and_history(self):
        current, historical = Path("current").resolve(), Path("historical").resolve()
        notice = {"reason": "malformed_wal", "recovery_method": "verified_prefix",
                  "chosen_db_path": str(current / "nowledge_graph_v2.db"), "historical_root_paths": [str(historical)]}
        recovery_notice({"db_recovery_notice": notice}, current, [historical])
        mutations = [{"reason": "unknown"}, {"recovery_method": "whole_wal_isolation"},
                     {"chosen_db_path": str(historical / "nowledge_graph_v1.db")},
                     {"historical_root_paths": []}, {"historical_root_paths": [str(historical), str(current)]}]
        for mutation in mutations:
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                recovery_notice({"db_recovery_notice": {**notice, **mutation}}, current, [historical])

    def test_only_an_empty_lifecycle_lease_is_excluded_from_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "nowledge_graph_v1.db").write_bytes(b"historical")
            before = tree(root)
            lease = root / ".nmem-app-data-lifecycle-v1.lock"
            lease.touch()
            self.assertEqual(tree(root), before)
            (root / "unexpected.lock").touch()
            self.assertNotEqual(tree(root), before)
            lease.write_bytes(b"unexpected")
            with self.assertRaises(RuntimeError):
                tree(root)


class WindowsOwnedListenerContract(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows native process/listener contract")
    @patch("verify_packaged_recovery.command", side_effect=subprocess.TimeoutExpired(["powershell.exe"], 10))
    def test_owned_process_without_listener_can_later_become_ready(self, unavailable_cim):
        script = ("import socket,sys\nprint('ready',flush=True)\n"
                  "assert sys.stdin.readline().strip() == 'listen'\n"
                  "s=socket.socket();s.bind(('127.0.0.1',0));s.listen()\n"
                  "print(s.getsockname()[1],flush=True)\nsys.stdin.readline()\ns.close()\n")
        child = subprocess.Popen([sys.executable, "-u", "-c", script], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "ready")
            self.assertEqual(owned_ports(child.pid), [])
            child.stdin.write("listen\n")
            child.stdin.flush()
            port = int(child.stdout.readline().strip())
            deadline = time.monotonic() + 20
            observed = []
            while time.monotonic() < deadline:
                observed = owned_ports(child.pid)
                if observed:
                    break
                time.sleep(0.1)
            self.assertEqual(observed, [port])
            child.stdin.close()
            self.assertEqual(child.wait(timeout=10), 0)
            unavailable_cim.assert_not_called()
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=10)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None and not stream.closed:
                    stream.close()


if __name__ == "__main__":
    unittest.main()
