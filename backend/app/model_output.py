"""Parse model JSON without logging response text or credentials."""
import json
import re
from pydantic import ValidationError

class ModelOutputError(ValueError):
    def __init__(self, code, details=''):
        self.code = code
        self.details = details
        super().__init__(code)

def response_object(response):
    try:
        choice = response.json()['choices'][0]
        message = choice['message']
        if choice.get('finish_reason') == 'length':
            raise ModelOutputError('truncated')
        if choice.get('finish_reason') == 'content_filter' or message.get('refusal'):
            raise ModelOutputError('refused')
        content = message.get('content')
        if isinstance(content, list):
            content = ''.join(block['text'] for block in content
                              if isinstance(block, dict) and block.get('type') == 'text' and isinstance(block.get('text'), str))
        if not isinstance(content, str) or not content.strip():
            raise ModelOutputError('empty')
        text = content.strip().lstrip('\ufeff')
        fence = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', text, flags=re.I)
        if fence:
            text = fence.group(1)
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ModelOutputError('schema')
        return value
    except (KeyError, IndexError, TypeError, AttributeError):
        raise ModelOutputError('envelope') from None
    except json.JSONDecodeError:
        raise ModelOutputError('json') from None

def validate_analysis(value, schema):
    try:
        return schema.model_validate(value)
    except ValidationError as exc:
        # Only known schema field names, indices and error codes; never input/msg/ctx.
        allowed = {'goal', 'assumptions', 'questions', 'knowledge', 'steps', 'alternatives',
                   'name', 'importance', 'explanation', 'goal_connection', 'plan', 'queries', 'year_from', 'year_to'}
        details = '; '.join('.'.join(str(p) if isinstance(p, int) or p in allowed else '?' for p in e['loc'])
                            + ':' + e['type'] for e in exc.errors(include_input=False, include_context=False, include_url=False)[:8])
        raise ModelOutputError('schema', details) from None
