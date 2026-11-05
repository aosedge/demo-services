#!/usr/bin/env bash
# Copyright (c) 2018-2026 EPAM Systems Inc.
# SPDX-License-Identifier: Apache-2.0
#
# The built-in checks of the new version, then your tests, then an HTML report. Run it after wait (it reads AosCloud only).
# Inputs (environment): SERVICE_ID, VERSION, BATCH (or BATCH_ID), UNIT_ID (or AOS_TEST_UNITS: several Unit ids,
# separated by spaces), and the settings of verify_batch.py (AOS_API_URL, AOS_CERT, AOS_CA).
# AOS_WATCH_SECONDS (or WATCH_SECONDS, which wins): how long to observe the version after the checks; default 60, 0 = off.
# Your tests: tests/your_tests.sh, when it exists (start from tests/your_tests.sh.example).
# Writes report/report.html and report/junit.xml (REPORT_DIR changes the folder).
# RUN_PART: all (default), or builtin and then yours: the job runs them in two steps, so your tests have no key at all.
# VERSION_FAILED=yes: wait already saw the version fail on a Unit. Only the report: no observation, and not your tests.
# Exit 0 = every check passed: approve. 1 = a check failed: reject. 2 = a setting is missing.
# 3 = AosCloud could not be read, or a test Unit is offline: decide nothing, neither says anything about the version.
# builtin always exits 0.
set -u
here=$(cd "$(dirname "$0")" && pwd)
python=${PYTHON:-python}
batch=${BATCH_ID:-${BATCH:-}}
units=${AOS_TEST_UNITS:-${UNIT_ID:-}}
observe=${WATCH_SECONDS:-${AOS_WATCH_SECONDS:-60}}
out=${REPORT_DIR:-report}
part=${RUN_PART:-all}
version_failed=${VERSION_FAILED:-}
if [ -n "$version_failed" ]; then observe=0; fi

