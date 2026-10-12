# syntax=docker/dockerfile:1
# build with: docker build -t tfa-11 .
# run with: docker run -p 5007:5007 --gpus all tfa-11

FROM python:3.11

# install requirements for python
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# download models
ENV EMOTION_MODEL='MilaNLProc/xlm-emo-t'
ENV EMBEDDING_MODEL='intfloat/multilingual-e5-small'
RUN python -m spacy download de_core_news_lg
RUN python -m spacy download en_core_web_lg
RUN python -m spacy download xx_ent_wiki_sm
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBEDDING_MODEL}')"
RUN python -c "from transformers import AutoModel; AutoModel.from_pretrained('${EMOTION_MODEL}')"
RUN python -c "from transformers import AutoModel; AutoModel.from_pretrained('hannybal/disaster-twitter-xlm-roberta-al')"
RUN python -c "from transformers import AutoModel; AutoModel.from_pretrained('hannybal/multilingual-disaster-relevance-twhin-bert')"
RUN python -c "import nltk; nltk.download('punkt')"
RUN python -c "import nltk; nltk.download('stopwords')"
RUN python -c "import nltk; nltk.download('averaged_perceptron_tagger')"

# Set timezone to Europe/Vienna (CEST/CET)
ENV TZ=Europe/Vienna

# Install Spatialite library
RUN apt-get update && \
    apt-get install -y libsqlite3-mod-spatialite && \
    rm -rf /var/lib/apt/lists/*

# Install ngrok
# RUN apt-get update && \
#     apt-get install -y --no-install-recommends curl gnupg && \
#     curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok.asc | tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && \
#     echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | tee /etc/apt/sources.list.d/ngrok.list && \
#     apt-get update && \
#     apt-get install -y --no-install-recommends ngrok && \
#     apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy the necessary files into the working directory (/app) in the container
# This includes all files from the current directory (where the Dockerfile is located) into the /app directory within the container
COPY . /component

# Set the working directory to /app. This directory will be the default location for any subsequent commands executed within the container
WORKDIR /component

# Environment variables must be defined here 
# Define mandatory environment variables without default values
# These variables are essential for the proper functioning of the application, and users must provide values for them during container runtime

# The port on which the application will listen for incoming connections
ENV PORT='5007'
ENV MINIO_ENDPOINT='xxxxxxxxxx'
ENV MINIO_ACCESS_KEY='xxxxxxxxxx'
ENV MINIO_SECRET_KEY='xxxxxxxxxx'
ENV MINIO_BUCKET_NAME='xxxxxxxxxx'
ENV SECRET_KEY="xxxxxxxxxx"
ENV ORION_LD_ENDPOINT="xxxxxxxxxx"
ENV ORION_BASE_URL="xxxxxxxxxx/ngsi-ld/v1/entities"
ENV DB_CONN_STRING="sqlite:////component/data.db"
ENV UPDATE_INTERVAL_MIN=1440
ENV AOI_BUFFER_M=20000

# If the data is too granular for the resolution, there will be an error
ENV H3_RESOLUTION=7

ENV BROKER_TYPE_ID="Alert"
ENV PARTNER_ID="PLUS-tfa11"
ENV BROKER_SUBSCRIPTION_ID="xxxxxxxxxx"
ENV PUBLIC_IP_ADDRESS="xxxxxxxxxx"
ENV BASE_PATH="/tfa11"

# Disaster-relatedness keywords
ENV KEYWORDS='flood|fire|hochwasser|überschwemmung|flut|regen|regn|wasser|einsatz|katastrophe|rain|feuer|brand|alluvione|incendio|inundación|tulva|tulipalo|maastopalo|incend|fumo|brucia|burn|fire|emergen|pompier|elicotter|fiamm|evac|λάσπης|βοηθ|πλημμύ|πυρκαγι|καταιγίδ|πλημμύρα|metsäpalo'
# ENV RELATEDNESS_METHOD='hannybal/disaster-twitter-xlm-roberta-al'
ENV RELATEDNESS_METHOD='keywords'

# Live mode only considers the data up to now; if False, data from the future is also considered
ENV LIVE_MODE="False"

# Define environment variables with default values
# These variables are optional, and if not provided, the default values will be used

# Whether debugging mode is enabled. Default is set to False
ENV DEBUG=false        

EXPOSE ${PORT}
    
# run startup.sh script to launch ngrok and the app.py 
COPY startup.sh startup.sh
RUN chmod +x startup.sh

ENTRYPOINT ["./startup.sh"]