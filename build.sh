#!/usr/bin/env bash
set -euo pipefail

TOOLCHAIN_SETUP=""
TARGET_ARCH="amd64"
OUT_DIR_BASE=""

usage() {
    echo "Usage: $0 <source_folder> [--toolchain=<path>] [--arch=<name>] [--out-dir=<path>]" >&2
    echo "Builds a CMake-based demo service using the SDK toolchain." >&2
    echo "<source_folder> must contain a CMakeLists.txt." >&2
    echo "--toolchain is optional; if omitted, the current environment's toolchain is used" >&2
    echo "--out-dir defaults to <source_folder>/output; built executables are copied to <out-dir>/<arch>" >&2
    echo "--arch defaults to ${TARGET_ARCH}" >&2
}

if [[ $# -eq 0 ]]; then
    usage
    exit 1
fi

INVOCATION_DIR="$(pwd)"
SRC_DIR="$1"
shift

for arg in "$@"; do
    case "${arg}" in
        --toolchain=*)
            TOOLCHAIN_SETUP="${arg#*=}"
            TOOLCHAIN_SETUP="${TOOLCHAIN_SETUP/#\~/$HOME}"
            ;;

        --arch=*)
            TARGET_ARCH="${arg#*=}"
            ;;

        --out-dir=*)
            OUT_DIR_BASE="${arg#*=}"
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

if [[ ! -f "${SRC_DIR}/CMakeLists.txt" ]]; then
    echo "CMakeLists.txt not found in: ${SRC_DIR}" >&2

    exit 1
fi

SRC_DIR="$(cd "${SRC_DIR}" && pwd)"
BUILD_DIR="${SRC_DIR}/build/${TARGET_ARCH}"

if [[ -z "${OUT_DIR_BASE}" ]]; then
    OUT_DIR_BASE="${SRC_DIR}/output"
fi
if [[ "${OUT_DIR_BASE}" != /* ]]; then
    OUT_DIR_BASE="${INVOCATION_DIR}/${OUT_DIR_BASE}"
fi
OUT_DIR="${OUT_DIR_BASE}/${TARGET_ARCH}"

# Computed before sourcing --toolchain below: the SDK's environment-setup-* script exports its own
# ARCH (Yocto's kernel-style arch name, e.g. "x86"), which would otherwise clash with ours.
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

echo "Built ${BUILD_DIR} successfully."

mkdir -p "${OUT_DIR}"

find "${BUILD_DIR}" -maxdepth 1 -type f -perm -u+x -exec cp {} "${OUT_DIR}" \;

echo "Copied build output to ${OUT_DIR}"
