"""
Tests for BrandCodeService - atomic brand code allocation via Firestore counters.

Tests cover:
- Sequential allocation returns unique codes
- Recycled numbers are reused (smallest first)
- One-time initialization from Dropbox scan
- Both NOMAD and NOMADNP prefixes work independently
- Recycle then allocate round-trip
- parse_brand_code utility
"""
import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timezone

import sys
sys.modules.setdefault('google.cloud.firestore', MagicMock())
sys.modules.setdefault('google.cloud.storage', MagicMock())


def _make_doc_snapshot(exists, data=None):
    """Create a mock Firestore document snapshot."""
    doc = Mock()
    doc.exists = exists
    if data:
        doc.to_dict.return_value = data
    return doc


def _setup_service_and_mocks(mock_db, doc_snapshot):
    """Common setup: create service with fake transactional decorator."""
    mock_doc_ref = Mock()
    mock_db.collection.return_value.document.return_value = mock_doc_ref
    mock_doc_ref.get.return_value = doc_snapshot

    mock_transaction = Mock()
    mock_db.transaction.return_value = mock_transaction

    return mock_doc_ref, mock_transaction


class TestBrandCodeServiceAllocate:
    """Test allocate_brand_code method."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    def test_allocate_sequential_number(self, mock_db):
        """Test allocation returns next sequential number when no recycled numbers."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc.allocate_brand_code("NOMAD", "/Karaoke/Tracks-Organized")

        assert result == "NOMAD-1264"
        # Transaction.update should have been called to increment next_number
        mock_transaction.update.assert_called_once()
        update_args = mock_transaction.update.call_args[0]
        assert update_args[0] is mock_doc_ref  # first arg is doc_ref
        assert update_args[1]["next_number"] == 1265

    def test_allocate_uses_recycled_number(self, mock_db):
        """Test allocation pops smallest recycled number first."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [1250, 1230, 1245],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc.allocate_brand_code("NOMAD", "/path")

        assert result == "NOMAD-1230"
        # Verify recycled list was updated (1230 removed, sorted remainder)
        update_args = mock_transaction.update.call_args[0][1]
        assert update_args["recycled"] == [1245, 1250]

    def test_allocate_initializes_from_dropbox_when_no_counter(self, mock_db):
        """Test first allocation scans Dropbox and creates counter doc."""
        doc_snapshot = _make_doc_snapshot(False)
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)

            with patch.object(svc, '_get_initial_next_number', return_value=1264):
                result = svc.allocate_brand_code("NOMAD", "/Karaoke/Tracks-Organized")

        assert result == "NOMAD-1264"
        # Verify counter doc was created via transaction.set
        mock_transaction.set.assert_called_once()
        set_args = mock_transaction.set.call_args[0]
        assert set_args[0] is mock_doc_ref
        assert set_args[1]["prefix"] == "NOMAD"
        assert set_args[1]["next_number"] == 1265
        assert set_args[1]["recycled"] == []

    def test_allocate_nomadnp_prefix(self, mock_db):
        """Test NOMADNP prefix works independently."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMADNP",
            "next_number": 13,
            "recycled": [],
        })
        _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc.allocate_brand_code("NOMADNP", "/Karaoke/NP")

        assert result == "NOMADNP-0013"
        mock_db.collection.assert_called_with("brand_code_counters")
        mock_db.collection.return_value.document.assert_called_with("NOMADNP")

    def test_allocate_formats_with_leading_zeros(self, mock_db):
        """Test brand codes are zero-padded to 4 digits."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMADNP",
            "next_number": 5,
            "recycled": [],
        })
        _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc.allocate_brand_code("NOMADNP", "/path")

        assert result == "NOMADNP-0005"


class TestBrandCodeServiceRecycle:
    """Test recycle_brand_code method."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    def test_recycle_adds_number_to_pool(self, mock_db):
        """Test recycling adds number to the recycled list."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [1250],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            svc.recycle_brand_code("NOMAD", 1230)

        update_args = mock_transaction.update.call_args[0][1]
        assert 1230 in update_args["recycled"]
        assert 1250 in update_args["recycled"]

    def test_recycle_rejects_number_at_or_above_next(self, mock_db):
        """Test recycling a number >= next_number is rejected."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            # Number equal to next_number — hasn't been allocated yet
            svc.recycle_brand_code("NOMAD", 1264)

        mock_transaction.update.assert_not_called()

    def test_recycle_rejects_zero_or_negative(self, mock_db):
        """Test recycling zero or negative numbers is rejected."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            svc.recycle_brand_code("NOMAD", 0)

        mock_transaction.update.assert_not_called()

    def test_recycle_skips_duplicate(self, mock_db):
        """Test recycling a number already in the pool doesn't duplicate it."""
        doc_snapshot = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [1250],
        })
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            svc.recycle_brand_code("NOMAD", 1250)

        mock_transaction.update.assert_not_called()

    def test_recycle_no_counter_doc_logs_warning(self, mock_db):
        """Test recycling when counter doc doesn't exist logs warning and returns."""
        doc_snapshot = _make_doc_snapshot(False)
        mock_doc_ref, mock_transaction = _setup_service_and_mocks(mock_db, doc_snapshot)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            svc.recycle_brand_code("NOMAD", 1230)

        mock_transaction.update.assert_not_called()
        mock_transaction.set.assert_not_called()


