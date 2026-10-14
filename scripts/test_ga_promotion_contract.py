"""Guard release source-dependency and GA delivery boundaries."""

import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import textwrap
import json
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


def workflow_text(name):
    return (ROOT / ".github" / "workflows" / name).read_text()


def job_section(workflow, job):
    section = workflow.split(f"\n  {job}:\n", 1)[1]
    return re.split(r"\n  [A-Za-z0-9_-]+:\n", section, 1)[0]


def step_script(section, step_name):
    step = section.split(f"name: {step_name}\n", 1)[1]
    body = step.split("        run: |\n", 1)[1]
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith("          "):
            break
        lines.append(line)
    return textwrap.dedent("\n".join(lines)) + "\n"


# Minimal stand-in for `docker buildx imagetools` backed by a JSON file that
# maps image refs (tag or @digest) to manifest digests.
FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, sys
state_path = os.environ["FAKE_REGISTRY"]
state = json.load(open(state_path))
args = sys.argv[1:]
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
assert args[:2] == ["buildx", "imagetools"], args
if args[2] == "inspect":
    ref = args[3]
    digest = ref.split("@", 1)[1] if "@" in ref else state.get(ref)
    if digest is None or ("@" in ref and digest not in state.values()):
        sys.stderr.write(f"ERROR: {ref}: not found\n")
        sys.exit(1)
    if "--format" in args:
        print(json.dumps({"digest": digest}))
    else:
        print(f"Name: {ref}\nDigest: {digest}")
elif args[2] == "create":
    tags = [args[i + 1] for i, a in enumerate(args) if a == "-t"]
    src = args[-1]
    digest = src.split("@", 1)[1]
    digest = os.environ.get("FAKE_RETAG_DIGEST", digest)
    for tag in tags:
        state[tag] = digest
    json.dump(state, open(state_path, "w"))
else:
    sys.exit(2)
