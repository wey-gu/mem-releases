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


# Stand-in for `cosign verify`: records the call; FAKE_COSIGN_FAIL=1 means
# "no valid signature".
FAKE_COSIGN = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_DOCKER_LOG"], "a") as log:
    log.write(json.dumps(["cosign"] + sys.argv[1:]) + "\n")
sys.exit(1 if os.environ.get("FAKE_COSIGN_FAIL") else 0)
"""

SIGNER_RE = ("^https://github\\.com/wey-gu/mem-releases/\\.github/workflows/"
             "(release-docker|release-docker-updater|promote-gpu-variant)\\.yml@")


def fake_tools(test, registry):
    tmp = Path(tempfile.mkdtemp())
    test.addCleanup(subprocess.run, ["rm", "-rf", str(tmp)])
    for name, body in (("docker", FAKE_DOCKER), ("cosign", FAKE_COSIGN)):
        tool = tmp / name
        tool.write_text(body)
        tool.chmod(0o755)
    state = tmp / "registry.json"
    state.write_text(json.dumps(registry))
    log = tmp / "docker.log"
    log.write_text("")
    env = dict(os.environ, PATH=str(tmp) + os.pathsep + os.environ["PATH"],
               FAKE_REGISTRY=str(state), FAKE_DOCKER_LOG=str(log),
               GITHUB_STEP_SUMMARY=str(tmp / "summary"))
    return tmp, state, log, env


class GAPromotionContractTest(unittest.TestCase):
    def test_release_bundle_commands_select_public_production_channel(self):
        for name, expected_bash, expected_powershell in (
            ("release-desktop.yml", 4, 2),
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
        # Path dependencies must exist before any target-cache restore (if the
        # job still has one) and before Cargo builds the backend.
        if "uses: Swatinem/rust-cache@v2" in windows:
            self.assertLess(windows.index(f"&{anchor}"),
                            windows.index("uses: Swatinem/rust-cache@v2"))
        self.assertLess(windows.index(f"&{anchor}"),
                        windows.index("name: Build Rust backend bundle (Windows)"))
        self.assertLess(windows.index("Initialize Rust path dependencies before cache restore"),
                        windows.index("Resolve or repair the PDFium runtime cache"))
        vulkan = workflow.split("\n  build-windows-vulkan:\n", 1)[1].split("\n  build-linux-vulkan:\n", 1)[0]
        if "uses: Swatinem/rust-cache@v2" in vulkan:
            self.assertLess(vulkan.index("*initialize_windows_path_dependencies"),
                            vulkan.index("uses: Swatinem/rust-cache@v2"))
        self.assertLess(vulkan.index("*initialize_windows_path_dependencies"),
                        vulkan.index("name: Build Rust backend bundle"))

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
        self.assertIn("- build-linux-deb-appimage", publish)
        self.assertIn("RPM=\"$(find artifacts/linux-rpm -name '*.rpm' -print -quit)\"", publish)
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

    def test_rpm_is_bundled_from_the_deb_appimage_build(self):
        workflow = (
            ROOT / ".github" / "workflows" / "release-desktop.yml"
        ).read_text()
        self.assertNotIn("\n  build-linux-rpm:\n", workflow)
        self.assertNotIn("build-linux-rpm", workflow)
        linux = workflow.split("\n  build-linux-deb-appimage:\n", 1)[1].split(
            "\n  build-windows-vulkan:\n", 1
        )[0]
        self.assertEqual(linux.count("./scripts/build-rust-bundle.sh "), 1)
        self.assertIn(" rpm cpio\n", linux)
        core_upload = linux.index("          name: linux-deb-appimage\n")
        rpm_step = linux.index("      - name: Build, locate, and verify native .rpm (Tauri bundle)\n")
        rpm_upload = linux.index("          name: linux-rpm\n")
        self.assertLess(core_upload, rpm_step, "RPM must not delay or block DEB/AppImage")
        self.assertLess(rpm_step, rpm_upload)
        rpm = linux[rpm_step:]
        self.assertIn("pnpm run tauri bundle --bundles rpm", rpm)
        self.assertNotIn("tauri build", rpm, "RPM must reuse the DEB/AppImage build outputs")
        self.assertIn("continue-on-error: true", rpm.split("\n      - ", 1)[0])
        self.assertIn('./scripts/verify-linux-package.sh "$OUT"', rpm)
        self.assertLess(rpm.index("verify-linux-package.sh"), rpm.index('echo "rpm=$OUT"'))
        self.assertIn("if: steps.rpm.outcome == 'success'", rpm)
        self.assertIn("if: steps.rpm.outcome != 'success'", rpm)
        upload = workflow.split("\n  prerelease-upload-linux-rpm:\n", 1)[1].split("\n  prerelease-recap:\n", 1)[0]
        self.assertIn("needs: [meta, prerelease-prepare, build-linux-deb-appimage]", upload)
        self.assertIn("artifact_name: linux-rpm", upload)

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
                # push_latest may only choose the floating tag, never gate the job.
                self.assertNotIn("outputs.push_latest == 'true' ||", section)
                self.assertNotRegex(section, r"\n    if:")
                self.assertIn(f"      VARIANT: {variant}\n", section)
                self.assertIn("tags: ${{ steps.tags.outputs.list }}", section)
        script = step_script(job_section(workflow, "build-cuda"), "Resolve GPU image tags")
        # (version, push_latest, variant, expected tags). An rc always has
        # push_latest=false; an older out-of-band GA can too.
        cases = (
            ("0.10.80-rc1", "false", "cuda",
             "docker.io/nowledgelabs/mem:0.10.80-rc1-cuda"),
            ("0.10.80-rc1", "false", "vulkan",
             "docker.io/nowledgelabs/mem:0.10.80-rc1-vulkan"),
            ("0.10.79", "false", "cuda",
             "docker.io/nowledgelabs/mem:0.10.79-cuda"),
            ("0.10.80", "true", "cuda",
             "docker.io/nowledgelabs/mem:0.10.80-cuda,docker.io/nowledgelabs/mem:0.10-cuda"),
            ("0.10.80", "true", "vulkan",
             "docker.io/nowledgelabs/mem:0.10.80-vulkan,docker.io/nowledgelabs/mem:0.10-vulkan"),
        )
        for version, push_latest, variant, expected in cases:
            with self.subTest(version=version, variant=variant), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "out"
                env = dict(os.environ, IMAGE="docker.io/nowledgelabs/mem", V=version,
                           PUSH_LATEST=push_latest, VARIANT=variant,
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

    def run_gpu_retag(self, registry, retag_digest=None, variant="cuda", source="retag",
                      move_floating="true", unsigned=False):
        section = job_section(workflow_text("promote-gpu-variant.yml"), "retag")
        script = step_script(section, "Re-tag rc GPU image to GA")
        _, state, log, env = fake_tools(self, registry)
        env.update(IMAGE="docker.io/nowledgelabs/mem", RC="0.10.80-rc2", GA="0.10.80",
                   VARIANT=variant, SOURCE=source, MOVE_FLOATING=move_floating,
                   SIGNER=SIGNER_RE)
        if retag_digest:
            env["FAKE_RETAG_DIGEST"] = retag_digest
        if unsigned:
            env["FAKE_COSIGN_FAIL"] = "1"
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
        creates = [c for c in calls if c[0] == "buildx" and c[2] == "create"]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][-1], "docker.io/nowledgelabs/mem@sha256:rc")

    def test_gpu_promotion_fails_closed_without_rc_image(self):
        result, registry, calls = self.run_gpu_retag({})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gpu_source=build", result.stdout)
        self.assertEqual([c for c in calls if c[0] == "buildx" and c[2] == "create"], [])
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

    def test_gpu_promotion_verifies_signature_before_retag(self):
        rc = {"docker.io/nowledgelabs/mem:0.10.80-rc2-cuda": "sha256:rc"}
        result, registry, calls = self.run_gpu_retag(rc, unsigned=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no valid release signature", result.stdout)
        self.assertEqual([c for c in calls if c[0] == "buildx" and c[2] == "create"], [])
        self.assertEqual(registry, rc)
        result, _, calls = self.run_gpu_retag(rc)
        verify = [c for c in calls if c[0] == "cosign"]
        self.assertEqual(verify[0][1:3], ["verify", "docker.io/nowledgelabs/mem@sha256:rc"])
        self.assertIn(f"--certificate-identity-regexp={SIGNER_RE}", verify[0])

    def test_gpu_promotion_moves_floating_tag_only_when_allowed(self):
        rc = {"docker.io/nowledgelabs/mem:0.10.80-rc2-vulkan": "sha256:rc"}
        result, registry, _ = self.run_gpu_retag(rc, variant="vulkan", move_floating="false")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(registry["docker.io/nowledgelabs/mem:0.10.80-vulkan"], "sha256:rc")
        self.assertNotIn("docker.io/nowledgelabs/mem:0.10-vulkan", registry)

    def run_cpu_publish_tags(self, workflow, push_latest, version):
        section = job_section(workflow_text(workflow), "publish")
        script = section.split("      - id: tags\n        run: |\n", 1)[1]
        script = textwrap.dedent(script.split("\n\n", 1)[0]) + "\n"
        script = (script.replace("${{ needs.meta.outputs.version }}", version)
                        .replace("${{ needs.meta.outputs.push_latest }}", push_latest))
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "out"
            env = dict(os.environ, IMAGE="docker.io/nowledgelabs/x", GITHUB_OUTPUT=str(output))
            result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return dict(line.split("=", 1) for line in output.read_text().splitlines())

    def test_rc_build_never_moves_floating_cpu_tags(self):
        for workflow in ("release-docker.yml", "release-docker-updater.yml"):
            for version, push_latest, minor, latest in (
                    ("0.10.80-rc1", "false", "", ""),
                    ("0.10.79", "false", "", ""),
                    ("0.10.80", "true", "docker.io/nowledgelabs/x:0.10",
                     "docker.io/nowledgelabs/x:latest")):
                with self.subTest(workflow=workflow, version=version):
                    tags = self.run_cpu_publish_tags(workflow, push_latest, version)
                    self.assertEqual(tags["primary"], f"docker.io/nowledgelabs/x:{version}")
                    self.assertEqual(tags["minor"], minor)
                    self.assertEqual(tags["latest"], latest)
            manifest = job_section(workflow_text(workflow), "publish")
            self.assertIn('if [[ -n "${{ steps.tags.outputs.minor }}" ]]; then', manifest)

    def run_cpu_promote(self, registry, push_latest, unsigned=False):
        section = job_section(workflow_text("promote-rc-to-ga.yml"), "promote-docker")
        script = section.split("run: &promote_cpu_image |\n", 1)[1]
        lines = []
        for line in script.splitlines():
            if line.strip() and not line.startswith("          "):
                break
            lines.append(line)
        script = textwrap.dedent("\n".join(lines)) + "\n"
        self.assertIn("run: *promote_cpu_image", section)
        _, state, log, env = fake_tools(self, registry)
        env.update(REPO="docker.io/nowledgelabs/mem", RC="0.10.80-rc2", GA="0.10.80",
                   PUSH_LATEST=push_latest, SIGNER=SIGNER_RE)
        if unsigned:
            env["FAKE_COSIGN_FAIL"] = "1"
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        return result, json.loads(state.read_text()), calls

    def test_cpu_promotion_moves_floating_tags_and_requires_signature(self):
        rc = {"docker.io/nowledgelabs/mem:0.10.80-rc2": "sha256:rc"}
        result, registry, _ = self.run_cpu_promote(dict(rc), "true")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        for tag in ("0.10.80", "0.10", "latest"):
            self.assertEqual(registry[f"docker.io/nowledgelabs/mem:{tag}"], "sha256:rc", tag)
        result, registry, _ = self.run_cpu_promote(dict(rc), "false")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertEqual(registry["docker.io/nowledgelabs/mem:0.10.80"], "sha256:rc")
        self.assertNotIn("docker.io/nowledgelabs/mem:0.10", registry)
        self.assertNotIn("docker.io/nowledgelabs/mem:latest", registry)
        result, registry, calls = self.run_cpu_promote(dict(rc), "true", unsigned=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(registry, rc)
        self.assertEqual([c for c in calls if c[0] == "buildx" and c[2] == "create"], [])
        result, _, _ = self.run_cpu_promote({}, "true")
        self.assertNotEqual(result.returncode, 0)

    def test_docker_rust_builds_pass_compile_cache_only_as_buildkit_secrets(self):
        ids = ("sccache_bucket=nowledge-mem-sccache",
               "sccache_endpoint=https://${{ secrets.CLOUDFLARE_ACCOUNT_ID }}.r2.cloudflarestorage.com",
               "sccache_access_key_id=${{ secrets.SCCACHE_R2_ACCESS_KEY_ID }}",
               "sccache_secret_access_key=${{ secrets.SCCACHE_R2_SECRET_ACCESS_KEY }}")

        def build_step(section):
            return section.split("uses: docker/build-push-action@v6\n", 1)[1]

        def build_args(step):
            block = step.split("          build-args: |\n", 1)[1]
            return re.split(r"\n {10}(?! )", block, 1)[0]

        docker = workflow_text("release-docker.yml")
        amd64 = build_step(job_section(docker, "build-amd64"))
        self.assertIn("          secrets: &sccache_secrets |\n", amd64)
        for line in ids:
            self.assertIn(f"            {line}\n", amd64)
        sections = {job: build_step(job_section(docker, job))
                    for job in ("build-amd64", "build-arm64", "build-cuda", "build-vulkan")}
        rebuild = build_step(job_section(workflow_text("promote-gpu-variant.yml"), "build"))
        for line in ids:
            self.assertIn(f"            {line}\n", rebuild)
        sections["promote-gpu-variant/build"] = rebuild
        for job, step in sections.items():
            with self.subTest(job=job):
                if job not in ("build-amd64", "promote-gpu-variant/build"):
                    self.assertIn("          secrets: *sccache_secrets\n", step)
                # Build args are recorded in the mode=max provenance attestation;
                # credentials must never travel that way.
                args = build_args(step)
                self.assertNotIn("secrets.", args)
                self.assertNotIn("sccache", args.lower())
                self.assertIn("provenance: mode=max", step)
                self.assertIn("sbom: true", step)

    def test_gpu_images_are_signed_where_they_are_built(self):
        docker = workflow_text("release-docker.yml")
        for variant in ("cuda", "vulkan"):
            with self.subTest(variant=variant):
                section = job_section(docker, f"build-{variant}")
                self.assertIn('cosign sign --yes "${IMAGE}@${DIGEST}"', section)
                self.assertIn("DIGEST: ${{ steps.build.outputs.digest }}", section)
        build = job_section(workflow_text("promote-gpu-variant.yml"), "build")
        self.assertIn('cosign sign --yes "${IMAGE}@${DIGEST}"', build)
        promote = workflow_text("promote-rc-to-ga.yml")
        for job in ("promote-cuda", "promote-vulkan"):
            self.assertIn("      move_floating: ${{ inputs.push_latest }}\n", job_section(promote, job))



# ---------------------------------------------------------------------------
# Nightly release-shape build (nowledge-co/mem#7108, W5)
# ---------------------------------------------------------------------------

NIGHTLY_SHA = "0123456789abcdef0123456789abcdef01234567"
CPU_BUILD_JOBS = ("build-macos-arm64", "build-macos-x86_64", "build-windows",
                  "build-linux-deb-appimage")
CPU_ARTIFACTS = ("macos-arm64", "macos-x86_64", "windows-x86_64",
                 "linux-deb-appimage", "linux-rpm")
# Anything that writes outside the run: GH Releases, R2, APT, Worker routes,
# tags, cache purges. Issue comments are the nightly's one intended effect.
SIDE_EFFECT_MARKERS = ("gh release create", "gh release upload", "gh release edit",
                       "upload-draft-release-assets", "upload-package", "update-apt-repo",
                       "purge_cache", "git push", "git tag", "wrangler")

# Fake `gh` for the meta scripts. Logs every call; serves a commit lookup, a
# raw package.json, and `run list`.
FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps({"args": sys.argv[1:], "token": os.environ.get("GH_TOKEN", "")}) + "\n")
args = sys.argv[1:]
if os.environ.get("FAKE_GH_FAIL") == "1":
    sys.exit(1)
if args[:1] == ["api"] and "/commits/" in args[-3 if "--jq" in args else -1]:
    print(os.environ.get("FAKE_GH_SHA", ""))
elif args[:1] == ["api"] and "/contents/nowledge-graph/package.json" in args[-1]:
    print(json.dumps({"name": "nowledge-graph", "version": os.environ.get("FAKE_GH_PKG", "")}))
elif args[:2] == ["run", "list"]:
    print(os.environ.get("FAKE_GH_RUNS", "[]"))
elif args[:2] == ["issue", "list"]:
    print(os.environ.get("FAKE_GH_ISSUES", "[]"))
elif args[:1] == ["issue"] or args[:2] == ["workflow", "run"]:
    pass
else:
    sys.exit(f"unexpected gh call: {args}")
"""


