"""
Author: Andreas Kramer
This script sets up a Flask web application with endpoints for uploading files to MinIO, creating NGSI-LD entities,
retrieving entity information, and downloading files from MinIO. It configures the application, handles file uploads,
interacts with MinIO for storage, and communicates with an Orion-LD instance.

Dependencies:
    - flask: For creating the web application and handling requests.
    - werkzeug: For securely handling file uploads.
    - os: For interacting with the operating system and environment variables.
    - helpers.config: Custom configuration module for loading application settings.
    - helpers: Custom helper functions for interacting with MinIO and Orion-LD.

Configuration:
    - The application uses environment variables for configuration, loaded via the `Config` class from `helpers.config`.
    - Upload and download folders are configured within the script.
    - Secret key and other credentials are securely handled via environment variables.

Usage:
    Run the script to start the Flask application. The application will be available at the specified host and port.
"""

import os
import torch
import time
import requests
import multiprocessing
import datetime
import pandas as pd
import geopandas as gpd
from sqlalchemy.orm import sessionmaker
from flask import Flask, request, redirect, jsonify, abort, url_for, send_file
from flask_cors import CORS
from config import Config
from flasgger import Swagger
from tasks import kill_process_by_pid, run_scheduled_alerts, trigger_workflow_from_db, start_photo_processing_task  # noqa
from shapely import Polygon
from sqlalchemy import text
from helpers.minio_io import uploadToMinio, displayInfo, downloadFromMinio, deleteFromMinio
from helpers.entity_creation import createMinioUrl, create_upload_entity, UploadEntity, create_update_data, parseMinioUrl
from helpers.orion_io import createOrionEntity, updateOrionEntity, getOrionEntityById, deleteOrionEntity
from helpers.utils import download_input_data, read_last_lines
from helpers.db import get_engine, init_db, Alert, set_live_mode, get_live_mode
from workflow import insert_input_data_into_db, start_background_geoparsing
from tasks import (start_disaster_monitoring_loop_separate,  # noqa
                   handle_social_media_captions_entity,
                   extract_and_upload_photo_urls,  # noqa
                   handle_external_tema_command)
from apscheduler.schedulers.background import BackgroundScheduler

# allow multiprocessing with torch
if torch.multiprocessing.get_start_method(allow_none=True) is None:
    torch.multiprocessing.set_start_method('spawn')


# ------------------------------------------------------------------------------
# Application Setup
# ------------------------------------------------------------------------------

# Creating the flask-app
app = Flask(__name__)
app.config["SECRET_KEY"] = Config.SECRET_KEY
app.config["PROPAGATE_EXCEPTIONS"] = True
CORS(app)
app.config["APPLICATION_ROOT"] = os.environ.get('BASE_PATH')

# print(os.environ.get('BASE_PATH'))

# initializing swagger
swagger = Swagger(app, template_file="static/openapi.json")

# Configuration settings
logger              = Config.LOGGER  # noqa
access_key          = Config.ACCESS_KEY  # noqa
secret_key          = Config.SECRET_KEY  # noqa
endpoint            = Config.ENDPOINT  # noqa
bucket_name         = Config.BUCKET_NAME  # noqa
orion_ld_endpoint   = Config.ORION_LD_ENPOINT  # noqa
orion_base_url      = Config.ORION_BASE_URL  # noqa
port                = Config.PORT  # noqa
downloadfolder      = Config.DOWNLOAD_FOLDER  # noqa
debug               = Config.DEBUG  # noqa

# check if GPU is available
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
logger.info(f"Pytorch device: {device}")

# Try to connect to the database
try:
    engine = get_engine(Config.DB_CONN_STRING)

    # Initialize spatial metadata by executing the SQL command.
    try:
        with engine.connect() as conn:
            conn.begin()
            conn.execute(text("SELECT InitSpatialMetaData(1);"))
            conn.commit()
    except Exception as e:
        logger.error("Error initializing spatial metadata:", e)

    init_db(engine=engine)
    set_live_mode(engine=engine, mode=Config.LIVE_MODE)

    logger.info('Remote database initialised successfully!')
except Exception as e:
    logger.error(f'Error while connecting to remote database: {e}')

files_to_be_uploaded = [
    {
        "file_path": Config.EXPORT_FILE_PATH_1,
        "object_name": Config.EXPORT_OBJECT_NAME_1,
    },
    {
        "file_path": Config.EXPORT_FILE_PATH_2,
        "object_name": Config.EXPORT_OBJECT_NAME_2,
    },
]

entities_to_be_created = [
    {
        "entity-type": Config.ENTITY_TYPE_1,
        "title": Config.TITLE_1,
        "bucket": bucket_name,
        "filename": Config.EXPORT_OBJECT_NAME_1,
        "description": Config.DESCRIPTION_SINGLE_POST,
        "minio_url": f"https://{endpoint}/{bucket_name}/{Config.EXPORT_OBJECT_NAME_1}",
    },
    {
        "entity-type": Config.ENTITY_TYPE_2,
        "title": Config.TITLE_2,
        "bucket": bucket_name,
        "filename": Config.EXPORT_OBJECT_NAME_2,
        "description": Config.DESCRIPTION_HOTSPOT,
        "minio_url": f"https://{endpoint}/{bucket_name}/{Config.EXPORT_OBJECT_NAME_2}",
    },
]

