"""Lore project preparation commands."""
from ...storage.files import locks


def prepare(*, root, identifier, executable, row, database, project, generation_dir, settings, run_step):
    model = settings.get("model_dir", str(root / "models" / "lore"))
    settings["model_dir"] = model
    base = [executable, *row["args"], "--index-dir", str(generation_dir), "--model-dir", model]
    if settings.get("install_model"):
        with locks(root / ".locks", "lore-model", wait=True):
            run_step(root, identifier, "lore: model installation", [*base, "model", "install"], generation_dir / "model-report.txt")
    run_step(root, identifier, "lore: index", [*base, "index", str(project), "--json"], generation_dir / "index-report.json")
    run_step(root, identifier, "lore: freshness", [*base, "status", "--check", "--json"], generation_dir / "status-report.json")
    args = [*row["args"], "--index-dir", str(generation_dir), "--model-dir", model, "mcp"]
    if settings.get("usage_config"):
        args += ["--usage-config", settings["usage_config"]]
    return args
