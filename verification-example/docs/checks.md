# Example checks

12 scenarios we ran on production AosCloud 6.1 (28–30.10.2026), and step ⓪, finding the Verification batch. Use them as a starting point if you have no tests
yet. Most are already built into `verify_batch.py` and `tests/run_tests.sh`; the rest are a few lines in
`tests/your_tests.sh`.

## What runs by default

`./aos-verify test` (`bash tests/run_tests.sh`, after `wait`) runs the built-in checks A1, B1, C2 and the observation (C3/C4, 60 s) with no setup, then
partner tests if `tests/your_tests.sh` exists. Each check is one row in `report/report.html`; the report ends with a
"Scenarios covered" table with a status for each scenario in that run (Checked now, Covered by the waiting logic, Not run, Next Service version, or Needs AosCloud 6.2) and what you can change for it. The commands below run as they are in
the folder of the example, after `./aos-verify setup` and `./aos-verify discover`, once you have loaded the values
(`SERVICE_ID`, `UNIT_ID`, `VERSION`, `BATCH`) into your terminal:

```bash
source .venv/bin/activate && source .aos-verify.env && source .aos-verify/state
```

| # | Scenario | What can go wrong | Covered by | Row in the report | Result on production |
|---|---|---|---|---|---|
| ⓪ | Found automatically (discover) | nobody starts the job, or it starts on an old Verification batch | `discover`: the highest Service version, Units from the Verification Set | Scenarios covered: batch …, Service version …, Units from the Verification Set | read-only on production and on staging, 04.11: right batch, stale ones left alone. The scheduled watch ran only against a mock so far |
| A1 | Exactly this Service and Service version | a decision on the wrong batch | `wait`, then the built-in checks read it again | Built-in checks: A1 | seen on production, 30.10: the batch of the uploaded Service version found |
| A2 | Upload processed | the bundle is refused, and nobody notices | `aos-signer upload` output; SP UI Deployment bundles; `wait` says `no Verification batch yet` | Scenarios covered: Checked now | Done: seen on production, 30.10; Error with the reason: seen on production, 28.10 |
| A3 | Same Service version uploaded again | a decision on an old Service version | `wait`: no batch, exit 2, nothing decided | Scenarios covered: Covered by the waiting logic | seen on production, 28.10 |
| A4 | Evidence for every release | nobody can say later why a Service version was approved | the report, `junit.xml`, the job log, the batch in the UI | the report itself | seen on production, 30.10: batch Verified in the UI |
| B1 | On every test Unit | approved before the Service version runs everywhere | `wait`, then the built-in checks read it again | Built-in checks: B1 | seen on production, 30.10: active on the test Unit about 15 s after the upload |
| B2 | A Unit that never gets it | approved while half of the Units never got it | `wait` times out, exit 2, no decision | Scenarios covered: Covered by the waiting logic | seen on production, 29.10 |
| C1 | The Service's own tests | the Service version runs but does not work | `tests/your_tests.sh` | Partner tests | seen on production, 30.10: the sample checks passed |
| C2 | Crash on start | a Service version that does not start is approved | `wait` exit 1 → reject; the built-in checks read it again | Built-in checks: C2 | seen on production, 29.10 |
| C3 | Late failure | the Service version fails minutes after the tests | the observation, on by default (60 s) | Built-in checks: C3/C4 | seen on production, 29.10 |
| C4 | Restarts | the Service version keeps restarting | the observation, at each read; a restart check in `tests/your_tests.sh`. Memory is not measured | Built-in checks: C3/C4; Partner tests | seen on production, 29.10 |
| E1 | Bad configuration | a wrong setting ships with good code | a config test in `tests/your_tests.sh` | Partner tests | seen on production, 29.10 |
| E2 | Recovery: the fixed Service version | the fix skips the gate | upload the fix as a new Service version; the same steps approve it | the report of the fixed Service version | seen on production, 29.10 |

## ⓪. Found automatically (discover)

**What it checks.** The right Verification batch and the right Units are found with nobody naming them: by the watch in GitHub, by
`./aos-verify run` on a laptop.

