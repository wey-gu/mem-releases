import unittest

import app_store_release


class FakeClient:
    def __init__(self) -> None:
        self.calls = []

    def call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if path == "/v1/reviewSubmissions":
            return {"data": {"id": "submission-id"}}
        return {}


class AppStoreReleaseTest(unittest.TestCase):
    def test_version_state_prefers_current_field(self) -> None:
        version = {
            "attributes": {
                "appStoreState": "READY_FOR_REVIEW",
                "appVersionState": "PREPARE_FOR_SUBMISSION",
            }
        }

        self.assertEqual(app_store_release._version_state(version), "READY_FOR_REVIEW")

    def test_single_rejects_ambiguous_matches(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "expected one version, found 2"):
            app_store_release._single([{"id": "one"}, {"id": "two"}], "version")

    def test_submitted_states_include_review_and_release(self) -> None:
        self.assertIn("WAITING_FOR_REVIEW", app_store_release.SUBMITTED_STATES)
        self.assertIn("READY_FOR_SALE", app_store_release.SUBMITTED_STATES)

    def test_has_values_requires_every_non_empty_value(self) -> None:
        attributes = {"name": "Nowledge", "email": ""}

        self.assertTrue(app_store_release._has_values(attributes, ("name",)))
        self.assertFalse(app_store_release._has_values(attributes, ("name", "email")))
        self.assertFalse(app_store_release._has_values(attributes, ("missing",)))

    def test_submit_existing_fails_closed_on_build_mismatch(self) -> None:
        status = {
            "app": {"id": "app-id"},
            "build": {"id": "new-build"},
            "app_store_version": {
                "id": "version-id",
                "version": "0.10.80",
                "state": "PREPARE_FOR_SUBMISSION",
                "linked_build_id": "old-build",
                "locales": ["en-US"],
                "has_review_detail": True,
            },
            "review_submissions": [],
        }

        with self.assertRaisesRegex(RuntimeError, "not linked"):
            app_store_release.submit_existing(FakeClient(), status)

    def test_submit_existing_is_idempotent_after_submission(self) -> None:
        client = FakeClient()
        status = {
            "app": {"id": "app-id"},
            "build": {"id": "build-id"},
            "app_store_version": {
                "id": "version-id",
                "version": "0.10.80",
                "state": "WAITING_FOR_REVIEW",
                "linked_build_id": "build-id",
                "locales": ["en-US"],
                "has_review_detail": True,
            },
            "review_submissions": [],
        }

        app_store_release.submit_existing(client, status)

        self.assertEqual(client.calls, [])

    def test_submit_existing_posts_the_prepared_version(self) -> None:
        client = FakeClient()
        status = {
            "app": {"id": "app-id"},
            "build": {"id": "build-id"},
            "app_store_version": {
                "id": "version-id",
                "version": "0.10.80",
                "state": "PREPARE_FOR_SUBMISSION",
                "linked_build_id": "build-id",
                "locales": ["en-US"],
                "has_review_detail": True,
            },
            "review_submissions": [],
        }

        app_store_release.submit_existing(client, status)

        self.assertEqual(client.calls[0][0:2], ("POST", "/v1/reviewSubmissions"))
        self.assertEqual(client.calls[1][0:2], ("POST", "/v1/reviewSubmissionItems"))
        self.assertEqual(
            client.calls[2][0:2],
            ("PATCH", "/v1/reviewSubmissions/submission-id"),
        )
        relationship = client.calls[1][2]["data"]["relationships"]["appStoreVersion"]
        self.assertEqual(relationship["data"]["id"], "version-id")


if __name__ == "__main__":
    unittest.main()
