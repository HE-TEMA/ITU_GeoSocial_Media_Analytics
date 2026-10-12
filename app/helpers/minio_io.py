"""
Author: Andreas Kramer
This script contains utility functions for uploading and downloading files to and from a MinIO bucket.
These functions facilitate the interaction with MinIO by providing simplified methods to handle
file uploads and downloads, including error handling for various potential issues.

Functions:
    - uploadToMinio: Uploads a file to a specified MinIO bucket.
    - downloadFromMinio: Downloads a file from a specified MinIO bucket.

Usage:
    These functions can be imported and used within other parts of the application where MinIO
    file storage and retrieval operations are required.

Dependencies:
    - minio: MinIO Python SDK for interacting with MinIO server.
    - os: Used for interacting with the operating system.
"""

from minio import Minio
from minio.error import S3Error
import os
import datetime
from config import Config
logger = Config.LOGGER


# uploads file to MinIO bucket
def uploadToMinio(
    endpoint, access_key, secret_key, bucket_name, file_path, object_name, logger, bm_id
):
    # Configure the MinIO client
    client = Minio(
        endpoint,  # MinIO server endpoint with port
        access_key=access_key,  # access key
        secret_key=secret_key,  # secret key
        secure=True,  # Set to True for HTTPS, False for HTTP
    )
    try:
        #  file_path = os.path.join(os.path.dirname(__file__), file_path)
        # Check if the  file exists and has correct permissions
        upload_file_path = os.path.normpath(file_path)
        if not os.path.exists(upload_file_path):
            raise FileNotFoundError(f"File {upload_file_path} not found")
        if not os.access(upload_file_path, os.R_OK):
            raise PermissionError(f"Cannot read file {upload_file_path}")

        logger.info(f"File {file_path} found and is readable")

        # Check if the bucket already exists
        found = client.bucket_exists(bucket_name)
        if not found:
            client.make_bucket(bucket_name)
            logger.info(f"Bucket '{bucket_name}' created successfully")
            # print(f"Bucket '{bucket_name}' created successfully")
        else:
            logger.info(f"Bucket '{bucket_name}' already exists")
            # print(f"Bucket '{bucket_name}' already exists")

        # Upload the  file to the bucket
        unique_name = addTimeStamp(object_name)
        logger.debug(f"Adding time-stamp: {unique_name}")
        bucket_path = f'tfa11/{bm_id}/{unique_name}'
        result = client.fput_object(bucket_name, bucket_path, file_path)
        logger.info(f"File: '{bucket_path}' Uploaded successfully")
        logger.debug(f"ETag: {result.etag}, Version ID: {result.version_id}")
        # Return True indicating the upload was successful
        return (True, bucket_path)

    except FileNotFoundError as e:
        logger.warning(f"File not found error: {e}. Did you run main.py first?")
    except PermissionError as e:
        logger.warning(f"Permission error: {e}")
    except S3Error as e:
        logger.warning(f"S3 error: {e}")
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")

    # Return False if any exception was raised
    return (False, None)


def addTimeStamp(object_name):
    if not isinstance(object_name, str):
        raise TypeError("The object_name must be a string")

    # Check for valid extension and determine the extension length
    if object_name.endswith(".geojson"):
        ext = ".geojson"
        base_name = object_name[:-len(ext)]
    elif object_name.endswith(".json"):
        ext = ".json"
        base_name = object_name[:-len(ext)]
    else:
        raise ValueError("The object name must end with .geojson or .json")

    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    new_name = f"{base_name}_{timestamp}{ext}"
    return new_name


def displayInfo(file_name, endpoint, bucket_name, minio_URL):
    # Create JSON entity to display uploaded file info
    return {
        "name": file_name,
        "endpoint": endpoint,
        "bucket": bucket_name,
        "minio-URL": minio_URL,
    }


def downloadFromMinio(
    endpoint, access_key, secret_key, bucket_name, object_name, download_path, logger
):
    # Configure the MinIO client
    client = Minio(
        endpoint,  # MinIO server endpoint with port
        access_key=access_key,  # Replace with your access key
        secret_key=secret_key,  # Replace with your secret key
        secure=True,  # Set to True for HTTPS, False for HTTP
    )
    try:
        # Check if the object exists
        found = client.stat_object(bucket_name, object_name)
        if found:
            logger.info(f"Object '{object_name}' found in bucket '{bucket_name}'")
            # Download the object
            client.fget_object(bucket_name, object_name, download_path)
            logger.info(
                f"File '{object_name}' downloaded successfully to '{download_path}'"
            )
            # Return True indicating the download was successful
            return True
        else:
            return False

    except S3Error as e:
        logger.warning(f"S3 error: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")
        return False


def deleteFromMinio(endpoint, access_key, secret_key, bucket_name, object_name, logger):
    # Configure the MinIO client
    client = Minio(
        endpoint,  # MinIO server endpoint with port
        access_key=access_key,  # Replace with your access key
        secret_key=secret_key,  # Replace with your secret key
        secure=True,  # Set to True for HTTPS, False for HTTP
    )
    try:
        # Check if the object exists
        found = client.stat_object(bucket_name, object_name)
        if found:
            # Delete the object
            logger.info(f"Object '{object_name}' found in bucket '{bucket_name}'")
            client.remove_object(bucket_name, object_name)
            # Return True indicating the deletion was successful
            return True
        else:
            return False
    except S3Error as e:
        logger.warning(f"S3 error: {e}")
        return False
    except Exception as e:
        logger.warning(f"Unexpected error: {e}")
        return False
