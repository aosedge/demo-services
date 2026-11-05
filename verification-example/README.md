# Automatic verification of a new Service version

AosCloud installs a new Service version only on the Verification Set and then waits for an approval. It does not tell a pipeline that a version waits, so today a person has to notice and approve by hand. This example closes that step: a CI job learns that a Service version waits, checks that it runs on the test Units, runs your tests, and approves or rejects it in AosCloud, with a report for every release. Nothing reaches the fleet before the decision. When the job cannot be sure (a Unit offline, a timeout, an error) it decides nothing, and you can keep the decision for a person.

GitHub Actions is the example; the logic is one Python file and one shell script, so it runs in any CI with Python 3.10 that reaches AosCloud (see [docs/run-locally.md](docs/run-locally.md)).

## How the job knows it is time

| Question | Answer |
|---|---|
| Which Service version? | The one the upload job just uploaded: it passes `version` (Start A). Fallback: the watch asks for the highest Service version that is Waiting verification (Start B) |
| Which Units? | AosCloud picks the Verification Set. The job reads its Units from the batch and keeps those that have the Service: the test Units |
| Which tests? | The built-in checks, then the partner tests |
| Did the deployment succeed? | Each test Unit: online, Service version installed, instance active. One failed Unit rejects the Service version; a Unit offline or never updated ends in a timeout and no decision |

The calls: six GETs and one PATCH, see [API reference](docs/api.md).