class TestBrandCodeServiceRecycleRoundTrip:
    """Test recycle-then-allocate round trip."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    def test_recycle_then_allocate_reuses_number(self, mock_db):
        """Test that a recycled number is returned on next allocation."""
        mock_doc_ref = Mock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref
        mock_transaction = Mock()
        mock_db.transaction.return_value = mock_transaction

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)

            # Step 1: Recycle number 1230
            mock_doc_ref.get.return_value = _make_doc_snapshot(True, {
                "prefix": "NOMAD",
                "next_number": 1264,
                "recycled": [],
            })
            svc.recycle_brand_code("NOMAD", 1230)

            # Step 2: Allocate — should get recycled 1230
            mock_doc_ref.get.return_value = _make_doc_snapshot(True, {
                "prefix": "NOMAD",
                "next_number": 1264,
                "recycled": [1230],
            })
            result = svc.allocate_brand_code("NOMAD", "/path")

        assert result == "NOMAD-1230"


class TestInitializeFromDropbox:
    """Test _get_initial_next_number."""

    @pytest.fixture
    def mock_db(self):
        return MagicMock()

    def test_init_from_dropbox_finds_max(self, mock_db):
        """Test initialization finds the max existing number and adds 1."""
        mock_dropbox = Mock()
        mock_dropbox.is_configured = True
        mock_dropbox.list_folders.return_value = [
            "NOMAD-1260 - Artist A - Song A",
            "NOMAD-1261 - Artist B - Song B",
            "NOMAD-1263 - Artist C - Song C",
            "Some Other Folder",
        ]

        with patch('backend.services.brand_code_service.settings') as mock_settings, \
             patch('backend.services.dropbox_service.get_dropbox_service', return_value=mock_dropbox):
            mock_settings.google_cloud_project = 'test-project'

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc._get_initial_next_number("NOMAD", "/Karaoke/Tracks-Organized")

        assert result == 1264  # max(1260, 1261, 1263) + 1

    def test_init_from_dropbox_no_existing(self, mock_db):
        """Test initialization returns 1 when no existing codes found."""
        mock_dropbox = Mock()
        mock_dropbox.is_configured = True
        mock_dropbox.list_folders.return_value = ["Some Random Folder"]

        with patch('backend.services.brand_code_service.settings') as mock_settings, \
             patch('backend.services.dropbox_service.get_dropbox_service', return_value=mock_dropbox):
            mock_settings.google_cloud_project = 'test-project'

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc._get_initial_next_number("NOMAD", "/path")

        assert result == 1

    def test_init_from_dropbox_not_configured(self, mock_db):
        """Test initialization returns 1 when Dropbox not configured."""
        mock_dropbox = Mock()
        mock_dropbox.is_configured = False

        with patch('backend.services.brand_code_service.settings') as mock_settings, \
             patch('backend.services.dropbox_service.get_dropbox_service', return_value=mock_dropbox):
            mock_settings.google_cloud_project = 'test-project'

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc._get_initial_next_number("NOMAD", "/path")

        assert result == 1

    def test_init_nomadnp_prefix_independent(self, mock_db):
        """Test NOMADNP initialization only finds NOMADNP codes, not NOMAD."""
        mock_dropbox = Mock()
        mock_dropbox.is_configured = True
        mock_dropbox.list_folders.return_value = [
            "NOMADNP-0010 - Artist A",
            "NOMADNP-0012 - Artist B",
            "NOMAD-1263 - Artist C",  # Should NOT match NOMADNP prefix
        ]

        with patch('backend.services.brand_code_service.settings') as mock_settings, \
             patch('backend.services.dropbox_service.get_dropbox_service', return_value=mock_dropbox):
            mock_settings.google_cloud_project = 'test-project'

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)
            result = svc._get_initial_next_number("NOMADNP", "/Karaoke/NP")

        assert result == 13  # max(10, 12) + 1


class TestParseBrandCode:
    """Test parse_brand_code static method."""

    def test_parse_nomad_code(self):
        from backend.services.brand_code_service import BrandCodeService
        prefix, number = BrandCodeService.parse_brand_code("NOMAD-1234")
        assert prefix == "NOMAD"
        assert number == 1234

    def test_parse_nomadnp_code(self):
        from backend.services.brand_code_service import BrandCodeService
        prefix, number = BrandCodeService.parse_brand_code("NOMADNP-0012")
        assert prefix == "NOMADNP"
        assert number == 12

    def test_parse_leading_zeros(self):
        from backend.services.brand_code_service import BrandCodeService
        prefix, number = BrandCodeService.parse_brand_code("NOMAD-0001")
        assert prefix == "NOMAD"
        assert number == 1

    def test_parse_invalid_format_raises(self):
        from backend.services.brand_code_service import BrandCodeService
        with pytest.raises(ValueError, match="Invalid brand code format"):
            BrandCodeService.parse_brand_code("invalid")

    def test_parse_no_dash_raises(self):
        from backend.services.brand_code_service import BrandCodeService
        with pytest.raises(ValueError, match="Invalid brand code format"):
            BrandCodeService.parse_brand_code("NOMAD1234")

    def test_parse_lowercase_raises(self):
        from backend.services.brand_code_service import BrandCodeService
        with pytest.raises(ValueError, match="Invalid brand code format"):
            BrandCodeService.parse_brand_code("nomad-1234")


class TestCleanupDistributionRecycling:
    """Test that cleanup_distribution endpoint recycles brand codes correctly."""

    def test_parse_and_recycle_nomad_brand_code(self):
        """Test that a NOMAD brand code is correctly parsed and recycled."""
        from backend.services.brand_code_service import BrandCodeService

        prefix, number = BrandCodeService.parse_brand_code("NOMAD-1234")
        assert prefix == "NOMAD"
        assert number == 1234

    def test_parse_and_recycle_nomadnp_brand_code(self):
        """Test that a NOMADNP brand code is correctly parsed and recycled."""
        from backend.services.brand_code_service import BrandCodeService

        prefix, number = BrandCodeService.parse_brand_code("NOMADNP-0012")
        assert prefix == "NOMADNP"
        assert number == 12

    def test_recycle_called_after_successful_dropbox_delete(self):
        """Test the integration pattern: parse brand code, then recycle the number."""
        mock_db = MagicMock()
        mock_doc_ref = Mock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref
        mock_transaction = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_doc_ref.get.return_value = _make_doc_snapshot(True, {
            "prefix": "NOMAD",
            "next_number": 1264,
            "recycled": [],
        })

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)

            # Simulate what cleanup_distribution does:
            # 1. Parse the brand code
            brand_code = "NOMAD-1234"
            prefix, number = BrandCodeService.parse_brand_code(brand_code)
            # 2. Recycle it
            svc.recycle_brand_code(prefix, number)

        # Verify transaction.update was called to add 1234 to recycled pool
        mock_transaction.update.assert_called_once()
        update_args = mock_transaction.update.call_args[0][1]
        assert 1234 in update_args["recycled"]

    def test_recycle_failure_does_not_raise(self):
        """Test that recycling a brand code with no counter doc doesn't raise."""
        mock_db = MagicMock()
        mock_doc_ref = Mock()
        mock_db.collection.return_value.document.return_value = mock_doc_ref
        mock_transaction = Mock()
        mock_db.transaction.return_value = mock_transaction

        mock_doc_ref.get.return_value = _make_doc_snapshot(False)

        with patch('backend.services.brand_code_service.firestore') as mock_fs, \
             patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            def fake_transactional(func):
                def wrapper(transaction, *args, **kwargs):
                    return func(transaction, *args, **kwargs)
                return wrapper
            mock_fs.transactional = fake_transactional

            from backend.services.brand_code_service import BrandCodeService
            svc = BrandCodeService(db=mock_db)

            # Should not raise — cleanup should be resilient
            svc.recycle_brand_code("NOMAD", 1234)

        mock_transaction.update.assert_not_called()


