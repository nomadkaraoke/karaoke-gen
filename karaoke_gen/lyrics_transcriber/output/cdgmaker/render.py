from collections.abc import Sequence
import itertools as it

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .config import *


import logging


RENDERED_BLANK = 0
RENDERED_MASK = 1
RENDERED_FILL = 1
RENDERED_STROKE = 2


def get_wrapped_text(
        text: str,
        font: ImageFont.FreeTypeFont,
        width: int,
) -> str:
    """
    Add newlines to text such that it fits within the specified width
    using the specified font.

    Existing newlines are preserved.

    Parameters
    ----------
    text : str
        Text to add newlines to.
    font : `PIL.ImageFont.FreeTypeFont`
        Font in which text will be rendered.
    width : int
        Maximum width of text lines in pixels.

    Returns
    -------
    str
        Text with inserted newlines.
    """
    lines: list[str] = []
    for text_line in text.split("\n"):
        words: list[str] = []
        for word in text_line.split():
            if font.getlength(" ".join(words + [word])) > width:
                lines.append(" ".join(words))
                words.clear()
            words.append(word)
        lines.append(" ".join(words))
        words.clear()
    return "\n".join(lines)


def render_text(
        text: str,
        font: ImageFont.FreeTypeFont,
        fill: int = RENDERED_FILL,
        stroke_fill: int = RENDERED_STROKE,
        stroke_width: int = 0,
        stroke_type: StrokeType = StrokeType.OCTAGON,
) -> Image.Image:
    """
    Render one text line as a `PIL.Image.Image` in `P` mode.

    There may be horizontal padding on both sides of the image. However,
    for the same text prefix or suffix, the padding on that side will be
    the same.

    Parameters
    ----------
    text : str
        Text line to render.
    font : `PIL.ImageFont.FreeTypeFont`
        Font to render text with.
    config : `config.Settings`
        Config settings.
    fill : int, default 1
        Color index of the text fill.
    stroke_fill : int, default 2
        Color index of the text stroke.
    stroke_width : int, default 0
        Width of the text stroke.
    stroke_type : `StrokeType`, default `StrokeType.OCTAGON`
        Stroke type.

    Returns
    -------
    `PIL.Image.Image`
        Image with rendered text.
    """
    # Get relevant dimensions for font
    _, _, text_width, _ = font.getbbox(text)
    ascent, descent = font.getmetrics()
    (_, _), (offset_x, _) = font.font.getsize(text)

    image_width = text_width - offset_x
    image_height = ascent + descent
    # Add space on left/right for stroke
    image_width += 2 * stroke_width
    # Add space on top/bottom for stroke
    image_height += 2 * stroke_width
    # HACK I don't know exactly why, but sometimes a few pixels are cut
    # off on the sides, so we add some horizontal padding here. (This is
    # cropped by another function, so it's okay.)
    padding_x = font.size * 4
    image_width += padding_x
    offset_x -= padding_x // 2

    image = Image.new("P", (image_width, image_height), 0)
    draw = ImageDraw.Draw(image)
    # Turn off antialiasing
    draw.fontmode = "1"

    draw_x = stroke_width - offset_x
    draw_y = stroke_width
    # If we are to draw a text stroke
    if stroke_width and stroke_fill is not None:
        # NOTE PIL allows text to be drawn with a stroke, but this
        # stroke is anti-aliased, and you can't turn off the anti-
        # aliasing on it. So instead, we're simulating a stroke by
        # drawing the text multiple times at various offsets.
        stroke_coords = list(it.product(
            range(-stroke_width, stroke_width + 1), repeat=2,
        ))
        match stroke_type:
            case StrokeType.CIRCLE:
                stroke_coords = [
                    (x, y)
                    for x, y in stroke_coords
                    if x**2 + y**2 <= stroke_width ** 2
                ]
            case StrokeType.SQUARE:
                pass
            case StrokeType.OCTAGON:
                stroke_coords = [
                    (x, y)
                    for x, y in stroke_coords

                    if (abs(x) + abs(y)) * 2 <= stroke_width * 3
                ]

        # Create image for text stroke
        stroke_image = Image.new("P", image.size, 0)
        stroke_draw = ImageDraw.Draw(stroke_image)
        # Turn off antialiasing
        stroke_draw.fontmode = "1"

        # Render text stroke
        stroke_draw.text((draw_x, draw_y), text, stroke_fill, font)
        # Create mask for text stroke
        stroke_mask = stroke_image.point(lambda v: v and 255, mode="1")
        # Draw text stroke at various offsets
        for x, y in stroke_coords:
            image.paste(stroke_image, (x, y), mask=stroke_mask)
        # NOTE Drawing the stroke once and pasting it multiple times is
        # faster than drawing the stroke multiple times.

    # Draw text fill
    draw.text((draw_x, draw_y), text, fill, font)
    return image


