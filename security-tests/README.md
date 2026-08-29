# AosEdge security tests

A self-contained suite that lets you verify security properties of an AosEdge
unit yourself: bring up a unit, exercise it, and read a plain PASS/FAIL summary.

Everything here is public. No credentials are shipped - you point the suite at
your own AosCloud tenant and your own OEM identity.

---

## What this suite proves, and what it does not

Read this before running anything.

| Property | Status on the VM target |
|---|---|
| Deployable-item data at rest is stored as ciphertext | **Proven** (test A) |
| The encrypted volume exists only while the unit is provisioned | **Proven** (test A) |
| Deprovisioning destroys the volume and its contents | **Proven** (test A) |
| The encryption key cannot be extracted | **NOT proven here** - see below |
| Only signed, validated services are installed | Specified, not yet implemented (test C) |
| A unit is served only after valid provisioning | Specified, not yet implemented (test B) |

### Key protection

AosCore holds the disk-encryption key in a PKCS#11 token. On real hardware that
token is backed by **OP-TEE**, and the key is not extractable from the secure
element.

**A QEMU VM has no secure element.** On this target AosCore falls back to
**SoftHSM**: the token lives at `/var/lib/softhsm/tokens/` and its user PIN is
stored at `/var/aos/iam/.usrpin` - on `/var`, which is *not* encrypted. Anyone
with root in the VM, or with the disk image, can unlock the volume.

So the suite reports key protection as a **stated hardware guarantee**, never as
a verified result, and test A7 prints exactly that. To verify it, run the
equivalent check on the target SoC with OP-TEE. Any claim to the contrary here
would be refuted by a single `cat`.

### Why the cloud is always needed

There is no offline mode. The encrypted volume is created **by provisioning**,
not by installing the image: AosCore runs `setupdisk.sh create` against a
PKCS#11 token that only exists once the unit is registered. So even the
"inspect the disk" test needs one round-trip to a tenant first. After that,
test A only inspects the unit locally.

### About the VM login

The published VM image ships a well-known `root` password. That is a property
of the test image, not of AosEdge, and the suite uses it to run commands in the
guest. Do not treat the VM as a hardened deployment.

---

## Requirements

- Ubuntu 22.04 or 24.04 (or any Linux with the packages below)
- `qemu-system-x86_64`, OVMF firmware, ~25 GB free disk
- Python 3.10+
- KVM strongly recommended. Without it the suite falls back to software
  emulation, which works but is slow.
- An AosCloud tenant with: an OEM PKCS#12, a service-provider PKCS#12, a
  validation unit-set and a test subject.

```sh
sudo apt install qemu-system-x86 ovmf python3-venv
```

## Setup

1. **Get the unit image** and check it against its published hash:

   ```sh
   wget https://github.com/aosedge/meta-aos-vm/releases/download/v6.1.1/aos-vm-image-qemux86-64-6.1.1.tar.xz
   echo "cdf6376d663cdcc77ca707b776cb3a0e84d5511606527102a2a471e46a1725fc  aos-vm-image-qemux86-64-6.1.1.tar.xz" | sha256sum -c -
   tar -xf aos-vm-image-qemux86-64-6.1.1.tar.xz
   ```

2. **Install the suite dependencies**:

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

## Run

```sh
./run.sh                 # everything that is implemented
./run.sh bootstrap       # test A only
```

Output is one line per check:

```
[PASS] A1 volume-is-luks: provisioning created a LUKS2-encrypted volume for AosCore data
[PASS] A4 marker-absent-from-raw-device: the data written by the service cannot be found in
       plaintext anywhere on the raw device - it is stored as ciphertext
[INFO] A7 key-protection: key protection is NOT verified on this target ...
```

`[INFO]` lines are statements the suite makes but does not prove on this
target. They are never counted as evidence.

## Targets

`AOS_TARGET` selects the platform:

- `local-ubuntu` - QEMU VM on your machine. Implemented.
- `aws` - AosCore VM on EC2. Phase 2; selecting it fails loudly rather than
  silently running somewhere else.

## Cleanup

The suite detaches, deprovisions and deletes its unit, and powers the VM off,
even when a test fails. If a run is interrupted, check your tenant for a
leftover unit and remove it.

## Layout

```
security-tests/
  runner/        bring-up, cloud client, helpers, target adapters
  bootstrap/     test A - storage at rest
  cloud/         tests B and C - specifications for review
  probes/        deployable payloads used by the tests
  ci/            workflow to copy into .github/workflows when published
```

## Notes for reviewers

- The suite does not depend on any private repository.
- `runner/bringup.sh` is used instead of `meta-aos-vm/scripts/aos_vm.sh`: that
  script presents the disk as virtio-scsi while the v6.1.1 image's initramfs
  waits for `/dev/nvme0n1p5`, and it also needs root, hardcodes `-enable-kvm`
  and creates a fixed `10.0.0.1/24` bridge.
- Attaching a unit is done with the sub-resource endpoints keyed by system id.
  `PATCH units/{id}` answers 200 and silently does not apply.
