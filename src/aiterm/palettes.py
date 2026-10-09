"""Terminal color palettes: 16 ANSI colors plus foreground and background for
the light and the dark system theme."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    name: str
    light: tuple  # (foreground, background)
    dark: tuple
    colors: tuple  # 16 ANSI colors: 8 normal, then 8 bright


PALETTES = {p.name: p for p in (
    # Adwaita named colors; the background matches libadwaita's "view" color,
    # so the terminal blends with the header bar
    Palette(
        "GNOME",
        light=("#1e1e1e", "#ffffff"),
        dark=("#ffffff", "#1d1d20"),
        colors=(
            "#241f31", "#c01c28", "#2ec27e", "#e5a50a", "#1c71d8", "#9141ac", "#0ab9dc", "#c0bfbc",
            "#5e5c64", "#ed333b", "#57e389", "#f8e45c", "#51a1ff", "#c061cb", "#4fd2fd", "#f6f5f4",
        ),
    ),
    Palette(
        "Tango",
        light=("#2e3436", "#ffffff"),
        dark=("#d3d7cf", "#2e3436"),
        colors=(
            "#2e3436", "#cc0000", "#4e9a06", "#c4a000", "#3465a4", "#75507b", "#06989a", "#d3d7cf",
            "#555753", "#ef2929", "#8ae234", "#fce94f", "#729fcf", "#ad7fa8", "#34e2e2", "#eeeeec",
        ),
    ),
    Palette(
        "Solarized",
        light=("#657b83", "#fdf6e3"),
        dark=("#839496", "#002b36"),
        colors=(
            "#073642", "#dc322f", "#859900", "#b58900", "#268bd2", "#d33682", "#2aa198", "#eee8d5",
            "#002b36", "#cb4b16", "#586e75", "#657b83", "#839496", "#6c71c4", "#93a1a1", "#fdf6e3",
        ),
    ),
)}
DEFAULT_PALETTE = "GNOME"
