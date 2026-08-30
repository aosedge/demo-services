# AosEdge security tests

A self-contained suite you can clone and run yourself to check security
properties of an AosEdge unit: it brings up a unit, exercises it, and prints a
plain-language PASS / INFO / FAIL line for every check.

Everything here is public. No credentials are shipped - you point the suite at
your own AosCloud tenant and your own OEM identity.

---

## 1. Read this first: what the suite proves, and what it does not

A security check is only worth the claim it can support. Every result is
therefore one of two kinds, and they are never mixed:

* **`[PASS]`** - the suite observed the mechanism act. Something was attempted
  and refused, or something was inspected and found to be as claimed.
* **`[INFO]`** - a statement the suite makes but does **not** prove on this
  target, with the reason. An `[INFO]` line is never evidence.

| Property | On the QEMU VM target |
|---|---|
| Deployable-item data at rest is stored as ciphertext | **Proven** (A) |
| The encrypted volume exists only while the unit is registered | **Proven** (A) |
| Deprovisioning destroys the volume and its contents | **Proven** (A) |
| The encryption key cannot be extracted | **Not proven** - see below |
| A unit is served only after valid registration | **Proven** (B) |
| Mutual TLS is required to reach the cloud API | **Proven** (B) |
| Only signed, unmodified payloads are published | **Proven** (C) |
| Delivery follows explicit attachment, not publication | **Proven** (C) |
| One instance cannot reach the unit's own data | **Proven** (D) |
| An instance runs as a restricted identity and cannot escalate | **Proven** (E) |
| Domain (hypervisor) isolation | **Not exercised** - no hypervisor here |
| Enforcement of explicitly granted permissions | **Not exercised** - no surface |

### Key protection

AosCore keeps the disk-encryption key in a PKCS#11 token. On real hardware that
token is backed by **OP-TEE** and the key is not extractable from the secure
element.

**A QEMU VM has no secure element.** On this target AosCore falls back to
**SoftHSM**: the token lives at `/var/lib/softhsm/tokens/` and its user PIN is
stored at `/var/aos/iam/.usrpin` - on `/var`, which is *not* encrypted. Anyone
with root in the VM, or with the disk image, can unlock the volume.

So check A7 prints an `[INFO]` line saying exactly that. To verify key
protection, run the equivalent check on the target SoC with OP-TEE. Any claim
to the contrary here would be refuted by a single `cat`.

### Domain isolation

The published VM image runs no hypervisor - there is no `/proc/xen` and no Xen
in its kernel log - so there are no domains to separate. Check D3 says so. On
the hardware platforms that do run Xen, this is a property of the hypervisor
configuration and must be checked there.

### Granted permissions

Nothing on the image and none of the published demo services declares a
permission, so there is nothing to grant or withhold and no grant/deny test can
be written. Check E3 says so, and points at the boundary the platform does
enforce here: a non-root assigned identity (E1) that cannot escalate (E2) and
cannot reach the unit's data (D2).

### The VM login

The published image ships a well-known `root` password, and the suite uses it
to drive the unit's serial console. That is a property of the test image, not
of AosEdge. Do not treat the VM as a hardened deployment.

---

## 2. Why the cloud is always needed

There is no offline mode. The encrypted volume is created **by registration**,
not by installing the image: AosCore runs `setupdisk.sh create` against a
PKCS#11 token that only exists once the unit is provisioned. So even the
"inspect the disk" checks need one round trip to a tenant first.

---

## 3. Requirements

* Ubuntu 22.04 or 24.04, or any Linux with the packages below
* `qemu-system-x86_64`, OVMF firmware, about 25 GB free disk
* Python 3.10 or newer
* KVM strongly recommended. Without it QEMU falls back to software
  emulation, which works but is several times slower.
* An AosCloud tenant with: an OEM PKCS#12, a service-provider PKCS#12, a
  validation unit-set, and a test subject.

```sh
sudo apt install qemu-system-x86 ovmf python3-venv
```

### How the suite reaches the unit

Through the unit's **serial console**, exposed as a unix socket. The published
image starts no SSH server, and the console is the only channel that works in
every state, including before registration.

### One environment assumption

The image expects its network gateway to also answer DNS. QEMU's user-mode
networking routes correctly but places its DNS forwarder on a different
address, so on that topology the unit would never reach the cloud. When the
suite finds the unit cannot resolve the cloud endpoint, it points the unit's
resolver at the emulated network's DNS and prints an `[INFO] environment` line
saying it did. On a bridged topology whose gateway serves DNS, nothing is
changed. Override the address with `AOS_EMULATED_DNS` if your emulator differs.

---

## 4. Setup

1. **Get the unit image.** The project publishes no checksum file, so the
   hash below is the one this suite was validated against - compare, and if
   it differs you are on a different build than the results here describe:

   ```sh
   wget https://github.com/aosedge/meta-aos-vm/releases/download/v6.1.1/aos-vm-image-qemux86-64-6.1.1.tar.xz
   echo "cdf6376d663cdcc77ca707b776cb3a0e84d5511606527102a2a471e46a1725fc  aos-vm-image-qemux86-64-6.1.1.tar.xz" | sha256sum -c -
   tar -xf aos-vm-image-qemux86-64-6.1.1.tar.xz
   ```

2. **Install the dependencies**:

   ```sh
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   pip install aos-prov aos-signer
   ```

