"""
Unified style loading and configuration module.

This module provides a single source of truth for:
- Default style configurations (intro, karaoke, end, cdg)
- Asset key mappings (GCS asset keys -> style JSON paths)
- Style loading from local files
- Style loading from GCS with asset path updates

Used by both the local CLI (karaoke-gen) and the cloud backend workers.
"""
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


logger = logging.getLogger(__name__)


# =============================================================================
# DEFAULT STYLE CONFIGURATIONS
# =============================================================================

DEFAULT_INTRO_STYLE = {
    "video_duration": 5,
    "existing_image": None,
    "background_color": "#000000",
    "background_image": None,
    "font": "Montserrat-Bold.ttf",
    "artist_color": "#ffdf6b",
    "artist_gradient": None,
    "artist_text_transform": None,
    "title_color": "#ffffff",
    "title_gradient": None,
    "title_text_transform": None,
    "title_region": "370, 200, 3100, 480",
    "artist_region": "370, 700, 3100, 480",
    "extra_text": None,
    "extra_text_color": "#ffffff",
    "extra_text_gradient": None,
    "extra_text_region": "370, 1200, 3100, 480",
    "extra_text_text_transform": None,
}

DEFAULT_END_STYLE = {
    "video_duration": 5,
    "existing_image": None,
    "background_color": "#000000",
    "background_image": None,
    "font": "Montserrat-Bold.ttf",
    "artist_color": "#ffdf6b",
    "artist_gradient": None,
    "artist_text_transform": None,
    "title_color": "#ffffff",
    "title_gradient": None,
    "title_text_transform": None,
    "title_region": None,
    "artist_region": None,
    "extra_text": "THANK YOU FOR SINGING!",
    "extra_text_color": "#ff7acc",
    "extra_text_gradient": None,
    "extra_text_region": None,
    "extra_text_text_transform": None,
}

DEFAULT_KARAOKE_STYLE = {
    # Video background
    "background_color": "#000000",
    "background_image": None,
    # Font settings
    # Using "Noto Sans" for full Unicode support (CJK, Arabic, symbols, etc.)
    # Requires fonts-noto-* packages installed in Docker image
    "font": "Noto Sans",
    "font_path": "",  # Must be string, not None (for ASS generator)
    "ass_name": "Default",
    # Colors in "R, G, B, A" format (required by ASS)
    "primary_color": "112, 112, 247, 255",
    "secondary_color": "255, 255, 255, 255",
    "outline_color": "26, 58, 235, 255",
    "back_color": "0, 0, 0, 0",
    # Boolean style options
    "bold": False,
    "italic": False,
    "underline": False,
    "strike_out": False,
    # Numeric style options (all required for ASS)
    "scale_x": 100,
    "scale_y": 100,
    "spacing": 0,
    "angle": 0.0,
    "border_style": 1,
    "outline": 1,
    "shadow": 0,
    "margin_l": 0,
    "margin_r": 0,
    "margin_v": 0,
    "encoding": 0,
    # Layout settings
    "max_line_length": 40,
    "top_padding": 200,
    "font_size": 250,
    # NEW: optional per-singer color overrides. Keys: "1" / "2" / "both".
    # Missing singer or missing field falls back to the flat colors above.
    # Key note: "both" corresponds to SingerId 0.
    #
    # Duet color model: UNHIGHLIGHTED text shows the singer's signature colour
    # (blue / pink / yellow) so viewers can read ahead and see who's about to
    # sing. As each word is sung, the karaoke sweep fills it with a near-white
    # tint of the same hue, signalling "this has been sung" while subtly
    # preserving the singer identity. Outline stays dark-hue for contrast.
    "singers": {
        # Singer 1: blue pre-sung → subtle blue-tinted near-white when sung
        "1": {
            "primary_color":   "230, 230, 255, 255",
            "secondary_color": "112, 112, 247, 255",
            "outline_color":   "26, 58, 235, 255",
            "back_color":      "0, 0, 0, 0",
        },
        # Singer 2: pink pre-sung → subtle pink-tinted near-white when sung
        "2": {
            "primary_color":   "255, 235, 245, 255",
            "secondary_color": "247, 112, 180, 255",
            "outline_color":   "158, 26, 96, 255",
            "back_color":      "0, 0, 0, 0",
        },
        # Both: yellow pre-sung → subtle yellow-tinted near-white when sung
        "both": {
            "primary_color":   "255, 252, 230, 255",
            "secondary_color": "252, 211, 77, 255",
            "outline_color":   "146, 108, 0, 255",
            "back_color":      "0, 0, 0, 0",
        },
    },
}

