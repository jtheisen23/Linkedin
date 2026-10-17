"""The contact record every source produces."""

import re
from dataclasses import dataclass
from urllib.parse import unquote

_PUBLIC_ID_RE = re.compile(r"linkedin\.com/in/([^/?#]+)", re.IGNORECASE)


def public_id_from_url(url: str) -> str:
    """Return the vanity id from a profile URL such as linkedin.com/in/jane-doe."""
    match = _PUBLIC_ID_RE.search(url or "")
    return unquote(match.group(1)) if match else ""


@dataclass
class Contact:
    first_name: str = ""
    last_name: str = ""
    public_id: str = ""
    urn_id: str = ""
    headline: str = ""
    company: str = ""
    position: str = ""
    location: str = ""
    email: str = ""
    phone: str = ""
    connected_on: str = ""  # ISO date, YYYY-MM-DD
    source: str = ""

    @property
    def key(self) -> str:
        """Stable identity used to merge the same person across sources."""
        if self.public_id:
            return f"in:{self.public_id.lower()}"
        if self.urn_id:
            return f"urn:{self.urn_id}"
        return f"name:{self.first_name.strip().lower()} {self.last_name.strip().lower()}"

    @property
    def profile_url(self) -> str:
        return f"https://www.linkedin.com/in/{self.public_id}" if self.public_id else ""
