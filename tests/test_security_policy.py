"""A public repo tells finders how to report a vulnerability privately.

GitHub's private vulnerability reporting is the channel, so the policy names
no person and no email address: the report form is the only contact.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "SECURITY.md"
FORM = "https://github.com/Back-Road-Creative/quiet-corners/security/advisories/new"


def test_security_policy_points_to_private_reporting() -> None:
    text = POLICY.read_text(encoding="utf-8")
    assert FORM in text, "SECURITY.md must link the private report form"
    assert "public issue" in text, (
        "SECURITY.md must ask finders not to file a public issue"
    )


def test_security_policy_names_no_email() -> None:
    text = POLICY.read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), (
        "the form is the contact, not an email"
    )
