# Who tells whom

AosCloud sends no notification to a pipeline; a webhook would replace the watch. So the upload job starts the
verification itself (Start A), or the watch asks on a schedule (Start B), with the SP certificate alone.

![How the pipeline learns that a Service version waits. 1, upload with aos-signer upload, an existing step. 2, AosCloud creates a Verification batch, Waiting verification; the Service version goes only to the Set. 3 and 4, the Service version is installed on the Test Units, and they report installed, active or failed. 0, Start B, the watch, the fallback: watch-batches.yml repeats every 5 minutes: is a new Service version waiting for verification? GET Verification batches, statistics and Units returns the batch id, the Service version and the Unit ids of the Set. AosCloud sends no notification, so the watch asks; 5 minutes is GitHub's minimum, best effort. verify-batch.yml starts, one run per batch: Start A, the main start, the upload job starts it right after the upload; Start B, the fallback, the watch starts it. 5, wait: each Unit until active or failed. 6, built-in checks and partner tests, then the report; the built-in checks read the Units in AosCloud for 60 s. 7, decision: auto, one PATCH, approve or reject, only while still in Waiting verification; report-only, no PATCH, a person decides in the SP UI. Verified goes to the normal rollout; Invalid: the rollout stops.](img/sequence.svg)

[Open the diagram full size](img/sequence.svg)

| Who | Tells whom | What | Where you see it |
|---|---|---|---|
| Your upload job | AosCloud | a new Service version | the job log; SP UI → Deployment bundles |
| AosCloud | the Verification Set Units | install this Service version | SP UI → Verification batches: Waiting verification |
| A test Unit | AosCloud | installed, and active or failed | SP UI → Unit → Subjects & Services → Service Instances |
| AosCloud, when the watch asks | the watch | the batch id, the Service version, the Unit ids of the Set | the log of `watch-batches`; the title of the `verify-batch` run |
| The verification job | AosCloud | one decision: approve or reject | the report; SP UI: Verified or Invalid |

Two starts, no buttons:

- **Start A (main):** your upload job calls the verification right after the upload, in the same workflow.
- **Start B (fallback), the watch:** it asks AosCloud itself and finds any upload, wherever it came from. GitHub runs a
  schedule on a best-effort basis, so the watch can be late.

Both end in the same `verify-batch`, in one queue per Service version in one repository: a second run waits for the
first, finds the batch decided, and sends nothing. Two repositories on one Service are not queued:
[FAQ, the watch](faq.md#the-watch-timing-and-old-verification-batches), Two pipelines on one Service.

**Next →** back to the [README](../README.md)
