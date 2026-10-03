"""Guard the GA recovery boundary that failed during 0.10.94 promotion."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CORE_PLATFORMS = (
    'VERIFY_PLATFORMS="mac,mac-intel,win,linux,linux-deb,linux-appimage"'
)


class GAPromotionContractTest(unittest.TestCase):
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
                self.assertIn('name: Refuse to overwrite a published GA Release', workflow)

        promote = (ROOT / ".github" / "workflows" / "promote-rc-to-ga.yml").read_text()
        validate = promote.split("\n  validate:\n", 1)[1].split("\n  promote-docker:\n", 1)[0]
        self.assertIn('Pre-flight desktop policy before any promotion job', validate)
        self.assertIn('node scripts/verify-ga-delivery.mjs --preflight', validate)
        self.assertIn('Older desktop GA cannot move Docker :latest', validate)

        direct = (ROOT / ".github" / "workflows" / "release-desktop.yml").read_text()
        meta = direct.split("\n  meta:\n", 1)[1].split("\n  build-macos-arm64:\n", 1)[0]
        self.assertIn('Pre-flight GA policy before build and optional Vulkan jobs', meta)
        self.assertIn('Refuse published GA before optional Vulkan jobs', meta)

    def test_partial_recovery_does_not_require_deferred_rpm(self):
        workflow = (
            ROOT / ".github" / "workflows" / "finish-ga-release.yml"
        ).read_text()
        self.assertNotIn('steps.rename.outputs.rpm', workflow)
        self.assertNotIn('x86_64-unknown-linux-gnu.rpm', workflow)
        self.assertIn('name: Create draft GA GitHub release', workflow)
        self.assertIn('name: Refuse to modify a published GA Release', workflow)
        self.assertIn('--json isDraft --jq .isDraft', workflow)
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
                self.assertIn('gh release upload "$TAG" -R "$REPO"', section)
                self.assertNotIn("--clobber", section)
                self.assertIn("if: ${{ needs.meta.outputs.is_dry_run == 'true' || needs.meta.outputs.run_publish == 'true' }}", section)
                attach = section.split("Attach -vulkan", 1)[1]
                self.assertLess(attach.index('--json isDraft --jq .isDraft'),
                                attach.index('gh release upload "$TAG"'))

    def test_core_ga_attachment_requires_draft_and_never_clobbers(self):
        for name in ("promote-rc-to-ga.yml", "finish-ga-release.yml",
                     "release-desktop.yml"):
            with self.subTest(workflow=name):
                workflow = (ROOT / ".github" / "workflows" / name).read_text()
                attach = workflow.rsplit("- name: Create ", 1)[1].split(
                    'gh release upload "$TAG"', 1
                )[0]
                self.assertIn('--json isDraft --jq .isDraft', attach)
                upload = workflow.rsplit('gh release upload "$TAG"', 1)[1]
                self.assertFalse(upload.lstrip().startswith(' -R "$REPO" --clobber'))


if __name__ == "__main__":
    unittest.main()
