import pytest
import os
import pickle
from unittest.mock import patch, MagicMock, mock_open, call, ANY

# Adjust the import path
from karaoke_gen.karaoke_finalise.karaoke_finalise import KaraokeFinalise
from .test_initialization import mock_logger, basic_finaliser, MINIMAL_CONFIG # Reuse fixtures
from .test_file_input_validation import BASE_NAME, ARTIST, TITLE, TITLE_JPG # Reuse constants
from .test_ffmpeg_commands import OUTPUT_FILES as FFMPEG_OUTPUT_FILES # Reuse constants

YOUTUBE_SECRETS_FILE = "client_secrets.json"
YOUTUBE_DESC_FILE = "youtube_description.txt"
YOUTUBE_TOKEN_FILE = "/tmp/karaoke-finalise-youtube-token.pickle"

# Define expected output filenames relevant to YouTube
OUTPUT_FILES_YT = {
    "final_karaoke_lossless_mkv": FFMPEG_OUTPUT_FILES["final_karaoke_lossless_mkv"],
}
INPUT_FILES_YT = {
    "title_jpg": TITLE_JPG,
}

@pytest.fixture
def finaliser_for_yt(mock_logger):
    """Fixture for a finaliser configured for YouTube tasks."""
    config = MINIMAL_CONFIG.copy()
    config.update({
        "youtube_client_secrets_file": YOUTUBE_SECRETS_FILE,
        "youtube_description_file": YOUTUBE_DESC_FILE,
    })
    with patch.object(KaraokeFinalise, 'detect_best_aac_codec', return_value='aac'):
        finaliser = KaraokeFinalise(logger=mock_logger, **config)
    # Manually enable features for testing specific methods
    finaliser.youtube_upload_enabled = True
    return finaliser

@pytest.fixture
def mock_youtube_service():
    """Fixture for a mocked YouTube service object."""
    mock_service = MagicMock()
    # Mock chained calls
    mock_service.channels.return_value.list.return_value.execute.return_value = {"items": [{"id": "UC_test_channel_id"}]}
    mock_service.search.return_value.list.return_value.execute.return_value = {"items": []} # Default: no search results
    mock_service.videos.return_value.delete.return_value.execute.return_value = {} # Default: delete success
    mock_service.videos.return_value.insert.return_value.execute.return_value = {"id": "new_video_id"} # Default: insert success
    mock_service.thumbnails.return_value.set.return_value.execute.return_value = {} # Default: thumbnail set success
    return mock_service

@pytest.fixture
def mock_google_auth():
    """Fixture to mock Google auth dependencies."""
    # Patch build, pickle, os.path, and Request. Flow is patched in specific tests.
    with patch('googleapiclient.discovery.build') as mock_build, \
         patch('pickle.dump') as mock_pickle_dump, \
         patch('pickle.load') as mock_pickle_load, \
         patch('os.path.exists') as mock_path_exists, \
         patch('google.auth.transport.requests.Request') as mock_request, \
         patch('googleapiclient.discovery._retrieve_discovery_doc') as mock_retrieve_doc:

        # Mock credentials object centrally
        mock_credentials = MagicMock()
        mock_credentials.valid = True
        mock_credentials.expired = False
        mock_credentials.refresh_token = "fake_refresh_token"
        # Mock the refresh method on the credentials object itself
        mock_credentials.refresh = MagicMock()
        
        # Mock the discovery document retrieval to avoid file access issues
        mock_retrieve_doc.return_value = '{"kind": "discovery#restDescription", "name": "youtube", "version": "v3", "rootUrl": "https://www.googleapis.com/", "servicePath": "youtube/v3/"}'

        # Yield the necessary mocks for tests to use
        yield {
            "mock_request": mock_request,
            "mock_build": mock_build,
            "mock_pickle_dump": mock_pickle_dump,
            "mock_pickle_load": mock_pickle_load,
            "mock_path_exists": mock_path_exists,
            "mock_credentials": mock_credentials,
            "mock_retrieve_doc": mock_retrieve_doc,
        }

