"""Vérifie le DAG dans l'image Airflow (lancé par la CI, hors pytest : Airflow n'est pas
installé dans l'environnement de test local).

    docker compose run --rm --no-deps -T -v "$PWD/tests:/opt/gazette/tests:ro" \
        --entrypoint python airflow-scheduler /opt/gazette/tests/check_dag.py
"""

import sys
import warnings

from airflow.dag_processing.dagbag import DagBag

warnings.simplefilter("error", DeprecationWarning)

bag = DagBag(dag_folder="/opt/airflow/dags")
if bag.import_errors:
    sys.exit(f"Erreurs d'import : {bag.import_errors}")

dag = bag.get_dag("gazette_daily")
assert dag is not None, "DAG gazette_daily introuvable"
assert dag.catchup is False
assert dag.max_active_runs == 1
assert dag.timetable.summary == "0 5 * * *"
assert str(dag.timezone) in ("Europe/Paris", "Timezone('Europe/Paris')")

tasks = {t.task_id: t for t in dag.tasks}
assert set(tasks) == {"collection_date", "list_sources", "collect", "write_manifest"}
assert tasks["collect"].partial_kwargs["retries"] == 2
assert tasks["write_manifest"].trigger_rule == "all_done"
print("DAG gazette_daily OK :", sorted(tasks))
