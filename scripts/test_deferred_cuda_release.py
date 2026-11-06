"""Deferred CUDA admission and mutation-boundary regressions; not GPU proof."""

import copy
import io
import json
import subprocess
import unittest
from unittest.mock import patch

import deferred_cuda_release as deferred
from test_ga_promotion_contract import fake_tools, job_section, step_script, workflow_text


PLAN = dict(ga_version="0.10.115", release_tag="v0.10.115-rc2", source_sha="a" * 40,
            tools_sha="b" * 40, producer_run_id=123, expected_digest="sha256:" + "c" * 64,
            core_acceptance="https://github.com/nowledge-co/mem/issues/9726#issuecomment-6682239296")
IDENTITY = dict(repository="wey-gu/mem-releases", build_run_id=123, build_attempt=1,
                build_workflow_sha=PLAN["tools_sha"], release_tag=PLAN["release_tag"],
                source_sha=PLAN["source_sha"], variant="cuda", digest=PLAN["expected_digest"])
LATEST = dict(tag_name="v0.10.115", draft=False, prerelease=False,
              published_at="2026-11-06T09:44:53Z")


class DeferredAdmissionTest(unittest.TestCase):
    def test_only_explicit_reviewed_producers_are_eligible(self):
        event = dict(workflow_run=dict(repository=dict(full_name=deferred.docker.REPOSITORY), id=123))
        self.assertEqual(deferred.select_plan([PLAN], "workflow_run", event, ""), PLAN)
        event["workflow_run"]["id"] = 456
        self.assertIsNone(deferred.select_plan([PLAN], "workflow_run", event, ""))
        self.assertEqual(deferred.select_plan([PLAN], "workflow_dispatch", {}, "0.10.115"), PLAN)
        with self.assertRaises(RuntimeError):
            deferred.select_plan([PLAN], "workflow_dispatch", {}, "0.10.116")
        event["workflow_run"]["repository"]["full_name"] = "other/repository"
        with self.assertRaises(RuntimeError):
            deferred.select_plan([PLAN], "workflow_run", event, "")

    def test_invalid_or_ambiguous_plans_cannot_arm_release(self):
        for key, value in [("ga_version", "0.0.0"), ("release_tag", "v0.10.116-rc2"),
                           ("source_sha", "main"), ("expected_digest", "latest"),
                           ("producer_run_id", True), ("core_acceptance", "")]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                deferred.validate_plan(dict(PLAN, **{key: value}))
        with self.assertRaises(RuntimeError):
            deferred.select_plan([PLAN, PLAN], "workflow_dispatch", {}, "0.10.115")

    def test_native_evidence_cannot_be_borrowed_from_another_candidate(self):
        deferred.validate_binding(PLAN, IDENTITY)
        for key, value in [("source_sha", "d" * 40), ("digest", "sha256:" + "e" * 64),
                           ("build_run_id", 456), ("build_workflow_sha", "f" * 40),
                           ("release_tag", "v0.10.115-rc1"), ("variant", "cpu")]:
            with self.subTest(key=key), self.assertRaises(RuntimeError):
                deferred.validate_binding(PLAN, dict(IDENTITY, **{key: value}))

    def test_latest_ga_must_be_public_and_same_source(self):
        for latest in [dict(LATEST, tag_name="v0.10.116"), dict(LATEST, draft=True),
                       dict(LATEST, prerelease=True), dict(LATEST, published_at=None)]:
            with self.subTest(latest=latest), patch.object(deferred.docker, "api", return_value=latest):
                with self.assertRaises(RuntimeError):
                    deferred.published_ga(PLAN)
        with patch.object(deferred.docker, "api", side_effect=[LATEST, {"sha": "d" * 40}]):
            with self.assertRaises(RuntimeError):
                deferred.published_ga(PLAN)

    def test_no_runner_or_unsuccessful_producer_blocks_before_native(self):
        with patch.object(deferred.docker, "api") as api:
            with self.assertRaises(RuntimeError):
                deferred.admit(PLAN, "")
            api.assert_not_called()
        for status, conclusion in [("in_progress", None), ("completed", "failure"),
                                   ("completed", "cancelled"), ("completed", "skipped")]:
            run = dict(status=status, conclusion=conclusion, head_sha=PLAN["tools_sha"])
            with self.subTest(status=status, conclusion=conclusion), \
                    patch.object(deferred.docker, "api", return_value=run), \
                    patch.object(deferred.docker, "identity") as identity:
                with self.assertRaises(RuntimeError):
                    deferred.admit(PLAN, "approved-nvidia")
                identity.assert_not_called()

    def test_admission_requires_original_identity_and_live_ga_notes(self):
        run = dict(status="completed", conclusion="success", head_sha=PLAN["tools_sha"])
        notes = dict(found=True, version="0.10.115", date="2026-11-06")
        for change, accepted in [({}, True), ({"date": "unreleased"}, False),
                                 ({"version": "0.10.114"}, False), ({"found": False}, False)]:
            response = io.StringIO(json.dumps(dict(notes, **change)))
            response.status = 200
            with self.subTest(change=change), \
                    patch.object(deferred.docker, "api", side_effect=[run, LATEST, {"sha": PLAN["source_sha"]}]), \
                    patch.object(deferred.docker, "identity", return_value=copy.deepcopy(IDENTITY)), \
                    patch.object(deferred, "urlopen", return_value=response):
                if accepted:
                    self.assertEqual(deferred.admit(PLAN, "approved-nvidia"), IDENTITY)
                else:
                    with self.assertRaises(RuntimeError):
                        deferred.admit(PLAN, "approved-nvidia")


