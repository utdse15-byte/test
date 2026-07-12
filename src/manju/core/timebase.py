"""Rational time, SMPTE timecode and int-grid drift analysis — the pure-math
foundation for a future ``manju.media-technical-profile`` (roadmap §5.1 / §15.1).

Why this module exists
----------------------
Today the engine destroys frame-rate rationality at three points:

* ``ProjectConfig.fps`` is an ``int`` (``core/models.py``) — an edit rate of
  ``24000/1001`` (NTSC 23.976) is simply unrepresentable.
* ``media/probe.py`` parses ffprobe's ``r_frame_rate`` fraction straight into a
  ``float`` — ``24000/1001`` becomes ``23.976023976…`` and the exact ratio is
  gone.
* ``timeline/compiler.snap_to_frame_grid(duration_ms, fps:int)`` snaps on a
  millisecond × integer-fps grid.

That is fine for whole-number rates but silently wrong for the NTSC 1001
family, and the error accumulates across a long concat. This module is the
**foundation only**: a self-contained, exact-integer time library that a future
governed migration can adopt. It changes *nothing* in the engine.

Design rules honoured here
--------------------------
* **Exact integer math via** :class:`fractions.Fraction` **everywhere.** No
  ``float`` ever appears in a conversion path. Floats are confined to two
  clearly-labelled display/parse surfaces: :attr:`Rate.fps_float` and the
  *input* of :func:`classify_rate` / :func:`rate_mode`.
* **Explicit rounding.** Every frame/ms/sample conversion takes a
  :class:`Rounding` policy — there is no hidden banker's rounding.
* **Honesty over convenience.** Unknown decimals are refused, never snapped to a
  nearby standard; drop-frame is legal only where the standard permits it; and
  where an invariant cannot hold (millisecond granularity vs 1001 rates) the
  docstring and a test state the exact bound instead of pretending.

Dependency direction: this module imports only the standard library. Nothing in
the engine imports it this loop (one-way: ``timebase`` is importable by future
code and depends on nothing).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction

__all__ = [
    "RANGE_SEMANTICS",
    "UNKNOWN",
    "Rate",
    "Rounding",
    "Timecode",
    "classify_rate",
    "frames_to_ms",
    "frames_to_samples",
    "grid_drift_ms",
    "ms_to_frames",
    "one_frame_drift_at",
    "parse_timecode",
    "rate_mode",
    "sample_alignment_period",
    "samples_per_frame",
    "samples_to_frames",
]

# Module-wide range convention, matching the existing cutdown/timeline contract
# (roadmap §5.1 ``range_semantics: start_inclusive_end_exclusive``). A range
# [start, end) contains ``start`` and every frame up to but NOT including
# ``end``; an empty range is start == end.
RANGE_SEMANTICS = "start_inclusive_end_exclusive"

# The NTSC ("1001") family. A decimal alias is only ever accepted if it appears
# in this fixed table — there is no float guessing. Keys are the *exact* strings
# a human or tool writes; values are the exact fractions they denote.
_DECIMAL_ALIASES: dict[str, Fraction] = {
    "23.976": Fraction(24000, 1001),
    "23.98": Fraction(24000, 1001),
    "29.97": Fraction(30000, 1001),
    "47.952": Fraction(48000, 1001),
    "59.94": Fraction(60000, 1001),
    "119.88": Fraction(120000, 1001),
}


class Rounding(Enum):
    """Explicit rounding policy for every frame/ms/sample conversion.

    * :attr:`FLOOR` — round toward −∞ (``math.floor``).
    * :attr:`CEIL` — round toward +∞ (``math.ceil``).
    * :attr:`ROUND_HALF_UP` — round to nearest; ties break toward +∞
      (equivalently *away from zero* for the non-negative durations this module
      deals in). This is **not** Python's built-in banker's rounding.
    """

    FLOOR = "floor"
    ROUND_HALF_UP = "round_half_up"
    CEIL = "ceil"


def _round(value: Fraction, rounding: Rounding) -> int:
    """Round an exact :class:`Fraction` to ``int`` under ``rounding``.

    Uses only exact integer arithmetic — ``math.floor``/``math.ceil`` on a
    ``Fraction`` return exact ``int`` results, and the half-up tie rule is
    ``floor(value + 1/2)``.
    """
    if rounding is Rounding.FLOOR:
        return math.floor(value)
    if rounding is Rounding.CEIL:
        return math.ceil(value)
    if rounding is Rounding.ROUND_HALF_UP:
        return math.floor(value + Fraction(1, 2))
    raise ValueError(f"unknown rounding policy: {rounding!r}")


class Rate:
    """An exact rational frame rate (frames per second).

    A :class:`Rate` wraps a positive :class:`fractions.Fraction`. Construct one
    with :meth:`from_fraction` or :meth:`parse`; the value is always stored in
    lowest terms so equality and hashing are canonical
    (``24000/1001 == 48000/2002``).

    Examples::

        Rate.parse("24000/1001")   # NTSC 23.976
        Rate.parse("24")           # film, exact
        Rate.parse("29.97")        # -> 30000/1001 (alias, exact)
        Rate.from_fraction(30, 1)  # 30 fps exact
    """

    __slots__ = ("_frac",)

    def __init__(self, fraction: Fraction) -> None:
        if not isinstance(fraction, Fraction):
            raise TypeError("Rate() takes a fractions.Fraction; use from_fraction/parse")
        if fraction <= 0:
            raise ValueError(f"frame rate must be positive, got {fraction}")
        self._frac = fraction

    # -- constructors ------------------------------------------------------- #

    @classmethod
    def from_fraction(cls, num: int, den: int = 1) -> "Rate":
        """Build a :class:`Rate` from an explicit numerator/denominator.

        The fraction is normalised to lowest terms. ``den == 0`` or a
        non-positive result raises :class:`ValueError`.
        """
        if not isinstance(num, int) or not isinstance(den, int):
            raise TypeError("from_fraction requires integer num/den")
        if den == 0:
            raise ValueError("frame-rate denominator must be non-zero")
        frac = Fraction(num, den)  # normalises + carries sign into numerator
        if frac <= 0:
            raise ValueError(f"frame rate must be positive, got {num}/{den}")
        return cls(frac)

    @classmethod
    def parse(cls, text: str) -> "Rate":
        """Parse a human/tool spelling of a rate into an exact :class:`Rate`.

        Accepted forms (STRICT — this is explicit user input):

        * ``"num/den"`` fraction, e.g. ``"24000/1001"``;
        * a plain integer, e.g. ``"24"``;
        * an exact-integer decimal, e.g. ``"24.0"``;
        * one of the well-known NTSC decimal aliases (``"23.976"``, ``"29.97"``,
          ``"59.94"``, …) which map to their EXACT fraction.

        Any other decimal is **refused** with :class:`ValueError` — there is no
        float guessing (``"23.976023976"`` and ``"23.5"`` both raise). Use
        :func:`classify_rate` for lenient, tolerance-based classification of
        messy probe values.
        """
        if not isinstance(text, str):
            raise TypeError("Rate.parse expects a string")
        s = text.strip()
        if not s:
            raise ValueError("empty rate string")
        if "/" in s:
            num_s, _, den_s = s.partition("/")
            try:
                num, den = int(num_s), int(den_s)
            except ValueError as exc:
                raise ValueError(f"malformed fraction rate: {text!r}") from exc
            return cls.from_fraction(num, den)
        if "." not in s:
            try:
                return cls.from_fraction(int(s), 1)
            except ValueError as exc:
                raise ValueError(f"malformed integer rate: {text!r}") from exc
        # decimal: alias table first, then exact-integer decimals, else refuse.
        if s in _DECIMAL_ALIASES:
            return cls(_DECIMAL_ALIASES[s])
        int_part, _, frac_part = s.partition(".")
        if frac_part.strip("0") == "" and int_part.lstrip("+-").isdigit():
            return cls.from_fraction(int(int_part), 1)
        raise ValueError(
            f"refusing to guess an exact rate from decimal {text!r} "
            f"(known aliases: {sorted(_DECIMAL_ALIASES)})"
        )

    # -- properties --------------------------------------------------------- #

    @property
    def fraction(self) -> Fraction:
        """The exact rate as a :class:`fractions.Fraction` (lowest terms)."""
        return self._frac

    @property
    def numerator(self) -> int:
        return self._frac.numerator

    @property
    def denominator(self) -> int:
        return self._frac.denominator

    @property
    def fps_float(self) -> float:
        """Display-only float approximation. NEVER use in a conversion path."""
        return self._frac.numerator / self._frac.denominator

    @property
    def is_ntsc(self) -> bool:
        """True for the NTSC "1001" family (denominator a multiple of 1001)."""
        return self._frac.denominator % 1001 == 0

    @property
    def exact_int(self) -> int | None:
        """The integer fps if the rate is a whole number, else ``None``."""
        return self._frac.numerator if self._frac.denominator == 1 else None

    @property
    def nominal_int(self) -> int:
        """The integer timecode-labelling rate (fps rounded to nearest).

        24000/1001 → 24, 30000/1001 → 30, 60000/1001 → 60, 25 → 25. This is the
        number of frame *labels* per timecode-second (the ``FF`` field range),
        which for NTSC differs from the true playback rate — the gap is exactly
        what drop-frame timecode compensates for.
        """
        return math.floor(self._frac + Fraction(1, 2))

    # -- dunder ------------------------------------------------------------- #

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Rate) and other._frac == self._frac

    def __hash__(self) -> int:
        return hash((Rate, self._frac))

    def __repr__(self) -> str:
        return f"Rate({self._frac.numerator}/{self._frac.denominator})"

    def __str__(self) -> str:
        if self._frac.denominator == 1:
            return str(self._frac.numerator)
        return f"{self._frac.numerator}/{self._frac.denominator}"


# --------------------------------------------------------------------------- #
# frame / millisecond / sample conversions — exact, explicit rounding          #
# --------------------------------------------------------------------------- #

def frames_to_ms(frames: int, rate: Rate, rounding: Rounding = Rounding.ROUND_HALF_UP) -> int:
    """Milliseconds spanned by ``frames`` frames at ``rate`` (exact, then round).

    The exact duration is ``frames * 1000 * den / num`` ms, computed as a
    :class:`Fraction` and rounded once under ``rounding``.

    Exactness note (the honest bound): the result is exact only when
    ``1000/fps`` is an integer (e.g. 25 fps → 40 ms, 50 fps → 20 ms). For 24,
    30, 60 and the whole NTSC 1001 family a frame boundary never lands on a whole
    millisecond, so the value carries up to ½ ms of rounding error per call.
    Round-tripping through :func:`ms_to_frames` still recovers the frame index
    (see that function), but the millisecond timestamps themselves drift — which
    is precisely what :func:`grid_drift_ms` quantifies.
    """
    exact = Fraction(frames * 1000 * rate.denominator, rate.numerator)
    return _round(exact, rounding)


def ms_to_frames(ms: int, rate: Rate, rounding: Rounding = Rounding.ROUND_HALF_UP) -> int:
    """Frame count in ``ms`` milliseconds at ``rate`` (exact, then round).

    Exact value ``ms * num / (1000 * den)`` frames, rounded under ``rounding``.

    Invariant (proven by the test-suite for frames 0..10_000 and huge values at
    every supported rate): with :attr:`Rounding.ROUND_HALF_UP`,
    ``ms_to_frames(frames_to_ms(n)) == n`` for all n. The *reverse* trip
    ``frames_to_ms(ms_to_frames(ms))`` is deliberately NOT the identity — a frame
    index cannot carry the sub-frame millisecond a timestamp holds; the residual
    is bounded by half a frame period.
    """
    exact = Fraction(ms * rate.numerator, 1000 * rate.denominator)
    return _round(exact, rounding)


def samples_per_frame(rate: Rate, sample_rate: int) -> Fraction:
    """Exact audio samples per video frame — a :class:`Fraction`.

    ``sample_rate / fps`` = ``sample_rate * den / num``. For 48 kHz this is a
    whole number at every integer rate and at 24000/1001 (exactly 2002), but
    ``8008/5`` at 30000/1001 and ``4004/5`` at 60000/1001 — integer only every
    5th frame (the "1001-periodic" alignment).
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    return Fraction(sample_rate * rate.denominator, rate.numerator)


