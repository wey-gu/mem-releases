"""Verify the exact uncompressed RPM payload against its SHA-256 header tag."""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess


def verify_archive_digest(rpm):
    metadata = subprocess.check_output(
        [
            "rpm",
            "-qp",
            "--qf",
            "%{PAYLOADDIGESTALT}\n%{PAYLOADDIGESTALGO}\n%{LONGARCHIVESIZE}\n",
            str(rpm),
        ],
        text=True,
    ).splitlines()
    if len(metadata) != 3:
        raise ValueError(f"Unexpected RPM digest metadata: {metadata}")
    expected, algorithm, archive_size = metadata
    if algorithm != "8" or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("RPM must declare a SHA-256 uncompressed payload digest")
    digest = hashlib.sha256()
    with subprocess.Popen(["rpm2cpio", str(rpm)], stdout=subprocess.PIPE) as process:
        for chunk in iter(lambda: process.stdout.read(1024 * 1024), b""):
            digest.update(chunk)
        status = process.wait()
    actual = digest.hexdigest()
    if actual != expected:
        raise ValueError(
            f"Uncompressed RPM archive digest mismatch: expected {expected}, "
            f"got {actual} (rpm2cpio exit {status})"
        )
    if status:
        # RPM 4.17 compares copied bytes with LONGARCHIVESIZE and exits 1 when
        # that optional tag is absent. rpm-rs 0.16 omits it. Require the full
        # archive digest to match before accepting this specific legacy case.
        if status != 1 or archive_size != "(none)":
            raise subprocess.CalledProcessError(status, ["rpm2cpio", str(rpm)])
        print("rpm2cpio legacy size check: absent LONGARCHIVESIZE; digest verified")
    print(f"Uncompressed RPM archive SHA-256 verified: {actual}")
    return actual


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rpm", type=Path)
    verify_archive_digest(parser.parse_args().rpm)
