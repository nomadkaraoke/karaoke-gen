"""
Google Drive Validator Cloud Function

Validates the Nomad Karaoke public share folder for:
- Duplicate sequence numbers (NOMAD-XXXX)
- Invalid filename formats
- Sequence gaps (with configurable known gaps)

Sends email notification (via Postmark) if issues are detected.
Pushbullet is kept as a secondary fallback.

Triggered daily via Cloud Scheduler and after each job completion.
"""
import os
import re
import json
import logging
from collections import defaultdict
from typing import Union
import functions_framework
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration from environment variables
GDRIVE_FOLDER_ID = os.environ.get("GDRIVE_FOLDER_ID", "1laRKAyxo0v817SstfM5XkpbWiNKNAMSX")
PUSHBULLET_API_KEY = os.environ.get("PUSHBULLET_API_KEY", "").strip()
POSTMARK_SERVER_TOKEN = os.environ.get("POSTMARK_SERVER_TOKEN", "").strip()
EMAIL_TO = os.environ.get("EMAIL_TO", "gen@nomadkaraoke.com")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "gen@nomadkaraoke.com")

# Set to "true" to receive daily summary even when no issues found
# Set to "false" to only receive notifications when issues are detected
NOTIFY_ON_SUCCESS = os.environ.get("NOTIFY_ON_SUCCESS", "true").lower() == "true"

# Known gaps configuration - matches the local validate_and_sync.py script
# These are HISTORICAL ONLY (pre-generator era, before Dec 2024). Do NOT add new entries.
# New gaps are bugs — see docs/GDRIVE-VALIDATOR.md § "Sequence Gap Detected" for fix procedure.
# Format: Directory name -> List of ranges (inclusive) or individual numbers to exclude
KNOWN_GAPS = {
    "MP4": [
        532
    ],
    "MP4-720p": [
        532
    ],
    "CDG": [
        (4, 6),      # Early missing files
        21, 30, 41,  # Individual missing files
        67, 79, 168, 195,
        (197, 329),  # Large historical gap
        (331, 377),  # Another large gap
        381, 382,
        (385, 387),
        (393, 395),
        397, 398, 399, 402,
        439, 443, 451,
        532, 556
    ]
}


def expand_gaps(gaps: list[Union[int, tuple]]) -> set[int]:
    """Convert a list of gaps (individual numbers or ranges) into a set of all excluded numbers."""
    excluded = set()
    for gap in gaps:
        if isinstance(gap, tuple):
            start, end = gap
            excluded.update(range(start, end + 1))
        else:
            excluded.add(gap)
    return excluded


# Pre-compute excluded numbers for each directory
EXCLUDED_NUMBERS = {
    dir_name: expand_gaps(gaps)
    for dir_name, gaps in KNOWN_GAPS.items()
}


def get_drive_service():
    """Create a Google Drive API service using default credentials."""
    # In Cloud Functions, use Application Default Credentials
    # The function's service account will be used automatically
    from google.auth import default
    credentials, _ = default(scopes=['https://www.googleapis.com/auth/drive.readonly'])
    return build('drive', 'v3', credentials=credentials)


def list_folder_contents(service, folder_id: str) -> dict[str, list[str]]:
    """
    List all files in a Google Drive folder and its subfolders.
    
    Returns a dict mapping subfolder name to list of filenames.
    Expected structure:
    - CDG/ (contains .zip files)
    - MP4/ (contains .mp4 files)  
    - MP4-720p/ (contains .mp4 files)
    """
    results = {}
    
    try:
        # First, list the top-level subfolders (CDG, MP4, MP4-720p)
        query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = service.files().list(
            q=query,
            fields="files(id, name)",
            pageSize=100
        ).execute()
        
        subfolders = response.get('files', [])
        logger.info(f"Found {len(subfolders)} subfolders in root folder")
        
        for folder in subfolders:
            folder_name = folder['name']
            folder_id_inner = folder['id']
            
            # Only process expected folders
            if folder_name not in ['CDG', 'MP4', 'MP4-720p']:
                logger.warning(f"Unexpected folder: {folder_name}")
                continue
            
            # List all files in this subfolder
            files = []
            page_token = None
            
            while True:
                query = f"'{folder_id_inner}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
                response = service.files().list(
                    q=query,
                    fields="nextPageToken, files(name)",
                    pageSize=1000,
                    pageToken=page_token
                ).execute()
                
                files.extend([f['name'] for f in response.get('files', [])])
                page_token = response.get('nextPageToken')
                
                if not page_token:
                    break
            
            results[folder_name] = files
            logger.info(f"Found {len(files)} files in {folder_name}/")
    
    except HttpError:
        logger.exception("Error accessing Google Drive")
        raise
    
    return results


