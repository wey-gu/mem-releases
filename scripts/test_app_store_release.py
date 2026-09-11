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


class AvailabilityClient:
    def __init__(self) -> None:
        self.body = None

    def call(self, method, path, body=None):
        if path.startswith("/v1/territories?"):
            return {"data": [{"id": "USA"}, {"id": "SGP"}]}
        if method == "POST" and path == "/v2/appAvailabilities":
            self.body = body
            return {"data": {"id": "availability-id"}}
        raise AssertionError(f"unexpected request: {method} {path}")


class AppStoreReleaseTest(unittest.TestCase):
    def prepared_status(self, state="PREPARE_FOR_SUBMISSION"):
        return {
            "app": {"id": "app-id"},
            "build": {"id": "build-id"},
            "app_store_version": {
                "id": "version-id",
                "version": "0.10.80",
                "state": state,
                "linked_build_id": "build-id",
                "locales": ["en-US"],
                "has_review_detail": True,
                "screenshots": [
                    {
                        "locale": "en-US",
                        "display_type": "APP_IPHONE_67",
                        "files": [{"state": "COMPLETE"}],
                    },
                    {
                        "locale": "en-US",
                        "display_type": "APP_IPAD_PRO_3GEN_129",
                        "files": [{"state": "COMPLETE"}],
                    },
                ],
            },
            "store_preparation": {
                "app_infos": [
                    {
                        "has_age_rating_declaration": True,
                        "primary_category_id": "PRODUCTIVITY",
                        "localizations": [
                            {"privacy_policy_url": "https://mem.nowledge.co/privacy"}
                        ],
                    }
                ],
                "has_price_schedule": True,
                "has_availability": True,
            },
            "review_submissions": [],
        }

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

    def test_release_metadata_and_screenshots_are_valid(self) -> None:
        directory, metadata = app_store_release._load_metadata("0.10.80")

        self.assertEqual(metadata["version"], "0.10.80")
        self.assertEqual(len(metadata["screenshots"]), 2)
        self.assertTrue((directory / metadata["screenshots"][0]["file"]).is_file())

    def test_availability_uses_matching_inline_local_ids(self) -> None:
        client = AvailabilityClient()

        app_store_release._create_availability(client, "app-id")

        relationship = client.body["data"]["relationships"][
            "territoryAvailabilities"
        ]["data"]
        included = client.body["included"]
        expected_ids = ["${territory-0}", "${territory-1}"]
        self.assertEqual([item["id"] for item in relationship], expected_ids)
        self.assertEqual([item["id"] for item in included], expected_ids)
        self.assertEqual(
            [item["relationships"]["territory"]["data"]["id"] for item in included],
            ["USA", "SGP"],
        )

    def test_complete_screenshot_without_reported_checksum_is_reusable(self) -> None:
        screenshot = {
            "attributes": {
                "fileName": "ipad-13.png",
                "sourceFileChecksum": None,
                "assetDeliveryState": {"state": "COMPLETE"},
            }
        }

        self.assertTrue(
            app_store_release._is_reusable_screenshot(
                screenshot, "ipad-13.png", "expected-checksum"
            )
        )

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
        status = self.prepared_status("WAITING_FOR_REVIEW")

        app_store_release.submit_existing(client, status)

        self.assertEqual(client.calls, [])

    def test_submit_existing_posts_the_prepared_version(self) -> None:
        client = FakeClient()
        status = self.prepared_status()

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
