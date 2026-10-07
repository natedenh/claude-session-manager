"""A one-line strip that moves while any session is working, in a few styles to pick from."""
from __future__ import annotations

import math

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.widgets import Static

LINE = "⎽⎼─⎻⎺"  # scan lines, low to high: one thin line at five heights
BARS = " ▁▂▃▄▅▆▇█"
FPS = 15
SPARK = (0xD9, 0x77, 0x57)  # Claude's coral
EMBER = (0xB8, 0x9A, 0x5E)  # warm amber the glow fades out through
TAIL = 14  # cells of fading glow behind each spark
DEMO = 3.0  # seconds a newly picked style plays when nothing is working
# Braille dots by (column, row from the top), two columns by four rows per cell.
DOTS = ((0x01, 0x02, 0x04, 0x40), (0x08, 0x10, 0x20, 0x80))


def wave(width: int, phase: float, amp: float = 1.0, energy: float = 1.0) -> str:
    """Two sines at different speeds, so the wave drifts instead of looking like a fixed loop.
    `amp` scales the height (0 is a flat line); `energy` tightens and raises it a little."""
    out = []
    for x in range(width):
        y = 0.65 * math.sin(x * 0.22 * energy - phase) + 0.35 * math.sin(x * 0.07 + phase * 0.45)
        y = max(-1.0, min(1.0, y * amp * min(1.0, 0.75 + 0.25 * energy)))
        out.append(LINE[min(len(LINE) - 1, int((y + 1) / 2 * len(LINE)))])
    return "".join(out)


