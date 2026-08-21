"""
TripleTen Cloud Platform — Autonomous Incident Defense
======================================================
Module:             services/incident-agent-api/src/incident_agent_api/security/sanitizer.py
Component:          PII Redaction Engine
Purpose:            Pure functions that mask credentials, tokens, addresses, and customer data
                    out of raw log text before any model or datastore sees it.
Interacts With:     None (pure functions)

Curriculum Project:  Project 4 — Security, PII Redaction & Guardrails
Skills:             PII Sanitization, Regex Masking, Deterministic Simulation
Tools:              Python 3.11

Two properties this module is built around, both asserted in the unit suite:

**Zero leakage.** No masked secret may survive anywhere in the returned object, not just in the
message field. That is why `sanitize()` returns a frozen result rather than mutating a dict a
caller might have already copied a raw value out of.

**No over-redaction.** Masking everything would pass a leak test and destroy the demo: the
agent reasons over these lines, and `db_user=admin`, the `:6379` port, and the
`JSONDecodeError` exception type are the diagnostic signal. Over-redaction is a failure here,
not a safe default.
"""

import re
from dataclasses import dataclass, field

from tripleten_contracts import RedactionType, redaction_token


@dataclass(frozen=True)
class SanitizedLog:
    """One log line after masking, with the evidence the guardrail fired."""

    message: str
    redacted_token_count: int
    redacted_types: tuple[RedactionType, ...] = field(default=())

    @property
    def sanitized(self) -> bool:
        """True when something was actually masked — what the War Room's badge counts.

        Not "was this line inspected": every line is. The LOG_STREAM payload's `sanitized` flag
        means the same thing, so the two cannot drift.
        """
        return self.redacted_token_count > 0


