"""Verify native Bash logging with fake Bazel subprocesses on all three OSes."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / ".github/actions/bazel-build-observability"
WRAPPER = ACTION / "bazel.sh"
NATIVE_METRICS = "INFO: Elapsed time: 116.585s, Critical Path: 22.26s\nINFO: 1385 processes: 945 remote cache hit, 440 internal."
FAKE_BAZEL = r'''#!/usr/bin/env bash
printf '%s\n' "$@" > "$FAKE_ARGS"
printf 'bazel-out/bin/result\r\n'
printf 'native Bazel diagnostic\n' >&2
if [[ " $* " == *" build "* && -n "${FAKE_METRICS:-}" ]]; then
  printf '%s\n' "$FAKE_METRICS" >&2
fi
exit "${FAKE_STATUS:-0}"
'''


class NativeBazelLoggingTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bazel logging test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake = self.bin / "bazel"
        self.fake.write_bytes(FAKE_BAZEL.encode())
        self.fake.chmod(0o755)
        self.bash = shutil.which("bash")
        if os.name == "nt":
            git_bash = Path(os.environ["ProgramFiles"]) / "Git/bin/bash.exe"
            if git_bash.exists():
                self.bash = str(git_bash)
        self.assertIsNotNone(self.bash)
        self.env = {**os.environ, "NMEM_BAZEL_LOG_REAL": self.fake.as_posix(),
                    "FAKE_ARGS": str(self.root / "args"), "FAKE_METRICS": NATIVE_METRICS,
                    "FAKE_STATUS": "0", "GITHUB_STEP_SUMMARY": str(self.root / "summary.md")}

    def invoke(self, args, **env):
        return subprocess.run([self.bash, str(WRAPPER), *args], cwd=self.root,
                              env={**self.env, **env}, capture_output=True, check=False)

    def summary(self):
        return (self.root / "summary.md").read_text(encoding="utf-8")

    def forwarded(self):
        return (self.root / "args").read_text(encoding="utf-8").splitlines()

    def test_build_streams_native_output_and_quotes_original_cache_statistics(self):
        args = ["build", "--compilation_mode=opt", "//:server"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b"bazel-out/bin/result\r\n", result.stdout)
        self.assertIn(NATIVE_METRICS.encode(), result.stdout)
        self.assertIn(b"Bazel module:", result.stderr)
        self.assertIn(b"Bazel command:", result.stderr)
        self.assertEqual(self.forwarded(), args, "Logging must not add Bazel flags")
        self.assertIn(NATIVE_METRICS, self.summary())
        self.assertIn("Exit code: 0", self.summary())
        self.assertNotIn("%", self.summary())

    def test_query_info_and_version_keep_both_streams_unchanged(self):
        for args in (["query", "//:server"], ["cquery", "//:server", "--output=files"],
                     ["info", "execution_root"], ["version"], ["--version"], []):
            with self.subTest(args=args):
                result = self.invoke(args)
                native = subprocess.run([self.bash, str(self.fake), *args], cwd=self.root,
                                        env=self.env, capture_output=True, check=False)
                self.assertEqual(result.returncode, native.returncode, result.stderr)
                self.assertEqual(result.stdout, native.stdout)
                self.assertEqual(result.stderr, native.stderr)
        self.assertFalse((self.root / "summary.md").exists())

    def test_failed_build_and_query_retain_native_exit_codes(self):
        for command in ("build", "cquery"):
            with self.subTest(command=command):
                result = self.invoke([command, "//:server"], FAKE_STATUS="37")
                self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn("Exit code: 37", self.summary())
        self.assertIn(NATIVE_METRICS, self.summary())

    def test_missing_native_metrics_are_not_reported_as_zero_hits(self):
        for metrics in ("", "INFO: Elapsed time: 0.01s"):
            with self.subTest(metrics=metrics):
                result = self.invoke(["build", "//:server"], FAKE_METRICS=metrics, FAKE_STATUS="41")
                self.assertEqual(result.returncode, 41, result.stderr)
                self.assertIn("not reported", self.summary())
                self.assertNotIn("0 hits", self.summary())
                self.assertNotIn("remote cache hit", self.summary())

    def test_private_arguments_are_forwarded_but_redacted_from_observer_output(self):
        args = ["build", "--remote_header=Authorization=Bearer private-token",
                "--google_credentials", "private-creds.json", "--repo_env=TOKEN=private-env",
                "--remote_cache", "https://user:private-password@cache.example/path?token=private-query",
                "--bes_backend=grpcs://user:private-bes@bes.example", "//:server"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.forwarded(), args)
        output = result.stderr.decode() + self.summary()
        for secret in ("private-token", "private-creds", "private-env", "private-password",
                       "private-query", "private-bes"):
            self.assertNotIn(secret, output)
        self.assertIn("remote_header", output)
        self.assertIn("redacted", output)

    def test_startup_options_existing_bep_and_announcement_settings_pass_unchanged(self):
        caller_bep = self.root / "caller.jsonl"
        caller_bep.write_text("caller-owned data")
        args = ["--output_base", "output base", "--batch", "build", "--announce_rc=false",
                f"--build_event_json_file={caller_bep}", "//:server"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.forwarded(), args)
        self.assertEqual(caller_bep.read_text(), "caller-owned data")
        self.assertIn(NATIVE_METRICS, self.summary())

    def test_timestamped_and_zero_process_native_summaries_are_kept(self):
        metrics = "[12:34:56] INFO: Elapsed time: 0.01s\n[12:34:56] INFO: 0 processes."
        result = self.invoke(["build", "//:server"], FAKE_METRICS=metrics)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(metrics, self.summary())

    def test_summary_write_failure_does_not_hide_a_native_failure(self):
        result = self.invoke(["build", "//:server"], FAKE_STATUS="37",
                             GITHUB_STEP_SUMMARY=str(self.root / "missing/summary.md"))
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertIn(b"native exit code is preserved", result.stderr)

    def test_action_enables_bash_path_for_independent_modules(self):
        text = (ACTION / "action.yml").read_text()
        script = "\n".join(line[8:] for line in text.split("      run: |\n", 1)[1].splitlines())
        env_file, path_file = self.root / "env", self.root / "path"
        env = {**self.env, "RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(env_file),
               "GITHUB_PATH": str(path_file), "OBSERVABILITY_ACTION_PATH": str(ACTION),
               "PATH_PREFIX": self.bin.as_posix()}
        env.pop("NMEM_BAZEL_LOG_REAL")
        prefix = 'if command -v cygpath >/dev/null; then PATH_PREFIX=$(cygpath -u "$PATH_PREFIX"); fi\nexport PATH="$PATH_PREFIX:$PATH"\n'
        result = subprocess.run([self.bash, "-c", prefix + script], env=env,
                                capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        configured = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
        wrapper_dir = path_file.read_text().strip()
        self.assertNotIn(b"\r", (self.root / "nmem-bazel-wrapper/bazel").read_bytes())
        for module in (self.root / "product", self.root / "product/integrations/hawdb"):
            module.mkdir(parents=True, exist_ok=True)
            run = prefix + "bazel build //:server"
            result = subprocess.run([self.bash, "-c", run], cwd=module,
                                    env={**env, **configured, "PATH_PREFIX": wrapper_dir},
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.summary().count("### Bazel build"), 2)
        self.assertIn("hawdb", self.summary())
        self.assertNotIn("python", text.lower(), "Production logging must not require Python")


class WorkflowNativeLoggingContractTest(unittest.TestCase):
    def test_every_bazel_bundle_job_enables_the_same_helper_before_build(self):
        for filename in ("release-desktop.yml", "test-windows-bazel.yml"):
            workflow = (ROOT / ".github/workflows" / filename).read_text()
            anchors = dict(re.findall(r"^      - &([a-z_]+)\n((?:^        .*\n|^\n)+)",
                                      workflow, re.MULTILINE))
            expanded = re.sub(r"^      - \*([a-z_]+)\n", lambda match: "      -\n" + anchors[match[1]],
                              workflow, flags=re.MULTILINE)
            jobs = re.split(r"\n  [a-z][a-z0-9_-]+:\n", expanded)[1:]
            observed = 0
            for job in jobs:
                if "uses: bazel-contrib/setup-bazel@" not in job:
                    continue
                observed += 1
                self.assertLess(job.index("Set up Bazel"), job.index("Enable Bazel build logging"))
                self.assertLess(job.index("Enable Bazel build logging"), job.index("./scripts/build-rust-bundle.sh"))
                self.assertIn("ref: ${{ github.sha }}", job)
                self.assertIn("uses: ./release-tools/.github/actions/bazel-build-observability", job)
                self.assertNotIn("nmem-bazel-receipts", job)
            self.assertEqual(observed, 5 if filename == "release-desktop.yml" else 1)


if __name__ == "__main__":
    unittest.main()
