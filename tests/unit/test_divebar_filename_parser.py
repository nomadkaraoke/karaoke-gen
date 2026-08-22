"""Unit tests for the Divebar filename parser.

The parser lives in the Cloud Function package (not on the default path), so we
add its directory to sys.path and import it directly. It has stdlib-only deps.

Fixtures are real filenames observed in the live `divebar_catalog` index, grouped
by the naming convention each community brand uses. The goal of the rewrite is
folder-brand-aware parsing: use the (reliable) top-level folder brand to strip the
brand token wherever it sits, then split the remainder into artist/title.
"""

import pathlib
import sys

import pytest

FUNC_DIR = (
    pathlib.Path(__file__).resolve().parents[2]
    / "infrastructure"
    / "functions"
    / "divebar_mirror"
)
sys.path.insert(0, str(FUNC_DIR))

import filename_parser as fp  # noqa: E402


def parse(filename, folder):
    return fp.parse_filename(filename, brand_folder=folder)


# --------------------------------------------------------------------------
# disc-id PREFIX (works today — must be preserved)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title,brand_code,disc_id",
    [
        ("FATBIRD-163 - Incubus - I Miss You.zip", "Fatbird Karaoke",
         "Incubus", "I Miss You", "FATBIRD", "FATBIRD-163"),
        ("NOMAD-0003 - Cher - Apples Don't Fall Far From The Tree.mp4", "Nomad Karaoke",
         "Cher", "Apples Don't Fall Far From The Tree", "NOMAD", "NOMAD-0003"),
        ("IK00-01 - Costello, Elvis - Goon Squad.zip", "Imperfekt Karaoke",
         "Costello, Elvis", "Goon Squad", "IK00", "IK00-01"),
        ("GGZ001-02 - Partman Parthorse - Magik (ggnzla REMIX).cdg", "GGZ-ggnzla",
         "Partman Parthorse", "Magik (ggnzla REMIX)", "GGZ001", "GGZ001-02"),
        ("KFNO101 - The Kills - List of Demands (Reparations).mp4", "Karaoke For No One",
         "The Kills", "List of Demands (Reparations)", "KFNO", "KFNO101"),
    ],
)
def test_disc_id_prefix(filename, folder, artist, title, brand_code, disc_id):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title
    assert r["brand_code"] == brand_code
    assert r["disc_id"] == disc_id


# --------------------------------------------------------------------------
# disc-id PREFIX, space-separated / mixed-case / digit-leading
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title,disc_id",
    [
        # Potos: "CODE-#### Artist - Title" (space, not " - ", after disc-id)
        ("RABZ-0001 The Seatbelts & Steve Conte - Call Me, Call Me.zip",
         "Potos Power Plant Productions",
         "The Seatbelts & Steve Conte", "Call Me, Call Me", "RABZ-0001"),
        # Lopenash: mixed-case folder-name prefix
        ("Lopenash-002 - Dexter's Laboratory - Breathe in The Sunshine.mp4", "Lopenash",
         "Dexter's Laboratory", "Breathe in The Sunshine", "Lopenash-002"),
        # 7uLpA: digit-leading mixed-case token
        ("7uLpA-0004 - The Beauty of Gemina - Wonders.mp4", "7uLpA_Made",
         "The Beauty of Gemina", "Wonders", "7uLpA-0004"),
    ],
)
def test_disc_id_prefix_variants(filename, folder, artist, title, disc_id):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title
    assert r["disc_id"] == disc_id


def test_disc_id_prefix_single_field():
    # Lopenash medley with no artist/title split -> title only, artist None
    r = parse("Lopenash-001 - Super Energy Apocalypse.zip", "Lopenash")
    assert r["artist"] is None
    assert r["title"] == "Super Energy Apocalypse"
    assert r["disc_id"] == "Lopenash-001"


def test_disc_id_prefix_drops_leading_track_number():
    # Mix Tape: "CODE-### - NN - Artist - Title" -> drop the NN track number
    r = parse("MIX001 - 02 - Psychostick - Mega Man.mp4", "Mix Tape Karaoke")
    assert r["artist"] == "Psychostick"
    assert r["title"] == "Mega Man"


