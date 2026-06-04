"""aictl completion — generate shell completions."""

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


def _bash_completion() -> str:
    """Generate bash completion script."""
    return r'''# aictl bash completion — add to ~/.bashrc:
# eval "$(aictl completion bash)"
_aictl() {
    local cur prev commands
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    commands="init doctor ps apply down recipe model upgrade serve node cluster logs config status snapshot otel recommend bench setup watch proxy warmup net mig audit apikey image fabric context scale tenant trace cost security convert deploy completion selftest"

    if [ $COMP_CWORD -eq 1 ]; then
        COMPREPLY=($(compgen -W "$commands" -- "$cur"))
        return 0
    fi

    case "$prev" in
        recipe) COMPREPLY=($(compgen -W "list run" -- "$cur")) ;;
        model) COMPREPLY=($(compgen -W "list register pull verify cache" -- "$cur")) ;;
        fabric) COMPREPLY=($(compgen -W "detect policy" -- "$cur")) ;;
        context) COMPREPLY=($(compgen -W "save restore list gc" -- "$cur")) ;;
        scale) COMPREPLY=($(compgen -W "keda hpa" -- "$cur")) ;;
        tenant) COMPREPLY=($(compgen -W "classes namespace cgroup" -- "$cur")) ;;
        cost) COMPREPLY=($(compgen -W "estimate compare" -- "$cur")) ;;
        deploy) COMPREPLY=($(compgen -W "plan manifest dynamo kvbm" -- "$cur")) ;;
        convert) COMPREPLY=($(compgen -W "bank model" -- "$cur")) ;;
        config) COMPREPLY=($(compgen -W "show set reset" -- "$cur")) ;;
        cluster) COMPREPLY=($(compgen -W "promote export" -- "$cur")) ;;
        snapshot) COMPREPLY=($(compgen -W "create list restore diff" -- "$cur")) ;;
        image) COMPREPLY=($(compgen -W "build formats" -- "$cur")) ;;
        apply) COMPREPLY=($(compgen -f -- "$cur")) ;;
    esac
}
complete -F _aictl aictl
complete -F _aictl python3\ -m\ aictl'''


def _zsh_completion() -> str:
    """Generate zsh completion script."""
    return r'''#compdef aictl
# aictl zsh completion — add to ~/.zshrc:
# eval "$(aictl completion zsh)"
_aictl() {
    local -a commands=(
        'init:Initialize node'
        'doctor:System diagnosis'
        'ps:List services'
        'apply:Apply stack'
        'down:Stop stack'
        'recipe:Manage recipes'
        'model:Model management'
        'serve:Start daemon'
        'deploy:Zero-config deploy'
        'status:Dashboard'
        'recommend:Model recommendations'
        'fabric:Memory fabric'
        'cost:Cost estimation'
        'security:Security scan'
        'scale:Autoscaling'
        'tenant:Multi-tenant'
        'trace:Request tracing'
    )
    _describe 'command' commands
}
compdef _aictl aictl'''


def _fish_completion() -> str:
    """Generate fish completion script."""
    cmds = ["init", "doctor", "ps", "apply", "down", "recipe", "model",
            "serve", "deploy", "status", "recommend", "fabric", "cost",
            "security", "scale", "tenant", "trace", "net", "watch",
            "proxy", "warmup", "bench", "audit", "apikey", "image",
            "otel", "config", "cluster", "snapshot", "logs", "upgrade",
            "setup", "mig", "context", "convert", "completion", "selftest", "node"]
    lines = ["# aictl fish completion", "# add to ~/.config/fish/completions/aictl.fish"]
    for c in cmds:
        lines.append(f'complete -c aictl -n "__fish_use_subcommand" -a {c}')
    return "\n".join(lines)
