"""Contract fixtures exercise the real verifier subprocess; not artifact proof."""

import copy
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

import verify_packaged_docker as verify


SOURCE = "a" * 40
TOOLS = "b" * 40
INDEX = "sha256:" + "c" * 64
CHILD = "sha256:" + "d" * 64
IDENTITY = {"repository": verify.REPOSITORY, "build_run_id": 123, "build_attempt": 1,
            "build_workflow_sha": TOOLS, "release_tag": "v0.10.104-rc2", "source_sha": SOURCE,
            "variant": "cpu", "digest": INDEX}

FIXTURE = r'''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
arch = os.environ['FIXTURE_ARCH']
index = 'sha256:' + 'c' * 64
child = 'sha256:' + 'd' * 64
gpu = 'GPU-12345678-1234-1234-1234-123456789abc'
gpu_output = gpu + ', 560.35.03'
if pathlib.Path(sys.argv[0]).name == 'gh':
    assert args[0] == 'api'
    path = args[1]
    if path.endswith('/logs'):
        if '--allow-escape-sequences' not in args:
            sys.exit('the response contains terminal escape sequences')
        print('\x1b[0mPublished manifest digest: ' + os.environ.get('FIXTURE_PUBLISHED_DIGEST', index))
    elif path.endswith('/actions/runs/123'):
        print(json.dumps({'id': 123, 'run_attempt': 1, 'repository': {'full_name': 'wey-gu/mem-releases'},
              'path': '.github/workflows/release-docker.yml', 'event': os.environ.get('FIXTURE_EVENT', 'push'),
              'head_branch': 'v0.10.104-rc2', 'head_sha': 'b' * 40,
              'status': 'in_progress', 'conclusion': None}))
    elif '/commits/' in path:
        print(json.dumps({'sha': ('a' if '/nowledge-co/mem/' in path else 'b') * 40}))
    elif '/jobs?' in path:
        names = ['meta', 'build-amd64', 'build-arm64', 'publish',
                 'verify (ubuntu-latest, amd64)', 'verify (ubuntu-24.04-arm, arm64)']
        jobs = [{'id': i + 1, 'name': name, 'status': 'completed', 'conclusion': 'success'}
                for i, name in enumerate(names)]
        print(json.dumps({'total_count': len(jobs), 'jobs': jobs}))
    else:
        raise AssertionError(path)
elif pathlib.Path(sys.argv[0]).name == 'cosign':
    print(json.dumps([{'critical': {'image': {'docker-manifest-digest': index}}}]))
elif pathlib.Path(sys.argv[0]).name == 'nvidia-smi':
    print(os.environ.get('FIXTURE_HOST_GPU', gpu_output))
elif args[0] == 'info':
    if args[-1] == '{{json .Runtimes}}':
        print(json.dumps({} if os.environ.get('FIXTURE_NO_TOOLKIT') else {'nvidia': {}}))
    else:
        print(arch)
elif args[:3] == ['buildx', 'imagetools', 'inspect']:
    if '--raw' in args:
        print(json.dumps({'manifests': [{'digest': child, 'platform': {'os': 'linux', 'architecture': item}}
                                      for item in ('amd64', 'arm64')]}))
    else:
        print(json.dumps({'digest': os.environ.get('FIXTURE_DIGEST', index)}))
elif args[0] == 'pull':
    print('pulled')
elif args[:2] == ['image', 'inspect']:
    print(json.dumps([{'Id': 'sha256:' + 'e' * 64, 'Os': 'linux',
          'Architecture': os.environ.get('FIXTURE_IMAGE_ARCH', arch),
          'Config': {'User': os.environ.get('FIXTURE_USER', 'nowledge'), 'Labels': {
              'org.opencontainers.image.version': '0.10.104-rc2',
              'org.opencontainers.image.source': 'https://github.com/wey-gu/mem-releases'}}}]))
elif args[0] == 'create':
    assert '--user' not in args and '--volume' not in args and '-v' not in args
    assert args[args.index('--network') + 1] == 'none'
    cuda = os.environ.get('FIXTURE_VARIANT') == 'cuda'
    assert ('--gpus' in args) == cuda
    if cuda:
        assert args[args.index('--gpus') + 1] == 'all'
    (root / args[args.index('--name') + 1]).write_text(args[-1])
    if os.environ.get('FIXTURE_CREATE_FAIL'):
        sys.exit(19)
    print('container')
elif args[0] == 'start':
    shell = (root / args[-1]).read_text().removeprefix('exec ')
    outputs = {'id -u': '10001', 'id -g': '10001',
               'nvidia-smi --query-gpu=uuid,driver_version --format=csv,noheader,nounits':
                   os.environ.get('FIXTURE_CONTAINER_GPU', gpu_output),
               'nmem-server --version': 'nmem-server 0.10.104\nnmem-build-sha:' + 'a' * 40,
               'nmem --version': os.environ.get('FIXTURE_CLI', 'nmem 0.10.104'),
               'nmem tui --version': 'nmem-tui 0.10.104',
               'nmem-tui --version': 'nmem-tui 0.10.104',
               'nmem-legacy-graph-reader --version': json.dumps({
                   'engine': 'kuzu-0.11.0', 'protocol': 1, 'mem_commit': 'a' * 40})}
    if shell == 'nmem --version' and os.environ.get('FIXTURE_LAUNCH_FAIL'):
        print('fixture startup output')
        print('fixture runtime dependency missing', file=sys.stderr)
        sys.exit(int(os.environ.get('FIXTURE_LAUNCH_EXIT_CODE', '17')))
    print(outputs[shell])
elif args[0] == 'rm':
    assert '--force' in args and '--volumes' in args
    if os.environ.get('FIXTURE_CLEANUP_FAIL'):
        sys.exit(18)
    (root / args[-1]).unlink()
    print('removed')
elif args[0] == 'inspect':
    print(json.dumps({'Status': 'exited', 'ExitCode': int(os.environ.get('FIXTURE_EXIT_CODE', '0')),
                      'OOMKilled': False}))
else:
    raise AssertionError(args)
'''


