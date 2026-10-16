#!/usr/bin/env bash
# Bring up an AosCore VM with QEMU.
#
# Deliberately self-contained: no root, no bridge, no package install, no
# change to host networking. Guest reachability comes from user-mode networking
# with forwarded localhost ports, so several suites can run side by side and
# nothing outlives the process.
#
# The guest is driven through a serial console exposed as a unix socket: the
# published image starts no SSH server, so the console is the only channel that
# works in every state, including before provisioning.
#
# Disk presentation matters and differs per machine within the same release:
#   qemux86-64     expects /dev/sda        -> virtio-scsi  (AOS_VM_DISK_IF=scsi)
#   genericx86-64  expects /dev/nvme0n1    -> nvme         (AOS_VM_DISK_IF=nvme)
# Booting with the wrong one leaves the guest in its initramfs printing
# "waiting for /dev/sdaN" or "waiting for /dev/nvme0n1pN".
set -euo pipefail

: "${AOS_VM_DISK:?AOS_VM_DISK is required}"
accel="${AOS_VM_ACCEL:-kvm}"
disk_if="${AOS_VM_DISK_IF:-scsi}"
cpus="${AOS_VM_CPUS:-4}"
mem="${AOS_VM_MEM:-4G}"
ssh_port="${AOS_VM_SSH_PORT:-12222}"
prov_port="${AOS_VM_PROV_PORT:-18089}"
extra_port="${AOS_VM_EXTRA_PORT:-18090}"
bios="${AOS_VM_BIOS:-/usr/share/ovmf/OVMF.fd}"
console="${AOS_VM_CONSOLE:-/dev/null}"
# The unit image configures a static address and does not take a DHCP lease, so
# the emulated user network is made to match it: otherwise forwarded ports have
# no guest address to deliver to and connections time out inside the guest.
guest_net="${AOS_VM_GUEST_NET:-10.0.0.0/24}"
guest_gw="${AOS_VM_GUEST_GW:-10.0.0.1}"
guest_ip="${AOS_VM_GUEST_IP:-10.0.0.100}"
: "${AOS_VM_SERIAL:?AOS_VM_SERIAL is required (unix socket for the console)}"

command -v qemu-system-x86_64 >/dev/null || {
    echo "qemu-system-x86_64 not found. Install qemu-system-x86." >&2; exit 2; }

if [[ ! -f "$bios" ]]; then
    for candidate in /usr/share/ovmf/OVMF.fd /usr/share/OVMF/OVMF_CODE_4M.fd \
                     /usr/share/OVMF/OVMF_CODE.fd; do
        [[ -f "$candidate" ]] && { bios="$candidate"; break; }
    done
fi
[[ -f "$bios" ]] || { echo "No OVMF firmware found; set AOS_VM_BIOS." >&2; exit 2; }

case "$disk_if" in
    scsi) disk_args=(-device virtio-scsi-pci,id=scsi
                     -device scsi-hd,drive=aos-image,bootindex=0) ;;
    nvme) disk_args=(-device nvme,drive=aos-image,serial=aosvm01,bootindex=0) ;;
    *)    echo "AOS_VM_DISK_IF must be 'scsi' or 'nvme', got '$disk_if'." >&2; exit 2 ;;
esac

if [[ "$accel" == "kvm" && -w /dev/kvm ]]; then
    accel_args=(-cpu host -enable-kvm)
else
    # Falls back to software emulation so the suite still runs where KVM is
    # unavailable or deliberately disabled.
    accel_args=(-cpu max -accel tcg,thread=multi)
fi

# bootindex is required: without it OVMF drops into the UEFI shell.
exec qemu-system-x86_64 -name main \
    -drive file="$AOS_VM_DISK",if=none,id=aos-image,format=qcow2 \
    "${disk_args[@]}" \
    "${accel_args[@]}" -smp cpus="$cpus" -m "$mem" \
    -bios "$bios" \
    -nic user,model=virtio-net-pci,net="$guest_net",host="$guest_gw",hostfwd=tcp::"$prov_port"-"$guest_ip":8089,hostfwd=tcp::"$extra_port"-"$guest_ip":8090 \
    -display none -serial unix:"$AOS_VM_SERIAL",server,nowait >"$console" 2>&1
