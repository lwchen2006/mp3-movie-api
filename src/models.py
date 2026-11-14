from pydantic import BaseModel

# Keep your MP2 ProcessConfig model (and any others main.py still uses) here.
# Delete the old dataset-specific response models that no longer apply.


class MovieUploadInfo(BaseModel):
    filename: str
    rows: int
    columns: int
    column_names: list[str]


class MovieDataResponse(BaseModel):
    data: list[dict]
    total_rows: int


class MovieProcessingReport(BaseModel):
    validation_rows_removed: int
    cleaning_report: dict