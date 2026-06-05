from __future__ import annotations

import json
import os
import re
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
except ImportError:  # pragma: no cover - lets local CSV mode run without Sheets deps.
    gspread = None
    Credentials = None


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DEPLOY_MANIFEST = APP_DIR / "manifest.csv"
DEFAULT_MANIFEST = APP_DIR / "sample_manifest.csv"
DEFAULT_PROJECT_MANIFEST = (
    APP_DIR.parent / "dataset_exhibit" / "master_gt_dataset_exhibit.csv"
)
DEFAULT_OUTPUT_DIR = APP_DIR / "outputs"
CREDENTIALS_FILE = APP_DIR / "credentials.json"
SHEET_ID_FILE = APP_DIR / "sheet_id.txt"
MANIFEST_PATH_FILE = APP_DIR / "manifest_path.txt"
BRAND_VOCAB_FILE = APP_DIR / "brand_vocab.csv"
BRAND_NOT_IN_LIST = "Brand not in list"
GOOGLE_SHEET_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SHEET_HEADERS = [
    "timestamp_utc",
    "annotator",
    "row_id",
    "raw_id",
    "front_url",
    "rear_url",
    "parallel_url",
    "brand",
    "brand_status",
    "model",
    "model_status",
    "dimensions",
    "dimensions_status",
    "needs_review",
    "skip_row",
    "notes",
]
STATUS_OPTIONS = ["complete", "partial", "not_visible"]
LEGACY_STATUS_MAP = {
    "good": "complete",
    "difficult": "partial",
    "impossible": "not_visible",
}
STATUS_LABELS = {
    "complete": "Complete",
    "partial": "Partial",
    "not_visible": "Not visible",
}
FIELD_NAMES = ["brand", "model", "dimensions"]
VIEW_CANDIDATES = {
    "front": ["front_url", "front", "Front"],
    "rear": ["rear_url", "rear", "Rear"],
    "parallel": ["parallel_url", "parallel", "Parallel"],
}


def main() -> None:
    st.set_page_config(page_title="Tire Annotation", layout="wide")
    st.title("Tire Annotation")

    manifest = load_manifest()
    if manifest.empty:
        st.info("No annotation manifest is configured.")
        return

    manifest = normalize_manifest(manifest)
    brand_vocab = load_brand_vocab()
    output_dir = DEFAULT_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    sheet_id = get_default_sheet_id()
    st.session_state["google_sheet_error"] = ""
    sheet = get_google_sheet(sheet_id)
    if sheet is None:
        if not sheet_id:
            st.sidebar.warning("Google Sheets is not configured; saving local backup only.")
        else:
            st.sidebar.warning(
                "Google Sheets unavailable; saving local copy only. "
                "Ask the app admin to check the sheet ID, API setup, or sharing permissions."
            )
            if st.session_state.get("google_sheet_error"):
                st.sidebar.error(st.session_state["google_sheet_error"])

    st.session_state.setdefault("annotator", "")
    annotator = str(st.session_state.get("annotator", "")).strip()
    if not annotator:
        show_login_screen(manifest, sheet)
        return

    annotator_slug = slugify(annotator)
    annotation_csv = output_dir / f"annotations_{annotator_slug}.csv"
    event_jsonl = output_dir / f"annotation_events_{annotator_slug}.jsonl"

    annotations = {}
    if sheet is not None and annotator:
        annotations.update(load_sheet_annotations(sheet, annotator))
    else:
        annotations.update(load_annotations(annotation_csv, annotator=annotator))
    st.session_state.setdefault("row_index", 0)
    if st.session_state.pop("reset_to_next_unfinished", False):
        st.session_state["row_index"] = find_next_unfinished_index(manifest, annotations, start=0)
    st.session_state["row_index"] = min(st.session_state["row_index"], len(manifest) - 1)

    draw_sidebar(manifest, annotations, annotation_csv, event_jsonl, sheet)

    if len(annotations) >= len(manifest):
        st.success("All rows are annotated for this login.")
        if st.button("Review from first row"):
            st.session_state["row_index"] = 0
            st.rerun()
        return

    row_index = st.session_state["row_index"]
    row = manifest.iloc[row_index].to_dict()
    row_id = str(row["row_id"])
    existing = annotations.get(row_id, {})

    draw_progress(manifest, annotations, row_index)
    draw_row_header(row, row_index, len(manifest))
    draw_images(row)

    show_review = slugify(annotator).lower() == "venus"
    values = draw_annotation_form(existing, brand_vocab, row_id, show_review)
    submitted = st.button("Save Annotation", type="primary", use_container_width=True)

    if submitted:
        record = build_annotation_record(row, values)
        annotations[row_id] = record
        write_annotations(annotation_csv, annotations)
        append_event(event_jsonl, record)
        if sheet is not None:
            save_annotation_to_sheet(sheet, record)
        st.success(f"Saved row {row_id}")
        next_index = find_next_unfinished_index(manifest, annotations, start=row_index + 1)
        if next_index != row_index:
            st.session_state["row_index"] = next_index
            st.rerun()

    draw_navigation(len(manifest))


