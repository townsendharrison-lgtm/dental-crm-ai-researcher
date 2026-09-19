"""Domain allow-list for web research. Enforced in code, not only in prompts."""
from urllib.parse import urlparse

# Short curated list of trusted admissions / dental-education hosts.
# School official domains are added per request from schools.official_url.
CURATED_SOURCE_DOMAINS = frozenset({
    "adea.org",
    "www.adea.org",
    "explorehealthcareers.org",
    "www.explorehealthcareers.org",
    "asdanet.org",
    "www.asdanet.org",
})


class DomainRejected(ValueError):
    pass


def hostname_of(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DomainRejected("URL must be http(s) with a hostname")
    return parsed.hostname.lower().rstrip(".")


def registrable_suffix(host: str) -> str:
    parts = host.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return host


def allowed_hosts_for_school(official_url: str, *, extra_domains: tuple[str, ...] = ()) -> frozenset[str]:
    school_host = hostname_of(official_url)
    hosts = set(CURATED_SOURCE_DOMAINS)
    hosts.add(school_host)
    hosts.add(registrable_suffix(school_host))
    for domain in extra_domains:
        cleaned = domain.strip().lower().rstrip(".")
        if cleaned:
            hosts.add(cleaned)
    return frozenset(hosts)


def official_hosts_for_school(official_url: str) -> frozenset[str]:
    """School domain only (plus www) — used when discovering trusted pages via search."""
    school_host = hostname_of(official_url)
    suffix = registrable_suffix(school_host)
    hosts = {school_host, suffix, f"www.{suffix}"}
    return frozenset(hosts)


def trusted_discovery_hosts(official_url: str) -> frozenset[str]:
    """Official school hosts + ADEA (trusted admissions directory). No blogs/rankers."""
    hosts = set(official_hosts_for_school(official_url))
    hosts.update({"adea.org", "www.adea.org"})
    return frozenset(hosts)


def is_allowed_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    try:
        host = hostname_of(url)
    except DomainRejected:
        return False
    if host in allowed_hosts:
        return True
    return any(host == allowed or host.endswith("." + allowed) for allowed in allowed_hosts)


def assert_allowed_url(url: str, allowed_hosts: frozenset[str]) -> str:
    if not is_allowed_url(url, allowed_hosts):
        raise DomainRejected("Source domain is not on the allow-list")
    return url.strip()
