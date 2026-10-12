import libpysal
import pandas as pd
import numpy as np
import h3pandas  # noqa
import geopandas as gpd
from shapely.geometry import Polygon
from esda import G_Local
from config import Config
import warnings
logger = Config.LOGGER


def create_hexagon(
    gdf: gpd.GeoDataFrame,
    extent: gpd.GeoDataFrame,
    h3_resolution: int = 4,
    reference_system: int = 4326,
    name_count: str = None,
):
    """A function that counts the number of points within h3 grid

    Args:
        gdf (gpd.GeoDataFrame): GeoDataFrame, e.g. Tweets
        extent (gpd.GeoDataFrame): points of bounding box as geodataframe.
        h3_resolution (int, optional): h3 scale (see https://h3geo.org/docs/core-library/restable/). Defaults to 4.
        reference_system (int, optional): EPSG code. Defaults to 4326.
        name_count (str, optional): name of count column. Default to None.

    Returns:
        grid (GeoDataFrame): hexagonal grid
    """

    # create hexagons
    grid = gdf.assign(count=1).h3.geo_to_h3_aggregate(h3_resolution)

    # convert count to integer
    grid["count"] = grid["count"].astype(int)

    # set crs
    grid = grid.set_crs(reference_system)

    # only select geometry and count column (i.e. how many tweets are there per grid cell)
    grid = grid[["count", "geometry"]].reset_index()

    # rename h3 column
    if h3_resolution < 10:
        grid = grid.rename(columns={f"h3_0{h3_resolution}": "h3"})
    else:
        grid = grid.rename(columns={f"h3_{h3_resolution}": "h3"})

    # get h3 grid for entire extent
    h3_grid_extent = extent.h3.polyfill_resample(h3_resolution).reset_index()
    h3_grid_extent = h3_grid_extent.rename(columns={"h3_polyfill": "h3"})

    grid = pd.merge(h3_grid_extent, grid, how="left")
    grid = grid.fillna(0)

    if name_count:
        grid = grid.rename(columns={"count": name_count})

    return grid


def add_GI_bin(data, z_score="z_score", p_value="p_value"):
    "A function that adds a GI_bin column"

    # sort into bins
    conditions = [
        (data[z_score] <= -2.58) & (data[p_value] <= 0.01),
        (data[z_score] <= -1.96)
        & (data[z_score] > -2.58)
        & (data[p_value] <= 0.05)
        & (data[p_value] > 0.01),
        (data[z_score] <= -1.65)
        & (data[z_score] > -1.96)
        & (data[p_value] <= 0.1)
        & (data[p_value] > 0.05),
        (data[z_score] >= 1.65)
        & (data[z_score] < 1.96)
        & (data[p_value] <= 0.1)
        & (data[p_value] > 0.05),
        (data[z_score] >= 1.96)
        & (data[z_score] < 2.58)
        & (data[p_value] <= 0.05)
        & (data[p_value] > 0.01),
        (data[z_score] >= 2.58) & (data[p_value] <= 0.01),
    ]
    values = [-3, -2, -1, 1, 2, 3]

    data["bin"] = np.select(conditions, values, default=0)

    return data


def hot_spot_analysis(
    data: gpd.GeoDataFrame,
    h3_resolution: int = 4,
    date_range: int = 1,
    handle_nan: str = "fill",
    column: str = "prediction",
):
    """Spatial hot spot analysis on H3 grid.

    Args:
        data (gpd.GeoDataFrame): Classified singular Tweets.
        h3_resolution (int, optional): H3 class. Defaults to 4.
        date_range (int, optional): number of days for moving window. Defaults to 1.
        handle_nan (str, optional): either "fill" or "drop" NaN values. Defaults to "fill".
        column (str, optional): column name of classification output. Defaults to 'prediction'.

    Returns:
        pd.DataFrame: hotspots
    """

    # prepare date column
    data["date"] = pd.to_datetime(data["date"], format='mixed')

    # day range
    day_range = pd.Timedelta(days=date_range)

    # get list of days
    unique_days = sorted(data["date"].dt.date.unique())

    grids = []

    # reproject
    data = data.to_crs(4326)

    # get extent of dataframe
    minx, miny, maxx, maxy = data.total_bounds
    extent_points = [
        (minx, miny),
        (maxx, miny),
        (maxx, maxy),
        (minx, maxy),
        (minx, miny),
    ]

    # Create a GeoDataFrame
    extent_points = gpd.GeoDataFrame(geometry=[Polygon(extent_points)], crs=4326)

    for day in unique_days:

        # convert to np dateformat
        day = np.datetime64(day)

        # get date range
        start_date = day - day_range
        end_date = day + 1

        # filter data within date range
        data_filtered = data[data["date"].between(start_date, end_date)].drop(
            columns="date"
        )

        # calculate h3 grid with count for all tweets
        h3_grid_all = create_hexagon(
            data_filtered, extent=extent_points, h3_resolution=h3_resolution
        )

        # calculate h3 grid with count for related tweets
        if len(data_filtered[data_filtered[column] == 1]) <= 0:
            h3_grid = h3_grid_all.copy()
            h3_grid["ratio"] = 0
            h3_grid = h3_grid.drop(columns="index")

        else:
            # calculate h3 grid with count for related tweets
            h3_grid_related = create_hexagon(
                data_filtered[data_filtered[column] == 1],
                extent=extent_points,
                h3_resolution=h3_resolution,
                name_count="count_related",
            )

            # merge grids
            h3_grid = pd.merge(
                h3_grid_all, h3_grid_related[["h3", "count_related"]], on="h3"
            )

            # calculate ratio
            h3_grid["ratio"] = h3_grid["count_related"] / h3_grid["count"]
            h3_grid = h3_grid.drop(columns="index")

            if handle_nan == "fill":
                h3_grid = h3_grid.fillna(0)
            if handle_nan == "drop":
                h3_grid = h3_grid.dropna()

        # create spatial weights matrix
        w = libpysal.weights.Queen.from_dataframe(h3_grid)

        try:
            with warnings.catch_warnings(action="ignore"):
                # G_Local instance for Getis-Ord Gi* analysis
                lg_star = G_Local(
                    y=h3_grid["ratio"], w=w, transform="B", star=True, n_jobs=1
                )

            # add values to dataframe
            h3_grid["z_score"] = lg_star.Zs
            h3_grid["p_value"] = lg_star.p_sim

            # add GI_bin
            h3_grid = add_GI_bin(h3_grid)

        except Exception:
            logger.info(f"Did not work for {day}!")

            # add values to dataframe
            h3_grid["z_score"] = None
            h3_grid["p_value"] = None
            h3_grid["bin"] = 0

        h3_grid["date"] = day
        grids.append(h3_grid)

    output = pd.concat(grids)
    return output
