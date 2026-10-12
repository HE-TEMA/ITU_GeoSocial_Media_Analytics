import os
import gc
import re
import sys
import time
import torch
import pandas as pd
import warnings
import datetime
import threading
import numpy as np
import geopandas as gpd
from numba.core.errors import NumbaDeprecationWarning, NumbaPendingDeprecationWarning
warnings.simplefilter('ignore', category=NumbaDeprecationWarning)
warnings.simplefilter('ignore', category=NumbaPendingDeprecationWarning)

from config import Config  # noqa
from shapely import wkt, Geometry  # noqa
from shapely.geometry import Point  # noqa
from umap import UMAP  # noqa
from nltk.corpus import stopwords  # noqa
from sklearn.feature_extraction.text import CountVectorizer  # noqa
from bertopic import BERTopic  # noqa
from transformers import pipeline  # noqa
from transformers.pipelines.pt_utils import KeyDataset  # noqa
from datasets import Dataset  # noqa
from sqlalchemy.orm import sessionmaker  # noqa
from sqlalchemy import func, or_  # noqa

from socialmedia.integration import social_media_integration  # noqa
from socialmedia.text_analysis import (   # noqa
    emotion_analysis_sentence_level,
    classify_disaster_relevance
)
from socialmedia.hotspot_analysis import hot_spot_analysis  # noqa
from socialmedia.geoparser import Geoparser  # noqa
from socialmedia.functions import clean_text_bert  # noqa
from helpers.utils import download_input_data  # noqa
from helpers.db import get_engine, insert_social_media_data, db_results_to_geodataframe, SocialMediaPost, get_live_mode  # noqa
logger = Config.LOGGER

# define constants
INPUT_PATH = Config.INPUT_PATH
TEMP_PATH = Config.TEMP_PATH
OUTPUT_PATH = Config.OUTPUT_PATH
EMOTION_MODEL = Config.EMOTION_MODEL
EMBEDDING_MODEL = Config.EMBEDDING_MODEL

# todo: social media paths
# TWITTER     = os.path.join(INPUT_PATH, Config.TWITTER)    if Config.TWITTER   else None  # noqa
# TELEGRAM    = os.path.join(INPUT_PATH, Config.TELEGRAM)   if Config.TELEGRAM  else None  # noqa
# MASTODON    = os.path.join(INPUT_PATH, Config.MASTODON)   if Config.MASTODON  else None  # noqa
# TIKTOK      = os.path.join(INPUT_PATH, Config.TIKTOK)     if Config.TIKTOK    else None  # noqa
# REDDIT      = os.path.join(INPUT_PATH, Config.REDDIT)     if Config.REDDIT    else None  # noqa
# YOUTUBE     = os.path.join(INPUT_PATH, Config.YOUTUBE)    if Config.YOUTUBE   else None  # noqa
# LIVE_DATA   = os.path.join(INPUT_PATH, Config.LIVE_DATA)  if Config.LIVE_DATA   else None  # noqa

# file paths
PATH_POSTS_INTEGRATED   = os.path.join(TEMP_PATH,   "social_media_integrated.csv")  # noqa
PATH_POSTS_GEOCODED     = os.path.join(TEMP_PATH,   "social_media_integrated_geocoded.csv")  # noqa

PATH_SINGLE_POSTS       = os.path.join(OUTPUT_PATH, "single_posts_results.geojson")  # noqa
PATH_HOTSPOT_RESULTS    = os.path.join(OUTPUT_PATH, "hotspot_results.geojson")  # noqa

# configuration values
DROP_NO_GEOMETRY = bool(Config.DROP_NO_GEOMETRY)
EMOTION_ANALYSIS_PROBABILITY_THRESHOLD = float(
    Config.EMOTION_ANALYSIS_PROBABILITY_THRESHOLD
)
H3_RESOLUTION = int(Config.H3_RESOLUTION)
KEYWORDS = Config.KEYWORDS.lower()
HOTSPOTS_DATE_RANGE = int(Config.HOTSPOTS_DATE_RANGE)
CRS = Config.CRS



