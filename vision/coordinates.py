# ==========================
# ELIAN — coordinate transforms (Phase 5)
# ==========================

from __future__ import annotations

from typing import Iterable, Optional, Tuple

from vision.models import Bounds


def scale_bounds(
    bounds: Bounds,
    *,
    from_width: int,
    from_height: int,
    to_width: int,
    to_height: int,
) -> Bounds:
    """Map bbox from model/image space to real screen space."""
    if from_width <= 0 or from_height <= 0:
        raise ValueError("from dimensions must be positive")
    if to_width <= 0 or to_height <= 0:
        raise ValueError("to dimensions must be positive")

    sx = to_width / float(from_width)
    sy = to_height / float(from_height)
    return Bounds(
        x=int(round(bounds.x * sx)),
        y=int(round(bounds.y * sy)),
        width=max(1, int(round(bounds.width * sx))),
        height=max(1, int(round(bounds.height * sy))),
    )


def scale_point(
    x: int,
    y: int,
    *,
    from_width: int,
    from_height: int,
    to_width: int,
    to_height: int,
) -> Tuple[int, int]:
    if from_width <= 0 or from_height <= 0:
        raise ValueError("from dimensions must be positive")
    sx = to_width / float(from_width)
    sy = to_height / float(from_height)
    return int(round(x * sx)), int(round(y * sy))


def validate_point_in_screen(
    x: int,
    y: int,
    screen: Bounds,
) -> Optional[str]:
    """Return error message if point is outside screen, else None."""
    if x < screen.x or y < screen.y or x >= screen.right or y >= screen.bottom:
        return (
            f"Point ({x},{y}) outside screen "
            f"({screen.x},{screen.y},{screen.width}x{screen.height})"
        )
    return None


def validate_bounds_in_screen(
    bounds: Bounds,
    screen: Bounds,
    *,
    require_full_containment: bool = False,
) -> Optional[str]:
    cx, cy = bounds.center
    err = validate_point_in_screen(cx, cy, screen)
    if err:
        return err
    if require_full_containment:
        if (
            bounds.x < screen.x
            or bounds.y < screen.y
            or bounds.right > screen.right
            or bounds.bottom > screen.bottom
        ):
            return "Bounds not fully inside screen"
    return None


def clamp_point(x: int, y: int, screen: Bounds) -> Tuple[int, int]:
    return (
        min(max(x, screen.x), screen.right - 1),
        min(max(y, screen.y), screen.bottom - 1),
    )


def dpi_aware_scale(logical: float, dpi_scale: float) -> float:
    """Apply Windows DPI scale (e.g. 1.5 for 150%)."""
    if dpi_scale <= 0:
        return logical
    return logical * dpi_scale
