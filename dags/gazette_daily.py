"""DAG quotidien de La gazette : collecte des flux RSS à 5 h (heure de Paris).

Aucune logique métier ici : le DAG appelle les fonctions du paquet `gazette`,
qui fonctionnent aussi sans Airflow (`python -m gazette collect`).
"""

from __future__ import annotations

from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import CronTriggerTimetable

PARIS = pendulum.timezone("Europe/Paris")


@dag(
    dag_id="gazette_daily",
    description="Collecte quotidienne des flux RSS PQR/PQN et stockage brut",
    schedule=CronTriggerTimetable("0 5 * * *", timezone=PARIS),
    start_date=pendulum.datetime(2026, 10, 1, tz=PARIS),
    catchup=False,
    max_active_runs=1,
    default_args={"owner": "la-gazette"},
    tags=["gazette", "collecte"],
)
def gazette_daily():
    @task
    def collection_date() -> str:
        """Jour de collecte = date (heure de Paris) à laquelle le run est prévu."""
        run_after = get_current_context()["dag_run"].run_after
        return pendulum.instance(run_after).in_timezone(PARIS).date().isoformat()

    @task
    def list_sources() -> list[str]:
        from gazette.collect.runner import collectable_sources, default_repository
        from gazette.config import load_settings

        repo = default_repository(load_settings())
        return [source.slug for source in collectable_sources(repo)]

    @task(
        retries=2,
        retry_delay=timedelta(minutes=2),
        retry_exponential_backoff=True,
        map_index_template="{{ source_slug }}",
    )
    def collect(slug: str, day: str) -> dict:
        from gazette.collect.fetcher import PoliteFetcher
        from gazette.collect.runner import collect_source, default_repository
        from gazette.config import load_settings

        context = get_current_context()
        context["source_slug"] = slug  # libellé de la tâche mappée dans l'UI
        ti = context["ti"]
        # L'échec n'est compté en base qu'au dernier essai (try_number commence à 1).
        final_attempt = ti.try_number > ti.max_tries
        settings = load_settings()
        repo = default_repository(settings)
        source = repo.get(slug)
        with PoliteFetcher(settings) as fetcher:
            result = collect_source(
                source,
                settings,
                date.fromisoformat(day),
                fetcher,
                repo,
                final_attempt=final_attempt,
            )
        if result.status == "failed":
            # Erreur permanente (robots.txt, 404, flux illisible) : inutile de relancer.
            if not result.retryable:
                raise AirflowFailException(result.error)
            raise RuntimeError(result.error)
        return result.model_dump()

    @task(trigger_rule="all_done")
    def write_manifest(day: str) -> dict:
        """S'exécute même si des sources ont échoué : elles apparaissent en échec."""
        from gazette.collect.runner import default_repository, finalize
        from gazette.config import load_settings

        settings = load_settings()
        return finalize(settings, date.fromisoformat(day), default_repository(settings))["summary"]

    day = collection_date()
    collected = collect.partial(day=day).expand(slug=list_sources())
    collected >> write_manifest(day)


gazette_daily()