def load_manifest() -> pd.DataFrame:
    path = get_manifest_path()
    return pd.read_csv(path)


def get_manifest_path() -> Path:
    secrets = get_streamlit_secrets()
    if secrets and "manifest_path" in secrets:
        return Path(str(secrets["manifest_path"])).expanduser()
    env_value = os.environ.get("TIRE_ANNOTATION_MANIFEST", "")
    if env_value:
        return Path(env_value).expanduser()
    if MANIFEST_PATH_FILE.exists():
        return Path(MANIFEST_PATH_FILE.read_text().strip()).expanduser()
    if DEFAULT_DEPLOY_MANIFEST.exists():
        return DEFAULT_DEPLOY_MANIFEST
    if DEFAULT_PROJECT_MANIFEST.exists():
        return DEFAULT_PROJECT_MANIFEST
    return DEFAULT_MANIFEST


def load_brand_vocab() -> list[str]:
    if not BRAND_VOCAB_FILE.exists():
        return []
    df = pd.read_csv(BRAND_VOCAB_FILE).fillna("")
    if "brand" not in df.columns:
        return []
    brands = []
    seen = set()
    for value in df["brand"].astype(str):
        brand = " ".join(value.strip().split())
        key = brand.lower()
        if brand and key not in seen:
            brands.append(brand)
            seen.add(key)
    return brands


def show_login_screen(manifest: pd.DataFrame, sheet) -> None:
    st.markdown("### Annotator Login")
    st.caption("Enter your assigned annotator ID to continue. Your progress is saved automatically.")

    col_left, col_right = st.columns([1, 1])
    with col_left:
        annotator_id = st.text_input(
            "Annotator ID",
            placeholder="e.g. venu, annotator01",
            key="login_annotator_input",
        )
        if st.button("Continue", type="primary", use_container_width=True):
            cleaned = annotator_id.strip()
            if len(cleaned) < 2:
                st.error("Please enter at least 2 characters.")
            else:
                st.session_state["annotator"] = cleaned
                st.session_state["reset_to_next_unfinished"] = True
                st.rerun()
    with col_right:
        st.metric("Rows in dataset", len(manifest))
        if sheet is not None:
            st.success("Autosave is connected")
        else:
            st.warning("Autosave is not connected. Local backup will be used.")


def find_next_unfinished_index(
    manifest: pd.DataFrame,
    annotations: dict[str, dict[str, Any]],
    start: int,
) -> int:
    completed = set(annotations)
    total = len(manifest)
    for offset in range(total):
        idx = (start + offset) % total
        row_id = str(manifest.iloc[idx]["row_id"])
        if row_id not in completed:
            return idx
    return min(start, total - 1)


def slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return slug.strip("_") or "annotator"


def normalize_sheet_id(value: str) -> str:
    cleaned = str(value or "").strip().strip('"').strip("'")
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", cleaned)
    if match:
        return match.group(1)
    return cleaned


