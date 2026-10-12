import gc
import torch
import pandas as pd
from datasets import Dataset
from transformers import pipeline, AutoModelForSequenceClassification, AutoTokenizer
from transformers.pipelines.pt_utils import KeyDataset
# from tqdm import tqdm
from config import Config
logger = Config.LOGGER


def emotion_analysis_sentence_level(
    data: pd.DataFrame,
    classifier: str = "MilaNLProc/xlm-emo-t",
    probability_threshold: float = None,
    batch_size: int = 16,
):
    """A function that performs sentence-level emotion analysis on a social media dataset.

    Args:
        data (pd.DataFrame): dataframe containg social media data and a "text" column
        classifier (str, optional): name of huggingface emotion analysis model. Defaults to "MilaNLProc/xlm-emo-t".
        probability_threshold (float, optional): filter classification based on p_value (0 to 1). Defaults to None.
        batch_size (int, optional): Batch size for inference. Defaults to 16.

    Returns:
        pd.DataFrame: added columns
    """

    if "text_bert" not in data.columns:
        raise ValueError("Column 'text_bert' is not included in input dataframe.")
    # check if GPU is available
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    dataset = Dataset.from_dict({'text_bert': data['text_bert'].tolist()})
    if device.type == 'cuda':
        logger.info(f'Started emotion classification on device: {device}')
        classifier = pipeline(
            "text-classification", model=classifier, return_all_scores=True,
            batch_size=batch_size, device=0, max_length=512, padding='max_length', truncation=True
        )
    else:
        logger.info('Started emotion classification on device: cpu')
        classifier = pipeline(
            "text-classification", model=classifier, return_all_scores=True,
            batch_size=batch_size, device=-1, max_length=512, padding='max_length', truncation=True
        )


    num_samples = len(dataset)
    progress_step = num_samples // 20  # Calculate step for 5% progress updates
    emotions = []
    emotions_probability = []

    for idx, out in enumerate(classifier(KeyDataset(dataset, 'text_bert'))):
        try:
            most_likely_emotion = max(out, key=lambda x: x["score"])["label"]
            emotion_likelihood = max(out, key=lambda x: x["score"])["score"]
        except Exception:
            most_likely_emotion, emotion_likelihood = None, None

        if probability_threshold and emotion_likelihood < probability_threshold:
            most_likely_emotion = None
            emotion_likelihood = None

        emotions.append(most_likely_emotion)
        emotions_probability.append(emotion_likelihood)

        # Print progress every 10% of the dataset
        if (idx + 1) % progress_step == 0 or (idx + 1) == num_samples:
            logger.info(f"Emotion Analysis: {round((idx + 1) / num_samples * 100)}% done.")

    # add list to df
    data["emotion_label"] = emotions
    data["emotion_label_probability"] = emotions_probability

    # free up space
    del classifier
    gc.collect()
    torch.cuda.empty_cache()

    return data


def classify_disaster_relevance(texts: list[str], 
                                model_path: str = 'hannybal/multilingual-disaster-relevance-twhin-bert', 
                                batch_size: int = 8,
                                max_length: int = 512) -> list[int]:
    """
    Classify a list of texts as disaster-related or not.

    Args:
        texts (List[str]): List of preprocessed text strings.
        model_path (str): Path to the trained model for classification. Defaults to our HuggingFace model.
        batch_size (int): Number of samples per batch through the pipeline.
        max_length (int): Max token length for truncation/padding.

    Returns:
        List[int]: Relevance labels for each input text.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    model = AutoModelForSequenceClassification.from_pretrained(
        model_path,
        attn_implementation="eager"  # or "sdpa", "flash_attention_2"
    )

    model = model.to(device)          # ✅ move to GPU
    model.eval()


    pipe = pipeline(
        'text-classification',
        model=model,
        tokenizer=tokenizer,
        add_special_tokens=True,
        padding='max_length',
        truncation='longest_first',
        max_length=max_length,
        device=device,
        batch_size=batch_size,
        return_all_scores=True,
    )

    dataset = Dataset.from_dict({'text': texts})

    num_samples = len(dataset)
    progress_step = num_samples // 20  # Calculate step for 5% progress updates
    relevance_labels = []
    relevance_probs = []
    relevance_scores = []

    for idx, output in enumerate(pipe(KeyDataset(dataset, 'text'))):
        try:
            most_likely_class = max(output, key=lambda x: x["score"])["label"]
            softmax_prob = max(output, key=lambda x: x["score"])["score"]

            # Convert output list to dict {label: score}
            probs = {x["label"]: x["score"] for x in output}
            p_not = probs.get("Not related", 0)
            p_related_not_relevant = probs.get("Related but not relevant", 0)
            p_related_relevant = probs.get("Related and relevant", 0)
            # weighted score
            score = 0 * p_not + 0.5 * p_related_not_relevant + 1 * p_related_relevant

        except Exception:
            most_likely_class, softmax_prob, score = None, None

        relevance_labels.append(most_likely_class)
        relevance_probs.append(softmax_prob)
        relevance_scores.append(score)

        # Print progress every 10% of the dataset
        if (idx + 1) % progress_step == 0 or (idx + 1) == num_samples:
            logger.info(f"Relevance classification: {round((idx + 1) / num_samples * 100)}% done.")

    # free up space
    del pipe
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return relevance_labels, relevance_probs, relevance_scores