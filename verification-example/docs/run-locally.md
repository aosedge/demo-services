# Run it from your terminal

One story, step by step: you upload a new version, the built-in checks run against it, you open the report and approve it.
It is what the GitHub job does, by hand, so you can try it in about 10 minutes or find out why the job failed. Click
**Copy** on each block and paste it into one terminal. You edit two lines (steps 3 and 4); everything else runs as it
is. The outputs of steps 5, 7 and 9 come from production runs; ids are shortened to `…` plus 4 characters.

## 1. Get the files

Everything runs from the folder of this example.

**macOS / Linux**

```bash
unzip aos-verification-example.zip && cd aos-verification-example
```

**Windows (PowerShell)**

```powershell
Expand-Archive aos-verification-example.zip -DestinationPath .; cd aos-verification-example
```

Note: unpacked it with a double click? Then only `cd aos-verification-example`.

## 2. Python

The example needs Python 3.10 or later and one package, `requests`, in a folder of its own (`.venv`).

**macOS / Linux**

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install requests
```

**Windows (PowerShell)**

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install requests
```

Check it:

```bash
python -c "import requests; print('requests OK')"
```

You should see `requests OK`.

Note: the `[notice] A new release of pip` lines are harmless. With a custom prompt theme, `(.venv)` may not show in
the prompt; the check above is what counts.

## 3. Your certificate

AosCloud knows you by your certificate (mutual TLS): there is no password and no token. Find your certificates:

**macOS / Linux**

```bash
find ~/.aos/security -name "*.p12"
```

**Windows (PowerShell)**

```powershell
Get-ChildItem "$env:USERPROFILE\.aos\security" -Recurse -Filter *.p12 | ForEach-Object FullName
```

You should see one line per certificate, for example `…/.aos/security/aos-user-sp.p12` (the full path to your home folder comes first).

Note: `aos-keys` saves one `.p12` per AosCloud and role. Pick the **SP** certificate of the AosCloud you call; its
name and folder may differ, for example `~/.aos/security/prod/aos-user-sp-prod.p12`.

Put its path into this line, then run it (the only edit in this step):

**macOS / Linux**

```bash
export AOS_P12=<path to your SP .p12>
```

**Windows (PowerShell)**

```powershell
$env:AOS_P12 = "<path to your SP .p12>"
```

`.p12` is a password-protected bundle; Python `requests` and `curl` need PEM files. Make one PEM file: the
certificate, the intermediate certificates and the key. Do not add `-clcerts`, or AosCloud answers `unknown ca`.

**macOS / Linux**

```bash
openssl pkcs12 -in "$AOS_P12" -nodes -out user.pem
```

**Windows (PowerShell)**

```powershell
openssl pkcs12 -in "$env:AOS_P12" -nodes -out user.pem
```

Note: `Enter Import Password` asks for the password of the `.p12` file, not of your computer. Often it is empty:
just press Enter. `Mac verify error: invalid password?` means a wrong file password (MAC is an integrity check, not
your Mac).

The AosEdge root CA comes with `aos-keys`, in the folder where the AosEdge docs install the tools (`~/.aos/venv`):

**macOS / Linux**

```bash
cp "$(~/.aos/venv/bin/python3 -c 'import aos_keys, pathlib; print(pathlib.Path(aos_keys.__file__).parent / "files" / "1rootCA.crt")')" aos-root-ca.crt
```

**Windows (PowerShell)**

```powershell
Copy-Item (& "$env:USERPROFILE\.aos\venv\Scripts\python" -c "import aos_keys, pathlib; print(pathlib.Path(aos_keys.__file__).parent / 'files' / '1rootCA.crt')") aos-root-ca.crt
```

Tell the example where they are:

**macOS / Linux**

```bash
export AOS_API_URL=https://api.aoscloud.io:10000/api/v11 AOS_CERT="$PWD/user.pem" AOS_CA="$PWD/aos-root-ca.crt"
```

**Windows (PowerShell)**

```powershell
$env:AOS_API_URL = "https://api.aoscloud.io:10000/api/v11"; $env:AOS_CERT = "$PWD\user.pem"; $env:AOS_CA = "$PWD\aos-root-ca.crt"
```

## 4. Your values, once

