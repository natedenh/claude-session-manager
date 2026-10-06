"""A one-line strip that moves while any session is working."""
from __future__ import annotations

import math

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

LINE = "⎽⎼─⎻⎺"  # scan lines, low to high: one thin line at five heights
FPS = 15
SPARK = (0xD9, 0x77, 0x57)  # Claude's coral
EMBER = (0xB8, 0x9A, 0x5E)  # warm amber the glow fades out through
TAIL = 14  # cells of fading glow behind each spark


def wave(width: int, phase: float, amp: float = 1.0, energy: float = 1.0) -> str:
    """Two sines at different speeds, so the wave drifts instead of looking like a fixed loop.
    `amp` scales the height (0 is a flat line); `energy` tightens and raises it a little."""
    out = []
    for x in range(width):
        y = 0.65 * math.sin(x * 0.22 * energy - phase) + 0.35 * math.sin(x * 0.07 + phase * 0.45)
        y = max(-1.0, min(1.0, y * amp * min(1.0, 0.75 + 0.25 * energy)))
        out.append(LINE[min(len(LINE) - 1, int((y + 1) / 2 * len(LINE)))])
    return "".join(out)


def glow(width: int, phase: float, sparks: int) -> list[float]:
    """0..1 brightness per cell: sparks sweeping left to right, each with a fading tail."""
    out = [0.0] * width
    if width <= 0:
        return out
    span = width + TAIL
    for k in range(sparks):
        head = (phase * 6 + k * span / sparks) % span
        for d in range(TAIL):
            x = int(head) - d
            if 0 <= x < width:
                out[x] = max(out[x], (1 - d / TAIL) ** 1.6)
    return out


class Activity(Static):
    """Shows "N working" and a moving wavy line while sessions are busy; a flat line otherwise."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.busy = 0
        self.phase = 0.0
        self.amp = 0.0  # eases toward 1 while busy and back to 0 when idle

    def on_mount(self) -> None:
        # Paused while nothing is working (and the wave has settled), so an idle csm doesn't redraw.
        self.timer = self.set_interval(1 / FPS, self.tick, pause=True)
        self.draw()

    def set_busy(self, n: int) -> None:
        if n == self.busy:
            return
        self.busy = n
        self.timer.resume()  # also when stopping, to let the wave settle
        self.draw()

    def tick(self) -> None:
        energy = 1 + 0.15 * min(self.busy, 4)
        self.phase += 0.3 * energy
        self.amp += ((1.0 if self.busy else 0.0) - self.amp) * 0.12
        if not self.busy and self.amp < 0.02:
            self.amp = 0.0
            self.timer.pause()
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def draw(self) -> None:
        width = max(0, self.content_size.width)
        if not self.busy and self.amp == 0.0:
            self.update(Text(LINE[2] * width, style="dim"))
            return
        label = f"{self.busy} working " if self.busy else ""
        n = max(0, width - len(label))
        energy = 1 + 0.15 * min(self.busy, 4)
        line = wave(n, self.phase, self.amp, energy)
        light = glow(n, self.phase, 1 + (self.busy >= 3))
        text = Text(label, style="yellow")
        for ch, g in zip(line, light):
            g *= self.amp
            text.append(ch, UNLIT if g < 0.12 else lit(g))
        self.update(text)


UNLIT = Style(color="yellow", dim=True)  # the terminal's own yellow, like the rest of csm


def lit(g: float) -> Style:
    rgb = tuple(round(a + (b - a) * g) for a, b in zip(EMBER, SPARK))
    return Style(color=Color.from_rgb(*rgb), bold=g > 0.6)
