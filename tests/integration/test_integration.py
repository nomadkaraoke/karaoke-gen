import os
import pytest
import tempfile
import shutil
import subprocess
import sys
import json
from karaoke_gen.karaoke_gen import KaraokePrep
from unittest.mock import MagicMock, call, patch, AsyncMock, ANY
from karaoke_gen.utils.gen_cli import async_main
import shlex
import asyncio
from googleapiclient.http import MediaFileUpload
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise

# Register the asyncio marker
pytest.mark.asyncio = pytest.mark.asyncio

# Set the default event loop policy to 'auto'
pytest_configure = lambda config: config.addinivalue_line("markers", "asyncio: mark test as async")


@pytest.mark.asyncio
@pytest.mark.integration
async def test_karaoke_gen_integration():
    # Create a temporary directory for test outputs
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a dummy input file (e.g., a small WAV file)
        input_file = os.path.join(temp_dir, "input.wav")
        # Use ffmpeg to generate a small WAV file with a sine wave
        subprocess.run(["ffmpeg", "-f", "lavfi", "-i", "sine=frequency=1000:duration=1", input_file], check=True)

        # Instantiate KaraokePrep with the dummy input file
        kp = KaraokePrep(
            input_media=input_file,
            artist="Test Artist",
            title="Test Title",
            output_dir=temp_dir,
            skip_lyrics=True,  # Skip lyrics processing for this test
            skip_separation=True,  # Skip audio separation for this test
            dry_run=False,
            log_level="INFO"
        )

        # Function to simulate file creation
        def create_dummy_files(output_image_filepath_noext, output_video_filepath, **kwargs):
            # Create empty files for PNG, JPG, and MOV
            for ext in ['.png', '.jpg']:
                try:
                    # Ensure directory exists
                    os.makedirs(os.path.dirname(f"{output_image_filepath_noext}{ext}"), exist_ok=True)
                    with open(f"{output_image_filepath_noext}{ext}", 'w') as f:
                        f.write('dummy') # Write something small
                except Exception as e:
                     print(f"Error creating dummy file {output_image_filepath_noext}{ext}: {e}") # Debug print
            try:
                # Ensure directory exists
                os.makedirs(os.path.dirname(output_video_filepath), exist_ok=True)
                with open(output_video_filepath, 'w') as f:
                    f.write('dummy') # Write something small
            except Exception as e:
                print(f"Error creating dummy file {output_video_filepath}: {e}") # Debug print

        # Function to simulate WAV creation
        def create_dummy_wav(input_media_path, output_filename_no_extension):
            wav_path = f"{output_filename_no_extension} ({kp.extractor}).wav" # Construct expected path
            try:
                os.makedirs(os.path.dirname(wav_path), exist_ok=True)
                with open(wav_path, 'w') as f:
                    f.write('dummy_wav') # Write something small
                return wav_path # Return the created path
            except Exception as e:
                print(f"Error creating dummy WAV file {wav_path}: {e}")
                return None # Indicate failure

        # Mock external dependencies and handler methods
        with patch('karaoke_gen.metadata.extract_info_for_online_media') as mock_extract, \
             patch('karaoke_gen.metadata.parse_track_metadata') as mock_parse, \
             patch.object(kp.file_handler, 'setup_output_paths', return_value=(os.path.join(temp_dir, "Test Artist - Test Title"), "Test Artist - Test Title")) as mock_setup_paths, \
             patch.object(kp.file_handler, 'copy_input_media', return_value="copied.mp4") as mock_copy, \
             patch.object(kp.file_handler, 'convert_to_wav', side_effect=create_dummy_wav) as mock_convert, \
             patch.object(kp.file_handler, 'download_audio_from_fetcher_result') as mock_download, \
             patch.object(kp.file_handler, 'extract_still_image_from_video') as mock_extract_image, \
             patch.object(kp.file_handler, '_file_exists', return_value=False) as mock_file_exists, \
             patch.object(kp.lyrics_processor, 'transcribe_lyrics', AsyncMock(return_value={'corrected_lyrics_text': 'lyrics text', 'corrected_lyrics_text_filepath': 'lyrics.txt'})) as mock_transcribe, \
             patch.object(kp.audio_processor, 'process_audio_separation', AsyncMock(return_value={})) as mock_separate, \
             patch.object(kp.video_generator, 'create_title_video', side_effect=create_dummy_files) as mock_create_title, \
             patch.object(kp.video_generator, 'create_end_video', side_effect=create_dummy_files) as mock_create_end, \
             patch('os.system') as mock_os_system:

            # Run the process
            results = await kp.process()

        # Verify that the result is a list (even for a single track)
        assert isinstance(results, list)
        assert len(results) > 0

        # Get the first track result
        track = results[0]
        assert isinstance(track, dict)
        assert "track_output_dir" in track
        assert "artist" in track
        assert "title" in track

        # Verify mocks were called as expected
        mock_transcribe.assert_not_called()
        mock_separate.assert_not_called()
        mock_create_title.assert_called_once() # Check if video creation was called
        mock_create_end.assert_called_once()   # Check if video creation was called

        # Verify that the expected output files exist and have non-zero sizes
        expected_files = [
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (Title).mov"),
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (End).mov"),
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (Title).png"),
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (Title).jpg"),
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (End).png"),
            os.path.join(track["track_output_dir"], f"{track['artist']} - {track['title']} (End).jpg")
        ]

        for file_path in expected_files:
            assert os.path.exists(file_path), f"Expected file {file_path} does not exist."
            assert os.path.getsize(file_path) > 0, f"Expected file {file_path} is empty."

        # Optional: Verify that the input audio WAV file was created
        input_wav = track.get("input_audio_wav")
        if input_wav:
            assert os.path.exists(input_wav), f"Input WAV file {input_wav} does not exist."
            assert os.path.getsize(input_wav) > 0, f"Input WAV file {input_wav} is empty."

        # Optional: Verify that the separated audio files exist (if not skipped)
        if not kp.skip_separation:
            separated_audio = track.get("separated_audio", {})
            for category, files in separated_audio.items():
                if isinstance(files, dict):
                    for file_path in files.values():
                        if file_path:
                            assert os.path.exists(file_path), f"Separated audio file {file_path} does not exist."
                            assert os.path.getsize(file_path) > 0, f"Separated audio file {file_path} is empty."

        # Optional: Verify that the lyrics file exists (if not skipped)
        if not kp.skip_lyrics:
            lyrics_file = track.get("lyrics")
            if lyrics_file:
                assert os.path.exists(lyrics_file), f"Lyrics file {lyrics_file} does not exist."
                assert os.path.getsize(lyrics_file) > 0, f"Lyrics file {lyrics_file} is empty."

        # Clean up the temporary directory (optional, as it will be removed automatically)
        # shutil.rmtree(temp_dir) 


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.slow
async def test_full_cli_integration(tmp_path, mocker):
    """Tests the full CLI workflow by calling async_main directly with mocked sys.argv."""

    # --- 1. Setup Temporary Directories and Files ---
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # output_dir = tmp_path / "output" # KaraokePrep will create this based on args
    # output_dir.mkdir()
    organised_dir = tmp_path / "organised"
    organised_dir.mkdir()
    public_share_dir = tmp_path / "public_share"
    public_share_dir.mkdir()
    public_share_mp4_dir = public_share_dir / "MP4"
    public_share_mp4_dir.mkdir()
    public_share_720p_dir = public_share_dir / "MP4-720p"
    public_share_720p_dir.mkdir()
    public_share_cdg_dir = public_share_dir / "CDG"
    public_share_cdg_dir.mkdir()

    # Copy input audio
    source_audio = "tests/data/waterloo10sec.flac"
    input_audio = data_dir / os.path.basename(source_audio)
    shutil.copy2(source_audio, input_audio)

    # Copy background images to /tmp directory (exists on both local and CI)
    background_images = [
        "karaoke-background-image-nomad-4k.png",
        "karaoke-title-screen-background-nomad-4k.png", 
        "cdg-instrumental-background-nomad-notes.png",
        "cdg-title-screen-background-nomad-simple.png"
    ]
    for image_name in background_images:
        source_image = f"tests/data/{image_name}"
        if os.path.exists(source_image):
            dest_image = f"/tmp/{image_name}"
            shutil.copy2(source_image, dest_image)
            print(f"Copied {source_image} to {dest_image}")
        else:
            print(f"Warning: Background image {source_image} not found, skipping")

    # Copy font file to /tmp directory
    font_source = "karaoke_gen/resources/AvenirNext-Bold.ttf"
    font_dest = "/tmp/AvenirNext-Bold.ttf"
    if os.path.exists(font_source):
        shutil.copy2(font_source, font_dest)
        print(f"Copied {font_source} to {font_dest}")
    else:
        print(f"Warning: Font file {font_source} not found, skipping")

    # Copy styles.json file (which now has /tmp paths)
    source_style_params = "tests/data/styles.json"
    style_params_path = data_dir / os.path.basename(source_style_params)
    shutil.copy2(source_style_params, style_params_path)

    # Create dummy youtube client secrets file
    yt_secrets_path = data_dir / "kgenclientsecret.json"
    yt_secrets_content = {"installed": {"client_id": "dummy", "client_secret": "dummy"}}
    with open(yt_secrets_path, "w") as f:
        json.dump(yt_secrets_content, f)

    # Create dummy youtube description file
    yt_desc_path = data_dir / "ytdesc.txt"
    with open(yt_desc_path, "w") as f:
        f.write("Test Description {artist} - {title}")

    # Create dummy email template file
    email_template_path = data_dir / "emailtemplate.txt"
    with open(email_template_path, "w") as f:
        f.write("Subject: Test Email\n\nYT: {youtube_url}\nDB: {dropbox_url}")

    # --- Define test variables used later in assertions ---
    expected_brand_code = "NOMAD-0001"
    discord_webhook_url = "https://discord.com/api/webhooks/123/abc"
    rclone_destination = "andrewdropboxfull:public_share"

    # --- 2. Mock External Interactions ---
    # Mock YouTube API
    mock_youtube_service = MagicMock()
    mock_youtube_insert = MagicMock()
    mock_youtube_insert.execute.return_value = {'id': 'mock_video_id'}
    mock_youtube_service.videos().insert.return_value = mock_youtube_insert
    mock_youtube_service.thumbnails().set().execute.return_value = {}
    mock_build = mocker.patch('googleapiclient.discovery.build', return_value=mock_youtube_service)

    # Mock YouTube credential flow and existing video check
    mocker.patch('google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file')
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.check_if_video_title_exists_on_youtube_channel', return_value=False)
    
    # Mock the upload_final_mp4_to_youtube_with_title_thumbnail method to directly return mock_video_id to bypass YouTube API issues
    # Use autospec=True so 'self' is passed to the side_effect function
    def upload_side_effect(self, *args, **kwargs):
        self.youtube_video_id = 'manual_mock_video_id'
        self.youtube_url = f"https://www.youtube.com/watch?v=manual_mock_video_id"
    mock_upload = mocker.patch.object(KaraokeFinalise, 'upload_final_mp4_to_youtube_with_title_thumbnail', 
                                       side_effect=upload_side_effect, autospec=True)
    
    # Keep the authenticate_youtube mock to return the service
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.authenticate_youtube', return_value=mock_youtube_service)

    # Mock Gmail API (reuse youtube mock for simplicity, just check call)
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.authenticate_gmail', return_value=mock_youtube_service)
    mock_draft_create = MagicMock()
    mock_draft_create.execute.return_value = {'id': 'mock_draft_id'}
    mock_youtube_service.users().drafts().create.return_value = mock_draft_create

    # Mock Discord requests
    mock_requests_post = mocker.patch('requests.post')
    mock_requests_post.return_value.raise_for_status.return_value = None

    # Store original functions needed for mocks
    original_os_system = os.system
    original_subprocess_run = subprocess.run
    original_os_chdir = os.chdir
    original_os_getcwd = os.getcwd
    original_os_listdir = os.listdir
    original_os_rename = os.rename

    # Mock os.system and subprocess.run for external calls (Rclone, ffmpeg etc)
    mock_os_system = mocker.patch('os.system')
    mock_subprocess_run = mocker.patch('subprocess.run')
    def rclone_side_effect(*args, **kwargs):
        # args[0] is the command (either string for os.system or list for subprocess.run)
        cmd_arg = args[0]
        cmd_str = cmd_arg if isinstance(cmd_arg, str) else " ".join(map(shlex.quote, cmd_arg))

        # --- Commands to execute with original subprocess ---
        execute_commands = ["ffmpeg", "ffprobe", "uname"]
        should_execute = False
        if isinstance(cmd_arg, list) and cmd_arg and cmd_arg[0] in execute_commands:
            should_execute = True
        elif isinstance(cmd_arg, str) and any(cmd in cmd_arg for cmd in execute_commands):
             # Be a bit careful with string matching for execute commands
             # Only execute if it clearly starts with ffmpeg etc.
             # This covers the os.system call for ffmpeg
             if cmd_arg.strip().startswith("ffmpeg"):
                 should_execute = True
        elif hasattr(cmd_arg, 'strip') and hasattr(cmd_arg, 'startswith'):
             # Handle bytes objects that have strip and startswith methods
             # Convert to string first for comparison
             cmd_str_decoded = cmd_arg.decode('utf-8', errors='replace') if isinstance(cmd_arg, bytes) else cmd_arg
             if any(cmd in cmd_str_decoded for cmd in execute_commands):
                 if cmd_str_decoded.strip().startswith("ffmpeg"):
                     should_execute = True

        # Special handling for ALL ffmpeg commands - create dummy output files instead of running actual ffmpeg
        if isinstance(cmd_arg, list) and len(cmd_arg) > 0 and cmd_arg[0] == "ffmpeg":
            print(f"SIDE_EFFECT: Simulating ffmpeg command instead of executing: {cmd_str}")
            
            # Handle WAV conversion
            if any(".wav" in arg for arg in cmd_arg):
                # Extract the output WAV path
                output_wav_path = None
                for i, arg in enumerate(cmd_arg):
                    if arg.endswith(".wav") and i > 0 and cmd_arg[i-1] != "-i":
                        output_wav_path = arg
                        break
                    
                if output_wav_path:
                    # Create the output directory if it doesn't exist
                    os.makedirs(os.path.dirname(output_wav_path), exist_ok=True)
                    # Create a dummy WAV file with minimal header (44 bytes, 8000Hz, mono, PCM)
                    with open(output_wav_path, 'wb') as f:
                        # Simple WAV header + minimal audio data
                        f.write(b'RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00@\x1f\x00\x00@\x1f\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00')
                        # Add some silence (1 second at 8000Hz)
                        f.write(b'\x80' * 8000)
                    print(f"SIDE_EFFECT: Created dummy WAV file at {output_wav_path}")
                    return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")
            
            # Handle video conversion (MP4, MKV output)
            elif any(arg.endswith((".mp4", ".mkv")) for arg in cmd_arg):
                # Extract the output video path
                output_video_path = None
                for i, arg in enumerate(cmd_arg):
                    if arg.endswith((".mp4", ".mkv")) and i > 0 and cmd_arg[i-1] != "-i":
                        output_video_path = arg
                        break
                
                if output_video_path:
                    # Create the output directory if it doesn't exist
                    os.makedirs(os.path.dirname(output_video_path), exist_ok=True)
                    # Create a dummy video file
                    with open(output_video_path, 'w') as f:
                        f.write('dummy video content for testing')
                    print(f"SIDE_EFFECT: Created dummy video file at {output_video_path}")
                    return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")
            
            # Default ffmpeg mock - just return success
            print(f"SIDE_EFFECT: Default ffmpeg mock - returning success for: {cmd_str}")
            return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")
        
        # For other non-ffmpeg commands that should be executed, proceed to normal execution
        if should_execute:
            print(f"SIDE_EFFECT: Executing original: {cmd_str}")
            # Pass original args and kwargs to the original function
            # Use shell=True only if the original command was a string (likely from os.system)
            use_shell = isinstance(cmd_arg, str)
            result = None
            try:
                # Use original_subprocess_run for consistency, handling shell arg
                run_kwargs = kwargs.copy() # Copy kwargs to avoid modifying original

                # Remove our custom flag that subprocess.run doesn't understand
                if '_os_system_call' in run_kwargs:
                    del run_kwargs['_os_system_call']

                # Handle shell parameter to avoid conflicts
                if 'shell' in run_kwargs:
                    # If shell was already specified, use that value
                    use_shell = run_kwargs['shell']
                else:
                    # Otherwise set it based on command type
                    run_kwargs['shell'] = use_shell

                # Determine if output should be captured based on original kwargs or if it's ffmpeg/ffprobe
                should_capture = False
                if isinstance(cmd_arg, list) and cmd_arg[0] in ["ffmpeg", "ffprobe"]:
                    # Check if the original call already requested capture
                    if run_kwargs.get('stdout') == subprocess.PIPE or run_kwargs.get('stderr') == subprocess.PIPE or run_kwargs.get('capture_output'):
                        should_capture = True # Original call wants capture, let it happen
                        # Ensure we don't add capture_output=True if stdout/stderr are set
                        run_kwargs.pop('capture_output', None)
                    else:
                        # Original call didn't request capture, but we want to for logging
                        should_capture = True
                        run_kwargs['capture_output'] = True
                        run_kwargs['text'] = True # Assume text if we're capturing
                
                # Adjust run_kwargs based on capture decision
                if should_capture and 'capture_output' not in run_kwargs:
                    # If original used stdout/stderr=PIPE, we need text=True to decode
                    if run_kwargs.get('stdout') == subprocess.PIPE or run_kwargs.get('stderr') == subprocess.PIPE:
                         run_kwargs['text'] = True 

                # Get the mocked CWD for ffmpeg/ffprobe execution
                current_mocked_dir = mock_current_dir_state()
                print(f"SIDE_EFFECT: Executing {cmd_arg[0]} in mocked CWD: {current_mocked_dir}")

                # Set CWD in kwargs and call original subprocess run
                run_kwargs['cwd'] = current_mocked_dir
                result = original_subprocess_run(*args, **run_kwargs)

                # Log results if captured
                if should_capture and hasattr(result, 'returncode'): # Check if it's a CompletedProcess
                    is_ffmpeg_or_probe = isinstance(cmd_arg, list) and cmd_arg[0] in ["ffmpeg", "ffprobe"]
                    
                    print(f"SIDE_EFFECT: Original {cmd_arg[0]} command finished. Return Code: {result.returncode}")
                    
                    # Print full ffmpeg/ffprobe args for debugging
                    if is_ffmpeg_or_probe:
                            print(f"SIDE_EFFECT: Full {cmd_arg[0]} Args: {cmd_arg}")

                    # Print stdout/stderr for ffmpeg/ffprobe even on success for detailed debugging
                    if is_ffmpeg_or_probe and result.stdout:
                         print(f"SIDE_EFFECT: {cmd_arg[0]} stdout (first 500 chars):\n{result.stdout[:500]}...")
                    if result.stderr: # Always print stderr if it exists
                         print(f"SIDE_EFFECT: {cmd_arg[0]} stderr (first 500 chars):\n{result.stderr[:500]}...")
                    elif result.returncode == 0 and not is_ffmpeg_or_probe:
                         print(f"SIDE_EFFECT: Original {cmd_arg[0]} executed successfully (No stderr).")
                     
                return result

            except Exception as e:
                print(f"SIDE_EFFECT: Error executing original command '{cmd_str}': {e}")
                # Log traceback for unexpected errors during subprocess execution
                import traceback
                print(traceback.format_exc())
                # Return appropriate error object based on calling context
                return subprocess.CompletedProcess(args=cmd_arg if isinstance(cmd_arg, list) else [cmd_arg], returncode=1, stderr=str(e))

        # --- Rclone commands to mock ---
        # Rclone link (string from os.system OR subprocess.run with shell=True)
        if isinstance(cmd_arg, str) and cmd_arg.startswith('rclone link'):
            print(f"SIDE_EFFECT: Mocking rclone link (string cmd): {cmd_str}")
            # Return CompletedProcess even for string command if subprocess.run was likely used
            # Check if original call used subprocess.run (we can guess based on kwargs)
            is_subprocess_run = not kwargs.get('_os_system_call', False) # Add a flag or check args
            if is_subprocess_run:
                return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="https://fake.sharing.link/mock_folder", stderr="")
            else: # Assume os.system call
                return 0 # Simulate success code

        # Rclone link (list from subprocess.run without shell=True)
        elif isinstance(cmd_arg, list) and cmd_arg[0:2] == ['rclone', 'link']:
             print(f"SIDE_EFFECT: Mocking rclone link (list cmd): {cmd_str}")
             return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="https://fake.sharing.link/mock_folder", stderr="")

        # Rclone sync/copy (string for os.system OR subprocess.run with shell=True)
        elif isinstance(cmd_arg, str) and (cmd_arg.startswith('rclone sync') or cmd_arg.startswith('rclone copy')):
             print(f"SIDE_EFFECT: Mocking rclone sync/copy (string cmd): {cmd_str}")
             is_subprocess_run = not kwargs.get('_os_system_call', False)
             if is_subprocess_run:
                return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")
             else: # Assume os.system call
                return 0 # Simulate success code

        # Rclone sync/copy (list for subprocess.run without shell=True)
        elif isinstance(cmd_arg, list) and len(cmd_arg) >= 2 and cmd_arg[0] == 'rclone' and cmd_arg[1] in ['sync', 'copy']:
             print(f"SIDE_EFFECT: Mocking rclone sync/copy (list cmd): {cmd_str}")
             return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")

        # --- Default mock behavior for other commands ---
        print(f"SIDE_EFFECT: Default mock return for unhandled command: {cmd_str}")
        # Check if this is a subprocess.run call by checking the _os_system_call flag
        is_subprocess_run = not kwargs.get('_os_system_call', False)
        
        if is_subprocess_run:
            # For subprocess.run, always return CompletedProcess
            # Check if check=True was passed, raise if so, otherwise return success
            if kwargs.get('check'):
                 raise subprocess.CalledProcessError(returncode=1, cmd=cmd_arg, stderr="Mocked process failed check")
            return subprocess.CompletedProcess(args=cmd_arg if isinstance(cmd_arg, list) else [cmd_arg], returncode=0, stdout="", stderr="")
        else: 
            # For os.system, return integer exit code
            return 0 # Default success code

    # We need to differentiate calls to the side effect from os.system vs subprocess.run
    # A simple way is to wrap the side effect
    def os_system_wrapper(*args, **kwargs):
        # Create a copy of kwargs to avoid modifying the original
        kwargs_copy = kwargs.copy()
        kwargs_copy['_os_system_call'] = True
        return rclone_side_effect(*args, **kwargs_copy)

    def subprocess_run_wrapper(*args, **kwargs):
        # Create a copy of kwargs to avoid modifying the original
        kwargs_copy = kwargs.copy()
        kwargs_copy['_os_system_call'] = False
        return rclone_side_effect(*args, **kwargs_copy)

    mock_os_system.side_effect = os_system_wrapper
    mock_subprocess_run.side_effect = subprocess_run_wrapper

    # Mock pyperclip
    mock_pyperclip = mocker.patch('pyperclip.copy')

    # Mock os.chdir, os.getcwd, os.listdir, os.rename to simulate directory structure
    final_track_output_dir = tmp_path / "ABBA - Waterloo" # Predict the output dir path
    # Define base_name early so we can use it in the MediaFileUpload mock
    artist, title = 'ABBA', 'Waterloo'
    base_name = f"{artist} - {title}"
    
    # Create a simple mock for MediaFileUpload since we're bypassing the upload method
    mock_media_file_upload = MagicMock()
    mock_media_file_upload.filename.return_value = str(final_track_output_dir / f"{base_name} (Final Karaoke Lossless 4k).mkv")
    mocker.patch('googleapiclient.http.MediaFileUpload', return_value=mock_media_file_upload)
    
    # Use a separate mock object to hold the mocked current directory state
    # Initialize it with the actual starting CWD
    mock_current_dir_state = mocker.Mock(return_value=original_os_getcwd())

    def chdir_side_effect(path):
        abs_path = os.path.abspath(path) # Ensure consistent path format
        print(f"MOCK os.chdir: Setting mocked cwd state to: {abs_path}")
        mock_current_dir_state.return_value = abs_path
        # No actual chdir occurs

    def rename_side_effect(src, dst):
        # src is likely relative filename, dst is likely absolute path in temp dir
        current_mocked_dir = mock_current_dir_state()
        # Resolve src relative to the *mocked* CWD to get the actual source path
        # This assumes the file was created in the mocked CWD
        # Resolve src relative to mocked CWD if it's not absolute
        actual_src_path = src if os.path.isabs(src) else os.path.abspath(os.path.join(current_mocked_dir, src))
        # Resolve dst relative to mocked CWD if it's not absolute
        actual_dst_path = dst if os.path.isabs(dst) else os.path.abspath(os.path.join(current_mocked_dir, dst))

        print(f"MOCK os.rename: Called with src='{src}', dst='{dst}'. Mocked CWD='{current_mocked_dir}'")
        print(f"MOCK os.rename: Resolved actual paths: src='{actual_src_path}', dst='{actual_dst_path}'.")

        try:
            # Check if source file actually exists before attempting rename
            # Note: This check uses the *real* filesystem
            if os.path.exists(actual_src_path):
                print(f"MOCK os.rename: Source file '{actual_src_path}' exists. Attempting rename...")
                # Ensure the destination directory exists (e.g., the 'stems' dir)
                os.makedirs(os.path.dirname(actual_dst_path), exist_ok=True)
                original_os_rename(actual_src_path, actual_dst_path)
                print(f"MOCK os.rename: Actual rename successful.")
            else:
                # This can happen if the file creation step failed or was skipped.
                print(f"MOCK os.rename: Source file '{actual_src_path}' does NOT exist. Skipping actual rename.")
                # In a test, if the file *should* exist, this indicates a problem earlier
                # or a flaw in the mocking logic for where files are created.

        except Exception as e:
            print(f"MOCK os.rename: Actual rename failed for src='{actual_src_path}', dst='{actual_dst_path}': {e}")
            # Optionally re-raise if the rename failure should fail the test
            # raise e

        return None

    mocker.patch('os.chdir', side_effect=chdir_side_effect)
    # Patch getcwd to always return the value our mock state holder has
    mocker.patch('os.getcwd', new=mock_current_dir_state)
    # Temporarily commenting out the listdir mock to test if real files work
    # mocker.patch('os.listdir', side_effect=listdir_side_effect)
    # Also mock os.listdir specifically in the finalisation module
    # mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.os.listdir', side_effect=listdir_side_effect)
    mocker.patch('os.rename', side_effect=rename_side_effect)

    # Mock os.path.isfile to use the mocked CWD
    original_os_path_isfile = os.path.isfile
    def isfile_side_effect(path):
        current_mocked_dir = mock_current_dir_state()
        # Resolve path relative to mocked CWD if it's not absolute
        abs_path_to_check = path if os.path.isabs(path) else os.path.abspath(os.path.join(current_mocked_dir, path))
        print(f"MOCK os.path.isfile: Checking path='{path}'. Mocked CWD='{current_mocked_dir}'. Resolved='{abs_path_to_check}'")
        # Call the *original* isfile on the resolved absolute path in the temp FS
        exists = original_os_path_isfile(abs_path_to_check)
        print(f"MOCK os.path.isfile: Original check returned: {exists}")
        return exists
    mocker.patch('os.path.isfile', side_effect=isfile_side_effect)

    # Mock os.path.exists to use the mocked CWD
    original_os_path_exists = os.path.exists
    def exists_side_effect(path):
        current_mocked_dir = mock_current_dir_state()
        # Resolve path relative to mocked CWD if it's not absolute
        abs_path_to_check = path if os.path.isabs(path) else os.path.abspath(os.path.join(current_mocked_dir, path))
        print(f"MOCK os.path.exists: Checking path='{path}'. Mocked CWD='{current_mocked_dir}'. Resolved='{abs_path_to_check}'")
        # Call the *original* exists on the resolved absolute path in the temp FS
        exists = original_os_path_exists(abs_path_to_check)
        print(f"MOCK os.path.exists: Original check returned: {exists}")
        return exists
    mocker.patch('os.path.exists', side_effect=exists_side_effect)

    # Mock builtins.open to use the mocked CWD
    original_builtin_open = open
    def open_side_effect(file, mode='r', *args, **kwargs):
        # Check if 'file' is an integer (file descriptor)
        if isinstance(file, int):
            print(f"MOCK builtins/io.open: Received integer FD={file}. Calling original open.")
            return original_builtin_open(file, mode, *args, **kwargs)

        # If it's not an int, assume it's a path and proceed with path resolution
        current_mocked_dir = mock_current_dir_state()
        # Resolve file path relative to mocked CWD if it's not absolute
        path_to_open = file if os.path.isabs(file) else os.path.abspath(os.path.join(current_mocked_dir, file))
        print(f"MOCK builtins/io.open: Requested path='{file}', Mode='{mode}'. Mocked CWD='{current_mocked_dir}'. Resolved='{path_to_open}'")
        # Call the *original* open on the resolved absolute path in the temp FS
        try:
            # Ensure the directory exists if opening for writing
            if 'w' in mode or 'a' in mode or 'x' in mode:
                 os.makedirs(os.path.dirname(path_to_open), exist_ok=True)
            return original_builtin_open(path_to_open, mode, *args, **kwargs)
        except FileNotFoundError as e:
            print(f"MOCK builtins.open: Original open FAILED for path '{path_to_open}': {e}")
            # Re-raise the exception so the calling code sees it
            raise e
        except Exception as e:
            print(f"MOCK builtins.open: Original open FAILED unexpectedly for path '{path_to_open}': {e}")
            raise e
    mocker.patch('builtins.open', side_effect=open_side_effect)
    # Also mock io.open as zipfile might use it directly
    mocker.patch('io.open', side_effect=open_side_effect)

    # Mock os.stat to use the mocked CWD
    original_os_stat = os.stat
    def stat_side_effect(path, *args, **kwargs):
        current_mocked_dir = mock_current_dir_state()
        # Resolve path relative to mocked CWD if it's not absolute
        path_to_stat = path if os.path.isabs(path) else os.path.abspath(os.path.join(current_mocked_dir, path))
        print(f"MOCK os.stat: Requested path='{path}'. Mocked CWD='{current_mocked_dir}'. Resolved='{path_to_stat}'")
        # Call the *original* stat on the resolved absolute path in the temp FS
        try:
            return original_os_stat(path_to_stat, *args, **kwargs)
        except FileNotFoundError as e:
            print(f"MOCK os.stat: Original stat FAILED for path '{path_to_stat}': {e}")
            raise e # Re-raise the exception
        except Exception as e:
            print(f"MOCK os.stat: Original stat FAILED unexpectedly for path '{path_to_stat}': {e}")
            raise e
    mocker.patch('os.stat', side_effect=stat_side_effect)

    # Mock os.remove to use the mocked CWD
    original_os_remove = os.remove
    def remove_side_effect(path):
        current_mocked_dir = mock_current_dir_state()
        # Resolve path relative to mocked CWD if it's not absolute
        path_to_remove = path if os.path.isabs(path) else os.path.abspath(os.path.join(current_mocked_dir, path))
        print(f"MOCK os.remove: Requested path='{path}'. Mocked CWD='{current_mocked_dir}'. Resolved='{path_to_remove}'")
        # Call the *original* remove on the resolved absolute path in the temp FS
        try:
            return original_os_remove(path_to_remove)
        except FileNotFoundError as e:
            print(f"MOCK os.remove: Original remove FAILED for path '{path_to_remove}': {e}")
            raise e # Re-raise the exception
        except Exception as e:
            print(f"MOCK os.remove: Original remove FAILED unexpectedly for path '{path_to_remove}': {e}")
            raise e
    mocker.patch('os.remove', side_effect=remove_side_effect)

    # Mock ReviewServer.start to return the correction_result stored on the instance
    def mock_start_return_self_correction_result(mock_instance):
        print("MOCK ReviewServer.start: Bypassing UI, returning instance's correction_result.")
        # Assuming the ReviewServer instance stores the result in an attribute like 'correction_result'
        # We access it via the 'mock_instance' passed to the side_effect
        # The actual attribute name might differ, adjust if needed based on ReviewServer implementation
        return mock_instance.correction_result # Access the attribute on the mocked instance

    # Patch the 'start' method where ReviewServer is defined
    mocker.patch(
        'karaoke_gen.lyrics_transcriber.review.server.ReviewServer.start',
        side_effect=mock_start_return_self_correction_result,
        autospec=True
    )

    # Mock builtins.input to handle unexpected prompts
    def mock_input_side_effect(prompt=""):
        print(f"MOCK input: Received prompt: '{prompt}'")
        if "Enter the manually uploaded YouTube video ID:" in prompt:
            print("MOCK input: Returning dummy YouTube ID.")
            return "manual_mock_video_id"
        # Handle other potential prompts if necessary, or raise error
        print(f"MOCK input: WARNING - Unexpected prompt received!")
        return "unexpected_input_response" # Or raise an exception
    mocker.patch('builtins.input', side_effect=mock_input_side_effect)

    # Mock the transcribe_lyrics method to return consistent results between local and CI
    def mock_transcribe_lyrics_side_effect(*args, **kwargs):
        print("MOCK transcribe_lyrics: Bypassing real transcription, returning mock results")
        # The method expects to return a dict with file paths
        # Match the LRC file that the listdir_side_effect creates
        track_output_dir = args[3] if len(args) > 3 else kwargs.get('track_output_dir')
        artist = args[1] if len(args) > 1 else kwargs.get('artist', 'ABBA')
        title = args[2] if len(args) > 2 else kwargs.get('title', 'Waterloo')
        
        expected_lrc_path = os.path.join(track_output_dir, f"{artist} - {title} (Karaoke).lrc")
        expected_video_path = os.path.join(track_output_dir, f"{artist} - {title} (With Vocals).mkv")
        
        # Create LRC file with the same content as the listdir mock but ensure it's valid for CDG
        with open(expected_lrc_path, "w") as f:
            # Use the exact LRC format that the CDG parser expects (no metadata, 3-digit milliseconds)
            lrc_content = """[00:01.000]This is a test song
[00:03.000]With some sample lyrics here
[00:07.000]For testing purposes only
[00:09.000]Generic placeholder content
[00:13.000]Test lyrics continue
[00:15.000]End of test content"""
            f.write(lrc_content)
        
        # Create a dummy video file to satisfy the expectations
        with open(expected_video_path, "w") as f:
            f.write("dummy mkv content for testing")
        
        # Create additional files that the finalisation step expects to find
        title_mov = os.path.join(track_output_dir, f"{artist} - {title} (Title).mov")
        title_jpg = os.path.join(track_output_dir, f"{artist} - {title} (Title).jpg")
        end_mov = os.path.join(track_output_dir, f"{artist} - {title} (End).mov")
        end_jpg = os.path.join(track_output_dir, f"{artist} - {title} (End).jpg")
        original_wav = os.path.join(track_output_dir, f"{artist} - {title} (Original).wav")
        instrumental_flac = os.path.join(track_output_dir, f"{artist} - {title} (Instrumental model_bs_roformer_ep_317_sdr_12.9755.ckpt).flac")
        
        # Create these files if they don't exist yet
        for video_file in [title_mov, end_mov]:
            if not os.path.exists(video_file):
                print(f"MOCK transcribe_lyrics: Creating video file: {video_file}")
                with open(video_file, "w") as f:
                    f.write("dummy video content for testing")
        
        for image_file in [title_jpg, end_jpg]:
            if not os.path.exists(image_file):
                print(f"MOCK transcribe_lyrics: Creating image file: {image_file}")
                with open(image_file, "w") as f:
                    f.write("dummy image content for testing")
        
        if not os.path.exists(original_wav):
            print(f"MOCK transcribe_lyrics: Creating WAV file: {original_wav}")
            with open(original_wav, "w") as f:
                f.write("dummy wav content for testing")
                
        if not os.path.exists(instrumental_flac):
            print(f"MOCK transcribe_lyrics: Creating instrumental file: {instrumental_flac}")
            with open(instrumental_flac, "w") as f:
                f.write("dummy flac content for testing")
        
        print(f"MOCK transcribe_lyrics: Created all required files for finalisation in {track_output_dir}")
        
        return {
            "lrc_filepath": expected_lrc_path,
            "ass_filepath": None,  # Not needed for this test
            "corrected_lyrics_text": "Mock corrected lyrics text",
        }
    
    mocker.patch('karaoke_gen.lyrics_processor.LyricsProcessor.transcribe_lyrics', 
                 side_effect=mock_transcribe_lyrics_side_effect)

    # Mock convert_to_wav to prevent failure on dummy audio files
    def mock_convert_to_wav(input_filename, output_filename_no_extension):
        """Mock WAV conversion - just create dummy WAV file"""
        output_wav_path = f"{output_filename_no_extension}.wav"
        print(f"MOCK convert_to_wav: Creating dummy WAV file at {output_wav_path}")
        # Create a dummy WAV file
        with open(output_wav_path, "w") as f:
            f.write("dummy wav content for testing")
        return output_wav_path
    
    mocker.patch('karaoke_gen.file_handler.FileHandler.convert_to_wav', 
                 side_effect=mock_convert_to_wav)

    # Mock audio separation to prevent FFmpeg dependency issues
    def mock_process_audio_separation(audio_file, artist_title, track_output_dir):
        """Mock audio separation - just create dummy separated files"""
        print(f"MOCK process_audio_separation: Creating dummy separated files for {artist_title}")
        
        # Create dummy instrumental files (using a generic model name)
        model_name = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        instrumental_file = os.path.join(track_output_dir, f"{artist_title} (Instrumental {model_name}).flac")
        
        with open(instrumental_file, "w") as f:
            f.write("dummy instrumental audio content")
        
        # Return data in the format expected by auto_select_instrumental
        return {
            "instrumental_path": instrumental_file,
            "vocals_path": None,  # Not needed for this test
            # clean_instrumental is what auto_select_instrumental looks for
            "clean_instrumental": {
                "instrumental": instrumental_file
            },
        }
    
    mocker.patch('karaoke_gen.audio_processor.AudioProcessor.process_audio_separation', 
                 side_effect=mock_process_audio_separation)

    # Mock CDG generation to prevent FFmpeg/ffprobe dependency issues
    def mock_generate_cdg_from_lrc(*args, **kwargs):
        """Mock CDG generation - just create dummy CDG files"""
        print("MOCK generate_cdg_from_lrc: Creating dummy CDG files")
        
        # Extract directory from arguments to create files in the right place
        # The generate_cdg_from_lrc method typically returns (cdg_file, mp3_file, zip_file)
        current_dir = os.getcwd()
        
        cdg_file = os.path.join(current_dir, "ABBA - Waterloo (Karaoke).cdg")
        mp3_file = os.path.join(current_dir, "ABBA - Waterloo (Karaoke).mp3") 
        zip_file = os.path.join(current_dir, "ABBA - Waterloo (Final Karaoke CDG).zip")
        
        # Create dummy files
        with open(cdg_file, "w") as f:
            f.write("dummy CDG content")
        with open(mp3_file, "w") as f:
            f.write("dummy MP3 content")
            
        # Create a proper ZIP file containing the dummy files
        import zipfile
        with zipfile.ZipFile(zip_file, 'w') as zf:
            zf.write(cdg_file, os.path.basename(cdg_file))
            zf.write(mp3_file, os.path.basename(mp3_file))
        
        return cdg_file, mp3_file, zip_file
    
    mocker.patch('karaoke_gen.lyrics_transcriber.output.cdg.CDGGenerator.generate_cdg_from_lrc', 
                 side_effect=mock_generate_cdg_from_lrc)

    # Mock all FFmpeg command execution in finalisation to prevent system dependencies
    execute_command_calls = []  # Track calls for testing
    
    # Mock the specific video conversion methods that call FFmpeg directly
    def mock_convert_mov_to_mp4(*args, **kwargs):
        """Mock MOV to MP4 conversion - create dummy output file"""
        print("MOCK convert_mov_to_mp4: Creating dummy MP4 output")
        # The method signature is convert_mov_to_mp4(self, input_file, output_file)
        if len(args) >= 3:  # self, input_file, output_file
            input_file = args[1]
            output_file = args[2]
        else:
            input_file = kwargs.get('input_file', "ABBA - Waterloo (With Vocals).mkv")
            output_file = kwargs.get('output_file', f"{input_file.replace('.mkv', '').replace('.mov', '')}.mp4")
        
        with open(output_file, "w") as f:
            f.write("dummy mp4 content for testing")
        print(f"MOCK convert_mov_to_mp4: Created {output_file}")
        return output_file
    
    def mock_execute_command_with_fallback(*args, **kwargs):
        """Mock execute_command_with_fallback to avoid FFmpeg calls and create expected output files"""
        print("MOCK execute_command_with_fallback: Mocking hardware/software fallback")
        
        # The arguments are (self, gpu_command, cpu_command, description)
        if len(args) >= 4:
            description = args[3]
        else:
            description = kwargs.get('description', 'Unknown FFmpeg operation')
        
        print(f"MOCK execute_command_with_fallback: {description}")
        
        # Create dummy output files that the finalisation process expects
        if "Creating MP4 version with PCM audio" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossless 4k).mp4"
            with open(output_file, "w") as f:
                f.write("dummy lossless mp4 content")
            print(f"MOCK execute_command_with_fallback: Created {output_file}")
        elif "Creating MP4 version with AAC audio" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossy 4k).mp4"
            with open(output_file, "w") as f:
                f.write("dummy lossy mp4 content")
            print(f"MOCK execute_command_with_fallback: Created {output_file}")
        elif "Creating MKV version with FLAC audio" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossless 4k).mkv"
            with open(output_file, "w") as f:
                f.write("dummy mkv content")
            print(f"MOCK execute_command_with_fallback: Created {output_file}")
        elif "Encoding 720p version" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossy 720p).mp4"
            with open(output_file, "w") as f:
                f.write("dummy 720p mp4 content")
            print(f"MOCK execute_command_with_fallback: Created {output_file}")
        elif "Converting MOV video to MP4" in description:
            output_file = "ABBA - Waterloo (With Vocals).mp4"
            with open(output_file, "w") as f:
                f.write("dummy with vocals mp4 content")
            print(f"MOCK execute_command_with_fallback: Created {output_file}")
        
        return
    
    def mock_remux_with_instrumental(*args, **kwargs):
        """Mock remux_with_instrumental to create dummy output file"""
        print("MOCK remux_with_instrumental: Creating dummy karaoke MP4")
        # The method signature is remux_with_instrumental(self, with_vocals_file, instrumental_audio, output_file)
        if len(args) >= 4:  # self, with_vocals_file, instrumental_audio, output_file
            output_file = args[3]
        else:
            output_file = kwargs.get('output_file', "ABBA - Waterloo (Karaoke).mp4")
        
        with open(output_file, "w") as f:
            f.write("dummy karaoke mp4 content for testing")
        print(f"MOCK remux_with_instrumental: Created {output_file}")
        return output_file
    
    # Mock the specific encoding methods that create the output files we need
    def mock_encode_lossless_mp4(*args, **kwargs):
        """Mock encode_lossless_mp4 to create expected output file"""
        print("MOCK encode_lossless_mp4: Creating lossless MP4 output")
        output_file = "ABBA - Waterloo (Final Karaoke Lossless 4k).mp4"
        with open(output_file, "w") as f:
            f.write("dummy lossless mp4 content for testing")
        print(f"MOCK encode_lossless_mp4: Created {output_file}")
        return
    
    def mock_encode_lossy_mp4(*args, **kwargs):
        """Mock encode_lossy_mp4 to create expected output file"""
        print("MOCK encode_lossy_mp4: Creating lossy MP4 output")
        output_file = "ABBA - Waterloo (Final Karaoke Lossy 4k).mp4"
        with open(output_file, "w") as f:
            f.write("dummy lossy mp4 content for testing")
        print(f"MOCK encode_lossy_mp4: Created {output_file}")
        return
    
    def mock_encode_lossless_mkv(*args, **kwargs):
        """Mock encode_lossless_mkv to create expected output file"""
        print("MOCK encode_lossless_mkv: Creating lossless MKV output")
        output_file = "ABBA - Waterloo (Final Karaoke Lossless 4k).mkv"
        with open(output_file, "w") as f:
            f.write("dummy mkv content for testing")
        print(f"MOCK encode_lossless_mkv: Created {output_file}")
        return
    
    def mock_encode_720p_version(*args, **kwargs):
        """Mock encode_720p_version to create expected output file"""
        print("MOCK encode_720p_version: Creating 720p MP4 output")
        output_file = "ABBA - Waterloo (Final Karaoke Lossy 720p).mp4"
        with open(output_file, "w") as f:
            f.write("dummy 720p mp4 content for testing")
        print(f"MOCK encode_720p_version: Created {output_file}")
        return
    
    # Mock the video conversion methods in the finalisation module
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.convert_mov_to_mp4', 
                 side_effect=mock_convert_mov_to_mp4)
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.remux_with_instrumental', 
                 side_effect=mock_remux_with_instrumental)
    
    # Mock the specific encoding methods that create the output files
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.encode_lossless_mp4', 
                 side_effect=mock_encode_lossless_mp4)
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.encode_lossy_mp4', 
                 side_effect=mock_encode_lossy_mp4)
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.encode_lossless_mkv', 
                 side_effect=mock_encode_lossless_mkv)
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.encode_720p_version', 
                 side_effect=mock_encode_720p_version)
    


    def mock_execute_command_general(*args, **kwargs):
        """Mock execute_command for general shell commands and create expected output files"""
        print("MOCK execute_command: Mocking general command execution")
        
        # Debug the arguments
        print(f"MOCK execute_command: args = {args}")
        print(f"MOCK execute_command: kwargs = {kwargs}")
        
        # The arguments are (command, description) when called directly
        if len(args) >= 2:
            command = args[0]
            description = args[1]
        elif len(args) == 1:
            command = args[0]
            description = kwargs.get('description', 'Unknown description')
        else:
            command = kwargs.get('command', 'Unknown command')
            description = kwargs.get('description', 'Unknown description')
        
        print(f"MOCK execute_command: {description}")
        print(f"MOCK execute_command: Command: {command}")
        
        # Track this call for testing
        execute_command_calls.append((command, description))
        
        # Create dummy output files that the finalisation process expects
        if "Creating MP4 version with AAC audio" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossy 4k).mp4"
            with open(output_file, "w") as f:
                f.write("dummy lossy mp4 content")
            print(f"MOCK execute_command: Created {output_file}")
        elif "Creating MKV version with FLAC audio" in description:
            output_file = "ABBA - Waterloo (Final Karaoke Lossless 4k).mkv"
            with open(output_file, "w") as f:
                f.write("dummy mkv content")
            print(f"MOCK execute_command: Created {output_file}")
        
        return
    
    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.execute_command', 
                 side_effect=mock_execute_command_general)

    # Mock convert_file for lyrics format conversion
    def mock_convert_file(*args, **kwargs):
        """Mock TXT conversion - return simple text content"""
        print("MOCK convert_file: Creating dummy TXT conversion")
        return "This is a test song\nWith some sample lyrics here\nFor testing purposes only\nGeneric placeholder content\nTest lyrics continue\nEnd of test content"
    
    mocker.patch('lyrics_converter.LyricsConverter.convert_file', 
                 side_effect=mock_convert_file)

    # Mock find_with_vocals_file to simply return the With Vocals file we know exists
    def mock_find_with_vocals_file(self):
        print("MOCK find_with_vocals_file: Returning the known With Vocals file")
        current_dir = os.getcwd()
        print(f"MOCK find_with_vocals_file: Current working directory: {current_dir}")
        
        # List files for debugging
        try:
            files_in_dir = os.listdir(".")
            print(f"MOCK find_with_vocals_file: Files found by os.listdir('.'): {files_in_dir}")
        except Exception as e:
            print(f"MOCK find_with_vocals_file: Error calling os.listdir('.'): {e}")
        
        # Return the With Vocals file we know exists
        with_vocals_file = "ABBA - Waterloo (With Vocals).mkv"
        print(f"MOCK find_with_vocals_file: Returning: {with_vocals_file}")
        return with_vocals_file
    
    mocker.patch.object(KaraokeFinalise, 'find_with_vocals_file', mock_find_with_vocals_file)
    
    # Mock choose_instrumental_audio_file to return the instrumental file we know exists
    def mock_choose_instrumental_audio_file(self, base_name):
        print("MOCK choose_instrumental_audio_file: Returning the known instrumental file")
        instrumental_file = "ABBA - Waterloo (Instrumental model_bs_roformer_ep_317_sdr_12.9755.ckpt).flac"
        print(f"MOCK choose_instrumental_audio_file: Returning: {instrumental_file}")
        return instrumental_file
    
    mocker.patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', mock_choose_instrumental_audio_file)

    # Mock create_cdg_zip_file to create the CDG ZIP and its components
    def mock_create_cdg_zip_file(self, input_files, output_files, artist, title):
        import zipfile
        print("MOCK create_cdg_zip_file: Creating CDG ZIP and component files")
        
        # Create the component files that would be in the ZIP
        cdg_file = f"{artist} - {title} (Karaoke).cdg"
        mp3_file = f"{artist} - {title} (Karaoke).mp3"
        zip_file = f"{artist} - {title} (Final Karaoke CDG).zip"
        
        # Create dummy component files
        with open(cdg_file, "w") as f:
            f.write("dummy CDG content")
        with open(mp3_file, "w") as f:
            f.write("dummy MP3 content")
        
        # Create a proper ZIP file containing the dummy files
        with zipfile.ZipFile(zip_file, 'w') as zf:
            zf.write(cdg_file, os.path.basename(cdg_file))
            zf.write(mp3_file, os.path.basename(mp3_file))
        
        # Update output_files dictionary to include the new files
        output_files['karaoke_cdg'] = cdg_file
        output_files['karaoke_mp3'] = mp3_file
        output_files['final_karaoke_cdg_zip'] = zip_file
        
        print(f"MOCK create_cdg_zip_file: Created {zip_file} with components {cdg_file}, {mp3_file}")
    
    mocker.patch.object(KaraokeFinalise, 'create_cdg_zip_file', mock_create_cdg_zip_file)
    
    # Mock create_txt_zip_file to create the TXT ZIP and its components
    def mock_create_txt_zip_file(self, input_files, output_files):
        import zipfile
        print("MOCK create_txt_zip_file: Creating TXT ZIP and component files")
        
        # Extract artist and title from existing files
        # We can derive this from the with_vocals_mp4 path if available
        base_name = "ABBA - Waterloo"  # fallback, could be more dynamic
        
        txt_file = f"{base_name} (Karaoke).txt"
        zip_file = f"{base_name} (Final Karaoke TXT).zip"
        
        # Create dummy TXT file
        with open(txt_file, "w") as f:
            f.write("This is a test song\nWith some sample lyrics here\nFor testing purposes only\nGeneric placeholder content\nTest lyrics continue\nEnd of test content")
        
        # Create a proper ZIP file containing the TXT file
        with zipfile.ZipFile(zip_file, 'w') as zf:
            zf.write(txt_file, os.path.basename(txt_file))
        
        # Update output_files dictionary to include the new files
        output_files['karaoke_txt'] = txt_file
        output_files['final_karaoke_txt_zip'] = zip_file
        
        print(f"MOCK create_txt_zip_file: Created {zip_file} with component {txt_file}")
    
    mocker.patch.object(KaraokeFinalise, 'create_txt_zip_file', mock_create_txt_zip_file)

    # --- 3. Construct Args and Call async_main ---
    # Reusing artist and title variables defined earlier
    artist, title = 'ABBA', 'Waterloo'

    # Override sys.argv to simulate CLI invocation with the proper argument format
    sys.argv = [
        'karaoke-gen',  # program name
        str(input_audio),  # Positional: input media file
        artist,  # Positional: artist name  
        title,  # Positional: title
        '--style_params_json', str(style_params_path),  # Named: style params path
        '--organised_dir', str(organised_dir),  # Named: organised directory
        '--organised_dir_rclone_root', 'andrewdropboxfull:organised',  # Named: organised directory rclone root
        '--public_share_dir', str(public_share_dir),  # Named: public share directory
        '--rclone_destination', rclone_destination,  # Named: rclone destination
        '--brand_prefix', expected_brand_code.split('-')[0],  # Named: brand prefix (NOMAD)
        '--discord_webhook_url', discord_webhook_url,  # Named: discord webhook
        '--youtube_client_secrets_file', str(yt_secrets_path),  # Named: YouTube secrets
        '--youtube_description_file', str(yt_desc_path),  # Named: YouTube description
        '--email_template_file', str(email_template_path),  # Named: email template
        '--enable_cdg',  # Named: enable CDG creation
        '--enable_txt',  # Named: enable TXT creation
        '-y',  # Named: auto-confirm (bypass user confirmation)
        '--log_level', 'DEBUG',  # Named: log level
        '--skip_instrumental_review',  # Named: skip instrumental review UI (use auto-select)
    ]

    # Now run the actual karaoke generation pipeline
    original_argv = sys.argv
    try:
        # Call the imported function
        await async_main()
    finally:
        sys.argv = original_argv

    # --- 4. Assert Local File Outputs ---
    final_branded_dir_name = f"{expected_brand_code} - {artist} - {title}"
    final_branded_dir_path = organised_dir / final_branded_dir_name
    base_name = f"{artist} - {title}"
    final_base_name = f"{expected_brand_code} - {base_name}"

    assert final_branded_dir_path.is_dir(), f"Expected branded directory not found: {final_branded_dir_path}"

    # Check files in the final branded directory
    expected_files_in_branded_dir = [
        f"{base_name} (Final Karaoke Lossless 4k).mp4",
        f"{base_name} (Final Karaoke Lossless 4k).mkv",
        f"{base_name} (Final Karaoke Lossy 4k).mp4",
        f"{base_name} (Final Karaoke Lossy 720p).mp4",
        f"{base_name} (Final Karaoke CDG).zip",
        f"{base_name} (Final Karaoke TXT).zip",
        f"{base_name} (Karaoke).mp3", # Extracted from zip
        f"{base_name} (Karaoke).cdg", # Extracted from zip
        f"{base_name} (Karaoke).txt", # Created for zip
    ]
    for filename in expected_files_in_branded_dir:
        filepath = final_branded_dir_path / filename
        assert filepath.is_file(), f"Expected file not found in branded dir: {filepath}"
        assert filepath.stat().st_size > 0, f"File is empty: {filepath}"

    # Check files copied to public share
    expected_public_mp4 = public_share_mp4_dir / f"{final_base_name}.mp4"
    expected_public_720p = public_share_720p_dir / f"{final_base_name}.mp4"
    expected_public_cdg = public_share_cdg_dir / f"{final_base_name}.zip"

    assert expected_public_mp4.is_file(), f"Expected file not found in public share MP4: {expected_public_mp4}"
    assert expected_public_mp4.stat().st_size > 0
    assert expected_public_720p.is_file(), f"Expected file not found in public share 720p: {expected_public_720p}"
    assert expected_public_720p.stat().st_size > 0
    assert expected_public_cdg.is_file(), f"Expected file not found in public share CDG: {expected_public_cdg}"
    assert expected_public_cdg.stat().st_size > 0

    # --- 5. Assert Mock Calls ---
    # YouTube Upload - we're checking that upload_final_mp4_to_youtube_with_title_thumbnail was called
    assert mock_upload.called
    assert mock_requests_post.call_args.kwargs['json']['content'].find('manual_mock_video_id') != -1
    
    # Discord Notification
    mock_requests_post.assert_called_once_with(discord_webhook_url, json=mocker.ANY)
    discord_content = mock_requests_post.call_args[1]['json']['content']
    assert 'manual_mock_video_id' in discord_content, f"Expected 'manual_mock_video_id' in Discord content: {discord_content}"

    # Rclone Sync - check if execute_command was called with rclone copy
    # The rclone copy is executed through the mocked execute_command method  
    rclone_sync_called = any(
        'rclone copy' in command and str(public_share_dir) in command and rclone_destination in command
        for command, description in execute_command_calls
    )
    
    if not rclone_sync_called:
        print(f"\n--- DEBUG: All execute_command calls ---")
        for i, (command, description) in enumerate(execute_command_calls):
            print(f"Call {i}: command='{command}' description='{description}'")
    
    assert rclone_sync_called, "rclone sync not called through execute_command"
    
    # Debug all os.system calls
    print("\n--- Debug: ALL mock_os_system calls ---")
    for i, call_args in enumerate(mock_os_system.call_args_list):
        print(f"Call {i}: {call_args[0][0]}")
    
    # Look specifically at the rclone mock side effect in the code
    print("\n--- Important: How the mock captures rclone calls ---")
    print("The mock captures exact strings for os.system calls.")
    
    # Print all os.system calls for debugging
    print("\n--- ALL mock_os_system.call_args_list calls ---")
    all_calls = []
    for i, call_args in enumerate(mock_os_system.call_args_list):
        command = call_args[0][0]
        all_calls.append(command)
        print(f"Call {i}: '{command}'")
    
    # Extract the command from the finalise.py implementation for debugging
    expected_rclone_link_prefix = "rclone link"
    organised_dir_folder = "andrewdropboxfull:organised/NOMAD-0001 - ABBA - Waterloo"
    
    # Also look for the command in subprocess_run calls (used by the finalise.py implementation)
    print("\n--- ALL mock_subprocess_run.call_args_list calls ---")
    subprocess_all_calls = []
    for i, call_args in enumerate(mock_subprocess_run.call_args_list):
        if call_args[0]: # Check if there are positional args
            command = call_args[0][0]
            subprocess_all_calls.append(command)
            print(f"Call {i}: '{command}'")
    
    # Check for rclone link command in both os.system and subprocess.run calls
    all_system_commands = all_calls + subprocess_all_calls
    
    # Look for rclone link in all calls
    found_rclone_link = False
    matching_command = None
    
    for command in all_system_commands:
        if isinstance(command, str) and expected_rclone_link_prefix in command and organised_dir_folder in command:
            found_rclone_link = True
            matching_command = command
            print(f"\nSUCCESS: Found matching command: '{command}'")
            break
        elif isinstance(command, list) and len(command) >= 3 and command[0] == "rclone" and command[1] == "link" and organised_dir_folder in " ".join(command):
            found_rclone_link = True
            matching_command = command
            print(f"\nSUCCESS: Found matching command (list): {command}")
            break
    
    if not found_rclone_link:
        # Try to find partial matches for better error messages
        print("\nSearching for partial matches...")
        
        # For string commands
        rclone_matches = [cmd for cmd in all_system_commands if isinstance(cmd, str) and expected_rclone_link_prefix in cmd]
        if rclone_matches:
            print("\nFound commands with 'rclone link' but missing correct path:")
            for cmd in rclone_matches:
                print(f"- '{cmd}'")
        else:
            print("\nNo string commands found containing 'rclone link'")
        
        # For list commands
        rclone_list_matches = [cmd for cmd in all_system_commands if isinstance(cmd, list) and len(cmd) >= 2 and cmd[0] == "rclone" and cmd[1] == "link"]
        if rclone_list_matches:
            print("\nFound list commands with 'rclone link' but missing correct path:")
            for cmd in rclone_list_matches:
                print(f"- {cmd}")
        
        # Check for the path fragment
        path_matches = [cmd for cmd in all_system_commands if isinstance(cmd, str) and organised_dir_folder in cmd]
        if path_matches:
            print("\nFound commands with the correct path but not 'rclone link':")
            for cmd in path_matches:
                print(f"- '{cmd}'")
        
        # Final assertion with clear error message
        # Check if the command was logged in the output for debugging
        log_lines = [line for line in mock_os_system.mock_calls if "rclone link" in str(line)]
        if log_lines:
            print("\nFound logged rclone link commands in mock calls:")
            for line in log_lines:
                print(f"- {line}")
        
        error_message = f"No command found containing both '{expected_rclone_link_prefix}' and '{organised_dir_folder}'"
        print(f"\nASSERTION ERROR: {error_message}")
        assert False, error_message
    
    # Gmail Draft
    mock_youtube_service.users().drafts().create.assert_called_once()
    draft_args, draft_kwargs = mock_youtube_service.users().drafts().create.call_args
    assert draft_kwargs['userId'] == 'me'

    # Pyperclip
    # Check if it was called at least once (exact calls can be tricky with multiple potential copies)
    assert mock_pyperclip.call_count >= 1


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.slow
async def test_full_cli_integration_no_video(tmp_path, mocker):
    """Tests the full CLI workflow with --no-video flag to ensure CDG/TXT are generated but video encoding is skipped."""

    # --- 1. Setup Temporary Directories and Files (same as full test) ---
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    organised_dir = tmp_path / "organised"
    organised_dir.mkdir()
    public_share_dir = tmp_path / "public_share"
    public_share_dir.mkdir()
    public_share_mp4_dir = public_share_dir / "MP4"
    public_share_mp4_dir.mkdir()
    public_share_720p_dir = public_share_dir / "MP4-720p"
    public_share_720p_dir.mkdir()
    public_share_cdg_dir = public_share_dir / "CDG"
    public_share_cdg_dir.mkdir()

    # Copy input audio
    source_audio = "tests/data/waterloo10sec.flac"
    input_audio = data_dir / os.path.basename(source_audio)
    shutil.copy2(source_audio, input_audio)

    # Copy background images to /tmp directory
    background_images = [
        "karaoke-background-image-nomad-4k.png",
        "karaoke-title-screen-background-nomad-4k.png",
        "cdg-instrumental-background-nomad-notes.png",
        "cdg-title-screen-background-nomad-simple.png"
    ]
    for image_name in background_images:
        source_image = f"tests/data/{image_name}"
        if os.path.exists(source_image):
            dest_image = f"/tmp/{image_name}"
            shutil.copy2(source_image, dest_image)
            print(f"Copied {source_image} to {dest_image}")

    # Copy font file
    font_source = "karaoke_gen/resources/AvenirNext-Bold.ttf"
    font_dest = "/tmp/AvenirNext-Bold.ttf"
    if os.path.exists(font_source):
        shutil.copy2(font_source, font_dest)
        print(f"Copied {font_source} to {font_dest}")

    # Copy styles.json
    source_style_params = "tests/data/styles.json"
    style_params_path = data_dir / os.path.basename(source_style_params)
    shutil.copy2(source_style_params, style_params_path)

    # --- Define test variables used later in assertions ---
    artist, title = 'ABBA', 'Waterloo'
    expected_brand_code = "NOMAD-0001"
    discord_webhook_url = "https://discord.com/api/webhooks/123/abc"
    rclone_destination = "andrewdropboxfull:public_share"

    # --- Track Mock Calls ---
    youtube_upload_called = False
    discord_called = False
    rclone_sync_called = False
    modal_separation_called = False
    audioshake_transcription_called = False
    video_encoding_called = False

    # --- 2. Mock External Interactions ---
    # Mock YouTube upload tracking
    def upload_side_effect_no_video(self, *args, **kwargs):
        nonlocal youtube_upload_called
        youtube_upload_called = True
        self.youtube_video_id = 'mock_video_id'
        self.youtube_url = f"https://www.youtube.com/watch?v=mock_video_id"

    mock_upload = mocker.patch.object(
        KaraokeFinalise,
        'upload_final_mp4_to_youtube_with_title_thumbnail',
        side_effect=upload_side_effect_no_video,
        autospec=True
    )

    # Mock Discord notification tracking
    def discord_side_effect(*args, **kwargs):
        nonlocal discord_called
        discord_called = True
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        return mock_response

    mock_requests_post = mocker.patch('requests.post', side_effect=discord_side_effect)

    # Store originals for directory operations
    original_os_chdir = os.chdir
    original_os_getcwd = os.getcwd
    original_os_listdir = os.listdir

    # Track current mocked directory
    mock_cwd_stack = [str(tmp_path)]

    def mock_current_dir_state():
        return mock_cwd_stack[-1] if mock_cwd_stack else str(tmp_path)

    def chdir_side_effect(path):
        abs_path = path if os.path.isabs(path) else os.path.abspath(os.path.join(mock_current_dir_state(), path))
        # Create directory if it doesn't exist
        if not os.path.exists(abs_path):
            os.makedirs(abs_path, exist_ok=True)
            print(f"MOCK os.chdir: Created directory {abs_path}")
        mock_cwd_stack.append(abs_path)
        print(f"MOCK os.chdir: Changed to {abs_path}")
        return original_os_chdir(abs_path)

    def getcwd_side_effect():
        current = mock_current_dir_state()
        print(f"MOCK os.getcwd: Returning {current}")
        return current

    mocker.patch('os.chdir', side_effect=chdir_side_effect)
    mocker.patch('os.getcwd', side_effect=getcwd_side_effect)

    # Track execute_command calls for rclone sync detection
    execute_command_calls = []

    def mock_execute_command(command, description=""):
        nonlocal rclone_sync_called
        execute_command_calls.append((command, description))
        if 'rclone copy' in command or 'rclone sync' in command:
            rclone_sync_called = True
        print(f"MOCK execute_command: {description} - {command}")
        return 0

    mocker.patch('karaoke_gen.karaoke_finalise.karaoke_finalise.KaraokeFinalise.execute_command', mock_execute_command)

    # Mock subprocess.run for ffmpeg and rclone
    original_subprocess_run = subprocess.run

    def subprocess_run_side_effect(*args, **kwargs):
        cmd_arg = args[0] if args else kwargs.get('args', [])
        cmd_str = cmd_arg if isinstance(cmd_arg, str) else " ".join(map(str, cmd_arg))

        # Track rclone calls
        if isinstance(cmd_arg, list) and len(cmd_arg) > 0 and cmd_arg[0] == "rclone":
            nonlocal rclone_sync_called
            rclone_sync_called = True
            print(f"MOCK subprocess.run: Detected rclone command: {cmd_str}")
            return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")

        # Handle ffmpeg commands - create dummy output files
        if isinstance(cmd_arg, list) and len(cmd_arg) > 0 and cmd_arg[0] == "ffmpeg":
            print(f"MOCK subprocess.run: Simulating ffmpeg command: {cmd_str}")

            # Extract output file path
            output_path = None
            for i, arg in enumerate(cmd_arg):
                if arg.endswith((".mp4", ".mkv", ".wav", ".mp3")) and i > 0 and cmd_arg[i-1] != "-i":
                    output_path = arg
                    break

            if output_path:
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, 'w') as f:
                    f.write('dummy ffmpeg output for testing')
                print(f"MOCK subprocess.run: Created dummy file at {output_path}")

            return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")

        # Execute ffprobe and uname normally
        if isinstance(cmd_arg, list) and len(cmd_arg) > 0 and cmd_arg[0] in ["ffprobe", "uname"]:
            return original_subprocess_run(*args, **kwargs)

        # Default: return success
        return subprocess.CompletedProcess(args=cmd_arg, returncode=0, stdout="", stderr="")

    mocker.patch('subprocess.run', side_effect=subprocess_run_side_effect)

    # Mock Modal separation tracking
    def mock_process_audio_separation(audio_file, artist_title, track_output_dir):
        nonlocal modal_separation_called
        modal_separation_called = True
        print(f"MOCK process_audio_separation: Creating instrumental file for {artist_title}")

        model_name = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        instrumental_file = os.path.join(track_output_dir, f"{artist_title} (Instrumental {model_name}).flac")

        with open(instrumental_file, "w") as f:
            f.write("dummy instrumental audio content")

        return {
            "instrumental_path": instrumental_file,
            "clean_instrumental": {"instrumental": instrumental_file},
        }

    mocker.patch(
        'karaoke_gen.audio_processor.AudioProcessor.process_audio_separation',
        side_effect=mock_process_audio_separation
    )

    # Mock AudioShake transcription tracking
    def mock_transcribe_lyrics_side_effect(*args, **kwargs):
        nonlocal audioshake_transcription_called
        audioshake_transcription_called = True
        print("MOCK transcribe_lyrics: Creating LRC file")

        track_output_dir = args[3] if len(args) > 3 else kwargs.get('track_output_dir')
        artist = args[1] if len(args) > 1 else kwargs.get('artist', 'ABBA')
        title = args[2] if len(args) > 2 else kwargs.get('title', 'Waterloo')

        lrc_path = os.path.join(track_output_dir, f"{artist} - {title} (Karaoke).lrc")
        video_path = os.path.join(track_output_dir, f"{artist} - {title} (With Vocals).mkv")

        # Create LRC file
        with open(lrc_path, "w") as f:
            lrc_content = """[00:01.000]This is a test song
[00:03.000]With some sample lyrics here
[00:07.000]For testing purposes only
[00:09.000]Generic placeholder content
[00:13.000]Test lyrics continue
[00:15.000]End of test content"""
            f.write(lrc_content)

        # Create dummy video file
        with open(video_path, "w") as f:
            f.write("dummy mkv content for testing")

        # Create additional required files
        for suffix in ["(Title).mov", "(Title).jpg", "(End).mov", "(End).jpg"]:
            filepath = os.path.join(track_output_dir, f"{artist} - {title} {suffix}")
            with open(filepath, "w") as f:
                f.write("dummy content")

        return {
            "lrc_filepath": lrc_path,
            "corrected_lyrics_text": "Mock corrected lyrics text",
        }

    mocker.patch(
        'karaoke_gen.lyrics_processor.LyricsProcessor.transcribe_lyrics',
        side_effect=mock_transcribe_lyrics_side_effect
    )

    # Mock WAV conversion
    def mock_convert_to_wav(input_filename, output_filename_no_extension):
        output_wav_path = f"{output_filename_no_extension}.wav"
        print(f"MOCK convert_to_wav: Creating dummy WAV at {output_wav_path}")
        with open(output_wav_path, "w") as f:
            f.write("dummy wav content")
        return output_wav_path

    mocker.patch(
        'karaoke_gen.file_handler.FileHandler.convert_to_wav',
        side_effect=mock_convert_to_wav
    )

    # Mock CDG generation to create actual files
    def mock_create_cdg_zip_file(self, input_files, output_files, artist, title):
        import zipfile
        print("MOCK create_cdg_zip_file: Creating CDG ZIP")

        cdg_file = f"{artist} - {title} (Karaoke).cdg"
        mp3_file = f"{artist} - {title} (Karaoke).mp3"
        zip_file = f"{artist} - {title} (Final Karaoke CDG).zip"

        # Create component files
        with open(cdg_file, "w") as f:
            f.write("dummy CDG content")
        with open(mp3_file, "w") as f:
            f.write("dummy MP3 content")

        # Create ZIP
        with zipfile.ZipFile(zip_file, 'w') as zf:
            zf.write(cdg_file, os.path.basename(cdg_file))
            zf.write(mp3_file, os.path.basename(mp3_file))

        output_files['karaoke_cdg'] = cdg_file
        output_files['karaoke_mp3'] = mp3_file
        output_files['final_karaoke_cdg_zip'] = zip_file

        print(f"MOCK create_cdg_zip_file: Created {zip_file}")

    mocker.patch.object(KaraokeFinalise, 'create_cdg_zip_file', mock_create_cdg_zip_file)

    # Mock TXT generation to create actual files
    def mock_create_txt_zip_file(self, input_files, output_files):
        import zipfile
        print("MOCK create_txt_zip_file: Creating TXT ZIP")

        base_name = "ABBA - Waterloo"
        txt_file = f"{base_name} (Karaoke).txt"
        zip_file = f"{base_name} (Final Karaoke TXT).zip"

        # Create TXT file
        with open(txt_file, "w") as f:
            f.write("This is a test song\nWith some sample lyrics\nFor testing purposes")

        # Create ZIP
        with zipfile.ZipFile(zip_file, 'w') as zf:
            zf.write(txt_file, os.path.basename(txt_file))

        output_files['karaoke_txt'] = txt_file
        output_files['final_karaoke_txt_zip'] = zip_file

        print(f"MOCK create_txt_zip_file: Created {zip_file}")

    mocker.patch.object(KaraokeFinalise, 'create_txt_zip_file', mock_create_txt_zip_file)

    # Mock instrumental file finder
    def mock_find_with_vocals_file(self):
        with_vocals_file = "ABBA - Waterloo (With Vocals).mkv"
        print(f"MOCK find_with_vocals_file: Returning {with_vocals_file}")
        return with_vocals_file

    mocker.patch.object(KaraokeFinalise, 'find_with_vocals_file', mock_find_with_vocals_file)

    # Mock instrumental chooser
    def mock_choose_instrumental_audio_file(self, base_name):
        instrumental_file = "ABBA - Waterloo (Instrumental model_bs_roformer_ep_317_sdr_12.9755.ckpt).flac"
        print(f"MOCK choose_instrumental_audio_file: Returning {instrumental_file}")
        return instrumental_file

    mocker.patch.object(KaraokeFinalise, 'choose_instrumental_audio_file', mock_choose_instrumental_audio_file)

    # Mock auto_select_instrumental to return the instrumental file path
    def mock_auto_select_instrumental(track: dict, track_dir: str, logger):
        artist = track.get('artist', 'ABBA')
        title = track.get('title', 'Waterloo')
        model_name = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"
        instrumental_file = f"{artist} - {title} (Instrumental {model_name}).flac"

        # Create the instrumental file in the current directory
        with open(instrumental_file, "w") as f:
            f.write("dummy instrumental audio content")

        print(f"MOCK auto_select_instrumental: Created and returning {instrumental_file}")
        return instrumental_file

    mocker.patch('karaoke_gen.utils.gen_cli.auto_select_instrumental', mock_auto_select_instrumental)

    # Mock check_input_files_exist to bypass file existence checks
    def mock_check_input_files_exist(self, base_name, with_vocals_file, instrumental_audio_file):
        print(f"MOCK check_input_files_exist: Returning mock input files dict")
        return {
            "title_mov": f"{base_name} (Title).mov",
            "title_jpg": f"{base_name} (Title).jpg",
            "instrumental_audio": instrumental_audio_file,
            "with_vocals_mov": with_vocals_file,
            "karaoke_lrc": f"{base_name} (Karaoke).lrc",
            "end_mov": f"{base_name} (End).mov",
            "end_jpg": f"{base_name} (End).jpg",
        }

    mocker.patch.object(KaraokeFinalise, 'check_input_files_exist', mock_check_input_files_exist)

    # Mock ReviewServer to bypass UI
    def mock_start_return_self_correction_result(mock_instance):
        return mock_instance.correction_result

    mocker.patch(
        'karaoke_gen.lyrics_transcriber.review.server.ReviewServer.start',
        side_effect=mock_start_return_self_correction_result,
        autospec=True
    )

    # Mock input to handle unexpected prompts
    def mock_input_side_effect(prompt=""):
        print(f"MOCK input: Received prompt: '{prompt}'")
        return "unexpected_input_response"

    mocker.patch('builtins.input', side_effect=mock_input_side_effect)

    # Mock video encoding to track if it's called
    def mock_remux_and_encode(self, *args, **kwargs):
        nonlocal video_encoding_called
        video_encoding_called = True
        print("MOCK remux_and_encode_output_video_files: Video encoding called")

    mocker.patch.object(KaraokeFinalise, 'remux_and_encode_output_video_files', mock_remux_and_encode)

    # --- 3. Construct Args with --no-video Flag and Call async_main ---
    sys.argv = [
        'karaoke-gen',
        str(input_audio),
        artist,
        title,
        '--style_params_json', str(style_params_path),
        '--enable_cdg',
        '--enable_txt',
        '--no-video',  # KEY FLAG: Skip video encoding and distribution
        '-y',
        '--log_level', 'DEBUG',
        '--skip_instrumental_review',
    ]

    original_argv = sys.argv
    try:
        await async_main()
    finally:
        sys.argv = original_argv

    # --- 4. Assert CDG/TXT Files Were Created ---
    base_name = f"{artist} - {title}"

    # Look for CDG/TXT files in current directory (where they're generated)
    current_dir = os.getcwd()

    cdg_zip = os.path.join(current_dir, f"{base_name} (Final Karaoke CDG).zip")
    txt_zip = os.path.join(current_dir, f"{base_name} (Final Karaoke TXT).zip")

    assert os.path.isfile(cdg_zip), f"CDG ZIP not found: {cdg_zip}"
    assert os.path.getsize(cdg_zip) > 0, f"CDG ZIP is empty: {cdg_zip}"
    assert os.path.isfile(txt_zip), f"TXT ZIP not found: {txt_zip}"
    assert os.path.getsize(txt_zip) > 0, f"TXT ZIP is empty: {txt_zip}"

    print(f"SUCCESS: CDG and TXT ZIPs created: {cdg_zip}, {txt_zip}")

    # --- 5. Assert Audio Processing Still Happened ---
    assert modal_separation_called, "Modal audio separation should have been called even with --no-video"
    assert audioshake_transcription_called, "AudioShake transcription should have been called even with --no-video"

    print("SUCCESS: Audio separation and transcription were called as expected")

    # --- 6. Assert Video Encoding and Distribution Features Were Skipped ---
    assert not video_encoding_called, "Video encoding (remux_and_encode_output_video_files) should NOT have been called with --no-video"
    assert not youtube_upload_called, "YouTube upload should NOT have been called with --no-video"
    assert not discord_called, "Discord notification should NOT have been called with --no-video"
    assert not rclone_sync_called, "Rclone sync should NOT have been called with --no-video"

    print("SUCCESS: Video encoding, YouTube upload, Discord, and rclone sync were skipped as expected")