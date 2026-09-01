from __future__ import annotations

from datetime import UTC, datetime

from cherryfin.intelligence.models import (
    IdentifierScheme,
    Instrument,
    InstrumentIdentifier,
)
from cherryfin.intelligence.store_common import (
    IdentifierConflictError,
    IntegrityConflictError,
    RecordNotFoundError,
    iso,
    json_dumps,
)


class InstrumentStoreMixin:
    def add_instrument(self, instrument: Instrument) -> tuple[Instrument, bool]:
        instrument_id = str(instrument.instrument_id)
        payload = json_dumps(instrument)
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT payload_json FROM instruments WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
            if existing:
                if existing["payload_json"] != payload:
                    raise IntegrityConflictError(
                        f"instrument {instrument_id} already exists with different content"
                    )
                return instrument, False

            for identifier in instrument.identifiers:
                collision = self._connection.execute(
                    """
                    SELECT instrument_id FROM instrument_identifiers
                    WHERE scheme = ? AND normalized_value = ? AND venue = ?
                    """,
                    identifier.key,
                ).fetchone()
                if collision and collision["instrument_id"] != instrument_id:
                    raise IdentifierConflictError(
                        f"identifier {identifier.key} already belongs to another instrument"
                    )

            self._connection.execute(
                "INSERT INTO instruments(instrument_id, payload_json, created_at) VALUES (?, ?, ?)",
                (instrument_id, payload, iso(datetime.now(UTC))),
            )
            self._connection.executemany(
                """
                INSERT INTO instrument_identifiers(
                    scheme, normalized_value, venue, instrument_id
                ) VALUES (?, ?, ?, ?)
                """,
                [(*identifier.key, instrument_id) for identifier in instrument.identifiers],
            )
        return instrument, True

    def get_instrument(self, instrument_id: str) -> Instrument:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM instruments WHERE instrument_id = ?",
                (instrument_id,),
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"instrument {instrument_id} was not found")
        return Instrument.model_validate_json(row["payload_json"])

    def resolve_instrument(
        self,
        *,
        scheme: IdentifierScheme,
        value: str,
        venue: str | None = None,
    ) -> Instrument:
        normalized = InstrumentIdentifier(scheme=scheme, value=value, venue=venue)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT i.payload_json
                FROM instrument_identifiers AS ii
                JOIN instruments AS i ON i.instrument_id = ii.instrument_id
                WHERE ii.scheme = ? AND ii.normalized_value = ? AND ii.venue = ?
                """,
                normalized.key,
            ).fetchone()
        if row is None:
            raise RecordNotFoundError(f"instrument identifier {normalized.key} was not found")
        return Instrument.model_validate_json(row["payload_json"])
