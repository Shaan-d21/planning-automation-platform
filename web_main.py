"""Development entry point for the Oracle EPM web application."""

from __future__ import annotations

import uvicorn

from app.web import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "web_main:app",
        host="127.0.0.1",
        port=8080,
        reload=True,
    )

