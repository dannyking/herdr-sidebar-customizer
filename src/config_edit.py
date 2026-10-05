"""Small TOML edits with reversible ownership; preserve unrelated text."""
import re
import tomllib


def section_span(text, name):
    match = re.search(r"(?m)^\[" + re.escape(name) + r"\][^\n]*(?:\n|$)", text)
    if not match:
        return None
    end = re.search(r"(?m)^\[", text[match.end():])
    return match.start(), match.end(), match.end() + end.start() if end else len(text)


def assignment(text, section, key):
    span = section_span(text, section)
    if not span:
        return None
    _, start, end = span
    match = re.search(r"(?m)^[ \t]*" + re.escape(key) + r"\s*=", text[start:end])
    if not match:
        return None
    begin = start + match.start()
    chunk = ""
    for line in text[begin:end].splitlines(keepends=True):
        chunk += line
        try:
            data = tomllib.loads(chunk)
        except tomllib.TOMLDecodeError:
            continue
        if key in data:
            return begin, begin + len(chunk), chunk
    raise ValueError("Cannot safely edit " + section + "." + key)


def set_assignment(text, section, key, raw):
    old = assignment(text, section, key)
    if old:
        return text[:old[0]] + (raw or "") + text[old[1]:]
    if raw is None:
        return text
    span = section_span(text, section)
    if not span:
        text = text.rstrip() + ("\n\n" if text.strip() else "") + "[" + section + "]\n"
        span = section_span(text, section)
    return text[:span[1]] + raw + text[span[1]:]


def is_note(line):
    stripped = line.strip()
    return not stripped or stripped.startswith("#")


def command_chunks(text):
    """Yield (start, end, raw) for each [[keys.command]] table.

    A table runs to the next header, but comments after its last key belong to
    whatever follows, so removing the table keeps them. Blank lines directly
    after the table go with it.
    """
    for match in re.finditer(r"(?ms)^\[\[keys\.command\]\][^\n]*\n.*?(?=^\[|\Z)", text):
        lines = match[0].splitlines(keepends=True)
        end = len(lines)
        while end > 1 and is_note(lines[end - 1]):
            end -= 1
        while end < len(lines) and not lines[end].strip():
            end += 1
        raw = "".join(lines[:end])
        yield match.start(), match.start() + len(raw), raw


def commands(text, plugin):
    result = []
    for start, end, raw in command_chunks(text):
        item = tomllib.loads(raw)["keys"]["command"][0]
        if item.get("type") == "plugin_action" and item.get("command", "").startswith(plugin + "."):
            result.append((start, end, raw))
    return result


def remove_commands(text, plugin):
    for start, end, _ in reversed(commands(text, plugin)):
        text = text[:start] + text[end:]
    return text


def owned_values(text, plugin):
    parsed = tomllib.loads(text)
    sidebar = parsed.get("ui", {}).get("sidebar", {})
    result = {section + "." + key: sidebar.get(section, {}).get(key)
              for section in ("spaces", "agents") for key in ("rows", "row_gap")}
    result["commands"] = [tomllib.loads(raw)["keys"]["command"][0]
                          for _, _, raw in commands(text, plugin)]
    return result


def restore_owned(current, original, applied, plugin):
    """Keep later edits. Return conflicts rather than overwriting them."""
    if current == applied:
        return original, []
    before = original or ""
    expected = owned_values(applied, plugin)
    actual = owned_values(current, plugin)
    result, conflicts = current, []
    for section in ("spaces", "agents"):
        table = "ui.sidebar." + section
        for key in ("rows", "row_gap"):
            name = section + "." + key
            if actual[name] != expected[name]:
                conflicts.append(name)
                continue
            old = assignment(before, table, key)
            result = set_assignment(result, table, key, old[2] if old else None)
        if not section_span(before, table):
            span = section_span(result, table)
            if span and not result[span[1]:span[2]].strip():
                result = result[:span[0]] + result[span[2]:]
    if actual["commands"] == expected["commands"]:
        result = remove_commands(result, plugin)
        for _, _, raw in commands(before, plugin):
            result = result.rstrip() + "\n\n" + raw
    else:
        conflicts.append("shortcuts")
    tomllib.loads(result)
    return result, conflicts
