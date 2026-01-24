import os
import sys
import json
import shlex
import logging
import zipfile
import shutil
import re
import requests
import pickle
from lyrics_converter import LyricsConverter
from thefuzz import fuzz
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload
import subprocess
import time
from google.oauth2.credentials import Credentials
import base64
from email.mime.text import MIMEText
from karaoke_gen.lyrics_transcriber.output.cdg import CDGGenerator
from ..utils import sanitize_filename


class KaraokeFinalise:
    def __init__(
        self,
        logger=None,
        log_level=logging.DEBUG,
        log_formatter=None,
        dry_run=False,
        instrumental_format="flac",
        enable_cdg=False,
        enable_txt=False,
        brand_prefix=None,
        organised_dir=None,
        organised_dir_rclone_root=None,
        public_share_dir=None,
        youtube_client_secrets_file=None,
        youtube_description_file=None,
        rclone_destination=None,
        discord_webhook_url=None,
        email_template_file=None,
        cdg_styles=None,
        keep_brand_code=False,
        non_interactive=False,
        user_youtube_credentials=None,  # Add support for pre-stored credentials
        server_side_mode=False,  # New parameter for server-side deployment
        selected_instrumental_file=None,  # Add support for pre-selected instrumental file
        countdown_padding_seconds=None,  # Padding applied to vocals; instrumental must match
        no_video=False,  # Skip video encoding and distribution
    ):
        self.log_level = log_level
        self.log_formatter = log_formatter

        if logger is None:
            self.logger = logging.getLogger(__name__)
            self.logger.setLevel(log_level)
            # Prevent log propagation to root logger to avoid duplicate logs
            # when external packages (like lyrics_converter) configure root logger handlers
            self.logger.propagate = False

            self.log_handler = logging.StreamHandler()

            if self.log_formatter is None:
                self.log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(module)s - %(message)s")

            self.log_handler.setFormatter(self.log_formatter)
            self.logger.addHandler(self.log_handler)
        else:
            self.logger = logger

        self.logger.debug(
            f"KaraokeFinalise instantiating, dry_run: {dry_run}, brand_prefix: {brand_prefix}, organised_dir: {organised_dir}, public_share_dir: {public_share_dir}, rclone_destination: {rclone_destination}"
        )

        # Path to the Windows PyInstaller frozen bundled ffmpeg.exe, or the system-installed FFmpeg binary on Mac/Linux
        ffmpeg_path = os.path.join(sys._MEIPASS, "ffmpeg.exe") if getattr(sys, "frozen", False) else "ffmpeg"

        self.ffmpeg_base_command = f"{ffmpeg_path} -hide_banner -nostats"

        if self.log_level == logging.DEBUG:
            self.ffmpeg_base_command += " -loglevel verbose"
        else:
            self.ffmpeg_base_command += " -loglevel fatal"

        self.dry_run = dry_run
        self.instrumental_format = instrumental_format

        self.brand_prefix = brand_prefix
        self.organised_dir = organised_dir
        self.organised_dir_rclone_root = organised_dir_rclone_root

        self.public_share_dir = public_share_dir
        self.youtube_client_secrets_file = youtube_client_secrets_file
        self.youtube_description_file = youtube_description_file
        self.rclone_destination = rclone_destination
        self.discord_webhook_url = discord_webhook_url
        self.enable_cdg = enable_cdg
        self.enable_txt = enable_txt

        self.youtube_upload_enabled = False
        self.discord_notication_enabled = False
        self.folder_organisation_enabled = False
        self.public_share_copy_enabled = False
        self.public_share_rclone_enabled = False

        self.skip_notifications = False
        self.non_interactive = non_interactive
        self.user_youtube_credentials = user_youtube_credentials
        self.server_side_mode = server_side_mode
        self.selected_instrumental_file = selected_instrumental_file
        self.countdown_padding_seconds = countdown_padding_seconds
        self.no_video = no_video

        self.suffixes = {
            "title_mov": " (Title).mov",
            "title_jpg": " (Title).jpg",
            "end_mov": " (End).mov",
            "end_jpg": " (End).jpg",
            "with_vocals_mov": " (With Vocals).mov",
            "with_vocals_mp4": " (With Vocals).mp4",
            "with_vocals_mkv": " (With Vocals).mkv",
            "karaoke_lrc": " (Karaoke).lrc",
            "karaoke_txt": " (Karaoke).txt",
            "karaoke_mp4": " (Karaoke).mp4",
            "karaoke_cdg": " (Karaoke).cdg",
            "karaoke_mp3": " (Karaoke).mp3",
            "final_karaoke_lossless_mp4": " (Final Karaoke Lossless 4k).mp4",
            "final_karaoke_lossless_mkv": " (Final Karaoke Lossless 4k).mkv",
            "final_karaoke_lossy_mp4": " (Final Karaoke Lossy 4k).mp4",
            "final_karaoke_lossy_720p_mp4": " (Final Karaoke Lossy 720p).mp4",
            "final_karaoke_cdg_zip": " (Final Karaoke CDG).zip",
            "final_karaoke_txt_zip": " (Final Karaoke TXT).zip",
        }

        self.youtube_url_prefix = "https://www.youtube.com/watch?v="

        self.youtube_url = None
        self.brand_code = None
        self.new_brand_code_dir = None
        self.new_brand_code_dir_path = None
        self.brand_code_dir_sharing_link = None

        self.email_template_file = email_template_file
        self.gmail_service = None

        self.cdg_styles = cdg_styles

        # Determine best available AAC codec
        self.aac_codec = self.detect_best_aac_codec()

        self.keep_brand_code = keep_brand_code

        # MP4 output flags for better compatibility and streaming
        self.mp4_flags = "-pix_fmt yuv420p -movflags +faststart+frag_keyframe+empty_moov"

        # Update ffmpeg base command to include -y if non-interactive
        if self.non_interactive:
            self.ffmpeg_base_command += " -y"

        # Detect and configure hardware acceleration
        # TODO: Re-enable this once we figure out why the resulting MP4s are 10x larger than when encoded with x264...
        self.nvenc_available = False # self.detect_nvenc_support()
        self.configure_hardware_acceleration()

    def check_input_files_exist(self, base_name, with_vocals_file, instrumental_audio_file):
        self.logger.info(f"Checking required input files exist...")

        input_files = {
            "instrumental_audio": instrumental_audio_file,
        }

        # Video files are only required when not in --no-video mode
        if not self.no_video:
            input_files["title_mov"] = f"{base_name}{self.suffixes['title_mov']}"
            input_files["title_jpg"] = f"{base_name}{self.suffixes['title_jpg']}"
            input_files["with_vocals_mov"] = with_vocals_file

        optional_input_files = {
            "end_mov": f"{base_name}{self.suffixes['end_mov']}",
            "end_jpg": f"{base_name}{self.suffixes['end_jpg']}",
        }

        if self.enable_cdg or self.enable_txt:
            input_files["karaoke_lrc"] = f"{base_name}{self.suffixes['karaoke_lrc']}"

        for key, file_path in input_files.items():
            if not os.path.isfile(file_path):
                raise Exception(f"Input file {key} not found: {file_path}")

            self.logger.info(f" Input file {key} found: {file_path}")

        for key, file_path in optional_input_files.items():
            if not os.path.isfile(file_path):
                self.logger.info(f" Optional input file {key} not found: {file_path}")
            else:
                self.logger.info(f" Input file {key} found, adding to input_files: {file_path}")
                input_files[key] = file_path

        return input_files

    def prepare_output_filenames(self, base_name):
        output_files = {
            "karaoke_mp4": f"{base_name}{self.suffixes['karaoke_mp4']}",
            "karaoke_mp3": f"{base_name}{self.suffixes['karaoke_mp3']}",
            "karaoke_cdg": f"{base_name}{self.suffixes['karaoke_cdg']}",
            "with_vocals_mp4": f"{base_name}{self.suffixes['with_vocals_mp4']}",
            "final_karaoke_lossless_mp4": f"{base_name}{self.suffixes['final_karaoke_lossless_mp4']}",
            "final_karaoke_lossless_mkv": f"{base_name}{self.suffixes['final_karaoke_lossless_mkv']}",
            "final_karaoke_lossy_mp4": f"{base_name}{self.suffixes['final_karaoke_lossy_mp4']}",
            "final_karaoke_lossy_720p_mp4": f"{base_name}{self.suffixes['final_karaoke_lossy_720p_mp4']}",
        }

        if self.enable_cdg:
            output_files["final_karaoke_cdg_zip"] = f"{base_name}{self.suffixes['final_karaoke_cdg_zip']}"

        if self.enable_txt:
            output_files["karaoke_txt"] = f"{base_name}{self.suffixes['karaoke_txt']}"
            output_files["final_karaoke_txt_zip"] = f"{base_name}{self.suffixes['final_karaoke_txt_zip']}"

        return output_files

    def prompt_user_confirmation_or_raise_exception(self, prompt_message, exit_message, allow_empty=False):
        if self.non_interactive:
            self.logger.info(f"Non-interactive mode, automatically confirming: {prompt_message}")
            return True

        if not self.prompt_user_bool(prompt_message, allow_empty=allow_empty):
            self.logger.error(exit_message)
            raise Exception(exit_message)

    def prompt_user_bool(self, prompt_message, allow_empty=False):
        if self.non_interactive:
            self.logger.info(f"Non-interactive mode, automatically answering yes to: {prompt_message}")
            return True

        options_string = "[y]/n" if allow_empty else "y/[n]"
        accept_responses = ["y", "yes"]
        if allow_empty:
            accept_responses.append("")

        print()
        response = input(f"{prompt_message} {options_string} ").strip().lower()
        return response in accept_responses

    def validate_input_parameters_for_features(self):
        self.logger.info(f"Validating input parameters for enabled features...")

        current_directory = os.getcwd()
        self.logger.info(f"Current directory to process: {current_directory}")

        # Enable youtube upload if client secrets file is provided and is valid JSON
        if self.youtube_client_secrets_file is not None and self.youtube_description_file is not None:
            if not os.path.isfile(self.youtube_client_secrets_file):
                raise Exception(f"YouTube client secrets file does not exist: {self.youtube_client_secrets_file}")

            if not os.path.isfile(self.youtube_description_file):
                raise Exception(f"YouTube description file does not exist: {self.youtube_description_file}")

            # Test parsing the file as JSON to check it's valid
            try:
                with open(self.youtube_client_secrets_file, "r") as f:
                    json.load(f)
            except json.JSONDecodeError as e:
                raise Exception(f"YouTube client secrets file is not valid JSON: {self.youtube_client_secrets_file}") from e

            self.logger.debug(f"YouTube upload checks passed, enabling YouTube upload")
            self.youtube_upload_enabled = True
        
        # Also enable YouTube upload if pre-stored credentials are provided (server-side mode)
        elif self.user_youtube_credentials is not None and self.youtube_description_file is not None:
            if not os.path.isfile(self.youtube_description_file):
                raise Exception(f"YouTube description file does not exist: {self.youtube_description_file}")
            
            self.logger.debug(f"Pre-stored YouTube credentials provided, enabling YouTube upload")
            self.youtube_upload_enabled = True

        # Enable discord notifications if webhook URL is provided and is valid URL
        if self.discord_webhook_url is not None:
            # Strip whitespace/newlines that may have been introduced from environment variables or secrets
            self.discord_webhook_url = self.discord_webhook_url.strip()
            if not self.discord_webhook_url.startswith("https://discord.com/api/webhooks/"):
                raise Exception(f"Discord webhook URL is not valid: {self.discord_webhook_url}")

            self.logger.debug(f"Discord webhook URL checks passed, enabling Discord notifications")
            self.discord_notication_enabled = True

        # Enable folder organisation if brand prefix and target directory are provided and target directory is valid
        # In server-side mode, we skip the local folder organization but may still need brand codes
        if self.brand_prefix is not None and self.organised_dir is not None:
            if not self.server_side_mode and not os.path.isdir(self.organised_dir):
                raise Exception(f"Target directory does not exist: {self.organised_dir}")

            if not self.server_side_mode:
                self.logger.debug(f"Brand prefix and target directory provided, enabling local folder organisation")
                self.folder_organisation_enabled = True
            else:
                self.logger.debug(f"Server-side mode: brand prefix provided for remote organization")
                self.folder_organisation_enabled = False  # Disable local folder organization in server mode

        # Enable public share copy if public share directory is provided and is valid directory with MP4 and CDG subdirectories
        if self.public_share_dir is not None:
            if not os.path.isdir(self.public_share_dir):
                raise Exception(f"Public share directory does not exist: {self.public_share_dir}")

            if not os.path.isdir(os.path.join(self.public_share_dir, "MP4")):
                raise Exception(f"Public share directory does not contain MP4 subdirectory: {self.public_share_dir}")

            if not os.path.isdir(os.path.join(self.public_share_dir, "CDG")):
                raise Exception(f"Public share directory does not contain CDG subdirectory: {self.public_share_dir}")

            self.logger.debug(f"Public share directory checks passed, enabling public share copy")
            self.public_share_copy_enabled = True

        # Enable public share rclone if rclone destination is provided
        if self.rclone_destination is not None:
            self.logger.debug(f"Rclone destination provided, enabling rclone sync")
            self.public_share_rclone_enabled = True

        # Tell user which features are enabled, prompt them to confirm before proceeding
        self.logger.info(f"Enabled features:")
        self.logger.info(f" CDG ZIP creation: {self.enable_cdg}")
        self.logger.info(f" TXT ZIP creation: {self.enable_txt}")
        self.logger.info(f" YouTube upload: {self.youtube_upload_enabled}")
        self.logger.info(f" Discord notifications: {self.discord_notication_enabled}")
        self.logger.info(f" Folder organisation: {self.folder_organisation_enabled}")
        self.logger.info(f" Public share copy: {self.public_share_copy_enabled}")
        self.logger.info(f" Public share rclone: {self.public_share_rclone_enabled}")

        # Skip user confirmation in non-interactive mode for Modal deployment
        if not self.non_interactive:
            self.prompt_user_confirmation_or_raise_exception(
                f"Confirm features enabled log messages above match your expectations for finalisation?",
                "Refusing to proceed without user confirmation they're happy with enabled features.",
                allow_empty=True,
            )
        else:
            self.logger.info("Non-interactive mode: automatically confirming enabled features")

    def authenticate_youtube(self):
        """Authenticate with YouTube and return service object."""
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from google_auth_oauthlib.flow import InstalledAppFlow
        import pickle
        import os

        # Check if we have pre-stored credentials (for non-interactive mode)
        if self.user_youtube_credentials and self.non_interactive:
            try:
                # Create credentials object from stored data
                credentials = Credentials(
                    token=self.user_youtube_credentials['token'],
                    refresh_token=self.user_youtube_credentials.get('refresh_token'),
                    token_uri=self.user_youtube_credentials.get('token_uri'),
                    client_id=self.user_youtube_credentials.get('client_id'),
                    client_secret=self.user_youtube_credentials.get('client_secret'),
                    scopes=self.user_youtube_credentials.get('scopes')
                )
                
                # Refresh token if needed
                if credentials.expired and credentials.refresh_token:
                    credentials.refresh(Request())
                
                # Build YouTube service with credentials
                youtube = build('youtube', 'v3', credentials=credentials)
                self.logger.info("Successfully authenticated with YouTube using pre-stored credentials")
                return youtube
                
            except Exception as e:
                self.logger.error(f"Failed to authenticate with pre-stored credentials: {str(e)}")
                # Fall through to original authentication if pre-stored credentials fail
        
        # Original authentication code for interactive mode
        if self.non_interactive:
            raise Exception("YouTube authentication required but running in non-interactive mode. Please pre-authenticate or disable YouTube upload.")
        
        # Token file stores the user's access and refresh tokens for YouTube.
        youtube_token_file = "/tmp/karaoke-finalise-youtube-token.pickle"
        
        credentials = None
        
        # Check if we have saved credentials
        if os.path.exists(youtube_token_file):
            with open(youtube_token_file, "rb") as token:
                credentials = pickle.load(token)

        # If there are no valid credentials, let the user log in.
        if not credentials or not credentials.valid:
            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())
            else:
                if self.non_interactive:
                    raise Exception("YouTube authentication required but running in non-interactive mode. Please pre-authenticate or disable YouTube upload.")
                    
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.youtube_client_secrets_file, scopes=["https://www.googleapis.com/auth/youtube"]
                )
                credentials = flow.run_local_server(port=0)  # This will open a browser for authentication

            # Save the credentials for the next run
            with open(youtube_token_file, "wb") as token:
                pickle.dump(credentials, token)

        return build("youtube", "v3", credentials=credentials)

    def get_channel_id(self):
        youtube = self.authenticate_youtube()

        # Get the authenticated user's channel
        request = youtube.channels().list(part="snippet", mine=True)
        response = request.execute()

        # Extract the channel ID
        if "items" in response:
            channel_id = response["items"][0]["id"]
            return channel_id
        else:
            return None

    def check_if_video_title_exists_on_youtube_channel(self, youtube_title):
        youtube = self.authenticate_youtube()
        channel_id = self.get_channel_id()

        self.logger.info(f"Searching YouTube channel {channel_id} for title: {youtube_title}")
        request = youtube.search().list(part="snippet", channelId=channel_id, q=youtube_title, type="video", maxResults=10)
        response = request.execute()

        # Check if any videos were found
        if "items" in response and len(response["items"]) > 0:
            for item in response["items"]:
                # YouTube search API sometimes returns results from other channels even with channelId filter
                # Verify the video actually belongs to our channel
                result_channel_id = item["snippet"]["channelId"]
                if result_channel_id != channel_id:
                    self.logger.debug(
                        f"Skipping video from different channel: {item['snippet']['title']} (channel: {result_channel_id})"
                    )
                    continue

                found_title = item["snippet"]["title"]

                # In server-side mode, require an exact match to avoid false positives.
                # Otherwise, use fuzzy matching for interactive CLI usage.
                if self.server_side_mode:
                    is_match = youtube_title.lower() == found_title.lower()
                    similarity_score = 100 if is_match else 0
                else:
                    similarity_score = fuzz.ratio(youtube_title.lower(), found_title.lower())
                    is_match = similarity_score >= 70

                if is_match:
                    found_id = item["id"]["videoId"]
                    self.logger.info(
                        f"Potential match found on YouTube channel with ID: {found_id} and title: {found_title} (similarity: {similarity_score}%)"
                    )

                    # In non-interactive mode (server mode), we don't prompt. Just record the match and return.
                    if self.non_interactive:
                        self.logger.info(f"Non-interactive mode, found a match.")
                        self.youtube_video_id = found_id
                        self.youtube_url = f"{self.youtube_url_prefix}{self.youtube_video_id}"
                        return True

                    confirmation = input(f"Is '{found_title}' the video you are finalising? (y/n): ").strip().lower()
                    if confirmation == "y":
                        self.youtube_video_id = found_id
                        self.youtube_url = f"{self.youtube_url_prefix}{self.youtube_video_id}"
                        return True

        self.logger.info(f"No matching video found with title: {youtube_title}")
        return False

    def delete_youtube_video(self, video_id):
        """
        Delete a YouTube video by its ID.
        
        Args:
            video_id: The YouTube video ID to delete
            
        Returns:
            True if successful, False otherwise
        """
        self.logger.info(f"Deleting YouTube video with ID: {video_id}")
        
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would delete YouTube video with ID: {video_id}")
            return True
            
        try:
            youtube = self.authenticate_youtube()
            youtube.videos().delete(id=video_id).execute()
            self.logger.info(f"Successfully deleted YouTube video with ID: {video_id}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete YouTube video with ID {video_id}: {e}")
            return False

    def truncate_to_nearest_word(self, title, max_length):
        if len(title) <= max_length:
            return title
        truncated_title = title[:max_length].rsplit(" ", 1)[0]
        if len(truncated_title) < max_length:
            truncated_title += " ..."
        return truncated_title

    def upload_final_mp4_to_youtube_with_title_thumbnail(self, artist, title, input_files, output_files, replace_existing=False):
        self.logger.info(f"Uploading final MKV to YouTube with title thumbnail...")
        if self.dry_run:
            self.logger.info(
                f'DRY RUN: Would upload {output_files["final_karaoke_lossless_mkv"]} to YouTube with thumbnail {input_files["title_jpg"]} using client secrets file: {self.youtube_client_secrets_file}'
            )
        else:
            youtube_title = f"{artist} - {title} (Karaoke)"

            # Truncate title to the nearest whole word and add ellipsis if needed
            max_length = 95
            youtube_title = self.truncate_to_nearest_word(youtube_title, max_length)

            # In server-side mode, we should always replace videos if an exact match is found.
            # Otherwise, respect the replace_existing flag from CLI.
            should_replace = True if self.server_side_mode else replace_existing

            if self.check_if_video_title_exists_on_youtube_channel(youtube_title):
                if should_replace:
                    self.logger.info(f"Video already exists on YouTube, deleting before re-upload: {self.youtube_url}")
                    if self.delete_youtube_video(self.youtube_video_id):
                        self.logger.info(f"Successfully deleted existing video, proceeding with upload")
                        # Reset the video ID and URL since we're uploading a new one
                        self.youtube_video_id = None
                        self.youtube_url = None
                    else:
                        self.logger.error(f"Failed to delete existing video, aborting upload")
                        return
                else:
                    self.logger.warning(f"Video already exists on YouTube, skipping upload: {self.youtube_url}")
                    return

            youtube_description = f"Karaoke version of {artist} - {title} created using karaoke-gen python package."
            if self.youtube_description_file is not None:
                with open(self.youtube_description_file, "r") as f:
                    youtube_description = f.read()

            youtube_category_id = "10"  # Category ID for Music
            youtube_keywords = ["karaoke", "music", "singing", "instrumental", "lyrics", artist, title]

            self.logger.info(f"Authenticating with YouTube...")
            # Upload video to YouTube and set thumbnail.
            youtube = self.authenticate_youtube()

            body = {
                "snippet": {
                    "title": youtube_title,
                    "description": youtube_description,
                    "tags": youtube_keywords,
                    "categoryId": youtube_category_id,
                },
                "status": {"privacyStatus": "public"},
            }

            # Use MediaFileUpload to handle the video file - using the MKV with FLAC audio
            media_file = MediaFileUpload(output_files["final_karaoke_lossless_mkv"], mimetype="video/x-matroska", resumable=True)

            # Call the API's videos.insert method to create and upload the video.
            self.logger.info(f"Uploading final MKV to YouTube...")
            request = youtube.videos().insert(part="snippet,status", body=body, media_body=media_file)
            response = request.execute()

            self.youtube_video_id = response.get("id")
            self.youtube_url = f"{self.youtube_url_prefix}{self.youtube_video_id}"
            self.logger.info(f"Uploaded video to YouTube: {self.youtube_url}")

            # Uploading the thumbnail
            if input_files.get("title_jpg") and os.path.isfile(input_files["title_jpg"]):
                try:
                    self.logger.info(f"Uploading thumbnail from: {input_files['title_jpg']}")
                    media_thumbnail = MediaFileUpload(input_files["title_jpg"], mimetype="image/jpeg")
                    youtube.thumbnails().set(videoId=self.youtube_video_id, media_body=media_thumbnail).execute()
                    self.logger.info(f"Uploaded thumbnail for video ID {self.youtube_video_id}")
                except Exception as e:
                    self.logger.error(f"Failed to upload thumbnail: {e}")
                    self.logger.warning("Video uploaded but thumbnail not set. You may need to set it manually on YouTube.")
            else:
                self.logger.warning(f"Thumbnail file not found, skipping thumbnail upload: {input_files.get('title_jpg')}")

    def get_next_brand_code(self):
        """
        Calculate the next sequence number based on existing directories in the organised_dir.
        Fills gaps in the sequence for numbers >= 1001 (preserves legacy gaps below 1000).
        Assumes directories are named with the format: BRAND-XXXX Artist - Title
        """
        pattern = re.compile(rf"^{re.escape(self.brand_prefix)}-(\d{{4}})")

        if not os.path.isdir(self.organised_dir):
            raise Exception(f"Target directory does not exist: {self.organised_dir}")

        existing_nums: set[int] = set()
        for dir_name in os.listdir(self.organised_dir):
            match = pattern.match(dir_name)
            if match:
                existing_nums.add(int(match.group(1)))

        max_existing = max(existing_nums) if existing_nums else 0

        # Only fill gaps for numbers >= 1001 (preserve legacy gaps below 1000)
        if max_existing >= 1000:
            next_seq_number = 1001
            while next_seq_number in existing_nums:
                next_seq_number += 1
        else:
            next_seq_number = max_existing + 1

        if max_existing >= 1000 and next_seq_number <= max_existing:
            self.logger.info(f"Next sequence number for brand {self.brand_prefix}: {next_seq_number} (filling gap, max existing: {max_existing})")
        else:
            self.logger.info(f"Next sequence number for brand {self.brand_prefix}: {next_seq_number} (max existing: {max_existing})")

        return f"{self.brand_prefix}-{next_seq_number:04d}"

    def post_discord_message(self, message, webhook_url):
        """Post a message to a Discord channel via webhook."""
        data = {"content": message}
        response = requests.post(webhook_url, json=data)
        response.raise_for_status()  # This will raise an exception if the request failed
        self.logger.info("Message posted to Discord")

    def find_with_vocals_file(self):
        self.logger.info("Finding input file ending in (With Vocals).mov/.mp4/.mkv or (Karaoke).mov/.mp4/.mkv")

        # Define all possible suffixes for with vocals files
        with_vocals_suffixes = [
            self.suffixes["with_vocals_mov"],
            self.suffixes["with_vocals_mp4"],
            self.suffixes["with_vocals_mkv"],
        ]

        # First try to find a properly named with vocals file in any supported format
        with_vocals_files = [f for f in os.listdir(".") if any(f.endswith(suffix) for suffix in with_vocals_suffixes)]

        if with_vocals_files:
            self.logger.info(f"Found with vocals file: {with_vocals_files[0]}")
            return with_vocals_files[0]

        # If no with vocals file found, look for potentially misnamed karaoke files
        karaoke_suffixes = [" (Karaoke).mov", " (Karaoke).mp4", " (Karaoke).mkv"]
        karaoke_files = [f for f in os.listdir(".") if any(f.endswith(suffix) for suffix in karaoke_suffixes)]

        if karaoke_files:
            for file in karaoke_files:
                # Get the current extension
                current_ext = os.path.splitext(file)[1].lower()  # Convert to lowercase
                base_without_suffix = file.replace(f" (Karaoke){current_ext}", "")

                # Map file extension to suffix dictionary key
                ext_to_suffix = {".mov": "with_vocals_mov", ".mp4": "with_vocals_mp4", ".mkv": "with_vocals_mkv"}

                if current_ext in ext_to_suffix:
                    new_file = f"{base_without_suffix}{self.suffixes[ext_to_suffix[current_ext]]}"

                    self.prompt_user_confirmation_or_raise_exception(
                        f"Found '{file}' but no '(With Vocals)', rename to {new_file} for vocal input?",
                        "Unable to proceed without With Vocals file or user confirmation of rename.",
                        allow_empty=True,
                    )

                    os.rename(file, new_file)
                    self.logger.info(f"Renamed '{file}' to '{new_file}'")
                    return new_file
                else:
                    self.logger.warning(f"Unsupported file extension: {current_ext}")

        raise Exception(
            "No suitable files found for processing.\n"
            "\n"
            "WHAT THIS MEANS:\n"
            "The finalisation step requires a '(With Vocals).mkv' video file, which is created "
            "during the lyrics transcription phase. This file contains the karaoke video with "
            "synchronized lyrics overlay.\n"
            "\n"
            "COMMON CAUSES:\n"
            "1. Transcription provider not configured - No AUDIOSHAKE_API_TOKEN or RUNPOD_API_KEY set\n"
            "2. Transcription failed - Check logs above for API errors or timeout messages\n"
            "3. Invalid API credentials - Verify your API tokens are correct and active\n"
            "4. Network issues - Unable to reach transcription service\n"
            "5. Running in wrong directory - Make sure you're in the track output folder\n"
            "\n"
            "TROUBLESHOOTING STEPS:\n"
            "1. Check environment variables:\n"
            "   - AUDIOSHAKE_API_TOKEN (for AudioShake transcription)\n"
            "   - RUNPOD_API_KEY + WHISPER_RUNPOD_ID (for Whisper transcription)\n"
            "2. Review the log output above for transcription errors\n"
            "3. Try running with --log_level debug for more detailed output\n"
            "4. If you don't need synchronized lyrics, use --skip-lyrics for instrumental-only karaoke\n"
            "\n"
            "See README.md 'Transcription Providers' and 'Troubleshooting' sections for more details."
        )

    def choose_instrumental_audio_file(self, base_name):
        self.logger.info(f"Choosing instrumental audio file to use as karaoke audio...")

        search_string = " (Instrumental"
        self.logger.info(f"Searching for files in current directory containing {search_string}")

        all_instrumental_files = [f for f in os.listdir(".") if search_string in f]
        flac_files = set(f.rsplit(".", 1)[0] for f in all_instrumental_files if f.endswith(".flac"))
        mp3_files = set(f.rsplit(".", 1)[0] for f in all_instrumental_files if f.endswith(".mp3"))
        wav_files = set(f.rsplit(".", 1)[0] for f in all_instrumental_files if f.endswith(".wav"))

        self.logger.debug(f"FLAC files found: {flac_files}")
        self.logger.debug(f"MP3 files found: {mp3_files}")
        self.logger.debug(f"WAV files found: {wav_files}")

        # Filter out MP3 files if their FLAC or WAV counterpart exists
        # Filter out WAV files if their FLAC counterpart exists
        filtered_files = [
            f
            for f in all_instrumental_files
            if f.endswith(".flac")
            or (f.endswith(".wav") and f.rsplit(".", 1)[0] not in flac_files)
            or (f.endswith(".mp3") and f.rsplit(".", 1)[0] not in flac_files and f.rsplit(".", 1)[0] not in wav_files)
        ]

        self.logger.debug(f"Filtered instrumental files: {filtered_files}")

        if not filtered_files:
            raise Exception(f"No instrumental audio files found containing {search_string}")

        if len(filtered_files) == 1:
            return filtered_files[0]

        # In non-interactive mode, always choose the first option
        if self.non_interactive:
            self.logger.info(f"Non-interactive mode, automatically choosing first instrumental file: {filtered_files[0]}")
            return filtered_files[0]

        # Sort the remaining instrumental options alphabetically
        filtered_files.sort(reverse=True)

        self.logger.info(f"Found multiple files containing {search_string}:")
        for i, file in enumerate(filtered_files):
            self.logger.info(f" {i+1}: {file}")

        print()
        response = input(f"Choose instrumental audio file to use as karaoke audio: [1]/{len(filtered_files)}: ").strip().lower()
        if response == "":
            response = "1"

        try:
            response = int(response)
        except ValueError:
            raise Exception(f"Invalid response to instrumental audio file choice prompt: {response}")

        if response < 1 or response > len(filtered_files):
            raise Exception(f"Invalid response to instrumental audio file choice prompt: {response}")

        return filtered_files[response - 1]

    def get_names_from_withvocals(self, with_vocals_file):
        self.logger.info(f"Getting artist and title from {with_vocals_file}")

        # Remove both possible suffixes and their extensions
        base_name = with_vocals_file
        for suffix_key in ["with_vocals_mov", "with_vocals_mp4", "with_vocals_mkv"]:
            suffix = self.suffixes[suffix_key]
            if suffix in base_name:
                base_name = base_name.replace(suffix, "")
                break

        # If we didn't find a match above, try removing just the extension
        if base_name == with_vocals_file:
            base_name = os.path.splitext(base_name)[0]

        artist, title = base_name.split(" - ", 1)
        return base_name, artist, title

    def _pad_audio_file(self, input_audio, output_audio, padding_seconds):
        """
        Pad an audio file by prepending silence at the beginning.
        
        Uses the same ffmpeg approach as LyricsTranscriber's CountdownProcessor
        to ensure consistent padding behavior.
        
        Args:
            input_audio: Path to input audio file
            output_audio: Path for the padded output file
            padding_seconds: Amount of silence to prepend (in seconds)
        """
        self.logger.info(f"Padding audio file with {padding_seconds}s of silence")
        
        # Use ffmpeg to prepend silence - this matches the approach in audio_processor.py
        # adelay filter adds delay in milliseconds
        delay_ms = int(padding_seconds * 1000)
        
        ffmpeg_command = (
            f'{self.ffmpeg_base_command} -i "{input_audio}" '
            f'-af "adelay={delay_ms}|{delay_ms}" '
            f'"{output_audio}"'
        )
        
        self.execute_command(ffmpeg_command, f"Padding audio with {padding_seconds}s silence")

    def execute_command(self, command, description):
        """Execute a shell command and log the output. For general commands (rclone, etc.)"""
        self.logger.info(f"{description}")
        self.logger.debug(f"Executing command: {command}")
        
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would execute: {command}")
            return
        
        try:
            result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=600)
            
            # Log command output for debugging
            if result.stdout and result.stdout.strip():
                self.logger.debug(f"Command STDOUT: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                self.logger.debug(f"Command STDERR: {result.stderr.strip()}")
            
            if result.returncode != 0:
                error_msg = f"Command failed with exit code {result.returncode}"
                self.logger.error(error_msg)
                self.logger.error(f"Command: {command}")
                if result.stdout:
                    self.logger.error(f"STDOUT: {result.stdout}")
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                raise Exception(f"{error_msg}: {command}")
            else:
                self.logger.info(f"✓ Command completed successfully")
                
        except subprocess.TimeoutExpired:
            error_msg = f"Command timed out after 600 seconds"
            self.logger.error(error_msg)
            raise Exception(f"{error_msg}: {command}")
        except Exception as e:
            if "Command failed" not in str(e):
                error_msg = f"Command failed with exception: {e}"
                self.logger.error(error_msg)
                raise Exception(f"{error_msg}: {command}")
            else:
                raise

    def remux_with_instrumental(self, with_vocals_file, instrumental_audio, output_file):
        """Remux the video with instrumental audio to create karaoke version"""
        # Safety net: If countdown padding was applied to vocals, ensure instrumental is padded too
        actual_instrumental = instrumental_audio
        if self.countdown_padding_seconds and self.countdown_padding_seconds > 0:
            # Check if the instrumental file is already padded (has "(Padded)" in name)
            if "(Padded)" not in instrumental_audio:
                self.logger.warning(
                    f"Countdown padding ({self.countdown_padding_seconds}s) was applied to vocals, "
                    f"but instrumental doesn't appear to be padded. Creating padded version..."
                )
                # Create a padded version of the instrumental
                base, ext = os.path.splitext(instrumental_audio)
                padded_instrumental = f"{base} (Padded){ext}"
                
                if not os.path.exists(padded_instrumental):
                    self._pad_audio_file(instrumental_audio, padded_instrumental, self.countdown_padding_seconds)
                    self.logger.info(f"Created padded instrumental: {padded_instrumental}")
                
                actual_instrumental = padded_instrumental
            else:
                self.logger.info(f"Using already-padded instrumental: {instrumental_audio}")
        
        # This operation is primarily I/O bound (remuxing), so hardware acceleration doesn't provide significant benefit
        # Keep the existing approach but use the new execute method
        ffmpeg_command = (
            f'{self.ffmpeg_base_command} -an -i "{with_vocals_file}" '
            f'-vn -i "{actual_instrumental}" -c:v copy -c:a pcm_s16le "{output_file}"'
        )
        self.execute_command(ffmpeg_command, "Remuxing video with instrumental audio")

    def convert_mov_to_mp4(self, input_file, output_file):
        """Convert MOV file to MP4 format with hardware acceleration support"""
        # Hardware-accelerated version
        gpu_command = (
            f'{self.ffmpeg_base_command} {self.hwaccel_decode_flags} -i "{input_file}" '
            f'-c:v {self.video_encoder} {self.get_nvenc_quality_settings("high")} -c:a {self.aac_codec} -ar 48000 {self.mp4_flags} "{output_file}"'
        )
        
        # Software fallback version
        cpu_command = (
            f'{self.ffmpeg_base_command} -i "{input_file}" '
            f'-c:v libx264 -c:a {self.aac_codec} -ar 48000 {self.mp4_flags} "{output_file}"'
        )
        
        self.execute_command_with_fallback(gpu_command, cpu_command, "Converting MOV video to MP4")

    def encode_lossless_mp4(self, title_mov_file, karaoke_mp4_file, env_mov_input, ffmpeg_filter, output_file):
        """Create the final MP4 with PCM audio (lossless) using hardware acceleration when available"""
        # Hardware-accelerated version
        gpu_command = (
            f"{self.ffmpeg_base_command} {self.hwaccel_decode_flags} -i {title_mov_file} "
            f"{self.hwaccel_decode_flags} -i {karaoke_mp4_file} {env_mov_input} "
            f'{ffmpeg_filter} -map "[outv]" -map "[outa]" -c:v {self.video_encoder} '
            f'{self.get_nvenc_quality_settings("lossless")} -c:a pcm_s16le {self.mp4_flags} "{output_file}"'
        )
        
        # Software fallback version
        cpu_command = (
            f"{self.ffmpeg_base_command} -i {title_mov_file} -i {karaoke_mp4_file} {env_mov_input} "
            f'{ffmpeg_filter} -map "[outv]" -map "[outa]" -c:v libx264 -c:a pcm_s16le '
            f'{self.mp4_flags} "{output_file}"'
        )
        
        self.execute_command_with_fallback(gpu_command, cpu_command, "Creating MP4 version with PCM audio")

    def encode_lossy_mp4(self, input_file, output_file):
        """Create MP4 with AAC audio (lossy, for wider compatibility)"""
        # This is primarily an audio re-encoding operation, video is copied
        # Hardware acceleration doesn't provide significant benefit for copy operations
        ffmpeg_command = (
            f'{self.ffmpeg_base_command} -i "{input_file}" '
            f'-c:v copy -c:a {self.aac_codec} -ar 48000 -b:a 320k {self.mp4_flags} "{output_file}"'
        )
        self.execute_command(ffmpeg_command, "Creating MP4 version with AAC audio")

    def encode_lossless_mkv(self, input_file, output_file):
        """Create MKV with FLAC audio (for YouTube)"""
        # This is primarily an audio re-encoding operation, video is copied
        # Hardware acceleration doesn't provide significant benefit for copy operations
        ffmpeg_command = (
            f'{self.ffmpeg_base_command} -i "{input_file}" '
            f'-c:v copy -c:a flac "{output_file}"'
        )
        self.execute_command(ffmpeg_command, "Creating MKV version with FLAC audio for YouTube")

    def encode_720p_version(self, input_file, output_file):
        """Create 720p MP4 with AAC audio (for smaller file size) using hardware acceleration when available"""
        # Hardware-accelerated version with GPU scaling and encoding
        gpu_command = (
            f'{self.ffmpeg_base_command} {self.hwaccel_decode_flags} -i "{input_file}" '
            f'-c:v {self.video_encoder} -vf "{self.scale_filter}=1280:720" '
            f'{self.get_nvenc_quality_settings("medium")} -b:v 2000k '
            f'-c:a {self.aac_codec} -ar 48000 -b:a 128k {self.mp4_flags} "{output_file}"'
        )
        
        # Software fallback version
        cpu_command = (
            f'{self.ffmpeg_base_command} -i "{input_file}" '
            f'-c:v libx264 -vf "scale=1280:720" -b:v 2000k -preset medium -tune animation '
            f'-c:a {self.aac_codec} -ar 48000 -b:a 128k {self.mp4_flags} "{output_file}"'
        )
        
        self.execute_command_with_fallback(gpu_command, cpu_command, "Encoding 720p version of the final video")

    def prepare_concat_filter(self, input_files):
        """Prepare the concat filter and additional input for end credits if present"""
        env_mov_input = ""
        ffmpeg_filter = '-filter_complex "[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[outv][outa]"'

        if "end_mov" in input_files and os.path.isfile(input_files["end_mov"]):
            self.logger.info(f"Found end_mov file: {input_files['end_mov']}, including in final MP4")
            end_mov_file = shlex.quote(os.path.abspath(input_files["end_mov"]))
            env_mov_input = f"-i {end_mov_file}"
            ffmpeg_filter = '-filter_complex "[0:v:0][0:a:0][1:v:0][1:a:0][2:v:0][2:a:0]concat=n=3:v=1:a=1[outv][outa]"'

        return env_mov_input, ffmpeg_filter

    def remux_and_encode_output_video_files(self, with_vocals_file, input_files, output_files):
        self.logger.info(f"Remuxing and encoding output video files (4 formats, ~15-20 minutes total)...")

        # Check if output files already exist
        if os.path.isfile(output_files["final_karaoke_lossless_mp4"]) and os.path.isfile(output_files["final_karaoke_lossless_mkv"]):
            if not self.prompt_user_bool(
                f"Found existing Final Karaoke output files. Overwrite (y) or skip (n)?",
            ):
                self.logger.info(f"Skipping Karaoke MP4 remux and Final video renders, existing files will be used.")
                return

        # Create karaoke version with instrumental audio
        self.logger.info(f"[Step 1/6] Remuxing video with instrumental audio...")
        self.remux_with_instrumental(with_vocals_file, input_files["instrumental_audio"], output_files["karaoke_mp4"])

        # Convert the with vocals video to MP4 if needed
        if not with_vocals_file.endswith(".mp4"):
            self.logger.info(f"[Step 2/6] Converting karaoke video to MP4...")
            self.convert_mov_to_mp4(with_vocals_file, output_files["with_vocals_mp4"])

            # Delete the with vocals mov after successfully converting it to mp4
            if not self.dry_run and os.path.isfile(with_vocals_file):
                self.logger.info(f"Deleting with vocals MOV file: {with_vocals_file}")
                os.remove(with_vocals_file)
        else:
            self.logger.info(f"[Step 2/6] Skipped - video already in MP4 format")

        # Quote file paths to handle special characters
        title_mov_file = shlex.quote(os.path.abspath(input_files["title_mov"]))
        karaoke_mp4_file = shlex.quote(os.path.abspath(output_files["karaoke_mp4"]))

        # Prepare concat filter for combining videos
        env_mov_input, ffmpeg_filter = self.prepare_concat_filter(input_files)

        # Create all output versions with progress logging
        self.logger.info(f"[Step 3/6] Encoding lossless 4K MP4 (title + karaoke + end, ~5 minutes)...")
        self.encode_lossless_mp4(title_mov_file, karaoke_mp4_file, env_mov_input, ffmpeg_filter, output_files["final_karaoke_lossless_mp4"])
        
        self.logger.info(f"[Step 4/6] Encoding lossy 4K MP4 with AAC audio (~1 minute)...")
        self.encode_lossy_mp4(output_files["final_karaoke_lossless_mp4"], output_files["final_karaoke_lossy_mp4"])
        
        self.logger.info(f"[Step 5/6] Creating MKV with FLAC audio for YouTube (~1 minute)...")
        self.encode_lossless_mkv(output_files["final_karaoke_lossless_mp4"], output_files["final_karaoke_lossless_mkv"])
        
        self.logger.info(f"[Step 6/6] Encoding 720p version (~3 minutes)...")
        self.encode_720p_version(output_files["final_karaoke_lossless_mp4"], output_files["final_karaoke_lossy_720p_mp4"])

        # Skip user confirmation in non-interactive mode for Modal deployment
        if not self.non_interactive:
            # Prompt user to check final video files before proceeding
            self.prompt_user_confirmation_or_raise_exception(
                f"Final video files created:\n"
                f"- Lossless 4K MP4: {output_files['final_karaoke_lossless_mp4']}\n"
                f"- Lossless 4K MKV: {output_files['final_karaoke_lossless_mkv']}\n"
                f"- Lossy 4K MP4: {output_files['final_karaoke_lossy_mp4']}\n"
                f"- Lossy 720p MP4: {output_files['final_karaoke_lossy_720p_mp4']}\n"
                f"Please check them! Proceed?",
                "Refusing to proceed without user confirmation they're happy with the Final videos.",
                allow_empty=True,
            )
        else:
            self.logger.info("Non-interactive mode: automatically confirming final video files")

    def create_cdg_zip_file(self, input_files, output_files, artist, title):
        self.logger.info(f"Creating CDG and MP3 files, then zipping them...")

        # Check if CDG file already exists, if so, ask user to overwrite or skip
        if os.path.isfile(output_files["final_karaoke_cdg_zip"]):
            if not self.prompt_user_bool(
                f"Found existing CDG ZIP file: {output_files['final_karaoke_cdg_zip']}. Overwrite (y) or skip (n)?",
            ):
                self.logger.info(f"Skipping CDG ZIP file creation, existing file will be used.")
                return

        # Check if individual MP3 and CDG files already exist
        if os.path.isfile(output_files["karaoke_mp3"]) and os.path.isfile(output_files["karaoke_cdg"]):
            self.logger.info(f"Found existing MP3 and CDG files, creating ZIP file directly")
            if not self.dry_run:
                with zipfile.ZipFile(output_files["final_karaoke_cdg_zip"], "w") as zipf:
                    zipf.write(output_files["karaoke_mp3"], os.path.basename(output_files["karaoke_mp3"]))
                    zipf.write(output_files["karaoke_cdg"], os.path.basename(output_files["karaoke_cdg"]))
                self.logger.info(f"Created CDG ZIP file: {output_files['final_karaoke_cdg_zip']}")
            return

        # Generate CDG and MP3 files if they don't exist
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would generate CDG and MP3 files")
        else:
            self.logger.info(f"Generating CDG and MP3 files")

            if self.cdg_styles is None:
                raise ValueError("CDG styles configuration is required when enable_cdg is True")

            generator = CDGGenerator(output_dir=os.getcwd(), logger=self.logger)
            cdg_file, mp3_file, zip_file = generator.generate_cdg_from_lrc(
                lrc_file=input_files["karaoke_lrc"],
                audio_file=input_files["instrumental_audio"],
                title=title,
                artist=artist,
                cdg_styles=self.cdg_styles,
            )

            # Rename the generated ZIP file to match our expected naming convention
            if os.path.isfile(zip_file):
                os.rename(zip_file, output_files["final_karaoke_cdg_zip"])
                self.logger.info(f"Renamed CDG ZIP file from {zip_file} to {output_files['final_karaoke_cdg_zip']}")

        if not os.path.isfile(output_files["final_karaoke_cdg_zip"]):
            self.logger.error(f"Failed to find any CDG ZIP file. Listing directory contents:")
            for file in os.listdir():
                self.logger.error(f" - {file}")
            raise Exception(f"Failed to create CDG ZIP file: {output_files['final_karaoke_cdg_zip']}")

        self.logger.info(f"CDG ZIP file created: {output_files['final_karaoke_cdg_zip']}")

        # Extract the CDG ZIP file
        self.logger.info(f"Extracting CDG ZIP file: {output_files['final_karaoke_cdg_zip']}")
        with zipfile.ZipFile(output_files["final_karaoke_cdg_zip"], "r") as zip_ref:
            zip_ref.extractall()

        if os.path.isfile(output_files["karaoke_mp3"]):
            self.logger.info(f"Found extracted MP3 file: {output_files['karaoke_mp3']}")
        else:
            self.logger.error("Failed to find extracted MP3 file")
            raise Exception("Failed to extract MP3 file from CDG ZIP")

    def create_txt_zip_file(self, input_files, output_files):
        self.logger.info(f"Creating TXT ZIP file...")

        # Check if TXT file already exists, if so, ask user to overwrite or skip
        if os.path.isfile(output_files["final_karaoke_txt_zip"]):
            if not self.prompt_user_bool(
                f"Found existing TXT ZIP file: {output_files['final_karaoke_txt_zip']}. Overwrite (y) or skip (n)?",
            ):
                self.logger.info(f"Skipping TXT ZIP file creation, existing file will be used.")
                return

        # Create the ZIP file containing the MP3 and TXT files
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would create TXT ZIP file: {output_files['final_karaoke_txt_zip']}")
        else:
            self.logger.info(f"Running karaoke-converter to convert MidiCo LRC file {input_files['karaoke_lrc']} to TXT format")
            txt_converter = LyricsConverter(output_format="txt", filepath=input_files["karaoke_lrc"])
            converted_txt = txt_converter.convert_file()

            with open(output_files["karaoke_txt"], "w") as txt_file:
                txt_file.write(converted_txt)
                self.logger.info(f"TXT file written: {output_files['karaoke_txt']}")

            self.logger.info(f"Creating ZIP file containing {output_files['karaoke_mp3']} and {output_files['karaoke_txt']}")
            with zipfile.ZipFile(output_files["final_karaoke_txt_zip"], "w") as zipf:
                zipf.write(output_files["karaoke_mp3"], os.path.basename(output_files["karaoke_mp3"]))
                zipf.write(output_files["karaoke_txt"], os.path.basename(output_files["karaoke_txt"]))

            if not os.path.isfile(output_files["final_karaoke_txt_zip"]):
                raise Exception(f"Failed to create TXT ZIP file: {output_files['final_karaoke_txt_zip']}")

            self.logger.info(f"TXT ZIP file created: {output_files['final_karaoke_txt_zip']}")

    def move_files_to_brand_code_folder(self, brand_code, artist, title, output_files):
        self.logger.info(f"Moving files to new brand-prefixed directory...")

        self.new_brand_code_dir = f"{brand_code} - {artist} - {title}"
        self.new_brand_code_dir_path = os.path.join(self.organised_dir, self.new_brand_code_dir)

        # self.prompt_user_confirmation_or_raise_exception(
        #     f"Move files to new brand-prefixed directory {self.new_brand_code_dir_path} and delete current dir?",
        #     "Refusing to move files without user confirmation of move.",
        #     allow_empty=True,
        # )

        orig_dir = os.getcwd()
        os.chdir(os.path.dirname(orig_dir))
        self.logger.info(f"Changed dir to parent directory: {os.getcwd()}")

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would move original directory {orig_dir} to: {self.new_brand_code_dir_path}")
        else:
            os.rename(orig_dir, self.new_brand_code_dir_path)

        # Update output_files dictionary with the new paths after moving
        self.logger.info(f"Updating output file paths to reflect move to {self.new_brand_code_dir_path}")
        for key in output_files:
            if output_files[key]: # Check if the path exists (e.g., optional files)
                old_basename = os.path.basename(output_files[key])
                new_path = os.path.join(self.new_brand_code_dir_path, old_basename)
                output_files[key] = new_path
                self.logger.debug(f"  Updated {key}: {new_path}")

    def copy_final_files_to_public_share_dirs(self, brand_code, base_name, output_files):
        self.logger.info(f"Copying final MP4, 720p MP4, and ZIP to public share directory...")

        # Validate public_share_dir is a valid folder with MP4, MP4-720p, and CDG subdirectories
        if not os.path.isdir(self.public_share_dir):
            raise Exception(f"Public share directory does not exist: {self.public_share_dir}")

        if not os.path.isdir(os.path.join(self.public_share_dir, "MP4")):
            raise Exception(f"Public share directory does not contain MP4 subdirectory: {self.public_share_dir}")

        if not os.path.isdir(os.path.join(self.public_share_dir, "MP4-720p")):
            raise Exception(f"Public share directory does not contain MP4-720p subdirectory: {self.public_share_dir}")

        if not os.path.isdir(os.path.join(self.public_share_dir, "CDG")):
            raise Exception(f"Public share directory does not contain CDG subdirectory: {self.public_share_dir}")

        if brand_code is None:
            raise Exception(f"New track prefix was not set, refusing to copy to public share directory")

        dest_mp4_dir = os.path.join(self.public_share_dir, "MP4")
        dest_720p_dir = os.path.join(self.public_share_dir, "MP4-720p")
        dest_cdg_dir = os.path.join(self.public_share_dir, "CDG")
        os.makedirs(dest_mp4_dir, exist_ok=True)
        os.makedirs(dest_720p_dir, exist_ok=True)
        os.makedirs(dest_cdg_dir, exist_ok=True)

        dest_mp4_file = os.path.join(dest_mp4_dir, f"{brand_code} - {base_name}.mp4")
        dest_720p_mp4_file = os.path.join(dest_720p_dir, f"{brand_code} - {base_name}.mp4")
        dest_zip_file = os.path.join(dest_cdg_dir, f"{brand_code} - {base_name}.zip")

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would copy final MP4, 720p MP4, and ZIP to {dest_mp4_file}, {dest_720p_mp4_file}, and {dest_zip_file}"
            )
        else:
            shutil.copy2(output_files["final_karaoke_lossy_mp4"], dest_mp4_file)  # Changed to use lossy MP4
            shutil.copy2(output_files["final_karaoke_lossy_720p_mp4"], dest_720p_mp4_file)
            
            # Only copy CDG ZIP if CDG creation is enabled
            if self.enable_cdg and "final_karaoke_cdg_zip" in output_files:
                shutil.copy2(output_files["final_karaoke_cdg_zip"], dest_zip_file)
                self.logger.info(f"Copied CDG ZIP file to public share directory")
            else:
                self.logger.info(f"CDG creation disabled, skipping CDG ZIP copy")
                
            self.logger.info(f"Copied final files to public share directory")

    def sync_public_share_dir_to_rclone_destination(self):
        self.logger.info(f"Copying public share directory to rclone destination...")

        # Delete .DS_Store files recursively before copying
        for root, dirs, files in os.walk(self.public_share_dir):
            for file in files:
                if file == ".DS_Store":
                    file_path = os.path.join(root, file)
                    os.remove(file_path)
                    self.logger.info(f"Deleted .DS_Store file: {file_path}")

        rclone_cmd = f"rclone copy -v --ignore-existing {shlex.quote(self.public_share_dir)} {shlex.quote(self.rclone_destination)}"
        self.execute_command(rclone_cmd, "Copying to cloud destination")

    def post_discord_notification(self):
        self.logger.info(f"Posting Discord notification...")

        if self.skip_notifications:
            self.logger.info(f"Skipping Discord notification as video was previously uploaded to YouTube")
            return
        
        # Only post if we have a YouTube URL
        if not self.youtube_url:
            self.logger.info(f"Skipping Discord notification - no YouTube URL available")
            return

        if self.dry_run:
            self.logger.info(
                f"DRY RUN: Would post Discord notification for youtube URL {self.youtube_url} using webhook URL: {self.discord_webhook_url}"
            )
        else:
            discord_message = f"New upload: {self.youtube_url}"
            self.post_discord_message(discord_message, self.discord_webhook_url)

    def generate_organised_folder_sharing_link(self):
        self.logger.info(f"Getting Organised Folder sharing link for new brand code directory...")

        rclone_dest = f"{self.organised_dir_rclone_root}/{self.new_brand_code_dir}"
        rclone_link_cmd = f"rclone link {shlex.quote(rclone_dest)}"

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would get sharing link with: {rclone_link_cmd}")
            return "https://file-sharing-service.com/example"

        # Add a 5-second delay to allow dropbox to index the folder before generating a link
        self.logger.info("Waiting 5 seconds before generating link...")
        time.sleep(5)

        try:
            self.logger.info(f"Running command: {rclone_link_cmd}")
            result = subprocess.run(rclone_link_cmd, shell=True, check=True, capture_output=True, text=True)
            
            # Log command output for debugging
            if result.stdout and result.stdout.strip():
                self.logger.debug(f"Command STDOUT: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                self.logger.debug(f"Command STDERR: {result.stderr.strip()}")
                
            self.brand_code_dir_sharing_link = result.stdout.strip()
            self.logger.info(f"Got organised folder sharing link: {self.brand_code_dir_sharing_link}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to get organised folder sharing link. Exit code: {e.returncode}")
            self.logger.error(f"Command output (stdout): {e.stdout}")
            self.logger.error(f"Command output (stderr): {e.stderr}")
            self.logger.error(f"Full exception: {e}")

    def get_next_brand_code_server_side(self):
        """
        Calculate the next sequence number based on existing directories in the remote organised_dir using rclone.
        Fills gaps in the sequence left by deleted outputs.
        Assumes directories are named with the format: BRAND-XXXX Artist - Title
        """
        if not self.organised_dir_rclone_root:
            raise Exception("organised_dir_rclone_root not configured for server-side brand code generation")

        self.logger.info(f"Getting next brand code from remote organized directory: {self.organised_dir_rclone_root}")

        pattern = re.compile(rf"^{re.escape(self.brand_prefix)}-(\d{{4}})")

        # Use rclone lsf --dirs-only for clean, machine-readable directory listing
        rclone_list_cmd = f"rclone lsf --dirs-only {shlex.quote(self.organised_dir_rclone_root)}"

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would run: {rclone_list_cmd}")
            return f"{self.brand_prefix}-0001"

        try:
            self.logger.info(f"Running command: {rclone_list_cmd}")
            result = subprocess.run(rclone_list_cmd, shell=True, check=True, capture_output=True, text=True)

            # Log command output for debugging
            if result.stdout and result.stdout.strip():
                self.logger.debug(f"Command STDOUT: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                self.logger.debug(f"Command STDERR: {result.stderr.strip()}")

            # Parse the output to find matching directories
            existing_nums: set[int] = set()
            for line in result.stdout.strip().split('\n'):
                if line.strip():
                    # Remove trailing slash and whitespace
                    dir_name = line.strip().rstrip('/')

                    # Check if directory matches our brand pattern
                    match = pattern.match(dir_name)
                    if match:
                        existing_nums.add(int(match.group(1)))

            self.logger.info(f"Found {len(existing_nums)} matching directories with pattern {self.brand_prefix}-XXXX")

            max_existing = max(existing_nums) if existing_nums else 0

            # Only fill gaps for numbers >= 1001 (preserve legacy gaps below 1000)
            if max_existing >= 1000:
                next_seq_number = 1001
                while next_seq_number in existing_nums:
                    next_seq_number += 1
            else:
                next_seq_number = max_existing + 1

            brand_code = f"{self.brand_prefix}-{next_seq_number:04d}"

            if max_existing >= 1000 and next_seq_number <= max_existing:
                self.logger.info(f"Next sequence number for brand {self.brand_prefix}: {next_seq_number} (filling gap, max existing: {max_existing})")
            else:
                self.logger.info(f"Next sequence number for brand {self.brand_prefix}: {next_seq_number} (max existing: {max_existing})")
            return brand_code

        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to list remote organized directory. Exit code: {e.returncode}")
            self.logger.error(f"Command output (stdout): {e.stdout}")
            self.logger.error(f"Command output (stderr): {e.stderr}")
            raise Exception(f"Failed to get brand code from remote directory: {e}")

    def upload_files_to_organized_folder_server_side(self, brand_code, artist, title):
        """
        Upload all files from current directory to the remote organized folder using rclone.
        Creates a brand-prefixed directory in the remote organized folder.
        """
        if not self.organised_dir_rclone_root:
            raise Exception("organised_dir_rclone_root not configured for server-side file upload")

        self.new_brand_code_dir = f"{brand_code} - {artist} - {title}"
        remote_dest = f"{self.organised_dir_rclone_root}/{self.new_brand_code_dir}"
        
        self.logger.info(f"Uploading files to remote organized directory: {remote_dest}")

        # Get current directory path to upload
        current_dir = os.getcwd()
        
        # Use rclone copy to upload the entire current directory to the remote destination
        rclone_upload_cmd = f"rclone copy -v --ignore-existing {shlex.quote(current_dir)} {shlex.quote(remote_dest)}"
        
        if self.dry_run:
            self.logger.info(f"DRY RUN: Would upload current directory to: {remote_dest}")
            self.logger.info(f"DRY RUN: Command: {rclone_upload_cmd}")
        else:
            self.execute_command(rclone_upload_cmd, f"Uploading files to organized folder: {remote_dest}")

        # Generate a sharing link for the uploaded folder
        self.generate_organised_folder_sharing_link_server_side(remote_dest)

    def generate_organised_folder_sharing_link_server_side(self, remote_path):
        """Generate a sharing link for the remote organized folder using rclone."""
        self.logger.info(f"Getting sharing link for remote organized folder: {remote_path}")

        rclone_link_cmd = f"rclone link {shlex.quote(remote_path)}"

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would get sharing link with: {rclone_link_cmd}")
            self.brand_code_dir_sharing_link = "https://file-sharing-service.com/example"
            return

        # Add a 10-second delay to allow the remote service to index the folder before generating a link
        self.logger.info("Waiting 10 seconds before generating link...")
        time.sleep(10)

        try:
            self.logger.info(f"Running command: {rclone_link_cmd}")
            result = subprocess.run(rclone_link_cmd, shell=True, check=True, capture_output=True, text=True)
            
            # Log command output for debugging
            if result.stdout and result.stdout.strip():
                self.logger.debug(f"Command STDOUT: {result.stdout.strip()}")
            if result.stderr and result.stderr.strip():
                self.logger.debug(f"Command STDERR: {result.stderr.strip()}")
                
            self.brand_code_dir_sharing_link = result.stdout.strip()
            self.logger.info(f"Got organized folder sharing link: {self.brand_code_dir_sharing_link}")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to get organized folder sharing link. Exit code: {e.returncode}")
            self.logger.error(f"Command output (stdout): {e.stdout}")
            self.logger.error(f"Command output (stderr): {e.stderr}")
            self.logger.error(f"Full exception: {e}")

    def get_existing_brand_code(self):
        """Extract brand code from current directory name"""
        current_dir = os.path.basename(os.getcwd())
        if " - " not in current_dir:
            raise Exception(f"Current directory '{current_dir}' does not match expected format 'BRAND-XXXX - Artist - Title'")

        brand_code = current_dir.split(" - ")[0]
        if not brand_code or "-" not in brand_code:
            raise Exception(f"Could not extract valid brand code from directory name '{current_dir}'")

        self.logger.info(f"Using existing brand code: {brand_code}")
        return brand_code

    def execute_optional_features(self, artist, title, base_name, input_files, output_files, replace_existing=False):
        self.logger.info(f"Executing optional features...")

        if self.youtube_upload_enabled:
            try:
                self.upload_final_mp4_to_youtube_with_title_thumbnail(artist, title, input_files, output_files, replace_existing)
            except Exception as e:
                self.logger.error(f"Failed to upload video to YouTube: {e}")
                if not self.non_interactive:
                    print("Please manually upload the video to YouTube.")
                    print()
                    self.youtube_video_id = input("Enter the manually uploaded YouTube video ID: ").strip()
                    self.youtube_url = f"{self.youtube_url_prefix}{self.youtube_video_id}"
                    self.logger.info(f"Using manually provided YouTube video ID: {self.youtube_video_id}")
                else:
                    self.logger.error("YouTube upload failed in non-interactive mode, skipping")

        # Discord notification - runs independently of YouTube upload
        # Wrapped in try/except so failures don't crash the entire job
        if self.discord_notication_enabled:
            try:
                self.post_discord_notification()
            except Exception as e:
                self.logger.error(f"Failed to send Discord notification: {e}")
                self.logger.warning("Continuing without Discord notification - this is non-fatal")

        # Handle folder organization - different logic for server-side vs local mode
        if self.server_side_mode and self.brand_prefix and self.organised_dir_rclone_root:
            self.logger.info("Executing server-side organization...")
            
            # Generate brand code from remote directory listing
            if self.keep_brand_code:
                self.brand_code = self.get_existing_brand_code()
            else:
                self.brand_code = self.get_next_brand_code_server_side()

            # Upload files to organized folder via rclone
            self.upload_files_to_organized_folder_server_side(self.brand_code, artist, title)

            # Copy files to public share if enabled
            if self.public_share_copy_enabled:
                self.copy_final_files_to_public_share_dirs(self.brand_code, base_name, output_files)

            # Sync public share to cloud destination if enabled
            if self.public_share_rclone_enabled:
                self.sync_public_share_dir_to_rclone_destination()

        elif self.folder_organisation_enabled:
            self.logger.info("Executing local folder organization...")
            
            if self.keep_brand_code:
                self.brand_code = self.get_existing_brand_code()
                self.new_brand_code_dir = os.path.basename(os.getcwd())
                self.new_brand_code_dir_path = os.getcwd()
            else:
                self.brand_code = self.get_next_brand_code()
                self.move_files_to_brand_code_folder(self.brand_code, artist, title, output_files)
                # Update output file paths after moving
                for key in output_files:
                    output_files[key] = os.path.join(self.new_brand_code_dir_path, os.path.basename(output_files[key]))

            if self.public_share_copy_enabled:
                self.copy_final_files_to_public_share_dirs(self.brand_code, base_name, output_files)

            if self.public_share_rclone_enabled:
                self.sync_public_share_dir_to_rclone_destination()

            self.generate_organised_folder_sharing_link()
        
        elif self.public_share_copy_enabled or self.public_share_rclone_enabled:
            # If only public share features are enabled (no folder organization), we still need a brand code
            self.logger.info("No folder organization enabled, but public share features require brand code...")
            if self.brand_prefix:
                if self.server_side_mode and self.organised_dir_rclone_root:
                    self.brand_code = self.get_next_brand_code_server_side()
                elif not self.server_side_mode and self.organised_dir:
                    self.brand_code = self.get_next_brand_code()
                else:
                    # Fallback to timestamp-based brand code if no organized directory configured
                    import datetime
                    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                    self.brand_code = f"{self.brand_prefix}-{timestamp}"
                    self.logger.warning(f"No organized directory configured, using timestamp-based brand code: {self.brand_code}")

                if self.public_share_copy_enabled:
                    self.copy_final_files_to_public_share_dirs(self.brand_code, base_name, output_files)

                if self.public_share_rclone_enabled:
                    self.sync_public_share_dir_to_rclone_destination()

    def authenticate_gmail(self):
        """Authenticate and return a Gmail service object."""
        creds = None
        gmail_token_file = "/tmp/karaoke-finalise-gmail-token.pickle"

        if os.path.exists(gmail_token_file):
            with open(gmail_token_file, "rb") as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if self.non_interactive:
                    raise Exception("Gmail authentication required but running in non-interactive mode. Please pre-authenticate or disable email drafts.")
                    
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.youtube_client_secrets_file, ["https://www.googleapis.com/auth/gmail.compose"]
                )
                creds = flow.run_local_server(port=0)
            with open(gmail_token_file, "wb") as token:
                pickle.dump(creds, token)

        return build("gmail", "v1", credentials=creds)

    def draft_completion_email(self, artist, title, youtube_url, dropbox_url):
        # Completely disable email drafts in server-side mode
        if self.server_side_mode:
            self.logger.info("Server-side mode: skipping email draft creation")
            return

        if not self.email_template_file:
            self.logger.info("Email template file not provided, skipping email draft creation.")
            return

        if not self.youtube_client_secrets_file:
            self.logger.error("Email template file was provided, but youtube_client_secrets_file is required for Gmail authentication.")
            self.logger.error("Please provide --youtube_client_secrets_file parameter to enable email draft creation.")
            self.logger.info("Skipping email draft creation.")
            return

        with open(self.email_template_file, "r") as f:
            template = f.read()

        email_body = template.format(youtube_url=youtube_url, dropbox_url=dropbox_url)

        # Sanitize artist/title to handle Unicode characters (curly quotes, em dashes, etc.)
        # that cause email header encoding issues (MIME headers use latin-1)
        safe_artist = sanitize_filename(artist) if artist else "Unknown"
        safe_title = sanitize_filename(title) if title else "Unknown"
        subject = f"{self.brand_code}: {safe_artist} - {safe_title}"

        if self.dry_run:
            self.logger.info(f"DRY RUN: Would create email draft with subject: {subject}")
            self.logger.info(f"DRY RUN: Email body:\n{email_body}")
        else:
            if not self.gmail_service:
                self.gmail_service = self.authenticate_gmail()

            message = MIMEText(email_body)
            message["subject"] = subject
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
            draft = self.gmail_service.users().drafts().create(userId="me", body={"message": {"raw": raw_message}}).execute()
            self.logger.info(f"Email draft created with ID: {draft['id']}")

    def test_email_template(self):
        if not self.email_template_file:
            self.logger.error("Email template file not provided. Use --email_template_file to specify the file path.")
            return

        fake_artist = "Test Artist"
        fake_title = "Test Song"
        fake_youtube_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        fake_dropbox_url = "https://www.dropbox.com/sh/fake/folder/link"
        fake_brand_code = "TEST-0001"

        self.brand_code = fake_brand_code
        self.draft_completion_email(fake_artist, fake_title, fake_youtube_url, fake_dropbox_url)

        self.logger.info("Email template test complete. Check your Gmail drafts for the test email.")

    def detect_best_aac_codec(self):
        """Detect the best available AAC codec (aac_at > libfdk_aac > aac)"""
        self.logger.info("Detecting best available AAC codec...")

        codec_check_command = f"{self.ffmpeg_base_command} -codecs"
        result = os.popen(codec_check_command).read()

        if "aac_at" in result:
            self.logger.info("Using aac_at codec (best quality)")
            return "aac_at"
        elif "libfdk_aac" in result:
            self.logger.info("Using libfdk_aac codec (good quality)")
            return "libfdk_aac"
        else:
            self.logger.info("Using built-in aac codec (basic quality)")
            return "aac"

    def detect_nvenc_support(self):
        """Detect if NVENC hardware encoding is available."""
        try:
            self.logger.info("🔍 Detecting NVENC hardware acceleration...")
            
            if self.dry_run:
                self.logger.info("  DRY RUN: Assuming NVENC is available")
                return True
            
            import subprocess
            import os
            import shutil
            
            # Check for nvidia-smi (indicates NVIDIA driver presence)
            try:
                nvidia_smi_result = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"], 
                                                  capture_output=True, text=True, timeout=10)
                if nvidia_smi_result.returncode == 0:
                    gpu_info = nvidia_smi_result.stdout.strip()
                    self.logger.info(f"  ✓ NVIDIA GPU detected: {gpu_info}")
                else:
                    self.logger.debug(f"nvidia-smi failed: {nvidia_smi_result.stderr}")
                    self.logger.info("  ✗ NVENC not available (no NVIDIA GPU)")
                    return False
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.CalledProcessError) as e:
                self.logger.debug(f"nvidia-smi not available: {e}")
                self.logger.info("  ✗ NVENC not available (no NVIDIA GPU)")
                return False
            
            # Check for NVENC encoders in FFmpeg
            try:
                encoders_cmd = f"{self.ffmpeg_base_command} -hide_banner -encoders 2>/dev/null | grep nvenc"
                encoders_result = subprocess.run(encoders_cmd, shell=True, capture_output=True, text=True, timeout=10)
                if encoders_result.returncode == 0 and "nvenc" in encoders_result.stdout:
                    nvenc_encoders = [line.strip() for line in encoders_result.stdout.split('\n') if 'nvenc' in line]
                    self.logger.debug(f"Found NVENC encoders: {nvenc_encoders}")
                else:
                    self.logger.debug("No NVENC encoders found in FFmpeg")
                    self.logger.info("  ✗ NVENC not available (no FFmpeg support)")
                    return False
            except Exception as e:
                self.logger.debug(f"Failed to check FFmpeg NVENC encoders: {e}")
                self.logger.info("  ✗ NVENC not available")
                return False
            
            # Check for libcuda.so.1 (critical for NVENC)
            try:
                libcuda_check = subprocess.run(["ldconfig", "-p"], capture_output=True, text=True, timeout=10)
                if libcuda_check.returncode == 0 and "libcuda.so.1" in libcuda_check.stdout:
                    self.logger.debug("libcuda.so.1 found in system libraries")
                else:
                    self.logger.debug("libcuda.so.1 NOT found - may need nvidia/cuda:*-devel image")
                    self.logger.info("  ✗ NVENC not available (missing CUDA libraries)")
                    return False
            except Exception as e:
                self.logger.debug(f"Failed to check for libcuda.so.1: {e}")
                self.logger.info("  ✗ NVENC not available")
                return False
            
            # Test h264_nvenc encoder
            test_cmd = f"{self.ffmpeg_base_command} -hide_banner -loglevel error -f lavfi -i testsrc=duration=1:size=320x240:rate=1 -c:v h264_nvenc -f null -"
            self.logger.debug(f"Testing NVENC: {test_cmd}")
            
            try:
                result = subprocess.run(test_cmd, shell=True, capture_output=True, text=True, timeout=30)
                
                if result.returncode == 0:
                    self.logger.info("  ✓ NVENC encoding available")
                    return True
                else:
                    self.logger.debug(f"NVENC test failed (exit code {result.returncode}): {result.stderr}")
                    self.logger.info("  ✗ NVENC not available")
                    return False
                    
            except subprocess.TimeoutExpired:
                self.logger.debug("NVENC test timed out")
                self.logger.info("  ✗ NVENC not available (timeout)")
                return False
                
        except Exception as e:
            self.logger.debug(f"Failed to detect NVENC support: {e}")
            self.logger.info("  ✗ NVENC not available (error)")
            return False

    def configure_hardware_acceleration(self):
        """Configure hardware acceleration settings based on detected capabilities."""
        if self.nvenc_available:
            self.video_encoder = "h264_nvenc"
            # Use simpler hardware acceleration that works with complex filter chains
            # Remove -hwaccel_output_format cuda as it causes pixel format conversion issues
            self.hwaccel_decode_flags = "-hwaccel cuda"
            self.scale_filter = "scale"  # Use CPU scaling for complex filter chains
            self.logger.info("🚀 Using NVENC hardware acceleration for video encoding")
        else:
            self.video_encoder = "libx264"
            self.hwaccel_decode_flags = ""
            self.scale_filter = "scale"
            self.logger.info("🔧 Using software encoding (libx264) for video")

    def get_nvenc_quality_settings(self, quality_mode="high"):
        """Get NVENC settings based on quality requirements."""
        if quality_mode == "lossless":
            return "-preset lossless"
        elif quality_mode == "high":
            return "-preset p4 -tune hq -cq 18"  # High quality
        elif quality_mode == "medium":
            return "-preset p4 -cq 23"  # Balanced quality/speed
        elif quality_mode == "fast":
            return "-preset p1 -tune ll"  # Low latency, faster encoding
        else:
            return "-preset p4"  # Balanced default

    def execute_command_with_fallback(self, gpu_command, cpu_command, description):
        """Execute GPU command with automatic fallback to CPU if it fails."""
        self.logger.info(f"{description}")
        
        if self.dry_run:
            if self.nvenc_available:
                self.logger.info(f"DRY RUN: Would run GPU-accelerated command: {gpu_command}")
            else:
                self.logger.info(f"DRY RUN: Would run CPU command: {cpu_command}")
            return
        
        # Try GPU-accelerated command first if available
        if self.nvenc_available and gpu_command != cpu_command:
            self.logger.debug(f"Attempting hardware-accelerated encoding: {gpu_command}")
            try:
                result = subprocess.run(gpu_command, shell=True, capture_output=True, text=True, timeout=300)
                
                if result.returncode == 0:
                    self.logger.info(f"✓ Hardware acceleration successful")
                    return
                else:
                    self.logger.warning(f"✗ Hardware acceleration failed (exit code {result.returncode})")
                    self.logger.warning(f"GPU Command: {gpu_command}")
                    
                    # If we didn't get detailed error info and using fatal loglevel, try again with verbose logging
                    if (not result.stderr or len(result.stderr.strip()) < 10) and "-loglevel fatal" in gpu_command:
                        self.logger.warning("Empty error output detected, retrying with verbose logging...")
                        verbose_gpu_command = gpu_command.replace("-loglevel fatal", "-loglevel error")
                        try:
                            verbose_result = subprocess.run(verbose_gpu_command, shell=True, capture_output=True, text=True, timeout=300)
                            self.logger.warning(f"Verbose GPU Command: {verbose_gpu_command}")
                            if verbose_result.stderr:
                                self.logger.warning(f"FFmpeg STDERR (verbose): {verbose_result.stderr}")
                            if verbose_result.stdout:
                                self.logger.warning(f"FFmpeg STDOUT (verbose): {verbose_result.stdout}")
                        except Exception as e:
                            self.logger.warning(f"Verbose retry failed: {e}")
                    
                    if result.stderr:
                        self.logger.warning(f"FFmpeg STDERR: {result.stderr}")
                    else:
                        self.logger.warning("FFmpeg STDERR: (empty)")
                    if result.stdout:
                        self.logger.warning(f"FFmpeg STDOUT: {result.stdout}")
                    else:
                        self.logger.warning("FFmpeg STDOUT: (empty)")
                    self.logger.info("Falling back to software encoding...")
                    
            except subprocess.TimeoutExpired:
                self.logger.warning("✗ Hardware acceleration timed out, falling back to software encoding")
            except Exception as e:
                self.logger.warning(f"✗ Hardware acceleration failed with exception: {e}, falling back to software encoding")
        
        # Use CPU command (either as fallback or primary method)
        self.logger.debug(f"Running software encoding: {cpu_command}")
        try:
            result = subprocess.run(cpu_command, shell=True, capture_output=True, text=True, timeout=600)
            
            if result.returncode != 0:
                error_msg = f"Software encoding failed with exit code {result.returncode}"
                self.logger.error(error_msg)
                self.logger.error(f"CPU Command: {cpu_command}")
                if result.stderr:
                    self.logger.error(f"FFmpeg STDERR: {result.stderr}")
                else:
                    self.logger.error("FFmpeg STDERR: (empty)")
                if result.stdout:
                    self.logger.error(f"FFmpeg STDOUT: {result.stdout}")
                else:
                    self.logger.error("FFmpeg STDOUT: (empty)")
                raise Exception(f"{error_msg}: {cpu_command}")
            else:
                self.logger.info(f"✓ Software encoding successful")
                
        except subprocess.TimeoutExpired:
            error_msg = "Software encoding timed out"
            self.logger.error(error_msg)
            raise Exception(f"{error_msg}: {cpu_command}")
        except Exception as e:
            if "Software encoding failed" not in str(e):
                error_msg = f"Software encoding failed with exception: {e}"
                self.logger.error(error_msg)
                raise Exception(f"{error_msg}: {cpu_command}")
            else:
                raise

    def process(self, replace_existing=False):
        if self.dry_run:
            self.logger.warning("Dry run enabled. No actions will be performed.")

        self.logger.info("=" * 60)
        self.logger.info("Starting KaraokeFinalise processing pipeline")
        self.logger.info("=" * 60)

        # Check required input files and parameters exist, get user to confirm features before proceeding
        self.validate_input_parameters_for_features()

        # When --no-video is set, we don't have a (With Vocals).mkv file
        # Extract artist/title from the current directory name instead
        if self.no_video:
            with_vocals_file = None
            # Current directory should be "Artist - Title"
            dir_name = os.path.basename(os.getcwd())
            if " - " in dir_name:
                artist, title = dir_name.split(" - ", 1)
                base_name = dir_name
                self.logger.info(f"--no-video mode: extracted artist/title from directory name: {dir_name}")
            else:
                raise Exception(
                    f"Cannot extract artist/title from directory name '{dir_name}'. "
                    "Expected format: 'Artist - Title'"
                )
        else:
            with_vocals_file = self.find_with_vocals_file()
            base_name, artist, title = self.get_names_from_withvocals(with_vocals_file)

        self.logger.info(f"Processing: {artist} - {title}")

        # Use the selected instrumental file if provided, otherwise search for one
        if self.selected_instrumental_file:
            if not os.path.isfile(self.selected_instrumental_file):
                raise Exception(f"Selected instrumental file not found: {self.selected_instrumental_file}")
            instrumental_audio_file = self.selected_instrumental_file
            self.logger.info(f"Using pre-selected instrumental file: {instrumental_audio_file}")
        else:
            self.logger.info("No instrumental file pre-selected, searching for instrumental files...")
            instrumental_audio_file = self.choose_instrumental_audio_file(base_name)

        input_files = self.check_input_files_exist(base_name, with_vocals_file, instrumental_audio_file)
        output_files = self.prepare_output_filenames(base_name)

        if self.enable_cdg:
            self.logger.info("Creating CDG package...")
            self.create_cdg_zip_file(input_files, output_files, artist, title)
            self.logger.info("CDG package created successfully")

        if self.enable_txt:
            self.logger.info("Creating TXT package...")
            self.create_txt_zip_file(input_files, output_files)
            self.logger.info("TXT package created successfully")

        if not self.no_video:
            self.logger.info("Starting video encoding (this is the longest step, ~15-20 minutes)...")
            self.remux_and_encode_output_video_files(with_vocals_file, input_files, output_files)
            self.logger.info("Video encoding completed successfully")

            self.logger.info("Executing distribution features (YouTube, Dropbox, Discord)...")
            self.execute_optional_features(artist, title, base_name, input_files, output_files, replace_existing)
        else:
            self.logger.info("--no-video flag set: Skipping video encoding and distribution")

        result = {
            "artist": artist,
            "title": title,
        }

        if not self.no_video:
            result.update({
                "video_with_vocals": output_files["with_vocals_mp4"],
                "video_with_instrumental": output_files["karaoke_mp4"],
                "final_video": output_files["final_karaoke_lossless_mp4"],
                "final_video_mkv": output_files["final_karaoke_lossless_mkv"],
                "final_video_lossy": output_files["final_karaoke_lossy_mp4"],
                "final_video_720p": output_files["final_karaoke_lossy_720p_mp4"],
                "youtube_url": self.youtube_url,
                "brand_code": self.brand_code,
                "new_brand_code_dir_path": self.new_brand_code_dir_path,
                "brand_code_dir_sharing_link": self.brand_code_dir_sharing_link,
            })
        else:
            result.update({
                "video_with_vocals": None,
                "video_with_instrumental": None,
                "final_video": None,
                "final_video_mkv": None,
                "final_video_lossy": None,
                "final_video_720p": None,
                "youtube_url": None,
                "brand_code": None,
                "new_brand_code_dir_path": None,
                "brand_code_dir_sharing_link": None,
            })

        if self.enable_cdg:
            result["final_karaoke_cdg_zip"] = output_files["final_karaoke_cdg_zip"]

        if self.enable_txt:
            result["final_karaoke_txt_zip"] = output_files["final_karaoke_txt_zip"]

        if self.email_template_file and not self.no_video:
            self.draft_completion_email(artist, title, result["youtube_url"], result["brand_code_dir_sharing_link"])

        return result