class NativeBoundary(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for binary in ("docker", "cosign", "gh", "nvidia-smi"):
            path = self.root / binary
            path.write_text(FIXTURE)
            path.chmod(0o755)
        self.identity = self.root / "identity.json"
        self.identity.write_text(json.dumps(IDENTITY))
        self.receipt = self.root / "receipt.json"
        self.arch = {"x86_64": "amd64", "aarch64": "arm64"}[platform.machine()]

    def run_probe(self, *, script=None, **changes):
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                   FIXTURE_ROOT=str(self.root), FIXTURE_ARCH=self.arch,
                   GITHUB_ACTIONS="true", GITHUB_SHA="f" * 40, GITHUB_RUN_ID="789")
        env.update(changes)
        return subprocess.run([sys.executable, str(script or Path(verify.__file__)), "probe",
                               "--identity", str(self.identity), "--arch", self.arch,
                               "--output", str(self.receipt)], env=env, capture_output=True, text=True, timeout=30)

    def test_valid_final_environment_records_all_commands(self):
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(self.receipt.read_text())
        verify.validate_receipt(receipt)
        self.assertEqual(set(receipt["outputs"]), set(verify.SURFACES))

    def assert_cuda_runtime(self, script=None, **changes):
        identity = dict(IDENTITY, variant="cuda")
        self.identity.write_text(json.dumps(identity))
        result = self.run_probe(script=script, FIXTURE_VARIANT="cuda", **changes)
        self.assertEqual(result.returncode, 0, "CUDA probe failed: " + result.stderr)
        receipt = json.loads(self.receipt.read_text())
        verify.validate_receipt(receipt)
        runtime = receipt["cuda_runtime"]
        self.assertEqual(runtime["device_request"], "all")
        self.assertEqual(runtime["host_gpus"], runtime["container_gpus"])
        self.assertEqual(len(runtime["host_gpus"]), 1)
        self.assertEqual(list(self.root.glob("nmem-version-*")), [])
        return receipt

    def test_cuda_uses_nvidia_runtime_and_binds_driver_receipts(self):
        receipt = self.assert_cuda_runtime()
        directory = self.root / "receipts"
        directory.mkdir()
        path = directory / "amd64.json"
        path.write_text(json.dumps(receipt))
        self.assertTrue(verify.aggregate(directory, receipt["identity"])["accepted"])
        for runtime in (None, dict(receipt["cuda_runtime"], device_request=""),
                        dict(receipt["cuda_runtime"], container_gpus={})):
            with self.subTest(runtime=runtime), self.assertRaises(RuntimeError):
                path.write_text(json.dumps(dict(receipt, cuda_runtime=runtime)))
                verify.aggregate(directory, receipt["identity"])

    def test_cuda_missing_or_mismatched_runtime_fails_without_receipt(self):
        self.identity.write_text(json.dumps(dict(IDENTITY, variant="cuda")))
        for changes in ({"FIXTURE_HOST_GPU": ""}, {"FIXTURE_HOST_GPU": "not a GPU"},
                        {"FIXTURE_NO_TOOLKIT": "1"}, {"FIXTURE_CONTAINER_GPU": ""},
                        {"FIXTURE_CONTAINER_GPU": "GPU-12345678-1234-1234-1234-123456789abc, 555.42.02"}):
            with self.subTest(changes=changes):
                result = self.run_probe(FIXTURE_VARIANT="cuda", **changes)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(self.receipt.exists())
                self.assertEqual(list(self.root.glob("nmem-version-*")), [])

    def test_cuda_device_request_removal_is_caught_by_unchanged_assertion(self):
        source = Path(verify.__file__).read_text()
        target = 'device_options = ["--gpus", "all"] if identity["variant"] == "cuda" else []'
        self.assertEqual(source.count(target), 1)
        mutant = self.root / "mutant-cuda.py"
        mutant.write_text(source.replace(target, "device_options = []"))
        old = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(Path(verify.__file__).parent)
        try:
            with self.assertRaisesRegex(AssertionError, "CUDA probe failed"):
                self.assert_cuda_runtime(mutant)
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
        print("Mutation RED: unchanged CUDA assertion caught missing NVIDIA device request")

    def test_vulkan_keeps_the_original_non_cuda_runtime(self):
        self.identity.write_text(json.dumps(dict(IDENTITY, variant="vulkan")))
        result = self.run_probe(FIXTURE_VARIANT="vulkan")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("cuda_runtime", json.loads(self.receipt.read_text()))

    def test_real_gh_command_captures_escaped_logs_and_binds_digest(self):
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                   FIXTURE_ROOT=str(self.root), FIXTURE_ARCH=self.arch, MEM_READ_TOKEN="fixture-token")
        argv = [sys.executable, verify.__file__, "identity", "--build-run-id", "123",
                "--release-tag", IDENTITY["release_tag"], "--source-sha", SOURCE,
                "--variant", "cpu", "--digest", INDEX, "--output", str(self.receipt)]
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        result_identity = json.loads(self.receipt.read_text())
        self.assertEqual(result_identity["digest"], INDEX)
        self.receipt.unlink()
        env["FIXTURE_PUBLISHED_DIGEST"] = "sha256:" + "0" * 64
        result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.receipt.exists())

    def assert_cli_mismatch_rejected(self, script=None):
        result = self.run_probe(script=script, FIXTURE_CLI="nmem 0.10.103")
        self.assertNotEqual(result.returncode, 0, "Verifier accepted the previous installed CLI")
        self.assertFalse(self.receipt.exists(), "Rejected candidate wrote a success receipt")
        self.assertIn("version '0.10.103'", result.stderr)

    def test_previous_cli_rejected(self):
        self.assert_cli_mismatch_rejected()

    def assert_launch_diagnostics(self, script=None):
        result = self.run_probe(script=script, FIXTURE_LAUNCH_FAIL="1", FIXTURE_LAUNCH_EXIT_CODE="127")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(list(self.root.glob("nmem-version-*")), [])
        self.assertIn("Packaged command 'nmem --version' exited 127", result.stderr,
                      "Missing packaged command identity and exit code")
        self.assertIn("fixture startup output", result.stderr)
        self.assertIn("fixture runtime dependency missing", result.stderr)

    def test_failed_launch_reports_captured_output(self):
        self.assert_launch_diagnostics()

    def test_diagnostic_removal_is_caught_by_unchanged_assertion(self):
        source = Path(verify.__file__).read_text()
        target = '''f"Packaged command {shell!r} exited {error.returncode}; "
                    f"stdout={str(error.stdout or '').strip()[:4096]!r}; "
                    f"stderr={str(error.stderr or '').strip()[:4096]!r}"'''
        self.assertEqual(source.count(target), 1)
        mutant = self.root / "mutant-diagnostics.py"
        mutant.write_text(source.replace(target, '"Packaged command failed"'))
        old = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = str(Path(verify.__file__).parent)
        try:
            with self.assertRaisesRegex(AssertionError, "Missing packaged command identity and exit code"):
                self.assert_launch_diagnostics(mutant)
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
        print("Mutation RED: unchanged launch assertion caught removed command diagnostics")

    def test_uncertain_create_failure_still_removes_container_and_volumes(self):
        result = self.run_probe(FIXTURE_CREATE_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.receipt.exists())
        self.assertEqual(list(self.root.glob("nmem-version-*")), [])

    def test_bad_runtime_boundaries_fail_without_receipt(self):
        for changes in ({"FIXTURE_CLI": ""}, {"FIXTURE_CLI": "nmem 0.0.0"},
                        {"FIXTURE_CLI": "nmem PLACEHOLDER"}, {"FIXTURE_LAUNCH_FAIL": "1"},
                        {"FIXTURE_USER": "root"}, {"FIXTURE_DIGEST": "sha256:" + "0" * 64},
                        {"FIXTURE_IMAGE_ARCH": "wrong"}, {"FIXTURE_CLEANUP_FAIL": "1"},
                        {"FIXTURE_EXIT_CODE": "17"}):
            with self.subTest(changes=changes):
                result = self.run_probe(**changes)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(self.receipt.exists())

    def test_cli_validation_removal_is_caught_by_unchanged_assertion(self):
        source = Path(verify.__file__).read_text()
        target = '    exact_version(outputs["cli"], "nmem", version)\n'
        self.assertEqual(source.count(target), 1)
        mutant = self.root / "mutant.py"
        mutant.write_text(source.replace(target, ""))
        env_path = str(Path(verify.__file__).parent)
        old = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = env_path
        try:
            with self.assertRaisesRegex(AssertionError, "Verifier accepted the previous installed CLI"):
                self.assert_cli_mismatch_rejected(mutant)
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
        self.assertTrue(self.receipt.exists(), "Mutation did not reach the bad acceptance state")
        print("Mutation RED: unchanged rejection assertion caught CLI 0.10.103 accepted by validation bypass")


