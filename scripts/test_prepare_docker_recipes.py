import tempfile
import unittest
from pathlib import Path

from scripts.prepare_docker_recipes import (
    RECIPE_NAMES,
    SPACE_ENCODING_COPY,
    WEB_BUILD_COMMAND,
    prepare_docker_recipes,
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


if __name__ == "__main__":
    unittest.main()
