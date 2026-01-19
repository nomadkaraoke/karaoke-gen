class Event:
    aliases = {}
    formatters = None
    order = [
        "Layer",
        "Start",
        "End",
        "Style",
        "Name",
        "MarginL",
        "MarginR",
        "MarginV",
        "Effect",
        "Text",
    ]

    # Constructor
    def __init__(self):
        self.type = None

        self.Layer = 0
        self.Start = 0.0
        self.End = 0.0
        self.Style = None
        self.Name = ""
        self.MarginL = 0
        self.MarginR = 0
        self.MarginV = 0
        self.Effect = ""
        self.Text = ""

    def set(self, attribute_name, value, *args):
        if hasattr(self, attribute_name) and attribute_name[0].isupper():
            setattr(
                self,
                attribute_name,
                self.formatters[attribute_name][0](value, *args),
            )

    def get(self, attribute_name, *args):
        if hasattr(self, attribute_name) and attribute_name[0].isupper():
            return self.formatters[attribute_name][1](getattr(self, attribute_name), *args)
        return None

    def copy(self, other=None):
        if other is None:
            other = self.__class__()
            target = other
            source = self
        else:
            target = other
            source = self

        # Copy all attributes
        target.type = source.type
        target.Layer = source.Layer
        target.Start = source.Start
        target.End = source.End
        target.Style = source.Style
        target.Name = source.Name
        target.MarginL = source.MarginL
        target.MarginR = source.MarginR
        target.MarginV = source.MarginV
        target.Effect = source.Effect
        target.Text = source.Text

        return target

    def equals(self, other):
        return (
            self.type == other.type
            and self.Layer == other.Layer
            and self.Start == other.Start
            and self.End == other.End
            and self.Style is other.Style
            and self.Name == other.Name
            and self.MarginL == other.MarginL
            and self.MarginR == other.MarginR
            and self.MarginV == other.MarginV
            and self.Effect == other.Effect
            and self.Text == other.Text
        )

    def same_style(self, other):
        return (
            self.type == other.type
            and self.Layer == other.Layer
            and self.Style is other.Style
            and self.Name == other.Name
            and self.MarginL == other.MarginL
            and self.MarginR == other.MarginR
            and self.MarginV == other.MarginV
            and self.Effect == other.Effect
        )
