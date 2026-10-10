from scripts.env import load, ROOT
load(ROOT / "secrets" / "models.env")
from .main import main
main()
