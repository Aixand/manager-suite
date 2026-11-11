"""Sonar project preparation commands."""


def prepare(*, root, identifier, executable, row, database, project, generation_dir, settings, run_step):
    base = [executable, *row["args"], "--db", str(database)]
    run_step(root, identifier, "sonar: index", [*base, "index", str(project), "--resources", settings.get("resources", "none")], generation_dir / "index-report.json")
    run_step(root, identifier, "sonar: navigation", [*base, "index-navigation"], generation_dir / "navigation-report.json")
    if settings.get("resolve"):
        run_step(root, identifier, "sonar: resolve", [*base, "resolve"], generation_dir / "resolve-report.json")
    if settings.get("model_dir"):
        run_step(root, identifier, "sonar: embeddings", [*base, "embed", "--model", settings["model_dir"], "--representation", "separate"], generation_dir / "embedding-report.json")
    args = [*row["args"], "--db", str(database), "mcp"]
    if settings.get("model_dir"):
        args += ["--model", settings["model_dir"]]
    return args
