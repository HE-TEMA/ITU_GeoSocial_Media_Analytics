"""
Author: Andreas Kramer
This script handles the configuration for a Flask application, including loading environment variables
from a .env file. It defines different configurations for development, testing, and production environments.

Classes:
    - Config: Base configuration class with default settings.
    - DevelopmentConfig: Configuration settings for development environment.
    - TestingConfig: Configuration settings for testing environment.
    - ProductionConfig: Configuration settings for production environment.

Usage:
    The appropriate configuration class can be selected based on the environment. The configuration settings
    include Flask-specific settings, MinIO settings, Orion-LD settings, and other application-specific settings.

Dependencies:
    - os: Used for interacting with the operating system and environment variables.
"""
import os
import sys
import logging
import threading
import multiprocessing
from logging.handlers import RotatingFileHandler
# flake8: noqa

# from dotenv import load_dotenv
# dotenv_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
# load_dotenv(dotenv_path)  # Load environment variables from a .env file


class StreamToLogger:
    """
    Redirects writes to stdout to a logger instance.
    """
    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ''

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            self.logger.log(self.log_level, line)

    def flush(self):
        pass  # Needed for file-like behavior


class Config:
    # Flask settings
    FLASK_SECRET_KEY = (
        os.environ.get("FLASK_SECRET_KEY") or "example_default_secret_key"
    )

    DOWNLOAD_FOLDER     = os.environ.get("DOWNLOAD_FOLDER")

    # Configuration for MinIO, specific values in Dockerfile
    ACCESS_KEY          = os.environ.get("MINIO_ACCESS_KEY")
    SECRET_KEY          = os.environ.get("MINIO_SECRET_KEY")
    BUCKET_NAME         = os.environ.get("MINIO_BUCKET_NAME")
    ENDPOINT            = os.environ.get("MINIO_ENDPOINT")

    # Configurations for Port
    PORT                = os.environ.get("PORT")

    # Configuration for Orion-LD, specific values in .env file
    ORION_LD_ENPOINT    = os.environ.get("ORION_LD_ENDPOINT")
    ORION_BASE_URL      = os.environ.get("ORION_BASE_URL")

    # Configuration for Huggingface models
    EMOTION_MODEL       = os.environ.get("EMOTION_MODEL")
    EMBEDDING_MODEL     = os.environ.get("EMBEDDING_MODEL")

    # Configuration of cloud storage with input data
    DATA_STORAGE        = os.environ.get("DATA_STORAGE")

    # Configuration for Database
    DB_CONN_STRING      = os.getenv('DB_CONN_STRING', None)

    # Timezone info
    TIME_ZONE = os.getenv('TZ', 'Europe/Vienna')

    # Configuration for allowed file extensions
    ALLOWED_EXTENSIONS      = {"json", "geojson"}  # To allow only json and geojson files

    # Configuration for file upload and download
    EXPORT_FILE_PATH_1      = "./data/export_data/single_posts_results.geojson"
    EXPORT_OBJECT_NAME_1    = "single_posts_results.geojson"

    EXPORT_FILE_PATH_2      = "./data/export_data/hotspot_results.geojson"
    EXPORT_OBJECT_NAME_2    = "hotspot_results.geojson"

    DOWNLOAD_FOLDER         = "./downloads"

    SWAGGER_URL             = "/apidocs"
    API_URL                 = "/static/swagger.json"

    DESCRIPTION_SINGLE_POST = "Geospatial data of individual social media posts with integrated and geocoded topic labels, keywords, and emotion analysis."
    DESCRIPTION_HOTSPOT     = "Hexagonal binning hotspot analysis of social media activity clusters related to specific keywords."

    TITLE_1                 = "Single Geo-Social Media Posts"
    TITLE_2                 = "Geo-Social Media Hotspots"

    ENTITY_TYPE_1           = "SinglePostResult"
    ENTITY_TYPE_2           = "HotspotResult"

    UPDATE_INTERVAL_MIN     = int(os.getenv('UPDATE_INTERVAL_MIN', 30))
    AOI_BUFFER_M            = int(os.getenv('AOI_BUFFER_M', 50000))  # buffer for the alert AOI

    # Configuration for Main.py
    INPUT_PATH              = "./data/input_data"
    TEMP_PATH               = "./temp"
    OUTPUT_PATH             = "./data/export_data"

    ## Configuration for social media paths
    TWITTER                 = "twitter_data.csv"
    TELEGRAM                =  "telegram_data.csv"
    MASTODON                =  "mastodon_data.csv"
    TIKTOK                  =  "tiktok_data.csv"
    REDDIT                  =  "reddit_data.csv"
    YOUTUBE                 =  "youtube_data.csv"
    LIVE_DATA               =  "live_data.csv"  # data that is collected ad-hoc

    ## Configuration Settings for Analysis
    DROP_NO_GEOMETRY = os.getenv("DROP_NO_GEOMETRY", True)
    EMOTION_ANALYSIS_PROBABILITY_THRESHOLD = os.getenv(
        "EMOTION_ANALYSIS_PROBABILITY_THRESHOLD", 0.5
    )
    CRS = os.getenv("CRS", 4326)
    H3_RESOLUTION = os.getenv("H3_RESOLUTION", 6)
    HOTSPOTS_DATE_RANGE = os.getenv("HOTSPOTS_DATE_RANGE", 1)
    KEYWORDS = os.getenv("KEYWORDS", 'flood|fire|überschwemmung|flut|feuer|brand|πλημμύρ|πυρκαγι|καταιγίδ|alluvione|incendio|inundación|tulva|tulipalo|maastopalo')
    RELATEDNESS_METHOD = os.getenv("RELATEDNESS_METHOD", "hannybal/disaster-twitter-xlm-roberta-al")

    DEBUG = False
    TESTING = False
    LIVE_MODE = (os.getenv('LIVE_MODE', "True") == "True")  # buffer for the alert AOI, mutable list container

    # Log path configuration
    LOG_PATH = os.getenv("LOG_PATH", "./logs/app.log")
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    # Reconfigure logger with file rotation
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(),  # Logs to console
            RotatingFileHandler(
                LOG_PATH, 
                maxBytes=5 * 1024 * 1024,  # Max file size: 5 MB
            )
        ]
    )
    LOGGER = logging.getLogger()

    # Concurrency management
    LIVE_LOCK = threading.Lock()
    WORKFLOW_LOCK = threading.Lock()
    WORKFLOW_PROCESS: multiprocessing.Process | None = None


class DevelopmentConfig(Config):
    DEBUG = True
    FLASK_ENV = "development"


class TestingConfig(Config):
    TESTING = True
    # MINIO_BUCKET_NAME = 'test_bucket'  # overriding a specific setting for testing (example)


class ProductionConfig(Config):
    FLASK_ENV = "production"
    DEBUG = False


config = {
    "development":  DevelopmentConfig,
    "testing":      TestingConfig,
    "production":   ProductionConfig,
    "default":      DevelopmentConfig,
}