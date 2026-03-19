"""
Unit tests for EmailValidationService.

Tests email normalization, disposable domain detection, and blocklist management.
"""

import pytest
from unittest.mock import Mock, MagicMock, patch

# Mock Google Cloud before imports
import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()


class TestEmailNormalization:
    """Test email normalization logic."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Firestore client."""
        mock = MagicMock()
        # Default: empty blocklist config
        mock_doc = Mock()
        mock_doc.exists = False
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        """Create EmailValidationService instance with mocks."""
        from backend.services.email_validation_service import EmailValidationService
        service = EmailValidationService(db=mock_db)
        return service

    # =========================================================================
    # Gmail Normalization Tests
    # =========================================================================

    def test_normalize_gmail_removes_dots(self, email_service):
        """Test that dots are removed from Gmail local part."""
        result = email_service.normalize_email("j.o.h.n@gmail.com")
        assert result == "john@gmail.com"

    def test_normalize_gmail_removes_plus_suffix(self, email_service):
        """Test that +tag is removed from Gmail local part."""
        result = email_service.normalize_email("john+spam@gmail.com")
        assert result == "john@gmail.com"

    def test_normalize_gmail_removes_dots_and_plus(self, email_service):
        """Test both dots and +tag are removed from Gmail."""
        result = email_service.normalize_email("j.o.h.n+newsletter@gmail.com")
        assert result == "john@gmail.com"

    def test_normalize_googlemail_treated_like_gmail(self, email_service):
        """Test that googlemail.com is normalized like gmail.com."""
        result = email_service.normalize_email("j.o.h.n+test@googlemail.com")
        assert result == "john@googlemail.com"

    def test_normalize_gmail_to_lowercase(self, email_service):
        """Test Gmail addresses are lowercased."""
        result = email_service.normalize_email("JOHN.DOE+Test@GMAIL.COM")
        assert result == "johndoe@gmail.com"

    # =========================================================================
    # Non-Gmail Normalization Tests
    # =========================================================================

    def test_normalize_non_gmail_preserves_dots(self, email_service):
        """Test dots are preserved for non-Gmail domains."""
        result = email_service.normalize_email("j.o.h.n@example.com")
        assert result == "j.o.h.n@example.com"

    def test_normalize_non_gmail_preserves_plus(self, email_service):
        """Test +tag is preserved for non-Gmail domains."""
        result = email_service.normalize_email("john+tag@company.com")
        assert result == "john+tag@company.com"

    def test_normalize_non_gmail_to_lowercase(self, email_service):
        """Test non-Gmail addresses are lowercased."""
        result = email_service.normalize_email("John.Doe@Example.Com")
        assert result == "john.doe@example.com"

    def test_normalize_strips_whitespace(self, email_service):
        """Test whitespace is stripped."""
        result = email_service.normalize_email("  john@example.com  ")
        assert result == "john@example.com"

    # =========================================================================
    # Edge Cases
    # =========================================================================

    def test_normalize_empty_string(self, email_service):
        """Test empty string returns empty."""
        result = email_service.normalize_email("")
        assert result == ""

    def test_normalize_none_returns_empty(self, email_service):
        """Test None returns empty string."""
        result = email_service.normalize_email(None)
        assert result == ""

    def test_normalize_invalid_no_at_sign(self, email_service):
        """Test email without @ is returned as-is (lowercase)."""
        result = email_service.normalize_email("notanemail")
        assert result == "notanemail"


class TestDisposableDomainDetection:
    """Test disposable domain detection."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Firestore client with new data model."""
        mock = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com", "mailinator.com", "guerrillamail.com"],
            "manual_domains": [],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        """Create EmailValidationService instance with mocks."""
        from backend.services.email_validation_service import EmailValidationService
        # Clear any cached blocklist
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None
        service = EmailValidationService(db=mock_db)
        return service

    def test_detect_known_disposable_domain(self, email_service):
        """Test known disposable domains are detected."""
        assert email_service.is_disposable_domain("user@tempmail.com") is True
        assert email_service.is_disposable_domain("user@mailinator.com") is True
        assert email_service.is_disposable_domain("user@guerrillamail.com") is True

    def test_detect_legitimate_domain(self, email_service):
        """Test legitimate domains are not flagged."""
        assert email_service.is_disposable_domain("user@gmail.com") is False
        assert email_service.is_disposable_domain("user@yahoo.com") is False
        assert email_service.is_disposable_domain("user@company.com") is False

    def test_detect_case_insensitive(self, email_service):
        """Test domain detection is case-insensitive."""
        assert email_service.is_disposable_domain("user@TEMPMAIL.COM") is True
        assert email_service.is_disposable_domain("user@TempMail.Com") is True

    def test_detect_invalid_email_returns_false(self, email_service):
        """Test invalid email returns False."""
        assert email_service.is_disposable_domain("notanemail") is False
        assert email_service.is_disposable_domain("") is False


