import time
import spacy
import pandas as pd
from geopy.geocoders import Nominatim
from socialmedia.functions import edit_text
from config import Config
logger = Config.LOGGER


class Geoparser:
    def __init__(
        self,
        df,
        spacy_model,
        text_column,
        output_path_geocode=None,
    ):
        self.df = df
        self.text_column = text_column
        self.output_path_geocode = output_path_geocode
        self.geolocator = Nominatim(user_agent="geo_coder")
        self.nlp = spacy.load(spacy_model)

    def preprocess_data(self):
        # Edit text
        self.df["edited_text"] = self.df[self.text_column].apply(
            lambda x: edit_text(
                str(x),
                stoplist=[],
                min_length_word=0,
                remove_links=True,
                remove_user_names=True,
                remove_emojis=True,
                letters_only=True,
            )
        )

    def extract_locations(self, text):
        # Function to extract location entities from text using spaCy
        doc = self.nlp(text)
        entities = doc.ents
        locations = []
        locations1 = []
        locations2 = []
        locations3 = []
        for entity in entities:
            if entity.label_ == "GPE":
                locations.append(entity.text)
            if entity.label_ == "FAC":
                locations1.append(entity.text)
            if entity.label_ == "LOC":
                locations2.append(entity.text)
            if entity.label_ == "ORG":
                locations3.append(entity.text)
        return pd.Series(
            (locations, locations1, locations2, locations3),
            index=["GPE", "FAC", "LOC", "ORG"],
        )

    def combine_values(self, row):
        # Helper function to combine location values into a single string
        order = ["FAC", "LOC", "GPE", "ORG"]
        combined_values = [row[col] for col in order if row[col]]
        return ", ".join(combined_values)

    def process_data(self):
        self.preprocess_data()

        # Extract location
        self.df[["GPE", "FAC", "LOC", "ORG"]] = self.df["edited_text"].apply(
            self.extract_locations
        )
        logger.info("LER finished")
        # Remove brackets from location columns
        columns_to_clean = ["GPE", "FAC", "LOC", "ORG"]
        for col in columns_to_clean:
            self.df[col] = (
                self.df[col].astype(str).str.replace("[", "").str.replace("]", "")
            )

        # Combine location values into a new 'locations' column, remove '
        self.df["locations"] = self.df.apply(self.combine_values, axis=1)
        self.df["locations"] = self.df["locations"].str.replace("'", "")

        # Geocoding
        dict_for_locs = {}
        self.df["latitude"] = None
        self.df["longitude"] = None
        unique_loc = 0
        total_rows = 0
        row_with_loc = 0
        total_requests = 0
        for index, row in self.df.iterrows():
            total_rows += 1
            if total_rows % 100 == 0:
                logger.info(
                    f"Total rows: {total_rows}; Rows with loc: {row_with_loc}; Total requests: {total_requests}; "
                    f"Unique locs: {unique_loc}"
                )
            if total_rows % 1000 == 0:
                self.df.to_csv(f"result{total_rows}.csv")
            loc = row["locations"]
            if loc and loc != "nan":
                row_with_loc += 1
                if loc in dict_for_locs:
                    self.df.loc[index, "latitude"] = dict_for_locs[loc][0]
                    self.df.loc[index, "longitude"] = dict_for_locs[loc][1]
                else:

                    try:
                        geocoded_loc = self.geolocator.geocode(loc, timeout=200)
                        total_requests += 1
                        if geocoded_loc:
                            self.df.loc[index, "latitude"] = geocoded_loc.latitude
                            self.df.loc[index, "longitude"] = geocoded_loc.longitude
                            dict_for_locs[loc] = [
                                geocoded_loc.latitude,
                                geocoded_loc.longitude,
                            ]
                            unique_loc += 1
                        else:
                            dict_for_locs[loc] = ["", ""]
                    except Exception as e:
                        logger.info(f"Error geocoding location '{loc}': {str(e)}.")
                        self.df.loc[index, "latitude"] = "error"
                        self.df.loc[index, "longitude"] = "error"
                        time.sleep(2)

        # Save processed data to CSV
        if self.output_path_geocode:
            self.df.to_csv(self.output_path_geocode, index=False)

        return self.df
