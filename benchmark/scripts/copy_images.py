#!/usr/bin/env python3
"""Copy a service's per-architecture build output into one numbered folder per service instance.

Copies every architecture subfolder found under --image-dir (each produced by a separate
build.sh --arch=<name> run) into --dest-dir/<service_id>/<arch>/ (default dest-dir: services) for
each service ID from 1 to --num-services (emptying that service's folder first if it already
exists), adding a test.dat file of --data-size MiB of random payload to each copy if --data-size
is given.

Run this before create_services.py renders a config.yaml.in that references @IMAGES@ - it only
touches --image-dir/--dest-dir, not config.yaml.in/config.yaml.

Usage:
    copy_images.py --num-services N [--image-dir DIR] [--dest-dir DIR] [--data-size MiB]
"""

import argparse
import os
import shutil
import sys

CHUNK_SIZE = 1024 * 1024


def parse_args():
    """Parse --num-services, --image-dir, --dest-dir and --data-size command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-services", type=int, required=True, help="number of service folders to create")
    parser.add_argument(
        "--image-dir",
        default="output",
        help="directory containing build.sh's per-arch output (default: %(default)s)",
    )
    parser.add_argument(
        "--dest-dir",
        default="services",
        help="directory to copy each service's per-arch output into (default: %(default)s)",
    )
    parser.add_argument(
        "--data-size",
        type=float,
        default=None,
        help="size of each service's test.dat, in MiB (default: no test.dat)",
    )
    return parser.parse_args()


def discover_archs(image_dir, dest_dir):
    """Return the sorted names of the architecture subfolders build.sh produced under image_dir."""
    if not os.path.isdir(image_dir):
        sys.exit(f"image directory not found: {image_dir} - run build.sh first")

    dest_dir_norm = os.path.normpath(dest_dir)
    archs = sorted(
        name
        for name in os.listdir(image_dir)
        if os.path.isdir(os.path.join(image_dir, name))
        and os.path.normpath(os.path.join(image_dir, name)) != dest_dir_norm
    )
    if not archs:
        sys.exit(f"no architecture subfolders found in: {image_dir} - run build.sh first")

    return archs


def create_service_dir(archs, service_id, data_size, image_dir, dest_dir):
    """(Re)create dest_dir/<service_id>/ with a copy of each image_dir/<arch>/ and an optional test.dat."""
    service_dir = os.path.join(dest_dir, str(service_id))

    if os.path.exists(service_dir):
        shutil.rmtree(service_dir)
    os.makedirs(service_dir)

    for arch in archs:
        arch_dir = os.path.join(service_dir, arch)
        shutil.copytree(os.path.join(image_dir, arch), arch_dir)

        if data_size is None:
            continue

        remaining = round(data_size * 1024 * 1024)
        with open(os.path.join(arch_dir, "test.dat"), "wb") as test_file:
            while remaining > 0:
                chunk = min(CHUNK_SIZE, remaining)
                test_file.write(os.urandom(chunk))
                remaining -= chunk


def main():
    args = parse_args()

    if args.num_services < 1:
        sys.exit("num_services must be at least 1")

    archs = discover_archs(args.image_dir, args.dest_dir)

    for service_id in range(1, args.num_services + 1):
        create_service_dir(archs, service_id, args.data_size, args.image_dir, args.dest_dir)


if __name__ == "__main__":
    main()
