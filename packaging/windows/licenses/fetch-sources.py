#!/usr/bin/env python3
"""Fetch the pinned upstream source references only when --fetch is explicit.

Python 3.9+ is sufficient. Git and SVN are required for their respective entries.
The inventory includes optional/other-platform dependencies; it is not a build.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.request


def run(*args):
    subprocess.run(args, check=True)


def fetch_archive(item, target):
    if target.exists():
        if item.get("sha256") and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]:
            return
        raise RuntimeError(f"Destination already exists; review it before retrying: {target}")
    partial = target.with_name(target.name + ".partial")
    digest = hashlib.sha256()
    request = urllib.request.Request(item["url"], headers={"User-Agent": "ClipGo-source-fetcher"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            output.write(chunk)
            digest.update(chunk)
    actual = digest.hexdigest()
    if item.get("sha256") and actual != item["sha256"]:
        raise RuntimeError(f"SHA-256 mismatch; inspect {partial}")
    partial.rename(target)
    target.with_name(target.name + ".sha256").write_text(actual + "  " + target.name + "\n", encoding="utf-8")


def fetch_checkout(item, target):
    if target.exists():
        raise RuntimeError(f"Checkout already exists; use a new output directory: {target}")
    if item["kind"] == "svn":
        run("svn", "export", "-r", item["ref"], item["url"], str(target))
        return
    run("git", "init", str(target))
    run("git", "-C", str(target), "remote", "add", "origin", item["url"])
    run("git", "-C", str(target), "fetch", "--depth", "1", "origin", item["ref"])
    run("git", "-C", str(target), "checkout", "--detach", "FETCH_HEAD")
    run("git", "-c", "protocol.file.allow=never", "-c", "protocol.ext.allow=never", "-C", str(target),
        "submodule", "update", "--init", "--recursive", "--depth", "1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", action="append", choices=("core", "ffmpeg-deps", "deno-deps"),
                        help="May be repeated; defaults to core")
    parser.add_argument("--output", type=Path, help="New directory for downloaded sources")
    parser.add_argument("--fetch", action="store_true", help="Actually download; otherwise only list references")
    args = parser.parse_args()
    manifest = json.loads(Path(__file__).with_name("SOURCE-MANIFEST.json").read_text(encoding="utf-8"))
    groups = args.group or ["core"]
    if args.fetch and args.output is None:
        parser.error("--fetch requires --output")
    for group in groups:
        for item in manifest["groups"][group]:
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+", item["name"]):
                raise ValueError("Unsafe source filename")
            if not item["url"].startswith("https://"):
                raise ValueError("Only HTTPS source locations are allowed")
            print(group, item["name"], item["url"], item.get("ref", ""))
            if not args.fetch:
                continue
            destination = args.output.resolve() / group
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / item["name"]
            if item["kind"] == "archive":
                fetch_archive(item, target)
            else:
                fetch_checkout(item, target)


if __name__ == "__main__":
    main()