DEFAULT_CDG_STYLE = {
    "font_path": None,
    "instrumental_background": None,
    "title_screen_background": None,
    "outro_background": None,
}

# Combined defaults for convenience
DEFAULT_STYLE_PARAMS = {
    "intro": DEFAULT_INTRO_STYLE.copy(),
    "end": DEFAULT_END_STYLE.copy(),
    "karaoke": DEFAULT_KARAOKE_STYLE.copy(),
    "cdg": DEFAULT_CDG_STYLE.copy(),
}


# =============================================================================
# SINGER COLOR RESOLUTION (for multi-singer / duet rendering)
# =============================================================================

# Map an internal SingerId (0, 1, 2) to the theme's singers-block key.
_SINGER_KEY_MAP = {0: "both", 1: "1", 2: "2"}

_SINGER_COLOR_FIELDS = ("primary_color", "secondary_color", "outline_color", "back_color")


def resolve_singer_colors(karaoke_style: Dict[str, Any], singer_id: int) -> Dict[str, str]:
    """Return the resolved color dict for a given SingerId.

    Resolution order (first match wins) per color field:
      1. karaoke_style["singers"][key] from the theme JSON (author-specified
         per-singer override).
      2. DEFAULT_KARAOKE_STYLE["singers"][key] (built-in blue/pink/yellow
         duet palette — used when the theme doesn't declare singer colors).
      3. karaoke_style[field] (theme's flat color).

    The default-palette fallback (step 2) means duets rendered with themes
    that don't declare singer colors still get distinct blue/pink/yellow
    singer identification instead of collapsing to a single color.

    singer_id must be 0 ("both"), 1, or 2.
    """
    key = _SINGER_KEY_MAP[singer_id]
    theme_singers = karaoke_style.get("singers") or {}
    theme_override = theme_singers.get(key) or {}
    default_override = DEFAULT_KARAOKE_STYLE.get("singers", {}).get(key, {})

    def _pick(field: str):
        if field in theme_override:
            return theme_override[field]
        if field in default_override:
            return default_override[field]
        return karaoke_style.get(field)

    return {field: _pick(field) for field in _SINGER_COLOR_FIELDS}


# =============================================================================
# ASSET KEY MAPPINGS
# =============================================================================

# Maps GCS/upload asset keys to (section, field) paths in the style JSON.
# Some assets map to multiple fields (e.g., font applies to intro, karaoke, end).
ASSET_KEY_MAPPINGS: Dict[str, Union[Tuple[str, str], List[Tuple[str, str]]]] = {
    # Background images - each maps to one section
    "intro_background": ("intro", "background_image"),
    "style_intro_background": ("intro", "background_image"),  # CLI upload key
    "karaoke_background": ("karaoke", "background_image"),
    "style_karaoke_background": ("karaoke", "background_image"),  # CLI upload key
    "end_background": ("end", "background_image"),
    "style_end_background": ("end", "background_image"),  # CLI upload key
    
    # Font - maps to multiple sections
    "font": [
        ("intro", "font"),
        ("karaoke", "font_path"),
        ("end", "font"),
        ("cdg", "font_path"),
    ],
    "style_font": [  # CLI upload key
        ("intro", "font"),
        ("karaoke", "font_path"),
        ("end", "font"),
        ("cdg", "font_path"),
    ],
    
    # CDG-specific backgrounds
    "cdg_instrumental_background": ("cdg", "instrumental_background"),
    "style_cdg_instrumental_background": ("cdg", "instrumental_background"),
    "cdg_title_background": ("cdg", "title_screen_background"),
    "style_cdg_title_background": ("cdg", "title_screen_background"),
    "cdg_outro_background": ("cdg", "outro_background"),
    "style_cdg_outro_background": ("cdg", "outro_background"),
}


