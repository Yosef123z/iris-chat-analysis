"""Local filesystem persistence for business knowledge indexes."""

from __future__ import annotations

import os
import pickle
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

import structlog

from app.services.business_knowledge_service import (
    BusinessVectorIndex,
    StoredBusinessKnowledge,
)

logger = structlog.get_logger("app.services.business_kb_persistence")

_FORMAT_VERSION = 1


@dataclass(frozen=True)
class PersistedBusinessKnowledge:
    format_version: int
    stored: StoredBusinessKnowledge
    index: BusinessVectorIndex


class BusinessKBPersistence:
    """Persist and restore business KB artifacts from local pickle files."""

    def __init__(self, storage_dir: str | Path) -> None:
        self.storage_dir = Path(storage_dir)

    def save(
        self,
        stored: StoredBusinessKnowledge,
        index: BusinessVectorIndex,
    ) -> Path:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        artifact = PersistedBusinessKnowledge(
            format_version=_FORMAT_VERSION,
            stored=stored,
            index=index,
        )
        target_path = self.path_for_business(stored.business_id)

        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                delete=False,
                dir=self.storage_dir,
                prefix=f".{target_path.stem}.",
                suffix=".tmp",
            ) as temp_file:
                temp_name = temp_file.name
                pickle.dump(artifact, temp_file, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(temp_name, target_path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

        return target_path

    def load_all(self) -> Iterator[tuple[StoredBusinessKnowledge, BusinessVectorIndex]]:
        if not self.storage_dir.exists():
            return

        for path in sorted(self.storage_dir.glob("*.pkl")):
            try:
                artifact = self._load_one(path)
            except Exception as exc:
                logger.warning(
                    "business_kb_persistence_load_failed",
                    path=str(path),
                    error=str(exc),
                )
                continue
            yield artifact.stored, artifact.index

    def path_for_business(self, business_id: str) -> Path:
        return self.storage_dir / f"{quote(business_id, safe='')}.pkl"

    @staticmethod
    def _load_one(path: Path) -> PersistedBusinessKnowledge:
        with path.open("rb") as file:
            artifact = pickle.load(file)

        if not isinstance(artifact, PersistedBusinessKnowledge):
            raise ValueError("Unexpected persisted KB artifact type")
        if artifact.format_version != _FORMAT_VERSION:
            raise ValueError("Unsupported persisted KB artifact version")
        if not isinstance(artifact.stored, StoredBusinessKnowledge):
            raise ValueError("Persisted KB stored payload is invalid")
        if not isinstance(artifact.index, BusinessVectorIndex):
            raise ValueError("Persisted KB vector index is invalid")
        if artifact.stored.business_id != artifact.index.business_id:
            raise ValueError("Persisted KB business ids do not match")

        return artifact