# --- Truncate Title Test ---
def test_truncate_to_nearest_word(basic_finaliser):
    """Test title truncation logic."""
    long_title = "This is a very long title that definitely exceeds the maximum length allowed"
    max_len = 50
    truncated = basic_finaliser.truncate_to_nearest_word(long_title, max_len)
    assert truncated == "This is a very long title that definitely exceeds ..."
    assert len(truncated) <= max_len + 4 # Allow for ellipsis

    short_title = "Short title"
    truncated_short = basic_finaliser.truncate_to_nearest_word(short_title, max_len)
    assert truncated_short == short_title

    exact_title = "Title exactly fifty characters long right here now!" # 50 chars
    truncated_exact = basic_finaliser.truncate_to_nearest_word(exact_title, max_len)
    # The current logic adds ellipsis even if it fits exactly after truncation before space
    assert truncated_exact == "Title exactly fifty characters long right here ..."

    title_with_no_space = "a"*60
    truncated_no_space = basic_finaliser.truncate_to_nearest_word(title_with_no_space, max_len)
    # If no space is found, it truncates to max_len without adding ellipsis
    assert truncated_no_space == "a"*max_len

# --- Authentication Tests ---

# Patch open for token write.
# Patch the from_client_secrets_file method to control the flow instance.
@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.InstalledAppFlow.from_client_secrets_file')
@patch("builtins.open")
def test_authenticate_youtube_new_token(mock_open, mock_from_secrets, finaliser_for_yt, mock_google_auth, mock_youtube_service):
    """Test authentication flow when no token file exists in interactive mode."""
    # Ensure interactive mode
    finaliser_for_yt.non_interactive = False
    
    mock_google_auth["mock_path_exists"].return_value = False # Token file doesn't exist
    mock_google_auth["mock_build"].return_value = mock_youtube_service

    # Configure mock_open side_effect to handle various file accesses
    mock_token_handle = mock_open().return_value
    def open_side_effect(file, mode='r', *args, **kwargs):
        if file == YOUTUBE_TOKEN_FILE and mode == 'wb':
            return mock_token_handle
        # For the secrets file read, return a default mock handle
        elif file == YOUTUBE_SECRETS_FILE and mode == 'r':
             return mock_open().return_value
        # Handle Google API discovery document reads
        elif file.endswith('.json') and 'discovery_cache' in file:
            return mock_open(read_data='{"kind": "discovery#restDescription"}').return_value
        # For any other file access, return a default mock handle
        else:
            return mock_open().return_value
    mock_open.side_effect = open_side_effect

    # Mock the flow instance that from_client_secrets_file returns
    mock_flow_instance = MagicMock()
    # Crucially, mock the run_local_server method ON THIS INSTANCE
    mock_flow_instance.run_local_server.return_value = mock_google_auth["mock_credentials"]
    mock_from_secrets.return_value = mock_flow_instance

    # Run the authentication
    service = finaliser_for_yt.authenticate_youtube()

    # Assertions
    # Check open was called for the token write
    mock_open.assert_any_call(YOUTUBE_TOKEN_FILE, "wb")
    # Ensure it wasn't called unnecessarily otherwise (implicitly checked by side_effect)

    # Check flow setup and execution
    mock_from_secrets.assert_called_once_with(
        YOUTUBE_SECRETS_FILE, scopes=["https://www.googleapis.com/auth/youtube"]
    )
    mock_google_auth["mock_path_exists"].assert_called_once_with(YOUTUBE_TOKEN_FILE)
    mock_flow_instance.run_local_server.assert_called_once_with(port=0)
    # Check token saving - use the result of the context manager __enter__
    mock_google_auth["mock_pickle_dump"].assert_called_once_with(
        mock_google_auth["mock_credentials"], mock_token_handle.__enter__()
    )
    # Check service build
    mock_google_auth["mock_build"].assert_called_once_with("youtube", "v3", credentials=mock_google_auth["mock_credentials"])
    assert service == mock_youtube_service


