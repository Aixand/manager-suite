"""Public source operations backed by MCP-owned source and job services."""

import time
from .records import digest
from .repository import SourceRepository


class SourceService:
    def __init__(self, home, engine, *, source_client=None):
        self.home, self.engine = home, engine
        self.source_client = source_client or (lambda: None)

    def backend(self):
        return SourceRepository(self.home, self.engine, client=self.source_client())

    def call(self, method, *args, **kwargs):
        return getattr(self.backend(), method)(*args, **kwargs)

    def source_capabilities(self):
        return {
            "schema": "resource-sources/capabilities-1",
            "create": True,
            "github": True,
            "private_github": False,
            "source_jobs": True,
            "installation": True,
            "transports": ["stdio"],
        }

    def create_managed(self, name, **options):
        return self.call("create", name, **options)

    def discover_source(self, url, **options):
        return self.call("discover", url, **options)

    def import_source(self, discovery_id, selections=None, **options):
        return self.call("import_source", discovery_id, selections, **options)

    def import_local(self, path, **options):
        return self.call("import_local", path, **options)

    def check_updates(self, publisher, identifier, **options):
        return self.call("check", publisher, identifier, **options)

    def source_status(self, publisher, identifier):
        status = self.engine.status(publisher, identifier)
        return {
            "source": status.get("source"),
            "check": self.call("check_status", publisher, identifier),
        }

    def source_settings(self, publisher, identifier, **options):
        return self.call("settings", publisher, identifier, **options)

    def switch_source(self, publisher, identifier, **options):
        return self.call("switch", publisher, identifier, **options)

    def check_due_sources(self):
        import uuid

        result = []
        sources = self.backend()
        for row in self.engine.catalog(verify=False):
            source = row.get("source")
            if not source or source["pinned"] or not row["tracking"]:
                continue
            last = sources.check_status(row["publisher_id"], row["id"]) or {}
            interval = source.get("interval_hours", 24) * 3600
            if time.time() - last.get("checked_at", 0) < interval:
                continue
            identity = digest(
                [
                    str(sources.home),
                    "mcp",
                    row["publisher_id"],
                    row["id"],
                    source["commit"],
                    int(time.time() // interval),
                ]
            )
            key = str(uuid.uuid5(uuid.NAMESPACE_URL, identity))
            result.append(
                self.start_source_job(
                    "check",
                    {
                        "publisher": row["publisher_id"],
                        "identifier": row["id"],
                        "force": False,
                    },
                    key=key,
                )
            )
        return result

    def job_call(self, method, *args, **kwargs):
        from ..jobs import sources as jobs

        return getattr(jobs, method)(self.backend(), *args, **kwargs)

    def start_source_job(self, operation, request, *, key=None):
        return self.job_call("start", operation, request, key=key)

    def source_job(self, job_id):
        return self.job_call("status", job_id)

    def source_jobs(self):
        return self.job_call("listing")

    def cancel_source_job(self, job_id):
        return self.job_call("cancel", job_id)