# Ordered, and the order is load-bearing. Each pattern runs against the output of the previous
# one, so a broad pattern placed early eats the text a narrower one needs:
#
#  * JWT first, and before PASSWORD specifically. A JWT is often carried under a key this module
#    also recognises (`token=eyJ...`), and whichever pattern runs first decides the *label* the
#    War Room shows. `jwt` is a more precise answer than `password` for a bearer token, so JWT gets
#    first refusal. It cannot steal anything from PASSWORD: it is anchored on the `eyJ` prefix,
#    which is base64 for `{"` and is how a JWT header always begins.
#  * PASSWORD next, because `db_pass=prod_k8s_secret_992` is the most specific keyed form.
#  * The URL-credential form before EMAIL, because a connection string puts a password
#    immediately before an `@host` and the email pattern would otherwise claim the whole span —
#    masking the secret, which is fine, but labelling it `email` and eating the hostname with it.
#  * JWT before AWS_KEY: a bearer token's base64 body matches the AWS secret-key shape.
#  * AWS_KEY before HOSTNAME and IP, because the key body contains `/` and alphanumerics that
#    no address pattern should be allowed to nibble at.
#  * HOSTNAME before IP: `ip-10-0-8-12` is a dashed EC2 hostname. An IPv4 pattern cannot see it
#    (the separators are hyphens, not dots), but running IP first on a line containing both
#    forms invites a future IP pattern loosened to hyphens to swallow the hostname silently.
#
# Every pattern keeps its key and its delimiter and replaces only the value, which is what
# preserves `db_user=admin` and `node=<masked>:6379` as diagnostics.
_PATTERNS: tuple[tuple[RedactionType, re.Pattern[str], str], ...] = (
    (
        RedactionType.JWT,
        # The `Bearer ` prefix is optional, and making it optional closed a real hole: a bare
        # `token=eyJ...` was matched by nothing at all, because the PASSWORD key list below named
        # `token` in its comment and omitted it from the pattern. Anchored on `eyJ` with a length
        # floor so the bare word cannot trip it, and the prefix is preserved when present so
        # `auth_header=Bearer <masked>` still reads as an auth header.
        re.compile(r"(?P<prefix>\bBearer\s+)?\beyJ[A-Za-z0-9._\-/+=]{10,}"),
        r"\g<prefix>{token}",
    ),
    (
        RedactionType.PASSWORD,
        # Keyed secrets: db_pass, password, passwd, pwd, secret, token, api_key.
        #
        # `token` was named in this comment and missing from the pattern, so `token=<secret>` was
        # redacted by nothing. The delimiter now accepts `:` as well as `=`, because
        # `password: value` is at least as common in real log and config output as `password=value`.
        #
        # The value runs to the first delimiter so a trailing `,` or `)` in the log line survives.
        #
        # `(?!\[REDACTED)` is not paranoia. Patterns run in sequence over the *previous* pattern's
        # output, and now that JWT runs first, `token=eyJ...` arrives here as
        # `token=[REDACTED: jwt]` — whose value, read up to the first space, is `[REDACTED:`. That
        # got masked a second time and left ` jwt]` stranded in the middle of the line. Any keyed
        # rule that can fire after another one needs this guard.
        re.compile(
            r"(?P<key>\b(?:db_pass|password|passwd|pwd|secret|token|api_key)\s*[:=]\s*)"
            r"(?P<value>(?!\[REDACTED)[^\s,;)\"']+)",
            re.IGNORECASE,
        ),
        r"\g<key>{token}",
    ),
    (
        RedactionType.PASSWORD,
        # URL credentials: `scheme://user:secret@host`. Only the password is replaced — the
        # scheme, the user and the host stay, because they are the diagnostics. Placed before
        # EMAIL for the reason given in the ordering note above.
        #
        # The user part is `*`, not `+`: `redis://:hunter2@host` omits the user entirely, which is
        # the ordinary form for a Redis URL with only a password.
        re.compile(r"(?P<key>\b[a-z][a-z0-9+.\-]*://[^\s:/@]*:)(?P<value>(?!\[REDACTED)[^\s@/]+)(?=@)"),
        r"\g<key>{token}",
    ),
    (
        RedactionType.AWS_KEY,
        re.compile(
            r"(?P<key>\b(?:AWS_SECRET_ACCESS_KEY|aws_secret_access_key)\s*=\s*)(?P<value>[A-Za-z0-9/+=]{20,})",
        ),
        r"\g<key>{token}",
    ),
    (
        RedactionType.EMAIL,
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "{token}",
    ),
    (
        RedactionType.HOSTNAME,
        # Dashed private hostnames: ip-10-0-8-12, ip-172-31-4-9. Anchored on the `ip-` prefix
        # and four dash-separated octet groups so it cannot match an ordinary hyphenated word.
        re.compile(r"\bip(?:-\d{1,3}){4}\b"),
        "{token}",
    ),
    (
        RedactionType.IP,
        # RFC1918 only. A public address in a log line is not customer data and masking it
        # would remove a diagnostic; the demo's leaks are all private-range.
        re.compile(
            r"\b(?:10(?:\.\d{1,3}){3}"
            r"|192\.168(?:\.\d{1,3}){2}"
            r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2})\b"
        ),
        "{token}",
    ),
)


def sanitize(raw: str) -> SanitizedLog:
    """Masks every recognised sensitive entity in a raw log line.

    Returns the masked text, the exact number of entities replaced, and which types fired —
    the three things the War Room badge, the LOG_STREAM payload, and the leak assertions each
    need. Deterministic: the same input always produces the same output and the same count.
    """
    message = raw
    total = 0
    fired: list[RedactionType] = []

    for kind, pattern, template in _PATTERNS:
        replacement = template.format(token=redaction_token(kind))
        message, count = pattern.subn(replacement, message)
        if count:
            total += count
            fired.append(kind)

    return SanitizedLog(message=message, redacted_token_count=total, redacted_types=tuple(fired))


def sanitize_all(raw_lines: tuple[str, ...]) -> tuple[SanitizedLog, ...]:
    """Sanitizes a batch of lines, preserving order."""
    return tuple(sanitize(line) for line in raw_lines)


def contains_secret(haystack: str, secrets: tuple[str, ...]) -> str | None:
    """Returns the first secret found in `haystack`, or None.

    The negative assertion the test suite runs against a whole serialized payload rather than
    one field, so a secret that leaked into a log message, an excerpt, or an agent's reasoning
    text is caught the same way.
    """
    for secret in secrets:
        if secret in haystack:
            return secret
    return None