# --------------------------------------------------------------------------
# disc-id SUFFIX (unconditional strip)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title,brand_code,disc_id",
    [
        ("3Blue1Brown - How They Fool Ya - IMBK-0076.mp4", "It Might Be Karaoke",
         "3Blue1Brown", "How They Fool Ya", "IMBK", "IMBK-0076"),
        ("54-40 - Nice to Luv you - SOKC-0036 (KARAOKE).zip", "Steve-O Karaoke Cult",
         "54-40", "Nice to Luv you", "SOKC", "SOKC-0036"),
        ("Against Me - Wagon Wheel - SOKC-0306 - (KARAOKE).zip", "Steve-O Karaoke Cult",
         "Against Me", "Wagon Wheel", "SOKC", "SOKC-0306"),
        # disc-id suffix with no dash before the code
        ("Age Of Electric - Remote Control SOKC-0042 (KARAOKE).zip", "Steve-O Karaoke Cult",
         "Age Of Electric", "Remote Control", "SOKC", "SOKC-0042"),
        # ATG: code with no dash inside (ATG0014)
        ("Angelcorpse - Wolflust - ATG0014.zip", "ATG Karaoke - Zipped MP3+G",
         "Angelcorpse", "Wolflust", "ATG", "ATG0014"),
        ("1349 - Singer Of Strange Songs - ATG0072.zip", "ATG Karaoke - Zipped MP3+G",
         "1349", "Singer Of Strange Songs", "ATG", "ATG0072"),
    ],
)
def test_disc_id_suffix(filename, folder, artist, title, brand_code, disc_id):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title
    assert r["brand_code"] == brand_code
    assert r["disc_id"] == disc_id


# --------------------------------------------------------------------------
# bracket / paren brand SUFFIX (gated on folder match)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title",
    [
        ("3rd Bass - Green Eggs And Swine [DJ Sauly Karaoke].zip", "DJ Sauly Collection",
         "3rd Bass", "Green Eggs And Swine"),
        # keep the legit (Banda Remix), strip only the [DJ Sauly Karaoke] tag
        ("50 Cent - In Da Club (Banda Remix) [DJ Sauly Karaoke].zip", "DJ Sauly Collection",
         "50 Cent", "In Da Club (Banda Remix)"),
        ("10 Years - Fix Me (BellySings).mp4", "BellySings Karaoke",
         "10 Years", "Fix Me"),
        ("Bad Religion - Fuck You (popeoke).zip", "Popeoke",
         "Bad Religion", "Fuck You"),
        ("B.E.R. - The Night Begins to Shine [popeoke].zip", "Popeoke",
         "B.E.R.", "The Night Begins to Shine"),
    ],
)
def test_brand_suffix_bracket_or_paren(filename, folder, artist, title):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title


# --------------------------------------------------------------------------
# dash brand SUFFIX (gated on folder match / per-folder token)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title",
    [
        ("100 Gecs - Last Train To Awesometown - Rock Solid Karaoke.zip",
         "Rock Solid Karaoke CDG files", "100 Gecs", "Last Train To Awesometown"),
        ("311 - Amber - Rock Solid Karaoke.zip",
         "Rock Solid Karaoke CDG files", "311", "Amber"),
        ("Akon - Right Now (Na Na Na) - EBK.avi", "ForteKaraoke Discord",
         "Akon", "Right Now (Na Na Na)"),
    ],
)
def test_brand_suffix_dash(filename, folder, artist, title):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title


# --------------------------------------------------------------------------
# code / folder-name PREFIX (no number)
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title,brand_code",
    [
        ("(SDK) (G)I-DLE - Queencard.cdg", "Sandell Karaoke",
         "(G)I-DLE", "Queencard", "SDK"),
        ("(SDK) 21 Pilots - House Of Gold.cdg", "Sandell Karaoke",
         "21 Pilots", "House Of Gold", "SDK"),
        ("CKK -  Buoys, The - Timothy.zip", "Cereal Killer Karaoke",
         "Buoys, The", "Timothy", "CKK"),
        ("CKK - A Day To Remember - If It Means A Lot To You.zip", "Cereal Killer Karaoke",
         "A Day To Remember", "If It Means A Lot To You", "CKK"),
        ("TNSK - Davy Jones - Girl.zip", "The Nerdy Singer Karaoke",
         "Davy Jones", "Girl", "TNS"),
        ("Crossfire - Eurythmics - Missionary Man.mp4", "Crossfire Karaoke",
         "Eurythmics", "Missionary Man", None),
    ],
)
def test_code_or_name_prefix(filename, folder, artist, title, brand_code):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title
    assert r["brand_code"] == brand_code


# --------------------------------------------------------------------------
# generic karaoke tag strip (display title, both () and [])
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title",
    [
        ("Bill Murray - Star Wars [Karaoke].mp4", "Mobile Karaoke Unit",
         "Bill Murray", "Star Wars"),
        ("3 Doors Down - Down Poison (Karaoke HD).zip", "Funbox Karaoke",
         "3 Doors Down", "Down Poison"),
        ("Islands - Pumpkin (Karaoke Version).mp4", "Matt Joy Karaoke",
         "Islands", "Pumpkin"),
    ],
)
def test_generic_karaoke_tag_stripped_from_title(filename, folder, artist, title):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title


