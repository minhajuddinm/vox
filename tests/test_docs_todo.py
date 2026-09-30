import importlib.util
import os

SPEC = importlib.util.spec_from_file_location(
    "docs_todo", os.path.join(os.path.dirname(__file__), "..", "documentation", "tools", "docs_todo.py"))
docs_todo = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(docs_todo)


def pages(*paths, status="M"):
    return docs_todo.pages_for([(status, p) for p in paths])


def test_engine_change_points_at_the_windows_page():
    assert "documentation/04-windows-app.md" in pages("windows/engine.py")


def test_core_change_points_at_pipeline_and_config_pages():
    got = pages("windows/vox_core.py")
    assert "documentation/06-pipeline.md" in got and "documentation/07-config-and-data.md" in got


def test_java_helper_change_points_at_the_android_page_and_pipeline_for_groqclient():
    got = pages("android/src/com/minhaj/vox/GroqClient.java")
    assert "documentation/05-android-app.md" in got and "documentation/06-pipeline.md" in got


def test_prefs_change_points_at_the_config_page():
    assert "documentation/07-config-and-data.md" in pages("android/src/com/minhaj/vox/Prefs.java")


def test_workflow_and_tests_point_at_the_build_page():
    got = pages(".github/workflows/build.yml", "tests/test_x.py")
    assert list(got) == ["documentation/10-build-test-release.md"]


def test_documentation_files_do_not_ask_for_more_documentation():
    assert pages("documentation/04-windows-app.md", "CHANGELOG.md", "AGENTS.md") == {}


def test_unknown_files_map_to_nothing():
    assert pages("README.md") == {}


def test_every_rule_targets_a_real_page():
    root = os.path.join(os.path.dirname(__file__), "..")
    for _pattern, page, _why in docs_todo.RULES:
        assert os.path.exists(os.path.join(root, page)), page
    for page, _why in docs_todo.ALWAYS:
        assert os.path.exists(os.path.join(root, page)), page
