"""CLI profile conventions independent of provider protocol backends."""
from agent_manager.profiles.identities import environment_id

from agent_manager.errors import Error


def model_id(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or "\0" in value
        or "\n" in value
    ):
        raise Error("Model must be a nonempty single-line string")
    return value


def launch_args(manager, profile, args, model=None):
    forwarded = list(args)
    options = forwarded[: forwarded.index("--")] if "--" in forwarded else forwarded
    raw_model = any(
        value == "--model" or value.startswith("--model=") for value in options
    )
    if raw_model and model is not None:
        raise Error("Specify the launch model before or after --, not both")
    selected = (
        model
        if model is not None
        else None
        if raw_model
        else manager.state().get("models", {}).get(environment_id(manager.state(), profile))
    )
    extra = ["--model", model_id(selected)] if selected is not None else []
    return (
        manager.saved_launch_args(profile)
        + extra
        + forwarded
    )


def effective_model(manager, profile, args=(), model=None):
    """Read explicit native model arguments; unknown native defaults stay unknown."""
    values = manager.launch_args(profile, args, model)
    if '--' in values: values = values[:values.index('--')]
    selected, index = None, 0
    while index < len(values):
        item = values[index]
        if item in ('--model', '-m'):
            index += 1
            if index >= len(values): raise Error('The model option requires a value')
            selected = values[index]
        elif item.startswith('--model='):
            selected = item.partition('=')[2]
        index += 1
    return selected