# Files to ignore during validation (these are not karaoke files)
IGNORED_FILES = {
    '.DS_Store',
    '.keep',
    '.gitkeep',
    'kj-nomad.index.json',
    'desktop.ini',
    'Thumbs.db',
}


def should_ignore_file(filename: str) -> bool:
    """Check if a file should be ignored during validation."""
    return filename in IGNORED_FILES or filename.startswith('.')


def validate_filename_format(filename: str) -> bool:
    """Check if filename matches the expected format: NOMAD-XXXX - Artist - Title.(mp4|zip)"""
    if should_ignore_file(filename):
        return True  # Ignored files pass validation
    pattern = r"^NOMAD-\d{4} - .+ - .+\.(mp4|zip)$"
    return bool(re.match(pattern, filename))


def get_sequence_number(filename: str) -> int | None:
    """Extract sequence number from filename. Returns None if not found."""
    match = re.match(r"NOMAD-(\d{4})", filename)
    if match:
        return int(match.group(1))
    return None


def validate_files(files_by_folder: dict[str, list[str]]) -> dict:
    """
    Validate all files and return a dict of issues found.

    Returns:
        {
            'duplicates': {'MP4': {1107: ['file1.mp4', 'file2.mp4']}, ...},
            'invalid_filenames': {'CDG': ['bad_file.json'], ...},
            'gaps': {'MP4': [123, 124, 125], ...},
            'summary': {'total_files': 3000, 'mp4': 1127, ...}
        }
    """
    issues = {
        'duplicates': defaultdict(dict),
        'invalid_filenames': defaultdict(list),
        'gaps': defaultdict(list),
        'summary': {}
    }

    total_files = 0
    sequence_numbers_by_folder: dict[str, set[int]] = {}

    for folder_name, files in files_by_folder.items():
        # Filter out ignored files for counting
        karaoke_files = [f for f in files if not should_ignore_file(f)]
        total_files += len(karaoke_files)
        issues['summary'][folder_name.lower().replace('-', '_')] = len(karaoke_files)

        # Check for invalid filenames (skip ignored files)
        for filename in files:
            if should_ignore_file(filename):
                continue
            if not validate_filename_format(filename):
                issues['invalid_filenames'][folder_name].append(filename)

        # Check for duplicate sequence numbers
        seq_to_files = defaultdict(list)
        valid_files = [f for f in files if not should_ignore_file(f) and validate_filename_format(f)]

        for filename in valid_files:
            seq_num = get_sequence_number(filename)
            if seq_num is not None:
                seq_to_files[seq_num].append(filename)

        for seq_num, filenames in seq_to_files.items():
            if len(filenames) > 1:
                issues['duplicates'][folder_name][seq_num] = filenames

        # Collect sequence numbers for gap checking
        sequence_numbers = set()
        for filename in valid_files:
            seq_num = get_sequence_number(filename)
            if seq_num is not None:
                sequence_numbers.add(seq_num)
        sequence_numbers_by_folder[folder_name] = sequence_numbers

    issues['summary']['total'] = total_files

    # Use the global max across all folders so that a folder missing the latest
    # track(s) is caught — e.g. CDG missing NOMAD-1277 when MP4 already has it.
    all_nums = [n for nums in sequence_numbers_by_folder.values() for n in nums]
    global_max = max(all_nums) if all_nums else 0

    for folder_name, sequence_numbers in sequence_numbers_by_folder.items():
        if sequence_numbers and global_max:
            min_num = min(sequence_numbers)
            expected_range = set(range(min_num, global_max + 1))
            excluded = EXCLUDED_NUMBERS.get(folder_name, set())
            missing = sorted(expected_range - sequence_numbers - excluded)

            if missing:
                issues['gaps'][folder_name] = missing

    # Convert defaultdicts to regular dicts for cleaner output
    issues['duplicates'] = dict(issues['duplicates'])
    issues['invalid_filenames'] = dict(issues['invalid_filenames'])
    issues['gaps'] = dict(issues['gaps'])

    return issues


# Folders a public release must appear in. (CDG is included — every public NOMAD
# release ships a CDG zip; a track missing from CDG is a real shortfall.)
REQUIRED_TRACK_FOLDERS = ('MP4', 'MP4-720p', 'CDG')


def check_track_completeness(files_by_folder: dict[str, list[str]], brand_code: str) -> list[str]:
    """Return the folders a specific brand code is MISSING from.

    Incident-hardening D2: when the post-job trigger names the just-published
    ``brand_code`` (e.g. "NOMAD-1632"), verify that track is present in every
    expected folder — *independent of the global-max gap logic*, which only
    notices a missing latest track once a later track raises the max. This fires
    same-run (5 min post-job) instead of up to 24h later.

    Matching is by the ``NOMAD-#### `` filename prefix, so it ignores the
    (differing) artist/title tail.
    """
    # Normalize "1632" / "NOMAD-1632" / "nomad-1632" to the "NOMAD-1632 " prefix.
    code = str(brand_code).strip().upper()
    if not code.startswith("NOMAD-"):
        code = f"NOMAD-{code}"
    prefix = f"{code} "

    missing_from = []
    for folder in REQUIRED_TRACK_FOLDERS:
        files = files_by_folder.get(folder, [])
        if not any(f.startswith(prefix) for f in files):
            missing_from.append(folder)
    return missing_from


