import re


class Formatters:
    __re_color_format = re.compile(r"&H([0-9a-fA-F]{8}|[0-9a-fA-F]{6})", re.U)
    __re_tag_number = re.compile(r"^\s*([\+\-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))", re.U)

    @classmethod
    def same(cls, val, *args):
        return val

    @classmethod
    def color_to_str(cls, val, *args):
        return "&H{0:02X}{1:02X}{2:02X}{3:02X}".format(255 - val[3], val[2], val[1], val[0])

    @classmethod
    def str_to_color(cls, val, *args):
        match = cls.__re_color_format.search(val)
        if match:
            hex_val = "{0:>08s}".format(match.group(1))
            return (
                int(hex_val[6:8], 16),  # Red
                int(hex_val[4:6], 16),  # Green
                int(hex_val[2:4], 16),  # Blue
                255 - int(hex_val[0:2], 16),  # Alpha
            )
        # Return white (255, 255, 255, 255) for invalid input
        return (255, 255, 255, 255)

    @classmethod
    def n1bool_to_str(cls, val, *args):
        if val:
            return "-1"
        return "0"

    @classmethod
    def str_to_n1bool(cls, val, *args):
        try:
            val = int(val, 10)
        except ValueError:
            return False
        return val != 0

    @classmethod
    def integer_to_str(cls, val, *args):
        return str(int(val))

    @classmethod
    def str_to_integer(cls, val, *args):
        try:
            return int(val, 10)
        except ValueError:
            return 0

    @classmethod
    def number_to_str(cls, val, *args):
        if int(val) == val:
            return str(int(val))
            # No decimal
        return str(val)

    @classmethod
    def str_to_number(cls, val, *args):
        try:
            return float(val)
        except ValueError:
            return 0.0

    @classmethod
    def timecode_to_str_generic(
        cls,
        timecode,
        decimal_length=2,
        seconds_length=2,
        minutes_length=2,
        hours_length=1,
    ):
        if decimal_length > 0:
            total_length = seconds_length + decimal_length + 1
        else:
            total_length = seconds_length

        tc_parts = [
            "{{0:0{0:d}d}}".format(hours_length).format(int(timecode // 3600)),
            "{{0:0{0:d}d}}".format(minutes_length).format(int((timecode // 60) % 60)),
            "{{0:0{0:d}.{1:d}f}}".format(total_length, decimal_length).format(timecode % 60),
        ]
        return ":".join(tc_parts)

    @classmethod
    def timecode_to_str(cls, val, *args):
        return cls.timecode_to_str_generic(val, 2)

    @classmethod
    def str_to_timecode(cls, val, *args):
        time = 0.0
        mult = 1

        for t in reversed(val.split(":")):
            time += float(t) * mult
            mult *= 60

        return time

    @classmethod
    def style_to_str(cls, val, *args):
        if val is None:
            return ""
        return val.Name

    @classmethod
    def str_to_style(cls, val, style_map, style_constructor, *args):
        if val in style_map:
            return style_map[val]

        # Create fake
        style = style_constructor()
        style.fake = True
        style.Name = val

        # Add to map (will not be included in global style list, but allows for duplicate "fake" styles to reference the same object)
        style_map[style.Name] = style

        # Return the new style
        return style

    @classmethod
    def tag_argument_to_number(cls, arg, default_value=None):
        match = cls.__re_tag_number.match(arg)
        if match is None:
            return default_value
        return float(match.group(1))
