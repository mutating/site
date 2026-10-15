from sqlalchemy import inspect

from throng_api.models import Computer

HIDDEN = {"password_hash", "token_hash", "storage_key", "avatar_data", "name_search"}


def public(row):
    values = {
        column.key: getattr(row, column.key)
        for column in inspect(type(row)).columns
        if column.key not in HIDDEN
    }
    if isinstance(row, Computer):
        for name in (
            "cpu_millis",
            "memory_bytes",
            "reserved_cpu",
            "reserved_memory",
        ):
            values[name] = getattr(row, name)
        for name, total, reserved, target in (
            ("free_cpu", row.cpu_millis, row.reserved_cpu, row.target_cpu),
            ("free_memory", row.memory_bytes, row.reserved_memory, row.target_memory),
            ("free_disk", row.disk_bytes, row.reserved_disk, row.target_disk),
        ):
            values[name] = max(0, min(total, target if target is not None else total) - reserved)
    return values