def strands(width: int, phase: float, amp: float = 1.0, n: int = 1) -> str:
    """One smooth braille sine per working session (up to four), each at its own speed, woven together."""
    cells = [0] * width
    for k in range(max(1, min(n, 4))):
        speed, freq, shift = 1 + 0.35 * k, 0.11 + 0.025 * k, k * 2.1
        for sx in range(width * 2):
            y = math.sin(sx * freq - phase * speed + shift) * amp
            row = min(3, max(0, round((1 - y) * 1.5)))  # 0 is the top row
            cells[sx // 2] |= DOTS[sx % 2][row]
    return "".join(chr(0x2800 + c) for c in cells)


def equalizer(width: int, phase: float, amp: float = 1.0, energy: float = 1.0) -> str:
    """Bars bouncing like a music visualizer: busier sessions, livelier bars."""
    out = []
    for x in range(width):
        h = (0.5 + 0.5 * math.sin(x * 0.9 + phase * 1.7)) * (0.55 + 0.45 * math.sin(x * 0.13 - phase * 0.6 * energy))
        h += 0.25 * (0.5 + 0.5 * math.sin(x * 2.3 + phase * 3.1))  # flicker
        out.append(BARS[min(len(BARS) - 1, max(1, round(h * amp * (len(BARS) - 1))))])
    return "".join(out)


def heartbeat(width: int, phase: float, amp: float = 1.0, beats: int = 1) -> tuple[str, list[float]]:
    """A flat trace with a beat travelling along it, like a monitor; one beat per working session."""
    shape = (3, 4, 0, 1, 3)  # LINE heights across a beat: up, spike, dip, recover
    out, light = [LINE[2]] * width, [0.0] * width
    if width <= 0:
        return "", light
    span = width + TAIL
    for k in range(max(1, min(beats, 3))):
        head = int(phase * 5 + k * span / max(1, min(beats, 3))) % span
        for i, level in enumerate(reversed(shape)):  # the newest part of the trace is on the right
            x = head - i
            if 0 <= x < width and amp > 0.3:
                out[x] = LINE[level]
        for d in range(TAIL):
            if 0 <= (x := head - d) < width:
                light[x] = max(light[x], (1 - d / TAIL) ** 1.6)
    return "".join(out), light


def starfield(width: int, phase: float, amp: float = 1.0, n: int = 1) -> tuple[str, list[float]]:
    """Stars twinkling at their own pace; more of them the more sessions are working."""
    marks = "·∙•✦"
    out, light = [], []
    density = 0.18 + 0.08 * min(n, 4)
    for x in range(width):
        seed = math.sin(x * 12.9898) * 43758.5453 % 1  # a fixed, scattered per-cell value, so stars stay put
        if seed > density * amp:
            out.append(" ")
            light.append(0.0)
            continue
        t = 0.5 + 0.5 * math.sin(phase * (0.6 + seed * 2.5) + seed * 40)
        out.append(marks[min(3, int(t * 4))])
        light.append(t ** 3)
    return "".join(out), light


STYLES = ("wave", "strands", "equalizer", "heartbeat", "stars")


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
    """Shows "N working" and a moving line while sessions are busy; a flat line otherwise.
    Click it (or press ~) to change the style."""

    def __init__(self, style: str = "wave", **kwargs):
        super().__init__(**kwargs)
        self.busy = 0
        self.phase = 0.0
        self.amp = 0.0  # eases toward 1 while busy and back to 0 when idle
        self.kind = style if style in STYLES else STYLES[0]
        self.demo = 0.0  # seconds left of playing a just-picked style while idle

    def on_mount(self) -> None:
        # Paused while nothing is working (and the line has settled), so an idle csm doesn't redraw.
        self.timer = self.set_interval(1 / FPS, self.tick, pause=True)
        self.draw()

    def set_busy(self, n: int) -> None:
        if n == self.busy:
            return
        self.busy = n
        self.timer.resume()  # also when stopping, to let the line settle
        self.draw()

    def set_style(self, kind: str) -> None:
        """Switch styles; with nothing working, play it for a few seconds so you can see it."""
        self.kind = kind if kind in STYLES else STYLES[0]
        self.demo = DEMO  # also names the style for a moment
        self.timer.resume()
        self.draw()

    @property
    def active(self) -> bool:
        return bool(self.busy or self.demo > 0)

    def tick(self) -> None:
        self.demo = max(0.0, self.demo - 1 / FPS)
        energy = 1 + 0.15 * min(self.busy, 4)
        self.phase += 0.3 * energy
        self.amp += ((1.0 if self.active else 0.0) - self.amp) * 0.12
        if not self.active and self.amp < 0.02:
            self.amp = 0.0
            self.timer.pause()
        self.draw()

    def on_resize(self) -> None:
        self.draw()

    def on_click(self) -> None:
        self.app.action_next_wave()

    def frame(self, n: int) -> tuple[str, list[float]]:
        busy = max(1, self.busy)
        energy = 1 + 0.15 * min(self.busy, 4)
        sparks = 1 + (self.busy >= 3)
        if self.kind == "strands":
            return strands(n, self.phase * 0.5, self.amp, busy), glow(n, self.phase, sparks)
        if self.kind == "equalizer":
            return equalizer(n, self.phase, self.amp, energy), glow(n, self.phase, sparks)
        if self.kind == "heartbeat":
            return heartbeat(n, self.phase, self.amp, busy)
        if self.kind == "stars":
            return starfield(n, self.phase, self.amp, busy)
        return wave(n, self.phase, self.amp, energy), glow(n, self.phase, sparks)

    def draw(self) -> None:
        width = max(0, self.content_size.width)
        if not self.active and self.amp == 0.0:
            self.update(Text(LINE[2] * width, style="dim"))
            return
        label = (f"{self.busy} working " if self.active else "") + (f"· {self.kind} " if self.demo else "")
        n = max(0, width - len(label))
        line, light = self.frame(n)
        text = Text(label, style="warn")
        unlit = UNLIT if self.app.current_theme.dark else UNLIT_ON_LIGHT
        for ch, g in zip(line, light):
            g *= self.amp
            text.append(ch, unlit if g < 0.12 else lit(g))
        self.update(text)


UNLIT = Style(color="yellow", dim=True)  # the terminal's own yellow, like the rest of csm
UNLIT_ON_LIGHT = Style(color=Color.parse("#b0905a"))  # light themes often make yellow too pale to see


def lit(g: float) -> Style:
    rgb = tuple(round(a + (b - a) * g) for a, b in zip(EMBER, SPARK))
    return Style(color=Color.from_rgb(*rgb), bold=g > 0.6)
