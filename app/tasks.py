import re
import os
import time
import uuid
import datetime
import psutil
import multiprocessing
import pyproj
import shutil
import threading
import h3
import pandas as pd
from typing import Optional
from config import Config
from shapely import Geometry, Polygon
from shapely.ops import unary_union
from helpers.entity_creation import create_upload_entity, createMinioUrl, UploadEntity
from helpers.orion_io import createOrionEntity, updateOrionEntity, create_social_media_alert_entity
from helpers.minio_io import uploadToMinio, downloadFromMinio
from helpers.db import Alert, get_engine, SocialMediaPost, db_results_to_geodataframe, set_alert_cutoff_date
from workflow import trigger_workflow_from_db
from sqlalchemy.orm import sessionmaker
from sqlalchemy import func, or_, and_
from geoalchemy2 import shape
from shapely.ops import transform
from apscheduler.schedulers.background import BackgroundScheduler

logger = Config.LOGGER
access_key = Config.ACCESS_KEY
secret_key = Config.SECRET_KEY
endpoint = Config.ENDPOINT
bucket_name = Config.BUCKET_NAME
orion_ld_endpoint = Config.ORION_LD_ENPOINT
orion_base_url = Config.ORION_BASE_URL
port = Config.PORT

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

caption_received_event = threading.Event()


def run_main_and_upload_entities(bm_id: Optional[str] = None,  alert_id: Optional[str] = None,
                                 aoi_geom: Geometry = None, alert_date: datetime.datetime = None,
                                 cutoff_date: datetime.datetime = None):
    """
    Execute the main script and upload entities to MinIO.

    This function runs the main script using the provided NER model, uploads the resulting
    files to MinIO, and creates corresponding NGSI-LD entities. It logs the process and returns
    the status of the script execution.

    Args:
        ner_model (str): The NER model to be used by the main script. Defaults to "xx_ent_wiki_sm".
        bm_id (str, optional): The business mission ID which is propagated. Defaults to None.
        alert_id (str, optional): The alert ID which is propagated. Defaults to None.
        aoi_geom (Geometry, optional): The bounding box for the AOI. Defaults to None.
        alert_date (datetime.datetime, optional): Sent date of the alert. Defaults to None.
        cutoff_date (datetime.datetime, optional): Cutoff date for the data; for trials. Defaults to None.

    Returns:
        dict: A dictionary containing the status and output of the main script execution.
    """
    try:
        trigger_workflow_from_db(aoi_geom=aoi_geom, alert_date=alert_date, cutoff_date=cutoff_date)
        upload_files_and_create_entities(bm_id=bm_id, alert_id=alert_id)

        return {"status": "success"}
    except Exception as e:
        logger.info(f"Error in run_main_and_upload_entities: {e}")


def run_main_and_update_existing_entities(singlepost_id: str, hotspot_id: str,
                                          bm_id: Optional[str] = None, alert_id: Optional[str] = None,
                                          aoi_geom: Geometry = None, alert_date: datetime.datetime = None,
                                          cutoff_date: datetime.datetime = None):
    """
    Execute the main script and update existing entities in Orion/MinIO.

    Args:
        singlepost_id (str): The id of the existing SinglePostResult entity.
        hotspot_id (str): The id of the existing HotspotResult entity.
        bm_id (str, optional): The business mission ID which is propagated. Defaults to None.
        alert_id (str, optional): The alert ID which is propagated. Defaults to None.
        aoi_geom (Geometry, optional): The bounding box for the AOI. Defaults to None.
        alert_date (datetime.datetime, optional): Sent date of the alert. Defaults to None.
        cutoff_date (datetime.datetime, optional): Cutoff date for the data; for trials. Defaults to None.

    Returns:
        dict: A dictionary containing the status and output of the main script execution.
    """
    try:
        trigger_workflow_from_db(aoi_geom=aoi_geom, alert_date=alert_date, cutoff_date=cutoff_date)
        upload_files_and_update_entities(bm_id=bm_id, alert_id=alert_id, singlepost_id=singlepost_id,
                                         hotspot_id=hotspot_id)

        return {"status": "success"}
    except Exception as e:
        logger.info(f"Error in run_main_and_upload_entities: {e}")


