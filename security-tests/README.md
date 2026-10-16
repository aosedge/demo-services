# AosEdge security tests

A self-contained suite that brings up an AosEdge unit, exercises it against
your own AosCloud tenant, and reports what it was able to establish about the
platform's security behaviour.

No credentials are shipped. You point the suite at your own tenant and your own
OEM and service-provider identities through a git-ignored `config.env`.

**Scope: Phase 1, Ubuntu-local.** The suite runs the released VM image under
QEMU on an Ubuntu host. An AWS EC2 target is declared in the configuration but
is **not implemented** — selecting it fails immediately rather than quietly
running somewhere else.

---

## What this suite does not prove

Some of the guarantees people expect from AosEdge are properties of the
*hardware* platform. A QEMU VM cannot demonstrate them, and this suite does not
pretend otherwise — it reports them as `[INFO]` lines naming the reason:

| Property | Why not here |
|---|---|
| The disk-encryption key cannot be extracted | The VM has no secure element. AosCore falls back to SoftHSM, whose token is a file and whose PIN is stored on unencrypted `/var`. Verify on the target SoC with OP-TEE-backed PKCS#11. |
| Domain (hypervisor) isolation | The published VM image runs no hypervisor, so there are no domains to separate. Belongs on the Xen-based hardware platforms. |
| Enforcement of explicitly granted permissions | Nothing on the image and none of the published demo services declares a permission, so there is nothing to grant or withhold. |

Every result is one of two kinds, and they are never mixed:

* **`[PASS]`** — the mechanism was observed to act.
* **`[INFO]`** — a statement the suite deliberately does not prove here, with
  the reason. An `[INFO]` line is never counted as a pass.

The VM image also ships a well-known root password, which the suite uses to
drive the serial console. That is a property of the test image, not of
AosEdge; do not treat the VM as a hardened deployment.

## What each check establishes

| ID | Establishes |
|---|---|
| **A** | **Data at rest is ciphertext** |
| A0 | No encrypted volume exists before the unit is registered |
| A1 | Registration creates a LUKS2 volume for AosCore data |
| A2 | A signed probe service is delivered and its instance runs |
| A3 | The instance reads and writes its own storage normally |
| A4 | A marker the instance wrote appears nowhere in plaintext on the raw partition |
| A6 | Deprovisioning destroys the volume and its contents |
| A7 | `[INFO]` key protection — see above |
| **B** | **Identity and transport** |
| B1 | An unregistered unit is unknown to the tenant and its identity service is held back |
| B2 | After registration with a valid OEM identity the unit is known, Online, and served |
| B3 | The API answers a valid client certificate and refuses both no certificate and an untrusted one |
| B4 | After release the unit is no longer served |
| **C** | **Delivery integrity** |
| C1 | A correctly signed service is accepted, installed, and runs its payload |
| C2 | A payload modified after signing is refused, while an identically repacked one is accepted |
| C3 | A service published through a subject the unit is not attached to is never offered to it |
| **D** | **Instance isolation** |
| D1 | An instance reads its own data — the control for D2 |
| D2 | Files that demonstrably exist on the unit cannot be read from inside an instance |
| D3 | `[INFO]` domain isolation — see above |
| **E** | **Instance identity** |
| E1 | An instance runs as the non-root identity the unit assigned it |
| E2 | The kernel refuses every attempt to take root: `chroot`, `mknod`, writing into the unit's `/etc`, remounting `/`, `setgid(0)`, `setuid(0)` |
| E3 | `[INFO]` granted permissions — see above |

Checks are written so they can only pass by observing a mechanism act. A
refusal is never inferred from silence, and every negative check is paired with
a positive control in the same run.

Anti-rollback is deliberately out of scope: cloud-side version ordering is not
evidence that a unit refuses an older build.

## Requirements

* Ubuntu 22.04 or 24.04, `qemu-system-x86_64`, OVMF firmware, ~25 GB free disk
* Python 3.10+
* KVM strongly recommended; without it QEMU falls back to software emulation,
  which works but is several times slower
* An AosCloud tenant with an OEM PKCS#12, a service-provider PKCS#12, a
  validation unit-set and a test subject

```sh
sudo apt install qemu-system-x86 ovmf python3-venv
```

The suite drives the unit over its **serial console**: the published image
starts no SSH server, and the console is the only channel available before
registration.

## Setup

1. Get the unit image. The project publishes no checksum file, so the hash
   below is the one this suite was validated against:

   ```sh
   wget https://github.com/aosedge/meta-aos-vm/releases/download/v6.1.1/aos-vm-image-qemux86-64-6.1.1.tar.xz
   echo "cdf6376d663cdcc77ca707b776cb3a0e84d5511606527102a2a471e46a1725fc  aos-vm-image-qemux86-64-6.1.1.tar.xz" | sha256sum -c -
   tar -xf aos-vm-image-qemux86-64-6.1.1.tar.xz
   ```

2. Install the dependencies:

   ```sh
   python3 -m venv .venv && . .venv/bin/activate
   pip install -r requirements.txt
   pip install aos-prov aos-signer
   ```

3. Configure:

   ```sh
   cp config.example.env config.env
   $EDITOR config.env
   ```

   `config.env` is git-ignored. Never commit certificates. Point
   `AOS_CLOUD_API` at your tenant and `AOS_OEM_P12` / `AOS_SP_P12` at your own
   identities; `AOS_UNIT_SET_ID` should be a **validation** unit-set.

   The suite publishes its probes as services in your tenant and derives each
   version from the versions already there, so it never collides with yours.

## Run

```sh
./run.sh                 # everything
./run.sh bootstrap       # A only
./run.sh cloud           # B, C, D, E
```

The run ends with a block naming what it established:

```
========================== what this run established ===========================
[PASS] A4 marker-absent-from-raw-device: the data written by the service cannot be
       found in plaintext anywhere on the raw device
[INFO] A7 key-protection: key protection is NOT verified on this target ...
```

Roughly 35 minutes under software emulation, substantially less with KVM. Most
of that is provisioning: each group brings up its own unit, so a failure in one
group cannot contaminate another.

## Cleanup

The suite detaches, releases and deletes its unit and powers the VM off, even
when a check fails. If a run is interrupted, look in your tenant for a leftover
unit and remove it. The probe services it publishes stay in your tenant by
design and are inert unless attached.

## Environment note

The image expects its network gateway to also answer DNS. QEMU's user-mode
networking routes correctly but places its DNS forwarder elsewhere, so when the
unit cannot resolve the cloud endpoint the suite points the resolver at the
emulated network's DNS and prints an `[INFO] environment` line saying it did.
On a bridged topology whose gateway serves DNS nothing is changed. Override
with `AOS_EMULATED_DNS`.

## Layout

```
security-tests/
  runner/      bring-up, serial console, cloud client, helpers, targets
  bootstrap/   A - storage at rest
  cloud/       B, C, D, E
  probes/      deployable payloads the checks use
  ci/          workflow to copy into .github/workflows
```

`runner/bringup.sh` starts QEMU directly instead of using
`meta-aos-vm/scripts/aos_vm.sh`, which hardcodes an OVMF path that Ubuntu 24.04
no longer ships (EPMPAOS-7529). `aos-signer upload` exits 0 for a bundle the
cloud later refuses, so C2 reads the outcome from the tenant's published
versions rather than from the command's exit code (EPMPAOS-7530).