def load_release_desktop():
    import yaml  # PyYAML ships with the hosted Ubuntu images.
    return yaml.safe_load(workflow_text("release-desktop.yml"))


def github_expr(expression, context):
    """Evaluate the subset of GitHub expression syntax used by `if:` and
    `concurrency.group` in release-desktop.yml. Unknown names raise so a new
    condition cannot pass these tests unexamined."""
    expr = expression.strip()
    if expr.startswith("${{") and expr.endswith("}}"):
        expr = expr[3:-2]
    out, i = [], 0
    token = re.compile(r"\s+|'[^']*'|&&|\|\||==|!=|!|\(|\)|,|[A-Za-z_][A-Za-z0-9_.-]*")
    while i < len(expr):
        m = token.match(expr, i)
        if not m:
            raise AssertionError(f"unsupported expression syntax: {expression!r}")
        t = m.group(0)
        i = m.end()
        if t.isspace() or t in ("(", ")", ",", "==", "!="):
            out.append(t)
        elif t.startswith("'"):
            out.append(repr(t[1:-1]))
        elif t == "&&":
            out.append(" and ")
        elif t == "||":
            out.append(" or ")
        elif t == "!":
            out.append(" not ")
        elif t in ("always", "success", "format"):
            out.append(f"_{t}")
        elif t in ("true", "false"):
            out.append(str(t == "true"))
        else:
            if t not in context:
                raise AssertionError(f"unmodelled context {t!r} in {expression!r}")
            out.append(repr(context[t]))
    return eval("".join(out), {"__builtins__": {}}, {
        "_always": lambda: True, "_success": lambda: True,
        "_format": lambda fmt, *args: fmt.format(*args),
    })