# --------------------------------------------------------------------------
# clean "Artist - Title" — only the curated brand_code map applies
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "filename,folder,artist,title,brand_code",
    [
        ("A Tribe Called Quest - Lyrics To Go.avi", "WIZZYOKE",
         "A Tribe Called Quest", "Lyrics To Go", "WIZZY"),
        ("100 gecs - Hollywood Baby.cdg", "Funbox Karaoke",
         "100 gecs", "Hollywood Baby", "FBK"),
        ("Alice in Chains - Angry Chair.mp4", "Brilliant Trash Karaoke",
         "Alice in Chains", "Angry Chair", "BTRASH"),
        ("16 Horsepower - American Wheeze.cdg", "Monster Tracks",
         "16 Horsepower", "American Wheeze", "MTRAX"),
    ],
)
def test_clean_artist_title_with_curated_code(filename, folder, artist, title, brand_code):
    r = parse(filename, folder)
    assert r["artist"] == artist
    assert r["title"] == title
    assert r["brand_code"] == brand_code


# --------------------------------------------------------------------------
# GUARDS — must NOT over-strip real song text
# --------------------------------------------------------------------------

def test_guard_keeps_non_brand_bracket_in_title():
    # [Nuxx] and (RVZN Edit) are NOT the folder brand -> keep them
    r = parse("RVZ-016 - UNDERWORLD - Born Slippy [Nuxx] (RVZN Edit).mp4", "R3V1Z10N Karaoke")
    assert r["artist"] == "UNDERWORLD"
    assert r["title"] == "Born Slippy [Nuxx] (RVZN Edit)"
    assert r["brand_code"] == "RVZ"


def test_guard_keeps_paren_artist():
    r = parse("(SDK) (G)I-DLE - Queencard.cdg", "Sandell Karaoke")
    assert r["artist"] == "(G)I-DLE"  # only the (SDK) brand prefix removed


def test_guard_keeps_live_qualifier_in_title():
    # A trailing (Live) that is not the brand must survive (folder doesn't match)
    r = parse("311 - All Re-Mixed Up (live) - Rock Solid Karaoke.zip",
              "Rock Solid Karaoke CDG files")
    assert r["artist"] == "311"
    assert r["title"] == "All Re-Mixed Up (live)"


def test_guard_numeric_artist_preserved():
    r = parse("311 - Amber - Rock Solid Karaoke.zip", "Rock Solid Karaoke CDG files")
    assert r["artist"] == "311"


def test_guard_alphanumeric_artist_not_eaten_as_disc_id():
    # "UB40", "D12" are uppercase+digits but NOT disc-shaped (no hyphen, <3 digit
    # number) -> must stay the artist, not be stripped as a disc-id prefix.
    r = parse("UB40 - Kingston Town (BellySings).mp4", "BellySings Karaoke")
    assert r["artist"] == "UB40"
    assert r["title"] == "Kingston Town"
    r2 = parse("D12 - My Band.mp4", "Funbox Karaoke")
    assert r2["artist"] == "D12"
    assert r2["title"] == "My Band"


def test_en_dash_separator():
    r = parse("Iron Maiden – The Writing On The Wall.mp4", "ATG Karaoke - Zipped MP3+G")
    assert r["artist"] == "Iron Maiden"
    assert r["title"] == "The Writing On The Wall"


def test_strips_other_brand_karaoke_tag_in_title():
    # Funbox re-hosts WTF Karaoke files tagged "- (WTF Karaoke)"; the tag mentions
    # karaoke so it is stripped even though it isn't the folder brand.
    r = parse("Alice in Chains - Heaven Beside You - (WTF Karaoke).zip", "Funbox Karaoke")
    assert r["artist"] == "Alice in Chains"
    assert r["title"] == "Heaven Beside You"


def test_leading_dash_hugging_artist_cleaned():
    r = parse("7uLpA-0013 -Zynic - After Effects.mp4", "7uLpA_Made")
    assert r["artist"] == "Zynic"
    assert r["title"] == "After Effects"


# --------------------------------------------------------------------------
# normalized fields flow from parsed artist/title (xref keys)
# --------------------------------------------------------------------------

def test_normalized_fields_use_cleaned_title():
    r = parse("3rd Bass - Green Eggs And Swine [DJ Sauly Karaoke].zip", "DJ Sauly Collection")
    assert fp.normalize_for_search(r["artist"]) == "3rd bass"
    assert fp.normalize_for_search(r["title"]) == "green eggs and swine"


# --------------------------------------------------------------------------
# existing helpers still behave
# --------------------------------------------------------------------------

def test_should_index_file():
    assert fp.should_index_file("x.mp4") is True
    assert fp.should_index_file("x.zip") is True
    assert fp.should_index_file(".DS_Store") is False
    assert fp.should_index_file("notes.txt") is False


def test_detect_format():
    assert fp.detect_format("x.zip") == "zip"
    assert fp.detect_format("x.MP4") == "mp4"
    assert fp.detect_format("x.cdg") == "cdg"
