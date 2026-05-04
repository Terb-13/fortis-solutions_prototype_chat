import glob
import os
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
from docx import Document
from pypdf import PdfReader
from dotenv import load_dotenv
from supabase import create_client

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
REPO_ROOT = Path(__file__).resolve().parents[1]
TRAINING_DIR = REPO_ROOT / "Chatbot Training"
load_dotenv(dotenv_path=ENV_PATH, override=True)


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    # utf-8-sig handles optional BOM that can break first key parsing.
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        cleaned = value.strip().strip("'").strip('"')
        values[key.strip()] = cleaned
    return values


env_file_values = _read_env_file(ENV_PATH)

SUPABASE_URL = os.getenv("SUPABASE_URL") or env_file_values.get("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or env_file_values.get("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "Missing Supabase environment variables. Set SUPABASE_URL and "
        "SUPABASE_SERVICE_ROLE_KEY in your .env file, then save the file "
        "before rerunning."
    )


def _disable_proxy_for_supabase(url: str) -> None:
    # Some environments inject HTTP(S)_PROXY that blocks Supabase requests.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)

    host = urlparse(url).hostname
    if not host:
        return
    existing = os.getenv("NO_PROXY", "")
    if host not in existing.split(","):
        os.environ["NO_PROXY"] = f"{existing},{host}".strip(",")


_disable_proxy_for_supabase(SUPABASE_URL)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def insert_knowledge(source, category, title, content, date=None, metadata=None):
    if not content or len(content.strip()) < 10:
        return
    supabase.table("fortis_knowledge").insert(
        {
            "source": source,
            "category": category,
            "title": title,
            "content": content.strip(),
            "date": date,
            "metadata": metadata or {},
        }
    ).execute()


# === 1. SBU Status Update ===
def ingest_sbu_status():
    df = pd.read_excel(TRAINING_DIR / "SBU-Status-Update.xlsx", header=None)
    for idx, row in df.iterrows():
        content = " | ".join([str(x) for x in row.values if pd.notna(x)])
        if len(content) > 30:
            insert_knowledge(
                source="sbu-status-update",
                category="sbu",
                title=f"SBU Update Row {idx}",
                content=content,
                date="2026-04-28",
            )


# === 2. Pricing Grid ===
def ingest_pricing():
    import pandas as pd
    from supabase import create_client
    import os

    SUPABASE_URL = os.getenv("SUPABASE_URL") or env_file_values.get("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or env_file_values.get("SUPABASE_SERVICE_ROLE_KEY")
    pricing_supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

    df = pd.read_excel(TRAINING_DIR / "Portal Quick Ship Labels.xlsx")

    # Clean column names
    df.columns = [c.strip().lower().replace(" ", "_").replace("/", "_").replace("(", "").replace(")", "") for c in df.columns]

    for _, row in df.iterrows():
        try:
            pricing_supabase.table("fortis_pricing").insert(
                {
                    "sku": str(row.get("sku__die_#_generated", "")),
                    "comment_application": str(row.get("comment__application", "")),
                    "shape": str(row.get("shape", "")),
                    "width_in": row.get("width_in"),
                    "height_in": row.get("height_in"),
                    "material": str(row.get("material", "")),
                    "finish": str(row.get("finish", "")),
                    "gsm": int(row.get("gsm", 0)) if pd.notna(row.get("gsm")) else None,
                    "print_device": str(row.get("print_device", "")),
                    "cost_100": row.get("cost@100"),
                    "cost_250": row.get("cost@250"),
                    "cost_500": row.get("cost@500"),
                    "cost_1000": row.get("cost@1k"),
                    "cost_1500": row.get("cost@1.5k"),
                    "cost_2000": row.get("cost@2k"),
                    "cost_2500": row.get("cost@2.5k"),
                    "cost_3000": row.get("cost@3k"),
                    "cost_4000": row.get("cost@4k"),
                    "cost_5000": row.get("cost@5k"),
                    "notes": str(row.get("notes__ganging__tow_priority", "")),
                    "ganging_priority": str(row.get("notes__ganging__tow_priority", "")),
                }
            ).execute()
        except Exception as e:
            print(f"Error inserting row: {e}")

    print("✅ Pricing data ingested into fortis_pricing table.")


# === 3. Emails ===
def ingest_emails():
    df = pd.read_excel(TRAINING_DIR / "chatbot_training_emails.xlsx", header=None)
    for idx, row in df.iterrows():
        content = " | ".join([str(x) for x in row.values if pd.notna(x)])
        if len(content) > 30:
            insert_knowledge(
                source="email-history",
                category="email",
                title=f"Customer Email {idx}",
                content=content,
            )


# === 4. Transcripts (DOCX + PDF) ===
def ingest_transcripts():
    transcript_files = glob.glob(str(TRAINING_DIR / "Flexlink Training" / "transcriptions" / "*.*"))
    for file_path in transcript_files:
        filename = os.path.basename(file_path)
        try:
            if file_path.endswith(".docx"):
                doc = Document(file_path)
                content = "\n".join([p.text for p in doc.paragraphs])
            elif file_path.endswith(".pdf"):
                reader = PdfReader(file_path)
                content = "\n".join([page.extract_text() or "" for page in reader.pages])
            else:
                continue
            insert_knowledge(
                source="transcript",
                category="flexlink",
                title=filename,
                content=content[:8000],  # limit length
                metadata={"filename": filename},
            )
        except Exception as e:
            print(f"Error processing {filename}: {e}")


if __name__ == "__main__":
    print("Ingesting SBU Status...")
    ingest_sbu_status()
    print("Ingesting Pricing...")
    ingest_pricing()
    print("Ingesting Emails...")
    ingest_emails()
    print("Ingesting Transcripts...")
    ingest_transcripts()
    print("✅ Done! All knowledge loaded into Supabase.")
