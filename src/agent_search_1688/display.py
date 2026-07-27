"""Hermes-compatible terminal status display."""

from __future__ import annotations

import os
import random
import sys
import threading
import time
from typing import TextIO


class HermesThinkingSpinner:
    """Render Hermes' quiet-mode thinking animation on a terminal."""

    SPINNERS = {
        "dots": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
        "star": ["✶", "✷", "✸", "✹", "✺", "✹", "✸", "✷"],
        "moon": ["🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘"],
        "pulse": ["◜", "◠", "◝", "◞", "◡", "◟"],
        "brain": ["🧠", "💭", "💡", "✨", "💫", "🌟", "💡", "💭"],
        "sparkle": ["⁺", "˚", "*", "✧", "✦", "✧", "*", "˚"],
    }

    THINKING_FACES = [
        "(｡•́︿•̀｡)",
        "(◔_◔)",
        "(¬‿¬)",
        "( •_•)>⌐■-■",
        "(⌐■_■)",
        "(´･_･`)",
        "◉_◉",
        "(°ロ°)",
        "( ˘⌣˘)♡",
        "ヽ(>∀<☆)☆",
        "٩(๑❛ᴗ❛๑)۶",
        "(⊙_⊙)",
        "(¬_¬)",
        "( ͡° ͜ʖ ͡°)",
        "ಠ_ಠ",
    ]

    THINKING_VERBS = [
        "pondering",
        "contemplating",
        "musing",
        "cogitating",
        "ruminating",
        "deliberating",
        "mulling",
        "reflecting",
        "processing",
        "reasoning",
        "analyzing",
        "computing",
        "synthesizing",
        "formulating",
        "brainstorming",
    ]

    def __init__(
        self,
        message: str,
        spinner_type: str = "dots",
        *,
        output: TextIO | None = None,
    ) -> None:
        self.message = message
        self.spinner_frames = self.SPINNERS.get(
            spinner_type,
            self.SPINNERS["dots"],
        )
        self.running = False
        self.thread: threading.Thread | None = None
        self.frame_idx = 0
        self.start_time: float | None = None
        self.last_line_len = 0
        # Capture the stream before another component can redirect sys.stdout.
        self._out = output if output is not None else sys.stdout

    @classmethod
    def create_for_model_request(cls) -> "HermesThinkingSpinner":
        """Create the same randomized quiet-mode status used by Hermes."""

        face = random.choice(cls.THINKING_FACES)
        verb = random.choice(cls.THINKING_VERBS)
        spinner_type = random.choice(
            ["brain", "sparkle", "pulse", "moon", "star"]
        )
        return cls(f"{face} {verb}...", spinner_type=spinner_type)

    def _write(self, text: str, *, end: str = "\n", flush: bool = False) -> None:
        try:
            self._out.write(text + end)
            if flush:
                self._out.flush()
        except (OSError, ValueError):
            pass

    @property
    def _is_tty(self) -> bool:
        try:
            return hasattr(self._out, "isatty") and self._out.isatty()
        except (OSError, ValueError):
            return False

    def _animate(self) -> None:
        if not self._is_tty:
            self._write(f"  [tool] {self.message}", flush=True)
            while self.running:
                time.sleep(0.5)
            return

        while self.running:
            if os.getenv("HERMES_SPINNER_PAUSE"):
                time.sleep(0.1)
                continue
            frame = self.spinner_frames[self.frame_idx % len(self.spinner_frames)]
            assert self.start_time is not None
            elapsed = time.time() - self.start_time
            line = f"  {frame} {self.message} ({elapsed:.1f}s)"
            pad = max(self.last_line_len - len(line), 0)
            self._write(f"\r{line}{' ' * pad}", end="", flush=True)
            self.last_line_len = len(line)
            self.frame_idx += 1
            time.sleep(0.12)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.start_time = time.time()
        self.thread = threading.Thread(target=self._animate, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.running = False
        if self.thread:
            self.thread.join(timeout=0.5)
        if self._is_tty:
            blanks = " " * max(self.last_line_len + 5, 40)
            self._write(f"\r{blanks}\r", end="", flush=True)