def upload_files_and_create_entities(bm_id: Optional[str] = None, alert_id: Optional[str] = None):
    """
    Upload files to MinIO and create corresponding NGSI-LD entities.

    This function handles the uploading of files to a MinIO server and the creation of
    corresponding NGSI-LD entities. It ensures that the number of entities matches the
    number of uploaded files, logs the process, and returns the status of the operation.

    Args:
        bm_id (str, optional): The source business mission id. Defaults to None.
        alert_id (str, optional): The alert ID which is propagated. Defaults to None.

    Raises:
        Exception: If the number of entities to be created does not match the number of
                   uploaded files.
        Exception: If there is an error during the upload or entity creation process.

    Returns:
        dict: A dictionary containing the status and message of the operation.
    """
    try:
        all_uploaded = True
        uploaded_files_info = []  # Listing to store info about uploaded files

        # Uploading files
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
                bm_id
            )
            unique_object_name = success[1]
            if success[0]:
                minio_URL = createMinioUrl(endpoint, bucket_name, unique_object_name)
                uploaded_files_info.append(
                    {"object_name": unique_object_name, "minio_url": minio_URL}
                )
                logger.info(
                    f"Uploaded file: {unique_object_name} from path: {file_path} at endpoint: {endpoint} in bucket: {bucket_name}"
                )
            else:
                all_uploaded = False
                logger.warning(
                    f"Error uploading '{unique_object_name}' from path: '{file_path}'"
                )

        if all_uploaded:
            logger.info("All files uploaded successfully")
        else:
            logger.warning("Some files failed to upload")

        created_entities = []

        # Ensuring the number of entities matches the number of uploaded files
        if len(entities_to_be_created) != len(uploaded_files_info):
            raise Exception(
                "The number of entities to be created does not match the number of uploaded files."
            )

        # little delay
        time.sleep(5)

        # Creating entities
        for index, new_entity in enumerate(entities_to_be_created):
            uploaded_file_info = uploaded_files_info[index]
            entity_class_instance = UploadEntity(
                type=new_entity["entity-type"],
                title=new_entity["title"],
                description=new_entity["description"],
                bucket=new_entity["bucket"],
                file_name=uploaded_file_info["object_name"],
                minio_url=uploaded_file_info["minio_url"],
                alert_ref=alert_id,
                bm_id=bm_id,
                resource_creation=f"{datetime.datetime.now()}"
            )
            logger.info(f'Entity: {entity_class_instance}')

            entity, entity_id = create_upload_entity(entity_class_instance)
            success = createOrionEntity(
                orion_base_url, entity, entity_class_instance.type, logger
            )
            if success:
                created_entities.append({"entity": entity})
                logger.info(
                    f"NGSI-LD entity created successfully. Type: {entity_class_instance.type} ID: {entity_id}"
                )
                # Update the Alert table record by setting its entity_id where bm_id matches.
                try:
                    engine = get_engine(Config.DB_CONN_STRING)
                    Session = sessionmaker(bind=engine)
                    session = Session()
                    if entity_class_instance.type == Config.ENTITY_TYPE_1:  # SinglePostResult
                        session.query(Alert).filter(Alert.bm_id == bm_id).update({"singlepost_id": entity_id})
                    if entity_class_instance.type == Config.ENTITY_TYPE_2:  # HotspotResult
                        session.query(Alert).filter(Alert.bm_id == bm_id).update({"hotspot_id": entity_id})
                    session.commit()
                    logger.info(f"Updated database entry for alert (bm_id: {bm_id}).")
                except Exception as update_error:
                    session.rollback()
                    logger.error(f"Failed to update alert for bm_id {bm_id}: {update_error}")
                finally:
                    session.close()
            else:
                logger.warning(
                    f"Failed to create NGSI-LD entity. Type: {entity_class_instance.type} ID: {entity_id}"
                )

        return {
            "status": "success",
            "message": "Files uploaded and entities created successfully.",
        }

    except Exception as e:
        raise Exception(f"There was an error: {e}.")