class DeferredRetagTest(unittest.TestCase):
    def run_retag(self, *, source_digest=None, ga_digest=None, expected=None, latest=None, gh_exit=0):
        digest = PLAN["expected_digest"]
        registry = {"docker.io/nowledgelabs/mem:0.10.115-rc2-cuda": source_digest or digest}
        if ga_digest:
            registry["docker.io/nowledgelabs/mem:0.10.115-cuda"] = ga_digest
        tmp, state, log, env = fake_tools(self, registry)
        gh = tmp / "gh"
        gh.write_text("#!/usr/bin/env python3\nimport os,sys\nprint(os.environ['FIXTURE_LATEST'])\nsys.exit(int(os.environ['FIXTURE_GH_EXIT']))\n")
        gh.chmod(0o755)
        env.update(IMAGE="docker.io/nowledgelabs/mem", RC="0.10.115-rc2", GA="0.10.115",
                   VARIANT="cuda", SOURCE="retag", MOVE_FLOATING="true", SIGNER="fixture",
                   EXPECTED_DIGEST=digest if expected is None else expected, REQUIRE_LATEST_GA="true",
                   GITHUB_REPOSITORY=deferred.docker.REPOSITORY,
                   FIXTURE_LATEST=json.dumps(LATEST if latest is None else latest),
                   FIXTURE_GH_EXIT=str(gh_exit))
        script = step_script(job_section(workflow_text("promote-gpu-variant.yml"), "retag"),
                             "Re-tag rc GPU image to GA")
        result = subprocess.run(["bash", "-c", script], env=env, cwd=tmp,
                                capture_output=True, text=True)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        creates = [c for c in calls if c[:3] == ["buildx", "imagetools", "create"]]
        return result, json.loads(state.read_text()), creates

    def test_only_native_accepted_original_digest_is_published(self):
        result, registry, creates = self.run_retag()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(creates), 1)
        for tag in ["0.10.115-cuda", "0.10-cuda"]:
            self.assertEqual(registry["docker.io/nowledgelabs/mem:" + tag], PLAN["expected_digest"])
        self.assertNotIn("docker.io/nowledgelabs/mem:latest", registry)

    def test_drift_supersession_and_read_failures_never_write(self):
        for change in [dict(source_digest="sha256:" + "d" * 64), dict(expected=""),
                       dict(ga_digest="sha256:" + "e" * 64),
                       dict(latest=dict(LATEST, tag_name="v0.10.116")),
                       dict(latest=dict(LATEST, draft=True)), dict(gh_exit=1)]:
            with self.subTest(change=change):
                result, registry, creates = self.run_retag(**change)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(creates, [])
                self.assertNotIn("docker.io/nowledgelabs/mem:0.10-cuda", registry)


if __name__ == "__main__":
    unittest.main()
