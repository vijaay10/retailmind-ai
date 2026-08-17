"""PII scrubbing — safety layer before sending text to external LLM.

Detects and removes sensitive customer information that shouldn't be
sent to external AI providers.
"""

import re


class PIIScrubber:
    """Detects and scrubs PII from text before sending to LLM.

    The LLM should receive business context, not raw personal data.
    """

    # Patterns for common PII
    EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    PHONE_PATTERN = re.compile(r"\b(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
    CREDIT_CARD_PATTERN = re.compile(r"\b\d{4}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b")
    SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

    # UK-specific patterns
    # UK postcode: 1-2 letters, 1-2 digits, optional letter, space, digit, 2 letters
    # Examples: SW1A 1AA, EC1A 1BB, W1A 0AX, M1 1AE, B33 8TH
    POSTCODE_PATTERN = re.compile(
        r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}\b",
        re.IGNORECASE,
    )
    NI_NUMBER_PATTERN = re.compile(r"\b[A-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-Z]\b")

    def scrub(self, text: str) -> str:
        """Remove PII from text.

        Args:
            text: Raw text that may contain PII

        Returns:
            Scrubbed text with PII replaced by placeholders
        """
        scrubbed = text

        # Email addresses
        scrubbed = self.EMAIL_PATTERN.sub("[EMAIL_REDACTED]", scrubbed)

        # Phone numbers
        scrubbed = self.PHONE_PATTERN.sub("[PHONE_REDACTED]", scrubbed)

        # Credit card numbers
        scrubbed = self.CREDIT_CARD_PATTERN.sub("[CARD_REDACTED]", scrubbed)

        # SSN
        scrubbed = self.SSN_PATTERN.sub("[SSN_REDACTED]", scrubbed)

        # UK postcodes (but preserve first part for regional analysis if needed)
        scrubbed = self.POSTCODE_PATTERN.sub("[POSTCODE]", scrubbed)

        # UK National Insurance numbers
        scrubbed = self.NI_NUMBER_PATTERN.sub("[NI_NUMBER_REDACTED]", scrubbed)

        return scrubbed

    def contains_pii(self, text: str) -> bool:
        """Check if text likely contains PII.

        Args:
            text: Text to check

        Returns:
            True if PII patterns detected
        """
        patterns = [
            self.EMAIL_PATTERN,
            self.PHONE_PATTERN,
            self.CREDIT_CARD_PATTERN,
            self.SSN_PATTERN,
            self.POSTCODE_PATTERN,
            self.NI_NUMBER_PATTERN,
        ]

        return any(pattern.search(text) for pattern in patterns)