def insert_input_data_into_db() -> dict:
    """
    Downloads all files from the Google Drive folder specified in Config.DATA_STORAGE,
    then reads data from "./temp/social_media_integrated.csv" and inserts it into the
    'social_media_posts' table in the database.

    Returns:
        dict: A dictionary containing the status and message of the operation.
    """
    # directories exist?
    if not os.path.exists(TEMP_PATH):
        os.makedirs(TEMP_PATH)

    if not os.path.exists(OUTPUT_PATH):
        os.makedirs(OUTPUT_PATH)

    try:
        # Todo: This is the deprecated integration from multiple CSV files but I am scared to delete it in case
        # there are unexpected requirements.
        # social_media_integration_task()  # takes data from "./data/input_data" -> "./temp/social_media_integrated.csv"
        # csv_path = os.path.join("temp", "social_media_integrated.csv")
        # if not os.path.exists(csv_path):
        #     raise Exception(f"CSV file not found at {csv_path}")
        # df = pd.read_csv(csv_path)

        # read data from GeoJSON and GPKG
        required_columns: list[str] = ["message_id", "date", "text", "photo_url", "geometry", "source"]
        dfs: list = []

        for file in os.listdir(INPUT_PATH):
            if file.endswith((".geojson", ".gpkg")):
                file_path = os.path.join(INPUT_PATH, file)
                logger.info(f"Reading file: {file_path}")
                gdf: gpd.GeoDataFrame = gpd.read_file(file_path, engine="pyogrio")

                # Check for required columns
                missing_cols = [col for col in required_columns if col not in gdf.columns]
                if len(missing_cols) > 0:
                    raise ValueError(f"Missing required columns in {file}: {missing_cols}")

                # Rename 'message_id' to 'id'
                gdf: gpd.GeoDataFrame = gdf.rename(columns={"message_id": "id"})

                # anonymise the text column
                gdf["text"] = gdf["text"].apply(clean_text_bert)

                dfs.append(gdf)

        if not dfs:
            raise FileNotFoundError("No valid GeoJSON/GeoPackage files found in './data/'")

        df = pd.concat(dfs, ignore_index=True)
        logger.info(f"Read {len(df)} rows from GeoJSON/GeoPackage files.")

        # Insert the data into the database.
        engine = get_engine(Config.DB_CONN_STRING)
        insert_social_media_data(df, engine)
        logger.info(f"Inserted {len(df)} social media posts into the database.")

        return {
            "status": "success",
            "message": "Files downloaded and social media data inserted successfully."
        }

    except Exception as e:
        logger.error(f"Error during database insert: {e}")
        return {"status": "failure", "message": str(e)}


