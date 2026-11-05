"""Runtime settings. Everything can be overridden with SPR_* environment variables."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.getenv("SPR_DATA_DIR", ROOT / "data"))
CRITERIA_DIR = Path(os.getenv("SPR_CRITERIA_DIR", ROOT / "criteria"))

L1_WINDOW_WORDS = 400
L1_STEP_WORDS = 200

# Local language model (Ollama) that reads every page and decides what each passage is about.
LLM_URL = os.getenv("SPR_LLM_URL", "http://localhost:11434")
LLM_MODEL = os.getenv("SPR_LLM_MODEL", "qwen3:8b")
LLM_ENABLED = os.getenv("SPR_LLM", "1") == "1"
LLM_PARALLEL = int(os.getenv("SPR_LLM_PARALLEL", "3"))  # model readings in flight (match OLLAMA_NUM_PARALLEL)
LLM_VOTES = int(os.getenv("SPR_LLM_VOTES", "1"))   # readings per chunk; 2 = also a second wording, most cautious wins
LLM_TIMEOUT = 180

# Cloud LLMs are disabled: protected text must not leave the machine without explicit approval.
CLOUD_LLM_ALLOWED = False

# Guide's color table: "Anything found on List 1" is red, and yellow means "nothing found on List 1". So a
# *possible* List 1 match (ambiguous marking, beside a disclaimer, not read in context) is red too, with a note
# saying why the tool is unsure. Set SPR_LIST1_UNCERTAIN=yellow to show those as yellow instead.
LIST1_UNCERTAIN_COLOR = os.getenv("SPR_LIST1_UNCERTAIN", "red")


def list_color(list_no: int, color: str) -> str:
    """Color of an uncertain finding: List 1 follows LIST1_UNCERTAIN_COLOR, List 2 keeps its own."""
    return LIST1_UNCERTAIN_COLOR if list_no == 1 and color == "yellow" else color

MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_PAGES = int(os.getenv("SPR_MAX_PAGES", "600"))                 # larger documents are refused, not half-read
MAX_DOCX_UNZIPPED = 300 * 1024 * 1024                               # zip-bomb guard for Word files
LLM_PAPER_BUDGET_SECONDS = int(os.getenv("SPR_LLM_BUDGET", "900"))  # after this, unread passages stay "possible"

# No accounts: actions are recorded under this name in the history.
REVIEWER_NAME = "reviewer"
