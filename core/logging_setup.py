# core/logging_setup.py — Centralized logging configuration

import logging
import logging.handlers
from pathlib import Path
import config

# Ensure logs directory exists
config.LOGS_DIR.mkdir(exist_ok=True)

def setup_logging(name: str = "printshop_manager") -> logging.Logger:
    """
    Configure centralized logging with rotation.
    
    Args:
        name: Logger name (typically module name)
    
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Avoid duplicate handlers
    if logger.hasHandlers():
        return logger
    
    logger.setLevel(config.LOG_LEVEL)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(config.LOG_LEVEL)
    console_formatter = logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler with rotation
    log_file = config.LOGS_DIR / config.LOG_FILE_NAME
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT
    )
    file_handler.setLevel(config.LOG_LEVEL)
    file_formatter = logging.Formatter(config.LOG_FORMAT, config.LOG_DATE_FORMAT)
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)
    
    return logger


# Root logger for the application
LOGGER = setup_logging("printshop_manager")
