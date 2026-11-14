import logging
import sys

class MillisecondFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        t = logging.Formatter.formatTime(self, record, "%H:%M:%S")
        return f"{t}.{int(record.msecs):03d}"

def get_logger(name: str = "MSIM-CORE") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(MillisecondFormatter("[%(asctime)s] [%(name)-16s] [%(levelname)-5s] %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
    return logger
