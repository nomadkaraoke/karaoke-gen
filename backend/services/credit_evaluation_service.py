"""
AI-powered credit evaluation service.

Evaluates whether to grant free credits (welcome or feedback) by collecting
abuse signals and asking Gemini for a grant/deny decision. Fail-open: if
the AI call fails for any reason, credits are granted.
"""
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.config import get_settings

logger = logging.getLogger(__name__)

CREDIT_EVALUATIONS_COLLECTION = "credit_evaluations"


@dataclass
class CreditEvaluation:
    """Result of an AI credit evaluation."""
    decision: str  # "grant" or "deny"
    reasoning: str
    confidence: float
    error: Optional[str] = None  # Set if evaluation failed (decision defaults to "grant")


SYSTEM_PROMPT = """You are an anti-abuse evaluator for Nomad Karaoke, a web service that creates professional karaoke videos. Each job costs the service real money in API credits and cloud compute.

New users get 2 free welcome credits to try the service. Users who complete 2 jobs and submit feedback get 2 more credits. Some users abuse this by creating multiple accounts to get unlimited free karaoke generation.

Your job: Given the signals below, decide whether to GRANT or DENY free credits to this user.

## Decision Guidelines

**DENY if any of these are true:**
- The user's device fingerprint matches other accounts (strong signal of multi-accounting)
- The user's signup IP matches other accounts AND those accounts also have suspicious patterns (e.g., no spend, same user agent)
- There's a clear pattern of account creation from the same device/network to farm free credits

**GRANT if:**
- This appears to be a genuine new user with no suspicious correlations
- The IP match is explainable (e.g., different fingerprints suggest different actual users on a shared network like a school or office)
- There's insufficient evidence to confidently flag abuse

**When in doubt, GRANT.** False positives (blocking real users) are worse than false negatives (letting an occasional abuser through). We'd rather lose a few dollars than alienate a real customer.

## Response Format

Respond with ONLY a JSON object, no other text:
{"decision": "grant" or "deny", "reasoning": "Brief explanation", "confidence": 0.0 to 1.0}
"""


def _build_user_prompt(
    email: str,
    grant_type: str,
    user_data: dict,
    fingerprint_matches: list,
    ip_matches: list,
    ip_geo: Optional[dict],
    recent_signups_ip: int,
    recent_signups_fp: int,
    user_agent: Optional[str],
    feedback_content: Optional[dict] = None,
) -> str:
    """Build the user prompt with all abuse signals."""
    lines = [
        f"## Credit Grant Request",
        f"- **Email:** {email}",
        f"- **Grant type:** {grant_type} credits",
        f"- **User's device fingerprint:** {user_data.get('device_fingerprint', 'unknown')}",
        f"- **User's signup IP:** {user_data.get('signup_ip', 'unknown')}",
        f"- **User agent:** {user_agent or 'unknown'}",
        f"- **Account created:** {user_data.get('created_at', 'unknown')}",
        f"- **Jobs created:** {user_data.get('total_jobs_created', 0)}",
        f"- **Jobs completed:** {user_data.get('total_jobs_completed', 0)}",
        f"- **Total spent (real money):** ${user_data.get('total_spent', 0) / 100:.2f}",
        f"- **Current credits:** {user_data.get('credits', 0)}",
        "",
    ]

    # IP geolocation
    if ip_geo and ip_geo.get("status") == "success":
        lines.extend([
            f"## IP Geolocation",
            f"- **Country:** {ip_geo.get('country', 'unknown')} ({ip_geo.get('country_code', '')})",
            f"- **City/Region:** {ip_geo.get('city', '')}, {ip_geo.get('region', '')}",
            f"- **ISP:** {ip_geo.get('isp', 'unknown')}",
            f"- **Organization:** {ip_geo.get('org', 'unknown')}",
            f"- **ASN:** {ip_geo.get('as_number', '')} {ip_geo.get('as_name', '')}",
            "",
        ])

    # Recent signup patterns
    lines.extend([
        f"## Recent Signup Patterns (last 24 hours)",
        f"- **New accounts from same IP:** {recent_signups_ip}",
        f"- **New accounts from same fingerprint:** {recent_signups_fp}",
        "",
    ])

    # Fingerprint correlations
    if fingerprint_matches:
        lines.append("## Other Accounts with Same Device Fingerprint (RED FLAG)")
        for match in fingerprint_matches:
            lines.append(
                f"- {match.get('email', '?')} | "
                f"Jobs: {match.get('total_jobs_created', 0)} | "
                f"Spent: ${match.get('total_spent', 0) / 100:.2f} | "
                f"Created: {match.get('created_at', '?')}"
            )
        lines.append("")
    else:
        lines.extend(["## Other Accounts with Same Device Fingerprint", "None found.", ""])

    # IP correlations
    if ip_matches:
        lines.append("## Other Accounts from Same Signup IP")
        for match in ip_matches:
            lines.append(
                f"- {match.get('email', '?')} | "
                f"Fingerprint: {match.get('device_fingerprint', 'unknown')} | "
                f"Jobs: {match.get('total_jobs_created', 0)} | "
                f"Spent: ${match.get('total_spent', 0) / 100:.2f} | "
                f"Created: {match.get('created_at', '?')}"
            )
        lines.append("")
    else:
        lines.extend(["## Other Accounts from Same Signup IP", "None found.", ""])

    # Feedback content (if applicable)
    if feedback_content:
        lines.extend([
            "## Feedback Submitted",
            f"- **Overall rating:** {feedback_content.get('overall_rating', '?')}/5",
            f"- **What went well:** {feedback_content.get('what_went_well', 'N/A')[:200]}",
            f"- **What could improve:** {feedback_content.get('what_could_improve', 'N/A')[:200]}",
            f"- **Additional comments:** {feedback_content.get('additional_comments', 'N/A')[:200]}",
            "",
        ])

    lines.append("Based on these signals, should we GRANT or DENY free credits to this user?")
    return "\n".join(lines)


