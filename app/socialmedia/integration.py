import datetime
import pandas as pd
from shapely import wkb, wkt
import ast
import json
import re
from typing import Optional
from socialmedia.functions import get_nsfw_word_list, check_nsfw_words
from config import Config


def ExtractMastodonImageUrls(media: Optional[str]) -> Optional[str]:
    """
    Extracts valid image URLs from a Mastodon media JSON string.

    This function parses a string containing JSON-like malformed data (e.g., single quotes instead of double quotes)
    representing a list of media elements. It filters for items of type "image", checks for valid image file formats
    in the `remote_url` or `url` fields, and excludes forbidden formats (e.g., `.jpe`). The resulting valid URLs
    are returned as a JSON string.

    Args:
        media (Optional[str]): A string containing a JSON-like structure representing media elements,
                               or `None` if no media data is provided.

    Returns:
        Optional[str]: A JSON-formatted string containing the extracted image URLs, or `None` if no valid
                       URLs are found or the input is invalid.

    Examples:
        >>> media="[{'type': 'image', 'remote_url': 'https://example.com/image.png', 'url': 'https://example.com/fallback.png'}]"
        >>> ExtractMastodonImageUrls(media)
        '["https://example.com/image.png"]'

        >>> media="[{'type': 'image', 'remote_url': 'https://example.com/image.jpe', 'url': 'https://example.com/fallback.jpe'}]"
        >>> ExtractMastodonImageUrls(media)
        None

        >>> ExtractMastodonImageUrls(None)
        None
    """
    if media is None:
        return None
    try:
        # Safely evaluate the malformed JSON using `ast.literal_eval`
        parsed = ast.literal_eval(media)

        if not isinstance(parsed, list):
            return None

        # Compile regex patterns
        image_formats = re.compile(r'\.(png|jpg|jpeg|bmp|webp|tiff|svg)$', re.IGNORECASE)
        forbidden_formats = re.compile(r'\.jpe(?!g)$', re.IGNORECASE)

        # Extract URLs
        urls = [
            el['remote_url']    if el.get('remote_url')     and image_formats.search(el['remote_url'])  else  # noqa
            el['url']           if el.get('url')            and image_formats.search(el['url'])         else  # noqa
            None

            for el in parsed if

            el.get('type') == 'image'                           and  # noqa
            not forbidden_formats.search(el.get('remote_url'))  and  # noqa
            not forbidden_formats.search(el.get('url'))
        ]

        # Filter out `None` values
        urls = [url for url in urls if url is not None]

        if not urls:
            return None

        # Return as string-wrapped JSON
        return json.dumps(urls)

    except (ValueError, SyntaxError, KeyError, TypeError):
        return None


