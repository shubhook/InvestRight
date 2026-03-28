# Logging system

import logging

def setup_logger(name):
    """Set up and return a logger with the given name.

    Guards against adding duplicate handlers when the same module is
    imported multiple times (e.g. during testing or scheduler restarts).
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        # Already configured — return as-is to avoid duplicate log lines
        return logger
    logger.setLevel(logging.INFO)
    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger