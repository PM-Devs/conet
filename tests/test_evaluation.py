import pytest

from conet.observability.evaluation import log_evaluation_run

mlflow = pytest.importorskip('mlflow', reason="mlflow is an optional dependency: pip install 'conet[eval]'")


@pytest.fixture
def tracking_uri(tmp_path):
    return f'sqlite:///{tmp_path / "mlflow.db"}'


def test_log_evaluation_run_records_metrics_and_params(tracking_uri):
    run = log_evaluation_run(
        experiment_name='conet-eval-test',
        metrics={'accuracy': 0.92, 'latency_ms': 340.0},
        params={'model': 'invoice-checker-v2'},
        tracking_uri=tracking_uri,
    )
    assert run.run_id
    assert run.tracking_uri == tracking_uri

    client = mlflow.tracking.MlflowClient(tracking_uri=tracking_uri)
    fetched = client.get_run(run.run_id)
    assert fetched.data.metrics['accuracy'] == 0.92
    assert fetched.data.params['model'] == 'invoice-checker-v2'


def test_evaluation_run_ui_url_uses_the_mlflow_ui_server_address_not_the_backend_store(tracking_uri):
    run = log_evaluation_run(
        experiment_name='conet-eval-test', metrics={'accuracy': 1.0},
        tracking_uri=tracking_uri, ui_base_url='http://localhost:5000',
    )
    assert run.ui_url == f'http://localhost:5000/#/experiments/{run.experiment_id}/runs/{run.run_id}'
    assert 'sqlite' not in run.ui_url


def test_log_evaluation_run_uses_default_tracking_and_ui_urls_when_none_given(monkeypatch, tmp_path):
    monkeypatch.delenv('CONET_MLFLOW_TRACKING_URI', raising=False)
    monkeypatch.delenv('CONET_MLFLOW_UI_URL', raising=False)
    monkeypatch.chdir(tmp_path)
    run = log_evaluation_run(experiment_name='conet-eval-default', metrics={'accuracy': 0.5})
    assert run.tracking_uri == 'sqlite:///mlflow.db'
    assert run.ui_base_url == 'http://localhost:5000'
