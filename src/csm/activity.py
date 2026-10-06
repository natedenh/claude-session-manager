"""A one-line strip that moves while any session is working."""
from __future__ import annotations

import math

from rich.text import Text
from textual.widgets import Static

BARS = "▁▂▃▄▅▆▇█"
FPS = 12


def wave(width: int, phase: float) -> str:
    """Two sines at different speeds, so the wave drifts instead of looking like a fixed loop."""
    out = []
    for x in range(width):
        y = 0.65 * math.sin(x * 0.22 - phase) + 0.35 * math.sin(x * 0.07 + phase * 0.45)
        out.append(BARS[min(len(BARS) - 1, int((y + 1) / 2 * len(BARS)))])
    return "".join(out)


class Activity(Static):
    """Shows "N working" and an animated wave while sessions are busy; a flat line otherwise."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.busy = 0
        self.phase = 0.0

    def on_mount(self) -> None:
        # Paused while nothing is working, so an idle csm doesn't redraw at all.
        self.timer = self.set_interval(1 / FPS, self.tick, pause=True)
        self.draw()

    def set_busy(self, n: int) -> None:
        if n == self.busy:
            return
        self.busy = n
        self.timer.resume() if n else self.timer.pause()
        self.draw()

    def tick(self) -> None:
        self.phase += 0.35
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def draw(self) -> None:
        width = max(0, self.content_size.width)
        if not self.busy:
            self.update(Text(BARS[0] * width, style="dim"))
            return
        label = f"{self.busy} working "
        self.update(Text.assemble((label, "bold yellow"), (wave(max(0, width - len(label)), self.phase), "yellow")))
