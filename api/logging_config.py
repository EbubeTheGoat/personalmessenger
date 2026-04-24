import logging
import json
import os
from datetime import datetime


class JsonFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
        }

        # Include ALL extra fields dynamically
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno",
                "pathname", "filename", "module", "exc_info",
                "exc_text", "stack_info", "lineno", "funcName",
                "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process"
            ):
                log[key] = value

        return json.dumps(log)


def get_logger(name: str):
    logger = logging.getLogger(name)

    if logger.handlers:  # prevent duplicate handlers
        return logger


    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    logger.propagate = False

    return logger