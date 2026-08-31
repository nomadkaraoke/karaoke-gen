"""
Emulator integration tests for the vanity referral request approve/deny flow.

Exercises ReferralService against a REAL Firestore emulator so the multi-doc
rename migration (copy → migrate attribution → disable old) is verified for real,
not mocked. Run with: scripts/run-emulator-tests.sh
"""
import uuid

import pytest
from google.cloud import firestore

from backend.models.referral import ReferralLink, ReferralLinkStats
from backend.services.referral_service import (
    ReferralService,
    REFERRAL_LINKS_COLLECTION,
    REFERRAL_EARNINGS_COLLECTION,
    REFERRAL_VANITY_REQUESTS_COLLECTION,
    USERS_COLLECTION,
)


@pytest.fixture
def service():
    """ReferralService backed by the Firestore emulator."""
    return ReferralService(db=firestore.Client())


def _uniq() -> str:
    return uuid.uuid4().hex[:8]


def _seed_link(service, code, owner_email, **overrides):
    """Create a referral link doc directly in the emulator."""
    data = ReferralLink(
        code=code,
        owner_email=owner_email,
        display_name="Seeded",
        stats=ReferralLinkStats(clicks=5, signups=2, purchases=1, total_earned_cents=500),
        **overrides,
    ).model_dump()
    service.db.collection(REFERRAL_LINKS_COLLECTION).document(code).set(data)
    return data


class TestRenameLink:
    def test_preserves_stats_and_disables_old(self, service):
        suffix = _uniq()
        old, new = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)

        ok, link, _ = service.rename_link(old, new)

        assert ok is True
        assert link.code == new
        assert link.is_vanity is True
        assert link.enabled is True
        # Stats carried over
        assert link.stats.clicks == 5
        assert link.stats.signups == 2
        assert link.stats.total_earned_cents == 500

        # New doc exists & enabled; old doc kept but disabled with a breadcrumb
        new_doc = service.db.collection(REFERRAL_LINKS_COLLECTION).document(new).get().to_dict()
        old_doc = service.db.collection(REFERRAL_LINKS_COLLECTION).document(old).get().to_dict()
        assert new_doc["enabled"] is True
        assert old_doc["enabled"] is False
        assert old_doc["renamed_to"] == new

    def test_rejects_invalid_new_code(self, service):
        suffix = _uniq()
        old = f"old{suffix}"
        _seed_link(service, old, f"{suffix}@example.com")

        ok, link, msg = service.rename_link(old, "admin")  # reserved

        assert ok is False
        assert link is None

    def test_rejects_taken_code(self, service):
        suffix = _uniq()
        old, taken = f"old{suffix}", f"taken{suffix}"
        _seed_link(service, old, f"{suffix}@example.com")
        _seed_link(service, taken, f"other{suffix}@example.com")

        ok, link, msg = service.rename_link(old, taken)

        assert ok is False
        assert "taken" in msg.lower()

    def test_migrates_attribution(self, service):
        suffix = _uniq()
        old, new = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)

        # A referred user and an earning both point at the old code
        referred_email = f"referred{suffix}@example.com"
        service.db.collection(USERS_COLLECTION).document(referred_email).set({
            "email": referred_email,
            "referred_by_code": old,
        })
        earning_id = f"earn{suffix}"
        service.db.collection(REFERRAL_EARNINGS_COLLECTION).document(earning_id).set({
            "id": earning_id,
            "referral_code": old,
            "referrer_email": owner,
        })

        service.rename_link(old, new)

        user_doc = service.db.collection(USERS_COLLECTION).document(referred_email).get().to_dict()
        earning_doc = service.db.collection(REFERRAL_EARNINGS_COLLECTION).document(earning_id).get().to_dict()
        assert user_doc["referred_by_code"] == new
        assert earning_doc["referral_code"] == new

    def test_get_or_create_link_prefers_enabled_after_rename(self, service):
        suffix = _uniq()
        old, new = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)
        service.rename_link(old, new)

        # Owner now has a disabled old doc + enabled new doc with same owner_email
        link = service.get_or_create_link(owner)
        assert link.code == new
        assert link.enabled is True


class TestVanityRequestFlow:
    def test_create_and_list(self, service):
        suffix = _uniq()
        owner = f"{suffix}@example.com"
        service.create_vanity_request(owner, f"old{suffix}", f"yt{suffix}")

        pending = service.list_vanity_requests(status="pending")
        assert any(r.owner_email == owner and r.desired_code == f"yt{suffix}" for r in pending)

    def test_rerequest_overwrites(self, service):
        suffix = _uniq()
        owner = f"{suffix}@example.com"
        service.create_vanity_request(owner, f"old{suffix}", f"first{suffix}")
        service.create_vanity_request(owner, f"old{suffix}", f"second{suffix}")

        req = service.get_vanity_request(owner)
        assert req.desired_code == f"second{suffix}"
        # Only one doc for this owner
        docs = list(
            service.db.collection(REFERRAL_VANITY_REQUESTS_COLLECTION)
            .where("owner_email", "==", owner)
            .stream()
        )
        assert len(docs) == 1

    def test_approve_renames_link_and_marks_approved(self, service):
        suffix = _uniq()
        old, desired = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)
        service.create_vanity_request(owner, old, desired)

        ok, link, _ = service.approve_vanity_request(owner, resolved_by="admin@nomadkaraoke.com")

        assert ok is True
        assert link.code == desired
        assert link.stats.clicks == 5  # preserved
        req = service.get_vanity_request(owner)
        assert req.status == "approved"
        assert req.resolved_by == "admin@nomadkaraoke.com"

    def test_approve_fails_when_desired_code_taken(self, service):
        suffix = _uniq()
        old, desired = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)
        # Someone else already owns the desired code
        _seed_link(service, desired, f"squatter{suffix}@example.com")
        service.create_vanity_request(owner, old, desired)

        ok, link, msg = service.approve_vanity_request(owner)

        assert ok is False
        assert link is None
        # Request stays pending so the admin can retry / deny
        assert service.get_vanity_request(owner).status == "pending"

    def test_approve_twice_is_rejected(self, service):
        suffix = _uniq()
        old, desired = f"old{suffix}", f"yt{suffix}"
        owner = f"{suffix}@example.com"
        _seed_link(service, old, owner)
        service.create_vanity_request(owner, old, desired)

        service.approve_vanity_request(owner)
        ok, _, msg = service.approve_vanity_request(owner)

        assert ok is False
        assert "approved" in msg.lower()

    def test_deny_marks_denied(self, service):
        suffix = _uniq()
        owner = f"{suffix}@example.com"
        service.create_vanity_request(owner, f"old{suffix}", f"yt{suffix}")

        ok, _ = service.deny_vanity_request(owner, resolved_by="admin@nomadkaraoke.com", note="taken")

        assert ok is True
        req = service.get_vanity_request(owner)
        assert req.status == "denied"
        assert req.note == "taken"
