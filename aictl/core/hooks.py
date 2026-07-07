"""Integration hooks: centralize event emission and audit logging.

Instead of modifying every command file, this module provides
decorator-style hooks that emit events and write audit entries
for key operations. Wire this into the daemon and CLI entry points.

Each hook (except on_proxy_request — see below) also calls
hook_dispatch.dispatch() so a user-configured webhook/script can react to
the event in real time ("aictl hooks add <event> --webhook/--script ..."),
not just observe it via the in-process event bus or audit log.

on_proxy_request is deliberately excluded from dispatch: it fires on every
single completions-proxy request, and a synchronous webhook/script call
there would add real latency to the hot inference path. The proxy already
writes its own audit entry directly (aictl/daemon/proxy.py).
"""

from __future__ import annotations

from typing import Any


from aictl.core.events import emit, STACK_APPLIED, STACK_STOPPED, \
    MODEL_REGISTERED, SNAPSHOT_CREATED, SLO_VIOLATION, ENGINE_READY, ENGINE_OFFLINE, \
    NODE_JOINED
from aictl.core.audit import audit
from aictl.core.hook_dispatch import dispatch as _dispatch


def on_stack_applied(stack_name: str, file: str, mode: str = "direct",
                     services: int = 0, state_dir: Any=None) -> None:
    """Called after a stack is successfully applied."""
    emit(STACK_APPLIED, source="apply",
         name=stack_name, file=file, mode=mode, services=services)
    audit("stack.applied", resource=stack_name, action="apply",
          outcome="success", state_dir=state_dir,
          mode=mode, services=services)
    _dispatch("stack.applied", state_dir=state_dir,
             name=stack_name, file=file, mode=mode, services=services)


def on_stack_stopped(stack_name: str, state_dir: Any=None) -> None:
    """Called after a stack is stopped."""
    emit(STACK_STOPPED, source="down", name=stack_name)
    audit("stack.stopped", resource=stack_name, action="stop",
          outcome="success", state_dir=state_dir)
    _dispatch("stack.stopped", state_dir=state_dir, name=stack_name)


def on_model_registered(model_name: str, digest: str = "",
                        runtime: str = "", state_dir: Any=None) -> None:
    """Called after a model is registered."""
    emit(MODEL_REGISTERED, source="model",
         name=model_name, digest=digest, runtime=runtime)
    audit("model.registered", resource=model_name, action="register",
          outcome="success", state_dir=state_dir,
          digest=digest, runtime=runtime)
    _dispatch("model.registered", state_dir=state_dir,
             name=model_name, digest=digest, runtime=runtime)


def on_model_verified(model_name: str, method: str = "",
                      valid: bool = True, state_dir: Any=None) -> None:
    """Called after a model signature is verified."""
    outcome = "success" if valid else "failure"
    event_type = "model.verified" if valid else "trust.violation"
    audit(event_type, resource=model_name, action="verify",
          outcome=outcome, state_dir=state_dir, method=method)
    _dispatch(event_type, state_dir=state_dir, name=model_name,
             method=method, valid=valid)


def on_snapshot_created(snapshot_id: str, label: str = "", state_dir: Any=None) -> None:
    """Called after a snapshot is created."""
    emit(SNAPSHOT_CREATED, source="snapshot",
         snapshot_id=snapshot_id, label=label)
    audit("snapshot.created", resource=snapshot_id, action="create",
          outcome="success", state_dir=state_dir, label=label)
    _dispatch("snapshot.created", state_dir=state_dir,
             snapshot_id=snapshot_id, label=label)


def on_engine_health_changed(engine: str, status: str,
                             endpoint: str = "", state_dir: Any=None) -> None:
    """Called when an engine's health status changes."""
    if status in ("READY", "ready", "ok"):
        emit(ENGINE_READY, source="health", engine=engine, endpoint=endpoint)
        _dispatch("engine.ready", state_dir=state_dir, engine=engine, endpoint=endpoint)
    else:
        emit(ENGINE_OFFLINE, source="health", engine=engine, endpoint=endpoint)
        _dispatch("engine.offline", state_dir=state_dir, engine=engine, endpoint=endpoint)


def on_slo_violation(engine: str, metric: str, value: float,
                     threshold: float, action: str = "", state_dir: Any=None) -> None:
    """Called when an SLO violation is detected."""
    emit(SLO_VIOLATION, source="governor",
         engine=engine, metric=metric, value=value, threshold=threshold)
    audit("slo.violation", resource=engine, action=action,
          outcome="warning", state_dir=state_dir,
          metric=metric, value=value, threshold=threshold)
    _dispatch("slo.violation", state_dir=state_dir, engine=engine,
             metric=metric, value=value, threshold=threshold, action=action)


def on_proxy_request(key_name: str = "", model: str = "",
                     engine: str = "", tokens: int = 0, state_dir: Any=None) -> None:
    """Called for each proxy request (for high-value audit).

    Deliberately does NOT dispatch — see module docstring. Fires on every
    single completions request; a synchronous webhook/script call here
    would add real latency to the hot inference path."""
    audit("proxy.request", resource=model, action="inference",
          outcome="success", state_dir=state_dir,
          key_name=key_name, engine=engine, tokens=tokens)


def on_node_joined(node_id: str, hostname: str = "",
                   address: str = "", state_dir: Any=None) -> None:
    """Called when a new node joins the cluster."""
    emit(NODE_JOINED, source="node", node_id=node_id,
         hostname=hostname, address=address)
    audit("node.joined", resource=node_id, action="join",
          outcome="success", state_dir=state_dir,
          hostname=hostname, address=address)
    _dispatch("node.joined", state_dir=state_dir, node_id=node_id,
             hostname=hostname, address=address)


def on_config_changed(key: str, old_value: str = "",
                      new_value: str = "", state_dir: Any=None) -> None:
    """Called when configuration changes."""
    audit("config.changed", resource=key, action="set",
          outcome="success", state_dir=state_dir,
          old_value=old_value, new_value=new_value)
    _dispatch("config.changed", state_dir=state_dir, key=key,
             old_value=old_value, new_value=new_value)
