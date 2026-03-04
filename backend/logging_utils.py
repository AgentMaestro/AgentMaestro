import logging

from django.conf import settings


def get_app_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    level = logging.DEBUG if getattr(settings, "DEBUG", False) else logging.INFO
    logger.setLevel(level)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.setLevel(level)
        logger.addHandler(handler)
        logger.propagate = False
    else:
        for handler in logger.handlers:
            handler.setLevel(level)
    return logger