def social_media_integration(
    twitter:    str = None,
    telegram:   str = None,
    mastodon:   str = None,
    tiktok:     str = None,
    youtube:    str = None,
    reddit:     str = None,
    live_data:  str = None,
    nsfw_filtering:     bool = True,
    file_path:  str = None,
):
    """A function to merge and standardise input files from different platforms into one singular file.

    Args:
        twitter (str, optional): Path to Twitter data. Defaults to None.
        telegram (str, optional): Path to Telegram data. Defaults to None.
        mastodon (str, optional): Path to Mastodon data. Defaults to None.
        tiktok (str, optional): Path to TikTok data. Defaults to None.
        youtube (str, optional): Path to YouTube data. Defaults to None.
        reddit (str, optional): Path to Reddit data. Defaults to None.
        live_data (str, optional): Path to real-time data. Defaults to None.
        nsfw_filtering (bool, optional): Apply NSFW keyword filtering. Defaults to True.
        file_path (str, optional): Path to output file. Defaults to None.

    Returns:
        merged, standardised dataframe
    """

    social_media_dfs = []

    if twitter:
        try:
            twitter_data = pd.read_csv(twitter, sep=",")
        except Exception:
            twitter_data = pd.read_csv(twitter, sep="\t")

        try:
            twitter_data = twitter_data[["message_id", "date", "text", "photo_url", "geom"]]
        except Exception:
            twitter_data = twitter_data[["message_id", "date", "text", "geom"]]
            twitter_data["photo_url"] = None

        twitter_data = twitter_data.rename(columns={"message_id": "id", "geom": "geometry"})

        twitter_data["date"] = pd.to_datetime(twitter_data["date"], format="mixed")

        try:
            twitter_data["geometry"] = twitter_data["geometry"].apply(
                lambda x: wkb.loads(x, hex=True)
            )
        except Exception:
            twitter_data["geometry"] = twitter_data["geometry"].apply(
                lambda x: wkt.loads(x)
            )

        twitter_data["source"] = "twitter"

        social_media_dfs.append(twitter_data)

    if telegram:
        telegram_data = pd.read_csv(telegram, sep=",")
        telegram_data["channel_id"] = telegram_data["peer_id"].apply(
            lambda x: (
                ast.literal_eval(x)["channel_id"]
                if pd.notnull(x)
                else "channel_unknown"
            )
        )
        telegram_data["unique_id"] = (
            telegram_data["channel_id"].astype(str)
            + "_"
            + telegram_data["id"].astype(str)
        )  # create a unique ID for each Telegram message
        telegram_data = telegram_data[["unique_id", "date", "message"]].rename(
            columns={"unique_id": "id", "message": "text"}
        )
        telegram_data["date"] = pd.to_datetime(telegram_data["date"])
        telegram_data["geometry"] = None
        telegram_data["source"] = "telegram"
        social_media_dfs.append(telegram_data)

    if mastodon:
        mastodon_data = pd.read_csv(mastodon, sep=",")
        mastodon_data = mastodon_data[["id", "created_at", "content", "media_attachments"]].rename(
            columns={"created_at": "date", "content": "text", "media_attachments": "photo_url"}
        )

        mastodon_data["date"] = pd.to_datetime(mastodon_data["date"])
        mastodon_data["geometry"] = None
        mastodon_data["photo_url"] = mastodon_data["photo_url"].apply(ExtractMastodonImageUrls)
        mastodon_data["source"] = "mastodon"
        social_media_dfs.append(mastodon_data)

    if tiktok:
        tiktok_data = pd.read_csv(tiktok, sep=",")
        tiktok_data = tiktok_data[["id", "create_time", "video_description"]].rename(
            columns={"create_time": "date", "video_description": "text"}
        )
        tiktok_data["date"] = pd.to_datetime(
            tiktok_data["date"].apply(
                lambda x: datetime.datetime.fromtimestamp(x).strftime("%c")
            )
        )
        tiktok_data["geometry"] = None
        tiktok_data["photo_url"] = None
        tiktok_data["source"] = "tiktok"
        social_media_dfs.append(tiktok_data)

    if youtube:
        youtube_data = pd.read_csv(youtube, sep=",")
        youtube_data = youtube_data[["video_id", "date", "description"]].rename(
            columns={"video_id": "id", "description": "text"}
        )
        youtube_data["date"] = pd.to_datetime(youtube_data["date"])
        youtube_data["geometry"] = None
        youtube_data["photo_url"] = None
        youtube_data["source"] = "youtube"
        social_media_dfs.append(youtube_data)

    if reddit:
        reddit_data = pd.read_csv(reddit, sep=",")
        reddit_data = reddit_data[["id", "created_utc", "selftext"]].rename(
            columns={"created_utc": "date", "selftext": "text"}
        )
        reddit_data["date"] = pd.to_datetime(
            reddit_data["date"].apply(
                lambda x: datetime.datetime.fromtimestamp(x).strftime("%c")
            )
        )

        reddit_data["geometry"] = None
        reddit_data["photo_url"] = None
        reddit_data["source"] = "reddit"
        social_media_dfs.append(reddit_data)

    # also append the live data
    if live_data:
        Config.LIVE_LOCK.acquire()
        live_df: pd.DataFrame = pd.read_csv(live_data)
        Config.LIVE_LOCK.release()

        try:
            live_df = live_df[["message_id", "date", "text", "photo_url", "geom"]]
        except Exception:
            live_df = live_df[["message_id", "date", "text", "geom"]]
            live_df["photo_url"] = None

        live_df = live_df.rename(columns={"message_id": "id", "geom": "geometry"})

        live_df["date"] = pd.to_datetime(live_df["date"], format='mixed')

        def parse_geometry(x: str) -> str | None:
            try:
                return wkt.loads(x)
            except Exception:
                return None

        live_df["geometry"] = live_df["geometry"].apply(lambda x: parse_geometry(x))
        social_media_dfs.append(live_df)

    # concatenate existing dataframes
    social_media_data = pd.concat(social_media_dfs).reset_index(drop=True)

    # NSFW filtering
    if nsfw_filtering:
        social_media_data["nsfw"] = (
            social_media_data["text"]
            .astype(str)
            .apply(check_nsfw_words, nsfw_words=get_nsfw_word_list("english"))
        )

    # harmonise dates
    social_media_data['date'] = social_media_data['date'].apply(
        lambda x: pd.to_datetime(x).tz_localize(None).strftime('%Y-%m-%d %H:%M:%S.%f')
    )

    if file_path:
        social_media_data.to_csv(file_path, sep=",", index=None)

    return social_media_data
