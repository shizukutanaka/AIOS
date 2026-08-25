"""aictl completion — generate shell completions.

The command lists are derived from the registered parser, not maintained by
hand. They used to be three separate hardcoded lists — bash 38 names, zsh 17,
fish 38 — against a surface of 80 commands, so most of the CLI had no tab
completion at all, and the bash subcommand table knew five of `model`'s eight
subcommands. Nothing failed; the completions were just silently wrong, which
for completions is invisible: a user cannot tab-complete a command they don't
know exists, and never learns it was the completion script's fault.

Each generator accepts explicit lists for tests and derives from
`aictl.core.cli_surface` when called bare, so `aictl completion <shell>`
always describes the parser it shipped with — plugin commands included.
"""

from __future__ import annotations

from typing import Any

import argparse


def register(sub: Any) -> None:
    """Register CLI subcommand and arguments."""
    p = sub.add_parser("completion", help="Generate shell completions")
    p.add_argument("shell", choices=["bash", "zsh", "fish"], help="Shell type")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """Execute the completion command."""
    shell = args.shell
    if shell == "bash":
        print(_bash_completion())
    elif shell == "zsh":
        print(_zsh_completion())
    elif shell == "fish":
        print(_fish_completion())
    return 0


def _surface() -> tuple[dict[str, str], dict[str, list[str]]]:
    from aictl.core.cli_surface import registered_commands, registered_subcommands
    return registered_commands(), registered_subcommands()


_BASH_TEMPLATE = r'''# aictl bash completion — add to ~/.bashrc:
# eval "$(aictl completion bash)"
_aictl() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="__AICTL_COMMANDS__"

    if [ $COMP_CWORD -eq 1 ]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        return 0
    fi

    case "$prev" in
__AICTL_SUBCOMMAND_CASES__
    esac
}
# -o default: when nothing above matched, fall back to filename completion —
# covers `aictl apply <manifest>` and friends without a per-command special case.
complete -o default -F _aictl aictl
complete -o default -F _aictl python3\ -m\ aictl'''


def _bash_completion(commands: dict[str, str] | None = None,
                     subs: dict[str, list[str]] | None = None) -> str:
    """Generate bash completion script."""
    if commands is None or subs is None:
        commands, subs = _surface()
    cases = "\n".join(
        f'        {name}) COMPREPLY=($(compgen -W "{" ".join(subnames)}" -- "$cur")) ;;'
        for name, subnames in sorted(subs.items()))
    # str.replace rather than an f-string: the template is full of ${...}
    # shell parameter braces that format() and f-strings both fight with.
    return (_BASH_TEMPLATE
            .replace("__AICTL_COMMANDS__", " ".join(sorted(commands)))
            .replace("__AICTL_SUBCOMMAND_CASES__", cases))


def _zsh_completion(commands: dict[str, str] | None = None) -> str:
    """Generate zsh completion script."""
    if commands is None:
        commands, _ = _surface()
    entries = []
    for name in sorted(commands):
        # _describe splits name from description on the first colon, so the
        # name side must escape colons; the description side need not. The
        # single-quote escape is load-bearing: `troubleshoot`'s help contains
        # a bare apostrophe, which would end the string mid-entry.
        desc = (commands[name] or name).replace("\n", " ").replace("'", "'\\''")
        entries.append(f"        '{name.replace(':', chr(92) + ':')}:{desc}'")
    body = "\n".join(entries)
    return (
        "#compdef aictl\n"
        "# aictl zsh completion — add to ~/.zshrc:\n"
        '# eval "$(aictl completion zsh)"\n'
        "_aictl() {\n"
        "    local -a commands=(\n"
        f"{body}\n"
        "    )\n"
        "    _describe 'command' commands\n"
        "}\n"
        "compdef _aictl aictl"
    )


def _fish_completion(commands: dict[str, str] | None = None,
                     subs: dict[str, list[str]] | None = None) -> str:
    """Generate fish completion script."""
    if commands is None or subs is None:
        commands, subs = _surface()
    lines = ["# aictl fish completion",
             "# add to ~/.config/fish/completions/aictl.fish"]
    for name in sorted(commands):
        desc = (commands[name] or name).replace("\n", " ")
        desc = desc.replace("\\", "\\\\").replace("'", "\\'")
        lines.append(f'complete -c aictl -n "__fish_use_subcommand" '
                     f"-a {name} -d '{desc}'")
    for name, subnames in sorted(subs.items()):
        lines.append(f'complete -c aictl -n "__fish_seen_subcommand_from {name}" '
                     f'-a "{" ".join(subnames)}"')
    return "\n".join(lines)
