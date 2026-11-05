# Automatic verification of a new Service version

AosCloud installs a new Service version only on the Verification Set and then waits for an approval. It does not tell a pipeline that a version waits, so today a person has to notice and approve by hand. This example closes that step: a CI job learns that a Service version waits, checks that it runs on the test Units, runs your tests, and approves or rejects it in AosCloud, with a report for every release. Nothing reaches the fleet before the decision. When the job cannot be sure (a Unit offline, a timeout, an error) it decides nothing, and you can keep the decision for a person.

GitHub Actions is the example; the logic is one Python file and one shell script, so it runs in any CI with Python 3.10 that reaches AosCloud (see [Run it from a terminal](docs/run-locally.md)).

![Automatic verification of a new Service version, in 7 steps. 1, Upload: the upload job, an existing step, uploads the Service version with aos-signer. 2, AosCloud creates a Verification batch, state Waiting verification. 3, the new Service version is installed only on the test Units of the Verification Set, and they report installed, active or failed. 4, Start: A, the main start, the upload job starts verify-batch right after the upload; or B, the fallback, watch-batches.yml polls AosCloud every 5 minutes: is a new Service version waiting for verification? It starts verify-batch when it finds a waiting batch. AosCloud sends no notification, so the watch asks; 5 minutes is GitHub's minimum, best effort. verify-batch.yml, one run per batch: 5 Wait until the Service version is active on every test Unit; a timeout or an offline Unit means no decision. 6 Test: built-in checks and partner tests, report.html and junit.xml. 7 Decide: auto, the default, the job approves or rejects; report-only, a person decides in the SP UI. Verified goes on to the normal rollout; Invalid: the rollout stops.](docs/img/flow.svg)

[Open the diagram at full size](https://raw.githubusercontent.com/aosedge/demo-services/main/verification-example/docs/img/flow.svg)

Tested on AosCloud 6.1.97, 05–06.11.2026: a Service version uploaded from GitHub was Verified about 1.5 min later, with no step in between.

## How the job knows it is time

| Question | Answer |
|---|---|
| Which Service version? | The one the upload job just uploaded: it passes `version` (Start A). Fallback: the watch asks for the highest Service version that is Waiting verification (Start B) |
| Which Units? | AosCloud picks the Verification Set. The job reads its Units from the batch and keeps those that have the Service: the test Units |
| Which tests? | The built-in checks, then the partner tests |
| Did the deployment succeed? | Each test Unit: online, Service version installed, instance active. One failed Unit rejects the Service version; a Unit offline or never updated ends in a timeout and no decision |

The calls: six GETs and one PATCH, see [API reference](docs/api.md).

## Add it to your pipeline

1. **Copy the job into your repository**: `verify_batch.py`, `tests/` and `.github/workflows/verify-batch.yml`, and
   `.github/workflows/watch-batches.yml` if you want the fallback start (the watch).

   ```bash
   mkdir -p /path/to/your-repo/.github/workflows
   cp .github/workflows/verify-batch.yml .github/workflows/watch-batches.yml /path/to/your-repo/.github/workflows/
   cp -R verify_batch.py tests /path/to/your-repo/
   ```

2. **Add the four secrets** to the environment `aos-verification`: `./aos-verify secrets` prints the commands
   ([Secrets](docs/setup.md#secrets)).
3. **Start it after your upload (Start A)**: add this `verify` job to your upload workflow, after the job that runs
   `aos-signer upload` and gives the uploaded version as its output `version`
   (the whole file: [`examples/release-caller.yml`](examples/release-caller.yml)):

   ```yaml
     verify:
       needs: upload
       uses: ./.github/workflows/verify-batch.yml
       with:
         version: ${{ needs.upload.outputs.version }}
       secrets: inherit
   ```

   The job decides by itself; see [decision modes](docs/how-it-works.md#decision-modes).

Before the first run some people have to act once (OEM: the Verification Set; SP: a CI user; GitHub owner: Actions enabled): [Setup](docs/setup.md).

## Try it in your browser

Fork this repository, add 4 secrets and press one button: [Run it yourself in 5 steps](https://github.com/aosedge/demo-services/blob/main/verification-example/TRY_IT.md).

## Read more

- [Setup](docs/setup.md): before you start, who does what, prerequisites, secrets, parameters, security
- [How it works](docs/how-it-works.md): step by step, Wait / Test / Decide, decision modes, the watch, who tells whom, where it was tested
- [Checks and the report](docs/tests.md): the built-in checks, your own tests, the report; [12 example checks](docs/checks.md)
- [API reference](docs/api.md): every call, with `curl`
- [FAQ](docs/faq.md), with the limits
- [Run it from a terminal](docs/run-locally.md): `./aos-verify setup`, upload, `./aos-verify run`
- From the zip: open `START_HERE.html`, all pages and the video, offline

Maintained by the AosEdge team (contact: the person who sent you this link), under Apache-2.0 ([LICENSE](LICENSE)).