def trigger_workflow_from_db(aoi_geom: Geometry = None, alert_date: datetime.datetime = None,
                             cutoff_date: datetime.datetime = None):
    """Trigger the entire analysis workflow, optionally with a filtering geometry.

    Args:
        aoi_geom (Geometry): Area of interest as a bounding box polygon for filtering.
        alert_date (datetime.datetime, optional): Sent date of the alert. Defaults to None.
        cutoff_date (datetime.datetime, optional): Cutoff date for the data. Defaults to None.
    """
    # Read data from the database instead of a CSV file.
    engine = get_engine(Config.DB_CONN_STRING)
    live_mode: bool = get_live_mode(engine=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        if aoi_geom is not None:
            query = session.query(SocialMediaPost)
            now = datetime.datetime.now()
            thirty_days_before_alert = alert_date - datetime.timedelta(days=30)

            # Filter posts from the last 30 days
            logger.info(f'Live mode: {live_mode}')
            if live_mode:
                query = query.filter(SocialMediaPost.date >= thirty_days_before_alert, SocialMediaPost.date <= now)
            elif cutoff_date is not None:
                query = query.filter(SocialMediaPost.date >= thirty_days_before_alert, SocialMediaPost.date <= cutoff_date)
                logger.info(f'Data filtered until <= {str(cutoff_date)}')
            else:
                query = query.filter(SocialMediaPost.date >= thirty_days_before_alert)

            # Filter posts that are within the area of interest.
            # We use ST_Within: only posts with geometry inside the aoi_geom are returned.
            query = query.filter(
                func.ST_Within(
                    SocialMediaPost.geometry,
                    func.ST_GeomFromText(aoi_geom.wkt, 4326)
                )
            )
            results = query.all()
        # If all fails, get the posts within the last thirty days
        else:
            now = datetime.datetime.now()
            thirty_days_ago = now - datetime.timedelta(days=30)
            if live_mode:
                results = session.query(SocialMediaPost).filter(
                    SocialMediaPost.date >= thirty_days_ago, SocialMediaPost.date <= now
                ).all()
            else:
                results = session.query(SocialMediaPost).filter(
                    SocialMediaPost.date >= thirty_days_ago
                ).all()
    except Exception as e:
        logger.error(f"Error querying social media posts: {e}")
        session.close()
        raise e
    session.close()

    if len(results) <= 0:
        raise Exception('0 posts found in the database.')

    # Convert query results into a GeoDataFrame.
    data = db_results_to_geodataframe(results)
    logger.info(f"Retrieved {len(data)} posts from the database.")

    # Compute spatial and temporal distance to the alert for each post and add as columns to the DataFrame
    if aoi_geom is not None and alert_date is not None:
        # Reproject to a metric CRS for accurate km distances
        data_metric = data["geometry"].to_crs(epsg=3857)
        aoi_geom_metric = gpd.GeoSeries([aoi_geom], crs=4326).to_crs(epsg=3857).iloc[0]
        
        data["distance_to_alert"] = data_metric.apply(lambda geom: geom.distance(aoi_geom_metric)) / 1000  # meters → km
        data["time_to_alert"] = data["date"].apply(lambda date: (date - alert_date).total_seconds() / 3600)

    # Run the entire workflow!
    topic_results = topic_modelling_task(data=data)  # takes the geocoded posts and runs the workflow
    emotion_results = text_analysis_task(data=topic_results)
    single_post_results, hotspot_results = hot_spot_analysis_task(data=emotion_results,
                                                                  relatedness_model=Config.RELATEDNESS_METHOD)
    export_results(single_post_results, hotspot_results)  # exports results to ./data/export_data"


# todo: def social_media_integration_task() -> None:
#     """Integrates social media data from various platforms into a single standardized file.

#     This function initiates the integration process by calling the `social_media_integration`
#     function with paths to data from multiple social media platforms (Twitter, Telegram, Mastodon,
#     TikTok, YouTube, Reddit). It also applies NSFW filtering and saves the integrated data to a
#     specified file path.

#     Args:
#         None

#     Returns:
#         None
#     """
#     # check if files exist
#     twitter_path: str = TWITTER if os.path.exists(TWITTER) else None
#     telegram_path: str = TELEGRAM if os.path.exists(TELEGRAM) else None
#     mastodon_path: str = MASTODON if os.path.exists(MASTODON) else None
#     tiktok_path: str = TIKTOK if os.path.exists(TIKTOK) else None
#     youtube_path: str = YOUTUBE if os.path.exists(YOUTUBE) else None
#     reddit_path: str = REDDIT if os.path.exists(REDDIT) else None
#     live_path: str = LIVE_DATA if os.path.exists(LIVE_DATA) else None

#     logger.info("Started social media integration.")
#     social_media_integration(
#         twitter_path,
#         telegram_path,
#         mastodon_path,
#         tiktok_path,
#         youtube_path,
#         reddit_path,
#         live_path,
#         nsfw_filtering=True,
#         file_path=PATH_POSTS_INTEGRATED,
#     )
#     logger.info("Finished social media integration.")
# todo: Also remove the entire integration file


def geoparsing_task(ner_model: str = 'xx_ent_wiki_sm') -> None:
    """Processes and geoparses social media data to add geographic coordinates.

    This function reads integrated social media data, separates entries with and without
    geographic coordinates, and uses a geoparser to add coordinates to entries without them.
    The processed data is then combined, cleaned, and saved to a specified file.

    Args:
        ner_model (str, optional): The SpaCy NER model to use for geoparsing.
                                        Defaults to 'xx_ent_wiki_sm'.

    Returns:
        None
    """
    logger.info(f"Started geoparsing with language (NER) model: {ner_model}.")
    data = pd.read_csv(PATH_POSTS_INTEGRATED)

    data_without_geometry = data[data["geometry"].isna()]
    data_with_geometry = data[data["geometry"].notna()]

    logger.info(f'Data without geometry: {data_without_geometry.shape[0]}')
    logger.info(f'Data with geometry: {data_with_geometry.shape[0]}')

    if data_without_geometry.empty:
        # if no data to geoparse, combine_data is just data_with_geometry
        combined_data = data_with_geometry.copy()
    else:
        # only geoparse from text data if there are rows without geometry
        geoparser = Geoparser(
            data_without_geometry,
            spacy_model=ner_model,
            text_column="text",
            output_path_geocode=PATH_POSTS_GEOCODED,  # TODO: how to handle SpaCy models?
        )
        logger.info("Set up geoparser.")
        data_without_geometry = geoparser.process_data()
        data_without_geometry["geocoded"] = True
        logger.info("Finished geoparsing.")
        data_without_geometry['geometry'] = data_without_geometry.apply(
            lambda row: Point(float(row["longitude"]), float(row["latitude"])) if row["longitude"] and row["latitude"] else None,
            axis=1
        )

        combined_data = pd.concat([data_with_geometry, data_without_geometry])
        logger.info("Combining Data.")

    # Filter out columns that do not exist in the dataframe
    existing_columns_to_drop = [
        col for col in [
            "edited_text",
            "GPE",
            "FAC",
            "LOC",
            "ORG",
            "locations",
            "latitude",
            "longitude",
        ]
        if col in combined_data.columns
    ]

    # Drop only the existing columns
    combined_data = combined_data.drop(columns=existing_columns_to_drop)

    if DROP_NO_GEOMETRY:
        combined_data = combined_data.dropna(subset="geometry")

    logger.info(f"Geoparsing to file: {combined_data.shape[0]} rows.")
    combined_data.to_csv(PATH_POSTS_GEOCODED, index=False)


def geoparse_posts_from_db_loop(ner_model: str = 'xx_ent_wiki_sm', sleep_interval: int = 300):
    """
    Continuously queries the database for SocialMediaPost rows without geometry
    and that haven't been attempted for geocoding, uses the Geoparser to geocode them,
    and updates the database with the new geometry and marks them as attempted.

    Args:
        ner_model (str): The SpaCy NER model to use for geoparsing.
        sleep_interval (int): Number of seconds to wait between iterations.
    """
    while True:
        logger.info("Starting background geoparsing iteration.")
        engine = get_engine(Config.DB_CONN_STRING)
        Session = sessionmaker(bind=engine)
        session = Session()
        try:
            # Query posts with no geometry and not yet attempted.
            posts_to_geocode = session.query(SocialMediaPost).filter(
                SocialMediaPost.geometry == None,  # noqa
                or_(SocialMediaPost.attempted_geocoding == None, SocialMediaPost.attempted_geocoding == False)  # noqa
            ).all()
            logger.info(f"Found {len(posts_to_geocode)} posts without geometry to geocode.")

            if posts_to_geocode:
                # Convert queried posts to a DataFrame.
                data = []
                for post in posts_to_geocode:
                    data.append({
                        "id": post.id,
                        "date": post.date,
                        "text": post.text,
                        "photo_url": post.photo_url,
                        "geometry": None,  # Placeholder
                        "caption": post.caption,
                        "source": post.source
                    })
                df_to_geocode = pd.DataFrame(data)

                # Initialize the Geoparser with the DataFrame.
                geoparser = Geoparser(
                    df_to_geocode,
                    spacy_model=ner_model,
                    text_column="text",
                    output_path_geocode=None  # No file output; we'll update the DB instead.
                )
                logger.info("Initialised geoparser for DB posts.")

                # Process the data to obtain geocoded results.
                geocoded_df = geoparser.process_data()
                geocoded_df["geocoded"] = True

                # Create a geometry from latitude and longitude if available.
                geocoded_df["geometry"] = geocoded_df.apply(
                    lambda row: Point(float(row["longitude"]), float(row["latitude"]))
                    if row.get("longitude") and row.get("latitude") else None,
                    axis=1
                )

                # Update each corresponding post in the database.
                for idx, row in geocoded_df.iterrows():
                    post = session.query(SocialMediaPost).filter_by(id=row["id"]).first()
                    if post:
                        if row["geometry"] is not None:
                            post.geometry = row["geometry"].wkt
                        # Mark that geocoding was attempted.
                        post.attempted_geocoding = True
                session.commit()
                logger.info(f"Database updated with {sum(geocoded_df['geometry'].notna())} geocoded posts.")
            else:
                logger.info("No new posts to geocode in this iteration.")
        except Exception as e:
            logger.error(f"Error during geoparsing posts from DB: {e}")
        finally:
            session.close()
            engine.dispose()

        logger.info(f"Sleeping for {sleep_interval} seconds before next geocoding iteration.")
        time.sleep(sleep_interval)


def start_background_geoparsing(ner_model: str = 'xx_ent_wiki_sm', sleep_interval: int = 300):
    """
    Starts the geoparsing loop in a background daemon thread.

    Args:
        ner_model (str): The SpaCy NER model to use.
        sleep_interval (int): Seconds to wait between iterations.
    """
    thread = threading.Thread(
        target=geoparse_posts_from_db_loop,
        kwargs={"ner_model": ner_model, "sleep_interval": sleep_interval}
    )
    thread.daemon = True  # Allows the thread to exit when the main program exits.
    thread.start()
    logger.info("Background geoparsing thread started.")


def topic_modelling_task(data: pd.DataFrame = None) -> pd.DataFrame:
    """Performs topic modeling on social media data and returns the processed DataFrame.

    This function reads geocoded social media data, preprocesses the text, and applies
    topic modeling using BERTopic. It then adds topic labels and keywords to the data
    and saves the processed data to a specified file.

    Args:
        data (pd.DataFrame): The data to process.

    Returns:
        pd.DataFrame: The DataFrame containing the original data along with topic labels
                      and keywords.
    """
    logger.info("Started topic modelling.")
    # read in the file in the specified format
    # data: pd.DataFrame = pd.read_csv(PATH_POSTS_GEOCODED)

    data['text'] = data['text'].astype(str)

    # edit geometry
    data = data.dropna(subset="geometry")
    # data["geometry"] = data["geometry"].apply(
    #     lambda x: wkt.loads(x)
    # )  # TODO: make format of geometry consistent (WKT, coordinates)
    # data = gpd.GeoDataFrame(data, geometry=data["geometry"], crs=4326).reset_index(
    #     drop=True
    # )
    # data["geometry"] = data["geometry"].centroid

    # build a stopword list
    stop_words: list[str] = (
        stopwords.words("english")
        + stopwords.words("german")
        + stopwords.words("dutch")
        + stopwords.words("french")
        + stopwords.words("spanish")
    )

    # text pre-processing
    processed_texts: list[str] = []
    for post_text in data["text"]:
        x: str = re.sub(r"@\w+", "@user", post_text)  # normalize users
        x = re.sub(r"https?://\S+|www\.\S+", "http", x)  # normalize links
        x = x.replace("\n", "")  # remove additional line breaks
        processed_texts.append(x)
    data["text_bert"] = processed_texts

    # for better keyword extraction and reproducability
    umap_model = UMAP(
        n_neighbors=15, n_components=5, min_dist=0.0, metric="cosine", random_state=42
    )
    vectorizer_model = CountVectorizer(stop_words=stop_words)

    # actual topic model application
    topic_model: BERTopic = BERTopic(
        umap_model=umap_model,
        # hdbscan_model=cluster_model,
        vectorizer_model=vectorizer_model,
        # representation_model=representation_model,
        embedding_model=EMBEDDING_MODEL,  # multilingual model
        verbose=False,
    )

    # extract and display topics
    topics, probs = topic_model.fit_transform(data["text_bert"])
    topic_info: pd.DataFrame = topic_model.get_topic_info().set_index("Topic")
    data["topic"] = topics
    data["keywords"] = data["topic"].apply(
        lambda x: topic_info.loc[x, "Representation"]
    )
    data["topic_label"] = data["keywords"].apply(lambda x: ', '.join(x))

    data.to_csv(PATH_POSTS_GEOCODED, index=False)

    logger.info("Finished topic modelling.")

    # free up space
    del topic_model
    gc.collect()
    torch.cuda.empty_cache()

    return data


def text_analysis_task(data: pd.DataFrame) -> pd.DataFrame:
    """Performs emotion analysis on social media data and returns the processed DataFrame.

    This function applies a multilingual emotion classifier to analyze the emotions expressed
    in the text data at the sentence level. The results are added to the DataFrame and returned.

    Args:
        data (pd.DataFrame): The DataFrame containing social media text data to be analyzed.

    Returns:
        pd.DataFrame: The DataFrame with emotion analysis results added.
    """
    # Emotion classification
    logger.info("Started emotion analysis.")
    data = emotion_analysis_sentence_level(
        data,
        classifier=EMOTION_MODEL,  # multilingual model
        probability_threshold=EMOTION_ANALYSIS_PROBABILITY_THRESHOLD,
    )
    logger.info("Finished emotion analysis.")

    # Relevance classification
    relevance_labels, relevance_probs, relevance_scores = classify_disaster_relevance(
        texts=data['text_bert']
    )
    data['relevance_label'] = relevance_labels
    data['relevance_label_probability'] = relevance_probs
    data['relevance_score_text'] = relevance_scores

    # decay parameters (tune these)
    SPATIAL_DECAY = 50   # halves every 60
    TEMPORAL_DECAY = 168  # halves every ~1 week

    # Non-linear decay weights (exponential decay)
    spatial_weight = np.exp(-data['distance_to_alert'] / SPATIAL_DECAY)
    temporal_weight = np.exp(-np.abs(data['time_to_alert']) / TEMPORAL_DECAY)

    # Combined multimodal relevance score
    data['relevance_score'] = data['relevance_score_text'] * spatial_weight * temporal_weight

    return data


def hot_spot_analysis_task(
        data: pd.DataFrame, relatedness_model: str = 'hannybal/disaster-twitter-xlm-roberta-al'
        ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Performs hot spot analysis on social media data and returns the results.

    This function analyzes the provided social media data to identify hot spots based on
    specified keywords. It processes the 'keywords' column to ensure it contains strings,
    creates a 'related' column indicating the relatedness of the post to the disaster, and then
    performs the hot spot analysis.

    Args:
        data (pd.DataFrame): The DataFrame containing social media data to be analyzed.
        relatedness_model (str): Which method should be used for relatedness classification.
            Defaults to our model 'hannybal/disaster-twitter-xlm-roberta-al'.

    Returns:
        pd.DataFrame: The enriched SinglePostResults and HotspotResults.
    """
    # hot spot analysis
    logger.info("Started hot spot analysis.")
    logger.debug(f"Data type of 'keywords' column: {data['keywords'].dtype}")

    element_types = data["keywords"].apply(type)
    logger.debug(element_types)
    data["keywords"] = data["keywords"].apply(
        lambda x: " ".join(x) if isinstance(x, object) else str(x)
    )

    element_types = data["keywords"].apply(type)
    logger.debug(element_types)

    if not all(isinstance(x, str) for x in data["keywords"]):
        logger.error("Data type conversion issue in 'keywords' column")

    # filter only the related posts
    if relatedness_model == 'hannybal/disaster-twitter-xlm-roberta-al':
        if "text_bert" not in data.columns:
            raise ValueError("Column 'text_bert' is not included in input dataframe.")

        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if device.type == 'cuda':
            classifier = pipeline(
                'text-classification',
                model='hannybal/disaster-twitter-xlm-roberta-al',
                tokenizer='cardiffnlp/twitter-xlm-roberta-base',
                add_special_tokens=True,
                padding='max_length',
                max_length=512,
                truncation='longest_first',
                return_all_scores=True,
                device=0
            )
        else:
            classifier = pipeline(
                'text-classification',
                model='hannybal/disaster-twitter-xlm-roberta-al',
                tokenizer='cardiffnlp/twitter-xlm-roberta-base',
                add_special_tokens=True,
                padding='max_length',
                max_length=512,
                truncation='longest_first',
                return_all_scores=True,
                device=-1
            )
        dataset = Dataset.from_dict({'text_bert': data['text_bert'].tolist()})

        related_labels = []
        num_samples = len(dataset)
        progress_step = num_samples // 20  # Calculate step for 5% progress updates

        for idx, out in enumerate(classifier(KeyDataset(dataset, 'text_bert'))):
            try:
                relatedness_label = max(out, key=lambda x: x["score"])["label"]
            except Exception:
                relatedness_label = None

            if relatedness_label == 'LABEL_1':
                related_labels.append(1)
            else:
                related_labels.append(0)

            # Print progress every 10% of the dataset
            if (idx + 1) % progress_step == 0 or (idx + 1) == num_samples:
                logger.info(f"Relatedness Classification: {round((idx + 1) / num_samples * 100)}% done.")

            # add list to df
        data["related"] = related_labels

        # free up space
        del classifier
        gc.collect()
        torch.cuda.empty_cache()

    elif relatedness_model == 'keywords':
        data["related"] = data["text"].str.lower().str.contains(KEYWORDS).astype(int)

    logger.info(f"Data type of 'related' column: {data['related'].dtype}")

    hotspot_results = hot_spot_analysis(
        data,
        h3_resolution=H3_RESOLUTION,
        date_range=HOTSPOTS_DATE_RANGE,
        handle_nan="fill",
        column="related",  # needs to be a boolean column!
    )
    logger.info("Finished hot spot analysis.")

    # Only filter for the disaster-related posts
    data = data[data["related"] == 1]

    # And return both results
    return data, hotspot_results


def export_results(singlepost_results: gpd.GeoDataFrame, hotspot_results: gpd.GeoDataFrame):
    """Exports the results of single post and hot spot analyses to GeoJSON files.

    This function takes the results of single post and hot spot analyses, ensures they are
    in the correct coordinate reference system (CRS), and exports them to specified GeoJSON
    files. It also logs the process and checks if the files were created successfully.

    Args:
        singlepost_results (GeoDataFrame): The GeoDataFrame containing the results of the
                                           single post analysis.
        hotspot_results (GeoDataFrame): The GeoDataFrame containing the results of the
                                        hot spot analysis.

    Returns:
        None
    """
    logger.info("Started exporting data.")

    if singlepost_results.crs != CRS:
        singlepost_results = singlepost_results.to_crs(CRS)
        logger.info("Converted singlepost_results to the correct CRS.")
    if hotspot_results.crs != CRS:
        hotspot_results = hotspot_results.to_crs(CRS)
        logger.info("Converted hotspot_results to the correct CRS.")

    hotspot_results.to_file(PATH_HOTSPOT_RESULTS, engine='pyogrio')  # Fiona sometimes get stuck here ...
    logger.info(f"Hotspot results exported to {PATH_HOTSPOT_RESULTS}.")

    singlepost_results.to_file(PATH_SINGLE_POSTS, engine='pyogrio')  # Fiona sometimes get stuck here ...
    logger.info(f"Singlepost results exported to {PATH_SINGLE_POSTS}.")

    if not os.path.exists(PATH_HOTSPOT_RESULTS):
        logger.error(f"Failed to create file {PATH_HOTSPOT_RESULTS}.")
    if not os.path.exists(PATH_SINGLE_POSTS):
        logger.error(f"Failed to create file {PATH_SINGLE_POSTS}.")

    logger.info("Finished exporting data.")
