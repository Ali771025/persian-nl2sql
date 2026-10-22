from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .models import Artifact, ArtifactKind, Catalog, ColumnInfo, ForeignKey
from .normalization import normalize_persian

_NOISY_PATH_PARTS = {
    "indexes",
    "statistics",
    "triggers",
    "imagesmetro",
    "images32x32metro",
    "__pycache__",
}
_TABLE_HEADER = re.compile(
    r"Table\s*:\s*(?:(?P<db>[\w-]+)\.)?"
    r"(?:(?P<schema>[\w-]+)\.)?(?P<table>[A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)
_REL_HEADER = re.compile(
    r"Relationship\s*:\s*(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s+on\s+(?P<child>[A-Za-z_][A-Za-z0-9_]*)",
    re.I,
)
_SQL_TABLE = re.compile(
    r"\b(?:FROM|JOIN|UPDATE|INTO|TABLE|REFERENCES)\s+"
    r"(?:\[[^\]]+\]\.)?\[?([A-Za-z_][A-Za-z0-9_]*)\]?",
    re.I,
)
_DDL_STATEMENT = re.compile(
    r"\b(?:CREATE|ALTER|DROP)\s+(?:OR\s+ALTER\s+)?"
    r"(?:TABLE|VIEW|FUNCTION|PROCEDURE|INDEX|TYPE|SCHEMA)\b",
    re.I,
)
_SEMANTIC_ALIAS_KEYS = ("مترادف", "alias", "synonym", "نام دیگر")
_SEMANTIC_RULE_KEYS = (
    "قانون",
    "قاعده",
    "رابطه",
    "مرحله",
    "حالت",
    "استثنا",
    "نکته",
    "ویژگی",
    "روال",
    "قابل نمایش",
    "نحوه تشخیص",
    "شرط",
)
_INGESTION_SCHEMA_VERSION = "4"


def _clean_markdown(value: str) -> str:
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = value.replace("&#xfeff;", "").replace("\ufeff", "")
    value = value.replace("\\[", "[").replace("\\]", "]")
    value = re.sub(r"<[^>]+>", " ", value)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _row_cells(line: str) -> list[str]:
    value = line.strip()
    if value.startswith("|"):
        value = value[1:]
    if value.endswith("|"):
        value = value[:-1]
    return [_clean_markdown(cell).strip() for cell in re.split(r"(?<!\\)\|", value)]


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^###\s+{re.escape(heading)}\s*$", text, flags=re.I | re.M)
    if not match:
        return ""
    tail = text[match.end() :]
    end = re.search(r"^(?:---\s*$|###\s+)", tail, flags=re.M)
    return tail[: end.start() if end else len(tail)].strip()


def _artifact_id(kind: ArtifactKind, source_path: str, title: str) -> str:
    raw = f"{kind.value}:{source_path}:{title}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


def _semantic_payload(value: str) -> dict[str, object]:
    candidate = value.replace(r"\[", "[").replace(r"\]", "]")
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _semantic_values(
    payload: object,
    key_fragments: tuple[str, ...],
) -> list[str]:
    output: list[str] = []

    def walk(value: object, parent_key: str = "") -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                normalized_key = normalize_persian(str(key)).lower()
                if any(fragment in normalized_key for fragment in key_fragments):
                    collect(nested)
                walk(nested, normalized_key)
        elif isinstance(value, list):
            for item in value:
                walk(item, parent_key)

    def collect(value: object) -> None:
        if isinstance(value, str) and value.strip():
            output.append(normalize_persian(value).strip())
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    walk(payload)
    return list(dict.fromkeys(output))


class DocumentIngestor:
    """Converts SQL Server markdown exports and SQL examples into semantic artifacts."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.files_scanned = 0

    def scan(self) -> Catalog:
        if not self.root.exists():
            raise FileNotFoundError(f"Documents root does not exist: {self.root}")

        artifacts: list[Artifact] = []
        foreign_keys: list[ForeignKey] = []
        digest = hashlib.sha256()
        digest.update(f"ingestion-schema:{_INGESTION_SCHEMA_VERSION}".encode())
        source_checksums: dict[str, str] = {}
        source_versions: dict[str, str] = {}

        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in {".md", ".sql"}:
                continue
            self.files_scanned += 1
            relative = path.relative_to(self.root).as_posix()
            raw = path.read_text(encoding="utf-8-sig", errors="replace")
            source_checksums[relative] = hashlib.sha256(raw.encode()).hexdigest()
            source_versions[relative] = self._document_version(raw, path)
            digest.update(relative.encode("utf-8"))
            digest.update(hashlib.sha256(raw.encode("utf-8")).digest())

            if path.suffix.lower() == ".sql":
                artifacts.extend(self._parse_sql(relative, raw))
                continue

            table_match = _TABLE_HEADER.search(raw[:1_000])
            if table_match:
                artifacts.append(self._parse_table(relative, raw, table_match))
                continue

            rel_match = _REL_HEADER.search(raw[:1_000])
            if rel_match:
                artifact, edge = self._parse_relationship(relative, raw, rel_match)
                artifacts.append(artifact)
                if edge:
                    foreign_keys.append(edge)
                continue

            stem = path.stem.lower()
            noisy_filename = (
                stem == "tables"
                or stem.endswith("_index")
                or stem.startswith(
                    (
                        "indexes_on_",
                        "statistics_on_",
                        "relationships_on_",
                        "db_object_index",
                        "indexes_overall",
                    )
                )
            )
            is_technical = noisy_filename or any(part.lower() in _NOISY_PATH_PARTS for part in path.parts)
            artifacts.extend(
                self._parse_business_markdown(
                    relative,
                    raw,
                    technical=is_technical,
                )
            )

        unique_artifacts = {artifact.id: artifact for artifact in artifacts}
        unique_edges = {
            (
                edge.child_table.lower(),
                edge.child_column.lower(),
                edge.parent_table.lower(),
                edge.parent_column.lower(),
            ): edge
            for edge in foreign_keys
        }
        normalized_edges = [
            edge.model_copy(
                update={
                    "document_version": source_versions.get(
                        edge.source_path,
                        "unversioned",
                    )
                }
            )
            for edge in unique_edges.values()
        ]
        known_table_names = {
            table.lower()
            for artifact in unique_artifacts.values()
            if artifact.kind == ArtifactKind.TABLE
            for table in artifact.tables
        }
        known_table_names.update(edge.child_table.lower() for edge in normalized_edges)
        known_table_names.update(edge.parent_table.lower() for edge in normalized_edges)
        normalized_artifacts = [
            artifact.model_copy(
                update={
                    "tables": [table for table in artifact.tables if table.lower() in known_table_names],
                    "doc_id": hashlib.sha256(artifact.source_path.encode()).hexdigest()[:24],
                    "document_version": source_versions.get(artifact.source_path, "unversioned"),
                    "content_hash": self._artifact_content_hash(
                        artifact,
                        source_checksums.get(artifact.source_path, ""),
                    ),
                }
            )
            for artifact in unique_artifacts.values()
        ]
        content_hash = digest.hexdigest()
        explicit_versions = sorted(
            {version for version in source_versions.values() if version != "unversioned"},
            key=self._version_sort_key,
        )
        snapshot_version = f"snapshot-{content_hash[:12]}"
        active_schema_version = explicit_versions[-1] if explicit_versions else snapshot_version
        databases = sorted(
            {
                str(artifact.metadata.get("database"))
                for artifact in normalized_artifacts
                if artifact.metadata.get("database")
            },
            key=str.lower,
        )
        return Catalog(
            artifacts=normalized_artifacts,
            foreign_keys=normalized_edges,
            content_hash=content_hash,
            source_file_count=self.files_scanned,
            databases=databases,
            schema_versions=[*explicit_versions, snapshot_version],
            snapshot_version=snapshot_version,
            active_schema_version=active_schema_version,
        )

    @staticmethod
    def _document_version(raw: str, path: Path) -> str:
        frontmatter = re.match(r"^---\s*\n(.*?)\n---", raw, flags=re.S)
        if frontmatter:
            match = re.search(
                r"^\s*(?:version|schema_version|doc_version)\s*:\s*['\"]?([^'\"\n]+)",
                frontmatter.group(1),
                flags=re.I | re.M,
            )
            if match:
                return match.group(1).strip()
        for part in reversed(path.parts):
            if re.fullmatch(r"v?\d+(?:\.\d+){1,3}", part, flags=re.I):
                return part.lstrip("vV")
        return "unversioned"

    @staticmethod
    def _version_sort_key(value: str) -> tuple[object, ...]:
        parts = re.split(r"(\d+)", value.lower().lstrip("v"))
        return tuple(int(part) if part.isdigit() else part for part in parts)

    @staticmethod
    def _artifact_content_hash(artifact: Artifact, source_checksum: str) -> str:
        payload = {
            "kind": artifact.kind.value,
            "title": artifact.title,
            "text": artifact.text,
            "tables": artifact.tables,
            "metadata": artifact.metadata,
            "source_checksum": source_checksum,
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _parse_table(self, relative: str, raw: str, match: re.Match[str]) -> Artifact:
        table = match.group("table")
        schema = match.group("schema") or "dbo"
        database = match.group("db") or "default"
        description = _clean_markdown(_section(raw, "Description"))[:6_000]
        semantic = _semantic_payload(description)
        aliases = _semantic_values(semantic, _SEMANTIC_ALIAS_KEYS)
        business_rules = _semantic_values(semantic, _SEMANTIC_RULE_KEYS)
        columns = self._parse_columns(raw)
        column_lines = []
        for column in columns:
            nullable = "NULL" if column.nullable else "NOT NULL" if column.nullable is False else ""
            detail = f" -- {column.description[:600]}" if column.description else ""
            alias_text = f" aliases: {', '.join(column.aliases)}" if column.aliases else ""
            column_lines.append(f"- {column.name} {column.data_type} {nullable}{detail}{alias_text}".rstrip())

        text = "\n".join(
            [
                f"TABLE {database}.{schema}.{table}",
                f"Business description: {description}" if description else "",
                f"Table aliases: {', '.join(aliases)}" if aliases else "",
                "Business rules:",
                *(f"- {rule}" for rule in business_rules[:30]),
                "Columns:",
                *column_lines,
            ]
        ).strip()
        return Artifact(
            id=_artifact_id(ArtifactKind.TABLE, relative, table),
            kind=ArtifactKind.TABLE,
            title=f"{schema}.{table}",
            text=text,
            source_path=relative,
            tables=[table],
            metadata={
                "schema": schema,
                "database": database,
                "table": table,
                "columns": [column.model_dump() for column in columns],
                "description": description,
                "semantic": semantic,
                "aliases": aliases,
                "business_rules": business_rules,
            },
        )

    def _parse_columns(self, raw: str) -> list[ColumnInfo]:
        section = _section(raw, "Columns")
        lines = [line for line in section.splitlines() if line.strip().startswith("|")]
        if len(lines) < 3:
            return []
        header = [cell.lower() for cell in _row_cells(lines[0])]
        try:
            name_idx = header.index("name")
            description_idx = header.index("description")
            type_idx = header.index("data type")
            nullable_idx = header.index("nullable")
        except ValueError:
            return []

        columns: list[ColumnInfo] = []
        for line in lines[2:]:
            cells = _row_cells(line)
            if len(cells) <= max(name_idx, description_idx, type_idx, nullable_idx):
                continue
            name = cells[name_idx].strip("[] ")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                continue
            nullable_raw = cells[nullable_idx].lower()
            if "red_cross" in nullable_raw or "red cross" in nullable_raw:
                nullable = False
            elif "check" in nullable_raw:
                nullable = True
            else:
                # Image markup is removed, so infer from the original cell text when possible.
                original_cells = re.split(r"(?<!\\)\|", line.strip().strip("|"))
                original_nullable = (
                    original_cells[nullable_idx].lower() if len(original_cells) > nullable_idx else ""
                )
                nullable = "check" in original_nullable if original_nullable else None
                if "red_cross" in original_nullable:
                    nullable = False
            description = normalize_persian(cells[description_idx])
            semantic = _semantic_payload(description)
            aliases = _semantic_values(semantic, _SEMANTIC_ALIAS_KEYS)
            columns.append(
                ColumnInfo(
                    name=name,
                    data_type=cells[type_idx].replace("[", "").replace("]", ""),
                    nullable=nullable,
                    description=description,
                    aliases=aliases,
                )
            )
        return columns

    def _parse_relationship(
        self, relative: str, raw: str, match: re.Match[str]
    ) -> tuple[Artifact, ForeignKey | None]:
        name = match.group("name")
        child = match.group("child")
        description = _clean_markdown(_section(raw, "Description"))[:2_000]
        fk_section = _section(raw, "FK columns")
        lines = [line for line in fk_section.splitlines() if line.strip().startswith("|")]
        edge: ForeignKey | None = None
        parent = ""
        child_column = ""
        parent_column = ""
        if len(lines) >= 3:
            header = _row_cells(lines[0])
            row = _row_cells(lines[2])
            if len(header) >= 2 and len(row) >= 2:
                parent = re.sub(r"\s+column$", "", header[1], flags=re.I).strip()
                child_from_header = re.sub(r"\s+column$", "", header[0], flags=re.I).strip()
                child = child_from_header or child
                child_column, parent_column = row[0], row[1]
                if all((child, parent, child_column, parent_column)):
                    edge = ForeignKey(
                        name=name,
                        child_table=child,
                        child_column=child_column,
                        parent_table=parent,
                        parent_column=parent_column,
                        description=description,
                        source_path=relative,
                    )

        tables = [value for value in (child, parent) if value]
        join_hint = (
            f"JOIN {child}.{child_column} = {parent}.{parent_column}"
            if edge
            else f"Relationship {name} on {child}"
        )
        artifact = Artifact(
            id=_artifact_id(ArtifactKind.RELATIONSHIP, relative, name),
            kind=ArtifactKind.RELATIONSHIP,
            title=name,
            text=f"{join_hint}\n{description}".strip(),
            source_path=relative,
            tables=tables,
            metadata={"foreign_key": edge.model_dump() if edge else None},
        )
        return artifact, edge

    def _parse_sql(self, relative: str, raw: str) -> list[Artifact]:
        cleaned = raw.replace("\ufeff", "").strip()
        if not cleaned:
            return []
        tables = sorted(set(_SQL_TABLE.findall(cleaned)), key=str.lower)
        sql_kind = "schema_change" if _DDL_STATEMENT.search(cleaned) else "query_pattern"
        artifact_kind = (
            ArtifactKind.SCHEMA_CHANGE if sql_kind == "schema_change" else ArtifactKind.SQL_EXAMPLE
        )
        chunks = self._chunk_text(cleaned, max_chars=12_000)
        output = []
        for index, chunk in enumerate(chunks, start=1):
            title = Path(relative).stem if len(chunks) == 1 else f"{Path(relative).stem} part {index}"
            output.append(
                Artifact(
                    id=_artifact_id(artifact_kind, relative, title),
                    kind=artifact_kind,
                    title=title,
                    text=f"SQL EXAMPLE\n{chunk}",
                    source_path=relative,
                    tables=tables,
                    metadata={
                        "part": index,
                        "parts": len(chunks),
                        "sql_kind": sql_kind,
                        "statement_hash": hashlib.sha256(chunk.encode()).hexdigest(),
                    },
                )
            )
        return output

    def _parse_business_markdown(
        self,
        relative: str,
        raw: str,
        *,
        technical: bool = False,
    ) -> list[Artifact]:
        cleaned = _clean_markdown(raw)
        if not cleaned:
            return []
        output = []
        artifact_kind = ArtifactKind.TECHNICAL_METADATA if technical else ArtifactKind.BUSINESS_RULE
        for index, chunk in enumerate(self._chunk_text(cleaned, max_chars=5_000), start=1):
            title = f"{Path(relative).stem}#{index}"
            tables = sorted(set(re.findall(r"\b(?:TINF|TBAS|TMP|TAcc)[A-Za-z0-9_]+\b", chunk)))
            output.append(
                Artifact(
                    id=_artifact_id(artifact_kind, relative, title),
                    kind=artifact_kind,
                    title=title,
                    text=chunk,
                    source_path=relative,
                    tables=tables,
                    metadata={"technical": technical},
                )
            )
        return output

    @staticmethod
    def _chunk_text(text: str, max_chars: int) -> list[str]:
        if len(text) <= max_chars:
            return [text]
        paragraphs = re.split(r"\n\s*\n", text)
        chunks: list[str] = []
        current = ""
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if current:
                    chunks.append(current.strip())
                    current = ""
                chunks.extend(paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars))
                continue
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current.strip())
                current = paragraph
            else:
                current = candidate
        if current:
            chunks.append(current.strip())
        return chunks