if [ -z "${SERVICE_ID:-}" ] || [ -z "${VERSION:-}" ]; then echo "SERVICE_ID or VERSION is empty: export them first"; exit 2; fi
if [ -z "$batch" ]; then echo "BATCH is empty: run wait first, it sets BATCH"; exit 2; fi
unit_args=()
for unit in $units; do unit_args+=(--unit "$unit"); done
if [ ${#unit_args[@]} -eq 0 ]; then echo "UNIT_ID is empty: export it first"; exit 2; fi

mkdir -p "$out"
results="$out/results.tsv"
if [ "$part" != yours ]; then : > "$results"; fi
failed=0
unread=0

record() {  # record <builtin|yours> <code> <name> <PASS|FAIL|SKIP|ERROR> <detail>
  printf '%s\t%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "$4" "$5" >> "$results"
  echo "$4 ${2:+$2 }$3${5:+: $5}"
  if [ "$4" = FAIL ]; then failed=1; fi
  if [ "$4" = ERROR ]; then unread=1; fi
}

if [ "$part" != yours ]; then
echo "Built-in checks of version $VERSION"

# A1, B1, C2: what wait proved, read again from AosCloud.
status=$("$python" "$here/../verify_batch.py" status --service-id "$SERVICE_ID" --version "$VERSION" \
         --batch-id "$batch" "${unit_args[@]}")
if [ $? -eq 2 ]; then
  record builtin A1 "read the batch and the Units" ERROR "AosCloud could not be read, or a Unit is offline: see the log"
else
  while IFS=$'\t' read -r code name result detail; do
    [ -n "$code" ] && record builtin "$code" "$name" "$result" "$detail"
  done <<< "$status"
fi

# wait saw it fail, even if AosCloud reads differently a moment later: the verdict must stay what wait said.
if [ -n "$version_failed" ] && ! grep -q $'^[^\t]*\t[^\t]*\t[^\t]*\tFAIL' "$results"; then
  record builtin B1 "$VERSION is installed and running on every test Unit" FAIL "wait saw the version fail on a test Unit"
fi

# C3, C4: the observation. Every 60 s, wait checks that the version still runs on every Unit. A restart between two reads
# is not seen, and memory is not measured: add a check to tests/your_tests.sh if you need them.
if [ "$observe" -gt 0 ]; then
  rounds=$(( (observe + 59) / 60 ))
  echo "observing for $observe s ($rounds round(s) of 60 s)"
  stopped=0
  lost=0
  for ((round = 1; round <= rounds; round++)); do
    sleep "${WATCH_INTERVAL:-60}"
    said=$("$python" "$here/../verify_batch.py" wait --service-id "$SERVICE_ID" --version "$VERSION" \
           "${unit_args[@]}" --timeout 60 2>&1 > /dev/null)
    code=$?
    if [ "$code" -ne 0 ]; then
      echo "$said"
      stopped=$round
      if [ "$code" -eq 3 ]; then lost=1; fi
      break
    fi
  done
  still="still running after $observe s, no restarts"
  if [ "$stopped" -eq 0 ]; then
    record builtin C3/C4 "$still" PASS "active on every Unit at each read, one read every 60 s; memory is not measured"
  elif [ "$lost" -eq 1 ]; then
    record builtin C3/C4 "$still" ERROR "AosCloud could not be read, or a Unit went offline, in round $stopped of $rounds"
  else
    record builtin C3/C4 "$still" FAIL "the version stopped running in round $stopped of $rounds"
  fi
elif [ -n "$version_failed" ]; then
  record builtin C3/C4 "still running, no restarts" SKIP "not run: the version already failed on a test Unit"
else
  record builtin C3/C4 "still running, no restarts" SKIP "60-second observation was off, AOS_WATCH_SECONDS=0"
fi

fi
if [ "$part" = builtin ]; then exit 0; fi

# Run by parts, the built-in results are already in the file. No built-in result: nothing to approve on.
if [ "$part" = yours ]; then
  if ! grep -q '^builtin' "$results" 2>/dev/null; then
    record builtin A1 "read the batch and the Units" ERROR "the built-in checks did not run"
  fi
  if grep -q $'^[^\t]*\t[^\t]*\t[^\t]*\tFAIL' "$results"; then failed=1; fi
  if grep -q $'^[^\t]*\t[^\t]*\t[^\t]*\tERROR' "$results"; then unread=1; fi
fi

# Your tests: tests/your_tests.sh prints one PASS or FAIL line per test. They run without AOS_CERT.
if [ -n "$version_failed" ]; then
  record yours "" "your tests" SKIP "not run: the version already failed on a test Unit"
elif [ -f "$here/your_tests.sh" ]; then
  echo "Your tests"
  yours=$(BATCH_ID=$batch env -u AOS_CERT -u AOS_KEY bash "$here/your_tests.sh" 2>&1)
  code=$?
  lines=0
  while IFS= read -r line; do
    if [[ $line =~ ^(PASS|FAIL)\ ([^ :]+):?\ ?(.*)$ ]]; then
      record yours "" "${BASH_REMATCH[2]}" "${BASH_REMATCH[1]}" "${BASH_REMATCH[3]}"
      lines=$((lines + 1))
    else
      echo "  $line"
    fi
  done <<< "$yours"
  if [ $code -ne 0 ] && ! grep -q $'^yours\t[^\t]*\t[^\t]*\tFAIL' "$results"; then
    record yours "" your_tests.sh FAIL "ended with exit code $code"
  elif [ $lines -eq 0 ]; then
    record yours "" your_tests.sh FAIL "printed no PASS or FAIL line"
  fi
else
  record yours "" "your tests" SKIP "no tests/your_tests.sh (start from tests/your_tests.sh.example)"
fi

"$python" "$here/report.py" --results "$results" --out "$out" --service-id "$SERVICE_ID" --version "$VERSION" \
  --batch-id "$batch" --units "$units" --observe "$observe" \
  --found-by "${FOUND_BY:-version}" --units-from "${UNITS_FROM:-the values you gave}" || failed=1
if [ $failed -eq 1 ]; then echo "a check failed: reject"; exit 1; fi
if [ $unread -eq 1 ]; then echo "AosCloud could not be read, or a Unit is offline: decide nothing, and run it again"; exit 3; fi
echo "all checks passed: approve"
exit 0
