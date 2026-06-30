"""Python standard-library logging integration for LogTide SDK."""

import logging

from logtide_sdk.client import LogTideClient
from logtide_sdk.enums import LogLevel
from logtide_sdk.models import LogEntry
from logtide_sdk.serialization import serialize_exception


class LogTideHandler(logging.Handler):
    """
    A standard logging.Handler that forwards log records to LogTideClient.

    Drop-in integration for applications already using Python's logging module.

    Example:
        import logging
        from logtide_sdk import LogTideClient, ClientOptions, LogTideHandler

        client = LogTideClient(ClientOptions(api_url=..., api_key=...))
        handler = LogTideHandler(client=client, service='my-app')
        handler.setLevel(logging.INFO)

        logging.getLogger().addHandler(handler)

        # Now standard logging calls are forwarded to LogTide:
        logging.info('Server started')
        logging.error('Unhandled exception', exc_info=True)

    Exception info from exc_info=True is automatically serialized into a
    structured 'exception' metadata key.
    """

    def __init__(
        self,
        client: LogTideClient,
        service: str,
        level: int = logging.NOTSET,
    ) -> None:
        """
        Initialize the handler.

        Args:
            client: An active LogTideClient instance
            service: Service name attached to every forwarded log entry
            level: Minimum logging level (default: NOTSET — accept all records)
        """
        super().__init__(level)
        self.client = client
        self.service = service

    def emit(self, record: logging.LogRecord) -> None:
        """
        Forward a LogRecord to LogTide.

        Called by the logging framework for each matching log record. Never
        raises — falls back to logging.Handler.handleError on exceptions.
        """
        try:
            logtide_level = self._map_level(record.levelno)

            metadata = {
                "logger": record.name,
                "module": record.module,
                "funcName": record.funcName,
                "lineno": record.lineno,
                "pathname": record.pathname,
            }

            # Serialize attached exception info
            if record.exc_info and record.exc_info[1] is not None:
                metadata["exception"] = serialize_exception(record.exc_info[1])

            self.client.log(
                LogEntry(
                    service=self.service,
                    level=logtide_level,
                    message=self.format(record),
                    metadata=metadata,
                )
            )
        except Exception:
            self.handleError(record)  # stdlib fallback — does not re-raise

    def _map_level(self, levelno: int) -> LogLevel:
        """Map a stdlib logging level integer to a LogTide LogLevel."""
        if levelno >= logging.CRITICAL:
            return LogLevel.CRITICAL
        if levelno >= logging.ERROR:
            return LogLevel.ERROR
        if levelno >= logging.WARNING:
            return LogLevel.WARN
        if levelno >= logging.INFO:
            return LogLevel.INFO
        return LogLevel.DEBUG
