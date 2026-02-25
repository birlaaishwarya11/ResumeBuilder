"""
Unified LLM abstraction layer.

Extracted from local_app.py so both the Flask web app and the MCP server
import from one place — no circular dependencies, no HTTP hops.

Supported providers: anthropic, openai, gemini
"""

import json
import re


def call_llm(provider: str, api_key: str, system_prompt: str, user_message: str, model: str | None = None) -> str:
    """Call a supported LLM provider and return the text response.

    Args:
        provider:     One of 'anthropic', 'openai', 'gemini'.
        api_key:      Provider API key. Never logged or stored.
        system_prompt: System/instruction context.
        user_message:  The user-facing content.
        model:         Optional model override. Falls back to a sensible default per provider.

    Returns:
        Raw text response from the model.

    Raises:
        ValueError: on unknown provider or empty Gemini response.
        Provider SDK errors are propagated as-is so callers can inspect status codes.
    """
    # Strip whitespace/tabs/newlines — API keys appear in HTTP headers or URLs
    # and non-printable characters cause hard-to-diagnose transport errors.
    api_key = (api_key or '').strip()
    if not api_key:
        raise ValueError('API key is empty after stripping whitespace. Please provide a valid key.')

    if provider == 'anthropic':
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        model_name = model or 'claude-3-haiku-20240307'
        message = client.messages.create(
            model=model_name,
            max_tokens=4000,
            temperature=0,
            system=system_prompt,
            messages=[{'role': 'user', 'content': user_message}]
        )
        return message.content[0].text

    elif provider == 'openai':
        import openai
        client = openai.OpenAI(api_key=api_key)
        model_name = model or 'gpt-3.5-turbo'
        completion = client.chat.completions.create(
            model=model_name,
            messages=[
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_message}
            ],
            temperature=0,
        )
        return completion.choices[0].message.content

    elif provider == 'gemini':
        from google import genai
        from google.genai import types
        # v1 REST API: fold system prompt into user content (systemInstruction not supported in v1)
        client = genai.Client(api_key=api_key, http_options={'api_version': 'v1'})
        model_name = model or 'gemini-2.0-flash'
        content = (system_prompt + '\n\n' + user_message) if system_prompt else user_message
        response = client.models.generate_content(
            model=model_name,
            contents=content,
            config=types.GenerateContentConfig(temperature=0)
        )
        result = response.text
        if result is None:
            raise ValueError(
                'Gemini returned an empty response (may have been blocked by safety filters)'
            )
        return result

    raise ValueError(f"Unknown provider: {provider!r}. Must be 'anthropic', 'openai', or 'gemini'.")


def extract_ai_error(exc) -> dict:
    """Extract a user-facing error message and HTTP status code from an AI provider exception.

    Returns:
        {"message": str, "status_code": int | None}
    """
    msg = str(exc)
    status_code = None

    if hasattr(exc, 'status_code'):
        status_code = exc.status_code
    elif hasattr(exc, 'code'):
        status_code = exc.code
    else:
        m = re.match(r'^(\d{3})\b', msg.strip())
        if m:
            status_code = int(m.group(1))

    short = msg.splitlines()[0][:300]
    return {'message': short, 'status_code': status_code}


def parse_json_response(response_text: str):
    """Parse JSON out of an LLM response that may have markdown fences or prose.

    Returns the parsed Python object (dict or list), or an empty dict on failure.
    """
    text = response_text.replace('```json', '').replace('```', '').strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find a JSON object or array in the text
    for pattern in (r'\{.*\}', r'\[.*\]'):
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

    return {}
