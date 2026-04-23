from karaoke_gen.lyrics_transcriber.output.ass.formatters import Formatters
from karaoke_gen.lyrics_transcriber.output.ass.constants import ALIGN_BOTTOM_CENTER


class Style:
    aliases = {}
    formatters = None
    order = [
        "Name",
        "Fontname",
        "Fontpath",
        "Fontsize",
        "PrimaryColour",
        "SecondaryColour",
        "OutlineColour",
        "BackColour",
        "Bold",
        "Italic",
        "Underline",
        "StrikeOut",
        "ScaleX",
        "ScaleY",
        "Spacing",
        "Angle",
        "BorderStyle",
        "Outline",
        "Shadow",
        "Alignment",
        "MarginL",
        "MarginR",
        "MarginV",
        "Encoding",
    ]

    # Constructor
    def __init__(self):
        self.type = None
        self.fake = False

        self.Name = ""
        self.Fontname = ""
        self.Fontpath = ""
        self.Fontsize = 1.0
        self.PrimaryColour = (255, 255, 255, 255)
        self.SecondaryColour = (255, 255, 255, 255)
        self.OutlineColour = (255, 255, 255, 255)
        self.BackColour = (255, 255, 255, 255)
        self.Bold = False
        self.Italic = False
        self.Underline = False
        self.StrikeOut = False
        self.ScaleX = 100
        self.ScaleY = 100
        self.Spacing = 0
        self.Angle = 0.0
        self.BorderStyle = 1
        self.Outline = 0
        self.Shadow = 0
        self.Alignment = ALIGN_BOTTOM_CENTER
        self.MarginL = 0
        self.MarginR = 0
        self.MarginV = 0
        self.Encoding = 0

    def set(self, attribute_name, value, *args):
        if hasattr(self, attribute_name):
            if not attribute_name[0].isupper():
                return
        elif attribute_name in self.aliases:
            attribute_name = self.aliases[attribute_name]
        else:
            return

        setattr(self, attribute_name, self.formatters[attribute_name][0](value, *args))

    def get(self, attribute_name, *args):
        if hasattr(self, attribute_name):
            if not attribute_name[0].isupper():
                return None
        elif attribute_name in self.aliases:
            attribute_name = self.aliases[attribute_name]
        else:
            return None

        return self.formatters[attribute_name][1](getattr(self, attribute_name), *args)

    def copy(self, other=None):
        if other is None:
            # Creating a new style
            other = self.__class__()
            target = other
            source = self
        else:
            # Copying into existing style
            target = other  # This was the issue - we had target and source swapped
            source = self

        # Copy all attributes
        target.type = source.type
        target.fake = source.fake  # Also need to copy the fake flag

        target.Name = source.Name
        target.Fontname = source.Fontname
        target.Fontpath = source.Fontpath
        target.Fontsize = source.Fontsize
        target.PrimaryColour = source.PrimaryColour
        target.SecondaryColour = source.SecondaryColour
        target.OutlineColour = source.OutlineColour
        target.BackColour = source.BackColour
        target.Bold = source.Bold
        target.Italic = source.Italic
        target.Underline = source.Underline
        target.StrikeOut = source.StrikeOut
        target.ScaleX = source.ScaleX
        target.ScaleY = source.ScaleY
        target.Spacing = source.Spacing
        target.Angle = source.Angle
        target.BorderStyle = source.BorderStyle
        target.Outline = source.Outline
        target.Shadow = source.Shadow
        target.Alignment = source.Alignment
        target.MarginL = source.MarginL
        target.MarginR = source.MarginR
        target.MarginV = source.MarginV
        target.Encoding = source.Encoding

        return target

    def equals(self, other, names_can_differ=False):
        return (
            self.type == other.type
            and not self.fake
            and not other.fake
            and not other.fake
            and (names_can_differ or self.Name == other.Name)
            and self.Fontname == other.Fontname
            and self.Fontpath == other.Fontpath
            and self.Fontsize == other.Fontsize
            and self.PrimaryColour == other.PrimaryColour
            and self.SecondaryColour == other.SecondaryColour
            and self.OutlineColour == other.OutlineColour
            and self.BackColour == other.BackColour
            and self.Bold == other.Bold
            and self.Italic == other.Italic
            and self.Underline == other.Underline
            and self.StrikeOut == other.StrikeOut
            and self.ScaleX == other.ScaleX
            and self.ScaleY == other.ScaleY
            and self.Spacing == other.Spacing
            and self.Angle == other.Angle
            and self.BorderStyle == other.BorderStyle
            and self.Outline == other.Outline
            and self.Shadow == other.Shadow
            and self.Alignment == other.Alignment
            and self.MarginL == other.MarginL
            and self.MarginR == other.MarginR
            and self.MarginV == other.MarginV
            and self.Encoding == other.Encoding
        )


