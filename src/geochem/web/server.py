"""Local Web server entrypoint."""

from __future__ import annotations

import threading
import webbrowser


def main() -> None:
    import uvicorn

    url = "http://127.0.0.1:8765"
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run("geochem.web.api:app", host="127.0.0.1", port=8765, reload=False)


if __name__ == "__main__":
    main()

