#!/usr/bin/env python3
"""Interactive chat CLI for the markdown-rag API.

Usage:
    python3 chat.py [--url http://localhost:8000]

Streams responses from /query/stream, rendering <think>…</think> blocks
as a dimmed indicator so the terminal shows immediate feedback while the
model reasons, then prints the actual answer token-by-token.
"""
from __future__ import annotations

import argparse
import sys
import readline  # noqa: F401 — imported for side-effect (line editing + history)

import httpx

STREAM_PATH = "/query/stream"
HEALTH_PATH = "/health"

ANSI_DIM   = "\033[2m"
ANSI_RESET = "\033[0m"
OPEN_TAG   = "<think>"
CLOSE_TAG  = "</think>"


def check_health(base_url: str) -> bool:
    try:
        r = httpx.get(base_url + HEALTH_PATH, timeout=60)
        return r.status_code == 200
    except Exception:
        return False


def stream_question(base_url: str, question: str) -> None:
    """POST question and stream the response, separating thinking from content."""
    timeout = httpx.Timeout(connect=10.0, read=600.0, write=30.0, pool=10.0)

    in_think = False
    think_shown = False
    buf = ""

    def flush_content(text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def flush_buf_to(tag: str) -> tuple[str, str]:
        """Return (before_tag, rest_of_buf) splitting buf on tag, or ('', buf) if not found."""
        idx = buf.find(tag)
        if idx == -1:
            return "", buf
        return buf[:idx], buf[idx + len(tag):]

    with httpx.stream(
        "POST",
        base_url + STREAM_PATH,
        json={"question": question},
        timeout=timeout,
    ) as resp:
        resp.raise_for_status()
        for raw in resp.iter_raw(chunk_size=64):
            chunk = raw.decode("utf-8", errors="replace")
            buf += chunk

            # Keep processing while there's something actionable in the buffer.
            while True:
                if not in_think:
                    idx = buf.find(OPEN_TAG)
                    if idx > 0:
                        # Content before the opening tag — print it.
                        flush_content(buf[:idx])
                        buf = buf[idx:]
                    elif idx == 0:
                        # Opening tag at the start of buf.
                        if not think_shown:
                            print(f"{ANSI_DIM}[thinking...]{ANSI_RESET}", file=sys.stderr, flush=True)
                            think_shown = True
                        in_think = True
                        buf = buf[len(OPEN_TAG):]
                    else:
                        # No opening tag — safe to flush all but the last few chars
                        # (in case the tag straddles a chunk boundary).
                        safe = len(buf) - len(OPEN_TAG) + 1
                        if safe > 0:
                            flush_content(buf[:safe])
                            buf = buf[safe:]
                        break
                else:
                    idx = buf.find(CLOSE_TAG)
                    if idx >= 0:
                        in_think = False
                        buf = buf[idx + len(CLOSE_TAG):]
                        # Strip the single newline the server emits after </think>.
                        if buf.startswith("\n"):
                            buf = buf[1:]
                    else:
                        # Still inside thinking block — discard content, keep
                        # a tail long enough to catch a straddling close tag.
                        keep = len(CLOSE_TAG) - 1
                        buf = buf[-keep:] if len(buf) > keep else buf
                        break

    # Flush any remaining content after the stream closes.
    if buf and not in_think:
        flush_content(buf)


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with the markdown-rag API")
    parser.add_argument("--url", default="http://localhost:8000", help="RAG API base URL")
    args = parser.parse_args()
    base_url = args.url.rstrip("/")

    if not check_health(base_url):
        print(f"Error: API not reachable at {base_url}{HEALTH_PATH}", file=sys.stderr)
        sys.exit(1)

    print("Type your questions. Press Ctrl-C or Ctrl-D to exit.\n")

    while True:
        try:
            question = input("Q: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue

        print("---")
        try:
            stream_question(base_url, question)
        except httpx.HTTPStatusError as exc:
            print(f"\n[Error] HTTP {exc.response.status_code}", file=sys.stderr)
        except httpx.RequestError as exc:
            print(f"\n[Error] {exc}", file=sys.stderr)
        print("\n---\n")


if __name__ == "__main__":
    main()
