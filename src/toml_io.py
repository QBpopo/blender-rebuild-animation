from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tomllib

_BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _format_key(key: str) -> str:
    if _BARE_KEY_RE.match(key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _format_dotted_path(path: str) -> str:
    return ".".join(_format_key(seg) for seg in path.split("."))


def load_toml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("rb") as f:
        return tomllib.load(f)


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return f"{value:.1f}"
        return f"{value:.10g}"
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        return f'"{escaped}"'
    if value is None:
        return '""'
    raise TypeError(f"Unsupported TOML scalar: {type(value)!r}")


def _format_array(value: list[Any]) -> str:
    items: list[str] = []
    for item in value:
        if isinstance(item, list):
            items.append(_format_array(item))
        elif isinstance(item, dict):
            raise TypeError("Inline dict in array is not supported by this writer")
        else:
            items.append(_format_scalar(item))
    return "[" + ", ".join(items) + "]"


def _split_table_fields(
    data: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, list[dict[str, Any]]]]:
    scalars: dict[str, Any] = {}
    tables: dict[str, Any] = {}
    array_tables: dict[str, list[dict[str, Any]]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            tables[key] = value
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(x, dict) for x in value)
        ):
            array_tables[key] = value  # type: ignore[assignment]
        else:
            scalars[key] = value
    return scalars, tables, array_tables


def _dump_array_table_items(
    lines: list[str], array_name: str, values: list[dict[str, Any]]
) -> None:
    for idx, item in enumerate(values):
        if idx > 0:
            lines.append("")
        lines.append(f"[[{_format_dotted_path(array_name)}]]")

        item_scalars, item_tables, item_array_tables = _split_table_fields(item)
        for scalar_key, scalar_value in item_scalars.items():
            if isinstance(scalar_value, list):
                lines.append(
                    f"{_format_key(scalar_key)} = {_format_array(scalar_value)}"
                )
            else:
                lines.append(
                    f"{_format_key(scalar_key)} = {_format_scalar(scalar_value)}"
                )

        if item_tables or item_array_tables:
            lines.append("")

        nested_first = True
        for nested_key, nested_value in item_tables.items():
            nested_name = f"{array_name}.{nested_key}"
            if not nested_first:
                lines.append("")
            _dump_table(lines, nested_name, nested_value)
            nested_first = False

        if item_tables and item_array_tables:
            lines.append("")

        nested_array_first = True
        for nested_key, nested_values in item_array_tables.items():
            if not nested_array_first:
                lines.append("")
            nested_array_name = f"{array_name}.{nested_key}"
            _dump_array_table_items(lines, nested_array_name, nested_values)
            nested_array_first = False


def _dump_table(lines: list[str], table_name: str | None, data: dict[str, Any]) -> None:
    scalars, tables, array_tables = _split_table_fields(data)
    if table_name:
        lines.append(f"[{_format_dotted_path(table_name)}]")
    for key, value in scalars.items():
        if isinstance(value, list):
            lines.append(f"{_format_key(key)} = {_format_array(value)}")
        else:
            lines.append(f"{_format_key(key)} = {_format_scalar(value)}")
    if table_name and (tables or array_tables):
        lines.append("")

    first_nested = True
    for key, value in tables.items():
        nested_name = f"{table_name}.{key}" if table_name else key
        if not first_nested:
            lines.append("")
        _dump_table(lines, nested_name, value)
        first_nested = False

    if tables and array_tables:
        lines.append("")

    first_array = True
    for key, values in array_tables.items():
        if not first_array:
            lines.append("")
        array_name = f"{table_name}.{key}" if table_name else key
        _dump_array_table_items(lines, array_name, values)

        first_array = False


def dump_toml(data: dict[str, Any], path: str | Path) -> None:
    lines: list[str] = []
    _dump_table(lines, None, data)
    content = "\n".join(lines).rstrip() + "\n"
    Path(path).write_text(content, encoding="utf-8")