class TestGetBrandCodeService:
    """Test singleton getter."""

    def test_returns_same_instance(self):
        """Test get_brand_code_service returns singleton."""
        with patch('backend.services.brand_code_service.settings') as mock_settings:
            mock_settings.google_cloud_project = 'test-project'

            import backend.services.brand_code_service as module
            # Reset singleton
            module._brand_code_service = None

            svc1 = module.get_brand_code_service()
            svc2 = module.get_brand_code_service()
            assert svc1 is svc2

            # Clean up
            module._brand_code_service = None


class TestIsPreservedBrandCode:
    def test_tenant_code_matching_keep_brand_code_is_preserved(self):
        from types import SimpleNamespace
        from backend.services.brand_code_service import is_preserved_brand_code

        assert is_preserved_brand_code(SimpleNamespace(keep_brand_code="VS6588"), "VS6588")

    def test_allocated_code_is_not_preserved(self):
        from types import SimpleNamespace
        from backend.services.brand_code_service import is_preserved_brand_code

        assert not is_preserved_brand_code(SimpleNamespace(keep_brand_code=None), "VSTAR-0003")
        assert not is_preserved_brand_code(SimpleNamespace(), "VSTAR-0003")
        assert not is_preserved_brand_code(SimpleNamespace(keep_brand_code="VS1"), "VSTAR-0003")
