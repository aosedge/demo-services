#!/usr/bin/env python3
"""Render a benchmark service's config.yaml.in into config.yaml.

Shared by any benchmark service that needs its config.yaml.in rendered - whether that template
describes a batch of identical items cloned by --num-services (timing), or a fixed set of items
that only need @NUM_INSTANCES@/@VERSION@/etc. substituted (diskio). Run it from within that
service's own directory, alongside its config.yaml.in.

If config.yaml.in contains @SERVICE_ID@, it is cloned once per service ID from 1 to --num-services,
substituting @SERVICE_ID@ with each ID. Otherwise the template already describes a fixed set of
items (e.g. diskio's random/sequential pair) and is rendered exactly once, regardless of
--num-services. Either way, @NUM_INSTANCES@, @VERSION@, @TEST_DIR@, @TEST_HOST@ and @UDP_BANDWIDTH@
are substituted wherever they appear (a placeholder absent from the template is simply left
unused).

Usage:
    create_services.py [--num-services N] [--num-instances N] [--version VERSION]
                        [--test-dir PATH] [--test-host HOST] [--udp-bandwidth RATE]
"""

import argparse
import sys

CONFIG_TEMPLATE = "config.yaml.in"
CONFIG_OUTPUT = "config.yaml"


def parse_args():
    """Parse --num-services, --num-instances, --version, --test-dir, --test-host and --udp-bandwidth."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--num-services",
        type=int,
        default=1,
        help="number of services to clone, substituting @SERVICE_ID@ with 1..N; ignored if "
        "config.yaml.in has no @SERVICE_ID@ (default: %(default)s)",
    )
    parser.add_argument(
        "--num-instances",
        type=int,
        default=1,
        help="minInstances for each service (default: %(default)s)",
    )
    parser.add_argument(
        "--version",
        default="1.0.0-beta.1",
        help="version for each service (default: %(default)s)",
    )
    parser.add_argument(
        "--test-dir",
        default="/storage",
        help="value substituted for @TEST_DIR@, if present (default: %(default)s)",
    )
    parser.add_argument(
        "--test-host",
        default="",
        help="value substituted for @TEST_HOST@, if present (default: %(default)s)",
    )
    parser.add_argument(
        "--udp-bandwidth",
        default="80M",
        help="value substituted for @UDP_BANDWIDTH@, if present (default: %(default)s)",
    )
    return parser.parse_args()


def render_config(template_text, num_services, num_instances, version, test_dir, test_host, udp_bandwidth):
    """Render config.yaml.in into config.yaml, with one items entry per service ID."""
    header, item_template = template_text.split("items:\n", 1)

    items = "\n".join(
        item_template.replace("@SERVICE_ID@", str(service_id))
        .replace("@NUM_INSTANCES@", str(num_instances))
        .replace("@VERSION@", version)
        .replace("@TEST_DIR@", test_dir)
        .replace("@TEST_HOST@", test_host)
        .replace("@UDP_BANDWIDTH@", udp_bandwidth)
        for service_id in range(1, num_services + 1)
    )

    with open(CONFIG_OUTPUT, "w", encoding="utf-8") as config_file:
        config_file.write(f"{header}items:\n{items}")


def main():
    args = parse_args()

    if args.num_services < 1:
        sys.exit("num_services must be at least 1")

    if args.num_instances < 1:
        sys.exit("num_instances must be at least 1")

    with open(CONFIG_TEMPLATE, encoding="utf-8") as template_file:
        template_text = template_file.read()

    # A template with no @SERVICE_ID@ already describes a fixed set of items (e.g. diskio's
    # random/sequential pair) rather than one item to clone per service ID, so it is only ever
    # rendered once, regardless of --num-services.
    num_services = args.num_services if "@SERVICE_ID@" in template_text else 1

    render_config(
        template_text,
        num_services,
        args.num_instances,
        args.version,
        args.test_dir,
        args.test_host,
        args.udp_bandwidth,
    )


if __name__ == "__main__":
    main()
