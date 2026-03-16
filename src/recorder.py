"""Raw data recorder — batched JSONL output for replay and debugging."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from config import VERSION, Config
from models import InstantSample

logger = logging.getLogger(__name__)


class Recorder:
    """Writes InstantSamples to JSONL files in batches."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._output_dir = Path(config.data_dir) / "recordings"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._batch: list[dict] = []
        self._current_file: Path | None = None
        self._file_handle = None
        self._samples_written = 0

    def record(self, sample: InstantSample) -> None:
        """Buffer a sample. Flushes when batch is full."""
        self._batch.append(asdict(sample))
        if len(self._batch) >= self._config.recorder_batch_size:
            self.flush()

    def flush(self) -> None:
        """Write buffered samples to disk."""
        if not self._batch:
            return

        # Rotate file daily
        date_str = time.strftime("%Y-%m-%d")
        target_file = self._output_dir / f"samples_{date_str}.jsonl"

        if target_file != self._current_file:
            self._close_file()
            self._current_file = target_file

        try:
            if self._file_handle is None:
                assert self._current_file is not None
                self._file_handle = open(self._current_file, "a")  # noqa: SIM115

            for record in self._batch:
                record["version"] = VERSION
                self._file_handle.write(json.dumps(record, default=str) + "\n")
            self._file_handle.flush()
            self._samples_written += len(self._batch)
        except OSError as exc:
            logger.error("Failed to write recording: %s", exc)
        finally:
            self._batch.clear()

    def close(self) -> None:
        """Flush remaining data and close file."""
        self.flush()
        self._close_file()
        logger.info("Recorder closed — %d samples written total", self._samples_written)

    def _close_file(self) -> None:
        if self._file_handle is not None:
            import contextlib

            with contextlib.suppress(OSError):
                self._file_handle.close()
            self._file_handle = None
