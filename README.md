# Tire Annotation App

Streamlit app for collecting tire sidewall annotations for brand, model, and dimensions.

The app is designed for a lightweight workflow:

```text
manifest.csv -> Streamlit annotation UI -> Google Sheets + local CSV audit
```

Annotators do not upload files or configure saving in the UI. The app owner configures the manifest and Google Sheet on the backend before launching the app.

## Manifest Format

The input CSV should contain one row per tire. Recommended columns:

```text
row_id,raw_id,front_url,rear_url,parallel_url
```

The app also accepts existing project-style columns:

```text
row_num,Front,Rear,Parallel
```

New annotation manifests do not need prefilled labels. The annotator enters the final brand, model, and dimensions from scratch.

Brands are selected from:

```text
apps/tire_annotation_app/brand_vocab.csv
```

The first dropdown option is **Brand not in list**. Selecting it opens a free-text field for uncommon or newly observed brands.

## Run Locally

From this directory:

```bash
pip install -r requirements.txt
streamlit run app.py
```

From the LoCoText root:

```bash
streamlit run apps/tire_annotation_app/app.py
```

## Backend Manifest Configuration

By default, the app loads the existing project exhibit CSV if available:

```text
apps/tire_annotation_app/manifest.csv
```

This checked-in manifest is intended for Streamlit Cloud deployment and contains image URLs only, not prefilled labels. To point a local app to a different annotation manifest, create a git-ignored file:

```text
apps/tire_annotation_app/manifest_path.txt
```

and put the absolute path to the manifest CSV inside it, for example:

```text
/home/vshah3/LoCoText/apps/tire_annotation_app/new_collection_manifest.csv
```

You can also set:

```bash
export TIRE_ANNOTATION_MANIFEST="/path/to/manifest.csv"
```

## Google Sheets Autosave

The app can autosave every submitted annotation to a live Google Sheet, similar to the face annotation app.

### 1. Create a Google Sheet

Create an empty Google Sheet and copy the sheet ID from the URL:

```text
https://docs.google.com/spreadsheets/d/<SHEET_ID>/edit
```

### 2. Create a Service Account

Create a Google Cloud service account with Sheets/Drive access, download the JSON key, and share the Google Sheet with the service account email.

### 3. Add Credentials

For local use, place the downloaded key here:

```text
apps/tire_annotation_app/credentials.json
```

This file is ignored by git.

For Streamlit Cloud, copy `.streamlit/secrets.toml.example` into the Streamlit secrets UI and fill in:

```toml
google_sheet_id = "your-google-sheet-id"

[gcp_service_account]
type = "service_account"
project_id = "..."
private_key = "..."
client_email = "..."
```

You can also set the sheet ID locally with:

```bash
export TIRE_ANNOTATION_GOOGLE_SHEET_ID="your-google-sheet-id"
```

Or create a local git-ignored file:

```text
apps/tire_annotation_app/sheet_id.txt
```

containing either the full Google Sheet URL or only the sheet ID.

To test the connection outside Streamlit:

```bash
python check_google_sheet.py
```

Once configured, annotators do not see the Sheet ID. Every click on **Save Annotation** appends to the configured Google Sheet immediately and also writes a local backup.

### Sheet Columns

The app initializes the first worksheet with:

```text
timestamp_utc, annotator, row_id, raw_id,
front_url, rear_url, parallel_url,
brand, brand_status,
model, model_status,
dimensions, dimensions_status,
needs_review, skip_row, notes
```

Each save appends a new row. If the same annotator edits the same tire again, the latest saved row is used when resuming.

If an older worksheet was initialized before hints were removed, it may still contain hint columns. The app writes by header name, so those old columns are left blank and saved values stay aligned.

## Outputs

By default, outputs are written under:

```text
apps/tire_annotation_app/outputs/
```

Local files:

```text
annotations.csv
annotation_events.jsonl
```

`annotations.csv` is the current local clean table. `annotation_events.jsonl` is the local audit trail of every saved annotation event. With Google Sheets enabled, the sheet is the live shared source of truth and the local files are a backup.

## Deployment Notes

For GitHub/Streamlit Cloud, keep this folder self-contained:

```text
app.py
requirements.txt
sample_manifest.csv
.streamlit/config.toml
```

On hosted Streamlit, local file writes may not be durable across restarts. For production annotation, prefer Google Sheets autosave as the source of truth.

- run locally/on a VM and periodically sync the `outputs/` directory,
- use Google Sheets autosave,
- download the CSV from the app after each session.
