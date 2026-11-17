from pathlib import Path

import pandas as pd
import pytest

import main
from src import MovieStorage
from src.storage import MOVIE_COLUMNS


def upload(client, content, filename="movies.csv"):
    return client.post("/upload", files={"file": (filename, content, "text/csv")})


# ---------------------------------------------------------------------------
# Required tests
# ---------------------------------------------------------------------------

def test_movies_persist(client, test_storage, uploaded_movies):
    reopened = MovieStorage(test_storage.db_path)

    df = reopened.get()
    assert reopened.count_movies() == 20
    assert len(df) == 20
    assert list(df.columns) == MOVIE_COLUMNS

    original_titles = pd.read_csv(Path("fixtures") / "sample_movies.csv")["title"].tolist()
    assert df["title"].tolist() == original_titles

    storage_ids = [row["id"] for row in reopened.select_columns(["title"], limit=100)]
    response = client.get("/movies", params={"limit": 100})
    assert response.status_code == 200
    api_ids = [row["id"] for row in response.json()["data"]]

    assert storage_ids == list(range(1, 21))
    assert api_ids == list(range(1, 21))


def test_cache_and_processing(client, uploaded_movies, processing_body):
    # 1. Two misses with different limits, each cached separately
    responses = {}
    for limit in (100, 1):
        response = client.get("/movies", params={"limit": limit})
        assert response.status_code == 200
        responses[limit] = response.json()
        assert main.cache.get(limit) == responses[limit]

    # 2. Repeat limit=100 -> same response (cache hit)
    repeat = client.get("/movies", params={"limit": 100})
    assert repeat.status_code == 200
    assert repeat.json() == responses[100]

    # 3. Processing replaces the data and clears the cache
    processed = client.post("/movies/process", json=processing_body)
    assert processed.status_code == 200
    body = processed.json()
    assert body["validation_rows_removed"] == 0
    assert body["cleaning_report"] == {
        "rows_before": 20,
        "rows_after": 16,
        "rows_removed": 4,
        "columns_before": 4,
        "columns_after": 4,
        "columns_removed": 0,
    }
    assert main.cache.get(100) is None
    assert main.cache.get(1) is None

    # 4. Fresh retrieval shows the 16 processed rows and is cached again
    after = client.get("/movies", params={"limit": 100})
    assert after.status_code == 200
    after_body = after.json()
    assert after_body["total_rows"] == 16
    assert [row["title"] for row in after_body["data"]] == [
        f"Movie {letter}" for letter in "ABCDEFGHIJKLMNOP"
    ]
    assert [row["id"] for row in after_body["data"]] == list(range(1, 17))
    assert main.cache.get(100) == after_body


def test_cache_hit_skips_database(client, uploaded_movies, monkeypatch):
    first = client.get("/movies", params={"limit": 100})
    assert first.status_code == 200
    first_body = first.json()

    def fail_if_called(*args, **kwargs):
        pytest.fail("Database was queried on a cache hit")

    monkeypatch.setattr(main.storage, "select_columns", fail_if_called)
    monkeypatch.setattr(main.storage, "count_movies", fail_if_called)

    second = client.get("/movies", params={"limit": 100})
    assert second.status_code == 200
    assert second.json() == first_body


# ---------------------------------------------------------------------------
# Optional tests
# ---------------------------------------------------------------------------

def test_health(client):
    assert client.get("/health").status_code == 200


def test_upload_response(uploaded_movies):
    assert uploaded_movies == {
        "filename": "sample_movies.csv",
        "rows": 20,
        "columns": 4,
        "column_names": MOVIE_COLUMNS,
    }


def test_unsupported_extension(client):
    response = client.post(
        "/upload", files={"file": ("movies.txt", b"a,b\n1,2", "text/plain")}
    )
    assert response.status_code == 400
    assert client.get("/health").status_code == 200


@pytest.mark.parametrize(
    "content",
    [b"title,year\nMovie X,2020\n", b"title,release_year,rating,genre\n"],
    ids=["wrong_columns", "no_data_rows"],
)
def test_bad_upload_keeps_stored_data(client, uploaded_movies, test_storage, content):
    response = upload(client, content)
    assert response.status_code == 400
    assert test_storage.count_movies() == 20


def test_missing_values_round_trip_as_null(client, test_storage):
    csv = (
        b"title,release_year,rating,genre\n"
        b"Movie X,2020,7.5,Drama\n"
        b"Movie Y,2021,,Comedy\n"
    )
    assert upload(client, csv).status_code == 200
    data = client.get("/movies").json()["data"]
    assert data[1]["rating"] is None

    body = {"processing": {"missing": {"enabled": True, "axis": "rows"}}}
    assert client.post("/movies/process", json=body).status_code == 200
    assert test_storage.count_movies() == 1


def test_reupload_replaces_rows_and_clears_cache(client, uploaded_movies, sample_csv_bytes):
    client.get("/movies", params={"limit": 100})
    assert main.cache.get(100) is not None

    assert upload(client, sample_csv_bytes, "sample_movies.csv").status_code == 200
    assert main.cache.get(100) is None

    body = client.get("/movies", params={"limit": 100}).json()
    assert body["total_rows"] == 20
    assert body["data"][0]["id"] == 1


def test_search_examples(client, uploaded_movies):
    default = client.get("/movies/search", params={"min_rating": 8.5, "limit": 2})
    assert default.json() == [{"id": 4, "title": "Movie C"}, {"id": 2, "title": "Movie B"}]

    selected = client.get(
        "/movies/search",
        params={"columns": ["title", "rating"], "min_rating": 8.5, "limit": 2},
    )
    assert selected.json() == [
        {"id": 4, "title": "Movie C", "rating": 9.0},
        {"id": 2, "title": "Movie B", "rating": 8.5},
    ]


def test_search_no_matches_returns_empty_list(client):
    response = client.get("/movies/search", params={"min_rating": 10})
    assert response.status_code == 200
    assert response.json() == []


def test_search_unsupported_column(client, uploaded_movies):
    assert client.get("/movies/search", params={"columns": "unknown"}).status_code == 400


@pytest.mark.parametrize("columns", [[], ["unknown"]])
def test_storage_rejects_bad_columns(test_storage, columns):
    with pytest.raises(ValueError):
        test_storage.select_columns(columns)
    with pytest.raises(ValueError):
        test_storage.query_movies(columns, 0, 10)


def test_failed_processing_preserves_database_and_cache(client, uploaded_movies, test_storage):
    cached = client.get("/movies", params={"limit": 100}).json()

    body = {"validation": {"numeric_columns": ["budget"]}}
    assert client.post("/movies/process", json=body).status_code == 400

    assert test_storage.count_movies() == 20
    assert main.cache.get(100) == cached


def test_empty_collection_returns_empty_response(client, test_storage):
    test_storage.save(pd.DataFrame(columns=MOVIE_COLUMNS))
    assert client.get("/movies").json() == {"data": [], "total_rows": 0}