Style.formatters = {
    "Name": (Formatters.same, Formatters.same),
    "Fontname": (Formatters.same, Formatters.same),
    "Fontpath": (Formatters.same, Formatters.same),
    "Fontsize": (Formatters.str_to_number, Formatters.number_to_str),
    "PrimaryColour": (Formatters.str_to_color, Formatters.color_to_str),
    "SecondaryColour": (Formatters.str_to_color, Formatters.color_to_str),
    "OutlineColour": (Formatters.str_to_color, Formatters.color_to_str),
    "BackColour": (Formatters.str_to_color, Formatters.color_to_str),
    "Bold": (Formatters.str_to_n1bool, Formatters.n1bool_to_str),
    "Italic": (Formatters.str_to_n1bool, Formatters.n1bool_to_str),
    "Underline": (Formatters.str_to_n1bool, Formatters.n1bool_to_str),
    "StrikeOut": (Formatters.str_to_n1bool, Formatters.n1bool_to_str),
    "ScaleX": (Formatters.str_to_integer, Formatters.integer_to_str),
    "ScaleY": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Spacing": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Angle": (Formatters.str_to_number, Formatters.number_to_str),
    "BorderStyle": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Outline": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Shadow": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Alignment": (Formatters.str_to_integer, Formatters.integer_to_str),
    "MarginL": (Formatters.str_to_integer, Formatters.integer_to_str),
    "MarginR": (Formatters.str_to_integer, Formatters.integer_to_str),
    "MarginV": (Formatters.str_to_integer, Formatters.integer_to_str),
    "Encoding": (Formatters.str_to_integer, Formatters.integer_to_str),
}


from karaoke_gen.style_loader import resolve_singer_colors  # noqa: E402


# Singer id → suffix for the generated ASS style name.
_DUET_STYLE_NAME_SUFFIX = {0: "Both", 1: "Singer1", 2: "Singer2"}


def build_karaoke_styles(karaoke_style: dict, singers, solo: bool = False) -> list[Style]:
    """Build one ASS Style per singer id.

    Args:
        karaoke_style: the theme's "karaoke" block (flat colors + optional "singers" block)
        singers: iterable of SingerId (0/1/2) to build styles for
        solo: if True, returns a single Style named per karaoke_style["ass_name"]
              with the flat colors. Used when is_duet=False for byte-identical
              regression with pre-change output.

    Returns:
        list[Style] — one Style per singer id.

    Note:
        The Alignment attribute on each returned Style is intentionally left
        at the Style() default. Callers are responsible for setting it (e.g.
        ALIGN_TOP_CENTER) before adding the styles to the ASS file.
    """
    def _parse_color(color_str):
        return tuple(int(x.strip()) for x in color_str.split(","))

    def _parse_bool(val):
        return -1 if val else 0

    def _make_style(name: str, colors: dict) -> Style:
        s = Style()
        s.type = "Style"
        s.Name = name
        s.Fontname = karaoke_style["font"]
        s.Fontpath = karaoke_style.get("font_path", "")
        # Fontsize is authoritative at the caller (SubtitlesGenerator overrides
        # s.Fontsize per resolution / preview mode after this factory returns).
        # Not every style JSON declares font_size at the karaoke-block level
        # (e.g. the nomad theme relies on the CLI/video-resolution default), so
        # fall back to the same default as DEFAULT_KARAOKE_STYLE rather than
        # crashing — the value is overwritten before rendering anyway.
        s.Fontsize = karaoke_style.get("font_size", 250)
        s.PrimaryColour = _parse_color(colors["primary_color"])
        s.SecondaryColour = _parse_color(colors["secondary_color"])
        s.OutlineColour = _parse_color(colors["outline_color"])
        s.BackColour = _parse_color(colors["back_color"])
        s.Bold = _parse_bool(karaoke_style["bold"])
        s.Italic = _parse_bool(karaoke_style["italic"])
        s.Underline = _parse_bool(karaoke_style["underline"])
        s.StrikeOut = _parse_bool(karaoke_style["strike_out"])
        s.ScaleX = int(karaoke_style["scale_x"])
        s.ScaleY = int(karaoke_style["scale_y"])
        s.Spacing = int(karaoke_style["spacing"])
        s.Angle = float(karaoke_style["angle"])
        s.BorderStyle = int(karaoke_style["border_style"])
        s.Outline = int(karaoke_style["outline"])
        s.Shadow = int(karaoke_style["shadow"])
        s.MarginL = int(karaoke_style["margin_l"])
        s.MarginR = int(karaoke_style["margin_r"])
        s.MarginV = int(karaoke_style["margin_v"])
        s.Encoding = int(karaoke_style["encoding"])
        # Alignment is set later by the caller via ALIGN_TOP_CENTER; leave default
        return s

    if solo:
        # Solo: one style, original ass_name, flat colors only.
        colors = {
            "primary_color":   karaoke_style["primary_color"],
            "secondary_color": karaoke_style["secondary_color"],
            "outline_color":   karaoke_style["outline_color"],
            "back_color":      karaoke_style["back_color"],
        }
        return [_make_style(karaoke_style["ass_name"], colors)]

    styles = []
    for sid in singers:
        colors = resolve_singer_colors(karaoke_style, sid)
        name = f"Karaoke.{_DUET_STYLE_NAME_SUFFIX[sid]}"
        styles.append(_make_style(name, colors))
    return styles
