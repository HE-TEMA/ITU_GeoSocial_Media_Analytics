"""
Author: Andreas Kramer
This script contains utility functions for interacting with the Orion-LD context broker.
These functions enable the creation and retrieval of NGSI-LD entities within the Orion-LD broker.

Functions:
    - createOrionEntity: Creates an NGSI-LD entity in the Orion-LD context broker.
    - getOrionEntityById: Retrieves an NGSI-LD entity from the Orion-LD context broker by its ID.

Usage:
    These functions can be imported and used within other parts of the application where
    interaction with the Orion-LD context broker is required.

Dependencies:
    - requests: Python HTTP library for making HTTP requests.
    - os: Used for interacting with the operating system.
"""
import uuid
import requests
import datetime
from shapely import Polygon


def createOrionEntity(orion_ld_endpoint, entity, type, logger):
    h = {  # noqa
        # header can be included here
    }
    # {"content-type": "application/json"}
    logger.debug(f"Creating entity of type {type} at {orion_ld_endpoint}")
    try:
        response = requests.post(f"{orion_ld_endpoint}", json=entity)
        # print(response.content)
        logger.info(response.content)
        response.raise_for_status()  # Raise an error for bad status codes
        logger.info(f"Entity created successfully with status code: {response.status_code}")
        logger.info(f"Posted new entity of type: {type}. ")
        return True

    except requests.RequestException as e:

        logger.error(
            f"Failed to create entity: Status code: {response.status_code if response else 'N/A'}, "
            f"Response: {response.text if response else 'N/A'}, Error: {e}"
        )
        return False


def updateOrionEntity(base_url, entity_id, update_body, logger):
    logger.debug(f"Updating entity: {entity_id}.")
    response = None
    try:
        update_attrs_url = f"{base_url}/{entity_id}/attrs"  # or without/attrs ?
        response = requests.post(update_attrs_url, json=update_body)

        logger.info(f"{response.content}")
        # response.raise_for_status()  # Raise an error for bad status codes

        logger.info(f"Updated entity: {entity_id} with new attributes: {update_body}. ")
        return response.status_code

    except requests.RequestException as e:

        logger.error(f"Failed to update entity: {str(e)}")
        return response.status_code


def deleteOrionEntity(orion_ld_endpoint, entity_id, logger):
    """
    Delete an entity from the Orion Context Broker.

    Parameters:
    orion_ld_endpoint (str): The Orion Context Broker endpoint.
    entity_id (str): The ID of the entity to delete.
    logger (Logger): The logger for logging messages.

    Returns:
    bool: True if the entity was deleted successfully, False otherwise.
    """
    headers = {"Content-Type": "application/json", "Accept": "application/ld+json"}
    delete_url = f"{orion_ld_endpoint}/{entity_id}"
    logger.debug(f"Deleting entity with ID {entity_id} at {orion_ld_endpoint}")
    try:
        response = requests.delete(delete_url, headers=headers)
        response.raise_for_status()  # Raise an error for bad status codes
        logger.info(
            f"Entity with ID {entity_id} deleted successfully with status code: {response.status_code}"
        )
        return response.status_code
    except requests.RequestException as e:
        logger.error(f"Failed to delete entity: {str(e)}")
        return response.status_code


def getOrionEntityById(entity_id, base_url, logger):
    h = {  # noqa
        "Content-Type": "application/json",
        "Accept": "application/ld+json",
        "Link": '<https://uri.etsi.org/ngsi-ld/v1/ngsi-ld-core-context.jsonld>; rel="http://www.w3.org/ns/json-ld#context"; type="application/ld+json"',  # noqa
        # "Authorization": f"Bearer {access_token}",
        # "Fiware-Service": fiware_service
    }

    url = f"{base_url}/{entity_id}"
    logger.debug(f"Retrieving entity with ID {entity_id} from {url}")
    try:
        response = requests.get(url)
        response.raise_for_status()  # Raise an error for bad status codes
        logger.info(f"Entity retrieved successfully: {response.json()}")
        return (True, response.json())
    except requests.RequestException as e:
        logger.error(
            f"Failed to retrieve entity: Status code: {response.status_code if response else 'N/A'}, "
            f"Response: {response.text if response else 'N/A'}, Error: {e}"
        )
        return (
            False,
            {
                "error": response.status_code if response else "N/A",
                "message": response.text if response else str(e),
            },
        )


def create_social_media_alert_entity(
        event_type: str, union_polygon: Polygon, bm_id: str, effective: datetime.datetime,
        expires: datetime.datetime, relevant_posts: int) -> dict:
    """
    Create a dictionary for an alert given a union polygon of triggered grid cells.

    Args:
        event_type (str): "Wildfire" or "Flood"
        union_polygon (Polygon): The union of adjacent H3 cell polygons.
        bm_id (str): Business mission identifier.
        effective (datetime): The effective time of the alert.
        expires (datetime): The expiry time of the alert.
        relevant_posts (int): The number of relevant posts in this area.

    Returns:
        dict: The alert JSON dictionary.
    """
    alert_id = f"urn:ngsi-ld:SocialMediaAlert:{uuid.uuid4()}"
    centroid = union_polygon.centroid

    alert = {
        "id": alert_id,
        "type": "SocialMediaAlert",
        "area": {
            "type": "GeoProperty",
            "value": {
                "type": "Polygon",
                "coordinates": [list(union_polygon.exterior.coords)]
            }
        },
        "areaDesc": {
            "type": "Property",
            "value": "TEMA Pilot exercise"
        },
        "bm_id": {
            "type": "Property",
            "value": bm_id
        },
        "category": {
            "type": "Property",
            "value": "Safety"
        },
        "certainty": {
            "type": "Property",
            "value": "Observed"
        },
        "effective": {
            "type": "Property",
            "value": effective.isoformat() + "Z"
        },
        "event": {
            "type": "Property",
            "value": event_type
        },
        "expires": {
            "type": "Property",
            "value": expires.isoformat() + "Z"
        },
        "ignitionPoints": {
            "type": "GeoProperty",
            "value": {
                "type": "Point",
                "coordinates": [centroid.x, centroid.y]
            }
        },
        "msgType": {
            "type": "Property",
            "value": "SocialMediaAlert"
        },
        "sender": {
            "type": "Property",
            "value": "alert@tema-project.eu"
        },
        "sent": {
            "type": "Property",
            "value": effective.isoformat() + "Z"
        },
        "severity": {
            "type": "Property",
            "value": "Severe"
        },
        "urgency": {
            "type": "Property",
            "value": "Immediate"
        },
        "status": {
            "type": "Property",
            "value": "Exercise"
        },
        "location": {
            "type": "GeoProperty",
            "value": {
                "type": "Polygon",
                "coordinates": [list(union_polygon.exterior.coords)]
            }
        },
        "scope": {
            "type": "Property",
            "value": "Private"
        },
        "relevant_posts": {
            "type": "Property",
            "value": relevant_posts
        }
    }
    return alert