# download input data from Google Drive
# download_input_data(
#     drive_url=Config.DATA_STORAGE, output_path=Config.INPUT_PATH)  # downloads the data into "./data/input_data"
# insert_input_data_into_db()

# Initialise scheduler for regular tasks
scheduler = BackgroundScheduler()
scheduler.start()
scheduler.add_job(
    run_scheduled_alerts,
    'interval',
    minutes=int(Config.UPDATE_INTERVAL_MIN),
    id='workflow',
    replace_existing=True  # In case a job with the same id exists.
)
Config.LOGGER.info("Scheduled recurring workflow job")

# Load existing data withing component
insert_input_data_into_db()
for filename in os.listdir(Config.INPUT_PATH):
    os.remove(os.path.join(Config.INPUT_PATH, filename))

# Start background jobs
# start_background_geoparsing(ner_model='en_core_web_lg', sleep_interval=300)  # more powerful NER model with short intervals
# start_disaster_monitoring_loop_separate()  # TODO: make this start in the future
start_photo_processing_task()


# ------------------------------------------------------------------------------
# Routes: Utility Functions
# ------------------------------------------------------------------------------
@app.route("/")
def index():
    return redirect(url_for("flasgger.apidocs"))


@app.route('/logs', methods=['GET'])
def get_logs():
    """
    Retrieve the last lines of a log file.

    This endpoint reads and returns the specified number of last lines from a log file.
    The number of lines to retrieve can be specified via a query parameter.

    Methods:
        GET: Retrieve the last lines of the log file.

    Query Parameters:
        lines (int): The number of lines to retrieve from the end of the log file. Defaults to 10.

    Returns:
        Response: A JSON response containing the file path and the retrieved lines,
                  along with an HTTP status code.

    Example:
        /logs?lines=10
    """
    num_lines = request.args.get('lines', 10, type=int)
    log_file_path = '/component/app/logs/app.log'
    last_lines = read_last_lines(log_file_path, num_lines)
    return jsonify({"file": log_file_path, "lines": last_lines})


