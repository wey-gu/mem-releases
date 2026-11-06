# Deferred CUDA GA delivery

Core GA can ship while CUDA is still building. `deferred-cuda-release.yml`
listens for completion of `release-docker` and only admits a producer listed
in the reviewed `release-plans/deferred-cuda.json`. It is an additional lane
for an already accepted public GA, not a way to admit a new App release.

The plan freezes the original producer, RC, source SHA and tools SHA, and links
the completed core GA acceptance. Add or change a plan only through normal
review, CI and merge. Register it while a slow CUDA build is still running.
Include `expected_digest` when already known; otherwise the completion handler
extracts the unique original digest from the successful CUDA publication job.
Admission always freezes that digest before native acceptance and promotion.

Set repository variable `CUDA_RELEASE_RUNNER` to the single label of an
approved existing native AMD64 NVIDIA runner. It must provide the compatible
driver and NVIDIA Container Toolkit described in `DOCKER_VERSION_E2E.md`.
An unset label fails before GPU scheduling. This workflow neither provisions
a GPU runner nor substitutes CPU execution for native NVIDIA evidence.

The persistent pipeline:

1. Matches the exact reviewed producer and requires terminal success, original
   publication digest and signature provenance, matching RC/GA source, and the
   already deployed public GA Changelog. Unrelated producer completions do nothing.
2. Executes the original image's installed version surfaces on NVIDIA hardware
   using the existing verifier, including host/container GPU UUID and driver
   equality. Launch, probe, cleanup and version parsing failures block publication.
3. Reuses `promote-gpu-variant` in retag-only mode, passing the native-accepted
   digest. It refuses RC drift, a conflicting immutable GA tag or a superseded
   public GA immediately before moving the CUDA tags. No rebuild occurs.
4. Independently reads both public tags and retains native and publication
   receipts in Actions artifacts. It does not rewrite core GA evidence or
   touch Desktop, CPU, updater, Vulkan, mobile or Slack.

For a producer that completed before this workflow was merged, or after a
runner/configuration/read-only failure, dispatch the catch-up entry from merged
`main` once:

```sh
gh workflow run deferred-cuda-release.yml -R wey-gu/mem-releases \
  --ref main -f ga_version=0.10.115
```

It uses the same reviewed plan and real native acceptance gates. There is no
fallback to a rebuild, another source, a guessed GPU label or a failed receipt.
After a failed promotion or lost response, inspect the existing run and public
tag digests before retrying. Registry writes have no cross-service transaction
with GitHub GA publication; a concurrently newer core GA must be reconciled,
and no failure is reported as successful CUDA delivery.
