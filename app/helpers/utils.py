"""Utility functions needed throughout the workflow.
"""
import gdown
from config import Config
logger = Config.LOGGER


def read_last_lines(file_path: str, num_lines: int = 10) -> list[str]:
    """
    Reads the last `num_lines` lines of a file.

    Args:
        file_path (str): The path to the file.
        num_lines (int, optional): The number of lines to read from the end of the file. Defaults to 10.

    Returns:
        list[str]: A list containing the last `num_lines` lines of the file.
            If the file is not found, returns a list with an error message.
    """
    try:
        with open(file_path, 'rb') as file:
            # Navigate to the end of the file
            file.seek(0, 2)
            file_size = file.tell()
            buffer_size = 1024
            data = b""
            # Read the file in reverse
            while file_size > 0 and len(data.splitlines()) <= num_lines:
                read_size = min(buffer_size, file_size)
                file.seek(file_size - read_size, 0)
                data = file.read(read_size) + data
                file_size -= read_size

            # Decode and get the last `num_lines` lines
            return data.decode(errors='ignore').splitlines()[-num_lines:]
    except FileNotFoundError:
        return ["Log file not found."]
    except Exception as e:
        return [f"An error occurred: {str(e)}"]


def download_input_data(drive_url: str, output_path: str) -> None:
    """Download input data from a specified Google Drive folder.

    This function uses the `gdown` library to download a folder from Google Drive and saves
    it to the specified input path defined in the configuration.

    Args:
        drive_url (str): The URL to the Google Drive folder.
        path (str): The path where to store the data.
    """
    gdown.download_folder(url=drive_url, output=output_path)
    logger.info(f'Downloaded data from {drive_url} to {output_path} ...')