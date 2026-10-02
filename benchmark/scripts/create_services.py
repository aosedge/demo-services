#!/usr/bin/env python3
"""Render a benchmark service's config.yaml.in, a Podman benchmark's compose.yaml.in, or a k3s
benchmark's manifest.yaml.in, into its rendered counterpart.

Shared by any benchmark service that needs its template rendered - whether that template describes
a batch of identical items cloned by --num-services (timing's config.yaml.in), a fixed set of items
that only need @NUM_INSTANCES@/@VERSION@/etc. substituted (diskio's config.yaml.in), or a Podman
compose.yaml.in with no items list at all (--num-services doesn't apply there - @NUM_INSTANCES@ is
what drives a compose file's own `deploy.replicas`). A k3s manifest.yaml.in is a multi-document
Kubernetes YAML file, cloned per document (see below). Run it from within that service's own
directory, alongside its template - whichever of config.yaml.in/compose.yaml.in/manifest.yaml.in is
present there.

If the template contains @SERVICE_ID@, its clonable region is cloned once per service ID from 1 to
--num-services, substituting @SERVICE_ID@ with each ID. Where that region starts differs by
template kind:

  - config.yaml.in: the fixed "items:\n" marker - AosCore's plain list of deployable items, always
    wholly cloned (there's no notion of a fixed, non-cloned sibling item).
  - compose.yaml.in: the first top-level service key that itself contains @SERVICE_ID@ (e.g.
    "  bandwidth-client-@SERVICE_ID@:\n"), found dynamically rather than fixed at "services:\n" -
    a compose file may declare a fixed, non-cloned sibling service before the one that clones (a
    single bandwidth/latency server serving every client instance, say), which a fixed
    "services:\n" marker would wrongly clone too.
  - manifest.yaml.in: the first YAML document separator line ("---\n"). Everything from it on is
    cloned, separator included, so every clone is its own document; anything before it (comments,
    a fixed Namespace or Service document) is emitted once. The header must therefore not contain
    a "---\n" of its own, not even inside a comment.

Either way, this requires the clonable region be the *last* top-level section in the file (nothing
may follow it - it's cloned by taking "everything from the marker line" verbatim), so a cloning
compose.yaml.in must put "services:" after "networks:"/"volumes:", the opposite of this repo's
non-cloning compose.yaml.in templates (diskio), which don't care about top-level key order since
YAML doesn't. Otherwise the template is rendered exactly once, regardless of --num-services
(diskio's fixed item pair, or any compose.yaml.in with no @SERVICE_ID@). Either way, @NUM_INSTANCES@,
@VERSION@, @TEST_DIR@, @TEST_HOST@, @UDP_BANDWIDTH@, @RANDOM_LABEL@, @RESOLVER@ and @REGISTRY_HOST@ are
substituted wherever they appear (a placeholder absent from the template is simply left unused).

Usage:
    create_services.py [--num-services N] [--num-instances N] [--version VERSION]
                        [--test-dir PATH] [--test-host HOST] [--udp-bandwidth RATE]
                        [--random-label 0|1] [--resolver ADDRESS] [--registry-host HOST:PORT]
"""

import argparse
import os
import re
import sys

# Template kinds this script knows how to render - see the @SERVICE_ID@ cloning note above.
TEMPLATES = ("config.yaml.in", "compose.yaml.in", "manifest.yaml.in")

# Where a manifest.yaml.in's clonable region starts - see find_marker().
YAML_DOC_SEPARATOR = "---\n"

# Matches a compose.yaml.in top-level service key that itself contains @SERVICE_ID@, e.g.
# "  bandwidth-client-@SERVICE_ID@:\n" - see find_marker().
COMPOSE_SERVICE_KEY_RE = re.compile(r"^ {2}\S*@SERVICE_ID@\S*:\n", re.MULTILINE)


def find_marker(template_path, template_text):
    """Return the literal text marking where template_text's clonable region begins."""
    if template_path == "config.yaml.in":
        return "items:\n"

    if template_path == "manifest.yaml.in":
        if YAML_DOC_SEPARATOR not in template_text:
            sys.exit(f"{template_path}: no '---' document separator found to clone from")
        return YAML_DOC_SEPARATOR

    match = COMPOSE_SERVICE_KEY_RE.search(template_text)
    if not match:
        sys.exit(f"{template_path}: no service key containing @SERVICE_ID@ found to clone from")
    return match.group(0)


def parse_args():
    """Parse --num-services, --num-instances, --version, --test-dir, --test-host, --udp-bandwidth,
    --random-label, --resolver and --registry-host."""
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
    parser.add_argument(
        "--random-label",
        default="0",
        help="value substituted for @RANDOM_LABEL@, if present (default: %(default)s)",
    )
    parser.add_argument(
        "--resolver",
        default="",
        help="value substituted for @RESOLVER@, if present (default: empty)",
    )
    parser.add_argument(
        "--registry-host",
        default="",
        help="value substituted for @REGISTRY_HOST@, if present (default: empty)",
    )
    return parser.parse_args()


