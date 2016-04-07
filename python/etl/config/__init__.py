from collections import defaultdict
from functools import lru_cache
import logging
import logging.config
import sys

import pkg_resources
import simplejson as json
import yaml


def configure_logging():
    """
    Setup logging to go to console and application file, etl.log

    Suppresses noisy logging from boto3 and botocore.
    """
    config_file = pkg_resources.resource_filename(__name__, "logging.cfg")
    logging.config.fileConfig(config_file)
    logging.captureWarnings(True)
    logging.getLogger(__name__).info("Appending to log 'etl.log' for: %s", sys.argv[0])


def load_settings(config_file):
    """
    Load settings from defaults and config file.
    """
    settings = defaultdict(dict)
    logger = logging.getLogger(__name__)
    default_file = pkg_resources.resource_filename(__name__, "defaults.yaml")
    for filename in (default_file, config_file):
        with open(filename) as f:
            logger.info("Loading configuration file '%s'", filename)
            new_settings = yaml.safe_load(f)
            for key in new_settings:
                # Try to update only update-able settings
                if not (key in settings and isinstance(settings[key], dict)):
                    settings[key] = new_settings[key]
                else:
                    settings[key].update(new_settings[key])

    class Accessor(object):
        def __init__(self, data):
            self._data = data

        def __call__(self, *argv):
            # TODO Add better error handling
            value = self._data
            for arg in argv:
                value = value[arg]
            return value

    return Accessor(settings)


@lru_cache()
def load_json(filename):
    return json.loads(pkg_resources.resource_string(__name__, filename))