from fastapi.testclient import TestClient

from logsift.web import create_app

PASTE = """\
{"timestamp":"2025-03-10T09:00:01Z","level":"info","message":"started"}
{"timestamp":"2025-03-10T09:00:02Z","level":"error","message":"timeout contacting inventory","status":500}
{"timestamp":"2025-03-10T09:00:03Z","level":"error","message":"timeout contacting inventory","status":500}
{"timestamp":"2025-03-10T09:00:04Z","level":"warn","message":"slow query"}
"""


def client(seed: bool = False) -> TestClient:
    return TestClient(create_app(":memory:", seed=seed))


def test_index_shows_seeded_sample():
    response = client(seed=True).get("/")
    assert response.status_code == 200
    assert "Sample API logs" in response.text


def test_upload_paste_then_view_dataset():
    c = client()
    created = c.post("/datasets", data={"name": "mine", "content": PASTE}, follow_redirects=False)
    assert created.status_code == 303
    page = c.get(created.headers["location"])
    assert page.status_code == 200
    assert "mine" in page.text
    # the dashboard surfaces the collapsed error template and level badges
    assert "timeout contacting inventory" in page.text


def test_empty_upload_is_rejected():
    bad = client().post("/datasets", data={"name": "x", "content": "   "})
    assert bad.status_code == 400
    assert "Paste some log lines" in bad.text


def test_level_filter_narrows_the_summary_json():
    c = client()
    c.post("/datasets", data={"name": "mine", "content": PASTE})
    full = c.get("/datasets/1.json").json()
    assert full["total"] == 4

    errors = c.get("/datasets/1.json", params={"level": "error"}).json()
    assert errors["total"] == 2
    assert errors["by_level"] == {"ERROR": 2}


def test_text_and_field_filters():
    c = client()
    c.post("/datasets", data={"name": "mine", "content": PASTE})
    by_text = c.get("/datasets/1.json", params={"q": "timeout"}).json()
    assert by_text["total"] == 2
    by_field = c.get("/datasets/1.json", params={"where": "status=500"}).json()
    assert by_field["total"] == 2


def test_bad_regex_is_a_400():
    c = client()
    c.post("/datasets", data={"name": "mine", "content": PASTE})
    assert c.get("/datasets/1", params={"q": "("}).status_code == 400
    assert c.get("/datasets/1.json", params={"q": "("}).status_code == 400


def test_unknown_dataset_is_404():
    assert client().get("/datasets/999").status_code == 404
    assert client().get("/datasets/999.json").status_code == 404
