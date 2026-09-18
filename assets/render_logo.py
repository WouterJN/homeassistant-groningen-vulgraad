"""Rasterise the integration icon.

The shapes are simple enough to draw with Pillow, so regenerating the artwork
needs no SVG engine. Everything is drawn at 4x and downsampled, which is what
gives the edges their smoothness.

    python assets/render_logo.py
"""

from PIL import Image, ImageChops, ImageDraw

BACKGROUND = (31, 111, 67)
BODY = (12, 56, 33)
LID = (207, 232, 216)
LEVEL = (124, 214, 157)

CANVAS = 2048          # drawn large, saved small
FILL_TOP = 300         # where the waste reaches, in 512 space


def u(value: float) -> float:
    """Design units (a 512 grid) to canvas pixels."""
    return value * CANVAS / 512


def body_outline() -> list[tuple[float, float]]:
    """A container tapering towards the bottom, seen from the front."""
    return [
        (u(126), u(196)), (u(386), u(196)),
        (u(356), u(430)), (u(344), u(452)),
        (u(168), u(452)), (u(156), u(430)),
    ]


def render() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, CANVAS, CANVAS], radius=u(104), fill=BACKGROUND)

    outline = body_outline()
    draw.polygon(outline, fill=BODY)

    # The fill level, clipped to the container. Masking the intersection of the
    # body and the level keeps the empty part of the container dark; masking
    # the body alone would erase it.
    body_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(body_mask).polygon(outline, fill=255)
    level_mask = Image.new("L", (CANVAS, CANVAS), 0)
    ImageDraw.Draw(level_mask).rectangle(
        [0, u(FILL_TOP), CANVAS, CANVAS], fill=255
    )
    image.paste(
        Image.new("RGBA", (CANVAS, CANVAS), LEVEL),
        (0, 0),
        ImageChops.multiply(body_mask, level_mask),
    )

    draw = ImageDraw.Draw(image)
    # Lid, overhanging the body on both sides, with the drop slot in it.
    draw.rounded_rectangle(
        [u(96), u(150), u(416), u(200)], radius=u(25), fill=LID
    )
    draw.rounded_rectangle(
        [u(214), u(164), u(298), u(186)], radius=u(11), fill=BACKGROUND
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
