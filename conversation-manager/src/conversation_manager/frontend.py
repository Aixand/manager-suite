"""Frontend discovery and durable, composite conversation startup."""

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path

from aixand_filesystem import lock_file, private_directory
from .agents import AgentEnvironments
from .models import (
    ConversationError,
    canonical,
    digest,
    operation_key,
    receipt,
    reject_credentials,
)


def agent_reference(target, source):
    return dict(
        schema="conversation-manager/agent-ref-1",
        provider=target["provider"],
        environment_id=target["environment_id"],
        registry=target["registry"],
        source=deepcopy(source),
    )


def validate_agent(agent):
    if (
        not isinstance(agent, dict)
        or set(agent) != {"schema", "provider", "environment_id", "registry", "source"}
        or agent.get("schema") != "conversation-manager/agent-ref-1"
        or any(
            not isinstance(agent.get(key), str) or not agent[key]
            for key in ("provider", "environment_id", "registry")
        )
        or not isinstance(agent.get("source"), dict)
        or set(agent["source"]) != {"provider", "config", "root"}
        or agent["source"]["provider"] != agent["provider"]
    ):
        raise ConversationError(
            "validation", "Use an exact reference from available_agents"
        )
    for key in ("config", "root"):
        if agent["source"][key] is not None and not isinstance(
            agent["source"][key], str
        ):
            raise ConversationError("validation", "Invalid Agent Manager source")
    return deepcopy(agent)


async def available_agents(client, providers):
    import asyncio
    from .support import native_startup_support

    result = []
    for provider in providers:
        environments = AgentEnvironments(
            provider, config=client.agent_config, root=client.agent_root
        )
        for target in await asyncio.to_thread(environments.list):
            support = native_startup_support(provider)
            supported = support['supported']
            result.append(
                dict(
                    reference=agent_reference(target, environments.identity),
                    target=target,
                    conversation=dict(
                        **support,
                        authentication=(
                            "Selected native Codex home; account access checked at startup"
                            if provider == "codex"
                            else "Selected native OpenCode database; model access verified only by inference"
                        )
                        if supported
                        else None,
                    ),
                )
            )
    return result


@contextmanager
def startup_lock(client, operation_id):
    folder = client.home / "startup-locks"
    private_directory(folder)
    with (folder / (digest(operation_id)[7:] + ".lock")).open("a") as stream:
        os.chmod(stream.name, 0o600)
        try:
            lock_file(stream, blocking=False)
        except BlockingIOError:
            raise ConversationError(
                "busy",
                "This conversation startup is already running",
                operation_id=operation_id,
            ) from None
        yield


def incomplete(client, operation_id, child, recovery):
    status = child.get("status", "uncertain")
    error = child.get("error") or {}
    uncertain = status in ("accepted", "uncertain")
    client.store.finish(
        receipt(
            operation_id,
            "uncertain" if uncertain else "failed",
            error=ConversationError(
                error.get("code", "startup"),
                error.get("message", "Conversation startup did not complete"),
                uncertain=uncertain,
                operation_id=operation_id,
                recovery=recovery,
            ).to_dict(),
            recovery=recovery,
        )
    )
    raise ConversationError(
        error.get("code", "startup"),
        error.get("message", "Conversation startup did not complete"),
        uncertain=uncertain,
        operation_id=operation_id,
        recovery=recovery,
    )