def get_default_sheet_id() -> str:
    secrets = get_streamlit_secrets()
    if secrets and "google_sheet_id" in secrets:
        return normalize_sheet_id(str(secrets["google_sheet_id"]))
    env_value = os.environ.get("TIRE_ANNOTATION_GOOGLE_SHEET_ID", "")
    if env_value:
        return normalize_sheet_id(env_value)
    if SHEET_ID_FILE.exists():
        return normalize_sheet_id(SHEET_ID_FILE.read_text().strip())
    return ""


def get_streamlit_secrets() -> Any | None:
    possible_paths = [
        Path.home() / ".streamlit" / "secrets.toml",
        APP_DIR / ".streamlit" / "secrets.toml",
    ]
    if not any(path.exists() for path in possible_paths):
        return None
    try:
        return st.secrets
    except Exception:
        return None


def get_google_sheet(spreadsheet_id: str):
    if not spreadsheet_id:
        return None
    if gspread is None or Credentials is None:
        st.session_state["google_sheet_error"] = (
            "Google Sheets dependencies are not installed. Run `pip install -r requirements.txt`."
        )
        return None
    try:
        secrets = get_streamlit_secrets()
        if CREDENTIALS_FILE.exists():
            creds = Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=GOOGLE_SHEET_SCOPES)
        elif secrets and "gcp_service_account" in secrets:
            creds = Credentials.from_service_account_info(
                dict(secrets["gcp_service_account"]),
                scopes=GOOGLE_SHEET_SCOPES,
            )
        else:
            st.session_state["google_sheet_error"] = (
                "No Google credentials found. Add credentials.json or Streamlit secrets."
            )
            return None

        client = gspread.authorize(creds)
        sheet = client.open_by_key(spreadsheet_id).sheet1
        ensure_sheet_headers(sheet)
        return sheet
    except Exception as exc:
        message = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        st.session_state["google_sheet_error"] = message or repr(exc)
        return None


def ensure_sheet_headers(sheet) -> None:
    values = sheet.get_all_values()
    if not values:
        sheet.append_row(SHEET_HEADERS)
        return
    existing = values[0]
    missing = [header for header in SHEET_HEADERS if header not in existing]
    if missing:
        sheet.update("1:1", [existing + missing])


def load_sheet_annotations(sheet, annotator: str) -> dict[str, dict[str, Any]]:
    if sheet is None or not annotator:
        return {}
    try:
        records = sheet.get_all_records()
    except Exception as exc:
        st.warning(f"Could not read existing Google Sheet annotations: {exc}")
        return {}

    annotations: dict[str, dict[str, Any]] = {}
    for record in records:
        if str(record.get("annotator", "")).strip() != annotator:
            continue
        row_id = str(record.get("row_id", "")).strip()
        if not row_id:
            continue
        annotations[row_id] = sheet_record_to_annotation(record)
    return annotations


def sheet_record_to_annotation(record: dict[str, Any]) -> dict[str, Any]:
    out = dict(record)
    if "timestamp_utc" in out:
        out["updated_at_utc"] = out["timestamp_utc"]
    return out


def save_annotation_to_sheet(sheet, record: dict[str, Any]) -> bool:
    if sheet is None:
        return False
    try:
        headers = get_sheet_headers(sheet)
        row = [record_to_sheet_value(record, header) for header in headers]
        sheet.append_row(row)
        return True
    except Exception as exc:
        st.error(f"Error saving annotation to Google Sheets: {exc}")
        return False


def get_sheet_headers(sheet) -> list[str]:
    headers = sheet.row_values(1)
    if not headers:
        return SHEET_HEADERS
    return headers


def record_to_sheet_value(record: dict[str, Any], header: str) -> Any:
    if header == "timestamp_utc":
        return record.get("updated_at_utc", "")
    return record.get(header, "")