def has_issues(issues: dict) -> bool:
    """Check if there are any validation issues."""
    return (
        bool(issues['duplicates']) or
        bool(issues['invalid_filenames']) or
        bool(issues['gaps']) or
        bool(issues.get('missing_for_track'))
    )


def format_notification(issues: dict) -> tuple[str, str]:
    """Format issues into a Pushbullet notification (title, body)."""
    title = "⚠️ Karaoke GDrive Validation Issues Found"
    
    lines = []
    issue_count = 0

    # Missing-for-track (same-run, per just-published brand code) — most urgent,
    # list first so a silent partial publish is the headline.
    if issues.get('missing_for_track'):
        lines.append("🚨 PARTIAL PUBLISH (just-published track missing from folder(s)):")
        for code, folders in issues['missing_for_track'].items():
            issue_count += 1
            lines.append(f"  • {code}: missing from {', '.join(folders)}")
        lines.append("")

    # Duplicates
    if issues['duplicates']:
        lines.append("DUPLICATES:")
        for folder, dupes in issues['duplicates'].items():
            for seq_num, filenames in dupes.items():
                issue_count += 1
                lines.append(f"  • {folder}: NOMAD-{seq_num:04d} appears {len(filenames)} times")
                for f in filenames[:3]:  # Limit to 3 examples
                    lines.append(f"    - {f}")
                if len(filenames) > 3:
                    lines.append(f"    - ...and {len(filenames) - 3} more")
        lines.append("")
    
    # Invalid filenames
    if issues['invalid_filenames']:
        lines.append("INVALID FILENAMES:")
        for folder, filenames in issues['invalid_filenames'].items():
            for f in filenames[:5]:  # Limit to 5 examples
                issue_count += 1
                lines.append(f"  • {folder}: {f}")
            if len(filenames) > 5:
                lines.append(f"  • ...and {len(filenames) - 5} more in {folder}")
        lines.append("")
    
    # Gaps (only show if not too many)
    if issues['gaps']:
        lines.append("SEQUENCE GAPS:")
        for folder, missing in issues['gaps'].items():
            if len(missing) <= 10:
                issue_count += len(missing)
                lines.append(f"  • {folder}: missing {', '.join(map(str, missing))}")
            else:
                issue_count += len(missing)
                lines.append(f"  • {folder}: {len(missing)} gaps (first 5: {', '.join(map(str, missing[:5]))}...)")
        lines.append("")
    
    # Summary
    summary = issues['summary']
    lines.append(f"Checked: {summary.get('mp4', 0)} MP4, {summary.get('mp4_720p', 0)} 720p, {summary.get('cdg', 0)} CDG files")
    
    body = f"{issue_count} issue(s) detected:\n\n" + "\n".join(lines)
    
    return title, body


def send_email_notification(subject: str, body: str) -> bool:
    """Send a notification via Postmark email API."""
    if not POSTMARK_SERVER_TOKEN:
        logger.warning("POSTMARK_SERVER_TOKEN not set, skipping email notification")
        return False

    try:
        # Convert plain text body to simple HTML (preserve whitespace/newlines)
        html_body = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html_body = html_body.replace("\n", "<br>\n")
        html_body = f"<pre style='font-family: monospace; white-space: pre-wrap;'>{html_body}</pre>"

        response = requests.post(
            "https://api.postmarkapp.com/email",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "X-Postmark-Server-Token": POSTMARK_SERVER_TOKEN,
            },
            json={
                "From": f'"Nomad Karaoke GDrive Validator" <{EMAIL_FROM}>',
                "To": EMAIL_TO,
                "Subject": subject,
                "HtmlBody": html_body,
                "TextBody": body,
                "MessageStream": "outbound",
            },
            timeout=10,
        )

        if 200 <= response.status_code < 300:
            logger.info(f"Email notification sent to {EMAIL_TO} via Postmark")
            return True

        try:
            err = response.json()
            logger.error(
                f"Postmark returned status {response.status_code}: "
                f"ErrorCode={err.get('ErrorCode')} Message={err.get('Message')}"
            )
        except ValueError:
            logger.error(f"Postmark returned status {response.status_code}: {response.text[:200]}")
        return False

    except Exception:
        logger.exception("Failed to send email notification via Postmark")
        return False