def substitute(
    text,
    num_instances,
    num_services,
    version,
    test_dir,
    test_host,
    udp_bandwidth,
    random_label,
    resolver,
    registry_host,
    service_id=None,
):
    """Replace every placeholder in text with its value; @SERVICE_ID@ is left alone if service_id is None."""
    if service_id is not None:
        text = text.replace("@SERVICE_ID@", str(service_id))

    return (
        text.replace("@NUM_INSTANCES@", str(num_instances))
        .replace("@NUM_SERVICES@", str(num_services))
        .replace("@VERSION@", version)
        .replace("@TEST_DIR@", test_dir)
        .replace("@TEST_HOST@", test_host)
        .replace("@UDP_BANDWIDTH@", udp_bandwidth)
        .replace("@RANDOM_LABEL@", random_label)
        .replace("@RESOLVER@", resolver)
        .replace("@REGISTRY_HOST@", registry_host)
    )


def render_config(
    template_text,
    marker,
    num_services,
    num_instances,
    version,
    test_dir,
    test_host,
    udp_bandwidth,
    random_label,
    resolver,
    registry_host,
):
    """Render a template into its output text.

    A template with @SERVICE_ID@ has a clonable region starting at marker (e.g. timing's
    config.yaml.in's "items:\n"): the part after it is repeated once per service ID from 1 to
    num_services, and the fixed part before it (e.g. bandwidth's non-cloned server service) is
    substituted too, just without a @SERVICE_ID@ value of its own to fill in - @NUM_SERVICES@ is
    how it learns the clone count (e.g. the server's own NUM_INSTANCES, matching how many client
    instances will actually be cloned below it).

    marker itself is either purely structural (config.yaml.in's "items:\n", or a cloning
    compose.yaml.in's "services:\n") and emitted exactly once ahead of the repeated region, or it
    belongs to the repeated region itself and so is repeated with it: a compose.yaml.in service key
    that itself contains @SERVICE_ID@ (e.g. "  bandwidth-client-@SERVICE_ID@:\n", from
    find_marker()), which needs its own @SERVICE_ID@ filled in fresh on every repetition, or a
    manifest.yaml.in's "---\n", which has to start every cloned document.

    Any other template - a fixed set of items (diskio's config.yaml.in) or a compose.yaml.in with
    no @SERVICE_ID@ - is substituted exactly once, regardless of num_services.
    """
    if "@SERVICE_ID@" in template_text:
        marker_repeats = "@SERVICE_ID@" in marker or marker == YAML_DOC_SEPARATOR
        header, rest = template_text.split(marker, 1)
        header = substitute(
            header,
            num_instances,
            num_services,
            version,
            test_dir,
            test_host,
            udp_bandwidth,
            random_label,
            resolver,
            registry_host,
        )
        item_template = marker + rest if marker_repeats else rest
        items = "\n".join(
            substitute(
                item_template,
                num_instances,
                num_services,
                version,
                test_dir,
                test_host,
                udp_bandwidth,
                random_label,
                resolver,
                registry_host,
                sid,
            )
            for sid in range(1, num_services + 1)
        )
        return f"{header}{items}" if marker_repeats else f"{header}{marker}{items}"

    return substitute(
        template_text,
        num_instances,
        num_services,
        version,
        test_dir,
        test_host,
        udp_bandwidth,
        random_label,
        resolver,
        registry_host,
    )


def main():
    args = parse_args()

    if args.num_services < 1:
        sys.exit("num_services must be at least 1")

    if args.num_instances < 1:
        sys.exit("num_instances must be at least 1")

    found = [t for t in TEMPLATES if os.path.exists(t)]
    if len(found) != 1:
        sys.exit(f"expected exactly one of {tuple(TEMPLATES)} in the current directory, found: {found}")
    template_path = found[0]
    output_path = template_path[: -len(".in")]

    with open(template_path, encoding="utf-8") as template_file:
        template_text = template_file.read()

    # A template with no @SERVICE_ID@ (diskio's config.yaml.in, or any compose.yaml.in) is only
    # ever rendered once, regardless of --num-services - see render_config(). find_marker() is
    # only meaningful (and only called) when @SERVICE_ID@ is actually present.
    has_service_id = "@SERVICE_ID@" in template_text
    num_services = args.num_services if has_service_id else 1
    marker = find_marker(template_path, template_text) if has_service_id else None

    output_text = render_config(
        template_text,
        marker,
        num_services,
        args.num_instances,
        args.version,
        args.test_dir,
        args.test_host,
        args.udp_bandwidth,
        args.random_label,
        args.resolver,
        args.registry_host,
    )

    with open(output_path, "w", encoding="utf-8") as output_file:
        output_file.write(output_text)


if __name__ == "__main__":
    main()
