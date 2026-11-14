import json
import os
import subprocess
import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.prepare_docker_recipes import (
    RECIPE_NAMES,
    SPACE_ENCODING_COPY,
    WEB_BUILD_COMMAND,
    prepare_docker_recipes,
    serialize_cpu_builder,
)


class PrepareDockerRecipesTest(unittest.TestCase):
    def make_source(self, root: Path, include_space_contract: bool) -> None:
        recipes = root / "nowledge-graph/docker"
        recipes.mkdir(parents=True)
        for name in RECIPE_NAMES:
            (recipes / name).write_text(f"FROM base\n{WEB_BUILD_COMMAND}")
        if include_space_contract:
            contract = root / "contracts/space-encoding.mjs"
            contract.parent.mkdir()
            contract.write_text("export const encodeSpace = () => {};\n")

    def test_stages_space_contract_once_in_all_recipes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root, include_space_contract=True)
            output = root / "output"

            prepare_docker_recipes(root, output, "new-source")

            for name in RECIPE_NAMES:
                recipe = (output / name).read_text()
                self.assertEqual(recipe.count(SPACE_ENCODING_COPY), 1, name)
                self.assertLess(recipe.index(SPACE_ENCODING_COPY), recipe.index(WEB_BUILD_COMMAND), name)

    def test_historical_source_without_contract_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root, include_space_contract=False)
            output = root / "output"

            prepare_docker_recipes(root, output, "old-source")

            for name in RECIPE_NAMES:
                recipe = (output / name).read_text()
                self.assertNotIn(SPACE_ENCODING_COPY, recipe, name)
                self.assertEqual(recipe.count(WEB_BUILD_COMMAND), 1, name)

    def test_serial_budget_changes_only_cpu_builder(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_source(root, include_space_contract=True)
            cpu = root / "nowledge-graph/docker/Dockerfile.rust"
            cpu.write_text(
                f"FROM base AS builder\nRUN cargo build --release\n"
                f"FROM runtime\n{WEB_BUILD_COMMAND}"
            )
            baseline, limited = root / "baseline", root / "limited"
            prepare_docker_recipes(root, baseline, "new-source")
            prepare_docker_recipes(root, limited, "new-source", serial_cpu_build=True)
            for name in RECIPE_NAMES:
                before, after = (baseline / name).read_text(), (limited / name).read_text()
                if name == "Dockerfile.rust":
                    self.assertEqual(after.replace("ENV CARGO_BUILD_JOBS=1\n", "", 1), before)
                    self.assertLess(after.index("ENV CARGO_BUILD_JOBS=1"), after.index("RUN cargo build"))
                else:
                    self.assertEqual(after, before)

    def test_serial_budget_rejects_unknown_or_already_configured_builder(self) -> None:
        for recipe in (
            "FROM base\n",
            "FROM base AS builder\nFROM other AS builder\n",
            "FROM base AS builder\nENV CARGO_BUILD_JOBS=8\n",
        ):
            with self.subTest(recipe=recipe), self.assertRaisesRegex(ValueError, "CPU builder"):
                serialize_cpu_builder(recipe)

    @unittest.skipUnless(os.environ.get("GITHUB_ACTIONS") == "true", "Docker integration runs in CI")
    def test_docker_stage_budget_is_absent_from_runtime_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            recipes = root / "nowledge-graph/docker"
            recipes.mkdir(parents=True)
            token = uuid.uuid4().hex
            for name in RECIPE_NAMES:
                (recipes / name).write_text(
                    f"FROM scratch AS builder\nLABEL fixture={token}\n"
                    f"FROM scratch AS runtime\nLABEL fixture={token}\n"
                )
            output = root / "output"
            prepare_docker_recipes(root, output, "fixture", serial_cpu_build=True)
            images = []
            try:
                for stage in ("builder", "runtime"):
                    image = subprocess.check_output(
                        ["docker", "build", "--quiet", "--target", stage,
                         "--file", str(output / "Dockerfile.rust"), str(root)],
                        text=True,
                    ).strip()
                    images.append(image)
                    config = json.loads(subprocess.check_output(
                        ["docker", "image", "inspect", image], text=True
                    ))[0]["Config"]
                    self.assertEqual("CARGO_BUILD_JOBS=1" in (config.get("Env") or []), stage == "builder")
            finally:
                for image in set(images):
                    subprocess.run(["docker", "image", "rm", "--force", image], check=True)


if __name__ == "__main__":
    unittest.main()
