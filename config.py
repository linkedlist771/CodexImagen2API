from pathlib import Path


ROOT = Path(__file__).parent

HOME_AUTH_PATH = Path.home() / ".codex" / "auth.json"
CONFIG_PATH = Path.home() / ".codex" / "config.toml"

AUTHEN_DIR = ROOT / "authens"
AUTHEN_DIR.mkdir(exist_ok=True, parents=True)
AUTH_FILE = AUTHEN_DIR / "auth_state.json"

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True, parents=True)

IMAGE_SAVE_DIR = ROOT / "images"
IMAGE_SAVE_DIR.mkdir(exist_ok=True, parents=True)

EXAMPLE_OUTPUT_DIR = ROOT / "example_outputs"
EXAMPLE_OUTPUT_DIR.mkdir(exist_ok=True, parents=True)

DEFAULT_CHATGPT_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_API_BASE_URL = "https://api.openai.com/v1"
DEFAULT_SERVER_BASE_URL = "http://127.0.0.1:8000"
REFRESH_TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

DEFAULT_MODEL = "gpt-5.4"
DEFAULT_REASONING = {"effort": "medium", "summary": "auto"}
DEFAULT_INSTRUCTIONS = (
    "When the user asks to generate or edit an image, call the image_generation tool. "
    "Do not answer with text only if image generation is possible."
)
DEFAULT_TEXT_PROMPT = (
    "Generate a polished illustrated poster of a small orange cat riding a bicycle "
    "through a rainy neon alley at dusk, cinematic lighting, teal and amber palette."
)
DEFAULT_EDIT_PROMPT = (
    "Use the attached image as a composition reference and transform it into a crisp "
    "retro travel-poster illustration with richer contrast, cleaner shapes, and a more "
    "dramatic sunset."
)

HTTP_TIMEOUT = 300
ORIGINATOR = "codex_cli_rs"

GENERATE_IMAGE_PROMPT_PREFIX = "Generate an image with the following description:\n"
