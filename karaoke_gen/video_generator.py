import os
import logging
import importlib.resources as pkg_resources
import shutil
import subprocess
from PIL import Image, ImageDraw, ImageFont


def _find_cjk_font() -> str | None:
    """Find a system CJK font using fontconfig (fc-match).

    fc-match :lang=zh can return a non-CJK font (e.g. NotoSans-Regular.ttf)
    that claims Chinese support but lacks CJK glyphs. We explicitly request
    "Noto Sans CJK" and fall back to known filesystem paths.
    """
    # Try fc-match with explicit CJK font family name
    for query in ["Noto Sans CJK", "Noto Sans CJK SC"]:
        try:
            result = subprocess.run(
                ["fc-match", "--format=%{file}", query],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout and os.path.exists(result.stdout):
                # Verify the result is actually a CJK font (not a fallback to a Latin font)
                if "CJK" in result.stdout or "cjk" in result.stdout.lower():
                    return result.stdout
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
    # Fallback: known paths for Noto Sans CJK (fonts-noto-cjk package on Debian/Ubuntu)
    for path in [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]:
        if os.path.exists(path):
            return path
    return None


def _text_needs_cjk_font(text: str) -> bool:
    """Check if text contains CJK characters that need a CJK-capable font."""
    if not text:
        return False
    for ch in text:
        cp = ord(ch)
        # CJK Unified Ideographs, CJK Extension A/B, CJK Compatibility
        # Hangul Syllables, Katakana, Hiragana
        if (0x4E00 <= cp <= 0x9FFF or   # CJK Unified Ideographs
            0x3400 <= cp <= 0x4DBF or   # CJK Extension A
            0x20000 <= cp <= 0x2A6DF or # CJK Extension B
            0xAC00 <= cp <= 0xD7AF or   # Hangul Syllables
            0x3040 <= cp <= 0x309F or   # Hiragana
            0x30A0 <= cp <= 0x30FF or   # Katakana
            0xF900 <= cp <= 0xFAFF):    # CJK Compatibility Ideographs
            return True
    return False


# Placeholder class or functions for video/image generation
class VideoGenerator:
    def __init__(self, logger, ffmpeg_base_command, render_bounding_boxes, output_png, output_jpg):
        self.logger = logger
        self.ffmpeg_base_command = ffmpeg_base_command
        self.render_bounding_boxes = render_bounding_boxes
        self.output_png = output_png
        self.output_jpg = output_jpg
        self._cjk_font_path = None  # Lazy-loaded

    def parse_region(self, region_str):
        if region_str:
            try:
                parts = region_str.split(",")
                if len(parts) != 4:
                    raise ValueError(f"Invalid region format: {region_str}. Expected 4 elements: 'x,y,width,height'")
                return tuple(map(int, parts))
            except ValueError as e:
                # Re-raise specific format errors or general ValueError for int conversion issues
                if "Expected 4 elements" in str(e):
                    raise e
                raise ValueError(f"Invalid region format: {region_str}. Could not convert to integers. Expected format: 'x,y,width,height'") from e
        return None

    def hex_to_rgb(self, hex_color):
        """Convert hex color to RGB tuple."""
        hex_color = hex_color.lstrip("#")
        return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))

    # Placeholder methods - to be filled by user moving code
    def create_video(
        self,
        extra_text,
        title_text,
        artist_text,
        format,
        output_image_filepath_noext,
        output_video_filepath,
        existing_image=None,
        duration=5,
    ):
        """Create a video with title, artist, and optional extra text."""
        self.logger.debug(f"Creating video with extra_text: '{extra_text}'")
        self.logger.debug(f"Format settings: {format}")

        resolution = (3840, 2160)  # 4K resolution
        self.logger.info(f"Creating video with format: {format}")
        self.logger.info(f"extra_text: {extra_text}, artist_text: {artist_text}, title_text: {title_text}")

        if existing_image:
            return self._handle_existing_image(existing_image, output_image_filepath_noext, output_video_filepath, duration)

        # Create or load background
        background = self._create_background(format, resolution)
        draw = ImageDraw.Draw(background)

        if format["font"] is not None:
            self.logger.info(f"Using font: {format['font']}")
            # Check if the font path is absolute
            if os.path.isabs(format["font"]):
                font_path = format["font"]
                if not os.path.exists(font_path):
                    self.logger.warning(f"Font file not found at {font_path}, falling back to default font")
                    font_path = None
            else:
                # Try to load from package resources
                try:
                    with pkg_resources.path("karaoke_gen.resources", format["font"]) as font_path:
                        font_path = str(font_path)
                except Exception as e:
                    self.logger.warning(f"Could not load font from resources: {e}, falling back to default font")
                    font_path = None

            # Render all text elements
            self._render_all_text(
                draw,
                font_path,
                title_text,
                artist_text,
                format,
                self.render_bounding_boxes,
            )
        else:
            self.logger.info("No font specified, skipping text rendering")

        # Save images and create video
        self._save_output_files(
            background, output_image_filepath_noext, output_video_filepath, duration, resolution
        )

    def calculate_text_size_to_fit(self, draw, text, font_path, region):
        font_size = 500  # Start with a large font size
        font = ImageFont.truetype(font_path, size=font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()

        def get_text_size(text, font):
            bbox = draw.textbbox((0, 0), text, font=font)
            # Use the actual text height without the font's internal padding
            return bbox[2], bbox[3] - bbox[1]

        text_width, text_height = get_text_size(text, font)
        target_height = region[3]  # Use full region height as target

        while text_width > region[2] or text_height > target_height:
            font_size -= 10
            if font_size <= 150:
                # Split the text into two lines
                words = text.split()
                mid = len(words) // 2
                line1 = " ".join(words[:mid])
                line2 = " ".join(words[mid:])

                # Reset font size for two-line layout
                font_size = 500
                font = ImageFont.truetype(font_path, size=font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()

                while True:
                    text_width1, text_height1 = get_text_size(line1, font)
                    text_width2, text_height2 = get_text_size(line2, font)
                    total_height = text_height1 + text_height2

                    # Add a small gap between lines (10% of line height)
                    line_gap = text_height1 * 0.1
                    total_height_with_gap = total_height + line_gap

                    if max(text_width1, text_width2) <= region[2] and total_height_with_gap <= target_height:
                        return font, (line1, line2)

                    font_size -= 10
                    if font_size <= 0:
                        raise ValueError("Cannot fit text within the defined region.")
                    font = ImageFont.truetype(font_path, size=font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()

            font = ImageFont.truetype(font_path, size=font_size) if font_path and os.path.exists(font_path) else ImageFont.load_default()
            text_width, text_height = get_text_size(text, font)

        return font, text

    def _render_text_in_region(self, draw, text, font_path, region, color, gradient=None, font=None):
        """Helper method to render text within a specified region."""
        self.logger.debug(f"Rendering text: '{text}' in region: {region} with color: {color} gradient: {gradient}")

        if text is None:
            self.logger.debug("Text is None, skipping rendering")
            return region

        if region is None:
            self.logger.debug("Region is None, skipping rendering")
            return region

        if font is None:
            font, text_lines = self.calculate_text_size_to_fit(draw, text, font_path, region)
        else:
            text_lines = text

        self.logger.debug(f"Using text_lines: {text_lines}")

        x, y, width, height = region

        # Get font metrics
        ascent, descent = font.getmetrics()
        font_height = ascent + descent

        def render_text_with_gradient(text, position, bbox):
            # Convert position coordinates to integers
            position = (int(position[0]), int(position[1]))

            if gradient is None:
                draw.text(position, text, fill=color, font=font)
            else:
                # Create a temporary image for this text
                text_layer = Image.new("RGBA", (bbox[2], bbox[3]), (0, 0, 0, 0))
                text_draw = ImageDraw.Draw(text_layer)

                # Draw text in first color
                text_draw.text((0, 0), text, fill=gradient["color1"], font=font)

                # Create and apply gradient mask
                mask = self._create_gradient_mask((bbox[2], bbox[3]), gradient)

                # Create second color layer
                color2_layer = Image.new("RGBA", (bbox[2], bbox[3]), (0, 0, 0, 0))
                color2_draw = ImageDraw.Draw(color2_layer)
                color2_draw.text((0, 0), text, fill=gradient["color2"], font=font)

                # Composite using gradient mask
                text_layer.paste(color2_layer, mask=mask)

                # Paste onto main image
                draw._image.paste(text_layer, position, text_layer)

        if isinstance(text_lines, tuple):  # Two lines
            line1, line2 = text_lines
            bbox1 = draw.textbbox((0, 0), line1, font=font)
            bbox2 = draw.textbbox((0, 0), line2, font=font)

            # Calculate line heights using bounding boxes
            line1_height = bbox1[3] - bbox1[1]
            line2_height = bbox2[3] - bbox2[1]

            # Use a small gap between lines (20% of average line height)
            line_gap = int((line1_height + line2_height) * 0.1)

            # Calculate total height needed
            total_height = line1_height + line_gap + line2_height

            # Center the entire text block vertically in the region
            y_start = y + (height - total_height) // 2

            # Draw first line
            pos1 = (x + (width - bbox1[2]) // 2, y_start)
            render_text_with_gradient(line1, pos1, bbox1)

            # Draw second line
            pos2 = (x + (width - bbox2[2]) // 2, y_start + line1_height + line_gap)
            render_text_with_gradient(line2, pos2, bbox2)
        else:
            # Single line
            bbox = draw.textbbox((0, 0), text_lines, font=font)

            # Center text vertically using font metrics
            y_pos = y + (height - font_height) // 2

            position = (x + (width - bbox[2]) // 2, y_pos)
            render_text_with_gradient(text_lines, position, bbox)

        return region

    def _draw_bounding_box(self, draw, region, color):
        """Helper method to draw a bounding box around a region."""
        if region is None:
            self.logger.debug("Region is None, skipping drawing bounding box")
            return

        x, y, width, height = region
        draw.rectangle([x, y, x + width, y + height], outline=color, width=2)

    def _create_gradient_mask(self, size, gradient_config):
        """Create a gradient mask for text coloring.

        Args:
            size (tuple): (width, height) of the mask
            gradient_config (dict): Configuration with keys:
                - color1: First color (hex)
                - color2: Second color (hex)
                - direction: 'horizontal' or 'vertical'
                - start: Start point of gradient transition (0-1)
                - stop: Stop point of gradient transition (0-1)
        """
        mask = Image.new("L", size)
        draw = ImageDraw.Draw(mask)

        width, height = size
        start = gradient_config["start"]
        stop = gradient_config["stop"]

        if gradient_config["direction"] == "horizontal":
            for x in range(width):
                # Calculate position in gradient (0 to 1)
                pos = x / width

                # Calculate color intensity
                if pos < start:
                    intensity = 0
                elif pos > stop:
                    intensity = 255
                else:
                    # Linear interpolation between start and stop
                    intensity = int(255 * (pos - start) / (stop - start))

                draw.line([(x, 0), (x, height)], fill=intensity)
        else:  # vertical
            for y in range(height):
                pos = y / height
                if pos < start:
                    intensity = 0
                elif pos > stop:
                    intensity = 255
                else:
                    intensity = int(255 * (pos - start) / (stop - start))

                draw.line([(0, y), (width, y)], fill=intensity)

        return mask

    def _handle_existing_image(self, existing_image, output_image_filepath_noext, output_video_filepath, duration):
        """Handle case where an existing image is provided."""
        self.logger.info(f"Using existing image file: {existing_image}")
        existing_extension = os.path.splitext(existing_image)[1]

        if existing_extension == ".png":
            self.logger.info(f"Copying existing PNG image file: {existing_image}")
            shutil.copy2(existing_image, output_image_filepath_noext + existing_extension)
        else:
            self.logger.info(f"Converting existing image to PNG")
            existing_image_obj = Image.open(existing_image)
            existing_image_obj.save(output_image_filepath_noext + ".png")

        if existing_extension != ".jpg":
            self.logger.info(f"Converting existing image to JPG")
            existing_image_obj = Image.open(existing_image)
            if existing_image_obj.mode == "RGBA":
                existing_image_obj = existing_image_obj.convert("RGB")
            existing_image_obj.save(output_image_filepath_noext + ".jpg", quality=95)

        if duration > 0:
            self._create_video_from_image(output_image_filepath_noext + ".png", output_video_filepath, duration)

    def _create_background(self, format, resolution):
        """Create or load the background image."""
        if format["background_image"] and os.path.exists(format["background_image"]):
            self.logger.info(f"Using background image file: {format['background_image']}")
            background = Image.open(format["background_image"])
        else:
            self.logger.info(f"Using background color: {format['background_color']}")
            background = Image.new("RGB", resolution, color=self.hex_to_rgb(format["background_color"]))

        return background.resize(resolution)

    def _get_font_path_for_text(self, font_path, text):
        """Return an appropriate font path for the given text.

        If the text contains CJK characters and the current font is a bundled
        Latin-only font, switch to a system CJK font so glyphs render correctly.
        """
        if not text or not _text_needs_cjk_font(text):
            return font_path

        # If font_path is already a CJK-capable system font, keep it
        if font_path and ("noto" in font_path.lower() or "cjk" in font_path.lower()):
            return font_path

        # Lazy-load CJK font path
        if self._cjk_font_path is None:
            self._cjk_font_path = _find_cjk_font() or ""
            if self._cjk_font_path:
                self.logger.info(f"Found CJK fallback font: {self._cjk_font_path}")
            else:
                self.logger.warning("No CJK font found on system - CJK characters may not render correctly")

        if self._cjk_font_path:
            self.logger.info(f"Using CJK font for text with CJK characters: {text[:30]}...")
            return self._cjk_font_path
        return font_path

    def _render_all_text(self, draw, font_path, title_text, artist_text, format, render_bounding_boxes):
        """Render all text elements on the image."""
        # Render title
        if format["title_region"]:
            title_font_path = self._get_font_path_for_text(font_path, title_text)
            region_parsed = self.parse_region(format["title_region"])
            region = self._render_text_in_region(
                draw, title_text, title_font_path, region_parsed, format["title_color"], gradient=format.get("title_gradient")
            )
            if render_bounding_boxes:
                self._draw_bounding_box(draw, region, format["title_color"])

        # Render artist
        if format["artist_region"]:
            artist_font_path = self._get_font_path_for_text(font_path, artist_text)
            region_parsed = self.parse_region(format["artist_region"])
            region = self._render_text_in_region(
                draw, artist_text, artist_font_path, region_parsed, format["artist_color"], gradient=format.get("artist_gradient")
            )
            if render_bounding_boxes:
                self._draw_bounding_box(draw, region, format["artist_color"])

        # Render extra text if provided
        if format["extra_text"]:
            extra_font_path = self._get_font_path_for_text(font_path, format["extra_text"])
            region_parsed = self.parse_region(format["extra_text_region"])
            region = self._render_text_in_region(
                draw, format["extra_text"], extra_font_path, region_parsed, format["extra_text_color"], gradient=format.get("extra_text_gradient")
            )
            if render_bounding_boxes:
                self._draw_bounding_box(draw, region, format["extra_text_color"])

    def _save_output_files(
        self, background, output_image_filepath_noext, output_video_filepath, duration, resolution
    ):
        """Save the output image files and create video if needed."""
        # Save static background image
        if self.output_png:
            background.save(f"{output_image_filepath_noext}.png")

        if self.output_jpg:
            # Save static background image as JPG for smaller filesize
            background_rgb = background.convert("RGB")
            background_rgb.save(f"{output_image_filepath_noext}.jpg", quality=95)

        if duration > 0:
            self._create_video_from_image(f"{output_image_filepath_noext}.png", output_video_filepath, duration, resolution)

    def _create_video_from_image(self, image_path, video_path, duration, resolution=(3840, 2160)):
        """Create a video from a static image."""
        ffmpeg_command = (
            f'{self.ffmpeg_base_command} -y -loop 1 -framerate 30 -i "{image_path}" '
            f"-f lavfi -i anullsrc -c:v libx264 -r 30 -t {duration} -pix_fmt yuv420p "
            f'-vf scale={resolution[0]}:{resolution[1]} -c:a aac -shortest "{video_path}"'
        )

        self.logger.info("Generating video...")
        self.logger.debug(f"Running command: {ffmpeg_command}")
        os.system(ffmpeg_command)

    def _transform_text(self, text, transform_type):
        """Helper method to transform text based on specified type."""
        if text is None:
            return None # Return None if input is None
        if transform_type == "uppercase":
            return text.upper()
        elif transform_type == "lowercase":
            return text.lower()
        elif transform_type == "propercase":
            return text.title()
        return text  # "none" or any other value returns original text

    def create_title_video(
        self, artist, title, format, output_image_filepath_noext, output_video_filepath, existing_title_image, intro_video_duration
    ):
        title_text = self._transform_text(title, format["title_text_transform"])
        artist_text = self._transform_text(artist, format["artist_text_transform"])
        self.create_video(
            title_text=title_text,
            artist_text=artist_text,
            extra_text=format["extra_text"],
            format=format,
            output_image_filepath_noext=output_image_filepath_noext,
            output_video_filepath=output_video_filepath,
            existing_image=existing_title_image,
            duration=intro_video_duration,
        )

    def create_end_video(
        self, artist, title, format, output_image_filepath_noext, output_video_filepath, existing_end_image, end_video_duration
    ):
        title_text = self._transform_text(title, format["title_text_transform"])
        artist_text = self._transform_text(artist, format["artist_text_transform"])
        self.create_video(
            title_text=title_text,
            artist_text=artist_text,
            extra_text=format["extra_text"],
            format=format,
            output_image_filepath_noext=output_image_filepath_noext,
            output_video_filepath=output_video_filepath,
            existing_image=existing_end_image,
            duration=end_video_duration,
        )
