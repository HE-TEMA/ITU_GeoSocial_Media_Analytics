import datetime
import pandas as pd
from sqlalchemy import create_engine, Column, String, DateTime, Text, event, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from geoalchemy2 import Geometry, shape
import geopandas as gpd
from config import Config
logger = Config.LOGGER

# Base class for declarative class definitions.
Base = declarative_base()


class AppSetting(Base):
    """
    SQLAlchemy ORM model for application-wide settings.
    Stores key-value pairs; mainly used for live_mode.
    """
    __tablename__ = 'app_settings'

    name = Column(String, primary_key=True)
    value = Column(Boolean, nullable=False)


class SocialMediaPost(Base):
    """
    SQLAlchemy ORM model representing a social media post.
    """
    __tablename__ = 'social_media_posts'  # No schema since SQLite doesn't support it.

    # Using string type for id to accommodate various identifier formats.
    id = Column(String, primary_key=True)
    date = Column(DateTime, nullable=False)
    text = Column(Text, nullable=False)
    photo_url = Column(Text, nullable=True)
    # Geometry column for spatial data in SpatiaLite.
    geometry = Column(Geometry(geometry_type='GEOMETRY', srid=4326), nullable=True)
    caption = Column(Text, nullable=True)
    source = Column(String, nullable=False)
    attempted_geocoding = Column(Boolean, nullable=True)


class Alert(Base):
    """
    SQLAlchemy ORM model representing an alert.
    """
    __tablename__ = 'alerts'

    bm_id = Column(Text, primary_key=True)
    id = Column(String, nullable=True)
    alert_type = Column(String, nullable=True)
    sender = Column(String, nullable=True)
    sent = Column(DateTime, nullable=True)
    status = Column(String, nullable=True)
    msg_type = Column(String, nullable=True)
    scope = Column(String, nullable=True)
    category = Column(String, nullable=True)
    event = Column(String, nullable=True)
    urgency = Column(String, nullable=True)
    severity = Column(String, nullable=True)
    certainty = Column(String, nullable=True)
    effective = Column(DateTime, nullable=True)
    date_cutoff = Column(DateTime, nullable=True)
    expires = Column(DateTime, nullable=True)
    area_desc = Column(Text, nullable=True)
    geometry = Column(Geometry(geometry_type='GEOMETRY', srid=4326), nullable=True)
    singlepost_id = Column(Text, nullable=True)
    hotspot_id = Column(Text, nullable=True)


def get_engine(db_path: str):
    """
    Creates and returns a SQLAlchemy engine for a local SpatiaLite database.

    Args:
        db_path (str): The connection string for SQLite.
                       For example: 'sqlite:///my_spatialite.db'

    Returns:
        engine: SQLAlchemy engine instance.
    """
    engine = create_engine(db_path, echo=False)

    # Load the SpatiaLite extension when a connection is created.
    @event.listens_for(engine, "connect")
    def load_spatialite(dbapi_connection, connection_record):
        dbapi_connection.enable_load_extension(True)
        try:
            dbapi_connection.load_extension("mod_spatialite")
        except Exception as e:
            print("Could not load SpatiaLite extension:", e)

    return engine


def init_db(engine):
    """
    Initializes the database by creating all tables defined by the ORM models
    if they do not already exist.

    Args:
        engine: SQLAlchemy engine.
    """
    Base.metadata.create_all(engine)


def insert_social_media_data(df: pd.DataFrame, engine):
    """
    Inserts or updates social media data from a pandas DataFrame into the database.

    The DataFrame is expected to have at least the following columns:
        - id, date, text, photo_url, geometry, caption, and source.

    The geometry column should contain either a shapely geometry or a valid WKT string.
    This function converts shapely geometries to WKT before storing.

    Args:
        df (pd.DataFrame): DataFrame containing the integrated social media data.
        engine: SQLAlchemy engine.
    """
    Session = sessionmaker(bind=engine)
    session = Session()

    for index, row in df.iterrows():
        # Convert geometry to WKT if it is a shapely geometry.
        geom_wkt = None
        if pd.notnull(row.get('geometry')):
            if hasattr(row['geometry'], 'wkt'):
                geom_wkt = row['geometry'].wkt
            else:
                # Assume the geometry is already a valid WKT string.
                geom_wkt = row['geometry']

        # Create or update the SocialMediaPost instance.
        post = SocialMediaPost(
            id=str(row['id']),
            date=pd.to_datetime(row['date']),
            text=row['text'],
            photo_url=row.get('photo_url'),
            geometry=geom_wkt,
            caption=row.get('caption'),
            source=row['source']
        )
        session.merge(post)
    session.commit()

    # Log total number of posts in the database.
    total_posts = session.query(SocialMediaPost).count()
    logger.info(f"Total posts in the database: {total_posts}")

    session.close()


def db_results_to_geodataframe(results):
    """
    Converts a list of SocialMediaPost instances to a GeoDataFrame.

    Returns:
        gpd.GeoDataFrame: GeoDataFrame containing the social media posts with a proper geometry column.
    """
    data = []
    for post in results:
        data.append({
            "id": post.id,
            "date": post.date,
            "text": post.text,
            "photo_url": post.photo_url,
            "caption": post.caption,
            "geometry": shape.to_shape(post.geometry) if post.geometry else None,
            "source": post.source
        })

    data = gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:4326")
    data["geometry"] = data["geometry"].centroid
    data = data.reset_index(drop=True)
    return data


def get_live_mode(engine) -> bool:
    """
    Retrieves the 'live_mode' setting from the database.
    If not present, initializes it to False.
    """
    Session = sessionmaker(bind=engine)
    session = Session()

    setting = session.query(AppSetting).filter_by(name='live_mode').first()
    if setting is None:
        setting = AppSetting(name='live_mode', value=False)
        session.add(setting)
        session.commit()
    session.close()
    return setting.value


def set_live_mode(engine, mode: bool):
    """
    Sets the 'live_mode' setting in the database to the given boolean value.
    """
    Session = sessionmaker(bind=engine)
    session = Session()

    setting = session.query(AppSetting).filter_by(name='live_mode').first()
    if setting is None:
        setting = AppSetting(name='live_mode', value=mode)
        session.add(setting)
    else:
        setting.value = mode
    session.commit()
    session.close()


def set_alert_cutoff_date(engine, bm_id: str, cutoff_date: datetime.datetime) -> bool:
    """Update the cutoff date for the analysis.

    Args:
        engine: Database engine.
        bm_id (str): Business-mission id to update.
        cutoff_date (datetime.datetime): New cutoff date.

    Returns:
        bool. Whether the update was successful
    """
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        alert = session.query(Alert).filter_by(bm_id=bm_id).first()
        if alert is None:
            logger.error(f'Could not update cutoff date for alert. bm_id {bm_id} does not exist.')
            return False
        else:
            alert.date_cutoff = cutoff_date
            session.commit()
            logger.info(f"Updated alert '{bm_id}' with new cutoff date {cutoff_date}.")
            return True

    except Exception as e:
        logger.error(f"Error setting cutoff date for alert '{bm_id}': {e}")
        session.rollback()
        return False
    finally:
        session.close()
