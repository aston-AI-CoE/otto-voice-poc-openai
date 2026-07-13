#!/usr/bin/env python3
"""Credential-free checks for the standalone Otto voice POC."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONSOLIDATION = ROOT / "CONSOLIDATION.md"


class SmokeTestError(Exception):
    pass


class BasicHTMLParser(HTMLParser):
    pass


def run_git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line for line in result.stdout.splitlines() if line]


def repo_files() -> list[str]:
    files = run_git("ls-files", "--cached", "--others", "--exclude-standard")
    return sorted(set(files))


def check_python_syntax(files: list[str]) -> None:
    for name in files:
        if not name.endswith(".py"):
            continue
        path = ROOT / name
        ast.parse(path.read_text(encoding="utf-8"), filename=name)


def check_html(files: list[str]) -> None:
    parser = BasicHTMLParser()
    for name in files:
        if not name.endswith(".html"):
            continue
        parser.feed((ROOT / name).read_text(encoding="utf-8"))
        parser.close()


def check_consolidation_coverage(files: list[str]) -> None:
    body = CONSOLIDATION.read_text(encoding="utf-8")
    missing = [name for name in files if f"`{name}`" not in body]
    if missing:
        raise SmokeTestError(
            "CONSOLIDATION.md is missing tracked file mappings: " + ", ".join(missing)
        )


def check_env_hygiene(files: list[str]) -> None:
    if ".env" in files:
        raise SmokeTestError(".env is tracked")

    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    if ".env" not in {line.strip() for line in gitignore}:
        raise SmokeTestError(".gitignore does not ignore .env")

    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    if re.search(r"sk-[A-Za-z0-9_-]{8,}", env_example):
        raise SmokeTestError(".env.example contains a key-shaped OpenAI placeholder")


def main() -> int:
    files = repo_files()
    checks = [
        ("python syntax", lambda: check_python_syntax(files)),
        ("html parse", lambda: check_html(files)),
        ("consolidation coverage", lambda: check_consolidation_coverage(files)),
        ("environment hygiene", lambda: check_env_hygiene(files)),
    ]

    for label, check in checks:
        check()
        print(f"ok - {label}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SmokeTestError, subprocess.CalledProcessError, SyntaxError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
