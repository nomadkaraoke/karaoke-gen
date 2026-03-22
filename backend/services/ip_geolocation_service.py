"""
IP geolocation service with Firestore caching.

Looks up IP addresses via ip-api.com (free, no key needed) and caches
results in Firestore to avoid repeated lookups. Used by admin UI to
enrich IP displays with country, city, ISP, and ASN info.
"""
import ipaddress
import logging
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, List

import httpx
from google.cloud import firestore

from backend.config import get_settings

logger = logging.getLogger(__name__)

IP_GEOLOCATION_COLLECTION = "ip_geolocation"
CACHE_TTL_DAYS = 90  # Cache results for 90 days
IP_API_URL = "http://ip-api.com/json"
IP_API_BATCH_URL = "http://ip-api.com/batch"
IP_API_FIELDS = "status,country,countryCode,regionName,city,isp,org,as,timezone,query"
RATE_LIMIT_PER_MINUTE = 45


def _is_private_ip(ip: str) -> bool:
    """Check if an IP is private/reserved (not routable on the internet)."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_reserved or addr.is_loopback or addr.is_link_local
    except ValueError:
        return True  # Invalid IP


def _parse_ip_api_response(data: dict) -> dict:
    """Parse ip-api.com response into our standard format."""
    if data.get("status") != "success":
        return {
            "status": "fail",
            "ip": data.get("query", ""),
            "cached_at": datetime.utcnow().isoformat(),
        }

    # Parse AS field: "AS14615 Comporium Inc" -> number + name
    as_field = data.get("as", "")
    as_number = ""
    as_name = ""
    if as_field:
        parts = as_field.split(" ", 1)
        as_number = parts[0] if parts else ""
        as_name = parts[1] if len(parts) > 1 else ""

    return {
        "status": "success",
        "ip": data.get("query", ""),
        "country": data.get("country", ""),
        "country_code": data.get("countryCode", ""),
        "region": data.get("regionName", ""),
        "city": data.get("city", ""),
        "isp": data.get("isp", ""),
        "org": data.get("org", ""),
        "as_number": as_number,
        "as_name": as_name,
        "timezone": data.get("timezone", ""),
        "cached_at": datetime.utcnow().isoformat(),
    }


class IpGeolocationService:
    """IP geolocation lookup with Firestore caching."""

    def __init__(self):
        settings = get_settings()
        self.db = firestore.Client(project=settings.google_cloud_project)
        self._last_request_times: List[float] = []

    def _check_rate_limit(self):
        """Simple rate limiter — sleep if approaching 45 req/min."""
        now = time.time()
        # Remove entries older than 60 seconds
        self._last_request_times = [
            t for t in self._last_request_times if now - t < 60
        ]
        if len(self._last_request_times) >= RATE_LIMIT_PER_MINUTE - 5:
            # Getting close to limit, wait
            sleep_time = 60 - (now - self._last_request_times[0])
            if sleep_time > 0:
                logger.info(f"IP geolocation rate limit: sleeping {sleep_time:.1f}s")
                time.sleep(sleep_time)
        self._last_request_times.append(time.time())

    def _get_cached(self, ip: str) -> Optional[dict]:
        """Get cached geolocation data for an IP."""
        try:
            # Firestore doc IDs can't contain '/' so IPv6 with :: is fine,
            # but we need to sanitize for doc ID
            doc_id = ip.replace("/", "_")
            doc = self.db.collection(IP_GEOLOCATION_COLLECTION).document(doc_id).get()
            if not doc.exists:
                return None

            data = doc.to_dict()

            # Check TTL
            cached_at = data.get("cached_at", "")
            if cached_at:
                try:
                    cached_dt = datetime.fromisoformat(cached_at)
                    if datetime.utcnow() - cached_dt > timedelta(days=CACHE_TTL_DAYS):
                        return None  # Expired
                except (ValueError, TypeError):
                    pass

            return data
        except Exception:
            logger.exception(f"Error reading IP geo cache for {ip}")
            return None

    def _cache_result(self, ip: str, data: dict):
        """Store geolocation result in Firestore cache."""
        try:
            doc_id = ip.replace("/", "_")
            self.db.collection(IP_GEOLOCATION_COLLECTION).document(doc_id).set(data)
        except Exception:
            logger.exception(f"Error caching IP geo for {ip}")

    def lookup_ip(self, ip: str) -> dict:
        """
        Look up geolocation for a single IP address.

        Returns cached result if available, otherwise queries ip-api.com.
        """
        if not ip or _is_private_ip(ip):
            return {"status": "private", "ip": ip or ""}

        # Check cache
        cached = self._get_cached(ip)
        if cached:
            return cached

        # Query ip-api.com
        try:
            self._check_rate_limit()
            with httpx.Client(timeout=10) as client:
                response = client.get(
                    f"{IP_API_URL}/{ip}",
                    params={"fields": IP_API_FIELDS},
                )
                response.raise_for_status()
                data = response.json()

            result = _parse_ip_api_response(data)
            self._cache_result(ip, result)
            return result

        except Exception:
            logger.exception(f"Error looking up IP {ip}")
            return {"status": "error", "ip": ip}

    def lookup_ips_batch(self, ips: List[str]) -> Dict[str, dict]:
        """
        Look up geolocation for multiple IPs.

        Returns dict of ip -> geo data. Uses cache for known IPs,
        batches uncached lookups to ip-api.com.
        """
        results = {}
        uncached = []

        for ip in set(ips):  # Deduplicate
            if not ip or _is_private_ip(ip):
                results[ip] = {"status": "private", "ip": ip or ""}
                continue

            cached = self._get_cached(ip)
            if cached:
                results[ip] = cached
            else:
                uncached.append(ip)

        if not uncached:
            return results

        # ip-api.com batch endpoint accepts up to 100 IPs per request
        for i in range(0, len(uncached), 100):
            batch = uncached[i:i + 100]
            try:
                self._check_rate_limit()
                # Batch endpoint takes a JSON array of IPs
                payload = [
                    {"query": ip, "fields": IP_API_FIELDS}
                    for ip in batch
                ]
                with httpx.Client(timeout=15) as client:
                    response = client.post(IP_API_BATCH_URL, json=payload)
                    response.raise_for_status()
                    batch_results = response.json()

                for data in batch_results:
                    result = _parse_ip_api_response(data)
                    ip = result.get("ip", data.get("query", ""))
                    if ip:
                        results[ip] = result
                        self._cache_result(ip, result)

            except Exception:
                logger.exception(f"Error in batch IP lookup ({len(batch)} IPs)")
                # Mark failed IPs
                for ip in batch:
                    if ip not in results:
                        results[ip] = {"status": "error", "ip": ip}

        return results


# Global instance
_service = None


def get_ip_geolocation_service() -> IpGeolocationService:
    """Get the global IP geolocation service instance."""
    global _service
    if _service is None:
        _service = IpGeolocationService()
    return _service
