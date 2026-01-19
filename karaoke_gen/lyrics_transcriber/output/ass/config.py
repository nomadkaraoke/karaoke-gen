from dataclasses import dataclass


class ScreenConfig:
    """Configuration for screen timing and layout.
    
    Lead-in Indicator Configuration:
        lead_in_enabled: bool - Enable/disable the lead-in indicator entirely (default: True)
        lead_in_width_percent: float - Width as percentage of screen width (default: 3.5)
        lead_in_height_percent: float - Height as percentage of screen height (default: 4.0)
        lead_in_opacity_percent: float - Opacity percentage, 0-100 (default: 70.0)
        lead_in_outline_thickness: int - Outline thickness in pixels, 0 for no outline (default: 0)
        lead_in_outline_color: str - Outline color in RGB format "R, G, B" (default: "0, 0, 0")
        lead_in_gap_threshold: float - Minimum gap in seconds to show lead-in (default: 5.0)
        lead_in_color: str - Fill color in RGB format "R, G, B" (default: "112, 112, 247")
        lead_in_horiz_offset_percent: float - Horizontal offset as percentage of screen width, can be negative (default: 0.0)
        lead_in_vert_offset_percent: float - Vertical offset as percentage of screen height, can be negative (default: 0.0)
        
    Example JSON configuration:
        {
          "karaoke": {
            "lead_in_enabled": true,
            "lead_in_width_percent": 4.0,
            "lead_in_height_percent": 5.0,
            "lead_in_opacity_percent": 80,
            "lead_in_outline_thickness": 2,
            "lead_in_outline_color": "255, 255, 255",
            "lead_in_gap_threshold": 3.0,
            "lead_in_color": "230, 139, 33",
            "lead_in_horiz_offset_percent": -2.0,
            "lead_in_vert_offset_percent": 1.0
          }
        }
    """

    def __init__(
        self,
        line_height: int = 50,
        max_visible_lines: int = 4,
        top_padding: int = None,
        video_width: int = 640,
        video_height: int = 360,
        screen_gap_threshold: float = 5.0,
        post_roll_time: float = 1.0,
        fade_in_ms: int = 200,
        fade_out_ms: int = 300,
        lead_in_color: str = "112, 112, 247",  # Default blue color in RGB format
        text_case_transform: str = "none",  # Options: "none", "uppercase", "lowercase", "propercase"
        # New lead-in indicator configuration options
        lead_in_enabled: bool = True,
        lead_in_width_percent: float = 3.5,
        lead_in_height_percent: float = 4.0,
        lead_in_opacity_percent: float = 70.0,
        lead_in_outline_thickness: int = 0,
        lead_in_outline_color: str = "0, 0, 0",
        lead_in_gap_threshold: float = 5.0,
        lead_in_horiz_offset_percent: float = 0.0,
        lead_in_vert_offset_percent: float = 0.0,
    ):
        # Screen layout
        self.max_visible_lines = max_visible_lines
        self.line_height = line_height
        self.top_padding = top_padding if top_padding is not None else line_height
        self.video_height = video_height
        self.video_width = video_width
        # Timing configuration
        self.screen_gap_threshold = screen_gap_threshold
        self.post_roll_time = post_roll_time
        self.fade_in_ms = fade_in_ms
        self.fade_out_ms = fade_out_ms
        # Lead-in configuration
        self.lead_in_color = lead_in_color
        self.lead_in_enabled = lead_in_enabled
        self.lead_in_width_percent = lead_in_width_percent
        self.lead_in_height_percent = lead_in_height_percent
        self.lead_in_opacity_percent = lead_in_opacity_percent
        self.lead_in_outline_thickness = lead_in_outline_thickness
        self.lead_in_outline_color = lead_in_outline_color
        self.lead_in_gap_threshold = lead_in_gap_threshold
        self.lead_in_horiz_offset_percent = lead_in_horiz_offset_percent
        self.lead_in_vert_offset_percent = lead_in_vert_offset_percent
        # Text formatting configuration
        self.text_case_transform = text_case_transform

    def get_lead_in_color_ass_format(self) -> str:
        """Convert RGB lead-in color to ASS format.
        
        Accepts either:
        - RGB format: "112, 112, 247" 
        - ASS format: "&HF77070&" (for backward compatibility)
        
        Returns ASS format color string.
        """
        color_str = self.lead_in_color.strip()
        
        # If already in ASS format, return as-is
        if color_str.startswith("&H") and color_str.endswith("&"):
            return color_str
            
        # Parse RGB format "R, G, B" or "R, G, B, A"
        try:
            parts = [int(x.strip()) for x in color_str.split(",")]
            if len(parts) == 3:
                r, g, b = parts
                a = 255  # Default full opacity
            elif len(parts) == 4:
                r, g, b, a = parts
            else:
                raise ValueError(f"Invalid color format: {color_str}")
                
            # Convert to ASS format: &H{alpha}{blue}{green}{red}&
            # Note: alpha is inverted in ASS (255-a)
            return f"&H{255-a:02X}{b:02X}{g:02X}{r:02X}&"
            
        except (ValueError, TypeError) as e:
            # Fallback to default blue if parsing fails
            return "&HF77070&"

    def get_lead_in_outline_color_ass_format(self) -> str:
        """Convert RGB lead-in outline color to ASS format.
        
        Accepts either:
        - RGB format: "0, 0, 0" 
        - ASS format: "&H000000&" (for backward compatibility)
        
        Returns ASS format color string.
        """
        color_str = self.lead_in_outline_color.strip()
        
        # If already in ASS format, return as-is
        if color_str.startswith("&H") and color_str.endswith("&"):
            return color_str
            
        # Parse RGB format "R, G, B" or "R, G, B, A"
        try:
            parts = [int(x.strip()) for x in color_str.split(",")]
            if len(parts) == 3:
                r, g, b = parts
                a = 255  # Default full opacity
            elif len(parts) == 4:
                r, g, b, a = parts
            else:
                raise ValueError(f"Invalid color format: {color_str}")
                
            # Convert to ASS format: &H{alpha}{blue}{green}{red}&
            # Note: alpha is inverted in ASS (255-a)
            return f"&H{255-a:02X}{b:02X}{g:02X}{r:02X}&"
            
        except (ValueError, TypeError) as e:
            # Fallback to default black if parsing fails
            return "&H000000&"
    
    def get_lead_in_opacity_ass_format(self) -> str:
        """Convert opacity percentage to ASS alpha format.
        
        Returns ASS alpha value (e.g., &H4D& for 70% opacity).
        """
        # ASS alpha is inverted: 0=opaque, 255=transparent
        # Convert percentage to alpha value
        alpha = int((100 - self.lead_in_opacity_percent) / 100 * 255)
        return f"&H{alpha:02X}&"


@dataclass
class LineTimingInfo:
    """Timing information for a single line."""

    fade_in_time: float
    end_time: float
    fade_out_time: float
    clear_time: float


@dataclass
class LineState:
    """Complete state for a single line."""

    text: str
    timing: LineTimingInfo
    y_position: int