3. **Configure**:

   ```sh
   cp config.example.env config.env
   $EDITOR config.env
   ```

   `config.env` is git-ignored. Never commit certificates.

   | Setting | What it is |
   |---|---|
   | `AOS_TARGET` | `local-ubuntu` (implemented) or `aws` (phase 2) |
   | `AOS_CLOUD_API` | your tenant's API root |
   | `AOS_OEM_P12` / `AOS_SP_P12` | your OEM and service-provider identities |
   | `AOS_UNIT_SET_ID` | a **validation** unit-set in your tenant |
   | `AOS_SUBJECT_ID` | a subject the suite may attach its probes to |
   | `AOS_VM_IMAGE` | the extracted `aos-vm-main-qemux86-64.qcow2` |
   | `AOS_VM_DISK_IF` | `scsi` for qemux86-64, `nvme` for genericx86-64 |
   | `AOS_PROBE_VERSION` | leave at `auto` |

   The suite publishes its probes as services in your tenant and computes each
   version from what is already there, so it never collides with your own.

## 5. Run

```sh
./run.sh                      # everything
./run.sh bootstrap            # storage at rest only
./run.sh cloud                # identity, integrity, isolation
```

The run ends with a block naming what it established:

```
========================== what this run established ===========================
[PASS] A1 volume-is-luks: provisioning created a LUKS2-encrypted volume ...
[PASS] A4 marker-absent-from-raw-device: the data written by the service cannot be found in
       plaintext anywhere on the raw device - it is stored as ciphertext
[INFO] A7 key-protection: key protection is NOT verified on this target ...
```

The reference run takes about 35 minutes under software emulation; with KVM
it is substantially faster. Most of that is provisioning: each group of checks
brings up its own unit, so a failure in one group cannot contaminate another.

## 6. What each check does

### A - data at rest

| ID | What it establishes |
|---|---|
| A0 | Before registration there is no encrypted volume at all |
| A1 | Registration creates a LUKS2 volume for AosCore data |
| A2 | A probe service is delivered and its instance runs |
| A3 | The instance reads and writes its own storage normally |
| A4 | A random marker the instance wrote is **nowhere** in plaintext on the raw partition |
| A6 | Deprovisioning destroys the volume group; the data is no longer readable |
| A7 | `[INFO]` key protection - see section 1 |

A4 refuses to run unless A3 confirmed the marker on the unit: "not found on the
device" would prove nothing if nothing had been written.

### B - identity and transport

| ID | What it establishes |
|---|---|
| B1 | Before registration the tenant does not know the unit and its identity service is held back by its start condition |
| B2 | After registration with a valid OEM identity the unit is known, comes Online, and its identity service runs |
| B3 | The same endpoint answers a valid client certificate and refuses both no certificate and an untrusted one |
| B4 | After release the tenant no longer knows the unit and its identity service is held back again |

B3 makes a successful call with the real certificate in the same run, so a
refusal cannot be mistaken for an unreachable endpoint.

### C - delivery integrity

| ID | What it establishes |
|---|---|
| C1 | A correctly signed service is accepted, installed, and runs its published payload |
| C2 | A payload modified after signing is refused publication, while an identically repacked one is accepted |
| C3 | A service published through a subject the unit is not attached to is never offered to it |

C2 is an A/B with a control, so the refusal is attributable to the modification
and not to the repacking. C3 asserts while the unit is demonstrably processing
desired-status updates, so absence is observed rather than assumed.

**Anti-rollback is not part of this suite.** Cloud-side version ordering is not
evidence that a unit refuses an older build, and no unit-side requirement has
been confirmed. It returns when there is one.

### D and E - isolation and identity of an instance

| ID | What it establishes |
|---|---|
| D1 | The probe reads its own data - so a refusal below is meaningful |
| D2 | Files that **demonstrably exist on the unit** cannot be read from inside an instance |
| D3 | `[INFO]` domain isolation - see section 1 |
| E1 | An instance runs as the non-root identity the unit assigned it |
| E2 | `su`, `sudo` and remounting the root filesystem all fail from inside an instance |
| E3 | `[INFO]` granted permissions - see section 1 |

D2 confirms unit-side that each target exists before treating its
unreachability as evidence.

## 7. Cleanup

The suite detaches, releases and deletes its unit and powers the VM off, even
when a check fails. If a run is interrupted, look in your tenant for a leftover
unit and remove it.

The probe services the suite publishes stay in your tenant by design, so
version numbering keeps moving forward. They are inert unless attached.

## 8. Targets

* `local-ubuntu` - QEMU VM on your machine. Implemented.
* `aws` - AosCore VM on EC2. Phase 2; selecting it fails loudly rather than
  silently running somewhere else.

## 9. Layout

```
security-tests/
  runner/        bring-up, serial console, cloud client, helpers, targets
  bootstrap/     A - storage at rest
  cloud/         B, C, D, E
  probes/        deployable payloads the checks use
  ci/            workflow to copy into .github/workflows when published
```

## 10. Notes for reviewers

* No dependency on any private repository.
* `runner/bringup.sh` is used instead of `meta-aos-vm/scripts/aos_vm.sh`: that
  script hardcodes an OVMF path absent on current Ubuntu, omits `bootindex`,
  and presents the disk the same way for every machine (EPMPAOS-7529).
* `aos-signer upload` reports success for a bundle the cloud then refuses, so
  C2 judges the outcome from the tenant's published versions, never from the
  command's exit code (EPMPAOS-7530).
* Attaching a unit uses the sub-resource endpoints keyed by system id;
  `PATCH units/{id}` answers 200 and silently does not apply.