Three values, in one line. This is the only line you edit; everything after it copies as it is.

- **Service id**: SP UI → Services → your Service → ID.
- **Unit id**: OEM UI → Units → your test Unit → ID. Not the System UID. Or look both up with the API:
  [find a Service id](api.md#find-the-service-id), [find a Unit id](api.md#find-a-unit-id).
- **Version**: the version you upload in step 6, `items[].version` in its `config.yaml`. It must be new.

**macOS / Linux**

```bash
export SERVICE_ID=<your service id> UNIT_ID=<your test unit id> VERSION=<the version you upload>
```

**Windows (PowerShell)**

```powershell
$env:SERVICE_ID = "<your service id>"; $env:UNIT_ID = "<your test unit id>"; $env:VERSION = "<the version you upload>"
```

## 5. Check the connection (writes nothing)

Before you upload anything: one read-only call proves the certificate, the root CA and both ids.

**macOS / Linux**

```bash
python verify_batch.py check --service-id "$SERVICE_ID" --unit "$UNIT_ID"
```

**Windows (PowerShell)**

```powershell
python verify_batch.py check --service-id $env:SERVICE_ID --unit $env:UNIT_ID
```

You should see:

```text
connection OK: the certificate, the root CA and the API address work
Service …ec65: latest batch …f5b8, version 4.7.0, state Valid
Unit …318d: installed 4.7.0; instances: 4.7.0 active
```

| You see | It means |
|---|---|
| `TLS error` | a wrong `AOS_CA`, or a PEM made with `-clcerts`: make `user.pem` again (step 3) |
| `HTTP 401` or `HTTP 403` | not the SP certificate of this AosCloud: pick the right `.p12` (step 3) |
| `HTTP 404` | a wrong id (step 4); the Unit id is not its System UID |

Now ask what the pipeline asks every 10 minutes: is a new version waiting?

```bash
python verify_batch.py discover --dry-run
```

You should see your latest batches, then:

```text
no waiting batch: the highest version 4.7.0 is Valid
```

`--dry-run` starts nothing, and writes nothing. Batches of older versions that still wait are listed as
`stale, left alone`: they are never chosen, so decide them in the UI.

## 6. Upload the new version, as you do today

After the upload (6), run 7, 8 and 9 one after the other in this terminal: 7 waits and keeps the batch in `BATCH`,
8 runs the built-in checks and writes the report, 9 decides that `BATCH`. In GitHub the job runs exactly these three for you.

Upload from your Service folder, with your usual `aos-signer`, in a second terminal window (this one keeps the
values of steps 3 and 4):

```bash
aos-signer sign
```

```bash
aos-signer upload
```

You should see `Deployment bundle successfully uploaded!`. In the SP UI, Verification batches shows your version as
**Waiting verification**.

Back in the first terminal, ask again:

```bash
python verify_batch.py discover --dry-run
```

You should see one JSON line with your batch, your version and the test Units:

```text
{"batch_id": "<batch id>", "version": "<your version>", "units": ["<unit id>"]}
```

If it says `no test Unit`, a Unit is missing from the Verification Set, or the Service is not linked to it
([prerequisite 2](../README.md#2-verification-configured-oem)).

## 7. Wait

`wait` finds the batch of your version, waits until the version runs on the test Unit, and keeps the batch id in
`BATCH`.

**macOS / Linux**

```bash
BATCH=$(python verify_batch.py wait --service-id "$SERVICE_ID" --version "$VERSION" --unit "$UNIT_ID"); export BATCH; echo "BATCH=$BATCH"
```

**Windows (PowerShell)**

```powershell
$env:BATCH = python verify_batch.py wait --service-id $env:SERVICE_ID --version $env:VERSION --unit $env:UNIT_ID; "BATCH=$env:BATCH"
```

You should see the Unit state, then the batch id:

```text
batch …ba9b: {'…318d': 'installed'}
BATCH=…ba9b
```

In a production run the version was active on the test Unit about 15 s after the upload.

## 8. Run the built-in checks

The built-in checks read the batch and the Unit again (exactly this version, on every Unit, no crash), then watch the version
for 60 s (late failure, restarts). Your own tests run after them, if you add them ([Your tests](tests.md)). The
script reads the values you exported; it takes no arguments.

**macOS / Linux**

```bash
bash tests/run_tests.sh
```

**Windows (PowerShell)**

```powershell
& "$env:ProgramFiles\Git\bin\bash.exe" tests/run_tests.sh
```

You should see (a sample run, against a recorded AosCloud state):

```text
Built-in checks of version 4.8.0
PASS A1 exactly this Service and version: batch …4b5a holds version 4.8.0 of Service …5c6d
PASS B1 on every listed Unit: version 4.8.0 active on 1 of 1 Units
PASS C2 no crash on start: no failed instance
watching for 60 s (1 round(s) of 60 s)
PASS C3/C4 still running (late failure, restarts): active on every Unit for 60 s
SKIP your tests: no tests/your_tests.sh (start from tests/your_tests.sh.example)
report: report/report.html (and junit.xml)
all checks passed: approve
```

Open the report:

**macOS**

```bash
open report/report.html
```

**Linux**

```bash
xdg-open report/report.html
```

**Windows (PowerShell)**

```powershell
start report\report.html
```

What each check covers: [Example checks](checks.md).

## 9. Decide

All checks passed: approve.

**macOS / Linux**

```bash
python verify_batch.py decide --batch-id "$BATCH" --approve
```

**Windows (PowerShell)**

```powershell
python verify_batch.py decide --batch-id $env:BATCH --approve
```

You should see `batch …ba9b: approve amd64`, then `batch …ba9b: approved, now Valid`. Reload the page in the UI: the
batch shows **Verified**.

A check failed: reject.

**macOS / Linux**

```bash
python verify_batch.py decide --batch-id "$BATCH" --reject
```

**Windows (PowerShell)**

```powershell
python verify_batch.py decide --batch-id $env:BATCH --reject
```

If your test Units do not cover every architecture of the batch, add `--arch` for the ones you tested.

## 10. The next version

Set `VERSION` to the new version (the line of step 4) and repeat steps 6 to 9. In GitHub the watch notices each upload
and the job does 7 to 9 by itself: [Add the job to your pipeline](../README.md#the-verification-job-in-3-steps).

## Exit codes

| Command | Code | Meaning | What to do |
|---|---|---|---|
| `discover` | 0 | a JSON line: this batch is to be verified; or no line: nothing waits | go on |
| `discover` | 2 | no test Unit for the batch, or an error; start nothing. With `--dry-run` a batch with no test Unit also exits 0 | read the message |
| `check` | 0 | connected; the latest batch and the Units are printed | go on |
| `check` | 2 | TLS error, a wrong role or id | read the message; see the table in step 5 |
| `wait` | 0 | the batch waits and the version runs on every Unit; the batch id is printed | run the built-in checks |
| `wait` | 1 | the version failed to start on a Unit; the batch id is printed | reject it |
| `wait` | 2 | timed out, no batch, the batch is already decided, no test Units, or an error that waiting cannot fix (a setting, a TLS error, an HTTP 4xx answer) | decide nothing; read the message, and see the [FAQ](faq.md#decisions) |
| `wait` | 3 | timed out while AosCloud could not be reached or a test Unit was offline | decide nothing; run it again |
| `tests/run_tests.sh` | 0 | every check passed | approve |
| `tests/run_tests.sh` | 1 | a check failed; the report says which | reject |
| `tests/run_tests.sh` | 2 | a value is missing (`SERVICE_ID`, `VERSION`, `BATCH`, `UNIT_ID`) | export it and run it again |
| `tests/run_tests.sh` | 3 | AosCloud could not be read, or a test Unit is offline | decide nothing; run it again |
| `decide` | 0 | the decision was sent; the new state is printed, or `sent` when the answer has no body | check the batch in the UI |
| `decide` | 2 | refused: the batch is not waiting, has no architectures or not the one in `--arch`, AosCloud was not reachable, or it answered 4xx | read the message; run `decide` again only if nothing was sent |
| `decide` | 3 | the decision was sent, and the answer was lost or HTTP 5xx: it may have been applied | look at the batch in the UI before you do anything else |

Delete `user.pem` when you are done: it holds your private key.

**Next →** [Add the job to your pipeline](../README.md#the-verification-job-in-3-steps), then [Your tests](tests.md)
