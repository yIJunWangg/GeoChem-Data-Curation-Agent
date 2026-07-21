"""Web server entrypoint for local preview and managed deployments."""

from __future__ import annotations

import argparse
import threading
import webbrowser

from ..core.runtime import load_runtime_settings


def main() -> None:
    import uvicorn

    settings = load_runtime_settings()
    parser = argparse.ArgumentParser(description="Run the GeoChem Web application")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", default=settings.reload)
    parser.add_argument("--no-browser", action="store_true", help="Do not open the local preview in a browser")
    args = parser.parse_args()

    preview_host = "127.0.0.1" if args.host in {"0.0.0.0", "::"} else args.host
    url = f"http://{preview_host}:{args.port}"
    if settings.open_browser and not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(
        "geochem.web.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1 if args.reload else settings.web_workers,
        log_level=settings.log_level,
        proxy_headers=settings.profile.value != "development",
        forwarded_allow_ips=settings.forwarded_allow_ips,
    )


if __name__ == "__main__":
    main()