"""


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
        workflows = ("release-desktop.yml", "test-windows-bazel.yml", "build-rust-bundle.yml")
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
        self.assertIn("desktop_upgrade_pause_active:", workflow)
        self.assertIn("node ../scripts/verify-upgrade-pause.mjs", workflow)
        self.assertIn("node ../scripts/verify-ga-delivery.mjs --staged-while-paused", workflow)

    def test_desktop_update_control_uses_named_secret_store_operator(self):
        workflow = (ROOT / '.github/workflows/desktop-update-control.yml').read_text()
        worker = (ROOT / 'scripts/desktop-update-operator-worker.mjs').read_text()
        config = (ROOT / 'wrangler.desktop-update-operator.jsonc').read_text()
        self.assertIn('environment: release-publish', workflow)
        self.assertIn('expected_revision', workflow)
        self.assertIn('operation_id', workflow)
        self.assertIn('Delete short-lived operator Worker', workflow)
        self.assertIn('result.actor !== env.OPERATOR_NAME', worker)
        self.assertIn('DESKTOP_UPDATE_OPERATOR_TOKENS_STORE', worker)
        self.assertIn('nmem_backbone__production__desktop_update_operators__v1', config)
        self.assertIn('CLOUDFLARE_SECRETS_STORE_API_TOKEN', workflow)
        self.assertIn('Upload the authority receipt', workflow)

    def test_recover_accepts_stopped_authority_with_retained_candidate(self):
        workflow = (ROOT / '.github/workflows/desktop-update-control.yml').read_text()
        section = workflow.split('      - name: Read and validate exact pre-state\n', 1)[1]
        shell = textwrap.dedent(section.split('        run: |\n', 1)[1].split('\n      - name:', 1)[0])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            state = tmp / 'pre-state.json'
            state.write_text(json.dumps({'mode': 'stopped', 'revision': 14,
                                        'stable': {'version': '0.10.98'},
                                        'candidate': {'version': '0.10.100'}}))
            node = tmp / 'node'
            node.write_text('#!/bin/sh\nexit 0\n')
            node.chmod(0o755)
            env = dict(os.environ, PATH=str(tmp) + os.pathsep + os.environ['PATH'],
                       ACTION='recover', EXPECTED_MODE='stopped', EXPECTED_REVISION='14',
                       EXPECTED_STABLE_VERSION='0.10.98', GA='')
            result = subprocess.run(['bash', '-c', shell.replace('/tmp/pre-state.json', str(state))],
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            env['ACTION'] = 'prepare'
            self.assertNotEqual(subprocess.run(['bash', '-c', shell.replace('/tmp/pre-state.json', str(state))],
                                              env=env, capture_output=True).returncode, 0)

    def test_desktop_unpause_requires_published_release_and_authority_receipt(self):
        workflow = (ROOT / '.github/workflows/remove-desktop-upgrade-pause.yml').read_text()
        routes = (ROOT / 'scripts/remove-desktop-pause-routes.mjs').read_text()
        protocol = (ROOT / 'scripts/verify-controlled-desktop-update.mjs').read_text()
        self.assertIn(".isDraft == false", workflow)
        self.assertIn('desktop-update-receipt-${{ inputs.authority_publish_operation_id }}', workflow)
        self.assertIn(".action == \"publish\"", workflow)
        self.assertIn('node scripts/verify-upgrade-pause.mjs --desktop-only', workflow)
        self.assertLess(workflow.index('verify-upgrade-pause.mjs --desktop-only'),
                        workflow.index('remove-desktop-pause-routes.mjs'))
        self.assertIn('node scripts/verify-controlled-desktop-update.mjs', workflow)
        closed = workflow.split('      - name: Verify published packages and the still-closed Desktop boundary', 1)[1].split('      - name: Remove the five exact Desktop pause routes', 1)[0]
        self.assertIn('--desktop-only --legacy-only', closed)
        self.assertIn('VERIFY_DESKTOP_MANIFEST: /tmp/desktop-update-manifest.json', closed)
        self.assertIn('VERIFY_AUTHORITY_REVISION:', closed)
        self.assertIn('node scripts/verify-controlled-desktop-update.mjs', closed)
        self.assertIn('nowledge-mem-upgrade-pause', routes)
        self.assertNotIn('wrangler delete', workflow)
        self.assertIn("control.status === 'allowed'", protocol)

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

    def test_emergency_pause_stages_core_before_apt_and_unpause(self):
        promote = (ROOT / ".github" / "workflows" /
                   "promote-rc-to-ga.yml").read_text()
        self.assertIn("desktop_upgrade_pause_active:", promote)
        self.assertIn("node scripts/verify-upgrade-pause.mjs", promote)
        self.assertIn("node ../scripts/verify-upgrade-pause.mjs", promote)
        self.assertLess(
            promote.index('export VERIFY_PLATFORMS="mac,mac-intel,win,linux,linux-deb,linux-appimage"',
                          promote.index("node ../scripts/verify-upgrade-pause.mjs") - 200),
            promote.index("node ../scripts/verify-upgrade-pause.mjs"),
        )
        self.assertIn("node ../scripts/verify-ga-delivery.mjs --staged-while-paused", promote)
        self.assertIn(
            "if: ${{ !inputs.allow_newer_desktop_latest && !inputs.desktop_upgrade_pause_active }}",
            promote,
        )
        self.assertIn("APT: deferred to promote-apt-to-ga", promote)

        apt = (ROOT / ".github" / "workflows" /
               "promote-apt-to-ga.yml").read_text()
        self.assertIn("node scripts/verify-upgrade-pause.mjs --desktop-only", apt)
        self.assertIn("node scripts/verify-ga-delivery.mjs --apt-preflight", apt)
        self.assertIn("node scripts/update-apt-repo.cjs \"$GA\" \"$DEB\"", apt)
        self.assertIn("node scripts/verify-ga-delivery.mjs --apt-only", apt)
        self.assertLess(apt.index("--apt-preflight"),
                        apt.index("name: Publish the exact GA DEB to APT"))

        open_apt = (ROOT / ".github" / "workflows" /
                    "open-apt-pause-route.yml").read_text()
        self.assertIn("permissions:\n  # Draft Releases are hidden from read-only GITHUB_TOKENs.\n  contents: write", open_apt)
        self.assertIn("node scripts/verify-upgrade-pause.mjs", open_apt)
        self.assertIn("node scripts/open-apt-pause-route.mjs", open_apt)
        self.assertIn("node scripts/verify-upgrade-pause.mjs --desktop-only", open_apt)
        self.assertIn("node scripts/verify-ga-delivery.mjs --apt-preflight", open_apt)
        self.assertLess(open_apt.index("node scripts/verify-upgrade-pause.mjs"),
                        open_apt.index("node scripts/open-apt-pause-route.mjs"))
        self.assertIn("permissions:\n  # Draft Releases are hidden from read-only GITHUB_TOKENs.\n  contents: write", apt)


    def test_release_docker_builds_gpu_images_on_rc_with_rc_scoped_tags(self):
        workflow = workflow_text("release-docker.yml")
        for variant in ("cuda", "vulkan"):
            with self.subTest(variant=variant):
                section = job_section(workflow, f"build-{variant}")
                # No GA-only gate: rc runs must produce the image GA re-tags.
                self.assertNotIn("push_latest", section)
                self.assertNotRegex(section, r"\n    if:")
                self.assertIn(f"      VARIANT: {variant}\n", section)
                self.assertIn("tags: ${{ steps.tags.outputs.list }}", section)
        script = step_script(job_section(workflow, "build-cuda"), "Resolve GPU image tags")
        cases = (
            ("0.10.80-rc1", "true", "cuda",
             "docker.io/nowledgelabs/mem:0.10.80-rc1-cuda"),
            ("0.10.80-rc1", "true", "vulkan",
             "docker.io/nowledgelabs/mem:0.10.80-rc1-vulkan"),
            ("0.10.80", "false", "cuda",
             "docker.io/nowledgelabs/mem:0.10.80-cuda,docker.io/nowledgelabs/mem:0.10-cuda"),
            ("0.10.80", "false", "vulkan",
             "docker.io/nowledgelabs/mem:0.10.80-vulkan,docker.io/nowledgelabs/mem:0.10-vulkan"),
        )
        for version, prerelease, variant, expected in cases:
            with self.subTest(version=version, variant=variant), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "out"
                env = dict(os.environ, IMAGE="docker.io/nowledgelabs/mem", V=version,
                           IS_PRERELEASE=prerelease, VARIANT=variant,
                           GITHUB_OUTPUT=str(output))
                result = subprocess.run(["bash", "-c", script], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_text(), f"list={expected}\n")
                self.assertNotIn("latest", expected)

    def test_release_docker_meta_classifies_prereleases(self):
        meta = job_section(workflow_text("release-docker.yml"), "meta")
        script = meta.split("      - id: v\n        run: |\n", 1)[1]
        script = textwrap.dedent(script.split("\n\n", 1)[0]) + "\n"
        script = (script.replace("${{ github.event_name }}", "push")
                        .replace("${{ inputs.tag }}", "")
                        .replace("${{ inputs.latest }}", ""))
        for ref, prerelease, latest in (("v0.10.80-rc1", "true", "false"),
                                        ("v0.10.80", "false", "true")):
            with self.subTest(ref=ref), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "out"
                env = dict(os.environ, GITHUB_REF_NAME=ref, GITHUB_OUTPUT=str(output))
                result = subprocess.run(["bash", "-c", script], env=env,
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                self.assertEqual(values["is_prerelease"], prerelease)
                self.assertEqual(values["push_latest"], latest)

    def run_gpu_retag(self, registry, retag_digest=None, variant="cuda", source="retag"):
        section = job_section(workflow_text("promote-gpu-variant.yml"), "retag")
        script = step_script(section, "Re-tag rc GPU image to GA")
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)])
        docker = tmp / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        state = tmp / "registry.json"
        state.write_text(json.dumps(registry))
        log = tmp / "docker.log"
        log.write_text("")
        env = dict(os.environ, PATH=str(tmp) + os.pathsep + os.environ["PATH"],
                   FAKE_REGISTRY=str(state), FAKE_DOCKER_LOG=str(log),
                   IMAGE="docker.io/nowledgelabs/mem", RC="0.10.80-rc2", GA="0.10.80",
                   VARIANT=variant, SOURCE=source,
                   GITHUB_STEP_SUMMARY=str(tmp / "summary"))
        if retag_digest:
            env["FAKE_RETAG_DIGEST"] = retag_digest
        result = subprocess.run(["bash", "-c", script], env=env,
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, json.loads(state.read_text()), calls

    def test_gpu_promotion_retags_exact_rc_digest(self):
        rc = {"docker.io/nowledgelabs/mem:0.10.80-rc2-cuda": "sha256:rc"}
        result, registry, calls = self.run_gpu_retag(rc)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(registry["docker.io/nowledgelabs/mem:0.10.80-cuda"], "sha256:rc")
        self.assertEqual(registry["docker.io/nowledgelabs/mem:0.10-cuda"], "sha256:rc")
        self.assertFalse(any("latest" in tag for tag in registry))
        creates = [c for c in calls if c[2] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][-1], "docker.io/nowledgelabs/mem@sha256:rc")

    def test_gpu_promotion_fails_closed_without_rc_image(self):
        result, registry, calls = self.run_gpu_retag({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gpu_source=build", result.stdout)
        self.assertEqual([c for c in calls if c[2] == "create"], [])
        self.assertEqual(registry, {})

    def test_gpu_promotion_rejects_digest_drift_and_bad_inputs(self):
        rc = {"docker.io/nowledgelabs/mem:0.10.80-rc2-vulkan": "sha256:rc"}
        result, _, _ = self.run_gpu_retag(rc, retag_digest="sha256:other", variant="vulkan")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("!= rc digest", result.stdout)
        for variant, source in (("rocm", "retag"), ("cuda", "rebuild")):
            with self.subTest(variant=variant, source=source):
                result, _, calls = self.run_gpu_retag(rc, variant=variant, source=source)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_gpu_rebuild_is_an_explicit_fallback(self):
        variant = workflow_text("promote-gpu-variant.yml")
        self.assertIn("    if: ${{ inputs.source != 'build' }}\n",
                      job_section(variant, "retag"))
        build = job_section(variant, "build")
        self.assertIn("    if: ${{ inputs.source == 'build' }}\n", build)
        self.assertIn("uses: docker/build-push-action@v6", build)
        self.assertNotIn("docker/build-push-action", job_section(variant, "retag"))
        self.assertIn("        default: retag\n", variant)

        promote = workflow_text("promote-rc-to-ga.yml")
        self.assertIn("      gpu_source:\n", promote)
        self.assertRegex(promote, r"gpu_source:\n(?:        .*\n)*?        default: retag\n")
        for job in ("promote-cuda", "promote-vulkan"):
            with self.subTest(job=job):
                self.assertIn("      source: ${{ inputs.gpu_source }}\n",
                              job_section(promote, job))


if __name__ == "__main__":
    unittest.main()
