"""Exercise the release wrapper with real subprocesses and fake Bazel builds."""

import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/actions/bazel-build-observability/bazel.py"
SPEC = importlib.util.spec_from_file_location("bazel_observer", SCRIPT)
OBSERVER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(OBSERVER)
EVENTS = [
    {"started": {"uuid": "c5956e1c-989c-4945-bf9b-d7a2b1a1ce99", "buildToolVersion": "9.3.0"}},
    {"buildMetrics": {"actionSummary": {
        "runnerCount": [{"name": "remote cache hit", "count": 945},
                        {"name": "internal", "count": 440}],
        "actionCacheStatistics": {"hits": 12, "misses": 3},
    }}},
]
FAKE_SCRIPT = r'''
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
Path(os.environ["FAKE_ARGS"]).write_text(json.dumps(args), encoding="utf-8")
sys.stdout.buffer.write(b"bazel-out/bin/result\r\n")
sys.stdout.buffer.flush()
print("native Bazel diagnostic", file=sys.stderr)
for index, arg in enumerate(args):
    if arg.startswith("--build_event_json_file="):
        event_file = arg.split("=", 1)[1]
    elif arg == "--build_event_json_file":
        event_file = args[index + 1]
    else:
        continue
    if event_file and not os.environ.get("FAKE_NO_BEP"):
        path = Path(event_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(os.environ["FAKE_EVENTS"], encoding="utf-8")
sys.exit(int(os.environ.get("FAKE_STATUS", "0")))
'''


class BazelBuildReceiptTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="bazel receipt test ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "fake.py"
        fake.write_text(FAKE_SCRIPT, encoding="utf-8")
        if os.name == "nt":
            self.bazel = self.bin / "bazel.cmd"
            self.bazel.write_text(f'@"{sys.executable}" "{fake}" %*\n@exit /b %errorlevel%\n',
                                  encoding="utf-8")
        else:
            self.bazel = self.bin / "bazel"
            self.bazel.write_text("#!/usr/bin/env bash\nexec " +
                                  shlex.join([sys.executable, str(fake)]) + ' "$@"\n',
                                  encoding="utf-8")
            self.bazel.chmod(0o755)
        self.env = {**os.environ, "NMEM_OBS_REAL_BAZEL": str(self.bazel),
                    "NMEM_OBS_RECEIPT_DIR": str(self.root / "receipts"),
                    "GITHUB_STEP_SUMMARY": str(self.root / "summary.md"),
                    "NMEM_BUILD_SHA_FULL": "a" * 40,
                    "FAKE_ARGS": str(self.root / "args.json"),
                    "FAKE_EVENTS": "\n".join(json.dumps(event) for event in EVENTS) + "\n"}
        self.env.pop("FAKE_NO_BEP", None)
        self.env.pop("FAKE_STATUS", None)

    def invoke(self, args, **env):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root,
                              env={**self.env, **env}, capture_output=True, check=False)

    def receipts(self):
        return sorted((json.loads(path.read_text(encoding="utf-8"))
                       for path in (self.root / "receipts").glob("*.json")),
                      key=lambda receipt: receipt["created_at"])

    def forwarded(self):
        return json.loads((self.root / "args.json").read_text(encoding="utf-8"))

    def test_build_reports_observed_counts_and_preserves_native_streams(self):
        result = self.invoke(["build", "--compilation_mode=opt", "//:server"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"bazel-out/bin/result\r\n")
        self.assertIn(b"native Bazel diagnostic", result.stderr)
        receipt, = self.receipts()
        self.assertEqual(receipt["module"], str(self.root.resolve()))
        self.assertEqual(receipt["source_sha"], "a" * 40)
        self.assertEqual(receipt["bazel_version"], "9.3.0")
        self.assertEqual(receipt["invocation_id"], EVENTS[0]["started"]["uuid"])
        self.assertEqual(receipt["metrics"]["runners"], {"remote cache hit": 945, "internal": 440})
        self.assertEqual(receipt["metrics"]["local_action_cache"], {"hits": 12, "misses": 3})
        self.assertIn("--announce_rc", self.forwarded())
        event_file = OBSERVER.option_value(self.forwarded(), "--build_event_json_file")
        self.assertFalse(Path(event_file).exists(), "Temporary raw BEP must be removed")
        summary = (self.root / "summary.md").read_text(encoding="utf-8")
        self.assertIn("945", summary)
        self.assertIn("440", summary)
        self.assertIn("12 hits, 3 misses", summary)
        self.assertNotIn("%", summary)
        self.assertEqual(set(receipt), {"schema_version", "created_at", "module", "source_sha",
                                       "command", "exit_code", "elapsed_seconds", "bazel_version",
                                       "invocation_id", "metrics"})

    def test_raw_command_events_are_excluded_from_the_receipt(self):
        events = self.env["FAKE_EVENTS"] + json.dumps({"structuredCommandLine": {
            "sections": [{"options": "Bearer raw-event-secret"}]}}) + "\n"
        result = self.invoke(["build", "//:server"], FAKE_EVENTS=events)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("raw-event-secret", json.dumps(self.receipts()))
        self.assertNotIn(b"raw-event-secret", result.stderr)

    def test_non_build_failure_preserves_the_exit_code_and_streams(self):
        result = self.invoke(["cquery", "//:server"], FAKE_STATUS="37")
        self.assertEqual(result.returncode, 37, result.stderr)
        self.assertEqual(result.stdout, b"bazel-out/bin/result\r\n")
        self.assertEqual(self.receipts(), [])

    def test_query_info_and_version_preserve_stdout_without_receipts(self):
        for command in ("query", "cquery", "aquery", "info", "version", "--version"):
            with self.subTest(command=command):
                result = self.invoke([command, "--output=files"])
                native = subprocess.run([str(self.bazel), command, "--output=files"],
                                        cwd=self.root, env=self.env, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, native.stdout)
                self.assertEqual(result.stderr, native.stderr)
                self.assertEqual(self.forwarded(), [command, "--output=files"])
        self.assertEqual(self.receipts(), [])
        self.assertFalse((self.root / "summary.md").exists())

    def test_failure_is_reported_without_changing_the_exit_code(self):
        result = self.invoke(["build", "//:server"], FAKE_STATUS="37")
        self.assertEqual(result.returncode, 37, result.stderr)
        receipt, = self.receipts()
        self.assertEqual(receipt["exit_code"], 37)
        self.assertIn("**37**", (self.root / "summary.md").read_text())
        self.assertEqual(receipt["metrics"]["runners"]["remote cache hit"], 945)

    def test_caller_bep_and_explicit_announcement_override_are_preserved(self):
        for spelling in ("equal", "paired"):
            with self.subTest(spelling=spelling):
                path = self.root / f"caller {spelling}.jsonl"
                option = ([f"--build_event_json_file={path}"] if spelling == "equal" else
                          ["--build_event_json_file", str(path)])
                args = ["build", "--announce_rc=false", *option, "//:server"]
                result = self.invoke(args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.forwarded(), args)
                self.assertEqual(path.read_text(encoding="utf-8"), self.env["FAKE_EVENTS"])

    def test_startup_options_and_noannounce_override_keep_their_order(self):
        args = ["--output_base", str(self.root / "output base"), "--batch", "build",
                "--noannounce_rc", "//:server"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        forwarded = self.forwarded()
        self.assertEqual(forwarded[:4], args[:4])
        self.assertEqual(forwarded[5:], args[4:])
        self.assertEqual(self.receipts()[0]["metrics"]["status"], "available")

    def test_private_values_are_forwarded_but_never_persisted_or_announced(self):
        args = ["build", "--remote_header=Authorization=Bearer private-token",
                "--google_credentials", "private-creds.json",
                "--remote_cache_header=x-api-key=private-key",
                "--repo_env=ACCESS_TOKEN=private-env",
                "--remote_cache", "https://user:private-password@cache.example/path?token=private-query#private-fragment",
                "--bes_backend=grpcs://user:private-bes@bes.example", "//:server"]
        result = self.invoke(args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.forwarded()[3:], args[1:])
        output = result.stderr.decode() + (self.root / "summary.md").read_text()
        output += json.dumps(self.receipts())
        for secret in ("private-token", "private-creds", "private-key", "private-env",
                       "private-password", "private-query", "private-fragment", "private-bes"):
            self.assertNotIn(secret, output)
        self.assertIn("cache.example/path", output)
        self.assertIn("bes.example", output)

    def test_missing_or_invalid_bep_never_fabricates_zero_hits_or_masks_failure(self):
        invalid = ("", "not-json\n", "null\n", "[]\n",
                   json.dumps({"buildMetrics": {}}) + "\n",
                   json.dumps({"buildMetrics": {"actionSummary": {"runnerCount": None}}}) + "\n",
                   json.dumps({"started": []}) + "\n" + self.env["FAKE_EVENTS"].split("\n", 1)[1],
                   self.env["FAKE_EVENTS"] * 2,
                   self.env["FAKE_EVENTS"].replace('"count": 945', '"count": -1'))
        for events in invalid:
            with self.subTest(events=events):
                result = self.invoke(["build", "//:server"], FAKE_EVENTS=events, FAKE_STATUS="41")
                self.assertEqual(result.returncode, 41, result.stderr)
                self.assertIn(b"unavailable", result.stderr)
        result = self.invoke(["build", "//:server"], FAKE_NO_BEP="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        for receipt in self.receipts():
            self.assertEqual(receipt["metrics"]["status"], "unavailable")
            self.assertNotIn("runners", receipt["metrics"])

    def test_local_cache_only_build_and_missing_cache_stats_are_distinct(self):
        for cache in ({"hits": 17}, {}, None):
            with self.subTest(cache=cache):
                actions = {} if cache is None else {"actionCacheStatistics": cache}
                events = json.dumps({"buildMetrics": {"actionSummary": actions}}) + "\n"
                result = self.invoke(["build", "//:server"], FAKE_EVENTS=events)
                self.assertEqual(result.returncode, 0, result.stderr)
                latest = self.receipts()[-1]
                self.assertEqual(latest["metrics"]["runners"], {})
                if cache is None:
                    self.assertNotIn("local_action_cache", latest["metrics"])
                else:
                    self.assertEqual(latest["metrics"]["local_action_cache"],
                                     {"hits": cache.get("hits", 0), "misses": 0})
                expected = b"not reported" if cache is None else f"{cache.get('hits', 0)} hits, 0 misses".encode()
                self.assertIn(expected, result.stderr)

    def test_receipt_io_errors_do_not_replace_the_native_result(self):
        blocked = self.root / "not-a-directory"
        blocked.write_text("occupied")
        for status in (0, 37):
            with self.subTest(status=status):
                result = self.invoke(["build", "//:server"], FAKE_STATUS=str(status),
                                     NMEM_OBS_RECEIPT_DIR=str(blocked / "receipts"))
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(b"could not be persisted", result.stderr)

    def test_configure_activates_wrapper_in_bash_for_each_module(self):
        env_file, path_file = self.root / "env", self.root / "path"
        env = {**self.env, "RUNNER_TEMP": str(self.root), "GITHUB_ENV": str(env_file),
               "GITHUB_PATH": str(path_file),
               "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}
        env.pop("NMEM_OBS_REAL_BAZEL")
        result = subprocess.run([sys.executable, str(SCRIPT), "--configure"],
                                env=env, capture_output=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        configured = dict(line.split("=", 1) for line in env_file.read_text().splitlines())
        self.assertEqual(Path(configured["NMEM_OBS_REAL_BAZEL"]), self.bazel)
        wrapper_dir = Path(path_file.read_text().strip())
        self.assertNotIn(b"\r", (wrapper_dir / "bazel").read_bytes(),
                         "The Bash entry point must use LF on Windows too")
        bash = shutil.which("bash")
        if os.name == "nt":
            git_bash = Path(os.environ["ProgramFiles"]) / "Git/bin/bash.exe"
            if git_bash.exists():
                bash = str(git_bash)
        self.assertIsNotNone(bash, "Bash is required by the native bundle entry point")
        for module in (self.root / "product", self.root / "product/integrations/hawdb"):
            module.mkdir(parents=True, exist_ok=True)
            result = subprocess.run([bash, "-c", 'export PATH="$WRAPPER_DIR:$PATH"; bazel build //:server'],
                                    cwd=module, env={**env, **configured, "WRAPPER_DIR": wrapper_dir.as_posix()},
                                    capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        receipts = list((self.root / "nmem-bazel-receipts").glob("*.json"))
        self.assertEqual(len(receipts), 2)
        self.assertEqual({json.loads(path.read_text())["module"] for path in receipts},
                         {str((self.root / "product").resolve()),
                          str((self.root / "product/integrations/hawdb").resolve())})

    def test_summaries_escape_markdown_and_redaction_blocks_line_injection(self):
        receipt = {"module": "<script>|module", "exit_code": 0, "elapsed_seconds": 1.0,
                   "bazel_version": "9.3.0", "invocation_id": "test", "command": ["bazel", "build"],
                   "metrics": {"status": "available", "runners": {"runner|<unsafe>": 1}}}
        output = OBSERVER.summary(receipt)
        self.assertNotIn("<script>", output)
        self.assertIn("&#124;", output)
        self.assertEqual(OBSERVER.redacted_command(["build", "--label=line\n::error::injected"]),
                         ["build", "--label=line\\n::error::injected"])


class WorkflowReceiptContractTest(unittest.TestCase):
    def test_every_bazel_bundle_job_enables_and_always_uploads_receipts(self):
        for filename in ("release-desktop.yml", "test-windows-bazel.yml"):
            workflow = (ROOT / ".github/workflows" / filename).read_text()
            anchors = dict(re.findall(r"^      - &([a-z_]+)\n((?:^        .*\n|^\n)+)",
                                      workflow, re.MULTILINE))
            expanded = re.sub(r"^      - \*([a-z_]+)\n",
                              lambda match: "      -\n" + anchors[match[1]], workflow, flags=re.MULTILINE)
            jobs = re.split(r"\n  [a-z][a-z0-9_-]+:\n", expanded)[1:]
            observed = 0
            for job in jobs:
                if "uses: bazel-contrib/setup-bazel@" not in job:
                    continue
                observed += 1
                self.assertLess(job.index("Set up Bazel"), job.index("Enable Bazel build receipts"))
                self.assertLess(job.index("Enable Bazel build receipts"), job.index("./scripts/build-rust-bundle.sh"))
                self.assertIn("ref: ${{ github.sha }}", job)
                self.assertIn("uses: ./release-tools/.github/actions/bazel-build-observability", job)
                upload = job.split("name: Upload Bazel build receipts\n", 1)[1].split("\n      -", 1)[0]
                self.assertIn("if: always()", upload)
                self.assertIn("/nmem-bazel-receipts/*.json", upload)
                self.assertNotIn("*.jsonl", upload)
            self.assertEqual(observed, 5 if filename == "release-desktop.yml" else 1)


if __name__ == "__main__":
    unittest.main()
