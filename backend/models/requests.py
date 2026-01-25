"""
API request models for karaoke generation endpoints.
"""
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, HttpUrl, validator


class URLSubmissionRequest(BaseModel):
    """Request to submit a job from a URL (YouTube, etc.)."""
    url: HttpUrl
    artist: Optional[str] = None  # Auto-detected if not provided
    title: Optional[str] = None   # Auto-detected if not provided
    
    # Optional preferences
    enable_cdg: bool = False  # Requires style config
    enable_txt: bool = False  # Requires style config
    enable_youtube_upload: Optional[bool] = None  # None = use server default
    youtube_description: Optional[str] = None
    webhook_url: Optional[str] = None
    user_email: Optional[str] = None


class UploadSubmissionRequest(BaseModel):
    """Request to submit a job from an uploaded file."""
    artist: str
    title: str

    # Optional preferences
    enable_cdg: bool = False  # Requires style config
    enable_txt: bool = False  # Requires style config
    enable_youtube_upload: Optional[bool] = None  # None = use server default
    youtube_description: Optional[str] = None
    webhook_url: Optional[str] = None
    user_email: Optional[str] = None


class CorrectionsSubmission(BaseModel):
    """
    Request to submit corrected lyrics after human review.
    
    This is the critical human-in-the-loop interaction point.
    The corrections data comes from the lyrics-transcriber review interface.
    """
    corrections: Dict[str, Any]  # Full corrections JSON from frontend
    user_notes: Optional[str] = None  # Optional notes from reviewer
    
    @validator('corrections')
    def validate_corrections_format(cls, v):
        """Validate corrections has required fields."""
        required_fields = ['lines', 'metadata']
        for field in required_fields:
            if field not in v:
                raise ValueError(f"Corrections must include '{field}' field")
        return v


class InstrumentalSelection(BaseModel):
    """
    Request to select instrumental audio option.

    This is the second critical human-in-the-loop interaction point.
    User chooses between clean instrumental, instrumental with backing vocals,
    or a custom instrumental (created via create-custom-instrumental endpoint).
    """
    selection: str  # "clean", "with_backing", or "custom"

    @validator('selection')
    def validate_selection(cls, v):
        """Validate selection is a valid option."""
        valid_options = ['clean', 'with_backing', 'custom']
        if v not in valid_options:
            raise ValueError(f"Selection must be one of: {valid_options}")
        return v


class CompleteReviewRequest(BaseModel):
    """
    Request to complete the review with optional instrumental selection.

    For the combined review flow, this includes the instrumental selection.
    For legacy flows, the instrumental_selection can be omitted.
    """
    instrumental_selection: Optional[str] = None  # "clean", "with_backing", or "custom"

    @validator('instrumental_selection')
    def validate_instrumental_selection(cls, v):
        """Validate instrumental selection if provided."""
        if v is None:
            return v
        valid_options = ['clean', 'with_backing', 'custom']
        if v not in valid_options:
            raise ValueError(f"instrumental_selection must be one of: {valid_options}")
        return v


class MuteRegionRequest(BaseModel):
    """A region to mute in the backing vocals."""
    start_seconds: float
    end_seconds: float
    
    @validator('start_seconds')
    def validate_start(cls, v):
        if v < 0:
            raise ValueError("start_seconds must be non-negative")
        return v
    
    @validator('end_seconds')
    def validate_end(cls, v, values):
        if 'start_seconds' in values and v <= values['start_seconds']:
            raise ValueError("end_seconds must be greater than start_seconds")
        return v


class CreateCustomInstrumentalRequest(BaseModel):
    """
    Request to create a custom instrumental with muted backing vocal regions.
    
    The mute_regions specify time ranges in the backing vocals track that
    should be silenced before mixing with the clean instrumental.
    """
    mute_regions: List[MuteRegionRequest]
    
    @validator('mute_regions')
    def validate_regions(cls, v):
        if not v:
            raise ValueError("At least one mute region is required")
        return v


class StartReviewRequest(BaseModel):
    """Request to mark job as in-review (user opened interface)."""
    pass  # No body needed, just triggers state transition


class CancelJobRequest(BaseModel):
    """Request to cancel a job."""
    reason: Optional[str] = None


class RetryJobRequest(BaseModel):
    """Request to retry a failed job."""
    from_stage: Optional[str] = None  # Optional: restart from specific stage