def sample_alignment_period(rate: Rate, sample_rate: int) -> int:
    """Smallest positive frame count whose sample count is a whole integer.

    This is the denominator of :func:`samples_per_frame` in lowest terms: 1 when
    every frame aligns (integer rates, and 24000/1001 @ 48 kHz), 5 for
    30000/1001 and 60000/1001 @ 48 kHz.
    """
    return samples_per_frame(rate, sample_rate).denominator


def frames_to_samples(
    frames: int, rate: Rate, sample_rate: int,
    rounding: Rounding = Rounding.ROUND_HALF_UP,
) -> int:
    """Audio samples spanned by ``frames`` frames (exact, then round).

    Exact when ``frames`` is a multiple of :func:`sample_alignment_period`
    (rounding never triggers); otherwise rounded under ``rounding``.
    """
    exact = Fraction(frames * sample_rate * rate.denominator, rate.numerator)
    return _round(exact, rounding)


def samples_to_frames(
    samples: int, rate: Rate, sample_rate: int,
    rounding: Rounding = Rounding.ROUND_HALF_UP,
) -> int:
    """Video frames spanned by ``samples`` audio samples (exact, then round)."""
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    exact = Fraction(samples * rate.numerator, sample_rate * rate.denominator)
    return _round(exact, rounding)


