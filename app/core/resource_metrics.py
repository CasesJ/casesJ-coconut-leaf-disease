"""Small, native-memory-safe diagnostics for resource-constrained deployments."""
from __future__ import annotations

from pathlib import Path


def process_memory_metrics() -> dict[str, int | None]:
    """Return process and cgroup memory without raising during request handling."""
    metrics: dict[str, int | None] = {"rss_bytes": None, "cgroup_current_bytes": None,
                                      "cgroup_limit_bytes": None, "cgroup_oom_kill": None}
    try:
        import psutil  # noqa: PLC0415

        metrics["rss_bytes"] = psutil.Process().memory_info().rss
    except Exception:
        pass
    for key, filename in (("cgroup_current_bytes", "memory.current"),
                          ("cgroup_limit_bytes", "memory.max")):
        try:
            value = Path("/sys/fs/cgroup", filename).read_text(encoding="ascii").strip()
            metrics[key] = None if value == "max" else int(value)
        except (OSError, ValueError):
            pass
    try:
        for line in Path("/sys/fs/cgroup/memory.events").read_text(encoding="ascii").splitlines():
            name, value = line.split(maxsplit=1)
            if name == "oom_kill":
                metrics["cgroup_oom_kill"] = int(value)
                break
    except (OSError, ValueError):
        pass
    return metrics
