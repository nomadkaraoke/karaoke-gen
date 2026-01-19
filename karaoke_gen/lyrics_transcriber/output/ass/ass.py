#!/usr/bin/env python
import os, re, sys, functools, collections
from karaoke_gen.lyrics_transcriber.output.ass.event import Event
from karaoke_gen.lyrics_transcriber.output.ass.style import Style
from karaoke_gen.lyrics_transcriber.output.ass.formatters import Formatters
from karaoke_gen.lyrics_transcriber.output.ass.constants import (
    ALIGN_BOTTOM_LEFT,
    ALIGN_BOTTOM_CENTER,
    ALIGN_BOTTOM_RIGHT,
    ALIGN_MIDDLE_LEFT,
    ALIGN_MIDDLE_CENTER,
    ALIGN_MIDDLE_RIGHT,
    ALIGN_TOP_LEFT,
    ALIGN_TOP_CENTER,
    ALIGN_TOP_RIGHT,
    LEGACY_ALIGNMENT_TO_REGULAR,
)

version_info = (1, 0, 4)


# Advanced SubStation Alpha read/write/modification class
class ASS:

    Event.formatters = {
        "Layer": (Formatters.str_to_integer, Formatters.integer_to_str),
        "Start": (Formatters.str_to_timecode, Formatters.timecode_to_str),
        "End": (Formatters.str_to_timecode, Formatters.timecode_to_str),
        "Style": (Formatters.str_to_style, Formatters.style_to_str),
        "Name": (Formatters.same, Formatters.same),
        "MarginL": (Formatters.str_to_integer, Formatters.integer_to_str),
        "MarginR": (Formatters.str_to_integer, Formatters.integer_to_str),
        "MarginV": (Formatters.str_to_integer, Formatters.integer_to_str),
        "Effect": (Formatters.same, Formatters.same),
        "Text": (Formatters.same, Formatters.same),
    }

    class Info:
        # Constructor
        def __init__(self, key, value):
            self.key = key
            self.value = value

    __re_ass_read_section_label = re.compile(r"^(?:\[(.+)\])$", re.U)
    __re_ass_read_key_value = re.compile(r"^([^:]+):\s?(.+)$", re.U)
    __re_tag_block = re.compile(r"(\{)(.*?)(\})", re.U)
    __re_tag_block_or_special = re.compile(r"(\{)(.+?)(\})|(\\[hnN])", re.U)
    __tags_with_parentheses = {
        "t": True,
        "fad": True,
        "org": True,
        "pos": True,
        "clip": True,
        "fade": True,
        "move": True,
        "iclip": True,
    }
    __tags_transformable = {
        "c": 1,
        "1c": 1,
        "2c": 1,
        "3c": 1,
        "4c": 1,
        "alpha": 1,
        "1a": 1,
        "2a": 1,
        "3a": 1,
        "4a": 1,
        "fs": 1,
        "fr": 1,
        "frx": 1,
        "fry": 1,
        "frz": 1,
        "fscx": 1,
        "fscy": 1,
        "fsp": 1,
        "bord": 1,
        "xbord": 1,
        "ybord": 1,
        "shad": 1,
        "xshad": 1,
        "yshad": 1,
        "clip": 4,
        "iclip": 4,
        "blur": 1,
        "be": 1,
        "fax": 1,
        "fay": 1,
    }
    __tags_animated = {
        "t": True,
        "k": True,
        "K": True,
        "kf": True,
        "ko": True,
        "move": True,
        "fad": True,
        "fade": True,
    }
    __re_tag = re.compile(
        r"""\\(?:
		(?:(fad|pos|org) \( ([^\\]+?) , ([^\\]+?) \) ) |
		(?:(move) \( ([^\\]+?) , ([^\\]+?) , ([^\\]+?) , ([^\\]+?) (?:, ([^\\]+?) , ([^\\]+?))? \) ) |
		(?:(fade) \( ([^\\]+?) , ([^\\]+?) , ([^\\]+?) , ([^\\]+?) , ([^\\]+?) , ([^\\]+?) , ([^\\]+?) \) ) |
		(?:(clip|iclip) \( ([^\\]+?) (?:, ([^\\]+?) (?:, ([^\\]+?) , ([^\\]+?))?)? \) ) |
		(?:(t) \( ([^,]+?) (?:, ([^,]+?) (?:, ([^,]+?) (?:, ([^,]+?))?)?)? \) ) |
		(?:(c|1c|2c|3c|4c) (&?H? [0-9a-fA-F]{1,6} &?) ) |
		(?:(alpha|1a|2a|3a|4a) (&?H? [0-9a-fA-F]{1,2} &?) ) |
		(i0|i1|u0|u1|s0|s1) |
		(?:(r) ([^\\]+)?) |
		(?:(xbord|xshad|ybord|yshad | bord|blur|fscx|fscy|shad | fax|fay|frx|fry|frz|fsp|pbo | an|be|fe|fn|fs|fr|kf|ko | a|b|k|K|p|q) ([^\\]+))
		)()??""",
        re.VERBOSE | re.U,
    )
    __re_draw_command = re.compile(r"([a-zA-Z]+)((?:\s+(?:[\+\-]?[0-9]+))*)", re.U)
    __re_remove_special = re.compile(r"(\s*)(?:\\([hnN]))(\s*)")
    __re_filename_format = (re.compile(r".py[co]$"), ".py")
    __re_draw_command_split = re.compile(r"\s+")
    __re_draw_commands_ord_min = ord("a")
    __re_draw_commands_ord_max = ord("z")

    @classmethod
    def __split_line(cls, line, split_time, naive):
        if split_time <= line.Start or split_time >= line.End:
            return None
            # Nothing to split

        modify_tag = None
        if not naive:
            modify_tag = lambda t: cls.__split_line_modify_tag(t, split_time)

        # Before
        before = line.copy()
        before.End = split_time
        before.Text = cls.parse_text(before.Text, modify_tag=modify_tag)

        # After
        after = line.copy()
        after.Start = split_time
        after.Text = cls.parse_text(after.Text, modify_tag=modify_tag)

        # Done
        return (before, after)

    @classmethod
    def __split_line3(cls, line, split_time, naive=False):
        if split_time < line.Start or split_time > line.End:
            return None
            # Nothing to split

        modify_tag = None
        if not naive:
            modify_tag = lambda t: cls.__split_line_modify_tag(t, split_time)

        # Before
        if line.Start < split_time:
            before = line.copy()
            before.End = split_time
            before.Text = cls.parse_text(before.Text, modify_tag=modify_tag)
        else:
            before = None

        # After
        if line.End > split_time:
            after = line.copy()
            after.Start = split_time
            after.Text = cls.parse_text(after.Text, modify_tag=modify_tag)
        else:
            after = None

        # Middle part
        middle = line.copy()
        middle.Start = split_time
        middle.End = split_time
        middle.Text = cls.parse_text(middle.Text, modify_tag=modify_tag)

        return (before, middle, after)

    @classmethod
    def __split_line_modify_tag(cls, tag, split_time):
        # This may better modify tags later, for now it's also a naive copy
        return [tag]

    __same_time_max_delta = 1.0e-5

    @classmethod
    def __join_lines(cls, line1, line2, naive):
        if abs(line2.End - line1.Start) <= cls.__same_time_max_delta:
            # Flip
            linetemp = line1
            line1 = line2
            line2 = linetemp

        # Join check
        line_join = None
        if abs(line1.End - line2.Start) <= cls.__same_time_max_delta:
            # Might be joinable
            if line1.Text == line2.Text:
                # Check if there are no animations
                if naive or not cls.__line_has_animations(line1.Text):
                    # Copy and return
                    line_join = line1.copy()
                    line_join.End = line2.End

        # Not joinable
        return line_join

    @classmethod
    def __line_has_animations(cls, text):
        state = {
            "animations": 0,
        }
        cls.parse_text(text, modify_tag=lambda t: cls.__line_has_animations_modify_tag(state, t))
        return state["animations"] > 0

    @classmethod
    def __line_has_animations_modify_tag(cls, state, tag):
        if tag[0] in cls.__tags_animated:
            state["animations"] += 1

        return [tag]

    @classmethod
    def __kwarg_default(cls, kwargs, key, default_value):
        if key in kwargs:
            return kwargs[key]
        return default_value

    def __change_event_styles(self, style_src, style_dest):
        for line in self.events:
            if line.Style is style_src:
                line.Style = style_dest

    def __get_minimum_timecode(self):
        if len(self.events) == 0:
            return 0.0

        t = self.events[0].Start
        for i in range(1, len(self.events)):
            t2 = self.events[i].Start
            if t2 < t:
                t = t2

        return t

    def __get_maximum_timecode(self):
        if len(self.events) == 0:
            return 0.0

        t = self.events[0].End
        for i in range(1, len(self.events)):
            t2 = self.events[i].End
            if t2 > t:
                t = t2

        return t

    def __range_cut(self, filter_types, start, end, naive):
        # Split
        if start is not None or end is not None:
            i = 0
            i_max = len(self.events)
            while i < i_max:
                line = self.events[i]
                if filter_types is None or line.type in filter_types:
                    # Must be dialogue
                    if start is not None:
                        # Split
                        line_split = self.__split_line(line, start, naive=naive)
                        if line_split is not None:
                            line = line_split[1]
                            self.events[i] = line
                            self.events.append(line_split[0])

                    if end is not None:
                        # Split
                        line_split = self.__split_line(line, end, naive=naive)
                        if line_split is not None:
                            self.events[i] = line_split[0]
                            self.events.append(line_split[1])

                # Next
                i += 1

    def __range_action(self, filter_types, start, end, full_inclusion, inverse, action):
        # Modify lines
        i = 0
        i_max = len(self.events)
        while i < i_max:
            line = self.events[i]
            if filter_types is None or line.type in filter_types:
                if full_inclusion:
                    perform = (start is None or line.Start >= start) and (end is None or line.End <= end)
                else:
                    perform = (start is None or line.End > start) and (end is None or line.Start < end)

                if perform ^ inverse:
                    # action should return None if the line should be removed, else it should return an Event object (likely the same one that was input)
                    # action should NOT remove/add any events
                    line_res = action(line)
                    if line_res is None:
                        self.events.pop(i)
                        i_max -= 1
                        continue
                    elif line_res is not line:
                        self.events[i] = line_res

            # Next
            i += 1

    def __set_script_info(self, key, value):
        if key not in self.script_info:
            instance = self.Info(key, value)
            self.script_info_ordered.append(instance)
            self.script_info[key] = instance
        else:
            self.script_info[key].value = value

    @classmethod
    def __legacy_align_to_regular(cls, value, default_value=None):
        value = str(value)
        if value in cls.__legacy_alignment_to_regular:
            return cls.__legacy_alignment_to_regular[value]
        return default_value

    # Python 2/3 support
    if sys.version_info[0] == 3:
        # Version 3
        @classmethod
        def __py_2or3_var_is_string(cls, obj):
            return isinstance(obj, str)

    else:
        # Version 2
        @classmethod
        def __py_2or3_var_is_string(cls, obj):
            return isinstance(obj, basestring)

    # Constructor
    def __init__(self):
        self.script_info_ordered = []
        self.script_info = {}

        self.styles_format = []
        self.styles = []

        self.events_format = []
        self.events = []

    # Reading/writing
    def read(self, filename):
        # Clear
        self.script_info_ordered = []
        self.script_info = {}

        self.styles_format = []
        self.styles = []
        styles_map = {}

        self.events_format = []
        self.events = []

        # Read and decode
        f = open(filename, "rb")
        s = f.read()
        f.close()

        s = s.decode("utf-8")
        # Decode using UTF-8
        s = s.replace("\ufeff", "")
        # Replace any BOM

        # Target region
        target_format = None
        target_map = None
        target_map_key_getter = None
        target_list = None
        target_class = None
        target_class_set_args = None

        # Iterate over each line
        lines = s.splitlines()
        for i in range(len(lines)):
            line = lines[i]

            # [Labeled Section]
            match = self.__re_ass_read_section_label.match(line)
            if match is not None:
                line = match.group(1)
                if line == "Script Info":
                    target_format = None
                    target_map = self.script_info
                    target_map_key_getter = lambda i: i.key
                    target_list = self.script_info_ordered
                    target_class = None
                    target_class_set_args = None
                elif line == "V4 Styles" or line == "V4+ Styles":
                    target_format = self.styles_format
                    target_map = styles_map
                    target_map_key_getter = lambda i: i.Name
                    target_list = self.styles
                    target_class = self.Style
                    target_class_set_args = []
                elif line == "Events":
                    target_format = self.events_format
                    target_map = None
                    target_map_key_getter = None
                    target_list = self.events
                    target_class = self.Event
                    target_class_set_args = [styles_map, self.Style]
                else:
                    # Invalid or not supported
                    target = None
            elif target_list is None:
                # No target
                pass
            elif len(line) == 0 or line[0] == ";":
                # Comment or empty line
                pass
            else:
                match = self.__re_ass_read_key_value.match(line)
                if match is not None:
                    # Valid
                    if target_format is None:
                        # Direct map [Script Info]
                        instance = self.Info(match.group(1), match.group(2))
                        target_list.append(instance)
                        target_map[target_map_key_getter(instance)] = instance
                    elif match.group(1) == "Format" and len(target_format) == 0:
                        # Setup target format
                        for f in match.group(2).split(","):
                            target_format.append(f.strip())
                    else:
                        # Map and add
                        values = match.group(2).split(",", len(target_format) - 1)
                        instance = target_class()
                        instance.type = match.group(1)

                        for i in range(len(values)):
                            instance.set(target_format[i], values[i], *target_class_set_args)

                        target_list.append(instance)
                        if target_map is not None:
                            target_map[target_map_key_getter(instance)] = instance

        # Done
        return self

    def write(self, filename, comments=None):
        # Generate source
        source = [
            "[Script Info]\n",
        ]

        # Comments
        if comments is None:
            # Default comment
            source.extend(
                [
                    "; Script generated by {0:s}\n".format(
                        self.__re_filename_format[0].sub(self.__re_filename_format[1], os.path.split(__file__)[1])
                    ),
                ]
            )
        else:
            # Custom comments
            source.extend(["; {0:s}".format(c) for c in comments])

        # Script info
        for entry in self.script_info_ordered:
            if entry.key in self.script_info:
                source.append("{0:s}: {1:s}\n".format(entry.key, entry.value))

        source.append("\n")

        # Styles
        source.append("[V4+ Styles]\n")
        source.append("Format: {0:s}\n".format(", ".join(self.styles_format)))
        for style in self.styles:
            style_list = []
            for key in self.styles_format:
                style_list.append(style.get(key))
            source.append("{0:s}: {1:s}\n".format(style.type, ",".join(style_list)))
        source.append("\n")

        # Events
        source.append("[Events]\n")
        source.append("Format: {0:s}\n".format(", ".join(self.events_format)))
        for event in self.events:
            if event.Start >= 0 and event.End >= 0:
                event_list = []
                for key in self.events_format:
                    event_list.append(event.get(key))
                source.append("{0:s}: {1:s}\n".format(event.type, ",".join(event_list)))

        # Write file
        f = open(filename, "wb")
        s = f.write(("".join(source)).encode("utf-8"))
        f.close()

        # Done
        return self

    def write_srt(self, filename, **kwargs):
        # Parse kwargs
        overlap = self.__kwarg_default(kwargs, "overlap", True)
        # if True, overlapping timecodes are allowed; else, overlapping timecodes are split
        newlines = self.__kwarg_default(kwargs, "newlines", False)
        # if True, minimal newlines are preserved
        remove_identical = self.__kwarg_default(kwargs, "remove_identical", True)
        # if True, identical lines (after tags are changed/removed) are removed
        join = self.__kwarg_default(kwargs, "join", True)
        # if True, identical sequential lines are joined
        filter_function = self.__kwarg_default(kwargs, "filter_function", None)
        # custom function to filter lines: takes 2 arguments: (event, final_text) and should return the same (or modified) final_text to keep, or None to remove

        # Source
        source = []

        # Events
        sorted_events = []
        for i in range(len(self.events)):
            event = self.events[i]
            if event.type == "Dialogue" and event.Start < event.End and event.Start >= 0:
                meta_event = self.__WriteSRTMetaEvent(event, i)
                meta_event.format_text(self, newlines)
                if len(meta_event.text) > 0:
                    sorted_events.append(meta_event)
        sorted_events.sort(key=lambda e: e.start, reverse=overlap)
        # reverse if overlap is allowed, since items are .pop'd from the end

        # Filter
        event_count = len(sorted_events)
        if remove_identical:
            i = 0
            while i < event_count:
                event = sorted_events[i]
                j = i + 1
                while j < event_count:
                    if event.equals(sorted_events[j]):
                        # Remove
                        sorted_events.pop(j)
                        event_count -= 1
                        continue
                    elif event.start < sorted_events[j].start:
                        # Done
                        break

                    # Next
                    j += 1

                # Next
                i += 1
        if filter_function is not None:
            i = 0
            while i < event_count:
                result = filter_function(sorted_events[i].event, sorted_events[i].text)
                if result is None:
                    # Remove
                    sorted_events.pop(i)
                    event_count -= 1
                    continue
                else:
                    sorted_events[i].text = result

                # Next
                i += 1

        # Format
        lines = []
        while event_count > 0:
            if overlap:
                # Simple mode; no overlap check
                event_data = sorted_events.pop()
                block_start = event_data.start
                block_end = event_data.event.End
                stack_lines = [event_data]
                event_count -= 1
            else:
                # Find time block range
                event_data = sorted_events[0]
                block_start = event_data.start
                block_end = event_data.event.End
                for i in range(1, event_count):
                    event_data = sorted_events[i]
                    if event_data.start < block_start + self.__same_time_max_delta:  # will set even if same
                        block_start = event_data.start
                        if event_data.event.End <= block_end - self.__same_time_max_delta:  # will set only if lower
                            block_end = event_data.event.End
                    elif event_data.start <= block_end - self.__same_time_max_delta:  # will set only if lower
                        block_end = event_data.start
                    assert block_start < block_end
                    # should never happen

                # Discover lines
                ac = event_count
                i = 0
                stack_lines = []
                stack_lines_ordered = collections.deque()
                stack_lines_unordered = collections.deque()
                while i < event_count:
                    event_data = sorted_events[i]
                    if event_data.start <= block_end - self.__same_time_max_delta:
                        # This line is included
                        if event_data.y_pos >= 0:
                            stack_lines_ordered.append(event_data)
                        else:
                            stack_lines_unordered.append(event_data)
                        if event_data.event.End <= block_end + self.__same_time_max_delta:
                            # Remove
                            sorted_events.pop(i)
                            event_count -= 1
                            continue
                        else:
                            # Update start
                            sorted_events[i].start = block_end

                    # Next
                    i += 1

                # Sort lines
                i = 0
                # stack_lines_ordered = collections.deque(sorted(stack_lines_ordered, key=lambda e: e[1]));
                while len(stack_lines_ordered) > 0 and len(stack_lines_unordered) > 0:
                    if stack_lines_ordered[0].y_pos == i:
                        stack_lines.append(stack_lines_ordered.popleft())
                        found = True
                    else:
                        e = stack_lines_unordered.popleft()
                        stack_lines.append(e)

                    # Next
                    i += 1
                stack_lines.extend(stack_lines_ordered)
                if len(stack_lines_unordered) > 1:
                    # Sort by vertical position; this is convenient for multiple lines appearing simultaneously; there are still cases ordering may be messed up
                    stack_lines_unordered = sorted(
                        stack_lines_unordered,
                        key=functools.cmp_to_key(lambda e1, e2: self.__write_srt_sort_lines_compare(e1, e2)),
                    )
                stack_lines.extend(stack_lines_unordered)
                for i in range(len(stack_lines)):
                    stack_lines[i].y_pos = i

            # Process lines
            text = []
            for e in reversed(stack_lines):
                text.append(e.text)

            # Add
            lines.append([block_start, block_end, "\n".join(text)])

        # Join
        if join:
            i = 0
            i_max = len(lines) - 1
            while i < i_max:
                if lines[i][2] == lines[i + 1][2] and lines[i][1] == lines[i + 1][0]:
                    lines[i][1] = lines[i + 1][1]
                    lines.pop(i + 1)
                    i_max -= 1
                    continue

                # Next
                i += 1

        # Process
        for i in range(len(lines)):
            line_start, line_end, line_text = lines[i]

            source.append("{0:d}\n".format(i + 1))
            source.append(
                "{0:s} --> {1:s}\n".format(
                    Formatters.timecode_to_str_generic(line_start, 3, 2, 2, 2).replace(".", ","),
                    Formatters.timecode_to_str_generic(line_end, 3, 2, 2, 2).replace(".", ","),
                )
            )
            source.append("{0:s}\n\n".format(line_text))

        # Write file
        f = open(filename, "wb")
        s = f.write(("".join(source)).encode("utf-8"))
        f.close()

        # Done
        return self

    def __write_srt_sort_lines_compare(self, line1, line2):
        # Sort by position
        order = 1
        pos1 = self.__get_line_position(line1.event)
        pos2 = self.__get_line_position(line2.event)
        if pos1 is not None and pos2 is not None:
            if pos1[1] > pos2[1]:
                return -order
            if pos1[1] < pos2[1]:
                return order

        # Sort by vertical alignment
        align1_y = self.get_xy_alignment(self.get_line_alignment(line1.event, True))[1]
        align2_y = self.get_xy_alignment(self.get_line_alignment(line2.event, True))[1]

        if align1_y > align2_y:
            return -order
        if align1_y < align2_y:
            return order

        if align1_y < 0:
            order = -order
            # switch

        # Sort by vertical margin
        margin1 = line1.event.MarginV
        margin2 = line2.event.MarginV
        if margin1 == 0:
            margin1 = line1.event.Style.MarginV
        if margin2 == 0:
            margin2 = line2.event.Style.MarginV

        if margin1 < margin2:
            return -order
        if margin1 > margin2:
            return order

        # Sort by order of appearance
        if line1.index < line2.index:
            return -order
        if line1.index > line2.index:
            return order
        return 0

    class __WriteSRTMetaEvent:
        def __init__(self, event, i):
            self.event = event
            self.start = event.Start
            self.y_pos = -1
            self.index = i
            self.text = None

        def equals(self, other):
            return self.text == other.text and self.start == other.start and self.event.End == other.event.End

        def format_text(self, parent, newlines):
            self.text = parent.parse_text(
                self.event.Text,
                modify_text=(lambda t: self.__write_srt_format_text(parent, newlines, t)),
                modify_tag_block=(lambda b: ""),
                modify_geometry=(lambda g: ""),
            )

        def __write_srt_format_text(self, parent, newlines, text):
            return parent.replace_special(text, (lambda c: self.__write_srt_format_text_space(newlines, c)), 1, 1)

        def __write_srt_format_text_space(self, newlines, character):
            if character == "h":
                return "\u00A0"
            if newlines:
                return "\n"
            return " "

    def set_resolution(self, resolution):
        self.__set_script_info("PlayResX", str(resolution[0]))
        self.__set_script_info("PlayResY", str(resolution[1]))

    # Script resolution
    def resolution(self):
        w = 0
        h = 0
        if "PlayResX" in self.script_info:
            try:
                w = int(self.script_info["PlayResX"].value, 10)
            except ValueError:
                pass
        if "PlayResY" in self.script_info:
            try:
                h = int(self.script_info["PlayResY"].value, 10)
            except ValueError:
                pass

        return (w, h)

    # Alignment
    @classmethod
    def get_line_alignment(cls, event, deep=True):
        state = [None]

        # Check more
        if deep:
            cls.parse_text(
                event.Text,
                modify_tag=(lambda t: cls.__get_line_alignment_modify_tag(state, t)),
            )

        # Return
        if state[0] is None:
            state[0] = event.Style.Alignment
        return state[0]

    @classmethod
    def __get_line_alignment_modify_tag(cls, state, tag):
        if state[0] is None:
            tag_name = tag[0]
            if tag_name == "a":
                state[0] = cls.__legacy_align_to_regular(Formatters.str_to_number(tag[1]))
            elif tag_name == "an":
                state[0] = Formatters.str_to_number(tag[1])

        # Done
        return [tag]

    @classmethod
    def get_xy_alignment(cls, align):
        if align >= ALIGN_TOP_LEFT and align <= ALIGN_TOP_RIGHT:
            align_y = -1
            if align == ALIGN_TOP_LEFT:
                align_x = -1
            elif align == ALIGN_TOP_RIGHT:
                align_x = 1
            else:
                align_x = 0
        elif align >= ALIGN_MIDDLE_LEFT and align <= ALIGN_MIDDLE_RIGHT:
            align_y = 0
            if align == ALIGN_MIDDLE_LEFT:
                align_x = -1
            elif align == ALIGN_MIDDLE_RIGHT:
                align_x = 1
            else:
                align_x = 0
        else:  # if (align >= ALIGN_BOTTOM_LEFT and align <= ALIGN_BOTTOM_RIGHT):
            align_y = 1
            if align == ALIGN_BOTTOM_LEFT:
                align_x = -1
            elif align == ALIGN_BOTTOM_RIGHT:
                align_x = 1
            else:
                align_x = 0

        return (align_x, align_y)

    @classmethod
    def __get_line_position(cls, event):
        state = [None]

        # Check more
        cls.parse_text(
            event.Text,
            modify_tag=(lambda t: cls.__get_line_position_modify_tag(state, t)),
        )

        # Return
        return state[0]

    @classmethod
    def __get_line_position_modify_tag(cls, state, tag):
        if state[0] is None:
            tag_name = tag[0]
            if tag_name == "pos":
                try:
                    state[0] = (float(tag[1]), float(tag[2]))
                except ValueError:
                    pass

        # Done
        return [tag]

    # Line parsing
    @classmethod
    def parse_text(
        cls,
        text,
        modify_text=None,
        modify_special=None,
        modify_tag_block=None,
        modify_tag=None,
        modify_comment=None,
        modify_geometry=None,
    ):
        """
        modify_tag:
                inputs:
                        tag_args - an array of the form:
                        [ tag_name , tag_arg1 , tag_arg2 , ... ]
                        where all tag_arg#'s are optional
                return:
                        must return an array containing only "tag_args" and strings
                        - "tag_args" are auto-converted into strings
                        - strings are treated as comments, or pre-formatted tags

        <everything else>:
                inputs:
                        the relevant string
                return:
                        the relevant string, modified

        Note:
                if modify_special is None, then "\\h", "\\n", and "\\N" will be treated part of text sections (i.e. they are not separated)
        """
        text_new = []

        if modify_special is None:
            re_matcher = cls.__re_tag_block
        else:
            re_matcher = cls.__re_tag_block_or_special

        next_geometry_scale = 0
        pos = 0

        for match in re_matcher.finditer(text):
            # Previous text
            if match.start(0) > pos:
                t = text[pos : match.start(0)]
                if next_geometry_scale <= 0:
                    if modify_text is not None:
                        t = modify_text(t)
                else:
                    if modify_geometry is not None:
                        t = modify_geometry(t)

                text_new.append(t)

            # Tag block
            if match.group(2) is None:
                t = match.group(4)
                t = modify_special(t)
                text_new.append(t)
            else:
                tag_new = [match.group(1)]

                # Parse individual tags
                tag_text, next_geometry_scale = cls.parse_tags(match.group(2), modify_tag, modify_comment, next_geometry_scale)
                tag_text = match.group(1) + tag_text + match.group(3)

                if modify_tag_block is not None:
                    tag_text = modify_tag_block(tag_text)

                text_new.append(tag_text)

            # Next
            pos = match.end(0)

        # Final
        if pos < len(text):
            t = text[pos:]
            if next_geometry_scale <= 0:
                if modify_text is not None:
                    t = modify_text(t)
            else:
                if modify_geometry is not None:
                    t = modify_geometry(t)

            text_new.append(t)

        # Done
        return "".join(text_new)

    @classmethod
    def parse_tags(cls, text, modify_tag=None, modify_comment=None, next_geometry_scale=0):
        """
        modify_tag:
                inputs:
                        tag_args - an array of the form:
                        [ tag_name , tag_arg1 , tag_arg2 , ... ]
                        where all tag_arg#'s are optional
                return:
                        must return an array containing only "tag_args" and strings
                        - "tag_args" are auto-converted into strings
                        - strings are treated as comments, or pre-formatted tags

        <everything else>:
                inputs:
                        the relevant string
                return:
                        the relevant string, modified
        """
        text_new = []
        pos = 0
        for match in cls.__re_tag.finditer(text):
            # Comment
            if match.start(0) > pos:
                tt = text[pos : match.start(0)]
                if modify_comment is not None:
                    tt = modify_comment(tt)
                text_new.append(tt)

            # Tag
            tt = match.group(0)
            tg = match.groups()

            start = 0
            while tg[start] is None:
                start += 1
            end = start + 1
            while tg[end] is not None:
                end += 1

            tag_args = tg[start:end]

            if modify_tag is None:
                tag_args_array = [tag_args]
            else:
                tag_args_array = modify_tag(tag_args)

                # Convert to a string
                tt_array = []
                for tag_args in tag_args_array:
                    if cls.__py_2or3_var_is_string(tag_args):
                        tt = tag_args
                    else:
                        if tag_args[0] in cls.__tags_with_parentheses:
                            tt = "\\{0:s}({1:s})"
                        else:
                            tt = "\\{0:s}{1:s}"
                        tt = tt.format(tag_args[0], ",".join(tag_args[1:]))
                    tt_array.append(tt)
                tt = "".join(tt_array)

            for tag_args in tag_args_array:
                if tag_args[0] == "p":
                    # Drawing command
                    next_geometry_scale = Formatters.tag_argument_to_number(tag_args[1], 0)

            text_new.append(tt)

            # Next
            pos = match.end(0)

        # Final comment
        if pos < len(text):
            tt = text[pos:]
            if modify_comment is not None:
                tt = modify_comment(tt)
            text_new.append(tt)

        # Done
        return ("".join(text_new), next_geometry_scale)

    # Other parsing
    @classmethod
    def replace_special(cls, text, space=" ", min_whitespace_length=1, max_whitespace_length=1):
        return cls.__re_remove_special.sub(
            (lambda m: cls.__replace_special_replacer(m, space, min_whitespace_length, max_whitespace_length)),
            text,
        )

    @classmethod
    def __replace_special_replacer(cls, match, space, min_whitespace_length, max_whitespace_length):
        ws = match.group(1) + match.group(3)
        ws_len = len(ws)

        if ws_len < min_whitespace_length or (ws_len > max_whitespace_length and max_whitespace_length >= 0):
            if hasattr(space, "__call__"):
                return space(match.group(2))
            return space

        return ws

    # Regenerate format orders
    def reformat(self, **kwargs):
        # Parse kwargs
        alias = self.__kwarg_default(kwargs, "alias", False)
        # doesn't do anything since there aren't aliases for events; kept for consistenccy

        # Process
        main_cls = self.Event
        new_format = list(main_cls.order)

        # Alias
        if alias:
            for i in range(len(new_format)):
                attr_name = new_format[i]
                if attr_name in main_cls.aliases:
                    new_format[i] = main_cls.aliases[attr_name]

        # Apply
        self.events_format = new_format

        # Done
        return self

    def reformat_styles(self, **kwargs):
        # Parse kwargs
        alias = self.__kwarg_default(kwargs, "alias", False)
        # if False, British spellings of "colour" are used (.ass files seem to function either way)

        # Process
        main_cls = self.Style
        new_format = list(main_cls.order)

        # Alias
        if alias:
            for i in range(len(new_format)):
                attr_name = new_format[i]
                if attr_name in main_cls.aliases:
                    new_format[i] = main_cls.aliases[attr_name]

        # Apply
        self.styles_format = new_format

        # Done
        return self

    # Add events/styles
    def add(self, event):
        self.events.append(event)

        # Check if a new style is necessary
        if not event.Style.fake:
            same_style = None
            for style in self.styles:
                if event.Style is style:
                    # Already exists
                    return
                elif event.Style.equals(style):
                    # Already exists
                    same_style = style

            if same_style is not None:
                # Copy
                event.Style = same_style
            else:
                # Add a new style
                event.Style = event.Style.copy()
                self.add_style(event.Style)

        # Done
        return self

    def add_style(self, style):
        self.styles.append(style)

        # Done
        return self

    # Tidy modifications
    def tidy(self, **kwargs):  # Join duplicates, sort
        # Parse kwargs
        sort = self.__kwarg_default(kwargs, "sort", False)
        # if True, events are sorted by starting time
        join = self.__kwarg_default(kwargs, "join", False)
        # if True, sequential events that would be visible as one are joined
        join_naive = self.__kwarg_default(kwargs, "join_naive", False)
        # if True, line joining will ignore any animation tags and join them anyway
        remove_unseen = self.__kwarg_default(kwargs, "remove_unseen", True)
        # if True, events with a duration of 0 (or less) are removed
        snap_start = self.__kwarg_default(kwargs, "snap_start", 0.0)
        # if greater than 0, starting timecodes within the specified time will be snapped together
        snap_end = self.__kwarg_default(kwargs, "snap_end", 0.0)
        # if greater than 0, ending timecodes within the specified time will be snapped together
        snap_together = self.__kwarg_default(kwargs, "snap_together", 0.0)
        # if greater than 0, start/end or end/start timecodes within the specified time will be snapped together

        # Snap
        if snap_start > 0:
            for i in range(len(self.events)):
                e1 = self.events[i]
                for j in range(i + 1, len(self.events)):
                    e2 = self.events[j]
                    if abs(e1.Start - e2.Start) <= snap_start:
                        # Perform snap
                        e2.Start = e1.Start

        if snap_end > 0:
            for i in range(len(self.events)):
                e1 = self.events[i]
                for j in range(i + 1, len(self.events)):
                    e2 = self.events[j]
                    if abs(e1.End - e2.End) <= snap_end:
                        # Perform snap
                        e2.End = e1.End

        if snap_together > 0:
            for i in range(len(self.events)):
                e1 = self.events[i]
                for j in range(i + 1, len(self.events)):
                    e2 = self.events[j]
                    if abs(e1.Start - e2.End) <= snap_together:
                        # Perform snap
                        e2.End = e1.Start
                    if abs(e1.End - e2.Start) <= snap_together:
                        # Perform snap
                        e2.Start = e1.End

        # Join
        if join:
            i = 0
            events_len = len(self.events)
            while i < events_len:
                e1 = self.events[i]

                j = 0
                while j < events_len:
                    if j != i:
                        # Styles match
                        e2 = self.events[j]
                        if e1.same_style(e2) and e1.type == e2.type:
                            # Attempt join
                            e_joined = self.__join_lines(e1, e2, join_naive)
                            if e_joined is not None:
                                # Update
                                e1 = e_joined
                                self.events[i] = e1
                                events_len -= 1

                                # Remove
                                self.events.pop(j)
                                if i > j:
                                    i -= 1

                                # Reset loop
                                j = 0
                                continue

                    # Next
                    j += 1

                # Next
                i += 1

        # Sort
        if sort:
            self.events.sort(key=lambda e: e.Start)

        # Remove 0 length
        if remove_unseen:
            i = 0
            i_max = len(self.events)
            while i < i_max:
                e = self.events[i]
                if e.End - e.Start <= 0:
                    self.events.pop(i)
                    i_max -= 1
                    continue

                # Next
                i += 1

        # Done
        return self

    def tidy_styles(self, **kwargs):  # Generate unique names, remove duplicates, and remove unused
        # Parse kwargs
        sort = self.__kwarg_default(kwargs, "sort", False)
        # if True, events are sorted by name
        join = self.__kwarg_default(kwargs, "join", False)
        # if True, duplicates are joined into a single style
        join_if_names_differ = self.__kwarg_default(kwargs, "join_if_names_differ", False)
        # if True, styles are joined even if their names are different
        rename = self.__kwarg_default(kwargs, "rename", False)
        # if True, styles with identical names are renamed
        rename_function = self.__kwarg_default(kwargs, "rename_function", None)
        # if not None, then this is a function deciding the new name; format is rename_function(style_name, copy_index); it is only called on duplicate named styles; copy_index starts at 0
        remove_unused = self.__kwarg_default(kwargs, "remove_unused", False)
        # if True, unused styles are removed

        # Setup
        styles_len = len(self.styles)

        # Join
        if join:
            i = 0
            while i < styles_len:
                s1 = self.styles[i]

                j = i + 1
                while j < styles_len:
                    s2 = self.styles[j]
                    if s1.equals(s2, join_if_names_differ):
                        # Join
                        self.__change_event_styles(s2, s1)
                        self.styles.pop(j)
                        styles_len -= 1
                        continue

                    # Next
                    j += 1
                # Next
                i += 1

        # Remove unused
        if remove_unused:
            i = 0
            while i < styles_len:
                s1 = self.styles[i]

                # Count uses
                count = 0
                for event in self.events:
                    if event.Style is s1:
                        count += 1

                # Remove
                if count == 0:
                    self.styles.pop(i)
                    styles_len -= 1
                    continue

                # Next
                i += 1

        # Rename
        if rename:
            if rename_function is None:
                rename_function = lambda n, i: "{0:s} ({1:d})".format(n, i + 1)

            # Sort by name
            name_map = {}
            for style in self.styles:
                if style.Name in name_map:
                    name_map[style.Name].append(style)
                else:
                    name_map[style.Name] = [style]

            # Check for duplicates
            for style_name, styles_list in name_map.items():
                if len(styles_list) > 1:
                    # Rename duplicates
                    for i in range(len(styles_list)):
                        styles_list[i].Name = rename_function(style_name, i)

        # Sort
        if sort:
            self.styles.sort(key=lambda e: e.Name)

        # Done
        return self

    # Modifications
    def shiftscale(self, **kwargs):  # Shift/scale a section's geometry and/or timecodes
        # Parse kwargs
        start = self.__kwarg_default(kwargs, "start", None)
        # time to start at, or None for not bounded
        end = self.__kwarg_default(kwargs, "end", None)
        # time to start at, or None for not bounded

        full_inclusion = self.__kwarg_default(kwargs, "full_inclusion", False)
        # if True, line timecodes must be fully included within the specified range
        inverse = self.__kwarg_default(kwargs, "inverse", False)
        # if True, operation is performed on all lines not included in the timecode range
        split = self.__kwarg_default(kwargs, "split", False)
        # if True, splits lines if they are not fully in the timecode range
        split_naive = self.__kwarg_default(kwargs, "split_naive", False)
        # if True, line splitting will not modify any formatting tags

        filter_types = self.__kwarg_default(kwargs, "filter_types", None)
        # list of event types to include; can be anything supporting the "in" operator; None means no filtering

        time_scale = self.__kwarg_default(kwargs, "time_scale", 1.0)
        # scale timecodes by this factor
        time_scale_origin = self.__kwarg_default(kwargs, "time_scale_origin", 0.0)
        # timecode scaling origin
        time_offset = self.__kwarg_default(kwargs, "time_offset", 0.0)
        # seconds to offset timecodes by
        time_clip_start = self.__kwarg_default(kwargs, "time_clip_start", None)
        # time to clip by; None = ignore; if times are shifted/scaled outside this range, they are removed/truncated as necessary; if inverse=True, this is ignored
        time_clip_end = self.__kwarg_default(kwargs, "time_clip_end", None)
        # time to clip by; None = ignore; if times are shifted/scaled outside this range, they are removed/truncated as necessary; if inverse=True, this is ignored
        geometry_resolution = self.__kwarg_default(kwargs, "geometry_resolution", None)
        # (x,y) new total resolution
        geometry_scale = self.__kwarg_default(kwargs, "geometry_scale", None)
        # (x,y) factors by which to scale geometry
        geometry_scale_origin = self.__kwarg_default(kwargs, "geometry_scale_origin", (0.0, 0.0))
        # (x,y) geometry scaling origin
        geometry_offset = self.__kwarg_default(kwargs, "geometry_offset", (0.0, 0.0))
        # (x,y) geometry shifting offset
        geometry_new_styles = self.__kwarg_default(kwargs, "geometry_new_styles", True)
        # True if new styles should be generated

        # Exceptions
        if start is not None and end is not None and start > end:
            raise ValueError("start cannot be greater than end")

        # Split
        if split:
            self.__range_cut(filter_types, start, end, split_naive)

        # Time scale
        if time_scale != 1.0 or time_offset != 0.0:
            self.__range_action(
                filter_types,
                start,
                end,
                full_inclusion,
                inverse,
                (
                    lambda line: self.__shiftscale_action_time(
                        inverse,
                        split_naive,
                        time_scale,
                        time_scale_origin,
                        time_offset,
                        time_clip_start,
                        time_clip_end,
                        line,
                    )
                ),
            )

        # Update resolution
        resolution_old = self.resolution()
        if geometry_resolution is not None:
            self.__set_script_info("PlayResX", str(geometry_resolution[0]))
            self.__set_script_info("PlayResY", str(geometry_resolution[1]))

            resolution_new = geometry_resolution
            if geometry_scale is None:
                geometry_scale = (
                    geometry_resolution[0] / float(resolution_old[0]),
                    geometry_resolution[1] / float(resolution_old[1]),
                )
        else:
            resolution_new = resolution_old
            if geometry_scale is None:
                geometry_scale = (1.0, 1.0)

        # Geometry scale
        if (
            (geometry_resolution is not None)
            or (geometry_scale[0] != 1.0 or geometry_scale[1] != 1.0)
            or (geometry_offset[0] != 0.0 or geometry_offset[1] != 0.0)
        ):
            # New bounds
            bounds = self.__shiftscale_action_get_new_bounds(
                geometry_scale,
                geometry_scale_origin,
                geometry_offset,
                resolution_old,
                resolution_new,
            )

            # Modify
            used_styles = {}
            self.__range_action(
                filter_types,
                start,
                end,
                full_inclusion,
                inverse,
                (
                    lambda line: self.__shiftscale_action_geometry(
                        geometry_scale,
                        geometry_scale_origin,
                        geometry_offset,
                        resolution_old,
                        resolution_new,
                        bounds,
                        used_styles,
                        line,
                    )
                ),
            )

            # New styles
            if geometry_new_styles:
                scale = (geometry_scale[0] + geometry_scale[1]) / 2.0
                for style_name, style_list in used_styles.items():
                    for style in style_list:
                        # Modify bounds
                        align = style.Alignment
                        ml, mr, mv = self.__shiftscale_action_get_new_margins(
                            bounds,
                            geometry_scale,
                            resolution_new,
                            align,
                            None,
                            style.MarginL,
                            style.MarginR,
                            style.MarginV,
                        )
                        style.MarginL = ml
                        style.MarginR = mr
                        style.MarginV = mv

                        # Modify scale
                        style.Fontsize *= scale
                        style.Spacing *= scale
                        style.Outline *= scale
                        style.Shadow *= scale

        # Done
        return self

    def __shiftscale_action_time(
        self,
        inverse,
        split_naive,
        time_scale,
        time_scale_origin,
        time_offset,
        time_clip_start,
        time_clip_end,
        line,
    ):
        # Modify
        line.Start = (line.Start - time_scale_origin) * time_scale + time_scale_origin + time_offset
        line.End = (line.End - time_scale_origin) * time_scale + time_scale_origin + time_offset

        # Modify timed tags
        line.Text = self.parse_text(
            line.Text,
            modify_tag=(lambda tag: self.__shiftscale_action_time_modify_tag(time_scale, tag)),
        )

        # Clip
        if not inverse:
            # Keep inside
            if time_clip_start is not None:
                if line.End <= time_clip_start:
                    line = None
                else:
                    line_splits = self.__split_line(line, time_clip_start, split_naive)
                    if line_splits is not None:
                        line = line_splits[1]
            if time_clip_end is not None:
                if line.Start >= time_clip_end:
                    line = None
                else:
                    line_splits = self.__split_line(line, time_clip_end, split_naive)
                    if line_splits is not None:
                        line = line_splits[0]

        # Done
        return line

    def __shiftscale_action_time_modify_tag(self, time_scale, tag):
        tag_name = tag[0]
        if tag_name in ["k", "K", "kf", "ko"]:
            tag = list(tag)
            tag[1] = str(int(Formatters.str_to_number(tag[1]) * time_scale))
        elif tag_name == "move":
            if len(tag) == 7:
                tag = list(tag)
                tag[5] = str(int(Formatters.str_to_number(tag[5]) * time_scale))
                tag[6] = str(int(Formatters.str_to_number(tag[6]) * time_scale))
        elif tag_name == "fade":
            tag = list(tag)
            tag[4] = str(int(Formatters.str_to_number(tag[4]) * time_scale))
            tag[5] = str(int(Formatters.str_to_number(tag[5]) * time_scale))
            tag[6] = str(int(Formatters.str_to_number(tag[6]) * time_scale))
            tag[7] = str(int(Formatters.str_to_number(tag[7]) * time_scale))
        elif tag_name == "t":
            if len(tag) >= 4:
                tag = list(tag)
                tag[1] = str(int(Formatters.str_to_number(tag[1]) * time_scale))
                tag[2] = str(int(Formatters.str_to_number(tag[2]) * time_scale))

        return [tag]

    def __shiftscale_action_geometry(
        self,
        geometry_scale,
        geometry_scale_origin,
        geometry_offset,
        resolution_old,
        resolution_new,
        bounds,
        used_styles,
        line,
    ):
        # Modify geometry
        state = {
            "align": None,
        }
        line.Text = self.parse_text(
            line.Text,
            modify_tag=(
                lambda tag: self.__shiftscale_action_geometry_modify_tag(state, geometry_scale, geometry_scale_origin, geometry_offset, tag)
            ),
            modify_geometry=(
                lambda geo: self.__shiftscale_action_geometry_modify_geometry(geometry_scale, geometry_scale_origin, geometry_offset, geo)
            ),
        )

        # Modify bounds
        ml, mr, mv = self.__shiftscale_action_get_new_margins(
            bounds,
            geometry_scale,
            resolution_new,
            state["align"],
            line.Style,
            line.MarginL,
            line.MarginR,
            line.MarginV,
        )
        line.MarginL = ml
        line.MarginR = mr
        line.MarginV = mv

        # Update styles
        if line.Style.Name not in used_styles:
            used_styles[line.Style.Name] = [line.Style]
        elif line.Style not in used_styles[line.Style.Name]:
            used_styles[line.Style.Name].append(line.Style)

        # Done
        return line

    def __shiftscale_action_geometry_modify_tag(self, state, geometry_scale, geometry_scale_origin, geometry_offset, tag):
        tag_name = tag[0]
        if tag_name in ["bord", "shad", "be", "blur", "fs"]:
            scale = (geometry_scale[0] + geometry_scale[1]) / 2.0
            tag = [
                tag_name,
                Formatters.number_to_str(Formatters.str_to_number(tag[1]) * scale),
            ]
        elif tag_name in ["xbord", "xshad", "fsp"]:
            tag = [
                tag_name,
                Formatters.number_to_str(Formatters.str_to_number(tag[1]) * geometry_scale[0]),
            ]
        elif tag_name in ["ybord", "yshad"]:
            tag = [
                tag_name,
                Formatters.number_to_str(Formatters.str_to_number(tag[1]) * geometry_scale[1]),
            ]
        elif tag_name in ["pos", "org"]:
            tag = [
                tag_name,
                Formatters.number_to_str(
                    (Formatters.str_to_number(tag[1]) - geometry_scale_origin[0]) * geometry_scale[0]
                    + geometry_scale_origin[0]
                    + geometry_offset[0]
                ),
                Formatters.number_to_str(
                    (Formatters.str_to_number(tag[2]) - geometry_scale_origin[1]) * geometry_scale[1]
                    + geometry_scale_origin[1]
                    + geometry_offset[1]
                ),
            ]
        elif tag_name in ["clip", "iclip"]:
            if len(tag) == 5:
                # Rectangle
                tag = list(tag)
                for i in range(1, len(tag)):
                    xy = (i + 1) % 2
                    val = Formatters.str_to_number(tag[i])
                    val = (val - geometry_scale_origin[xy]) * geometry_scale[xy] + geometry_scale_origin[xy] + geometry_offset[xy]
                    tag[i] = Formatters.number_to_str(val)
            else:
                # Draw command
                tag = list(tag)
                tag[-1] = self.__shiftscale_action_geometry_modify_geometry(geometry_scale, geometry_scale_origin, geometry_offset, tag[-1])
        elif tag_name == "move":
            tag = list(tag)
            for i in range(1, len(tag)):
                xy = (i + 1) % 2
                val = Formatters.str_to_number(tag[i])
                val = (val - geometry_scale_origin[xy]) * geometry_scale[xy] + geometry_scale_origin[xy] + geometry_offset[xy]
                tag[i] = Formatters.number_to_str(val)
        elif tag_name == "pbo":
            tag = [
                tag_name,
                str(int(Formatters.str_to_number(tag[1]) * geometry_scale[1])),
            ]
        elif tag_name == "t":
            # Parse more tags
            tag[-1] = self.parse_tags(
                tag[-1],
                modify_tag=(
                    lambda tag2: self.__shiftscale_action_geometry_modify_tag(
                        None,
                        geometry_scale,
                        geometry_scale_origin,
                        geometry_offset,
                        tag2,
                    )
                ),
            )
        elif tag_name in ["a", "an"]:
            if tag_name == "a":
                align = self.__legacy_align_to_regular(Formatters.str_to_number(tag[1]))
            else:  # if (tag_name == "an"):
                align = Formatters.str_to_number(tag[1])

            # State update
            if state is not None and state["align"] is None:
                state["align"] = align

            # Note: Middle vertical alignment will not always be properly positioned

        return [tag]

    def __shiftscale_action_geometry_modify_geometry(self, geometry_scale, geometry_scale_origin, geometry_offset, geo):
        points = self.__re_draw_command_split.split(geo.strip())
        xy = 0
        for i in range(len(points)):
            coord = points[i]
            if len(coord) == 1:
                coord_ord = ord(coord)
                if coord_ord >= self.__re_draw_commands_ord_min and coord_ord <= self.__re_draw_commands_ord_max:
                    # New command
                    xy = 0
                    continue

            # Value
            val = Formatters.str_to_number(coord)
            val = (val - geometry_scale_origin[xy]) * geometry_scale[xy] + geometry_scale_origin[xy] + geometry_offset[xy]
            points[i] = str(int(val))

            # Next
            xy = (xy + 1) % 2

        return " ".join(points)

    def __shiftscale_action_get_new_bounds(
        self,
        geometry_scale,
        geometry_scale_origin,
        geometry_offset,
        resolution_old,
        resolution_new,
    ):
        # Modify bounds
        return (
            geometry_offset[0],
            geometry_offset[1],
            (resolution_new[0] - resolution_old[0]) * geometry_scale[0] + geometry_offset[0],
            (resolution_new[1] - resolution_old[1]) * geometry_scale[1] + geometry_offset[1],
        )

    def __shiftscale_action_get_new_margins(
        self,
        bounds,
        geometry_scale,
        resolution_new,
        align,
        style,
        margin_left,
        margin_right,
        margin_vertical,
    ):
        # Default alignments
        if style is not None:
            if align is None:
                align = style.Alignment
            elif not style.fake and align != style.Alignment:
                margin_vertical = style.MarginV

        # Modify
        if margin_left != 0:
            margin_left = margin_left * geometry_scale[0] + bounds[0]
        if margin_right != 0:
            margin_right = resolution_new[0] - (bounds[2] - margin_right * geometry_scale[0])
        if margin_vertical != 0:
            align_xy = self.get_xy_alignment(align)
            if align_xy[1] < 0:  # Top
                margin_vertical = margin_vertical * geometry_scale[1] + bounds[1]
            elif align_xy[1] > 0:  # Bottom
                margin_vertical = resolution_new[1] - (bounds[3] - margin_vertical * geometry_scale[1])
            else:  # if (align_xy[1] == 0): # Middle
                margin_vertical = margin_vertical * geometry_scale[1]

        # Return
        return (margin_left, margin_right, margin_vertical)

    def loop(self, **kwargs):  # Duplicate a timecode (range) for a certain length
        # Parse kwargs
        time = self.__kwarg_default(kwargs, "time", None)
        # timecode to loop; shortcut for both start/end
        start = self.__kwarg_default(kwargs, "start", time)
        # time to start at, or None for not bounded
        end = self.__kwarg_default(kwargs, "end", time)
        # time to start at, or None for not bounded

        filter_types = self.__kwarg_default(kwargs, "filter_types", None)
        # list of event types to include; can be anything supporting the "in" operator; None means no filtering

        length = self.__kwarg_default(kwargs, "length", None)
        # duration to loop for; if None, this is ignored
        count = self.__kwarg_default(kwargs, "count", None)
        # number of times to loop the extracted section; if start==end, or if None, this is ignored

        # Exceptions
        if start is None and end is None:
            raise ValueError("start, end, or time must be specified")

        if start is None:
            start = self.__get_minimum_timecode()
            start = min(start, end)
        elif end is None:
            end = self.__get_maximum_timecode()
            end = max(end, start)

        if start > end:
            raise ValueError("start cannot be greater than end")

        if count is None and length is None:
            raise ValueError("count and length cannot both be None")

        if count is not None and count <= 0:
            raise ValueError("count cannot be 0 or negative")

        if length is not None and length <= 0:
            raise ValueError("length cannot be 0 or negative")

        # Cut parts
        temp = self.__class__()
        if start == end:
            i = 0
            i_max = len(self.events)
            while i < i_max:
                line = self.events[i]
                if filter_types is None or line.type in filter_types:
                    # Attempt to split
                    line_parts = self.__split_line3(line, start)
                    if line_parts is not None:
                        l_before, l_middle, l_after = line_parts

                        # Add to temp
                        l_middle.End = l_middle.Start + length
                        # Stretch
                        temp.add(l_middle)

                        # Replace old
                        if l_before is not None:
                            self.events[i] = l_before
                            if l_after is not None:
                                self.events.append(l_after)
                        elif l_after is not None:
                            self.events[i] = l_after
                        else:
                            self.events.pop(i)
                            i_max -= 1
                            continue
                            # Same as doing i -= 1, since something was removed and not replaced

                # Next
                i += 1

            # Modify count
            count = 1
            # Loop it exactly once
            length_single = length
        else:
            # Cut out a range
            self.extract(
                start=start,
                end=end,
                split=True,
                split_naive=False,
                full_inclusion=False,
                remove=True,
                other=temp,
                filter_types=filter_types,
            )

            # Modify count
            if length is None:
                # Update length
                length = (end - start) * count
            elif count is None:
                # Update count
                count = length / float(end - start)
            else:
                # Stretch
                scale = length / float((end - start) * count)
                temp.shiftscale(time_scale=scale, time_scale_origin=start)

            length_single = length / float(count)

        # Modify Start/End of all lines AFTER "end"
        length -= end - start
        # account for the self.extract call
        for line in self.events:
            if (filter_types is None or line.type in filter_types) and line.Start >= end:
                line.Start += length
                line.End += length

        # Merge temp
        time_offset = 0.0
        while count >= 1:
            # Merge
            self.merge(other=temp, remove=False, filter_types=None, time_shift=time_offset)

            # Shift for next
            time_offset += length_single
            count -= 1
        if count > 0:
            # Cut
            temp.extract(start=start, end=start + length_single * count, inverse=True)
            # Add
            self.merge(other=temp, remove=True, filter_types=None, time_shift=time_offset)

        # Done
        return self

    def extract(self, **kwargs):  # Copy/remove lines, possibly into another object
        # Parse kwargs
        start = self.__kwarg_default(kwargs, "start", None)
        # time to start at, or None for not bounded
        end = self.__kwarg_default(kwargs, "end", None)
        # time to start at, or None for not bounded

        full_inclusion = self.__kwarg_default(kwargs, "full_inclusion", False)
        # if True, line timecodes must be fully included within the specified range
        inverse = self.__kwarg_default(kwargs, "inverse", False)
        # if True, operation is performed on all lines not included in the timecode range
        split = self.__kwarg_default(kwargs, "split", False)
        # if True, splits lines if they are not fully in the timecode range
        split_naive = self.__kwarg_default(kwargs, "split_naive", False)
        # if True, line splitting will not modify any formatting tags

        filter_types = self.__kwarg_default(kwargs, "filter_types", None)
        # list of event types to include; can be anything supporting the "in" operator; None means no filtering

        filter_function = self.__kwarg_default(kwargs, "filter_function", None)
        # custom function to filter lines: takes 1 argument (line) and should return True if it's kept, or False to remove

        remove = self.__kwarg_default(kwargs, "remove", True)
        # if True, lines are removed from self
        other = self.__kwarg_default(kwargs, "other", None)
        # it not None, removes lines the other specified ASS instance

        # Exceptions
        if start is not None and end is not None and start > end:
            raise ValueError("start cannot be greater than end")

        # Split
        if split:
            self.__range_cut(filter_types, start, end, split_naive)

        # Modify lines
        self.__range_action(
            filter_types,
            start,
            end,
            full_inclusion,
            inverse,
            (lambda line: self.__extract_action(other, remove, filter_function, line)),
        )

        # Done
        return self

    def __extract_action(self, other, remove, filter_function, line):
        if filter_function is None or filter_function(line):
            if remove:
                if other is not None:
                    other.add(line)
                return None
                # removed
            elif other is not None:
                other.add(line.copy())

        return line

    def merge(self, **kwargs):  # Merge with another subtitle object
        # Parse kwargs
        remove = self.__kwarg_default(kwargs, "remove", False)
        # if True, lines are removed from other
        filter_types = self.__kwarg_default(kwargs, "filter_types", None)
        # list of event types to include; can be anything supporting the "in" operator; None means no filtering
        other = self.__kwarg_default(kwargs, "other", None)
        # adds lines to THIS object from OTHER
        time_offset = self.__kwarg_default(kwargs, "time_offset", 0.0)
        # amount to offset line timings from other by

        if other is None:
            raise ValueError("other cannot be None")

        # Add
        i = 0
        i_max = len(other.events)
        while i < i_max:
            line = other.events[i]
            if filter_types is None or line.type in filter_types:
                # Add to self
                if remove:
                    other.events.pop(i)
                else:
                    line = line.copy()
                line.Start += time_offset
                line.End += time_offset
                self.add(line)

                # Remove
                if remove:
                    i_max -= 1
                    continue
                    # Same as doing i -= 1, since something was removed

            # Next
            i += 1

        # Done
        return self

    def remove_formatting(self, **kwargs):  # Remove special formatting from lines
        # Parse kwargs
        start = self.__kwarg_default(kwargs, "start", None)
        # time to start at, or None for not bounded
        end = self.__kwarg_default(kwargs, "end", None)
        # time to start at, or None for not bounded

        full_inclusion = self.__kwarg_default(kwargs, "full_inclusion", False)
        # if True, line timecodes must be fully included within the specified range
        inverse = self.__kwarg_default(kwargs, "inverse", False)
        # if True, operation is performed on all lines not included in the timecode range
        split = self.__kwarg_default(kwargs, "split", False)
        # if True, splits lines if they are not fully in the timecode range
        split_naive = self.__kwarg_default(kwargs, "split_naive", False)
        # if True, line splitting will not modify any formatting tags

        filter_types = self.__kwarg_default(kwargs, "filter_types", None)
        # list of event types to include; can be anything supporting the "in" operator; None means no filtering

        remove_tags = self.__kwarg_default(kwargs, "tags", True)
        # True to remove
        remove_comments = self.__kwarg_default(kwargs, "comments", True)
        # True to remove
        remove_geometry = self.__kwarg_default(kwargs, "geometry", True)
        # True to remove
        remove_special = self.__kwarg_default(kwargs, "special", False)
        # True to remove

        # Exceptions
        if start is not None and end is not None and start > end:
            raise ValueError("start cannot be greater than end")

        # More setup
        modify_text = None
        modify_tag_block = None
        modify_tag = None
        modify_comment = None
        modify_geometry = None

        if remove_tags and remove_comments and remove_geometry:
            # Faster version
            modify_tag_block = lambda b: ""
            modify_geometry = lambda g: ""
        else:
            # Generic version
            modify_tag_block = lambda b: ("" if (len(b) == 2) else b)
            if remove_comments:
                modify_comment = lambda c: ""
            if remove_geometry:
                modify_geometry = lambda g: ""
                modify_tag = lambda t: []
            else:
                modify_tag = lambda t: ([t] if (t[0] == "p") else [])

        if remove_special:
            modify_text = lambda t: self.replace_special(t)

        # Split
        if split:
            self.__range_cut(filter_types, start, end, split_naive)

        # Modify lines
        self.__range_action(
            filter_types,
            start,
            end,
            full_inclusion,
            inverse,
            (
                lambda line: self.__remove_formatting_action(
                    modify_text,
                    modify_tag_block,
                    modify_tag,
                    modify_comment,
                    modify_geometry,
                    line,
                )
            ),
        )

        # Done
        return self

    def __remove_formatting_action(
        self,
        modify_text,
        modify_tag_block,
        modify_tag,
        modify_comment,
        modify_geometry,
        line,
    ):
        line.Text = self.parse_text(
            line.Text,
            modify_text=modify_text,
            modify_tag_block=modify_tag_block,
            modify_tag=modify_tag,
            modify_comment=modify_comment,
            modify_geometry=modify_geometry,
        )

        return line
