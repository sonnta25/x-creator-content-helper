from src.extension_prompts import gemini_single_pass_prompt


def test_gemini_single_pass_prompt_combines_task_and_reply_quality_rules() -> None:
    original = "You are a Twitter/X Reply Engine. Return only ONE final reply."

    prompt = gemini_single_pass_prompt(original)

    assert original in prompt
    assert "SINGLE-PASS WRITING AND QA" in prompt
    assert "specific source-grounded observation" in prompt
    assert "question-only reply is invalid" in prompt
    assert "Return only the final answer required by the original task." in prompt


def test_gemini_single_pass_prompt_preserves_replytargets_json_rules() -> None:
    prompt = gemini_single_pass_prompt(
        'You are a Twitter/X Reply Engine. Return only valid JSON with "targets".'
    )

    assert "same language and register as the source post" in prompt
    assert "could not be pasted under an unrelated post" in prompt
    assert "every `reply` field" in prompt
    assert "concrete read or comparison first" in prompt
