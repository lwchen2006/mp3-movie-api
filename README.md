# MP3 Movie API

This FastAPI application stores a movie collection in SQLite, queries it with SQL, cleans it with the MP1 data pipeline, and caches repeated retrievals. `POST /upload` reads a CSV, checks that it has exactly the columns `title`, `release_year`, `rating`, and `genre` and at least one row, and replaces the stored collection in a single transaction. `src/database.py` creates the connection and the `movies` table, and `src/storage.py` (`MovieStorage`) handles saving, retrieval, counting, and filtered search with SQL. `GET /movies` returns stored rows with a total count, and `GET /movies/search` filters by minimum rating and returns only the requested columns. `POST /movies/process` loads the collection, runs validation, duplicate removal, missing-value handling, and outlier removal from `data_pipeline`, and saves the result back to SQLite. `src/cache.py` (`MovieCache`) caches `GET /movies` responses in FakeRedis for 90 seconds, keyed by `limit`. Every successful upload or processing run clears the cache, so the next retrieval always reflects the current database, while the data itself persists across server restarts in `data/movies.db`.

## Running

Start the API from the repository root:

```bash
python -m uvicorn main:app --reload
```

Then open http://127.0.0.1:8000/docs.

Run the tests:

```bash
python -m pytest tests -v
```

## Sample processing request

After uploading `fixtures/sample_movies.csv`, send `POST /movies/process` with:

```json
{
  "validation": {
    "required_columns": ["title", "release_year", "rating", "genre"],
    "numeric_columns": ["release_year", "rating"]
  },
  "processing": {
    "remove_duplicates": true,
    "missing": {"enabled": true, "axis": "rows"},
    "outliers": {"enabled": true, "columns": ["rating"], "method": "iqr", "threshold": 1.5}
  }
}
```

Expected response:

```json
{
  "validation_rows_removed": 0,
  "cleaning_report": {
    "rows_before": 20,
    "rows_after": 16,
    "rows_removed": 4,
    "columns_before": 4,
    "columns_after": 4,
    "columns_removed": 0
  }
}
```

A following `GET /movies?limit=100` returns `total_rows: 16`.