def normalize_manifest(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if "row_id" not in df.columns:
        if "row_num" in df.columns:
            df["row_id"] = df["row_num"].apply(lambda x: f"row_{int(x):04d}")
        else:
            df["row_id"] = [f"row_{idx:04d}" for idx in range(len(df))]
    if "raw_id" not in df.columns:
        df["raw_id"] = ""

    for view, candidates in VIEW_CANDIDATES.items():
        target = f"{view}_url"
        if target not in df.columns:
            df[target] = first_existing_column(df, candidates, default="")

    return df.fillna("")


def first_existing_column(df: pd.DataFrame, candidates: list[str], default: str) -> pd.Series:
    for col in candidates:
        if col in df.columns:
            return df[col]
    return pd.Series([default] * len(df), index=df.index)


def load_annotations(path: Path, annotator: str = "") -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    df = pd.read_csv(path).fillna("")
    if "row_id" not in df.columns:
        return {}
    if annotator and "annotator" in df.columns:
        df = df[df["annotator"].astype(str).str.strip() == annotator]
    return {str(row["row_id"]): dict(row) for row in df.to_dict("records")}


def write_annotations(path: Path, annotations: dict[str, dict[str, Any]]) -> None:
    rows = list(annotations.values())
    df = pd.DataFrame(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def append_event(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=True) + "\n")


def draw_sidebar(
    manifest: pd.DataFrame,
    annotations: dict[str, dict[str, Any]],
    annotation_csv: Path,
    event_jsonl: Path,
    sheet,
) -> None:
    st.sidebar.header("Session")
    st.sidebar.write(f"Annotator: **{st.session_state.get('annotator', '')}**")

    completed = len(annotations)
    st.sidebar.metric("Rows", len(manifest))
    st.sidebar.metric("Completed", completed)
    st.sidebar.metric("Remaining", max(len(manifest) - completed, 0))

    if sheet is not None:
        st.sidebar.caption("Autosave: connected")
    else:
        st.sidebar.caption("Autosave: local backup")

    if st.sidebar.button("Switch annotator", use_container_width=True):
        st.session_state["annotator"] = ""
        st.session_state["row_index"] = 0
        st.session_state["reset_to_next_unfinished"] = False
        st.rerun()


def draw_progress(
    manifest: pd.DataFrame,
    annotations: dict[str, dict[str, Any]],
    row_index: int,
) -> None:
    completed_ids = set(annotations)
    current_row_id = str(manifest.iloc[row_index]["row_id"])
    completed = len(completed_ids)
    st.progress(completed / max(len(manifest), 1), text=f"{completed}/{len(manifest)} rows saved")
    if current_row_id in completed_ids:
        st.info("This row already has an annotation. Saving again will update it.")


def draw_row_header(row: dict[str, Any], row_index: int, total_rows: int) -> None:
    st.subheader(f"Row {row_index + 1} of {total_rows}")
    st.caption(f"row_id: {row.get('row_id', '')} | raw_id: {row.get('raw_id', '')}")


def draw_images(row: dict[str, Any]) -> None:
    cols = st.columns(3)
    for col, view in zip(cols, ["front", "rear", "parallel"]):
        url = str(row.get(f"{view}_url", "")).strip()
        with col:
            st.markdown(f"**{view.title()}**")
            if url:
                draw_stretch_image(url)
                st.link_button("Open source", url, use_container_width=True)
            else:
                st.warning("No image URL")


def draw_stretch_image(source: str) -> None:
    try:
        st.image(source, width="stretch")
    except TypeError:
        st.image(source, use_container_width=True)


def draw_annotation_form(
    existing: dict[str, Any],
    brand_vocab: list[str],
    row_id: str,
    show_review: bool,
) -> dict[str, Any]:
    st.markdown("### Labels")
    values: dict[str, Any] = {}
    label_cols = st.columns(3)

    with label_cols[0]:
        with st.container(border=True):
            st.markdown("#### Brand")
            values["brand"] = draw_brand_input(existing, brand_vocab, row_id)
            values["brand_status"] = draw_status_radio("brand", existing, row_id)

    with label_cols[1]:
        with st.container(border=True):
            st.markdown("#### Model")
            values["model"] = st.text_input(
                "Model",
                value=str(existing.get("model", "")),
                key=f"{row_id}_model",
            )
            values["model_status"] = draw_status_radio("model", existing, row_id)

    with label_cols[2]:
        with st.container(border=True):
            st.markdown("#### Dimensions")
            values["dimensions"] = st.text_input(
                "Dimensions",
                value=str(existing.get("dimensions", "")),
                key=f"{row_id}_dimensions",
            )
            values["dimensions_status"] = draw_status_radio("dimensions", existing, row_id)

    st.divider()

    if show_review:
        st.markdown("### Review")
        review_cols = st.columns([1, 1, 2])
        with review_cols[0]:
            values["needs_review"] = st.checkbox(
                "Needs review",
                value=to_bool(existing.get("needs_review", False)),
                key=f"{row_id}_needs_review",
            )
        with review_cols[1]:
            values["skip_row"] = st.checkbox(
                "Skip row",
                value=to_bool(existing.get("skip_row", False)),
                key=f"{row_id}_skip_row",
            )
        with review_cols[2]:
            values["notes"] = st.text_input(
                "Notes",
                value=str(existing.get("notes", "")),
                key=f"{row_id}_notes",
            )
    else:
        values["needs_review"] = to_bool(existing.get("needs_review", False))
        values["skip_row"] = to_bool(existing.get("skip_row", False))
        values["notes"] = str(existing.get("notes", "")).strip()
    return values


def draw_brand_input(existing: dict[str, Any], brand_vocab: list[str], row_id: str) -> str:
    existing_brand = str(existing.get("brand", "")).strip()
    options = [BRAND_NOT_IN_LIST] + brand_vocab
    index = options.index(existing_brand) if existing_brand in options else None
    selected = st.selectbox(
        "Brand",
        options,
        index=index,
        placeholder="Select brand",
        key=f"{row_id}_brand_select",
    )
    if selected == BRAND_NOT_IN_LIST:
        custom_default = "" if existing_brand in brand_vocab else existing_brand
        return st.text_input(
            "Enter brand",
            value=custom_default,
            key=f"{row_id}_brand_custom",
        )
    if selected:
        return selected
    return existing_brand


def draw_status_radio(field: str, existing: dict[str, Any], row_id: str) -> str:
    status_default = normalize_status(existing.get(f"{field}_status", "complete"))
    status_index = STATUS_OPTIONS.index(status_default) if status_default in STATUS_OPTIONS else 0
    return st.radio(
        "Visibility",
        STATUS_OPTIONS,
        index=status_index,
        horizontal=True,
        key=f"{row_id}_{field}_visibility",
        format_func=lambda value: STATUS_LABELS.get(value, value),
    )


def normalize_status(value: Any) -> str:
    cleaned = str(value or "complete").strip().lower()
    return LEGACY_STATUS_MAP.get(cleaned, cleaned)


def build_annotation_record(row: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    record: dict[str, Any] = {
        "row_id": str(row.get("row_id", "")),
        "raw_id": str(row.get("raw_id", "")),
        "annotator": st.session_state.get("annotator", ""),
        "updated_at_utc": now,
        "front_url": str(row.get("front_url", "")),
        "rear_url": str(row.get("rear_url", "")),
        "parallel_url": str(row.get("parallel_url", "")),
    }
    for field in FIELD_NAMES:
        record[field] = str(values.get(field, "")).strip()
        record[f"{field}_status"] = values.get(f"{field}_status", "complete")
    record["needs_review"] = bool(values.get("needs_review", False))
    record["skip_row"] = bool(values.get("skip_row", False))
    record["notes"] = str(values.get("notes", "")).strip()
    return record


def draw_navigation(total_rows: int) -> None:
    prev_col, row_col, next_col, jump_col = st.columns([1, 2, 1, 2])
    with prev_col:
        if st.button("Previous", use_container_width=True):
            st.session_state["row_index"] = max(st.session_state["row_index"] - 1, 0)
            st.rerun()
    with row_col:
        st.caption(f"Current index: {st.session_state['row_index'] + 1}")
    with next_col:
        if st.button("Next", use_container_width=True):
            st.session_state["row_index"] = min(st.session_state["row_index"] + 1, total_rows - 1)
            st.rerun()
    with jump_col:
        jump_to = st.number_input("Jump to row number", min_value=1, max_value=total_rows, value=st.session_state["row_index"] + 1)
        if jump_to != st.session_state["row_index"] + 1:
            st.session_state["row_index"] = int(jump_to) - 1
            st.rerun()


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "y"}
    return bool(value)


if __name__ == "__main__":
    main()
