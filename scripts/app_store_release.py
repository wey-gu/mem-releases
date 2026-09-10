#!/usr/bin/env python3
"""Inspect or submit an existing Nowledge Mem App Store version.

This deliberately does not create App Store metadata. A production submission
must reuse an existing version that already has the intended build, screenshots,
localizations, review details, pricing, and availability configured in App Store
Connect. Missing preparation therefore fails closed before a review submission
is created.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE = "https://api.appstoreconnect.apple.com"
BUNDLE_ID = "co.nowledge.mem.mobile"
SUBMITTED_STATES = {
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
    "PENDING_APPLE_RELEASE",
    "PENDING_DEVELOPER_RELEASE",
    "PROCESSING_FOR_DISTRIBUTION",
    "READY_FOR_DISTRIBUTION",
    "READY_FOR_SALE",
}
REVIEW_IN_PROGRESS_STATES = {
    "WAITING_FOR_REVIEW",
    "IN_REVIEW",
    "COMPLETING",
    "COMPLETE",
}


def _token(key_id: str, issuer: str, private_key: str) -> str:
    import jwt

    now = int(time.time())
    return jwt.encode(
        {"iss": issuer, "iat": now, "exp": now + 900, "aud": "appstoreconnect-v1"},
        private_key,
        algorithm="ES256",
        headers={"kid": key_id, "typ": "JWT"},
    )


class Client:
    def __init__(self, key_id: str, issuer: str, private_key: str) -> None:
        self._args = (key_id, issuer, private_key)

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        request = urllib.request.Request(
            BASE + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {_token(*self._args)}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request) as response:
                raw = response.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            if allow_not_found and error.code == 404:
                return {}
            detail = error.read().decode(errors="replace")[:2000]
            raise RuntimeError(f"{method} {path} -> {error.code} {detail}") from error


def _query(path: str, params: dict[str, str]) -> str:
    return f"{path}?{urllib.parse.urlencode(params)}"


def _single(items: list[dict[str, Any]], description: str) -> dict[str, Any]:
    if len(items) != 1:
        raise RuntimeError(f"expected one {description}, found {len(items)}")
    return items[0]


def _version_state(version: dict[str, Any]) -> str:
    attributes = version.get("attributes", {})
    return attributes.get("appStoreState") or attributes.get("appVersionState") or "UNKNOWN"


def _has_values(attributes: dict[str, Any], names: tuple[str, ...]) -> bool:
    return all(attributes.get(name) not in (None, "") for name in names)


def inspect(client: Client, version_string: str) -> dict[str, Any]:
    app = _single(
        client.call(
            "GET",
            _query("/v1/apps", {"filter[bundleId]": BUNDLE_ID, "limit": "2"}),
        ).get("data", []),
        f"app for bundle {BUNDLE_ID}",
    )
    app_id = app["id"]
    app_attributes = app.get("attributes", {})

    beta_review_detail = client.call(
        "GET",
        f"/v1/apps/{app_id}/betaAppReviewDetail",
        allow_not_found=True,
    ).get("data")
    beta_review_attributes = (
        beta_review_detail.get("attributes", {})
        if isinstance(beta_review_detail, dict)
        else {}
    )

    app_infos = client.call(
        "GET", _query(f"/v1/apps/{app_id}/appInfos", {"limit": "10"})
    ).get("data", [])
    app_info_status = []
    for app_info in app_infos:
        app_info_id = app_info["id"]
        localizations = client.call(
            "GET",
            _query(
                f"/v1/appInfos/{app_info_id}/appInfoLocalizations",
                {"limit": "200"},
            ),
        ).get("data", [])
        age_rating = client.call(
            "GET",
            f"/v1/appInfos/{app_info_id}/ageRatingDeclaration",
            allow_not_found=True,
        ).get("data")
        primary_category = client.call(
            "GET",
            f"/v1/appInfos/{app_info_id}/relationships/primaryCategory",
            allow_not_found=True,
        ).get("data")
        app_info_status.append(
            {
                "id": app_info_id,
                "state": app_info.get("attributes", {}).get("state"),
                "app_store_state": app_info.get("attributes", {}).get(
                    "appStoreState"
                ),
                "has_age_rating_declaration": isinstance(age_rating, dict),
                "primary_category_id": (
                    primary_category.get("id")
                    if isinstance(primary_category, dict)
                    else None
                ),
                "localizations": [
                    {
                        "locale": localization.get("attributes", {}).get("locale"),
                        "name": localization.get("attributes", {}).get("name"),
                        "subtitle": localization.get("attributes", {}).get("subtitle"),
                        "privacy_policy_url": localization.get("attributes", {}).get(
                            "privacyPolicyUrl"
                        ),
                    }
                    for localization in localizations
                ],
            }
        )

    price_schedule = client.call(
        "GET",
        f"/v1/apps/{app_id}/appPriceSchedule",
        allow_not_found=True,
    ).get("data")
    availability = client.call(
        "GET",
        f"/v1/apps/{app_id}/relationships/appAvailabilityV2",
        allow_not_found=True,
    ).get("data")

    builds = client.call(
        "GET",
        _query(
            "/v1/builds",
            {
                "filter[app]": app_id,
                "filter[version]": version_string,
                "sort": "-uploadedDate",
                "limit": "10",
            },
        ),
    ).get("data", [])
    valid_builds = [
        build
        for build in builds
        if build.get("attributes", {}).get("processingState") == "VALID"
    ]
    build = valid_builds[0] if valid_builds else None
    beta_review: dict[str, Any] | None = None
    beta_groups: list[dict[str, Any]] = []
    if build is not None:
        review = client.call(
            "GET", f"/v1/builds/{build['id']}/betaAppReviewSubmission"
        ).get("data")
        beta_review = review if isinstance(review, dict) else None
        beta_groups = client.call(
            "GET",
            _query(
                "/v1/betaGroups",
                {"filter[builds]": build["id"], "limit": "20"},
            ),
        ).get("data", [])

    versions = client.call(
        "GET",
        _query(
            f"/v1/apps/{app_id}/appStoreVersions",
            {
                "filter[platform]": "IOS",
                "filter[versionString]": version_string,
                "limit": "10",
            },
        ),
    ).get("data", [])
    store_version = (
        _single(versions, f"iOS App Store version {version_string}")
        if versions
        else None
    )

    localizations: list[dict[str, Any]] = []
    linked_build: dict[str, Any] | None = None
    review_detail: dict[str, Any] | None = None
    if store_version is not None:
        version_id = store_version["id"]
        localizations = client.call(
            "GET", f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations"
        ).get("data", [])
        linked = client.call(
            "GET", f"/v1/appStoreVersions/{version_id}/relationships/build"
        ).get("data")
        linked_build = linked if isinstance(linked, dict) else None
        details = client.call(
            "GET", f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail"
        ).get("data")
        review_detail = details if isinstance(details, dict) else None

    submissions = client.call(
        "GET",
        _query(
            f"/v1/apps/{app_id}/reviewSubmissions",
            {
                "filter[platform]": "IOS",
                "include": "appStoreVersionForReview",
                "limit": "20",
            },
        ),
    ).get("data", [])

    return {
        "app": {
            "id": app_id,
            "name": app_attributes.get("name"),
            "bundle_id": BUNDLE_ID,
            "sku": app_attributes.get("sku"),
            "primary_locale": app_attributes.get("primaryLocale"),
            "made_for_kids": app_attributes.get("isOrEverWasMadeForKids"),
            "content_rights_declaration": app_attributes.get(
                "contentRightsDeclaration"
            ),
        },
        "store_preparation": {
            "app_infos": app_info_status,
            "has_beta_review_contact": _has_values(
                beta_review_attributes,
                (
                    "contactFirstName",
                    "contactLastName",
                    "contactPhone",
                    "contactEmail",
                ),
            ),
            "beta_demo_account_required": beta_review_attributes.get(
                "demoAccountRequired"
            ),
            "has_beta_demo_credentials": _has_values(
                beta_review_attributes,
                ("demoAccountName", "demoAccountPassword"),
            ),
            "has_price_schedule": isinstance(price_schedule, dict),
            "has_availability": isinstance(availability, dict),
        },
        "build": (
            {
                "id": build["id"],
                "version": build.get("attributes", {}).get("version"),
                "uploaded_date": build.get("attributes", {}).get("uploadedDate"),
                "processing_state": build.get("attributes", {}).get("processingState"),
                "beta_review_state": (
                    beta_review.get("attributes", {}).get("betaReviewState")
                    if beta_review
                    else None
                ),
                "beta_groups": [
                    {
                        "name": group.get("attributes", {}).get("name"),
                        "is_internal": group.get("attributes", {}).get(
                            "isInternalGroup"
                        ),
                    }
                    for group in beta_groups
                ],
            }
            if build is not None
            else None
        ),
        "app_store_version": (
            {
                "id": store_version["id"],
                "version": store_version.get("attributes", {}).get("versionString"),
                "state": _version_state(store_version),
                "release_type": store_version.get("attributes", {}).get("releaseType"),
                "linked_build_id": linked_build.get("id") if linked_build else None,
                "locales": sorted(
                    localization.get("attributes", {}).get("locale", "UNKNOWN")
                    for localization in localizations
                ),
                "has_review_detail": review_detail is not None,
            }
            if store_version is not None
            else None
        ),
        "review_submissions": [
            {
                "id": submission["id"],
                "state": submission.get("attributes", {}).get("state"),
                "submitted_date": submission.get("attributes", {}).get("submittedDate"),
                "app_store_version_id": (
                    submission.get("relationships", {})
                    .get("appStoreVersionForReview", {})
                    .get("data", {})
                    .get("id")
                ),
            }
            for submission in submissions
        ],
    }


def submit_existing(client: Client, status: dict[str, Any]) -> None:
    build = status["build"]
    version = status["app_store_version"]
    if build is None:
        raise RuntimeError("no VALID App Store Connect build exists for this version")
    if version is None:
        raise RuntimeError(
            "the App Store version does not exist; prepare its metadata and screenshots first"
        )
    if version["linked_build_id"] != build["id"]:
        raise RuntimeError(
            "the App Store version is not linked to the newest VALID build; refusing to submit"
        )
    if not version["locales"] or not version["has_review_detail"]:
        raise RuntimeError(
            "the App Store version lacks localizations or App Review details; refusing to submit"
        )
    if version["state"] in SUBMITTED_STATES:
        print(f"App Store version is already submitted or released: {version['state']}")
        return

    matching_submissions = [
        submission
        for submission in status["review_submissions"]
        if submission["app_store_version_id"] == version["id"]
    ]
    submission = matching_submissions[0] if matching_submissions else None
    if submission is not None and submission["state"] in REVIEW_IN_PROGRESS_STATES:
        print(f"App review submission is already in progress: {submission['state']}")
        return
    if submission is not None and submission["state"] != "READY_FOR_REVIEW":
        raise RuntimeError(
            f"existing review submission is not reusable: {submission['state']}"
        )

    if submission is None:
        created = client.call(
            "POST",
            "/v1/reviewSubmissions",
            {
                "data": {
                    "type": "reviewSubmissions",
                    "attributes": {"platform": "IOS"},
                    "relationships": {
                        "app": {
                            "data": {"type": "apps", "id": status["app"]["id"]}
                        }
                    },
                }
            },
        )
        submission = created["data"]
        client.call(
            "POST",
            "/v1/reviewSubmissionItems",
            {
                "data": {
                    "type": "reviewSubmissionItems",
                    "relationships": {
                        "reviewSubmission": {
                            "data": {
                                "type": "reviewSubmissions",
                                "id": submission["id"],
                            }
                        },
                        "appStoreVersion": {
                            "data": {"type": "appStoreVersions", "id": version["id"]}
                        },
                    },
                }
            },
        )

    client.call(
        "PATCH",
        f"/v1/reviewSubmissions/{submission['id']}",
        {
            "data": {
                "type": "reviewSubmissions",
                "id": submission["id"],
                "attributes": {"submitted": True},
            }
        },
    )
    print(f"Submitted App Store version {version['version']} for review")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "submit-existing"))
    parser.add_argument("version")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        key_id = os.environ["APPLE_API_KEY"]
        issuer = os.environ["APPLE_API_ISSUER"]
        private_key = Path(os.environ["APPLE_API_KEY_PATH"]).read_text(encoding="utf-8")
        client = Client(key_id, issuer, private_key)
        status = inspect(client, args.version)
        print(json.dumps(status, indent=2, sort_keys=True))
        if args.action == "submit-existing":
            submit_existing(client, status)
            refreshed = inspect(client, args.version)
            print(json.dumps(refreshed, indent=2, sort_keys=True))
    except (KeyError, OSError, RuntimeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
