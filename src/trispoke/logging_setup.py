from loguru import logger
import sys
from pathlib import Path

# Create logs directory if it doesn't exist
logs_dir = Path("logs")
logs_dir.mkdir(exist_ok=True)

# Configure logger to write to stderr
logger.add(sys.stderr, level="INFO")

# Configure logger to write to rotating file
logger.add(
    "logs/trispoke.log",
    rotation="10 MB",
    retention="7 days",
    level="DEBUG"
)