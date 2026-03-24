from .base import *

DEBUG = True

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "()": "logging_utils.ScrubbingFormatter",
            "format": "%(asctime)s %(levelname)-5s %(name)s %(message)s",
            "datefmt": "%H:%M:%S",
        },
        "simple": {
            "()": "logging_utils.ScrubbingFormatter",
            "format": "%(asctime)s %(levelname)-5s %(name)s: %(message)s",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
            "level": "DEBUG",
        },
    },
    "loggers": {
        "": {
            "handlers": ["console"],
            "level": "DEBUG",
        },
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "llm": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "tools": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "agents": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
        "llm.services.providers.openai_ws": {
            "handlers": ["console"],
            "level": "WARNING",  # or INFO if you want more
            "propagate": False,
        },
        "websockets.client": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