def meta_context(outputs, extra=None):
    context = {f"needs.meta.outputs.{k}": v for k, v in outputs.items()}
    context.update({f"steps.resolve.outputs.{k}": v for k, v in outputs.items()})
    context.update({
        "steps.rpm.outcome": "success",
        "steps.setup_sccache.outcome": "success",
        "steps.desktop_dependency.outputs.sha": NIGHTLY_SHA,
        "env.RUSTC_WRAPPER": "sccache",
        "env.CLOUDFLARE_ZONE_ID": "zone", "env.CLOUDFLARE_API_TOKEN": "token",
        "inputs.allow_newer_desktop_latest": False,
        "inputs.windows_runner_probe": False,
        "inputs.windows_cache_benchmark": False,
        "inputs.windows_only": False,
    })
    context.update(extra or {})
    return context


class ReleaseDesktopNightlyContractTest(unittest.TestCase):
    def setUp(self):
        self.workflow = load_release_desktop()
        self.jobs = self.workflow["jobs"]
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        gh = self.tmp / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.log = self.tmp / "gh.log"

    def step(self, job, step_id=None, name=None):
        for step in self.jobs[job]["steps"]:
            if (step_id and step.get("id") == step_id) or (name and step.get("name") == name):
                return step
        raise AssertionError(f"{job}: no step {step_id or name}")

    def run_script(self, script, env):
        self.log.write_text("")
        output, summary = self.tmp / "output", self.tmp / "summary"
        for f in (output, summary):
            f.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", script], capture_output=True, text=True, check=False,
            env={"PATH": str(self.tmp / "bin") + os.pathsep + os.environ["PATH"],
                 "FAKE_GH_LOG": str(self.log), "GITHUB_OUTPUT": str(output),
                 "GITHUB_STEP_SUMMARY": str(summary), **env},
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result, values, calls, summary.read_text()

    def resolve(self, event, **env):
        base = {"EVENT_NAME": event, "DISPATCH_TAG": "", "DISPATCH_PUBLISH": "",
                "DISPATCH_VULKAN": "", "DISPATCH_SOURCE_REF": "", "PAYLOAD_REF": "",
                "PAYLOAD_VERSION": "", "PUSH_REF": "main", "MEM_REPO_TOKEN": "mem-token",
                "FAKE_GH_SHA": NIGHTLY_SHA, "FAKE_GH_PKG": "0.10.102"}
        base.update(env)
        return self.run_script(self.step("meta", step_id="resolve")["run"], base)

    def test_release_paths_resolve_exactly_as_before(self):
        keys = ("version", "source_ref", "is_dry_run", "run_publish", "run_vulkan", "is_nightly")
        cases = (
            ("push", {"PUSH_REF": "v0.10.102-rc1"},
             ("0.10.102-rc1", "v0.10.102-rc1", "true", "false", "false", "false")),
            ("workflow_dispatch", {"DISPATCH_TAG": "0.10.102", "DISPATCH_PUBLISH": "true",
                                   "DISPATCH_VULKAN": "false"},
             ("0.10.102", "v0.10.102", "false", "true", "false", "false")),
            ("workflow_dispatch", {"DISPATCH_TAG": "0.10.102", "DISPATCH_PUBLISH": "false",
                                   "DISPATCH_VULKAN": "true"},
             ("0.10.102", "v0.10.102", "false", "false", "true", "false")),
            ("workflow_dispatch", {"DISPATCH_TAG": "0.10.102-ci1", "DISPATCH_PUBLISH": "true",
                                   "DISPATCH_VULKAN": "true"},
             ("0.10.102-ci1", "v0.10.102-ci1", "true", "false", "true", "false")),
            ("repository_dispatch", {"PAYLOAD_REF": "v0.10.102-rc2", "PAYLOAD_VERSION": "0.10.102-rc2"},
             ("0.10.102-rc2", "v0.10.102-rc2", "true", "false", "false", "false")),
            ("repository_dispatch", {"PAYLOAD_REF": "v0.10.102", "PAYLOAD_VERSION": "0.10.102"},
             ("0.10.102", "v0.10.102", "false", "true", "false", "false")),
        )
        for event, env, expected in cases:
            with self.subTest(event=event, env=env):
                result, values, calls, _ = self.resolve(event, **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(tuple(values.get(k) for k in keys), expected)
                self.assertEqual(calls, [], "A release must not resolve its source through the API")
        result, values, _, _ = self.resolve("workflow_dispatch", DISPATCH_TAG="0.10.102",
                                            DISPATCH_PUBLISH="true", DISPATCH_SOURCE_REF="main")
        self.assertNotEqual(result.returncode, 0, "source_ref must never redirect a release build")
        self.assertIn("only accepted with tag=nightly", result.stdout + result.stderr)
        self.assertEqual(values, {})

    def test_nightly_pins_one_commit_and_never_publishes(self):
        for event, env, ref in (
            ("workflow_dispatch", {"DISPATCH_TAG": "nightly"}, "main"),
            ("workflow_dispatch", {"DISPATCH_TAG": "nightly", "DISPATCH_SOURCE_REF": "feat/x",
                                   "DISPATCH_PUBLISH": "true", "DISPATCH_VULKAN": "true"}, "feat/x"),
        ):
            with self.subTest(event=event, env=env):
                result, values, calls, summary = self.resolve(event, **env)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertRegex(values["version"], r"^0\.10\.102-nightly\.\d{8}$")
                self.assertEqual(values["version"].split("-", 1)[0], "0.10.102",
                                 "The package.json version check compares the part before '-'")
                self.assertEqual(values["source_ref"], NIGHTLY_SHA)
                self.assertEqual((values["is_dry_run"], values["run_publish"], values["run_vulkan"],
                                  values["is_nightly"]), ("false", "false", "false", "true"))
                self.assertTrue(all(c["token"] == "mem-token" for c in calls), calls)
                self.assertEqual(calls[0]["args"][:2], ["api", f"repos/nowledge-co/mem/commits/{ref}"])
                self.assertIn(f"package.json?ref={NIGHTLY_SHA}", calls[1]["args"][-1])
                self.assertIn(NIGHTLY_SHA, summary)
        for env in ({"FAKE_GH_SHA": "not-a-sha"}, {"FAKE_GH_PKG": "0.10"}, {"FAKE_GH_FAIL": "1"}):
            with self.subTest(failure=env):
                result, values, _, _ = self.resolve("workflow_dispatch", DISPATCH_TAG="nightly", **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(values, {})

    def side_effect_reachable(self, outputs, extra=None):
        context = meta_context(outputs, extra)
        reachable = []
        for name, job in self.jobs.items():
            job_if = job.get("if")
            if job_if is not None and not github_expr(str(job_if), context):
                continue
            if job.get("environment") or "upload-prerelease-desktop-artifact" in job.get("uses", ""):
                reachable.append(name)
                continue
            for step in job.get("steps", []):
                text = step.get("run", "")
                if not any(marker in text for marker in SIDE_EFFECT_MARKERS):
                    continue
                if step.get("if") is None or github_expr(str(step["if"]), context):
                    reachable.append(f"{name}: {step.get('name')}")
        return reachable

    def test_nightly_outputs_reach_no_publishing_job_or_step(self):
        nightly = {"version": "0.10.102-nightly.20261014", "is_dry_run": "false",
                   "run_publish": "false", "run_vulkan": "false", "is_nightly": "true"}
        self.assertEqual(self.side_effect_reachable(
            nightly, {"github.event_name": "workflow_dispatch", "inputs.tag": "nightly"}), [])
        # The evaluator is not vacuous: the release shapes do reach their writers.
        push = {"github.event_name": "push", "inputs.tag": None}
        rc = dict(nightly, version="0.10.102-rc1", is_dry_run="true", is_nightly="false")
        self.assertIn("prerelease-prepare: Create draft pre-release GH Release",
                      self.side_effect_reachable(rc, push))
        self.assertIn("prerelease-upload-macos-arm64", self.side_effect_reachable(rc, push))
        ga = dict(nightly, version="0.10.102", run_publish="true", is_nightly="false")
        self.assertIn("publish", self.side_effect_reachable(ga, push))
        vulkan_rc = dict(rc, run_vulkan="true")
        self.assertIn("build-linux-vulkan: Attach -vulkan bundles to GH Release (manual download only)",
                      self.side_effect_reachable(vulkan_rc, push))

    def test_nightly_uploads_no_binaries_and_builds_every_default_platform(self):
        for is_nightly, expect_upload in (("true", False), ("false", True)):
            context = meta_context({"is_nightly": is_nightly, "is_dry_run": "false",
                                    "run_publish": "false", "run_vulkan": "false"})
            for job in CPU_BUILD_JOBS:
                with self.subTest(is_nightly=is_nightly, job=job):
                    self.assertTrue(github_expr(self.jobs[job].get("if", "true"), context),
                                    "Default platform builds run without windows_only")
                    expected_needs = ["meta", "select-windows-runner"] if job == "build-windows" else "meta"
                    self.assertEqual(self.jobs[job]["needs"], expected_needs)
                    uploads = [s for s in self.jobs[job]["steps"]
                               if str(s.get("uses", "")).startswith("actions/upload-artifact")
                               and s["with"]["name"] in CPU_ARTIFACTS]
                    self.assertTrue(uploads)
                    for upload in uploads:
                        self.assertEqual(github_expr(upload.get("if", "true"), context), expect_upload,
                                         upload["with"]["name"])
        self.assertEqual(set(self.jobs["nightly-report"]["needs"]), {"meta", *CPU_BUILD_JOBS})

    def test_windows_only_build_is_nightly_and_has_no_publishers(self):
        result, outputs, _, _ = self.resolve("workflow_dispatch", DISPATCH_TAG="nightly",
                                             WINDOWS_ONLY="true", DISPATCH_SOURCE_REF=NIGHTLY_SHA)
        self.assertEqual(result.returncode, 0, result.stderr)
        extra = {"github.event_name": "workflow_dispatch", "inputs.tag": "nightly",
                 "inputs.windows_only": True}
        self.assertEqual(self.side_effect_reachable(outputs, extra), [])
        context = meta_context(outputs, extra)
        for job in CPU_BUILD_JOBS:
            self.assertEqual(github_expr(self.jobs[job].get("if", "true"), context),
                             job == "build-windows", job)
        self.assertFalse(github_expr(self.jobs["nightly-report"]["if"], context))
        self.assertEqual(outputs["source_ref"], NIGHTLY_SHA)
        for event, tag in (("workflow_dispatch", "0.10.102"), ("push", "nightly"),
                           ("repository_dispatch", "nightly")):
            result, outputs, _, _ = self.resolve(event, DISPATCH_TAG=tag, WINDOWS_ONLY="true")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(outputs, {})

    def test_nightly_never_shares_a_concurrency_group_with_a_release(self):
        group = self.workflow["concurrency"]["group"]
        self.assertFalse(self.workflow["concurrency"]["cancel-in-progress"])
        for event, tag, ref, expected in (
            ("push", None, "refs/tags/v0.10.102-rc1", "release-desktop-refs/tags/v0.10.102-rc1"),
            ("workflow_dispatch", "0.10.102", "refs/heads/main", "release-desktop-refs/heads/main"),
            ("repository_dispatch", None, "refs/heads/main", "release-desktop-refs/heads/main"),
            ("workflow_dispatch", "nightly", "refs/heads/main", "release-desktop-nightly"),
        ):
            with self.subTest(event=event, tag=tag):
                context = {"github.event_name": event, "inputs.tag": tag, "github.ref": ref,
                           "inputs.windows_only": False, "github.run_id": 100}
                self.assertEqual(github_expr(group, context), expected)
        for run_id in (100, 101):
            self.assertEqual(github_expr(group, {"inputs.windows_only": True, "github.run_id": run_id,
                                                "inputs.tag": "nightly", "github.ref": "refs/heads/main"}),
                             f"windows-qualification-{run_id}")
        report_if = self.jobs["nightly-report"]["if"]
        for event, tag, expected in (("workflow_dispatch", "nightly", True),
                                     ("workflow_dispatch", "0.10.102", False), ("push", None, False),
                                     ("repository_dispatch", None, False)):
            self.assertEqual(github_expr(report_if, {"github.event_name": event, "inputs.tag": tag, "inputs.windows_only": False}),
                             expected, (event, tag))
        self.assertEqual(self.jobs["nightly-report"]["permissions"], {"issues": "write"})

    def test_nightly_report_tracks_one_issue(self):
        script = self.jobs["nightly-report"]["steps"][0]["run"]
        title = "Nightly release-shape build is failing"
        green = {f"RESULT_{k}": "success" for k in ("META", "MACOS_ARM64", "MACOS_X86_64",
                                                     "WINDOWS", "LINUX")}
        base = dict(green, REPO="wey-gu/mem-releases", RUN_URL="https://example.invalid/run/1",
                    VERSION="0.10.102-nightly.20261014", SOURCE_REF=NIGHTLY_SHA)
        open_issue = json.dumps([{"number": 7, "title": title}])
        other_issue = json.dumps([{"number": 8, "title": title + " (old)"}])
        for env, expected in (
            ({"RESULT_WINDOWS": "failure"}, [["issue", "create"]]),
            ({"RESULT_META": "failure", "FAKE_GH_ISSUES": other_issue}, [["issue", "create"]]),
            ({"RESULT_LINUX": "cancelled", "FAKE_GH_ISSUES": open_issue}, [["issue", "comment", "7"]]),
            ({"FAKE_GH_ISSUES": open_issue}, [["issue", "comment", "7"], ["issue", "close", "7"]]),
            ({}, []),
        ):
            with self.subTest(env=env):
                result, _, calls, summary = self.run_script(script, dict(base, **env))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Nightly release-shape result", summary)
                writes = [c["args"][:3] if c["args"][1] != "create" else c["args"][:2]
                          for c in calls if c["args"][:2] != ["issue", "list"]]
                self.assertEqual(writes, expected)
                for call in calls:
                    if call["args"][:2] == ["issue", "create"]:
                        self.assertEqual(call["args"][call["args"].index("--title") + 1], title)

    def test_release_jobs_do_not_save_target_or_pnpm_caches_that_tag_runs_never_restore(self):
        for name, job in self.jobs.items():
            for step in job.get("steps", []):
                with self.subTest(job=name, step=step.get("name") or step.get("uses")):
                    self.assertFalse(str(step.get("uses", "")).startswith("Swatinem/rust-cache"))
                    self.assertNotIn("cache", (step.get("with") or {}) if str(step.get("uses", "")).startswith(
                        "actions/setup-node") else {})



class ReleaseShapeTriggerContractTest(unittest.TestCase):
    """release-shape-nightly.yml decides when to dispatch the nightly."""

    def setUp(self):
        import yaml
        self.workflow = yaml.safe_load(workflow_text("release-shape-nightly.yml"))
        self.steps = {s.get("id") or s["name"]: s for s in self.workflow["jobs"]["decide"]["steps"]}
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(subprocess.run, ["rm", "-rf", str(self.tmp)])
        gh = self.tmp / "bin" / "gh"
        gh.parent.mkdir()
        gh.write_text(FAKE_GH)
        gh.chmod(0o755)
        self.log = self.tmp / "gh.log"

    def run_step(self, step, env, cwd=None):
        self.log.write_text("")
        output, summary = self.tmp / "output", self.tmp / "summary"
        output.write_text("")
        summary.write_text("")
        result = subprocess.run(
            ["bash", "-e", "-c", step["run"]], cwd=cwd or self.tmp, capture_output=True, text=True,
            check=False,
            env={"PATH": str(self.tmp / "bin") + os.pathsep + os.environ["PATH"],
                 "FAKE_GH_LOG": str(self.log), "GITHUB_OUTPUT": str(output),
                 "GITHUB_STEP_SUMMARY": str(summary), "GITHUB_REPOSITORY": "wey-gu/mem-releases",
                 "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, **env},
        )
        values = dict(line.split("=", 1) for line in output.read_text().splitlines() if "=" in line)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        return result, values, calls

    def test_polls_hourly_and_only_reads_the_source(self):
        self.assertEqual(self.workflow[True]["schedule"], [{"cron": "17 * * * *"}])
        self.assertEqual(self.workflow["permissions"], {"contents": "read", "actions": "write"})
        checkout = self.steps["Fetch nowledge-co/mem main (trees only)"]["with"]
        self.assertEqual((checkout["repository"], checkout["ref"], checkout["filter"],
                          checkout["persist-credentials"]), ("nowledge-co/mem", "main", "blob:none", False))
        self.assertIn("secrets.MEM_REPO_TOKEN", checkout["token"])

    def test_fingerprint_tracks_cargo_manifests_lockfiles_and_compiled_gitlinks(self):
        repo = self.tmp / "mem"
        repo.mkdir()

        def git(*args):
            return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True,
                                  env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1",
                                       "GIT_CONFIG_GLOBAL": os.devnull})

        def write(path, text):
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
            git("add", path)

        def commit(message):
            git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit",
                "--no-gpg-sign", "-qm", message)

        def gitlink(path, sha):
            git("update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")

        git("init", "-q")
        for path in ("nmem-rs/Cargo.toml", "nmem-rs/Cargo.lock", "nmem-rs/bazel-Cargo.lock",
                     "integrations/hawdb/kuzu-Cargo.lock", "integrations/hawdb/nightly-server.Cargo.lock",
                     "nowledge-graph/package.json", "nmem-rs/src/lib.rs"):
            write(path, "v1\n")
        gitlink("hawdb", "1" * 40)
        gitlink("upstream_forks/ladybug", "2" * 40)
        gitlink("community", "3" * 40)
        commit("base")
        step = self.steps["fingerprint"]

        def fingerprint():
            result, values, _ = self.run_step(step, {})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(values["sha"], git("rev-parse", "HEAD").stdout.strip())
            self.assertIn(values["after_daily_slot"], ("true", "false"))
            self.assertRegex(values["day"], r"^\d{8}$")
            return values["deps"]

        base = fingerprint()
        for change, expect_changed in (
            (lambda: write("nmem-rs/src/lib.rs", "v2\n"), False),
            (lambda: write("nowledge-graph/package.json", "v2\n"), False),
            (lambda: gitlink("community", "4" * 40), False),
            (lambda: write("nmem-rs/Cargo.lock", "v2\n"), True),
            (lambda: write("integrations/hawdb/kuzu-Cargo.lock", "v2\n"), True),
            (lambda: write("nmem-rs/Cargo.toml", "v2\n"), True),
            (lambda: write("nowledge-graph/src-tauri/Cargo.toml", "new\n"), True),
            (lambda: gitlink("upstream_forks/ladybug", "5" * 40), True),
            (lambda: gitlink("hawdb", "6" * 40), True),
        ):
            change()
            commit("change")
            current = fingerprint()
            with self.subTest(fingerprint=current, expect_changed=expect_changed):
                self.assertEqual(current != base, expect_changed)
            base = current

    def test_decide_dispatches_on_new_dependencies_daily_or_force_but_never_beside_a_release(self):
        step = self.steps["decide"]
        idle = json.dumps([{"databaseId": 1, "status": "completed"}])
        busy = json.dumps([{"databaseId": 1, "status": "completed"}, {"databaseId": 2, "status": "queued"}])
        for force, deps_seen, day_seen, after, runs, expected in (
            ("", "true", "true", "true", idle, None),
            ("", "true", "", "false", idle, None),
            ("", "", "true", "false", idle, "dependencies"),
            ("", "", "", "true", idle, "dependencies"),
            ("", "true", "", "true", idle, "daily"),
            ("true", "true", "true", "false", idle, "manual"),
            ("", "", "", "true", busy, None),
            ("true", "true", "true", "true", busy, None),
        ):
            with self.subTest(force=force, deps_seen=deps_seen, day_seen=day_seen, after=after, runs=runs):
                result, values, calls = self.run_step(step, {
                    "FORCE": force, "DEPS_SEEN": deps_seen, "DAY_SEEN": day_seen,
                    "AFTER_DAILY_SLOT": after, "FAKE_GH_RUNS": runs, "GH_TOKEN": "t"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["build"], "true" if expected else "false")
                self.assertEqual(values.get("reason"), expected)
        result, values, _ = self.run_step(step, {"DEPS_SEEN": "", "FAKE_GH_FAIL": "1", "GH_TOKEN": "t"})
        self.assertNotEqual(result.returncode, 0, "An unknown release state must not dispatch")
        self.assertNotIn("build", values)

    def test_markers_and_dispatch_follow_the_decision(self):
        steps = self.workflow["jobs"]["decide"]["steps"]
        names = [s.get("name") for s in steps]
        dispatch = self.steps["Dispatch the nightly release-shape build"]
        # Markers are saved only for a dispatched nightly, before the dispatch.
        for name in ("Mark this dependency fingerprint as built", "Mark today's daily nightly as dispatched",
                     "Prepare marker"):
            self.assertIn("steps.decide.outputs.build == 'true'", self.steps[name]["if"])
            self.assertLess(names.index(name), names.index(dispatch["name"]))
        self.assertEqual(dispatch["if"], "steps.decide.outputs.build == 'true'")
        result, _, calls = self.run_step(dispatch, {"SOURCE_SHA": NIGHTLY_SHA, "REASON": "daily",
                                                    "DEPS": "abc", "GH_TOKEN": "t"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c["args"] for c in calls], [[
            "workflow", "run", "release-desktop.yml", "-R", "wey-gu/mem-releases", "--ref", "main",
            "-f", "tag=nightly", "-f", f"source_ref={NIGHTLY_SHA}"]])


if __name__ == "__main__":
    unittest.main()
