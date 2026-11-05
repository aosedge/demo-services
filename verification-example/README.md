# Verify a new Service version in your pipeline: a minimal example

Start here: open `START_HERE.html` (offline, all pages and the video).

> **Where to start**
> 1. [Put it in GitHub](#put-it-in-github): copy the files, add the secrets, done. This is the real use.
> 2. [Try it on a laptop](docs/run-locally.md): one command sets it up, one command runs it.
>
> Both need the [Prerequisites](#prerequisites): a Service, verification set up in AosCloud.

A new version of your Service goes to the test Units of your Verification Set first. AosCloud holds it there until
someone approves or rejects it. This example does that from GitHub Actions: it waits for the version, runs the
built-in checks (and your tests, when you add them), writes an HTML report, and approves or rejects the version with one
AosCloud API call.

> [!IMPORTANT]
> **Defaults:** the job decides by itself (`AOS_DECISION=auto`). Want a person to decide? Set `AOS_DECISION=report-only`: the job only checks and writes the report, you approve in the AosCloud UI. Or `ask` (laptop only).
>
> Our built-in checks always run (A1, B1, C2, C3/C4). Add your own tests in `tests/your_tests.sh` (start from `tests/your_tests.sh.example`): they run after ours, and a failing test leads to reject.

<details>
<summary>Why every 5 minutes?</summary>

AosCloud does not notify a pipeline when a new version waits (no webhook in 6.1/6.2), so the watch asks every 5 minutes. 5 minutes is the shortest interval GitHub allows; GitHub may start a scheduled run later or skip one under load, so the real delay can be longer. Each run takes about 20 s and only reads. To change it, edit one line in `watch-verification.yml`: `cron: "*/5 * * * *"` → `"*/10 * * * *"` (every 10 min), `"*/30 * * * *"`, `"0 * * * *"` (hourly). GitHub cannot read this from a variable. Faster than 5 min, or exact timing: start `verify-release` right after your upload job (the chain), or use an external scheduler. A webhook from AosCloud would make it instant (feature request).

</details>

![Your upload job uploads a new version; AosCloud holds it in a Verification batch (Waiting verification) and sends it only to the test Units of the Verification Set; the verification job waits, runs your tests and decides once: approve makes it Verified, on to your normal rollout; reject makes it Invalid, and it stops here](docs/img/flow.svg)

Tested end to end from a terminal on production AosCloud 6.1.97 on 30.10.2026, with the same three steps the job
runs: upload → the version active on the test Unit in about 15 s → tests → approved → Verified.

## How the pipeline knows it is time

AosCloud 6.1 and 6.2 send no notification to a pipeline; a webhook would replace the watch. So the pipeline asks, on a
schedule, with the SP certificate alone. Nobody presses anything.

![1, your upload job uploads a version. 2, AosCloud makes a batch that waits. 3 and 4, the version goes to the test Units, and they report installed and active, or failed. 0, new: about every 5 minutes, best effort, the watch asks AosCloud whether a new version waits, and gets the batch id, the version and the Unit ids; it starts one verify-release run per batch; or your upload job starts it right after the upload. 5, wait checks each Unit. 6, the built-in checks and your tests run. 7, one PATCH approves or rejects, only if the batch still waits](docs/img/sequence.svg)

| Who | Tells whom | What | Where you see it |
|---|---|---|---|
| Your upload job | AosCloud | a new version | the job log; SP UI → Deployment bundles |
| AosCloud | the test Units of the Verification Set | install this version | SP UI → Verification batches: Waiting verification |
| A test Unit | AosCloud | installed, and active or failed | SP UI → Unit → Subjects & Services → Service Instances |
| AosCloud, when the watch asks | the watch | the batch id, the version, the Unit ids of the Set | the log of `watch-verification`; the title of the `verify-release` run |
| The verification job | AosCloud | one decision: approve or reject | the report; SP UI: Verified or Invalid |

- **When to start, and which version:** the highest version of your Service, if its batch waits.
- **Which Set:** the test Units are the Units of the Verification Set that have your Service.
- **Which tests:** `tests/run_tests.sh` in your repository: the built-in checks, then your tests.
- **Did the deployment reach the whole Set?** `wait` asks every test Unit: online, installed, with an active instance. One failed Unit rejects the version; a Unit that is offline or never gets it ends in a timeout and no decision.

Two automatic starts, no buttons:

- **The chained start:** your upload job calls the verification right after the upload, in the same workflow.
- **The watch:** it asks AosCloud itself and finds any upload, wherever it came from. GitHub runs a schedule on a best-effort basis, so the watch can be late.

Both end in the same `verify-release`, in one queue per version, and cannot double-decide: a second run waits for the first, finds the batch decided, and sends nothing.

Note: `discover` ran read-only on production 6.1.97 and on staging 6.2.31 on 04.11.2026, and the upload-to-decision
steps ran on production on 30.10.2026. The scheduled watch itself has so far run only against a mock.

## What is in this repository

Files: [`aos-verify`](aos-verify) (setup and one command, for a laptop), [`verify_batch.py`](verify_batch.py) (about 500 lines, Python 3.10+ and `requests`),
[`tests/run_tests.sh`](tests/run_tests.sh) (the built-in checks, then your tests) with [`tests/report.py`](tests/report.py)
(the HTML report) and [`tests/your_tests.sh.example`](tests/your_tests.sh.example),
[`.github/workflows/watch-verification.yml`](.github/workflows/watch-verification.yml) (the watch),
[`.github/workflows/verify-release.yml`](.github/workflows/verify-release.yml) (the job) and
[`examples/release-caller.yml`](examples/release-caller.yml).

## Put it in GitHub

1. **Copy the two workflow files into your repository**, with the code they run. From the folder of this example:

   ```bash
   cp -R .github verify_batch.py tests /path/to/your-repo/
   ```

2. **Add the secrets.** One helper prints exactly what to paste. It makes the PEM from your `.p12`, finds the root CA and
   your Service (the first time it asks, as `setup` does), and never prints the key:

   ```bash
   ./aos-verify secrets --repo your-org/your-repo
   ```

   ```text
   gh api -X PUT repos/your-org/your-repo/environments/aos-verification
   gh secret set AOS_USER_PEM --repo your-org/your-repo --env aos-verification < '…/.aos-verify/user.pem'
   gh secret set AOS_ROOT_CA_PEM --repo your-org/your-repo --env aos-verification < '…/.aos-verify/aos-root-ca.crt'
   gh secret set AOS_SERVICE_ID --repo your-org/your-repo --env aos-verification < '…/.aos-verify/service_id'
   gh secret set AOS_API_URL --repo your-org/your-repo --env aos-verification --body 'https://api.aoscloud.io:10000/api/v11'
   ```

   Creating the environment needs admin rights on the repository.

3. **Commit to the default branch.** Done. The watch finds each new version by itself, and
   [`verify-release`](.github/workflows/verify-release.yml) checks it and decides, by itself
   ([Automatic decision](#automatic-decision) says how to change that). Add your own tests in
   [`tests/your_tests.sh`](docs/tests.md).

## How to use this repository

1. [Put it in GitHub](#put-it-in-github): the workflow files and the secrets.
2. [Try it on a laptop](docs/run-locally.md): `./aos-verify setup`, upload, `./aos-verify run`.
3. [Prerequisites](#prerequisites): a Service, verification set up in AosCloud, a version uploaded.
4. [What the job does](#the-verification-job-in-3-steps), and its [parameters](#parameters).
5. [Your tests](docs/tests.md): what `tests/run_tests.sh` checks, the report, and how to add your own tests.
6. [Example checks](docs/checks.md): 12 scenarios we ran on production, step by step, if you have no tests yet.
7. [API reference with examples](docs/api.md): every call, with `curl`.
8. [FAQ and tips](docs/faq.md).

## Prerequisites

### 1. A Service that your pipeline already deploys (SP)

1. The Service exists in AosCloud for your Service Provider (SP) account: SP UI → Services, or
   `POST services/ {title, codename, default_quotas}`
   ([services_create](https://api.aoscloud.io/api/v11/docs#/services/services_create)).
2. Note its **Service id**. The job finds the version by this id. It is the `id` of the item with your `codename` in
   `GET services/?search=<codename>` ([find the Service id](docs/api.md#find-the-service-id)).
3. Your pipeline signs and uploads it with `aos-signer`, using the SP user certificate (`.p12`).

### 2. Verification configured (OEM)

The OEM account does these steps once. The new version then goes to these Units first, and to no other Unit.

1. **Create the Verification Set**: a Unit Set with **Verification set = YES**, in the fleet of the test Units.
   OEM UI → Unit-sets → +, or `POST unit-sets/ {title, is_validation_set: true, fleet, description}`
   ([unit_sets_create](https://api.aoscloud.io/api/v11/docs#/unit-sets/unit_sets_create)).

   ![The Unit Sets list: the Verification Set has Verification set = YES](docs/img/01-oem-unit-sets.png)

2. **Add the test Units** to it: Add unit, or `POST unit-sets/{id}/units/ {system_uids}`
   ([unit_sets_units_create](https://api.aoscloud.io/api/v11/docs#/unit-sets/unit_sets_units_create)).
   The Units must be provisioned and online.

   ![The Verification Set with its test Unit](docs/img/02-oem-verification-set.png)

3. **Create a Subject for the test Units**: a group Subject, priority 0, with the test Units assigned.
   `POST subjects/ {label, is_group: true, priority: 0}`
   ([subjects_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_create)), then
   `POST subjects/{id}/units/ {system_uids}`
   ([subjects_units_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_units_create)).

   ![The Subject with the test Unit assigned](docs/img/03-oem-subject-unit.png)

4. **Give your Service to the Subject**: Services tab → Add service, or `POST subjects/{id}/services/ {service_ids}`
   ([subjects_services_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_services_create)).
   Without this link the version is never installed on the test Units.

   ![The Subject with the Service given to it](docs/img/04-oem-subject-service.png)

You do not need to give the job the ids of the test Units: with the SP certificate it reads them from each batch
([which Units](docs/api.md#the-units-of-the-verification-set)). The SP cannot read Unit Sets directly.

### 3. A version uploaded, so that a Verification batch exists (SP)

1. In the `config.yaml` of the Service, these parameters matter:

   | Parameter | Why |
   |---|---|
   | `items[].identity.codename` | the codename of your Service |
   | `items[].identity.id` (optional) | your Service id: the upload adds a version to that Service |
   | `items[].version` | the new version. It must be higher than the last one and never uploaded before |
   | `items[].images[].archInfo.architecture` | what the image is built for. The batch lists the architectures it needs an approval for: for example, `amd64` for an image built for `any`. `decide` sends one approval for each |
   | `publish.tlsKey` | the path of the SP `.p12`. Keep the file out of the repository |

2. Run `aos-signer sign` and `aos-signer upload` in the folder of the Service.
3. SP UI → Deployment bundles: the version is **Done**. An **Error** such as "Item version … already exists" means
   that the version was reused: raise it.

   ![Deployment bundles: version 4.5.0 is Done](docs/img/05-sp-deployment-bundles.png)

4. SP UI → Verification batches: AosCloud creates one batch per uploaded version. It waits for a decision, with no
   time limit: the API state is `Waiting_validation`, and the UI shows **Waiting verification**.

   ![Verification batches: the new version 4.6.0 is Waiting verification; older versions are Verified or Invalid](docs/img/10-sp-verification-batch-waiting.png)

   ![The waiting batch: its architecture amd64 is not decided yet](docs/img/11-sp-batch-waiting-detail.png)

## The verification job in 3 steps

All calls use mutual TLS with the SP certificate on `https://<api host>:10000/api/v11/`. Each call links to its
description, with a `curl` example and a sample response, in the [API reference](docs/api.md).

### 1. Wait: `verify_batch.py wait`

It finds the batch of this Service and version, takes the test Units from AosCloud (or from the `units` input or
`AOS_TEST_UNITS`, if you set them), then waits until the version runs on every test Unit.

- `GET verification-batch/?ordering=-created_at&limit=50&offset=<n>`, page by page: [find the batch](docs/api.md#find-the-batch)
- `GET verification-batch/{batch_id}/`: [check that it still waits](docs/api.md#read-the-batch)
- `GET verification-batch/{batch_id}/statistics/` and `GET services/{service_id}/units/`: [the test Units](docs/api.md#the-units-of-the-verification-set).
  Units you give must be in the Set, and every architecture of the batch needs one, because the decision covers them all
- `GET units/{unit_id}/`: [is the test Unit online](docs/api.md#is-the-unit-online)
- `GET units/{unit_id}/subjects-services/{service_id}/`: [the version on each test Unit](docs/api.md#read-the-version-on-a-unit)

Exit 0: ready, and the batch id is printed. Exit 1: the version failed to start on a Unit, the batch id is printed,
and the job rejects. Exit 2: timed out, no batch, the batch is already decided, or an error that waiting cannot fix
(a setting, a TLS error, an HTTP 4xx answer); the job decides nothing. Exit 3: timed out while AosCloud could not be
reached or a test Unit was offline; the job decides nothing. Only a lost connection, a timeout and HTTP 5xx are retried.

![The test Unit runs the new version (Unit → Subjects & Services → Service Instances)](docs/img/09-sp-unit-service-instances.png)

### 2. Test: `tests/run_tests.sh`

The built-in checks run first: they read the batch and the Units again (exactly this version, on every Unit, no crash) and
observe the version for 60 s (late failure, restarts). Then your tests, from `tests/your_tests.sh` when you add it
([Your tests](docs/tests.md)), in a step of their own, with no secret. Exit 0 when every check passed, 1 when a check
failed, 3 when AosCloud could not be read or a test Unit is offline: neither decides anything. It writes `report/report.html` and
`report/junit.xml`; the job keeps them, with the decision written into the report: open the run page → **Artifacts** →
**verification-report**.

### 3. Decide: `verify_batch.py decide --approve` or `--reject`

It reads the batch once more, refuses unless the state is `Waiting_validation` (UI: Waiting verification), prints
the architectures it decides (all of the batch), and sends one call, with no retry:

- `PATCH verification-batch/{batch_id}/` with `[{"architecture": "amd64", "is_approved": true}]`:
  [approve or reject](docs/api.md#approve-or-reject)

If your test Units do not cover every architecture of the batch, pass `--arch` for the ones you tested.

Approved: the batch becomes **Valid** (**Verified** in the UI): the version passed the gate, and it goes on to the
fleet through your normal rollout. Rejected: **Invalid**, and it stops here.

| Approved | Rejected |
|---|---|
| ![A Verified batch](docs/img/07-sp-batch-verified.png) | ![An Invalid batch](docs/img/08-sp-batch-invalid.png) |

## Automatic decision

One setting, `AOS_DECISION`, says what happens after the checks:

| Value | What happens | When to use it |
|---|---|---|
| `auto` | all checks pass: approve; a check fails: reject. The default in GitHub | you trust the checks, and nobody has to press anything |
| `ask` | it asks before it sends the decision. The default on a laptop; in GitHub there is no terminal to ask in, so it sends nothing | you run it by hand, and want to read the report first |
| `report-only` | the report and `junit.xml` only, no decision sent; a person decides in SP UI → Verification batches | you do not want the pipeline to release by itself. GitHub required reviewers are not on every plan; this works on all of them |

Reject is automatic too: with `auto`, a version that fails is rejected with no person involved.

In GitHub, set the variable `AOS_DECISION` in the environment `aos-verification`:

```bash
gh variable set AOS_DECISION --repo your-org/your-repo --env aos-verification --body report-only
```

On a laptop, `./aos-verify setup` asks once and keeps your answer in `.aos-verify.env`. `AOS_DECISION` in your environment
wins over that for one run, and `./aos-verify run --yes` is the short form of `auto`.

With `report-only` the job is still green or red: green when all checks pass, red when one fails.

The mode in use is on the first line of `./aos-verify run`, on the first line of the GitHub job summary, and in the header of the report.

> **Next**
> - [Your tests](docs/tests.md): what `tests/run_tests.sh` checks, the report, and how to add your own tests
> - [Example checks](docs/checks.md): 12 scenarios we ran on production, with the code
> - [API reference with examples](docs/api.md): every call, with `curl`, sample responses and errors
> - [FAQ and tips](docs/faq.md): decisions, running it, connection, security

## Start it by itself: the watch

Copy this file to `.github/workflows/watch-verification.yml` on the default branch: GitHub runs a schedule only from
there. It needs three settings, from [Parameters](#parameters):

1. `AOS_API_URL`, a secret or a variable.
2. The secret `AOS_SERVICE_ID`: a secret, so that GitHub masks it in the log.
3. The secrets `AOS_USER_PEM` and `AOS_ROOT_CA_PEM`, in the environment `aos-verification`.

`./aos-verify secrets` prints the commands for all of them: [Put it in GitHub](#put-it-in-github).

`AOS_TEST_UNITS` is no longer required: the job takes the Units from AosCloud.

```yaml
# Ask AosCloud on a schedule whether a new version waits on the Verification Set, and start one verification run for it.
# AosCloud sends no notification to a pipeline, so this job asks; nobody has to press anything.
name: watch-verification

on:
  schedule:
    # AosCloud does not notify a pipeline when a new version waits (no webhook in 6.1/6.2), so the watch asks
    # every 5 minutes. 5 minutes is the shortest interval GitHub allows; GitHub may start a scheduled run
    # later or skip one under load, so the real delay can be longer. Each run takes about 20 s and only reads.
    # To change it, edit one line in watch-verification.yml: cron: "*/5 * * * *" → "*/10 * * * *" (every 10
    # min), "*/30 * * * *", "0 * * * *" (hourly). GitHub cannot read this from a variable. Faster than 5 min,
    # or exact timing: start verify-release right after your upload job (the chain), or use an external
    # scheduler. A webhook from AosCloud would make it instant (feature request).
    - cron: "*/5 * * * *"
  workflow_dispatch:

concurrency:
  group: watch-verification
  cancel-in-progress: false

permissions:
  actions: write
  contents: read

jobs:
  watch:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    # the secrets live in this environment; required reviewers on it would stop every run of this job (see the FAQ)
    environment: aos-verification
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false

      - name: Say why it asks on a schedule
        run: echo "Why every 5 minutes? AosCloud does not notify a pipeline, so the watch asks; 5 minutes is the shortest interval GitHub allows. To change it, edit the cron line in watch-verification.yml." >> "$GITHUB_STEP_SUMMARY"

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install requests
        run: python -m pip install requests==2.32.3

      # The only step with the private key. It reads AosCloud, and it deletes the key when it ends.
      - name: 0. Ask AosCloud for a waiting batch
        id: discover
        env:
          AOS_API_URL: ${{ secrets.AOS_API_URL || vars.AOS_API_URL }}
          SERVICE_ID: ${{ secrets.AOS_SERVICE_ID }}
          TEST_UNITS: ${{ vars.AOS_TEST_UNITS }}
          USER_PEM: ${{ secrets.AOS_USER_PEM }}
          ROOT_CA: ${{ secrets.AOS_ROOT_CA_PEM }}
        run: |
          if [ -z "$USER_PEM" ] || [ -z "$ROOT_CA" ]; then
            echo "::error::AOS_USER_PEM or AOS_ROOT_CA_PEM is empty: put them in the environment aos-verification"
            exit 1
          fi
          units=()
          set -f
          for unit in $TEST_UNITS; do units+=(--unit "$unit"); done
          set +f
          dir="$RUNNER_TEMP/aos" && mkdir -p "$dir" && chmod 700 "$dir"
          trap 'rm -rf "$dir"' EXIT
          (umask 077 && printf '%s\n' "$USER_PEM" > "$dir/user.pem" && printf '%s\n' "$ROOT_CA" > "$dir/aos-root-ca.crt")
          export AOS_CERT="$dir/user.pem" AOS_CA="$dir/aos-root-ca.crt"
          python verify_batch.py discover "${units[@]}"

      # No key here. A run with this batch in its title, in any state, means the batch was handled: a run that
      # timed out is not repeated every 5 minutes. Start it again by hand, with the version.
      - name: Start one verification run for the batch
        if: steps.discover.outputs.batch_id != ''
        env:
          GH_TOKEN: ${{ github.token }}
          GH_REPO: ${{ github.repository }}
          BATCH_ID: ${{ steps.discover.outputs.batch_id }}
          VERSION: ${{ steps.discover.outputs.version }}
          UNITS: ${{ steps.discover.outputs.units }}
        run: |
          runs=$(gh run list --workflow verify-release.yml --limit 200 --json displayTitle,status)
          seen=$(jq -r --arg batch "$BATCH_ID" --arg version "$VERSION" '
            [.[] | (.displayTitle | split(" ")) as $t
             | select($t[1] == $batch or ($t[1] == "manual" and $t[2] == $version)) | .status] | first // empty' <<<"$runs")
          if [ -n "$seen" ]; then
            echo "batch $BATCH_ID, version $VERSION already has a run ($seen): nothing to start"
            exit 0
          fi
          echo "starting verify-release for batch $BATCH_ID, version $VERSION, Units $UNITS"
          gh workflow run verify-release.yml --ref "$GITHUB_REF_NAME" -f version="$VERSION" -f batch_id="$BATCH_ID" -f units="$UNITS"
```

To start the verification right after an upload in the same workflow, call `verify-release.yml` from it, as in
[`examples/release-caller.yml`](examples/release-caller.yml). You can use both.

## Parameters

`./aos-verify secrets` prints the commands for the four required values: [Put it in GitHub](#put-it-in-github). By hand:
create the environment `aos-verification`, put the secrets there, and, if you want a person to confirm, add required
reviewers (repository Settings → Environments) or set `AOS_DECISION` to `report-only`. The variables go in Settings → Secrets and variables → Actions.

| Name | Kind | Value |
|---|---|---|
| `AOS_API_URL` | secret or variable | `https://api.aoscloud.io:10000/api/v11`, or your API host. A secret wins over a variable of the same name |
| `AOS_USER_PEM` | secret | one PEM file with the SP user certificate, the intermediate certificates and the key (see below) |
| `AOS_ROOT_CA_PEM` | secret | the AosEdge root CA file `aos-root-ca.crt` (from `aos-keys`, see below; it is not secret) |
| `AOS_SERVICE_ID` | secret | your Service id; it is not secret, but GitHub masks a secret in the log |
| `AOS_TEST_UNITS` | variable | optional: test only these Units of the Verification Set; ids separated by spaces. A Unit you leave out is not checked. The job stops if one is outside the Set, or if an architecture of the batch has none |
| `AOS_DECISION` | variable | `auto` (default), `ask` or `report-only`: [Automatic decision](#automatic-decision) |
| `AOS_WATCH_SECONDS` | variable (on a laptop: environment) | how long the built-in checks observe the version on the test Units; default `60`, `0` turns it off. See [Example checks](docs/checks.md#c3-late-failure) |
| `AOS_WAIT_TIMEOUT` | variable (on a laptop: environment) | seconds to wait for the version on the Units; default `1800`. The job stops after 60 minutes |
| `version` | workflow input | the version to verify; to start the job by hand, this is the only input you need |
| `service_id`, `batch_id`, `units` | workflow inputs | optional: the Service (default `AOS_SERVICE_ID`), the batch (default found by the version), the test Units, separated by spaces |

What `./aos-verify secrets` does for the certificate, by hand: your SP user certificate is a `.p12` that `aos-keys` saved under `~/.aos/security/` (one per AosCloud and role;
`find ~/.aos/security -name "*.p12"` lists them). Put the path of the SP one of your AosCloud into `AOS_P12`, as in
[Run it from your terminal, appendix, step 3](docs/run-locally.md#3-your-certificate). `.p12` is a password-protected bundle;
Python `requests` and `curl` need PEM files. Turn it into one PEM file that holds the certificate, the intermediate
certificates and the key. Do not add `-clcerts`: without the intermediate certificates AosCloud refuses the
connection with `unknown ca`. Store the file as the secret, and delete it. Press Enter if you are asked for the
password of the file:

```bash
openssl pkcs12 -in "$AOS_P12" -nodes -out user.pem
```

```bash
gh secret set AOS_USER_PEM --env aos-verification < user.pem && rm user.pem
```

The AosEdge root CA comes with `aos-keys`, in the folder where the AosEdge docs install the tools:
`~/.aos/venv`, file `aos_keys/files/1rootCA.crt` (CN AosEdge Root CA). Copy it as `aos-root-ca.crt`:

```bash
cp "$(~/.aos/venv/bin/python3 -c 'import aos_keys, pathlib; print(pathlib.Path(aos_keys.__file__).parent / "files" / "1rootCA.crt")')" aos-root-ca.crt
gh secret set AOS_ROOT_CA_PEM --env aos-verification < aos-root-ca.crt
```

## Security

The certificate secret holds the private key of your SP user. The job reads it from the environment
`aos-verification` and writes it to a folder of the runner that only the job can read, only for the steps that call
AosCloud: the watch's discover step, wait, the built-in checks and the decision. Your tests (`tests/your_tests.sh`) run
in a step of their own, with no secret and no key file. Each step deletes the file when it ends. The job masks the
batch and Unit ids in its log; [what stays visible](docs/faq.md#security) matters in a public repository. More: [FAQ, Security](docs/faq.md#security).

## FAQ

- **Who may approve?** The SP. **A Unit never gets the version?** The job decides nothing.
- **Valid or Verified?** The same. **What does the job never do?** Decide twice, or change a decided batch.
- **Why every 5 minutes? Why not instant? What about old batches?** See [Timing and old batches](docs/faq.md#the-watch-timing-and-old-batches).
- More answers and tips, from proxies to `aos-signer` output in CI logs: [FAQ and tips](docs/faq.md).

## Licence

Apache-2.0, see [LICENSE](LICENSE).

**Next →** [Your tests](docs/tests.md), then [Example checks](docs/checks.md), [API reference with examples](docs/api.md) and [FAQ and tips](docs/faq.md)