class TestBlocklistManagement:
    """Test blocklist management functionality."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock Firestore client with blocklist."""
        mock = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "disposable_domains": ["custom-temp.com"],
            "blocked_emails": ["spammer@example.com"],
            "blocked_ips": ["192.168.1.100"],
        }
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        """Create EmailValidationService instance with mocks."""
        from backend.services.email_validation_service import EmailValidationService
        # Clear any cached blocklist
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None
        service = EmailValidationService(db=mock_db)
        return service

    def test_is_email_blocked(self, email_service):
        """Test blocked email detection."""
        assert email_service.is_email_blocked("spammer@example.com") is True
        assert email_service.is_email_blocked("legitimate@example.com") is False

    def test_is_email_blocked_case_insensitive(self, email_service):
        """Test blocked email detection is case-insensitive."""
        assert email_service.is_email_blocked("SPAMMER@example.com") is True
        assert email_service.is_email_blocked("Spammer@Example.Com") is True

    def test_is_ip_blocked(self, email_service):
        """Test blocked IP detection."""
        assert email_service.is_ip_blocked("192.168.1.100") is True
        assert email_service.is_ip_blocked("10.0.0.1") is False

    def test_custom_disposable_domain_added(self, email_service):
        """Test custom disposable domains from Firestore are included."""
        assert email_service.is_disposable_domain("user@custom-temp.com") is True

    def test_blocklist_caching(self, email_service, mock_db):
        """Test blocklist is cached."""
        # First call
        email_service.is_disposable_domain("user@test.com")
        # Second call should use cache
        email_service.is_disposable_domain("user@test2.com")

        # Should only call Firestore once due to caching
        assert mock_db.collection.return_value.document.return_value.get.call_count == 1


class TestEffectiveBlocklist:
    """Test effective blocklist computation with new data model."""

    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com", "mailinator.com", "allowed-one.com"],
            "manual_domains": ["my-custom.com"],
            "allowlisted_domains": ["allowed-one.com"],
            "blocked_emails": ["bad@example.com"],
            "blocked_ips": ["1.2.3.4"],
        }
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        from backend.services.email_validation_service import EmailValidationService
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None
        return EmailValidationService(db=mock_db)

    def test_effective_set_excludes_allowlisted(self, email_service):
        config = email_service.get_blocklist_config()
        assert "tempmail.com" in config["disposable_domains"]
        assert "mailinator.com" in config["disposable_domains"]
        assert "my-custom.com" in config["disposable_domains"]
        assert "allowed-one.com" not in config["disposable_domains"]

    def test_allowlisted_domain_not_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@allowed-one.com") is False

    def test_external_domain_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@tempmail.com") is True

    def test_manual_domain_blocked(self, email_service):
        assert email_service.is_disposable_domain("user@my-custom.com") is True