def send_pushbullet_notification(title: str, body: str) -> bool:
    """Send a notification via Pushbullet API (secondary fallback)."""
    if not PUSHBULLET_API_KEY:
        logger.warning("PUSHBULLET_API_KEY not set, skipping Pushbullet notification")
        return False

    try:
        response = requests.post(
            "https://api.pushbullet.com/v2/pushes",
            headers={
                "Access-Token": PUSHBULLET_API_KEY,
                "Content-Type": "application/json"
            },
            json={
                "type": "note",
                "title": title,
                "body": body
            },
            timeout=30
        )
        response.raise_for_status()
        logger.info("Pushbullet notification sent successfully")
        return True
    except requests.RequestException:
        logger.exception("Failed to send Pushbullet notification")
        return False


def send_notification(title: str, body: str) -> bool:
    """Send notification via email (primary) and Pushbullet (fallback). Returns True if any succeeded."""
    email_sent = send_email_notification(title, body)
    pushbullet_sent = send_pushbullet_notification(title, body)
    return email_sent or pushbullet_sent


@functions_framework.http
def validate_gdrive(request):
    """
    HTTP Cloud Function entry point.
    
    Validates the Google Drive folder and sends notifications if issues are found.
    
    Returns JSON with validation results.
    """
    logger.info("Starting Google Drive validation...")

    # Incident-hardening D2: the post-job trigger may name the just-published
    # track so we can verify *its own* completeness same-run. Accept brand_code
    # from the JSON body or a query param; absent → full-scan behaviour as before.
    brand_code = None
    try:
        body = request.get_json(silent=True) if hasattr(request, "get_json") else None
        if isinstance(body, dict):
            brand_code = body.get("brand_code")
        if not brand_code and hasattr(request, "args"):
            brand_code = request.args.get("brand_code")
    except Exception:  # noqa: BLE001 - request parsing must not break validation
        brand_code = None

    try:
        # Get Drive service
        service = get_drive_service()

        # List all files
        files_by_folder = list_folder_contents(service, GDRIVE_FOLDER_ID)

        if not files_by_folder:
            logger.warning("No folders found in Google Drive")
            return json.dumps({
                "status": "warning",
                "message": "No folders found in Google Drive folder"
            }), 200, {"Content-Type": "application/json"}

        # Validate files
        issues = validate_files(files_by_folder)

        # Targeted same-run completeness for the just-published track.
        if brand_code:
            missing_from = check_track_completeness(files_by_folder, brand_code)
            if missing_from:
                issues['missing_for_track'] = {str(brand_code): missing_from}
                logger.warning(
                    "Track %s missing from folder(s): %s", brand_code, missing_from
                )

        # Check if there are any issues
        if has_issues(issues):
            logger.warning(f"Validation issues found: {issues}")

            title, body = format_notification(issues)
            notification_sent = send_notification(title, body)

            return json.dumps({
                "status": "issues_found",
                "issues": issues,
                "notification_sent": notification_sent
            }), 200, {"Content-Type": "application/json"}
        else:
            logger.info("No validation issues found")
            summary = issues['summary']

            # Send daily summary notification if enabled
            if NOTIFY_ON_SUCCESS:
                title = "✅ Karaoke GDrive: All Clear"
                body = (
                    f"Daily validation complete - no issues found.\n\n"
                    f"📊 Files checked:\n"
                    f"  • {summary.get('mp4', 0):,} MP4 tracks\n"
                    f"  • {summary.get('mp4_720p', 0):,} MP4-720p tracks\n"
                    f"  • {summary.get('cdg', 0):,} CDG tracks\n"
                    f"  • {summary.get('total', 0):,} total files\n\n"
                    f"✓ No duplicates\n"
                    f"✓ No invalid filenames\n"
                    f"✓ No sequence gaps"
                )
                notification_sent = send_notification(title, body)
            else:
                notification_sent = False

            return json.dumps({
                "status": "ok",
                "message": "No validation issues found",
                "summary": summary,
                "notification_sent": notification_sent
            }), 200, {"Content-Type": "application/json"}
    
    except Exception as e:
        logger.exception("Error during validation")
        
        # Try to send error notification
        try:
            send_notification(
                "❌ Karaoke GDrive Validator Error",
                f"Validation failed with error:\n\n{e!s}"
            )
        except Exception as notify_err:
            logger.warning("Failed to send error notification: %s", notify_err)
        
        return json.dumps({
            "status": "error",
            "message": str(e)
        }), 500, {"Content-Type": "application/json"}


# For local testing
if __name__ == "__main__":
    import sys
    
    # Set up test environment
    os.environ.setdefault("GDRIVE_FOLDER_ID", "1laRKAyxo0v817SstfM5XkpbWiNKNAMSX")
    
    print("Testing Google Drive validation...")
    
    # Create a mock request
    class MockRequest:
        pass
    
    result = validate_gdrive(MockRequest())
    print(f"\nResult: {result}")

