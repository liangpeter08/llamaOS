import os

import uvicorn

from common import db

db.init(db.get())
uvicorn.run("services.api.app:app", host=os.environ.get("API_HOST", "127.0.0.1"),
            port=int(os.environ.get("API_PORT", "8080")), log_level="warning")
