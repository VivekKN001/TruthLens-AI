"""
Logging Configuration - Structured logging for TruthLens AI
"""

import logging
import sys
from typing import Optional


class ColorFormatter(logging.Formatter):
    """Custom formatter with colors for terminal output"""

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    ICONS = {
        "DEBUG": "🔍",
        "INFO": "✓",
        "WARNING": "⚠️",
        "ERROR": "❌",
        "CRITICAL": "🚨",
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        icon = self.ICONS.get(record.levelname, "")
        reset = self.RESET

        # Format the message
        record.msg = f"{color}{icon} {record.msg}{reset}"
        return super().format(record)


def get_logger(name: str, level: Optional[str] = None) -> logging.Logger:
    """
    Get a configured logger instance.

    Args:
        name: Logger name (typically __name__ of the module)
        level: Optional log level override

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)

    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger

    # Set level
    log_level = level or "INFO"
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Console handler with color formatting
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(ColorFormatter("%(message)s"))
    logger.addHandler(console_handler)

    # Prevent propagation to root logger
    logger.propagate = False

    return logger


class AgentLogger:
    """
    Specialized logger for agent operations.
    Provides consistent formatting for agent actions.
    """

    def __init__(self, agent_name: str, icon: str = "🤖"):
        self.logger = get_logger(f"agent.{agent_name}")
        self.agent_name = agent_name.upper()
        self.icon = icon

    def _format_message(self, message: str) -> str:
        return f"{self.icon} {self.agent_name}: {message}"

    def info(self, message: str) -> None:
        self.logger.info(self._format_message(message))

    def debug(self, message: str) -> None:
        self.logger.debug(self._format_message(message))

    def warning(self, message: str) -> None:
        self.logger.warning(self._format_message(message))

    def error(self, message: str) -> None:
        self.logger.error(self._format_message(message))

    def step(self, message: str) -> None:
        """Log a step in the agent's process"""
        self.logger.info(f"  {message}")

    def result(self, metric: str, value: any) -> None:
        """Log a result metric"""
        self.logger.info(f"  ✓ {metric}: {value}")
