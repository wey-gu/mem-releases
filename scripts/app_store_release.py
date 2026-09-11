#!/usr/bin/env python3
"""Prepare, inspect, or submit a Nowledge Mem App Store version."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


BASE = "https://api.appstoreconnect.apple.com"
BUNDLE_ID = "co.nowledge.mem.mobile"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
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
        url = path if path.startswith("https://") else BASE + path
        request = urllib.request.Request(
            url,
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
    return (
        attributes.get("appStoreState")
        or attributes.get("appVersionState")
        or "UNKNOWN"
    )


def _has_values(attributes: dict[str, Any], names: tuple[str, ...]) -> bool:
    return all(attributes.get(name) not in (None, "") for name in names)


def _all_pages(client: Client, path: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_path: str | None = path
    while next_path:
        response = client.call("GET", next_path)
        items.extend(response.get("data", []))
        next_path = response.get("links", {}).get("next")
    return items


def _png_properties(path: Path) -> tuple[int, int, bool]:
    header = path.read_bytes()[:29]
    if len(header) != 29 or header[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError(f"screenshot is not a PNG: {path}")
    if header[12:16] != b"IHDR":
        raise RuntimeError(f"screenshot has no PNG IHDR chunk: {path}")
    width, height, _depth, color_type, _compression, _filter, _interlace = (
        struct.unpack(">IIBBBBB", header[16:29])
    )
    return width, height, color_type in {4, 6}


def _load_metadata(version_string: str) -> tuple[Path, dict[str, Any]]:
    directory = REPOSITORY_ROOT / "app-store" / version_string
    path = directory / "metadata.json"
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"cannot load App Store metadata: {path}: {error}"
        ) from error
    if metadata.get("version") != version_string:
        raise RuntimeError(
            f"App Store metadata version mismatch: expected {version_string!r}"
        )

    app_info = metadata.get("app_info", {})
    subtitle = app_info.get("subtitle", "")
    if not app_info.get("privacy_policy_url", "").startswith("https://"):
        raise RuntimeError("App Store privacy policy URL must use HTTPS")
    if len(subtitle) > 30:
        raise RuntimeError("App Store subtitle exceeds 30 characters")

    price = metadata.get("price", {})
    if not price.get("base_territory"):
        raise RuntimeError("App Store price has no base territory")
    try:
        customer_price = Decimal(price.get("customer_price", ""))
    except InvalidOperation as error:
        raise RuntimeError("App Store customer price is invalid") from error
    if customer_price < 0:
        raise RuntimeError("App Store customer price cannot be negative")

    localizations = metadata.get("localizations", [])
    if not localizations:
        raise RuntimeError("App Store metadata has no localizations")
    localization_locales = {item.get("locale") for item in localizations}
    if app_info.get("locale") not in localization_locales:
        raise RuntimeError("App info locale has no matching version localization")
    for localization in localizations:
        if len(localization.get("description", "")) > 4000:
            raise RuntimeError("App Store description exceeds 4000 characters")
        if len(localization.get("keywords", "")) > 100:
            raise RuntimeError("App Store keywords exceed 100 characters")
        if len(localization.get("promotional_text", "")) > 170:
            raise RuntimeError("App Store promotional text exceeds 170 characters")
        for name in ("marketing_url", "support_url"):
            if not localization.get(name, "").startswith("https://"):
                raise RuntimeError(f"App Store {name} must use HTTPS")

    screenshots = metadata.get("screenshots", [])
    if not screenshots:
        raise RuntimeError("App Store metadata has no screenshots")
    for screenshot in screenshots:
        if screenshot.get("locale") not in localization_locales:
            raise RuntimeError("App Store screenshot locale has no localization")
        screenshot_path = directory / screenshot["file"]
        width, height, has_alpha = _png_properties(screenshot_path)
        if (width, height) != (screenshot["width"], screenshot["height"]):
            raise RuntimeError(
                "App Store screenshot dimensions do not match metadata: "
                f"{screenshot_path}"
            )
        if has_alpha:
            raise RuntimeError(
                f"App Store screenshot has an alpha channel: {screenshot_path}"
            )
    return directory, metadata


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
                        "id": localization.get("id"),
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
    manual_prices: list[dict[str, Any]] = []
    if isinstance(price_schedule, dict):
        manual_prices = _all_pages(
            client,
            _query(
                f"/v1/appPriceSchedules/{price_schedule['id']}/manualPrices",
                {"limit": "200"},
            ),
        )
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

    versions = _all_pages(
        client,
        _query(
            f"/v1/apps/{app_id}/appStoreVersions",
            {
                "filter[platform]": "IOS",
                "limit": "200",
            },
        ),
    )
    matching_versions = [
        version
        for version in versions
        if version.get("attributes", {}).get("versionString") == version_string
    ]
    store_version = (
        _single(matching_versions, f"iOS App Store version {version_string}")
        if matching_versions
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

    screenshot_status: list[dict[str, Any]] = []
    for localization in localizations:
        localization_id = localization["id"]
        screenshot_sets = client.call(
            "GET",
            _query(
                f"/v1/appStoreVersionLocalizations/{localization_id}/appScreenshotSets",
                {"limit": "50"},
            ),
        ).get("data", [])
        for screenshot_set in screenshot_sets:
            screenshots = client.call(
                "GET",
                _query(
                    f"/v1/appScreenshotSets/{screenshot_set['id']}/appScreenshots",
                    {"limit": "50"},
                ),
            ).get("data", [])
            screenshot_status.append(
                {
                    "locale": localization.get("attributes", {}).get("locale"),
                    "display_type": screenshot_set.get("attributes", {}).get(
                        "screenshotDisplayType"
                    ),
                    "files": [
                        {
                            "id": screenshot["id"],
                            "file_name": screenshot.get("attributes", {}).get(
                                "fileName"
                            ),
                            "checksum": screenshot.get("attributes", {}).get(
                                "sourceFileChecksum"
                            ),
                            "state": screenshot.get("attributes", {})
                            .get("assetDeliveryState", {})
                            .get("state"),
                        }
                        for screenshot in screenshots
                    ],
                }
            )

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
            "has_price": any(
                price.get("attributes", {}).get("endDate") is None
                for price in manual_prices
            ),
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
        "app_store_versions": [
            {
                "id": version["id"],
                "version": version.get("attributes", {}).get("versionString"),
                "state": _version_state(version),
            }
            for version in versions
        ],
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
                "screenshots": screenshot_status,
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


def _upload_part(operation: dict[str, Any], content: bytes) -> None:
    offset = operation["offset"]
    length = operation["length"]
    headers = {
        header["name"]: header["value"]
        for header in operation.get("requestHeaders", [])
    }
    request = urllib.request.Request(
        operation["url"],
        method=operation["method"],
        data=content[offset : offset + length],
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")[:1000]
        raise RuntimeError(
            f"App Store screenshot upload part failed with HTTP {error.code}: {detail}"
        ) from error


def _wait_for_screenshot(client: Client, screenshot_id: str) -> None:
    for _attempt in range(60):
        screenshot = client.call("GET", f"/v1/appScreenshots/{screenshot_id}")["data"]
        delivery = screenshot.get("attributes", {}).get("assetDeliveryState", {})
        state = delivery.get("state")
        if state == "COMPLETE":
            return
        if state == "FAILED":
            raise RuntimeError(
                f"App Store screenshot processing failed: {delivery.get('errors', [])}"
            )
        time.sleep(5)
    raise RuntimeError("timed out waiting for App Store screenshot processing")


def _is_reusable_screenshot(
    screenshot: dict[str, Any], file_name: str, checksum: str
) -> bool:
    attributes = screenshot.get("attributes", {})
    reported_checksum = attributes.get("sourceFileChecksum")
    return (
        attributes.get("fileName") == file_name
        and reported_checksum in (None, checksum)
        and attributes.get("assetDeliveryState", {}).get("state") == "COMPLETE"
    )


def _ensure_screenshot(
    client: Client,
    localization_id: str,
    display_type: str,
    path: Path,
) -> None:
    screenshot_sets = client.call(
        "GET",
        _query(
            f"/v1/appStoreVersionLocalizations/{localization_id}/appScreenshotSets",
            {"limit": "50"},
        ),
    ).get("data", [])
    matches = [
        item
        for item in screenshot_sets
        if item.get("attributes", {}).get("screenshotDisplayType") == display_type
    ]
    if len(matches) > 1:
        raise RuntimeError(
            f"multiple App Store screenshot sets exist for {display_type}"
        )
    if matches:
        screenshot_set = matches[0]
    else:
        screenshot_set = client.call(
            "POST",
            "/v1/appScreenshotSets",
            {
                "data": {
                    "type": "appScreenshotSets",
                    "attributes": {"screenshotDisplayType": display_type},
                    "relationships": {
                        "appStoreVersionLocalization": {
                            "data": {
                                "type": "appStoreVersionLocalizations",
                                "id": localization_id,
                            }
                        }
                    },
                }
            },
        )["data"]

    content = path.read_bytes()
    checksum = hashlib.md5(content).hexdigest()
    screenshots = client.call(
        "GET",
        _query(
            f"/v1/appScreenshotSets/{screenshot_set['id']}/appScreenshots",
            {"limit": "50"},
        ),
    ).get("data", [])
    if screenshots:
        reusable = [
            screenshot
            for screenshot in screenshots
            if _is_reusable_screenshot(screenshot, path.name, checksum)
        ]
        if len(reusable) == 1 and len(screenshots) == 1:
            return
        raise RuntimeError(
            f"App Store screenshot set {display_type} contains unexpected assets"
        )

    reservation = client.call(
        "POST",
        "/v1/appScreenshots",
        {
            "data": {
                "type": "appScreenshots",
                "attributes": {"fileName": path.name, "fileSize": len(content)},
                "relationships": {
                    "appScreenshotSet": {
                        "data": {
                            "type": "appScreenshotSets",
                            "id": screenshot_set["id"],
                        }
                    }
                },
            }
        },
    )["data"]
    operations = reservation.get("attributes", {}).get("uploadOperations", [])
    if not operations:
        raise RuntimeError("App Store screenshot reservation has no upload operations")
    for operation in operations:
        _upload_part(operation, content)
    client.call(
        "PATCH",
        f"/v1/appScreenshots/{reservation['id']}",
        {
            "data": {
                "type": "appScreenshots",
                "id": reservation["id"],
                "attributes": {
                    "uploaded": True,
                    "sourceFileChecksum": checksum,
                },
            }
        },
    )
    _wait_for_screenshot(client, reservation["id"])


def _create_availability(client: Client, app_id: str) -> None:
    territories = _all_pages(client, _query("/v1/territories", {"limit": "200"}))
    if not territories:
        raise RuntimeError("App Store Connect returned no territories")
    linkages = []
    included = []
    for index, territory in enumerate(territories):
        availability_id = f"${{territory-{index}}}"
        linkages.append(
            {"type": "territoryAvailabilities", "id": availability_id}
        )
        included.append(
            {
                "type": "territoryAvailabilities",
                "id": availability_id,
                "attributes": {"available": True, "preOrderEnabled": False},
                "relationships": {
                    "territory": {
                        "data": {"type": "territories", "id": territory["id"]}
                    }
                },
            }
        )
    client.call(
        "POST",
        "/v2/appAvailabilities",
        {
            "data": {
                "type": "appAvailabilities",
                "attributes": {"availableInNewTerritories": True},
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}},
                    "territoryAvailabilities": {"data": linkages},
                },
            },
            "included": included,
        },
    )


def _create_price_schedule(
    client: Client, app_id: str, price: dict[str, str]
) -> None:
    base_territory = price["base_territory"]
    customer_price = Decimal(price["customer_price"])
    price_points = _all_pages(
        client,
        _query(
            f"/v1/apps/{app_id}/appPricePoints",
            {
                "filter[territory]": base_territory,
                "fields[appPricePoints]": "customerPrice",
                "limit": "200",
            },
        ),
    )
    matching_points = [
        point
        for point in price_points
        if Decimal(point.get("attributes", {}).get("customerPrice", "-1"))
        == customer_price
    ]
    price_point = _single(
        matching_points,
        f"{base_territory} App Store price point for {customer_price}",
    )
    local_id = "${price-0}"
    client.call(
        "POST",
        "/v1/appPriceSchedules",
        {
            "data": {
                "type": "appPriceSchedules",
                "relationships": {
                    "app": {"data": {"type": "apps", "id": app_id}},
                    "baseTerritory": {
                        "data": {
                            "type": "territories",
                            "id": base_territory,
                        }
                    },
                    "manualPrices": {
                        "data": [{"type": "appPrices", "id": local_id}]
                    },
                },
            },
            "included": [
                {
                    "type": "appPrices",
                    "id": local_id,
                    "attributes": {"startDate": None, "endDate": None},
                    "relationships": {
                        "appPricePoint": {
                            "data": {
                                "type": "appPricePoints",
                                "id": price_point["id"],
                            }
                        }
                    },
                }
            ],
        },
    )


def prepare(client: Client, status: dict[str, Any], version_string: str) -> None:
    directory, metadata = _load_metadata(version_string)
    build = status["build"]
    if build is None:
        raise RuntimeError("no VALID App Store Connect build exists for this version")

    app_id = status["app"]["id"]
    app_infos = status["store_preparation"]["app_infos"]
    app_info = _single(app_infos, "editable App Store app info")
    app_info_localizations = [
        item
        for item in app_info["localizations"]
        if item["locale"] == metadata["app_info"]["locale"]
    ]
    app_info_localization = _single(
        app_info_localizations, "primary App Store app info localization"
    )
    categories = _all_pages(
        client,
        _query(
            "/v1/appCategories",
            {"filter[platforms]": "IOS", "limit": "200"},
        ),
    )
    if metadata["primary_category_id"] not in {item["id"] for item in categories}:
        raise RuntimeError(
            f"unknown iOS App Store category: {metadata['primary_category_id']}"
        )
    client.call(
        "PATCH",
        f"/v1/appInfoLocalizations/{app_info_localization['id']}",
        {
            "data": {
                "type": "appInfoLocalizations",
                "id": app_info_localization["id"],
                "attributes": {
                    "subtitle": metadata["app_info"]["subtitle"],
                    "privacyPolicyUrl": metadata["app_info"]["privacy_policy_url"],
                },
            }
        },
    )
    if app_info["primary_category_id"] != metadata["primary_category_id"]:
        client.call(
            "PATCH",
            f"/v1/appInfos/{app_info['id']}",
            {
                "data": {
                    "type": "appInfos",
                    "id": app_info["id"],
                    "relationships": {
                        "primaryCategory": {
                            "data": {
                                "type": "appCategories",
                                "id": metadata["primary_category_id"],
                            }
                        }
                    },
                }
            },
        )
    if not status["store_preparation"]["has_availability"]:
        _create_availability(client, app_id)
    if not status["store_preparation"]["has_price"]:
        _create_price_schedule(client, app_id, metadata["price"])

    version = status["app_store_version"]
    if version is None:
        reusable_drafts = [
            item
            for item in status["app_store_versions"]
            if item["state"] == "PREPARE_FOR_SUBMISSION"
        ]
        if reusable_drafts:
            version = _single(reusable_drafts, "reusable App Store version draft")
        else:
            version = client.call(
                "POST",
                "/v1/appStoreVersions",
                {
                    "data": {
                        "type": "appStoreVersions",
                        "attributes": {
                            "platform": "IOS",
                            "versionString": version_string,
                            "copyright": metadata["copyright"],
                            "releaseType": metadata["release_type"],
                        },
                        "relationships": {
                            "app": {"data": {"type": "apps", "id": app_id}},
                        },
                    }
                },
            )["data"]

    version_state = version.get("state") or _version_state(version)
    if version_state != "PREPARE_FOR_SUBMISSION":
        raise RuntimeError(
            f"App Store version cannot be prepared in state {version_state}"
        )
    client.call(
        "PATCH",
        f"/v1/appStoreVersions/{version['id']}",
        {
            "data": {
                "type": "appStoreVersions",
                "id": version["id"],
                "attributes": {
                    "versionString": version_string,
                    "copyright": metadata["copyright"],
                    "releaseType": metadata["release_type"],
                },
                "relationships": {
                    "build": {"data": {"type": "builds", "id": build["id"]}}
                },
            }
        },
    )

    version_id = version["id"]
    existing_localizations = client.call(
        "GET", f"/v1/appStoreVersions/{version_id}/appStoreVersionLocalizations"
    ).get("data", [])
    localization_by_locale = {
        item.get("attributes", {}).get("locale"): item
        for item in existing_localizations
    }
    for localization in metadata["localizations"]:
        attributes = {
            "description": localization["description"],
            "keywords": localization["keywords"],
            "marketingUrl": localization["marketing_url"],
            "promotionalText": localization["promotional_text"],
            "supportUrl": localization["support_url"],
        }
        existing = localization_by_locale.get(localization["locale"])
        if existing is None:
            created = client.call(
                "POST",
                "/v1/appStoreVersionLocalizations",
                {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "attributes": {
                            "locale": localization["locale"],
                            **attributes,
                        },
                        "relationships": {
                            "appStoreVersion": {
                                "data": {
                                    "type": "appStoreVersions",
                                    "id": version_id,
                                }
                            }
                        },
                    }
                },
            )["data"]
            localization_by_locale[localization["locale"]] = created
        else:
            client.call(
                "PATCH",
                f"/v1/appStoreVersionLocalizations/{existing['id']}",
                {
                    "data": {
                        "type": "appStoreVersionLocalizations",
                        "id": existing["id"],
                        "attributes": attributes,
                    }
                },
            )

    review_detail = client.call(
        "GET",
        f"/v1/appStoreVersions/{version_id}/appStoreReviewDetail",
        allow_not_found=True,
    ).get("data")
    if not isinstance(review_detail, dict):
        beta_review = client.call("GET", f"/v1/apps/{app_id}/betaAppReviewDetail")
        beta_attributes = beta_review["data"].get("attributes", {})
        review_fields = (
            "contactFirstName",
            "contactLastName",
            "contactPhone",
            "contactEmail",
            "demoAccountName",
            "demoAccountPassword",
            "demoAccountRequired",
            "notes",
        )
        client.call(
            "POST",
            "/v1/appStoreReviewDetails",
            {
                "data": {
                    "type": "appStoreReviewDetails",
                    "attributes": {
                        name: beta_attributes.get(name) for name in review_fields
                    },
                    "relationships": {
                        "appStoreVersion": {
                            "data": {"type": "appStoreVersions", "id": version_id}
                        }
                    },
                }
            },
        )

    for screenshot in metadata["screenshots"]:
        localization = localization_by_locale[screenshot["locale"]]
        _ensure_screenshot(
            client,
            localization["id"],
            screenshot["display_type"],
            directory / screenshot["file"],
        )


def submit_existing(client: Client, status: dict[str, Any]) -> None:
    build = status["build"]
    version = status["app_store_version"]
    if build is None:
        raise RuntimeError("no VALID App Store Connect build exists for this version")
    if version is None:
        raise RuntimeError(
            "the App Store version does not exist; prepare its metadata and "
            "screenshots first"
        )
    if version["linked_build_id"] != build["id"]:
        raise RuntimeError(
            "the App Store version is not linked to the newest VALID build; "
            "refusing to submit"
        )
    if version["state"] in SUBMITTED_STATES:
        print(f"App Store version is already submitted or released: {version['state']}")
        return
    if not version["locales"] or not version["has_review_detail"]:
        raise RuntimeError(
            "the App Store version lacks localizations or App Review details; "
            "refusing to submit"
        )
    preparation = status["store_preparation"]
    app_info = _single(preparation["app_infos"], "editable App Store app info")
    app_localizations = app_info["localizations"]
    if (
        not app_info["has_age_rating_declaration"]
        or not app_info["primary_category_id"]
        or not any(item["privacy_policy_url"] for item in app_localizations)
        or not preparation["has_price_schedule"]
        or not preparation["has_price"]
        or not preparation["has_availability"]
    ):
        raise RuntimeError(
            "the App Store app info, pricing, or availability is incomplete; "
            "refusing to submit"
        )
    _directory, metadata = _load_metadata(version["version"])
    required_screenshots = {
        (item["locale"], item["display_type"]) for item in metadata["screenshots"]
    }
    complete_screenshots = {
        (item["locale"], item["display_type"])
        for item in version["screenshots"]
        if len(item["files"]) == 1 and item["files"][0]["state"] == "COMPLETE"
    }
    if not required_screenshots.issubset(complete_screenshots):
        raise RuntimeError(
            "required App Store screenshots are incomplete; refusing to submit"
        )
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
    parser.add_argument("action", choices=("status", "prepare", "submit-existing"))
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
        if args.action == "prepare":
            prepare(client, status, args.version)
            refreshed = inspect(client, args.version)
            print(json.dumps(refreshed, indent=2, sort_keys=True))
        elif args.action == "submit-existing":
            submit_existing(client, status)
            refreshed = inspect(client, args.version)
            print(json.dumps(refreshed, indent=2, sort_keys=True))
    except (KeyError, OSError, RuntimeError) as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
