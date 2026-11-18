# Promote signed mobile RC artifacts

`promote-mobile-to-ga.yml` uploads the original signed IPA and APK from one
successful build-only `release-mobile.yml` run. It never runs a build or signing
command. The original AAB is retained and verified; it is not submitted to Play.

Use an immutable reviewed tooling ref. Persist an operation UUID before dispatch:

```sh
gh workflow run promote-mobile-to-ga.yml -R wey-gu/mem-releases \
  --ref <reviewed-immutable-tooling-ref> \
  -f rc_tag=0.10.121-rc3 -f ga_tag=0.10.121 \
  -f source_sha=<full-Mem-source-SHA> -f source_run=<original-mobile-build-run> \
  -f tools_sha=<original-producer-tooling-SHA> -f operation_id=<persisted-UUID> \
  -f publish=false
```

The default is read-only validation. Before setting `publish=true`, require the
accepted public RC and a matching `v<version>` Mem source tag. The source and
both original/promotion tooling commits must be normally merged. The original
producer must be the successful first attempt at the same RC tooling tag, and
its logs must prove exact source checkout and build-only behavior.

Admission verifies authenticated GitHub archive SHA-256/size, safe extraction,
App/share bundle version and SDK, actual APK version and signature, and AAB
signature. The admitted file hashes are verified again before publication.
No re-signing or filename clobbering is allowed. An existing equal APK is read
back and retained; a different one blocks the run. The community GA tag must
match the App's recorded gitlink. This does not activate Cloud services or run
any optional database expansion.

The iOS job refuses an already existing App Store build before another upload,
and requires explicit `UPLOAD SUCCEEDED with no errors` plus one Delivery UUID.
A timeout is an uncertain outcome, not permission to upload again. Preserve the
intent/log and reconcile App Store Connect first.

TestFlight processing/group attachment is a separate job. If upload succeeded
but processing failed, rerun only the failed distribution job, or use
`distribute-testflight-existing.yml` with the original promotion run, exact source
and Delivery UUID. That recovery workflow accepts only the explicit known upload
workflow/step pairs. It never uploads another IPA. External Apple approval and
physical-device execution remain separate acceptance states; a successful upload
is not a claim that external testers can install the build.
