#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOOLCHAIN_SETUP=""
ARCH="x86"

usage() {
    echo "Usage: $0 [--toolchain=<path>] [--arch=<name>]" >&2
    echo "Builds client and server binaries using SDK toolchain setup." >&2
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

SERVER_BUILD_DIR="${SCRIPT_DIR}/build/${ARCH}/server"
CLIENT_BUILD_DIR="${SCRIPT_DIR}/build/${ARCH}/client"
SERVER_OUT_DIR="${SCRIPT_DIR}/service/server/${ARCH}"
CLIENT_OUT_DIR="${SCRIPT_DIR}/service/client/${ARCH}"

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

mkdir -p "${SERVER_BUILD_DIR}" "${CLIENT_BUILD_DIR}"

cd "${SERVER_BUILD_DIR}"
cmake "${SCRIPT_DIR}/server"
make -j"$(nproc)"

cd "${CLIENT_BUILD_DIR}"
cmake "${SCRIPT_DIR}/client"
make -j"$(nproc)"

mkdir -p "${SERVER_OUT_DIR}" "${CLIENT_OUT_DIR}"
cp "${SERVER_BUILD_DIR}/aos_http_server" "${SERVER_OUT_DIR}/aos_http_server"
cp "${CLIENT_BUILD_DIR}/aos_http_client" "${CLIENT_OUT_DIR}/aos_http_client"

echo "Built ${SERVER_OUT_DIR}/aos_http_server"
echo "Built ${CLIENT_OUT_DIR}/aos_http_client"
