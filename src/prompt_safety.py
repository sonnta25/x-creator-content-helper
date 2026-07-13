from __future__ import annotations


def looks_like_prompt_leak(text: str) -> bool:
    normalized = " ".join(text.lower().split())
    if not normalized:
        return False
    prompt_markers = (
        "you are a twitter/x tweet qa",
        "you are a twitter/x reply engine",
        "you are an x reply qa",
        "you are an autonomous twitter/x knowledge engine",
        "your input is one generated tweet",
        "your input is one generated x reply",
        "your job is not to create a new topic",
        "your job is not to write a new reply",
        "your purpose is not to write tweets",
        "never explain your reasoning",
        "return only one final tweet",
        "return only the final rewritten reply",
        "step 1 - silent ai detection",
        "step 1 (silent)",
        "final silent qa",
        "original task:",
        "original reply task:",
        "original reply-target task:",
        "original tweet task:",
        "original tweet-variants task:",
        "original vietnamese-source task:",
        "chatgpt analysis/draft:",
        "chatgpt draft/analysis",
        "first-pass gemini draft:",
        "generated json:",
        "generated x reply:",
        "required json shape:",
        "tham khảo nội dung sau",
        "tham khao noi dung sau",
    )
    return any(marker in normalized for marker in prompt_markers)
