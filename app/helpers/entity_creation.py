"""
Author: Andreas Kramer
This script contains utility functions to support operations related to creating entity metadata
for an Orion-LD entity and parsing MinIO URLs. These utilities are intended to be used within
a larger application, -> Flask-based web application.

Functions:
    - create_entity_metadata: Generates metadata for an Orion-LD entity with a unique identifier
      and various properties such as geometry, text, media URL, relevance score, and more.
    - parseMinioUrl: Parses a MinIO URL to extract the endpoint, bucket name, and object name.

Usage:
    These functions can be imported and utilized in other parts of the application where
    entity creation and MinIO URL parsing are needed.

Dependencies:
    - uuid: Used for generating unique identifiers.
    - urllib.parse: Provides functions to parse URLs.
    - os: Used for interacting with the operating system.
"""

import uuid
from urllib.parse import urlparse
import os
import requests
from pydantic import BaseModel
from typing import Optional
import datetime

PARTNER_NAME = "IT:U"
TECH = "TFA-11"
TYPE_NAME = "SinglePostResult"
TYPE_NAME_2 = "HotspotResult"


class UploadEntity(BaseModel):
    type: str
    title: str
    bucket: str
    file_name: str
    description: str
    minio_url: str
    bm_id: Optional[str]
    alert_ref: Optional[str]
    resource_creation: str


class SinglePostResult(BaseModel):
    title: str
    bucket: str
    filename: str
    description: str
    minio_url: str


class HotspotResult(BaseModel):
    title: str
    bucket: str
    filename: str
    description: str
    minio_url: str


def create_entity_id(partner=PARTNER_NAME, tech=TECH, type_name=TYPE_NAME) -> str:
    unique_id = str(uuid.uuid4())
    entity_id = f"urn:ngsi-ld:tema:{partner}:{tech}:{type_name}:{unique_id}"
    return entity_id


def create_upload_entity(upload_entity: UploadEntity):
    id = create_entity_id(type_name=upload_entity.type)
    entity = {
        "id": f"{id}",
        "type": f"{upload_entity.type}",
        "creator": {"type": "Property", "value": f"{PARTNER_NAME}"},
        # "dateCreated": {"type": "Property", "value": f"{datetime.datetime.now()}"},
        "title": {"type": "Property", "value": f"{upload_entity.title}"},
        "description": {"type": "Property", "value": f"{upload_entity.description}"},
        "bucket": {"type": "Property", "value": f"{upload_entity.bucket}"},
        "filename": {"type": "Property", "value": f"{upload_entity.file_name}"},  # to be changed to file_name at some point but I do not want to break USE's system # noqa
        "minio_url": {"type": "Property", "value": f"{upload_entity.minio_url}"},
        "bm_id": {"type": "Property", "value": f"{upload_entity.bm_id}"},
        "alert_ref": {"type": "Property", "value": f"{upload_entity.alert_ref}"},
        "resource_creation": {"type": "Property", "value": f"{upload_entity.resource_creation}"},
    }
    return entity, id


def create_update_data(new_title, new_description):
    update_data = {
        "title": {"type": "Property", "value": f"{new_title}"},
        "description": {"type": "Property", "value": f"{new_description}"},
    }
    return update_data


def create_delete_data(entity_id, type):
    delete_data = {
        "contextElements": [
            {"type": f"{type}", "isPattern": "false", "id": f"{entity_id}"}
        ],
        "updateAction": "DELETE",
    }
    return delete_data


def create_singlepost_result_entity(single_post_result: SinglePostResult):
    id = create_entity_id()
    now = datetime.datetime.now()
    entity = {
        "id": {"type": "Property", "value": f"{id}"},
        "type": {"type": "Property", "value": f"{TYPE_NAME}"},
        "creator": {"type": "Property", "value": f"{PARTNER_NAME}"},
        "dateCreated": {"type": "Property", "value": f"{now}"},
        "title": {"type": "Property", "value": f"{single_post_result.title}"},
        "description": {
            "type": "Property",
            "value": f"{single_post_result.description}",
        },
        "bucket": {"type": "Property", "value": f"{single_post_result.bucket}"},
        "filename": {"type": "Property", "value": f"{single_post_result.filename}"},
        "minIO_url": {"type": "Property", "value": f"{single_post_result.minio_url}"},
    }
    return entity


"""
        "@context": [
          "https://schema.lab.fiware.org/ld/context",
          "https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld"
        ]
"""
# do not add @context for now, as it is not working with the current version of the Orion-LD


def create_hotspot_result_entity(hotspot_result: HotspotResult):
    id = create_entity_id(partner=PARTNER_NAME, tech=TECH, type_name=TYPE_NAME_2)
    now = datetime.datetime.now()
    entity = {
        "id": {"type": "Property", "value": f"{id}"},
        "type": {"type": "Property", "value": f"{TYPE_NAME_2}"},
        "creator": {"type": "Property", "value": f"{PARTNER_NAME}"},
        "dateCreated": {"type": "Property", "value": f"{now}"},
        "title": {"type": "Property", "value": f"{hotspot_result.title}"},
        "description": {"type": "Property", "value": f"{hotspot_result.description}"},
        "bucket": {"type": "Property", "value": f"{hotspot_result.bucket}"},
        "filename": {"type": "Property", "value": f"{hotspot_result.filename}"},
        "minIO_url": {"type": "Property", "value": f"{hotspot_result.minio_url}"},
    }
    return entity


def update_single_post_result_entity(id: str, filename: str):
    payload = {
        "filename": filename,
    }
    orion_endpoint = os.environ.get("ORION_LD_ENDPOINT")
    url = f"{orion_endpoint} /ngsi-ld/v1/entities/{id}/attrs"
    response = requests.post(url, json=payload)
    response.raise_for_status()


def createMinioUrl(endpoint, bucket_name, object_name):
    minio_url = f"https://{endpoint}/{bucket_name}/{object_name}"
    return minio_url


def parseMinioUrl(minio_url):

    parsed_url = urlparse(minio_url)
    endpoint = parsed_url.netloc
    path_parts = parsed_url.path.lstrip("/").split(
        "/", 1
    )  # Split only on the first '/' to separate bucket from the rest
    bucket_name = path_parts[0]
    object_name = path_parts[1] if len(path_parts) > 1 else ""
    return endpoint, bucket_name, object_name