def upload_files_and_update_entities(singlepost_id: str, hotspot_id: str,
                                     bm_id: Optional[str] = None, alert_id: Optional[str] = None):
    """
    Upload files to MinIO and update an existing NGSI-LD entity with new file information.

    This function uploads files to MinIO, then updates the NGSI-LD entity (identified by existing_entity_id)
    by updating the minio_url, filename, and resource_creation fields using the new file data.

    Args:
        singlepost_id (str): The ID of the NGSI-LD singlepost entity to update.
        hotspot_id (str): The ID of the NGSI-LD hotspot entity to update.
        bm_id (str, optional): The source business mission id.
        alert_id (str, optional): The alert id, if applicable.

    Returns:
        dict: A dictionary containing the status and updated values.

    Raises:
        Exception: If file upload fails or the Orion update operation does not succeed.
    """
    try:
        all_uploaded = True
        uploaded_files_info = []  # Store info about uploaded files

        # Upload files using the same logic as in upload_files_and_create_entities
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
                bm_id
            )
            unique_object_name = success[1]
            if success[0]:
                minio_URL = createMinioUrl(endpoint, bucket_name, unique_object_name)
                uploaded_files_info.append(
                    {"object_name": unique_object_name, "minio_url": minio_URL}
                )
                logger.info(
                    f"Uploaded file: {unique_object_name} from path: {file_path} at endpoint: {endpoint} in bucket: {bucket_name}"
                )
            else:
                all_uploaded = False
                logger.warning(
                    f"Error uploading '{unique_object_name}' from path: '{file_path}'"
                )

        if not all_uploaded or len(uploaded_files_info) < 1:
            raise Exception("File upload failed for one or more files.")

        # Ensuring the number of entities matches the number of uploaded files
        if len(entities_to_be_created) != len(uploaded_files_info):
            raise Exception(
                "The number of entities to be created does not match the number of uploaded files."
            )

        # little delay
        time.sleep(5)

        # Update existing entities
        for index, new_entity in enumerate(entities_to_be_created):
            uploaded_file_info = uploaded_files_info[index]
            new_minio_url = uploaded_file_info["minio_url"]
            new_filename = uploaded_file_info["object_name"]
            new_resource_creation = f"{datetime.datetime.now()}"

            update_payload = {
                "minio_url": {"value": new_minio_url},
                "filename": {"value": new_filename},
                "resource_creation": {"value": new_resource_creation}
            }

            # set existing entity id
            if new_entity["entity-type"] == Config.ENTITY_TYPE_1:  # SinglePostResult
                existing_entity_id: str = singlepost_id
            elif new_entity["entity-type"] == Config.ENTITY_TYPE_2:  # HotspotResult
                existing_entity_id: str = hotspot_id

            logger.info(f"Update payload for entity {existing_entity_id}: {update_payload}")

            # Update the entity in Orion. This function call is similar to that in update_entity.
            response_status = updateOrionEntity(orion_base_url, existing_entity_id, update_payload, logger)
            if response_status in [201, 204]:
                logger.info(f"Entity {existing_entity_id} updated successfully.")
            else:
                logger.error(f"Failed to update entity {existing_entity_id} with status: {response_status}")
                raise Exception(f"Failed to update entity {existing_entity_id} with status: {response_status}")
        return {
            "status": "success",
            "message": "Files uploaded and entities updated successfully.",
        }

    except Exception as e:
        raise Exception(f"There was an error in create_files_and_update_entities: {e}")


