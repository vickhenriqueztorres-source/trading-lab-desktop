"""A python wrapper for IQ Option API."""

import logging

def _prepare_logging():
    """Prepare logger for module IQ Option API."""
    logger = logging.getLogger(__name__)
    #https://github.com/Lu-Yi-Hsun/iqoptionapi_private/issues/1
    #try to fix this problem
    #logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.NullHandler())
    logger.propagate = False  # P03: raw upstream records must never reach app logs.

    websocket_logger = logging.getLogger("websocket")
    websocket_logger.setLevel(logging.CRITICAL)
    websocket_logger.addHandler(logging.NullHandler())
    websocket_logger.propagate = False

_prepare_logging()
