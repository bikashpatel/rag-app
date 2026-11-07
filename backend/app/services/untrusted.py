"""Safeguards for text the user didn't write (document passages, emails) on its way into a chat prompt: a random
boundary per prompt that untrusted text can't forge, removal of characters that hide text, and a heuristic detector
for text that reads like instructions to the assistant. The detector only labels (false positives must be harmless)
and knows English phrasings only: the prompt rules and the request check are the guards, this is defense in depth."""

import re
import secrets
import unicodedata

# Unicode tag characters (they can spell hidden ASCII), bidi embeddings, overrides and isolates, zero-width space,
# word joiner, BOM and soft hyphen. ZWJ and ZWNJ stay: Indian scripts need them.
_HIDDEN: dict[int, None] = {
    code: None
    for code in (
        *range(0xE0000, 0xE0080),
        *range(0x202A, 0x202F),
        *range(0x2066, 0x206A),
        0x200B,
        0x2060,
        0xFEFF,
        0x00AD,
    )
}
# A subdivision flag (🏴 + tag letters + cancel tag) has at most 7 tag characters; a longer run hides text.
_HIDDEN_RUN = re.compile("[\U000e0000-\U000e007f]{8,}")
_GAP = r"[^.!?]{0,40}?"  # a few words within one sentence
_AI = r"(?:ai|ai assistants?|ai agents?|chatbots?|llms?|(?:large )?language models?)"
_INSTRUCTION_LIKE = [
    re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    for pattern in (
        # "Ignore all previous instructions", "disregard the rules above", "forget your instructions"
        rf"\b(?:ignore|disregard)\b{_GAP}\b(?:previous|prior|preceding|above|earlier|all|any|every|your|original|"
        rf"initial|system|other)\b{_GAP}\b(?:instructions?|prompts?|rules|directions|directives|guidelines|guardrails|"
        r"commands)\b",
        rf"\bforget\b{_GAP}\b(?:previous|prior|preceding|above|earlier|your|original|initial|system)\b{_GAP}"
        r"\b(?:instructions?|prompts?|rules|directions|directives|guidelines|guardrails|commands)\b",
        rf"\b(?:ignore|disregard|forget)\b{_GAP}\b(?:instructions?|prompts?|rules|guidelines|directives) (?:above|"
        r"so far|you (?:were|have been) given)\b",
        # A new role or mode for the assistant
        r"\byou are (?:now|no longer) (?:an? |the |my )?(?:ai|assistant|chatbot|language model|llm|dan|jailbroken|"
        r"unrestricted|unfiltered|uncensored)\b",
        r"\b(?:god|jailbreak|jailbroken|dan|unrestricted|unfiltered) mode\b",
        # Text addressed to an AI that reads the document
        rf"\bif you are an? (?:artificial intelligence|{_AI})\b",
        rf"\b(?:note|message|instructions?) (?:to|for) (?:the |any |all )?{_AI}\b",
        rf"\battention:? (?:all |any )?{_AI}\b",
        r"\b(?:new|updated|revised|real|actual) system (?:prompt|message|instructions?)\b",
        r"^[ \t]*(?:#{1,6}[ \t]*|\[)?(?:system|developer) (?:prompt|instructions?)\b",
        # Asking for the hidden instructions
        rf"\b(?:reveal|print|repeat|output|display|disclose|leak|show|tell|share|write out|list)\b{_GAP}"
        r"\b(?:your (?:\w+ )?(?:prompt|instructions|rules)|(?:system|hidden|secret|developer) "
        r"(?:prompt|instructions))\b",
        # Chat-template and role markers
        r"<\|[a-z_]{2,30}\|>",
        r"\[/?INST\]|<</?SYS>>|<(?:start|end)_of_turn>",
        r"^[ \t]*(?:system|developer)[ \t]*:[ \t]*(?:you\b|ignore\b|disregard\b|from now on\b|new instructions\b)",
        r"^[ \t]*assistant[ \t]*:",
        # Acting behind the user's back, or sending their data somewhere
        r"\b(?:without (?:telling|informing|notifying|asking) the user|(?:don't|do not|never) (?:tell|inform|notify|"
        r"alert) the user)\b",
        rf"\b(?:send|forward|email|e-mail|mail|upload|post|transmit|exfiltrate|leak)\b{_GAP}"
        rf"(?:\b(?:the |this |all (?:of )?the )?users?(?:'s?)?\b|\b(?:conversation|chat history|inbox|emails|"
        rf"passwords?|credentials|api keys?|secrets|access tokens?)\b){_GAP}\bto\b[^.!?]{{0,20}}?"
        r"(?:[\w.+-]+@[\w-]+\.\w|https?://)",
    )
]
_UNTRUSTED_TAG = re.compile(r"<(\s*/?\s*)(context|passage)", re.IGNORECASE)


def new_boundary() -> str:
    """A random code for one prompt's passage tags: text written before the prompt was built can't contain it."""
    return secrets.token_hex(6)


def strip_hidden(text: str) -> str:
    """`text` without the characters that hide text from a reader (see _HIDDEN)."""
    return text.translate(_HIDDEN)


def defuse(text: str) -> str:
    """Neutralize <context> and <passage…> tags, so untrusted text can't open or close a prompt block."""
    return _UNTRUSTED_TAG.sub(r"[\1\2", text)


def looks_like_instructions(text: str) -> bool:
    """Whether `text` hides text or reads like instructions to the assistant (a heuristic for English)."""
    if _HIDDEN_RUN.search(text):
        return True
    plain = unicodedata.normalize("NFKC", strip_hidden(text)).replace("’", "'")
    return any(pattern.search(plain) for pattern in _INSTRUCTION_LIKE)
