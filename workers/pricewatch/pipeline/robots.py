"""robots.txt matching with wildcard support (RFC 9309).

Python's urllib.robotparser ignores `*` and `$` inside paths, so a rule like
`Disallow: /*?*` (block every URL with a query string) would silently be allowed.
This matcher implements the standard: most specific (longest) rule wins, Allow wins ties.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str]] = field(default_factory=list)  # (allow, pattern)


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = re.escape(pattern[:-1] if anchored else pattern).replace(r"\*", ".*")
    return re.compile(body + ("$" if anchored else ""))


class Robots:
    def __init__(self, text: str, user_agent: str):
        groups: list[_Group] = []
        current: _Group | None = None
        last_was_agent = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, value = (p.strip() for p in line.split(":", 1))
            key = key.lower()
            if key == "user-agent":
                if current is None or not last_was_agent:
                    current = _Group()
                    groups.append(current)
                current.agents.append(value.lower())
                last_was_agent = True
            elif key in ("allow", "disallow") and current is not None:
                current.rules.append((key == "allow", value))
                last_was_agent = False
            else:
                last_was_agent = False
        token = user_agent.split("/")[0].lower()
        specific = [g for g in groups if any(a != "*" and a in token for a in g.agents)]
        chosen = specific or [g for g in groups if "*" in g.agents]
        self.rules = [(allow, p, _compile(p)) for g in chosen for allow, p in g.rules if p]

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern, rx in self.rules:
            if rx.match(path):
                score = (len(pattern), allow)
                if best is None or score > best:
                    best = score
        return best is None or best[1]
