"""Load user-provided header description files."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from pathlib import Path


def load_header_descriptions(path: str | Path) -> OrderedDict[str, str]:
    """Load a header description mapping from JSON-like files.

    The user's files are intended to be JSON, but may contain unescaped quotes
    inside Chinese descriptions. This loader first tries strict JSON, then falls
    back to a line-oriented parser for simple `"header": "description"` files.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text, object_pairs_hook=OrderedDict)
        return OrderedDict((str(k), str(v)) for k, v in data.items())
    except json.JSONDecodeError:
        pass

    result: OrderedDict[str, str] = OrderedDict()
    for line in text.splitlines():
        line = line.strip()
        if not line or line in {"{", "}"}:
            continue
        if line.endswith(","):
            line = line[:-1]
        match = re.match(r'^"(?P<key>.+?)"\s*:\s*"(?P<value>.*)"$', line)
        if not match:
            continue
        result[match.group("key")] = match.group("value")
    if not result:
        raise ValueError(f"Could not parse header descriptions: {path}")
    return result
