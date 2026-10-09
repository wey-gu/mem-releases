"""Guard release source-dependency and GA delivery boundaries."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_PLATFORMS = (
    'VERIFY_PLATFORMS="mac,mac-intel,win,linux,linux-deb,linux-appimage"'
)


def bundle_arguments(workflow):
    bash_calls = []
    powershell_calls = []
    for line in workflow.splitlines():
        if "./scripts/build-rust-bundle.sh " in line:
            command = line.split("./scripts/build-rust-bundle.sh ", 1)[1].rstrip('"')
            bash_calls.append(shlex.split(command))
        elif r".\scripts\build-rust-bundle.ps1 " in line:
            command = line.split(r".\scripts\build-rust-bundle.ps1 ", 1)[1]
            powershell_calls.append(shlex.split(command))
    return bash_calls, powershell_calls


class GAPromotionContractTest(unittest.TestCase):
    def test_release_bundle_commands_select_public_production_channel(self):
        for name, expected_bash, expected_powershell in (
            ("release-desktop.yml", 5, 2),
            ("test-windows-bazel.yml", 1, 0),
        ):
            workflow = (ROOT / ".github" / "workflows" / name).read_text()
            bash_calls, powershell_calls = bundle_arguments(workflow)
            self.assertEqual(len(bash_calls), expected_bash, name)
            self.assertEqual(len(powershell_calls), expected_powershell, name)
            for args in bash_calls:
                with self.subTest(workflow=name, args=args):
                    self.assertNotIn("--bazel-hawdb-server", args, "Production cannot build the Nightly server")
                    self.assertIn("--bazel-public-binaries", args)
                    self.assertIn("--channel", args)
                    self.assertEqual(sum(arg == "--channel" or arg.startswith("--channel=")
                                         for arg in args), 1, "Production channel must be unique")
                    self.assertEqual(args[args.index("--channel") + 1], "production")
            for args in powershell_calls:
                with self.subTest(workflow=name, args=args):
                    self.assertIn("-Channel", args)
                    self.assertEqual(sum(arg.lower() == "-channel" or arg.lower().startswith("-channel:")
                                         for arg in args), 1, "Production channel must be unique")
                    self.assertEqual(args[args.index("-Channel") + 1], "production")

    def test_bundle_arguments_admitted_by_recorded_product_source(self):
        source_root = os.environ.get("NMEM_RELEASE_SOURCE_ROOT")
        if not source_root:
            self.skipTest("Set NMEM_RELEASE_SOURCE_ROOT to validate the frozen private product source")
        source = (Path(source_root) / "nowledge-graph/scripts/build-rust-bundle.sh").read_text()
        embed_root = Path(source_root) / "nmem-rs/crates/nmem-embed"
        self.assertIn('[[bin]]\nname = "nmem-models"', (embed_root / "Cargo.toml").read_text())
        self.assertIn('"fetch" => {', (embed_root / "src/bin/models_cli.rs").read_text())
        self.assertIn('name: "qwen3-embedding-0.6b-q8_0"', (embed_root / "src/registry.rs").read_text())
        marker = "# ----------------------------------------------------------------------------\n# Configuration / paths"
        self.assertIn(marker, source, "Source admission boundary changed; review the extraction")
        admission = source.split(marker, 1)[0]
        probe = admission + '\nprintf "admitted:%s:%s:%s\\n" "$CHANNEL" "$BAZEL_PUBLIC_BINARIES" "$BAZEL_HAWDB_SERVER"\n'
        for name in ("release-desktop.yml", "test-windows-bazel.yml"):
            workflow = (ROOT / ".github" / "workflows" / name).read_text()
            for args in bundle_arguments(workflow)[0]:
                with self.subTest(workflow=name, args=args):
                    result = subprocess.run(["bash", "-c", probe, "bundle-admission", *args],
                                            capture_output=True, text=True, check=False)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.splitlines()[-1], "admitted:production:true:false")
                    rejected = subprocess.run(
                        ["bash", "-c", probe, "bundle-admission", *args, "--bazel-hawdb-server"],
                        capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn("requires --channel nightly", rejected.stderr)

    def test_public_hawdb_does_not_require_private_credentials(self):
        workflows = ("release-desktop.yml", "test-windows-bazel.yml", "build-rust-bundle.yml", "rpm-test.yml")
        for name in workflows:
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text()
                for obsolete in ("SKEIN_REPO_SSH_KEY", "webfactory/ssh-agent",
                                 "git@github.com:nowledge-co/hawdb.git"):
                    self.assertFalse(obsolete in workflow, f"{name} still requires {obsolete}")
                self.assertIn("repository: nowledge-co/mem", workflow)
                self.assertIn("secrets.MEM_REPO_TOKEN", workflow)

    def test_hawdb_checkout_uses_the_exact_source_gitlink(self):
        expected_sha = "1519433bb70900bf3f51f5a3ed93de0873072713"
        for name in ("release-desktop.yml", "test-windows-bazel.yml"):
            workflow = (ROOT / ".github" / "workflows" / name).read_text()
            checkout = workflow.split("name: Checkout HawDB dependency\n", 1)[1].split(
                "\n      - ", 1
            )[0]
            self.assertIn("if: steps.desktop_dependency.outputs.sha != ''", checkout)
            self.assertIn("repository: nowledge-co/hawdb", checkout)
            self.assertRegex(
                checkout,
                r'(?m)^          ref: "?\$\{\{ steps\.desktop_dependency\.outputs\.sha \}\}"?$',
            )
            self.assertIn("persist-credentials: false", checkout)
            self.assertNotIn("ssh-key:", checkout)
            self.assertNotIn("token:", checkout)
            resolver = workflow.split("        id: desktop_dependency\n", 1)[1].split(
                "\n      - ", 1
            )[0]
            script = "\n".join(
                line[10:] for line in resolver.split("        run: |\n", 1)[1].splitlines()
            )
            cases = ((True, "gitlink"), (False, "missing"), (True, "blob"), (True, "missing"))
            for has_contract, entry_kind in cases:
                with (
                    self.subTest(workflow=name, has_contract=has_contract, entry_kind=entry_kind),
                    tempfile.TemporaryDirectory() as directory,
                ):
                    root = Path(directory)
                    source_script = root / "nowledge-graph" / "scripts" / "build-rust-bundle.sh"
                    source_script.parent.mkdir(parents=True)
                    source_script.write_text("--init-desktop-https\n" if has_contract else "legacy source\n")
                    git_env = {
                        **os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                    }

                    def git(*args):
                        return subprocess.run(["git", *args], cwd=root, env=git_env, check=True,
                                              capture_output=True, text=True)

                    git("init", "-q")
                    git("add", "nowledge-graph")
                    if entry_kind == "gitlink":
                        git("update-index", "--add", "--cacheinfo", f"160000,{expected_sha},hawdb")
                    elif entry_kind == "blob":
                        (root / "hawdb").write_text("This is not a dependency gitlink.\n")
                        git("add", "hawdb")
                    git("-c", "user.name=Release Contract", "-c", "user.email=release-contract@example.invalid",
                        "commit", "--no-gpg-sign", "-qm", "Record candidate dependency")
                    output = root / "github-output"
                    result = subprocess.run(["bash", "-c", script], cwd=root, check=False,
                                            env={**git_env, "GITHUB_OUTPUT": str(output)},
                                            capture_output=True, text=True)
                    if has_contract and entry_kind != "gitlink":
                        self.assertNotEqual(
                            result.returncode, 0, "Malformed source dependency must stop checkout"
                        )
                        self.assertFalse(output.exists())
                    else:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(output.read_text() if output.exists() else "",
                                         f"sha={expected_sha}\n" if has_contract else "")

    def test_build_initialization_uses_source_contract_before_cargo_and_propagates_failure(self):
        workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text()
        windows = workflow.split("\n  build-windows:\n", 1)[1].split("\n  build-linux-deb-appimage:\n", 1)[0]
        anchor = "initialize_windows_path_dependencies"
        definitions = re.findall(
            rf"^      - &{anchor}\n(?P<step>(?:^        .*\n|^\n)+)",
            workflow, re.MULTILINE,
        )
        self.assertEqual(len(definitions), 1, "The shared Windows step must have one definition")
        step = definitions[0]
        self.assertIn("        shell: bash\n", step)
        script = "\n".join(line[10:] for line in step.split("        run: |\n", 1)[1].splitlines())
        self.assertIn(f"- &{anchor}\n{step}", windows)
        self.assertLess(windows.index(f"&{anchor}"),
                        windows.index("uses: Swatinem/rust-cache@v2"))
        self.assertLess(windows.index("Initialize Rust path dependencies before cache restore"),
                        windows.index("Resolve or repair the PDFium runtime cache"))
        vulkan = workflow.split("\n  build-windows-vulkan:\n", 1)[1].split("\n  build-linux-vulkan:\n", 1)[0]
        self.assertLess(vulkan.index("*initialize_windows_path_dependencies"),
                        vulkan.index("uses: Swatinem/rust-cache@v2"))

        scripts_by_job = {"windows": script, "windows-vulkan": script}
        bundle = (ROOT / ".github" / "workflows" / "build-rust-bundle.yml").read_text()
        self.assertLess(bundle.index("Install Rust backend build toolchain (macOS)"),
                        bundle.index("cargo run"))
        self.assertIn("cargo run -p nmem-embed --bin nmem-models -- fetch", bundle)
        for name in ("test-windows-bazel.yml", "build-rust-bundle.yml"):
            standalone = (ROOT / ".github" / "workflows" / name).read_text()
            definitions = re.findall(
                r"^      - name: Initialize Rust path dependencies before cache restore\n"
                r"(?P<step>(?:^        .*\n|^\n)+)",
                standalone, re.MULTILINE,
            )
            self.assertEqual(len(definitions), 1, name)
            standalone_step = definitions[0]
            self.assertIn("        shell: bash\n", standalone_step, name)
            self.assertLess(standalone.index("Initialize Rust path dependencies before cache restore"),
                            standalone.index("uses: Swatinem/rust-cache@v2"), name)
            self.assertLess(standalone.index("Initialize Rust path dependencies before cache restore"),
                            standalone.index("cargo clean" if name.startswith("test-") else "cargo run"), name)
            scripts_by_job[name] = "\n".join(
                line[10:] for line in standalone_step.split("        run: |\n", 1)[1].splitlines()
            )

        # Vulkan resolves the YAML alias to this exact mapping. Exercise both
        # consumers so moving the definition cannot silently select a legacy step.
        for job, has_helper, status in (
            (job, has_helper, status)
            for job in scripts_by_job
            for has_helper, status in ((True, 0), (True, 37), (False, 0), (False, 41))
        ):
            with self.subTest(job=job, has_helper=has_helper, status=status), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "scripts").mkdir()
                if has_helper:
                    helper = root / "scripts" / "init-release-rust-submodules.sh"
                    helper.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > called-contract\n'
                                      f'exit {status}\n')
                    helper.chmod(0o755)
                (root / "bin").mkdir()
                git = root / "bin" / "git"
                git.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$@" > called-legacy\n'
                               f'exit {0 if has_helper else status}\n')
                git.chmod(0o755)
                result = subprocess.run(["bash", "-c", scripts_by_job[job]], cwd=root, check=False,
                                        env={**os.environ, "RUNNER_TEMP": str(root / "runner"),
                                             "GITHUB_ENV": str(root / "github-env"),
                                             "PATH": str(root / "bin") + os.pathsep + os.environ["PATH"]},
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                if has_helper:
                    self.assertTrue((root / "called-contract").is_file(), "Source-owned contract was not invoked")
                    self.assertEqual((root / "called-contract").read_text(), "--init-https\n")
                    self.assertFalse((root / "called-legacy").exists())
                else:
                    self.assertFalse((root / "called-contract").exists())
                    self.assertIn("submodule\nupdate\n--init\n--depth\n1\nupstream_forks/ladybug\nupstream_forks/rig\n",
                                  (root / "called-legacy").read_text())
                if job != "build-rust-bundle.yml":
                    self.assertIn("XDG_CACHE_HOME=", (root / "github-env").read_text())

    def test_desktop_promotion_does_not_deploy_backbone(self):
        for name in ("promote-rc-to-ga.yml", "finish-ga-release.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text()
                self.assertNotIn("update-latest-version.js", workflow)
                self.assertNotIn("wrangler deploy", workflow)
                self.assertIn(CORE_PLATFORMS, workflow)
                self.assertIn("VERIFY_SCOPE", workflow)
                self.assertIn("node ../scripts/verify-ga-delivery.mjs", workflow)
                self.assertIn("uses: actions/checkout@v4", workflow)

    def test_publish_paths_preflight_before_upload_and_limit_duplicate_probes(self):
        for name in ("promote-rc-to-ga.yml", "release-desktop.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text()
                publish = workflow.split("\n  promote-desktop:\n" if name.startswith("promote") else "\n  publish:\n", 1)[1]
                self.assertLess(publish.index("Pre-flight desktop version and APT policy"),
                                publish.index("Upload to Cloudflare R2"))
                self.assertIn("node scripts/verify-ga-delivery.mjs --preflight", publish)
                self.assertIn('VERIFY_SCOPE="$health_scope" timeout', publish)
                self.assertIn("health_scope='latest'", publish)
                self.assertIn('name: Refuse an existing GA Release', workflow)

        promote = (ROOT / ".github" / "workflows" / "promote-rc-to-ga.yml").read_text()
        validate = promote.split("\n  validate:\n", 1)[1].split("\n  promote-docker:\n", 1)[0]
        self.assertIn('Pre-flight desktop policy before any promotion job', validate)
        self.assertIn('node scripts/verify-ga-delivery.mjs --preflight', validate)
        self.assertIn('Older desktop GA cannot move Docker :latest', validate)
        self.assertIn('node scripts/assert-fresh-r2-version.mjs "$GA" core', validate)
        self.assertIn('gh release list -R', validate)

        direct = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text()
        meta = direct.split("\n  meta:\n", 1)[1].split("\n  build-macos-arm64:\n", 1)[0]
        self.assertIn('Pre-flight GA policy before build and optional Vulkan jobs', meta)
        self.assertIn('Refuse an existing GA Release before build and Vulkan jobs', meta)
        self.assertIn('node scripts/assert-fresh-r2-version.mjs "$VERSION" all', meta)

    def test_partial_recovery_does_not_require_deferred_rpm(self):
        workflow = (
            ROOT / ".github" / "workflows" / "finish-ga-release.yml"
        ).read_text()
        self.assertNotIn('steps.rename.outputs.rpm', workflow)
        self.assertNotIn('x86_64-unknown-linux-gnu.rpm', workflow)
        self.assertIn('name: Create draft GA GitHub release', workflow)
        self.assertIn('name: Refuse to modify a published GA Release', workflow)
        self.assertIn('node scripts/upload-draft-release-assets.mjs', workflow)
        self.assertIn('VERIFY_SCOPE="$health_scope" timeout', workflow)

    def test_direct_ga_keeps_all_artifacts_and_verifies_updater_without_deploy(self):
        workflow = (
            ROOT / ".github" / "workflows" / "release-desktop.yml"
        ).read_text()
        publish = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn('Deprecated compatibility input', workflow)
        self.assertNotIn("update-latest-version.js", publish)
        self.assertNotIn("wrangler deploy", publish)
        self.assertIn("- build-linux-rpm", publish)
        self.assertIn('node scripts/upload-package.cjs "$VERSION" linux     "${{ steps.paths.outputs.rpm }}"', publish)
        self.assertIn('"${{ steps.paths.outputs.rpm }}"', publish)
        self.assertIn('VERIFY_RELEASE_VERSION="$VERSION" VERIFY_SCOPE="$scope"', publish)
        self.assertIn("scope='latest,latest-redirect,direct,update'", publish)
        self.assertIn(
            'VERIFY_PLATFORMS="mac,mac-intel,win,linux,linux-deb,linux-rpm,linux-appimage"',
            publish,
        )
        self.assertIn("node ../scripts/verify-ga-delivery.mjs", publish)
        self.assertIn("uses: actions/checkout@v4", publish)

    def test_optional_vulkan_attachments_never_replace_existing_assets(self):
        workflow = (
            ROOT / ".github" / "workflows" / "release-desktop.yml"
        ).read_text()
        for job, next_job in (("build-windows-vulkan", "build-linux-vulkan"),
                              ("build-linux-vulkan", "prerelease-prepare")):
            with self.subTest(job=job):
                section = workflow.split(f"\n  {job}:\n", 1)[1].split(
                    f"\n  {next_job}:\n", 1
                )[0]
                self.assertIn('node release-tools/scripts/upload-draft-release-assets.mjs "$TAG" "$REPO"', section)
                self.assertIn('path: release-tools', section)
                self.assertNotIn('gh release upload "$TAG"', section)
                self.assertIn("if: ${{ needs.meta.outputs.is_dry_run == 'true' || needs.meta.outputs.run_publish == 'true' }}", section)
                self.assertIn('|| gh release view "$TAG" -R "$REPO" >/dev/null', section)

    def test_core_ga_attachment_uses_draft_reconciliation(self):
        for name in ("promote-rc-to-ga.yml", "finish-ga-release.yml",
                     "release-desktop.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text()
                self.assertIn('node scripts/upload-draft-release-assets.mjs "$TAG" "$REPO"',
                              workflow)
                self.assertIn('|| gh release view "$TAG" -R "$REPO" >/dev/null',
                              workflow)
                if name != "finish-ga-release.yml":
                    gate = workflow.split("\n  validate:\n" if name.startswith("promote") else "\n  meta:\n", 1)[1]
                    gate = gate.split("\n  promote-docker:\n" if name.startswith("promote") else "\n  build-macos-arm64:\n", 1)[0]
                    self.assertIn('node scripts/assert-fresh-r2-version.mjs', gate)
                    self.assertIn('gh release list -R', gate)

    def test_deferred_rpm_checks_source_and_ga_assets_before_r2(self):
        workflow = (ROOT / ".github" / "workflows" /
                    "promote-rpm-to-ga.yml").read_text()
        self.assertNotIn("mem-backbone", workflow)
        self.assertNotIn("--clobber", workflow)
        self.assertIn('source_digest="$(jq -r', workflow)
        self.assertIn('node scripts/rpm-ga-policy.mjs', workflow)
        self.assertLess(workflow.index('reconcile-rpm-release-asset.mjs --check-only'),
                        workflow.index('name: Reconcile RPM in Cloudflare R2'))
        self.assertIn('node scripts/reconcile-rpm-release-asset.mjs "v${GA}"', workflow)


if __name__ == "__main__":
    unittest.main()