def _parse_gemini_response(response_text: str) -> CreditEvaluation:
    """Parse Gemini's JSON response into a CreditEvaluation."""
    try:
        # Strip markdown code fences if present
        text = response_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)
        decision = data.get("decision", "").lower()
        if decision not in ("grant", "deny"):
            decision = "pending_review"  # fail-closed: unknown decision → manual review

        return CreditEvaluation(
            decision=decision,
            reasoning=str(data.get("reasoning", "No reasoning provided")),
            confidence=float(data.get("confidence", 0.5)),
        )
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        logger.warning(f"Failed to parse Gemini response: {e}. Response: {response_text[:200]}")
        return CreditEvaluation(
            decision="pending_review",
            reasoning="Failed to parse AI response — pending manual review",
            confidence=0.0,
            error=f"Parse error: {e}",
        )


class CreditEvaluationService:
    """Evaluates credit grant requests using Gemini AI."""

    def __init__(self):
        self.settings = get_settings()

    def _collect_signals(self, email: str) -> dict:
        """Collect all abuse signals for evaluation."""
        from backend.services.user_service import get_user_service
        from backend.services.ip_geolocation_service import get_ip_geolocation_service

        user_service = get_user_service()
        user = user_service.get_user(email)

        if not user:
            return {
                "user_data": {},
                "fingerprint_matches": [],
                "ip_matches": [],
                "ip_geo": None,
                "recent_signups_ip": 0,
                "recent_signups_fp": 0,
                "user_agent": None,
            }

        user_data = {
            "email": user.email,
            "device_fingerprint": user.device_fingerprint,
            "signup_ip": user.signup_ip,
            "credits": user.credits,
            "total_jobs_created": user.total_jobs_created,
            "total_jobs_completed": user.total_jobs_completed,
            "total_spent": user.total_spent,
            "created_at": str(user.created_at),
        }

        # Find other accounts with same fingerprint
        fingerprint_matches = []
        if user.device_fingerprint:
            fp_users = user_service.find_users_by_fingerprint(user.device_fingerprint)
            fingerprint_matches = [
                {
                    "email": u.email,
                    "total_jobs_created": u.total_jobs_created,
                    "total_spent": u.total_spent,
                    "created_at": str(u.created_at),
                }
                for u in fp_users if u.email != email
            ]

        # Find other accounts from same IP
        ip_matches = []
        if user.signup_ip:
            ip_users = user_service.find_users_by_signup_ip(user.signup_ip)
            ip_matches = [
                {
                    "email": u.email,
                    "device_fingerprint": u.device_fingerprint,
                    "total_jobs_created": u.total_jobs_created,
                    "total_spent": u.total_spent,
                    "created_at": str(u.created_at),
                }
                for u in ip_users if u.email != email
            ]

        # IP geolocation
        ip_geo = None
        if user.signup_ip:
            try:
                geo_service = get_ip_geolocation_service()
                ip_geo = geo_service.lookup_ip(user.signup_ip)
            except Exception:
                pass

        # Recent signup counts
        recent_signups_ip = 0
        recent_signups_fp = 0
        if user.signup_ip:
            recent_signups_ip = user_service.count_recent_signups_from_ip(user.signup_ip)
        if user.device_fingerprint:
            recent_signups_fp = user_service.count_recent_signups_from_fingerprint(user.device_fingerprint)

        # User agent from latest session
        user_agent = None
        try:
            from google.cloud.firestore_v1 import FieldFilter
            from google.cloud import firestore as fs
            sessions = (
                user_service.db.collection("sessions")
                .where(filter=FieldFilter("user_email", "==", email))
                .order_by("created_at", direction=fs.Query.DESCENDING)
                .limit(1)
                .stream()
            )
            for session_doc in sessions:
                user_agent = session_doc.to_dict().get("user_agent")
        except Exception:
            pass

        return {
            "user_data": user_data,
            "fingerprint_matches": fingerprint_matches,
            "ip_matches": ip_matches,
            "ip_geo": ip_geo,
            "recent_signups_ip": recent_signups_ip,
            "recent_signups_fp": recent_signups_fp,
            "user_agent": user_agent,
        }

    def _call_gemini(self, prompt: str) -> str:
        """Call Gemini and return the response text."""
        import google.generativeai as genai

        model = genai.GenerativeModel(
            model_name=self.settings.credit_eval_model,
            system_instruction=SYSTEM_PROMPT,
        )
        response = model.generate_content(prompt)
        return response.text

    def _log_evaluation(
        self,
        email: str,
        grant_type: str,
        evaluation: CreditEvaluation,
        signals: dict,
    ):
        """Log the evaluation decision to Firestore for audit trail."""
        try:
            from google.cloud import firestore as fs
            db = fs.Client(project=self.settings.google_cloud_project)
            doc_id = str(uuid.uuid4())
            db.collection(CREDIT_EVALUATIONS_COLLECTION).document(doc_id).set({
                "id": doc_id,
                "email": email,
                "grant_type": grant_type,
                "decision": evaluation.decision,
                "reasoning": evaluation.reasoning,
                "confidence": evaluation.confidence,
                "error": evaluation.error,
                "model": self.settings.credit_eval_model,
                "signals_snapshot": {
                    "fingerprint_match_count": len(signals.get("fingerprint_matches", [])),
                    "ip_match_count": len(signals.get("ip_matches", [])),
                    "recent_signups_ip": signals.get("recent_signups_ip", 0),
                    "recent_signups_fp": signals.get("recent_signups_fp", 0),
                    "user_ip": signals.get("user_data", {}).get("signup_ip"),
                    "user_fingerprint": signals.get("user_data", {}).get("device_fingerprint"),
                },
                "evaluated_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception:
            logger.exception(f"Failed to log credit evaluation for {email}")

    def evaluate(
        self,
        email: str,
        grant_type: str,
        feedback_content: Optional[dict] = None,
    ) -> CreditEvaluation:
        """
        Evaluate whether to grant free credits.

        Args:
            email: User's email
            grant_type: "welcome" or "feedback"
            feedback_content: Feedback form data (for feedback grants only)

        Returns:
            CreditEvaluation with decision, reasoning, confidence.
            Fail-closed: returns "pending_review" on any error (admin notified).
        """
        if not self.settings.credit_eval_enabled:
            return CreditEvaluation(
                decision="grant",
                reasoning="Credit evaluation disabled — auto-granting",
                confidence=1.0,
            )

        try:
            # Collect signals
            signals = self._collect_signals(email)

            # Quick-grant: if no correlations found at all, skip AI call
            if (
                not signals["fingerprint_matches"]
                and not signals["ip_matches"]
                and signals["recent_signups_ip"] <= 1
                and signals["recent_signups_fp"] <= 1
            ):
                evaluation = CreditEvaluation(
                    decision="grant",
                    reasoning="No suspicious correlations found — clean user",
                    confidence=1.0,
                )
                self._log_evaluation(email, grant_type, evaluation, signals)
                return evaluation

            # Build prompt and call Gemini
            prompt = _build_user_prompt(
                email=email,
                grant_type=grant_type,
                user_data=signals["user_data"],
                fingerprint_matches=signals["fingerprint_matches"],
                ip_matches=signals["ip_matches"],
                ip_geo=signals["ip_geo"],
                recent_signups_ip=signals["recent_signups_ip"],
                recent_signups_fp=signals["recent_signups_fp"],
                user_agent=signals["user_agent"],
                feedback_content=feedback_content,
            )

            response_text = self._call_gemini(prompt)
            evaluation = _parse_gemini_response(response_text)

            self._log_evaluation(email, grant_type, evaluation, signals)
            logger.info(
                f"Credit evaluation for {email} ({grant_type}): "
                f"{evaluation.decision} (confidence={evaluation.confidence:.2f})"
            )
            return evaluation

        except Exception as e:
            logger.exception(f"Credit evaluation failed for {email} — pending manual review (fail-closed)")
            evaluation = CreditEvaluation(
                decision="pending_review",
                reasoning="Evaluation failed — pending manual review",
                confidence=0.0,
                error=str(e),
            )
            try:
                self._log_evaluation(email, grant_type, evaluation, {})
            except Exception:
                pass
            return evaluation


_service_instance: Optional[CreditEvaluationService] = None


def get_credit_evaluation_service() -> CreditEvaluationService:
    """Get or create the singleton credit evaluation service."""
    global _service_instance
    if _service_instance is None:
        _service_instance = CreditEvaluationService()
    return _service_instance
