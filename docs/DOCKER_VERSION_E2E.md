# Published Docker version acceptance

The builder's version commands do not execute the final image. The runtime
stage changes the filesystem and dependencies before copying the four binaries
and selecting the unprivileged `nowledge` user. The existing Docker server
smoke and signature checks remain required independently.

Dispatch `docker-version-e2e.yml` with the original tag-push `release-docker`
run ID, exact RC tag, full Mem source SHA, variant and original published index
digest. The workflow performs no build, image push, retag, promotion or update
authority mutation. CPU requires native AMD64 and ARM64 receipts; CUDA and
Vulkan each require their own AMD64 receipt and digest. CPU success does not
qualify either GPU image or GPU hardware behavior.

The identity gate requires successful producer jobs for the selected variant,
the tools tag's peeled commit, the Mem source tag and the original publication
log's unique digest. CPU requires both original native server verification
jobs. Independent GPU jobs may still be running or fail; the receipt records
the producer's overall state without claiming that the entire run passed.

Each native runner checks the tag's current index digest, selects its immutable
platform child and pulls that digest. It verifies the original index signature
against the exact tag-push workflow identity. Runtime checks preserve the
image's default user, PATH, dependencies and filesystem. No replacement binary,
host bind mount, user override or injected dependency is used. Every created
container is removed even when execution fails; cleanup failure is fatal.
Failed version commands retain their exact invocation, exit code and bounded
captured stdout/stderr through cleanup, so a runtime loader failure is actionable.

The actual installed server, CLI, both TUI invocation paths and legacy reader
execute in the final image. Server, CLI and TUI report the numeric App version;
server also reports the full source SHA. The legacy reader has an independent
contract: `kuzu-0.11.0`, integer protocol `1` and the exact Mem commit. Docker
has no Desktop App surface. Image labels bind the RC package identity and build
repository; they do not substitute for binary output or imply a revision label.

Only a complete same-candidate native receipt set is accepted. A failed pull,
launch, parse, signature, version, source, user, architecture or cleanup check
exits nonzero without a success receipt. Retain original build/server-smoke
evidence alongside these additive version receipts.

## Required-test admission

- Protected invariant: the release `AGENTS.md` requires actual execution of
  every applicable installed version surface in each shipped packaging path.
- Terminal oracle: real verifier subprocess, command-boundary contract fixture,
  and separate native execution of the original published image digests.
- RED/GREEN: `test_cli_validation_removal_is_caught_by_unchanged_assertion`
  removes only the production CLI version validation in a temporary copy. The
  unchanged previous-version rejection assertion fails because the mutant
  writes a success receipt for CLI `0.10.103`; restoring the production helper
  rejects the identical fixture. CI owns execution and the observed result.
- Unique coverage: final runtime CLI and TUI execution was absent from the
  builder-only version commands and post-publication HTTP server smoke.
- Failure diagnostics: a real verifier subprocess receives exit 127 with both
  output streams from the command-boundary fixture. It must retain the command,
  exit and output, remove its container, and withhold a receipt. Removing only
  the production diagnostic message makes the unchanged assertion RED; the
  restored helper must pass. This does not qualify the failed runtime image.
- CI budget: PR contract job, five-minute ceiling, no shared mutable service,
  no retries. Native jobs are explicit release acceptance only, twenty-minute
  ceiling per architecture, owned by the release operator. Fixtures prove the
  verifier's rejection behavior; they are not package acceptance or GPU proof.

Related release tracking: https://github.com/nowledge-co/mem/issues/7604
