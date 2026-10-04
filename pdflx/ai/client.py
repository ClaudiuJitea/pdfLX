"""Minimal OpenRouter client (OpenAI-compatible chat completions with tool calling)."""
import json
import urllib.error
import urllib.request

from ..constants import APP_NAME


class AiError(Exception):
    pass


def _request(url, key, payload=None, timeout=600):
    headers = {'Content-Type': 'application/json',
               # OpenRouter uses these to attribute requests to the app.
               'HTTP-Referer': 'https://github.com/ClaudiuJitea/pdfLX', 'X-Title': APP_NAME}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    data = json.dumps(payload).encode('utf-8') if payload is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method='POST' if data else 'GET')
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        body = error.read()
        try:
            message = json.loads(body).get('error', {}).get('message') or body.decode('utf-8', 'replace')
        except ValueError:
            message = body.decode('utf-8', 'replace')
        if error.code == 401:
            raise AiError('The API key was rejected. Check it in AI settings.') from None
        if error.code == 402:
            raise AiError(f'The account has no credits left for this request ({message.strip()[:200]}).') from None
        raise AiError(f'{error.code}: {message.strip()[:500]}') from None
    except urllib.error.URLError as error:
        raise AiError(f'Could not reach the AI service: {error.reason}') from None
    except TimeoutError:
        raise AiError('The AI service did not answer in time.') from None
    try:
        result = json.loads(body)
    except ValueError:
        raise AiError('The AI service returned an unreadable answer.') from None
    if isinstance(result, dict) and result.get('error'):
        error = result['error']
        raise AiError(error.get('message', str(error)) if isinstance(error, dict) else str(error))
    return result


def chat(base_url, key, model, messages, tools):
    """Send one chat turn; returns (message dict, usage dict, finish_reason)."""
    if not key:
        raise AiError('Add your OpenRouter API key in AI settings.')
    if not model:
        raise AiError('Enter the OpenRouter model to use in AI settings.')
    payload = {'model': model, 'messages': messages, 'tools': tools, 'tool_choice': 'auto',
               'usage': {'include': True}}
    result = _request(f'{base_url}/chat/completions', key, payload)
    choices = result.get('choices') or []
    if not choices:
        raise AiError('The model returned no answer.')
    choice = choices[0]
    if choice.get('error'):
        error = choice['error']
        raise AiError(error.get('message', str(error)) if isinstance(error, dict) else str(error))
    return choice.get('message') or {}, result.get('usage') or {}, choice.get('finish_reason')


def list_models(base_url, key=None):
    """Models that accept images and tools, as (id, name, prompt $/M, completion $/M)."""
    result = _request(f'{base_url}/models', key, timeout=30)
    models = []
    for item in result.get('data', []):
        modalities = (item.get('architecture') or {}).get('input_modalities') or []
        if 'image' not in modalities or 'tools' not in (item.get('supported_parameters') or []):
            continue
        pricing = item.get('pricing') or {}
        def per_million(value):
            try:
                return float(value) * 1_000_000
            except (TypeError, ValueError):
                return None
        models.append((item['id'], item.get('name') or item['id'],
                       per_million(pricing.get('prompt')), per_million(pricing.get('completion'))))
    return sorted(models, key=lambda model: model[1].lower())