**How.** `discover` lists the batches of your Service, takes the highest Service version if its batch waits, and takes the
Units that are in the Verification Set and have the Service. It only reads.

```bash
python verify_batch.py discover --dry-run
```

**What you see.** One JSON line, or `no waiting Verification batch`. In the report, Scenarios covered: "found by the watch in GitHub: batch
…, Service version …, Units from the Verification Set", or "found by ./aos-verify on this computer: …".

**The decision.** None here. With no test Unit: exit 2, and nothing starts.

## A1. Exactly this Service and Service version

**What it checks.** The Verification batch holds exactly the Service and the Service version you uploaded, and it still waits.

**How.** `wait` takes only the batch whose item has your Service id and your Service version. The built-in checks read the batch again.

```bash
python verify_batch.py status --service-id "$SERVICE_ID" --version "$VERSION" --batch-id "$BATCH" --unit "$UNIT_ID"
```

**What you see.** In the report, Built-in checks: `A1 the Verification batch is for this Service and this Service version PASS`.

**The decision.** No matching batch: `wait` decides nothing (exit 2). It never decides on another batch.

## A2. Upload processed

**What it checks.** AosCloud accepted the bundle and created a Verification batch.

**How.** Read the end of the upload output. In the SP UI, Deployment bundles shows Done or Error with the reason.

```bash
aos-signer upload
```

**What you see.** `Deployment bundle successfully uploaded!`. While AosCloud still processes the bundle, `wait`
prints `no Verification batch yet for <Service version>, waiting`.

**The decision.** Error: no batch, so `wait` times out with exit 2 and decides nothing. Read the reason in the UI.

## A3. Same Service version uploaded again

**What it checks.** A Service version number that already exists is not decided again.

**How.** AosCloud refuses the upload (`already exists`), so there is no new Verification batch. The old batch is already decided,
and `wait` does not touch it.

**What you see.** `wait` prints `no Verification batch yet for <Service version>, waiting`, then `timed out`, or
`Verification batch … is no longer Waiting_validation: nothing to decide`.

**The decision.** Exit 2, nothing sent. Upload the change as a new Service version.

## A4. Evidence for every release

**What it checks.** You can show later what was checked and who approved.

**How.** `tests/run_tests.sh` writes `report/report.html` and `report/junit.xml`; the job keeps them as the artifact
**verification-report**. The job log holds the Verification batch id, the Unit states and the decision. The batch keeps its state
in the UI (SP → Verification batches). GitHub deletes artifacts and logs after the retention period of your
repository (Settings → Actions → General): keep a copy if you need it longer.

**What you see.** The report: the verdict at the top, one row per check. In the UI the batch shows Verified or
Invalid.

**The decision.** None; this is the record of the decision.

## B1. On every listed Unit

**What it checks.** The Service version runs on every test Unit before the checks go on.

**How.** In the job, the Units come from AosCloud. In the terminal, or to override them: one `--unit` per test Unit
(in the job: `AOS_TEST_UNITS`, separated by spaces; in the terminal: `AOS_TEST_UNITS` instead of `UNIT_ID`). `wait`
waits for each; the built-in checks read each again.

```bash
python verify_batch.py check --service-id "$SERVICE_ID" --unit "$UNIT_ID"
```

**What you see.** `wait` prints `Verification batch …: Unit … installed`; the report: `B1 … is installed and running on every test
Unit PASS`, "Service version … active on 1 of 1 Units".

**The decision.** Exit 0 only when the Service version is active on every Unit; then the checks run.

## B2. A Unit that never gets it

**What it checks.** No decision while a Unit is offline or stuck.

**How.** `wait` stops after `--timeout` (in the job: `AOS_WAIT_TIMEOUT`, default 1800 s).

**What you see.** The last Unit states, for example `{'…': 'installed', '…': 'waiting'}`, then
`timed out after 1800 s: decide nothing`. For an offline Unit: `Unit … is offline: what AosCloud shows for it is old, so
it does not count`, even if AosCloud still shows the Service version active.

**The decision.** Exit 2, or exit 3 if a Unit is offline; nothing sent, and the Verification batch keeps waiting. Bring the Unit
online and run it again.