# No patch for open needed here, rely on pickle.load patch from fixture
def test_authenticate_youtube_load_valid_token(finaliser_for_yt, mock_google_auth, mock_youtube_service):
    """Test authentication using a valid existing token file in interactive mode."""
    # Ensure interactive mode
    finaliser_for_yt.non_interactive = False
    
    mock_google_auth["mock_path_exists"].return_value = True # Token file exists
    # Configure pickle.load to return credentials when path exists
    mock_google_auth["mock_pickle_load"].side_effect = None # Remove FileNotFoundError side effect
    mock_google_auth["mock_pickle_load"].return_value = mock_google_auth["mock_credentials"]
    mock_google_auth["mock_credentials"].valid = True # Token is valid
    mock_google_auth["mock_build"].return_value = mock_youtube_service

    # Use mock_open for the read operation context manager
    with patch("builtins.open", mock_open()) as mock_pickle_open_read:
        # Call the function
        service = finaliser_for_yt.authenticate_youtube()

    # Check the file was opened for reading
    mock_pickle_open_read.assert_called_once_with(YOUTUBE_TOKEN_FILE, "rb")
    # REMOVE duplicate call: service = finaliser_for_yt.authenticate_youtube()

    mock_google_auth["mock_path_exists"].assert_called_once_with(YOUTUBE_TOKEN_FILE)
    # Ensure pickle.load was called
    mock_google_auth["mock_pickle_load"].assert_called_once()
    # mock_google_auth["mock_flow_instance"].run_local_server.assert_not_called() # REMOVED: mock_flow_instance not available here
    mock_google_auth["mock_pickle_dump"].assert_not_called() # Should not save token again
    mock_google_auth["mock_build"].assert_called_once_with("youtube", "v3", credentials=mock_google_auth["mock_credentials"])
    assert service == mock_youtube_service


# No patch for open needed for read, patch only for write
def test_authenticate_youtube_refresh_token(finaliser_for_yt, mock_google_auth, mock_youtube_service):
    """Test authentication refreshing an expired token in interactive mode."""
    # Ensure interactive mode
    finaliser_for_yt.non_interactive = False
    
    mock_google_auth["mock_path_exists"].return_value = True # Token file exists
    # Configure pickle.load to return credentials when path exists
    mock_google_auth["mock_pickle_load"].side_effect = None # Remove FileNotFoundError side effect
    mock_google_auth["mock_pickle_load"].return_value = mock_google_auth["mock_credentials"]
    mock_google_auth["mock_credentials"].valid = False # Token is invalid
    mock_google_auth["mock_credentials"].expired = True # But expired
    mock_google_auth["mock_credentials"].refresh_token = "fake_refresh_token" # And has refresh token
    mock_google_auth["mock_build"].return_value = mock_youtube_service

    # Use mock_open for both read and write operations
    # mock_open needs to handle multiple calls (read then write)
    mock_file_handles = [mock_open().return_value, mock_open().return_value]
    with patch("builtins.open", side_effect=mock_file_handles) as mock_multi_open:
        # Get the refresh mock from the centrally mocked credentials
        mock_refresh = mock_google_auth["mock_credentials"].refresh
        service = finaliser_for_yt.authenticate_youtube()
        # Use ANY because the actual Request object is created internally
        mock_refresh.assert_called_once_with(ANY)

    mock_google_auth["mock_path_exists"].assert_called_once_with(YOUTUBE_TOKEN_FILE)
    # Ensure pickle.load was called
    mock_google_auth["mock_pickle_load"].assert_called_once()
    # Check open calls: first read ('rb'), then write ('wb')
    mock_multi_open.assert_has_calls([
        call(YOUTUBE_TOKEN_FILE, "rb"),
        call(YOUTUBE_TOKEN_FILE, "wb")
    ])
    # mock_google_auth["mock_flow_instance"].run_local_server.assert_not_called() # REMOVED: mock_flow_instance not available here
    # Ensure pickle.dump was called and the file was opened for writing
    mock_google_auth["mock_pickle_dump"].assert_called_once()
    # Assert on the write call using the mock_multi_open context manager
    assert mock_multi_open.call_args_list[-1] == call(YOUTUBE_TOKEN_FILE, "wb") # Check last call was write
    assert mock_google_auth["mock_pickle_dump"].call_args[0][0] == mock_google_auth["mock_credentials"] # Check correct object dumped
    mock_google_auth["mock_build"].assert_called_once_with("youtube", "v3", credentials=mock_google_auth["mock_credentials"])
    assert service == mock_youtube_service

