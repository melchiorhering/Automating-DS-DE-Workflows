#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 --vmdk IN.vmdk --qcow2 OUT.qcow2"
    exit 1
}

# initialize to avoid unbound errors
VMDK=""
QCOW2=""

while [[ $# -gt 0 ]]; do
    case "$1" in
    --vmdk)
        VMDK="$2"
        shift 2
        ;;
    --qcow2)
        QCOW2="$2"
        shift 2
        ;;
    *) usage ;;
    esac
done

[[ -z "${VMDK:-}" || -z "${QCOW2:-}" ]] && usage

echo "[*] Creating output directory"
mkdir -p "$(dirname "$QCOW2")"

echo "[*] Converting $VMDK → $QCOW2"
qemu-img convert -p -f vmdk "$VMDK" -O qcow2 "$QCOW2"

if command -v virt-v2v &>/dev/null; then
    echo "[*] Stripping VMware drivers"
    virt-v2v -i disk "$QCOW2" -o disk -of qcow2 \
        -os "$(dirname "$QCOW2")" --vm-type kvm -oa sparse
else
    echo "[!] virt‑v2v not found – skipping driver strip"
fi

echo "[✓] $QCOW2 is ready."
