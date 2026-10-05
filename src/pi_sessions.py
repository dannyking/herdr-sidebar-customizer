"""Model, thinking level and context for pi and omp (oh-my-pi) sessions.

Both write an append-only JSONL tree: each entry names its parent, and the
active branch runs from the last entry back to the root. Herdr's integrations
report the session file's path, so a path is accepted only inside that
harness's own sessions directories. Only metadata fields are kept: never
message content, titles or custom entries.

Formats were read from pi (badlogic/pi-mono 7fbbd5f, @earendil-works 1.0.0) and
omp (can1357/oh-my-pi 3b003d8, 18.4.10) and checked against real sessions.
Context follows each app's own footer, from the latest finished reply on the
branch since any compaction: pi counts its total tokens, omp only the prompt.
Neither stores the context window, so it comes from the user's model config or
the installed model catalog; without one, context is omitted.
"""
import glob
import json
import os
from pathlib import Path
import shutil
import stat
import time

from agent_info import EFFORTS, context, number, read_json as read_any_json

KINDS = ('pi', 'omp')
# pi's and omp's documented window for a custom model that does not set one.
CUSTOM_DEFAULT_WINDOW = 128000
MAX_BYTES = 32 * 1024 * 1024
SKIP_LEAF = {'title', 'session', 'model_usage'}


def home():
    return Path.home()


def pi_agent_dir():
    value = os.environ.get('PI_CODING_AGENT_DIR')
    return Path(value).expanduser() if value else home() / '.pi/agent'


def omp_agent_dirs():
    root = home() / os.environ.get('PI_CONFIG_DIR', '.omp')
    value = os.environ.get('PI_CODING_AGENT_DIR')
    dirs = [Path(value).expanduser()] if value else []
    dirs.append(root / 'agent')
    dirs += [Path(p) for p in sorted(glob.glob(str(root / 'profiles/*/agent')))]
    return dirs


def session_roots(kind):
    """Directories whose .jsonl files may be read for this harness."""
    roots = []
    custom = os.environ.get('PI_CODING_AGENT_SESSION_DIR')
    if custom:
        roots.append(Path(custom).expanduser())
    if kind == 'pi':
        agent = pi_agent_dir()
        roots.append(agent / 'sessions')
        settings = read_json(agent / 'settings.json')
        if isinstance(settings.get('sessionDir'), str):
            roots.append(Path(settings['sessionDir']).expanduser())
    else:
        roots += [d / 'sessions' for d in omp_agent_dirs()]
        data = Path(os.environ.get('XDG_DATA_HOME', str(home() / '.local/share')))
        roots.append(data / 'omp/sessions')
    return roots


def allowed_path(kind, value):
    """The resolved session file when it is a .jsonl under one of the harness's roots.

    omp reports the path before its first turn writes the file, so a file that
    does not exist yet is accepted and simply has no details until it appears.
    """
    if kind not in KINDS or not isinstance(value, str) or not value.endswith('.jsonl') \
            or not os.path.isabs(value):
        return None
    try:
        path = Path(value).resolve()
    except (OSError, RuntimeError):
        return None
    if path.exists() and not path.is_file():
        return None
    for root in session_roots(kind):
        try:
            if path.is_relative_to(root.resolve()):
                return path
        except OSError:
            continue
    return None


def read_json(path):
    try:
        value = read_any_json(Path(path))
    except RecursionError:
        return {}
    return value if isinstance(value, dict) else {}


def parse_json(data):
    """Parsed JSON, or None when it is malformed or nested too deeply to parse."""
    try:
        return json.loads(data)
    except (ValueError, RecursionError):
        return None


def slim(entry):
    """Keep only the metadata the reader uses.

    The text fields are later used in set and dict lookups, so a value of any
    other type is dropped here rather than allowed to raise there.
    """
    kept = {k: entry[k] for k in ('type', 'id', 'parentId', 'thinkingLevel', 'provider',
                                  'modelId', 'model', 'role') if isinstance(entry.get(k), str)}
    message = entry.get('message')
    if isinstance(message, dict) and message.get('role') == 'assistant':
        texts = {k: message[k] for k in ('role', 'provider', 'model', 'stopReason')
                 if isinstance(message.get(k), str)}
        counts = {k: message[k] for k in ('usage', 'contextSnapshot') if k in message}
        kept['message'] = texts | counts
    return kept


