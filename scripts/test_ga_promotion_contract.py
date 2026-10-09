"""Guard the GA recovery boundary that failed during 0.10.94 promotion."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_PLATFORMS = (
    'VERIFY_PLATFORMS="mac,mac-intel,win,linux,linux-deb,linux-appimage"'
)


class GAPromotionContractTest(unittest.TestCase):
    def test_windows_pre_cache_initialization_uses_source_contract_and_propagates_failure(self):
        workflow = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text()
        windows = workflow.split("\n  build-windows:\n", 1)[1].split("\n  build-linux-deb-appimage:\n", 1)[0]
        step = windows.split("name: Initialize Rust path dependencies before cache restore\n", 1)[1]
        step = step.split("\n      # BoringSSL", 1)[0]
        self.assertIn("        shell: bash\n", step)
        script = "\n".join(line[10:] for line in step.split("        run: |\n", 1)[1].splitlines())
        self.assertLess(windows.index("Initialize Rust path dependencies before cache restore"),
                        windows.index("Resolve or repair the PDFium runtime cache"))
        vulkan = workflow.split("\n  build-windows-vulkan:\n", 1)[1].split("\n  build-linux-vulkan:\n", 1)[0]
        self.assertLess(vulkan.index("*initialize_windows_path_dependencies"),
                        vulkan.index("uses: Swatinem/rust-cache@v2"))

        for has_helper, status in ((True, 0), (True, 37), (False, 0), (False, 41)):
            with self.subTest(has_helper=has_helper, status=status), tempfile.TemporaryDirectory() as directory:
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
                result = subprocess.run(["bash", "-c", script], cwd=root, check=False,
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
