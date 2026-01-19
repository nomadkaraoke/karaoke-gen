from collections.abc import Collection
import itertools as it
import operator

from PIL import Image, ImageChops

from .cdg import *
from .render import *
from .utils import *


FILL = 0
STROKE = 1
HIGHLIGHT = 2


def image_section_to_tile_data(
        image: Image.Image,
        colors: Collection[int],
        xy: tuple[int, int] = (0, 0),
) -> list[int]:
    """
    Convert a section of an image to a list of CDG tile data bytes.

    The 6x12 section of the image with `xy` at the top left corner is
    converted to a list of CDG tile data bytes. If a pixel's color index
    is in `colors`, it is converted to color 1; otherwise, it is
    converted to color 0. Pixels outside of the image are considered to
    have a color index of 0.

    Parameters
    ----------
    image : `PIL.Image.Image`
        Image to convert to tile data bytes.
    colors : collection of int
        Color indices to convert as color 1.
    xy : tuple of (int, int), default (0, 0)
        Top left corner of image section to convert.

    Returns
    -------
    list of int
        Tile data bytes.
    """
    x, y = xy
    tile_data: list[int] = []
    for image_y in range(y, y + CDG_TILE_HEIGHT):
        # If image Y is out of bounds
        if not (0 <= image_y < image.height):
            # Every pixel in this row is considered 0
            tile_data.append(CDG_MASK if 0 in colors else 0)
            continue

        tile_line = 0
        for image_x in range(x, x + CDG_TILE_WIDTH):
            if 0 <= image_x < image.width:
                pixel = image.getpixel((image_x, image_y))
            else:
                # If image X is out of bounds, pixel is 0
                pixel = 0
            # Shift in one bit, which is "on" if the pixel is in the
            # collection of colors and "off" otherwise
            tile_line = (tile_line << 1) | (pixel in colors)
        tile_data.append(tile_line)

    return tile_data


def line_image_to_packets(
        image: Image.Image,
        xy: tuple[int, int],
        fill: int = FILL,
        stroke: int = STROKE,
        background: int = 0,
        erase: bool = False,
) -> list[CDGPacket]:
    """
    Convert a karaoke line image to CDG packets.

    Parameters
    ----------
    image : `PIL.Image.Image`
        Image to convert.
    xy : tuple of (int, int)
        Position of top left corner of image on-screen.
    fill : int, default 0
        Color index of text fill.
    stroke : int, default 1
        Color index of text stroke.
    background : int, default 0
        Color index of background.
    erase : bool, default False
        If true, render the CDG packets to erase this karaoke line; if
        false, render the CDG packets to draw this karaoke line.

    Returns
    -------
    list of `CDGPacket`
        CDG packets to draw this karaoke line image.
    """
    x, y = xy

    width = ceildiv(
        # Width includes the blank space to the left of the first tile
        (x % CDG_TILE_WIDTH) + image.width,
        CDG_TILE_WIDTH,
    )
    height = ceildiv(
        # Height includes the blank space above the first tile
        (y % CDG_TILE_HEIGHT) + image.height,
        CDG_TILE_HEIGHT,
    )

    packets: list[CDGPacket] = []
    # NOTE We iterate top-to-bottom, then left-to-right, so the effect
    # is sweeping across the columns from left to right.
    for tile_x, tile_y in it.product(range(width), range(height)):
        image_x = tile_x * CDG_TILE_WIDTH - (x % CDG_TILE_WIDTH)
        image_y = tile_y * CDG_TILE_HEIGHT - (y % CDG_TILE_HEIGHT)

        row = y // CDG_TILE_HEIGHT + tile_y
        column = x // CDG_TILE_WIDTH + tile_x
        # Skip if row or column is out of bounds
        if not (
            0 <= row < CDG_SCREEN_HEIGHT // CDG_TILE_HEIGHT
            and 0 <= column < CDG_SCREEN_WIDTH // CDG_TILE_WIDTH
        ):
            continue

        if erase:
            # Draw blank tiles over non-blank parts of the image
            tile_data = image_section_to_tile_data(
                image,
                colors=[RENDERED_BLANK],
                xy=(image_x, image_y),
            )
            if any(t != CDG_MASK for t in tile_data):
                packets.append(tile_block(
                    color0=background, color1=background,
                    row=row, column=column,
                    tile=[0 for _ in range(CDG_TILE_HEIGHT)],
                ))
        else:
            # Draw stroke
            tile_data = image_section_to_tile_data(
                image,
                colors=[RENDERED_STROKE],
                xy=(image_x, image_y),
            )
            drew_stroke = False
            if any(tile_data):
                drew_stroke = True
                packets.append(tile_block(
                    color0=background, color1=stroke,
                    row=row, column=column,
                    tile=tile_data,
                ))

            # Draw text
            tile_data = image_section_to_tile_data(
                image,
                colors=[RENDERED_FILL],
                xy=(image_x, image_y),
            )
            if any(tile_data):
                packet_func = tile_block
                color0 = background
                color1 = fill
                if drew_stroke:
                    packet_func = tile_block_xor
                    color0 = 0
                    color1 = fill ^ background
                packets.append(packet_func(
                    color0=color0, color1=color1,
                    row=row, column=column,
                    tile=tile_data,
                ))

    return packets