async def start_conversation(
    client, *, agent, cwd, settings, operation_id, process=None
):
    from .config import process_defaults
    from .runtime import process_options
    from .support import native_startup_support

    requested_process = process_options(process)
    process = process_defaults(client, process, operation_id)
    operation_key(operation_id)
    agent = validate_agent(agent)
    if not native_startup_support(agent['provider'])['supported']:
        raise ConversationError(
            "unsupported",
            "Automatic conversation startup supports local Codex and POSIX OpenCode",
        )
    if settings is not None and not isinstance(settings, dict):
        raise ConversationError("validation", "Conversation settings must be a mapping")
    intent = dict(
        method="start_conversation",
        agent=agent,
        cwd=str(Path(cwd).expanduser().resolve()),
        settings=deepcopy(settings or {}),
    )
    if client.runtime["kind"] != "standalone" or process:
        intent.update(runtime=deepcopy(client.runtime), process=process)
    reject_credentials(intent)
    canonical(intent)
    suffix = digest(operation_id)[7:]
    launch_id, create_id = (
        "conversation-launch:" + suffix,
        "conversation-create:" + suffix,
    )
    with startup_lock(client, operation_id):
        row, _ = client.store.claim(
            operation_id,
            intent,
            {"launch_operation_id": launch_id, "create_operation_id": create_id},
        )
        if row["status"] == "completed":
            try:
                conversation = await client.attach_conversation(
                    row["result"]["conversation_ref"]
                )
            except ConversationError as error:
                raise ConversationError(
                    error.code,
                    "The original conversation is unavailable; no replacement was started",
                    uncertain=error.uncertain,
                    operation_id=operation_id,
                    recovery={
                        **row["recovery"],
                        "conversation_ref": row["result"]["conversation_ref"],
                    },
                ) from error
            conversation.startup_receipt = deepcopy(row)
            return conversation
        recovery = dict(
            row.get("recovery", {}),
            launch_operation_id=launch_id,
            create_operation_id=create_id,
        )
        if row["status"] in ("failed", "unsupported"):
            incomplete(client, operation_id, row, recovery)
        connection = None
        transferred = False
        try:
            # Inspect retained launch before touching current Agent Manager state.
            try:
                launched = await client.operation(launch_id)
            except ConversationError as error:
                if error.code != "not_found":
                    raise
                import asyncio

                environments = AgentEnvironments(**agent["source"])
                target = await asyncio.to_thread(
                    environments.resolve, agent["environment_id"]
                )
                if target["registry"] != agent["registry"]:
                    raise ConversationError(
                        "conflict", "The original Agent Manager registry changed"
                    )
                if agent["provider"] == "opencode":
                    launched = await shared_opencode(
                        client,
                        agent,
                        intent["cwd"],
                        environments,
                        launch_id,
                        process=process,
                        requested_process=requested_process,
                    )
                else:
                    launched = await client.launch_codex(
                        agent["environment_id"],
                        cwd=intent["cwd"],
                        agents=environments,
                        settings=intent["settings"],
                        operation_id=launch_id,
                        **({"process": process} if process else {}),
                    )
            reference = (launched.get("result") or {}).get(
                "connection_ref"
            ) or launched.get("recovery", {}).get("connection_ref")
            if reference:
                recovery["connection_ref"] = reference
                recovery["runtime_ref"] = reference["runtime_ref"]
                client.store.finish(
                    receipt(operation_id, "accepted", recovery=recovery)
                )
            if launched["status"] != "completed":
                incomplete(client, operation_id, launched, recovery)
            connection = await client.connect(reference)
            try:
                created = await client.operation(create_id)
            except ConversationError as error:
                if error.code != "not_found":
                    raise
                if recovery.get("create_dispatched"):
                    raise ConversationError(
                        "uncertain",
                        "Conversation creation delivery is unknown; inspect without resending",
                        uncertain=True,
                    )
                recovery["create_dispatched"] = True
                client.store.finish(
                    receipt(operation_id, "accepted", recovery=recovery)
                )
                created = await connection.create_session(
                    {**intent["settings"], "cwd": intent["cwd"]}
                    if agent["provider"] == "opencode"
                    else {},
                    operation_id=create_id,
                )
            if created["status"] != "completed":
                incomplete(client, operation_id, created, recovery)
            conversation = await connection.attach_conversation(
                created["result"]["session_ref"]
            )
            row = client.store.finish(
                receipt(
                    operation_id,
                    "completed",
                    result={"conversation_ref": conversation.reference},
                    recovery=recovery,
                )
            )
            conversation.startup_receipt = deepcopy(row)
            transferred = True
            return conversation
        except ConversationError as error:
            recovery = {**recovery, **error.recovery}
            uncertain = error.uncertain or error.code == "busy"
            client.store.finish(
                receipt(
                    operation_id,
                    "uncertain" if uncertain else "failed",
                    error=ConversationError(
                        error.code,
                        error.message,
                        uncertain=uncertain,
                        operation_id=operation_id,
                        recovery=recovery,
                    ).to_dict(),
                    recovery=recovery,
                )
            )
            raise ConversationError(
                error.code,
                error.message,
                uncertain=uncertain,
                operation_id=operation_id,
                recovery=recovery,
            ) from error
        except Exception as error:
            client.store.finish(
                receipt(
                    operation_id,
                    "uncertain",
                    recovery=recovery,
                    error=ConversationError(
                        "uncertain",
                        "Startup outcome is unknown; inspect without replacement",
                        uncertain=True,
                        operation_id=operation_id,
                        recovery=recovery,
                    ).to_dict(),
                )
            )
            raise ConversationError(
                "uncertain",
                "Startup outcome is unknown; inspect without replacement",
                uncertain=True,
                operation_id=operation_id,
                recovery=recovery,
            ) from error
        finally:
            # Only successful startup transfers ownership of the frontend handle.
            if connection is not None and not transferred:
                await connection.disconnect()


