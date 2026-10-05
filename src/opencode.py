"""Model, variant and context for OpenCode sessions, read from its local database.

OpenCode 1.x keeps sessions in SQLite. The session row names the model and its
variant (reasoning effort); each finished assistant message records its token
total, which is what OpenCode shows as context use. The window comes from the
provider config (`limit.context`) or OpenCode's cached models.dev catalog, never
from a guess. Only these fields are read: no titles, prompts, parts or
credentials, and the database is opened read-only.
"""
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import quote

from agent_info import EFFORTS, context, number
from filecache import StampCache

# Older OpenCode 1.x releases write no `total`, only its parts. A zero count
# marks a message that is still being generated, so it is skipped either way.
LATEST_USAGE = """
    select json_extract(data, '$.tokens') from message
    where session_id = ? and json_extract(data, '$.role') = 'assistant'
      and coalesce(json_extract(data, '$.tokens.total'),
                   coalesce(json_extract(data, '$.tokens.input'), 0)
                   + coalesce(json_extract(data, '$.tokens.output'), 0)
                   + coalesce(json_extract(data, '$.tokens.reasoning'), 0)
                   + coalesce(json_extract(data, '$.tokens.cache.read'), 0)
                   + coalesce(json_extract(data, '$.tokens.cache.write'), 0)) > 0
    order by time_created desc limit 1
"""


def data_dir():
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'opencode'


def config_path():
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'opencode/opencode.json'


def catalog_path():
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'opencode/models.json'


def json_object(text):
    value = json.loads(text)
    return value if isinstance(value, dict) else {}


def read_only_uri(path):
    """An SQLite URI for `path`; quoting keeps `?`, `#` and `%` in it from being read as URI syntax."""
    return f'file:{quote(os.fspath(path))}?mode=ro'


def child(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return value if isinstance(value, dict) else {}


def model_entry(document, provider, model):
    """`provider.<id>.models.<id>` in opencode.json, or `<id>.models.<id>` in the catalog."""
    providers = document.get('provider', document)
    return child(child(child(providers, provider), 'models'), model)


def window(entries):
    for entry in entries:
        limit = number(child(entry, 'limit').get('context'))
        if limit:
            return limit
    return None


class Reader:
    def __init__(self, database=None, config=None, catalog=None):
        self.database = database or data_dir() / 'opencode.db'
        self.config = StampCache(config or config_path(), json_object, {})
        self.catalog = StampCache(catalog or catalog_path(), json_object, {})

    def query(self, session_id):
        """(session model JSON, latest usable token JSON or None), or None when unavailable."""
        try:
            connection = sqlite3.connect(read_only_uri(self.database), uri=True, timeout=1)
        except sqlite3.Error:
            return None
        try:
            row = connection.execute('select model from session where id = ?', (session_id,)).fetchone()
            usage = connection.execute(LATEST_USAGE, (session_id,)).fetchone()
        except sqlite3.Error:
            return None
        finally:
            connection.close()
        if not row:
            return None
        return row[0], usage[0] if usage else None

    def observe(self, session_id):
        """Observation for one session, or {} when the database or session is unavailable."""
        if not self.database.exists():
            return {}
        found = self.query(session_id)
        if found is None:
            return {}
        model = session_model(found[0])
        provider, model_id = model.get('providerID'), model.get('id')
        if not isinstance(provider, str) or not isinstance(model_id, str):
            return {}
        entries = [model_entry(self.config.load(), provider, model_id),
                   model_entry(self.catalog.load(), provider, model_id)]
        name = next((e['name'] for e in entries if isinstance(e.get('name'), str) and e['name']), '')
        observation = {'model_id': model_id, 'model': name or model_id, 'observed_at': time.time()}
        variant = model.get('variant')
        if isinstance(variant, str) and variant in EFFORTS:
            observation['effort'] = variant
        used = tokens_total(found[1])
        if used is not None:
            observation['context'] = context(used, window(entries))
        return observation


def parse_json(raw):
    """Parsed JSON text, or None when it is not text, is malformed or is nested too deeply."""
    if not isinstance(raw, (str, bytes)):
        return None
    try:
        return json.loads(raw)
    except (ValueError, RecursionError):
        return None


def session_model(raw):
    model = parse_json(raw)
    return model if isinstance(model, dict) else {}


def tokens_total(raw):
    tokens = raw if isinstance(raw, dict) else parse_json(raw)
    if not isinstance(tokens, dict):
        return None
    total = number(tokens.get('total'))
    if total is not None:
        return total
    cache = child(tokens, 'cache')
    parts = [tokens.get('input'), tokens.get('output'), tokens.get('reasoning'), cache.get('read'), cache.get('write')]
    numbers = [n for n in map(number, parts) if n is not None]
    return sum(numbers) if numbers else None