## C1. The Service's own tests

**What it checks.** The new Service version works on the real Unit.

**How.** Partner tests in `tests/your_tests.sh`, one line each. Start from the example ([Partner tests](tests.md)):

```bash
cp tests/your_tests.sh.example tests/your_tests.sh
```

A test is one line in that file, for example:

```bash
check health curl -fsS --max-time 10 "http://$UNIT_HOST:8080/health"
```

Then run everything:

```bash
bash tests/run_tests.sh
```

**What you see.** One `PASS` or `FAIL` line per test, in the section Partner tests of the report.

**The decision.** Every check passed: approve. Any check failed: reject.

```bash
python verify_batch.py decide --batch-id "$BATCH" --approve
```

## C2. Crash on start

**What it checks.** A Service version that does not start is rejected without a person.

**How.** AosCloud reports the instance of the new Service version as failed: `wait` exits 1, and the built-in checks read it again.

**What you see.** `Verification batch …: Unit … failed`; in the report `C2 the Service did not crash on start FAIL`, with the
error AosCloud gives.

**The decision.** Reject, without partner tests.

```bash
python verify_batch.py decide --batch-id "$BATCH" --reject
```

## C3. Late failure

**What it checks.** The Service version still runs some time after it started.

**How.** The observation, on by default: every 60 s for `WATCH_SECONDS` (default 60) it runs `verify_batch.py wait` again
with `--timeout 60` on every Unit. To observe longer:

```bash
WATCH_SECONDS=180 bash tests/run_tests.sh
```

In the job, set the variable `AOS_WATCH_SECONDS` (`0` turns it off). The observation reads the Units from AosCloud, so the
step of the built-in checks has the certificate; partner tests run in another step, with no secret.

**What you see.** `PASS C3/C4 still running after 60 s, no restarts`, or `FAIL C3/C4`: "the Service version stopped running in round … of …".

**The decision.** A failure in any round rejects the Service version. If AosCloud cannot be reached or a Unit goes offline,
nothing is decided.

## C4. Restarts

**What it checks.** The Service version does not keep crashing. Memory is not measured: the report says so.

**How.** When AosCloud reports the instance as failed, `wait`, the built-in C2 and the observation see it. The observation reads once
every 60 s, so a Service version that is killed and restarted between two reads can look fine: add a test of your own to `tests/your_tests.sh`, for example the
uptime that your Service reports:

```bash
uptime_ok() { [ "$(curl -fsS --max-time 10 "http://$UNIT_HOST:8080/uptime")" -ge 60 ]; }
check no_restarts uptime_ok
```

**What you see.** `PASS no_restarts`, or `FAIL no_restarts`, in Partner tests.

**The decision.** Reject, even when the other checks pass.

## E1. Bad configuration

**What it checks.** The settings that ship with the Service version are the ones you expect.

**How.** A config test in `tests/your_tests.sh`: ask the Service which settings it loaded, and compare.

```bash
config_ok() { curl -fsS --max-time 10 "http://$UNIT_HOST:8080/config" | grep -q '"feature_mode": *"normal"'; }
check config_loaded config_ok
```

**What you see.** `PASS config_loaded`, or `FAIL config_loaded`, in Partner tests.

**The decision.** Reject. The code may be fine; fix the setting and upload a new Service version.

## E2. Recovery: the fixed Service version

**What it checks.** The fix goes through the same gate as every other Service version.

**How.** Upload the fix with a new Service version number (higher than the rejected one), and run `./aos-verify run`
([Run it from your terminal](run-locally.md)). In GitHub the same job runs for it.

```bash
aos-signer upload
```

**What you see.** The same steps as for any Service version: `wait` ends with the Verification batch id, the report says PASS → approve,
`Verification batch … (amd64): approved, now Verified (Valid)`.

**The decision.** Approve: the batch shows Verified, and the Unit runs the fixed Service version.

Two more scenarios, a resource conflict and other Services on the same Unit, need AosCloud 6.2; they are not part of
this example.

**Next →** [API reference with examples](api.md)