@app.route('/get-single-post-result', methods=['GET'])
def get_single_post_result():
    """
    Endpoint to retrieve the SinglePostResult GeoJSON file from the container.

    Methods:
        GET: Get the file as GeoJSON.

    Returns:
        Response: GeoJSON file as an attachment.
    """
    if not os.path.exists(Config.EXPORT_FILE_PATH_1):
        return jsonify({"error": f"{Config.EXPORT_OBJECT_NAME_1} not found"}), 404

    try:
        gdf = gpd.read_file(Config.EXPORT_FILE_PATH_1, engine="pyogrio")
        return send_file(
            Config.EXPORT_FILE_PATH_1,
            mimetype='application/geo+json',
            as_attachment=True,
            download_name=Config.EXPORT_OBJECT_NAME_1
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@app.route('/get-hotspot-result', methods=['GET'])
def get_hotspot_result():
    """
    Endpoint to retrieve the HotspotResult GeoJSON file from the container.

    Methods:
        GET: Get the file as GeoJSON.

    Returns:
        Response: GeoJSON file as an attachment.
    """
    if not os.path.exists(Config.EXPORT_FILE_PATH_2):
        return jsonify({"error": f"{Config.EXPORT_OBJECT_NAME_2} not found"}), 404

    try:
        gdf = gpd.read_file(Config.EXPORT_FILE_PATH_2, engine="pyogrio")
        return send_file(
            Config.EXPORT_FILE_PATH_2,
            mimetype='application/geo+json',
            as_attachment=True,
            download_name=Config.EXPORT_OBJECT_NAME_2
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/toggle-live-mode', methods=['POST'])
def toggle_live_mode():
    """
    Toggle live mode of the component.

    Methods:
        POST: Reverses the LIVE_MODE variable.

    Returns:
        Response: A JSON response containing the updated variable value.
    """

    engine = get_engine(Config.DB_CONN_STRING)
    old_value: bool = get_live_mode(engine=engine)
    new_value: bool = not old_value
    set_live_mode(engine=engine, mode=new_value)
    logger.info(f"Live mode set to {new_value}")
    return jsonify({'live-mode': new_value})


@app.route("/kill-process", methods=["POST"])
def kill_process_by_pid_endpoint():
    """
    Kill a process based on its PID.

    This endpoint expects a JSON payload with a "pid" field. It will attempt to gracefully
    terminate the process and its children. If graceful termination fails, it will forcefully kill them.

    Methods:
        Post: Terminate the process with the specified PID.

    Returns:
        Response: A JSON response indicating whether the process was terminated or if an error occurred.
    """
    try:
        pid = int(request.args.get("pid"))
    except (TypeError, ValueError):
        return jsonify({"error": "Valid 'pid' parameter is required."}), 400

    tries: int = 0
    results: list = []
    while tries < 2:
        result = kill_process_by_pid(pid)
        results.append(result)
        status_code = 200 if "result" in result else 500

        if status_code == 200 and Config.WORKFLOW_PROCESS is not None and pid == Config.WORKFLOW_PROCESS.pid:
            Config.WORKFLOW_PROCESS = None
            logger.info(f'Workflow process with PID {pid} was killed!')
        time.sleep(1)
        tries += 1

    return jsonify(results), status_code


@app.route('/upload-input', methods=['POST'])
def upload_input_files():
    """
    This endpoint is able to upload a set of csv files to the input folder and
    inserts the data into the local spatialite database.

    Methods:
        POST: Upload a specific set of files.

    Returns:
        Response: A JSON response indicating whether the upload was successful or not.
    """
    if not os.path.exists(Config.INPUT_PATH):
        os.makedirs(Config.INPUT_PATH)

    # Check if the 'files' key is in the request.
    if 'files' not in request.files:
        return jsonify({'error': 'No files part in the request'}), 400

    files = request.files.getlist('files')
    if not files or all(file.filename == '' for file in files):
        return jsonify({'error': 'No files selected for uploading'}), 400

    saved_files = []
    saved_file_paths = []
    for file in files:
        if file.filename == '':
            continue  # skip empty file fields
        filepath = os.path.join(Config.INPUT_PATH, file.filename)
        file.save(filepath)
        saved_files.append(file.filename)
        saved_file_paths.append(filepath)

    if not saved_files:
        return jsonify({'error': 'No valid files selected for uploading'}), 400

    # Insert the new data into the database.
    insert_input_data_into_db()

    # Delete the files after processing.
    for filepath in saved_file_paths:
        try:
            os.remove(filepath)
            logger.info(f"Deleted file {filepath} after insertion into DB.")
        except Exception as e:
            logger.error(f"Error deleting file {filepath}: {e}")

    return jsonify({'message': 'Files successfully uploaded', 'filenames': saved_files}), 200


# ------------------------------------------------------------------------------
# Routes: File Operations
# ------------------------------------------------------------------------------
@app.route("/upload", methods=["POST"])
def upload_files():
    """
    Handle file uploads and upload the files to MinIO.

    This endpoint handles the uploading of files to a MinIO server. It iterates over a list of
    files to be uploaded, uploads each file to the specified MinIO bucket, and logs the process.
    If all files are uploaded successfully, it returns a success message; otherwise, it returns
    an error message indicating which files failed to upload.

    Methods:
        POST: Handle the uploaded files and upload them to MinIO.

    Returns:
        Response: A JSON response containing the status of the uploaded files and an HTTP status code.
    """
    all_uploaded = True
    uploaded_files = []
    for file_info in files_to_be_uploaded:
        file_path = file_info["file_path"]
        object_name = file_info["object_name"]
        success = uploadToMinio(
            endpoint,
            access_key,
            secret_key,
            bucket_name,
            file_path,
            object_name,
            logger,
        )

        if success[0]:
            minio_URL = createMinioUrl(endpoint, bucket_name, success[1])
            file = displayInfo(success[1], endpoint, bucket_name, minio_URL)
            uploaded_files.append({"file": file})
            logger.info(
                f"Uploaded file: {success[1]} from path: {file_path} at endpoint: {endpoint} in bucket: {bucket_name}"
            )
        else:
            all_uploaded = False
            logger.warning(f"Error uploading '{success[1]}' from path: '{file_path}'")

    if all_uploaded:
        logger.info("All files uploaded successfully")
    else:
        logger.warning("Some files failed to upload")
        return (
            jsonify(
                "Some files failed to upload. Make sure to use the correct file name and file path. Files are "
                "uploaded from the output folder, after the program finishes. "
            ),
            404,
        )
    return jsonify({"uploaded_fies": uploaded_files}), 200


@app.route("/download", methods=["GET"])
def download_file():
    """
    Download a file from MinIO.

    This endpoint handles the downloading of a file from a MinIO server. It parses the MinIO URL
    to extract the endpoint, bucket name, and object name, and then attempts to download the file
    to a specified local directory. The function logs the process and returns the status of the
    download operation.

    Methods:
        GET: Download the specified file from MinIO.

    Returns:
        Response: A JSON response indicating the success or failure of the download operation,
                  along with an HTTP status code.
    """
    try:
        minio_url = request.args.get("Minio_URL", "").strip()
        endpoint, bucket_name, object_name = parseMinioUrl(minio_url)
        download_path = os.path.join(downloadfolder, object_name)
        logger.debug(
            f"Parsing minio-url... endpoint: {endpoint}, bucket: {bucket_name}, filename: {object_name}"
        )
        success = downloadFromMinio(
            endpoint,
            access_key,
            secret_key,
            bucket_name,
            object_name,
            download_path,
            logger,
        )
    except ValueError as ve:
        logger.error(f"Error parsing MinIO URL for minio components {minio_url}: {ve}")
        return (jsonify(f"Error parsing MinIO URL for minio components {minio_url}: {ve}"), 400)

    if success:
        logger.info(f"File downloaded successfully. {object_name}")
        return jsonify(f"File downloaded successfully. {object_name}"), 200
    else:
        logger.error(f"Failed to download file. {object_name}")
        return jsonify(f"Failed to download file. {object_name}"), 400


@app.route("/delete-file", methods=["GET", "POST"])
def delete_file():
    """
    Delete a file from MinIO.

    This endpoint handles the deletion of a file from a MinIO server. It parses the MinIO URL
    to extract the endpoint, bucket name, and object name, and then attempts to delete the file
    from the specified MinIO bucket. The function logs the process and returns the status of the
    deletion operation.

    Methods:
        GET: Delete the specified file from MinIO.
        POST: Delete the specified file from MinIO.

    Returns:
        Response: A JSON response indicating the success or failure of the deletion operation,
                  along with an HTTP status code.
    """
    try:
        minio_url = request.args.get("Minio_URL", "").strip()
        endpoint, bucket_name, object_name = parseMinioUrl(minio_url)
        logger.debug(
            f"Parsing minio-url... endpoint: {endpoint}, bucket: {bucket_name}, filename: {object_name}"
        )
        success = deleteFromMinio(
            endpoint, access_key, secret_key, bucket_name, object_name, logger
        )
    except ValueError as ve:
        logger.error(f"Error parsing MinIO URL for MinIO components {minio_url}: {ve}")
        return (
            jsonify(f"Error parsing MinIO URL for MinIO components {minio_url}: {ve}"),
            400,
        )

    if success:
        logger.info(f"File deleted successfully. {object_name}")
        return jsonify(f"File deleted successfully. {object_name}"), 200
    else:
        logger.error(f"Failed to delete file. {object_name}")
        return jsonify(f"Failed to delete file. {object_name}"), 400


# ------------------------------------------------------------------------------
# Routes: NGSI-LD Entity Management
# ------------------------------------------------------------------------------

@app.route("/create-entities", methods=["POST"])
def create_entities():
    """
    Create NGSI-LD entities using Orion-LD.

    This endpoint handles the creation of NGSI-LD entities with hardcoded metadata. It processes
    the incoming JSON data, validates required fields, and creates entities using the Orion-LD
    context broker. The function logs the process and returns the status of the entity creation.

    Methods:
        POST: Create an entity with hardcoded metadata.

    Returns:
        Response: A JSON response containing the status of the created entities and an HTTP status code.
    """
    created_entities = []
    for new_entity in entities_to_be_created:
        entity_class_instance = UploadEntity(
            type=new_entity["entity-type"],
            title=new_entity["title"],  # title
            description=new_entity["description"],  # descripton
            bucket=new_entity["bucket"],
            filename=new_entity["filename"],
            minio_url=new_entity["minio_url"],
        )
        logger.debug(f"title: {entity_class_instance.title}")
        entity, entity_id = create_upload_entity(entity_class_instance)
        success = createOrionEntity(
            orion_base_url, entity, entity_class_instance.type, logger
        )
        if success:
            created_entities.append({"entity": entity})
            logger.info(
                f"NGSI-LD entity created successfully. Type: {entity_class_instance.type} ID: {entity_id}"
            )
        else:
            logger.warning(
                f"Failed to create NGSI-LD entity. Type: {entity_class_instance.type} ID: {entity_id}"
            )
            return (
                jsonify(
                    {
                        "error": "Failed to create NGSI-LD entity",
                        "type": entity_class_instance.type,
                        "id": entity_id,
                    }
                ),
                400,
            )
    logger.info("NGSI-LD entity creation finished.")

    return jsonify({"created_entities": created_entities}), 200


@app.route("/update-entity", methods=["GET", "POST"])
def update_entity():
    """
    Update an existing NGSI-LD entity using Orion-LD.

    This endpoint handles updating an existing NGSI-LD entity with new data. It processes
    the incoming request parameters, constructs the update payload, and sends it to the
    Orion-LD context broker. The function logs the process and returns the status of the
    update operation.

    Methods:
        GET: Retrieve the entity ID, title, and description from the request parameters.
        POST: Retrieve the entity ID, title, and description from the request parameters.

    Returns:
        Response: A JSON response containing the status of the update operation and an HTTP status code.
    """
    try:
        entity_id = request.args.get("Entity-ID", "").strip()
        logger.debug(f"Received entity_id: {entity_id}")
        title = request.args.get("Title", "").strip()
        description = request.args.get("Description", "").strip()
        update_body = create_update_data(title, description)
        response_status = updateOrionEntity(
            orion_base_url, entity_id, update_body, logger
        )

        result = getOrionEntityById(entity_id, orion_base_url, logger)
        value = result[1]

        status_map = {
            201: {
                "log_message": "Data successfully sent to Orion.",
                "json_response": {
                    "message": "Data successfully sent to Orion."
                    f"updated entity: {value}"
                },
            },
            204: {
                "log_message": "Data successfully sent to Orion.",
                "json_response": {
                    "message": "Data successfully sent to Orion."
                    f"updated entity: {value}"
                },
            },
            400: {
                "log_message": "Bad request to Orion.",
                "json_response": {"error": "Bad request to Orion"},
            },
            401: {
                "log_message": "Unauthorized request to Orion.",
                "json_response": {"error": "Unauthorized request to Orion"},
            },
            404: {
                "log_message": "Resource not found on Orion.",
                "json_response": {"error": "Resource not found on Orion"},
            },
            "default": {
                "log_message": "Unexpected response from Orion.",
                "json_response": {"error": "Unexpected response from Orion"},
            },
        }
        # Get the corresponding log message and JSON response for the status code
        status_info = status_map.get(response_status, status_map["default"])
        # log_message = status_info["log_message"]
        json_response = status_info["json_response"]
        logger.info(status_info)
        return jsonify(json_response), response_status

    except Exception as e:
        # Handle exceptions and log an error message
        app.logger.error(f"Error while sending data to Orion: {str(e)}")
        return jsonify({"error": f"Error while sending data to Orion: {str(e)}"}), 500


@app.route("/get-entity/", methods=["GET"])
def get_entity():
    """
    Retrieve an NGSI-LD entity by ID and parse its MinIO URL.

    This endpoint handles the retrieval of an NGSI-LD entity from the Orion-LD context broker
    using the provided entity ID. It fetches the entity details, checks for the presence of
    a MinIO URL, and parses it if available. The function logs the process and returns the
    entity information or an appropriate error message.

    Methods:
        GET: Retrieve and display entity information.

    Returns:
        Response: A JSON response containing the entity information or an error message,
                  along with an HTTP status code.
    """
    entity_id = request.args.get("Entity-ID", "").strip()
    logger.debug(f"Received entity_id: {entity_id}")
    try:
        logger.debug(f"Fetching entity with ID: {entity_id}")
        result = getOrionEntityById(entity_id, orion_base_url, logger)
        isOK = result[0]
        value = result[1]
        logger.debug(f"Result of fetch: isOK = {isOK}, entity_id={value['id']}")
        if not isOK:
            logger.error(f"Entity with ID {entity_id} not found")
            abort(
                404,
                description=f"Entity ({entity_id}) was not found. Check if the ID is correct.",
            )
        else:
            if "minio_url" in value:
                minio_url_value = value["minio_url"]["value"]
                logger.info(f"Minio-URL: {minio_url_value}")
                try:
                    endpoint, bucket_name, object_name = parseMinioUrl(minio_url_value)
                    logger.debug(
                        f"Parsing minio-url... endpoint: {endpoint}, bucket: {bucket_name}, filename: {object_name}"
                    )
                    # jsonify(f"Entity ID: {value['id']}, Endpoint: {endpoint}, Bucket Name: {bucket_name},
                    # Object Name: {object_name}, MinIO-URL: {minio_url_value}")
                except ValueError as ve:
                    logger.error(
                        f"Error parsing MinIO URL for entity ID {entity_id}: {ve}"
                    )
                    return (
                        jsonify(f"Entity ID: {value['id']}, Invalid MinIO URL format"),
                        400,
                    )
            else:
                logger.warning(f"minio_url not found for entity ID {entity_id}")
                return (
                    jsonify(
                        f"Entity ID: {value['id']}, minio_url not found in the entity"
                    ),
                    400,
                )
    except Exception as e:
        logger.error(f"Error processing entity with ID {entity_id}: {e}")
        return jsonify(f"Error processing entity: {str(e)}"), 404

    return jsonify({"entity": value}), 200


@app.route("/delete-entity/", methods=["GET"])
def delete_entity():
    """
    Delete an NGSI-LD entity by ID using Orion-LD.

    This endpoint handles the deletion of an NGSI-LD entity from the Orion-LD context broker
    using the provided entity ID. It sends a delete request to Orion-LD, logs the process,
    and returns the status of the deletion operation.

    Methods:
        GET: Retrieve the entity ID from the request parameters and delete the entity.

    Returns:
        Response: A JSON response containing the status of the deletion operation and an HTTP status code.
    """
    entity_id = request.args.get("Entity-ID", "").strip()

    logger.debug(f"Received entity_id: {entity_id}")
    try:
        # delete_body = create_delete_data(entity_id, entity_type)
        response_status = deleteOrionEntity(orion_base_url, entity_id, logger)

        status_map = {
            201: {
                "log_message": "Entity successfully deleted from Orion.",
                "json_response": {
                    "message": "Entity successfully deleted from Orion."
                },
            },
            204: {
                "log_message": "Entity successfully deleted from Orion. ID: ",
                "json_response": {
                    "message": "Entity successfully deleted from Orion. ID: "
                },
            },
            400: {
                "log_message": "Bad request to Orion.",
                "json_response": {"error": "Bad request to Orion"},
            },
            401: {
                "log_message": "Unauthorized request to Orion.",
                "json_response": {"error": "Unauthorized request to Orion"},
            },
            404: {
                "log_message": "Resource not found on Orion.",
                "json_response": {"error": "Resource not found on Orion"},
            },
            "default": {
                "log_message": "Unexpected response from Orion.",
                "json_response": {"error": "Unexpected response from Orion"},
            },
        }
        response_status = int(response_status)
        status_info = status_map.get(response_status, status_map["default"])
        # log_message = status_info["log_message"]
        json_response = status_info["json_response"]
        logger.info(status_info)
        return jsonify(json_response), response_status

    except Exception as e:
        # Handle exceptions and log an error message
        app.logger.error(f"Error while sending data to Orion: {str(e)}")
        return jsonify({"error": f"Error while sending data to Orion: {str(e)}"}), 500


@app.route("/subscribe_to_alert", methods=["POST"])
def subscribe_to_alert():
    """
    Subscribe to Orion-LD notifications for NGSI-LD entities.

    This endpoint handles the subscription to notifications from the Orion-LD context broker.
    It checks if a subscription already exists, updates it if necessary, or creates a new
    subscription. The function logs the process and returns the status of the subscription
    operation.

    Methods:
        POST: Create or update a subscription to Orion-LD notifications.

    Returns:
        Response: A JSON response indicating the success or failure of the subscription
                  operation, along with an HTTP status code.
    """
    try:
        # check if subscription already exists:
        '''
        request.get(CB_URL_SUBSCRIPTIONS)

        if subscribe_ID already in subscriptions:
            download, compare subscription content
            if !same:
                update to new subscription
        else:
            create new subscription
        '''
        # if there is a custom callback URL
        custom_callback_url = request.args.get("callback_url", "")
        logger.debug("POST request to subscribe to Orion notifications received.")
        subscribe_ID = f'urn:ngsi-ld:{os.environ.get("PARTNER_ID")}:{os.environ.get("BROKER_SUBSCRIPTION_ID")}'
        entity_type = os.environ.get("BROKER_TYPE_ID")
        entity_url = f'{os.environ.get("ORION_LD_ENDPOINT")}/ngsi-ld/v1/subscriptions'

        logger.info(custom_callback_url)
        if len(custom_callback_url) <= 1:
            # if there is a public url and a base path, use those for the callback url
            public_ip_address: str = os.environ.get("PUBLIC_IP_ADDRESS")
            base_path: str = os.environ.get("BASE_PATH")
            if public_ip_address is not None and base_path is not None:
                callback_url = f'{public_ip_address}{base_path}/notify'
            # otherwise use the ngrok url
            else:
                callback_url = f'{os.environ.get("BASE_NGROK_URL")}/notify'
        else:
            callback_url = custom_callback_url
        logger.info(f"Callback url delivered to Context Broker: {callback_url}")

        # Data for the POST request
        data = {
            "id":           subscribe_ID,
            "type":         "Subscription",
            "name":         "Alert to PLUS",
            "description":  "Alert subscription from " + f'{os.environ.get("PARTNER_ID")}',
            "entities":     [{"type": entity_type}],
            "notification": {
                "endpoint": {"uri": callback_url, "accept": "application/json"}
            },
        }
        logger.info(data)

        # Make a POST request to subscribe to notifications from the Orion Context Broker
        response = requests.post(
            entity_url,
            headers={'Content-Type': 'application/json'},
            json=data
        )

        logger.info(response.json())

        # Mapping of HTTP status codes to log messages and JSON responses
        status_map = {
            201: {
                "log_message": "Subscription successful.",
                "json_response": {"message": "Subscription successful."},
            },
            204: {
                "log_message": "No content response from Orion.",
                "json_response": {"message": "No content response from Orion."},
            },
            400: {
                "log_message": "Bad request to Orion.",
                "json_response": {"error": "Bad request to Orion."},
            },
            401: {
                "log_message": "Unauthorized request to Orion.",
                "json_response": {"error": "Unauthorized request to Orion."},
            },
            404: {
                "log_message": "Resource not found on Orion.",
                "json_response": {"error": "Resource not found on Orion."},
            },
            409: {
                "log_message": "Subscription already exists.",
                "json_response": {"message": "Subscription already exists."},
            },
            "default": {
                "log_message": "Unexpected response from Orion.",
                "json_response": {"error": "Unexpected response from Orion."},
            },
        }

        # Get the corresponding log message and JSON response for the status code
        status_info = status_map.get(response.status_code, status_map["default"])
        log_message = status_info["log_message"]
        json_response = status_info["json_response"]
        # Log the appropriate message
        app.logger.debug(log_message)

        # Return the appropriate JSON response
        return jsonify(json_response), response.status_code

    except Exception as e:
        # Handle exceptions and log an error message
        app.logger.error(f"Error while subscribing to Orion notifications: {str(e)}")
        return (
            jsonify(
                {"error": f"Error while subscribing to Orion notifications: {str(e)}"}
            ),
            500,
        )


# ------------------------------------------------------------------------------
# Routes: Workflow and Data Retrieval
# ------------------------------------------------------------------------------

@app.route("/retrieve-data", methods=["GET"])
def retrieve_data():
    """
    Retrieve input data from Google Drive.

    This endpoint triggers the download of input data from a specified Google Drive folder
    and saves it to the configured input path.

    Methods:
        GET: Initiate the download of input data.

    Returns:
        Response: A JSON response indicating the success or failure of the data retrieval operation,
                  along with an HTTP status code.
    """
    try:
        download_input_data()
    except Exception as e:
        logger.error(f"Error downloading input data: {e}")
        return jsonify({"error": f"Error during file download: {e}"}), 500

    downloaded_files = os.listdir(Config.INPUT_PATH)
    logger.info(f"Downloaded input files: {downloaded_files}")
    return jsonify(f"Input files downloaded successfully: {downloaded_files}."), 200


@app.route("/test-workflow", methods=["GET"])
def run_main():
    """
    Execute the main.py script with the specified NER model.

    This endpoint triggers the execution of the main.py script using the provided language model
    as an argument. It logs the process and returns the status of the script execution.

    Methods:
        GET: Execute the main.py script with the specified language model.

    Query Parameters:
        ner_model (str): The NER model to be used by the main.py script.

    Returns:
        Response: A JSON response indicating the success or failure of the script execution,
                  along with an HTTP status code.
    """
    # TODO: Change this
    try:
        ner_model = request.args.get("ner_model")
        if not ner_model:
            return jsonify({"error": "ner_model parameter is required"}), 400

        logger.info(f"Starting test workflow with ner_model: {ner_model}")

        with Config.WORKFLOW_LOCK:
            if Config.WORKFLOW_PROCESS is not None and Config.WORKFLOW_PROCESS.is_alive():
                logger.info(f"A workflow process with PID {Config.WORKFLOW_PROCESS.pid} is already running.")
                return jsonify({"error": "A workflow process is already running.",
                                "pid": Config.WORKFLOW_PROCESS.pid}), 400

            # # Start the workflow process using multiprocessing.Process
            # TODO: FIX THIS
            Config.WORKFLOW_PROCESS = multiprocessing.Process(
                target=trigger_workflow_from_db
            )
            Config.WORKFLOW_PROCESS.start()
        return jsonify(
            {"message": "Test workflow started. Check /data/export_data for results.",
             "pid": Config.WORKFLOW_PROCESS.pid}
        ), 200

    except Exception as e:
        logger.error(f"Error starting test workflow: {e}")
        return jsonify({"error": f"Error starting test workflow: {e}"}), 400


@app.route("/notify", methods=["POST"])
def notify():
    """
    Handle notifications from Orion-LD and trigger the main workflow.

    This endpoint processes incoming notifications from the Orion-LD context broker. It extracts
    relevant information from the notification payload, parses coordinates to create a polygon,
    and triggers the main workflow. The function logs the process and returns the status of the
    notification handling.

    Methods:
        POST: Handle the notification payload and trigger the main workflow.

    Returns:
        Response: A JSON response indicating the success or failure of the notification handling,
                  along with an HTTP status code.
    """
    try:
        payload_dict = request.get_json()
        try:
            pld_id          : str = payload_dict["data"][0]["id"]  # noqa
            pld_type        : str = payload_dict["data"][0]["type"]  # noqa
        except Exception as e:
            logger.info(f"Payload JSON does not contain required field: {str(e)}")
            return jsonify({"error": "Notify not successful."}), 500

        #############################
        # Notification A: ImageVQA
        #############################
        # If the notification is for SocialMediaCaptions, call the dedicated handler
        if pld_type == "ImageVQA":
            # Pass the entire data payload to the handler function.
            # The handler function is expected to extract its required values (like bucket and filename).
            result = handle_social_media_captions_entity(payload_dict["data"][0])
            if result.get("status") == "success":
                return jsonify(result), 200
            else:
                return jsonify(result), 500

        #############################
        # Notification B: TemaCommand
        #############################
        # Handle a dedicated TEMA command enttiy
        if pld_type == "TemaCommand":
            logger.info(f"Received a TEMA command: {str(payload_dict['data'][0])}")
            result = handle_external_tema_command(payload_dict["data"][0], scheduler)
            if result.get("status") == "success":
                return jsonify(result), 200
            else:
                return jsonify(result), 500

        #############################
        # Notification C: Alert (default)
        #############################
        # Otherwise, we assume that an alert is processed
        # Extract required fields from the payload
        try:
            pld_sender      : str = payload_dict["data"][0]["sender"]["value"]  # noqa
            pld_sent        : str = payload_dict["data"][0]["sent"]["value"]  # noqa
            pld_status      : str = payload_dict["data"][0]["status"]["value"]  # noqa
            pld_msgtype     : str = payload_dict["data"][0]["msgType"]["value"]  # noqa
            pld_scope       : str = payload_dict["data"][0]["scope"]["value"]  # noqa
            pld_category    : str = payload_dict["data"][0]["category"]["value"]  # noqa
            pld_event       : str = payload_dict["data"][0]["event"]["value"]  # noqa
            pld_urgency     : str = payload_dict["data"][0]["urgency"]["value"]  # noqa
            pld_severity    : str = payload_dict["data"][0]["severity"]["value"]  # noqa
            pld_certainty   : str = payload_dict["data"][0]["certainty"]["value"]  # noqa
            pld_effective   : str = payload_dict["data"][0]["effective"]["value"]  # noqa
            pld_expires     : str = payload_dict["data"][0]["expires"]["value"]  # noqa
            pld_areaDesc    : str = payload_dict["data"][0]["areaDesc"]["value"]  # noqa
            pld_coordinates : list = payload_dict["data"][0]["area"]["value"]["coordinates"][0]  # noqa
            pld_bm_id : list = payload_dict["data"][0]["bm_id"]["value"]  # noqa

            logger.debug(f"{payload_dict}")

        except Exception as e:
            logger.info(f"Payload JSON does not contain required field: {str(e)}")
            return jsonify({"error": "Notify not successful."}), 500

        logger.info("Alert received!")
        logger.info(f"Alert ID: {pld_id}")
        logger.info(f"Business Mission ID: {pld_bm_id}")
        logger.info(f'Effective date: {pld_effective}')

        # Parse coordinates to a polygon
        try:
            pld_polygon = Polygon(pld_coordinates)  # noqa
            logger.info(f"{pld_polygon}")
        except Exception as e:
            logger.info(f"Payload Coordinates could not be parsed to shapely polygon: {str(e)}")
            return jsonify({"error": "Notify not successful."}), 500

        # Insert the alert into the database.
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            # Check if an alert with the same bm_id already exists.
            existing_alert = session.query(Alert).filter_by(bm_id=str(pld_bm_id)).first()
            if existing_alert is not None:
                logger.info(f"Alert with bm_id {pld_bm_id} already exists in the database.")
                session.close()
                return jsonify({"error": f"Alert with bm_id {pld_bm_id} already exists."}), 400

            # Delete existing alerts -- the TEMA system cannot yet process multiple alerts at the same time
            session.query(Alert).delete()
            session.commit()
            logger.info("Deleted the old alerts in the database.")
            
            # Insert new alert
            new_alert = Alert(
                id=pld_id,
                alert_type=pld_type,
                sender=pld_sender,
                sent=pd.to_datetime(pld_sent),
                status=pld_status,
                msg_type=pld_msgtype,
                scope=pld_scope,
                category=pld_category,
                event=pld_event,
                urgency=pld_urgency,
                severity=pld_severity,
                certainty=pld_certainty,
                effective=pd.to_datetime(pld_effective, utc=True).tz_convert(Config.TIME_ZONE),
                date_cutoff=pd.to_datetime(pld_effective, utc=True).tz_convert(Config.TIME_ZONE),
                expires=pd.to_datetime(pld_expires, utc=True).tz_convert(Config.TIME_ZONE),
                area_desc=pld_areaDesc,
                geometry=pld_polygon.wkt,  # store as WKT string
                bm_id=str(pld_bm_id)
            )
            session.add(new_alert)
            session.commit()
            logger.info(f"Inserted alert {pld_id} into the database.")
            time.sleep(0.5)
        except Exception as e:
            session.rollback()
            logger.info(f"Failed to insert alert into database: {str(e)}")
        finally:
            session.close()

        # Run script asynchronously by notifying the processing job to process the alerts in the database
        with Config.WORKFLOW_LOCK:
            if Config.WORKFLOW_PROCESS is not None and Config.WORKFLOW_PROCESS.is_alive():
                logger.info(f"A workflow process with PID {Config.WORKFLOW_PROCESS.pid} is already running.")
                return jsonify({"error": "A workflow process is already running.",
                                "pid": Config.WORKFLOW_PROCESS.pid}), 400

            else:
                scheduler.get_job(job_id="workflow").modify(next_run_time=datetime.datetime.now())
                logger.info(f"Started process for alert ID: {pld_id}")

                # Upload photos
                # extract_and_upload_photo_urls()

                return jsonify({"result": "Notification received and process started."}), 200

    except Exception as e:
        logger.info(f"Error in notify, failed to parse payload: {str(e)}")
        return jsonify({"error": "Notify not successful."}), 500


@app.route("/clear-alerts", methods=["POST"])
def clear_alerts():
    """
    Clears all entries in the alerts table.

    Returns:
        A JSON response indicating the number of alerts deleted or an error message.
    """
    try:
        engine = get_engine(Config.DB_CONN_STRING)
        Session = sessionmaker(bind=engine)
        session = Session()
        deleted = session.query(Alert).delete()  # Delete all rows from alerts table.
        session.commit()
        session.close()
        logger.info(f"Cleared {deleted} alerts from the database.")
        return jsonify({"result": f"Cleared {deleted} alerts from the database."}), 200
    except Exception as e:
        logger.error(f"Error clearing alerts: {e}")
        return jsonify({"error": "Failed to clear alerts."}), 500


# ------------------------------------------------------------------------------
# Main entry point
# ------------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(port), debug=False)