class TestNewDataModelCRUD:
    """Test CRUD operations with the new data model."""

    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com", "mailinator.com"],
            "manual_domains": ["my-custom.com"],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock.collection.return_value.document.return_value.get.return_value = mock_doc
        return mock

    @pytest.fixture
    def email_service(self, mock_db):
        from backend.services.email_validation_service import EmailValidationService
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None
        return EmailValidationService(db=mock_db)

    def test_add_disposable_domain_writes_to_manual(self, email_service, mock_db):
        """Test add_disposable_domain writes to manual_domains field."""
        email_service.add_disposable_domain("new-spam.com", "admin@test.com")
        # Get the transaction set call args
        transaction = mock_db.transaction.return_value
        set_call = transaction.set
        assert set_call.called
        data = set_call.call_args[0][1]
        assert "new-spam.com" in data["manual_domains"]
        assert "my-custom.com" in data["manual_domains"]

    def test_remove_disposable_domain_manual(self, email_service, mock_db):
        """Test removing a manual domain removes from manual_domains."""
        email_service.remove_disposable_domain("my-custom.com", "admin@test.com")
        transaction = mock_db.transaction.return_value
        set_call = transaction.set
        assert set_call.called
        data = set_call.call_args[0][1]
        assert "my-custom.com" not in data["manual_domains"]

    def test_remove_disposable_domain_external_adds_to_allowlist(self, email_service, mock_db):
        """Test removing an external domain adds it to allowlisted_domains."""
        email_service.remove_disposable_domain("tempmail.com", "admin@test.com")
        transaction = mock_db.transaction.return_value
        set_call = transaction.set
        assert set_call.called
        data = set_call.call_args[0][1]
        assert "tempmail.com" in data["allowlisted_domains"]
        # external_domains should still contain it (not removed, just allowlisted)
        assert "tempmail.com" in data["external_domains"]

    def test_add_allowlisted_domain(self, email_service, mock_db):
        """Test add_allowlisted_domain writes to allowlisted_domains field."""
        email_service.add_allowlisted_domain("legit.com", "admin@test.com")
        transaction = mock_db.transaction.return_value
        set_call = transaction.set
        assert set_call.called
        data = set_call.call_args[0][1]
        assert "legit.com" in data["allowlisted_domains"]

    def test_remove_allowlisted_domain(self, email_service, mock_db):
        """Test remove_allowlisted_domain removes from allowlisted_domains."""
        # Set up mock with an existing allowlisted domain
        mock_doc = Mock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com"],
            "manual_domains": [],
            "allowlisted_domains": ["legit.com"],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = email_service.remove_allowlisted_domain("legit.com", "admin@test.com")
        assert result is True
        transaction = mock_db.transaction.return_value
        set_call = transaction.set
        assert set_call.called
        data = set_call.call_args[0][1]
        assert "legit.com" not in data["allowlisted_domains"]

    def test_remove_allowlisted_domain_not_found(self, email_service, mock_db):
        """Test remove_allowlisted_domain returns False when domain not found."""
        result = email_service.remove_allowlisted_domain("nonexistent.com", "admin@test.com")
        assert result is False

    def test_get_blocklist_raw_data(self, email_service, mock_db):
        """Test get_blocklist_raw_data returns all raw fields."""
        from backend.services.email_validation_service import EmailValidationService
        # Clear cache so it reads fresh
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None

        raw = email_service.get_blocklist_raw_data()
        assert "external_domains" in raw
        assert "manual_domains" in raw
        assert "allowlisted_domains" in raw
        assert "blocked_emails" in raw
        assert "blocked_ips" in raw
        assert "last_sync_at" in raw
        assert "last_sync_count" in raw
        assert "updated_at" in raw
        assert "updated_by" in raw
        # Check sorted
        assert raw["external_domains"] == sorted(["tempmail.com", "mailinator.com"])
        assert raw["manual_domains"] == ["my-custom.com"]

    def test_get_blocklist_raw_data_no_doc(self, email_service, mock_db):
        """Test get_blocklist_raw_data returns defaults when doc doesn't exist."""
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        raw = email_service.get_blocklist_raw_data()
        assert raw["external_domains"] == []
        assert raw["manual_domains"] == []
        assert raw["allowlisted_domains"] == []
        assert raw["last_sync_at"] is None

    def test_get_blocklist_stats_new_model(self, email_service, mock_db):
        """Test get_blocklist_stats returns counts for new data model fields."""
        from backend.services.email_validation_service import EmailValidationService
        EmailValidationService._blocklist_cache = None
        EmailValidationService._blocklist_cache_time = None

        stats = email_service.get_blocklist_stats()
        assert stats["external_domains_count"] == 2
        assert stats["manual_domains_count"] == 1
        assert stats["allowlisted_domains_count"] == 0
        assert stats["disposable_domains_count"] == 3  # 2 external + 1 manual - 0 allowlisted
        assert stats["blocked_emails_count"] == 0
        assert stats["blocked_ips_count"] == 0


class TestIPHashing:
    """Test IP address hashing."""

    @pytest.fixture
    def email_service(self):
        """Create EmailValidationService instance with mocks."""
        mock_db = MagicMock()
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        from backend.services.email_validation_service import EmailValidationService
        return EmailValidationService(db=mock_db)

    def test_hash_ip_consistent(self, email_service):
        """Test IP hashing produces consistent results."""
        hash1 = email_service.hash_ip("192.168.1.1")
        hash2 = email_service.hash_ip("192.168.1.1")
        assert hash1 == hash2

    def test_hash_ip_different_for_different_ips(self, email_service):
        """Test different IPs produce different hashes."""
        hash1 = email_service.hash_ip("192.168.1.1")
        hash2 = email_service.hash_ip("192.168.1.2")
        assert hash1 != hash2

    def test_hash_ip_is_sha256(self, email_service):
        """Test IP hash is SHA-256 (64 hex chars)."""
        hash_result = email_service.hash_ip("192.168.1.1")
        assert len(hash_result) == 64
        assert all(c in "0123456789abcdef" for c in hash_result)
