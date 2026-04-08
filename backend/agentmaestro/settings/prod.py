from .base import *

DEBUG = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "()": "logging_utils.ScrubbingFormatter",
            "format": "%(asctime)s %(levelname)-5s %(name)s %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "INFO",
        },
        "errors": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
            "level": "WARNING",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "agents": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "llm": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "tools": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "llm.services.providers.openai_ws": {
            "handlers": ["console"],
            "level": "INFO",  # or WARNING if you want even less
            "propagate": False,
        },
        "websockets.client": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpx": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
        "httpcore": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
