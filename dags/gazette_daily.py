"""DAG de La gazette : collecte des flux RSS toutes les 30 minutes (heure de Paris).

    collecte (une tâche par source) -> manifeste -> nettoyage -> contrôles qualité -> purge

Le nom `gazette_daily` est conservé (historique des runs) ; les fichiers bruts
restent organisés par jour de collecte, chaque passage y fusionne ses articles, puis
le nettoyage du jour est rejoué en entier (idempotent).

Aucune logique métier ici : le DAG appelle les fonctions du paquet `gazette`,
qui fonctionnent aussi sans Airflow (`python -m gazette collect|clean|check|purge`).
"""

from __future__ import annotations

from datetime import date, timedelta

import pendulum
from airflow.sdk import dag, get_current_context, task
from airflow.sdk.exceptions import AirflowFailException
from airflow.timetables.trigger import CronTriggerTimetable

PARIS = pendulum.timezone("Europe/Paris")
# Pool à 1 place, créé par airflow-init : une seule tâche de collecte à la fois,
# donc jamais deux requêtes en parallèle vers un même éditeur (8 flux, < 1 min).
COLLECT_POOL = "gazette_editeurs"


def _paris_date(context) -> str:
    """Jour de collecte = date (heure de Paris) à laquelle le run est prévu."""
    run_after = context["dag_run"].run_after
    return pendulum.instance(run_after).in_timezone(PARIS).date().isoformat()


@dag(
    dag_id="gazette_daily",
    description="Collecte PQR/PQN toutes les 30 min, stockage brut, nettoyage et contrôles",
    schedule=CronTriggerTimetable("*/30 * * * *", timezone=PARIS),
    start_date=pendulum.datetime(2026, 10, 1, tz=PARIS),
    catchup=False,
    max_active_runs=1,
    # Un run ne doit jamais chevaucher le suivant.
    dagrun_timeout=timedelta(minutes=25),
    # Relances pour toutes les tâches : une tâche tuée avant son démarrage (API
    # d'exécution lente au démarrage de la pile) est relancée au lieu d'échouer.
    default_args={"owner": "la-gazette", "retries": 2, "retry_delay": timedelta(minutes=1)},
    tags=["gazette", "collecte", "qualite"],
)
def gazette_daily():
    @task
    def collection_date() -> str:
        return _paris_date(get_current_context())

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
        pool=COLLECT_POOL,
        map_index_template="{{ source_slug }}",
    )
    def collect(slug: str, day: str) -> dict:
        from gazette.collect.fetcher import PoliteFetcher
        from gazette.collect.runner import collect_source, default_index, default_repository
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
                default_index(settings),
                final_attempt=final_attempt,
            )
        if result.status == "failed":
            # Erreur permanente (robots.txt, 404, flux illisible) : inutile de relancer.
            if not result.retryable:
                raise AirflowFailException(result.error)
            raise RuntimeError(result.error)
        return result.model_dump()

    @task(trigger_rule="all_done")
    def write_manifest(day: str | None) -> dict:
        """S'exécute même si des sources ont échoué : elles apparaissent en échec.
        Si `collection_date` a elle-même échoué, `day` vaut None : on recalcule la date."""
        from gazette.collect.runner import default_repository, finalize
        from gazette.config import load_settings

        day = day or _paris_date(get_current_context())
        settings = load_settings()
        return finalize(settings, date.fromisoformat(day), default_repository(settings))["summary"]

    @task(trigger_rule="all_done")
    def clean(day: str | None) -> dict:
        """Brut du jour -> articles_propres + rejets + rapport de qualité (TD 4).

        S'exécute même si des sources ont échoué : on nettoie ce qui a été collecté.
        """
        from gazette.clean.pipeline import run_cleaning
        from gazette.config import load_settings

        day = day or _paris_date(get_current_context())
        report = run_cleaning(load_settings(), date.fromisoformat(day))
        return {k: report[k] for k in ("jour", "entres", "gardes", "rejetes", "taux_rejet")}

    @task
    def quality_checks(day: str | None) -> dict:
        """Vérifie les faits sur les données du jour, publie les métriques, alerte.

        Le résumé part en XCom (visible dans l'UI). Un contrôle critique en échec fait
        échouer la tâche, après l'envoi de l'alerte (une seule fois par jour et par échec).
        """
        from gazette.clean.quality import run_quality_checks
        from gazette.config import load_settings

        day = day or _paris_date(get_current_context())
        summary = run_quality_checks(load_settings(), date.fromisoformat(day))
        if summary["critiques_en_echec"]:
            raise AirflowFailException(
                f"Contrôles critiques en échec : {summary['critiques_en_echec']}"
            )
        return {k: summary[k] for k in ("ok", "avertissements", "alerte_envoyee")}

    @task(trigger_rule="all_done")
    def purge() -> dict:
        """Rétention : brut > 90 j, rejets > 30 j… (quality.RETENTION, docs/donnees.md § 7)."""
        from gazette.clean.quality import run_purge
        from gazette.config import load_settings

        return run_purge(load_settings())

    day = collection_date()
    collected = collect.partial(day=day).expand(slug=list_sources())
    manifest = write_manifest(day)
    collected >> manifest >> clean(day) >> quality_checks(day) >> purge()


gazette_daily()
