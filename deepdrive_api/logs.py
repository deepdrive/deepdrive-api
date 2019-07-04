import os
import logging
from logging.handlers import RotatingFileHandler
import sys

log_format = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
log_level = logging.INFO

disabled = False


def get_log(namespace):
    global disabled

    if disabled:
        logging.disable(logging.CRITICAL)
        ret = logging.getLogger(namespace)
    else:
        ret = logging.getLogger(namespace)
        ret.setLevel(log_level)
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(log_format)
        ret.addHandler(ch)
        ret.propagate = False
    return ret


