from pathlib import Path

from streamlit.testing.v1 import AppTest

from tests.test_end_to_end import stack


def test_dashboard_comparison_cannot_mix_scenarios_or_drivers(stack, monkeypatch):
    engine, runner, manager = stack
    monkeypatch.setenv("ENGINE_URL", engine)
    # These completed empty runs are UI fixtures, not recorded demo results.
    runs = []
    for mode, driver, scenario in [
        ("unprotected_test", "enforcement", "recipient"),
        ("protected", "enforcement", "recipient"),
        ("protected", "enforcement", "approval"),
        ("protected", "otari", "recipient"),
    ]:
        run = manager.start(mode, driver=driver, scenario=scenario)
        manager.end(run.run_id)
        runs.append(run)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "dashboard/app.py", default_timeout=15
    ).run()
    assert not app.exception
    app.radio(key="view_mode").set_value("Run & inspect").run()
    baseline = next(
        box for box in app.selectbox if str(box.key).startswith("baseline_choice_")
    )
    protected = next(
        box for box in app.selectbox if str(box.key).startswith("protected_choice_")
    )
    assert baseline.value == str(runs[0].run_id)
    assert protected.value == str(runs[1].run_id)
    # There is no Otari baseline. An enforcement baseline must not be substituted.
    app.radio(key="comparison_driver").set_value("otari").run()
    assert not app.exception
    assert not any(str(box.key).startswith("baseline_choice_") for box in app.selectbox)
    assert any("Complete both" in element.value for element in app.info)
    app.radio(key="comparison_driver").set_value("enforcement").run()
    app.selectbox(key="comparison_scenario").set_value("approval").run()
    assert not app.exception
    assert not any(str(box.key).startswith("baseline_choice_") for box in app.selectbox)


def test_dashboard_displays_capability_audit_and_model_workflow_controls(
    stack, monkeypatch
):
    engine, runner, manager = stack
    monkeypatch.setenv("ENGINE_URL", engine)
    run = manager.start("protected", driver="otari", capability_profile=True)
    manager.end(run.run_id)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "dashboard/app.py", default_timeout=15
    ).run()
    assert not app.exception
    assert any(
        "Technical evidence self-audit" in element.value for element in app.markdown
    )
    app.radio(key="view_mode").set_value("Run & inspect").run()
    app.radio(key="run_type").set_value("Otari agent").run()
    assert not app.exception
    assert any(
        "Use Otari research" in item.label and item.value for item in app.checkbox
    )


def test_audience_overview_labels_evidence_and_opens_workspace(stack, monkeypatch):
    engine, runner, manager = stack
    monkeypatch.setenv("ENGINE_URL", engine)
    run = manager.start("protected", driver="enforcement")
    manager.end(run.run_id)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "dashboard/app.py", default_timeout=15
    ).run()
    assert not app.exception
    assert app.radio(key="view_mode").value == "Audience overview"
    app.selectbox(key="active_run").set_value(str(run.run_id)).run()
    assert {metric.label: metric.value for metric in app.metric}[
        "Otari features demonstrated"
    ] == "0"
    assert any("local enforcement only" in item.value for item in app.info)
    next(
        item for item in app.button if item.label == "Open live workspace"
    ).click().run()
    assert not app.exception
    assert app.radio(key="view_mode").value == "Run & inspect"
    assert any(item.label == "Start run" for item in app.button)
