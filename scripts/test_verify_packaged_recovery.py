"""Reject receipts that could disguise missing Graph records or mutated history."""

import copy
from pathlib import Path
import tempfile
import unittest

from verify_packaged_recovery import recovery_notice, retained_records, tree


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


if __name__ == "__main__":
    unittest.main()
