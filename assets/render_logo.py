"""Rasterise the integration icon.

One Groningen underground container, drawn from the photographs on
milieudienst.groningen.nl: a glossy black housing with a domed top, standing on
its tread plate, with the big light hatch panel across the front.

The bin is below ground and never visible, so the hatch panel doubles as the
gauge: it fills from the bottom with the level the integration reports.

The shapes are simple enough to draw with Pillow, so regenerating the artwork
needs no SVG engine. Everything is drawn at 4x and downsampled, which is what
gives the curves their smoothness.

    python assets/render_logo.py
"""

from PIL import Image, ImageDraw

BACKGROUND = (28, 102, 62)
BODY = (18, 20, 23)
PANEL_EMPTY = (226, 228, 222)
PANEL_FULL = (79, 180, 119)
PLATE = (146, 155, 161)

CANVAS = 2048          # drawn large, saved small
FILL = 0.62            # how full the hatch panel reads


def u(value: float) -> float:
    """Design units (a 512 grid) to canvas pixels."""
    return value * CANVAS / 512


def render() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, CANVAS, CANVAS], radius=u(104), fill=BACKGROUND)

    # the tread plate the housing stands on
    draw.rounded_rectangle(
        [u(88), u(414), u(424), u(450)], radius=u(11), fill=PLATE
    )

    # the housing: upright, with the domed top these containers have
    # squat, with the shallow domed roof these containers have, not an arch
    body = [u(122), u(156), u(390), u(426)]
    draw.rounded_rectangle(
        body, radius=u(78), corners=(True, True, False, False), fill=BODY
    )
    # the hatch panel across the front, which doubles as the fill gauge
    panel = [u(156), u(248), u(356), u(402)]
    draw.rounded_rectangle(panel, radius=u(14), fill=PANEL_EMPTY)
    surface = panel[1] + (panel[3] - panel[1]) * (1 - FILL)
    draw.rounded_rectangle(
        [panel[0], surface, panel[2], panel[3]], radius=u(14), fill=PANEL_FULL
    )
    # square off the top of the level so it reads as a surface, not a pill
    draw.rectangle([panel[0], surface, panel[2], surface + u(16)], fill=PANEL_FULL)
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