class SessionFile:
    """Incremental reader for one append-only session file.

    pi and omp embed pasted images as base64, so files can grow very large;
    each read is capped at MAX_BYTES and only small metadata is kept.
    """

    def __init__(self, path):
        self.path = path
        self.reset(None)

    def reset(self, identity):
        self.identity, self.offset, self.partial, self.skipping = identity, 0, b'', False
        self.entries, self.order = {}, []

    def update(self):
        """Read whatever was appended since the last call.

        Raises OSError when the path is no longer a regular file.
        """
        # Non-blocking, so a FIFO swapped in for the session file cannot stall
        # the worker on open; checking the open descriptor avoids a race.
        flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0)
        descriptor = os.open(self.path, flags)
        with os.fdopen(descriptor, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise OSError(f'not a regular file: {self.path}')
            if info.st_ino != self.identity or info.st_size < self.offset:
                # Rewritten (e.g. a format migration): start again.
                self.reset(info.st_ino)
            handle.seek(self.offset)
            while self.offset < info.st_size:
                chunk = handle.read(MAX_BYTES)
                if not chunk:
                    break
                self.offset += len(chunk)
                self.consume(chunk)

    def consume(self, chunk):
        if self.skipping:
            end = chunk.find(b'\n')
            if end < 0:
                return
            chunk, self.skipping = chunk[end + 1:], False
        *lines, self.partial = (self.partial + chunk).split(b'\n')
        if len(self.partial) > MAX_BYTES:
            # A single entry this large is mostly embedded data, not metadata:
            # drop it rather than buffer it, and resume at the next line.
            self.partial, self.skipping = b'', True
        for line in lines:
            self.add(line)

    def add(self, line):
        entry = parse_json(line)
        if isinstance(entry, dict) and isinstance(entry.get('id'), str):
            if entry['id'] not in self.entries:
                self.order.append(entry['id'])
            self.entries[entry['id']] = slim(entry)

    def branch(self):
        """Entries from the root to the active leaf, following parent links."""
        leaf = next((i for i in reversed(self.order) if self.entries[i].get('type') not in SKIP_LEAF), None)
        path, seen = [], set()
        while leaf in self.entries and leaf not in seen:
            seen.add(leaf)
            path.append(self.entries[leaf])
            leaf = self.entries[leaf].get('parentId')
        return path[::-1]


def context_tokens(kind, message):
    usage = message.get('usage') if isinstance(message.get('usage'), dict) else {}
    parts = [number(usage.get(k)) or 0 for k in ('input', 'output', 'cacheRead', 'cacheWrite')]
    if kind == 'pi':
        return number(usage.get('totalTokens')) or sum(parts)
    snapshot = message.get('contextSnapshot') if isinstance(message.get('contextSnapshot'), dict) else {}
    prompt = number(snapshot.get('promptTokens'))
    if prompt is not None:
        return max(0, prompt - (number(snapshot.get('historyRewriteTokensRemoved')) or 0))
    if number(usage.get('contextTokens')) is not None:
        return usage['contextTokens']
    # omp excludes output tokens from context.
    return parts[0] + parts[2] + parts[3] or number(usage.get('totalTokens')) or 0


def state(kind, branch):
    """(provider, model, thinking, used tokens or None) for the active branch."""
    provider = model = thinking = used = None
    for entry in branch:
        entry_type = entry.get('type')
        if entry_type == 'thinking_level_change':
            thinking = entry.get('thinkingLevel') or 'off'
        elif entry_type == 'model_change':
            if kind == 'pi' and isinstance(entry.get('modelId'), str):
                provider, model = entry.get('provider'), entry['modelId']
            elif kind == 'omp' and isinstance(entry.get('model'), str) and '/' in entry['model'] \
                    and entry.get('role') in (None, 'default', 'temporary', 'fallback'):
                provider, model = entry['model'].split('/', 1)
        elif entry_type == 'compaction':
            used = None  # Unknown until a reply follows the compaction.
        message = entry.get('message')
        if entry_type == 'message' and message:
            if isinstance(message.get('model'), str):
                provider, model = message.get('provider'), message['model']
            if message.get('stopReason') not in ('aborted', 'error'):
                tokens = context_tokens(kind, message)
                if tokens:
                    used = tokens
    return provider, model, thinking, used


def package_dir(command, marker):
    """The installed package directory for a CLI, found from its executable."""
    extra = [home() / '.local/bin', home() / '.bun/bin', home() / '.npm-global/bin',
             Path('/opt/homebrew/bin'), Path('/usr/local/bin')]
    path = os.environ.get('PATH', '') + os.pathsep + os.pathsep.join(map(str, extra))
    found = shutil.which(command, path=path)
    if not found:
        return None
    for parent in Path(found).resolve().parents:
        if (parent / 'package.json').exists() and parent.name == marker:
            return parent
    return None


def child(mapping, key):
    value = mapping.get(key) if isinstance(mapping, dict) else None
    return value if isinstance(value, dict) else {}


def text_or_none(value):
    return value if isinstance(value, str) else None


class Catalog:
    """{(provider, model): (window, name)} from the installed app's built-in models, loaded once."""

    def __init__(self, kind):
        self.kind, self.loaded, self.index = kind, False, {}

    def lookup(self, provider, model):
        if not self.loaded:
            self.loaded = True
            try:
                self.index = CATALOG_LOADERS[self.kind]()
            except (OSError, ValueError, AttributeError, RecursionError):
                self.index = {}
        return self.index.get((provider, model), (None, None))


def load_pi_catalog():
    index = {}
    package = package_dir('pi', 'pi-coding-agent')
    if not package:
        return index
    for data in (package / 'node_modules/@earendil-works/pi-ai/dist/providers/data',
                 package.parent / 'pi-ai/dist/providers/data'):
        for file in sorted(data.glob('*.json')):
            for api in json.loads(file.read_text()).values():
                for entry in (api.values() if isinstance(api, dict) else []):
                    provider = entry.get('provider') if isinstance(entry, dict) else None
                    add(index, provider, entry)
    return index


def load_omp_catalog():
    index = {}
    package = package_dir('omp', 'pi-coding-agent')
    if not package:
        return index
    for file in (package / 'node_modules/@oh-my-pi/pi-catalog/src/models.json',
                 package.parent / 'pi-catalog/src/models.json'):
        if file.exists():
            # About 12 MB; keep only the two fields used.
            for provider, models in json.loads(file.read_text()).items():
                for entry in (models.values() if isinstance(models, dict) else []):
                    add(index, provider, entry)
            break
    return index


CATALOG_LOADERS = {'pi': load_pi_catalog, 'omp': load_omp_catalog}


def add(index, provider, entry):
    if isinstance(entry, dict) and isinstance(provider, str) and isinstance(entry.get('id'), str):
        window = number(entry.get('contextWindow')) or None
        index[(provider, entry['id'])] = (window, text_or_none(entry.get('name')))


def custom_model(document, provider, model):
    """(window, name) from a user models file, or None when it does not mention the model.

    A model defined there gets the app's default window; an override of a
    built-in model leaves unset fields as None for the catalog to fill in.
    """
    entry = child(child(document, 'providers'), provider)
    override = child(child(entry, 'modelOverrides'), model)
    models = entry.get('models') if isinstance(entry.get('models'), list) else []
    defined = next((m for m in models if isinstance(m, dict) and m.get('id') == model), None)
    if not override and defined is None:
        return None
    defined = defined or {}
    window = number(override.get('contextWindow')) or number(defined.get('contextWindow'))
    if window is None and defined:
        window = CUSTOM_DEFAULT_WINDOW
    return window, text_or_none(override.get('name') or defined.get('name'))


def pi_model_info(provider, model):
    """(window, name) from pi's models.json or its models store, or None."""
    agent = pi_agent_dir()
    found = custom_model(read_json(agent / 'models.json'), provider, model)
    if found:
        return found
    store = child(read_json(agent / 'models-store.json'), provider)
    models = store.get('models') if isinstance(store.get('models'), list) else []
    for entry in models:
        if isinstance(entry, dict) and entry.get('id') == model:
            return number(entry.get('contextWindow')) or None, text_or_none(entry.get('name'))
    return None


def omp_model_info(path, provider, model):
    """(window, name) from the models.yml of the agent directory that owns this session, then the defaults."""
    owners = [p.parent for p in path.parents if p.name == 'sessions'][:1] + omp_agent_dirs()
    for agent in owners:
        for name in ('models.yml', 'models.yaml'):
            found = custom_model(load_yaml(agent / name), provider, model)
            if found:
                return found
    return None


def load_yaml(path):
    try:
        text = Path(path).read_text()
    except OSError:
        return {}
    try:
        import yaml  # Optional; the small parser below covers block-style models files.
        value = yaml.safe_load(text)
    except ImportError:
        value = simple_yaml(text)
    except Exception:  # Malformed YAML must not break the worker.
        return {}
    return value if isinstance(value, dict) else {}


def scalar(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in '\'"':
        return text[1:-1]
    for convert in (int, float):
        try:
            return convert(text)
        except ValueError:
            pass
    return {'true': True, 'false': False, 'null': None, '~': None}.get(text, text)


def simple_yaml(text):
    """Block mappings and lists of mappings with scalar values; anything else is skipped."""
    root = {}
    stack = [(-1, root)]
    lines = [line.split(' #', 1)[0].rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip() and not line.lstrip().startswith('#')]
    for index, line in enumerate(lines):
        indent = len(line) - len(line.lstrip())
        content = line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if not stack:
            return root
        parent = stack[-1][1]
        if content.startswith('- '):
            if not isinstance(parent, list):
                continue
            item = {}
            parent.append(item)
            stack.append((indent, item))
            content, indent = content[2:], indent + 2
            parent = item
        key, sep, value = content.partition(':')
        if not sep or not isinstance(parent, dict):
            continue
        key = key.strip().strip('\'"')
        if value.strip():
            parent[key] = scalar(value)
        else:
            # The next line decides between a mapping and a list.
            following = lines[index + 1].lstrip() if index + 1 < len(lines) else ''
            child = [] if following.startswith('- ') else {}
            parent[key] = child
            stack.append((indent, child))
    return root


class Reader:
    def __init__(self):
        self.files, self.catalogs = {}, {kind: Catalog(kind) for kind in KINDS}

    def window_and_name(self, kind, path, provider, model):
        """The user's configuration first; the built-in catalog fills whatever it leaves unset."""
        if kind == 'pi':
            found = pi_model_info(provider, model)
        else:
            found = omp_model_info(path, provider, model)
        window, name = found or (None, None)
        if window is None or name is None:
            catalog_window, catalog_name = self.catalogs[kind].lookup(provider, model)
            window, name = window or catalog_window, name or catalog_name
        return window, name

    def observe(self, kind, path):
        """Observation for a validated session path; {} when it cannot be read."""
        reader = self.files.get(path)
        if reader is None:
            reader = self.files[path] = SessionFile(path)
        try:
            reader.update()
        except OSError:
            return {}
        provider, model, thinking, used = state(kind, reader.branch())
        if not isinstance(model, str):
            return {}
        window, name = self.window_and_name(kind, path, provider, model)
        observation = {'model_id': model, 'model': name or model, 'observed_at': time.time()}
        if thinking in EFFORTS and thinking not in ('off', 'none'):
            observation['effort'] = thinking
        if used is not None:
            observation['context'] = context(used, window)
        return observation

    def forget(self, keep):
        self.files = {p: r for p, r in self.files.items() if p in keep}
