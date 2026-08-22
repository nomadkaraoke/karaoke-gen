"""Tests for portrait background generation and layout math."""
from karaoke_gen.portrait.background import PortraitBrandConfig, build_background
from karaoke_gen.portrait.renderer import PortraitLayout, _computed_top_padding


def test_background_dimensions_and_variants():
    cfg = PortraitBrandConfig(width=1080, height=1920, brand_text="NOMAD KARAOKE",
                              footer_text="nomadkaraoke.com")
    for variant in ("lyrics", "title", "end"):
        img = build_background(cfg, "Piri", "Dog", variant=variant)
        assert img.size == (1080, 1920)
        assert img.mode == "RGB"


def test_background_custom_size():
    cfg = PortraitBrandConfig(width=720, height=1280)
    img = build_background(cfg, "A", "B", variant="lyrics")
    assert img.size == (720, 1280)


def test_uppercase_meta_default_on():
    # Smoke: uppercase path shouldn't error and still yields a valid frame.
    cfg = PortraitBrandConfig(uppercase_meta=True, brand_text="NOMAD")
    img = build_background(cfg, "piri", "dog", variant="lyrics")
    assert img.size == (1080, 1920)


def test_computed_top_padding_centers_block():
    """top_padding should place the block centre near block_center_frac * height."""
    layout = PortraitLayout(height=1920, line_height=118, max_visible_lines=4,
                            block_center_frac=0.60)
    tp = _computed_top_padding(layout)
    total = layout.max_visible_lines * layout.line_height
    # Reproduce the generator's first-line formula and check the block centre.
    first = tp + (layout.height - total - tp) // 4
    block_center = first + total / 2
    assert abs(block_center - layout.height * 0.60) < 20
    assert tp >= 0