def test_authenticate_youtube_non_interactive_no_credentials(finaliser_for_yt):
    """Test authentication fails in non-interactive mode without pre-stored credentials."""
    finaliser_for_yt.non_interactive = True
    finaliser_for_yt.user_youtube_credentials = None
    
    with pytest.raises(Exception, match="YouTube authentication required but running in non-interactive mode"):
        finaliser_for_yt.authenticate_youtube()

def test_authenticate_youtube_non_interactive_with_credentials(finaliser_for_yt, mock_google_auth, mock_youtube_service):
    """Test authentication succeeds in non-interactive mode with pre-stored credentials."""
    finaliser_for_yt.non_interactive = True
    finaliser_for_yt.user_youtube_credentials = {
        'token': 'fake_token',
        'refresh_token': 'fake_refresh_token',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'client_id': 'fake_client_id',
        'client_secret': 'fake_client_secret',
        'scopes': ['https://www.googleapis.com/auth/youtube']
    }
    
    mock_google_auth["mock_build"].return_value = mock_youtube_service
    
    with patch('google.oauth2.credentials.Credentials') as mock_creds_class:
        mock_creds_instance = MagicMock()
        mock_creds_instance.expired = False
        mock_creds_class.return_value = mock_creds_instance
        
        service = finaliser_for_yt.authenticate_youtube()
        
        # Check that Credentials was created with the right parameters
        mock_creds_class.assert_called_once_with(
            token='fake_token',
            refresh_token='fake_refresh_token',
            token_uri='https://oauth2.googleapis.com/token',
            client_id='fake_client_id',
            client_secret='fake_client_secret',
            scopes=['https://www.googleapis.com/auth/youtube']
        )
        
        # Check that the service was built with the credentials
        mock_google_auth["mock_build"].assert_called_once_with('youtube', 'v3', credentials=mock_creds_instance)
        assert service == mock_youtube_service