# =============================================================================
# STYLE LOADING FUNCTIONS
# =============================================================================

def load_style_params_from_file(
    style_json_path: Optional[str],
    logger: Optional[logging.Logger] = None,
    exit_on_error: bool = True,
) -> Dict[str, Any]:
    """
    Load style parameters from a local JSON file.
    
    Args:
        style_json_path: Path to the style JSON file, or None for defaults.
        logger: Optional logger for messages.
        exit_on_error: If True, calls sys.exit(1) on file errors. 
                       If False, raises exceptions instead.
    
    Returns:
        Dictionary of style parameters (loaded or defaults).
    
    Raises:
        FileNotFoundError: If file not found and exit_on_error=False.
        json.JSONDecodeError: If invalid JSON and exit_on_error=False.
    """
    import sys
    
    log = logger or logging.getLogger(__name__)
    
    if not style_json_path:
        log.info("No style parameters JSON file provided. Using default styles.")
        return get_default_style_params()
    
    try:
        with open(style_json_path, "r") as f:
            style_params = json.load(f)
        log.info(f"Loaded style parameters from {style_json_path}")
        return style_params
    except FileNotFoundError:
        log.error(f"Style parameters configuration file not found: {style_json_path}")
        if exit_on_error:
            sys.exit(1)
        raise
    except json.JSONDecodeError as e:
        log.error(f"Invalid JSON in style parameters configuration file: {e}")
        if exit_on_error:
            sys.exit(1)
        raise
    except Exception as e:
        log.error(f"Error loading style parameters file {style_json_path}: {e}")
        if exit_on_error:
            sys.exit(1)
        raise


def apply_style_overrides(
    style_params: Dict[str, Any],
    overrides: Dict[str, str],
    logger: Optional[logging.Logger] = None,
) -> None:
    """
    Recursively apply overrides to style parameters (in place).
    
    Args:
        style_params: Style parameters dict to modify.
        overrides: Dict of "section.key" -> value overrides.
        logger: Optional logger for messages.
    """
    log = logger or logging.getLogger(__name__)
    
    for key, value in overrides.items():
        keys = key.split('.')
        current_level = style_params
        
        for i, k in enumerate(keys):
            if i == len(keys) - 1:
                if k in current_level:
                    # Cast to original type if possible
                    try:
                        original_type = type(current_level[k])
                        if original_type == bool:
                            value = str(value).lower() in ('true', '1', 't', 'y', 'yes')
                        elif current_level[k] is not None:
                            value = original_type(value)
                    except (ValueError, TypeError) as e:
                        log.warning(
                            f"Could not cast override value '{value}' for key '{key}' "
                            f"to original type. Using as string. Error: {e}"
                        )
                    current_level[k] = value
                    log.info(f"Overrode style: {key} = {value}")
                else:
                    log.warning(f"Override key '{key}' not found in style parameters.")
            elif k in current_level and isinstance(current_level[k], dict):
                current_level = current_level[k]
            else:
                log.warning(
                    f"Override key part '{k}' not found or not a dictionary for key '{key}'."
                )
                break


def update_asset_paths(
    style_data: Dict[str, Any],
    local_assets: Dict[str, str],
    logger: Optional[logging.Logger] = None,
) -> bool:
    """
    Update file paths in style data to point to local asset files.
    
    This is used when assets are downloaded from GCS and need their
    paths updated in the style JSON to point to the local copies.
    
    Args:
        style_data: Style parameters dict to modify (in place).
        local_assets: Dict mapping asset keys to local file paths.
        logger: Optional logger for messages.
    
    Returns:
        True if any paths were updated, False otherwise.
    """
    log = logger or logging.getLogger(__name__)
    updates_made = False
    
    for asset_key, local_path in local_assets.items():
        if asset_key not in ASSET_KEY_MAPPINGS:
            continue
        
        mappings = ASSET_KEY_MAPPINGS[asset_key]
        
        # Normalize to list of tuples
        if isinstance(mappings, tuple):
            mappings = [mappings]
        
        for section, field in mappings:
            if section in style_data and isinstance(style_data[section], dict):
                old_value = style_data[section].get(field, 'NOT SET')
                style_data[section][field] = local_path
                log.info(f"Updated {section}.{field}: {old_value} -> {local_path}")
                updates_made = True
    
    return updates_made


