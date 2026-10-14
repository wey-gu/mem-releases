#!/bin/bash
# Runs inside a Fedora container; see .github/workflows/rpm-upgrade-smoke.yml.
set -euo pipefail
fail=0
links() {
  local state=""
  for c in nmem browse-now; do
    if [[ -L /usr/local/bin/$c && -e /usr/local/bin/$c ]]; then state+="$c=ok "; else state+="$c=missing "; fi
  done
  echo "$state"
}
size() { rpm -q --qf '%{SIZE}' "Nowledge Mem" 2>/dev/null || rpm -qa --qf '%{NAME} %{SIZE}\n' | grep -i nowledge; }
check() { if eval "$2"; then echo "SUMMARY ok:   $1"; else echo "SUMMARY FAIL: $1"; fail=1; fi; }

# Desktop Fedora ships procps-ng (pgrep); the container image does not, and the
# maintainer scripts fail closed without it.
dnf -y -q install procps-ng >/dev/null
dnf -y -q install /rpms/base.rpm >/dev/null
echo "SUMMARY base installed: links [$(links)] size $(size)"

dnf -y -q install /rpms/$VER_A.rpm >/dev/null
echo "SUMMARY base -> $VER_A (old %postun runs last, reported only): links [$(links)] size $(size)"

dnf -y -q install /rpms/$VER_B.rpm >/dev/null
L="$(links)"
echo "SUMMARY $VER_A -> $VER_B: links [$L] size $(size)"
check "CLI links survive an upgrade between fixed packages (#7119)" '[[ "$L" == "nmem=ok browse-now=ok " ]]'
for b in nmem nmem-server browse-now; do
  check "/usr/lib/nowledge-mem/$b runs --version" "/usr/lib/nowledge-mem/$b --version >/dev/null 2>&1"
done
big="$(find /usr/lib/nowledge-mem -type f -size +1M 2>/dev/null | wc -l)"
check "no full binary copy under /usr/lib/nowledge-mem (found $big)" '[[ "$big" == 0 ]]'
check "nmem on PATH runs" 'nmem --version >/dev/null 2>&1'

dnf -y -q remove "Nowledge Mem" >/dev/null || rpm -e "$(rpm -qa | grep -i nowledge | head -1)"
L="$(links)"
check "CLI links removed on uninstall (got: $L)" '[[ "$L" == "nmem=missing browse-now=missing " ]]'
exit $fail
