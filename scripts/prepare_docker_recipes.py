#!/usr/bin/env python3
"""Prepare Docker recipes that need source-root build contracts."""

from __future__ import annotations

import argparse
from pathlib import Path


FROZEN_WEB_STATIC_SOURCE = "5a272fd06e3624f27de31f4d815cea9a1dddc232"
WEB_BUILD_COMMAND = "RUN pnpm run build:web && test -f dist-web/index-web.html\n"
WEB_STATIC_COPY = "COPY nmem-cloud/src/web_static.rs /nmem-cloud/src/web_static.rs\n"
SPACE_ENCODING_COPY = "COPY contracts/space-encoding.mjs /contracts/space-encoding.mjs\n"
RECIPE_NAMES = ("Dockerfile.rust", "Dockerfile.rust.cuda", "Dockerfile.rust.vulkan")


def insert_before_web_build(recipe: str, copy_instruction: str, recipe_name: str) -> str:
    if recipe.count(WEB_BUILD_COMMAND) != 1 or copy_instruction in recipe:
        raise ValueError(f"unexpected Web build recipe: {recipe_name}")
    return recipe.replace(WEB_BUILD_COMMAND, copy_instruction + WEB_BUILD_COMMAND, 1)


def prepare_docker_recipes(source_root: Path, output: Path, source_sha: str) -> None:
    output.mkdir(parents=True, exist_ok=True)
    has_space_contract = (source_root / "contracts/space-encoding.mjs").is_file()

    for recipe_name in RECIPE_NAMES:
        recipe = (source_root / "nowledge-graph/docker" / recipe_name).read_text()
        if source_sha == FROZEN_WEB_STATIC_SOURCE:
            if not (source_root / "nmem-cloud/src/web_static.rs").is_file():
                raise ValueError("missing frozen Web static contract")
            recipe = insert_before_web_build(recipe, WEB_STATIC_COPY, recipe_name)
        if has_space_contract:
            recipe = insert_before_web_build(recipe, SPACE_ENCODING_COPY, recipe_name)
        (output / recipe_name).write_text(recipe)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    args = parser.parse_args()
    prepare_docker_recipes(args.source_root, args.output, args.source_sha)


if __name__ == "__main__":
    main()
