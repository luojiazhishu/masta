"""Minimal ANSI helper for MASTA console output.

Colour codes degrade to empty strings when stdout is not a TTY, when
``NO_COLOR`` is set, or when ``TERM=dumb`` — so piping into ``less`` or
redirecting to a file produces clean text. Direct use only inside
``print`` statements; structured records in ``events.jsonl`` MUST stay
plain text.
"""

from __future__ import annotations

import os
import sys
import threading
import time


_USE_COLOR = (
    sys.stdout.isatty()
    and os.environ.get("NO_COLOR") is None
    and os.environ.get("TERM", "") != "dumb"
)


def _code(seq: str) -> str:
    return f"\033[{seq}m" if _USE_COLOR else ""


RESET = _code("0")
BOLD = _code("1")
DIM = _code("2")
RED = _code("31")
GREEN = _code("32")
YELLOW = _code("33")
BLUE = _code("34")
MAGENTA = _code("35")
CYAN = _code("36")
GREY = _code("90")


def paint(text: str, *codes: str) -> str:
    """Wrap ``text`` in the given ANSI codes, no-op when colour is off."""
    if not _USE_COLOR:
        return text
    prefix = "".join(c for c in codes if c)
    if not prefix:
        return text
    return prefix + text + RESET


_AGENT_COLOURS: dict[str, str] = {
    "architect": BLUE,
    "auditor": GREEN,
    "arbiter": MAGENTA,
}

_PREFIX_WIDTH = 11  # widest tag is "[architect]"


def _pad(label: str) -> str:
    return label.ljust(_PREFIX_WIDTH)


def masta_prefix() -> str:
    return paint(_pad("[masta]"), BOLD, CYAN)


def llm_prefix() -> str:
    return paint(_pad("[llm]"), DIM, GREY)


def agent_prefix(name: str) -> str:
    return paint(_pad(f"[{name}]"), _AGENT_COLOURS.get(name, ""))


def log(message: str) -> None:
    """Print one console line prefixed with an ``HH:MM:SS`` timestamp.

    Use this everywhere instead of bare ``print`` so MASTA's runtime
    output keeps a consistent ``<time> <prefix> <body>`` shape.
    """
    print(f"{paint(time.strftime('%H:%M:%S'), GREY)} {message}")


_PENDING_LOCK = threading.Lock()


class Pending:
    """An animated, in-place log line.

    On a TTY: prints ``HH:MM:SS <message> .`` and a daemon thread
    rotates the trailing dots every ~0.4s. ``done(final_message)``
    cancels the animation, clears the line, and prints
    ``HH:MM:SS <final_message>`` followed by a newline.

    Off a TTY (pipe, file redirect, NO_COLOR, etc.): no animation —
    the start message is printed once with a static ``...`` suffix
    and ``done()`` prints the final message on a new line, so logs
    redirected to disk stay readable as plain text.
    """

    def __init__(self, message: str) -> None:
        self._stamp = paint(time.strftime("%H:%M:%S"), GREY)
        self._message = message
        self._done = threading.Event()
        self._thread: threading.Thread | None = None
        self._tty = _USE_COLOR
        self._closed = False

    def start(self) -> "Pending":
        if not self._tty:
            with _PENDING_LOCK:
                sys.stdout.write(f"{self._stamp} {self._message} ...\n")
                sys.stdout.flush()
            return self
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        i = 0
        while not self._done.is_set():
            dots = "." * ((i % 3) + 1)
            with _PENDING_LOCK:
                sys.stdout.write(
                    f"\r\033[K{self._stamp} {self._message} {dots}"
                )
                sys.stdout.flush()
            i += 1
            self._done.wait(0.4)

    def done(self, final_message: str) -> None:
        if self._closed:
            return
        self._closed = True
        if not self._tty:
            with _PENDING_LOCK:
                sys.stdout.write(f"{self._stamp} {final_message}\n")
                sys.stdout.flush()
            return
        self._done.set()
        if self._thread is not None:
            self._thread.join()
        with _PENDING_LOCK:
            sys.stdout.write(
                f"\r\033[K{self._stamp} {final_message}\n"
            )
            sys.stdout.flush()


def pending(message: str) -> Pending:
    """Start an animated pending line; the caller must invoke
    ``done(final_message)`` when the underlying work returns."""
    return Pending(message).start()