# --------------------------------------------------------------------------- #
# SMPTE timecode                                                               #
# --------------------------------------------------------------------------- #

# Drop-frame is defined ONLY for these two rates.
_DF_LEGAL = {Fraction(30000, 1001), Fraction(60000, 1001)}
_TC_SPLIT = re.compile(r"[:;.]")


def _df_drop_count(nominal: int) -> int:
    """Frame numbers dropped at the top of each dropped minute: 2 at 30, 4 at 60."""
    return 2 * (nominal // 30)


@dataclass(frozen=True)
class Timecode:
    """An SMPTE timecode value object bound to a :class:`Rate` and a drop flag.

    Fields are the four label components (``hours``/``minutes``/``seconds``/
    ``frames``) plus the ``rate`` and ``drop_frame`` they were computed against.
    Build one with :meth:`from_frames` or :func:`parse_timecode`; recover the
    frame index with :meth:`to_frames`; render it with ``str()`` — ``':'`` before
    the frames for non-drop, ``';'`` for drop-frame.

    Drop-frame is a pure *relabelling* of the integer frame stream that keeps the
    29.97/59.94 clock close to real time by skipping frame **numbers** (never
    actual frames): 2 (or 4) numbers at the top of every minute except every
    tenth. It is legal only for 30000/1001 and 60000/1001; requesting it on any
    other rate raises :class:`ValueError`. All timecode math is exact integer
    arithmetic and wraps at 24 h.
    """

    hours: int
    minutes: int
    seconds: int
    frames: int
    rate: Rate
    drop_frame: bool = False

    # -- construction ------------------------------------------------------- #

    @classmethod
    def from_frames(cls, frame: int, rate: Rate, drop_frame: bool = False) -> "Timecode":
        """Label ``frame`` (0-based, wrapping at 24 h) as a timecode."""
        nominal = rate.nominal_int
        if drop_frame:
            if rate.fraction not in _DF_LEGAL:
                raise ValueError(
                    f"drop-frame timecode is only legal for 30000/1001 and "
                    f"60000/1001, not {rate}"
                )
            h, m, s, f = _df_frames_to_fields(frame, nominal)
        else:
            frames_per_day = nominal * 3600 * 24
            n = frame % frames_per_day
            f = n % nominal
            total_seconds = n // nominal
            s = total_seconds % 60
            m = (total_seconds // 60) % 60
            h = (total_seconds // 3600) % 24
        return cls(h, m, s, f, rate, drop_frame)

    # -- inversion ---------------------------------------------------------- #

    def to_frames(self) -> int:
        """The 0-based frame index this timecode labels (inverse of from_frames)."""
        nominal = self.rate.nominal_int
        self._validate_fields(nominal)
        if self.drop_frame:
            return _df_fields_to_frames(
                self.hours, self.minutes, self.seconds, self.frames, nominal
            )
        return (
            ((self.hours * 60 + self.minutes) * 60 + self.seconds) * nominal
            + self.frames
        )

    def _validate_fields(self, nominal: int) -> None:
        if self.drop_frame and self.rate.fraction not in _DF_LEGAL:
            raise ValueError(
                f"drop-frame timecode is only legal for 30000/1001 and "
                f"60000/1001, not {self.rate}"
            )
        if not (0 <= self.frames < nominal):
            raise ValueError(f"frame field {self.frames} out of range for {self.rate}")
        if not (0 <= self.seconds < 60 and 0 <= self.minutes < 60 and 0 <= self.hours < 24):
            raise ValueError("timecode field out of range")
        if self.drop_frame:
            drop = _df_drop_count(nominal)
            if self.seconds == 0 and self.minutes % 10 != 0 and self.frames < drop:
                raise ValueError(
                    f"{self} is a dropped frame number that does not exist at "
                    f"{self.rate} drop-frame"
                )

    # -- rendering ---------------------------------------------------------- #

    def __str__(self) -> str:
        sep = ";" if self.drop_frame else ":"
        return (
            f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}"
            f"{sep}{self.frames:02d}"
        )


def _df_frames_to_fields(frame: int, nominal: int) -> tuple[int, int, int, int]:
    """Drop-frame encode: integer frame index → (h, m, s, f). Exact ints only."""
    drop = _df_drop_count(nominal)
    frames_per_10min = nominal * 600 - drop * 9          # e.g. 18000 - 18 = 17982
    frames_per_min = nominal * 60 - drop                 # e.g.  1800 -  2 =  1798
    # 24 h wrap on the *actual* drop-frame day length.
    frames_per_day = _df_fields_to_frames(24, 0, 0, 0, nominal)
    n = frame % frames_per_day

    tens = n // frames_per_10min
    rem = n % frames_per_10min
    if rem > drop:
        n += drop * 9 * tens + drop * ((rem - drop) // frames_per_min)
    else:
        n += drop * 9 * tens

    f = n % nominal
    s = (n // nominal) % 60
    m = ((n // nominal) // 60) % 60
    h = (((n // nominal) // 60) // 60) % 24
    return h, m, s, f


def _df_fields_to_frames(h: int, m: int, s: int, f: int, nominal: int) -> int:
    """Drop-frame decode: (h, m, s, f) → integer frame index. Exact ints only."""
    drop = _df_drop_count(nominal)
    total_minutes = 60 * h + m
    nominal_frames = (nominal * 3600 * h) + (nominal * 60 * m) + (nominal * s) + f
    return nominal_frames - drop * (total_minutes - total_minutes // 10)


def parse_timecode(text: str, rate: Rate) -> Timecode:
    """Parse ``"HH:MM:SS:FF"`` (non-drop) or ``"HH:MM:SS;FF"`` (drop-frame).

    A ``';'`` (or ``'.'``) before the frames field means drop-frame; ``':'``
    means non-drop. Requesting drop-frame on a non-DF-legal rate, or a timecode
    whose frame number was dropped and therefore does not exist, raises
    :class:`ValueError`.
    """
    if not isinstance(text, str):
        raise TypeError("parse_timecode expects a string")
    s = text.strip()
    drop_frame = ";" in s or "." in s
    parts = _TC_SPLIT.split(s)
    if len(parts) != 4:
        raise ValueError(f"malformed timecode: {text!r}")
    try:
        h, m, sec, f = (int(p) for p in parts)
    except ValueError as exc:
        raise ValueError(f"malformed timecode: {text!r}") from exc
    tc = Timecode(h, m, sec, f, rate, drop_frame)
    tc.to_frames()  # validates ranges, DF legality, and dropped-number rejection
    return tc


# --------------------------------------------------------------------------- #
# drift analysis — the honest bridge to today's int-fps ms grid                #
# --------------------------------------------------------------------------- #

def grid_drift_ms(duration_ms: int, edit_fps_int: int, true_rate: Rate) -> Fraction:
    """Cumulative timing error of the current int-fps grid vs the true rate.

    Today the engine lays a timeline at an integer ``edit_fps_int`` on the
    millisecond grid. If the material's true rate is ``true_rate`` (e.g. NTSC
    24000/1001) the two clocks diverge. Over a nominal ``duration_ms`` of grid
    time the accumulated error is, exactly::

        drift = duration_ms * (edit_fps_int - true_rate) / true_rate   (ms)

    The result is an EXACT :class:`Fraction`. It is **signed**: positive when the
    integer grid runs ahead of a slower true rate (the 23.976-on-24 case), which
    is the common NTSC-on-integer situation. For every NTSC-on-integer pairing
    (23.976/24, 29.97/30, 59.94/60) this is exactly 0.1 % — 1 ms per 1000 ms,
    i.e. 3.6 s per hour and 7.2 s over two hours.
    """
    if edit_fps_int <= 0:
        raise ValueError("edit_fps_int must be positive")
    r = true_rate.fraction
    return Fraction(duration_ms) * (edit_fps_int - r) / r


def one_frame_drift_at(edit_fps_int: int, true_rate: Rate) -> Fraction:
    """Grid duration (ms) at which :func:`grid_drift_ms` first reaches one frame.

    "One frame" is one edit-grid frame period, ``1000/edit_fps_int`` ms. Solving
    ``grid_drift_ms(D) == 1000/edit_fps_int`` gives, exactly::

        D = 1000 * true_rate / (edit_fps_int * (edit_fps_int - true_rate))   (ms)

    which for 23.976-on-24 is ``125000/3`` ms ≈ 41.667 s (exactly 1000 grid
    frames); for 29.97-on-30, ``1_000_000/30`` ms ≈ 33.333 s; for 59.94-on-60,
    ``1_000_000/60`` ms ≈ 16.667 s. Returned as an exact :class:`Fraction`.
    """
    if edit_fps_int <= 0:
        raise ValueError("edit_fps_int must be positive")
    r = true_rate.fraction
    gap = edit_fps_int - r
    if gap == 0:
        raise ValueError("no drift: edit rate equals true rate")
    frame_period = Fraction(1000, edit_fps_int)
    return frame_period * r / gap


# --------------------------------------------------------------------------- #
# honest classification of messy ffprobe values                               #
# --------------------------------------------------------------------------- #

class _Unknown:
    """Sentinel returned by :func:`classify_rate` when a value cannot be trusted.

    Falsy, with a stable repr, so callers can write ``if classify_rate(x):`` or
    ``classify_rate(x) is UNKNOWN``.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNKNOWN"

    def __bool__(self) -> bool:
        return False


UNKNOWN = _Unknown()

# The standard rates a float/decimal is allowed to classify onto. An exact
# fraction or integer input bypasses this table (it is already exact).
_KNOWN_RATES: tuple[Rate, ...] = (
    Rate.from_fraction(24, 1),
    Rate.from_fraction(24000, 1001),
    Rate.from_fraction(25, 1),
    Rate.from_fraction(30, 1),
    Rate.from_fraction(30000, 1001),
    Rate.from_fraction(48, 1),
    Rate.from_fraction(48000, 1001),
    Rate.from_fraction(50, 1),
    Rate.from_fraction(60, 1),
    Rate.from_fraction(60000, 1001),
    Rate.from_fraction(120, 1),
    Rate.from_fraction(120000, 1001),
)
# Absolute tolerance for matching a float to a known rate. The closest pair of
# standard rates (23.976 vs 24.0) is 0.024 apart, so 0.005 uniquely identifies a
# rate while still absorbing display roundings ("23.976", "23.98", "29.97").
_CLASSIFY_TOL = 5e-3


def _match_known_float(value: float) -> Rate | _Unknown:
    """Return the unique known rate within tolerance of ``value``, else UNKNOWN."""
    if value != value or value <= 0:  # NaN / non-positive guard
        return UNKNOWN
    hits = [r for r in _KNOWN_RATES if abs(value - r.fps_float) <= _CLASSIFY_TOL]
    return hits[0] if len(hits) == 1 else UNKNOWN


def classify_rate(value: object) -> Rate | _Unknown:
    """Map a messy ffprobe frame-rate value to an exact :class:`Rate`, or UNKNOWN.

    Honest bridge from probe output to rational time:

    * **Exact inputs** — an ``int``, an ``"num/den"`` fraction string, or a
      plain-integer string — parse straight to their exact rate (no guessing is
      involved).
    * **Decimal / float inputs** — a ``float`` or a decimal string — classify
      only if they land within a tight tolerance of exactly one known standard
      (24, 25, 30, 50, 60, 48, 120 and their NTSC 1001 partners). ``23.976`` and
      the raw float ``23.976023976…`` both resolve to ``24000/1001``; ``24.0``
      resolves to ``24``.

    Anything else — ``23.5``, ``33.333``, malformed strings, ``None`` — returns
    :data:`UNKNOWN`. It is never snapped to a nearby standard.
    """
    if isinstance(value, bool):  # bool is an int subclass — reject explicitly
        return UNKNOWN
    if isinstance(value, int):
        try:
            return Rate.from_fraction(value, 1)
        except ValueError:
            return UNKNOWN
    if isinstance(value, float):
        return _match_known_float(value)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return UNKNOWN
        if "/" in s:
            num_s, _, den_s = s.partition("/")
            try:
                return Rate.from_fraction(int(num_s), int(den_s))
            except (ValueError, TypeError):
                return UNKNOWN
        if "." not in s:
            try:
                return Rate.from_fraction(int(s), 1)
            except ValueError:
                return UNKNOWN
        try:
            return _match_known_float(float(s))
        except ValueError:
            return UNKNOWN
    return UNKNOWN


# --------------------------------------------------------------------------- #
# VFR honesty helper                                                           #
# --------------------------------------------------------------------------- #

def _to_fraction_or_none(value: str | None) -> Fraction | None:
    """Parse an ffprobe rate spelling to an exact Fraction, or None if unusable."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in ("0/0", "N/A"):
        return None
    try:
        if "/" in s:
            num_s, _, den_s = s.partition("/")
            den = int(den_s)
            if den == 0:
                return None
            return Fraction(int(num_s), den)
        return Fraction(s)  # handles "24" and exact decimals like "24.0"
    except (ValueError, ZeroDivisionError):
        return None


def rate_mode(r_frame_rate: str | None, avg_frame_rate: str | None) -> str:
    """Classify constant- vs variable-frame-rate from two ffprobe fields.

    Returns:

    * ``"cfr"`` — both fields parse and denote the *same exact* rate
      (``"24/1"`` and ``"48000/2000"`` are equal → cfr);
    * ``"vfr_suspected"`` — both parse but differ (``r_frame_rate`` is the
      maximum, ``avg_frame_rate`` the average; a gap signals variable rate);
    * ``"unknown"`` — either field is missing, ``"0/0"``, ``"N/A"`` or otherwise
      unparseable.

    Pure string/fraction comparison — this helper never probes media.
    """
    a = _to_fraction_or_none(r_frame_rate)
    b = _to_fraction_or_none(avg_frame_rate)
    if a is None or b is None:
        return "unknown"
    return "cfr" if a == b else "vfr_suspected"