![Automatic verification of a new Service version. Step 1: the upload job, an existing step, uploads the Service version. Step 2: AosCloud creates a Verification batch, state Waiting verification, and installs the new Service version only on the Units of the Verification Set. Steps 3 and 4: the Service version is installed, and the Units report. Two starts. Start A, chain, the main start: the upload job starts verify-batch right after the upload. Start B, watch, the fallback, step 0: watch-batches.yml polls AosCloud every 5 minutes: is a new Service version waiting for verification? It starts verify-batch when it finds a waiting batch. AosCloud sends no notification, so the watch asks; 5 minutes is GitHub's minimum, best effort. verify-batch.yml: 5 Wait until the Service version is active on every test Unit; a timeout or an offline Unit means no decision. 6 Test: built-in checks and partner tests, report.html and junit.xml. 7 Decide: auto, the default, the job approves or rejects; report-only, a person decides in the SP UI. Verified goes on to the normal rollout; Invalid: the rollout stops.](docs/img/flow.svg)

[Open the diagram full size](docs/img/flow.svg)

## It ran on production

| Date | Where | What ran | Result |
|---|---|---|---|
| 06.11.2026 | GitHub | A fresh fork, only a browser: `demo-upload`, pressed once, uploaded 4.11.2 and started `verify-batch` | Verified, the `verify-batch` run took 1 min 22 s |
| 05.11.2026 | GitHub | `demo-upload`, pressed once: it uploaded 4.11.1 and started `verify-batch` (Start A) | Verified about 1.5 min after the upload, with no further step |
| 05.11.2026 | GitHub | Start B: the watch, started by hand, found 4.11.0 | Verified in 1 min 23 s |
| 30.10.2026 | Terminal | The same three steps | Active on the test Unit in about 15 s, Verified |

Production AosCloud 6.1.97.

## Limits

- AosCloud has no webhook, so the job is started by your upload job (Start A) or asks on a schedule (Start B).
- GitHub runs schedules on a best-effort basis; it has not yet started the watch in this repository. Use Start A as the main start.
- AosCloud accepts a second decision on a decided batch. The job reads the state right before its one call; two pipelines on one Service are not locked against each other.
- A test Unit that is offline means no decision: a person has to look.
- Not yet run on GitHub: a reject, report-only, Start A through `workflow_call` (`demo-upload` starts the job with a dispatch).
- AosCloud versions: proven on 6.1.97; on 6.2.31 only `discover` ran.
- The watch handles one Service per repository.

More on each: [FAQ, the watch](docs/faq.md#the-watch-timing-and-old-verification-batches).

## Step by step

| # | Step | Runs where | What is checked / decided | Where you see it |
|---|---|---|---|---|
| ① | Upload (existing step) | CI pipeline | New Service version sent | SP UI: Deployment bundles |
| ② | Verification batch created | AosCloud | State: Waiting verification | SP UI: Verification batches |
| ③④ | Service version installed, then reported | Verification Set Units | Installed, active or failed | SP UI: Unit → Subjects & Services |
| ⓪ | Start A (main), or Start B: the watch asks every 5 min (fallback) | upload job, or `watch-batches.yml` | New Service version waiting? | Job log |
| ⑤ | Wait | `verify-batch.yml` | test Units active | Job log |
| ⑥ | Test | `verify-batch.yml` | Built-in checks + partner tests | The run page: the whole report |
| ⑦ | Decide | `verify-batch.yml` | `auto`: the job. `report-only`: a person | The run page; job log |
| – | Result | AosCloud | Verified / Invalid | SP UI: Verification batches |

**Try it yourself, in a browser:** fork the repository, add 4 secrets, press one button. See [Run it yourself in 5 steps](https://github.com/aosedge/demo-services/blob/main/verification-example/TRY_IT.md).

> [!IMPORTANT]
> **Defaults:** the job decides by itself (`AOS_DECISION=auto`). For a decision by a person, set `AOS_DECISION=report-only`: the job only checks and writes the report, and a person approves or rejects in the SP UI. Or `ask` (laptop only).
>
> Built-in checks run on every Service version ([what they check](docs/tests.md)). Add your own tests in `tests/your_tests.sh` (start from `tests/your_tests.sh.example`): they run after the built-in checks. If any check or test fails, the verdict is reject; in `auto` mode the job sends it, in `report-only` a person decides.

## How to use this repository

1. [Set it up in GitHub](#set-it-up-in-github): who does what, the workflow files and the secrets.
2. [Try it on a laptop](docs/run-locally.md): `./aos-verify setup`, upload, `./aos-verify run`.
3. [Prerequisites](#prerequisites): a Service, verification set up in AosCloud, a Service version uploaded.
4. [What the job does](#the-verification-job-in-3-steps), and its [parameters](#parameters).
5. [Who tells whom](docs/who-tells-whom.md): the sequence, call by call.
6. [Partner tests](docs/tests.md): what `tests/run_tests.sh` checks, the report, and how to add your own tests.
7. [Example checks](docs/checks.md): 12 scenarios we ran on production, step by step, if you have no tests yet.
8. [API reference with examples](docs/api.md): every call, with `curl`.
9. [FAQ and tips](docs/faq.md).

## Set it up in GitHub

Who has to act before the first run:

| What | Who |
|---|---|
| Verification Set, test Units, Subject with your Service | OEM, once |
| A CI user with 9 permissions and its certificate | SP, once (a user who may create users) |
| Actions enabled; environment `aos-verification` | GitHub organisation owner; repository admin |
| Two workflow files, `verify_batch.py`, `tests/`, four secrets | You |

1. **Copy the two workflow files into your repository**, with the code they run. From the folder of this example:

   ```bash
   mkdir -p /path/to/your-repo/.github/workflows
   cp .github/workflows/verify-batch.yml .github/workflows/watch-batches.yml /path/to/your-repo/.github/workflows/
   cp -R verify_batch.py tests /path/to/your-repo/
   ```

   Demo only, not for your repository: `.github/workflows/demo-upload.yml`, `demo-service/`, `TRY_IT.md`.

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

3. **Start it from your upload workflow (Start A).** Add the `verify` job of
   [`examples/release-caller.yml`](examples/release-caller.yml) after your `aos-signer upload` step.

4. **Commit to the default branch.** The watch (Start B) also finds uploads made elsewhere, when GitHub runs its
   schedule. [`verify-batch`](.github/workflows/verify-batch.yml) checks the Service version and decides, by itself
   ([Automatic decision](#automatic-decision) says how to change that). Add your own tests in
   [`tests/your_tests.sh`](docs/tests.md).

## Prerequisites

### Before you start

GitHub Actions must be enabled for the repository. Organisation owners can switch Actions off for a repository or allow only selected actions; a maintainer cannot see this setting. Workflows used: actions/checkout, actions/setup-python, actions/upload-artifact.

### A. A Service that your pipeline already deploys (SP)

1. The Service exists in AosCloud for your Service Provider (SP) account: SP UI → Services, or
   `POST services/ {title, codename, default_quotas}`
   ([services_create](https://api.aoscloud.io/api/v11/docs#/services/services_create)).
2. Note its **Service id**. The job finds the Service version by this id. It is the `id` of the item with your `codename` in
   `GET services/?search=<codename>` ([find the Service id](docs/api.md#find-the-service-id)).
3. Your pipeline signs and uploads it with `aos-signer`, using the SP user certificate (`.p12`).
4. **A CI user for the job**: an SP user of its own, so that its certificate can be revoked alone. It needs these
   permissions: `verification_batch_list`, `verification_batch_read`, `verification_batch_statistics`,
   `verification_batch_approval`, `services_units_list`, `units_read`, `units_subjects_services_read`, `users_me`,
   `user_certificates_create`. Add `deployment_bundles_create` only if the same user uploads, as `demo-upload` does.
   `POST users/` also gives it the group `sp_management`; take it away with `PATCH users/{id}/ {"permission_groups": []}`.

### B. Verification configured (OEM)

The OEM account does these steps once. The new Service version then goes to these Units first, and to no other Unit.

1. **Create the Verification Set**: a Unit Set with **Verification set = YES**, in the fleet of its Units.
   OEM UI → Unit-sets → +, or `POST unit-sets/ {title, is_validation_set: true, fleet, description}`
   ([unit_sets_create](https://api.aoscloud.io/api/v11/docs#/unit-sets/unit_sets_create)).

   ![The Unit Sets list: the Verification Set has Verification set = YES](docs/img/01-oem-unit-sets.png)

2. **Add the Verification Set Units** to it: Add unit, or `POST unit-sets/{id}/units/ {system_uids}`
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
   Without this link the Service version is never installed on the test Units.

   ![The Subject with the Service given to it](docs/img/04-oem-subject-service.png)

You do not need to give the job the ids of the test Units: with the SP certificate it reads them from each Verification batch
([which Units](docs/api.md#the-units-of-the-verification-set)). The SP cannot read Unit Sets directly.

### C. A Service version uploaded, so that a Verification batch exists (SP)

1. In the `config.yaml` of the Service, these parameters matter:

   | Parameter | Why |
   |---|---|
   | `items[].identity.codename` | the codename of your Service |
   | `items[].identity.id` (optional) | your Service id: the upload adds a Service version to that Service |
   | `items[].version` | the new Service version. It must be higher than the last one and never uploaded before |
   | `items[].images[].archInfo.architecture` | what the image is built for. The batch lists the architectures it needs an approval for: for example, `amd64` for an image built for `any`. `decide` sends one approval for each |
   | `publish.tlsKey` | the path of the SP `.p12`. Keep the file out of the repository |

2. Run `aos-signer sign` and `aos-signer upload` in the folder of the Service.
3. SP UI → Deployment bundles: the Service version is **Done**. An **Error** such as "Item Service version … already exists" means
   that the Service version was reused: raise it.

   ![Deployment bundles: Service version 4.5.0 is Done](docs/img/05-sp-deployment-bundles.png)

4. SP UI → Verification batches: AosCloud creates one batch per uploaded Service version. It waits for a decision, with no
   time limit: the API state is `Waiting_validation`, and the UI shows **Waiting verification**.

   ![Verification batches: the new Service version 4.6.0 is Waiting verification; older Service versions are Verified or Invalid](docs/img/10-sp-verification-batch-waiting.png)

   ![The waiting batch: its architecture amd64 is not decided yet](docs/img/11-sp-batch-waiting-detail.png)

## The verification job in 3 steps

All calls use mutual TLS with the SP certificate on `https://<api host>:10000/api/v11/`. Each call links to its
description, with a `curl` example and a sample response, in the [API reference](docs/api.md). The numbers 5, 6 and 7
are those of the diagram.

### 5. Wait: `verify_batch.py wait`

It finds the Verification batch of this Service and Service version, takes the Verification Set Units from AosCloud (or from the `units` input or
`AOS_TEST_UNITS`, if you set them), then waits until the Service version runs on every test Unit.

- `GET verification-batch/?ordering=-created_at&limit=50&offset=<n>`, page by page: [find the batch](docs/api.md#find-the-verification-batch)
- `GET verification-batch/{batch_id}/`: [check that it still waits](docs/api.md#read-the-verification-batch)
- `GET verification-batch/{batch_id}/statistics/` and `GET services/{service_id}/units/`: [the test Units](docs/api.md#the-units-of-the-verification-set).
  Units you give must be in the Set, and every architecture of the batch needs one, because the decision covers them all
- `GET units/{unit_id}/`: [is the test Unit online](docs/api.md#is-the-unit-online)
- `GET units/{unit_id}/subjects-services/{service_id}/`: [the Service version on each test Unit](docs/api.md#read-the-service-version-on-a-unit)

Exit 0: ready, and the batch id is printed. Exit 1: the Service version failed to start on a Unit, the batch id is printed,
and the job rejects. Exit 2: timed out, no batch, the batch is already decided, or an error that waiting cannot fix
(a setting, a TLS error, an HTTP 4xx answer); the job decides nothing. Exit 3: timed out while AosCloud could not be
reached or a test Unit was offline; the job decides nothing. Only a lost connection, a timeout and HTTP 5xx are retried.

![The test Unit runs the new Service version (Unit → Subjects & Services → Service Instances)](docs/img/09-sp-unit-service-instances.png)

### 6. Test: `tests/run_tests.sh`

The built-in checks run first: they read the Verification batch and the Units again (exactly this Service version, on every Unit, no crash) and
observe the Service version for 60 s (late failure, restarts). Then partner tests, from `tests/your_tests.sh` when you add it
([Partner tests](docs/tests.md)), in a step of their own, with no secret. Exit 0 when every check passed, 1 when a check
failed, 3 when AosCloud could not be read or a test Unit is offline: neither decides anything. It writes `report/report.html`,
`report/report.md` and `report/junit.xml`, from the same results.

The whole report is on the run page, as the job summary, with the decision in it: the verdict, the Service, the Service
version, the Verification batch, the test Units, every check, the partner tests and the scenarios covered. Nothing to
download. `report.html` and `junit.xml` are under **Artifacts** → **verification-report** at the bottom of the run page.
When nothing ran (a timeout, no Verification batch), the summary says "No decision" and why.

### 7. Decide: `verify_batch.py decide --approve` or `--reject`

It reads the Verification batch once more, refuses unless the state is `Waiting_validation` (UI: Waiting verification), prints
the architectures it decides (all of the batch), and sends one call, with no retry:

- `PATCH verification-batch/{batch_id}/` with `[{"architecture": "amd64", "is_approved": true}]`:
  [approve or reject](docs/api.md#approve-or-reject)

If your test Units do not cover every architecture of the batch, pass `--arch` for the ones you tested.

Approved: the batch becomes **Valid** (**Verified** in the UI): the Service version passed the gate, and it goes on to the
fleet through your normal rollout. Rejected: **Invalid**, and it stops here.

| Approved | Rejected |
|---|---|
| ![A Verification batch, Verified](docs/img/07-sp-batch-verified.png) | ![A Verification batch, Invalid](docs/img/08-sp-batch-invalid.png) |

## Automatic decision

One setting, `AOS_DECISION`, says what happens after the checks:

| Value | What happens | When to use it |
|---|---|---|
| `auto` | all checks pass: approve; a check fails: reject. The default in GitHub | you trust the checks, and nobody has to press anything |
| `ask` | it asks before it sends the decision. The default on a laptop; in GitHub there is no terminal to ask in, so it sends nothing | you run it by hand, and want to read the report first |
| `report-only` | the report and `junit.xml` only, no decision sent; a person decides in SP UI → Verification batches | you do not want the pipeline to release by itself. GitHub required reviewers are not on every plan; this works on all of them |

Reject is automatic too: with `auto`, a Service version that fails is rejected with no person involved.

In GitHub, set the variable `AOS_DECISION` in the environment `aos-verification`:

```bash
gh variable set AOS_DECISION --repo your-org/your-repo --env aos-verification --body report-only
```

On a laptop, `./aos-verify setup` asks once and keeps your answer in `.aos-verify.env`. `AOS_DECISION` in your environment
wins over that for one run, and `./aos-verify run --yes` is the short form of `auto`.

With `report-only` the job is still green or red: green when all checks pass, red when one fails.

The mode in use is on the first line of `./aos-verify run` and in the header of the report, which is also the GitHub job summary.

## The watch (Start B)

The fallback start. [`.github/workflows/watch-batches.yml`](.github/workflows/watch-batches.yml) has to be on the default
branch: GitHub runs a schedule only from there. It needs `AOS_API_URL`, `AOS_SERVICE_ID`, `AOS_USER_PEM` and
`AOS_ROOT_CA_PEM`, from [Parameters](#parameters). `AOS_TEST_UNITS` is optional: the job takes the Units from AosCloud.
Why it asks every 5 minutes, and how to change that: [FAQ, the watch](docs/faq.md#the-watch-timing-and-old-verification-batches).

## Parameters

`./aos-verify secrets` prints the commands for the four required values: [Set it up in GitHub](#set-it-up-in-github). By hand:
create the environment `aos-verification`, put the secrets there, and, if you want a person to confirm, add required
reviewers (repository Settings → Environments) or set `AOS_DECISION` to `report-only`. The variables go in Settings → Secrets and variables → Actions.
How to make the PEM from your `.p12` and where the root CA is:
[Run it from your terminal, appendix, step 3](docs/run-locally.md#3-your-certificate).

| Name | Kind | Value |
|---|---|---|
| `AOS_API_URL` | secret or variable | `https://api.aoscloud.io:10000/api/v11`, or your API host. A secret wins over a variable of the same name |
| `AOS_USER_PEM` | secret | one PEM file with the SP user certificate, the intermediate certificates and the key (see below) |
| `AOS_ROOT_CA_PEM` | secret | the AosEdge root CA file `aos-root-ca.crt` (from `aos-keys`, see below; it is not secret) |
| `AOS_SERVICE_ID` | secret | your Service id; it is not secret, but GitHub masks a secret in the log |
| `AOS_TEST_UNITS` | variable | optional: test only these Units of the Verification Set; ids separated by spaces. A Unit you leave out is not checked. The job stops if one is outside the Set, or if an architecture of the Verification batch has none |
| `AOS_DECISION` | variable | `auto` (default), `ask` or `report-only`: [Automatic decision](#automatic-decision) |
| `AOS_WATCH` | repository variable (not in the environment) | `off` pauses the scheduled watch; not set or `on`: it runs. Not related to `AOS_WATCH_SECONDS` |
| `AOS_WATCH_SECONDS` | variable (on a laptop: environment) | how long the built-in checks observe the Service version on the Verification Set Units; default `60`, `0` turns it off. See [Example checks](docs/checks.md#c3-late-failure) |
| `AOS_WAIT_TIMEOUT` | variable (on a laptop: environment) | seconds to wait for the Service version on the Units; default `1800`. The job stops after 60 minutes |
| `version` | workflow input | the Service version to verify; to start the job by hand, this is the only input you need |
| `service_id`, `batch_id`, `units` | workflow inputs | optional: the Service (default `AOS_SERVICE_ID`), the batch (default found by the Service version), the test Units, separated by spaces |

## Security

The certificate secret holds the private key of your SP user. The job reads it from the environment
`aos-verification` and writes it to a folder of the runner that only the job can read, only for the steps that call
AosCloud: the watch's discover step, wait, the built-in checks and the decision. Partner tests (`tests/your_tests.sh`) run
in a step of their own, with no secret and no key file. Each step deletes the file when it ends. The job masks the
Verification batch and Unit ids in its log; [what stays visible](docs/faq.md#security) matters in a public repository. More: [FAQ, Security](docs/faq.md#security).

## Who maintains this

AosEdge team; contact: the person who sent you this link. From the zip: open `START_HERE.html` (offline, all pages and
the video).

## Licence

Apache-2.0, see [LICENSE](LICENSE).