def save_style_params(
    style_data: Dict[str, Any],
    output_path: str,
    logger: Optional[logging.Logger] = None,
) -> str:
    """
    Save style parameters to a JSON file.
    
    Args:
        style_data: Style parameters dict to save.
        output_path: Path to save the JSON file.
        logger: Optional logger for messages.
    
    Returns:
        The output path.
    """
    log = logger or logging.getLogger(__name__)
    
    with open(output_path, 'w') as f:
        json.dump(style_data, f, indent=2)
    
    log.info(f"Saved style parameters to: {output_path}")
    return output_path


# =============================================================================
# GCS STYLE LOADING (for backend workers)
# =============================================================================

def load_styles_from_gcs(
    style_params_gcs_path: Optional[str],
    style_assets: Optional[Dict[str, str]],
    temp_dir: str,
    download_func: Callable[[str, str], None],
    logger: Optional[logging.Logger] = None,
) -> Tuple[str, Dict[str, Any]]:
    """
    Download and process style configuration from GCS.
    
    This is the main entry point for backend workers to load styles.
    It handles:
    1. Downloading style_params.json from GCS
    2. Downloading all style assets (backgrounds, fonts)
    3. Updating paths in the style JSON to point to local files
    4. Saving the updated JSON
    
    Args:
        style_params_gcs_path: GCS path to style_params.json, or None.
        style_assets: Dict of asset_key -> GCS path for style assets.
        temp_dir: Temporary directory for downloaded files.
        download_func: Function(gcs_path, local_path) to download files.
        logger: Optional logger for messages.
    
    Returns:
        Tuple of (local_styles_path, style_data_dict).
        If no custom styles, returns (minimal_styles_path, minimal_styles_dict).
    """
    log = logger or logging.getLogger(__name__)
    
    style_dir = os.path.join(temp_dir, "style")
    os.makedirs(style_dir, exist_ok=True)
    styles_path = os.path.join(style_dir, "styles.json")
    
    if not style_params_gcs_path:
        raise ValueError(
            "style_params_gcs_path is required. All jobs must have a complete theme. "
            "Ensure the job has a theme_id set."
        )
    
    try:
        log.info(f"Downloading custom styles from {style_params_gcs_path}")
        download_func(style_params_gcs_path, styles_path)
        
        with open(styles_path, 'r') as f:
            style_data = json.load(f)
        
        log.info(f"Loaded style sections: {list(style_data.keys())}")
        
        # Download style assets
        local_assets = {}
        if style_assets:
            log.info(f"Downloading {len(style_assets)} style assets...")
            for asset_key, gcs_path in style_assets.items():
                if asset_key == 'style_params':
                    continue  # Already downloaded
                try:
                    ext = os.path.splitext(gcs_path)[1] or '.png'
                    local_path = os.path.join(style_dir, f"{asset_key}{ext}")
                    download_func(gcs_path, local_path)
                    local_assets[asset_key] = local_path
                    log.info(f"  Downloaded {asset_key}: {local_path}")
                except Exception as e:
                    log.warning(f"  Failed to download {asset_key}: {e}")
        
        # Update paths in style_data
        if local_assets:
            updates_made = update_asset_paths(style_data, local_assets, log)
            if updates_made:
                save_style_params(style_data, styles_path, log)
        
        # Log karaoke style for debugging
        if 'karaoke' in style_data:
            k = style_data['karaoke']
            log.info(
                f"Final karaoke style: background_image={k.get('background_image', 'NOT SET')}, "
                f"font_path={k.get('font_path', 'NOT SET')}"
            )
        
        return styles_path, style_data
        
    except Exception as e:
        log.error(f"Failed to download theme styles: {e}")
        raise ValueError(
            f"Failed to download theme from GCS: {e}. "
            "Cannot proceed without complete theme styles."
        ) from e


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_default_style_params() -> Dict[str, Any]:
    """Get a fresh copy of the default style parameters."""
    return {
        "intro": DEFAULT_INTRO_STYLE.copy(),
        "end": DEFAULT_END_STYLE.copy(),
        "karaoke": DEFAULT_KARAOKE_STYLE.copy(),
        "cdg": DEFAULT_CDG_STYLE.copy(),
    }


