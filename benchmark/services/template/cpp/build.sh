#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAIN_SETUP=""
ARCH="x86"

usage() {
    echo "Usage: $0 [--toolchain=<path>] [--arch=<name>]" >&2
    echo "Builds benchmark_template C++ service using the SDK toolchain." >&2
    echo "--toolchain is optional; if omitted, the current environment's toolchain is used" >&2
    echo "--arch defaults to ${ARCH}" >&2
}

for arg in "$@"; do
    case "${arg}" in
        --toolchain=*)
            TOOLCHAIN_SETUP="${arg#*=}"
            TOOLCHAIN_SETUP="${TOOLCHAIN_SETUP/#\~/$HOME}"
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

SRC_DIR="${SCRIPT_DIR}"
BUILD_DIR="${SCRIPT_DIR}/build/${ARCH}"
OUT_DIR="${SCRIPT_DIR}/output/${ARCH}"

if [[ -n "${TOOLCHAIN_SETUP}" ]]; then
    if [[ ! -f "${TOOLCHAIN_SETUP}" ]]; then
        echo "Toolchain setup file not found: ${TOOLCHAIN_SETUP}" >&2

        exit 1
    fi

    # shellcheck disable=SC1090
    . "${TOOLCHAIN_SETUP}"
fi

TOOLCHAIN_MARKER="${BUILD_DIR}/.toolchain"

if [[ -f "${TOOLCHAIN_MARKER}" ]] && [[ "$(cat "${TOOLCHAIN_MARKER}")" != "${TOOLCHAIN_SETUP}" ]]; then
    echo "Toolchain changed, removing stale build dir: ${BUILD_DIR}" >&2

    rm -rf "${BUILD_DIR}"
fi

mkdir -p "${BUILD_DIR}"
echo "${TOOLCHAIN_SETUP}" > "${TOOLCHAIN_MARKER}"

cd "${BUILD_DIR}"
cmake "${SRC_DIR}"
make -j"$(nproc)"

mkdir -p "${OUT_DIR}"
cp "${BUILD_DIR}/benchmark_template" "${OUT_DIR}"

echo "Built ${OUT_DIR}/benchmark_template successfully."
