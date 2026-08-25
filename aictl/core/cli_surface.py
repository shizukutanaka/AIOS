"""The registered command surface, derived — because four copies of it rotted.

aictl's real command surface is whatever `build_parser()` registers: 80
commands today, plus anything plugins add at runtime. Four places maintained
their own hand-written copy of that fact, and every one had drifted:

  * `gate`'s Docs phase checked a 10-name list frozen at v1.6.0 — directly
    above a CHANGELOG check that was *derived*, with a comment explaining why
    hardcoded literals rot. The argument was applied to one check and not the
    two beside it. Better: the phase built the full parser and computed
    `set(a.choices.keys())` — the true surface — and threw it away unassigned.
  * `help` told users this was "the full 65-command surface" (it was 80), and
    hand-maintained a category listing that its own gate check could never
    verify because the names were written without the `aictl ` prefix.
  * `completion` hardcoded three separate lists — bash 38 names, zsh 17,
    fish 38 — so up to 63 of 80 commands had no tab completion at all.

One derived source, consulted at call time so plugin-registered commands are
included. `build_parser` is imported lazily inside each function, the same
pattern gate.py and help.py already use, so importing this module from
`aictl.core` cannot create an import cycle with `aictl.__main__`.

The reference scanners exist for the other direction: documentation naming a
command that does not exist. Their matching rule is structural, not a stopword
list — `\\s` would cross newlines and match prose like "...aictl\\nanswer...",
and markdown prose legitimately says "aictl does" or "aictl exposes", so the
markdown scanner only trusts command contexts (fenced code blocks and inline
backticks).
"""

from __future__ import annotations

import argparse
import re

# Same-line only. `\s` crosses newlines, which is exactly how an earlier probe
# produced ghost "commands" out of ordinary prose.
_CMD_REF = re.compile(r"aictl[ \t]+([a-z][a-z0-9_-]*)")


def _subparser_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [a for a in parser._actions
            if isinstance(a, argparse._SubParsersAction)]


def registered_commands(
        parser: argparse.ArgumentParser | None = None) -> dict[str, str]:
    """Every registered command, mapped to its argparse help string.

    Derived at call time rather than cached: plugins register commands when the
    parser is built, so the set is a property of *this* invocation.
    """
    if parser is None:
        from aictl.__main__ import build_parser
        parser = build_parser()
    commands: dict[str, str] = {}
    for action in _subparser_actions(parser):
        for name in action.choices:
            commands.setdefault(name, "")
        # help= strings live on the pseudo-actions, not the subparsers.
        for choice in action._choices_actions:
            if choice.dest in commands:
                commands[choice.dest] = (choice.help or "").strip()
    return commands


def registered_subcommands(
        parser: argparse.ArgumentParser | None = None) -> dict[str, list[str]]:
    """Commands that have nested subcommands, mapped to those names, sorted."""
    if parser is None:
        from aictl.__main__ import build_parser
        parser = build_parser()
    nested: dict[str, list[str]] = {}
    for action in _subparser_actions(parser):
        for name, sub in action.choices.items():
            inner: set[str] = set()
            for inner_action in _subparser_actions(sub):
                inner.update(inner_action.choices)
            if inner:
                nested[name] = sorted(inner)
    return nested


def command_references(text: str) -> set[str]:
    """Every `aictl <token>` on a single line, anywhere in the text.

    For text that exists to name commands — the help topics — where every
    reference is intended as a reference.
    """
    return set(_CMD_REF.findall(text))


def markdown_command_references(text: str) -> set[str]:
    """`aictl <token>` references that appear in *command contexts* only.

    Markdown prose legitimately writes "aictl does not…" or "aictl exposes 19
    tools"; flagging `does` as a ghost command would make the checker cry wolf
    until someone turned it off. So the rule is structural: a token counts only
    inside a ``` fence, or when the match is immediately preceded by a
    backtick (inline code).
    """
    refs: set[str] = set()
    fenced = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            refs.update(_CMD_REF.findall(line))
            continue
        for match in _CMD_REF.finditer(line):
            if match.start() > 0 and line[match.start() - 1] == "`":
                refs.add(match.group(1))
    return refs


def mcp_declared_tools() -> set[str]:
    """Tool names the MCP server advertises in `tools/list`."""
    from aictl.mcp_server import TOOLS
    return {t["name"] for t in TOOLS if isinstance(t, dict) and "name" in t}


def mcp_dispatched_tools() -> set[str]:
    """Tool names `_dispatch_tool` actually routes.

    Read from the dispatcher's AST rather than by calling it: every handler
    does real work — hardware detection, a security scan, an LLM call — so
    probing reachability by invocation would make the gate slow and
    side-effecting. Which names the dispatcher compares against is a static
    property, so it is read statically, and parsed rather than grepped so a
    name appearing in a docstring or comment cannot be mistaken for a route.

    The pairing matters more than either number: a tool declared but not
    dispatched is advertised in `tools/list` and then fails when called, and
    one dispatched but not declared is unreachable code. A count threshold
    sees neither.
    """
    import ast
    from pathlib import Path

    import aictl.mcp_server as mcp

    tree = ast.parse(Path(mcp.__file__).read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == "_dispatch_tool"):
            continue
        for inner in ast.walk(node):
            if (isinstance(inner, ast.Compare)
                    and isinstance(inner.left, ast.Name)
                    and inner.left.id == "name"):
                for comparator in inner.comparators:
                    if isinstance(comparator, ast.Constant) and \
                            isinstance(comparator.value, str):
                        names.add(comparator.value)
    return names


def rest_endpoint_count(module_path: str | None = None) -> int:
    """How many `/v1/` REST endpoints the daemon serves. 0 if undeterminable.

    Routes are literal dicts inside the `do_GET`/`do_POST`/... handlers, so
    they are read from the AST — the same reasoning as `mcp_dispatched_tools`:
    the alternative is starting a daemon and probing it, which is slow and
    side-effecting for a number used to check a line of documentation.

    `/metrics` is excluded deliberately. It serves Prometheus text exposition,
    not JSON, and is not part of the REST API the docs count — which is why
    "30 REST API endpoints" is right while the handler has 31 routes. An
    earlier probe that counted routes naively concluded the documentation was
    wrong by one; it was not.
    """
    import ast
    from pathlib import Path

    if module_path is None:
        import aictl.daemon.aiosd as aiosd
        module_path = aiosd.__file__
    try:
        tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 0

    paths: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("do_")):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Dict):
                for key in inner.keys:
                    if (isinstance(key, ast.Constant)
                            and isinstance(key.value, str)
                            and key.value.startswith("/v1/")):
                        paths.add(key.value)
    return len(paths)


def unregistered_command_modules(root: "object | None" = None) -> list[str]:
    """Modules in aictl/cmd/ that no registered command corresponds to.

    CLAUDE.md's own workflow says "Register new commands in `__main__.py`",
    which is a step a person forgets. A module sitting there unregistered is
    invisible: it imports fine, it has tests, and the command simply does not
    exist for any user.

    Two modules are named `<command>_cmd.py` because their command name is a
    Python keyword or builtin (`import`, `cache`), so the suffix is stripped
    before looking the name up — a convention, not a defect.
    """
    from pathlib import Path

    base = Path(root) if root is not None else Path(".")
    directory = base / "aictl" / "cmd"
    if not directory.is_dir():
        return []
    registered = set(registered_commands())
    missing = []
    for module in sorted(directory.glob("*.py")):
        if module.stem.startswith("__"):
            continue
        expected = module.stem[:-4] if module.stem.endswith("_cmd") else module.stem
        if expected not in registered:
            missing.append(module.name)
    return missing