def get_minimal_karaoke_styles() -> Dict[str, Any]:
    """
    Get minimal styles for karaoke video generation.

    This is used when no custom styles are provided, providing
    just enough configuration for the ASS subtitle generator and CDG generator.
    """
    return {
        "karaoke": DEFAULT_KARAOKE_STYLE.copy(),
        "cdg": DEFAULT_CDG_STYLE.copy(),
    }


def validate_theme_completeness(
    style_params: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> Tuple[bool, List[str]]:
    """
    Validate that a theme has all required parameters.

    Args:
        style_params: Style parameters dict to validate.
        logger: Optional logger for messages.

    Returns:
        Tuple of (is_complete, list_of_missing_fields)
    """
    log = logger or logging.getLogger(__name__)
    missing = []

    # Required sections
    required_sections = ["intro", "end", "karaoke", "cdg"]
    for section in required_sections:
        if section not in style_params:
            missing.append(f"Missing section: {section}")
            continue

        # Check required fields per section
        if section == "intro":
            required_fields = DEFAULT_INTRO_STYLE.keys()
        elif section == "end":
            required_fields = DEFAULT_END_STYLE.keys()
        elif section == "karaoke":
            required_fields = DEFAULT_KARAOKE_STYLE.keys()
        elif section == "cdg":
            required_fields = DEFAULT_CDG_STYLE.keys()
        else:
            continue

        for field in required_fields:
            if field not in style_params[section]:
                missing.append(f"{section}.{field}")

    is_complete = len(missing) == 0
    if not is_complete:
        log.warning(f"Theme validation failed. Missing: {missing}")

    return is_complete, missing


def get_intro_format(style_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract intro/title screen format from style parameters.

    Raises:
        ValueError: If intro section is missing or incomplete
    """
    if "intro" not in style_params:
        raise ValueError(
            "Missing 'intro' section in theme. "
            "All themes must include intro, end, karaoke, and cdg sections. "
            "Use --theme to select a complete theme or --validate-theme to check your theme."
        )

    intro_params = style_params["intro"]

    # Validate all required fields are present
    required = DEFAULT_INTRO_STYLE.keys()
    missing = [f for f in required if f not in intro_params]

    if missing:
        raise ValueError(
            f"Incomplete 'intro' section. Missing: {missing}. "
            f"Themes must be complete - no defaults are provided. "
            f"Use --validate-theme to check your theme."
        )

    return intro_params.copy()  # Return as-is, no merging


def get_end_format(style_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract end screen format from style parameters.

    Raises:
        ValueError: If end section is missing or incomplete
    """
    if "end" not in style_params:
        raise ValueError(
            "Missing 'end' section in theme. "
            "All themes must include intro, end, karaoke, and cdg sections. "
            "Use --theme to select a complete theme or --validate-theme to check your theme."
        )

    end_params = style_params["end"]

    # Validate all required fields are present
    required = DEFAULT_END_STYLE.keys()
    missing = [f for f in required if f not in end_params]

    if missing:
        raise ValueError(
            f"Incomplete 'end' section. Missing: {missing}. "
            f"Themes must be complete - no defaults are provided. "
            f"Use --validate-theme to check your theme."
        )

    return end_params.copy()  # Return as-is, no merging


def get_karaoke_format(style_params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract karaoke video format from style parameters.

    Raises:
        ValueError: If karaoke section is missing or incomplete
    """
    if "karaoke" not in style_params:
        raise ValueError(
            "Missing 'karaoke' section in theme. "
            "All themes must include intro, end, karaoke, and cdg sections. "
            "Use --theme to select a complete theme or --validate-theme to check your theme."
        )

    karaoke_params = style_params["karaoke"]

    # Validate all required fields are present
    required = DEFAULT_KARAOKE_STYLE.keys()
    missing = [f for f in required if f not in karaoke_params]

    if missing:
        raise ValueError(
            f"Incomplete 'karaoke' section. Missing: {missing}. "
            f"Themes must be complete - no defaults are provided. "
            f"Use --validate-theme to check your theme."
        )

    return karaoke_params.copy()  # Return as-is, no merging


def get_cdg_format(style_params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Extract CDG generation format from style parameters.

    Returns None if no CDG section is defined.

    Raises:
        ValueError: If cdg section exists but is incomplete
    """
    if "cdg" not in style_params:
        return None

    cdg_params = style_params["cdg"]

    # Validate all required fields are present
    required = DEFAULT_CDG_STYLE.keys()
    missing = [f for f in required if f not in cdg_params]

    if missing:
        raise ValueError(
            f"Incomplete 'cdg' section. Missing: {missing}. "
            f"Themes must be complete - no defaults are provided. "
            f"Use --validate-theme to check your theme."
        )

    return cdg_params.copy()  # Return as-is, no merging


def get_video_durations(style_params: Dict[str, Any]) -> Tuple[int, int]:
    """
    Get intro and end video durations from style parameters.
    
    Returns:
        Tuple of (intro_duration, end_duration) in seconds.
    """
    intro_duration = style_params.get("intro", {}).get(
        "video_duration", DEFAULT_INTRO_STYLE["video_duration"]
    )
    end_duration = style_params.get("end", {}).get(
        "video_duration", DEFAULT_END_STYLE["video_duration"]
    )
    return intro_duration, end_duration


def get_existing_images(style_params: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Get existing title and end images from style parameters.

    Returns:
        Tuple of (existing_title_image, existing_end_image) paths or None.
    """
    existing_title_image = style_params.get("intro", {}).get("existing_image")
    existing_end_image = style_params.get("end", {}).get("existing_image")
    return existing_title_image, existing_end_image


# =============================================================================
# CDG DUET PALETTE (hardcoded — CDG has a 16-color palette budget)
# =============================================================================

# CDG singer colors (RGB tuples).
# Ordering: [Singer 1, Singer 2, Both]. CDG composer is 1-indexed so this
# list maps to SettingsLyric.singer = 1, 2, 3 respectively. Our internal
# SingerId 0 (Both) maps to CDG singer 3.
#
# Imported lazily to avoid circular imports; see karaoke_gen/lyrics_transcriber/output/cdg.py
# for usage.
def _build_cdg_duet_singers():
    # Imported here to avoid top-level import of the cdgmaker package
    from karaoke_gen.lyrics_transcriber.output.cdgmaker.config import SettingsSinger
    # Color model (matches the ASS/MP4 path, commit b2f20b43):
    #   inactive (pre-sung / upcoming)  → the singer's signature color, so
    #                                     performers can read ahead and see
    #                                     who sings what.
    #   active (currently being sung)   → white, making the sweep read
    #                                     clearly against the colored text.
    # CDG composer semantics: "active" = highlighted/sweep-fill,
    # "inactive" = the resting color before/after the sweep.
    return [
        # Singer 1: blue pre-sung → white when sung.
        # Brightened from #7070F7 to #9AA8FF (sky blue) so it reads clearly
        # against dark backgrounds like the nomad #111427 navy.
        SettingsSinger(
            active_fill="#FFFFFF", active_stroke="#505050",
            inactive_fill="#9AA8FF", inactive_stroke="#2A4AEB",
        ),
        # Singer 2: pink pre-sung → white when sung
        SettingsSinger(
            active_fill="#FFFFFF", active_stroke="#505050",
            inactive_fill="#F770B4", inactive_stroke="#9E1A60",
        ),
        # Both: yellow pre-sung → white when sung
        SettingsSinger(
            active_fill="#FFFFFF", active_stroke="#505050",
            inactive_fill="#FCD34D", inactive_stroke="#926C00",
        ),
    ]


# Use a module-level lazy accessor so first access triggers the import
class _CdgDuetSingersLazy:
    _cache = None
    def __iter__(self):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return iter(self._cache)
    def __getitem__(self, idx):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return self._cache[idx]
    def __len__(self):
        if self._cache is None:
            self._cache = _build_cdg_duet_singers()
        return len(self._cache)


CDG_DUET_SINGERS = _CdgDuetSingersLazy()
