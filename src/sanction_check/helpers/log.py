import logging

from sanction_check.settings import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sanction_check")
log.setLevel(get_settings().log_level)
