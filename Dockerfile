# Image Airflow 3 + paquet `gazette`.
ARG AIRFLOW_VERSION=3.3.2
ARG PYTHON_VERSION=3.12

FROM apache/airflow:${AIRFLOW_VERSION}-python${PYTHON_VERSION}

ARG AIRFLOW_VERSION
ARG PYTHON_VERSION

# Dépendances runtime installées avec le fichier de contraintes officiel :
# on réinstalle explicitement la même version d'Airflow pour que pip ne la change pas.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir \
      "apache-airflow==${AIRFLOW_VERSION}" \
      -r /tmp/requirements.txt \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt"

# Notre paquet (sans dépendances : elles sont déjà installées ci-dessus).
COPY --chown=airflow:0 pyproject.toml README.md /opt/gazette/
COPY --chown=airflow:0 src /opt/gazette/src
RUN pip install --no-cache-dir --no-deps /opt/gazette

WORKDIR /opt/gazette