def kill_process_by_pid(pid: int) -> dict:
    """
    Attempt to gracefully (and if needed forcefully) kill the process with the given PID,
    including any child processes.

    Args:
        pid (int): The process ID to terminate.

    Returns:
        dict: A result dictionary with either a "result" or an "error" key.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return {"result": f"No process with PID {pid} exists."}
    except Exception as e:
        return {"error": f"Error retrieving process: {e}"}

    try:
        # Attempt graceful termination.
        proc.terminate()
        gone, alive = psutil.wait_procs([proc], timeout=10)

        # If the process is still alive, force kill it.
        if alive:
            for p in alive:
                p.kill()
            psutil.wait_procs(alive, timeout=5)

        # Also, kill any child processes that may have been spawned.
        children = proc.children(recursive=True)
        if children:
            for child in children:
                child.terminate()
            gone_children, alive_children = psutil.wait_procs(children, timeout=5)
            if alive_children:
                for child in alive_children:
                    child.kill()
                psutil.wait_procs(alive_children, timeout=5)

        return {"result": f"Process with PID {pid} terminated."}
    except Exception as e:
        return {"error": f"Error killing process: {e}"}


def buffer_in_meters(geom: Geometry, distance_meters: float) -> Geometry:
    """
    Buffers a geometry by a specified distance in meters.

    Args:
        geom (shapely.geometry): Geometry in EPSG:4326.
        distance_meters (float): Buffer distance in meters.

    Returns:
        shapely.geometry: Buffered geometry in EPSG:4326.
    """
    # Transformer from EPSG:4326 to EPSG:3857 (meters)
    project = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True).transform
    geom_proj = transform(project, geom)
    buffered_proj = geom_proj.buffer(distance_meters)
    # Transform back to EPSG:4326
    project_back = pyproj.Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True).transform
    buffered = transform(project_back, buffered_proj)
    return buffered


def run_scheduled_alerts(bm_id: str = None):
    """
    Reads all alerts from the database that have not yet expired and starts the workflow
    for each alert by launching a separate process.
    """
    logger.info('Running scheduled alert tasks ...')
    try:
        engine = get_engine(Config.DB_CONN_STRING)
        Session = sessionmaker(bind=engine)
    except Exception as e:
        logger.error(f"Failed to create sessionmaker: {e}")
        return

    session = Session()
    now = datetime.datetime.now()
    try:
        # Query all alerts that haven't expired.
        if bm_id is None:
            active_alerts = session.query(Alert).filter(Alert.expires >= now).all()
            logger.info(f"Found {len(active_alerts)} active alerts to process.")
        else:
            active_alerts = session.query(Alert).filter(Alert.expires >= now, Alert.bm_id == bm_id).all()
            logger.info(f"Found {len(active_alerts)} active alerts with bm_id {bm_id} to process.")
    except Exception as e:
        logger.error(f"Error querying alerts: {e}")
        active_alerts = []
    finally:
        session.close()

    for alert in active_alerts:
        # Parse the geometry and enlarge with a buffer if available.
        if alert.geometry:
            original_geom = shape.to_shape(alert.geometry)
            aoi_geom = buffer_in_meters(original_geom, Config.AOI_BUFFER_M)  # 100 km = 100,000 meters
            logger.info(f"Buffered AOI geometry for alert id: {alert.id}")
        else:
            aoi_geom = None

        with Config.WORKFLOW_LOCK:
            if alert.singlepost_id and alert.hotspot_id:
                logger.info(f"Results found in database for alert id: {alert.id}.")
                Config.WORKFLOW_PROCESS = multiprocessing.Process(
                    target=run_main_and_update_existing_entities,
                    kwargs={"bm_id": alert.bm_id, "alert_id": alert.id,
                            "singlepost_id": alert.singlepost_id, "hotspot_id": alert.hotspot_id,
                            "aoi_geom": aoi_geom, "alert_date": alert.effective, 'cutoff_date': alert.date_cutoff}
                )
                # Config.WORKFLOW_PROCESS = multiprocessing.Process(
                #     target=run_main_and_upload_entities,
                #     kwargs={"bm_id": alert.bm_id, "alert_id": alert.id,
                #             "aoi_geom": aoi_geom, "alert_date": alert.effective}
                # )
            else:
                logger.info(f"Results are not in database alert id: {alert.id}.")
                Config.WORKFLOW_PROCESS = multiprocessing.Process(
                    target=run_main_and_upload_entities,
                    kwargs={"bm_id": alert.bm_id, "alert_id": alert.id,
                            "aoi_geom": aoi_geom, "alert_date": alert.effective, 'cutoff_date': alert.date_cutoff}
                )

            logger.info(f"Starting scheduled workflow for alert id: {alert.id}")
            Config.WORKFLOW_PROCESS.start()
            logger.info(f"Started process for alert id: {alert.id} with PID: {Config.WORKFLOW_PROCESS.pid}")
            Config.WORKFLOW_PROCESS.join()
            logger.info(f"Finished processing alert id: {alert.id}")


def run_disaster_monitoring_loop_separate(sleep_interval: int = 3600, threshold: int = 10, cooldown_hours: int = 72):
    """
    Continuously monitors disaster-related posts in Europe at a given H3 resolution.
    For each grid cell, it counts posts (from the last 12 hours) whose text matches either
    wildfire or flood keywords. For each event type, only adjacent cells are merged.
    If a cluster of adjacent cells exceeds the threshold, an alert JSON is generated.
    Once an alert is generated for a cell cluster, those cells are put on cooldown for the specified period.

    Args:
        sleep_interval (int): Number of seconds to wait between iterations.
        threshold (int): Post threshold to trigger an alert.
        cooldown_hours (int): Cooldown period in hours for which a grid cell cannot be reprocessed.
    """
    # Define a world polygon covering all longitudes and latitudes.
    europe_polygon = {
        "type": "Polygon",
        "coordinates": [[
            [34.040523, 2.131997],
            [34.040523, 27.450314],
            [54.893459, 27.450314],
            [54.893459, 2.131997],
            [34.040523, 2.131997]
        ]]
    }
    # europe_polygon = {
    #     "type": "Polygon",
    #     "coordinates": [
    #         [
    #             [47.877057, 10.904709],
    #             [47.877057, 12.179416],
    #             [48.492341, 12.179416],
    #             [48.492341, 10.904709],
    #             [47.877057, 10.904709]
    #         ]
    #     ]
    # }

    # Compile regex patterns.
    wildfire_pattern = re.compile(r"(fire|feuer|brand|incendio|tulipalo|maastopalo|πυρκαγι|καταιγίδ)", re.IGNORECASE)
    flood_pattern = re.compile(r"(flood|überschwemmung|flut|alluvione|inundación|tulva|πλημμύρ)", re.IGNORECASE)

    # Get H3 cells covering the world.
    all_cells = h3.polyfill(europe_polygon, Config.H3_RESOLUTION)
    logger.info(f"Total grid cells at resolution {Config.H3_RESOLUTION}: {len(all_cells)}")

    # Dictionaries to track cooldowns.
    cooldown_wildfire = {}
    cooldown_flood = {}

    # Dictionaries to store triggered cells with their counts.
    # Keys: cell id, Value: tuple (cell_polygon, count)
    triggered_wildfire = {}
    triggered_flood = {}

    while True:
        now = datetime.datetime.utcnow()
        twelve_hours_ago = now - datetime.timedelta(hours=12)

        # Remove expired cooldown entries.
        cooldown_wildfire = {cell: expiry for cell, expiry in cooldown_wildfire.items() if expiry > now}
        cooldown_flood = {cell: expiry for cell, expiry in cooldown_flood.items() if expiry > now}

        # Clear dictionaries for this iteration.
        triggered_wildfire.clear()
        triggered_flood.clear()

        for cell in all_cells:
            # Skip cells if already on cooldown for both events.
            if cell in cooldown_wildfire and cell in cooldown_flood:
                continue

            cell_boundary = h3.h3_to_geo_boundary(cell, geo_json=True)
            cell_polygon = Polygon(cell_boundary)

            # Query posts intersecting this cell.
            engine = get_engine(Config.DB_CONN_STRING)
            Session = sessionmaker(bind=engine)
            session = Session()
            try:
                posts = session.query(SocialMediaPost).filter(
                    SocialMediaPost.date >= twelve_hours_ago,
                    func.ST_Intersects(
                        SocialMediaPost.geometry,
                        func.ST_GeomFromText(cell_polygon.wkt, 4326)
                    )
                ).all()
            except Exception as e:
                logger.error(f"Error querying posts for cell {cell}: {e}")
                posts = []
            finally:
                session.close()
                engine.dispose()

            # Count matching posts.
            wildfire_count = sum(1 for post in posts if post.text and wildfire_pattern.search(post.text))
            flood_count = sum(1 for post in posts if post.text and flood_pattern.search(post.text))

            if wildfire_count > threshold and cell not in cooldown_wildfire:
                triggered_wildfire[cell] = (cell_polygon, wildfire_count)
                cooldown_wildfire[cell] = now + datetime.timedelta(hours=cooldown_hours)
                # logger.info(f"Triggered wildfire in cell {cell_polygon.wkt} with {wildfire_count} posts.")
            if flood_count > threshold and cell not in cooldown_flood:
                triggered_flood[cell] = (cell_polygon, flood_count)
                cooldown_flood[cell] = now + datetime.timedelta(hours=cooldown_hours)
                # logger.info(f"Triggered flood in cell {cell_polygon.wkt} with {flood_count} posts.")

        # Process wildfire clusters.
        if triggered_wildfire:
            # Get list of cell polygons for wildfire.
            polygons = [val[0] for val in triggered_wildfire.values()]
            union_wildfire = unary_union(polygons)
            if union_wildfire.geom_type == "Polygon":
                clusters_wildfire = [union_wildfire]
            elif union_wildfire.geom_type == "MultiPolygon":
                clusters_wildfire = list(union_wildfire.geoms)
            else:
                clusters_wildfire = []
            # For each cluster, sum the counts of cells whose centroids fall within the cluster.
            for cluster in clusters_wildfire:
                cluster_count = 0
                for cell, (poly, count) in triggered_wildfire.items():
                    if cluster.contains(poly.centroid):
                        cluster_count += count
                effective = now
                expires = now + datetime.timedelta(hours=48)
                bm_id_wildfire = str(uuid.uuid4())
                alert_json = create_social_media_alert_entity("Wildfire", cluster, bm_id_wildfire, effective, expires,
                                                              cluster_count)
                logger.info(f"Generated Wildfire Alert in {cluster.wkt}: {cluster_count}")
                success = createOrionEntity(Config.ORION_BASE_URL, alert_json, 'SocialMediaAlert', logger)
                if success:
                    logger.info(f"NGSI-LD Alert created successfully. ID: {alert_json['id']}")
                else:
                    logger.warning(f"Failed to create NGSI-LD Alert. ID: {alert_json['id']}")

        # Process flood clusters.
        if triggered_flood:
            polygons = [val[0] for val in triggered_flood.values()]
            union_flood = unary_union(polygons)
            if union_flood.geom_type == "Polygon":
                clusters_flood = [union_flood]
            elif union_flood.geom_type == "MultiPolygon":
                clusters_flood = list(union_flood.geoms)
            else:
                clusters_flood = []
            for cluster in clusters_flood:
                cluster_count = 0
                for cell, (poly, count) in triggered_flood.items():
                    if cluster.contains(poly.centroid):
                        cluster_count += count
                effective = now
                expires = now + datetime.timedelta(hours=48)
                bm_id_flood = str(uuid.uuid4())
                alert_json = create_social_media_alert_entity("Flood", cluster, bm_id_flood, effective, expires,
                                                              cluster_count)
                logger.info(f"Generated Flood Alert in {cluster.wkt}: {cluster_count}")
                success = createOrionEntity(Config.ORION_BASE_URL, alert_json, 'SocialMediaAlert', logger)
                if success:
                    logger.info(f"NGSI-LD Alert created successfully. ID: {alert_json['id']}")
                else:
                    logger.warning(f"Failed to create NGSI-LD Alert. ID: {alert_json['id']}")

        logger.info(f"Iteration complete. Sleeping for {sleep_interval} seconds before next disaster monitoring iteration.")
        time.sleep(sleep_interval)


def start_disaster_monitoring_loop_separate(sleep_interval: int = 3600, threshold: int = 10, cooldown_hours: int = 72):
    """
    Starts the disaster monitoring loop in a background daemon thread.

    Args:
        sleep_interval (int): Number of seconds to wait between iterations.
        threshold (int): Number of posts needed to trigger an event.
        cooldown_hours (int): Cooldown period (in hours) for which a grid cell is not reprocessed.
    """
    thread = threading.Thread(
        target=run_disaster_monitoring_loop_separate,
        kwargs={
            "sleep_interval": sleep_interval,
            "threshold": threshold,
            "cooldown_hours": cooldown_hours
        }
    )
    thread.daemon = True
    thread.start()
    logger.info("Separate disaster monitoring loop started in background.")


def delete_all_files_in_input_path():
    folder = Config.INPUT_PATH
    if os.path.exists(folder) and os.path.isdir(folder):
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
                elif os.path.isdir(file_path):
                    shutil.rmtree(file_path)
            except Exception as e:
                print(f'Failed to delete {file_path}. Reason: {e}')


def extract_and_upload_photo_urls(chunk_size: int = 100, wait_timeout_min: int = 60) -> dict:
    """
    Extract posts from the database that have a photo_url, create a GeoJSON file containing
    "id", "text", and "photo_url", upload it to MinIO, and create a corresponding NGSI-LD upload entity.
    This is done in chunks. If captions don't arrive within a certain time-frame, the chunk is uploaded again.

    Args:
        chunk_size (int): The chunk size. Defaults to 100.
        wait_timeout_min (int): The wait timeout in minutes. Defaults to 120.

    Returns:
        dict: A dictionary with the status, message, and upload details.
    """
    try:
        # Create a session and query posts with a photo_url
        engine = get_engine(Config.DB_CONN_STRING)
        Session = sessionmaker(bind=engine)
        session = Session()
        posts = session.query(SocialMediaPost).filter(
            and_(
                and_(
                    SocialMediaPost.photo_url != None,
                    func.length(SocialMediaPost.photo_url) >= 5
                ),
                or_(
                    SocialMediaPost.caption == None,
                    func.length(SocialMediaPost.caption) <= 3
                )
            )
        ).all()
    except Exception as e:
        logger.error(f"Error querying posts: {e}")
        return {"status": "error", "message": f"Error querying posts: {e}"}
    finally:
        session.close()
        engine.dispose()

    if not posts:
        logger.info("No photo posts found; nothing to do.")
        return {"status": "success", "message": "No posts to upload."}

    # Build GeoJSON features from the posts
    post_df = pd.DataFrame(db_results_to_geodataframe(posts)[['id', 'text', 'photo_url']])

    if len(post_df) == 0:
        logger.info("No photo posts found; nothing to do.")
        return {"status": "success", "message": "No posts to upload."}

    ###################
    # Main loop
    ###################
    # iterate in chunks of chunk_size
    chunk_results: list = []
    for idx, start in enumerate(range(0, len(post_df), chunk_size), start=1):
        chunk: pd.DataFrame = post_df.iloc[start:start + chunk_size]
        file_name = f"photo_posts_{idx}.json"
        file_path = os.path.join(Config.TEMP_PATH, file_name)

        # write JSON to disk
        try:
            chunk.to_json(file_path, orient='records')
            logger.info(f"Image chunk {idx}: wrote {len(chunk)} posts to {file_path}")
        except Exception as e:
            logger.error(f"Image chunk {idx}: error writing JSON: {e}")
            return {"status": "error", "message": f"Chunk {idx} write error: {e}"}

        # upload JSON  to MinIO
        ok, unique_name = uploadToMinio(
            endpoint,
            access_key,
            secret_key,
            bucket_name,
            file_path,
            file_name,
            logger,
            'images'
        )
        if not ok:
            logger.error(f"Image chunk {idx}: upload failed.")
            return {"status": "error", "message": f"Image chunk {idx} upload failed."}

        minio_url = createMinioUrl(endpoint, bucket_name, unique_name)
        logger.info(f"Image chunk {idx}: uploaded to MinIO at {minio_url}")

        # create NGSI-LD entity
        upload_entity = UploadEntity(
            type="SocialMediaImages",
            title=f"Image chunk {idx}",
            description=f"JSON for the photo URLS of posts {start+1} to {start+len(chunk)}",
            bucket=bucket_name,
            file_name=unique_name,
            minio_url=minio_url,
            alert_ref=None,
            bm_id='images',
            resource_creation=str(datetime.datetime.now())
        )
        entity, entity_id = create_upload_entity(upload_entity)

        # Define a helper to send the entity
        def send_entity():
            success = createOrionEntity(orion_base_url, entity, upload_entity.type, logger)
            if success:
                logger.info(f"Chunk {idx}: NGSI-LD entity sent (ID: {entity_id})")
            else:
                logger.warning(f"Chunk {idx}: failed to send NGSI-LD entity (ID: {entity_id})")
            return success

        # Initial send
        send_entity()

        # Wait loop: on timeout retry sending the entity
        logger.info(f"Image chunk {idx}: waiting up to {wait_timeout_min} minutes for captions...")
        received = caption_received_event.wait(timeout=(wait_timeout_min * 60))
        if received:
            logger.info(f"Chunk {idx}: received captions, proceeding to next chunk.")
            caption_received_event.clear()
            status = "processed"
        else:
            logger.warning(f"Chunk {idx}: no captions in {wait_timeout_min} minutes; skipping this chunk.")
            status = "skipped"

        chunk_results.append({
            "chunk": idx,
            "count": len(chunk),
            "minio_url": minio_url,
            "entity_id": entity_id,
            "status": status
        })

    return {
        "status": "success",
        "message": f"Uploaded {len(chunk_results)} image chunks ({len(post_df)} posts total).",
        "chunks": chunk_results
    }


def update_posts_caption_from_minio(caption_object_name: str, caption_bucket: str, download_path: str) -> dict:
    """
    Downloads a JSON file from MinIO containing GeoJSON features with an additional "caption" property,
    and updates the caption column for each post in the database using the post's "id".

    Args:
        caption_object_name (str): The name of the object to download from MinIO.
        caption_bucket (str): The bucket where the captions are stored.
        download_path (str): The local file path where the downloaded JSON should be saved.

    Returns:
        dict: A dictionary with the operation status and a message.
    """
    # TODO: this is not yet in use
    # Download the file from MinIO using the provided function.
    download_success = downloadFromMinio(
        endpoint, access_key, secret_key, caption_bucket, caption_object_name, download_path, logger
    )
    if not download_success:
        logger.error("Failed to download the captions from MinIO")
        return {"status": "error", "message": "Failed to download the file from MinIO"}

    # Load the JSON data from the downloaded file.
    try:
        data = pd.read_json(download_path, lines=True, dtype={'id': str})
    except Exception as e:
        logger.error(f"Error reading captions JSON file: {e}")
        return {"status": "error", "message": f"Error reading captions JSON file: {e}"}

    # Set up a database session.
    engine = get_engine(Config.DB_CONN_STRING)
    Session = sessionmaker(bind=engine)
    session = Session()

    updated_count = 0
    try:
        # Iterate over each feature in the data.
        for index, row in data.iterrows():
            post_id = row["id"]
            caption = row["caption"]

            # Only update if both id and caption are available.
            if post_id is not None and caption is not None:
                # Locate the respective post in the database using the post's ID.
                # logger.info(post_id, caption)
                post = session.query(SocialMediaPost).filter(SocialMediaPost.id == post_id).first()
                # logger.info(str(post))
                if post:
                    post.caption = caption
                    updated_count += 1
        session.commit()
        logger.info(f"Updated caption for {updated_count} posts in the database.")
    except Exception as e:
        session.rollback()
        logger.error(f"Error updating posts: {e}")
        return {"status": "error", "message": f"Error updating posts: {e}"}
    finally:
        session.close()
        engine.dispose()

    return {"status": "success", "message": f"Updated caption for {updated_count} posts in the database."}


def handle_social_media_captions_entity(entity: dict) -> dict:
    """
    Handles an incoming SocialMediaCaptions entity with the structure of UploadEntity.
    It downloads the associated file from MinIO and updates the caption field for posts in the database.

    The expected entity structure should include:
        - "bucket": The MinIO bucket where the file is stored.
        - "filename": The name of the file/object in MinIO.

    Args:
        entity (dict): The SocialMediaCaptions entity.

    Returns:
        dict: A dictionary with the operation status and message.
    """
    # Extract required information from the entity.
    bucket = entity.get("bucket")["value"]
    filename = entity.get("filename")["value"]

    if not bucket or not filename:
        logger.error("Entity is missing required 'bucket' or 'filename' keys.")
        return {"status": "error", "message": "Entity is missing required 'bucket' or 'filename' keys."}

    if not os.path.exists(Config.TEMP_PATH):
        os.makedirs(Config.TEMP_PATH)
    download_path = os.path.join(Config.TEMP_PATH, filename)

    # Call the function that downloads the file from MinIO and updates the captions.
    result = update_posts_caption_from_minio(filename, bucket, download_path)

    # Signal that a SocialMediaCaptions entity was processed.
    caption_received_event.set()
    return result


def handle_external_tema_command(entity: dict, scheduler: BackgroundScheduler) -> dict:
    """Handle an external TEMA command entity, specifically for setting the cutoff date of analyses.

    Args:
        entity (dict): The TemaCommand entity data.

    Returns:
        dict: A dictionary with the operation status and message.
    """
    target_component: str = entity.get("target_component", {}).get("value")
    command: str = entity.get("command", {}).get("value")
    parameter_dict: dict = entity.get("parameters", {}).get("value")

    # currently, the only functionality is to force an update of the cutoff date and to re-run the analysis
    if target_component == 'tfa11' and command == 'update':
        logger.info(f'Received an update request with the following parameters: {str(parameter_dict)}')

        bm_id: str = parameter_dict.get("bm_id")
        trial: str = parameter_dict.get("trial")  # noqa
        until_date_raw: str = parameter_dict.get("until_date")
        update_interval: str = parameter_dict.get("update_interval")

        # update the until date in the database
        if until_date_raw is not None and bm_id is not None:
            engine = get_engine(Config.DB_CONN_STRING)
            until_date: datetime.date = pd.to_datetime(parameter_dict.get("until_date"), utc=True).tz_convert(Config.TIME_ZONE)
            update_success: bool = set_alert_cutoff_date(engine=engine, bm_id=bm_id, cutoff_date=until_date)

            # trigger the job in a background thread if the update succeeded
            if update_success:
                threading.Thread(target=run_scheduled_alerts, kwargs={"bm_id": bm_id}).start()
                return {"status": "success", "message": f"Update applied; analysis re-run started for bm_id={bm_id}."}
            else:
                return {"status": "error", "message": f"Failed to update cutoff date for bm_id={bm_id}. Does the bm_id exist?"}
        if update_interval is not None:
            scheduler.reschedule_job('workflow', trigger='interval', minutes=int(update_interval))
            logger.info(f"Updated workflow interval to {update_interval} minutes")
            return {"status": "success", "message": f"Updated workflow interval to {update_interval} minutes"}

    elif target_component == 'tfa11' and command != 'update':
        return {"status": "success", "message": "Received an unknown command. Did nothing."}
    else:
        return {"status": "success", "message": "Received command, but not for tfa11."}


def run_photo_processing_loop(wait_timeout: int = 3600) -> None:
    """
    Background task that repeatedly runs the process of extracting and uploading photo URLs,
    then waits for an incoming SocialMediaCaptions entity to update captions, and repeats.

    Args:
        wait_timeout (int): Maximum time in seconds to wait for a captions entity signal.
    """
    while True:
        logger.info("Starting extract_and_upload_photo_urls process.")
        result = extract_and_upload_photo_urls()
        logger.info(f"Extract and upload result: {result}")

        logger.info(f"Sleeping for {wait_timeout} seconds before next cycle.")
        time.sleep(wait_timeout)


def start_photo_processing_task():
    photo_loop_thread = threading.Thread(target=run_photo_processing_loop, kwargs={"wait_timeout": 3600},
                                         daemon=True)
    photo_loop_thread.start()
