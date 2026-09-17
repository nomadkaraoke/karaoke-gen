"""Unit tests for backend.utils.test_data email classification helpers."""

from backend.utils.test_data import (
    is_test_email,
    is_internal_email,
    is_internal_or_test_email,
)


class TestIsTestEmail:
    def test_testmail_domain_is_test(self):
        assert is_test_email("k1l43.test-123@inbox.testmail.app") is True

    def test_e2e_test_runner_account_is_test(self):
        # Persistent account impersonated by the CI canary + daily E2E runs.
        assert is_test_email("e2e-test-runner@nomadkaraoke.com") is True

    def test_e2e_test_runner_is_case_insensitive(self):
        assert is_test_email("E2E-Test-Runner@NomadKaraoke.com") is True

    def test_regular_team_account_is_not_test(self):
        # Other @nomadkaraoke.com accounts are internal, not test data.
        assert is_test_email("andrew@nomadkaraoke.com") is False

    def test_real_user_is_not_test(self):
        assert is_test_email("real@example.com") is False

    def test_empty_is_not_test(self):
        assert is_test_email("") is False
        assert is_test_email(None) is False


class TestIsInternalEmail:
    def test_nomadkaraoke_is_internal(self):
        assert is_internal_email("andrew@nomadkaraoke.com") is True

    def test_e2e_runner_is_also_internal_domain(self):
        # It lives on the internal domain; is_internal stays True. The point of
        # the TEST_EMAIL_ADDRESSES entry is that is_test_email ALSO returns True
        # so the admin dashboard's exclude_test filter hides it.
        assert is_internal_email("e2e-test-runner@nomadkaraoke.com") is True

    def test_real_user_is_not_internal(self):
        assert is_internal_email("real@example.com") is False


class TestIsInternalOrTestEmail:
    def test_e2e_runner_matches(self):
        assert is_internal_or_test_email("e2e-test-runner@nomadkaraoke.com") is True

    def test_real_user_does_not_match(self):
        assert is_internal_or_test_email("real@example.com") is False
