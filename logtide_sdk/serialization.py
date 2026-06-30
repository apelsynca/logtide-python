import traceback
from typing import Any


def serialize_exception(exc: BaseException) -> dict[str, Any]:
    """
    Serialize an exception into a structured format.

    Returns a dict with keys: type, message, language, stacktrace, raw.
    stacktrace is a list of frame dicts: {file, function, line}.
    Chained exceptions (exc.__cause__) are serialized recursively as 'cause'.
    """
    frames: list[dict[str, Any]] = []
    tb = exc.__traceback__
    while tb is not None:
        frame = tb.tb_frame
        frames.append(
            {
                "file": frame.f_code.co_filename,
                "function": frame.f_code.co_name,
                "line": tb.tb_lineno,
            }
        )
        tb = tb.tb_next

    result: dict[str, Any] = {
        "type": type(exc).__name__,
        "message": str(exc),
        "language": "python",
        "stacktrace": frames,
        "raw": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
    }

    if exc.__cause__ is not None:
        result["cause"] = serialize_exception(exc.__cause__)

    return result
