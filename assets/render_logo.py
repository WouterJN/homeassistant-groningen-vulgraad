"""Rasterise the integration icon.

The subject is the real thing: a Groningen underground container as it looks
from the street. A dark charcoal housing with a slanted top, a hinged door on
the front, the stainless deposit drum beside it, and the diamond tread plate it
stands on. The bin itself is below ground and never visible, so the fill level
is shown as a gauge window in the door.

The shapes are simple enough to draw with Pillow, so regenerating the artwork
needs no SVG engine. Everything is drawn at 4x and downsampled, which is what
gives the edges their smoothness.

    python assets/render_logo.py
"""

from PIL import Image, ImageDraw

BACKGROUND = (31, 111, 67)
HOUSING = (43, 47, 52)
HOUSING_EDGE = (62, 68, 75)
WINDOW = (22, 25, 28)
DRUM = (176, 184, 189)
DRUM_LIGHT = (205, 212, 216)
PLATE_EDGE = (163, 171, 176)
PLATE = (138, 147, 153)
LEVEL = (124, 214, 157)
LABEL = (214, 74, 74)

CANVAS = 2048          # drawn large, saved small
FILL = 0.62            # how full the gauge window reads


def u(value: float) -> float:
    """Design units (a 512 grid) to canvas pixels."""
    return value * CANVAS / 512


def render() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, CANVAS, CANVAS], radius=u(104), fill=BACKGROUND)

    # the tread plate the housing stands on, wider than the housing itself
    draw.rounded_rectangle(
        [u(48), u(404), u(464), u(446)], radius=u(12), fill=PLATE
    )
    draw.rounded_rectangle(
        [u(48), u(404), u(464), u(418)], radius=u(7), fill=PLATE_EDGE
    )

    # housing. The real roof slopes only slightly, up towards the drum side.
    draw.polygon(
        [(u(110), u(170)), (u(402), u(140)), (u(402), u(408)), (u(110), u(408))],
        fill=HOUSING,
    )
    draw.polygon(
        [(u(110), u(170)), (u(402), u(140)), (u(402), u(156)), (u(110), u(186))],
        fill=HOUSING_EDGE,
    )

    # the stainless deposit drum, a cylinder with a domed top
    draw.rounded_rectangle(
        [u(306), u(180), u(388), u(396)], radius=u(41), fill=DRUM
    )
    draw.ellipse([u(306), u(180), u(388), u(240)], fill=DRUM_LIGHT)

    # gauge window in the door, empty part first
    window = [u(134), u(222), u(288), u(394)]
    draw.rounded_rectangle(window, radius=u(12), fill=WINDOW)
    top = window[1] + (window[3] - window[1]) * (1 - FILL)
    draw.rounded_rectangle(
        [window[0], top, window[2], window[3]], radius=u(12), fill=LEVEL
    )
    # square off the top of the level so it reads as a surface, not a pill
    draw.rectangle([window[0], top, window[2], top + u(14)], fill=LEVEL)

    # the number plate every container carries, upper left of the door
    draw.rounded_rectangle(
        [u(134), u(182), u(196), u(212)], radius=u(7), fill=LABEL
    )

    return image


if __name__ == "__main__":
    artwork = render()
    for name, size in (
        ("icon.png", 256),
        ("icon@2x.png", 512),
        ("logo.png", 256),
        ("logo@2x.png", 512),
    ):
        artwork.resize((size, size), Image.LANCZOS).save(f"assets/{name}")
        print(f"  assets/{name}  {size}x{size}")
