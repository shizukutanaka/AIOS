"""aictl guardrails — local PII detection and content filtering.

Portkey has this, but it's SaaS. LiteLLM has none.
This gives the same protection without sending data anywhere.

Two modes:
  detect  — scan a prompt/response and report what was found
  redact  — return a sanitized copy with PII replaced by placeholders

PII patterns (regex-based, stdlib only, zero external deps):
  - Email addresses
  - Japanese phone numbers  (090-XXXX-XXXX, 03-XXXX-XXXX)
  - International phone numbers
  - Credit card numbers (Luhn-validated)
  - Japanese My Number (12 digits, Luhn)
  - Social Security Numbers (US)
  - IPv4 addresses
  - API keys / secrets (high-entropy tokens)
  - Japanese postal codes (〒NNN-NNNN)

Content filters (keyword/pattern):
  - Prompt injection attempts
  - Jailbreak patterns
  - System-prompt leakage requests
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# Homoglyph fold: map common look-alike Unicode chars to ASCII.
# Defends against evasion via Cyrillic/Greek look-alikes (arXiv:2504.11168).
# e.g. Cyrillic 'а' (U+0430) → Latin 'a'; full-width chars normalized by NFKC.
_HOMOGLYPHS = {
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c",
    "\u0445": "x", "\u0443": "y", "\u0456": "i", "\u0455": "s", "\u04bb": "h",
    "\u0391": "A", "\u0392": "B", "\u0395": "E", "\u0396": "Z", "\u0397": "H",
    "\u0399": "I", "\u039a": "K", "\u039c": "M", "\u039d": "N", "\u039f": "O",
    "\u03a1": "P", "\u03a4": "T", "\u03a5": "Y", "\u03a7": "X",
    "\u0410": "A", "\u0412": "B", "\u0421": "C", "\u0415": "E", "\u041d": "H",
    "\u041a": "K", "\u041c": "M", "\u041e": "O", "\u0420": "P", "\u0422": "T",
    "\u0425": "X",
}

# Zero-width and invisible chars used to break up keywords (e.g. "ign\u200bore").
_INVISIBLE = re.compile(
    r"[\u200b\u200c\u200d\u200e\u200f\u2060\ufeff\u00ad\u180e\u061c"
    r"\u202a-\u202e\u2066-\u2069]"
)


def normalize_for_scan(text: str) -> str:
    """Canonicalize text before pattern matching to resist evasion.

    Applies (in order): strip invisible/zero-width chars, fold homoglyphs to
    ASCII, then NFKC normalization (collapses full-width/compatibility forms).
    This closes the obfuscation gap where attackers split or disguise keywords
    like "ignore previous instructions" (arXiv:2504.11168, arXiv:2603.25176).

    Used internally by scan(); the original text is preserved for output.
    """
    cleaned = _INVISIBLE.sub("", text)
    cleaned = "".join(_HOMOGLYPHS.get(ch, ch) for ch in cleaned)
    return unicodedata.normalize("NFKC", cleaned)


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Normalize for scanning while tracking each char's original index.

    Returns ``(normalized_text, orig_index)`` where ``orig_index[i]`` is the
    position in the ORIGINAL ``text`` that produced normalized character ``i``.
    Invisible/zero-width chars are dropped, homoglyphs folded, and each char
    NFKC-normalized individually so the index map stays exact. This lets PII
    detection run on a canonicalized copy (catching obfuscated tokens) while
    still redacting the correct span — including embedded invisible chars — in
    the original text (arXiv:2504.11168).
    """
    out: list[str] = []
    orig_idx: list[int] = []
    for i, ch in enumerate(text):
        if _INVISIBLE.match(ch):
            continue
        for nch in unicodedata.normalize("NFKC", _HOMOGLYPHS.get(ch, ch)):
            out.append(nch)
            orig_idx.append(i)
    return "".join(out), orig_idx


# ── PII patterns ───────────────────────────────────────────

@dataclass
class PIIMatch:
    kind: str       # "email", "phone_jp", "credit_card", etc.
    value: str      # the matched text (or masked form for logging)
    start: int
    end: int

    @property
    def masked(self) -> str:
        """Return first+last 2 chars with *** in between."""
        v = self.value
        if len(v) <= 4:
            return "***"
        return v[:2] + "***" + v[-2:]


