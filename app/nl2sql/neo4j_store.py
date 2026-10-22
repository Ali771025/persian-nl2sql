from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from .knowledge_protocols import StagedStoreSnapshot
from .models import ArtifactKind, Catalog
from .normalization import normalize_persian, tokenize


class Neo4jKnowledgeGraph:
    """Temporal schema graph retaining document and table versions across ingestions."""

    name = "neo4j"

    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        database: str = "neo4j",
        timeout_seconds: float = 10.0,
    ):
        try:
            from neo4j import GraphDatabase
        except ImportError as exc:
            raise RuntimeError("neo4j package is required for distributed knowledge mode") from exc
        self.uri = uri
        self.database = database
        self.driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=timeout_seconds,
            max_connection_lifetime=1_800,
            max_connection_pool_size=20,
        )

    def sync(self, catalog: Catalog, generation: int) -> int:
        staged = self.stage(catalog, generation)
        self.activate(staged)
        return staged.item_count

    def stage(self, catalog: Catalog, generation: int) -> StagedStoreSnapshot:
        """Build a complete inactive graph snapshot; activation is a separate transaction."""
        self._ensure_constraints()
        snapshot_id = catalog.content_hash
        now = datetime.now(UTC).isoformat()
        if self.active_reference() == snapshot_id:
            # Identical content is already visible. Keep it active and only advance metadata.
            with self.driver.session(database=self.database) as session:
                session.run(
                    """
                    MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})
                    SET snapshot.generation = $generation,
                        snapshot.indexed_at = $indexed_at
                    """,
                    snapshot_id=snapshot_id,
                    generation=generation,
                    indexed_at=now,
                ).consume()
            rows = self._artifact_rows(catalog)
            table_rows, _ = self._table_rows(catalog)
            expected = 1 + len(rows) + len(table_rows) + sum(len(row["columns"]) for row in table_rows)
            return StagedStoreSnapshot(snapshot_id, snapshot_id, expected)
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                MERGE (snapshot:SchemaSnapshot {id: $snapshot_id})
                SET snapshot.active = false,
                    snapshot.generation = $generation,
                    snapshot.version = $snapshot_version,
                    snapshot.content_hash = $snapshot_id,
                    snapshot.indexed_at = $indexed_at,
                    snapshot.source_file_count = $source_file_count,
                    snapshot.databases = $databases,
                    snapshot.schema_versions = $schema_versions
                """,
                snapshot_id=snapshot_id,
                generation=generation,
                snapshot_version=catalog.snapshot_version,
                indexed_at=now,
                source_file_count=catalog.source_file_count,
                databases=catalog.databases,
                schema_versions=catalog.schema_versions,
            ).consume()

        rows = self._artifact_rows(catalog)
        for batch in self._batches(rows, 200):
            self._write_artifact_batch(snapshot_id, batch)

        table_rows, table_versions = self._table_rows(catalog)
        for batch in self._batches(table_rows, 100):
            self._write_table_batch(snapshot_id, batch)

        link_rows = []
        for row in rows:
            for table in row["tables"]:
                for key in table_versions.get(str(table).lower(), []):
                    link_rows.append({"artifact_key": row["artifact_key"], "table_key": key})
        for batch in self._batches(link_rows, 500):
            self._write_about_batch(batch)

        edge_rows = self._edge_rows(catalog, table_versions)
        for batch in self._batches(edge_rows, 300):
            self._write_edge_batch(batch)

        expected = 1 + len(rows) + len(table_rows) + sum(len(row["columns"]) for row in table_rows)
        with self.driver.session(database=self.database) as session:
            record = session.run(
                """
                MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})
                OPTIONAL MATCH (snapshot)-[:HAS_ARTIFACT]->(artifact:ArtifactVersion)
                WITH snapshot, count(DISTINCT artifact) AS artifacts
                OPTIONAL MATCH (snapshot)-[:CONTAINS]->(table:TableVersion)
                OPTIONAL MATCH (table)-[:HAS_COLUMN]->(column:ColumnVersion)
                RETURN artifacts, count(DISTINCT table) AS tables, count(DISTINCT column) AS columns
                """,
                snapshot_id=snapshot_id,
            ).single()
        actual = 1 + int(record["artifacts"] or 0) + int(record["tables"] or 0) + int(record["columns"] or 0)
        if actual != expected:
            raise RuntimeError(f"Neo4j staged node count mismatch: expected={expected} actual={actual}")
        return StagedStoreSnapshot(snapshot_id=snapshot_id, reference=snapshot_id, item_count=actual)

    def activate(self, snapshot: StagedStoreSnapshot) -> None:
        with self.driver.session(database=self.database) as session:
            record = session.run(
                """
                MATCH (target:SchemaSnapshot {id: $snapshot_id})
                OPTIONAL MATCH (old:SchemaSnapshot {active: true})
                SET old.active = false
                WITH target
                SET target.active = true, target.activated_at = $activated_at
                RETURN target.id AS id
                """,
                snapshot_id=snapshot.snapshot_id,
                activated_at=datetime.now(UTC).isoformat(),
            ).single()
        if not record or record["id"] != snapshot.snapshot_id:
            raise RuntimeError("Neo4j snapshot activation target does not exist")

    def active_reference(self) -> str | None:
        with self.driver.session(database=self.database) as session:
            record = session.run(
                "MATCH (snapshot:SchemaSnapshot {active: true}) RETURN snapshot.id AS id LIMIT 1"
            ).single()
        return str(record["id"]) if record and record["id"] else None

    def restore(self, reference: str | None) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(
                "MATCH (snapshot:SchemaSnapshot {active: true}) SET snapshot.active = false"
            ).consume()
        if reference:
            self.activate(StagedStoreSnapshot(reference, reference, 0))

    @staticmethod
    def finalize(snapshot: StagedStoreSnapshot) -> None:
        del snapshot

    def abort(self, snapshot: StagedStoreSnapshot) -> None:
        if self.active_reference() == snapshot.snapshot_id:
            return
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})
                OPTIONAL MATCH (versioned)
                WHERE versioned.snapshot_id = $snapshot_id
                DETACH DELETE versioned, snapshot
                """,
                snapshot_id=snapshot.snapshot_id,
            ).consume()

    def related_tables(
        self,
        seeds: set[str],
        terms: list[str],
        depth: int,
        schema_version: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, int]:
        depth = min(max(int(depth), 1), 4)
        normalized_terms = sorted(
            {normalize_persian(term).lower() for term in terms if len(normalize_persian(term).strip()) >= 2}
        )
        version_filter = (
            ""
            if not schema_version
            else "AND (table.document_version = $version OR snapshot.version = $version)"
        )
        output: dict[str, int] = {}
        snapshot_match = (
            "MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})"
            if snapshot_id
            else "MATCH (snapshot:SchemaSnapshot {active: true})"
        )
        with self.driver.session(database=self.database) as session:
            if seeds:
                records = session.run(
                    f"""
                    {snapshot_match}
                    MATCH (snapshot)-[:CONTAINS]->(seed:TableVersion)
                    WHERE seed.normalized_name IN $seeds
                    MATCH path = (seed)-[:FOREIGN_KEY*1..{depth}]-(table:TableVersion)
                    WHERE table.snapshot_id = snapshot.id {version_filter}
                    RETURN table.name AS name, min(length(path)) AS distance
                    """,
                    seeds=[value.lower() for value in seeds],
                    version=schema_version,
                    snapshot_id=snapshot_id,
                )
                for record in records:
                    output[str(record["name"])] = int(record["distance"])
            if normalized_terms:
                records = session.run(
                    f"""
                    {snapshot_match}
                    MATCH (snapshot)-[:CONTAINS]->(table:TableVersion)
                    MATCH (term:SemanticTerm)-[:ALIAS_OF]->(target)
                    WHERE term.normalized IN $terms
                      AND (target = table OR (target:ColumnVersion AND (table)-[:HAS_COLUMN]->(target)))
                      {version_filter}
                    RETURN table.name AS name
                    """,
                    terms=normalized_terms,
                    version=schema_version,
                    snapshot_id=snapshot_id,
                )
                for record in records:
                    name = str(record["name"])
                    output[name] = min(output.get(name, 99), 1)
        return output

    def status(self) -> dict[str, object]:
        try:
            self.driver.verify_connectivity()
            with self.driver.session(database=self.database) as session:
                record = session.run(
                    """
                    OPTIONAL MATCH (snapshot:SchemaSnapshot {active: true})
                    WITH snapshot
                    OPTIONAL MATCH (snapshot)-[:CONTAINS]->(table:TableVersion)
                    RETURN snapshot.version AS version,
                           snapshot.generation AS generation,
                           snapshot.id AS snapshot_id,
                           count(DISTINCT table) AS tables
                    """
                ).single()
            return {
                "enabled": True,
                "connected": True,
                "backend": "neo4j",
                "database": self.database,
                "snapshot_version": record["version"] if record else None,
                "generation": record["generation"] if record else None,
                "snapshot_id": record["snapshot_id"] if record else None,
                "tables": int(record["tables"] or 0) if record else 0,
            }
        except Exception as exc:
            return {
                "enabled": True,
                "connected": False,
                "backend": "neo4j",
                "error": type(exc).__name__,
            }

    def close(self) -> None:
        self.driver.close()

    def _ensure_constraints(self) -> None:
        statements = [
            "CREATE CONSTRAINT schema_snapshot_id IF NOT EXISTS "
            "FOR (n:SchemaSnapshot) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT document_id IF NOT EXISTS FOR (n:Document) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT document_version_key IF NOT EXISTS "
            "FOR (n:DocumentVersion) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT artifact_version_key IF NOT EXISTS "
            "FOR (n:ArtifactVersion) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT table_version_key IF NOT EXISTS FOR (n:TableVersion) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT column_version_key IF NOT EXISTS "
            "FOR (n:ColumnVersion) REQUIRE n.key IS UNIQUE",
            "CREATE CONSTRAINT semantic_term_key IF NOT EXISTS FOR (n:SemanticTerm) REQUIRE n.key IS UNIQUE",
        ]
        with self.driver.session(database=self.database) as session:
            for statement in statements:
                session.run(statement).consume()

    def _write_artifact_batch(self, snapshot_id: str, rows: list[dict[str, Any]]) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})
                MERGE (document:Document {id: row.doc_id})
                SET document.path = row.source_path
                MERGE (version:DocumentVersion {key: row.document_key})
                SET version.version = row.document_version,
                    version.content_hash = row.content_hash,
                    version.snapshot_id = $snapshot_id
                MERGE (document)-[:HAS_VERSION]->(version)
                MERGE (artifact:ArtifactVersion {key: row.artifact_key})
                SET artifact.artifact_id = row.artifact_id,
                    artifact.kind = row.kind,
                    artifact.title = row.title,
                    artifact.snapshot_id = $snapshot_id
                MERGE (snapshot)-[:HAS_ARTIFACT]->(artifact)
                MERGE (version)-[:PRODUCED]->(artifact)
                """,
                snapshot_id=snapshot_id,
                rows=rows,
            ).consume()

    def _write_table_batch(self, snapshot_id: str, rows: list[dict[str, Any]]) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (snapshot:SchemaSnapshot {id: $snapshot_id})
                MERGE (table:TableVersion {key: row.table_key})
                SET table.name = row.name,
                    table.normalized_name = row.normalized_name,
                    table.database = row.database,
                    table.schema = row.schema,
                    table.description = row.description,
                    table.document_version = row.document_version,
                    table.snapshot_id = $snapshot_id
                MERGE (snapshot)-[:CONTAINS]->(table)
                WITH row, table
                UNWIND row.columns AS column_row
                MERGE (column:ColumnVersion {key: column_row.key})
                SET column.name = column_row.name,
                    column.normalized_name = column_row.normalized_name,
                    column.data_type = column_row.data_type,
                    column.nullable = column_row.nullable,
                    column.description = column_row.description,
                    column.snapshot_id = $snapshot_id
                MERGE (table)-[:HAS_COLUMN]->(column)
                FOREACH (alias IN column_row.aliases |
                    MERGE (term:SemanticTerm {key: alias.key})
                    SET term.value = alias.value, term.normalized = alias.normalized
                    MERGE (term)-[:ALIAS_OF]->(column)
                )
                WITH row, table
                FOREACH (alias IN row.aliases |
                    MERGE (term:SemanticTerm {key: alias.key})
                    SET term.value = alias.value, term.normalized = alias.normalized
                    MERGE (term)-[:ALIAS_OF]->(table)
                )
                """,
                snapshot_id=snapshot_id,
                rows=rows,
            ).consume()

    def _write_about_batch(self, rows: list[dict[str, str]]) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (artifact:ArtifactVersion {key: row.artifact_key})
                MATCH (table:TableVersion {key: row.table_key})
                MERGE (artifact)-[:ABOUT]->(table)
                """,
                rows=rows,
            ).consume()

    def _write_edge_batch(self, rows: list[dict[str, str]]) -> None:
        with self.driver.session(database=self.database) as session:
            session.run(
                """
                UNWIND $rows AS row
                MATCH (child:TableVersion {key: row.child_key})
                MATCH (parent:TableVersion {key: row.parent_key})
                MERGE (child)-[relation:FOREIGN_KEY {key: row.key}]->(parent)
                SET relation.name = row.name,
                    relation.child_column = row.child_column,
                    relation.parent_column = row.parent_column,
                    relation.description = row.description
                """,
                rows=rows,
            ).consume()

    @staticmethod
    def _artifact_rows(catalog: Catalog) -> list[dict[str, Any]]:
        rows = []
        for artifact in catalog.artifacts:
            document_key = f"{artifact.doc_id}|{artifact.document_version}|{artifact.content_hash}"
            rows.append(
                {
                    "artifact_key": f"{catalog.content_hash}|{artifact.id}",
                    "artifact_id": artifact.id,
                    "kind": artifact.kind.value,
                    "title": artifact.title,
                    "source_path": artifact.source_path,
                    "doc_id": artifact.doc_id,
                    "document_key": document_key,
                    "document_version": artifact.document_version,
                    "content_hash": artifact.content_hash,
                    "tables": artifact.tables,
                }
            )
        return rows

    @classmethod
    def _table_rows(
        cls,
        catalog: Catalog,
    ) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
        rows: list[dict[str, Any]] = []
        versions: dict[str, list[str]] = defaultdict(list)
        for artifact in catalog.artifacts:
            if artifact.kind != ArtifactKind.TABLE or not artifact.tables:
                continue
            name = artifact.tables[0]
            database = str(artifact.metadata.get("database", "default"))
            schema = str(artifact.metadata.get("schema", "dbo"))
            key = "|".join(
                [
                    catalog.content_hash,
                    artifact.document_version,
                    database.lower(),
                    schema.lower(),
                    name.lower(),
                ]
            )
            versions[name.lower()].append(key)
            columns = []
            for item in artifact.metadata.get("columns", []):
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                column_name = str(item["name"])
                columns.append(
                    {
                        "key": f"{key}|{column_name.lower()}",
                        "name": column_name,
                        "normalized_name": column_name.lower(),
                        "data_type": str(item.get("data_type", "")),
                        "nullable": item.get("nullable"),
                        "description": str(item.get("description", ""))[:4_000],
                        "aliases": cls._term_rows(
                            list(item.get("aliases", [])),
                            f"column:{key}:{column_name.lower()}",
                        ),
                    }
                )
            aliases = list(artifact.metadata.get("aliases", []))
            aliases.extend([name, artifact.title])
            rows.append(
                {
                    "table_key": key,
                    "name": name,
                    "normalized_name": name.lower(),
                    "database": database,
                    "schema": schema,
                    "description": str(artifact.metadata.get("description", ""))[:8_000],
                    "document_version": artifact.document_version,
                    "columns": columns,
                    "aliases": cls._term_rows(aliases, f"table:{key}"),
                }
            )
        referenced_tables = {edge.child_table for edge in catalog.foreign_keys} | {
            edge.parent_table for edge in catalog.foreign_keys
        }
        for name in sorted(referenced_tables, key=str.lower):
            if name.lower() in versions:
                continue
            key = "|".join(
                [
                    catalog.content_hash,
                    catalog.snapshot_version,
                    "default",
                    "dbo",
                    name.lower(),
                ]
            )
            versions[name.lower()].append(key)
            rows.append(
                {
                    "table_key": key,
                    "name": name,
                    "normalized_name": name.lower(),
                    "database": "default",
                    "schema": "dbo",
                    "description": "Table referenced by an indexed foreign key",
                    "document_version": catalog.snapshot_version,
                    "columns": [],
                    "aliases": cls._term_rows([name], f"table:{key}"),
                }
            )
        return rows, dict(versions)

    @staticmethod
    def _edge_rows(
        catalog: Catalog,
        table_versions: dict[str, list[str]],
    ) -> list[dict[str, str]]:
        rows = []
        for edge in catalog.foreign_keys:
            children = table_versions.get(edge.child_table.lower(), [])
            parents = table_versions.get(edge.parent_table.lower(), [])
            if edge.document_version != "unversioned":
                versioned_children = [
                    key for key in children if key.split("|", 2)[1] == edge.document_version
                ]
                versioned_parents = [key for key in parents if key.split("|", 2)[1] == edge.document_version]
                children = versioned_children or children
                parents = versioned_parents or parents
            for child_key in children:
                child_version = child_key.split("|", 2)[1]
                compatible = [key for key in parents if key.split("|", 2)[1] == child_version]
                for parent_key in compatible or parents[:1]:
                    rows.append(
                        {
                            "key": (f"{catalog.content_hash}|{edge.name}|{child_key}|{parent_key}"),
                            "name": edge.name,
                            "child_key": child_key,
                            "parent_key": parent_key,
                            "child_column": edge.child_column,
                            "parent_column": edge.parent_column,
                            "description": edge.description,
                        }
                    )
        return rows

    @staticmethod
    def _term_rows(values: list[object], owner: str) -> list[dict[str, str]]:
        rows = []
        seen: set[str] = set()
        for value in values:
            text = str(value).strip()
            normalized = normalize_persian(text).lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rows.append(
                {
                    "key": f"{owner}|{normalized}",
                    "value": text[:500],
                    "normalized": normalized[:500],
                }
            )
        return rows

    @staticmethod
    def query_terms(question: str) -> list[str]:
        return tokenize(normalize_persian(question))

    @staticmethod
    def _batches(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
        return [rows[index : index + size] for index in range(0, len(rows), size)]
