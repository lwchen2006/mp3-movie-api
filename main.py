import io
import logging
import sqlite3
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

from data_pipeline import create_cleaning_report, process_data, validate_dataframe
from src import (
    HealthResponse,
    MovieDataResponse,
    MovieProcessingReport,
    MovieStorage,
    MovieUploadInfo,
    ProcessConfig,
    log_requests,
    setup_logging,
)
from src.storage import MOVIE_COLUMNS

setup_logging(verbose=True)
logger = logging.getLogger(__name__)

app = FastAPI(title="Movie API", version="1.0.0")
app.middleware("http")(log_requests)
storage = MovieStorage()


@app.get("/health", response_model=HealthResponse)
def health_check():
    """Return the application status and version."""
    return {"status": "ok", "version": "1.0.0"}


@app.post("/upload", response_model=MovieUploadInfo)
async def upload_movies(file: UploadFile = File(...)):
    """Read a CSV upload and replace the stored movie collection."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix != ".csv":
        logger.warning("Unsupported file format: %s", suffix)
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {suffix or 'none'}. Upload a .csv file.",
        )

    contents = await file.read()
    try:
        df = pd.read_csv(io.BytesIO(contents))
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError) as error:
        logger.warning("Could not read CSV %s: %s", file.filename, error)
        raise HTTPException(
            status_code=400, detail=f"Could not read CSV file: {error}"
        )

    if set(df.columns) != set(MOVIE_COLUMNS) or len(df.columns) != len(MOVIE_COLUMNS):
        logger.warning("Unexpected columns in %s: %s", file.filename, list(df.columns))
        raise HTTPException(
            status_code=400,
            detail=f"CSV must have exactly these columns: {MOVIE_COLUMNS}",
        )
    if df.empty:
        logger.warning("CSV has no data rows: %s", file.filename)
        raise HTTPException(
            status_code=400, detail="CSV must contain at least one data row"
        )

    df = df[MOVIE_COLUMNS]

    try:
        storage.save(df)
    except sqlite3.Error:
        raise HTTPException(status_code=500, detail="Failed to store movies")

    logger.info("Uploaded movies: filename=%s, rows=%s", file.filename, len(df))
    return {
        "filename": file.filename,
        "rows": len(df),
        "columns": len(df.columns),
        "column_names": list(df.columns),
    }


@app.get("/movies", response_model=MovieDataResponse)
def get_movies(limit: int = Query(default=100, ge=1)):
    """Return up to limit stored movies and the total count."""
    total_rows = storage.count_movies()
    if total_rows == 0:
        logger.info("Retrieved movies: returned_rows=0")
        return {"data": [], "total_rows": 0}

    data = storage.select_columns(list(MOVIE_COLUMNS), limit=limit)
    logger.info(
        "Retrieved movies: returned_rows=%s, total_rows=%s", len(data), total_rows
    )
    return {"data": data, "total_rows": total_rows}


@app.get("/movies/search", response_model=list[dict])
def query_movies(
    columns: list[str] = Query(default=["title"]),
    min_rating: float = Query(default=0, ge=0, le=10),
    limit: int = Query(default=100, ge=1),
):
    """Return id plus selected fields for movies with rating >= min_rating."""
    try:
        results = storage.query_movies(columns, min_rating, limit)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    logger.info(
        "Queried movies: columns=%s, min_rating=%s, returned_rows=%s",
        columns,
        min_rating,
        len(results),
    )
    return results


@app.post("/movies/process", response_model=MovieProcessingReport)
def process_movies(config: ProcessConfig):
    """Validate and process the stored movies using MP1 functions."""
    df = storage.get()
    if df.empty:
        logger.warning("No movies stored to process")
        raise HTTPException(
            status_code=400, detail="No movies stored. Upload a CSV first."
        )

    config_dict = config.model_dump()
    validation = config_dict["validation"]

    try:
        for column in validation["numeric_columns"]:
            if column not in df.columns:
                raise ValueError(f"Missing numeric column: {column}")

        validated = validate_dataframe(
            df, validation["required_columns"], validation["numeric_columns"]
        )
        validation_rows_removed = len(df) - len(validated)
        validated = validated.copy()

        processed = process_data(validated, config_dict)
        report = create_cleaning_report(validated, processed)
    except ValueError as error:
        logger.warning("Processing failed: %s", error)
        raise HTTPException(status_code=400, detail=str(error))

    storage.save(processed)
    logger.info(
        "Processed movies: validation_removed=%s, rows_before=%s, rows_after=%s",
        validation_rows_removed,
        report["rows_before"],
        report["rows_after"],
    )
    return {
        "validation_rows_removed": validation_rows_removed,
        "cleaning_report": report,
    }