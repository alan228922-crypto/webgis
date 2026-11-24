"""Opt-in settings for the worksite tests that require two SQLite connections."""

from os import environ
from pathlib import Path

from config.settings import *

DATABASES["default"]["TEST"] = {"NAME": Path(environ["WORKSITE_TEST_SQLITE_PATH"])}