_PII_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email",        re.compile(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")),
    ("phone_jp",     re.compile(
        r"\b(?:0\d{1,4}[-\u2212\u30FC]?\d{1,4}[-\u2212\u30FC]?\d{3,4})\b")),
    ("phone_intl",   re.compile(
        r"\+?1?\s?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}\b")),
    ("ssn",          re.compile(
        r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b")),
    ("postal_jp",    re.compile(
        r"[〒\u3012]?\s?\d{3}[-\u2212]\d{4}")),
    ("ipv4",         re.compile(
        r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
        r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\b")),
    ("credit_card",  re.compile(
        r"\b(?:\d[ \-]?){13,16}\b")),
    ("api_key",      re.compile(
        # High-entropy 20-64 char alphanum/dash/underscore strings
        # that look like tokens (e.g. sk-xxxx, pk_xxxx, Bearer xxx)
        r"(?:sk|pk|api|key|token|secret|bearer)[_\-]?[A-Za-z0-9_\-]{20,64}",
        re.IGNORECASE)),
    ("my_number_jp", re.compile(
        r"\b\d{4}[\s\-]?\d{4}[\s\-]?\d{4}\b")),
]


def _is_non_pii_ip(octets: list[int]) -> bool:
    """True if IP is private, loopback, or special (not personally identifiable)."""
    if not octets or len(octets) < 4:
        return True
    first = octets[0]
    # Loopback (127.x.x.x)
    if first == 127:
        return True
    # Private: 10.x.x.x
    if first == 10:
        return True
    # Private: 172.16.0.0 – 172.31.255.255
    if first == 172 and 16 <= octets[1] <= 31:
        return True
    # Private: 192.168.x.x
    if first == 192 and octets[1] == 168:
        return True
    # Link-local: 169.254.x.x
    if first == 169 and octets[1] == 254:
        return True
    # Multicast / broadcast / special
    if first >= 224 or first == 0:
        return True
    # Subnet masks (255.255.x.x)
    if first == 255:
        return True
    return False


def _luhn_valid(number: str) -> bool:
    """Luhn algorithm — validates credit cards and My Number."""
    digits = [int(c) for c in re.sub(r"\D", "", number)]
    if len(digits) < 12:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_pii(text: str) -> list[PIIMatch]:
    """Find all PII-looking substrings. Returns a list of matches.

    Scanning runs on a normalized copy (zero-width stripped, homoglyphs folded,
    NFKC) so PII disguised with invisible chars or look-alike glyphs is caught;
    match spans are mapped back to the original text so redaction stays exact.
    """
    norm, orig_idx = _normalize_with_map(text)
    matches: list[PIIMatch] = []
    seen_spans: set[tuple[int, int]] = set()

    for kind, pattern in _PII_PATTERNS:
        for m in pattern.finditer(norm):
            if m.end() == m.start():
                continue
            # Map the normalized span back to original-text indices.
            o_start = orig_idx[m.start()]
            o_end = orig_idx[m.end() - 1] + 1
            span = (o_start, o_end)
            if span in seen_spans:
                continue
            value = m.group()

            # Extra validation for high-false-positive patterns
            if kind == "credit_card" and not _luhn_valid(value):
                continue
            if kind == "my_number_jp" and not _luhn_valid(value):
                continue
            # IPv4: skip non-PII addresses (private, loopback, special)
            if kind == "ipv4":
                octets = [int(p) for p in re.findall(r"\d+", value)[:4]]
                if any(o > 255 for o in octets):
                    continue
                if _is_non_pii_ip(octets):
                    continue
                # Skip version-number-like strings (e.g., "v1.2.3.4")
                before = norm[max(0, m.start() - 2):m.start()].lower()
                if before.endswith("v") or before.endswith("n "):
                    continue

            seen_spans.add(span)
            matches.append(PIIMatch(kind=kind, value=value,
                                    start=o_start, end=o_end))

    return sorted(matches, key=lambda x: x.start)


def redact(text: str, replacement: str = "[REDACTED]") -> tuple[str, list[PIIMatch]]:
    """Return (redacted_text, list_of_what_was_redacted)."""
    found = detect_pii(text)
    if not found:
        return text, []
    result = list(text)
    # Replace back-to-front to preserve indices
    for m in sorted(found, key=lambda x: -x.start):
        result[m.start:m.end] = list(replacement)
    return "".join(result), found


# ── Content filter ─────────────────────────────────────────

@dataclass
class ContentViolation:
    rule: str
    severity: str  # "block" | "warn"
    excerpt: str


_CONTENT_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    # Prompt injection
    ("prompt_injection", "block", re.compile(
        r"ignore\s+(all\s+)?previous\s+instructions?"
        r"|disregard\s+(all\s+)?prior"
        r"|forget\s+everything\s+above"
        r"|system\s*prompt\s*:\s*you\s+are\s+now"
        r"|new\s+instructions?:\s",
        re.IGNORECASE)),
    # Jailbreaks
    ("jailbreak", "block", re.compile(
        r"DAN\s+mode"
        r"|jailbreak"
        r"|developer\s+mode\s+enabled"
        r"|pretend\s+(you\s+are|to\s+be)\s+(a\s+)?(?:evil|unrestricted|unfiltered)",
        re.IGNORECASE)),
    # System prompt leakage
    ("system_leak", "warn", re.compile(
        r"(print|show|reveal|repeat|output)\s+"
        r"(your\s+)?(system\s+prompt|instructions?|context|rules)",
        re.IGNORECASE)),
    # Excessive tokens in single message (abuse)
    ("token_bomb", "warn", re.compile(
        r"(.)\1{500,}")),  # 500+ repetitions of same char
]


def check_content(text: str) -> list[ContentViolation]:
    """Return a list of policy violations found in text.

    Matches against a normalized copy (zero-width stripped, homoglyphs folded,
    NFKC) so obfuscated injections cannot slip past the keyword rules. Excerpts
    are taken from the normalized text for clarity in the violation report.
    """
    scan_text = normalize_for_scan(text)
    violations: list[ContentViolation] = []
    for rule, severity, pattern in _CONTENT_RULES:
        for m in pattern.finditer(scan_text):
            excerpt = scan_text[max(0, m.start()-20):m.end()+20].replace("\n", " ")
            violations.append(ContentViolation(
                rule=rule, severity=severity, excerpt=f"...{excerpt}..."
            ))
            break  # one violation per rule per text is enough
    return violations


# ── Composite scan ─────────────────────────────────────────

@dataclass
class ScanResult:
    """Combined result of one guardrail scan."""
    passed: bool
    pii: list[PIIMatch] = field(default_factory=list)
    violations: list[ContentViolation] = field(default_factory=list)
    # If passed=False, action should be "block" or "redact"
    recommended_action: str = "allow"


def scan(
    text: str,
    *,
    redact_pii: bool = False,
    block_on_pii: bool = False,
    block_on_injection: bool = True,
) -> tuple[ScanResult, str]:
    """Scan text and return (result, possibly_redacted_text).

    Args:
        text: The prompt or response to scan.
        redact_pii: If True, replace detected PII with [REDACTED].
        block_on_pii: If True, treat any PII as a blocking violation.
        block_on_injection: If True (default), block prompt injection.

    Returns:
        (ScanResult, processed_text)
    """
    processed = text
    pii_found = detect_pii(text)
    violations = check_content(text)

    if redact_pii and pii_found:
        processed, _ = redact(text)

    blocking = []
    if block_on_pii and pii_found:
        blocking.append("pii_detected")
    if block_on_injection:
        blocking.extend(
            v.rule for v in violations if v.severity == "block"
        )

    action = "block" if blocking else ("redact" if pii_found and redact_pii else "allow")

    result = ScanResult(
        passed=len(blocking) == 0,
        pii=pii_found,
        violations=violations,
        recommended_action=action,
    )
    return result, processed


# ── MCP tool-poisoning detection ───────────────────────────
# Tool Poisoning Attack (TPA): malicious instructions embedded in an MCP
# tool's natural-language description, injected into the agent's context at
# registration (arXiv:2508.14925, arXiv:2603.22489). Defense = static
# metadata analysis before trusting any third-party tool description.

_TPA_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("hidden_instruction", re.compile(
        r"before\s+(any|using|calling|each)\s+\w+.{0,40}\b(you\s+must|always|first)\b",
        re.IGNORECASE | re.DOTALL)),
    ("imperative_to_agent", re.compile(
        r"\b(you\s+must|you\s+should\s+always|the\s+assistant\s+must|"
        r"as\s+an?\s+(ai|assistant|agent))\b",
        re.IGNORECASE)),
    ("file_exfiltration", re.compile(
        r"(read|cat|open|send|upload|exfiltrate).{0,30}"
        r"(\.ssh|id_rsa|\.env|credentials?|secret|password|/etc/passwd|api[_-]?key)",
        re.IGNORECASE | re.DOTALL)),
    ("instruction_override", re.compile(
        r"ignore\s+(all\s+)?(previous|prior|other)\s+|disregard\s+|"
        r"do\s+not\s+(tell|inform|mention\s+to)\s+the\s+user",
        re.IGNORECASE)),
    ("hidden_channel", re.compile(
        r"\b(bcc|secretly|silently|without\s+(the\s+)?user|"
        r"do\s+not\s+(show|display|reveal))\b",
        re.IGNORECASE)),
]


@dataclass
class ToolAudit:
    """Result of auditing one MCP tool's metadata for poisoning."""
    name: str
    safe: bool
    findings: list[str] = field(default_factory=list)


def audit_tool_description(name: str, description: str) -> ToolAudit:
    """Statically analyze an MCP tool description for poisoning indicators.

    Returns a ToolAudit; safe=False means the description contains text that
    looks like instructions aimed at the agent rather than documentation for
    the user. Normalizes first so obfuscation cannot hide payloads.
    """
    text = normalize_for_scan(description)
    findings: list[str] = []
    for label, pattern in _TPA_PATTERNS:
        if pattern.search(text):
            findings.append(label)
    return ToolAudit(name=name, safe=len(findings) == 0, findings=findings)


def audit_tools(tools: list[dict[str, Any]]) -> list[ToolAudit]:
    """Audit a list of MCP tool definitions (each with name + description)."""
    audits: list[ToolAudit] = []
    for tool in tools:
        name = str(tool.get("name", "<unnamed>"))
        desc = str(tool.get("description", ""))
        audits.append(audit_tool_description(name, desc))
    return audits