async def shared_opencode(
    client,
    agent,
    cwd,
    environments,
    operation_id,
    *,
    process=None,
    requested_process=None,
):
    """One retained OpenCode owner per selected environment and local store."""
    from .runtime import process_matches

    key = digest([agent["registry"], agent["environment_id"]])
    with startup_lock(client, "opencode-owner:" + key):
        owner = client.store.get("opencode-owner", key)
        previous = None
        if owner:
            try:
                previous = await client.operation(owner["operation_id"])
            except ConversationError as error:
                if error.code != "not_found":
                    raise
                # Native launch claims its receipt before subprocess dispatch.
                # With the owner lock acquired, absence means no process dispatch.
        if previous is not None:
            reference = (previous.get("result") or {}).get(
                "connection_ref"
            ) or previous.get("recovery", {}).get("connection_ref")
            if reference:
                observed = await client.inspect_runtime(reference["runtime_ref"])
                policy = client.store.get("launch-policy", reference["connection_id"])
                if (
                    observed["state"] == "running"
                    and policy is not None
                    and (
                        policy["runtime"] != client.runtime
                        or not process_matches(policy["process"], requested_process)
                    )
                ):
                    raise ConversationError(
                        "conflict",
                        "The shared OpenCode worker has a different runtime or process configuration",
                    )
                if observed["state"] == "running":
                    if previous["status"] != "completed":
                        raise ConversationError(
                            "uncertain",
                            "OpenCode worker initialization is unconfirmed",
                            uncertain=True,
                            recovery={
                                "connection_ref": reference,
                                "runtime_ref": reference["runtime_ref"],
                            },
                        )
                    connection = await client.connect(reference)
                    await connection.disconnect()
                    client.store.claim(
                        operation_id,
                        dict(method="reuse_opencode", agent=agent, cwd=cwd),
                        {
                            "connection_ref": reference,
                            "runtime_ref": reference["runtime_ref"],
                        },
                    )
                    return client.store.finish(
                        receipt(
                            operation_id,
                            "completed",
                            result={"connection_ref": reference},
                            recovery={
                                "connection_ref": reference,
                                "runtime_ref": reference["runtime_ref"],
                            },
                        )
                    )
            elif previous["status"] in ("uncertain", "accepted"):
                raise ConversationError(
                    "uncertain",
                    "The prior OpenCode launch outcome is unknown",
                    uncertain=True,
                )
            elif owner["operation_id"] == operation_id:
                return previous
        client.store.put("opencode-owner", key, {"operation_id": operation_id})
        return await client.launch_opencode(
            agent["environment_id"],
            cwd=cwd,
            agents=environments,
            settings={},
            operation_id=operation_id,
            **({"process": process} if process else {}),
        )
