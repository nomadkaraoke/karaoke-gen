import pytest
from unittest.mock import MagicMock

import sys
sys.modules['google.cloud.firestore'] = MagicMock()
sys.modules['google.cloud.storage'] = MagicMock()


class TestParseExternalBlocklist:
    def test_parse_newline_delimited_domains(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "tempmail.com\nmailinator.com\n\nguerrillamail.com\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com", "guerrillamail.com"}

    def test_parse_strips_whitespace(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "  tempmail.com  \n  mailinator.com\t\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com"}

    def test_parse_lowercases_domains(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        text = "TempMail.COM\nMailinator.com\n"
        result = parse_blocklist_text(text)
        assert result == {"tempmail.com", "mailinator.com"}

    def test_parse_empty_text(self):
        from backend.services.disposable_domain_sync_service import parse_blocklist_text
        assert parse_blocklist_text("") == set()
        assert parse_blocklist_text("  \n\n  ") == set()


class TestFetchExternalBlocklist:
    @pytest.mark.asyncio
    async def test_fetch_success(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "tempmail.com\nmailinator.com\n"
        mock_response.headers = {"content-length": "30"}

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            domains = await fetch_external_blocklist()
            assert domains == {"tempmail.com", "mailinator.com"}

    @pytest.mark.asyncio
    async def test_fetch_rejects_too_many_domains(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        fake_text = "\n".join(f"domain{i}.com" for i in range(50_001))
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = fake_text
        mock_response.headers = {"content-length": str(len(fake_text))}

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(ValueError, match="exceeds maximum"):
                await fetch_external_blocklist()

    @pytest.mark.asyncio
    async def test_fetch_rejects_non_200(self):
        from backend.services.disposable_domain_sync_service import fetch_external_blocklist
        from unittest.mock import AsyncMock, patch

        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status = MagicMock(side_effect=Exception("Server Error"))

        with patch("backend.services.disposable_domain_sync_service.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            with pytest.raises(Exception):
                await fetch_external_blocklist()


class TestSyncDisposableDomains:
    @pytest.fixture
    def mock_db(self):
        mock = MagicMock()
        return mock

    def test_sync_first_run_migration(self, mock_db):
        """First sync: migrate old disposable_domains to manual_domains."""
        from unittest.mock import patch
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "disposable_domains": ["tempmail.com", "custom-temp.com"],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com"}

        with patch("backend.services.disposable_domain_sync_service.DEFAULT_DISPOSABLE_DOMAINS", set()):
            result = sync_disposable_domains(mock_db, external_domains)

        assert result["migrated_to_manual"] == ["custom-temp.com"]
        assert result["external_count"] == 2

        mock_db.collection.return_value.document.return_value.set.assert_called_once()
        call_data = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert set(call_data["external_domains"]) == {"tempmail.com", "mailinator.com"}
        assert set(call_data["manual_domains"]) == {"custom-temp.com"}
        assert "disposable_domains" not in call_data

    def test_sync_subsequent_run(self, mock_db):
        """Subsequent sync: just replace external_domains."""
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com"],
            "manual_domains": ["my-custom.com"],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com", "newdomain.com"}

        result = sync_disposable_domains(mock_db, external_domains)

        assert result["external_count"] == 3
        assert result["added"] == 2
        assert result["removed"] == 0

    def test_sync_cleans_redundant_manual_domains(self, mock_db):
        """Sync removes manual domains that now appear in external list."""
        from backend.services.disposable_domain_sync_service import sync_disposable_domains

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {
            "external_domains": ["tempmail.com"],
            "manual_domains": ["mailinator.com", "my-custom.com"],
            "allowlisted_domains": [],
            "blocked_emails": [],
            "blocked_ips": [],
        }
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        external_domains = {"tempmail.com", "mailinator.com"}

        result = sync_disposable_domains(mock_db, external_domains)

        call_data = mock_db.collection.return_value.document.return_value.set.call_args[0][0]
        assert set(call_data["manual_domains"]) == {"my-custom.com"}