def line_mask_to_packets(
        image: Image.Image,
        xy: tuple[int, int],
        edges: tuple[int, int],
        highlight: int = HIGHLIGHT,
) -> list[CDGPacket]:
    """
    Convert a section of a karaoke mask image to CDG packets.

    The section is assumed to take up only one column of tiles.

    Parameters
    ----------
    image : `PIL.Image.Image`
        Image to convert.
    xy : tuple of (int, int)
        Position of top left corner of image on-screen.
    edges : tuple of (int, int)
        X positions of left and right edges of section.
    highlight : int, default 2
        Number with necessary bits on for highlight. This is XORed with
        the color index for pixels with highlight.

    Returns
    -------
    list of `CDGPacket`
        CDG packets to draw this section of this karaoke line mask.
    """
    x, y = xy
    left_edge, right_edge = edges

    # Create section mask
    section_mask = Image.new("P", image.size, 0)
    # Draw rectangle with color 255 with edges as boundaries
    # NOTE The color index must be 255 for ImageChops.multiply to
    # preserve the color indices.
    section_mask.paste(255, (left_edge - x, 0, right_edge - x, image.height))
    # Mask out section of image
    section = ImageChops.multiply(image, section_mask)

    height = ceildiv(
        # Height includes the blank space above the first tile
        (y % CDG_TILE_HEIGHT) + image.height,
        CDG_TILE_HEIGHT,
    )

    packets: list[CDGPacket] = []
    tile_x = (left_edge // CDG_TILE_WIDTH) - (x // CDG_TILE_WIDTH)
    # For all tiles in this column
    for tile_y in range(height):
        image_x = tile_x * CDG_TILE_WIDTH - (x % CDG_TILE_WIDTH)
        image_y = tile_y * CDG_TILE_HEIGHT - (y % CDG_TILE_HEIGHT)

        row = y // CDG_TILE_HEIGHT + tile_y
        column = x // CDG_TILE_WIDTH + tile_x
        # Skip if row or column is out of bounds
        if not (
            0 <= row < CDG_SCREEN_HEIGHT // CDG_TILE_HEIGHT
            and 0 <= column < CDG_SCREEN_WIDTH // CDG_TILE_WIDTH
        ):
            continue

        # Draw mask section as highlight
        tile_data = image_section_to_tile_data(
            section,
            colors=[RENDERED_MASK],
            xy=(image_x, image_y),
        )
        if any(tile_data):
            packets.append(tile_block_xor(
                color0=0, color1=highlight,
                row=row, column=column,
                tile=tile_data,
            ))

    return packets


def image_to_packets(
        image: Image.Image,
        xy: tuple[int, int] = (0, 0),
        background: Image.Image | None = None,
) -> dict[tuple[int, int], list[CDGPacket]]:
    """
    Convert an image to CDG packets.

    Parameters
    ----------
    image : `PIL.Image.Image`
        Image to convert.
    xy : tuple of (int, int), default (0, 0)
        Position to draw image on screen.

    Returns
    -------
    dict of {tuple of (int, int): list of CDGPacket}
        Tile positions, and CDG packets to draw at those positions.
    """
    # Image must be in palette mode
    assert image.mode == "P"
    # Image must have correct number of colors
    # HACK Assuming the palette is in RGB, there should be one palette
    # entry per band (R, G, B). I don't know of a better way to count
    # palette entries.
    # REVIEW Is there a better way to count palette entries?
    palette_mode, palette_data = image.palette.getdata()
    assert palette_mode == "RGB"
    assert len(palette_data) // 3 <= 16

    # Same things apply to the background image, if any
    if background is not None:
        assert background.mode == "P"
        bg_palette_mode, bg_palette_data = background.palette.getdata()
        assert bg_palette_mode == "RGB"
        assert len(bg_palette_data) // 3 <= 16

    x, y = xy

    width = ceildiv(
        # Width includes the blank space to the left of the first tile
        (x % CDG_TILE_WIDTH) + image.width,
        CDG_TILE_WIDTH,
    )
    height = ceildiv(
        # Height includes the blank space above the first tile
        (y % CDG_TILE_HEIGHT) + image.height,
        CDG_TILE_HEIGHT,
    )

    packets: dict[tuple[int, int], list[CDGPacket]] = {}
    for tile_y, tile_x in it.product(range(height), range(width)):
        image_x = tile_x * CDG_TILE_WIDTH - (x % CDG_TILE_WIDTH)
        image_y = tile_y * CDG_TILE_HEIGHT - (y % CDG_TILE_HEIGHT)

        row = y // CDG_TILE_HEIGHT + tile_y
        column = x // CDG_TILE_WIDTH + tile_x
        # Skip if row or column is out of bounds
        if not (
            0 <= row < CDG_SCREEN_HEIGHT // CDG_TILE_HEIGHT
            and 0 <= column < CDG_SCREEN_WIDTH // CDG_TILE_WIDTH
        ):
            continue

        tile = image.crop((
            image_x, image_y,
            image_x + CDG_TILE_WIDTH, image_y + CDG_TILE_HEIGHT,
        ))
        background_tile = None
        if background is not None:
            background_tile = background.crop((
                image_x, image_y,
                image_x + CDG_TILE_WIDTH, image_y + CDG_TILE_HEIGHT,
            ))
        packets[(row, column)] = tile_to_packets(
            tile, row, column,
            background_tile=background_tile,
        )

    return packets


# REVIEW How can I change this function to draw an image over already
# existing pixels on the screen? For example, sometimes it would be more
# efficient to XOR over existing pixels than to draw a new tile.
def tile_to_packets(
        tile: Image.Image,
        row: int,
        column: int,
        background_tile: Image.Image | None = None,
) -> list[CDGPacket]:
    """
    Convert a tile to CDG packets.

    The tile is assumed to be a 6x12 image in `P` mode.

    Parameters
    ----------
    tile : `PIL.Image.Image`
        Tile to convert.
    row : int
        Row to draw tile on screen.
    column : int
        Column to draw tile on screen.

    Returns
    -------
    list of CDGPacket
        CDG packets to draw this tile.
    """
    # If the background tile has the same pixels as the tile we want to
    # draw, don't draw this tile
    if (
        background_tile is not None
        and list(tile.getdata()) == list(background_tile.getdata())
    ):
        return []

    # Sort colors in descending order by frequency
    colors: list[int] = list(map(
        operator.itemgetter(1),
        sorted(tile.getcolors(), reverse=True),
    ))

    if len(colors) == 1:
        # HACK If the only color is 0 (and we're not drawing over a
        # background tile), we don't draw this tile. This is not always
        # desirable, but it's fine for our purposes.
        if background_tile is None and not colors[0]:
            return []
        return [
            tile_block(
                color0=0, color1=colors[0],
                row=row, column=column,
                tile=[CDG_MASK] * CDG_TILE_HEIGHT,
            ),
        ]

    if len(colors) == 2:
        return [
            tile_block(
                color0=colors[1], color1=colors[0],
                row=row, column=column,
                tile=image_section_to_tile_data(tile, [colors[0]]),
            ),
        ]

    if len(colors) == 3:
        return [
            tile_block(
                color0=colors[1], color1=colors[0],
                row=row, column=column,
                tile=image_section_to_tile_data(tile, [colors[0]]),
            ),
            tile_block_xor(
                color0=0, color1=colors[1] ^ colors[2],
                row=row, column=column,
                tile=image_section_to_tile_data(tile, [colors[2]]),
            ),
        ]

    colors_or = 0x00
    colors_xor = 0x00
    colors_and = 0xff
    for color in colors:
        colors_or |= color
        colors_xor ^= color
        colors_and &= color
    and_bits = colors_and.bit_count()
    or_bits = colors_or.bit_count()
    used_bits = or_bits - and_bits

    if len(colors) == 4 and used_bits > 2 and colors_xor != 0:
        return [
            tile_block(
                color0=colors[0], color1=colors[1],
                row=row, column=column,
                tile=image_section_to_tile_data(
                    tile, [colors[1], colors[2], colors[3]],
                ),
            ),
            tile_block_xor(
                color0=0, color1=colors[1] ^ colors[2],
                row=row, column=column,
                tile=image_section_to_tile_data(
                    tile, [colors[2]],
                ),
            ),
            tile_block_xor(
                color0=0, color1=colors[1] ^ colors[3],
                row=row, column=column,
                tile=image_section_to_tile_data(
                    tile, [colors[3]],
                ),
            ),
        ]

    if len(colors) > 4 or colors_xor != 0:
        tile_packets: list[CDGPacket] = []

        packet_func = tile_block
        for i in range(4):
            if not colors_or & (1 << i):
                continue
            if colors_and & (1 << i):
                continue

            color0 = 0
            color1 = 1 << i
            if packet_func == tile_block and colors_and:
                color0 |= colors_and
                color1 |= colors_and

            tile_packets.append(packet_func(
                color0=color0, color1=color1,
                row=row, column=column,
                tile=image_section_to_tile_data(
                    tile,
                    [color for color in range(16) if color & (1 << i)],
                ),
            ))
            packet_func = tile_block_xor
        return tile_packets

    assert colors[2] ^ colors[0] == colors[1] ^ colors[3]
    return [
        tile_block(
            color0=colors[1], color1=colors[0],
            row=row, column=column,
            tile=image_section_to_tile_data(
                tile, [colors[0], colors[2]],
            ),
        ),
        tile_block_xor(
            color0=0, color1=colors[2] ^ colors[0],
            row=row, column=column,
            tile=image_section_to_tile_data(
                tile, [colors[2], colors[3]],
            ),
        ),
    ]


__all__ = [
    "image_section_to_tile_data", "line_image_to_packets",
    "line_mask_to_packets", "image_to_packets", "tile_to_packets",
]