# --- Channel ID / Video Check Tests ---

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_get_channel_id(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test getting the channel ID."""
    mock_auth.return_value = mock_youtube_service
    channel_id = finaliser_for_yt.get_channel_id()
    assert channel_id == "UC_test_channel_id"
    mock_youtube_service.channels().list.assert_called_once_with(part="snippet", mine=True)

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
@patch('builtins.input', return_value='y') # Confirm match
def test_check_if_video_title_exists_found_confirm(mock_input, mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test finding an existing video and confirming."""
    finaliser_for_yt.non_interactive = False # Ensure prompts are enabled
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    found_video_id = "existing_video_id"
    found_video_title = f"{ARTIST} - {TITLE} (Karaoke) Official"
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [{"id": {"videoId": found_video_id}, "snippet": {"title": found_video_title, "channelId": "UC_test_channel_id"}}]
    }
    mock_fuzz_ratio.return_value = 85 # High similarity

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is True
    assert finaliser_for_yt.youtube_video_id == found_video_id
    assert finaliser_for_yt.youtube_url == f"https://www.youtube.com/watch?v={found_video_id}"
    mock_fuzz_ratio.assert_called_once_with(youtube_title.lower(), found_video_title.lower())
    mock_input.assert_called_once()

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
@patch('builtins.input', return_value='n') # Reject match
def test_check_if_video_title_exists_found_reject(mock_input, mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test finding an existing video but rejecting the match."""
    finaliser_for_yt.non_interactive = False # Ensure prompts are enabled
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    found_video_id = "existing_video_id"
    found_video_title = f"{ARTIST} - {TITLE} (Karaoke) Official"
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [{"id": {"videoId": found_video_id}, "snippet": {"title": found_video_title, "channelId": "UC_test_channel_id"}}]
    }
    mock_fuzz_ratio.return_value = 85

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    # If user rejects ('n'), the function should return False and not set attributes
    assert exists is False
    assert not hasattr(finaliser_for_yt, 'youtube_video_id') # ID should NOT be set
    assert finaliser_for_yt.youtube_url is None # URL should NOT be set
    assert finaliser_for_yt.skip_notifications is False # Should remain False
    mock_input.assert_called_once()

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
def test_check_if_video_title_exists_low_similarity(mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test skipping a potential match due to low similarity."""
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    found_video_id = "existing_video_id"
    found_video_title = "Completely Different Title"
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [{"id": {"videoId": found_video_id}, "snippet": {"title": found_video_title, "channelId": "UC_test_channel_id"}}]
    }
    mock_fuzz_ratio.return_value = 30 # Low similarity

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is False
    # youtube_video_id attribute is not set if no match found
    assert not hasattr(finaliser_for_yt, 'youtube_video_id')
    assert finaliser_for_yt.youtube_url is None # Check initial state remains None
    assert finaliser_for_yt.skip_notifications is False

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
@patch('builtins.input') # Should not be called
def test_check_if_video_title_exists_non_interactive(mock_input, mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test non-interactive confirmation of existing video."""
    finaliser_for_yt.non_interactive = True
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    found_video_id = "existing_video_id"
    found_video_title = f"{ARTIST} - {TITLE} (Karaoke) Official"
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [{"id": {"videoId": found_video_id}, "snippet": {"title": found_video_title, "channelId": "UC_test_channel_id"}}]
    }
    mock_fuzz_ratio.return_value = 85

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is True
    assert finaliser_for_yt.youtube_video_id == found_video_id
    mock_input.assert_not_called() # No prompt in non-interactive

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_check_if_video_title_exists_not_found(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test when no video is found."""
    mock_auth.return_value = mock_youtube_service
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {"items": []} # No results
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is False
    # youtube_video_id attribute is not set if no match found
    assert not hasattr(finaliser_for_yt, 'youtube_video_id')
    assert finaliser_for_yt.youtube_url is None # Check initial state remains None
    assert finaliser_for_yt.skip_notifications is False

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
@patch('builtins.input')
def test_check_if_video_title_exists_filters_other_channels(mock_input, mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test that videos from other channels are filtered out even if returned by search API.
    
    The YouTube search API sometimes returns results from other channels even when
    channelId parameter is specified. This test verifies we filter those out.
    """
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    
    # Simulate YouTube API bug: returns videos from other channels despite channelId filter
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [
            # Video from another channel - should be skipped
            {"id": {"videoId": "other_channel_video_1"}, "snippet": {"title": f"{ARTIST} - {TITLE}", "channelId": "UC_other_channel_1"}},
            {"id": {"videoId": "other_channel_video_2"}, "snippet": {"title": f"{ARTIST} - {TITLE} (Official)", "channelId": "UC_other_channel_2"}},
            # Video from user's channel - should be matched
            {"id": {"videoId": "my_channel_video"}, "snippet": {"title": f"{ARTIST} - {TITLE} (Karaoke)", "channelId": "UC_test_channel_id"}},
        ]
    }
    mock_fuzz_ratio.return_value = 95
    mock_input.return_value = 'y'

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is True
    # Should match the video from our channel, not the ones from other channels
    assert finaliser_for_yt.youtube_video_id == "my_channel_video"
    # fuzz.ratio should only be called for videos from our channel (1 video)
    assert mock_fuzz_ratio.call_count == 1


@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch('thefuzz.fuzz.ratio')
def test_check_if_video_title_exists_all_from_other_channels(mock_fuzz_ratio, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test that if all returned videos are from other channels, no match is found."""
    mock_auth.return_value = mock_youtube_service
    youtube_title = f"{ARTIST} - {TITLE} (Karaoke)"
    
    # All results are from other channels
    mock_youtube_service.search.return_value.list.return_value.execute.return_value = {
        "items": [
            {"id": {"videoId": "other_channel_video_1"}, "snippet": {"title": f"{ARTIST} - {TITLE}", "channelId": "UC_other_channel_1"}},
            {"id": {"videoId": "other_channel_video_2"}, "snippet": {"title": f"{ARTIST} - {TITLE} (Official)", "channelId": "UC_other_channel_2"}},
        ]
    }

    exists = finaliser_for_yt.check_if_video_title_exists_on_youtube_channel(youtube_title)

    assert exists is False
    # fuzz.ratio should never be called since all videos are filtered out
    mock_fuzz_ratio.assert_not_called()
    # Should log debug messages about skipping videos from other channels
    assert finaliser_for_yt.logger.debug.call_count >= 2

# --- Delete / Upload Tests ---

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_delete_youtube_video_success(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test successful video deletion."""
    mock_auth.return_value = mock_youtube_service
    video_id = "video_to_delete"
    result = finaliser_for_yt.delete_youtube_video(video_id)
    assert result is True
    mock_youtube_service.videos().delete.assert_called_once_with(id=video_id)
    finaliser_for_yt.logger.info.assert_any_call(f"Successfully deleted YouTube video with ID: {video_id}")

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_delete_youtube_video_failure(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test failed video deletion."""
    mock_auth.return_value = mock_youtube_service
    video_id = "video_to_delete"
    mock_youtube_service.videos.return_value.delete.return_value.execute.side_effect = Exception("API Error")
    result = finaliser_for_yt.delete_youtube_video(video_id)
    assert result is False
    mock_youtube_service.videos().delete.assert_called_once_with(id=video_id)
    finaliser_for_yt.logger.error.assert_any_call(f"Failed to delete YouTube video with ID {video_id}: API Error")

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_delete_youtube_video_already_gone_404_counts_as_deleted(mock_auth, finaliser_for_yt, mock_youtube_service):
    """A 404 means the video was already removed (e.g. in YouTube Studio): success, not failure."""
    from googleapiclient.errors import HttpError
    mock_auth.return_value = mock_youtube_service
    resp = MagicMock(status=404, reason="Not Found")
    mock_youtube_service.videos.return_value.delete.return_value.execute.side_effect = HttpError(resp, b"{}")
    assert finaliser_for_yt.delete_youtube_video("gone_video") is True
    finaliser_for_yt.logger.error.assert_not_called()

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_delete_youtube_video_403_still_fails(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Other HTTP errors (e.g. 403 wrong channel) are still failures."""
    from googleapiclient.errors import HttpError
    mock_auth.return_value = mock_youtube_service
    resp = MagicMock(status=403, reason="Forbidden")
    mock_youtube_service.videos.return_value.delete.return_value.execute.side_effect = HttpError(resp, b"{}")
    assert finaliser_for_yt.delete_youtube_video("other_video") is False

@patch.object(KaraokeFinalise, 'authenticate_youtube')
def test_delete_youtube_video_dry_run(mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test video deletion dry run."""
    finaliser_for_yt.dry_run = True
    mock_auth.return_value = mock_youtube_service
    video_id = "video_to_delete"
    result = finaliser_for_yt.delete_youtube_video(video_id)
    assert result is True
    mock_youtube_service.videos().delete.assert_not_called()
    finaliser_for_yt.logger.info.assert_any_call(f"DRY RUN: Would delete YouTube video with ID: {video_id}")


@patch('builtins.open', new_callable=mock_open, read_data="Custom description from file")
# Patch the MediaFileUpload class where it's imported in the module under test
@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.MediaFileUpload')
@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch.object(KaraokeFinalise, 'check_if_video_title_exists_on_youtube_channel', return_value=False) # Assume not exists
@patch.object(KaraokeFinalise, 'truncate_to_nearest_word', side_effect=lambda t, l: t) # Passthrough truncate
@patch('os.path.isfile', return_value=True)  # Mock isfile to allow thumbnail upload
def test_upload_youtube_success(mock_isfile, mock_truncate, mock_check_exists, mock_auth, mock_media_upload_cls, mock_open_desc, finaliser_for_yt, mock_youtube_service):
    """Test successful YouTube upload."""
    mock_auth.return_value = mock_youtube_service
    # Mock the instances returned by the MediaFileUpload constructor
    mock_video_media = MagicMock()
    mock_thumb_media = MagicMock()
    mock_media_upload_cls.side_effect = [mock_video_media, mock_thumb_media] # Return video then thumb mock

    finaliser_for_yt.upload_final_mp4_to_youtube_with_title_thumbnail(ARTIST, TITLE, INPUT_FILES_YT, OUTPUT_FILES_YT)

    expected_title = f"{ARTIST} - {TITLE} (Karaoke)"
    expected_keywords = ["karaoke", "music", "singing", "instrumental", "lyrics", ARTIST, TITLE]
    expected_description = "Custom description from file"
    expected_category_id = "10"
    expected_privacy = "public"

    mock_check_exists.assert_called_once_with(expected_title)
    mock_open_desc.assert_called_once_with(YOUTUBE_DESC_FILE, "r") # Corrected mock name
    mock_truncate.assert_called_once_with(expected_title, 95)

    # Check video insert call
    insert_call_args = mock_youtube_service.videos().insert.call_args
    assert insert_call_args[1]['part'] == "snippet,status"
    body = insert_call_args[1]['body']
    assert body['snippet']['title'] == expected_title
    assert body['snippet']['description'] == expected_description
    assert body['snippet']['tags'] == expected_keywords
    assert body['snippet']['categoryId'] == expected_category_id
    assert body['status']['privacyStatus'] == expected_privacy
    assert insert_call_args[1]['media_body'] == mock_video_media # Check correct mock instance used
    # Check MediaFileUpload constructor calls
    mock_media_upload_cls.assert_has_calls([
        call(OUTPUT_FILES_YT["final_karaoke_lossless_mkv"], mimetype="video/x-matroska", resumable=True),
        call(INPUT_FILES_YT["title_jpg"], mimetype="image/jpeg")
    ], any_order=True)

    # Check thumbnail set call
    mock_youtube_service.thumbnails().set.assert_called_once()
    thumb_call_args = mock_youtube_service.thumbnails().set.call_args
    assert thumb_call_args[1]['videoId'] == "new_video_id"
    # Ensure the media_body for thumbnail is the one created for the JPG
    assert thumb_call_args[1]['media_body'] == mock_thumb_media # Check correct mock instance used

    assert finaliser_for_yt.youtube_video_id == "new_video_id"
    assert finaliser_for_yt.youtube_url == f"https://www.youtube.com/watch?v=new_video_id"


@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.MediaFileUpload')
@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch.object(KaraokeFinalise, 'check_if_video_title_exists_on_youtube_channel', return_value=True) # Assume exists
@patch.object(KaraokeFinalise, 'delete_youtube_video')
def test_upload_youtube_exists_skip(mock_delete, mock_check_exists, mock_auth, mock_media_upload_cls, finaliser_for_yt, mock_youtube_service):
    """Test skipping upload if video exists and replace_existing is False."""
    mock_auth.return_value = mock_youtube_service
    finaliser_for_yt.youtube_video_id = "existing_id" # Set by check_if_video_title_exists
    finaliser_for_yt.youtube_url = "http://youtube.com/existing_id"

    finaliser_for_yt.upload_final_mp4_to_youtube_with_title_thumbnail(ARTIST, TITLE, INPUT_FILES_YT, OUTPUT_FILES_YT, replace_existing=False)

    mock_check_exists.assert_called_once()
    mock_delete.assert_not_called()
    mock_youtube_service.videos().insert.assert_not_called()
    mock_youtube_service.thumbnails().set.assert_not_called()
    finaliser_for_yt.logger.warning.assert_called_with(f"Video already exists on YouTube, skipping upload: {finaliser_for_yt.youtube_url}")


@patch('builtins.open', new_callable=mock_open, read_data="Desc") # Mock open for description file
@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.MediaFileUpload')
@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch.object(KaraokeFinalise, 'check_if_video_title_exists_on_youtube_channel', return_value=True) # Assume exists
@patch.object(KaraokeFinalise, 'delete_youtube_video', return_value=True) # Delete succeeds
@patch('os.path.isfile', return_value=True)  # Mock isfile to allow thumbnail upload
def test_upload_youtube_exists_replace_success(mock_isfile, mock_delete, mock_check_exists, mock_auth, mock_media_upload_cls, mock_open_desc, finaliser_for_yt, mock_youtube_service):
    """Test replacing an existing video successfully."""
    mock_auth.return_value = mock_youtube_service
    finaliser_for_yt.youtube_video_id = "existing_id" # Set by check_if_video_title_exists
    finaliser_for_yt.youtube_url = "http://youtube.com/existing_id"

    finaliser_for_yt.upload_final_mp4_to_youtube_with_title_thumbnail(ARTIST, TITLE, INPUT_FILES_YT, OUTPUT_FILES_YT, replace_existing=True)

    mock_check_exists.assert_called_once()
    mock_delete.assert_called_once_with("existing_id")
    # Check that video ID and URL were reset before upload attempt
    # (Difficult to check state mid-function, but ensure insert is called)
    mock_youtube_service.videos().insert.assert_called_once()
    mock_youtube_service.thumbnails().set.assert_called_once()
    # Check final state reflects the *new* video ID
    assert finaliser_for_yt.youtube_video_id == "new_video_id"
    assert finaliser_for_yt.youtube_url == f"https://www.youtube.com/watch?v=new_video_id"
    mock_open_desc.assert_called_once_with(YOUTUBE_DESC_FILE, "r") # Check desc file read


@patch('karaoke_gen.karaoke_finalise.karaoke_finalise.MediaFileUpload')
@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch.object(KaraokeFinalise, 'check_if_video_title_exists_on_youtube_channel', return_value=True) # Assume exists
@patch.object(KaraokeFinalise, 'delete_youtube_video', return_value=False) # Delete fails
def test_upload_youtube_exists_replace_delete_fails(mock_delete, mock_check_exists, mock_auth, mock_media_upload_cls, finaliser_for_yt, mock_youtube_service):
    """Test aborting replacement if deletion fails."""
    mock_auth.return_value = mock_youtube_service
    finaliser_for_yt.youtube_video_id = "existing_id"
    finaliser_for_yt.youtube_url = "http://youtube.com/existing_id"

    # Should not raise an exception, just log error and return
    finaliser_for_yt.upload_final_mp4_to_youtube_with_title_thumbnail(ARTIST, TITLE, INPUT_FILES_YT, OUTPUT_FILES_YT, replace_existing=True)

    mock_check_exists.assert_called_once()
    mock_delete.assert_called_once_with("existing_id")
    mock_youtube_service.videos().insert.assert_not_called() # Upload aborted
    mock_youtube_service.thumbnails().set.assert_not_called()
    finaliser_for_yt.logger.error.assert_called_with("Failed to delete existing video, aborting upload")
    # Video ID and URL should remain the old ones
    assert finaliser_for_yt.youtube_video_id == "existing_id"
    assert finaliser_for_yt.youtube_url == "http://youtube.com/existing_id"

@patch.object(KaraokeFinalise, 'authenticate_youtube')
@patch.object(KaraokeFinalise, 'check_if_video_title_exists_on_youtube_channel', return_value=False)
def test_upload_youtube_dry_run(mock_check_exists, mock_auth, finaliser_for_yt, mock_youtube_service):
    """Test YouTube upload dry run."""
    finaliser_for_yt.dry_run = True
    mock_auth.return_value = mock_youtube_service

    finaliser_for_yt.upload_final_mp4_to_youtube_with_title_thumbnail(ARTIST, TITLE, INPUT_FILES_YT, OUTPUT_FILES_YT)

    # Check should *not* happen in dry run for upload
    mock_check_exists.assert_not_called()
    mock_youtube_service.videos().insert.assert_not_called()
    mock_youtube_service.thumbnails().set.assert_not_called()
    # Check that the log message contains the expected dry run text
    dry_run_log_found = any("DRY RUN: Would upload" in call_args[0][0] for call_args in finaliser_for_yt.logger.info.call_args_list)
    assert dry_run_log_found, "Expected dry run log message for YouTube upload not found."
    # youtube_video_id is not set in dry run, so don't assert on it
    assert finaliser_for_yt.youtube_url is None