class IdentityAndAggregation(unittest.TestCase):
    def test_real_matrix_command_requires_explicit_cuda_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "matrix.json"
            for variant, runner, expected in (
                    ("cpu", "", ["ubuntu-22.04", "ubuntu-24.04-arm"]),
                    ("vulkan", "", ["ubuntu-22.04"]), ("cuda", "approved-nvidia", ["approved-nvidia"])):
                argv = [sys.executable, verify.__file__, "matrix", "--variant", variant,
                        "--cuda-runner", runner, "--output", str(output)]
                result = subprocess.run(argv, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual([row["runner"] for row in json.loads(output.read_text())["include"]], expected)
                output.unlink()
            for variant, runner in (("cuda", ""), ("cuda", "label\ninjection"), ("cpu", "approved-nvidia")):
                result = subprocess.run([sys.executable, verify.__file__, "matrix", "--variant", variant,
                                         "--cuda-runner", runner, "--output", str(output)],
                                        capture_output=True, text=True, timeout=10)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(output.exists())

    def test_producer_must_be_original_exact_tag_push(self):
        run = {"repository": {"full_name": verify.REPOSITORY}, "path": ".github/workflows/release-docker.yml",
               "event": "push", "head_branch": IDENTITY["release_tag"], "head_sha": TOOLS, "status": "in_progress"}
        verify.validate_producer(run, IDENTITY["release_tag"], TOOLS)
        for key, value in (("event", "workflow_dispatch"), ("head_sha", "0" * 40),
                           ("head_branch", "v0.10.103-rc1"), ("path", "other.yml")):
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                verify.validate_producer(dict(run, **{key: value}), IDENTITY["release_tag"], TOOLS)

    def test_direct_ga_dispatch_keeps_exact_tag_and_tools_commit_binding(self):
        run = {"repository": {"full_name": verify.REPOSITORY}, "path": ".github/workflows/release-docker.yml",
               "event": "workflow_dispatch", "head_branch": "v0.10.108", "head_sha": TOOLS, "status": "in_progress"}
        verify.validate_producer(run, "v0.10.108", TOOLS)
        verify.validate_identity(dict(IDENTITY, release_tag="v0.10.108"))
        for key, value in (("head_branch", "main"), ("head_branch", "v0.10.107"),
                           ("head_sha", "0" * 40), ("path", "other.yml"),
                           ("event", "repository_dispatch")):
            with self.subTest(key=key, value=value), self.assertRaises(RuntimeError):
                verify.validate_producer(dict(run, **{key: value}), "v0.10.108", TOOLS)
        for tag in ("v0.10.108-ci1", "v0.10.108-nightly", "v0.10.108-rc0", "main"):
            with self.subTest(tag=tag), self.assertRaises(RuntimeError):
                verify.validate_identity(dict(IDENTITY, release_tag=tag))

    def test_aggregate_rejects_missing_duplicate_and_foreign_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outputs = {"server": "nmem-server 0.10.104\nnmem-build-sha:" + SOURCE,
                       "cli": "nmem 0.10.104", "cli_tui": "nmem-tui 0.10.104", "tui": "nmem-tui 0.10.104",
                       "legacy_reader": json.dumps({"engine": "kuzu-0.11.0", "protocol": 1, "mem_commit": SOURCE})}
            receipt = {"identity": IDENTITY, "arch": "amd64", "uid": "10001", "gid": "10001",
                       "child_digest": CHILD, "config_id": CHILD, "outputs": outputs,
                       "acceptance_workflow_sha": TOOLS, "acceptance_run_id": "789"}
            with self.assertRaises(RuntimeError):
                verify.aggregate(root, IDENTITY)
            (root / "amd64.json").write_text(json.dumps(receipt))
            with self.assertRaises(RuntimeError):
                verify.aggregate(root, IDENTITY)
            (root / "arm64.json").write_text(json.dumps(receipt))
            with self.assertRaises(RuntimeError):
                verify.aggregate(root, IDENTITY)
            arm = copy.deepcopy(receipt)
            arm["arch"] = "arm64"
            (root / "arm64.json").write_text(json.dumps(arm))
            self.assertTrue(verify.aggregate(root, IDENTITY)["accepted"])
            arm["identity"]["source_sha"] = "0" * 40
            (root / "arm64.json").write_text(json.dumps(arm))
            with self.assertRaises(RuntimeError):
                verify.aggregate(root, IDENTITY)
            arm["identity"] = copy.deepcopy(IDENTITY)
            arm["outputs"]["cli"] = "nmem 0.10.103"
            (root / "arm64.json").write_text(json.dumps(arm))
            with self.assertRaises(RuntimeError):
                verify.aggregate(root, IDENTITY)


if __name__ == "__main__":
    unittest.main()