def render_lines_and_masks(
        lines: Sequence[Sequence[str]],
        font: ImageFont.FreeTypeFont,
        stroke_width: int = 0,
        stroke_type: StrokeType = StrokeType.OCTAGON,
        render_masks: bool = True,
        logger: logging.Logger = logging.getLogger(__name__),
) -> tuple[list[Image.Image], list[list[Image.Image]]]:
    """
    Render set of karaoke lines as `PIL.Image.Image`s, and masks for
    each syllable as lists of `PIL.Image.Image`s.

    The line images will be cropped as much as possible on the left,
    right, and bottom sides. The top side of all line images will be
    cropped by the largest amount that does not shrink any of their
    bounding boxes.

    Parameters
    ----------
    lines : list of list of str
        Lines as lists of syllables.
    font : `PIL.ImageFont.FreeTypeFont`
        Font to render text with.
    stroke_width : int, default 0
        WIdth of the text stroke.
    stroke_type : `StrokeType`, default `StrokeType.OCTAGON`
        Stroke type.
    render_masks : bool, default True
        If true, render masks for each line.

    Returns
    -------
    list of `PIL.Image.Image`
        Images with rendered lines.
    list of list of `PIL.Image.Image`
        Images with rendered masks for each syllable for each line.
    """
    logger.debug("rendering line images")
    # Render line images
    uncropped_line_images = [
        render_text(
            text="".join(line),
            font=font,
            fill=RENDERED_FILL,
            stroke_fill=RENDERED_STROKE,
            stroke_width=stroke_width,
            stroke_type=stroke_type,
        )
        for line in lines
    ]
    # Calculate how much the tops of the lines can be cropped
    top_crop = min(
        (
            bbox[1]
            for image in uncropped_line_images
            if (bbox := image.getbbox()) is not None
        ),
        default=0,
    )
    logger.debug(
        f"line images will be cropped by {top_crop} pixel(s) on the top"
    )

    # Crop line images
    line_images: list[Image.Image] = []
    bboxes: list[Sequence[int]] = []
    logger.debug("cropping line images")
    for image in uncropped_line_images:
        bbox = image.getbbox()
        if bbox is None:
            # Create empty bounding box if image is empty
            bbox = (0, 0, 0, 0)
        else:
            # Crop top of bounding box is image is not empty
            bbox = list(bbox)
            bbox[1] = top_crop

        bboxes.append(bbox)
        line_images.append(image.crop(bbox))

    if not render_masks:
        logger.debug("not rendering masks")
        return line_images, []

    # Render mask images
    line_masks: list[list[Image.Image]] = []
    logger.debug("rendering/cropping masks")
    for line, bbox in zip(lines, bboxes):
        # HACK For whatever reason, the presence or absence of certain
        # characters of text can cause the rendered text to be 1 pixel
        # off. We fix this by adding the entire rest of the text after
        # each rendered part of it, so this mysterious offset is at
        # least consistent.
        extra_text = "".join(line)
        # NOTE We will prefix the extra text with way more spaces than
        # necessary, so it doesn't show up in the mask images.
        text_padding = " " * bbox[2]
        # REVIEW More testing is needed. Which characters does this
        # happen for? Why does this even happen?
        # Using Old Sans Black, this happens with at least "t" and "!".

        # Get masks of the line's text from the start up to each
        # syllable
        # e.g. ["Don't ", "walk ", "a", "way"] ->
        # ["Don't ", "Don't walk ", "Don't walk a", "Don't walk away"]
        full_line_masks = [
            render_text(
                text="".join(line[:i+1]) + text_padding + extra_text,
                font=font,
                fill=RENDERED_MASK,
                stroke_fill=RENDERED_MASK,
                stroke_width=stroke_width,
                stroke_type=stroke_type,
            ).crop(bbox)
            for i in range(len(line))
        ]

        line_mask: list[Image.Image] = []
        # If this line has any syllables
        if full_line_masks:
            # Start with the first syllable's mask...
            line_mask = [full_line_masks[0]] + [
                # ...then get the pixel-by-pixel difference between each
                # pair of full-line masks
                ImageChops.difference(prev_mask, next_mask)
                for prev_mask, next_mask in it.pairwise(full_line_masks)
            ]
        # NOTE This will isolate the pixels that make up this syllable,
        # by basically "cancelling out" the previous syllables of the
        # line.
        line_masks.append(line_mask)

    return line_images, line_masks


def render_lines(
        lines: Sequence[Sequence[str]],
        font: ImageFont.FreeTypeFont,
        stroke_width: int = 0,
        stroke_type: StrokeType = StrokeType.OCTAGON,
) -> list[Image.Image]:
    """
    Render set of karaoke lines as `PIL.Image.Image`s.

    The line images will be cropped as much as possible on the left,
    right, and bottom sides. The top side of all line images will be
    cropped by the largest amount that does not shrink any of their
    bounding boxes.

    Parameters
    ----------
    lines : list of list of str
        Lines as lists of syllables.
    font : `PIL.ImageFont.FreeTypeFont`
        Font to render text with.
    stroke_width : int, default 0
        WIdth of the text stroke.
    stroke_type : `StrokeType`, default `StrokeType.OCTAGON`
        Stroke type.

    Returns
    -------
    list of `PIL.Image.Image`
        Images with rendered lines.
    """
    images, _ = render_lines_and_masks(
        lines,
        font=font,
        stroke_width=stroke_width,
        stroke_type=stroke_type,
        render_masks=False,
    )
    return images


__all__ = [
    "RENDERED_BLANK", "RENDERED_MASK", "RENDERED_FILL",
    "RENDERED_STROKE",

    "get_wrapped_text", "render_text", "render_lines_and_masks",
    "render_lines",
]
