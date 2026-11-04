# AosCloud API reference, with examples

This page lists every AosCloud API call that the job and the prerequisites use, each with a `curl` example and a
short sample response. Start with the [README](../README.md#how-to-use-this-repository) if you have not set up the job yet.

> **Before you call the API**
>
> - Base URL: `$AOS_API_URL` = `https://api.aoscloud.io:10000/api/v11` (or your API host), API v11.
> - Mutual TLS: your user certificate is the client certificate, and the server is checked against the AosEdge root
>   CA file `aos-root-ca.crt`. It comes with `aos-keys`, in the folder where the AosEdge docs install the tools:
>   `~/.aos/venv`, file `aos_keys/files/1rootCA.crt`; the `cp` line below copies it. There is no token and no
>   password.
> - The role of the certificate decides what you may do: **SP** (Service Provider) or **OEM**.
> - The **Swagger** links open the official reference. Your browser asks for your certificate first.
> - One PEM file with the certificate, the intermediate certificates and the key, from your SP `.p12`, which
>   `aos-keys` saves under `~/.aos/security/` (`find ~/.aos/security -name "*.p12"`; keep its path in `AOS_P12`,
>   as in [Run it from your terminal, step 3](run-locally.md#3-your-certificate)). `.p12` is a
>   password-protected bundle; Python `requests` and `curl` need PEM files (no `-clcerts`:
>   without the intermediate certificates the server answers `unknown ca`). Press Enter if you are asked for a
>   password:
>
>   ```bash
>   openssl pkcs12 -in "$AOS_P12" -nodes -out user.pem
>   cp "$(~/.aos/venv/bin/python3 -c 'import aos_keys, pathlib; print(pathlib.Path(aos_keys.__file__).parent / "files" / "1rootCA.crt")')" aos-root-ca.crt
>   export AOS_CERT=$PWD/user.pem AOS_CA=$PWD/aos-root-ca.crt
>   export AOS_API_URL=https://api.aoscloud.io:10000/api/v11
>   ```

Every example uses `curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA"`. The ids in the samples are placeholders such as
`<batch id>`, and the answers show only the fields the job reads.

**Errors you can meet**

| Code | When | What to do |
|---|---|---|
| TLS error, before any HTTP code | a wrong or missing certificate, or a PEM without the intermediate CAs | make the PEM again without `-clcerts`; check `AOS_CERT` and `AOS_CA` |
| 403 | the wrong role (for example an SP certificate on `unit-sets/`) | use the certificate of the role named for the call |
| 404 | the id does not exist, or your account cannot see it | check the id; Units and batches are per tenant |
| 405 | the decision is not allowed for this certificate or batch | see [Approve or reject](#approve-or-reject) |
| 400, 422 | the body is wrong: a missing field, a wrong type | compare with the body shown for the call |
| 5xx | an error inside AosCloud | `wait` tries again; after a decision, look at the batch in the UI |

## Calls of the job

### Find the batch

Lists the newest Verification batches, so the job can find the one of your Service and version.
**GET `verification-batch/`** · SP · read ·
Swagger: [verification_batch_list](https://api.aoscloud.io/api/v11/docs#/verification-batch/verification_batch_list)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/verification-batch/?ordering=-created_at&limit=50&offset=0"
```

```json
{"total": 1, "offset": 0, "items": [
  {"id": "<batch id>", "state": "Waiting_validation", "architectures": ["amd64"],
   "update_items": [{"identity_id": "<service id>", "version": "4.6.0", "codename": "my-service"}]}]}
```

The job reads the pages (`offset` 0, 50, 100, …) up to `total`, and takes the batch whose
`update_items[].identity_id` is your Service id and whose `version` is the uploaded version. It prefers a batch in
`Waiting_validation`, and stops reading once it finds one. There is one batch per uploaded version.

`discover` reads all the pages, and takes a batch only if its version is the highest of your Service. Filter on your
side: AosCloud ignores `state=` and `created_at__gte=` here, and answers 200 with every batch.

### Read the batch

Reads one batch again, to check that it still waits for a decision.
**GET `verification-batch/{batch_id}/`** · SP · read ·
Swagger: [verification_batch_read](https://api.aoscloud.io/api/v11/docs#/verification-batch/verification_batch_read)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/verification-batch/$BATCH_ID/"
```

```json
{"id": "<batch id>", "state": "Waiting_validation", "architectures": ["amd64"], "approval_states": null}
```

`state` is one of `Waiting_validation` (UI: **Waiting verification**), `Valid` (UI: **Verified**), `Invalid`,
`Partially_validated`. Error: 404 for an unknown id.

### The Units of the Verification Set

The job takes the test Units from these two calls, with the SP certificate alone. A test Unit has to be in both.

**GET `verification-batch/{batch_id}/statistics/`** · SP · read ·
Swagger: [verification_batch_statistics](https://api.aoscloud.io/api/v11/docs#/verification-batch/verification_batch_statistics)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/verification-batch/$BATCH_ID/statistics/"
```

```json
{"batch_id": "<batch id>", "sub_batches": [
  {"architecture": "amd64", "verification_set": {"count": 1, "unit_ids": ["<unit id>"]}}]}
```

The job reads only `verification_set`: the Units of the Verification Set, per architecture. It does not say whether a
Unit has your Service. The job never reads `installed`: on AosCloud 6.1 its count stays 0 after an install.

**GET `services/{service_id}/units/`** · SP · read ·
Swagger: [services_units_list](https://api.aoscloud.io/api/v11/docs#/services/services_units_list)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/services/$SERVICE_ID/units/?limit=50&offset=0"
```

```json
{"total": 1, "offset": 0, "items": [{"id": "<unit id>", "system_uid": "<system uid>", "subjects": ["<subject id>"]}]}
```

The Units linked to your Service through a Subject, whether they are in the Set or not. An architecture of the batch with no Unit in both lists stops
the job with no decision: `no test Unit for arm64`.

### Read the version on a Unit

Shows which version of your Service a test Unit has installed, and whether it runs.
**GET `units/{unit_id}/subjects-services/{service_id}/`** · SP · read ·
Swagger: [units_subjects_services_read](https://api.aoscloud.io/api/v11/docs#/units/units_subjects_services_read)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/units/$UNIT_ID/subjects-services/$SERVICE_ID/"
```

While the new version is on its way:

```json
{"items": [{"service_versions": {
  "pending_service_version_status": "pending", "pending_service_version": {"version": "4.6.0"},
  "installed_service_version": {"version": "4.5.0"}}, "instances": [{"version": "4.5.0", "run_state": "active"}]}]}
```

When it runs:

```json
{"items": [{"service_versions": {
  "pending_service_version_status": "installed", "pending_service_version": null,
  "installed_service_version": {"version": "4.6.0"}}, "instances": [{"version": "4.6.0", "run_state": "active"}]}]}
```

The job counts a Unit as **ready** when `installed_service_version` is the new version and an instance of the new
version has `run_state` `active`; an instance that is still starting is not ready yet. It counts a Unit as
**failed** when an instance of the new version has `run_state` `failed`, or when `pending_service_version_status` is
`error`, `resource conflict` or `rejected`. An empty `items` list means that
the Unit is not linked to the Service through a Subject (prerequisite 2, steps 3 and 4).

### Approve or reject

Records the decision for every architecture of the batch, or for the ones you name with `--arch`. The job sends it
**once**, and only while the state is `Waiting_validation`.
**PATCH `verification-batch/{batch_id}/`** · SP · **write** ·
Swagger: [verification_batch_approval](https://api.aoscloud.io/api/v11/docs#/verification-batch/verification_batch_approval)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X PATCH -H 'Content-Type: application/json' \
     -d '[{"architecture": "amd64", "is_approved": true}]' "$AOS_API_URL/verification-batch/$BATCH_ID/"
```

Body: a list with one entry for each architecture in the batch's `architectures` (for example `amd64`, for an image
built for `any`). `true` approves it, and `false` rejects it.

```json
{"id": "<batch id>", "state": "Valid",
 "approval_states": {"amd64": {"is_approved": true, "user_id": "<user id>", "updated_at": "2026-10-30T12:51:04Z"}}}
```

After the call the state is `Valid` (all approved, shown as **Verified** in the UI), `Invalid` (one rejected), or
`Partially_validated` (some architectures still open).

Answers and errors:
- A success with an empty body is still a success: `decide` prints `sent` and exits 0.
- **405**: the decision is not allowed for this certificate or batch.
- **5xx**: the decision may have been applied. `decide` exits 3 and does not send it again: look at the batch in the
  UI.
- 400 or 422: the body is not a list of `{architecture, is_approved}`.
- `verify_batch.py` reads the state first, and decides only a batch that is `Waiting_validation`. This check matters:
  AosCloud also accepts this call on a batch that is already decided, so a second call could turn an approve into a
  reject.

## Calls of the prerequisites (once, or in the UI)

### Create the Service

**POST `services/`** · SP · write ·
Swagger: [services_create](https://api.aoscloud.io/api/v11/docs#/services/services_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"title": "My Service", "codename": "my-service", "default_quotas": {}}' "$AOS_API_URL/services/"
```

```json
{"id": "<service id>", "codename": "my-service"}
```

The `id` is the Service id of the job.

### Find the Service id

**GET `services/?search=<codename>`** · SP · read ·
Swagger: [services_list](https://api.aoscloud.io/api/v11/docs#/services/services_list)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/services/?search=my-service"
```

```json
{"total": 1, "offset": 0, "items": [{"id": "<service id>", "title": "My Service", "codename": "my-service"}]}
```

`search` matches a part of the title or the codename: take the item whose `codename` is exactly yours.

### Create the Verification Set

**POST `unit-sets/`** · OEM · write ·
Swagger: [unit_sets_create](https://api.aoscloud.io/api/v11/docs#/unit-sets/unit_sets_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"title": "Verification Set", "is_validation_set": true, "fleet": "'"$FLEET_ID"'", "description": "test Units"}' \
     "$AOS_API_URL/unit-sets/"
```

```json
{"id": "<unit set id>", "title": "Verification Set", "is_validation_set": true}
```

Error: 403 with an SP certificate.

### Add Units to the Verification Set

**POST `unit-sets/{unit_set_id}/units/`** · OEM · write ·
Swagger: [unit_sets_units_create](https://api.aoscloud.io/api/v11/docs#/unit-sets/unit_sets_units_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"system_uids": ["<system uid>"]}' "$AOS_API_URL/unit-sets/$UNIT_SET_ID/units/"
```

The body takes the **System UIDs** of the Units, not their ids.

### Find a Unit id

The job needs the `id` of each test Unit, not its System UID.
**GET `units/?system_uid=<system uid>`** · OEM · read ·
Swagger: [units_list](https://api.aoscloud.io/api/v11/docs#/units/units_list)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" "$AOS_API_URL/units/?system_uid=<system uid>"
```

```json
{"total": 1, "offset": 0, "items": [{"id": "<unit id>", "system_uid": "<system uid>"}]}
```

### Create the Subject

**POST `subjects/`** · OEM · write ·
Swagger: [subjects_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"label": "verification-subject", "is_group": true, "priority": 0}' "$AOS_API_URL/subjects/"
```

```json
{"id": "<subject id>", "label": "verification-subject", "is_group": true}
```

### Assign Units to the Subject

**POST `subjects/{subject_id}/units/`** · OEM · write ·
Swagger: [subjects_units_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_units_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"system_uids": ["<system uid>"]}' "$AOS_API_URL/subjects/$SUBJECT_ID/units/"
```

### Give the Service to the Subject

**POST `subjects/{subject_id}/services/`** · OEM · write ·
Swagger: [subjects_services_create](https://api.aoscloud.io/api/v11/docs#/subjects/subjects_services_create)

```bash
curl -sS --cert "$AOS_CERT" --cacert "$AOS_CA" -X POST -H 'Content-Type: application/json' \
     -d '{"service_ids": ["'"$SERVICE_ID"'"]}' "$AOS_API_URL/subjects/$SUBJECT_ID/services/"
```

## More calls

Every call above is part of the public AosCloud API v11. The Swagger reference lists all the other calls.

**Next →** [FAQ and tips](faq.md) · back to the [README](../README.md)
