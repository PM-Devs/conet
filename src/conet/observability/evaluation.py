import logging
import os
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class EvaluationRun:
    run_id: str
    experiment_id: str
    tracking_uri: str
    ui_base_url: str

    @property
    def ui_url(self) -> str:
        """The link the dashboard shows — MLflow's own UI (`mlflow ui` / `mlflow
        server`), a separate process from this one regardless of backend store;
        never embedded (Feature Plan F13: "linked from the dashboard, not
        embedded")."""
        return f'{self.ui_base_url.rstrip("/")}/#/experiments/{self.experiment_id}/runs/{self.run_id}'


def log_evaluation_run(
    experiment_name: str,
    metrics: dict[str, float],
    params: dict[str, Any] | None = None,
    tracking_uri: str | None = None,
    ui_base_url: str | None = None,
) -> EvaluationRun:
    """Records one offline agent/model evaluation run in MLflow.

    F13 is explicitly independent of the live path — this is a thin
    recording call a caller makes with whatever metrics it computed; it is
    never invoked automatically by Discovery/Router/SkillServer. mlflow is
    an optional dependency (`pip install 'conet[eval]'`); importing it here
    rather than at module load keeps the rest of the package usable
    without it installed.
    """
    try:
        import mlflow
    except ImportError as exc:
        raise RuntimeError(
            "mlflow is not installed — it's an optional dependency: pip install 'conet[eval]'",
        ) from exc

    # MLflow's plain filesystem backend ('file:./mlruns') is in maintenance
    # mode as of the installed version and rejects new runs by default;
    # sqlite is the backend MLflow itself now recommends.
    resolved_tracking_uri = tracking_uri or os.environ.get('CONET_MLFLOW_TRACKING_URI', 'sqlite:///mlflow.db')
    resolved_ui_base_url = ui_base_url or os.environ.get('CONET_MLFLOW_UI_URL', 'http://localhost:5000')

    mlflow.set_tracking_uri(resolved_tracking_uri)
    experiment = mlflow.set_experiment(experiment_name)

    with mlflow.start_run() as run:
        if params:
            mlflow.log_params(params)
        mlflow.log_metrics(metrics)
        run_id = run.info.run_id

    logger.info('evaluation run %s recorded in experiment %s', run_id, experiment_name)
    return EvaluationRun(
        run_id=run_id, experiment_id=experiment.experiment_id,
        tracking_uri=resolved_tracking_uri, ui_base_url=resolved_ui_base_url,
    )
