"""KaraokeNerds brand code -> human display name.

Our own KaraokeNerds catalog (BigQuery `karaokenerds_community` + the GCS export)
stores only the brand *code* (e.g. "NOMAD", "OBSK"). The old live scrape also
surfaced the human name ("Nomad Karaoke", "ObsKure Karaoke") which the UI shows.
KaraokeNerds exposes no authorized brand-name data source, so this static map
(kept in sync with kjbox's version_priority registry, plus the highest-frequency
community brands) reproduces those names. Unknown codes fall back to the code —
the same graceful degradation the UI already applies.
"""

KN_BRAND_NAMES: dict[str, str] = {
    # Community brands (mirrors kjbox version_priority.COMMUNITY_BRANDS display names)
    "CC": "CC Karaoke",
    "LC": "Lemmy Caution",
    "FBK": "Funbox Karaoke",
    "BELLY": "BellySings",
    "NOMAD": "Nomad Karaoke",
    "FAKEY": "FakeyOke",
    "PMK": "Punk Media Karaoke",
    "OBSK": "ObsKure Karaoke",
    "SDK": "SNDL Karaoke",
    "DBK": "Deep Bench Karaoke",
    # Commercial brands (mirrors version_priority.COMMERCIAL_BRANDS display names)
    "KV": "Karaoke Version",
    "SC": "Sound Choice",
    "SBI": "SBI Karaoke",
    "SF": "Sunfly",
    "CB": "Chart Buster",
    "ZM": "Zoom",
    "VS": "Vocal Star",
    "SK": "Sing King",
    "MR": "Mr. Entertainer",
    "PT": "Party Tyme",
    "EK": "Easy Karaoke",
    # Additional high-frequency community brands (harvested from KaraokeNerds).
    "IKV": "Imperfekt Karaoke",
    "ZP": "Zipper Karaoke",
    "JL311": "Rock Solid Karaoke",
    "CAR": "Caritas",
    "MKU": "Mobile Karaoke Unit",
    "VONAGAM": "Vonagam Karaoke",
    "GR": "ggnzla RECORDS",
    "HALJAM": "Hal Jam",
    "REEKIES": "Reekies Karaoke",
    "DJS": "DJ Sauly Karaoke",
}


def brand_name_for(code: str) -> str:
    """Human display name for a KaraokeNerds brand code; the code itself if unknown."""
    if not code:
        return ""
    return KN_BRAND_NAMES.get(code.upper().strip(), code)
