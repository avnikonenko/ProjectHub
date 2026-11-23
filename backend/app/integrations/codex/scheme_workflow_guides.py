"""Find guide passages that explicitly select a different workflow."""

import re


_CREDENTIAL = re.compile(
    r'\b(?:credentials?|passwords?|api[-_ ]+keys?|access[-_ ]+tokens?|'
    r'authentication|sign[- ]in|log[- ]in)\b',
    re.I,
)
_CHOICE = re.compile(
    r'\b(?:if|when|unless|instead(?:\s+of)?|'
    r'omit(?:s|ting|ted)?|'
    r'(?:provid(?:e|es|ing)|suppl(?:y|ies|ying))\s+(?:only\s+)?\S+|'
    r'to\s+(?:start|run|process|resume)\s+(?:from|with|without|using))\b',
    re.I,
)
_CONSEQUENCE = re.compile(
    r'\b(?:reus\w*|bypass\w*|skipp?\w*|'
    r'(?:is|are|will|does|do)\s+not\s+(?:run\w*|perform\w*|generat\w*|execut\w*)|'
    r'no\s+[\w-]+(?:\s+[\w-]+){0,2}\s+(?:is|are)\s+(?:perform\w*|run\w*|generat\w*|execut\w*)|'
    r'omit(?:s|ting|ted)?\s+(?:the\s+)?[\x60\x22\x27]?--[\w-]+)\b',
    re.I,
)
_SENTENCE_BREAK = re.compile(r'(?<=[.!?])\s+')


def is_workflow_alternative(paragraph: str) -> bool:
    """Recognize an input choice with an explicit effect on what runs."""
    return bool(not _CREDENTIAL.search(paragraph)
                and any(_CHOICE.search(sentence) and _CONSEQUENCE.search(sentence)
                        for sentence in _SENTENCE_BREAK.split(paragraph)))
