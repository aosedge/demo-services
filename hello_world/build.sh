#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAIN_SETUP=""
ARCH="x86"
OUT_NAME="hello_world"

usage() {
    echo "Usage: $0 [--toolchain=<path>] [--arch=<name>]" >&2
    echo "Builds hello_world binary using SDK toolchain setup." >&2
    echo "--toolchain is required; --arch defaults to ${ARCH}." >&2
}

for arg in "$@"; do
    case "${arg}" in
        --toolchain=*)
            TOOLCHAIN_SETUP="${arg#*=}"
            ;;
        --arch=*)
            ARCH="${arg#*=}"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: ${arg}" >&2
            usage
            exit 1
            ;;
    esac
done

BUILD_DIR="${SCRIPT_DIR}/build/${ARCH}"
OUT_DIR="${SCRIPT_DIR}/service/${ARCH}"

if [[ -z "${TOOLCHAIN_SETUP}" ]]; then
    echo "Missing required argument: --toolchain=<path>" >&2
    usage
    exit 1
fi

if [[ ! -f "${TOOLCHAIN_SETUP}" ]]; then
    echo "Toolchain setup file not found: ${TOOLCHAIN_SETUP}" >&2
    exit 1
fi

# shellcheck disable=SC1090
. "${TOOLCHAIN_SETUP}"

mkdir -p "${BUILD_DIR}"
cd "${BUILD_DIR}"
cmake "${SCRIPT_DIR}/src"
make -j"$(nproc)"

mkdir -p "${OUT_DIR}"
cp "${BUILD_DIR}/${OUT_NAME}" "${OUT_DIR}/${OUT_NAME}"

echo "Built ${OUT_DIR}/${OUT_NAME}"
